from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from django.test import override_settings

from dashboard.services.boss import BossAction
from dashboard.services.company import EightEmployeeCompany
from dashboard.services.six_employee_pipeline import Lead
from dashboard.tests.test_six_employee_pipeline import verified_website_lead


@dataclass(frozen=True)
class FakeReceipt:
    message_id: str = "msg-1"
    thread_id: str = "thread-1"


@dataclass
class FakeDeliveryClient:
    calls: list[dict] = field(default_factory=list)

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return FakeReceipt()


@dataclass
class FakeSuppressionStore:
    suppressed: set[str] = field(default_factory=set)
    writes: list[dict] = field(default_factory=list)

    def is_suppressed(self, email: str) -> bool:
        return email.strip().lower() in self.suppressed

    def suppress(self, email: str, **kwargs) -> None:
        normalized = email.strip().lower()
        self.suppressed.add(normalized)
        self.writes.append({"email": normalized, **kwargs})


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_company_successful_delivery_reruns_workers_then_hands_exact_message_to_sales_bot():
    company = EightEmployeeCompany()
    lead = verified_website_lead()
    prepared = company.prepare_outreach(lead)
    client = FakeDeliveryClient()

    delivered = company.deliver_outreach(prepared, client=client)

    assert delivered.employee == "Sales Bot"
    assert delivered.recipient == "alex@example.com"
    assert len(client.calls) == 1
    assert client.calls[0] == {
        "to": "alex@example.com",
        "subject": prepared.subject,
        "body": prepared.body,
    }
    assert lead.notes["delivery_status"] == "sent"
    assert delivered.boss is not None
    assert delivered.boss.action is BossAction.MONITOR_REPLY


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_company_rejects_recipient_or_research_drift_before_network_send():
    company = EightEmployeeCompany()
    lead = verified_website_lead()
    prepared = company.prepare_outreach(lead)
    client = FakeDeliveryClient()

    lead.email = "attacker@example.com"

    with pytest.raises(ValueError, match="revalidation failed"):
        company.deliver_outreach(prepared, client=client)

    assert client.calls == []
    assert "delivery_status" not in lead.notes


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_company_requires_fresh_prepare_when_message_configuration_changes():
    company = EightEmployeeCompany()
    lead = verified_website_lead()
    prepared = company.prepare_outreach(lead)
    client = FakeDeliveryClient()

    with override_settings(OUTREACH_SENDER_NAME="Different Sender"):
        with pytest.raises(ValueError, match="approved message changed"):
            company.deliver_outreach(prepared, client=client)

    assert client.calls == []


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_sales_bot_final_delivery_rejects_message_drift_directly():
    company = EightEmployeeCompany()
    lead = verified_website_lead()
    prepared = company.prepare_outreach(lead)
    client = FakeDeliveryClient()

    prepared.lead.notes["body"] = "tampered after approval"

    with pytest.raises(ValueError, match="approved message drifted"):
        from dashboard.services.six_employee_pipeline import SalesBot

        SalesBot().deliver_outreach(prepared, client=client)

    assert client.calls == []


def test_closer_cannot_silently_drop_required_suppression_when_recipient_missing():
    store = FakeSuppressionStore()
    company = EightEmployeeCompany(suppression_store=store)

    with pytest.raises(RuntimeError, match="recipient email is unavailable"):
        company.handle_reply(
            "Please unsubscribe me and stop emailing.",
            lead_id="lead-1",
            thread_id="thread-1",
            recipient_email="",
        )

    assert store.writes == []


def test_closer_persists_required_suppression_to_exact_recipient_before_boss_review():
    store = FakeSuppressionStore()
    company = EightEmployeeCompany(suppression_store=store)

    outcome = company.handle_reply(
        "No thanks, not interested.",
        lead_id="lead-2",
        thread_id="thread-2",
        recipient_email=" Prospect@Example.com ",
    )

    assert store.writes == [
        {
            "email": "prospect@example.com",
            "reason": "not_interested",
            "lead_reference": "lead-2",
            "thread_id": "thread-2",
        }
    ]
    assert outcome.boss is not None
    assert outcome.boss.action is BossAction.SUPPRESSED


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_company_final_suppression_refresh_blocks_prepared_result():
    store = FakeSuppressionStore()
    company = EightEmployeeCompany(suppression_store=store)
    lead: Lead = verified_website_lead()
    prepared = company.prepare_outreach(lead)
    client = FakeDeliveryClient()

    store.suppressed.add("alex@example.com")

    with pytest.raises(ValueError, match="lead is suppressed"):
        company.deliver_outreach(prepared, client=client)

    assert client.calls == []
