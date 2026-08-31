from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from dashboard.adapter.gmail_delivery import DeliveryReceipt
from dashboard.adapter.gmail_oauth import GmailOAuthConfig, GmailOAuthError, GmailOAuthTokenProvider
from dashboard.adapter.pipeline import LivePipelineAdapter


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_gmail_oauth_requires_all_private_credentials(monkeypatch):
    provider = GmailOAuthTokenProvider(
        GmailOAuthConfig(client_id="", client_secret="", refresh_token="")
    )
    monkeypatch.setattr(
        "dashboard.adapter.gmail_oauth.urlopen",
        lambda *args, **kwargs: pytest.fail("network must not be touched without configuration"),
    )

    with pytest.raises(GmailOAuthError, match="not configured"):
        provider()


def test_gmail_oauth_refreshes_once_then_reuses_access_token(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout, request.data.decode("utf-8")))
        return FakeResponse({"access_token": "access_123", "expires_in": 3600})

    monkeypatch.setattr("dashboard.adapter.gmail_oauth.urlopen", fake_urlopen)
    provider = GmailOAuthTokenProvider(
        GmailOAuthConfig(
            client_id="client_123",
            client_secret="secret_123",
            refresh_token="refresh_123",
            timeout_seconds=3,
        )
    )

    assert provider() == "access_123"
    assert provider() == "access_123"
    assert len(calls) == 1
    url, timeout, body = calls[0]
    assert url == "https://oauth2.googleapis.com/token"
    assert timeout == 3
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh_123" in body


@dataclass
class FakeGmailClient:
    calls: list[dict]

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return DeliveryReceipt(message_id="msg_live_1", thread_id="thr_live_1")


def test_live_sales_email_uses_gmail_and_returns_provider_receipt(monkeypatch, settings):
    settings.OUTREACH_EMAIL = "sender@example.com"
    calls = []
    client = FakeGmailClient(calls)
    monkeypatch.setattr("dashboard.adapter.pipeline.gmail_oauth_configured", lambda: True)
    monkeypatch.setattr("dashboard.adapter.pipeline.get_gmail_delivery_client", lambda: client)

    result = LivePipelineAdapter().send_prospect_email(
        lead_id=1,
        to_email="buyer@example.com",
        subject="Invoice ready",
        body="Pay here: https://invoice.stripe.test/i/1",
        idempotency_key="ignored-by-direct-live-adapter-test",
    )

    assert result.status == "success"
    assert result.payload == {"message_id": "msg_live_1", "thread_id": "thr_live_1"}
    assert calls == [
        {
            "to": "buyer@example.com",
            "subject": "Invoice ready",
            "body": "Pay here: https://invoice.stripe.test/i/1",
        }
    ]


def test_live_sales_email_fails_closed_when_gmail_is_not_configured(monkeypatch, settings):
    settings.OUTREACH_EMAIL = "sender@example.com"
    monkeypatch.setattr("dashboard.adapter.pipeline.gmail_oauth_configured", lambda: False)
    monkeypatch.setattr(
        "dashboard.adapter.pipeline.get_gmail_delivery_client",
        lambda: pytest.fail("Gmail client must not be created when OAuth is incomplete"),
    )

    result = LivePipelineAdapter().send_prospect_email(
        lead_id=1,
        to_email="buyer@example.com",
        subject="Invoice ready",
        body="Pay here",
        idempotency_key="ignored-by-direct-live-adapter-test",
    )

    assert result.status == "failure"
    assert "not configured" in result.failure_reason


def test_live_delivery_email_contains_exact_archive_link(monkeypatch, settings):
    settings.OUTREACH_EMAIL = "sender@example.com"
    settings.OUTREACH_SENDER_NAME = "Web Team"
    calls = []
    client = FakeGmailClient(calls)
    monkeypatch.setattr("dashboard.adapter.pipeline.gmail_oauth_configured", lambda: True)
    monkeypatch.setattr("dashboard.adapter.pipeline.get_gmail_delivery_client", lambda: client)

    result = LivePipelineAdapter().send_delivery_email(
        deal_id=1,
        to_email="buyer@example.com",
        archive_link="https://delivery.example/final.zip",
        idempotency_key="ignored-by-direct-live-adapter-test",
    )

    assert result.status == "success"
    assert len(calls) == 1
    assert "https://delivery.example/final.zip" in calls[0]["body"]
    assert calls[0]["to"] == "buyer@example.com"
