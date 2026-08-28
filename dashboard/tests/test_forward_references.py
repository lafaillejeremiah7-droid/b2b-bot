"""The expiry date on task 2.2's four unreferenced columns (Requirements 13.5, 13.9).

Task 2.2 declared ``deals.invoice_id``, ``emails.outreach_request_id``,
``emails.site_project_id`` and ``calls.outreach_request_id`` without a
``REFERENCES`` clause, because they point at tables task 2.3 creates and Django
cannot build a ``ForeignKey`` to a model that does not exist. The reasoning and the
``SeparateDatabaseAndState`` shape task 2.3 must use are in the
``dashboard.models.deal`` module docstring; the register is
``dashboard.models.forward_references.PENDING_FOREIGN_KEYS``.

**This module is what makes that a deferral rather than a hole.** Requirement 13.5
requires real database references and §4.3 is explicit that the alternative — an
application-level existence check — is not acceptable. The test below reads the
PostgreSQL catalog and asserts, per entry:

* the column still exists, with the name, type and nullability task 2.2 declared,
  so a task 2.3 migration that drops and recreates it is caught;
* **if** the referenced table now exists, a foreign key from that column to it
  exists too.

Today every referenced table is absent, so the conditional half is vacuous and
says so out loud in the report. The moment task 2.3 creates ``outreach_requests``,
``site_projects`` or ``invoices``, that half becomes live and fails until the
reference is wired. Nothing has to remember to switch it on, which is the same
argument ``scripts/check_deferred_activations.py`` makes for task 1.4's
switched-off contracts.

When the last entry is wired, delete the register and this module.
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase

from dashboard.models import PENDING_FOREIGN_KEYS

#: The shape task 2.2 declared, per entry, so a drop-and-recreate is visible.
DECLARED_SHAPE = {
    ("deals", "invoice_id"): ("bigint", "YES", False),
    ("emails", "outreach_request_id"): ("uuid", "NO", True),
    ("emails", "site_project_id"): ("bigint", "YES", False),
    ("calls", "outreach_request_id"): ("uuid", "YES", True),
}


def table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [name])
        return cursor.fetchone()[0]


def column_shape(table: str, column: str) -> tuple[str, str] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
            """,
            [table, column],
        )
        result = cursor.fetchone()
    return (result[0], result[1]) if result else None


def has_unique(table: str, column: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*) FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conrelid = %s::regclass
              AND c.contype IN ('u', 'p')
              AND cardinality(c.conkey) = 1
              AND a.attname = %s
            """,
            [table, column],
        )
        return cursor.fetchone()[0] > 0


def references(table: str, column: str) -> str | None:
    """The table ``table.column``'s single-column foreign key points at, if any."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT confrelid::regclass::text FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
            WHERE c.conrelid = %s::regclass
              AND c.contype = 'f'
              AND cardinality(c.conkey) = 1
              AND a.attname = %s
            """,
            [table, column],
        )
        result = cursor.fetchone()
    return result[0] if result else None


class PendingForeignKeyTests(TestCase):
    """Requirement 13.5: every reference ends up a real database reference."""

    def test_the_register_matches_the_shapes_this_task_declared(self):
        self.assertEqual(
            {(entry.table, entry.column) for entry in PENDING_FOREIGN_KEYS},
            set(DECLARED_SHAPE),
        )

    def test_each_column_still_carries_the_shape_task_2_2_declared(self):
        """A ``RemoveField`` + ``AddField`` swap would satisfy "the column has a
        foreign key" while having dropped the ``UNIQUE`` index and the ``NOT NULL``
        this task declared, along with any row. Asserting the shape as well as the
        reference is what makes the swap detectable."""
        for entry in PENDING_FOREIGN_KEYS:
            expected_type, expected_nullable, expected_unique = DECLARED_SHAPE[
                (entry.table, entry.column)
            ]
            with self.subTest(table=entry.table, column=entry.column):
                self.assertEqual(
                    column_shape(entry.table, entry.column),
                    (expected_type, expected_nullable),
                )
                self.assertEqual(
                    has_unique(entry.table, entry.column), expected_unique
                )

    def test_every_reference_is_real_once_its_table_exists(self):
        """The live half. Vacuous while task 2.3 has not run; a failing build the
        moment it creates a referenced table without wiring the reference."""
        checked = 0
        for entry in PENDING_FOREIGN_KEYS:
            if not table_exists(entry.references):
                continue
            checked += 1
            with self.subTest(
                table=entry.table,
                column=entry.column,
                references=entry.references,
                owning_task=entry.owning_task,
            ):
                self.assertEqual(
                    references(entry.table, entry.column),
                    entry.references,
                    msg=(
                        f"{entry.table}.{entry.column} must be a real database "
                        f"REFERENCES {entry.references}({entry.references_column}) "
                        f"now that {entry.references} exists "
                        f"(Requirements {entry.requirement}; task "
                        f"{entry.owning_task} owns attaching it). Attach the "
                        f"constraint to the existing column with "
                        f"SeparateDatabaseAndState — do not let the autodetector "
                        f"drop and recreate it. See the dashboard.models.deal "
                        f"module docstring."
                    ),
                )
        print(
            f"\nforward references: {checked} of {len(PENDING_FOREIGN_KEYS)} "
            f"referenced tables exist and were checked."
        )

    def test_the_references_this_task_could_declare_are_declared(self):
        """The other side of the same rule: where the referenced table *did* exist,
        task 2.2 declared a real foreign key rather than deferring for symmetry."""
        self.assertEqual(references("deals", "lead_id"), "leads")
        self.assertEqual(references("deals", "verified_by_operator_id"), "operators")
        self.assertEqual(references("emails", "lead_id"), "leads")
        self.assertEqual(references("calls", "lead_id"), "leads")
