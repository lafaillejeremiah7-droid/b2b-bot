"""Tests for the ``deals`` table (Requirements 13.2, 13.6, 13.12, 8.5, 8.17, 8.21).

**Every claim about a stored invariant is driven through raw SQL, not the ORM**,
for the reason ``test_lead_model`` gives at length: ``deals`` is part of a shared
schema (design §4.2), a test that only exercised ``Deal.objects.create`` would
pass identically against a model whose bounds lived in form validation, and the
compliance and release guarantees of Requirement 8 are stated over *stored
records*. So each constraint is attacked with an ``INSERT`` that bypasses Python
entirely.

Scope: task 2.2's own constraints. Task 2.5 owns Property 41 (the exhaustive
just-inside/just-outside sweep across every constrained column of §4.3 from a
declarative table) and Property 42 (referential integrity plus concurrent Deal
creation on separate connections).
"""

from __future__ import annotations

import datetime as dt

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from dashboard.models import Deal, Lead, Operator, PipelineState

MOMENT = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)


def make_lead(name: str = "Acme Roofing") -> Lead:
    """A Lead for a Deal to hang off. Lead creation is task 2.1's subject."""
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


class DealRawInsertMixin:
    """A valid Deal as raw column values, plus one-column-out-of-bounds inserts."""

    def setUp(self):
        super().setUp()
        self.lead = make_lead()

    def valid(self) -> dict:
        return {
            "lead_id": self.lead.id,
            "agreed_price": 800,
            "payment_anomaly_flag": False,
        }

    def raw_insert(self, **overrides) -> int:
        values = {**self.valid(), **overrides}
        columns = ", ".join(f'"{name}"' for name in values)
        placeholders = ", ".join(["%s"] * len(values))
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO deals ({columns}) VALUES ({placeholders}) "
                f"RETURNING deal_id",
                list(values.values()),
            )
            return cursor.fetchone()[0]

    def assert_rejected_by(self, constraint: str, **overrides):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.raw_insert(**overrides)
        self.assertIn(constraint, str(caught.exception))

    def assert_accepted(self, **overrides) -> int:
        """Insert a Deal expected to be storable, on a Lead of its own.

        The fresh Lead is not incidental: ``deals.lead_id`` is UNIQUE
        (Requirement 13.12), so a loop over accepted values would otherwise fail
        its second iteration on the uniqueness rather than on the bound under
        test. Pass ``lead_id`` explicitly to aim at a specific Lead.
        """
        if "lead_id" not in overrides:
            overrides["lead_id"] = make_lead(f"Accepted {self._accepted_count()}").id
        with transaction.atomic():
            return self.raw_insert(**overrides)

    def _accepted_count(self) -> int:
        self._accepted = getattr(self, "_accepted", 0) + 1
        return self._accepted


class ColumnShapeTests(DealRawInsertMixin, TestCase):
    """Requirement 13.2's column list, at the types design §4.1 declares."""

    REQUIRED_COLUMNS = frozenset(
        {
            "deal_id",
            "lead_id",
            "agreed_price",
            "quote_sent_date",
            "invoice_id",
            "payment_received",
            "paid_date",
            "payment_verified_at",
            "verified_by_operator_id",
            "delivery_sent",
            "delivered_date",
            "payment_anomaly_flag",
            "payment_anomaly_reason",
        }
    )

    def _columns(self) -> dict[str, tuple[str, str, int | None]]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable, datetime_precision
                FROM information_schema.columns
                WHERE table_name = 'deals'
                """
            )
            return {
                name: (kind, nullable, precision)
                for name, kind, nullable, precision in cursor.fetchall()
            }

    def test_every_declared_column_exists_and_nothing_else_does(self):
        self.assertEqual(set(self._columns()), self.REQUIRED_COLUMNS)

    def test_only_lead_id_and_the_anomaly_flag_are_not_null(self):
        """Requirement 13.2: every other column is 'unset until' its action."""
        columns = self._columns()
        for name in ("deal_id", "lead_id", "payment_anomaly_flag"):
            with self.subTest(column=name, expected="NOT NULL"):
                self.assertEqual(columns[name][1], "NO")
        for name in (
            "agreed_price",
            "quote_sent_date",
            "invoice_id",
            "payment_received",
            "paid_date",
            "payment_verified_at",
            "verified_by_operator_id",
            "delivery_sent",
            "delivered_date",
            "payment_anomaly_reason",
        ):
            with self.subTest(column=name, expected="NULL"):
                self.assertEqual(columns[name][1], "YES")

    def test_payment_verified_at_is_timestamptz_at_millisecond_precision(self):
        """Requirements 8.5, 8.8 and §4.3: ``TIMESTAMPTZ(3)``, not the default."""
        kind, _, precision = self._columns()["payment_verified_at"]
        self.assertEqual(kind, "timestamp with time zone")
        self.assertEqual(precision, 3)

    def test_the_other_timestamp_columns_are_plain_timestamptz(self):
        """Requirement 13.11's floor. The contrast with the column above is the
        design's: §4.3 asks for millisecond precision only where a requirement
        does."""
        columns = self._columns()
        for name in ("quote_sent_date", "delivered_date"):
            with self.subTest(column=name):
                self.assertEqual(columns[name][0], "timestamp with time zone")
                self.assertEqual(columns[name][2], 6)

    def test_paid_date_is_a_date_as_design_4_1_declares(self):
        """§4.1 writes ``date paid_date`` beside two ``timestamptz`` columns, so
        Requirement 13.11's UTC-timestamp rule does not reach it. The payment
        *instant* lives on the ``payments`` record (task 2.3)."""
        self.assertEqual(self._columns()["paid_date"][0], "date")

    def test_pg_typeof_confirms_the_stored_value_types(self):
        deal_id = self.raw_insert(
            payment_verified_at=MOMENT,
            paid_date=dt.date(2026, 3, 2),
            invoice_id=41,
            payment_received=True,
        )
        self.assertEqual(
            row(
                """
                SELECT pg_typeof(deal_id)::text,
                       pg_typeof(lead_id)::text,
                       pg_typeof(agreed_price)::text,
                       pg_typeof(payment_verified_at)::text,
                       pg_typeof(paid_date)::text,
                       pg_typeof(invoice_id)::text,
                       pg_typeof(payment_anomaly_flag)::text
                FROM deals WHERE deal_id = %s
                """,
                [deal_id],
            ),
            (
                "bigint",
                "bigint",
                "integer",
                "timestamp with time zone",
                "date",
                "bigint",
                "boolean",
            ),
        )

    def test_the_anomaly_flag_default_is_in_the_database_not_only_the_model(self):
        """Requirement 13.6's ``DEFAULT false``, proved on a connection Python's
        default never touches: the column is omitted from the INSERT."""
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO deals (lead_id) VALUES (%s) "
                "RETURNING payment_anomaly_flag",
                [self.lead.id],
            )
            self.assertIs(cursor.fetchone()[0], False)


class ConstraintInventoryTests(TestCase):
    """The §4.3 constraints exist, by name, in ``pg_constraint``."""

    def _names(self, contype: str) -> set[str]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'deals'::regclass AND contype = %s
                """,
                [contype],
            )
            return {name for (name,) in cursor.fetchall()}

    def test_both_named_checks_are_installed(self):
        self.assertEqual(
            {"deals_agreed_price_range", "deals_payment_anomaly_reason_matches_flag"}
            - self._names("c"),
            set(),
        )

    def test_the_lead_id_uniqueness_is_a_database_constraint(self):
        """Requirement 13.12 as DDL, and the name Requirement 13.8's report keys
        on. ``deals_lead_id_key`` is PostgreSQL's implicit name for the inline
        ``UNIQUE`` a ``OneToOneField`` renders — see the model comment for why the
        field is a OneToOneField rather than a ForeignKey plus a named
        UniqueConstraint."""
        self.assertIn("deals_lead_id_key", self._names("u"))

    def test_both_resolvable_references_are_real_foreign_keys(self):
        """Requirements 13.5, 13.9: rejected by the database, not by an
        application-level existence check."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attname, confrelid::regclass::text
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                WHERE c.conrelid = 'deals'::regclass AND c.contype = 'f'
                """
            )
            self.assertEqual(
                dict(cursor.fetchall()),
                {"lead_id": "leads", "verified_by_operator_id": "operators"},
            )


class AgreedPriceTests(DealRawInsertMixin, TestCase):
    """Requirements 13.2 and 7.6: null, or 550 through 1000."""

    def test_null_and_the_inclusive_bounds_are_accepted(self):
        for price in (None, 550, 1000, 851):
            with self.subTest(agreed_price=price):
                self.assert_accepted(agreed_price=price)

    def test_one_dollar_outside_either_bound_is_rejected(self):
        for price in (549, 1001, 0, -1):
            with self.subTest(agreed_price=price):
                self.assert_rejected_by("deals_agreed_price_range", agreed_price=price)

    def test_the_payments_lower_bound_is_deliberately_wider(self):
        """A note in test form. Requirement 8.3 records a payment of 1 to 1000 so
        a shortfall against a 550-minimum price is recordable and displayable
        under Requirement 8.6. The two ranges disagree on purpose; ``payments``
        is task 2.3's table and must not be narrowed to match this one."""
        self.assert_rejected_by("deals_agreed_price_range", agreed_price=200)


class PaymentAnomalyTwoWayCheckTests(DealRawInsertMixin, TestCase):
    """Requirements 13.6, 8.21, 8.22: reason present **iff** flag true.

    The point of these tests is that *both* halves reject. A one-way check —
    "flagged implies a reason" — is the easy mistake, and it leaves an unflagged
    Deal able to carry a stale reason, which Requirement 8.22's Deal_Room_View
    indicator and Lead_List_View badge would then display for a Deal that has no
    anomaly.
    """

    def test_flagged_with_a_reason_is_the_valid_flagged_shape(self):
        self.assert_accepted(
            payment_anomaly_flag=True,
            payment_anomaly_reason="Lead state Quoted forms no transition to Paid_Pending_Verification.",
        )

    def test_unflagged_with_no_reason_is_the_valid_unflagged_shape(self):
        self.assert_accepted(payment_anomaly_flag=False, payment_anomaly_reason=None)

    def test_half_one_flagged_with_no_reason_is_unstorable(self):
        self.assert_rejected_by(
            "deals_payment_anomaly_reason_matches_flag",
            payment_anomaly_flag=True,
            payment_anomaly_reason=None,
        )

    def test_half_one_survives_null_propagation(self):
        """The specific trap this constraint is written to avoid.

        Without the explicit ``payment_anomaly_reason IS NOT NULL`` term,
        ``length(NULL) >= 1`` evaluates to NULL, the flagged branch evaluates to
        NULL, ``NULL OR false`` evaluates to NULL — and PostgreSQL admits a row
        whose CHECK evaluated to NULL. So the assertion above would pass for a
        constraint that permits exactly the state it exists to forbid. This test
        asserts the *reason* the row is rejected is the constraint firing, not an
        unrelated error.
        """
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.raw_insert(
                    payment_anomaly_flag=True, payment_anomaly_reason=None
                )
        self.assertIn("violates check constraint", str(caught.exception))
        self.assertIn(
            "deals_payment_anomaly_reason_matches_flag", str(caught.exception)
        )

    def test_half_two_unflagged_carrying_a_reason_is_unstorable(self):
        self.assert_rejected_by(
            "deals_payment_anomaly_reason_matches_flag",
            payment_anomaly_flag=False,
            payment_anomaly_reason="a stale reason from an anomaly already cleared",
        )

    def test_the_reason_holds_1_to_500_characters_while_flagged(self):
        for length in (1, 500):
            with self.subTest(length=length):
                self.assert_accepted(
                    payment_anomaly_flag=True,
                    payment_anomaly_reason="r" * length,
                )
        for length in (0, 501):
            with self.subTest(length=length):
                self.assert_rejected_by(
                    "deals_payment_anomaly_reason_matches_flag",
                    payment_anomaly_flag=True,
                    payment_anomaly_reason="r" * length,
                )

    def test_clearing_the_flag_without_clearing_the_reason_fails(self):
        """Requirement 8.22's clear action has to do both in one statement.

        The constraint is what makes that a database rule rather than a thing the
        clear-payment-anomaly handler is trusted to remember. Asserted over an
        ``UPDATE`` because that is the shape the clearing action takes.
        """
        deal_id = self.raw_insert(
            payment_anomaly_flag=True, payment_anomaly_reason="no invoice record"
        )
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE deals SET payment_anomaly_flag = false "
                        "WHERE deal_id = %s",
                        [deal_id],
                    )
        self.assertIn(
            "deals_payment_anomaly_reason_matches_flag", str(caught.exception)
        )
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE deals SET payment_anomaly_flag = false, "
                    "payment_anomaly_reason = NULL WHERE deal_id = %s",
                    [deal_id],
                )
        self.assertIs(
            scalar(
                "SELECT payment_anomaly_flag FROM deals WHERE deal_id = %s", [deal_id]
            ),
            False,
        )

    def test_the_flag_itself_is_not_nullable(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.raw_insert(payment_anomaly_flag=None)


class OneDealPerLeadTests(DealRawInsertMixin, TestCase):
    """Requirement 13.12: at most one Deal per Lead, enforced by the database."""

    def test_a_second_deal_for_the_same_lead_is_rejected(self):
        self.raw_insert()
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.raw_insert(agreed_price=900)
        self.assertIn("deals_lead_id_key", str(caught.exception))

    def test_a_second_deal_is_rejected_through_the_orm_too(self):
        Deal.objects.create(lead=self.lead)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Deal.objects.create(lead=self.lead)

    def test_one_deal_each_for_two_leads_is_fine(self):
        other = make_lead("Beta Plumbing")
        self.raw_insert()
        self.assert_accepted(lead_id=other.id)

    def test_lead_id_is_required(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.raw_insert(lead_id=None)

    def test_an_unresolvable_lead_id_is_rejected_by_the_database(self):
        """Requirement 13.9. ``SET CONSTRAINTS ALL IMMEDIATE`` because Django
        declares its foreign keys ``DEFERRABLE INITIALLY DEFERRED``, so the check
        would otherwise be postponed to a commit this test never reaches."""
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                self.raw_insert(lead_id=9_999_999)
        self.assertIn("foreign key", str(caught.exception).lower())

    def test_an_unresolvable_verifying_operator_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                self.raw_insert(verified_by_operator_id=9_999_999)
        self.assertIn("foreign key", str(caught.exception).lower())

    def test_the_orm_exposes_the_relation_as_one_object_not_a_collection(self):
        """Why the field is a OneToOneField: Requirement 13.12's cardinality is
        visible to every reader, so nothing has to re-encode 'at most one' in
        Python."""
        deal = Deal.objects.create(lead=self.lead)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.deal, deal)
        with self.assertRaises(Deal.DoesNotExist):
            make_lead("Dealless Ltd").deal


class PaymentVerificationTests(DealRawInsertMixin, TestCase):
    """Requirements 8.5, 8.17, 8.20: the timestamp *is* the Payment_Verified_Flag."""

    def test_the_flag_reads_as_set_exactly_when_the_timestamp_is_set(self):
        unverified = Deal.objects.create(lead=self.lead)
        self.assertFalse(unverified.payment_verified_flag)
        unverified.payment_verified_at = MOMENT
        self.assertTrue(unverified.payment_verified_flag)

    def test_a_verifying_operator_is_recorded_against_an_existing_account(self):
        operator = Operator.objects.create_operator("verifier@example.com", "pw12345!")
        deal_id = self.raw_insert(
            payment_verified_at=MOMENT, verified_by_operator_id=operator.id
        )
        deal = Deal.objects.get(pk=deal_id)
        self.assertEqual(deal.verified_by_operator, operator)
        self.assertTrue(deal.payment_verified_flag)

    def test_millisecond_precision_is_retained(self):
        """Requirement 8.5 asks for millisecond precision; §4.3 declares the
        column ``TIMESTAMPTZ(3)``. So 123 ms survives the round trip."""
        moment = dt.datetime(2026, 3, 1, 12, 0, 0, 123000, tzinfo=dt.timezone.utc)
        deal = Deal.objects.create(lead=self.lead, payment_verified_at=moment)
        deal.refresh_from_db()
        self.assertEqual(deal.payment_verified_at, moment)
        self.assertEqual(deal.payment_verified_at.microsecond, 123000)

    def test_the_orm_truncates_sub_millisecond_input_rather_than_rounding_it(self):
        """The direction matters — see ``MillisecondDateTimeField``'s docstring.

        Requirements 5.19 and 5.20 compare a clearance instant *strictly* against
        a plain-``timestamptz`` opt-out column, so a stored value that rounded
        *up* could be later than an instant it genuinely preceded, and task 3.2's
        trigger would then reject a row whose message was already sent. Truncation
        can only move a stored instant earlier.
        """
        moment = dt.datetime(2026, 3, 1, 12, 0, 0, 999_999, tzinfo=dt.timezone.utc)
        deal = Deal.objects.create(lead=self.lead, payment_verified_at=moment)
        deal.refresh_from_db()
        self.assertEqual(deal.payment_verified_at.microsecond, 999_000)
        self.assertLess(deal.payment_verified_at, moment)

    def test_a_raw_writer_gets_postgresql_rounding_which_is_monotonic(self):
        """The residual, recorded rather than papered over.

        A writer that bypasses the ORM gets the column's own round-half-up. That
        is safe for Requirement 8.11's non-strict chain because rounding is
        monotonic — ``a <= b`` implies ``round(a) <= round(b)`` — which this test
        asserts directly, for the two values either side of a rounding boundary.
        """
        early = dt.datetime(2026, 3, 1, 12, 0, 0, 1_500, tzinfo=dt.timezone.utc)
        late = dt.datetime(2026, 3, 1, 12, 0, 0, 2_600, tzinfo=dt.timezone.utc)
        first = self.raw_insert(payment_verified_at=early)
        second = self.raw_insert(
            lead_id=make_lead("Monotone Inc").id, payment_verified_at=late
        )
        stored_early = scalar(
            "SELECT payment_verified_at FROM deals WHERE deal_id = %s", [first]
        )
        stored_late = scalar(
            "SELECT payment_verified_at FROM deals WHERE deal_id = %s", [second]
        )
        self.assertEqual(stored_early.microsecond, 2_000)
        self.assertEqual(stored_late.microsecond, 3_000)
        self.assertLessEqual(stored_early, stored_late)


class UnsetUntilRecordedTests(DealRawInsertMixin, TestCase):
    """Requirement 13.2: the money and delivery columns start unset.

    These are the columns whose 'set' predicate is ``IS TRUE`` rather than
    ``IS NOT NULL``, because Requirement 13.6 pointedly declines to give them the
    ``NOT NULL DEFAULT false`` it gives ``payment_anomaly_flag``. The helper
    properties are the single spelling of that predicate.
    """

    def test_a_fresh_deal_has_every_requirement_8_column_unset(self):
        deal = Deal.objects.create(lead=self.lead)
        for field in (
            "agreed_price",
            "quote_sent_date",
            "invoice_id",
            "payment_received",
            "paid_date",
            "payment_verified_at",
            "verified_by_operator_id",
            "delivery_sent",
            "delivered_date",
            "payment_anomaly_reason",
        ):
            with self.subTest(field=field):
                self.assertIsNone(getattr(deal, field))
        self.assertIs(deal.payment_anomaly_flag, False)
        self.assertFalse(deal.payment_verified_flag)
        self.assertFalse(deal.is_delivered)
        self.assertFalse(deal.has_payment_anomaly)

    def test_the_delivery_set_predicate_distinguishes_null_from_false(self):
        deal = Deal.objects.create(lead=self.lead)
        self.assertFalse(deal.is_delivered)
        deal.delivery_sent = False
        self.assertFalse(deal.is_delivered)
        deal.delivery_sent = True
        self.assertTrue(deal.is_delivered)

    def test_no_requirement_8_column_carries_a_database_default(self):
        """A default would let a Requirement 8 action look applied because the
        column has a plausible value, which is the same argument task 2.1 makes
        for ``leads.last_activity_at``."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, column_default
                FROM information_schema.columns
                WHERE table_name = 'deals'
                  AND column_default IS NOT NULL
                """
            )
            defaulted = dict(cursor.fetchall())
        self.assertEqual(set(defaulted) - {"deal_id"}, {"payment_anomaly_flag"})
