from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dashboard.services.six_employee_pipeline import Lead


@dataclass(frozen=True)
class DiscoveryHandoff:
    """Immutable Scout -> Researcher evidence contract for a Google Maps lead."""

    place_reference: str
    business_name: str
    contact_email: str
    website: str = ""
    verified_no_website: bool = False
    contact_verified: bool = False
    website_verified: bool = False
    website_observations: tuple[str, ...] = ()
    evidence_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.place_reference.strip():
            raise ValueError("Google Maps handoff requires a place reference.")
        if not self.business_name.strip():
            raise ValueError("Google Maps handoff requires a business name.")
        if not self.contact_email.strip() or "@" not in self.contact_email:
            raise ValueError("Google Maps handoff requires a candidate contact email.")
        if not self.contact_verified:
            raise ValueError("Google Maps handoff requires verified contact evidence.")
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
        encoded = json.dumps(
            self.payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DiscoveryHandoff":
        return cls(
            place_reference=str(payload.get("place_reference", "")),
            business_name=str(payload.get("business_name", "")),
            contact_email=str(payload.get("contact_email", "")),
            website=str(payload.get("website", "")),
            verified_no_website=bool(payload.get("verified_no_website", False)),
            contact_verified=bool(payload.get("contact_verified", False)),
            website_verified=bool(payload.get("website_verified", False)),
            website_observations=tuple(payload.get("website_observations", ())),
            evidence_urls=tuple(payload.get("evidence_urls", ())),
        )


def apply_discovery_handoff(lead: "Lead", handoff: DiscoveryHandoff) -> None:
    """Attach a validated handoff to a lead without allowing identity drift."""
    if lead.source != "google_maps":
        raise ValueError("Discovery handoffs may only be applied to Google Maps leads.")
    if lead.email and lead.email.casefold() != handoff.contact_email.casefold():
        raise ValueError("Lead email does not match the verified discovery handoff.")
    if lead.company and lead.company.casefold() != handoff.business_name.casefold():
        raise ValueError("Lead company does not match the verified discovery handoff.")
    if lead.website and lead.website != handoff.website:
        raise ValueError("Lead website does not match the verified discovery handoff.")

    lead.email = handoff.contact_email
    lead.company = handoff.business_name
    lead.website = handoff.website
    lead.notes["discovery_handoff"] = handoff.payload()
    lead.notes["discovery_digest"] = handoff.digest
    lead.notes["discovery_verified"] = True
    lead.notes["google_maps_place_id"] = handoff.place_reference
    lead.notes["contact_verified"] = handoff.contact_verified
    lead.notes["website_verified"] = handoff.website_verified
    lead.notes["verified_no_website"] = handoff.verified_no_website
    lead.notes["website_observations"] = list(handoff.website_observations)
    lead.notes["evidence_urls"] = list(handoff.evidence_urls)


def verify_discovery_handoff(lead: "Lead") -> bool:
    """Reconstruct and verify the handoff digest plus lead identity fields."""
    payload = lead.notes.get("discovery_handoff")
    stored_digest = str(lead.notes.get("discovery_digest", ""))
    if not isinstance(payload, dict) or not stored_digest:
        return False
    try:
        handoff = DiscoveryHandoff.from_payload(payload)
    except (TypeError, ValueError):
        return False
    if handoff.digest != stored_digest:
        return False
    if lead.email.casefold() != handoff.contact_email.casefold():
        return False
    if (lead.company or "").casefold() != handoff.business_name.casefold():
        return False
    if lead.website != handoff.website:
        return False
    return True
