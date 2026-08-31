from __future__ import annotations

from datetime import date

import pytest
from django.conf import settings
from django.db import connection
from django.utils import timezone

from dashboard.adapter.website_research import WebsiteResearchError, _validate_public_http_url
from dashboard.models import Deal, Lead, Operator, Payment, PipelineState
from dashboard.services.auth_service import AuthService
from dashboard.services.money import PaymentService


@pytest.mark.django_db
def test_sign_in_never_redirects_to_protocol_relative_external_host():
    Operator.objects.create_operator("owner@example.com", "safe-password")

    outcome = AuthService.sign_in(
        "owner@example.com",
        "safe-password",
        retained_screen="//evil.example/phish",
    )

    assert outcome.established is True
    assert outcome.redirect_to == "/leads/"


@pytest.mark.django_db
def test_sign_in_keeps_legitimate_local_retained_screen():
    Operator.objects.create_operator("owner@example.com", "safe-password")

    outcome = AuthService.sign_in(
        "owner@example.com",
        "safe-password",
        retained_screen="/dashboard/?room=discovery",
    )

    assert outcome.established is True
    assert outcome.redirect_to == "/dashboard/?room=discovery"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://10.1.2.3/private",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
        "https://user:pass@example.com/",
    ],
)
def test_researcher_rejects_direct_local_private_or_credentialed_urls(url):
    with pytest.raises(WebsiteResearchError):
        _validate_public_http_url(url)


def test_researcher_rejects_hostname_resolving_to_private_network(monkeypatch):
    monkeypatch.setattr(
        "dashboard.adapter.website_research.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("192.168.1.20", 443)),
        ],
    )

    with pytest.raises(WebsiteResearchError, match="private-network"):
        _validate_public_http_url("https://business.example/")


def test_researcher_accepts_hostname_only_when_all_resolved_addresses_are_public(monkeypatch):
    monkeypatch.setattr(
        "dashboard.adapter.website_research.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )

    assert _validate_public_http_url("https://business.example/contact") == "https://business.example/contact"


@pytest.mark.django_db
def test_payment_recording_opens_its_own_atomic_transaction(monkeypatch):
    lead = Lead.objects.create(
        company_name="Atomic Payment Co",
        researched_score=4,
        status=PipelineState.NEW_LEAD,
        last_activity_at=timezone.now(),
    )
    deal = Deal.objects.create(lead=lead, agreed_price=700)
    original = Payment.objects.get_or_create
    observed = []

    def wrapped_get_or_create(*args, **kwargs):
        observed.append(connection.in_atomic_block)
        return original(*args, **kwargs)

    monkeypatch.setattr(Payment.objects, "get_or_create", wrapped_get_or_create)

    payment, anomaly = PaymentService.record_received(
        deal=deal,
        event_id="atomic-payment-1",
        amount_usd=700,
        paid_date=date.today(),
    )

    assert payment.event_id == "atomic-payment-1"
    assert anomaly == "payment received without an invoice record"
    assert observed == [True]
    deal.refresh_from_db()
    assert deal.payment_received is True
    assert deal.payment_anomaly_flag is True


def test_processed_event_retention_cleanup_is_scheduled():
    schedule = settings.CELERY_BEAT_SCHEDULE["purge-processed-events"]
    assert schedule["task"] == "dashboard.tasks.purge_processed_events"
    assert schedule["schedule"] == 86400.0
