from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from django.utils import timezone

from dashboard.adapter.pipeline import AdapterResult
from dashboard.adapter.stripe_invoicing import StripeInvoiceClient, StripeInvoiceReceipt
from dashboard.models import Deal, Invoice, Lead, Operator, PipelineState
from dashboard.services.confirmation import mint_confirmation
from dashboard.services.invoice_send import InvoiceSendGate


class Session(dict):
    modified = False


@dataclass
class FakeStripeClient:
    calls: int = 0

    def create_invoice_link(self, **kwargs):
        self.calls += 1
        assert kwargs["recipient_email"] == "buyer@example.com"
        assert kwargs["amount_usd"] == 700
        return StripeInvoiceReceipt(
            provider_invoice_id="in_test_123",
            invoice_number="INV-STRIPE-123",
            hosted_invoice_url="https://invoice.stripe.test/i/123",
        )


@dataclass
class FakeSalesAdapter:
    failure: bool = False
    calls: list[dict] = field(default_factory=list)

    def send_prospect_email(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            return AdapterResult("failure", failure_reason="Gmail delivery unavailable")
        return AdapterResult("success", payload={"message_id": "msg_123", "thread_id": "thr_123"})


def _records():
    operator = Operator.objects.create_operator("agent@example.com", "pw", role=Operator.Role.AGENT)
    lead = Lead.objects.create(
        company_name="Buyer Co",
        contact_name="Buyer",
        contact_email="Buyer@Example.com",
        researched_score=4,
        status=PipelineState.INVOICED,
        last_activity_at=timezone.now(),
    )
    deal = Deal.objects.create(lead=lead, agreed_price=700)
    invoice = Invoice.objects.create(deal=deal, invoice_number="LOCAL-1", amount=700)
    return operator, lead, deal, invoice


@pytest.mark.django_db
def test_invoice_is_not_generated_or_sent_without_yes_approval(monkeypatch):
    operator, _lead, deal, invoice = _records()
    stripe = FakeStripeClient()
    sales = FakeSalesAdapter()
    monkeypatch.setattr("dashboard.services.invoice_send.get_stripe_invoice_client", lambda: stripe)
    monkeypatch.setattr("dashboard.services.invoice_send.get_pipeline_adapter", lambda: sales)

    with pytest.raises(Exception, match="Confirmation"):
        InvoiceSendGate.send(
            deal_id=deal.pk,
            operator=operator,
            session=Session(),
            confirmation_token="",
        )

    invoice.refresh_from_db()
    assert invoice.sent_at is None
    assert invoice.provider_invoice_id is None
    assert stripe.calls == 0
    assert sales.calls == []


@pytest.mark.django_db
def test_yes_approval_closer_generates_link_and_sales_bot_sends_once(monkeypatch):
    operator, _lead, deal, invoice = _records()
    stripe = FakeStripeClient()
    sales = FakeSalesAdapter()
    monkeypatch.setattr("dashboard.services.invoice_send.get_stripe_invoice_client", lambda: stripe)
    monkeypatch.setattr("dashboard.services.invoice_send.get_pipeline_adapter", lambda: sales)

    session = Session()
    token = mint_confirmation(session, action="invoice.send", target_id=invoice.pk)
    outcome = InvoiceSendGate.send(
        deal_id=deal.pk,
        operator=operator,
        session=session,
        confirmation_token=token,
    )

    assert outcome.already_sent is False
    assert outcome.sales_result.status == "success"
    invoice.refresh_from_db()
    assert invoice.recipient_email == "buyer@example.com"
    assert invoice.provider_invoice_id == "in_test_123"
    assert invoice.invoice_number == "INV-STRIPE-123"
    assert invoice.hosted_invoice_url == "https://invoice.stripe.test/i/123"
    assert invoice.sent_at is not None
    assert invoice.sent_by_operator_id == operator.pk
    assert stripe.calls == 1
    assert len(sales.calls) == 1
    sent = sales.calls[0]
    assert sent["to_email"] == "buyer@example.com"
    assert "https://invoice.stripe.test/i/123" in sent["body"]
    assert "$700" in sent["body"]

    # A second approval cannot generate another Stripe invoice or submit a
    # second Sales Bot email after the first success was persisted.
    second = Session()
    token2 = mint_confirmation(second, action="invoice.send", target_id=invoice.pk)
    outcome2 = InvoiceSendGate.send(
        deal_id=deal.pk,
        operator=operator,
        session=second,
        confirmation_token=token2,
    )
    assert outcome2.already_sent is True
    assert stripe.calls == 1
    assert len(sales.calls) == 1


@pytest.mark.django_db
def test_sales_failure_keeps_stripe_link_and_retry_does_not_regenerate(monkeypatch):
    operator, _lead, deal, invoice = _records()
    stripe = FakeStripeClient()
    sales = FakeSalesAdapter(failure=True)
    monkeypatch.setattr("dashboard.services.invoice_send.get_stripe_invoice_client", lambda: stripe)
    monkeypatch.setattr("dashboard.services.invoice_send.get_pipeline_adapter", lambda: sales)

    first_session = Session()
    first_token = mint_confirmation(first_session, action="invoice.send", target_id=invoice.pk)
    first = InvoiceSendGate.send(
        deal_id=deal.pk,
        operator=operator,
        session=first_session,
        confirmation_token=first_token,
    )
    assert first.sales_result.status == "failure"
    invoice.refresh_from_db()
    assert invoice.provider_invoice_id == "in_test_123"
    assert invoice.hosted_invoice_url == "https://invoice.stripe.test/i/123"
    assert invoice.sent_at is None
    assert stripe.calls == 1
    assert len(sales.calls) == 1
    first_key = sales.calls[0]["idempotency_key"]

    sales.failure = False
    second_session = Session()
    second_token = mint_confirmation(second_session, action="invoice.send", target_id=invoice.pk)
    second = InvoiceSendGate.send(
        deal_id=deal.pk,
        operator=operator,
        session=second_session,
        confirmation_token=second_token,
    )
    assert second.sales_result.status == "success"
    invoice.refresh_from_db()
    assert invoice.sent_at is not None
    assert stripe.calls == 1
    assert len(sales.calls) == 2
    assert sales.calls[1]["idempotency_key"] == first_key
    assert "https://invoice.stripe.test/i/123" in sales.calls[1]["body"]


def test_stripe_client_never_calls_send_endpoint(monkeypatch, settings):
    settings.STRIPE_CURRENCY = "usd"
    settings.STRIPE_INVOICE_DAYS_UNTIL_DUE = 1
    client = StripeInvoiceClient(secret_key="rk_test_example", timeout=1)
    paths = []

    def fake_post(path, data, *, idempotency_key):
        paths.append(path)
        if path == "/customers":
            return {"id": "cus_test"}
        if path == "/invoices":
            return {"id": "in_test"}
        if path == "/invoiceitems":
            return {"id": "ii_test"}
        if path == "/invoices/in_test/finalize":
            return {
                "id": "in_test",
                "number": "INV-1",
                "hosted_invoice_url": "https://invoice.stripe.test/i/1",
            }
        raise AssertionError(f"Unexpected Stripe endpoint: {path}")

    monkeypatch.setattr(client, "_post", fake_post)
    receipt = client.create_invoice_link(
        local_invoice_id=1,
        recipient_email="buyer@example.com",
        customer_name="Buyer",
        amount_usd=700,
        description="Website Design & Digital Presence",
    )

    assert receipt.hosted_invoice_url == "https://invoice.stripe.test/i/1"
    assert paths == [
        "/customers",
        "/invoices",
        "/invoiceitems",
        "/invoices/in_test/finalize",
    ]
    assert not any(path.endswith("/send") for path in paths)
