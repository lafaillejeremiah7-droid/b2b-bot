"""Strict runtime contracts for the Luna → Terra → Sol discovery pipeline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeAlias
from urllib.parse import urlparse

from dashboard.discovery.provenance import content_digest

SCHEMA_VERSION = "discovery-handoff-v1"
PROMPT_VERSION = "discovery-corporation-v1"
MAX_SOURCES = 20
MAX_SOURCE_CONTENT = 50_000

Scalar: TypeAlias = str | int


class ContractError(ValueError):
    """Raised when model output violates an exact runtime schema."""


class Stage(str, Enum):
    EXTRACT = "EXTRACT"
    VERIFY = "VERIFY"
    ADJUDICATE = "ADJUDICATE"


class ClaimVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIED = "UNVERIFIED"


class DecisionOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


LEAD_FIELD_LIMITS: Mapping[str, tuple[type, int | None, int | None]] = {
    "company_name": (str, 1, 200),
    "industry": (str, 1, 200),
    "website_url": (str, 1, 2048),
    "owner": (str, 1, 500),
    "researched_score": (int, 1, 5),
    "preferred_price": (int, 550, 1000),
    "contact_name": (str, 1, 200),
    "contact_email": (str, 1, 320),
    "contact_phone": (str, 1, 32),
    "website_condition": (int, 1, 5),
    "urgency": (int, 1, 5),
    "estimated_page_count": (int, 0, 200),
    "timezone": (str, 1, 64),
    "region": (str, 1, 200),
}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ContractError(f"{label} keys must be strings")
    return value


def _exact(value: Any, label: str, fields: set[str]) -> Mapping[str, Any]:
    data = _mapping(value, label)
    actual = set(data)
    if actual != fields:
        missing = sorted(fields - actual)
        extra = sorted(actual - fields)
        raise ContractError(f"{label} fields mismatch; missing={missing}, extra={extra}")
    return data


def _text(value: Any, label: str, *, minimum: int = 1, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} must contain {minimum}..{maximum} characters")
    if minimum > 0 and not value.strip():
        raise ContractError(f"{label} must not be blank")
    return value


def _web_url(value: Any, label: str) -> str:
    url = _text(value, label, maximum=2048)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContractError(f"{label} must be an absolute http or https URL")
    if any(character.isspace() for character in url):
        raise ContractError(f"{label} must not contain whitespace")
    return url


def _string_list(value: Any, label: str, *, maximum: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{label} must be a list with at most {maximum} items")
    return tuple(_text(item, f"{label} item", maximum=200) for item in value)


def _scalar(value: Any, field_name: str) -> Scalar:
    specification = LEAD_FIELD_LIMITS.get(field_name)
    if specification is None:
        raise ContractError(f"Unknown or unauthorized Lead field: {field_name}")
    expected, lower, upper = specification
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ContractError(f"{field_name} must be an integer")
        if lower is not None and value < lower or upper is not None and value > upper:
            raise ContractError(f"{field_name} is outside its allowed range")
    else:
        if not isinstance(value, str):
            raise ContractError(f"{field_name} must be text")
        if lower is not None and len(value) < lower or upper is not None and len(value) > upper:
            raise ContractError(f"{field_name} is outside its allowed length")
        if field_name == "website_url":
            _web_url(value, "website_url")
    return value


@dataclass(frozen=True)
class DiscoverySource:
    url: str
    title: str
    content: str
    retrieved_at: str

    def __post_init__(self) -> None:
        _web_url(self.url, "source.url")
        _text(self.title, "source.title", maximum=500)
        _text(self.content, "source.content", maximum=MAX_SOURCE_CONTENT)
        _text(self.retrieved_at, "source.retrieved_at", maximum=64)
        try:
            parsed = datetime.fromisoformat(self.retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("source.retrieved_at must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ContractError("source.retrieved_at must include a timezone")


@dataclass(frozen=True)
class DiscoveryRequest:
    idempotency_key: str
    brief: str
    sources: tuple[DiscoverySource, ...]

    def __post_init__(self) -> None:
        _text(self.idempotency_key, "idempotency_key", maximum=128)
        _text(self.brief, "brief", maximum=2000)
        if not isinstance(self.sources, tuple) or not all(
            isinstance(source, DiscoverySource) for source in self.sources
        ):
            raise ContractError("sources must be a tuple of DiscoverySource values")
        if not 1 <= len(self.sources) <= MAX_SOURCES:
            raise ContractError(f"sources must contain 1..{MAX_SOURCES} items")

    @property
    def request_digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "brief": self.brief,
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
    def for_request(cls, request: DiscoveryRequest) -> "IdempotencyClaim":
        return cls(request.idempotency_key, request.request_digest, request.operation_id)

    def matches(self, request: DiscoveryRequest) -> bool:
        return self == self.for_request(request)


@dataclass(frozen=True)
class Claim:
    field_name: str
    value: Scalar
    source_indexes: tuple[int, ...]

    @property
    def digest(self) -> str:
        return content_digest(self)

    @classmethod
    def from_json(cls, value: Any, *, source_count: int) -> "Claim":
        data = _exact(value, "claim", {"field_name", "value", "source_indexes"})
        field_name = _text(data["field_name"], "claim.field_name", maximum=64)
        scalar = _scalar(data["value"], field_name)
        indexes = data["source_indexes"]
        if not isinstance(indexes, list) or not indexes:
            raise ContractError("claim.source_indexes must be a non-empty list")
        if any(isinstance(index, bool) or not isinstance(index, int) for index in indexes):
            raise ContractError("claim.source_indexes must contain integers")
        normalized = tuple(indexes)
        if len(set(normalized)) != len(normalized):
            raise ContractError("claim.source_indexes must not contain duplicates")
        if any(index < 0 or index >= source_count for index in normalized):
            raise ContractError("claim.source_indexes contains an unavailable source")
        return cls(field_name, scalar, normalized)


@dataclass(frozen=True)
class Extraction:
    parent_digest: str
    claims: tuple[Claim, ...]
    notes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, *, request: DiscoveryRequest) -> "Extraction":
        data = _exact(
            value, "Luna extraction", {"schema_version", "parent_digest", "claims", "notes"}
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Luna extraction schema_version is unsupported")
        if data["parent_digest"] != request.request_digest:
            raise ContractError("Luna extraction parent digest mismatch")
        if not isinstance(data["claims"], list) or len(data["claims"]) > 100:
            raise ContractError("Luna claims must be a list with at most 100 items")
        claims = tuple(
            Claim.from_json(item, source_count=len(request.sources))
            for item in data["claims"]
        )
        if len({claim.field_name for claim in claims}) != len(claims):
            raise ContractError("Luna may emit at most one claim per Lead field")
        return cls(
            parent_digest=request.request_digest,
            claims=claims,
            notes=_string_list(data["notes"], "Luna notes"),
        )

    @property
    def digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "parent_digest": self.parent_digest,
            "claims": self.claims,
            "notes": self.notes,
        })


@dataclass(frozen=True)
class ClaimAssessment:
    claim_digest: str
    verdict: ClaimVerdict
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any) -> "ClaimAssessment":
        data = _exact(value, "claim assessment", {"claim_digest", "verdict", "reason_codes"})
        digest = _text(data["claim_digest"], "claim_digest", minimum=64, maximum=64)
        if any(character not in "0123456789abcdef" for character in digest):
            raise ContractError("claim_digest must be a lowercase SHA-256 digest")
        try:
            verdict = ClaimVerdict(data["verdict"])
        except (TypeError, ValueError) as exc:
            raise ContractError("claim assessment verdict is unsupported") from exc
        reasons = _string_list(data["reason_codes"], "reason_codes")
        if not reasons:
            raise ContractError("claim assessment requires a reason code")
        return cls(digest, verdict, reasons)


@dataclass(frozen=True)
class Verification:
    parent_digest: str
    assessments: tuple[ClaimAssessment, ...]
    conflicts: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, *, extraction: Extraction) -> "Verification":
        data = _exact(
            value,
            "Terra verification",
            {"schema_version", "parent_digest", "assessments", "conflicts"},
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Terra verification schema_version is unsupported")
        if data["parent_digest"] != extraction.digest:
            raise ContractError("Terra verification parent digest mismatch")
        if not isinstance(data["assessments"], list):
            raise ContractError("Terra assessments must be a list")
        assessments = tuple(ClaimAssessment.from_json(item) for item in data["assessments"])
        expected = {claim.digest for claim in extraction.claims}
        actual = {assessment.claim_digest for assessment in assessments}
        if len(actual) != len(assessments) or actual != expected:
            raise ContractError("Terra must assess every Luna claim exactly once")
        return cls(
            parent_digest=extraction.digest,
            assessments=assessments,
            conflicts=_string_list(data["conflicts"], "Terra conflicts"),
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
class Decision:
    parent_digest: str
    outcome: DecisionOutcome
    lead_payload: Mapping[str, Scalar]
    evidence_digests: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(
        cls,
        value: Any,
        *,
        extraction: Extraction,
        verification: Verification,
    ) -> "Decision":
        data = _exact(
            value,
            "Sol decision",
            {
                "schema_version",
                "parent_digest",
                "outcome",
                "lead_payload",
                "evidence_digests",
                "reason_codes",
            },
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise ContractError("Sol decision schema_version is unsupported")
        if data["parent_digest"] != verification.digest:
            raise ContractError("Sol decision parent digest mismatch")
        try:
            outcome = DecisionOutcome(data["outcome"])
        except (TypeError, ValueError) as exc:
            raise ContractError("Sol decision outcome is unsupported") from exc
        raw_payload = _mapping(data["lead_payload"], "Sol lead_payload")
        payload = {field: _scalar(item, field) for field, item in raw_payload.items()}
        evidence = _string_list(data["evidence_digests"], "Sol evidence_digests", maximum=100)
        reasons = _string_list(data["reason_codes"], "Sol reason_codes")
        if not reasons:
            raise ContractError("Sol decision requires a reason code")
        verified = {
            assessment.claim_digest
            for assessment in verification.assessments
            if assessment.verdict is ClaimVerdict.VERIFIED
        }
        if len(set(evidence)) != len(evidence) or not set(evidence).issubset(verified):
            raise ContractError("Sol may cite each verified Terra claim at most once")
        claims_by_field = {claim.field_name: claim for claim in extraction.claims}
        for field_name, field_value in payload.items():
            claim = claims_by_field.get(field_name)
            if (
                claim is None
                or claim.value != field_value
                or claim.digest not in verified
                or claim.digest not in evidence
            ):
                raise ContractError(f"Sol payload field {field_name} lacks verified evidence")
        if outcome is DecisionOutcome.ACCEPTED:
            if verification.conflicts:
                raise ContractError("Sol cannot accept a handoff with unresolved conflicts")
            if any(
                assessment.verdict is ClaimVerdict.CONTRADICTED
                for assessment in verification.assessments
            ):
                raise ContractError("Sol cannot accept contradicted evidence")
            for required in ("company_name", "researched_score"):
                if required not in payload:
                    raise ContractError(f"Accepted Sol payload is missing {required}")
        elif payload:
            raise ContractError("Only an accepted decision may expose a Lead payload")
        return cls(
            parent_digest=verification.digest,
            outcome=outcome,
            lead_payload=MappingProxyType(payload),
            evidence_digests=evidence,
            reason_codes=reasons,
        )

    @property
    def digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "parent_digest": self.parent_digest,
            "outcome": self.outcome,
            "lead_payload": dict(self.lead_payload),
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
class DiscoveryPacket:
    operation_id: str
    request_digest: str
    decision: Decision
    stages: tuple[StageRecord, ...]

    @property
    def handoff_digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class DiscoveryFailure:
    operation_id: str
    request_digest: str
    failed_stage: Stage | None
    error_code: str
    completed_stages: tuple[StageRecord, ...] = ()
