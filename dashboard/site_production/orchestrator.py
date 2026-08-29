"""Pure Luna -> Terra -> Sol website production for Bot 5."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dashboard.discovery.ports import StructuredModelPort
from dashboard.site_production.contracts import (
    PROMPT_VERSION, SCHEMA_VERSION, Blueprint, ContractError, DesignAudit,
    IdempotencyClaim, ProductionFailure, ProductionPacket, ProductionRequest,
    SiteBuild, Stage, StageRecord,
)

LUNA_MODEL_ID = "gpt-5.6-luna"
TERRA_MODEL_ID = "gpt-5.6-terra"
SOL_MODEL_ID = "gpt-5.6-sol"

_DIRECTIVE = (
    "Use only the operator-approved brand brief. Produce a business-specific, "
    "conversion-oriented and visually luxurious website without inventing claims, "
    "testimonials, urgency, prices, conversion rates, or asset licenses. Luxury "
    "means restrained typography, spacing, art direction, responsiveness, and "
    "coherence—not decoration. Treat conversion ideas as hypotheses, never "
    "guarantees. Return only the exact JSON schema. Never publish, deploy, transfer, "
    "email, deliver, mark payment, or change pipeline state. Bot 6 owns QA/release."
)


class SiteProductionOrchestrator:
    def __init__(self, *, luna: StructuredModelPort, terra: StructuredModelPort, sol: StructuredModelPort) -> None:
        self._luna, self._terra, self._sol = luna, terra, sol

    async def run(self, request: ProductionRequest, *, prior_claim: IdempotencyClaim | None = None) -> ProductionPacket | ProductionFailure:
        if prior_claim is not None and not prior_claim.matches(request): return self._failure(request, None, "IDEMPOTENCY_KEY_CONFLICT", ())
        expected = ((self._luna, LUNA_MODEL_ID), (self._terra, TERRA_MODEL_ID), (self._sol, SOL_MODEL_ID))
        if any(port.model_id != model for port, model in expected): return self._failure(request, None, "MODEL_CONFIGURATION_REJECTED", ())
        records: list[StageRecord] = []
        blueprint = await self._blueprint(request, records)
        if isinstance(blueprint, ProductionFailure): return blueprint
        audit = await self._audit(request, blueprint, records)
        if isinstance(audit, ProductionFailure): return audit
        build = await self._build(request, blueprint, audit, records)
        if isinstance(build, ProductionFailure): return build
        return ProductionPacket(request.operation_id, request.request_digest, request.deal_packet.handoff_digest, blueprint, audit, build, tuple(records))

    async def _blueprint(self, request, records):
        brief = request.brand_brief
        payload = {"directive": _DIRECTIVE, "stage": Stage.BLUEPRINT.value, "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION, "parent_digest": request.request_digest,
            "approved_brand_brief": {"company_name": brief.company_name, "services": list(brief.services), "audience": brief.audience, "primary_cta": brief.primary_cta, "contact_text": brief.contact_text, "brand_direction": brief.brand_direction, "approved_claims": list(brief.approved_claims), "approved_asset_urls": list(brief.approved_asset_urls)},
            "quality_dimensions": ["conversion_clarity", "luxury_coherence", "mobile_usability", "accessibility_readiness", "evidence_integrity"],
            "required_output_fields": ["schema_version", "parent_digest", "pages", "palette", "font_stack", "motion_principle", "hypotheses", "used_claims"]}
        result = await self._call(self._luna, LUNA_MODEL_ID, payload, request, Stage.BLUEPRINT, records)
        if isinstance(result, ProductionFailure): return result
        try: output = Blueprint.from_json(result, request)
        except ContractError: return self._failure(request, Stage.BLUEPRINT, "SCHEMA_REJECTED", records)
        records.append(StageRecord(Stage.BLUEPRINT, LUNA_MODEL_ID, PROMPT_VERSION, request.request_digest, output.digest)); return output

    async def _audit(self, request, blueprint, records):
        payload = {"directive": _DIRECTIVE, "stage": Stage.AUDIT.value, "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION, "parent_digest": blueprint.digest,
            "blueprint": {"pages": [{"path": p.path, "title": p.title, "purpose": p.purpose, "sections": list(p.sections)} for p in blueprint.pages], "palette": list(blueprint.palette), "font_stack": blueprint.font_stack, "motion_principle": blueprint.motion_principle, "hypotheses": [{"hypothesis": h.hypothesis, "target_event": h.target_event, "supporting_fields": list(h.supporting_fields)} for h in blueprint.hypotheses], "used_claims": list(blueprint.used_claims)},
            "approved_brand_brief": {"company_name": request.brand_brief.company_name, "primary_cta": request.brand_brief.primary_cta, "approved_claims": list(request.brand_brief.approved_claims)},
            "required_output_fields": ["schema_version", "parent_digest", "scores", "violations", "required_changes"]}
        result = await self._call(self._terra, TERRA_MODEL_ID, payload, request, Stage.AUDIT, records)
        if isinstance(result, ProductionFailure): return result
        try: output = DesignAudit.from_json(result, blueprint)
        except ContractError: return self._failure(request, Stage.AUDIT, "SCHEMA_REJECTED", records)
        records.append(StageRecord(Stage.AUDIT, TERRA_MODEL_ID, PROMPT_VERSION, blueprint.digest, output.digest)); return output

    async def _build(self, request, blueprint, audit, records):
        payload = {"directive": _DIRECTIVE, "stage": Stage.BUILD.value, "schema_version": SCHEMA_VERSION, "prompt_version": PROMPT_VERSION, "parent_digest": audit.digest, "blueprint_digest": blueprint.digest,
            "audit": {"scores": dict(audit.scores), "violations": list(audit.violations), "required_changes": list(audit.required_changes)},
            "approved_brand_brief": {"company_name": request.brand_brief.company_name, "services": list(request.brand_brief.services), "audience": request.brand_brief.audience, "primary_cta": request.brand_brief.primary_cta, "contact_text": request.brand_brief.contact_text, "approved_claims": list(request.brand_brief.approved_claims), "approved_asset_urls": list(request.brand_brief.approved_asset_urls)},
            "artifact_rules": {"required_files": ["index.html", "styles.css"], "max_files": 30, "max_total_bytes": 750000, "destination": "BOT_6_QA_ONLY"},
            "required_output_fields": ["schema_version", "parent_digest", "blueprint_digest", "outcome", "files", "used_claims", "reason_codes"]}
        result = await self._call(self._sol, SOL_MODEL_ID, payload, request, Stage.BUILD, records)
        if isinstance(result, ProductionFailure): return result
        try: output = SiteBuild.from_json(result, request, blueprint, audit)
        except ContractError: return self._failure(request, Stage.BUILD, "SCHEMA_REJECTED", records)
        records.append(StageRecord(Stage.BUILD, SOL_MODEL_ID, PROMPT_VERSION, audit.digest, output.digest)); return output

    async def _call(self, port: StructuredModelPort, expected: str, payload: Mapping[str, Any], request: ProductionRequest, stage: Stage, records):
        try: result = await port.complete(payload)
        except Exception: return self._failure(request, stage, "MODEL_TRANSPORT_ERROR", records)
        if port.model_id != expected: return self._failure(request, stage, "MODEL_IDENTITY_CHANGED", records)
        if not isinstance(result, Mapping): return self._failure(request, stage, "MODEL_OUTPUT_NOT_OBJECT", records)
        return result

    @staticmethod
    def _failure(request, stage, code, records):
        return ProductionFailure(request.operation_id, request.request_digest, stage, code, tuple(records))
