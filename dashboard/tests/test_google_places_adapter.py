from dashboard.adapter.google_places import (
    PLACES_FIELD_MASK,
    PLACES_TEXT_SEARCH_URL,
    GooglePlacesTextSearchClient,
)
from dashboard.services.company import SevenEmployeeCompany


def test_google_places_client_returns_scout_candidate_without_inventing_contact_data():
    captured = {}

    def fake_transport(url, payload, headers, timeout):
        captured.update(
            url=url,
            payload=payload,
            headers=headers,
            timeout=timeout,
        )
        return {
            "places": [
                {
                    "id": "place-123",
                    "displayName": {"text": "Example Roofing"},
                    "formattedAddress": "123 Main St",
                    "websiteUri": "https://example.com",
                    "businessStatus": "OPERATIONAL",
                    "types": ["roofing_contractor"],
                }
            ]
        }

    client = GooglePlacesTextSearchClient("test-key", transport=fake_transport)
    candidates = client.search("roofers in Austin", max_results=5)

    assert captured["url"] == PLACES_TEXT_SEARCH_URL
    assert captured["payload"]["textQuery"] == "roofers in Austin"
    assert captured["payload"]["pageSize"] == 5
    assert captured["headers"]["X-Goog-Api-Key"] == "test-key"
    assert captured["headers"]["X-Goog-FieldMask"] == PLACES_FIELD_MASK
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.place_id == "place-123"
    assert candidate.business_name == "Example Roofing"
    assert candidate.website_uri == "https://example.com"
    assert not hasattr(candidate, "email")


def test_company_scout_search_creates_verified_scout_lead_then_stops_for_research():
    def fake_transport(url, payload, headers, timeout):
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
    client = GooglePlacesTextSearchClient("test-key", transport=fake_transport)
    leads = company.scout_google_maps("roofers in Austin", client=client, max_results=5)

    assert len(leads) == 1
    lead = leads[0]
    assert lead.company == "Example Roofing"
    assert lead.email == ""
    assert lead.notes["scout_digest"]

    result = company.prepare_outreach(lead)
    statuses = {stage["employee"]: stage["status"] for stage in result.stages}
    assert statuses["Scout"] == "complete"
    assert statuses["Researcher"] == "blocked"
    assert result.approved_to_send is False
