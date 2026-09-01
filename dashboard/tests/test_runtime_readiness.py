from types import SimpleNamespace

import pytest
from django.test import override_settings

from dashboard.checks import check_live_runtime_configuration
from dashboard.models import NotificationChannel
from dashboard.services.runtime_readiness import (
    company_is_ready,
    live_configuration_failures,
    runtime_requirements,
)
from dashboard.tasks import _send_channel


LIVE_SETTINGS = {
    "DEBUG": False,
    "SECRET_KEY": "production-test-secret",
    "PIPELINE_ADAPTER_MODE": "live",
    "PIPELINE_EVENT_SECRET": "event-secret",
    "GOOGLE_MAPS_API_KEY": "maps-key",
    "SERPAPI_API_KEY": "search-key",
    "OUTREACH_EMAIL": "sender@example.com",
    "OUTREACH_PHONE": "555-0100",
    "YAHOO_SMTP_HOST": "smtp.mail.yahoo.com",
    "YAHOO_SMTP_PORT": 465,
    "YAHOO_SMTP_USERNAME": "sender@example.com",
    "YAHOO_SMTP_APP_PASSWORD": "app-password",
    "STRIPE_SECRET_KEY": "stripe-secret",
    "STRIPE_WEBHOOK_SECRET": "stripe-webhook-secret",
    "CELERY_BROKER_URL": "rediss://queue.example.com:6379/0",
    "CELERY_RESULT_BACKEND": "rediss://queue.example.com:6379/0",
}


@override_settings(**LIVE_SETTINGS)
def test_live_runtime_readiness_requires_every_external_boundary():
    assert len(runtime_requirements()) == 8
    assert all(item.configured for item in runtime_requirements())
    assert live_configuration_failures() == ()
    assert company_is_ready() is True
    assert check_live_runtime_configuration(None) == []


@override_settings(**{**LIVE_SETTINGS, "YAHOO_SMTP_APP_PASSWORD": ""})
def test_deploy_check_fails_closed_without_exposing_secret_values():
    failures = live_configuration_failures()
    assert "Yahoo Business SMTP is not configured" in failures
    assert company_is_ready() is False

    errors = check_live_runtime_configuration(None)
    assert len(errors) == 1
    assert errors[0].id == "dashboard.E900"
    message = str(errors[0].msg)
    assert "Yahoo Business SMTP is not configured" in message
    assert "app-password" not in message


@override_settings(**LIVE_SETTINGS)
def test_local_celery_url_is_not_considered_production_ready():
    with override_settings(
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_RESULT_BACKEND="redis://localhost:6379/0",
    ):
        queue = next(item for item in runtime_requirements() if item.key == "queue")
        assert queue.configured is False
        assert "Background queue is not configured" in live_configuration_failures()


def test_notification_email_uses_hardened_yahoo_boundary(monkeypatch):
    sent = []

    class FakeClient:
        def send(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr("dashboard.tasks.get_yahoo_smtp_client", lambda: FakeClient())
    notification = SimpleNamespace(
        id=42,
        operator=SimpleNamespace(
            registered_email="owner@example.com",
            email="",
            slack_webhook_target="",
        ),
        event_type="site_ready",
        payload={},
        deep_link="/deals/7/",
        get_event_type_display=lambda: "Site ready",
    )

    _send_channel(notification, NotificationChannel.EMAIL)

    assert sent == [
        {
            "to": "owner@example.com",
            "subject": "B2B Deal Room: Site ready",
            "body": "Site preview is ready for review.\n/deals/7/",
            "idempotency_key": "notification-42-email",
        }
    ]
