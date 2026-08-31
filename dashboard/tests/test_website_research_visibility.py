import pytest

from dashboard.adapter.website_research import FetchedPage, WebsiteResearchClient, WebsiteResearchError
from dashboard.services.discovery_handoff import ScoutHandoff


def test_researcher_ignores_email_strings_inside_script_code():
    scout = ScoutHandoff(
        place_reference="place-hidden-email",
        business_name="Example Roofing",
        candidate_website="https://example.com",
    )
    html = """
    <html>
      <head><title>Example Roofing</title></head>
      <body>
        <h1>Example Roofing</h1>
        <p>Residential roofing services.</p>
        <script>window.analyticsUser = "tracking@example.com";</script>
      </body>
    </html>
    """

    def fetch(url: str, timeout: float) -> FetchedPage:
        return FetchedPage(url, "https://example.com", html)

    with pytest.raises(WebsiteResearchError, match="public business email"):
        WebsiteResearchClient(fetch=fetch).research(scout)
