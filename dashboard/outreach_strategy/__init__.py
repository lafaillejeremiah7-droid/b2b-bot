"""Company Bot 3: evidence-linked outreach strategy."""

from dashboard.outreach_strategy.contracts import (
    OutreachFailure,
    OutreachOutcome,
    OutreachPacket,
    OutreachRequest,
)
from dashboard.outreach_strategy.orchestrator import OutreachStrategyOrchestrator

__all__ = [
    "OutreachFailure",
    "OutreachOutcome",
    "OutreachPacket",
    "OutreachRequest",
    "OutreachStrategyOrchestrator",
]
