"""Strict contracts for Bot 6's Luna -> Terra -> Sol QA pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from dashboard.discovery.provenance import content_digest
from dashboard.site_production.contracts import (
    BuildOutcome,
    ProductionPacket,
)
from dashboard.site_production.contracts import (
    Stage as ProductionStage,
)

SCHEMA_VERSION = "release-quality-handoff-v1"
PROMPT_VERSION = "release-quality-corporation-v1"
QUALITY_DIMENSIONS = frozenset(
    {
        "functional_integrity",
        "responsive_integrity",
        "accessibility",
        "content_accuracy",
        "security",
        "evidence_integrity",
    }
)


class ContractError(ValueError):
    pass


class Stage(str, Enum):
    INSPECT = "INSPECT"
    AUDIT = "AUDIT"
    DECIDE = "DECIDE"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class FindingVerdict(str, Enum):
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"


class QAOutcome(str, Enum):
    APPROVED_FOR_HUMAN_RELEASE = "APPROVED_FOR_HUMAN_RELEASE"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    REJECTED = "REJECTED"


def _map(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractError(f"{label} must be an object")
    return value


def _exact(value: Any, label: str, fields: set[str]) -> Mapping[str, Any]:
    data = _map(value, label)
    if set(data) != fields:
        raise ContractError(f"{label} fields mismatch")
    return data


def _text(value: Any, label: str, minimum: int = 1, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} has invalid length")
    if minimum and not value.strip():
        raise ContractError(f"{label} must not be blank")
    return value


def _strings(
    value: Any, label: str, minimum: int = 0, maximum: int = 30
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} must contain {minimum}..{maximum} items")
    result = tuple(_text(item, label, maximum=500) for item in value)
    if len(set(result)) != len(result):
        raise ContractError(f"{label} must not contain duplicates")
    return result


def _digest(value: Any, label: str) -> str:
    raw = _text(value, label, 64, 64)
    if any(character not in "0123456789abcdef" for character in raw):
        raise ContractError(f"{label} must be SHA-256")
    return raw


def _valid_production(packet: ProductionPacket) -> bool:
    if packet.build.outcome is not BuildOutcome.READY_FOR_QA or len(packet.stages) != 3:
        return False
    one, two, three = packet.stages
    return (
        tuple(record.stage for record in packet.stages)
        == (ProductionStage.BLUEPRINT, ProductionStage.AUDIT, ProductionStage.BUILD)
        and one.parent_digest == packet.request_digest
        and two.parent_digest == one.output_digest
        and three.parent_digest == two.output_digest
        and three.output_digest == packet.build.digest
        and packet.build.blueprint_digest == packet.blueprint.digest
        and packet.build.parent_digest == packet.audit.digest
    )


@dataclass(frozen=True)
class QualityRequest:
    idempotency_key: str
    production_packet: ProductionPacket

    def __post_init__(self) -> None:
        _text(self.idempotency_key, "idempotency_key", maximum=128)
        if not isinstance(
            self.production_packet, ProductionPacket
        ) or not _valid_production(self.production_packet):
            raise ContractError("production packet is not ready for independent QA")

    @property
    def request_digest(self) -> str:
        return content_digest(
            {
                "schema": SCHEMA_VERSION,
                "prompt": PROMPT_VERSION,
                "production_handoff": self.production_packet.handoff_digest,
            }
        )

    @property
    def operation_id(self) -> str:
        return content_digest((self.idempotency_key, self.request_digest))


@dataclass(frozen=True)
class IdempotencyClaim:
    key: str
    request_digest: str
    operation_id: str

    @classmethod
    def for_request(cls, request: QualityRequest) -> IdempotencyClaim:
        return cls(
            request.idempotency_key, request.request_digest, request.operation_id
        )

    def matches(self, request: QualityRequest) -> bool:
        return self == self.for_request(request)


@dataclass(frozen=True)
class Finding:
    code: str
    dimension: str
    severity: Severity
    file_path: str
    file_digest: str
    summary: str
    remediation: str

    @classmethod
    def from_json(cls, value: Any, request: QualityRequest) -> Finding:
        data = _exact(
            value,
            "finding",
            {
                "code",
                "dimension",
                "severity",
                "file_path",
                "file_digest",
                "summary",
                "remediation",
            },
        )
        code = _text(data["code"], "finding code", maximum=80)
        dimension = _text(data["dimension"], "finding dimension", maximum=80)
        if dimension not in QUALITY_DIMENSIONS:
            raise ContractError("finding dimension is unsupported")
        try:
            severity = Severity(data["severity"])
        except (TypeError, ValueError) as exc:
            raise ContractError("finding severity is unsupported") from exc
        path = _text(data["file_path"], "finding file path", maximum=200)
        files = {
            item.path: item.digest for item in request.production_packet.build.files
        }
        if (
            path not in files
            or _digest(data["file_digest"], "finding file digest") != files[path]
        ):
            raise ContractError(
                "finding evidence does not match the immutable artifact"
            )
        return cls(
            code,
            dimension,
            severity,
            path,
            files[path],
            _text(data["summary"], "finding summary", maximum=1000),
            _text(data["remediation"], "finding remediation", maximum=1000),
        )

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class Inspection:
    parent_digest: str
    covered_dimensions: tuple[str, ...]
    findings: tuple[Finding, ...]

    @classmethod
    def from_json(cls, value: Any, request: QualityRequest) -> Inspection:
        data = _exact(
            value,
            "Luna inspection",
            {"schema_version", "parent_digest", "covered_dimensions", "findings"},
        )
        if (
            data["schema_version"] != SCHEMA_VERSION
            or data["parent_digest"] != request.request_digest
        ):
            raise ContractError("inspection version or digest mismatch")
        covered = _strings(data["covered_dimensions"], "covered dimensions", 6, 6)
        if set(covered) != QUALITY_DIMENSIONS:
            raise ContractError("inspection must cover every quality dimension")
        if not isinstance(data["findings"], list) or len(data["findings"]) > 30:
            raise ContractError("findings must be a bounded list")
        findings = tuple(Finding.from_json(item, request) for item in data["findings"])
        if len({item.code for item in findings}) != len(findings):
            raise ContractError("finding codes must be unique")
        return cls(request.request_digest, covered, findings)

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class QualityAudit:
    parent_digest: str
    scores: Mapping[str, int]
    finding_verdicts: Mapping[str, FindingVerdict]
    required_changes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, inspection: Inspection) -> QualityAudit:
        data = _exact(
            value,
            "Terra quality audit",
            {
                "schema_version",
                "parent_digest",
                "scores",
                "finding_verdicts",
                "required_changes",
            },
        )
        if (
            data["schema_version"] != SCHEMA_VERSION
            or data["parent_digest"] != inspection.digest
        ):
            raise ContractError("quality audit version or digest mismatch")
        scores = _map(data["scores"], "quality scores")
        if set(scores) != QUALITY_DIMENSIONS or any(
            isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5
            for score in scores.values()
        ):
            raise ContractError("quality scores are invalid")
        verdicts = _map(data["finding_verdicts"], "finding verdicts")
        if set(verdicts) != {finding.code for finding in inspection.findings}:
            raise ContractError("every finding requires exactly one verdict")
        try:
            parsed = {
                code: FindingVerdict(verdict) for code, verdict in verdicts.items()
            }
        except (TypeError, ValueError) as exc:
            raise ContractError("finding verdict is unsupported") from exc
        changes = _strings(data["required_changes"], "required changes", 0, 30)
        return cls(inspection.digest, dict(scores), parsed, changes)

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class QualityDecision:
    parent_digest: str
    inspection_digest: str
    outcome: QAOutcome
    confirmed_finding_codes: tuple[str, ...]
    reason_codes: tuple[str, ...]
    human_release_approval_required: bool

    @classmethod
    def from_json(
        cls, value: Any, inspection: Inspection, audit: QualityAudit
    ) -> QualityDecision:
        data = _exact(
            value,
            "Sol quality decision",
            {
                "schema_version",
                "parent_digest",
                "inspection_digest",
                "outcome",
                "confirmed_finding_codes",
                "reason_codes",
                "human_release_approval_required",
            },
        )
        if (
            data["schema_version"] != SCHEMA_VERSION
            or data["parent_digest"] != audit.digest
            or data["inspection_digest"] != inspection.digest
        ):
            raise ContractError("quality decision version or digest mismatch")
        try:
            outcome = QAOutcome(data["outcome"])
        except (TypeError, ValueError) as exc:
            raise ContractError("quality outcome is unsupported") from exc
        confirmed = _strings(
            data["confirmed_finding_codes"], "confirmed finding codes", 0, 30
        )
        expected_confirmed = {
            code
            for code, verdict in audit.finding_verdicts.items()
            if verdict is FindingVerdict.CONFIRMED
        }
        if set(confirmed) != expected_confirmed:
            raise ContractError("decision must carry every confirmed finding")
        reasons = _strings(data["reason_codes"], "reason codes", 1, 30)
        if data["human_release_approval_required"] is not True:
            raise ContractError("Bot 6 never owns final release authority")
        findings = {finding.code: finding for finding in inspection.findings}
        blockers = [
            findings[code]
            for code in expected_confirmed
            if findings[code].severity is Severity.BLOCKING
        ]
        severe_blocker = any(
            item.dimension in {"security", "evidence_integrity"} for item in blockers
        )
        clean = (
            not expected_confirmed
            and not audit.required_changes
            and all(score >= 4 for score in audit.scores.values())
        )
        expected_outcome = (
            QAOutcome.APPROVED_FOR_HUMAN_RELEASE
            if clean
            else QAOutcome.REJECTED
            if severe_blocker
            else QAOutcome.REWORK_REQUIRED
        )
        if outcome is not expected_outcome:
            raise ContractError("quality outcome does not match deterministic gates")
        return cls(audit.digest, inspection.digest, outcome, confirmed, reasons, True)

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
class QualityPacket:
    operation_id: str
    request_digest: str
    production_handoff_digest: str
    inspection: Inspection
    audit: QualityAudit
    decision: QualityDecision
    stages: tuple[StageRecord, ...]

    @property
    def handoff_digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class QualityFailure:
    operation_id: str
    request_digest: str
    failed_stage: Stage | None
    error_code: str
    completed_stages: tuple[StageRecord, ...] = ()
