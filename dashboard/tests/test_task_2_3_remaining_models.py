from __future__ import annotations

import datetime as dt
import uuid

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from dashboard.models import (
    AdapterInvocation,
    AuditEntry,
    Email,
    EmailVariantAssignment,
    LoginAttempt,
    Notification,
    NotificationDelivery,
    NotificationPreference,
    Operator,
    OutreachRequest,
    PipelineState,
    PipelineStateHistory,
    ProcessedEvent,
    RejectedEvent,
    Variant,
)

MOMENT = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)


def make_lead(name: str = "Task 2.3 Co"):
    from dashboard.models import Lead

    return Lead.objects.create(
        company_name=name,
        researched_score=3,
        status=PipelineState.NEW_LEAD,
        last_activity_at=MOMENT,
    )


def make_operator(email: str = "agent@example.com"):
    return Operator.objects.create_operator(email, "pw12345!")


class RemainingTableInventoryTests(TestCase):
    def test_all_nineteen_task_2_3_tables_exist(self):
        expected = {
            "site_projects",
            "site_pages",
            "contacts",
            "invoices",
            "payments",
            "release_authorizations",
            "audit_entries",
            "pipeline_state_history",
            "processed_events",
            "outreach_requests",
            "rejected_events",
            "adapter_invocations",
            "email_bounces",
            "notifications",
            "notification_deliveries",
            "notification_preferences",
            "login_attempts",
            "variants",
            "email_variant_assignments",
        }
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
            actual = {name for (name,) in cursor.fetchall()}
        self.assertEqual(expected - actual, set())


class EventBookkeepingTests(TestCase):
    def setUp(self):
        self.lead = make_lead()

    def test_processed_event_identifier_is_the_database_dedupe_key(self):
        ProcessedEvent.objects.create(
            event_id="event-1",
            event_type="email_opened",
            lead=self.lead,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcessedEvent.objects.create(
                    event_id="event-1",
                    event_type="email_clicked",
                    lead=self.lead,
                )

    def test_processed_event_id_is_limited_to_128_characters(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProcessedEvent.objects.create(
                    event_id="e" * 129,
                    event_type="email_opened",
                    lead=self.lead,
                )

    def test_rejected_event_can_preserve_an_unresolvable_reported_lead_id(self):
        rejected = RejectedEvent.objects.create(
            event_id="bad-event",
            event_type="unknown_type",
            reported_lead_id=9_999_999,
            payload={"lead_id": 9_999_999},
            rejection_reason="lead_id does not resolve",
        )
        self.assertEqual(rejected.reported_lead_id, 9_999_999)


class AuditAndHistoryTests(TestCase):
    def setUp(self):
        self.lead = make_lead()
        self.operator = make_operator()

    def test_audit_action_type_is_closed_in_postgresql(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AuditEntry.objects.create(
                    actor=self.operator,
                    action_type="invented_action",
                    target_type="lead",
                    target_id=self.lead.id,
                )

    def test_genesis_history_requires_new_lead(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PipelineStateHistory.objects.create(
                    lead=self.lead,
                    from_state=None,
                    to_state=PipelineState.CONTACTED,
                    actor=self.operator,
                    actor_kind="operator",
                )

    def test_adapter_history_requires_no_operator_and_a_source_event(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PipelineStateHistory.objects.create(
                    lead=self.lead,
                    from_state=PipelineState.NEW_LEAD,
                    to_state=PipelineState.CONTACTED,
                    actor=self.operator,
                    actor_kind="adapter_event",
                    source_event_id="evt-1",
                )

        valid = PipelineStateHistory.objects.create(
            lead=self.lead,
            from_state=PipelineState.NEW_LEAD,
            to_state=PipelineState.CONTACTED,
            actor=None,
            actor_kind="adapter_event",
            source_event_id="evt-1",
        )
        self.assertEqual(valid.source_event_id, "evt-1")


class AdapterInvocationTests(TestCase):
    def test_operation_and_elapsed_time_are_database_constrained(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AdapterInvocation.objects.create(
                    operation_name="not_an_operation",
                    arguments={},
                    idempotency_key=uuid.uuid4(),
                    result="success",
                    elapsed_ms=1,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AdapterInvocation.objects.create(
                    operation_name="send_prospect_email",
                    arguments={},
                    idempotency_key=uuid.uuid4(),
                    result="success",
                    elapsed_ms=-1,
                )


class NotificationSchemaTests(TestCase):
    def setUp(self):
        self.lead = make_lead()
        self.operator = make_operator()

    def notification(self, event_id: str = "evt-notify"):
        return Notification.objects.create(
            event_id=event_id,
            operator=self.operator,
            lead=self.lead,
            event_type="prospect_replied",
            payload={"excerpt": "Interested"},
            deep_link=f"/leads/{self.lead.id}/",
        )

    def test_one_notification_per_event_and_operator(self):
        self.notification()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.notification()

    def test_one_delivery_per_notification_and_channel_with_attempt_range(self):
        notification = self.notification()
        NotificationDelivery.objects.create(
            notification=notification,
            channel="email",
            attempt_count=1,
            outcome="delivered",
            last_attempt_at=MOMENT,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                NotificationDelivery.objects.create(
                    notification=notification,
                    channel="email",
                    attempt_count=2,
                    outcome="failed",
                    failure_reason="duplicate channel",
                    last_attempt_at=MOMENT,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                NotificationDelivery.objects.create(
                    notification=notification,
                    channel="slack",
                    attempt_count=5,
                    outcome="failed",
                    failure_reason="too many attempts",
                    last_attempt_at=MOMENT,
                )

    def test_one_preference_row_per_operator_and_event_type(self):
        NotificationPreference.objects.create(
            operator=self.operator,
            event_type="prospect_replied",
            subscribed=True,
            email_enabled=False,
            slack_enabled=True,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                NotificationPreference.objects.create(
                    operator=self.operator,
                    event_type="prospect_replied",
                )


class LoginAndVariantTests(TestCase):
    def test_login_attempt_outcome_is_closed(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LoginAttempt.objects.create(
                    identifier_hash="hash-value",
                    outcome="maybe",
                )

    def test_variant_dimension_and_value_are_unique_together(self):
        Variant.objects.create(dimension="cta_style", value="soft-question")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Variant.objects.create(dimension="cta_style", value="soft-question")

    def test_email_gets_at_most_one_variant_per_dimension(self):
        lead = make_lead("Variant Co")
        reservation = OutreachRequest.objects.create(
            lead=lead,
            channel="email",
            clearance_timestamp=MOMENT,
        )
        email = Email.objects.create(
            lead=lead,
            outreach_request_id=reservation.id,
            subject="Quick idea",
            body="A short message",
            clearance_timestamp=MOMENT,
            sent_at=MOMENT,
        )
        first = Variant.objects.create(dimension="subject_line", value="quick-idea")
        second = Variant.objects.create(dimension="subject_line", value="website-idea")
        EmailVariantAssignment.objects.create(
            email=email,
            variant=first,
            dimension="subject_line",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EmailVariantAssignment.objects.create(
                    email=email,
                    variant=second,
                    dimension="subject_line",
                )
