import pytest
from django.test import override_settings

from dashboard.services.outreach_templates import OutreachContext, render_google_maps_outreach
from dashboard.services.six_employee_pipeline import Lead, Personalizer


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_google_maps_website_brand_gets_two_real_observations_and_professional_signature():
    lead = Lead(
        name="Alex Morgan",
        email="alex@example.com",
        company="Example Roofing",
        website="https://example.com",
        source="google_maps",
        notes={
            "qualified": True,
            "website_observations": [
                "Your mobile homepage pushes the main quote button below the first screen.",
                "Your services page lists each service but does not place a quote CTA beside them.",
            ],
            "preview_url": "https://preview.example.com",
        },
    )

    result = Personalizer().run(lead)

    assert result["status"] == "complete"
    body = lead.notes["body"]
    assert "1. Your mobile homepage" in body
    assert "2. Your services page" in body
    assert "Phone Number: 555-0100" in body
    assert "Email: sender@example.com" in body
    assert "https://preview.example.com" in body


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_google_maps_no_website_brand_gets_no_website_template_and_professional_signature():
    lead = Lead(
        name="Taylor",
        email="taylor@example.com",
        company="Example Auto Detail",
        source="google_maps",
        notes={"qualified": True, "verified_no_website": True},
    )

    result = Personalizer().run(lead)

    assert result["status"] == "complete"
    body = lead.notes["body"]
    assert "don't currently have a verified dedicated website" in body
    assert "What you offer and why they should choose you" in body
    assert "How to contact, book, request a quote" in body
    assert "Phone Number: 555-0100" in body
    assert "Email: sender@example.com" in body


def test_website_brand_requires_two_verified_observations():
    context = OutreachContext(
        business_name="Example Brand",
        website="https://example.com",
        observations=("Only one observation",),
    )

    with pytest.raises(ValueError, match="two verified website observations"):
        render_google_maps_outreach(context)


def test_no_website_brand_must_be_explicitly_verified():
    context = OutreachContext(business_name="Example Brand")

    with pytest.raises(ValueError, match="official website URL"):
        render_google_maps_outreach(context)
