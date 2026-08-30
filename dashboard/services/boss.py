from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Iterable

from .closer import CloserDecision, ReplyCategory
from .six_employee_pipeline import PipelineResult


class BossAction(StrEnum):
    READY_FOR_DELIVERY = "ready_for_delivery"
    MONITOR_REPLY = "monitor_reply"
    REVIEW_RESEARCH = "review_research"
    DROP_LEAD = "drop_lead"
    OWNER_REVIEW = "owner_review"
    SUPPRESSED = "suppressed"
    INVESTIGATE = "investigate"


@dataclass(frozen=True)
class BossDecision:
    action: BossAction
    reason: str
    responsible_employee: str = ""
    owner_attention: bool = False
    priority: int = 0

    def payload(self) -> dict[str, object]:
        data = asdict(self)
        data["action"] = self.action.value
        return data


@dataclass(frozen=True)
class EmployeeKPI:
    employee: str
    complete: int = 0
    blocked: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class BossSnapshot:
    leads_reviewed: int
    outbound_approved: int
    outbound_sent: int
    replies_reviewed: int
    positive_replies: int
    suppressions: int
    employee_kpis: tuple[EmployeeKPI, ...]


class Boss:
    """Employee #8: supervise the company without bypassing worker controls.

    Boss is intentionally read-only with respect to outreach authorization. It may
    audit, prioritize, and report, but it cannot create Researcher evidence,
    clearance, suppression exceptions, or Gmail submissions.
    """

    name = "Boss"

    def review_outbound(self, result: PipelineResult) -> BossDecision:
        lead = result.lead
        if lead.notes.get("suppressed") or lead.notes.get("opted_out"):
            return BossDecision(
                action=BossAction.SUPPRESSED,
                reason="Lead is in a do-not-contact state; no outbound action is permitted.",
                responsible_employee="Sales Bot",
                priority=100,
            )

        non_complete = [
            stage
            for stage in result.stages
            if stage.get("status") != "complete"
        ]
        if non_complete:
            blocker = non_complete[0]
            employee = str(blocker.get("employee", ""))
            output = str(blocker.get("output", "Pipeline stage did not complete."))

            if employee == "Scout":
                action = BossAction.DROP_LEAD
                attention = False
                priority = 20
            elif employee == "Researcher":
                action = BossAction.REVIEW_RESEARCH
                attention = False
                priority = 40
            elif employee == "Qualifier":
                action = BossAction.DROP_LEAD
                attention = False
                priority = 20
            elif employee in {"Personalizer", "Sales Bot"}:
                action = BossAction.OWNER_REVIEW
                attention = True
                priority = 70
            else:
                action = BossAction.INVESTIGATE
                attention = True
                priority = 80

            return BossDecision(
                action=action,
                reason=f"{employee or 'Pipeline'} stopped the lead: {output}",
                responsible_employee=employee,
                owner_attention=attention,
                priority=priority,
            )

        if not result.approved_to_send:
            return BossDecision(
                action=BossAction.INVESTIGATE,
                reason="Every visible stage completed but Manager did not approve delivery.",
                responsible_employee="Manager",
                owner_attention=True,
                priority=90,
            )

        if lead.notes.get("delivery_status") == "sent":
            return BossDecision(
                action=BossAction.MONITOR_REPLY,
                reason="Delivery receipt is recorded; wait for and classify any inbound reply.",
                responsible_employee="Closer",
                priority=30,
            )

        return BossDecision(
            action=BossAction.READY_FOR_DELIVERY,
            reason="All six outbound workers completed and Manager approved the exact message.",
            responsible_employee="Sales Bot",
            priority=50,
        )

    def review_reply(self, decision: CloserDecision) -> BossDecision:
        if decision.suppression_required or decision.category in {
            ReplyCategory.UNSUBSCRIBE,
            ReplyCategory.NOT_INTERESTED,
        }:
            return BossDecision(
                action=BossAction.SUPPRESSED,
                reason=decision.reason,
                responsible_employee="Closer",
                priority=100,
            )

        if decision.category in {ReplyCategory.INTERESTED, ReplyCategory.MEETING_REQUEST}:
            return BossDecision(
                action=BossAction.OWNER_REVIEW,
                reason=decision.reason,
                responsible_employee="Closer",
                owner_attention=True,
                priority=95,
            )

        if decision.escalate_to_owner:
            return BossDecision(
                action=BossAction.OWNER_REVIEW,
                reason=decision.reason,
                responsible_employee="Closer",
                owner_attention=True,
                priority=80,
            )

        return BossDecision(
            action=BossAction.INVESTIGATE,
            reason="Closer produced no explicit terminal or escalation action.",
            responsible_employee="Closer",
            owner_attention=True,
            priority=70,
        )

    def snapshot(
        self,
        outbound_results: Iterable[PipelineResult],
        reply_decisions: Iterable[CloserDecision] = (),
    ) -> BossSnapshot:
        results = tuple(outbound_results)
        replies = tuple(reply_decisions)
        counters: dict[str, dict[str, int]] = {}

        for result in results:
            for stage in result.stages:
                employee = str(stage.get("employee", "Unknown"))
                status = str(stage.get("status", "failed"))
                employee_counts = counters.setdefault(
                    employee,
                    {"complete": 0, "blocked": 0, "failed": 0, "skipped": 0},
                )
                if status not in employee_counts:
                    status = "failed"
                employee_counts[status] += 1

        employee_kpis = tuple(
            EmployeeKPI(employee=employee, **counts)
            for employee, counts in sorted(counters.items())
        )
        positive = sum(
            decision.category in {ReplyCategory.INTERESTED, ReplyCategory.MEETING_REQUEST}
            for decision in replies
        )
        suppressions = sum(decision.suppression_required for decision in replies)

        return BossSnapshot(
            leads_reviewed=len(results),
            outbound_approved=sum(result.approved_to_send for result in results),
            outbound_sent=sum(
                result.lead.notes.get("delivery_status") == "sent"
                for result in results
            ),
            replies_reviewed=len(replies),
            positive_replies=positive,
            suppressions=suppressions,
            employee_kpis=employee_kpis,
        )
