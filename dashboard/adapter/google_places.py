from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dashboard.services.discovery_handoff import ScoutHandoff

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.websiteUri",
        "places.businessStatus",
        "places.types",
    )
)


class GooglePlacesError(RuntimeError):
    """Raised when the official Google Places API cannot produce a safe result."""


@dataclass(frozen=True)
class PlaceCandidate:
    place_id: str
    business_name: str
    formatted_address: str = ""
    website_uri: str = ""
    business_status: str = ""
    types: tuple[str, ...] = ()

    @property
    def maps_evidence_url(self) -> str:
        return f"https://www.google.com/maps/search/?api=1&query_place_id={self.place_id}"

    def to_scout_handoff(self) -> ScoutHandoff:
        evidence = (self.maps_evidence_url,)
        return ScoutHandoff(
            place_reference=self.place_id,
            business_name=self.business_name,
            candidate_website=self.website_uri,
            formatted_address=self.formatted_address,
            evidence_urls=evidence,
        )


Transport = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]


def _default_transport(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Google API host
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise GooglePlacesError(f"Google Places HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise GooglePlacesError(f"Google Places transport error: {exc.reason}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GooglePlacesError("Google Places returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise GooglePlacesError("Google Places returned a non-object response.")
    return parsed


class GooglePlacesTextSearchClient:
    """Minimal official Places Text Search client used only by Scout.

    It discovers business identity, address, and the website Google associates with
    the place. It deliberately does not invent contact emails or claim that a missing
    websiteUri proves no official website exists; those are Researcher responsibilities.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 15.0,
        transport: Transport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("GOOGLE_MAPS_API_KEY is required for Scout discovery.")
        self._api_key = api_key.strip()
        self._timeout = timeout_seconds
        self._transport = transport or _default_transport

    def search(
        self,
        text_query: str,
        *,
        max_results: int = 10,
        include_service_area_businesses: bool = True,
    ) -> list[PlaceCandidate]:
        query = text_query.strip()
        if not query:
            raise ValueError("Scout search query cannot be blank.")
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20.")

        payload = {
            "textQuery": query,
            "pageSize": max_results,
            "includePureServiceAreaBusinesses": include_service_area_businesses,
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": PLACES_FIELD_MASK,
        }
        response = self._transport(
            PLACES_TEXT_SEARCH_URL,
            payload,
            headers,
            self._timeout,
        )
        raw_places = response.get("places", [])
        if not isinstance(raw_places, list):
            raise GooglePlacesError("Google Places response has an invalid places field.")

        candidates: list[PlaceCandidate] = []
        for raw in raw_places[:max_results]:
            if not isinstance(raw, dict):
                continue
            place_id = str(raw.get("id", "")).strip()
            display_name = raw.get("displayName") or {}
            if not isinstance(display_name, dict):
                display_name = {}
            business_name = str(display_name.get("text", "")).strip()
            if not place_id or not business_name:
                continue
            candidates.append(
                PlaceCandidate(
                    place_id=place_id,
                    business_name=business_name,
                    formatted_address=str(raw.get("formattedAddress", "")).strip(),
                    website_uri=str(raw.get("websiteUri", "")).strip(),
                    business_status=str(raw.get("businessStatus", "")).strip(),
                    types=tuple(
                        item for item in raw.get("types", ())
                        if isinstance(item, str) and item
                    ),
                )
            )
        return candidates
