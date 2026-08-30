"""Tests for the ``leads`` table (Requirements 13.1, 13.6, 13.7, 13.11).

**Every claim about a stored invariant is driven through raw SQL, not the ORM.**
That is not belt-and-braces; it is the only way these tests test the thing the
requirement states. ``leads`` is a shared-schema table (design §4.2): the future
bot writes the discovery columns over its own connection and never executes a
line of this codebase. A test that only exercised ``Lead.objects.create`` would
pass identically against a model whose bounds lived in ``choices`` and form
validation — which is to say, against a schema with no constraints in it at all.
So each ``CHECK`` is attacked with an ``INSERT`` that bypasses Python entirely.

Scope: task 2.1's own constraints. Task 2.5 owns Property 41 (the exhaustive
just-inside/just-outside sweep across every constrained column of §4.3 from a
declarative table) and Property 42 (referential integrity); these are the direct
tests that prove this task's schema does what it claims, not that sweep.
"""

from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, connection, transaction
from django.db.utils import DataError, InternalError, ProgrammingError
from django.test import TestCase

from dashboard.models import Lead, PipelineState

# A Lead that satisfies every constraint, as raw column values. Tests copy it and
# push exactly one column out of bounds, so a failure names one column.
VALID = {
    "company_name": "Acme Roofing",
    "industry": "Construction",
    "website_url": "https://acme-roofing.example",
    "owner": "Dana Okonkwo",
    "researched_score": 4,
    "preferred_price": 750,
    "contact_name": "Dana Okonkwo",
    "contact_email": "dana@acme-roofing.example",
    "contact_phone": "+1 (555) 010-2030",
    "status": PipelineState.NEW_LEAD.value,
    "state_version": 0,
    "website_condition": 3,
    "urgency": 2,
    "estimated_page_count": 8,
    "timezone": "America/New_York",
    "region": "Northeast",
    "manual_review_flag": False,
    "last_activity_at": dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc),
}


def raw_insert(**overrides) -> int:
    """``INSERT`` one Lead over the raw connection, returning its id.

    Deliberately omits the two generated columns and lets ``created_at`` take its
    database default, so this function exercises the same statement shape the
    future bot's own connection would issue.
    """
    values = {**VALID, **overrides}
    columns = ", ".join(f'"{name}"' for name in values)
    placeholders = ", ".join(["%s"] * len(values))
    with connection.cursor() as cursor:
        cursor.execute(
            f'INSERT INTO leads ({columns}) VALUES ({placeholders}) RETURNING id',
            list(values.values()),
        )
        return cursor.fetchone()[0]


def scalar(sql: str, params: list | None = None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchone()[0]


def row(sql: str, params: list | None = None):
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.fetchone()


class ColumnShapeTests(TestCase):
    """Requirements 13.1 and 13.11: the declared columns, at the declared types."""

    # Requirement 13.1's column list, as the requirement writes it, plus the two
    # generated columns of §3.6.5. Compared as a set against the catalog, so a
    # column silently dropped from the model fails here rather than in whatever
    # later task first needed it.
    REQUIRED_COLUMNS = frozenset(
        {
            "id",
            "company_name",
            "industry",
            "website_url",
            "contact_name",
            "contact_email",
            "contact_phone",
            "owner",
            "researched_score",
            "preferred_price",
            "website_condition",
            "urgency",
            "estimated_page_count",
            "status",
            "state_version",
            "timezone",
            "region",
            "unsubscribed_at",
            "do_not_call_at",
            "manual_review_flag",
            "last_activity_at",
            "created_at",
            # design §3.6.5
            "email_normalized",
            "phone_digits",
        }
    )

    def _columns(self) -> dict[str, tuple[str, str]]:
        """``{column: (data_type, is_nullable)}`` from ``information_schema``."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'leads'
                """
            )
            return {name: (kind, nullable) for name, kind, nullable in cursor.fetchall()}

    def test_every_declared_column_exists_and_nothing_else_does(self):
        self.assertEqual(set(self._columns()), self.REQUIRED_COLUMNS)

    def test_every_timestamp_column_is_timestamptz(self):
        """Requirement 13.11: UTC, one second or finer. ``timestamptz`` is µs."""
        columns = self._columns()
        for name in (
            "unsubscribed_at",
            "do_not_call_at",
            "last_activity_at",
            "created_at",
        ):
            with self.subTest(column=name):
                self.assertEqual(columns[name][0], "timestamp with time zone")

    def test_pg_typeof_confirms_bigint_id_and_timestamptz_timestamps(self):
        """The catalog can be read; ``pg_typeof`` reads the *stored value*."""
        lead_id = raw_insert(unsubscribed_at=VALID["last_activity_at"])
        types = row(
            """
            SELECT pg_typeof(id)::text,
                   pg_typeof(last_activity_at)::text,
                   pg_typeof(created_at)::text,
                   pg_typeof(unsubscribed_at)::text,
                   pg_typeof(state_version)::text,
                   pg_typeof(researched_score)::text,
                   pg_typeof(email_normalized)::text,
                   pg_typeof(phone_digits)::text
            FROM leads WHERE id = %s
            """,
            [lead_id],
        )
        self.assertEqual(
            types,
            (
                "bigint",
                "timestamp with time zone",
                "timestamp with time zone",
                "timestamp with time zone",
                "integer",
                "smallint",
                "text",
                "text",
            ),
        )

    def test_the_nullable_columns_are_nullable_and_the_required_ones_are_not(self):
        """Requirement 13.1: company_name, last_activity_at, created_at required.

        ``status``, ``state_version``, ``researched_score`` and
        ``manual_review_flag`` are additionally NOT NULL — see the model
        docstrings for why each is read as required.
        """
        columns = self._columns()
        for name in (
            "company_name",
            "last_activity_at",
            "created_at",
            "status",
            "state_version",
            "researched_score",
            "manual_review_flag",
        ):
            with self.subTest(column=name, expected="NOT NULL"):
                self.assertEqual(columns[name][1], "NO")
        for name in (
            "industry",
            "website_url",
            "owner",
            "contact_name",
            "contact_email",
            "contact_phone",
            "preferred_price",
            "website_condition",
            "urgency",
            "estimated_page_count",
            "timezone",
            "region",
            "unsubscribed_at",
            "do_not_call_at",
        ):
            with self.subTest(column=name, expected="NULL"):
                self.assertEqual(columns[name][1], "YES")


class ConstraintInventoryTests(TestCase):
    """The fifteen ``CHECK``s of §4.3 exist, by name, in ``pg_constraint``."""

    EXPECTED = frozenset(
        {
            "leads_company_name_length",
            "leads_industry_length",
            "leads_contact_name_length",
            "leads_website_url_length",
            "leads_contact_email_length",
            "leads_contact_phone_length",
            "leads_researched_score_range",
            "leads_preferred_price_range",
            "leads_website_condition_range",
            "leads_urgency_range",
            "leads_estimated_page_count_range",
            "leads_state_version_non_negative",
            "leads_timezone_length",
            "leads_region_length",
            "leads_status_in_enum",
        }
    )

    def test_every_named_check_is_installed(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'leads'::regclass AND contype = 'c'
                  AND conname LIKE 'leads_%'
                """
            )
            installed = {name for (name,) in cursor.fetchall()}
        self.assertEqual(self.EXPECTED - installed, set(), "missing CHECK constraints")


class DatabaseLevelCheckTests(TestCase):
    """Every §4.3 bound rejects an out-of-range value at the *database*.

    Each case is one raw ``INSERT``, so nothing in Python has a chance to
    intervene. The assertion names the constraint that fired, which is what
    Requirement 13.8 needs to be able to report.
    """

    def assert_rejected_by(self, constraint: str, **overrides):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                raw_insert(**overrides)
        self.assertIn(constraint, str(caught.exception))

    def assert_accepted(self, **overrides):
        with transaction.atomic():
            raw_insert(**overrides)

    # --- Requirement 13.6's numeric ranges, at both boundaries ------------

    def test_researched_score_accepts_1_through_5_and_rejects_0_and_6(self):
        for score in (1, 3, 5):
            with self.subTest(researched_score=score):
                self.assert_accepted(researched_score=score)
        for score in (0, 6, -1):
            with self.subTest(researched_score=score):
                self.assert_rejected_by(
                    "leads_researched_score_range", researched_score=score
                )

    def test_researched_score_is_not_nullable(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                raw_insert(researched_score=None)

    def test_preferred_price_accepts_null_and_550_to_1000(self):
        for price in (None, 550, 1000, 800):
            with self.subTest(preferred_price=price):
                self.assert_accepted(preferred_price=price)
        for price in (549, 1001, 0, -1):
            with self.subTest(preferred_price=price):
                self.assert_rejected_by(
                    "leads_preferred_price_range", preferred_price=price
                )

    def test_website_condition_accepts_null_and_1_to_5(self):
        for value in (None, 1, 5):
            with self.subTest(website_condition=value):
                self.assert_accepted(website_condition=value)
        for value in (0, 6):
            with self.subTest(website_condition=value):
                self.assert_rejected_by(
                    "leads_website_condition_range", website_condition=value
                )

    def test_urgency_accepts_null_and_1_to_5(self):
        for value in (None, 1, 5):
            with self.subTest(urgency=value):
                self.assert_accepted(urgency=value)
        for value in (0, 6):
            with self.subTest(urgency=value):
                self.assert_rejected_by("leads_urgency_range", urgency=value)

    def test_estimated_page_count_accepts_null_and_0_to_200(self):
        for value in (None, 0, 200):
            with self.subTest(estimated_page_count=value):
                self.assert_accepted(estimated_page_count=value)
        for value in (-1, 201):
            with self.subTest(estimated_page_count=value):
                self.assert_rejected_by(
                    "leads_estimated_page_count_range", estimated_page_count=value
                )

    def test_state_version_is_non_negative_and_defaults_to_zero(self):
        for value in (0, 1, 10_000):
            with self.subTest(state_version=value):
                self.assert_accepted(state_version=value)
        self.assert_rejected_by("leads_state_version_non_negative", state_version=-1)

    def test_state_version_default_is_in_the_database_not_only_the_model(self):
        """Requirement 13.6's ``DEFAULT 0``, proved on a connection Python's
        default never touches: the column is simply omitted from the INSERT."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO leads (company_name, researched_score, status,
                                   last_activity_at)
                VALUES ('Defaults Inc', 3, 'New_Lead', %s)
                RETURNING state_version, manual_review_flag
                """,
                [VALID["last_activity_at"]],
            )
            state_version, manual_review_flag = cursor.fetchone()
        self.assertEqual(state_version, 0)
        self.assertIs(manual_review_flag, False)

    # --- Requirement 13.1's length bounds --------------------------------

    def test_company_name_holds_1_to_200_characters_and_is_required(self):
        self.assert_accepted(company_name="A")
        self.assert_accepted(company_name="x" * 200)
        self.assert_rejected_by("leads_company_name_length", company_name="")
        self.assert_rejected_by("leads_company_name_length", company_name="x" * 201)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                raw_insert(company_name=None)

    def test_industry_and_contact_name_hold_1_to_200_or_null(self):
        for column, constraint in (
            ("industry", "leads_industry_length"),
            ("contact_name", "leads_contact_name_length"),
        ):
            with self.subTest(column=column):
                self.assert_accepted(**{column: None})
                self.assert_accepted(**{column: "y"})
                self.assert_accepted(**{column: "y" * 200})
                # The empty string is rejected by the lower bound, which is what
                # makes NULL the single representation of unset.
                self.assert_rejected_by(constraint, **{column: ""})
                self.assert_rejected_by(constraint, **{column: "y" * 201})

    def test_the_ceiling_only_columns_reject_one_character_over(self):
        for column, constraint, ceiling in (
            ("website_url", "leads_website_url_length", 2048),
            ("contact_email", "leads_contact_email_length", 320),
            ("contact_phone", "leads_contact_phone_length", 32),
            ("timezone", "leads_timezone_length", 64),
            ("region", "leads_region_length", 200),
        ):
            with self.subTest(column=column, ceiling=ceiling):
                self.assert_accepted(**{column: None})
                self.assert_accepted(**{column: "z" * ceiling})
                self.assert_rejected_by(constraint, **{column: "z" * (ceiling + 1)})

    def test_length_bounds_count_characters_not_bytes(self):
        """``char_length`` semantics: a 32-character multi-byte phone fits.

        Worth pinning because the obvious alternative implementation —
        ``octet_length`` — would reject this row, and the requirement says
        characters.
        """
        self.assert_accepted(contact_phone="＋" * 32)
        self.assert_rejected_by("leads_contact_phone_length", contact_phone="＋" * 33)

    # --- Requirement 13.7's closed value set -----------------------------

    def test_every_one_of_the_eleven_pipeline_states_is_storable(self):
        self.assertEqual(len(PipelineState.values), 11)
        for state in PipelineState.values:
            with self.subTest(status=state):
                self.assert_accepted(status=state)

    def test_a_twelfth_state_is_unstorable(self):
        for bogus in ("Refunded", "new_lead", "NEW_LEAD", "", "Won "):
            with self.subTest(status=bogus):
                self.assert_rejected_by("leads_status_in_enum", status=bogus)

    def test_status_has_no_database_default(self):
        """Deliberate: a Lead's status is written with its genesis history row
        (Requirements 4.12, 13.13), never conjured by a column default."""
        default = scalar(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'leads' AND column_name = 'status'
            """
        )
        self.assertIsNone(default)


class LastActivityAtTests(TestCase):
    """Requirements 13.1/13.14: required, and bootstrapped by the caller.

    See the module docstring of ``dashboard.models.lead`` for the reasoning. What
    these tests pin is the pair of decisions task 8.2 depends on: the column
    cannot hold NULL, and it has no default that could satisfy 13.14's
    creation-time equality without task 8.2 having copied anything.
    """

    def test_last_activity_at_rejects_null_at_the_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                raw_insert(last_activity_at=None)

    def test_omitting_last_activity_at_entirely_also_fails(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO leads (company_name, researched_score, status)
                        VALUES ('No Activity Ltd', 3, 'New_Lead')
                        """
                    )

    def test_last_activity_at_has_no_database_default(self):
        """The guarantee task 8.2 rests on.

        With a default, an assertion that ``last_activity_at`` equals the genesis
        row's ``occurred_at`` would hold whether or not task 8.2 ever copied it.
        Without one, that assertion can only pass because something copied it.
        """
        default = scalar(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'leads' AND column_name = 'last_activity_at'
            """
        )
        self.assertIsNone(default)

    def test_created_at_by_contrast_does_default(self):
        """``created_at``'s default records a fact with no other source; the
        contrast with ``last_activity_at`` is the point."""
        default = scalar(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_name = 'leads' AND column_name = 'created_at'
            """
        )
        self.assertIsNotNone(default)

    def test_a_supplied_value_round_trips_unchanged_in_utc(self):
        moment = dt.datetime(2026, 3, 1, 12, 0, 0, 123456, tzinfo=dt.timezone.utc)
        lead = Lead.objects.create(
            company_name="Round Trip Co",
            researched_score=3,
            status=PipelineState.NEW_LEAD,
            last_activity_at=moment,
        )
        lead.refresh_from_db()
        self.assertEqual(lead.last_activity_at, moment)
        # Sub-second precision survives, which is Requirement 13.11's floor.
        self.assertEqual(lead.last_activity_at.microsecond, 123456)


class GeneratedColumnTests(TestCase):
    """Design §3.6.5: normalization is defined once, in the database."""

    def normalized(self, **overrides) -> tuple[str | None, str]:
        lead_id = raw_insert(**overrides)
        return row(
            "SELECT email_normalized, phone_digits FROM leads WHERE id = %s",
            [lead_id],
        )

    def test_email_is_lowercased_and_trimmed(self):
        email, _ = self.normalized(contact_email="   Dana.OKonkwo@Acme-Roofing.EXAMPLE  ")
        self.assertEqual(email, "dana.okonkwo@acme-roofing.example")

    def test_btrim_removes_spaces_only_not_tabs_or_newlines(self):
        """A real limit of §3.6.5's expression, pinned rather than papered over.

        One-argument ``btrim`` strips ASCII space (U+0020) and nothing else, so a
        tab- or newline-padded address does **not** normalize to the same value as
        a space-padded one and Requirement 5.7 will not see the two as duplicates.
        §3.6.5 fixes the SQL and this task reproduces it verbatim, so the correct
        move is to record the behaviour here: whoever finds a missed duplicate
        with a tab in the address should find this test rather than a surprise.
        Widening it means widening §3.6.5 — a design change, and one that would
        also have to rebuild the ``idx_leads_email_norm`` index of task 2.4.
        """
        padded, _ = self.normalized(contact_email="\tsam@example.com\t")
        plain, _ = self.normalized(contact_email="sam@example.com")
        self.assertEqual(padded, "\tsam@example.com\t")
        self.assertNotEqual(padded, plain)

    def test_email_normalization_folds_case_only_after_trimming(self):
        """Requirement 5.7's duplicate rule, as an equality the index can use."""
        first, _ = self.normalized(contact_email="Sam@Example.com")
        second, _ = self.normalized(contact_email="   sam@EXAMPLE.com  ")
        self.assertEqual(first, second)

    def test_email_normalized_is_null_for_a_null_email(self):
        email, _ = self.normalized(contact_email=None)
        self.assertIsNone(email)

    def test_phone_keeps_digits_and_drops_punctuation_and_letters(self):
        _, digits = self.normalized(contact_phone="+1 (555) 010-2030 ext. x9")
        self.assertEqual(digits, "15550102030" + "9")

    def test_phone_digits_of_a_null_phone_is_the_empty_string_not_null(self):
        """§3.6.5's ``coalesce`` makes this the empty string, and callers must
        know: an equality query over this column matches every phoneless Lead to
        every other. The expression is the design's; the *query* is where that is
        excluded."""
        _, digits = self.normalized(contact_phone=None)
        self.assertEqual(digits, "")
        self.assertIsNotNone(digits)

    def test_phone_digits_of_a_digitless_phone_is_also_the_empty_string(self):
        _, digits = self.normalized(contact_phone="ext. unknown")
        self.assertEqual(digits, "")

    def test_unicode_digits_are_not_treated_as_digits(self):
        r"""``\D`` is ASCII-class here, so a full-width digit is stripped.

        Pinned rather than asserted as desirable: it is what §3.6.5's expression
        does, and a later duplicate-detection bug report should find the
        behaviour recorded instead of surprising.
        """
        _, digits = self.normalized(contact_phone="５5")
        self.assertEqual(digits, "5")

    def test_both_columns_are_recomputed_when_the_source_column_changes(self):
        lead_id = raw_insert(contact_email="Old@Example.com", contact_phone="111")
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE leads SET contact_email = %s, contact_phone = %s WHERE id = %s",
                ["  NEW@Example.COM ", "(222) 333", lead_id],
            )
        email, digits = row(
            "SELECT email_normalized, phone_digits FROM leads WHERE id = %s", [lead_id]
        )
        self.assertEqual(email, "new@example.com")
        self.assertEqual(digits, "222333")

    def test_both_columns_are_stored_not_virtual(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT attname, attgenerated FROM pg_attribute
                WHERE attrelid = 'leads'::regclass
                  AND attname IN ('email_normalized', 'phone_digits')
                ORDER BY attname
                """
            )
            self.assertEqual(
                cursor.fetchall(),
                [("email_normalized", "s"), ("phone_digits", "s")],
            )

    def test_the_stored_expressions_are_exactly_the_ones_design_3_6_5_declares(self):
        """The single-definition claim, checked against the catalog.

        ``pg_get_expr`` renders the parsed expression, so this compares what
        PostgreSQL will actually evaluate — not the text of the migration.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attname, pg_get_expr(d.adbin, d.adrelid)
                FROM pg_attribute a
                JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE a.attrelid = 'leads'::regclass AND a.attgenerated = 's'
                """
            )
            expressions = dict(cursor.fetchall())
        self.assertEqual(expressions["email_normalized"], "lower(btrim(contact_email))")
        self.assertEqual(
            expressions["phone_digits"],
            "regexp_replace(COALESCE(contact_phone, ''::text), '\\D'::text, ''::text, 'g'::text)",
        )

    def test_a_generated_column_cannot_be_written_directly(self):
        lead_id = raw_insert()
        for column in ("email_normalized", "phone_digits"):
            with self.subTest(column=column, statement="UPDATE"):
                with self.assertRaises(
                    (ProgrammingError, InternalError, DataError, IntegrityError)
                ):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"UPDATE leads SET {column} = 'forged' WHERE id = %s",
                                [lead_id],
                            )

    def test_a_generated_column_cannot_be_supplied_at_insert(self):
        with self.assertRaises(
            (ProgrammingError, InternalError, DataError, IntegrityError)
        ):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO leads (company_name, researched_score, status,
                                           last_activity_at, contact_email,
                                           email_normalized)
                        VALUES ('Forger Ltd', 3, 'New_Lead', %s, 'A@b.com', 'not-it')
                        """,
                        [VALID["last_activity_at"]],
                    )

    def test_the_orm_never_sends_the_generated_columns(self):
        """A Django-side restatement of the same guarantee: ``save()`` on a Lead
        whose in-memory generated value is wrong must not write it."""
        lead = Lead.objects.create(
            company_name="ORM Co",
            researched_score=3,
            status=PipelineState.NEW_LEAD,
            last_activity_at=VALID["last_activity_at"],
            contact_email=" MiXeD@Case.COM ",
            contact_phone="555-1234",
        )
        lead.refresh_from_db()
        self.assertEqual(lead.email_normalized, "mixed@case.com")
        self.assertEqual(lead.phone_digits, "5551234")


class OptOutSemanticsTests(TestCase):
    """Requirement 13.1: NULL means not opted out, and it is the only such form."""

    def _lead(self, **overrides) -> Lead:
        return Lead.objects.create(
            company_name="Opt Out Co",
            researched_score=3,
            status=PipelineState.NEW_LEAD,
            last_activity_at=VALID["last_activity_at"],
            **overrides,
        )

    def test_a_fresh_lead_is_neither_unsubscribed_nor_do_not_call(self):
        lead = self._lead()
        self.assertIsNone(lead.unsubscribed_at)
        self.assertIsNone(lead.do_not_call_at)
        self.assertFalse(lead.is_unsubscribed)
        self.assertFalse(lead.is_do_not_call)

    def test_setting_the_timestamp_is_exactly_the_opted_out_condition(self):
        moment = dt.datetime(2026, 4, 1, 9, 30, tzinfo=dt.timezone.utc)
        lead = self._lead(unsubscribed_at=moment, do_not_call_at=moment)
        self.assertTrue(lead.is_unsubscribed)
        self.assertTrue(lead.is_do_not_call)
        # The instant itself is retained, which is what the task 3.2 compliance
        # triggers compare a Clearance_Timestamp against.
        lead.refresh_from_db()
        self.assertEqual(lead.unsubscribed_at, moment)
        self.assertEqual(lead.do_not_call_at, moment)

    def test_manual_review_flag_defaults_false_and_is_not_nullable(self):
        self.assertIs(self._lead().manual_review_flag, False)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                raw_insert(manual_review_flag=None)


class PipelineStateValueSetTests(TestCase):
    """Requirement 13.7's value set — and nothing about transition legality."""

    def test_the_eleven_values_are_the_requirements_own_spellings(self):
        self.assertEqual(
            PipelineState.values,
            [
                "New_Lead",
                "Contacted",
                "Replied",
                "Scheduled",
                "Quoted",
                "Won",
                "Invoiced",
                "Paid_Pending_Verification",
                "Payment_Verified",
                "Released",
                "Closed_Lost",
            ],
        )

    def test_this_module_declares_no_transition_table(self):
        """Task 6.1 owns ``LEGAL_TRANSITIONS`` and ``TERMINAL_STATES``, and the
        §7.6 CI step keys its import-time assertion check on their *definition*.
        Declaring either here would make that step demand assertions this task
        must not write."""
        import dashboard.models.lead as lead_module

        self.assertFalse(hasattr(lead_module, "LEGAL_TRANSITIONS"))
        self.assertFalse(hasattr(lead_module, "TERMINAL_STATES"))

    def test_status_enum_reads_the_stored_value(self):
        lead = Lead.objects.create(
            company_name="Enum Co",
            researched_score=3,
            status=PipelineState.WON,
            last_activity_at=VALID["last_activity_at"],
        )
        lead.refresh_from_db()
        self.assertIs(lead.status_enum, PipelineState.WON)


class NoIndexOrTriggerYetTests(TestCase):
    """Scope discipline, asserted rather than trusted.

    Tasks 2.4 and 3.x own the ``leads`` indexes and triggers. If this task had
    quietly created one, the task that owns it would find its work half-done and
    ``make check-activations`` would start demanding task 3.5's marker.
    """

    def test_only_the_primary_key_index_exists(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'leads'")
            self.assertEqual({name for (name,) in cursor.fetchall()}, {"leads_pkey"})

    def test_no_trigger_exists_on_leads(self):
        count = scalar(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'leads'::regclass "
            "AND NOT tgisinternal"
        )
        self.assertEqual(count, 0)
