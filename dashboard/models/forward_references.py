"""The four columns task 2.2 declared without their foreign key, and the deadline.

Requirement 13.5 requires a real database ``REFERENCES`` for every reference, and
design §4.3 says why: an unresolvable reference must be rejected by the database
rather than by a hopeful application-level existence check (Requirement 13.9).
Task 2.2 could not honour that for four columns, because they point at tables task
2.3 creates and Django will not build a ``ForeignKey`` to a model that does not
exist. The reasoning, and the ``SeparateDatabaseAndState`` mechanism task 2.3 must
use so the swap attaches the constraint instead of dropping the column, are in the
``dashboard.models.deal`` module docstring.

**This module is the part that stops the deferral being permanent.**
``dashboard/tests/test_forward_references.py`` iterates
:data:`PENDING_FOREIGN_KEYS` and asserts, for each entry, that *if the referenced
table exists in the database then a foreign key on that column exists too*. The
test passes today because none of the three referenced tables exists. It starts
failing the moment task 2.3's migration creates one without wiring the reference,
and it keeps failing until the reference is real.

That shape is deliberate. A comment naming task 2.3 would be a comment; a check
whose subject appears on its own is a build step, which is the same argument
``scripts/check_deferred_activations.py`` makes for task 1.4's switched-off rules.
The predicate is the *database catalog*, not the model source, so it cannot be
satisfied by a Python-level relation that failed to produce DDL.

When task 2.3 attaches a reference, delete that entry from the tuple. When the
tuple is empty, delete it and its test.
"""

from __future__ import annotations

from typing import NamedTuple


class PendingForeignKey(NamedTuple):
    """A column awaiting the ``REFERENCES`` clause task 2.3 will attach."""

    #: The table holding the column.
    table: str
    #: The column, already carrying its final name, type, nullability, uniqueness.
    column: str
    #: The table it must reference once that table exists.
    references: str
    #: The column in ``references`` it must point at.
    references_column: str
    #: The task that owns attaching the constraint.
    owning_task: str
    #: The requirement the eventual foreign key satisfies.
    requirement: str


PENDING_FOREIGN_KEYS: tuple[PendingForeignKey, ...] = (
    PendingForeignKey(
        table="deals",
        column="invoice_id",
        references="invoices",
        references_column="id",
        owning_task="2.3",
        requirement="13.2, 13.5",
    ),
    PendingForeignKey(
        table="emails",
        column="outreach_request_id",
        references="outreach_requests",
        references_column="id",
        owning_task="2.3",
        requirement="13.3, 13.5, 5.9",
    ),
    PendingForeignKey(
        table="emails",
        column="site_project_id",
        references="site_projects",
        references_column="id",
        owning_task="2.3",
        requirement="13.5, 6.7",
    ),
    PendingForeignKey(
        table="calls",
        column="outreach_request_id",
        references="outreach_requests",
        references_column="id",
        owning_task="2.3",
        requirement="13.4, 13.5, 5.9",
    ),
)
