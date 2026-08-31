from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from dashboard.models import (
    Deal,
    Email,
    EmailBounce,
    InboundEventType,
    Invoice,
    Lead,
    NotificationEventType,
    PipelineState,
    ProcessedEvent,
    RejectedEvent,
    SitePage,
    SiteProject,
    SiteReviewState,
)
from dashboard.services.errors import ActionRejected, ValidationRejected
from dashboard.services.money import PaymentService
from dashboard.services.notifications import NotificationService
from dashboard.services.pipeline_state import PipelineStateMachine


@dataclass(frozen=True)
class EventIntakeOutcome:
    accepted: bool
    duplicate: bool = False
    rejection_reason: str | None = None


def _timestamp(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise ValidationRejected("event_timestamp must be an ISO-8601 timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed.astimezone(dt_timezone.utc)


def _validate(payload: dict) -> tuple[str, str, int, datetime]:
    event_id = str(payload.get("event_id", "")).strip()
    if not 1 <= len(event_id) <= 128:
        raise ValidationRejected("event_id must contain 1 to 128 characters")
    event_type = str(payload.get("event_type", "")).strip()
    if event_type not in InboundEventType.values:
        raise ValidationRejected(f"unknown event_type {event_type!r}")
    try:
        lead_id = int(payload.get("lead_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationRejected("lead_id must identify an existing Lead") from exc
    return event_id, event_type, lead_id, _timestamp(payload.get("event_timestamp"))


def _email_for_event(lead: Lead, payload: dict) -> Email | None:
    email_id = payload.get("email_id")
    if email_id is not None:
        try:
            return Email.objects.get(pk=int(email_id), lead_id=lead.id)
        except (Email.DoesNotExist, TypeError, ValueError) as exc:
            raise ValidationRejected("email_id does not identify an email for this Lead") from exc
    return Email.objects.filter(lead_id=lead.id).order_by("-sent_at", "-id").first()


def _advance_activity(lead: Lead, candidate: datetime) -> None:
    lead.refresh_from_db(fields=["last_activity_at"])
    if candidate > lead.last_activity_at:
        Lead.objects.filter(pk=lead.pk).update(last_activity_at=candidate)
        lead.last_activity_at = candidate


def _record_rejection(payload: dict, reason: str) -> None:
    raw_id = payload.get("event_id")
    raw_type = payload.get("event_type")
    raw_lead = payload.get("lead_id")
    try:
        reported_lead_id = int(raw_lead) if raw_lead is not None else None
    except (TypeError, ValueError):
        reported_lead_id = None
    RejectedEvent.objects.create(
        event_id=str(raw_id)[:500] if raw_id is not None else None,
        event_type=str(raw_type)[:500] if raw_type is not None else None,
        reported_lead_id=reported_lead_id,
        payload=payload,
        rejection_reason=reason[:1000],
    )


class EventIntake:
    @classmethod
    def handle(cls, payload: dict) -> EventIntakeOutcome:
        try:
            event_id, event_type, lead_id, event_timestamp = _validate(payload)
            if not Lead.objects.filter(pk=lead_id).exists():
                raise ValidationRejected("lead_id does not identify an existing Lead")
            return cls._handle_valid(
                payload=payload,
                event_id=event_id,
                event_type=event_type,
                lead_id=lead_id,
                event_timestamp=event_timestamp,
            )
        except (ValidationRejected, ActionRejected) as exc:
            with transaction.atomic():
                _record_rejection(payload, str(exc))
            return EventIntakeOutcome(False, rejection_reason=str(exc))

    @staticmethod
    @transaction.atomic
    def _handle_valid(
        *,
        payload: dict,
        event_id: str,
        event_type: str,
        lead_id: int,
        event_timestamp: datetime,
    ) -> EventIntakeOutcome:
        lead = Lead.objects.select_for_update().get(pk=lead_id)
        claimed, created = ProcessedEvent.objects.get_or_create(
            event_id=event_id,
            defaults={"event_type": event_type, "lead": lead},
        )
        if not created:
            return EventIntakeOutcome(True, duplicate=True)

        if event_type in {
            InboundEventType.EMAIL_OPENED,
            InboundEventType.EMAIL_CLICKED,
            InboundEventType.PROSPECT_REPLIED,
        }:
            email = _email_for_event(lead, payload)
            if email is None:
                raise ValidationRejected("This Lead has no email row for the event")
            if event_type == InboundEventType.PROSPECT_REPLIED:
                # The mapped transition is part of the event; if illegal the outer
                # transaction rolls back the engagement timestamp and claim too.
                PipelineStateMachine.request_from_event(
                    lead_id=lead.id,
                    event_type=event_type,
                    event_id=event_id,
                )
                email.reply_at = event_timestamp
                email.save(update_fields=["reply_at"])
                excerpt = str(payload.get("reply_excerpt") or payload.get("body") or "")[:500]
                NotificationService.generate(
                    event_id=event_id,
                    event_type=NotificationEventType.PROSPECT_REPLIED,
                    lead=lead,
                    payload={"excerpt": excerpt},
                )
            elif event_type == InboundEventType.EMAIL_OPENED:
                email.opened_at = event_timestamp
                email.save(update_fields=["opened_at"])
            else:
                email.clicked_at = event_timestamp
                email.save(update_fields=["clicked_at"])
            _advance_activity(lead, event_timestamp)
            return EventIntakeOutcome(True)

        if event_type == InboundEventType.EMAIL_BOUNCED:
            address = str(payload.get("contact_email") or lead.contact_email or "").strip()
            if not address:
                raise ValidationRejected("email_bounced requires contact_email")
            EmailBounce.objects.create(
                lead=lead,
                contact_email=address,
                reason=str(payload.get("reason") or "")[:500] or None,
                occurred_at=event_timestamp,
            )
            lead.manual_review_flag = True
            lead.save(update_fields=["manual_review_flag"])
            NotificationService.generate(
                event_id=event_id,
                event_type=NotificationEventType.COMPLIANCE_EVENT,
                lead=lead,
                payload={"event": "email_bounced", "reason": str(payload.get("reason") or "bounce")[:500]},
            )
            _advance_activity(lead, event_timestamp)
            return EventIntakeOutcome(True)

        if event_type == InboundEventType.UNSUBSCRIBED:
            lead.unsubscribed_at = event_timestamp
            lead.save(update_fields=["unsubscribed_at"])
            email = _email_for_event(lead, payload)
            if email is not None:
                email.unsubscribed = True
                email.save(update_fields=["unsubscribed"])
            NotificationService.generate(
                event_id=event_id,
                event_type=NotificationEventType.COMPLIANCE_EVENT,
                lead=lead,
                payload={"event": "unsubscribed", "reason": "prospect unsubscribed"},
            )
            _advance_activity(lead, event_timestamp)
            return EventIntakeOutcome(True)

        if event_type == InboundEventType.PAYMENT_RECEIVED:
            try:
                deal_id = int(payload.get("deal_id"))
                amount = int(payload.get("amount"))
            except (TypeError, ValueError) as exc:
                raise ValidationRejected("payment_received requires integer deal_id and amount") from exc
            try:
                deal = Deal.objects.select_for_update().select_related("lead").get(
                    pk=deal_id,
                    lead_id=lead.id,
                )
            except Deal.DoesNotExist as exc:
                raise ValidationRejected("deal_id does not resolve to this Lead") from exc
            payment, anomaly = PaymentService.record_received(
                deal=deal,
                event_id=event_id,
                amount_usd=amount,
                paid_date=event_timestamp.date(),
            )
            invoice = Invoice.objects.filter(deal_id=deal.pk).first()
            NotificationService.generate(
                event_id=event_id,
                event_type=NotificationEventType.PAYMENT_RECEIVED,
                lead=lead,
                payload={
                    "amount_usd": payment.amount_usd,
                    "invoice_amount": invoice.amount if invoice else None,
                },
            )
            if anomaly:
                NotificationService.generate(
                    event_id=(event_id[:119] + ":anomaly"),
                    event_type=NotificationEventType.COMPLIANCE_EVENT,
                    lead=lead,
                    payload={"event": "payment_anomaly", "reason": anomaly[:500]},
                )
            _advance_activity(lead, event_timestamp)
            return EventIntakeOutcome(True)

        if event_type == InboundEventType.SITE_GENERATION_FINISHED:
            preview_url = str(payload.get("preview_url") or "").strip()
            try:
                page_count = int(payload.get("page_count"))
            except (TypeError, ValueError) as exc:
                raise ValidationRejected("site_generation_finished requires page_count") from exc
            if not preview_url or not 0 <= page_count <= 200:
                raise ValidationRejected("site generation requires a preview_url and page_count from 0 to 200")
            site_id = payload.get("site_project_id")
            if site_id is not None:
                try:
                    site = SiteProject.objects.select_for_update().get(pk=int(site_id), lead_id=lead.id)
                except (SiteProject.DoesNotExist, TypeError, ValueError) as exc:
                    raise ValidationRejected("site_project_id does not resolve to this Lead") from exc
            else:
                site = SiteProject.objects.create(lead=lead)
            site.preview_url = preview_url
            site.page_count = page_count
            site.generated_at = event_timestamp
            site.review_state = SiteReviewState.READY_FOR_REVIEW
            site.rejection_reason = None
            site.save(
                update_fields=[
                    "preview_url",
                    "page_count",
                    "generated_at",
                    "review_state",
                    "rejection_reason",
                ]
            )
            pages = payload.get("pages") or []
            if isinstance(pages, list):
                for index, text in enumerate(pages[:200]):
                    SitePage.objects.update_or_create(
                        site_project=site,
                        page_index=index,
                        defaults={"text_content": str(text)},
                    )
            NotificationService.generate(
                event_id=event_id,
                event_type=NotificationEventType.SITE_READY,
                lead=lead,
                payload={"site_project_id": site.id, "preview_url": site.preview_url, "page_count": page_count},
            )
            _advance_activity(lead, event_timestamp)
            return EventIntakeOutcome(True)

        raise ValidationRejected(f"event_type {event_type!r} has no handler")
