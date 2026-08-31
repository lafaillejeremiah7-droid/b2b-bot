from django.test import override_settings

from dashboard.services.boss import Boss, BossAction
from dashboard.services.closer import Closer
from dashboard.services.company import SevenEmployeeCompany
from dashboard.services.discovery_handoff import (
    ResearchHandoff,
    ScoutHandoff,
    apply_research_handoff,
    apply_scout_handoff,
)
from dashboard.services.outreach_clearance import OutreachClearance, apply_outreach_clearance
from dashboard.services.six_employee_pipeline import Lead, SixEmployeePipeline


def researched_lead(*, cleared: bool) -> Lead:
    lead = Lead(name="Alex", email="", source="google_maps")
    scout = ScoutHandoff(
        place_reference="place-123",
        business_name="Example Roofing",
        candidate_website="https://example.com",
    )
    apply_scout_handoff(lead, scout)
    apply_research_handoff(
        lead,
        ResearchHandoff(
            scout_digest=scout.digest,
            contact_email="alex@example.com",
            website="https://example.com",
            contact_verified=True,
            website_verified=True,
            website_observations=(
                "The homepage HTML does not declare a responsive viewport meta tag.",
                "The homepage does not expose a click-to-call telephone link.",
            ),
        ),
    )
    if cleared:
        apply_outreach_clearance(
            lead,
            OutreachClearance(
                recipient_email=lead.email,
                research_digest=lead.notes["research_digest"],
                authority_reference="test-policy",
            ),
        )
    return lead


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_boss_flags_missing_clearance_but_does_not_create_it():
    lead = researched_lead(cleared=False)
    result = SixEmployeePipeline().run(lead)

    decision = Boss().review_outbound(result)

    assert result.approved_to_send is False
    assert decision.action is BossAction.OWNER_REVIEW
    assert decision.responsible_employee == "Sales Bot"
    assert decision.owner_attention is True
    assert "outreach_clearance" not in lead.notes
    assert "outreach_clearance_digest" not in lead.notes


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_boss_marks_fully_approved_result_ready_then_monitors_after_delivery_receipt():
    result = SixEmployeePipeline().run(researched_lead(cleared=True))
    boss = Boss()

    ready = boss.review_outbound(result)
    assert ready.action is BossAction.READY_FOR_DELIVERY
    assert ready.responsible_employee == "Sales Bot"

    result.lead.notes["delivery_status"] = "sent"
    sent = boss.review_outbound(result)
    assert sent.action is BossAction.MONITOR_REPLY
    assert sent.responsible_employee == "Closer"


def test_boss_prioritizes_positive_reply_for_owner_and_opt_out_for_suppression():
    closer = Closer()
    boss = Boss()

    interested = boss.review_reply(closer.run("I'm interested, tell me more", lead_id="1"))
    unsubscribe = boss.review_reply(closer.run("Please unsubscribe me", lead_id="2"))

    assert interested.action is BossAction.OWNER_REVIEW
    assert interested.owner_attention is True
    assert interested.priority >= 90
    assert unsubscribe.action is BossAction.SUPPRESSED
    assert unsubscribe.priority == 100


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_boss_snapshot_counts_worker_results_and_reply_outcomes():
    approved = SixEmployeePipeline().run(researched_lead(cleared=True))
    blocked = SixEmployeePipeline().run(researched_lead(cleared=False))
    approved.lead.notes["delivery_status"] = "sent"
    closer = Closer()
    replies = [
        closer.run("Sounds good, I'm interested"),
        closer.run("No thanks, not interested"),
    ]

    snapshot = Boss().snapshot([approved, blocked], replies)
    by_employee = {kpi.employee: kpi for kpi in snapshot.employee_kpis}

    assert snapshot.leads_reviewed == 2
    assert snapshot.outbound_approved == 1
    assert snapshot.outbound_sent == 1
    assert snapshot.replies_reviewed == 2
    assert snapshot.positive_replies == 1
    assert snapshot.suppressions == 1
    assert by_employee["Scout"].complete == 2
    assert by_employee["Sales Bot"].complete == 1
    assert by_employee["Sales Bot"].blocked == 1


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_company_runs_boss_automatically_after_outbound_preparation():
    company = SevenEmployeeCompany()
    lead = researched_lead(cleared=True)

    result = company.prepare_outreach(lead)

    assert result.approved_to_send is True
    assert company.employee_names[-1] == "Boss"
    assert lead.notes["boss_review"]["action"] == BossAction.READY_FOR_DELIVERY.value
