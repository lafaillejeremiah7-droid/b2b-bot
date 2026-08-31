from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from dashboard.adapter.pipeline import LivePipelineAdapter
from dashboard.adapter.yahoo_smtp import (
    YahooSMTPConfig,
    YahooSMTPDeliveryClient,
    YahooSMTPError,
    YahooSMTPReceipt,
)


class FakeSMTP:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.login_calls = []
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, message):
        self.messages.append(message)
        return {}


def test_yahoo_smtp_requires_private_credentials():
    client = YahooSMTPDeliveryClient(
        YahooSMTPConfig(
            host="smtp.mail.yahoo.com",
            port=465,
            username="",
            app_password="",
        )
    )
    with pytest.raises(YahooSMTPError, match="not configured"):
        client.send(
            to="buyer@example.com",
            subject="Invoice",
            body="Pay here",
            idempotency_key=uuid4(),
        )


def test_yahoo_smtp_uses_stable_message_id_and_business_mailbox():
    instances = []

    def factory(*args, **kwargs):
        smtp = FakeSMTP(*args, **kwargs)
        instances.append(smtp)
        return smtp

    key = uuid4()
    client = YahooSMTPDeliveryClient(
        YahooSMTPConfig(
            host="smtp.mail.yahoo.com",
            port=465,
            username="business@example.com",
            app_password="app-secret",
            use_ssl=True,
            timeout_seconds=12,
        ),
        sender_name="Web Team",
        smtp_ssl_factory=factory,
    )

    first = client.send(
        to="buyer@example.com",
        subject="Your invoice",
        body="Pay here: https://invoice.stripe.test/i/1",
        idempotency_key=key,
    )
    second = client.send(
        to="buyer@example.com",
        subject="Your invoice",
        body="Pay here: https://invoice.stripe.test/i/1",
        idempotency_key=key,
    )

    assert first.message_id == second.message_id
    assert first.message_id == f"<b2b-{key}@example.com>"
    assert len(instances) == 2
    for smtp in instances:
        assert smtp.args[:2] == ("smtp.mail.yahoo.com", 465)
        assert smtp.kwargs["timeout"] == 12
        assert smtp.login_calls == [("business@example.com", "app-secret")]
        message = smtp.messages[0]
        assert message["To"] == "buyer@example.com"
        assert message["From"] == "Web Team <business@example.com>"
        assert message["Message-ID"] == first.message_id
        assert "https://invoice.stripe.test/i/1" in message.get_content()


@dataclass
class FakeYahooClient:
    calls: list[dict]

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return YahooSMTPReceipt(message_id="<b2b-live@example.com>")


def test_live_sales_email_uses_yahoo_smtp(monkeypatch):
    calls = []
    client = FakeYahooClient(calls)
    monkeypatch.setattr("dashboard.adapter.pipeline.yahoo_smtp_configured", lambda: True)
    monkeypatch.setattr("dashboard.adapter.pipeline.get_yahoo_smtp_client", lambda: client)

    result = LivePipelineAdapter().send_prospect_email(
        lead_id=1,
        to_email="buyer@example.com",
        subject="Invoice ready",
        body="Pay here: https://invoice.stripe.test/i/1",
        idempotency_key="invoice-email-1",
    )

    assert result.status == "success"
    assert result.payload == {"message_id": "<b2b-live@example.com>"}
    assert calls == [
        {
            "to": "buyer@example.com",
            "subject": "Invoice ready",
            "body": "Pay here: https://invoice.stripe.test/i/1",
            "idempotency_key": "invoice-email-1",
        }
    ]


def test_live_sales_email_fails_closed_when_yahoo_not_configured(monkeypatch):
    monkeypatch.setattr("dashboard.adapter.pipeline.yahoo_smtp_configured", lambda: False)
    monkeypatch.setattr(
        "dashboard.adapter.pipeline.get_yahoo_smtp_client",
        lambda: pytest.fail("Yahoo client must not be created without configuration"),
    )

    result = LivePipelineAdapter().send_prospect_email(
        lead_id=1,
        to_email="buyer@example.com",
        subject="Invoice ready",
        body="Pay here",
        idempotency_key="invoice-email-1",
    )

    assert result.status == "failure"
    assert "Yahoo business SMTP sender is not configured" in result.failure_reason


def test_live_delivery_email_contains_archive_link(monkeypatch, settings):
    settings.OUTREACH_SENDER_NAME = "Web Team"
    calls = []
    client = FakeYahooClient(calls)
    monkeypatch.setattr("dashboard.adapter.pipeline.yahoo_smtp_configured", lambda: True)
    monkeypatch.setattr("dashboard.adapter.pipeline.get_yahoo_smtp_client", lambda: client)

    result = LivePipelineAdapter().send_delivery_email(
        deal_id=1,
        to_email="buyer@example.com",
        archive_link="https://delivery.example/final.zip",
        idempotency_key="delivery-email-1",
    )

    assert result.status == "success"
    assert len(calls) == 1
    assert calls[0]["to"] == "buyer@example.com"
    assert "https://delivery.example/final.zip" in calls[0]["body"]
    assert calls[0]["idempotency_key"] == "delivery-email-1"
