from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .closer import Closer, CloserDecision
from .discovery_handoff import ScoutHandoff, apply_scout_handoff
from .six_employee_pipeline import Lead, PipelineResult, SixEmployeePipeline


class ScoutCandidate(Protocol):
    business_name: str
    website_uri: str

    def to_scout_handoff(self) -> ScoutHandoff: ...


class ScoutSearchClient(Protocol):
    def search(self, text_query: str, *, max_results: int = 10) -> list[ScoutCandidate]: ...


@dataclass(frozen=True)
class CompanyReplyResult:
    employee: str
    decision: CloserDecision


class SevenEmployeeCompany:
    """Company facade for the six outbound workers plus Employee #7, the Closer."""

    employee_names = (
        "Scout",
        "Researcher",
        "Qualifier",
        "Personalizer",
        "Sales Bot",
        "Manager",
        "Closer",
    )

    def __init__(self) -> None:
        self.outbound = SixEmployeePipeline()
        self.closer = Closer()

    def scout_google_maps(
        self,
        text_query: str,
        *,
        client: ScoutSearchClient,
        max_results: int = 10,
    ) -> list[Lead]:
        """Employee #1 discovers candidates and attaches a verified Scout handoff.

        Contact/email research is intentionally not done here. The returned leads
        must receive a ResearchHandoff before the outbound pipeline can advance.
        """
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

    def prepare_outreach(self, lead: Lead) -> PipelineResult:
        """Run Employees 1-6. The Closer has no work until a reply exists."""
        return self.outbound.run(lead)

    def handle_reply(
        self,
        reply_text: str,
        first_name: str = "there",
        *,
        lead_id: str = "",
        thread_id: str = "",
    ) -> CompanyReplyResult:
        """Run Employee #7 on an inbound reply while preserving persistence keys."""
        return CompanyReplyResult(
            employee=self.closer.name,
            decision=self.closer.run(
                reply_text=reply_text,
                first_name=first_name,
                lead_id=lead_id,
                thread_id=thread_id,
            ),
        )
