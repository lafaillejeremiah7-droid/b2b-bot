from django.test import override_settings

from dashboard.services.discovery_handoff import DiscoveryHandoff, apply_discovery_handoff
from dashboard.services.six_employee_pipeline import Lead, SixEmployeePipeline


def verified_website_lead(*, clearance: bool = True) -> Lead:
    lead = Lead(
        name="Alex",
        email="alex@example.com",
        company="Example Roofing",
        website="https://example.com",
        source="google_maps",
        notes={"outreach_clearance": clearance},
    )
    apply_discovery_handoff(
        lead,
        DiscoveryHandoff(
            place_reference="place-123",
            business_name="Example Roofing",
            contact_email="alex@example.com",
            website="https://example.com",
            contact_verified=True,
            website_verified=True,
            website_observations=(
                "The primary quote action is below the first mobile viewport.",
                "Service pages do not place a quote action beside individual services.",
            ),
            evidence_urls=("https://maps.example/place-123", "https://example.com"),
        ),
    )
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


def test_tampered_discovery_handoff_is_blocked_at_scout():
    lead = verified_website_lead()
    lead.notes["discovery_handoff"]["business_name"] = "Tampered Company"

    result = SixEmployeePipeline().run(lead)

    assert result.approved_to_send is False
    assert result.stages[0]["employee"] == "Scout"
    assert result.stages[0]["status"] == "blocked"


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
    assert result.stages[-1]["blocked_by"]


def test_verified_external_lead_without_clearance_stops_at_sales_bot():
    result = SixEmployeePipeline().run(verified_website_lead(clearance=False))

    statuses = {stage["employee"]: stage["status"] for stage in result.stages}
    assert result.approved_to_send is False
    assert statuses["Scout"] == "complete"
    assert statuses["Researcher"] == "complete"
    assert statuses["Qualifier"] == "complete"
    assert statuses["Personalizer"] == "complete"
    assert statuses["Sales Bot"] == "blocked"


def test_external_recipient_without_supported_discovery_is_blocked_at_scout():
    result = SixEmployeePipeline().run(
        Lead(name="Prospect", email="prospect@example.com", source="manual")
    )

    assert result.approved_to_send is False
    assert result.stages[0]["employee"] == "Scout"
    assert result.stages[0]["status"] == "blocked"
