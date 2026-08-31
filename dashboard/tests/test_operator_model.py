"""Tests for the ``operators`` model (Requirements 1.5, 9.5, 9.6).

Every claim these tests make about a *stored* invariant is driven through raw
SQL as well as through the ORM. That is the whole point: Requirement 1.5's
Viewer default and three-value role set are security properties of the schema,
and a test that only exercises ``Operator.objects.create_operator`` would pass
identically against a model whose role rules lived in ``choices`` alone — which
a raw ``INSERT``, a data migration, or the future bot's connection (§4.2) never
consults.

The role *authorization* table (``MIN_ROLE``, ``available_actions``) is task
4.2's and is not tested here; only the ordering these tests confirm is
expressible.
"""

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from dashboard.models import Operator, Role


class RoleOrderingTests(TestCase):
    """The ``Viewer < Agent < Admin`` ordering of design §3.2."""

    def test_exactly_three_roles(self):
        self.assertEqual(Role.values, ["Viewer", "Agent", "Admin"])

    def test_ordering_is_strict_and_ascending(self):
        self.assertLess(Role.VIEWER.rank, Role.AGENT.rank)
        self.assertLess(Role.AGENT.rank, Role.ADMIN.rank)

    def test_at_least_is_reflexive_and_ordered(self):
        for role in Role:
            self.assertTrue(role.at_least(role), f"{role} should satisfy itself")
        self.assertTrue(Role.ADMIN.at_least(Role.VIEWER))
        self.assertTrue(Role.ADMIN.at_least(Role.AGENT))
        self.assertTrue(Role.AGENT.at_least(Role.VIEWER))
        self.assertFalse(Role.VIEWER.at_least(Role.AGENT))
        self.assertFalse(Role.VIEWER.at_least(Role.ADMIN))
        self.assertFalse(Role.AGENT.at_least(Role.ADMIN))

    def test_instance_helper_reads_the_stored_value(self):
        operator = Operator.objects.create_operator("ordering@example.com", "pw-12345678")
        self.assertIs(operator.role_enum, Role.VIEWER)
        self.assertTrue(operator.has_role_at_least(Role.VIEWER))
        self.assertFalse(operator.has_role_at_least(Role.AGENT))


class ViewerDefaultTests(TestCase):
    """Requirement 1.5: every newly created account arrives as a Viewer."""

    def test_orm_creation_defaults_to_viewer(self):
        operator = Operator.objects.create_operator("new@example.com", "pw-12345678")
        operator.refresh_from_db()
        self.assertEqual(operator.role, Role.VIEWER)

    def test_creation_ignores_an_attempt_to_name_a_role(self):
        """A caller cannot mint an Admin at creation time by passing ``role=``.

        Requirement 1.5 reaches Admin only through the Admin-only role change
        (task 4.2), so creation has exactly one outcome for the role field.
        """
        operator = Operator.objects.create_operator(
            "escalate@example.com", "pw-12345678", role=Role.ADMIN
        )
        operator.refresh_from_db()
        self.assertEqual(operator.role, Role.VIEWER)

    def test_raw_insert_omitting_role_still_gets_viewer(self):
        """The default is the column's, not the ORM's (``db_default``)."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO operators (password, email)
                VALUES ('!unusable', 'raw@example.com')
                RETURNING role
                """
            )
            self.assertEqual(cursor.fetchone()[0], "Viewer")

    def test_role_column_default_is_declared_in_the_catalog(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_default, is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'operators' AND column_name = 'role'
                """
            )
            column_default, is_nullable = cursor.fetchone()
        self.assertIn("Viewer", column_default)
        self.assertEqual(is_nullable, "NO")


class RoleConstraintTests(TestCase):
    """Requirement 1.5: the three-value set is enforced by the database."""

    def test_fourth_role_value_is_rejected_on_insert(self):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO operators (password, email, role)
                    VALUES ('!unusable', 'fourth@example.com', 'Superuser')
                    """
                )
        self.assertIn("operators_role_in_enum", str(caught.exception))

    def test_fourth_role_value_is_rejected_on_update(self):
        operator = Operator.objects.create_operator("update@example.com", "pw-12345678")
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE operators SET role = %s WHERE id = %s",
                    ["Owner", operator.id],
                )
        self.assertIn("operators_role_in_enum", str(caught.exception))

    def test_case_variant_of_a_legal_role_is_rejected(self):
        """The constraint is over the exact stored spellings, not a fold."""
        for rejected in ("viewer", "AGENT", "admin ", ""):
            with self.subTest(role=rejected):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO operators (password, email, role)
                            VALUES ('!unusable', %s, %s)
                            """,
                            [f"case-{rejected.strip() or 'empty'}@example.com", rejected],
                        )

    def test_all_three_legal_values_are_storable(self):
        for index, role in enumerate(Role.values):
            with self.subTest(role=role):
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO operators (password, email, role)
                        VALUES ('!unusable', %s, %s)
                        RETURNING role
                        """,
                        [f"legal-{index}@example.com", role],
                    )
                    self.assertEqual(cursor.fetchone()[0], role)


class PrimaryKeyWidthTests(TestCase):
    """§4.1 references ``operators`` from four tables as ``bigint``."""

    def test_primary_key_is_bigint(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_type
                  FROM information_schema.columns
                 WHERE table_name = 'operators' AND column_name = 'id'
                """
            )
            self.assertEqual(cursor.fetchone()[0], "bigint")


class EmailIdentifierTests(TestCase):
    """Requirement 9.6: the registered email address the email channel uses."""

    def test_email_is_the_username_field(self):
        self.assertEqual(Operator.USERNAME_FIELD, "email")
        self.assertEqual(Operator.EMAIL_FIELD, "email")
        self.assertEqual(Operator.REQUIRED_FIELDS, [])

    def test_identifier_is_stored_normalized_and_looked_up_case_insensitively(self):
        operator = Operator.objects.create_operator("  Mixed.Case@Example.COM ", "pw-12345678")
        self.assertEqual(operator.email, "mixed.case@example.com")
        self.assertEqual(
            Operator.objects.get_by_natural_key("MIXED.CASE@EXAMPLE.com").pk,
            operator.pk,
        )

    def test_unnormalized_address_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO operators (password, email)
                    VALUES ('!unusable', 'Upper@Example.com')
                    """
                )
        self.assertIn("operators_email_normalized", str(caught.exception))

    def test_empty_identifier_is_rejected_by_the_database(self):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO operators (password, email) VALUES ('!unusable', '')"
                )
        self.assertIn("operators_email_present", str(caught.exception))

    def test_creation_without_an_identifier_is_refused(self):
        for absent in ("", "   "):
            with self.subTest(email=absent):
                with self.assertRaises(ValueError):
                    Operator.objects.create_operator(absent, "pw-12345678")

    def test_identifier_is_unique(self):
        Operator.objects.create_operator("dup@example.com", "pw-12345678")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Operator.objects.create_operator("DUP@example.com", "pw-12345678")


class SlackWebhookTests(TestCase):
    """Requirement 9.5: the Slack webhook target is optional.

    Requirement 9.12's "enabling Slack with no recorded target is rejected" is
    task 16.1's; what is settled here is that "recorded" has exactly one
    representation, so 16.1 has a single predicate to test.
    """

    def test_absent_by_default(self):
        operator = Operator.objects.create_operator("noslack@example.com", "pw-12345678")
        operator.refresh_from_db()
        self.assertIsNone(operator.slack_webhook_url)
        self.assertFalse(operator.has_slack_target)

    def test_recorded_target_is_readable(self):
        operator = Operator.objects.create_operator(
            "slack@example.com",
            "pw-12345678",
            slack_webhook_url="https://hooks.slack.com/services/T000/B000/xyz",
        )
        operator.refresh_from_db()
        self.assertTrue(operator.has_slack_target)

    def test_column_is_nullable(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT is_nullable
                  FROM information_schema.columns
                 WHERE table_name = 'operators'
                   AND column_name = 'slack_webhook_url'
                """
            )
            self.assertEqual(cursor.fetchone()[0], "YES")

    def test_empty_string_is_not_a_representation_of_absent(self):
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO operators (password, email, slack_webhook_url)
                    VALUES ('!unusable', 'blankslack@example.com', '')
                    """
                )
        self.assertIn("operators_slack_webhook_null_or_present", str(caught.exception))


class PasswordStorageTests(TestCase):
    """``AbstractBaseUser`` supplies the hashing; confirm it is actually used."""

    def test_password_is_hashed_and_verifiable(self):
        operator = Operator.objects.create_operator("pw@example.com", "correct-horse-battery")
        self.assertNotIn("correct-horse-battery", operator.password)
        self.assertTrue(operator.check_password("correct-horse-battery"))
        self.assertFalse(operator.check_password("wrong"))

    def test_no_django_permission_machinery_is_attached(self):
        """§3.2: the role field is the whole authorization model.

        ``PermissionsMixin`` is deliberately absent, so there is no second
        authority (groups, per-model permissions, ``is_superuser``) that task
        4.2's ``available_actions()`` would not see.
        """
        field_names = {field.name for field in Operator._meta.get_fields()}
        self.assertTrue(field_names.isdisjoint({"is_superuser", "groups", "user_permissions"}))
