from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .boss import Boss, BossDecision
from .closer import Closer, CloserDecision
from .discovery_handoff import (
    ResearchHandoff,
    ScoutHandoff,
    apply_research_handoff,
    apply_scout_handoff,
    verify_scout_handoff,
)
from .six_employee_pipeline import Lead, PipelineResult, SalesBot, SixEmployeePipeline
from .suppression import SuppressionStore


class ScoutCandidate(Protocol):
    business_name: str
    website_uri: str

    def to_scout_handoff(self) -> ScoutHandoff: ...


class ScoutSearchClient(Protocol):
    def search(self, text_query: str, *, max_results: int = 10) -> list[ScoutCandidate]: ...


class ResearchClient(Protocol):
    def research(self, scout: ScoutHandoff) -> ResearchHandoff: ...


class DeliveryReceiptLike(Protocol):
    message_id: str
    thread_id: str


class DeliveryClient(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> DeliveryReceiptLike: ...


@dataclass(frozen=True)
class CompanyReplyResult:
    employee: str
    decision: CloserDecision
    boss: BossDecision | None = None


@dataclass(frozen=True)
class CompanyDeliveryResult:
    employee: str
    recipient: str
    message_id: str
    thread_id: str
    boss: BossDecision | None = None


class EightEmployeeCompany:
    """Company facade for six outbound workers, Closer #7, and Boss #8.

    Boss supervises results and priorities but cannot bypass Researcher evidence,
    outreach clearance, durable suppression, or the delivery boundary.
    """

    employee_names = (
        "Scout",
        "Researcher",
        "Qualifier",
        "Personalizer",
        "Sales Bot",
        "Manager",
        "Closer",
        "Boss",
    )

    def __init__(self, *, suppression_store: SuppressionStore | None = None) -> None:
        self.outbound = SixEmployeePipeline()
        self.closer = Closer()
        self.boss = Boss()
        self.suppression_store = suppression_store

    def scout_google_maps(
        self,
        text_query: str,
        *,
        client: ScoutSearchClient,
        max_results: int = 10,
    ) -> list[Lead]:
        """Employee #1 discovers candidates and attaches a verified Scout handoff."""
        leads: list[Lead] = []
        for candidate in client.search(text_query, max_results=max_results):
            lead = Lead(
                name=candidate.business_name,
                email="",
                company=candidate.business_name,
                website=candidate.website_uri,
                source="google_maps",
            )
            apply_scout_handoff(lead, candidate.to_scout_handoff())
            leads.append(lead)
        return leads

    def _scout_from_lead(self, lead: Lead) -> ScoutHandoff:
        if not verify_scout_handoff(lead):
            raise ValueError("Researcher requires an intact Scout handoff.")
        payload = lead.notes.get("scout_handoff")
        if not isinstance(payload, dict):
            raise ValueError("Scout handoff payload is missing.")
        return ScoutHandoff.from_payload(payload)

    def research_lead(self, lead: Lead, *, client: ResearchClient) -> Lead:
        """Employee #2 verifies contact/site evidence with an explicit research client."""
        scout = self._scout_from_lead(lead)
        handoff = client.research(scout)
        apply_research_handoff(lead, handoff)
        return lead

    def research_discovered_lead(
        self,
        lead: Lead,
        *,
        website_client: ResearchClient,
        no_website_client: ResearchClient,
    ) -> Lead:
        """Route Researcher automatically using Scout's candidate-website evidence."""
        scout = self._scout_from_lead(lead)
        client = website_client if scout.candidate_website else no_website_client
        handoff = client.research(scout)
        apply_research_handoff(lead, handoff)
        return lead

    def _refresh_suppression(self, lead: Lead) -> None:
        if self.suppression_store is None or not lead.email.strip():
            return
        if self.suppression_store.is_suppressed(lead.email):
            lead.notes["suppressed"] = True

    def prepare_outreach(self, lead: Lead) -> PipelineResult:
        """Run Employees 1-6, then have Boss audit the exact result."""
        self._refresh_suppression(lead)
        result = self.outbound.run(lead)
        lead.notes["boss_review"] = self.boss.review_outbound(result).payload()
        return result

    def deliver_outreach(
        self,
        result: PipelineResult,
        *,
        client: DeliveryClient,
    ) -> CompanyDeliveryResult:
        """Re-run Employees 1-6, then let Sales Bot submit the exact approved message."""
        if not result.approved_to_send:
            raise ValueError("Delivery rejected: the outbound pipeline did not pass.")
        if not result.lead.email.strip():
            raise ValueError("Delivery rejected: recipient email is missing.")

        self._refresh_suppression(result.lead)
        if result.lead.notes.get("suppressed") or result.lead.notes.get("opted_out"):
            raise ValueError("Delivery rejected: lead is suppressed.")

        # Nothing is trusted just because it passed earlier. Re-run the complete
        # Scout -> Researcher -> Qualifier -> Personalizer -> Sales Bot -> Manager
        # chain immediately before the network side effect. This catches evidence,
        # recipient, clearance, template, or configuration drift between prepare
        # and send. If the regenerated message changes, require a fresh prepare.
        revalidated = self.outbound.run(result.lead)
        if not revalidated.approved_to_send:
            raise ValueError("Delivery rejected: final six-worker revalidation failed.")
        if revalidated.subject != result.subject or revalidated.body != result.body:
            raise ValueError("Delivery rejected: approved message changed before submission.")

        receipt = SalesBot().deliver_outreach(revalidated, client=client)
        if not receipt.message_id or not receipt.thread_id:
            raise ValueError("Delivery adapter returned an incomplete receipt.")
        revalidated.lead.notes["sent_message_id"] = receipt.message_id
        revalidated.lead.notes["sent_thread_id"] = receipt.thread_id
        revalidated.lead.notes["delivery_status"] = "sent"
        boss_decision = self.boss.review_outbound(revalidated)
        revalidated.lead.notes["boss_review"] = boss_decision.payload()
        return CompanyDeliveryResult(
            employee="Sales Bot",
            recipient=revalidated.lead.email.strip().lower(),
            message_id=receipt.message_id,
            thread_id=receipt.thread_id,
            boss=boss_decision,
        )

    def handle_reply(
        self,
        reply_text: str,
        first_name: str = "there",
        *,
        lead_id: str = "",
        thread_id: str = "",
        recipient_email: str = "",
    ) -> CompanyReplyResult:
        """Run Closer, persist suppression if needed, then have Boss prioritize it."""
        decision = self.closer.run(
            reply_text=reply_text,
            first_name=first_name,
            lead_id=lead_id,
            thread_id=thread_id,
        )
        if decision.suppression_required:
            destination = recipient_email.strip().lower()
            if not destination or "@" not in destination or len(destination) > 320:
                raise RuntimeError(
                    "Closer requested suppression but the exact recipient email is unavailable."
                )
            if self.suppression_store is None:
                raise RuntimeError(
                    "Closer requested suppression but no durable suppression store is configured."
                )
            self.suppression_store.suppress(
                destination,
                reason=decision.category.value,
                lead_reference=lead_id,
                thread_id=thread_id,
            )
        return CompanyReplyResult(
            employee=self.closer.name,
            decision=decision,
            boss=self.boss.review_reply(decision),
        )


# Compatibility alias for code written before Boss #8 was introduced.
SevenEmployeeCompany = EightEmployeeCompany
