from __future__ import annotations

import hashlib
import json
import string
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from dashboard.services.six_employee_pipeline import Lead


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _payload_text(payload: dict[str, Any], key: str, *, default: str = "") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text.")
    return value


def _payload_bool(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean.")
    return value


def _string_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value in (None, ()):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple of text values.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} must contain only text values.")
        cleaned = item.strip()
        if cleaned:
            normalized.append(cleaned)
    return tuple(normalized)


def _http_url(value: str, *, field_name: str, allow_blank: bool = True) -> str:
    cleaned = value.strip()
    if not cleaned and allow_blank:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL.")
    return cleaned


def _sha256_text(value: str, *, field_name: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) != 64 or any(char not in string.hexdigits for char in cleaned):
        raise ValueError(f"{field_name} must be a SHA-256 digest.")
    return cleaned


@dataclass(frozen=True)
class ScoutHandoff:
    """Immutable Google Maps discovery evidence produced by Scout."""

    place_reference: str
    business_name: str
    candidate_website: str = ""
    formatted_address: str = ""
    evidence_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        place_reference = self.place_reference.strip()
        business_name = self.business_name.strip()
        candidate_website = _http_url(
            self.candidate_website,
            field_name="Scout candidate website",
        )
        formatted_address = self.formatted_address.strip()
        evidence_urls = tuple(
            _http_url(item, field_name="Scout evidence URL", allow_blank=False)
            for item in _string_tuple(self.evidence_urls, field_name="Scout evidence URLs")
        )
        if not place_reference:
            raise ValueError("Scout handoff requires a Google Maps place reference.")
        if not business_name:
            raise ValueError("Scout handoff requires a business name.")
        object.__setattr__(self, "place_reference", place_reference)
        object.__setattr__(self, "business_name", business_name)
        object.__setattr__(self, "candidate_website", candidate_website)
        object.__setattr__(self, "formatted_address", formatted_address)
        object.__setattr__(self, "evidence_urls", evidence_urls)

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_urls"] = list(self.evidence_urls)
        return data

    @property
    def digest(self) -> str:
        return _digest(self.payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ScoutHandoff":
        if not isinstance(payload, dict):
            raise ValueError("Scout handoff payload must be an object.")
        return cls(
            place_reference=_payload_text(payload, "place_reference"),
            business_name=_payload_text(payload, "business_name"),
            candidate_website=_payload_text(payload, "candidate_website"),
            formatted_address=_payload_text(payload, "formatted_address"),
            evidence_urls=_string_tuple(
                payload.get("evidence_urls", ()),
                field_name="Scout evidence URLs",
            ),
        )


@dataclass(frozen=True)
class ResearchHandoff:
    """Immutable contact/site verification produced by Researcher."""

    scout_digest: str
    contact_email: str
    website: str = ""
    verified_no_website: bool = False
    contact_verified: bool = False
    website_verified: bool = False
    website_observations: tuple[str, ...] = ()
    evidence_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        scout_digest = _sha256_text(self.scout_digest, field_name="Scout digest")
        contact_email = self.contact_email.strip().lower()
        website = _http_url(self.website, field_name="Research website")
        observations = _string_tuple(
            self.website_observations,
            field_name="Website observations",
        )
        evidence_urls = tuple(
            _http_url(item, field_name="Research evidence URL", allow_blank=False)
            for item in _string_tuple(self.evidence_urls, field_name="Research evidence URLs")
        )
        if not contact_email or "@" not in contact_email or len(contact_email) > 320:
            raise ValueError("Research handoff requires a valid candidate contact email.")
        if not isinstance(self.contact_verified, bool) or not isinstance(self.website_verified, bool) or not isinstance(self.verified_no_website, bool):
            raise ValueError("Research verification flags must be booleans.")
        if not self.contact_verified:
            raise ValueError("Research handoff requires verified contact evidence.")

        object.__setattr__(self, "scout_digest", scout_digest)
        object.__setattr__(self, "contact_email", contact_email)
        object.__setattr__(self, "website", website)
        object.__setattr__(self, "website_observations", observations)
        object.__setattr__(self, "evidence_urls", evidence_urls)

        if self.verified_no_website:
            if website:
                raise ValueError("No-website evidence cannot also include an official website URL.")
            if self.website_verified:
                raise ValueError("No-website evidence cannot mark a website as verified.")
        else:
            if not website:
                raise ValueError("Website leads require an official website URL.")
            if not self.website_verified:
                raise ValueError("Website leads require website verification.")
            if len(observations) < 2:
                raise ValueError("Website leads require at least two verified observations.")

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["website_observations"] = list(self.website_observations)
        data["evidence_urls"] = list(self.evidence_urls)
        return data

    @property
    def digest(self) -> str:
        return _digest(self.payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ResearchHandoff":
        if not isinstance(payload, dict):
            raise ValueError("Research handoff payload must be an object.")
        return cls(
            scout_digest=_payload_text(payload, "scout_digest"),
            contact_email=_payload_text(payload, "contact_email"),
            website=_payload_text(payload, "website"),
            verified_no_website=_payload_bool(payload, "verified_no_website"),
            contact_verified=_payload_bool(payload, "contact_verified"),
            website_verified=_payload_bool(payload, "website_verified"),
            website_observations=_string_tuple(
                payload.get("website_observations", ()),
                field_name="Website observations",
            ),
            evidence_urls=_string_tuple(
                payload.get("evidence_urls", ()),
                field_name="Research evidence URLs",
            ),
        )


def _scout_from_verified_lead(lead: "Lead") -> ScoutHandoff | None:
    payload = lead.notes.get("scout_handoff")
    stored_digest = str(lead.notes.get("scout_digest", ""))
    if lead.source != "google_maps" or not isinstance(payload, dict) or not stored_digest:
        return None
    try:
        handoff = ScoutHandoff.from_payload(payload)
    except (TypeError, ValueError, AttributeError):
        return None
    if handoff.digest != stored_digest:
        return None
    if (lead.company or "").strip().casefold() != handoff.business_name.casefold():
        return None
    if (lead.website or "").strip() != handoff.candidate_website:
        return None
    return handoff


def apply_scout_handoff(lead: "Lead", handoff: ScoutHandoff) -> None:
    if lead.source != "google_maps":
        raise ValueError("Scout handoffs may only be applied to Google Maps leads.")
    if lead.company and lead.company.strip().casefold() != handoff.business_name.casefold():
        raise ValueError("Lead company does not match the Scout handoff.")
    if lead.website and lead.website.strip() != handoff.candidate_website:
        raise ValueError("Lead website does not match Scout's candidate website.")

    lead.company = handoff.business_name
    # Scout is the canonical owner of discovery website state. An empty
    # candidate must clear any stale/manual website rather than leave drift.
    lead.website = handoff.candidate_website
    lead.notes["scout_handoff"] = handoff.payload()
    lead.notes["scout_digest"] = handoff.digest
    lead.notes["google_maps_place_id"] = handoff.place_reference
    lead.notes["scout_evidence_urls"] = list(handoff.evidence_urls)


def verify_scout_handoff(lead: "Lead") -> bool:
    return _scout_from_verified_lead(lead) is not None


def apply_research_handoff(lead: "Lead", handoff: ResearchHandoff) -> None:
    scout = _scout_from_verified_lead(lead)
    if scout is None:
        raise ValueError("A valid Scout handoff is required before Researcher can write evidence.")
    if handoff.scout_digest != lead.notes.get("scout_digest"):
        raise ValueError("Research handoff does not belong to this Scout discovery.")

    # The Researcher must complete the path Scout assigned. A buggy client may
    # not silently switch a website lead into no-website mode (or vice versa).
    if scout.candidate_website:
        if handoff.verified_no_website or handoff.website != scout.candidate_website:
            raise ValueError("Research handoff does not match Scout's candidate website path.")
    elif not handoff.verified_no_website or handoff.website:
        raise ValueError("No-candidate Scout handoffs require verified no-website research.")

    lead.email = handoff.contact_email
    lead.website = handoff.website
    lead.notes["research_handoff"] = handoff.payload()
    lead.notes["research_digest"] = handoff.digest
    lead.notes["contact_verified"] = handoff.contact_verified
    lead.notes["website_verified"] = handoff.website_verified
    lead.notes["verified_no_website"] = handoff.verified_no_website
    lead.notes["website_observations"] = list(handoff.website_observations)
    lead.notes["research_evidence_urls"] = list(handoff.evidence_urls)


def verified_research_handoff(lead: "Lead") -> ResearchHandoff | None:
    scout = _scout_from_verified_lead(lead)
    if scout is None:
        return None
    payload = lead.notes.get("research_handoff")
    stored_digest = str(lead.notes.get("research_digest", ""))
    if not isinstance(payload, dict) or not stored_digest:
        return None
    try:
        handoff = ResearchHandoff.from_payload(payload)
    except (TypeError, ValueError, AttributeError):
        return None
    if handoff.digest != stored_digest:
        return None
    if handoff.scout_digest != lead.notes.get("scout_digest"):
        return None
    if lead.email.strip().casefold() != handoff.contact_email.casefold():
        return None
    if (lead.website or "").strip() != handoff.website:
        return None
    if scout.candidate_website:
        if handoff.verified_no_website or handoff.website != scout.candidate_website:
            return None
    elif not handoff.verified_no_website or handoff.website:
        return None
    return handoff


def verify_research_handoff(lead: "Lead") -> bool:
    return verified_research_handoff(lead) is not None
