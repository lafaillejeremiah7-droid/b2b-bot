"""Bot 1: evidence-bound business discovery.

This package is intentionally pure. It does not import Django models, call the
pipeline adapter, send outreach, or create Leads. Its only output is a sealed
``DiscoveryPacket`` for the future qualification bot and dashboard-owned Lead
creation seam.
"""

from dashboard.discovery.contracts import (
    DecisionOutcome,
    DiscoveryFailure,
    DiscoveryPacket,
    DiscoveryRequest,
    DiscoverySource,
    IdempotencyClaim,
)
from dashboard.discovery.orchestrator import DiscoveryOrchestrator

__all__ = [
    "DecisionOutcome",
    "DiscoveryFailure",
    "DiscoveryOrchestrator",
    "DiscoveryPacket",
    "DiscoveryRequest",
    "DiscoverySource",
    "IdempotencyClaim",
]
