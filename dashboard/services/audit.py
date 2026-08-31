from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from django.db import transaction
from django.db.models import Model

from dashboard.models import AuditActionType, AuditEntry, Operator
from dashboard.services.errors import ActionRejected

T = TypeVar("T")


class AuditLogger:
    """Single append-only audit writer used by every formal dashboard action."""

    @staticmethod
    def _target(target: Model | tuple[str, int] | None) -> tuple[str, int]:
        if target is None:
            return "unknown", 0
        if isinstance(target, tuple):
            return str(target[0]), int(target[1])
        return target._meta.model_name, int(target.pk)

    @classmethod
    def record(
        cls,
        actor: Operator,
        action_type: str,
        target: Model | tuple[str, int] | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        *,
        occurred_at=None,
    ) -> AuditEntry:
        target_type, target_id = cls._target(target)
        kwargs: dict[str, Any] = {
            "actor": actor,
            "action_type": action_type,
            "target_type": target_type,
            "target_id": target_id,
            "before_value": before,
            "after_value": after,
        }
        if occurred_at is not None:
            kwargs["occurred_at"] = occurred_at
        return AuditEntry.objects.create(**kwargs)


def apply_action(
    *,
    actor: Operator,
    handler: Callable[..., T],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> T:
    """Run one operator action atomically; persist a rejection only after rollback."""

    try:
        with transaction.atomic():
            return handler(*args, **(kwargs or {}))
    except ActionRejected as rejection:
        # A fresh transaction is intentional: the rejected action's transaction
        # is already gone, while the rejected-attempt audit record must survive.
        with transaction.atomic():
            AuditLogger.record(
                actor,
                AuditActionType.REJECTED_ACTION_ATTEMPT,
                (rejection.target_type, rejection.target_id),
                rejection.before_snapshot,
                None,
                occurred_at=rejection.occurred_at,
            )
        raise
