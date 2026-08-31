from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .discovery_handoff import verify_research_handoff

if TYPE_CHECKING:
    from .six_employee_pipeline import Lead


@dataclass(frozen=True)
class OutreachClearance:
    """Explicit approval bound to one recipient and one Researcher evidence digest."""

    recipient_email: str
    research_digest: str
    channel: str = "email"
    purpose: str = "initial_outreach"
    authority_reference: str = "policy"

    def __post_init__(self) -> None:
        normalized = self.recipient_email.strip().lower()
        object.__setattr__(self, "recipient_email", normalized)
        if not normalized or "@" not in normalized:
            raise ValueError("Outreach clearance requires a valid recipient email.")
        if len(self.research_digest) != 64:
            raise ValueError("Outreach clearance requires the exact Researcher digest.")
        if self.channel != "email":
            raise ValueError("This runtime slice only supports email outreach clearance.")
        if self.purpose != "initial_outreach":
            raise ValueError("Unsupported outreach purpose.")
        if not self.authority_reference.strip():
            raise ValueError("Outreach clearance requires an authority reference.")

    def payload(self) -> dict[str, Any]:
        return asdict(self)

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
    def from_payload(cls, payload: dict[str, Any]) -> "OutreachClearance":
        return cls(
            recipient_email=str(payload.get("recipient_email", "")),
            research_digest=str(payload.get("research_digest", "")),
            channel=str(payload.get("channel", "email")),
            purpose=str(payload.get("purpose", "initial_outreach")),
            authority_reference=str(payload.get("authority_reference", "policy")),
        )


def apply_outreach_clearance(lead: "Lead", clearance: OutreachClearance) -> None:
    if not verify_research_handoff(lead):
        raise ValueError("A valid Researcher handoff is required before clearance.")
    if lead.email.strip().lower() != clearance.recipient_email:
        raise ValueError("Clearance recipient does not match the researched lead.")
    if lead.notes.get("research_digest") != clearance.research_digest:
        raise ValueError("Clearance does not belong to this Researcher evidence.")
    lead.notes["outreach_clearance"] = clearance.payload()
    lead.notes["outreach_clearance_digest"] = clearance.digest


def verify_outreach_clearance(lead: "Lead") -> bool:
    if not verify_research_handoff(lead):
        return False
    payload = lead.notes.get("outreach_clearance")
    stored_digest = str(lead.notes.get("outreach_clearance_digest", ""))
    if not isinstance(payload, dict) or not stored_digest:
        return False
    try:
        clearance = OutreachClearance.from_payload(payload)
    except (TypeError, ValueError):
        return False
    if clearance.digest != stored_digest:
        return False
    if lead.email.strip().lower() != clearance.recipient_email:
        return False
    if lead.notes.get("research_digest") != clearance.research_digest:
        return False
    return True
