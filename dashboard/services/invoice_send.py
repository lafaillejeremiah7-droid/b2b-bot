from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from dashboard.adapter.stripe_invoicing import StripeInvoiceReceipt, get_stripe_invoice_client
from dashboard.models import Deal, Invoice, Operator
from dashboard.services.authz import Action, Authz
from dashboard.services.confirmation import consume_confirmation
from dashboard.services.errors import ValidationRejected


@dataclass(frozen=True)
class InvoiceSendOutcome:
    invoice: Invoice
    already_sent: bool
    receipt: StripeInvoiceReceipt | None = None


class InvoiceSendGate:
    """Human approval gate for customer-facing Stripe invoice email."""

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

            # Snapshot the exact address approved on-screen before any external
            # request. A later Lead edit cannot silently redirect a retry.
            if invoice.recipient_email != destination or invoice.sent_by_operator_id != operator.pk:
                invoice.recipient_email = destination
                invoice.sent_by_operator = operator
                invoice.save(update_fields=["recipient_email", "sent_by_operator"])

            invoice_id = invoice.pk
            customer_name = (deal.lead.contact_name or deal.lead.company_name or destination).strip()
            amount = invoice.amount

        receipt = get_stripe_invoice_client().create_and_send_invoice(
            local_invoice_id=invoice_id,
            recipient_email=destination,
            customer_name=customer_name,
            amount_usd=amount,
            description=settings.STRIPE_INVOICE_DESCRIPTION,
        )

        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
            if invoice.sent_at is not None:
                return InvoiceSendOutcome(invoice=invoice, already_sent=True, receipt=receipt)
            invoice.provider_invoice_id = receipt.provider_invoice_id
            if receipt.invoice_number:
                invoice.invoice_number = receipt.invoice_number[:200]
            invoice.hosted_invoice_url = receipt.hosted_invoice_url
            invoice.sent_at = timezone.now()
            invoice.sent_by_operator = operator
            invoice.save(
                update_fields=[
                    "provider_invoice_id",
                    "invoice_number",
                    "hosted_invoice_url",
                    "sent_at",
                    "sent_by_operator",
                ]
            )
            return InvoiceSendOutcome(invoice=invoice, already_sent=False, receipt=receipt)
