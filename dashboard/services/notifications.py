from __future__ import annotations

from django.db import transaction

from dashboard.models import (
    Notification,
    NotificationChannel,
    NotificationEventType,
    NotificationPreference,
    Operator,
)


class NotificationService:
    @staticmethod
    def _preference(operator: Operator, event_type: str) -> tuple[bool, tuple[str, ...]]:
        pref = NotificationPreference.objects.filter(
            operator=operator,
            event_type=event_type,
        ).first()
        # Requirement 9.7: absent preference means subscribed with both channels.
        if pref is None:
            return True, (NotificationChannel.EMAIL, NotificationChannel.SLACK)
        channels: list[str] = []
        if pref.email_enabled:
            channels.append(NotificationChannel.EMAIL)
        if pref.slack_enabled:
            channels.append(NotificationChannel.SLACK)
        return pref.subscribed, tuple(channels)

    @classmethod
    def generate(
        cls,
        *,
        event_id: str,
        event_type: str,
        lead,
        payload: dict,
    ) -> list[Notification]:
        if event_type not in NotificationEventType.values:
            raise ValueError(f"unsupported notification event_type {event_type!r}")
        created: list[Notification] = []
        recipients = Operator.objects.filter(
            is_active=True,
            role__in=(Operator.Role.AGENT, Operator.Role.ADMIN),
        ).order_by("id")
        for operator in recipients:
            subscribed, channels = cls._preference(operator, event_type)
            if not subscribed:
                continue
            notification, was_created = Notification.objects.get_or_create(
                event_id=event_id,
                operator=operator,
                defaults={
                    "lead": lead,
                    "event_type": event_type,
                    "payload": payload,
                    "deep_link": f"/deals/{lead.id}/",
                },
            )
            if not was_created:
                continue
            created.append(notification)
            for channel in channels:
                transaction.on_commit(
                    lambda nid=notification.id, ch=str(channel): cls._enqueue(nid, ch)
                )
        return created

    @staticmethod
    def _enqueue(notification_id: int, channel: str) -> None:
        # Import lazily so model/service imports never require a live broker.
        from dashboard.tasks import deliver_notification

        try:
            deliver_notification.apply_async(args=(notification_id, channel))
        except Exception:
            # The durable Notification row already exists; a broker outage must
            # not roll back the triggering business event after commit.
            return

    @staticmethod
    @transaction.atomic
    def set_preference(
        *,
        operator: Operator,
        event_type: str,
        subscribed: bool,
        email_enabled: bool,
        slack_enabled: bool,
    ) -> NotificationPreference:
        if event_type not in NotificationEventType.values:
            raise ValueError("unknown notification event type")
        if slack_enabled and not (operator.slack_webhook_url or "").strip():
            raise ValueError("A Slack webhook target is required before Slack can be enabled.")
        pref, _ = NotificationPreference.objects.update_or_create(
            operator=operator,
            event_type=event_type,
            defaults={
                "subscribed": bool(subscribed),
                "email_enabled": bool(email_enabled),
                "slack_enabled": bool(slack_enabled),
            },
        )
        return pref
