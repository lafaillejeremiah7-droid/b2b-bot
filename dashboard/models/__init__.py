"""Persistence layer (design §3.0.1 rule 1, §4.1–§4.5).

Task 1.3 adds ``Operator`` — the ``operators`` table and the project's
``AUTH_USER_MODEL``. Tasks 2.1–2.3 add ``leads``, ``deals``, and the remaining
tables of Requirement 13.5. Everything is re-exported here so
``from dashboard.models import X`` is the single import surface the
import-linter contracts target.
"""

from dashboard.models.operator import Operator, OperatorManager, Role

__all__ = ["Operator", "OperatorManager", "Role"]
