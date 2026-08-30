import pytest
from django.test import override_settings

from dashboard.adapter.no_website_research import (
    NoWebsiteResearchClient,
    NoWebsiteResearchError,
    SearchResult,
    SerpApiGoogleSearchClient,
)
from dashboard.services.company import SevenEmployeeCompany
from dashboard.services.discovery_handoff import ScoutHandoff, apply_scout_handoff
from dashboard.services.outreach_clearance import OutreachClearance, apply_outreach_clearance
from dashboard.services.six_employee_pipeline import Lead


class FakeSearchClient:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def search(self, query, *, location="", max_results=10):
        self.calls.append((query, location, max_results))
        if not self.batches:
            raise AssertionError("unexpected search call")
        return self.batches.pop(0)


def no_website_scout() -> ScoutHandoff:
    return ScoutHandoff(
        place_reference="place-456",
        business_name="Example Auto Detail",
        formatted_address="456 Main St, Austin, TX",
        evidence_urls=("https://www.google.com/maps/search/?api=1&query_place_id=place-456",),
    )


def corroborated_batches():
    return [
        [
            SearchResult(
                title="Example Auto Detail - Yelp",
                link="https://www.yelp.com/biz/example-auto-detail-austin",
                snippet="Example Auto Detail in Austin. Email hello@exampledetail.com for appointments.",
            )
        ],
        [
            SearchResult(
                title="Example Auto Detail | BBB Business Profile",
                link="https://www.bbb.org/us/tx/austin/profile/auto-detailing/example-auto-detail",
                snippet="Example Auto Detail. Contact: hello@exampledetail.com",
            )
        ],
    ]


def test_serpapi_client_parses_only_valid_organic_results():
    captured = {}

    def transport(url, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        return {
            "organic_results": [
                {
                    "title": "Example Auto Detail - Yelp",
                    "link": "https://www.yelp.com/biz/example-auto-detail",
                    "snippet": "Email hello@exampledetail.com",
                },
                {"title": "Missing link"},
            ]
        }

    client = SerpApiGoogleSearchClient("test-key", transport=transport)
    results = client.search("Example Auto Detail", location="Austin, TX", max_results=5)

    assert captured["params"]["engine"] == "google"
    assert captured["params"]["api_key"] == "test-key"
    assert captured["params"]["location"] == "Austin, TX"
    assert captured["params"]["num"] == "5"
    assert len(results) == 1
    assert results[0].link.startswith("https://www.yelp.com/")


def test_no_website_research_requires_corroborated_public_email():
    client = NoWebsiteResearchClient(FakeSearchClient(corroborated_batches()))

    handoff = client.research(no_website_scout())

    assert handoff.verified_no_website is True
    assert handoff.contact_verified is True
    assert handoff.website == ""
    assert handoff.contact_email == "hello@exampledetail.com"
    assert any("yelp.com" in url for url in handoff.evidence_urls)
    assert any("bbb.org" in url for url in handoff.evidence_urls)


def test_no_website_research_blocks_single_source_email():
    batches = [
        [
            SearchResult(
                title="Example Auto Detail - Yelp",
                link="https://www.yelp.com/biz/example-auto-detail-austin",
                snippet="Email hello@exampledetail.com",
            )
        ],
        [
            SearchResult(
                title="Example Auto Detail - Yellow Pages",
                link="https://www.yellowpages.com/austin-tx/example-auto-detail",
                snippet="Example Auto Detail phone and hours",
            )
        ],
    ]

    with pytest.raises(NoWebsiteResearchError, match="corroborated"):
        NoWebsiteResearchClient(FakeSearchClient(batches)).research(no_website_scout())


def test_no_website_research_blocks_when_possible_official_site_is_found():
    batches = [
        [
            SearchResult(
                title="Example Auto Detail - Official Site",
                link="https://exampleautodetail.com/",
                snippet="Example Auto Detail in Austin",
            )
        ],
        [
            SearchResult(
                title="Example Auto Detail - Yelp",
                link="https://www.yelp.com/biz/example-auto-detail-austin",
                snippet="Email hello@exampledetail.com",
            )
        ],
    ]

    with pytest.raises(NoWebsiteResearchError, match="plausible official website"):
        NoWebsiteResearchClient(FakeSearchClient(batches)).research(no_website_scout())


def test_no_website_research_blocks_if_one_independent_search_has_no_results():
    batches = [corroborated_batches()[0], []]

    with pytest.raises(NoWebsiteResearchError, match="independent web searches"):
        NoWebsiteResearchClient(FakeSearchClient(batches)).research(no_website_scout())


@override_settings(
    OUTREACH_SENDER_NAME="Test Sender",
    OUTREACH_PHONE="555-0100",
    OUTREACH_EMAIL="sender@example.com",
)
def test_company_auto_routes_no_website_research_then_pipeline_passes_with_clearance():
    scout = no_website_scout()
    lead = Lead(
        name=scout.business_name,
        email="",
        company=scout.business_name,
        source="google_maps",
    )
    apply_scout_handoff(lead, scout)

    class WebsiteClientMustNotRun:
        def research(self, scout):
            raise AssertionError("website researcher should not run for a no-website Scout lead")

    no_site_client = NoWebsiteResearchClient(FakeSearchClient(corroborated_batches()))
    company = SevenEmployeeCompany()
    company.research_discovered_lead(
        lead,
        website_client=WebsiteClientMustNotRun(),
        no_website_client=no_site_client,
    )

    apply_outreach_clearance(
        lead,
        OutreachClearance(
            recipient_email=lead.email,
            research_digest=lead.notes["research_digest"],
            authority_reference="test-policy",
        ),
    )
    result = company.prepare_outreach(lead)

    assert lead.notes["verified_no_website"] is True
    assert result.approved_to_send is True
    assert all(stage["status"] == "complete" for stage in result.stages)
    assert "don't currently have a verified dedicated website" in result.body
