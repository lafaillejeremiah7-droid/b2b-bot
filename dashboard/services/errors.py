from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.utils import timezone


class DashboardError(Exception):
    """Base class for errors that are safe to surface to an operator."""


@dataclass
class ActionRejected(DashboardError):
    message: str
    action_type: str = "rejected_action_attempt"
    target_type: str = "unknown"
    target_id: int = 0
    before_snapshot: dict[str, Any] | None = None
    occurred_at: datetime = field(default_factory=timezone.now)

    def __str__(self) -> str:
        return self.message


class ValidationRejected(ActionRejected):
    pass


class AuthorizationRejected(ActionRejected):
    pass


class TransitionRejected(ActionRejected):
    pass


class ComplianceRejected(ActionRejected):
    pass


class ConfirmationRejected(ActionRejected):
    pass


class AdapterFailure(DashboardError):
    pass


class RecordNotFound(DashboardError):
    pass


class ConcurrencyRejected(ActionRejected):
    pass
