from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from django.utils import timezone

from dashboard.models import Deal, Invoice, Lead, Payment, PipelineState, ProcessedEvent
from dashboard.services.stripe_webhooks import (
    StripeWebhookError,
    StripeWebhookIntake,
    verify_stripe_signature,
)


def _signature(payload: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_stripe_signature_rejects_bad_digest():
    payload = b'{"id":"evt_1"}'
    now = int(time.time())
    with pytest.raises(StripeWebhookError, match="verification failed"):
        verify_stripe_signature(
            payload,
            f"t={now},v1={'0' * 64}",
            "whsec_test",
            now=now,
        )


def test_stripe_signature_rejects_stale_timestamp():
    payload = b'{"id":"evt_1"}'
    now = int(time.time())
    signature = _signature(payload, "whsec_test", now - 1000)
    with pytest.raises(StripeWebhookError, match="outside the allowed tolerance"):
        verify_stripe_signature(
            payload,
            signature,
            "whsec_test",
            tolerance_seconds=300,
            now=now,
        )


def _invoice_records():
    lead = Lead.objects.create(
        company_name="Buyer Co",
        contact_name="Buyer",
        contact_email="buyer@example.com",
        researched_score=4,
        status=PipelineState.INVOICED,
        last_activity_at=timezone.now(),
    )
    deal = Deal.objects.create(lead=lead, agreed_price=700)
    invoice = Invoice.objects.create(
        deal=deal,
        invoice_number="LOCAL-STRIPE-1",
        amount=700,
        provider_invoice_id="in_live_123",
        hosted_invoice_url="https://invoice.stripe.test/i/123",
    )
    return lead, deal, invoice


@pytest.mark.django_db
def test_signed_invoice_paid_records_once_and_advances_state(settings):
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS = 300
    settings.STRIPE_CURRENCY = "usd"
    now = int(time.time())
    lead, deal, invoice = _invoice_records()
    event = {
        "id": "evt_invoice_paid_123",
        "type": "invoice.paid",
        "created": now,
        "data": {
            "object": {
                "id": "in_live_123",
                "status": "paid",
                "currency": "usd",
                "amount_paid": 70000,
                "metadata": {"local_invoice_id": str(invoice.pk)},
            }
        },
    }
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    signature = _signature(payload, settings.STRIPE_WEBHOOK_SECRET, now)

    first = StripeWebhookIntake.handle(payload, signature)
    second = StripeWebhookIntake.handle(payload, signature)

    assert first.accepted is True
    assert first.duplicate is False
    assert second.accepted is True
    assert second.duplicate is True
    assert Payment.objects.filter(event_id="evt_invoice_paid_123").count() == 1
    assert ProcessedEvent.objects.filter(event_id="evt_invoice_paid_123").count() == 1
    deal.refresh_from_db()
    lead.refresh_from_db()
    assert deal.payment_received is True
    assert deal.paid_date is not None
    assert lead.status == PipelineState.PAID_PENDING_VERIFICATION


@pytest.mark.django_db
def test_signed_local_invoice_paid_rejects_wrong_currency_before_payment_record(settings):
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS = 300
    settings.STRIPE_CURRENCY = "usd"
    now = int(time.time())
    _lead, _deal, invoice = _invoice_records()
    event = {
        "id": "evt_invoice_paid_eur",
        "type": "invoice.paid",
        "created": now,
        "data": {
            "object": {
                "id": "in_live_123",
                "status": "paid",
                "currency": "eur",
                "amount_paid": 70000,
                "metadata": {"local_invoice_id": str(invoice.pk)},
            }
        },
    }
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    signature = _signature(payload, settings.STRIPE_WEBHOOK_SECRET, now)

    with pytest.raises(StripeWebhookError, match="does not match configured currency"):
        StripeWebhookIntake.handle(payload, signature)

    assert Payment.objects.count() == 0
    assert ProcessedEvent.objects.count() == 0


@pytest.mark.django_db
def test_unrelated_stripe_invoice_is_acknowledged_but_ignored(settings):
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    settings.STRIPE_WEBHOOK_TOLERANCE_SECONDS = 300
    now = int(time.time())
    event = {
        "id": "evt_unrelated_1",
        "type": "invoice.paid",
        "created": now,
        "data": {
            "object": {
                "id": "in_some_other_product",
                "status": "paid",
                "currency": "usd",
                "amount_paid": 50000,
                "metadata": {},
            }
        },
    }
    payload = json.dumps(event, separators=(",", ":")).encode("utf-8")
    signature = _signature(payload, settings.STRIPE_WEBHOOK_SECRET, now)

    outcome = StripeWebhookIntake.handle(payload, signature)

    assert outcome.accepted is True
    assert outcome.ignored is True
    assert Payment.objects.count() == 0
    assert ProcessedEvent.objects.count() == 0


@pytest.mark.django_db
def test_pipeline_events_endpoint_requires_private_secret(client, settings):
    settings.PIPELINE_EVENT_SECRET = "event-secret"
    payload = {
        "event_id": "evt-noauth",
        "event_type": "email_opened",
        "lead_id": 999,
        "event_timestamp": timezone.now().isoformat(),
    }

    denied = client.post("/events/", data=json.dumps(payload), content_type="application/json")
    wrong = client.post(
        "/events/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_PIPELINE_EVENT_SECRET="wrong",
    )

    assert denied.status_code == 401
    assert wrong.status_code == 401
