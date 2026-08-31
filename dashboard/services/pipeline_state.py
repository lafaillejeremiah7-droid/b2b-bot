from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from dashboard.models import (
    AuditActionType,
    Deal,
    HistoryActorKind,
    Invoice,
    Lead,
    Operator,
    PipelineState,
    PipelineStateHistory,
    ReleaseAuthorization,
)
from dashboard.services.audit import AuditLogger
from dashboard.services.errors import ConcurrencyRejected, TransitionRejected, ValidationRejected

S = PipelineState

TERMINAL_STATES = frozenset({S.RELEASED, S.CLOSED_LOST})
LEGAL_TRANSITIONS = frozenset(
    {
        (S.NEW_LEAD, S.CONTACTED),
        (S.NEW_LEAD, S.CLOSED_LOST),
        (S.CONTACTED, S.REPLIED),
        (S.CONTACTED, S.CLOSED_LOST),
        (S.REPLIED, S.SCHEDULED),
        (S.REPLIED, S.QUOTED),
        (S.REPLIED, S.CLOSED_LOST),
        (S.SCHEDULED, S.QUOTED),
        (S.SCHEDULED, S.CLOSED_LOST),
        (S.QUOTED, S.WON),
        (S.QUOTED, S.CLOSED_LOST),
        (S.WON, S.INVOICED),
        (S.WON, S.CLOSED_LOST),
        (S.INVOICED, S.PAID_PENDING_VERIFICATION),
        (S.INVOICED, S.CLOSED_LOST),
        (S.PAID_PENDING_VERIFICATION, S.PAYMENT_VERIFIED),
        (S.PAYMENT_VERIFIED, S.RELEASED),
    }
)

assert len(LEGAL_TRANSITIONS) == 17
assert all(a != b for a, b in LEGAL_TRANSITIONS)
assert not any(a in TERMINAL_STATES for a, _ in LEGAL_TRANSITIONS)

EVENT_STATE_MAP = {
    "email_opened": None,
    "email_clicked": None,
    "prospect_replied": S.REPLIED,
    "email_bounced": None,
    "unsubscribed": None,
    "payment_received": S.PAID_PENDING_VERIFICATION,
    "site_generation_finished": None,
}
assert S.RELEASED not in EVENT_STATE_MAP.values()
assert S.PAYMENT_VERIFIED not in EVENT_STATE_MAP.values()


@dataclass(frozen=True)
class TransitionOutcome:
    lead_id: int
    from_state: PipelineState
    to_state: PipelineState
    state_version: int
    history_id: int


def legal_successors(state: PipelineState) -> tuple[PipelineState, ...]:
    return tuple(sorted((to for frm, to in LEGAL_TRANSITIONS if frm == state), key=lambda v: v.value))


def _parse_state(value: str | PipelineState) -> PipelineState:
    try:
        return value if isinstance(value, PipelineState) else PipelineState(value)
    except ValueError as exc:
        raise ValidationRejected(f"Unknown Pipeline_State: {value!r}.") from exc


def _deal(lead_id: int) -> Deal | None:
    return Deal.objects.filter(lead_id=lead_id).first()


def _assert_preconditions(lead: Lead, target: PipelineState) -> None:
    deal = _deal(lead.id)
    if target == S.INVOICED:
        invoice_exists = bool(deal and Invoice.objects.filter(deal_id=deal.pk).exists())
        if deal is None or deal.agreed_price is None or not invoice_exists:
            raise TransitionRejected(
                "Invoiced requires an agreed price and an invoice record.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"status": lead.status},
            )
    elif target == S.PAYMENT_VERIFIED:
        if deal is None or deal.payment_verified_at is None:
            raise TransitionRejected(
                "Payment_Verified requires the Payment_Verified_Flag.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"status": lead.status},
            )
    elif target == S.RELEASED:
        if deal is None or deal.payment_verified_at is None or not ReleaseAuthorization.objects.filter(deal_id=deal.pk).exists():
            raise TransitionRejected(
                "Released requires verified payment and a release authorization.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"status": lead.status},
            )


class PipelineStateMachine:
    @staticmethod
    @transaction.atomic
    def request(
        *,
        lead_id: int,
        to_state: str | PipelineState,
        actor: Operator,
        expected_from_state: str | PipelineState | None = None,
        expected_version: int | None = None,
    ) -> TransitionOutcome:
        target = _parse_state(to_state)
        try:
            lead = Lead.objects.select_for_update().get(pk=lead_id)
        except Lead.DoesNotExist as exc:
            raise ValidationRejected(f"Lead {lead_id} does not exist.") from exc

        current = PipelineState(lead.status)
        if current in TERMINAL_STATES:
            raise TransitionRejected(
                f"{current.value} is terminal and cannot transition.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"status": current.value, "state_version": lead.state_version},
            )
        if (current, target) not in LEGAL_TRANSITIONS:
            successors = ", ".join(v.value for v in legal_successors(current)) or "none"
            raise TransitionRejected(
                f"Illegal transition {current.value} → {target.value}; legal successors: {successors}.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"status": current.value, "state_version": lead.state_version},
            )

        _assert_preconditions(lead, target)

        expected_from = _parse_state(expected_from_state) if expected_from_state is not None else current
        expected_ver = lead.state_version if expected_version is None else int(expected_version)
        if expected_from != current or expected_ver != lead.state_version:
            raise ConcurrencyRejected(
                "The Lead changed before this action was applied; refresh and retry.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"status": current.value, "state_version": lead.state_version},
            )

        occurred_at = timezone.now()
        updated = Lead.objects.filter(
            pk=lead.id,
            status=current.value,
            state_version=expected_ver,
        ).update(
            status=target.value,
            state_version=F("state_version") + 1,
            last_activity_at=occurred_at,
        )
        if updated != 1:
            raise ConcurrencyRejected(
                "The Lead changed before this action was applied; refresh and retry.",
                target_type="lead",
                target_id=lead.id,
            )

        audit = AuditLogger.record(
            actor,
            AuditActionType.PIPELINE_STATE_CHANGE,
            lead,
            {"status": current.value, "state_version": expected_ver},
            {"status": target.value, "state_version": expected_ver + 1},
            occurred_at=occurred_at,
        )
        history = PipelineStateHistory.objects.create(
            lead=lead,
            from_state=current.value,
            to_state=target.value,
            occurred_at=occurred_at,
            actor=actor,
            actor_kind=HistoryActorKind.OPERATOR,
            audit_entry=audit,
        )
        return TransitionOutcome(lead.id, current, target, expected_ver + 1, history.id)

    @staticmethod
    @transaction.atomic
    def request_from_event(
        *,
        lead_id: int,
        event_type: str,
        event_id: str,
    ) -> TransitionOutcome | None:
        target = EVENT_STATE_MAP.get(event_type)
        if target is None:
            return None
        try:
            lead = Lead.objects.select_for_update().get(pk=lead_id)
        except Lead.DoesNotExist as exc:
            raise ValidationRejected(f"Lead {lead_id} does not exist.") from exc
        current = PipelineState(lead.status)
        if current in TERMINAL_STATES or (current, target) not in LEGAL_TRANSITIONS:
            raise TransitionRejected(
                f"Event {event_type} cannot move {current.value} to {target.value}.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"status": current.value, "event_type": event_type},
            )
        _assert_preconditions(lead, target)
        occurred_at = timezone.now()
        prior_version = lead.state_version
        updated = Lead.objects.filter(pk=lead.id, status=current.value).update(
            status=target.value,
            state_version=F("state_version") + 1,
            last_activity_at=occurred_at,
        )
        if updated != 1:
            raise ConcurrencyRejected("Concurrent state change detected.", target_type="lead", target_id=lead.id)
        history = PipelineStateHistory.objects.create(
            lead=lead,
            from_state=current.value,
            to_state=target.value,
            occurred_at=occurred_at,
            actor=None,
            actor_kind=HistoryActorKind.ADAPTER_EVENT,
            source_event_id=event_id,
        )
        return TransitionOutcome(lead.id, current, target, prior_version + 1, history.id)

    @staticmethod
    @transaction.atomic
    def create_lead(
        *,
        company_name: str,
        researched_score: int,
        actor: Operator | None = None,
        source_event_id: str | None = None,
        **fields,
    ) -> Lead:
        if "status" in fields and _parse_state(fields["status"]) != S.NEW_LEAD:
            raise ValidationRejected("A Lead must be created at New_Lead.")
        occurred_at = timezone.now()
        fields.pop("status", None)
        lead = Lead.objects.create(
            company_name=company_name,
            researched_score=researched_score,
            status=S.NEW_LEAD,
            last_activity_at=occurred_at,
            **fields,
        )
        if actor is not None:
            audit = AuditLogger.record(
                actor,
                AuditActionType.PIPELINE_STATE_CHANGE,
                lead,
                None,
                {"status": S.NEW_LEAD.value, "state_version": 0},
                occurred_at=occurred_at,
            )
            PipelineStateHistory.objects.create(
                lead=lead,
                from_state=None,
                to_state=S.NEW_LEAD,
                occurred_at=occurred_at,
                actor=actor,
                actor_kind=HistoryActorKind.OPERATOR,
                audit_entry=audit,
            )
        else:
            PipelineStateHistory.objects.create(
                lead=lead,
                from_state=None,
                to_state=S.NEW_LEAD,
                occurred_at=occurred_at,
                actor=None,
                actor_kind=HistoryActorKind.ADAPTER_EVENT,
                source_event_id=source_event_id or f"lead-create-{lead.id}",
            )
        return lead
