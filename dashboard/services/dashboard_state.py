from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from dashboard.models import Invoice, OutreachSuppression


@dataclass(frozen=True)
class IntegrationReadiness:
    name: str
    configured: bool | None
    purpose: str


@dataclass(frozen=True)
class PendingInvoiceApproval:
    invoice_id: int
    deal_id: int
    lead_id: int
    company_name: str
    contact_name: str
    email: str
    amount: int


def suppression_count() -> int:
    """Return the durable do-not-contact count for the operator dashboard."""
    return OutreachSuppression.objects.count()


def first_pending_invoice() -> PendingInvoiceApproval | None:
    """Return the oldest invoice waiting for explicit human send approval."""
    invoice = (
        Invoice.objects.filter(sent_at__isnull=True)
        .select_related("deal__lead")
        .order_by("issued_at", "id")
        .first()
    )
    if invoice is None:
        return None
    lead = invoice.deal.lead
    return PendingInvoiceApproval(
        invoice_id=invoice.pk,
        deal_id=invoice.deal_id,
        lead_id=lead.pk,
        company_name=lead.company_name,
        contact_name=(lead.contact_name or lead.company_name),
        email=(invoice.recipient_email or lead.contact_email or ""),
        amount=invoice.amount,
    )


def integration_readiness() -> tuple[IntegrationReadiness, ...]:
    """Report configuration truth without exposing or persisting credentials."""
    yahoo_ready = bool(
        settings.YAHOO_SMTP_HOST
        and settings.YAHOO_SMTP_PORT
        and settings.YAHOO_SMTP_USERNAME
        and settings.YAHOO_SMTP_APP_PASSWORD
    )
    return (
        IntegrationReadiness(
            name="Google Places",
            configured=bool(settings.GOOGLE_MAPS_API_KEY),
            purpose="Scout discovery",
        ),
        IntegrationReadiness(
            name="Independent web search",
            configured=bool(settings.SERPAPI_API_KEY),
            purpose="No-website verification",
        ),
        IntegrationReadiness(
            name="Outbound identity",
            configured=bool(settings.OUTREACH_EMAIL and settings.OUTREACH_PHONE),
            purpose="Professional email signature",
        ),
        IntegrationReadiness(
            name="Yahoo Business SMTP",
            configured=yahoo_ready,
            purpose="Sales Bot invoice/outreach and final delivery email",
        ),
    )
