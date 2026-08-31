import pytest
from django.test import override_settings

from dashboard.services.discovery_handoff import (
    ResearchHandoff,
    ScoutHandoff,
    apply_research_handoff,
    apply_scout_handoff,
)
from dashboard.services.outreach_clearance import (
    OutreachClearance,
    apply_outreach_clearance,
)
from dashboard.services.six_employee_pipeline import Lead, SixEmployeePipeline


def _apply_test_clearance(lead: Lead) -> None:
    apply_outreach_clearance(
        lead,
        OutreachClearance(
            recipient_email=lead.email,
            research_digest=lead.notes["research_digest"],
            authority_reference="test-policy",
        ),
    )


def verified_website_lead(*, clearance: bool = True) -> Lead:
    lead = Lead(name="Alex", email="", source="google_maps")
    scout = ScoutHandoff(
        place_reference="place-123",
        business_name="Example Roofing",
        candidate_website="https://example.com",
        formatted_address="123 Main St",
        evidence_urls=("https://maps.example/place-123",),
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
                "The primary quote action is below the first mobile viewport.",
                "Service pages do not place a quote action beside individual services.",
            ),
            evidence_urls=("https://example.com",),
        ),
    )
    if clearance:
        _apply_test_clearance(lead)
    return lead


def verified_no_website_lead(*, clearance: bool = True) -> Lead:
    lead = Lead(name="Taylor", email="", source="google_maps")
    scout = ScoutHandoff(
        place_reference="place-456",
        business_name="Example Auto Detail",
        formatted_address="456 Main St",
        evidence_urls=("https://maps.example/place-456",),
    )
    apply_scout_handoff(lead, scout)
    apply_research_handoff(
        lead,
        ResearchHandoff(
            scout_digest=scout.digest,
            contact_email="taylor@example.com",
            verified_no_website=True,
            contact_verified=True,
            evidence_urls=("https://maps.example/place-456",),
        ),
    )
    if clearance:
        _apply_test_clearance(lead)
    return lead


@override_settings(
    OUTREACH_SENDER_NAME="Jeremiah Lafaille",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_internal_gmail_self_test_passes_all_six_outbound_employees():
    result = SixEmployeePipeline().run(
        Lead(
            name="Test Owner",
            email="owner@example.com",
            source="internal_gmail_test",
        )
    )

    assert result.approved_to_send is True
    assert [stage["employee"] for stage in result.stages] == [
        "Scout",
        "Researcher",
        "Qualifier",
        "Personalizer",
        "Sales Bot",
        "Manager",
    ]
    assert all(stage["status"] == "complete" for stage in result.stages)
    assert "Employee #7, Closer" in result.body
    assert "Phone Number: 555-0100" in result.body
    assert "Email: sender@example.com" in result.body


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_verified_google_maps_website_lead_passes_end_to_end_pre_send_controls():
    result = SixEmployeePipeline().run(verified_website_lead())

    assert result.approved_to_send is True
    assert result.lead.notes["qualification_score"] == 4
    assert all(stage["status"] == "complete" for stage in result.stages)
    assert "1. The primary quote action" in result.body
    assert "2. Service pages" in result.body


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_verified_google_maps_no_website_lead_passes_correct_template():
    result = SixEmployeePipeline().run(verified_no_website_lead())

    assert result.approved_to_send is True
    assert all(stage["status"] == "complete" for stage in result.stages)
    assert "don't currently have a verified dedicated website" in result.body


def test_tampered_scout_handoff_is_blocked_at_scout():
    lead = verified_website_lead()
    lead.notes["scout_handoff"]["business_name"] = "Tampered Company"

    result = SixEmployeePipeline().run(lead)

    assert result.approved_to_send is False
    assert result.stages[0]["employee"] == "Scout"
    assert result.stages[0]["status"] == "blocked"


def test_tampered_research_handoff_is_blocked_at_researcher():
    lead = verified_website_lead()
    lead.notes["research_handoff"]["contact_email"] = "tampered@example.com"

    result = SixEmployeePipeline().run(lead)

    statuses = {stage["employee"]: stage["status"] for stage in result.stages}
    assert result.approved_to_send is False
    assert statuses["Scout"] == "complete"
    assert statuses["Researcher"] == "blocked"
    assert statuses["Qualifier"] == "skipped"


def test_malformed_research_handoff_fails_closed_instead_of_crashing():
    lead = verified_website_lead()
    lead.notes["research_handoff"]["website_observations"] = [123]

    result = SixEmployeePipeline().run(lead)

    statuses = {stage["employee"]: stage["status"] for stage in result.stages}
    assert result.approved_to_send is False
    assert statuses["Scout"] == "complete"
    assert statuses["Researcher"] == "blocked"
    assert statuses["Qualifier"] == "skipped"


def test_researcher_rebuilds_tampered_mirror_fields_from_signed_handoff():
    lead = verified_website_lead()
    lead.notes["website_observations"] = ["INJECTED CLAIM", "ANOTHER INJECTED CLAIM"]
    lead.notes["website_verified"] = False
    lead.notes["verified_no_website"] = True

    result = SixEmployeePipeline().run(lead)

    assert result.approved_to_send is True
    assert "INJECTED CLAIM" not in result.body
    assert "The primary quote action" in result.body
    assert lead.notes["website_verified"] is True
    assert lead.notes["verified_no_website"] is False


def test_researcher_cannot_switch_scout_website_path_to_no_website():
    lead = Lead(name="Alex", email="", source="google_maps")
    scout = ScoutHandoff(
        place_reference="place-789",
        business_name="Path Locked Co",
        candidate_website="https://path.example",
    )
    apply_scout_handoff(lead, scout)

    with pytest.raises(ValueError, match="candidate website path"):
        apply_research_handoff(
            lead,
            ResearchHandoff(
                scout_digest=scout.digest,
                contact_email="owner@example.com",
                verified_no_website=True,
                contact_verified=True,
            ),
        )


def test_no_website_scout_rejects_stale_manual_website_state():
    lead = Lead(
        name="Taylor",
        email="",
        company="No Site Co",
        website="https://stale.example",
        source="google_maps",
    )
    scout = ScoutHandoff(
        place_reference="place-no-site",
        business_name="No Site Co",
    )

    with pytest.raises(ValueError, match="website does not match"):
        apply_scout_handoff(lead, scout)


def test_missing_google_maps_handoff_blocks_and_skips_downstream_workers():
    result = SixEmployeePipeline().run(
        Lead(
            name="Prospect",
            email="prospect@example.com",
            company="Example Co",
            source="google_maps",
        )
    )

    statuses = {stage["employee"]: stage["status"] for stage in result.stages}
    assert result.approved_to_send is False
    assert statuses["Scout"] == "blocked"
    assert statuses["Researcher"] == "skipped"
    assert statuses["Qualifier"] == "skipped"
    assert statuses["Personalizer"] == "skipped"
    assert statuses["Sales Bot"] == "skipped"
    manager = result.stages[-1]
    assert manager["blocked_by"] == ["Scout"]
    assert manager["skipped_employees"] == [
        "Researcher",
        "Qualifier",
        "Personalizer",
        "Sales Bot",
    ]


def test_verified_external_lead_without_clearance_stops_at_sales_bot():
    result = SixEmployeePipeline().run(verified_website_lead(clearance=False))

    statuses = {stage["employee"]: stage["status"] for stage in result.stages}
    assert result.approved_to_send is False
    assert statuses["Scout"] == "complete"
    assert statuses["Researcher"] == "complete"
    assert statuses["Qualifier"] == "complete"
    assert statuses["Personalizer"] == "complete"
    assert statuses["Sales Bot"] == "blocked"
    assert result.stages[-1]["blocked_by"] == ["Sales Bot"]


def test_tampered_clearance_stops_at_sales_bot():
    lead = verified_website_lead()
    lead.notes["outreach_clearance"]["recipient_email"] = "other@example.com"

    result = SixEmployeePipeline().run(lead)

    statuses = {stage["employee"]: stage["status"] for stage in result.stages}
    assert result.approved_to_send is False
    assert statuses["Scout"] == "complete"
    assert statuses["Researcher"] == "complete"
    assert statuses["Qualifier"] == "complete"
    assert statuses["Personalizer"] == "complete"
    assert statuses["Sales Bot"] == "blocked"


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_whitespace_only_contact_name_never_crashes_personalizer():
    lead = verified_website_lead()
    lead.name = "   "

    result = SixEmployeePipeline().run(lead)

    assert result.approved_to_send is True
    assert result.body.startswith("Hi there,")


def test_external_recipient_without_supported_discovery_is_blocked_at_scout():
    result = SixEmployeePipeline().run(
        Lead(name="Prospect", email="prospect@example.com", source="manual")
    )

    assert result.approved_to_send is False
    assert result.stages[0]["employee"] == "Scout"
    assert result.stages[0]["status"] == "blocked"
