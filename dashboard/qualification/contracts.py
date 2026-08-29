"""Strict contracts for the Luna -> Terra -> Sol qualification pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeAlias
from urllib.parse import urlparse

from dashboard.discovery.contracts import (
    DecisionOutcome,
    DiscoveryPacket,
    Stage as DiscoveryStage,
)
from dashboard.discovery.provenance import content_digest

SCHEMA_VERSION = "qualification-handoff-v1"
PROMPT_VERSION = "qualification-corporation-v1"
MAX_SOURCES = 20
MAX_SOURCE_CONTENT = 50_000

Scalar: TypeAlias = str | int


class ContractError(ValueError):
    """Raised when an input or model output violates an exact schema."""


class Stage(str, Enum):
    RESEARCH = "RESEARCH"
    AUDIT = "AUDIT"
    QUALIFY = "QUALIFY"


class ClaimVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"


class QualificationOutcome(str, Enum):
    QUALIFIED = "QUALIFIED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REJECTED = "REJECTED"


FIELD_LIMITS: Mapping[str, tuple[type, int, int]] = {
    "website_gap_summary": (str, 1, 2000),
    "economic_argument": (str, 1, 2000),
    "service_fit": (str, 1, 1000),
    "decision_maker_name": (str, 1, 200),
    "decision_maker_role": (str, 1, 200),
    "contact_channel": (str, 1, 500),
    "offer_fit": (int, 1, 5),
    "urgency": (int, 1, 5),
    "ability_to_pay": (int, 1, 5),
    "contactability": (int, 1, 5),
    "evidence_quality": (int, 1, 5),
}

REQUIRED_QUALIFIED_FIELDS = frozenset({
    "website_gap_summary",
    "economic_argument",
    "service_fit",
    "offer_fit",
    "urgency",
    "ability_to_pay",
    "contactability",
    "evidence_quality",
})
SCORE_FIELDS = (
    "offer_fit",
    "urgency",
    "ability_to_pay",
    "contactability",
    "evidence_quality",
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


def _text(value: Any, label: str, *, minimum: int = 1, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} must contain {minimum}..{maximum} characters")
    if minimum and not value.strip():
        raise ContractError(f"{label} must not be blank")
    return value


def _digest(value: Any, label: str) -> str:
    digest = _text(value, label, minimum=64, maximum=64)
    if any(character not in "0123456789abcdef" for character in digest):
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _web_url(value: Any, label: str) -> str:
    url = _text(value, label, maximum=2048)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContractError(f"{label} must be an absolute http or https URL")
    if any(character.isspace() for character in url):
        raise ContractError(f"{label} must not contain whitespace")
    return url


def _strings(value: Any, label: str, *, maximum: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{label} must be a list with at most {maximum} items")
    return tuple(_text(item, f"{label} item", maximum=200) for item in value)


def _scalar(value: Any, field_name: str) -> Scalar:
    specification = FIELD_LIMITS.get(field_name)
    if specification is None:
        raise ContractError(f"Unknown or unauthorized qualification field: {field_name}")
    expected, lower, upper = specification
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError(f"{field_name} must be an integer")
        if not lower <= value <= upper:
            raise ContractError(f"{field_name} is outside its allowed range")
    else:
        if not isinstance(value, str) or not lower <= len(value) <= upper:
            raise ContractError(f"{field_name} is outside its allowed length")
        if not value.strip():
            raise ContractError(f"{field_name} must not be blank")
    return value


def _valid_discovery_packet(packet: DiscoveryPacket) -> bool:
    if packet.decision.outcome is not DecisionOutcome.ACCEPTED:
        return False
    if tuple(record.stage for record in packet.stages) != (
        DiscoveryStage.EXTRACT,
        DiscoveryStage.VERIFY,
        DiscoveryStage.ADJUDICATE,
    ):
        return False
    first, second, third = packet.stages
    return (
        first.parent_digest == packet.request_digest
        and second.parent_digest == first.output_digest
        and third.parent_digest == second.output_digest
        and third.output_digest == packet.decision.digest
    )


@dataclass(frozen=True)
class QualificationSource:
    url: str
    title: str
    content: str
    retrieved_at: str

    def __post_init__(self) -> None:
        _web_url(self.url, "source.url")
        _text(self.title, "source.title", maximum=500)
        _text(self.content, "source.content", maximum=MAX_SOURCE_CONTENT)
        try:
            parsed = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ContractError("source.retrieved_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ContractError("source.retrieved_at must include a timezone")


@dataclass(frozen=True)
class QualificationRequest:
    idempotency_key: str
    discovery_packet: DiscoveryPacket
    sources: tuple[QualificationSource, ...]

    def __post_init__(self) -> None:
        _text(self.idempotency_key, "idempotency_key", maximum=128)
        if not isinstance(self.discovery_packet, DiscoveryPacket):
            raise ContractError("discovery_packet must be a DiscoveryPacket")
        if not _valid_discovery_packet(self.discovery_packet):
            raise ContractError("discovery_packet is not an accepted complete chain")
        if not isinstance(self.sources, tuple) or not all(
            isinstance(source, QualificationSource) for source in self.sources
        ):
            raise ContractError("sources must be a tuple of QualificationSource values")
        if not 1 <= len(self.sources) <= MAX_SOURCES:
            raise ContractError(f"sources must contain 1..{MAX_SOURCES} items")

    @property
    def request_digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "discovery_handoff_digest": self.discovery_packet.handoff_digest,
            "sources": self.sources,
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
    def for_request(cls, request: QualificationRequest) -> "IdempotencyClaim":
        return cls(request.idempotency_key, request.request_digest, request.operation_id)

    def matches(self, request: QualificationRequest) -> bool:
        return self == self.for_request(request)


@dataclass(frozen=True)
class OpportunityClaim:
    field_name: str
    value: Scalar
    source_indexes: tuple[int, ...]

    @property
    def digest(self) -> str:
        return content_digest(self)

    @classmethod
    def from_json(cls, value: Any, *, source_count: int) -> "OpportunityClaim":
        data = _exact(value, "opportunity claim", {"field_name", "value", "source_indexes"})
        field_name = _text(data["field_name"], "claim.field_name", maximum=64)
        scalar = _scalar(data["value"], field_name)
        indexes = data["source_indexes"]
        if not isinstance(indexes, list) or not indexes:
            raise ContractError("claim.source_indexes must be a non-empty list")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in indexes):
            raise ContractError("claim.source_indexes must contain integers")
        normalized = tuple(indexes)
        if len(set(normalized)) != len(normalized):
            raise ContractError("claim.source_indexes must not contain duplicates")
        if any(item < 0 or item >= source_count for item in normalized):
            raise ContractError("claim.source_indexes contains an unavailable source")
        return cls(field_name, scalar, normalized)


@dataclass(frozen=True)
class Research:
    parent_digest: str
    claims: tuple[OpportunityClaim, ...]
    limitations: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, *, request: QualificationRequest) -> "Research":
        data = _exact(
            value,
            "Luna research",
            {"schema_version", "parent_digest", "claims", "limitations"},
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Luna research schema_version is unsupported")
        if data["parent_digest"] != request.request_digest:
            raise ContractError("Luna research parent digest mismatch")
        if not isinstance(data["claims"], list) or len(data["claims"]) > 50:
            raise ContractError("Luna claims must be a list with at most 50 items")
        claims = tuple(
            OpportunityClaim.from_json(item, source_count=len(request.sources))
            for item in data["claims"]
        )
        if len({claim.field_name for claim in claims}) != len(claims):
            raise ContractError("Luna may emit at most one claim per qualification field")
        return cls(
            request.request_digest,
            claims,
            _strings(data["limitations"], "Luna limitations"),
        )

    @property
    def digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "parent_digest": self.parent_digest,
            "claims": self.claims,
            "limitations": self.limitations,
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
            raise ContractError("claim assessment verdict is unsupported") from exc
        reasons = _strings(data["reason_codes"], "reason_codes")
        if not reasons:
            raise ContractError("claim assessment requires a reason code")
        return cls(digest, verdict, reasons)


@dataclass(frozen=True)
class Audit:
    parent_digest: str
    assessments: tuple[ClaimAssessment, ...]
    conflicts: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, *, research: Research) -> "Audit":
        data = _exact(
            value,
            "Terra audit",
            {"schema_version", "parent_digest", "assessments", "conflicts"},
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Terra audit schema_version is unsupported")
        if data["parent_digest"] != research.digest:
            raise ContractError("Terra audit parent digest mismatch")
        if not isinstance(data["assessments"], list):
            raise ContractError("Terra assessments must be a list")
        assessments = tuple(ClaimAssessment.from_json(item) for item in data["assessments"])
        expected = {claim.digest for claim in research.claims}
        actual = {assessment.claim_digest for assessment in assessments}
        if len(actual) != len(assessments) or actual != expected:
            raise ContractError("Terra must assess every Luna claim exactly once")
        return cls(
            research.digest,
            assessments,
            _strings(data["conflicts"], "Terra conflicts"),
        )

    @property
    def digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "parent_digest": self.parent_digest,
            "assessments": self.assessments,
            "conflicts": self.conflicts,
        })


@dataclass(frozen=True)
class QualificationDecision:
    parent_digest: str
    outcome: QualificationOutcome
    opportunity_profile: Mapping[str, Scalar]
    evidence_digests: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(
        cls,
        value: Any,
        *,
        research: Research,
        audit: Audit,
    ) -> "QualificationDecision":
        data = _exact(
            value,
            "Sol qualification",
            {
                "schema_version",
                "parent_digest",
                "outcome",
                "opportunity_profile",
                "evidence_digests",
                "reason_codes",
            },
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Sol qualification schema_version is unsupported")
        if data["parent_digest"] != audit.digest:
            raise ContractError("Sol qualification parent digest mismatch")
        try:
            outcome = QualificationOutcome(data["outcome"])
        except (TypeError, ValueError) as exc:
            raise ContractError("Sol qualification outcome is unsupported") from exc
        profile = {
            field: _scalar(item, field)
            for field, item in _mapping(
                data["opportunity_profile"], "Sol opportunity_profile"
            ).items()
        }
        evidence = _strings(data["evidence_digests"], "Sol evidence_digests", maximum=50)
        reasons = _strings(data["reason_codes"], "Sol reason_codes")
        if not reasons:
            raise ContractError("Sol qualification requires a reason code")
        verified = {
            assessment.claim_digest
            for assessment in audit.assessments
            if assessment.verdict is ClaimVerdict.VERIFIED
        }
        if len(set(evidence)) != len(evidence) or not set(evidence).issubset(verified):
            raise ContractError("Sol may cite each verified Terra claim at most once")
        claims_by_field = {claim.field_name: claim for claim in research.claims}
        for field_name, field_value in profile.items():
            claim = claims_by_field.get(field_name)
            if (
                claim is None
                or claim.value != field_value
                or claim.digest not in verified
                or claim.digest not in evidence
            ):
                raise ContractError(f"Sol profile field {field_name} lacks verified evidence")
        if outcome is QualificationOutcome.QUALIFIED:
            missing = REQUIRED_QUALIFIED_FIELDS - set(profile)
            if missing:
                raise ContractError(f"Qualified profile is missing {sorted(missing)}")
            if audit.conflicts or any(
                item.verdict is ClaimVerdict.CONTRADICTED for item in audit.assessments
            ):
                raise ContractError("Sol cannot qualify disputed evidence")
            scores = {field: int(profile[field]) for field in SCORE_FIELDS}
            if (
                sum(scores.values()) < 18
                or scores["offer_fit"] < 4
                or scores["ability_to_pay"] < 3
                or scores["contactability"] < 3
                or scores["evidence_quality"] < 4
            ):
                raise ContractError("Qualified profile does not pass deterministic gates")
        elif profile:
            raise ContractError("Only a qualified decision may expose an opportunity profile")
        return cls(
            audit.digest,
            outcome,
            MappingProxyType(profile),
            evidence,
            reasons,
        )

    @property
    def digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "parent_digest": self.parent_digest,
            "outcome": self.outcome,
            "opportunity_profile": dict(self.opportunity_profile),
            "evidence_digests": self.evidence_digests,
            "reason_codes": self.reason_codes,
        })


@dataclass(frozen=True)
class StageRecord:
    stage: Stage
    model_id: str
    prompt_version: str
    parent_digest: str
    output_digest: str


@dataclass(frozen=True)
class QualificationPacket:
    operation_id: str
    request_digest: str
    discovery_handoff_digest: str
    decision: QualificationDecision
    stages: tuple[StageRecord, ...]

    @property
    def handoff_digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class QualificationFailure:
    operation_id: str
    request_digest: str
    discovery_handoff_digest: str
    failed_stage: Stage | None
    error_code: str
    completed_stages: tuple[StageRecord, ...] = ()
