"""Behavioral tests for Company Bot 3's strategy-only action."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

import pytest

from dashboard.discovery.contracts import (
    Claim,
    ClaimAssessment as DiscoveryAssessment,
    ClaimVerdict as DiscoveryVerdict,
    Decision,
    DecisionOutcome,
    DiscoveryPacket,
    Extraction,
    Stage as DiscoveryStage,
    StageRecord as DiscoveryRecord,
    Verification,
)
from dashboard.outreach_strategy.contracts import (
    ContractError,
    IdempotencyClaim,
    OutreachFailure,
    OutreachOutcome,
    OutreachPacket,
    OutreachRequest,
    Stage,
)
from dashboard.outreach_strategy.orchestrator import (
    LUNA_MODEL_ID,
    SOL_MODEL_ID,
    TERRA_MODEL_ID,
    OutreachStrategyOrchestrator,
)
from dashboard.qualification.contracts import (
    Audit,
    ClaimAssessment as QualificationAssessment,
    ClaimVerdict as QualificationVerdict,
    OpportunityClaim,
    QualificationDecision,
    QualificationOutcome,
    QualificationPacket,
    Research,
    Stage as QualificationStage,
    StageRecord as QualificationRecord,
)


class FakePort:
    def __init__(self, model_id: str, responder: Callable[[Mapping[str, Any]], Any], calls):
        self.model_id = model_id
        self.responder = responder
        self.calls = calls

    async def complete(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((self.model_id, payload))
        result = self.responder(payload)
        if isinstance(result, Exception):
            raise result
        return result


def discovery_packet(name: str = "Acme Plumbing") -> DiscoveryPacket:
    claims = (
        Claim("company_name", name, (0,)),
        Claim("researched_score", 4, (0,)),
        Claim("website_url", "https://acme.test", (0,)),
    )
    extraction = Extraction("1" * 64, claims, ())
    assessments = tuple(
        DiscoveryAssessment(item.digest, DiscoveryVerdict.VERIFIED, ("SUPPORTED",))
        for item in claims
    )
    verification = Verification(extraction.digest, assessments, ())
    decision = Decision(
        verification.digest,
        DecisionOutcome.ACCEPTED,
        {item.field_name: item.value for item in claims},
        tuple(item.digest for item in claims),
        ("ACCEPTED",),
    )
    records = (
        DiscoveryRecord(DiscoveryStage.EXTRACT, "luna", "v1", "1" * 64, extraction.digest),
        DiscoveryRecord(
            DiscoveryStage.VERIFY, "terra", "v1", extraction.digest, verification.digest
        ),
        DiscoveryRecord(
            DiscoveryStage.ADJUDICATE, "sol", "v1", verification.digest, decision.digest
        ),
    )
    return DiscoveryPacket("discovery-op", "1" * 64, decision, records)


def qualification_packet(discovery: DiscoveryPacket) -> QualificationPacket:
    profile = {
        "website_gap_summary": "The public site lacks a clear booking path.",
        "economic_argument": "A clearer booking path could reduce lost inquiries.",
        "service_fit": "A focused service website matches the observed gap.",
        "offer_fit": 5,
        "urgency": 3,
        "ability_to_pay": 3,
        "contactability": 3,
        "evidence_quality": 4,
    }
    claims = tuple(OpportunityClaim(key, value, (0,)) for key, value in profile.items())
    research = Research("2" * 64, claims, ())
    assessments = tuple(
        QualificationAssessment(
            item.digest, QualificationVerdict.VERIFIED, ("SUPPORTED",)
        )
        for item in claims
    )
    audit = Audit(research.digest, assessments, ())
    decision = QualificationDecision(
        audit.digest,
        QualificationOutcome.QUALIFIED,
        profile,
        tuple(item.digest for item in claims),
        ("GATES_PASSED",),
    )
    records = (
        QualificationRecord(
            QualificationStage.RESEARCH, "luna", "v1", "2" * 64, research.digest
        ),
        QualificationRecord(
            QualificationStage.AUDIT, "terra", "v1", research.digest, audit.digest
        ),
        QualificationRecord(
            QualificationStage.QUALIFY, "sol", "v1", audit.digest, decision.digest
        ),
    )
    return QualificationPacket(
        "qualification-op", "2" * 64, discovery.handoff_digest, decision, records
    )


def action(name: str = "Acme Plumbing") -> OutreachRequest:
    discovery = discovery_packet(name)
    return OutreachRequest(
        "outreach-strategy:acme",
        discovery,
        qualification_packet(discovery),
    )


def successful_orchestrator(
    calls,
    *,
    violations=None,
    luna_override=None,
    terra_override=None,
    sol_override=None,
):
    def luna(payload):
        output = {
            "schema_version": payload["schema_version"],
            "parent_digest": payload["parent_digest"],
            "channel": "EMAIL",
            "audience_role": "Owner",
            "subject": "Quick thought about Acme's booking path",
            "opening": "I noticed Acme's public website makes booking difficult.",
            "value_argument": "A clearer path could help interested visitors reach you.",
            "call_to_action": "Would you be open to seeing a concise redesign concept?",
            "claims": [
                {
                    "text": "The public website lacks a clear booking path.",
                    "supporting_fields": ["website_gap_summary"],
                },
                {
                    "text": "A focused service website matches the observed gap.",
                    "supporting_fields": ["service_fit"],
                },
            ],
            "follow_ups": [
                {"delay_days": 3, "message": "Just checking whether a concept would help."}
            ],
        }
        output.update(luna_override or {})
        return output

    def terra(payload):
        output = {
            "schema_version": payload["schema_version"],
            "parent_digest": payload["parent_digest"],
            "assessments": [
                {
                    "claim_digest": item["claim_digest"],
                    "verdict": "VERIFIED",
                    "reason_codes": ["MATCHES_VERIFIED_FIELD"],
                }
                for item in payload["draft"]["claims"]
            ],
            "violations": list(violations or []),
        }
        output.update(terra_override or {})
        return output

    def sol(payload):
        output = {
            "schema_version": payload["schema_version"],
            "parent_digest": payload["parent_digest"],
            "draft_digest": payload["draft_digest"],
            "outcome": "READY_FOR_HUMAN_APPROVAL",
            "approved_claim_digests": [
                item["claim_digest"] for item in payload["terra_assessments"]
            ],
            "reason_codes": ["CLEAN_AUDIT"],
        }
        output.update(sol_override or {})
        return output

    return OutreachStrategyOrchestrator(
        luna=FakePort(LUNA_MODEL_ID, luna, calls),
        terra=FakePort(TERRA_MODEL_ID, terra, calls),
        sol=FakePort(SOL_MODEL_ID, sol, calls),
    )


def test_success_runs_three_models_and_still_requires_human_approval() -> None:
    calls = []
    result = asyncio.run(successful_orchestrator(calls).run(action()))
    assert isinstance(result, OutreachPacket)
    assert result.decision.outcome is OutreachOutcome.READY_FOR_HUMAN_APPROVAL
    assert result.human_approval_required is True
    assert [model for model, _ in calls] == [LUNA_MODEL_ID, TERRA_MODEL_ID, SOL_MODEL_ID]
    assert tuple(item.stage for item in result.stages) == (
        Stage.DRAFT, Stage.AUDIT, Stage.APPROVE
    )
    assert result.stages[1].parent_digest == result.stages[0].output_digest
    assert result.stages[2].parent_digest == result.stages[1].output_digest


def test_mismatched_discovery_and_qualification_packets_are_rejected() -> None:
    first = discovery_packet("Acme Plumbing")
    second = discovery_packet("Different Company")
    with pytest.raises(ContractError, match="different discovery handoffs"):
        OutreachRequest("mismatch", first, qualification_packet(second))


def test_manual_review_qualification_cannot_enter_bot_3() -> None:
    discovery = discovery_packet()
    qualification = qualification_packet(discovery)
    qualification = replace(
        qualification,
        decision=replace(
            qualification.decision,
            outcome=QualificationOutcome.MANUAL_REVIEW,
        ),
    )
    with pytest.raises(ContractError, match="qualified complete chain"):
        OutreachRequest("not-qualified", discovery, qualification)


@pytest.mark.parametrize(
    "copy",
    [
        "I can build this for $900.",
        "This has a 70% conversion probability.",
        "I guarantee more customers.",
    ],
)
def test_price_probability_and_guarantee_are_rejected_from_copy(copy) -> None:
    result = asyncio.run(
        successful_orchestrator([], luna_override={"value_argument": copy}).run(action())
    )
    assert isinstance(result, OutreachFailure)
    assert result.failed_stage is Stage.DRAFT
    assert result.error_code == "SCHEMA_REJECTED"


def test_claim_cannot_cite_a_field_absent_from_verified_handoffs() -> None:
    result = asyncio.run(
        successful_orchestrator(
            [],
            luna_override={
                "claims": [
                    {"text": "Revenue is rising.", "supporting_fields": ["revenue"]}
                ]
            },
        ).run(action())
    )
    assert isinstance(result, OutreachFailure)
    assert result.failed_stage is Stage.DRAFT


def test_terra_violation_prevents_ready_for_approval() -> None:
    result = asyncio.run(
        successful_orchestrator([], violations=["UNSUPPORTED_ECONOMIC_CLAIM"]).run(action())
    )
    assert isinstance(result, OutreachFailure)
    assert result.failed_stage is Stage.APPROVE


def test_terra_must_audit_every_claim_exactly_once() -> None:
    result = asyncio.run(
        successful_orchestrator([], terra_override={"assessments": []}).run(action())
    )
    assert isinstance(result, OutreachFailure)
    assert result.failed_stage is Stage.AUDIT


def test_sol_cannot_approve_only_a_convenient_subset_of_claims() -> None:
    result = asyncio.run(
        successful_orchestrator(
            [], sol_override={"approved_claim_digests": []}
        ).run(action())
    )
    assert isinstance(result, OutreachFailure)
    assert result.failed_stage is Stage.APPROVE


def test_call_strategy_requires_an_empty_subject() -> None:
    failed = asyncio.run(
        successful_orchestrator([], luna_override={"channel": "CALL"}).run(action())
    )
    assert isinstance(failed, OutreachFailure)
    successful = asyncio.run(
        successful_orchestrator(
            [], luna_override={"channel": "CALL", "subject": ""}
        ).run(action())
    )
    assert isinstance(successful, OutreachPacket)


def test_follow_up_delays_must_be_unique_and_increasing() -> None:
    result = asyncio.run(
        successful_orchestrator(
            [],
            luna_override={
                "follow_ups": [
                    {"delay_days": 5, "message": "Later"},
                    {"delay_days": 2, "message": "Earlier"},
                ]
            },
        ).run(action())
    )
    assert isinstance(result, OutreachFailure)
    assert result.failed_stage is Stage.DRAFT


@pytest.mark.parametrize(
    ("model", "stage"),
    [
        (LUNA_MODEL_ID, Stage.DRAFT),
        (TERRA_MODEL_ID, Stage.AUDIT),
        (SOL_MODEL_ID, Stage.APPROVE),
    ],
)
def test_transport_failure_stops_the_chain(model, stage) -> None:
    orchestrator = successful_orchestrator([])
    for port in (orchestrator._luna, orchestrator._terra, orchestrator._sol):
        if port.model_id == model:
            port.responder = lambda payload: TimeoutError("provider detail")
    result = asyncio.run(orchestrator.run(action()))
    assert isinstance(result, OutreachFailure)
    assert result.failed_stage is stage
    assert result.error_code == "MODEL_TRANSPORT_ERROR"


def test_model_substitution_and_idempotency_conflict_fail_before_work() -> None:
    calls = []
    orchestrator = successful_orchestrator(calls)
    orchestrator._sol.model_id = TERRA_MODEL_ID
    result = asyncio.run(orchestrator.run(action()))
    assert isinstance(result, OutreachFailure)
    assert result.error_code == "MODEL_CONFIGURATION_REJECTED"
    assert calls == []

    first = action()
    prior = IdempotencyClaim.for_request(first)
    calls = []
    result = asyncio.run(
        successful_orchestrator(calls).run(action("Different Company"), prior_claim=prior)
    )
    assert isinstance(result, OutreachFailure)
    assert result.error_code == "IDEMPOTENCY_KEY_CONFLICT"
    assert calls == []


def test_upstream_data_is_nested_below_fixed_authority_directive() -> None:
    calls = []
    result = asyncio.run(successful_orchestrator(calls).run(action()))
    assert isinstance(result, OutreachPacket)
    payload = calls[0][1]
    assert payload["verified_lead_fields"]["company_name"] == "Acme Plumbing"
    assert "Never send" in payload["directive"]
    assert "human approval" in payload["directive"].lower()
