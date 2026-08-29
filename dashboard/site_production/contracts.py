"""Strict contracts for Bot 5's Luna -> Terra -> Sol production pipeline."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from dashboard.deal_compliance.contracts import DealPacket, Stage as DealStage
from dashboard.discovery.provenance import content_digest

SCHEMA_VERSION = "site-production-handoff-v1"
PROMPT_VERSION = "site-production-corporation-v1"
MAX_FILES = 30
MAX_FILE_BYTES = 150_000
MAX_TOTAL_BYTES = 750_000


class ContractError(ValueError):
    pass


class Stage(str, Enum):
    BLUEPRINT = "BLUEPRINT"
    AUDIT = "AUDIT"
    BUILD = "BUILD"


class BuildOutcome(str, Enum):
    READY_FOR_QA = "READY_FOR_QA"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REJECTED = "REJECTED"


def _map(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise ContractError(f"{label} must be an object")
    return value


def _exact(value: Any, label: str, fields: set[str]) -> Mapping[str, Any]:
    data = _map(value, label)
    if set(data) != fields:
        raise ContractError(f"{label} fields mismatch")
    return data


def _text(value: Any, label: str, minimum: int = 1, maximum: int = 4000) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} has invalid length")
    if minimum and not value.strip():
        raise ContractError(f"{label} must not be blank")
    return value


def _strings(value: Any, label: str, minimum: int = 0, maximum: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} must contain {minimum}..{maximum} items")
    return tuple(_text(item, label, maximum=1000) for item in value)


def _time(value: Any, label: str) -> str:
    raw = _text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{label} must include a timezone")
    return raw


def _url(value: Any, label: str) -> str:
    raw = _text(value, label, maximum=2048)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContractError(f"{label} must be an absolute web URL")
    return raw


def _valid_deal(packet: DealPacket) -> bool:
    if packet.human_approval_required is not True or len(packet.stages) != 3:
        return False
    one, two, three = packet.stages
    return (
        tuple(item.stage for item in packet.stages)
        == (DealStage.EXTRACT, DealStage.AUDIT, DealStage.DECIDE)
        and one.parent_digest == packet.request_digest
        and two.parent_digest == one.output_digest
        and three.parent_digest == two.output_digest
        and three.output_digest == packet.decision.digest
    )


@dataclass(frozen=True)
class ProductionAuthorization:
    authorization_id: str
    operator_id: str
    deal_handoff_digest: str
    contract_confirmed: bool
    authorized_at: str

    def __post_init__(self) -> None:
        _text(self.authorization_id, "authorization_id", maximum=128)
        _text(self.operator_id, "operator_id", maximum=128)
        _text(self.deal_handoff_digest, "deal_handoff_digest", 64, 64)
        if self.contract_confirmed is not True:
            raise ContractError("production requires operator-confirmed contract")
        _time(self.authorized_at, "authorized_at")


@dataclass(frozen=True)
class BrandBrief:
    company_name: str
    services: tuple[str, ...]
    audience: str
    primary_cta: str
    contact_text: str
    brand_direction: str
    approved_claims: tuple[str, ...]
    approved_asset_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.company_name, "company_name", maximum=200)
        if not isinstance(self.services, tuple) or not 1 <= len(self.services) <= 20:
            raise ContractError("services must contain 1..20 items")
        for item in self.services: _text(item, "service", maximum=200)
        _text(self.audience, "audience", maximum=1000)
        _text(self.primary_cta, "primary_cta", maximum=200)
        _text(self.contact_text, "contact_text", maximum=500)
        _text(self.brand_direction, "brand_direction", maximum=1000)
        if not isinstance(self.approved_claims, tuple): raise ContractError("approved_claims must be a tuple")
        for item in self.approved_claims: _text(item, "approved claim", maximum=1000)
        if not isinstance(self.approved_asset_urls, tuple): raise ContractError("approved_asset_urls must be a tuple")
        for item in self.approved_asset_urls: _url(item, "approved asset URL")


@dataclass(frozen=True)
class ProductionRequest:
    idempotency_key: str
    deal_packet: DealPacket
    authorization: ProductionAuthorization
    brand_brief: BrandBrief

    def __post_init__(self) -> None:
        _text(self.idempotency_key, "idempotency_key", maximum=128)
        if not isinstance(self.deal_packet, DealPacket) or not _valid_deal(self.deal_packet):
            raise ContractError("deal packet is not a complete chain")
        if self.authorization.deal_handoff_digest != self.deal_packet.handoff_digest:
            raise ContractError("production authorization targets a different deal")

    @property
    def request_digest(self) -> str:
        return content_digest({"schema": SCHEMA_VERSION, "prompt": PROMPT_VERSION, "deal": self.deal_packet.handoff_digest, "authorization": self.authorization, "brief": self.brand_brief})

    @property
    def operation_id(self) -> str:
        return content_digest((self.idempotency_key, self.request_digest))


@dataclass(frozen=True)
class IdempotencyClaim:
    key: str
    request_digest: str
    operation_id: str

    @classmethod
    def for_request(cls, request: ProductionRequest) -> "IdempotencyClaim":
        return cls(request.idempotency_key, request.request_digest, request.operation_id)

    def matches(self, request: ProductionRequest) -> bool:
        return self == self.for_request(request)


@dataclass(frozen=True)
class PagePlan:
    path: str
    title: str
    purpose: str
    sections: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any) -> "PagePlan":
        data = _exact(value, "page plan", {"path", "title", "purpose", "sections"})
        path = _text(data["path"], "page path", maximum=100)
        if not path.startswith("/") or ".." in path: raise ContractError("page path is unsafe")
        return cls(path, _text(data["title"], "page title", maximum=200), _text(data["purpose"], "page purpose", maximum=1000), _strings(data["sections"], "sections", 1, 20))


@dataclass(frozen=True)
class ConversionHypothesis:
    hypothesis: str
    target_event: str
    supporting_fields: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any) -> "ConversionHypothesis":
        data = _exact(value, "conversion hypothesis", {"hypothesis", "target_event", "supporting_fields"})
        return cls(_text(data["hypothesis"], "hypothesis", maximum=1000), _text(data["target_event"], "target event", maximum=100), _strings(data["supporting_fields"], "supporting_fields", 1, 10))


@dataclass(frozen=True)
class Blueprint:
    parent_digest: str
    pages: tuple[PagePlan, ...]
    palette: tuple[str, ...]
    font_stack: str
    motion_principle: str
    hypotheses: tuple[ConversionHypothesis, ...]
    used_claims: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, request: ProductionRequest) -> "Blueprint":
        data = _exact(value, "Luna blueprint", {"schema_version", "parent_digest", "pages", "palette", "font_stack", "motion_principle", "hypotheses", "used_claims"})
        if data["schema_version"] != SCHEMA_VERSION or data["parent_digest"] != request.request_digest: raise ContractError("blueprint version or digest mismatch")
        if not isinstance(data["pages"], list) or not 1 <= len(data["pages"]) <= 10: raise ContractError("pages must contain 1..10 items")
        pages = tuple(PagePlan.from_json(item) for item in data["pages"])
        if len({item.path for item in pages}) != len(pages) or "/" not in {item.path for item in pages}: raise ContractError("pages require unique paths and a home page")
        palette = _strings(data["palette"], "palette", 3, 8)
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", item) for item in palette): raise ContractError("palette values must be six-digit hex")
        if not isinstance(data["hypotheses"], list) or not 1 <= len(data["hypotheses"]) <= 20: raise ContractError("hypotheses must contain 1..20 items")
        hypotheses = tuple(ConversionHypothesis.from_json(item) for item in data["hypotheses"])
        claims = _strings(data["used_claims"], "used_claims", 0, 20)
        if not set(claims).issubset(set(request.brand_brief.approved_claims)): raise ContractError("blueprint uses an unapproved business claim")
        return cls(request.request_digest, pages, palette, _text(data["font_stack"], "font_stack", maximum=500), _text(data["motion_principle"], "motion_principle", maximum=500), hypotheses, claims)

    @property
    def digest(self) -> str: return content_digest(self)


@dataclass(frozen=True)
class DesignAudit:
    parent_digest: str
    scores: Mapping[str, int]
    violations: tuple[str, ...]
    required_changes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, blueprint: Blueprint) -> "DesignAudit":
        data = _exact(value, "Terra design audit", {"schema_version", "parent_digest", "scores", "violations", "required_changes"})
        if data["schema_version"] != SCHEMA_VERSION or data["parent_digest"] != blueprint.digest: raise ContractError("audit version or digest mismatch")
        required = {"conversion_clarity", "luxury_coherence", "mobile_usability", "accessibility_readiness", "evidence_integrity"}
        scores = _map(data["scores"], "scores")
        if set(scores) != required or any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5 for v in scores.values()): raise ContractError("audit scores are invalid")
        return cls(blueprint.digest, dict(scores), _strings(data["violations"], "violations"), _strings(data["required_changes"], "required_changes"))

    @property
    def digest(self) -> str: return content_digest(self)


@dataclass(frozen=True)
class SiteFile:
    path: str
    content: str

    @classmethod
    def from_json(cls, value: Any) -> "SiteFile":
        data = _exact(value, "site file", {"path", "content"})
        path = _text(data["path"], "file path", maximum=200)
        if path.startswith(("/", ".")) or posixpath.normpath(path) != path or ".." in path.split("/") or not re.fullmatch(r"[A-Za-z0-9_./-]+", path): raise ContractError("site file path is unsafe")
        if not path.endswith((".html", ".css", ".js", ".json", ".svg", ".txt")): raise ContractError("site file type is unsupported")
        content = _text(data["content"], "file content", maximum=MAX_FILE_BYTES)
        lowered = content.lower()
        if "javascript:" in lowered or re.search(r"\son[a-z]+\s*=", lowered) or re.search(r"<script[^>]+src\s*=\s*['\"]https?://", lowered): raise ContractError("site file contains unsafe executable content")
        return cls(path, content)

    @property
    def digest(self) -> str: return content_digest(self)


@dataclass(frozen=True)
class SiteBuild:
    parent_digest: str
    blueprint_digest: str
    outcome: BuildOutcome
    files: tuple[SiteFile, ...]
    used_claims: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, request: ProductionRequest, blueprint: Blueprint, audit: DesignAudit) -> "SiteBuild":
        data = _exact(value, "Sol site build", {"schema_version", "parent_digest", "blueprint_digest", "outcome", "files", "used_claims", "reason_codes"})
        if data["schema_version"] != SCHEMA_VERSION or data["parent_digest"] != audit.digest or data["blueprint_digest"] != blueprint.digest: raise ContractError("build version or digest mismatch")
        try: outcome = BuildOutcome(data["outcome"])
        except (TypeError, ValueError) as exc: raise ContractError("unsupported build outcome") from exc
        if not isinstance(data["files"], list) or len(data["files"]) > MAX_FILES: raise ContractError("files must be a bounded list")
        files = tuple(SiteFile.from_json(item) for item in data["files"])
        if len({item.path for item in files}) != len(files): raise ContractError("file paths must be unique")
        claims = _strings(data["used_claims"], "used_claims", 0, 20)
        if not set(claims).issubset(set(request.brand_brief.approved_claims)): raise ContractError("build uses an unapproved claim")
        reasons = _strings(data["reason_codes"], "reason_codes", 1, 20)
        if outcome is BuildOutcome.READY_FOR_QA:
            if audit.violations or audit.required_changes or any(score < 4 for score in audit.scores.values()): raise ContractError("site cannot build through a failed design gate")
            paths = {item.path for item in files}
            if not {"index.html", "styles.css"}.issubset(paths): raise ContractError("ready site requires index.html and styles.css")
            if sum(len(item.content.encode("utf-8")) for item in files) > MAX_TOTAL_BYTES: raise ContractError("site artifact is too large")
            joined = "\n".join(item.content for item in files)
            if request.brand_brief.company_name not in joined or request.brand_brief.primary_cta not in joined: raise ContractError("site omits company identity or primary CTA")
            if re.search(r"\bguarantee(?:d|s)?\b|\b\d+(?:\.\d+)?%\s+conversion", joined, re.I): raise ContractError("site contains an unsupported performance promise")
        elif files:
            raise ContractError("only READY_FOR_QA may expose site files")
        return cls(audit.digest, blueprint.digest, outcome, files, claims, reasons)

    @property
    def digest(self) -> str: return content_digest(self)


@dataclass(frozen=True)
class StageRecord:
    stage: Stage
    model_id: str
    prompt_version: str
    parent_digest: str
    output_digest: str


@dataclass(frozen=True)
class ProductionPacket:
    operation_id: str
    request_digest: str
    deal_handoff_digest: str
    blueprint: Blueprint
    audit: DesignAudit
    build: SiteBuild
    stages: tuple[StageRecord, ...]

    @property
    def handoff_digest(self) -> str: return content_digest(self)


@dataclass(frozen=True)
class ProductionFailure:
    operation_id: str
    request_digest: str
    failed_stage: Stage | None
    error_code: str
    completed_stages: tuple[StageRecord, ...] = ()
