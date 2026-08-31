from __future__ import annotations

from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from dashboard.adapter import get_pipeline_adapter
from dashboard.models import AuditActionType, Operator, SiteProject, SiteReviewState
from dashboard.services.audit import AuditLogger
from dashboard.services.authz import Action, Authz
from dashboard.services.confirmation import consume_confirmation
from dashboard.services.errors import ValidationRejected


class SiteReviewGate:
    @staticmethod
    def approve(*, site_id: int, operator: Operator, session, confirmation_token: str) -> SiteProject:
        Authz.check(operator, Action.SITE_APPROVE)
        consume_confirmation(session, token=confirmation_token, action="site.approve", target_id=site_id)
        with transaction.atomic():
            site = SiteProject.objects.select_for_update().get(pk=site_id)
            if site.review_state != SiteReviewState.READY_FOR_REVIEW:
                raise ValidationRejected(
                    "Site approval is available only from Ready_For_Review.",
                    target_type="siteproject",
                    target_id=site.id,
                    before_snapshot={"review_state": site.review_state},
                )
            before = site.review_state
            site.review_state = SiteReviewState.APPROVED
            site.approved_at = timezone.now()
            site.rejection_reason = None
            site.save(update_fields=["review_state", "approved_at", "rejection_reason"])
            AuditLogger.record(
                operator,
                AuditActionType.SITE_APPROVAL,
                site,
                {"review_state": before},
                {"review_state": site.review_state, "approved_at": site.approved_at.isoformat()},
            )
            return site

    @staticmethod
    def reject(
        *,
        site_id: int,
        operator: Operator,
        session,
        confirmation_token: str,
        reason: str,
    ):
        Authz.check(operator, Action.SITE_REJECT)
        reason = (reason or "").strip()
        if not 10 <= len(reason) <= 1000:
            raise ValidationRejected("Rejection reason must contain 10 to 1000 characters.")
        consume_confirmation(session, token=confirmation_token, action="site.reject", target_id=site_id)
        with transaction.atomic():
            site = SiteProject.objects.select_for_update().get(pk=site_id)
            if site.review_state != SiteReviewState.READY_FOR_REVIEW:
                raise ValidationRejected(
                    "Site rejection is available only from Ready_For_Review.",
                    target_type="siteproject",
                    target_id=site.id,
                    before_snapshot={"review_state": site.review_state},
                )
            before = site.review_state
            site.review_state = SiteReviewState.REJECTED
            site.rejection_reason = reason
            site.save(update_fields=["review_state", "rejection_reason"])
            AuditLogger.record(
                operator,
                AuditActionType.SITE_REJECTION,
                site,
                {"review_state": before},
                {"review_state": site.review_state, "rejection_reason": reason},
            )
            lead_id = site.lead_id

        # Regeneration is a network boundary and therefore happens after commit.
        key = uuid4()
        result = get_pipeline_adapter().generate_site_preview(lead_id=lead_id, idempotency_key=key)
        return site, result, key

    @staticmethod
    def assert_preview_link_permitted(*, site_project_id: int | None, clearance_timestamp) -> None:
        if site_project_id is None:
            return
        site = SiteProject.objects.get(pk=site_project_id)
        if site.review_state != SiteReviewState.APPROVED or site.approved_at is None:
            raise ValidationRejected("A preview link may be sent only after site approval.")
        if site.approved_at > clearance_timestamp:
            raise ValidationRejected("The site was not approved when this outreach was cleared.")
