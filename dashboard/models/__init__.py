"""Persistence layer (design §3.0.1 rule 1, §4.1–§4.5).

Task 1.3 adds ``Operator`` — the ``operators`` table and the project's
``AUTH_USER_MODEL``. Task 2.1 adds ``leads``, task 2.2 adds ``deals``, ``emails``
and ``calls``, and task 2.3 adds the remaining tables of Requirement 13.5.
Everything is re-exported here so ``from dashboard.models import X`` is the single
import surface the import-linter contracts target.

Two support modules are re-exported alongside the models because later tasks need
them by name: ``constraints`` (the §4.3 bound builders every table shares) and
``fields`` (:class:`~dashboard.models.fields.MillisecondDateTimeField`, the
``TIMESTAMPTZ(3)`` columns of Requirements 8.5 and 8.8).
"""

from dashboard.models.constraints import length_at_most, length_between, unset_or
from dashboard.models.deal import Deal
from dashboard.models.fields import (
    MillisecondDateTimeField,
    truncate_to_millisecond,
)
from dashboard.models.forward_references import (
    PENDING_FOREIGN_KEYS,
    PendingForeignKey,
)
from dashboard.models.lead import (
    Lead,
    NormalizedEmail,
    PhoneDigits,
    PipelineState,
)
from dashboard.models.operator import Operator, OperatorManager, Role
from dashboard.models.outreach import Call, CallOutcome, Email

__all__ = [
    "PENDING_FOREIGN_KEYS",
    "Call",
    "CallOutcome",
    "Deal",
    "Email",
    "Lead",
    "MillisecondDateTimeField",
    "NormalizedEmail",
    "Operator",
    "OperatorManager",
    "PendingForeignKey",
    "PhoneDigits",
    "PipelineState",
    "Role",
    "length_at_most",
    "length_between",
    "truncate_to_millisecond",
    "unset_or",
]
