from django.test import override_settings

from dashboard.services.boss import BossAction
from dashboard.services.closer import Closer, ReplyCategory
from dashboard.services.company import SevenEmployeeCompany


def test_company_declares_eight_employees():
    assert SevenEmployeeCompany.employee_names == (
        "Scout",
        "Researcher",
        "Qualifier",
        "Personalizer",
        "Sales Bot",
        "Manager",
        "Closer",
        "Boss",
    )


@override_settings(
    OUTREACH_SENDER_NAME="Jeremiah Lafaille",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_interested_reply_escalates_with_thread_context_and_professional_signature():
    result = SevenEmployeeCompany().handle_reply(
        "I'm interested. Tell me more.",
        first_name="Alex",
        lead_id="lead-123",
        thread_id="thread-456",
    )

    assert result.employee == "Closer"
    assert result.decision.category is ReplyCategory.INTERESTED
    assert result.decision.stop_followups is True
    assert result.decision.escalate_to_owner is True
    assert result.decision.auto_send_allowed is False
    assert result.decision.lead_id == "lead-123"
    assert result.decision.thread_id == "thread-456"
    assert "Alex" in result.decision.draft_reply
    assert "Phone Number: 555-0100" in result.decision.draft_reply
    assert "Email: sender@example.com" in result.decision.draft_reply
    assert "B2B Bot" not in result.decision.draft_reply
    assert result.boss is not None
    assert result.boss.action is BossAction.OWNER_REVIEW
    assert result.boss.owner_attention is True


def test_meeting_request_is_high_intent_but_not_auto_sent_without_calendar():
    decision = Closer().run("Send your calendar link so we can schedule a call.", first_name="Sam")

    assert decision.category is ReplyCategory.MEETING_REQUEST
    assert decision.stop_followups is True
    assert decision.escalate_to_owner is True
    assert decision.auto_send_allowed is False
    assert "Sam" in decision.draft_reply


def test_objection_pauses_sequence_and_escalates():
    decision = Closer().run("This is too expensive for our budget.")

    assert decision.category is ReplyCategory.OBJECTION
    assert decision.stop_followups is True
    assert decision.escalate_to_owner is True
    assert decision.auto_send_allowed is False


def test_unsubscribe_requires_persistent_suppression():
    decision = Closer().run(
        "Please unsubscribe me and stop emailing.",
        lead_id="lead-123",
        thread_id="thread-456",
    )

    assert decision.category is ReplyCategory.UNSUBSCRIBE
    assert decision.stop_followups is True
    assert decision.escalate_to_owner is False
    assert decision.auto_send_allowed is False
    assert decision.suppression_required is True
    assert decision.lead_id == "lead-123"
    assert decision.thread_id == "thread-456"
    assert decision.draft_reply == ""


def test_unknown_reply_never_guesses_or_auto_sends():
    decision = Closer().run("Circle back on the thing from earlier")

    assert decision.category is ReplyCategory.UNKNOWN
    assert decision.stop_followups is True
    assert decision.escalate_to_owner is True
    assert decision.auto_send_allowed is False
