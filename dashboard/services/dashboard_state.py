from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dashboard.models import (
    Invoice,
    Lead,
    OutreachRequest,
    OutreachRequestStatus,
    OutreachSuppression,
    PipelineState,
    RejectedEvent,
    SiteProject,
    SiteReviewState,
)
from dashboard.services.runtime_readiness import runtime_requirements


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


@dataclass(frozen=True)
class CompanyKitchenSnapshot:
    total_leads: int
    in_progress: int
    completed: int
    furnace_events: int
    ready_for_review: int
    approved_sites: int
    successful_outreach: int
    failed_outreach: int
    closed_lost: int
    rejected_sites: int
    rejected_events: int


@dataclass(frozen=True)
class RecentLeadDish:
    lead_id: int
    company_name: str
    status: str
    status_label: str
    quality_score: int
    last_activity_at: datetime
    disposition: str


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


def company_kitchen_snapshot() -> CompanyKitchenSnapshot:
    """Build dashboard counts exclusively from persisted records.

    ``furnace_events`` is deliberately an event count rather than a unique-lead
    count: a rejected site, a failed outreach request, and a rejected inbound
    event are distinct fail-closed decisions even when they concern one lead.
    """
    total_leads = Lead.objects.count()
    completed = Lead.objects.filter(status=PipelineState.RELEASED).count()
    closed_lost = Lead.objects.filter(status=PipelineState.CLOSED_LOST).count()
    in_progress = Lead.objects.exclude(
        status__in=(PipelineState.RELEASED, PipelineState.CLOSED_LOST)
    ).count()
    ready_for_review = SiteProject.objects.filter(
        review_state=SiteReviewState.READY_FOR_REVIEW
    ).count()
    approved_sites = SiteProject.objects.filter(
        review_state=SiteReviewState.APPROVED
    ).count()
    rejected_sites = SiteProject.objects.filter(
        review_state=SiteReviewState.REJECTED
    ).count()
    successful_outreach = OutreachRequest.objects.filter(
        status=OutreachRequestStatus.SUCCEEDED
    ).count()
    failed_outreach = OutreachRequest.objects.filter(
        status=OutreachRequestStatus.FAILED
    ).count()
    rejected_events = RejectedEvent.objects.count()
    furnace_events = closed_lost + rejected_sites + failed_outreach + rejected_events
    return CompanyKitchenSnapshot(
        total_leads=total_leads,
        in_progress=in_progress,
        completed=completed,
        furnace_events=furnace_events,
        ready_for_review=ready_for_review,
        approved_sites=approved_sites,
        successful_outreach=successful_outreach,
        failed_outreach=failed_outreach,
        closed_lost=closed_lost,
        rejected_sites=rejected_sites,
        rejected_events=rejected_events,
    )


def recent_lead_dishes(limit: int = 6) -> tuple[RecentLeadDish, ...]:
    """Return recent persisted leads for the kitchen output table."""
    labels = dict(PipelineState.choices)
    rows = Lead.objects.order_by("-last_activity_at", "-id")[:limit]
    dishes = []
    for lead in rows:
        if lead.status == PipelineState.RELEASED:
            disposition = "completed"
        elif lead.status == PipelineState.CLOSED_LOST:
            disposition = "rejected"
        elif lead.status in (
            PipelineState.REPLIED,
            PipelineState.SCHEDULED,
            PipelineState.QUOTED,
            PipelineState.WON,
            PipelineState.INVOICED,
            PipelineState.PAID_PENDING_VERIFICATION,
            PipelineState.PAYMENT_VERIFIED,
        ):
            disposition = "review"
        else:
            disposition = "cooking"
        dishes.append(
            RecentLeadDish(
                lead_id=lead.pk,
                company_name=lead.company_name,
                status=lead.status,
                status_label=labels.get(lead.status, lead.status),
                quality_score=lead.researched_score,
                last_activity_at=lead.last_activity_at,
                disposition=disposition,
            )
        )
    return tuple(dishes)


def integration_readiness() -> tuple[IntegrationReadiness, ...]:
    """Report deployment truth without exposing or persisting credentials."""
    return tuple(
        IntegrationReadiness(
            name=item.name,
            configured=item.configured,
            purpose=item.purpose,
        )
        for item in runtime_requirements()
    )
