"""Strict contracts for Bot 4's Luna -> Terra -> Sol deal pipeline."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from dashboard.discovery.provenance import content_digest
from dashboard.outreach_strategy.contracts import (
    OutreachOutcome,
    OutreachPacket,
    Stage as OutreachStage,
)

SCHEMA_VERSION = "deal-compliance-handoff-v1"
PROMPT_VERSION = "deal-compliance-corporation-v1"
PRICE_MIN = 550
PRICE_MAX = 1000


class ContractError(ValueError):
    pass


class Stage(str, Enum):
    EXTRACT = "EXTRACT"
    AUDIT = "AUDIT"
    DECIDE = "DECIDE"


class FactKind(str, Enum):
    INTEREST = "INTEREST"
    QUESTION = "QUESTION"
    OBJECTION = "OBJECTION"
    ACCEPTANCE = "ACCEPTANCE"
    OPT_OUT = "OPT_OUT"


class FactVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"


class DealAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    DRAFT_RESPONSE = "DRAFT_RESPONSE"
    PREPARE_QUOTE = "PREPARE_QUOTE"
    PREPARE_CONTRACT = "PREPARE_CONTRACT"
    PREPARE_INVOICE = "PREPARE_INVOICE"


def _map(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
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


def _time(value: Any, label: str) -> str:
    raw = _text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{label} needs a timezone")
    return raw


def _digest(value: Any, label: str) -> str:
    raw = _text(value, label, 64, 64)
    if any(ch not in "0123456789abcdef" for ch in raw):
        raise ContractError(f"{label} must be SHA-256")
    return raw


def _strings(value: Any, label: str, maximum: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ContractError(f"{label} must be a bounded list")
    return tuple(_text(item, label, maximum=200) for item in value)


def _valid_outreach(packet: OutreachPacket) -> bool:
    if (
        packet.decision.outcome is not OutreachOutcome.READY_FOR_HUMAN_APPROVAL
        or packet.human_approval_required is not True
        or len(packet.stages) != 3
    ):
        return False
    one, two, three = packet.stages
    return (
        tuple(item.stage for item in packet.stages)
        == (OutreachStage.DRAFT, OutreachStage.AUDIT, OutreachStage.APPROVE)
        and one.parent_digest == packet.request_digest
        and two.parent_digest == one.output_digest
        and three.parent_digest == two.output_digest
        and three.output_digest == packet.decision.digest
        and packet.decision.draft_digest == packet.draft.digest
    )


@dataclass(frozen=True)
class OperatorApproval:
    approval_id: str
    operator_id: str
    draft_digest: str
    approved_at: str

    def __post_init__(self) -> None:
        _text(self.approval_id, "approval_id", maximum=128)
        _text(self.operator_id, "operator_id", maximum=128)
        _digest(self.draft_digest, "draft_digest")
        _time(self.approved_at, "approved_at")


@dataclass(frozen=True)
class ProspectEvent:
    event_id: str
    content: str
    occurred_at: str
    email_opt_out: bool = False
    do_not_call: bool = False
    operator_agreed_price: int | None = None
    price_authorization_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id", maximum=128)
        _text(self.content, "content", maximum=20_000)
        _time(self.occurred_at, "occurred_at")
        if type(self.email_opt_out) is not bool or type(self.do_not_call) is not bool:
            raise ContractError("opt-out flags must be booleans")
        if self.operator_agreed_price is not None:
            if (
                isinstance(self.operator_agreed_price, bool)
                or not isinstance(self.operator_agreed_price, int)
                or not PRICE_MIN <= self.operator_agreed_price <= PRICE_MAX
                or not self.price_authorization_id
            ):
                raise ContractError("agreed price requires a bounded operator authorization")
        elif self.price_authorization_id is not None:
            raise ContractError("price authorization requires an agreed price")


@dataclass(frozen=True)
class DealRequest:
    idempotency_key: str
    outreach_packet: OutreachPacket
    approval: OperatorApproval
    event: ProspectEvent

    def __post_init__(self) -> None:
        _text(self.idempotency_key, "idempotency_key", maximum=128)
        if not isinstance(self.outreach_packet, OutreachPacket) or not _valid_outreach(
            self.outreach_packet
        ):
            raise ContractError("outreach packet is not ready for approval")
        if self.approval.draft_digest != self.outreach_packet.draft.digest:
            raise ContractError("operator approval does not match the audited draft")

    @property
    def request_digest(self) -> str:
        return content_digest({
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "outreach_handoff": self.outreach_packet.handoff_digest,
            "approval": self.approval,
            "event": self.event,
        })

    @property
    def operation_id(self) -> str:
        return content_digest((self.idempotency_key, self.request_digest))


@dataclass(frozen=True)
class IdempotencyClaim:
    key: str
    request_digest: str
    operation_id: str

    @classmethod
    def for_request(cls, request: DealRequest) -> "IdempotencyClaim":
        return cls(request.idempotency_key, request.request_digest, request.operation_id)

    def matches(self, request: DealRequest) -> bool:
        return self == self.for_request(request)


@dataclass(frozen=True)
class DealFact:
    kind: FactKind
    statement: str
    event_id: str

    @property
    def digest(self) -> str:
        return content_digest(self)

    @classmethod
    def from_json(cls, value: Any, event_id: str) -> "DealFact":
        data = _exact(value, "deal fact", {"kind", "statement", "event_id"})
        if data["event_id"] != event_id:
            raise ContractError("fact cites a different event")
        try:
            kind = FactKind(data["kind"])
        except (TypeError, ValueError) as exc:
            raise ContractError("unsupported fact kind") from exc
        return cls(kind, _text(data["statement"], "statement", maximum=1000), event_id)


@dataclass(frozen=True)
class Extraction:
    parent_digest: str
    facts: tuple[DealFact, ...]
    limitations: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, request: DealRequest) -> "Extraction":
        data = _exact(value, "Luna extraction", {"schema_version", "parent_digest", "facts", "limitations"})
        if data["schema_version"] != SCHEMA_VERSION or data["parent_digest"] != request.request_digest:
            raise ContractError("Luna extraction version or digest mismatch")
        if not isinstance(data["facts"], list) or len(data["facts"]) > 20:
            raise ContractError("facts must be a bounded list")
        facts = tuple(DealFact.from_json(item, request.event.event_id) for item in data["facts"])
        if len({item.digest for item in facts}) != len(facts):
            raise ContractError("facts must be unique")
        return cls(request.request_digest, facts, _strings(data["limitations"], "limitations"))

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class FactAssessment:
    fact_digest: str
    verdict: FactVerdict
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any) -> "FactAssessment":
        data = _exact(value, "fact assessment", {"fact_digest", "verdict", "reason_codes"})
        try:
            verdict = FactVerdict(data["verdict"])
        except (TypeError, ValueError) as exc:
            raise ContractError("unsupported fact verdict") from exc
        reasons = _strings(data["reason_codes"], "reason_codes")
        if not reasons:
            raise ContractError("assessment needs a reason")
        return cls(_digest(data["fact_digest"], "fact_digest"), verdict, reasons)


@dataclass(frozen=True)
class Audit:
    parent_digest: str
    assessments: tuple[FactAssessment, ...]
    violations: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, extraction: Extraction) -> "Audit":
        data = _exact(value, "Terra audit", {"schema_version", "parent_digest", "assessments", "violations"})
        if data["schema_version"] != SCHEMA_VERSION or data["parent_digest"] != extraction.digest:
            raise ContractError("Terra audit version or digest mismatch")
        if not isinstance(data["assessments"], list):
            raise ContractError("assessments must be a list")
        items = tuple(FactAssessment.from_json(item) for item in data["assessments"])
        if {item.fact_digest for item in items} != {fact.digest for fact in extraction.facts} or len(items) != len(extraction.facts):
            raise ContractError("Terra must assess every fact exactly once")
        return cls(extraction.digest, items, _strings(data["violations"], "violations"))

    @property
    def digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class DealDecision:
    parent_digest: str
    action: DealAction
    draft_text: str
    suggested_price: int | None
    agreed_price: int | None
    evidence_digests: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @classmethod
    def from_json(cls, value: Any, request: DealRequest, extraction: Extraction, audit: Audit) -> "DealDecision":
        data = _exact(value, "Sol decision", {"schema_version", "parent_digest", "action", "draft_text", "suggested_price", "agreed_price", "evidence_digests", "reason_codes"})
        if data["schema_version"] != SCHEMA_VERSION or data["parent_digest"] != audit.digest:
            raise ContractError("Sol decision version or digest mismatch")
        try:
            action = DealAction(data["action"])
        except (TypeError, ValueError) as exc:
            raise ContractError("unsupported deal action") from exc
        draft = _text(data["draft_text"], "draft_text", minimum=0, maximum=5000)
        suggested = data["suggested_price"]
        agreed = data["agreed_price"]
        for price, label in ((suggested, "suggested_price"), (agreed, "agreed_price")):
            if price is not None and (isinstance(price, bool) or not isinstance(price, int) or not PRICE_MIN <= price <= PRICE_MAX):
                raise ContractError(f"{label} is invalid")
        if agreed != request.event.operator_agreed_price:
            raise ContractError("Sol cannot create or alter an agreed price")
        evidence = tuple(_digest(item, "evidence digest") for item in _strings(data["evidence_digests"], "evidence_digests"))
        verified = {item.fact_digest for item in audit.assessments if item.verdict is FactVerdict.VERIFIED}
        if len(set(evidence)) != len(evidence) or not set(evidence).issubset(verified):
            raise ContractError("Sol evidence is not verified")
        reasons = _strings(data["reason_codes"], "reason_codes")
        if not reasons:
            raise ContractError("Sol decision needs a reason")
        facts = {fact.digest: fact for fact in extraction.facts}
        accepted = any(facts[item].kind is FactKind.ACCEPTANCE for item in evidence)
        opted_out = request.event.email_opt_out or request.event.do_not_call or any(
            facts[item].kind is FactKind.OPT_OUT for item in verified
        )
        if opted_out and (action is not DealAction.NO_ACTION or draft or suggested is not None):
            raise ContractError("opt-out requires no action")
        if action in {DealAction.PREPARE_CONTRACT, DealAction.PREPARE_INVOICE}:
            if not accepted or agreed is None or request.event.price_authorization_id is None:
                raise ContractError("contract or invoice needs accepted authorized price")
        if action is DealAction.NO_ACTION and draft:
            raise ContractError("no action cannot expose draft text")
        if action not in {DealAction.NO_ACTION, DealAction.HUMAN_REVIEW} and not draft:
            raise ContractError("prepared action requires draft text")
        if draft and re.search(r"\$\s*(\d+)", draft):
            amounts = {int(item) for item in re.findall(r"\$\s*(\d+)", draft)}
            allowed = {price for price in (suggested, agreed) if price is not None}
            if not amounts.issubset(allowed):
                raise ContractError("draft contains an unauthorized price")
        return cls(audit.digest, action, draft, suggested, agreed, evidence, reasons)

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
class DealPacket:
    operation_id: str
    request_digest: str
    outreach_handoff_digest: str
    decision: DealDecision
    human_approval_required: bool
    stages: tuple[StageRecord, ...]

    def __post_init__(self) -> None:
        if self.human_approval_required is not True:
            raise ContractError("deal proposals always require human approval")

    @property
    def handoff_digest(self) -> str:
        return content_digest(self)


@dataclass(frozen=True)
class DealFailure:
    operation_id: str
    request_digest: str
    failed_stage: Stage | None
    error_code: str
    completed_stages: tuple[StageRecord, ...] = ()
