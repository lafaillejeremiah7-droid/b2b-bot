"""Strict contracts for the Luna -> Terra -> Sol outreach-strategy pipeline."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from dashboard.discovery.contracts import (
    DecisionOutcome,
    DiscoveryPacket,
    Stage as DiscoveryStage,
)
from dashboard.discovery.provenance import content_digest
from dashboard.qualification.contracts import (
    QualificationOutcome,
    QualificationPacket,
    Stage as QualificationStage,
)

SCHEMA_VERSION = "outreach-strategy-handoff-v1"
PROMPT_VERSION = "outreach-strategy-corporation-v1"


class ContractError(ValueError):
    """Raised when an upstream handoff or model output violates its schema."""


class Stage(str, Enum):
    DRAFT = "DRAFT"
    AUDIT = "AUDIT"
    APPROVE = "APPROVE"


class Channel(str, Enum):
    EMAIL = "EMAIL"
    CALL = "CALL"


class ClaimVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"


class OutreachOutcome(str, Enum):
    READY_FOR_HUMAN_APPROVAL = "READY_FOR_HUMAN_APPROVAL"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REJECTED = "REJECTED"


_PROHIBITED = re.compile(
    r"(?:\$\s*\d|\b\d+(?:\.\d+)?\s*%|\bprobabilit(?:y|ies)\b|"
    r"\bodds\b|\bguarantee(?:d|s)?\b)",
    re.IGNORECASE,
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractError(f"{label} must be an object with string keys")
    return value


def _exact(value: Any, label: str, fields: set[str]) -> Mapping[str, Any]:
    data = _mapping(value, label)
    if set(data) != fields:
        raise ContractError(
            f"{label} fields mismatch; missing={sorted(fields - set(data))}, "
            f"extra={sorted(set(data) - fields)}"
        )
    return data


def _text(
    value: Any,
    label: str,
    *,
    minimum: int = 1,
    maximum: int,
    sales_copy: bool = False,
) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} must contain {minimum}..{maximum} characters")
    if minimum and not value.strip():
        raise ContractError(f"{label} must not be blank")
    if sales_copy and _PROHIBITED.search(value):
        raise ContractError(f"{label} contains a price, probability, or guarantee")
    return value


def _digest(value: Any, label: str) -> str:
    digest = _text(value, label, minimum=64, maximum=64)
    if any(character not in "0123456789abcdef" for character in digest):
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _strings(value: Any, label: str, *, maximum: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{label} must be a list with at most {maximum} items")
    return tuple(_text(item, f"{label} item", maximum=200) for item in value)


def _valid_discovery(packet: DiscoveryPacket) -> bool:
    if packet.decision.outcome is not DecisionOutcome.ACCEPTED or len(packet.stages) != 3:
        return False
    first, second, third = packet.stages
    return (
        tuple(item.stage for item in packet.stages)
        == (DiscoveryStage.EXTRACT, DiscoveryStage.VERIFY, DiscoveryStage.ADJUDICATE)
        and first.parent_digest == packet.request_digest
        and second.parent_digest == first.output_digest
        and third.parent_digest == second.output_digest
        and third.output_digest == packet.decision.digest
    )


def _valid_qualification(packet: QualificationPacket) -> bool:
    if (
        packet.decision.outcome is not QualificationOutcome.QUALIFIED
        or len(packet.stages) != 3
    ):
        return False
    first, second, third = packet.stages
    return (
        tuple(item.stage for item in packet.stages)
        == (QualificationStage.RESEARCH, QualificationStage.AUDIT, QualificationStage.QUALIFY)
        and first.parent_digest == packet.request_digest
        and second.parent_digest == first.output_digest
        and third.parent_digest == second.output_digest
        and third.output_digest == packet.decision.digest
    )


@dataclass(frozen=True)
class OutreachRequest:
    idempotency_key: str
    discovery_packet: DiscoveryPacket
    qualification_packet: QualificationPacket

    def __post_init__(self) -> None:
        _text(self.idempotency_key, "idempotency_key", maximum=128)
        if not isinstance(self.discovery_packet, DiscoveryPacket) or not _valid_discovery(
            self.discovery_packet
        ):
            raise ContractError("discovery_packet is not an accepted complete chain")
        if not isinstance(
            self.qualification_packet, QualificationPacket
        ) or not _valid_qualification(self.qualification_packet):
            raise ContractError("qualification_packet is not a qualified complete chain")
        if (
            self.qualification_packet.discovery_handoff_digest
            != self.discovery_packet.handoff_digest
        ):
            raise ContractError("upstream packets describe different discovery handoffs")

    @property
    def request_digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "discovery_handoff_digest": self.discovery_packet.handoff_digest,
            "qualification_handoff_digest": self.qualification_packet.handoff_digest,
        })

    @property
    def operation_id(self) -> str:
        return content_digest({
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest,
        })


@dataclass(frozen=True)
class IdempotencyClaim:
    idempotency_key: str
    request_digest: str
    operation_id: str

    @classmethod
    def for_request(cls, request: OutreachRequest) -> "IdempotencyClaim":
        return cls(request.idempotency_key, request.request_digest, request.operation_id)

    def matches(self, request: OutreachRequest) -> bool:
        return self == self.for_request(request)


@dataclass(frozen=True)
class MessageClaim:
    text: str
    supporting_fields: tuple[str, ...]

    @property
    def digest(self) -> str:
        return content_digest(self)

    @classmethod
    def from_json(
        cls,
        value: Any,
        *,
        available_fields: frozenset[str],
    ) -> "MessageClaim":
        data = _exact(value, "message claim", {"text", "supporting_fields"})
        text = _text(data["text"], "message claim text", maximum=1000, sales_copy=True)
        fields = _strings(data["supporting_fields"], "supporting_fields", maximum=10)
        if not fields or len(set(fields)) != len(fields):
            raise ContractError("message claim requires unique supporting fields")
        if not set(fields).issubset(available_fields):
            raise ContractError("message claim cites an unavailable verified field")
        return cls(text, fields)


@dataclass(frozen=True)
class FollowUp:
    delay_days: int
    message: str

    @classmethod
    def from_json(cls, value: Any) -> "FollowUp":
        data = _exact(value, "follow-up", {"delay_days", "message"})
        delay = data["delay_days"]
        if isinstance(delay, bool) or not isinstance(delay, int) or not 1 <= delay <= 30:
            raise ContractError("follow-up delay_days must be an integer from 1 to 30")
        return cls(
            delay,
            _text(data["message"], "follow-up message", maximum=2000, sales_copy=True),
        )


@dataclass(frozen=True)
class DraftStrategy:
    parent_digest: str
    channel: Channel
    audience_role: str
    subject: str
    opening: str
    value_argument: str
    call_to_action: str
    claims: tuple[MessageClaim, ...]
    follow_ups: tuple[FollowUp, ...]

    @classmethod
    def from_json(cls, value: Any, *, request: OutreachRequest) -> "DraftStrategy":
        data = _exact(
            value,
            "Luna outreach draft",
            {
                "schema_version",
                "parent_digest",
                "channel",
                "audience_role",
                "subject",
                "opening",
                "value_argument",
                "call_to_action",
                "claims",
                "follow_ups",
            },
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Luna draft schema_version is unsupported")
        if data["parent_digest"] != request.request_digest:
            raise ContractError("Luna draft parent digest mismatch")
        try:
            channel = Channel(data["channel"])
        except (TypeError, ValueError) as exc:
            raise ContractError("Luna draft channel is unsupported") from exc
        subject = _text(
            data["subject"],
            "subject",
            minimum=1 if channel is Channel.EMAIL else 0,
            maximum=200,
            sales_copy=True,
        )
        if channel is Channel.CALL and subject:
            raise ContractError("A call strategy must use an empty subject")
        available = frozenset(
            request.discovery_packet.decision.lead_payload
        ) | frozenset(request.qualification_packet.decision.opportunity_profile)
        if not isinstance(data["claims"], list) or not 1 <= len(data["claims"]) <= 20:
            raise ContractError("Luna draft claims must contain 1..20 items")
        claims = tuple(
            MessageClaim.from_json(item, available_fields=available)
            for item in data["claims"]
        )
        if len({claim.digest for claim in claims}) != len(claims):
            raise ContractError("Luna draft claims must be unique")
        if not isinstance(data["follow_ups"], list) or len(data["follow_ups"]) > 3:
            raise ContractError("Luna draft follow_ups must contain at most 3 items")
        follow_ups = tuple(FollowUp.from_json(item) for item in data["follow_ups"])
        delays = tuple(item.delay_days for item in follow_ups)
        if tuple(sorted(set(delays))) != delays:
            raise ContractError("follow-up delays must be unique and increasing")
        return cls(
            request.request_digest,
            channel,
            _text(data["audience_role"], "audience_role", maximum=200),
            subject,
            _text(data["opening"], "opening", maximum=2000, sales_copy=True),
            _text(
                data["value_argument"],
                "value_argument",
                maximum=4000,
                sales_copy=True,
            ),
            _text(
                data["call_to_action"],
                "call_to_action",
                maximum=1000,
                sales_copy=True,
            ),
            claims,
            follow_ups,
        )

    @property
    def digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "parent_digest": self.parent_digest,
            "channel": self.channel,
            "audience_role": self.audience_role,
            "subject": self.subject,
            "opening": self.opening,
            "value_argument": self.value_argument,
            "call_to_action": self.call_to_action,
            "claims": self.claims,
            "follow_ups": self.follow_ups,
        })


@dataclass(frozen=True)
class ClaimAssessment:
    claim_digest: str
    verdict: ClaimVerdict
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any) -> "ClaimAssessment":
        data = _exact(value, "claim assessment", {"claim_digest", "verdict", "reason_codes"})
        digest = _digest(data["claim_digest"], "claim_digest")
        try:
            verdict = ClaimVerdict(data["verdict"])
        except (TypeError, ValueError) as exc:
            raise ContractError("claim verdict is unsupported") from exc
        reasons = _strings(data["reason_codes"], "reason_codes")
        if not reasons:
            raise ContractError("claim assessment requires a reason code")
        return cls(digest, verdict, reasons)


@dataclass(frozen=True)
class StrategyAudit:
    parent_digest: str
    assessments: tuple[ClaimAssessment, ...]
    violations: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, *, draft: DraftStrategy) -> "StrategyAudit":
        data = _exact(
            value,
            "Terra strategy audit",
            {"schema_version", "parent_digest", "assessments", "violations"},
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Terra audit schema_version is unsupported")
        if data["parent_digest"] != draft.digest:
            raise ContractError("Terra audit parent digest mismatch")
        if not isinstance(data["assessments"], list):
            raise ContractError("Terra assessments must be a list")
        assessments = tuple(ClaimAssessment.from_json(item) for item in data["assessments"])
        expected = {claim.digest for claim in draft.claims}
        actual = {item.claim_digest for item in assessments}
        if len(actual) != len(assessments) or actual != expected:
            raise ContractError("Terra must assess every message claim exactly once")
        return cls(
            draft.digest,
            assessments,
            _strings(data["violations"], "Terra violations"),
        )

    @property
    def digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "parent_digest": self.parent_digest,
            "assessments": self.assessments,
            "violations": self.violations,
        })


@dataclass(frozen=True)
class OutreachDecision:
    parent_digest: str
    draft_digest: str
    outcome: OutreachOutcome
    approved_claim_digests: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(
        cls,
        value: Any,
        *,
        draft: DraftStrategy,
        audit: StrategyAudit,
    ) -> "OutreachDecision":
        data = _exact(
            value,
            "Sol outreach decision",
            {
                "schema_version",
                "parent_digest",
                "draft_digest",
                "outcome",
                "approved_claim_digests",
                "reason_codes",
            },
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Sol decision schema_version is unsupported")
        if data["parent_digest"] != audit.digest or data["draft_digest"] != draft.digest:
            raise ContractError("Sol decision digest mismatch")
        try:
            outcome = OutreachOutcome(data["outcome"])
        except (TypeError, ValueError) as exc:
            raise ContractError("Sol outreach outcome is unsupported") from exc
        approved = tuple(
            _digest(item, "approved claim digest")
            for item in _strings(
                data["approved_claim_digests"],
                "approved_claim_digests",
                maximum=20,
            )
        )
        if len(set(approved)) != len(approved):
            raise ContractError("approved claim digests must be unique")
        reasons = _strings(data["reason_codes"], "Sol reason_codes")
        if not reasons:
            raise ContractError("Sol decision requires a reason code")
        verified = {
            item.claim_digest
            for item in audit.assessments
            if item.verdict is ClaimVerdict.VERIFIED
        }
        expected = {claim.digest for claim in draft.claims}
        if outcome is OutreachOutcome.READY_FOR_HUMAN_APPROVAL:
            if audit.violations or verified != expected or set(approved) != expected:
                raise ContractError("A ready strategy requires a clean, fully verified audit")
        elif approved:
            raise ContractError("Only a ready strategy may expose approved claims")
        return cls(audit.digest, draft.digest, outcome, approved, reasons)

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class StageRecord:
    stage: Stage
    model_id: str
    prompt_version: str
    parent_digest: str
    output_digest: str


@dataclass(frozen=True)
class OutreachPacket:
    operation_id: str
    request_digest: str
    discovery_handoff_digest: str
    qualification_handoff_digest: str
    draft: DraftStrategy
    decision: OutreachDecision
    human_approval_required: bool
    stages: tuple[StageRecord, ...]

    def __post_init__(self) -> None:
        if self.human_approval_required is not True:
            raise ContractError("Every outreach packet must require human approval")

    @property
    def handoff_digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class OutreachFailure:
    operation_id: str
    request_digest: str
    failed_stage: Stage | None
    error_code: str
    completed_stages: tuple[StageRecord, ...] = ()
