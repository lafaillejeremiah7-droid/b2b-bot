import pytest
from django.test import override_settings

from dashboard.adapter.google_places import GooglePlacesTextSearchClient
from dashboard.adapter.website_research import (
    FetchedPage,
    WebsiteResearchClient,
    WebsiteResearchError,
)
from dashboard.services.company import SevenEmployeeCompany


HOME_HTML = """
<html>
  <head><title>Example Roofing - Austin</title></head>
  <body>
    <h1>Example Roofing</h1>
    <p>Residential and commercial roofing.</p>
    <a href="/contact">Contact</a>
  </body>
</html>
"""

CONTACT_HTML = """
<html>
  <head><title>Contact Example Roofing</title><meta name="viewport" content="width=device-width"></head>
  <body>
    <h1>Contact</h1>
    <a href="mailto:info@example.com">info@example.com</a>
    <a href="tel:+15550100">Call us</a>
  </body>
</html>
"""


def website_fetch(url: str, timeout: float) -> FetchedPage:
    if url.rstrip("/").endswith("contact"):
        return FetchedPage(url, "https://example.com/contact", CONTACT_HTML)
    return FetchedPage(url, "https://example.com", HOME_HTML)


def test_researcher_verifies_public_email_and_two_concrete_homepage_observations():
    from dashboard.services.discovery_handoff import ScoutHandoff

    scout = ScoutHandoff(
        place_reference="place-123",
        business_name="Example Roofing",
        candidate_website="https://example.com",
        evidence_urls=("https://maps.example/place-123",),
    )

    result = WebsiteResearchClient(fetch=website_fetch).research(scout)

    assert result.scout_digest == scout.digest
    assert result.contact_email == "info@example.com"
    assert result.contact_verified is True
    assert result.website_verified is True
    assert result.website == "https://example.com"
    assert len(result.website_observations) >= 2
    assert any("viewport" in item for item in result.website_observations)
    assert any("click-to-call" in item for item in result.website_observations)
    assert "https://example.com/contact" in result.evidence_urls


def test_researcher_fails_closed_when_no_public_email_can_be_verified():
    from dashboard.services.discovery_handoff import ScoutHandoff

    scout = ScoutHandoff(
        place_reference="place-123",
        business_name="Example Roofing",
        candidate_website="https://example.com",
    )

    def no_email_fetch(url: str, timeout: float) -> FetchedPage:
        html = "<html><head><title>Example</title></head><body><h1>Example</h1></body></html>"
        return FetchedPage(url, "https://example.com", html)

    with pytest.raises(WebsiteResearchError, match="public business email"):
        WebsiteResearchClient(fetch=no_email_fetch).research(scout)


def test_researcher_fails_closed_instead_of_inventing_two_website_problems():
    from dashboard.services.discovery_handoff import ScoutHandoff

    scout = ScoutHandoff(
        place_reference="place-123",
        business_name="Example Roofing",
        candidate_website="https://example.com",
    )

    polished_html = """
    <html>
      <head>
        <title>Example Roofing</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
      </head>
      <body>
        <h1>Example Roofing</h1>
        <a href="tel:+15550100">Call</a>
        <a href="/contact">Request a Quote</a>
        <form><input name="email"></form>
        <a href="mailto:info@example.com">Email</a>
      </body>
    </html>
    """

    def polished_fetch(url: str, timeout: float) -> FetchedPage:
        return FetchedPage(url, "https://example.com", polished_html)

    with pytest.raises(WebsiteResearchError, match="fewer than two concrete website issues"):
        WebsiteResearchClient(fetch=polished_fetch).research(scout)


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_company_runs_scout_then_researcher_then_full_outbound_pipeline():
    def places_transport(url, payload, headers, timeout):
        return {
            "places": [
                {
                    "id": "place-123",
                    "displayName": {"text": "Example Roofing"},
                    "formattedAddress": "123 Main St",
                    "websiteUri": "https://example.com",
                }
            ]
        }

    company = SevenEmployeeCompany()
    scout_client = GooglePlacesTextSearchClient("test-key", transport=places_transport)
    research_client = WebsiteResearchClient(fetch=website_fetch)

    lead = company.scout_google_maps("roofers in Austin", client=scout_client, max_results=1)[0]
    company.research_lead(lead, client=research_client)
    lead.notes["outreach_clearance"] = True
    result = company.prepare_outreach(lead)

    assert result.approved_to_send is True
    assert [stage["status"] for stage in result.stages] == [
        "complete",
        "complete",
        "complete",
        "complete",
        "complete",
        "complete",
    ]
    assert lead.email == "info@example.com"
    assert "Phone Number: 555-0100" in result.body
    assert "Email: sender@example.com" in result.body
