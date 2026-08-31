import pytest

from dashboard.models.suppression import OutreachSuppression
from dashboard.services.company import SevenEmployeeCompany
from dashboard.services.suppression import DjangoSuppressionStore
from dashboard.services.six_employee_pipeline import Lead


@pytest.mark.django_db
def test_closer_persists_unsubscribe_and_sales_bot_sees_it_on_next_prepare():
    store = DjangoSuppressionStore()
    company = SevenEmployeeCompany(suppression_store=store)

    reply = company.handle_reply(
        "Please unsubscribe me and stop emailing.",
        first_name="Alex",
        lead_id="lead-123",
        thread_id="thread-456",
        recipient_email="Prospect@Example.com",
    )

    assert reply.decision.suppression_required is True
    record = OutreachSuppression.objects.get(normalized_email="prospect@example.com")
    assert record.reason == "unsubscribe"
    assert record.lead_reference == "lead-123"
    assert record.thread_id == "thread-456"

    lead = Lead(
        name="Test Owner",
        email="prospect@example.com",
        source="internal_gmail_test",
    )
    result = company.prepare_outreach(lead)

    assert result.approved_to_send is False
    assert lead.notes["suppressed"] is True
    assert result.stages[2]["employee"] == "Qualifier"
    assert result.stages[2]["status"] == "blocked"


@pytest.mark.django_db
def test_delivery_rechecks_suppression_after_pipeline_approval():
    store = DjangoSuppressionStore()
    company = SevenEmployeeCompany(suppression_store=store)
    result = company.prepare_outreach(
        Lead(name="Test Owner", email="owner@example.com", source="internal_gmail_test")
    )
    assert result.approved_to_send is True

    store.suppress("owner@example.com", reason="manual")

    class MustNotSend:
        def send(self, **kwargs):
            raise AssertionError("suppressed mail must never reach the delivery adapter")

    with pytest.raises(ValueError, match="lead is suppressed"):
        company.deliver_outreach(result, client=MustNotSend())


@pytest.mark.django_db
def test_suppression_update_is_idempotent_per_email():
    store = DjangoSuppressionStore()
    store.suppress(
        "TEST@Example.com",
        reason="not_interested",
        lead_reference="lead-1",
        thread_id="thread-1",
    )
    store.suppress(
        "test@example.com",
        reason="unsubscribe",
        lead_reference="lead-2",
        thread_id="thread-2",
    )

    assert OutreachSuppression.objects.count() == 1
    record = OutreachSuppression.objects.get()
    assert record.normalized_email == "test@example.com"
    assert record.reason == "unsubscribe"
    assert record.lead_reference == "lead-2"
