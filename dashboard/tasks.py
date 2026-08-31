from __future__ import annotations

import json
from datetime import timedelta
from urllib import request as urllib_request

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from dashboard.models import (
    AuditActionType,
    AuditEntry,
    Call,
    Deal,
    DeliveryOutcome,
    Email,
    Notification,
    NotificationChannel,
    NotificationDelivery,
    OutreachRequest,
    OutreachRequestStatus,
    PipelineStateHistory,
    ProcessedEvent,
)


def _notification_text(notification: Notification) -> str:
    payload = notification.payload or {}
    if notification.event_type == "prospect_replied":
        excerpt = str(payload.get("excerpt", ""))[:500]
        return f"Prospect reply: {excerpt}\n{notification.deep_link}"
    if notification.event_type == "payment_received":
        return (
            f"Payment received: ${payload.get('amount_usd', '?')}"
            f" / invoice ${payload.get('invoice_amount', '?')}\n{notification.deep_link}"
        )
    if notification.event_type == "site_ready":
        return f"Site preview is ready for review.\n{notification.deep_link}"
    return f"Compliance event: {payload.get('reason', payload.get('event', 'update'))}\n{notification.deep_link}"


def _send_channel(notification: Notification, channel: str) -> None:
    operator = notification.operator
    text = _notification_text(notification)
    if channel == NotificationChannel.EMAIL:
        target = (operator.registered_email or operator.email or "").strip()
        if not target:
            raise RuntimeError("operator has no notification email")
        send_mail(
            subject=f"B2B Deal Room: {notification.get_event_type_display()}",
            message=text,
            from_email=settings.NOTIFICATION_EMAIL_FROM or None,
            recipient_list=[target],
            fail_silently=False,
        )
        return
    if channel == NotificationChannel.SLACK:
        target = (operator.slack_webhook_target or "").strip()
        if not target:
            raise RuntimeError("operator has no Slack webhook target")
        body = json.dumps({"text": text}).encode("utf-8")
        req = urllib_request.Request(
            target,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Slack returned HTTP {response.status}")
        return
    raise RuntimeError(f"unsupported notification channel {channel!r}")


@shared_task(bind=True, max_retries=3)
def deliver_notification(self, notification_id: int, channel: str):
    notification = Notification.objects.select_related("operator").get(pk=notification_id)
    attempt = min(int(self.request.retries) + 1, 4)
    try:
        _send_channel(notification, channel)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"[:500]
        NotificationDelivery.objects.update_or_create(
            notification=notification,
            channel=channel,
            defaults={
                "attempt_count": attempt,
                "outcome": DeliveryOutcome.FAILED,
                "failure_reason": reason,
                "last_attempt_at": timezone.now(),
            },
        )
        if attempt < 4:
            raise self.retry(exc=exc, countdown=60)
        return "failed"

    NotificationDelivery.objects.update_or_create(
        notification=notification,
        channel=channel,
        defaults={
            "attempt_count": attempt,
            "outcome": DeliveryOutcome.DELIVERED,
            "failure_reason": None,
            "last_attempt_at": timezone.now(),
        },
    )
    return "delivered"


@shared_task
def reconcile_outreach_reservations() -> int:
    cutoff = timezone.now() - timedelta(minutes=5)
    return OutreachRequest.objects.filter(
        status=OutreachRequestStatus.PENDING,
        reserved_at__lt=cutoff,
    ).update(
        status=OutreachRequestStatus.INDETERMINATE,
        failure_reason="submission outcome was not recorded within five minutes",
    )


@shared_task
def purge_processed_events() -> int:
    cutoff = timezone.now() - timedelta(days=180)
    deleted, _ = ProcessedEvent.objects.filter(received_at__lt=cutoff).delete()
    return deleted


def _max_non_null(values):
    values = [value for value in values if value is not None]
    return max(values) if values else None


@shared_task
def verify_last_activity_consistency() -> int:
    """Recompute Requirement 13.14 and repair drift from any future writer."""
    repaired = 0
    for lead in __import__("dashboard.models", fromlist=["Lead"]).Lead.objects.all().iterator():
        email_times = Email.objects.filter(lead_id=lead.id).aggregate(
            sent=Max("sent_at"), opened=Max("opened_at"), clicked=Max("clicked_at"), reply=Max("reply_at")
        )
        call_time = Call.objects.filter(lead_id=lead.id).aggregate(v=Max("timestamp"))["v"]
        history_time = PipelineStateHistory.objects.filter(lead_id=lead.id).aggregate(v=Max("occurred_at"))["v"]
        deal_id = Deal.objects.filter(lead_id=lead.id).values_list("deal_id", flat=True).first()
        target_q = Q(target_type="lead", target_id=lead.id)
        if deal_id is not None:
            target_q |= Q(target_type="deal", target_id=deal_id)
        audit_time = (
            AuditEntry.objects.filter(target_q)
            .exclude(action_type=AuditActionType.REJECTED_ACTION_ATTEMPT)
            .aggregate(v=Max("occurred_at"))["v"]
        )
        expected = _max_non_null(
            [
                email_times["sent"],
                email_times["opened"],
                email_times["clicked"],
                email_times["reply"],
                call_time,
                history_time,
                audit_time,
            ]
        )
        if expected is not None and lead.last_activity_at != expected:
            type(lead).objects.filter(pk=lead.pk).update(last_activity_at=expected)
            repaired += 1
    return repaired
