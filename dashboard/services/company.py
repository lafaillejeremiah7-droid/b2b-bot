from __future__ import annotations

from dataclasses import dataclass

from .closer import Closer, CloserDecision
from .six_employee_pipeline import Lead, PipelineResult, SixEmployeePipeline


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
