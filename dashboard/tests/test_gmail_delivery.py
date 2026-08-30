import base64
from email import policy
from email.parser import BytesParser

import pytest

from dashboard.adapter.gmail_delivery import GMAIL_SEND_URL, GmailDeliveryClient
from dashboard.services.company import SevenEmployeeCompany
from dashboard.services.six_employee_pipeline import Lead


def _decode_raw(raw: str):
    padding = "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(raw + padding)
    return BytesParser(policy=policy.default).parsebytes(decoded)


def test_gmail_adapter_builds_authorized_rfc_message_and_returns_receipt():
    captured = {}

    def transport(url, payload, headers, timeout):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {"id": "msg-123", "threadId": "thread-456"}

    client = GmailDeliveryClient(
        lambda: "oauth-token",
        sender_email="sender@example.com",
        transport=transport,
    )
    receipt = client.send(
        to="prospect@example.com",
        subject="Quick website idea",
        body="Hello there",
    )

    assert captured["url"] == GMAIL_SEND_URL
    assert captured["headers"]["Authorization"] == "Bearer oauth-token"
    message = _decode_raw(captured["payload"]["raw"])
    assert message["To"] == "prospect@example.com"
    assert message["From"] == "sender@example.com"
    assert message["Subject"] == "Quick website idea"
    assert "Hello there" in message.get_content()
    assert receipt.message_id == "msg-123"
    assert receipt.thread_id == "thread-456"


def test_company_refuses_delivery_when_pipeline_did_not_pass():
    company = SevenEmployeeCompany()
    result = company.prepare_outreach(
        Lead(name="Prospect", email="prospect@example.com", source="manual")
    )

    class MustNotSend:
        def send(self, **kwargs):
            raise AssertionError("delivery adapter must not be called")

    with pytest.raises(ValueError, match="pipeline did not pass"):
        company.deliver_outreach(result, client=MustNotSend())


def test_company_records_message_and_thread_ids_after_an_approved_send():
    company = SevenEmployeeCompany()
    result = company.prepare_outreach(
        Lead(name="Test Owner", email="owner@example.com", source="internal_gmail_test")
    )

    class Receipt:
        message_id = "msg-123"
        thread_id = "thread-456"

    class FakeDelivery:
        def send(self, *, to, subject, body):
            assert to == "owner@example.com"
            assert subject
            assert body
            return Receipt()

    delivery = company.deliver_outreach(result, client=FakeDelivery())

    assert delivery.employee == "Sales Bot"
    assert delivery.message_id == "msg-123"
    assert delivery.thread_id == "thread-456"
    assert result.lead.notes["sent_message_id"] == "msg-123"
    assert result.lead.notes["sent_thread_id"] == "thread-456"
    assert result.lead.notes["delivery_status"] == "sent"
