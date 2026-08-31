from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from dashboard.adapter import AdapterResult, get_pipeline_adapter
from dashboard.adapter.stripe_invoicing import StripeInvoiceReceipt, get_stripe_invoice_client
from dashboard.models import Deal, Invoice, Operator
from dashboard.services.authz import Action, Authz
from dashboard.services.closer import Closer
from dashboard.services.confirmation import consume_confirmation
from dashboard.services.errors import ValidationRejected
from dashboard.services.outreach_templates import first_name_token
from dashboard.services.six_employee_pipeline import SalesBot


@dataclass(frozen=True)
class InvoiceSendOutcome:
    invoice: Invoice
    already_sent: bool
    receipt: StripeInvoiceReceipt | None = None
    sales_result: AdapterResult | None = None


class InvoiceSendGate:
    """Human approval gate for Closer → Sales Bot invoice delivery.

    The first approved attempt asks Closer #7 to create/finalize the Stripe
    invoice and persist its Hosted Invoice Page URL. Sales Bot #5 then sends that
    URL in the normal branded company email. Stripe itself never receives a
    ``/send`` request from this flow.

    If Stripe succeeds but email delivery fails, the link stays persisted and a
    later approved retry reuses it rather than creating another invoice.
    """

    @staticmethod
    def send(
        *,
        deal_id: int,
        operator: Operator,
        session,
        confirmation_token: str,
    ) -> InvoiceSendOutcome:
        Authz.check(operator, Action.INVOICE_CREATE)

        with transaction.atomic():
            deal = (
                Deal.objects.select_for_update()
                .select_related("lead")
                .get(pk=deal_id)
            )
            invoice = Invoice.objects.select_for_update().filter(deal_id=deal.pk).first()
            if invoice is None:
                raise ValidationRejected("Create the invoice before sending it.")
            if invoice.sent_at is not None:
                return InvoiceSendOutcome(invoice=invoice, already_sent=True)

            consume_confirmation(
                session,
                token=confirmation_token,
                action="invoice.send",
                target_id=invoice.pk,
            )

            destination = (invoice.recipient_email or deal.lead.contact_email or "").strip().lower()
            if not destination:
                raise ValidationRejected("The customer has no email address for the invoice.")
            if "@" not in destination or len(destination) > 320:
                raise ValidationRejected("The customer invoice email is invalid.")

            # Snapshot only the exact destination approved on-screen. The
            # operator is not recorded as the sender until Yahoo actually
            # accepts the message; a failed attempt must not look sent.
            if invoice.recipient_email != destination:
                invoice.recipient_email = destination
                invoice.save(update_fields=["recipient_email"])

            invoice_id = invoice.pk
            lead_id = deal.lead_id
            first_name = first_name_token(deal.lead.contact_name)
            company_name = (deal.lead.company_name or "").strip()
            customer_name = (deal.lead.contact_name or deal.lead.company_name or destination).strip() or destination
            amount = invoice.amount
            existing_receipt = None
            if invoice.provider_invoice_id and invoice.hosted_invoice_url:
                existing_receipt = StripeInvoiceReceipt(
                    provider_invoice_id=invoice.provider_invoice_id,
                    invoice_number=None,
                    hosted_invoice_url=invoice.hosted_invoice_url,
                )

        # Closer owns invoice generation. This external call is outside the DB
        # transaction and Stripe receives deterministic idempotency keys derived
        # from invoice_id inside StripeInvoiceClient.
        receipt = existing_receipt
        if receipt is None:
            receipt = Closer().generate_invoice_link(
                client=get_stripe_invoice_client(),
                local_invoice_id=invoice_id,
                recipient_email=destination,
                customer_name=customer_name,
                amount_usd=amount,
                description=settings.STRIPE_INVOICE_DESCRIPTION,
            )
            with transaction.atomic():
                invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
                # A concurrent request may have persisted the same idempotent
                # Stripe invoice first. Never replace a different provider ID.
                if invoice.provider_invoice_id and invoice.provider_invoice_id != receipt.provider_invoice_id:
                    raise ValidationRejected("Invoice provider identity changed during generation; refusing to continue.")
                invoice.provider_invoice_id = receipt.provider_invoice_id
                invoice.hosted_invoice_url = receipt.hosted_invoice_url
                # Keep the application's unique local invoice_number stable.
                # Stripe's invoice number is provider metadata and must not
                # overwrite the local unique key or collide with another row.
                invoice.save(update_fields=["provider_invoice_id", "hosted_invoice_url"])

        hosted_url = (receipt.hosted_invoice_url or "").strip()
        if not hosted_url:
            raise ValidationRejected("Stripe invoice link is unavailable; Sales Bot cannot send the invoice.")

        # Sales Bot owns customer delivery. The stable UUID is reused across
        # retries so the provider boundary can keep a stable Message-ID.
        email_key = uuid5(NAMESPACE_URL, f"b2b-invoice-email:{invoice_id}")
        sales_result = SalesBot().send_invoice_link(
            adapter=get_pipeline_adapter(),
            lead_id=lead_id,
            to_email=destination,
            first_name=first_name,
            company_name=company_name,
            amount_usd=amount,
            hosted_invoice_url=hosted_url,
            idempotency_key=email_key,
        )

        # Stub mode intentionally performs no network I/O. Treat it as a failed
        # delivery for domain-state purposes so the dashboard never claims an
        # invoice was emailed when only a dry-run adapter executed.
        if sales_result.status == "success" and bool(sales_result.payload.get("stub")):
            sales_result = AdapterResult(
                "failure",
                failure_reason="Sales Bot delivery adapter is in stub mode; no customer email was sent.",
                payload=sales_result.payload,
            )

        if sales_result.status != "success":
            invoice = Invoice.objects.get(pk=invoice_id)
            return InvoiceSendOutcome(
                invoice=invoice,
                already_sent=False,
                receipt=receipt,
                sales_result=sales_result,
            )

        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
            if invoice.sent_at is not None:
                return InvoiceSendOutcome(
                    invoice=invoice,
                    already_sent=True,
                    receipt=receipt,
                    sales_result=sales_result,
                )
            invoice.sent_at = timezone.now()
            invoice.sent_by_operator = operator
            invoice.save(update_fields=["sent_at", "sent_by_operator"])
            return InvoiceSendOutcome(
                invoice=invoice,
                already_sent=False,
                receipt=receipt,
                sales_result=sales_result,
            )
