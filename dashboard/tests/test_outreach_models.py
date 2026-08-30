"""Tests for the ``emails`` and ``calls`` tables (Requirements 13.3, 13.4, 5.12, 5.18, 5.21, 5.22, 3.5).

**Every claim about a stored invariant is driven through raw SQL, not the ORM.**
These two tables are written by the dashboard *and* by the bot (design §4.2), and
Requirements 5.11, 5.12, 5.19 and 5.20 are stated as counts and orderings over
*stored rows*. A test that only exercised ``Email.objects.create`` would pass
against a schema with no constraints in it.

The centre of gravity here is the clearance pair. ``emails.clearance_timestamp``
is required and ``calls.clearance_timestamp`` is nullable for exactly one shape of
row, and task 3.2's compliance triggers compare that column — so a column that is
nullable when it should not be does not fail loudly, it silently turns a
compliance guard into a NULL comparison, which PostgreSQL counts as a satisfied
CHECK. These tests pin the nullability from both sides.

Scope: task 2.2's own constraints. Task 2.5 owns Properties 41 and 42; task 3.2
owns the triggers and the tests that a cleared-then-opted-out row is still
recorded.
"""

from __future__ import annotations

import datetime as dt
import uuid

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from dashboard.models import Call, CallOutcome, Email, Lead, PipelineState

MOMENT = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)
CLEARED_AT = dt.datetime(2026, 3, 1, 11, 59, 58, 500000, tzinfo=dt.timezone.utc)


def make_lead(name: str = "Acme Roofing") -> Lead:
    return Lead.objects.create(
        company_name=name,
        researched_score=3,
        status=PipelineState.NEW_LEAD,
        last_activity_at=MOMENT,
    )


def scalar(sql: str, params: list | None = None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchone()[0]


def row(sql: str, params: list | None = None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchone()


class RawInsertMixin:
    table: str
    pk_column: str = "id"

    def setUp(self):
        super().setUp()
        self.lead = make_lead()

    def valid(self) -> dict:
        raise NotImplementedError

    def raw_insert(self, **overrides) -> int:
        values = {**self.valid(), **overrides}
        columns = ", ".join(f'"{name}"' for name in values)
        placeholders = ", ".join(["%s"] * len(values))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self.table} ({columns}) VALUES ({placeholders}) "
                f"RETURNING {self.pk_column}",
                list(values.values()),
            )
            return cursor.fetchone()[0]

    def assert_rejected_by(self, constraint: str, **overrides):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.raw_insert(**overrides)
        self.assertIn(constraint, str(caught.exception))

    def assert_accepted(self, **overrides) -> int:
        with transaction.atomic():
            return self.raw_insert(**overrides)

    def columns(self) -> dict[str, tuple[str, str, int | None]]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable, datetime_precision
                FROM information_schema.columns
                WHERE table_name = %s
                """,
                [self.table],
            )
            return {
                name: (kind, nullable, precision)
                for name, kind, nullable, precision in cursor.fetchall()
            }

    def constraint_names(self, contype: str) -> set[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = %s::regclass AND contype = %s
                """,
                [self.table, contype],
            )
            return {name for (name,) in cursor.fetchall()}


class EmailMixin(RawInsertMixin):
    table = "emails"

    def valid(self) -> dict:
        return {
            "lead_id": self.lead.id,
            "outreach_request_id": str(uuid.uuid4()),
            "subject": "A quick question about your website",
            "body": "Hello — we build small business websites.",
            "clearance_timestamp": CLEARED_AT,
            "late_opt_out_marker": False,
            "sent_at": MOMENT,
            "unsubscribed": False,
        }


class CallMixin(RawInsertMixin):
    table = "calls"

    def valid(self) -> dict:
        return {
            "lead_id": self.lead.id,
            "outreach_request_id": str(uuid.uuid4()),
            "attempt_number": 1,
            "timestamp": MOMENT,
            "outcome": CallOutcome.ANSWERED.value,
            "clearance_timestamp": CLEARED_AT,
            "late_opt_out_marker": False,
        }


# ==========================================================================
# emails
# ==========================================================================
class EmailColumnShapeTests(EmailMixin, TestCase):
    """Requirement 13.3's column list, plus §3.8's ``site_project_id``."""

    REQUIRED_COLUMNS = frozenset(
        {
            "id",
            "lead_id",
            "outreach_request_id",
            "subject",
            "body",
            "clearance_timestamp",
            "late_opt_out_marker",
            "sent_at",
            "opened_at",
            "clicked_at",
            "reply_at",
            "unsubscribed",
            # design §3.8 — the Requirement 6.7 preview-link gate's operand
            "site_project_id",
        }
    )

    def test_every_declared_column_exists_and_nothing_else_does(self):
        self.assertEqual(set(self.columns()), self.REQUIRED_COLUMNS)

    def test_requirement_13_3s_four_required_columns_are_not_null(self):
        """13.3: "lead_id, outreach_request_id, clearance_timestamp, and sent_at
        are required" — plus subject, body, late_opt_out_marker and unsubscribed,
        which 13.3 declares with bounds and defaults rather than as nullable."""
        columns = self.columns()
        for name in (
            "lead_id",
            "outreach_request_id",
            "clearance_timestamp",
            "sent_at",
            "subject",
            "body",
            "late_opt_out_marker",
            "unsubscribed",
        ):
            with self.subTest(column=name, expected="NOT NULL"):
                self.assertEqual(columns[name][1], "NO")
        for name in ("opened_at", "clicked_at", "reply_at", "site_project_id"):
            with self.subTest(column=name, expected="NULL"):
                self.assertEqual(columns[name][1], "YES")

    def test_clearance_timestamp_is_timestamptz_at_millisecond_precision(self):
        kind, nullable, precision = self.columns()["clearance_timestamp"]
        self.assertEqual(kind, "timestamp with time zone")
        self.assertEqual(nullable, "NO")
        self.assertEqual(precision, 3)

    def test_every_other_timestamp_column_is_timestamptz(self):
        """Requirement 13.11: UTC, one second or finer."""
        columns = self.columns()
        for name in ("sent_at", "opened_at", "clicked_at", "reply_at"):
            with self.subTest(column=name):
                self.assertEqual(columns[name][0], "timestamp with time zone")

    def test_the_outreach_request_id_is_a_uuid(self):
        """§4.1 declares ``outreach_requests.id`` as ``uuid``, and this column has
        to match it for task 2.3's foreign key to attach."""
        self.assertEqual(self.columns()["outreach_request_id"][0], "uuid")

    def test_the_two_boolean_defaults_are_in_the_database(self):
        """Requirement 13.3's two ``default false`` booleans, proved on a
        connection Python's defaults never touch."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO emails (lead_id, outreach_request_id, subject, body,
                                    clearance_timestamp, sent_at)
                VALUES (%s, %s, 'Subject', 'Body', %s, %s)
                RETURNING late_opt_out_marker, unsubscribed
                """,
                [self.lead.id, str(uuid.uuid4()), CLEARED_AT, MOMENT],
            )
            self.assertEqual(cursor.fetchone(), (False, False))


class EmailConstraintTests(EmailMixin, TestCase):
    """Requirement 13.3's bounds, at the database."""

    def test_the_named_constraints_are_installed(self):
        self.assertEqual(
            {"emails_subject_length", "emails_body_length"} - self.constraint_names("c"),
            set(),
        )
        self.assertIn("emails_outreach_request_id_unique", self.constraint_names("u"))

    def test_lead_id_is_a_real_foreign_key(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attname, confrelid::regclass::text
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                WHERE c.conrelid = 'emails'::regclass AND c.contype = 'f'
                """
            )
            self.assertEqual(dict(cursor.fetchall()), {"lead_id": "leads"})

    def test_subject_holds_1_to_200_characters(self):
        self.assert_accepted(subject="A")
        self.assert_accepted(subject="s" * 200)
        self.assert_rejected_by("emails_subject_length", subject="")
        self.assert_rejected_by("emails_subject_length", subject="s" * 201)

    def test_body_holds_1_to_50000_characters(self):
        self.assert_accepted(body="B")
        self.assert_accepted(body="b" * 50_000)
        self.assert_rejected_by("emails_body_length", body="")
        self.assert_rejected_by("emails_body_length", body="b" * 50_001)

    def test_the_body_ceiling_is_deliberately_wider_than_the_composed_limit(self):
        """Requirement 13.3 stores up to 50,000 characters; Requirement 5.1 has
        the Outreach_Controller submit 1 to 10,000. The wider number is the shared
        storage ceiling, not a mistake — a 20,000-character row is storable and is
        not submittable, and neither number should be moved to match the other."""
        self.assert_accepted(body="b" * 20_000)

    def test_length_bounds_count_characters_not_bytes(self):
        self.assert_accepted(subject="é" * 200)
        self.assert_rejected_by("emails_subject_length", subject="é" * 201)

    def test_neither_required_column_accepts_null(self):
        for column in ("lead_id", "outreach_request_id", "subject", "body", "sent_at"):
            with self.subTest(column=column):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        self.raw_insert(**{column: None})


class EmailClearanceTests(EmailMixin, TestCase):
    """Requirements 13.3, 5.18: the clearance is required, with no way around it.

    ``clearance_timestamp`` is the operand of task 3.2's compliance trigger. If it
    were nullable, an insert omitting it would produce a row on which the
    trigger's comparison evaluates to NULL — which PostgreSQL treats as a
    satisfied CHECK — so the guard would be off for exactly the rows that had
    skipped the reservation. Hence both halves below: explicit NULL and omission.
    """

    def test_an_explicit_null_clearance_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.raw_insert(clearance_timestamp=None)

    def test_omitting_the_clearance_entirely_is_also_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO emails (lead_id, outreach_request_id, subject,
                                            body, sent_at)
                        VALUES (%s, %s, 'Subject', 'Body', %s)
                        """,
                        [self.lead.id, str(uuid.uuid4()), MOMENT],
                    )

    def test_the_clearance_has_no_database_default(self):
        """It is copied from the reservation, never conjured at record time.
        §3.6.4: "Phase 3 does not re-derive it and does not consult the clock."
        A ``DEFAULT now()`` would make every row look cleared, at the one instant
        that carries no compliance information."""
        self.assertIsNone(
            scalar(
                """
                SELECT column_default FROM information_schema.columns
                WHERE table_name = 'emails' AND column_name = 'clearance_timestamp'
                """
            )
        )

    def test_a_clearance_earlier_than_the_send_is_the_normal_shape(self):
        """§3.6.4: the clearance is recorded in Phase 1 and the row written in
        Phase 3, so it precedes ``sent_at`` on every row. Nothing constrains that
        ordering in the schema — it is a consequence of the sequence — and this
        test records the expectation the triggers of task 3.2 are written against.
        """
        email_id = self.raw_insert()
        clearance, sent_at = row(
            "SELECT clearance_timestamp, sent_at FROM emails WHERE id = %s",
            [email_id],
        )
        self.assertLess(clearance, sent_at)

    def test_a_late_opt_out_row_is_recorded_and_marked_rather_than_lost(self):
        """Requirement 5.21, as far as the schema can state it.

        The Lead unsubscribed *after* the clearance and before the row was
        written. The row is storable, carries the marker, and keeps a clearance
        strictly earlier than the opt-out — which is Requirement 5.19's invariant
        and the reason task 3.2's trigger can accept it. The notification and the
        setting of the marker are task 11.1's; what this asserts is that the
        schema does not stand in the way.
        """
        opted_out_at = dt.datetime(2026, 3, 1, 11, 59, 59, tzinfo=dt.timezone.utc)
        self.lead.unsubscribed_at = opted_out_at
        self.lead.save(update_fields=["unsubscribed_at"])
        email_id = self.assert_accepted(late_opt_out_marker=True)
        email = Email.objects.get(pk=email_id)
        self.assertTrue(email.was_sent_before_a_late_opt_out)
        self.assertLess(email.clearance_timestamp, opted_out_at)
        self.assertGreater(email.sent_at, opted_out_at)

    def test_millisecond_precision_survives_the_copy(self):
        cleared = dt.datetime(2026, 3, 1, 11, 59, 58, 123000, tzinfo=dt.timezone.utc)
        email = Email.objects.create(
            lead=self.lead,
            outreach_request_id=uuid.uuid4(),
            subject="Subject",
            body="Body",
            clearance_timestamp=cleared,
            sent_at=MOMENT,
        )
        email.refresh_from_db()
        self.assertEqual(email.clearance_timestamp, cleared)


class EmailIdempotencyTests(EmailMixin, TestCase):
    """Requirements 5.9, 5.10, 5.12: one row per outreach_request_id."""

    def test_a_second_row_for_the_same_request_id_is_rejected(self):
        request_id = str(uuid.uuid4())
        self.raw_insert(outreach_request_id=request_id)
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.raw_insert(outreach_request_id=request_id)
        self.assertIn("emails_outreach_request_id_unique", str(caught.exception))

    def test_two_rows_with_different_request_ids_are_fine(self):
        self.raw_insert()
        self.assert_accepted()

    def test_the_cross_table_half_of_5_12_is_not_enforced_here(self):
        """Requirement 5.12 counts email rows **plus** call rows per id, and a
        UNIQUE index cannot span two tables. So the same id in both tables is
        storable today. §3.6.4 puts the cross-table half in a trigger that rejects
        a row whose channel disagrees with the reservation, and that is **task
        3.2's**. Recorded as a test so the gap is a known, dated one rather than a
        surprise.
        """
        request_id = str(uuid.uuid4())
        self.raw_insert(outreach_request_id=request_id)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO calls (lead_id, outreach_request_id, attempt_number,
                                       timestamp, outcome, clearance_timestamp)
                    VALUES (%s, %s, 1, %s, 'answered', %s)
                    """,
                    [self.lead.id, request_id, MOMENT, CLEARED_AT],
                )
        self.assertEqual(
            scalar(
                "SELECT count(*) FROM calls WHERE outreach_request_id = %s",
                [request_id],
            ),
            1,
        )


class EmailUnsubscribedColumnTests(EmailMixin, TestCase):
    """Requirements 13.3, 5.8, 10.3: the row-level opt-out flag.

    Task 7.3's event handler is the only writer. That is worth a test because the
    column previously had a reader (Requirement 10.3's unsubscribe-rate numerator)
    and no writer, which made the metric permanently zero while looking
    implemented.
    """

    def test_it_defaults_false_and_is_not_nullable(self):
        email_id = self.raw_insert()
        self.assertIs(
            scalar("SELECT unsubscribed FROM emails WHERE id = %s", [email_id]), False
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.raw_insert(unsubscribed=None)

    def test_it_is_independent_of_the_lead_level_opt_out(self):
        """Requirement 5.23: a Lead-level opt-out with no email row sets this on no
        row at all, so the two are separate facts and neither implies the other."""
        self.lead.unsubscribed_at = MOMENT
        self.lead.save(update_fields=["unsubscribed_at"])
        email_id = self.raw_insert()
        self.assertIs(
            scalar("SELECT unsubscribed FROM emails WHERE id = %s", [email_id]), False
        )

    def test_setting_it_is_what_makes_the_10_3_numerator_countable(self):
        email_id = self.raw_insert()
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE emails SET unsubscribed = true WHERE id = %s", [email_id]
            )
        self.assertEqual(
            scalar("SELECT count(*) FROM emails WHERE unsubscribed"), 1
        )


# ==========================================================================
# calls
# ==========================================================================
class CallColumnShapeTests(CallMixin, TestCase):
    """Requirement 13.4's column list, at its declared types."""

    REQUIRED_COLUMNS = frozenset(
        {
            "id",
            "lead_id",
            "outreach_request_id",
            "attempt_number",
            "timestamp",
            "outcome",
            "clearance_timestamp",
            "late_opt_out_marker",
            "notes",
        }
    )

    def test_every_declared_column_exists_and_nothing_else_does(self):
        self.assertEqual(set(self.columns()), self.REQUIRED_COLUMNS)

    def test_the_nullable_columns_are_exactly_the_three_13_4_permits(self):
        """``outreach_request_id`` and ``clearance_timestamp`` are nullable *only*
        for the Operator-logged row of Requirement 3.5, and ``notes`` is optional.
        Everything else a call row is a record of is required."""
        columns = self.columns()
        for name in (
            "lead_id",
            "attempt_number",
            "timestamp",
            "outcome",
            "late_opt_out_marker",
        ):
            with self.subTest(column=name, expected="NOT NULL"):
                self.assertEqual(columns[name][1], "NO")
        for name in ("outreach_request_id", "clearance_timestamp", "notes"):
            with self.subTest(column=name, expected="NULL"):
                self.assertEqual(columns[name][1], "YES")

    def test_clearance_timestamp_is_timestamptz_at_millisecond_precision(self):
        kind, _, precision = self.columns()["clearance_timestamp"]
        self.assertEqual(kind, "timestamp with time zone")
        self.assertEqual(precision, 3)

    def test_the_call_timestamp_is_timestamptz(self):
        self.assertEqual(self.columns()["timestamp"][0], "timestamp with time zone")

    def test_attempt_number_is_a_smallint(self):
        """A range of 1 to 20 needs nothing wider, and pinning it means a later
        widening of the range has to change the column too."""
        self.assertEqual(self.columns()["attempt_number"][0], "smallint")

    def test_the_marker_default_is_in_the_database(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO calls (lead_id, attempt_number, timestamp, outcome)
                VALUES (%s, 1, %s, 'busy')
                RETURNING late_opt_out_marker
                """,
                [self.lead.id, MOMENT],
            )
            self.assertIs(cursor.fetchone()[0], False)


class CallConstraintTests(CallMixin, TestCase):
    """Requirement 13.4's bounds, at the database."""

    def test_the_named_constraints_are_installed(self):
        self.assertEqual(
            {
                "calls_attempt_number_range",
                "calls_outcome_in_enum",
                "calls_notes_length",
                "calls_clearance_required_with_reservation",
            }
            - self.constraint_names("c"),
            set(),
        )
        self.assertIn("calls_outreach_request_id_unique", self.constraint_names("u"))

    def test_lead_id_is_a_real_foreign_key(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attname, confrelid::regclass::text
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                WHERE c.conrelid = 'calls'::regclass AND c.contype = 'f'
                """
            )
            self.assertEqual(dict(cursor.fetchall()), {"lead_id": "leads"})

    def test_attempt_number_holds_1_through_20(self):
        for value in (1, 20, 7):
            with self.subTest(attempt_number=value):
                self.assert_accepted(attempt_number=value)
        for value in (0, 21, -1):
            with self.subTest(attempt_number=value):
                self.assert_rejected_by(
                    "calls_attempt_number_range", attempt_number=value
                )

    def test_the_attempt_number_range_is_a_storage_ceiling_not_the_view_rule(self):
        """Requirement 3.5 has the Deal_Room_View *assign* attempt_number — 1, then
        one more than the Lead's highest — rather than accept it, and says in terms
        that this is "deliberately narrower in origin" than the storage range of 1
        through 20. So a row at attempt 15 with no rows below it is storable, and
        that is not a bug in either place. Task 9.2 owns the assignment."""
        self.assert_accepted(attempt_number=15)

    def test_the_three_outcomes_are_storable_and_a_fourth_is_not(self):
        self.assertEqual(CallOutcome.values, ["answered", "busy", "no-answer"])
        for outcome in CallOutcome.values:
            with self.subTest(outcome=outcome):
                self.assert_accepted(outcome=outcome)
        for bogus in ("no_answer", "No-Answer", "voicemail", "", "answered "):
            with self.subTest(outcome=bogus):
                self.assert_rejected_by("calls_outcome_in_enum", outcome=bogus)

    def test_outcome_is_required(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.raw_insert(outcome=None)

    def test_notes_holds_up_to_5000_characters_or_is_unset(self):
        for value in (None, "", "n", "n" * 5000):
            with self.subTest(length=None if value is None else len(value)):
                self.assert_accepted(notes=value)
        self.assert_rejected_by("calls_notes_length", notes="n" * 5001)

    def test_the_notes_ceiling_is_deliberately_wider_than_the_view_limit(self):
        """Requirement 13.4 stores up to 5,000; Requirement 3.5 accepts up to
        2,000 and Requirement 3.9 rejects more. A 3,000-character row is therefore
        storable and not submittable, which 3.5 states as the intent. Task 9.2 owns
        the 2,000-character form rule and must not narrow this column to match."""
        self.assert_accepted(notes="n" * 3000)

    def test_notes_permits_the_empty_string_unlike_the_1_to_200_lead_columns(self):
        """Requirement 13.4 writes "up to 5,000 characters" with no lower bound,
        so ``calls_notes_length`` is a ceiling only — the same shape as
        ``leads_website_url_length`` and unlike ``leads_industry_length``, whose
        lower bound is what makes NULL the single unset form there."""
        call_id = self.assert_accepted(notes="")
        self.assertEqual(scalar("SELECT notes FROM calls WHERE id = %s", [call_id]), "")


class CallClearanceCheckTests(CallMixin, TestCase):
    """Requirements 13.4, 5.18, 3.5: the one row allowed to carry no clearance.

    ``CHECK (outreach_request_id IS NULL OR clearance_timestamp IS NOT NULL)``.
    All four combinations are exercised, because the constraint's whole content is
    which of them it forbids.
    """

    def test_a_reserved_call_with_its_clearance_is_accepted(self):
        self.assert_accepted(
            outreach_request_id=str(uuid.uuid4()), clearance_timestamp=CLEARED_AT
        )

    def test_an_operator_logged_call_with_neither_is_accepted(self):
        """Requirement 3.5's direct-entry path: no adapter submission, so no
        reservation, so nothing to copy."""
        call_id = self.assert_accepted(
            outreach_request_id=None, clearance_timestamp=None
        )
        self.assertTrue(Call.objects.get(pk=call_id).was_operator_logged)

    def test_a_reserved_call_with_no_clearance_is_the_forbidden_combination(self):
        self.assert_rejected_by(
            "calls_clearance_required_with_reservation",
            outreach_request_id=str(uuid.uuid4()),
            clearance_timestamp=None,
        )

    def test_omitting_the_clearance_on_a_reserved_call_fails_the_same_way(self):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO calls (lead_id, outreach_request_id,
                                           attempt_number, timestamp, outcome)
                        VALUES (%s, %s, 1, %s, 'answered')
                        """,
                        [self.lead.id, str(uuid.uuid4()), MOMENT],
                    )
        self.assertIn(
            "calls_clearance_required_with_reservation", str(caught.exception)
        )

    def test_a_clearance_without_a_reservation_stays_storable(self):
        """The check is one-way on purpose: 13.4 forbids a reservation without a
        clearance and does not forbid the converse. An Operator-logged call for
        which the Compliance_Guard happened to record an evaluation instant is not
        a compliance problem; a reserved call whose clearance was lost is, because
        task 3.2's trigger would have nothing to compare and a NULL comparison
        passes."""
        self.assert_accepted(outreach_request_id=None, clearance_timestamp=CLEARED_AT)

    def test_the_check_also_holds_against_an_update(self):
        """A row cannot be walked into the forbidden shape after the fact."""
        call_id = self.raw_insert()
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE calls SET clearance_timestamp = NULL WHERE id = %s",
                        [call_id],
                    )
        self.assertIn(
            "calls_clearance_required_with_reservation", str(caught.exception)
        )

    def test_dropping_the_reservation_and_the_clearance_together_is_permitted(self):
        call_id = self.raw_insert()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE calls SET outreach_request_id = NULL, "
                    "clearance_timestamp = NULL WHERE id = %s",
                    [call_id],
                )
        self.assertTrue(Call.objects.get(pk=call_id).was_operator_logged)


class CallIdempotencyTests(CallMixin, TestCase):
    """Requirements 5.9, 5.10, 5.12: one call row per outreach_request_id."""

    def test_a_second_row_for_the_same_request_id_is_rejected(self):
        request_id = str(uuid.uuid4())
        self.raw_insert(outreach_request_id=request_id)
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.raw_insert(outreach_request_id=request_id, attempt_number=2)
        self.assertIn("calls_outreach_request_id_unique", str(caught.exception))

    def test_many_operator_logged_calls_coexist_because_nulls_are_distinct(self):
        """PostgreSQL treats NULLs as distinct in a UNIQUE index, which is exactly
        what Requirement 13.4 needs: the uniqueness constrains reserved rows and
        leaves any number of Requirement 3.5 rows storable."""
        for attempt in (1, 2, 3):
            self.assert_accepted(
                outreach_request_id=None,
                clearance_timestamp=None,
                attempt_number=attempt,
            )
        self.assertEqual(
            scalar("SELECT count(*) FROM calls WHERE outreach_request_id IS NULL"), 3
        )


class CallLateOptOutTests(CallMixin, TestCase):
    """Requirement 5.22: a late do-not-call marks the row rather than losing it."""

    def test_the_marker_is_storable_alongside_a_clearance_before_the_opt_out(self):
        do_not_call_at = dt.datetime(2026, 3, 1, 11, 59, 59, tzinfo=dt.timezone.utc)
        self.lead.do_not_call_at = do_not_call_at
        self.lead.save(update_fields=["do_not_call_at"])
        call_id = self.assert_accepted(late_opt_out_marker=True)
        call = Call.objects.get(pk=call_id)
        self.assertTrue(call.late_opt_out_marker)
        self.assertLess(call.clearance_timestamp, do_not_call_at)

    def test_the_marker_is_not_nullable(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.raw_insert(late_opt_out_marker=None)

    def test_outcome_enum_reads_the_stored_value(self):
        call = Call.objects.create(
            lead=self.lead,
            attempt_number=1,
            timestamp=MOMENT,
            outcome=CallOutcome.NO_ANSWER,
        )
        call.refresh_from_db()
        self.assertIs(call.outcome_enum, CallOutcome.NO_ANSWER)
        self.assertEqual(call.outcome, "no-answer")
