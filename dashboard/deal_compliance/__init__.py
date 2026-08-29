"""Company Bot 4: deal and compliance recommendations."""

from dashboard.deal_compliance.contracts import (
    DealAction,
    DealFailure,
    DealPacket,
    DealRequest,
    OperatorApproval,
    ProspectEvent,
)
from dashboard.deal_compliance.orchestrator import DealComplianceOrchestrator

__all__ = [
    "DealAction", "DealComplianceOrchestrator", "DealFailure", "DealPacket",
    "DealRequest", "OperatorApproval", "ProspectEvent",
]
