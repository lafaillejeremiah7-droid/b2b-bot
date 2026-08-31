from __future__ import annotations

from dataclasses import dataclass

import pytest
from django.utils import timezone

from dashboard.adapter.stripe_invoicing import StripeInvoiceReceipt
from dashboard.models import Deal, Invoice, Lead, Operator, PipelineState
from dashboard.services.confirmation import mint_confirmation
from dashboard.services.invoice_send import InvoiceSendGate


class Session(dict):
    modified = False


@dataclass
class FakeStripeClient:
    calls: int = 0

    def create_and_send_invoice(self, **kwargs):
        self.calls += 1
        assert kwargs["recipient_email"] == "buyer@example.com"
        assert kwargs["amount_usd"] == 700
        return StripeInvoiceReceipt(
            provider_invoice_id="in_test_123",
            invoice_number="INV-STRIPE-123",
            hosted_invoice_url="https://invoice.stripe.test/i/123",
        )


@pytest.mark.django_db
def test_invoice_is_not_sent_without_yes_approval(monkeypatch):
    operator = Operator.objects.create_operator("agent@example.com", "pw", role=Operator.Role.AGENT)
    lead = Lead.objects.create(
        company_name="Buyer Co",
        contact_name="Buyer",
        contact_email="buyer@example.com",
        researched_score=4,
        status=PipelineState.INVOICED,
        last_activity_at=timezone.now(),
    )
    deal = Deal.objects.create(lead=lead, agreed_price=700)
    invoice = Invoice.objects.create(deal=deal, invoice_number="LOCAL-1", amount=700)
    fake = FakeStripeClient()
    monkeypatch.setattr("dashboard.services.invoice_send.get_stripe_invoice_client", lambda: fake)

    invoice.refresh_from_db()
    assert invoice.sent_at is None
    assert invoice.provider_invoice_id is None
    assert fake.calls == 0


@pytest.mark.django_db
def test_yes_approval_sends_once_and_snapshots_destination(monkeypatch):
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
    invoice = Invoice.objects.create(deal=deal, invoice_number="LOCAL-2", amount=700)
    fake = FakeStripeClient()
    monkeypatch.setattr("dashboard.services.invoice_send.get_stripe_invoice_client", lambda: fake)

    session = Session()
    token = mint_confirmation(session, action="invoice.send", target_id=invoice.pk)
    outcome = InvoiceSendGate.send(
        deal_id=deal.pk,
        operator=operator,
        session=session,
        confirmation_token=token,
    )

    assert outcome.already_sent is False
    invoice.refresh_from_db()
    assert invoice.recipient_email == "buyer@example.com"
    assert invoice.provider_invoice_id == "in_test_123"
    assert invoice.invoice_number == "INV-STRIPE-123"
    assert invoice.sent_at is not None
    assert invoice.sent_by_operator_id == operator.pk
    assert fake.calls == 1

    # A second approval cannot submit another Stripe invoice/email.
    second = Session()
    token2 = mint_confirmation(second, action="invoice.send", target_id=invoice.pk)
    outcome2 = InvoiceSendGate.send(
        deal_id=deal.pk,
        operator=operator,
        session=second,
        confirmation_token=token2,
    )
    assert outcome2.already_sent is True
    assert fake.calls == 1
