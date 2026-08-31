from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from dashboard.services.discovery_handoff import ResearchHandoff, ScoutHandoff

SERPAPI_SEARCH_URL = "https://serpapi.com/search.json"
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
THIRD_PARTY_DOMAINS = (
    "google.com",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "yelp.com",
    "yellowpages.com",
    "mapquest.com",
    "bbb.org",
    "chamberofcommerce.com",
    "nextdoor.com",
    "thumbtack.com",
    "angi.com",
    "homeadvisor.com",
)


class NoWebsiteResearchError(RuntimeError):
    """Raised when no-website evidence is too weak to use in outreach."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    link: str
    snippet: str = ""


class SearchClient(Protocol):
    def search(
        self,
        query: str,
        *,
        location: str = "",
        max_results: int = 10,
    ) -> list[SearchResult]: ...


SearchTransport = Callable[[str, dict[str, str], float], dict[str, Any]]


def _default_search_transport(
    url: str,
    params: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    request = Request(f"{url}?{urlencode(params)}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed SerpApi host
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise NoWebsiteResearchError(f"Search provider HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise NoWebsiteResearchError(f"Search provider transport error: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NoWebsiteResearchError("Search provider returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise NoWebsiteResearchError("Search provider returned a non-object response.")
    return parsed


class SerpApiGoogleSearchClient:
    """Optional Google Search evidence provider for the no-website Researcher path."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 20.0,
        transport: SearchTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("SERPAPI_API_KEY is required for no-website research.")
        self._api_key = api_key.strip()
        self._timeout = timeout_seconds
        self._transport = transport or _default_search_transport

    def search(
        self,
        query: str,
        *,
        location: str = "",
        max_results: int = 10,
    ) -> list[SearchResult]:
        if not query.strip():
            raise ValueError("Search query cannot be blank.")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20.")
        params = {
            "engine": "google",
            "q": query.strip(),
            "api_key": self._api_key,
            "num": str(max_results),
            "hl": "en",
            "gl": "us",
        }
        if location.strip():
            params["location"] = location.strip()
        response = self._transport(SERPAPI_SEARCH_URL, params, self._timeout)
        raw_results = response.get("organic_results", [])
        if not isinstance(raw_results, list):
            raise NoWebsiteResearchError("Search provider returned invalid organic results.")
        results: list[SearchResult] = []
        for item in raw_results[:max_results]:
            if not isinstance(item, dict):
                continue
            link = str(item.get("link", "")).strip()
            title = str(item.get("title", "")).strip()
            if not link or not title:
                continue
            results.append(
                SearchResult(
                    title=title,
                    link=link,
                    snippet=str(item.get("snippet", "")).strip(),
                )
            )
        return results


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").casefold().removeprefix("www.")


def _is_third_party(url: str) -> bool:
    host = _host(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in THIRD_PARTY_DOMAINS)


def _name_tokens(name: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", name.casefold())
        if len(token) >= 3 and token not in {"the", "and", "llc", "inc", "company", "co"}
    }


def _looks_like_business_match(result: SearchResult, business_name: str) -> bool:
    tokens = _name_tokens(business_name)
    if not tokens:
        return False
    haystack = f"{result.title} {result.snippet}".casefold()
    matched = sum(token in haystack for token in tokens)
    return matched >= max(1, (len(tokens) + 1) // 2)


def _emails(result: SearchResult) -> set[str]:
    text = f"{result.title} {result.snippet}"
    return {match.group(0).casefold() for match in EMAIL_RE.finditer(text)}


class NoWebsiteResearchClient:
    """Research businesses for which Google Places supplies no candidate website.

    Two independent searches must both return evidence. Any plausible non-directory
    site blocks the no-website claim. A public email is accepted only when the same
    address appears on at least two distinct matching third-party result URLs.
    """

    def __init__(self, search_client: SearchClient, *, max_results: int = 10) -> None:
        if not 3 <= max_results <= 20:
            raise ValueError("max_results must be between 3 and 20.")
        self._search = search_client
        self._max_results = max_results

    def research(self, scout: ScoutHandoff) -> ResearchHandoff:
        if scout.candidate_website:
            raise NoWebsiteResearchError(
                "Scout already has a candidate website; use the website Researcher path."
            )

        identity = f'"{scout.business_name}"'
        address = scout.formatted_address.strip()
        queries = [
            f"{identity} {address}".strip(),
            f"{identity} official website email",
        ]
        evidence_urls = list(scout.evidence_urls)
        email_sources: dict[str, set[str]] = {}

        for query in queries:
            results = self._search.search(
                query,
                location=address,
                max_results=self._max_results,
            )
            if not results:
                raise NoWebsiteResearchError(
                    "One of the independent web searches returned no evidence; no-website status is unverified."
                )
            for result in results:
                if result.link not in evidence_urls:
                    evidence_urls.append(result.link)
                if not _looks_like_business_match(result, scout.business_name):
                    continue
                if not _is_third_party(result.link):
                    raise NoWebsiteResearchError(
                        f"A plausible official website was found and must be inspected: {result.link}"
                    )
                for email in _emails(result):
                    email_sources.setdefault(email, set()).add(result.link)

        corroborated = {
            email: sources
            for email, sources in email_sources.items()
            if len(sources) >= 2
        }
        if not corroborated:
            raise NoWebsiteResearchError(
                "No public business email was corroborated by at least two distinct matching third-party results."
            )

        contact_email = sorted(
            corroborated,
            key=lambda item: (-len(corroborated[item]), item),
        )[0]

        return ResearchHandoff(
            scout_digest=scout.digest,
            contact_email=contact_email,
            verified_no_website=True,
            contact_verified=True,
            evidence_urls=tuple(evidence_urls),
        )
