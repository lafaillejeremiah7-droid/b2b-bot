from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

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


@dataclass(frozen=True)
class ScoutHandoff:
    """Immutable Google Maps discovery evidence produced by Scout."""

    place_reference: str
    business_name: str
    candidate_website: str = ""
    formatted_address: str = ""
    evidence_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.place_reference.strip():
            raise ValueError("Scout handoff requires a Google Maps place reference.")
        if not self.business_name.strip():
            raise ValueError("Scout handoff requires a business name.")

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_urls"] = list(self.evidence_urls)
        return data

    @property
    def digest(self) -> str:
        return _digest(self.payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ScoutHandoff":
        return cls(
            place_reference=str(payload.get("place_reference", "")),
            business_name=str(payload.get("business_name", "")),
            candidate_website=str(payload.get("candidate_website", "")),
            formatted_address=str(payload.get("formatted_address", "")),
            evidence_urls=tuple(payload.get("evidence_urls", ())),
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
        if len(self.scout_digest) != 64:
            raise ValueError("Research handoff requires the exact Scout digest.")
        if not self.contact_email.strip() or "@" not in self.contact_email:
            raise ValueError("Research handoff requires a candidate contact email.")
        if not self.contact_verified:
            raise ValueError("Research handoff requires verified contact evidence.")
        observations = tuple(item.strip() for item in self.website_observations if item.strip())
        object.__setattr__(self, "website_observations", observations)

        if self.verified_no_website:
            if self.website:
                raise ValueError("No-website evidence cannot also include an official website URL.")
            if self.website_verified:
                raise ValueError("No-website evidence cannot mark a website as verified.")
        else:
            if not self.website.strip():
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
        return cls(
            scout_digest=str(payload.get("scout_digest", "")),
            contact_email=str(payload.get("contact_email", "")),
            website=str(payload.get("website", "")),
            verified_no_website=bool(payload.get("verified_no_website", False)),
            contact_verified=bool(payload.get("contact_verified", False)),
            website_verified=bool(payload.get("website_verified", False)),
            website_observations=tuple(payload.get("website_observations", ())),
            evidence_urls=tuple(payload.get("evidence_urls", ())),
        )


def apply_scout_handoff(lead: "Lead", handoff: ScoutHandoff) -> None:
    if lead.source != "google_maps":
        raise ValueError("Scout handoffs may only be applied to Google Maps leads.")
    if lead.company and lead.company.casefold() != handoff.business_name.casefold():
        raise ValueError("Lead company does not match the Scout handoff.")
    if lead.website and handoff.candidate_website and lead.website != handoff.candidate_website:
        raise ValueError("Lead website does not match Scout's candidate website.")

    lead.company = handoff.business_name
    if handoff.candidate_website:
        lead.website = handoff.candidate_website
    lead.notes["scout_handoff"] = handoff.payload()
    lead.notes["scout_digest"] = handoff.digest
    lead.notes["google_maps_place_id"] = handoff.place_reference
    lead.notes["scout_evidence_urls"] = list(handoff.evidence_urls)


def verify_scout_handoff(lead: "Lead") -> bool:
    payload = lead.notes.get("scout_handoff")
    stored_digest = str(lead.notes.get("scout_digest", ""))
    if not isinstance(payload, dict) or not stored_digest:
        return False
    try:
        handoff = ScoutHandoff.from_payload(payload)
    except (TypeError, ValueError):
        return False
    if handoff.digest != stored_digest:
        return False
    if (lead.company or "").casefold() != handoff.business_name.casefold():
        return False
    if handoff.candidate_website and lead.website != handoff.candidate_website:
        return False
    return True


def apply_research_handoff(lead: "Lead", handoff: ResearchHandoff) -> None:
    if not verify_scout_handoff(lead):
        raise ValueError("A valid Scout handoff is required before Researcher can write evidence.")
    if handoff.scout_digest != lead.notes.get("scout_digest"):
        raise ValueError("Research handoff does not belong to this Scout discovery.")

    lead.email = handoff.contact_email
    lead.website = handoff.website
    lead.notes["research_handoff"] = handoff.payload()
    lead.notes["research_digest"] = handoff.digest
    lead.notes["contact_verified"] = handoff.contact_verified
    lead.notes["website_verified"] = handoff.website_verified
    lead.notes["verified_no_website"] = handoff.verified_no_website
    lead.notes["website_observations"] = list(handoff.website_observations)
    lead.notes["research_evidence_urls"] = list(handoff.evidence_urls)


def verify_research_handoff(lead: "Lead") -> bool:
    if not verify_scout_handoff(lead):
        return False
    payload = lead.notes.get("research_handoff")
    stored_digest = str(lead.notes.get("research_digest", ""))
    if not isinstance(payload, dict) or not stored_digest:
        return False
    try:
        handoff = ResearchHandoff.from_payload(payload)
    except (TypeError, ValueError):
        return False
    if handoff.digest != stored_digest:
        return False
    if handoff.scout_digest != lead.notes.get("scout_digest"):
        return False
    if lead.email.casefold() != handoff.contact_email.casefold():
        return False
    if lead.website != handoff.website:
        return False
    return True
