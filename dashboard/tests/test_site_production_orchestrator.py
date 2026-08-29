"""Behavioral tests for Company Bot 5."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import pytest

from dashboard.deal_compliance.contracts import DealAction, DealDecision, DealPacket, Stage as DealStage, StageRecord as DealRecord
from dashboard.site_production.contracts import (
    BrandBrief, BuildOutcome, ContractError, IdempotencyClaim,
    ProductionAuthorization, ProductionFailure, ProductionPacket,
    ProductionRequest, Stage,
)
from dashboard.site_production.orchestrator import LUNA_MODEL_ID, SOL_MODEL_ID, TERRA_MODEL_ID, SiteProductionOrchestrator


class FakePort:
    def __init__(self, model_id, responder, calls): self.model_id, self.responder, self.calls = model_id, responder, calls
    async def complete(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((self.model_id, payload)); result = self.responder(payload)
        if isinstance(result, Exception): raise result
        return result


def deal_packet() -> DealPacket:
    decision = DealDecision("a" * 64, DealAction.PREPARE_CONTRACT, "Contract draft.", None, 800, ("b" * 64,), ("READY",))
    records = (
        DealRecord(DealStage.EXTRACT, "luna", "v1", "1" * 64, "2" * 64),
        DealRecord(DealStage.AUDIT, "terra", "v1", "2" * 64, "a" * 64),
        DealRecord(DealStage.DECIDE, "sol", "v1", "a" * 64, decision.digest),
    )
    return DealPacket("deal-op", "1" * 64, "outreach", decision, True, records)


def request() -> ProductionRequest:
    deal = deal_packet()
    authorization = ProductionAuthorization("build-auth-1", "operator-1", deal.handoff_digest, True, "2026-08-29T16:00:00Z")
    brief = BrandBrief(
        "Acme Plumbing", ("Emergency plumbing", "Drain repair"),
        "Homeowners who need reliable local plumbing help", "Request Service",
        "Call (555) 010-2000", "Quiet editorial luxury with deep navy and warm ivory",
        ("Locally operated plumbing service",), (),
    )
    return ProductionRequest("production:acme", deal, authorization, brief)


def orchestrator(calls, *, scores=None, violations=None, required_changes=None, blueprint_override=None, build_override=None):
    audit_scores = {"conversion_clarity": 5, "luxury_coherence": 5, "mobile_usability": 4, "accessibility_readiness": 4, "evidence_integrity": 5}
    audit_scores.update(scores or {})
    def luna(payload):
        output = {"schema_version": payload["schema_version"], "parent_digest": payload["parent_digest"], "pages": [{"path": "/", "title": "Acme Plumbing", "purpose": "Convert qualified visitors into service requests", "sections": ["Hero", "Services", "Trust", "Contact"]}], "palette": ["#0B1F33", "#F4EFE6", "#B88A44"], "font_stack": "Georgia, serif", "motion_principle": "Subtle opacity and transform transitions with reduced-motion support", "hypotheses": [{"hypothesis": "A single visible service CTA may reduce decision friction", "target_event": "service_request", "supporting_fields": ["primary_cta", "audience"]}], "used_claims": ["Locally operated plumbing service"]}
        output.update(blueprint_override or {}); return output
    def terra(payload):
        return {"schema_version": payload["schema_version"], "parent_digest": payload["parent_digest"], "scores": audit_scores, "violations": list(violations or []), "required_changes": list(required_changes or [])}
    def sol(payload):
        output = {"schema_version": payload["schema_version"], "parent_digest": payload["parent_digest"], "blueprint_digest": payload["blueprint_digest"], "outcome": "READY_FOR_QA", "files": [
            {"path": "index.html", "content": "<!doctype html><html lang=\"en\"><head><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><link rel=\"stylesheet\" href=\"styles.css\"></head><body><main><h1>Acme Plumbing</h1><p>Locally operated plumbing service</p><a href=\"#contact\">Request Service</a></main></body></html>"},
            {"path": "styles.css", "content": ":root{--ink:#0B1F33;--paper:#F4EFE6;--gold:#B88A44}body{color:var(--ink);background:var(--paper);font-family:Georgia,serif;margin:0}main{max-width:72rem;margin:auto;padding:clamp(2rem,7vw,7rem)}a{color:var(--ink)}"}], "used_claims": ["Locally operated plumbing service"], "reason_codes": ["ALL_DESIGN_GATES_PASSED"]}
        output.update(build_override or {}); return output
    return SiteProductionOrchestrator(luna=FakePort(LUNA_MODEL_ID, luna, calls), terra=FakePort(TERRA_MODEL_ID, terra, calls), sol=FakePort(SOL_MODEL_ID, sol, calls))


def test_success_builds_multi_file_site_for_bot_6_only() -> None:
    calls = []; result = asyncio.run(orchestrator(calls).run(request()))
    assert isinstance(result, ProductionPacket)
    assert result.build.outcome is BuildOutcome.READY_FOR_QA
    assert {item.path for item in result.build.files} == {"index.html", "styles.css"}
    assert [item[0] for item in calls] == [LUNA_MODEL_ID, TERRA_MODEL_ID, SOL_MODEL_ID]
    assert tuple(item.stage for item in result.stages) == (Stage.BLUEPRINT, Stage.AUDIT, Stage.BUILD)
    assert calls[2][1]["artifact_rules"]["destination"] == "BOT_6_QA_ONLY"


def test_contract_and_exact_deal_authorization_are_required() -> None:
    deal = deal_packet()
    with pytest.raises(ContractError, match="contract"):
        ProductionAuthorization("a", "o", deal.handoff_digest, False, "2026-08-29T16:00:00Z")
    valid = request()
    with pytest.raises(ContractError, match="different deal"):
        ProductionRequest(valid.idempotency_key, valid.deal_packet, replace(valid.authorization, deal_handoff_digest="0" * 64), valid.brand_brief)


@pytest.mark.parametrize("field", ["conversion_clarity", "luxury_coherence", "mobile_usability", "accessibility_readiness", "evidence_integrity"])
def test_every_quality_dimension_is_an_independent_hard_gate(field) -> None:
    result = asyncio.run(orchestrator([], scores={field: 3}).run(request()))
    assert isinstance(result, ProductionFailure)
    assert result.failed_stage is Stage.BUILD


def test_violation_or_required_change_blocks_ready_build() -> None:
    assert isinstance(asyncio.run(orchestrator([], violations=["FAKE_TESTIMONIAL"]).run(request())), ProductionFailure)
    assert isinstance(asyncio.run(orchestrator([], required_changes=["FIX_MOBILE_NAV"]).run(request())), ProductionFailure)


def test_unapproved_business_claim_is_rejected_in_blueprint_and_build() -> None:
    bad_blueprint = asyncio.run(orchestrator([], blueprint_override={"used_claims": ["Award-winning"]}).run(request()))
    bad_build = asyncio.run(orchestrator([], build_override={"used_claims": ["Award-winning"]}).run(request()))
    assert isinstance(bad_blueprint, ProductionFailure)
    assert isinstance(bad_build, ProductionFailure)


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", ".hidden.html", "assets/../../x.js"])
def test_unsafe_artifact_paths_are_rejected(path) -> None:
    files = [{"path": path, "content": "x"}, {"path": "index.html", "content": "Acme Plumbing Request Service"}, {"path": "styles.css", "content": "body{}"}]
    result = asyncio.run(orchestrator([], build_override={"files": files}).run(request()))
    assert isinstance(result, ProductionFailure)


@pytest.mark.parametrize("unsafe", ["<a href=\"javascript:alert(1)\">x</a>", "<button onclick=\"steal()\">x</button>", "<script src=\"https://evil.test/x.js\"></script>"])
def test_unsafe_executable_content_is_rejected(unsafe) -> None:
    files = [{"path": "index.html", "content": f"Acme Plumbing Request Service {unsafe}"}, {"path": "styles.css", "content": "body{}"}]
    assert isinstance(asyncio.run(orchestrator([], build_override={"files": files}).run(request())), ProductionFailure)


def test_company_identity_cta_and_required_files_cannot_be_omitted() -> None:
    for files in ([{"path": "index.html", "content": "Acme Plumbing Request Service"}], [{"path": "index.html", "content": "Generic site"}, {"path": "styles.css", "content": "body{}"}]):
        assert isinstance(asyncio.run(orchestrator([], build_override={"files": files}).run(request())), ProductionFailure)


def test_conversion_guarantee_is_rejected_not_relabelled_as_evidence() -> None:
    files = [{"path": "index.html", "content": "Acme Plumbing Request Service guaranteed 80% conversion"}, {"path": "styles.css", "content": "body{}"}]
    assert isinstance(asyncio.run(orchestrator([], build_override={"files": files}).run(request())), ProductionFailure)


@pytest.mark.parametrize(("model", "stage"), [(LUNA_MODEL_ID, Stage.BLUEPRINT), (TERRA_MODEL_ID, Stage.AUDIT), (SOL_MODEL_ID, Stage.BUILD)])
def test_transport_failure_stops_chain(model, stage) -> None:
    engine = orchestrator([])
    for port in (engine._luna, engine._terra, engine._sol):
        if port.model_id == model: port.responder = lambda payload: TimeoutError("provider")
    result = asyncio.run(engine.run(request()))
    assert isinstance(result, ProductionFailure); assert result.failed_stage is stage


def test_model_substitution_and_idempotency_conflict_fail_before_work() -> None:
    calls = []; engine = orchestrator(calls); engine._sol.model_id = TERRA_MODEL_ID
    assert isinstance(asyncio.run(engine.run(request())), ProductionFailure); assert calls == []
    first = request(); prior = IdempotencyClaim.for_request(first); calls = []
    changed = replace(first, brand_brief=replace(first.brand_brief, primary_cta="Book Now"))
    result = asyncio.run(orchestrator(calls).run(changed, prior_claim=prior))
    assert isinstance(result, ProductionFailure); assert result.error_code == "IDEMPOTENCY_KEY_CONFLICT"; assert calls == []
