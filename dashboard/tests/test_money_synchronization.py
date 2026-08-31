from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import pytest
from django.utils import timezone

from dashboard.adapter.pipeline import AdapterResult
from dashboard.models import (
    AuditActionType,
    Deal,
    Invoice,
    Lead,
    Operator,
    Payment,
    PipelineState,
    PipelineStateHistory,
    ReleaseAuthorization,
)
from dashboard.services.confirmation import mint_confirmation
from dashboard.services.errors import ValidationRejected
from dashboard.services.money import InvoiceManager, PaymentService, ReleaseGate


class Session(dict):
    modified = False


@dataclass
class FakeDeliveryAdapter:
    fail_first: bool = False
    calls: list[dict] = field(default_factory=list)

    def send_delivery_email(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_first and len(self.calls) == 1:
            return AdapterResult("failure", failure_reason="temporary delivery failure")
        return AdapterResult("success", payload={"message_id": "delivery-1"})


@dataclass
class MutatingInvoiceAdapter:
    deal_id: int

    def create_invoice(self, **kwargs):
        Deal.objects.filter(pk=self.deal_id).update(agreed_price=800)
        return AdapterResult("success", payload={"draft_only": True})


def _operator(email: str = "agent@example.com") -> Operator:
    return Operator.objects.create_operator(email, "pw", role=Operator.Role.AGENT)


def _release_records():
    operator = _operator()
    lead = Lead.objects.create(
        company_name="Buyer Co",
        contact_name="Buyer",
        contact_email="buyer@example.com",
        researched_score=4,
        status=PipelineState.PAYMENT_VERIFIED,
        last_activity_at=timezone.now(),
    )
    deal = Deal.objects.create(
        lead=lead,
        agreed_price=700,
        payment_received=True,
        paid_date=timezone.now().date(),
        payment_verified_at=timezone.now(),
        verified_by_operator=operator,
    )
    return operator, lead, deal


@pytest.mark.django_db
def test_stub_mode_never_marks_website_delivery_as_sent(settings):
    operator, lead, deal = _release_records()
    settings.PIPELINE_ADAPTER_MODE = "stub"
    session = Session()
    token = mint_confirmation(session, action="release.authorize", target_id=deal.pk)

    outcome = ReleaseGate.authorize_release(
        deal_id=deal.pk,
        operator=operator,
        session=session,
        confirmation_token=token,
        archive_link="https://delivery.example/site.zip",
    )

    assert outcome.adapter_result is not None
    assert outcome.adapter_result.status == "failure"
    assert "stub mode" in outcome.adapter_result.failure_reason
    deal.refresh_from_db()
    lead.refresh_from_db()
    assert deal.delivery_sent is not True
    assert deal.delivered_date is None
    assert lead.status == PipelineState.PAYMENT_VERIFIED
    assert ReleaseAuthorization.objects.filter(deal=deal).count() == 1


@pytest.mark.django_db
def test_reapproval_retries_exact_original_release_snapshot(monkeypatch):
    authorizer, lead, deal = _release_records()
    retry_operator = _operator("retry@example.com")
    adapter = FakeDeliveryAdapter(fail_first=True)
    monkeypatch.setattr("dashboard.services.money.get_pipeline_adapter", lambda: adapter)

    first_session = Session()
    first_token = mint_confirmation(first_session, action="release.authorize", target_id=deal.pk)
    first = ReleaseGate.authorize_release(
        deal_id=deal.pk,
        operator=authorizer,
        session=first_session,
        confirmation_token=first_token,
        archive_link="https://delivery.example/site.zip",
    )
    assert first.adapter_result.status == "failure"
    assert len(adapter.calls) == 1
    first_key = adapter.calls[0]["idempotency_key"]
    assert isinstance(first_key, UUID)
    assert adapter.calls[0]["to_email"] == "buyer@example.com"

    # A later Lead edit must not redirect a previously authorized delivery.
    lead.contact_email = "different@example.com"
    lead.save(update_fields=["contact_email"])

    second_session = Session()
    second_token = mint_confirmation(second_session, action="release.authorize", target_id=deal.pk)
    second = ReleaseGate.authorize_release(
        deal_id=deal.pk,
        operator=retry_operator,
        session=second_session,
        confirmation_token=second_token,
        archive_link="https://delivery.example/site.zip",
    )

    assert second.adapter_result.status == "success"
    assert len(adapter.calls) == 2
    assert adapter.calls[1]["to_email"] == "buyer@example.com"
    assert adapter.calls[1]["archive_link"] == "https://delivery.example/site.zip"
    assert adapter.calls[1]["idempotency_key"] == first_key

    deal.refresh_from_db()
    lead.refresh_from_db()
    assert deal.delivery_sent is True
    assert deal.delivered_date is not None
    assert lead.status == PipelineState.RELEASED
    history = PipelineStateHistory.objects.get(lead=lead, to_state=PipelineState.RELEASED)
    assert history.actor_id == authorizer.pk
    assert history.audit_entry.actor_id == authorizer.pk

    authorization = ReleaseAuthorization.objects.get(deal=deal)
    audit = authorization.operator.audit_entries.get(
        action_type=AuditActionType.RELEASE_AUTHORIZATION,
        target_type="releaseauthorization",
        target_id=authorization.pk,
    )
    assert audit.after_value["recipient_email"] == "buyer@example.com"
    assert audit.after_value["archive_link"] == "https://delivery.example/site.zip"
    assert audit.after_value["delivery_idempotency_key"] == str(first_key)


@pytest.mark.django_db
def test_pending_release_cannot_change_archive_link(monkeypatch):
    operator, _lead, deal = _release_records()
    adapter = FakeDeliveryAdapter(fail_first=True)
    monkeypatch.setattr("dashboard.services.money.get_pipeline_adapter", lambda: adapter)

    first_session = Session()
    first_token = mint_confirmation(first_session, action="release.authorize", target_id=deal.pk)
    ReleaseGate.authorize_release(
        deal_id=deal.pk,
        operator=operator,
        session=first_session,
        confirmation_token=first_token,
        archive_link="https://delivery.example/original.zip",
    )

    second_session = Session()
    second_token = mint_confirmation(second_session, action="release.authorize", target_id=deal.pk)
    with pytest.raises(ValidationRejected, match="exact archive link"):
        ReleaseGate.authorize_release(
            deal_id=deal.pk,
            operator=operator,
            session=second_session,
            confirmation_token=second_token,
            archive_link="https://delivery.example/changed.zip",
        )

    assert len(adapter.calls) == 1


@pytest.mark.django_db
def test_invoice_creation_rejects_price_drift_after_adapter_call(monkeypatch):
    operator = _operator()
    lead = Lead.objects.create(
        company_name="Buyer Co",
        contact_email="buyer@example.com",
        researched_score=4,
        status=PipelineState.WON,
        last_activity_at=timezone.now(),
    )
    deal = Deal.objects.create(lead=lead, agreed_price=700)
    monkeypatch.setattr(
        "dashboard.services.money.get_pipeline_adapter",
        lambda: MutatingInvoiceAdapter(deal.pk),
    )

    with pytest.raises(ValidationRejected, match="Agreed price changed"):
        InvoiceManager.create_invoice(deal_id=deal.pk, operator=operator)

    assert Invoice.objects.filter(deal=deal).count() == 0


@pytest.mark.django_db
def test_payment_event_id_cannot_be_reused_with_different_facts():
    operator, _lead, deal = _release_records()
    invoice = Invoice.objects.create(deal=deal, invoice_number="PAY-1", amount=700)
    paid_date = timezone.now().date()
    Payment.objects.create(
        deal=deal,
        event_id="evt-payment-1",
        amount_usd=700,
        paid_date=paid_date,
    )

    with pytest.raises(ValidationRejected, match="different payment facts"):
        PaymentService.record_received(
            deal=deal,
            event_id="evt-payment-1",
            amount_usd=650,
            paid_date=paid_date,
        )

    assert Payment.objects.filter(event_id="evt-payment-1").get().amount_usd == 700
