"""Behavioral tests for Company Bot 6."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from dashboard.release_quality.contracts import (
    QUALITY_DIMENSIONS,
    ContractError,
    IdempotencyClaim,
    QAOutcome,
    QualityFailure,
    QualityPacket,
    QualityRequest,
    Stage,
)
from dashboard.release_quality.orchestrator import (
    LUNA_MODEL_ID,
    SOL_MODEL_ID,
    TERRA_MODEL_ID,
    ReleaseQualityOrchestrator,
)
from dashboard.site_production.contracts import ProductionPacket
from dashboard.tests.test_site_production_orchestrator import (
    orchestrator as production_orchestrator,
)
from dashboard.tests.test_site_production_orchestrator import (
    request as production_request,
)


class FakePort:
    def __init__(self, model_id, responder, calls):
        self.model_id, self.responder, self.calls = model_id, responder, calls

    async def complete(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((self.model_id, payload))
        result = self.responder(payload)
        if isinstance(result, Exception):
            raise result
        return result


def production_packet() -> ProductionPacket:
    result = asyncio.run(production_orchestrator([]).run(production_request()))
    assert isinstance(result, ProductionPacket)
    return result


def request() -> QualityRequest:
    return QualityRequest("quality:acme", production_packet())


def orchestrator(
    calls,
    *,
    findings=None,
    scores=None,
    verdicts=None,
    required_changes=None,
    outcome="APPROVED_FOR_HUMAN_RELEASE",
    decision_override=None,
):
    audit_scores = {dimension: 5 for dimension in QUALITY_DIMENSIONS}
    audit_scores.update(scores or {})
    inspection_findings = list(findings or [])

    def luna(payload):
        return {
            "schema_version": payload["schema_version"],
            "parent_digest": payload["parent_digest"],
            "covered_dimensions": sorted(QUALITY_DIMENSIONS),
            "findings": inspection_findings,
        }

    def terra(payload):
        finding_codes = [item["code"] for item in payload["inspection"]["findings"]]
        return {
            "schema_version": payload["schema_version"],
            "parent_digest": payload["parent_digest"],
            "scores": audit_scores,
            "finding_verdicts": verdicts
            or {code: "CONFIRMED" for code in finding_codes},
            "required_changes": list(required_changes or []),
        }

    def sol(payload):
        confirmed = [
            code
            for code, verdict in payload["audit"]["finding_verdicts"].items()
            if verdict == "CONFIRMED"
        ]
        output = {
            "schema_version": payload["schema_version"],
            "parent_digest": payload["parent_digest"],
            "inspection_digest": payload["inspection_digest"],
            "outcome": outcome,
            "confirmed_finding_codes": confirmed,
            "reason_codes": ["DETERMINISTIC_QA_GATE_APPLIED"],
            "human_release_approval_required": True,
        }
        output.update(decision_override or {})
        return output

    return ReleaseQualityOrchestrator(
        luna=FakePort(LUNA_MODEL_ID, luna, calls),
        terra=FakePort(TERRA_MODEL_ID, terra, calls),
        sol=FakePort(SOL_MODEL_ID, sol, calls),
    )


def finding(request_value, *, code="SEC-1", dimension="security", severity="BLOCKING"):
    artifact = request_value.production_packet.build.files[0]
    return {
        "code": code,
        "dimension": dimension,
        "severity": severity,
        "file_path": artifact.path,
        "file_digest": artifact.digest,
        "summary": "The artifact contains a testable quality defect.",
        "remediation": "Correct the defect and submit a new Bot 5 packet.",
    }


def test_clean_artifact_is_approved_for_human_release_only() -> None:
    calls = []
    result = asyncio.run(orchestrator(calls).run(request()))
    assert isinstance(result, QualityPacket)
    assert result.decision.outcome is QAOutcome.APPROVED_FOR_HUMAN_RELEASE
    assert result.decision.human_release_approval_required is True
    assert [call[0] for call in calls] == [LUNA_MODEL_ID, TERRA_MODEL_ID, SOL_MODEL_ID]
    assert tuple(record.stage for record in result.stages) == (
        Stage.INSPECT,
        Stage.AUDIT,
        Stage.DECIDE,
    )
    assert calls[0][1]["untrusted_site_files"][0]["digest"]
    assert (
        calls[2][1]["deterministic_gate"]["final_release_authority"]
        == "HUMAN_OPERATOR_ONLY"
    )


def test_incomplete_or_non_ready_production_packet_is_rejected() -> None:
    packet = production_packet()
    with pytest.raises(ContractError, match="not ready"):
        QualityRequest("quality:broken", replace(packet, stages=packet.stages[:2]))


def test_finding_must_reference_an_exact_immutable_file_digest() -> None:
    value = request()
    bad = finding(value)
    bad["file_digest"] = "0" * 64
    result = asyncio.run(
        orchestrator([], findings=[bad], outcome="REJECTED").run(value)
    )
    assert isinstance(result, QualityFailure)
    assert result.failed_stage is Stage.INSPECT
    assert result.error_code == "SCHEMA_REJECTED"


@pytest.mark.parametrize("dimension", sorted(QUALITY_DIMENSIONS))
def test_every_quality_dimension_is_an_independent_hard_gate(dimension) -> None:
    result = asyncio.run(
        orchestrator([], scores={dimension: 3}, outcome="REWORK_REQUIRED").run(
            request()
        )
    )
    assert isinstance(result, QualityPacket)
    assert result.decision.outcome is QAOutcome.REWORK_REQUIRED


def test_confirmed_blocking_security_or_evidence_issue_is_rejected() -> None:
    for dimension in ("security", "evidence_integrity"):
        value = request()
        issue = finding(value, dimension=dimension)
        result = asyncio.run(
            orchestrator([], findings=[issue], outcome="REJECTED").run(value)
        )
        assert isinstance(result, QualityPacket)
        assert result.decision.outcome is QAOutcome.REJECTED


def test_noncritical_confirmed_finding_requires_rework() -> None:
    value = request()
    issue = finding(value, dimension="accessibility", severity="WARNING")
    result = asyncio.run(
        orchestrator([], findings=[issue], outcome="REWORK_REQUIRED").run(value)
    )
    assert isinstance(result, QualityPacket)
    assert result.decision.outcome is QAOutcome.REWORK_REQUIRED


def test_sol_cannot_override_deterministic_gate_or_remove_human_control() -> None:
    wrong = asyncio.run(orchestrator([], scores={"security": 2}).run(request()))
    no_human = asyncio.run(
        orchestrator(
            [], decision_override={"human_release_approval_required": False}
        ).run(request())
    )
    assert isinstance(wrong, QualityFailure)
    assert wrong.failed_stage is Stage.DECIDE
    assert isinstance(no_human, QualityFailure)
    assert no_human.failed_stage is Stage.DECIDE


@pytest.mark.parametrize(
    ("model", "stage"),
    [
        (LUNA_MODEL_ID, Stage.INSPECT),
        (TERRA_MODEL_ID, Stage.AUDIT),
        (SOL_MODEL_ID, Stage.DECIDE),
    ],
)
def test_transport_failure_stops_chain(model, stage) -> None:
    engine = orchestrator([])
    for port in (engine._luna, engine._terra, engine._sol):
        if port.model_id == model:
            port.responder = lambda payload: TimeoutError("provider")
    result = asyncio.run(engine.run(request()))
    assert isinstance(result, QualityFailure)
    assert result.failed_stage is stage


def test_model_substitution_and_idempotency_conflict_fail_before_work() -> None:
    calls = []
    engine = orchestrator(calls)
    engine._sol.model_id = TERRA_MODEL_ID
    assert isinstance(asyncio.run(engine.run(request())), QualityFailure)
    assert calls == []

    first = request()
    prior = IdempotencyClaim.for_request(first)
    changed = QualityRequest("quality:changed", first.production_packet)
    calls = []
    result = asyncio.run(orchestrator(calls).run(changed, prior_claim=prior))
    assert isinstance(result, QualityFailure)
    assert result.error_code == "IDEMPOTENCY_KEY_CONFLICT"
    assert calls == []
