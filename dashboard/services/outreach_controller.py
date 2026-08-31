from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from dashboard.adapter import AdapterResult, get_pipeline_adapter
from dashboard.models import (
    AdapterInvocation,
    AdapterOperationName,
    AuditActionType,
    Call,
    CallOutcome,
    Email,
    HistoryActorKind,
    Lead,
    Operator,
    OutreachChannel,
    OutreachRequest,
    OutreachRequestStatus,
    PipelineState,
    PipelineStateHistory,
    SiteProject,
)
from dashboard.services.audit import AuditLogger
from dashboard.services.authz import Action, Authz
from dashboard.services.compliance import ComplianceGuard
from dashboard.services.confirmation import consume_confirmation
from dashboard.services.errors import ComplianceRejected, ValidationRejected
from dashboard.services.notifications import NotificationService


@dataclass(frozen=True)
class OutreachOutcome:
    request: OutreachRequest
    adapter_result: AdapterResult | None
    recorded_row_id: int | None = None
    duplicate_replay: bool = False


def _site_reference_from_body(lead_id: int, body: str, site_project_id: int | None) -> int | None:
    # A UI-supplied site id is only meaningful when that exact preview URL is
    # actually present in the outgoing body. This prevents an unrelated latest
    # draft/rejected site from blocking an email that contains no preview link.
    if site_project_id is not None:
        explicit = SiteProject.objects.filter(pk=site_project_id, lead_id=lead_id).first()
        if explicit is None:
            raise ComplianceRejected("The selected Site_Project does not belong to this Lead.")
        if explicit.preview_url and explicit.preview_url in body:
            return explicit.id

    for site in SiteProject.objects.filter(lead_id=lead_id).exclude(preview_url__isnull=True):
        if site.preview_url and site.preview_url in body:
            return site.id
    pattern = (settings.PREVIEW_HOST_PATTERN or "").strip()
    if pattern and pattern in body:
        raise ComplianceRejected("A preview-host link must resolve to this Lead's approved Site_Project.")
    return None


def _assert_request_identity(request: OutreachRequest, *, lead_id: int, channel: str) -> None:
    if request.lead_id != lead_id or request.channel != channel:
        raise ValidationRejected(
            "Outreach request id belongs to a different Lead or channel and cannot be replayed."
        )


def _original_email_arguments(request: OutreachRequest) -> dict:
    invocation = (
        AdapterInvocation.objects.filter(
            operation_name=AdapterOperationName.SEND_PROSPECT_EMAIL,
            idempotency_key=request.id,
        )
        .order_by("id")
        .first()
    )
    if invocation is None or not isinstance(invocation.arguments, dict):
        raise ValidationRejected("Email retry has no original adapter invocation to bind to safely.")
    arguments = invocation.arguments
    try:
        original_lead_id = int(arguments.get("lead_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationRejected("Original email invocation has invalid Lead identity.") from exc
    if original_lead_id != request.lead_id:
        raise ValidationRejected("Original email invocation does not belong to this outreach request Lead.")
    return arguments


def _mark_contacted_with_audit(lead: Lead, actor: Operator, audit, occurred_at) -> None:
    lead.refresh_from_db(fields=["status", "state_version", "last_activity_at"])
    if lead.status != PipelineState.NEW_LEAD:
        if occurred_at > lead.last_activity_at:
            Lead.objects.filter(pk=lead.pk).update(last_activity_at=occurred_at)
        return
    old_version = lead.state_version
    changed = Lead.objects.filter(
        pk=lead.pk,
        status=PipelineState.NEW_LEAD,
        state_version=old_version,
    ).update(
        status=PipelineState.CONTACTED,
        state_version=F("state_version") + 1,
        last_activity_at=occurred_at,
    )
    if changed == 1:
        PipelineStateHistory.objects.create(
            lead=lead,
            from_state=PipelineState.NEW_LEAD,
            to_state=PipelineState.CONTACTED,
            occurred_at=occurred_at,
            actor=actor,
            actor_kind=HistoryActorKind.OPERATOR,
            audit_entry=audit,
        )


class OutreachController:
    @staticmethod
    def _validate_email_content(subject: str, body: str) -> tuple[str, str]:
        subject = (subject or "").strip()
        body = (body or "").strip()
        if not 1 <= len(subject) <= 200:
            raise ValidationRejected("Email subject must contain 1 to 200 characters.")
        if not 1 <= len(body) <= 10_000:
            raise ValidationRejected("Email body must contain 1 to 10,000 characters.")
        return subject, body

    @classmethod
    def send_email(
        cls,
        *,
        lead_id: int,
        operator: Operator,
        session,
        confirmation_token: str,
        subject: str,
        body: str,
        site_project_id: int | None = None,
        duplicate_confirmation_token: str | None = None,
        outreach_request_id: UUID | None = None,
    ) -> OutreachOutcome:
        Authz.check(operator, Action.OUTREACH_SEND)
        subject, body = cls._validate_email_content(subject, body)
        consume_confirmation(session, token=confirmation_token, action="outreach.send", target_id=lead_id)

        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(pk=lead_id)
            site_project_id = _site_reference_from_body(lead.id, body, site_project_id)
            cleared = ComplianceGuard.clear(
                lead=lead,
                channel=OutreachChannel.EMAIL,
                at=timezone.now(),
                site_project_id=site_project_id,
            )
            if cleared.duplicate_lead_ids:
                if not duplicate_confirmation_token:
                    raise ComplianceRejected(
                        "Duplicate contact detected; a second confirmation is required.",
                        target_type="lead",
                        target_id=lead.id,
                        before_snapshot={"duplicate_lead_ids": list(cleared.duplicate_lead_ids)},
                    )
                consume_confirmation(
                    session,
                    token=duplicate_confirmation_token,
                    action="outreach.duplicate",
                    target_id=lead.id,
                )
            request_id = outreach_request_id or uuid4()
            existing = OutreachRequest.objects.filter(pk=request_id).first()
            if existing is not None:
                _assert_request_identity(existing, lead_id=lead.id, channel=OutreachChannel.EMAIL)
                row = Email.objects.filter(outreach_request_id=request_id).first()
                return OutreachOutcome(existing, None, row.id if row else None, duplicate_replay=True)
            request = OutreachRequest.objects.create(
                id=request_id,
                lead=lead,
                channel=OutreachChannel.EMAIL,
                status=OutreachRequestStatus.PENDING,
                clearance_timestamp=cleared.evaluated_at,
            )
            to_email = lead.contact_email

        result = get_pipeline_adapter().send_prospect_email(
            lead_id=lead_id,
            to_email=to_email,
            subject=subject,
            body=body,
            idempotency_key=request.id,
        )
        return cls._record_email_result(
            request_id=request.id,
            operator=operator,
            subject=subject,
            body=body,
            site_project_id=site_project_id,
            result=result,
        )

    @staticmethod
    def _record_email_result(
        *,
        request_id: UUID,
        operator: Operator,
        subject: str,
        body: str,
        site_project_id: int | None,
        result: AdapterResult,
    ) -> OutreachOutcome:
        with transaction.atomic():
            request = OutreachRequest.objects.select_for_update().select_related("lead").get(pk=request_id)
            if Email.objects.filter(outreach_request_id=request.id).exists():
                row = Email.objects.get(outreach_request_id=request.id)
                return OutreachOutcome(request, result, row.id, duplicate_replay=True)
            if result.status != "success":
                request.status = OutreachRequestStatus.FAILED
                request.failure_reason = result.failure_reason
                request.save(update_fields=["status", "failure_reason"])
                return OutreachOutcome(request, result)

            sent_at = timezone.now()
            late = bool(
                request.lead.unsubscribed_at
                and request.clearance_timestamp < request.lead.unsubscribed_at <= sent_at
            )
            row = Email.objects.create(
                lead=request.lead,
                outreach_request_id=request.id,
                subject=subject,
                body=body,
                site_project_id=site_project_id,
                clearance_timestamp=request.clearance_timestamp,
                late_opt_out_marker=late,
                sent_at=sent_at,
            )
            request.status = OutreachRequestStatus.SUCCEEDED
            request.failure_reason = None
            request.save(update_fields=["status", "failure_reason"])
            audit = AuditLogger.record(
                operator,
                AuditActionType.OUTREACH_SEND,
                row,
                None,
                {"lead_id": request.lead_id, "channel": "email", "outreach_request_id": str(request.id)},
                occurred_at=sent_at,
            )
            _mark_contacted_with_audit(request.lead, operator, audit, sent_at)
            if late:
                NotificationService.generate(
                    event_id=f"late-email-{row.id}",
                    event_type="compliance_event",
                    lead=request.lead,
                    payload={"event": "late_opt_out", "reason": "email cleared before a later unsubscribe"},
                )
            return OutreachOutcome(request, result, row.id)

    @classmethod
    def retry_email(
        cls,
        *,
        outreach_request_id: UUID,
        operator: Operator,
        subject: str,
        body: str,
        site_project_id: int | None = None,
    ) -> OutreachOutcome:
        Authz.check(operator, Action.OUTREACH_SEND)
        subject, body = cls._validate_email_content(subject, body)
        with transaction.atomic():
            request = OutreachRequest.objects.select_for_update().select_related("lead").get(pk=outreach_request_id)
            if request.channel != OutreachChannel.EMAIL:
                raise ValidationRejected("This outreach request is not an email.")
            row = Email.objects.filter(outreach_request_id=request.id).first()
            if row is not None:
                return OutreachOutcome(request, None, row.id, duplicate_replay=True)
            if request.status != OutreachRequestStatus.FAILED:
                raise ValidationRejected(
                    "Only a definitively failed email request can be retried automatically."
                )

            original = _original_email_arguments(request)
            original_subject = str(original.get("subject") or "")
            original_body = str(original.get("body") or "")
            original_to = str(original.get("to_email") or "").strip().lower()
            if subject != original_subject or body != original_body:
                raise ValidationRejected(
                    "Email retry cannot change the original subject or body while reusing the same idempotency key."
                )
            current_to = (request.lead.contact_email or "").strip().lower()
            if not original_to or current_to != original_to:
                raise ValidationRejected(
                    "Lead email changed after the failed send; create a new outreach request instead of retrying the old one."
                )

            site_project_id = _site_reference_from_body(
                request.lead_id,
                original_body,
                site_project_id,
            )
            ComplianceGuard.clear(
                lead=request.lead,
                channel=OutreachChannel.EMAIL,
                at=timezone.now(),
                site_project_id=site_project_id,
            )
            request.status = OutreachRequestStatus.PENDING
            request.failure_reason = None
            request.save(update_fields=["status", "failure_reason"])

        result = get_pipeline_adapter().send_prospect_email(
            lead_id=request.lead_id,
            to_email=original_to,
            subject=original_subject,
            body=original_body,
            idempotency_key=request.id,
        )
        return cls._record_email_result(
            request_id=request.id,
            operator=operator,
            subject=original_subject,
            body=original_body,
            site_project_id=site_project_id,
            result=result,
        )

    @classmethod
    def submit_call(
        cls,
        *,
        lead_id: int,
        operator: Operator,
        session,
        confirmation_token: str,
        outcome: str,
        notes: str = "",
        duplicate_confirmation_token: str | None = None,
        outreach_request_id: UUID | None = None,
    ) -> OutreachOutcome:
        Authz.check(operator, Action.OUTREACH_SEND)
        if outcome not in CallOutcome.values:
            raise ValidationRejected("Call outcome must be answered, busy, or no-answer.")
        if len(notes or "") > 2000:
            raise ValidationRejected("Call notes may contain at most 2,000 characters.")
        consume_confirmation(session, token=confirmation_token, action="outreach.call", target_id=lead_id)

        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(pk=lead_id)
            cleared = ComplianceGuard.clear(lead=lead, channel=OutreachChannel.CALL, at=timezone.now())
            if cleared.duplicate_lead_ids:
                if not duplicate_confirmation_token:
                    raise ComplianceRejected("Duplicate contact detected; a second confirmation is required.")
                consume_confirmation(
                    session,
                    token=duplicate_confirmation_token,
                    action="outreach.duplicate",
                    target_id=lead.id,
                )
            request_id = outreach_request_id or uuid4()
            existing = OutreachRequest.objects.filter(pk=request_id).first()
            if existing:
                _assert_request_identity(existing, lead_id=lead.id, channel=OutreachChannel.CALL)
                row = Call.objects.filter(outreach_request_id=request_id).first()
                return OutreachOutcome(existing, None, row.id if row else None, duplicate_replay=True)
            request = OutreachRequest.objects.create(
                id=request_id,
                lead=lead,
                channel=OutreachChannel.CALL,
                status=OutreachRequestStatus.PENDING,
                clearance_timestamp=cleared.evaluated_at,
            )

        result = get_pipeline_adapter().log_outbound_call(
            lead_id=lead_id,
            outcome=outcome,
            notes=notes or "",
            idempotency_key=request.id,
        )
        with transaction.atomic():
            request = OutreachRequest.objects.select_for_update().get(pk=request.id)
            lead = Lead.objects.select_for_update().get(pk=request.lead_id)
            if result.status != "success":
                request.status = OutreachRequestStatus.FAILED
                request.failure_reason = result.failure_reason
                request.save(update_fields=["status", "failure_reason"])
                return OutreachOutcome(request, result)
            timestamp = timezone.now()
            attempt = (Call.objects.filter(lead_id=lead.id).aggregate(v=Max("attempt_number"))["v"] or 0) + 1
            if attempt > 20:
                raise ValidationRejected("Call attempt_number storage limit of 20 has been reached.")
            late = bool(
                lead.do_not_call_at
                and request.clearance_timestamp < lead.do_not_call_at <= timestamp
            )
            row = Call.objects.create(
                lead=lead,
                outreach_request_id=request.id,
                attempt_number=attempt,
                timestamp=timestamp,
                outcome=outcome,
                notes=notes or None,
                clearance_timestamp=request.clearance_timestamp,
                late_opt_out_marker=late,
            )
            request.status = OutreachRequestStatus.SUCCEEDED
            request.failure_reason = None
            request.save(update_fields=["status", "failure_reason"])
            audit = AuditLogger.record(
                operator,
                AuditActionType.OUTREACH_SEND,
                row,
                None,
                {"lead_id": lead.id, "channel": "call", "outreach_request_id": str(request.id)},
                occurred_at=timestamp,
            )
            _mark_contacted_with_audit(lead, operator, audit, timestamp)
            if late:
                NotificationService.generate(
                    event_id=f"late-call-{row.id}",
                    event_type="compliance_event",
                    lead=lead,
                    payload={"event": "late_do_not_call", "reason": "call cleared before a later do-not-call"},
                )
            return OutreachOutcome(request, result, row.id)

    @staticmethod
    @transaction.atomic
    def log_operator_call(
        *,
        lead_id: int,
        operator: Operator,
        outcome: str,
        notes: str = "",
    ) -> Call:
        Authz.check(operator, Action.OUTREACH_SEND)
        if outcome not in CallOutcome.values:
            raise ValidationRejected("Call outcome must be answered, busy, or no-answer.")
        if len(notes or "") > 2000:
            raise ValidationRejected("Call notes may contain at most 2,000 characters.")
        lead = Lead.objects.select_for_update().get(pk=lead_id)
        ComplianceGuard.clear(lead=lead, channel=OutreachChannel.CALL, at=timezone.now())
        attempt = (Call.objects.filter(lead_id=lead.id).aggregate(v=Max("attempt_number"))["v"] or 0) + 1
        if attempt > 20:
            raise ValidationRejected("Call attempt_number storage limit of 20 has been reached.")
        timestamp = timezone.now()
        row = Call.objects.create(
            lead=lead,
            outreach_request_id=None,
            attempt_number=attempt,
            timestamp=timestamp,
            outcome=outcome,
            notes=notes or None,
            clearance_timestamp=None,
        )
        audit = AuditLogger.record(
            operator,
            AuditActionType.OUTREACH_SEND,
            row,
            None,
            {"lead_id": lead.id, "channel": "operator_logged_call"},
            occurred_at=timestamp,
        )
        _mark_contacted_with_audit(lead, operator, audit, timestamp)
        return row
