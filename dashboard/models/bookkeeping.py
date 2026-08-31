"""Task 2.3's remaining persistent bookkeeping tables.

These models cover the relational records that sit around the core Lead/Deal and
outreach tables: audit/history, adapter/event idempotency, notifications, login
lockout evidence, bounce evidence, and analytics variant attribution.

The models deliberately stop at schema-level invariants. Cross-table behavioral
triggers belong to task 3, performance indexes to task 2.4, and writers/services
to their later tasks.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models.functions import Now
from django.utils.timezone import now as utc_now

from dashboard.models.constraints import length_at_most, length_between, unset_or
from dashboard.models.fields import MillisecondDateTimeField
from dashboard.models.lead import PipelineState


class AuditActionType(models.TextChoices):
    OUTREACH_SEND = "outreach_send", "Outreach send"
    PIPELINE_STATE_CHANGE = "pipeline_state_change", "Pipeline state change"
    AGREED_PRICE_CHANGE = "agreed_price_change", "Agreed price change"
    SITE_APPROVAL = "site_approval", "Site approval"
    SITE_REJECTION = "site_rejection", "Site rejection"
    INVOICE_CREATION = "invoice_creation", "Invoice creation"
    PAYMENT_VERIFICATION = "payment_verification", "Payment verification"
    PAYMENT_ANOMALY_CLEARING = "payment_anomaly_clearing", "Payment anomaly clearing"
    RELEASE_AUTHORIZATION = "release_authorization", "Release authorization"
    LEAD_FIELD_EDIT = "lead_field_edit", "Lead field edit"
    REJECTED_ACTION_ATTEMPT = "rejected_action_attempt", "Rejected action attempt"


class HistoryActorKind(models.TextChoices):
    OPERATOR = "operator", "Operator"
    ADAPTER_EVENT = "adapter_event", "Adapter event"


class AdapterOperationName(models.TextChoices):
    GENERATE_SITE_PREVIEW = "generate_site_preview", "Generate site preview"
    SEND_PROSPECT_EMAIL = "send_prospect_email", "Send prospect email"
    SEND_DELIVERY_EMAIL = "send_delivery_email", "Send delivery email"
    CREATE_INVOICE = "create_invoice", "Create invoice"
    LOG_OUTBOUND_CALL = "log_outbound_call", "Log outbound call"


class AdapterResultStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"


class InboundEventType(models.TextChoices):
    EMAIL_OPENED = "email_opened", "Email opened"
    EMAIL_CLICKED = "email_clicked", "Email clicked"
    PROSPECT_REPLIED = "prospect_replied", "Prospect replied"
    EMAIL_BOUNCED = "email_bounced", "Email bounced"
    UNSUBSCRIBED = "unsubscribed", "Unsubscribed"
    PAYMENT_RECEIVED = "payment_received", "Payment received"
    SITE_GENERATION_FINISHED = "site_generation_finished", "Site generation finished"


class NotificationEventType(models.TextChoices):
    PROSPECT_REPLIED = "prospect_replied", "Prospect replied"
    PAYMENT_RECEIVED = "payment_received", "Payment received"
    SITE_READY = "site_ready", "Site ready"
    COMPLIANCE_EVENT = "compliance_event", "Compliance event"


class NotificationChannel(models.TextChoices):
    EMAIL = "email", "Email"
    SLACK = "slack", "Slack"


class DeliveryOutcome(models.TextChoices):
    DELIVERED = "delivered", "Delivered"
    FAILED = "failed", "Failed"


class LoginAttemptOutcome(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILURE = "failure", "Failure"


class VariantDimension(models.TextChoices):
    SUBJECT_LINE = "subject_line", "Subject line"
    BODY_LENGTH = "body_length", "Body length"
    CTA_STYLE = "cta_style", "CTA style"
    SEND_TIMING = "send_timing", "Send timing"
    PRICE_ANCHOR = "price_anchor", "Price anchor"


class AuditEntry(models.Model):
    id = models.BigAutoField(primary_key=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audit_entries",
        db_column="actor_id",
    )
    action_type = models.TextField(choices=AuditActionType.choices)
    target_type = models.TextField()
    target_id = models.BigIntegerField()
    before_value = models.JSONField(null=True, blank=True)
    after_value = models.JSONField(null=True, blank=True)
    occurred_at = MillisecondDateTimeField(
        default=utc_now,
        db_default=Now(),
        editable=False,
    )

    class Meta:
        db_table = "audit_entries"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action_type__in=AuditActionType.values),
                name="audit_entries_action_type_in_enum",
            ),
            models.CheckConstraint(
                condition=length_between("target_type", 1, 100),
                name="audit_entries_target_type_length",
            ),
        ]


class PipelineStateHistory(models.Model):
    id = models.BigAutoField(primary_key=True)
    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="state_history",
        db_column="lead_id",
    )
    from_state = models.TextField(choices=PipelineState.choices, null=True, blank=True)
    to_state = models.TextField(choices=PipelineState.choices)
    occurred_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="state_history_entries",
        db_column="actor_id",
        null=True,
        blank=True,
    )
    actor_kind = models.TextField(choices=HistoryActorKind.choices)
    source_event_id = models.TextField(null=True, blank=True)
    audit_entry = models.ForeignKey(
        AuditEntry,
        on_delete=models.PROTECT,
        related_name="state_history_entries",
        db_column="audit_entry_id",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "pipeline_state_history"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(to_state__in=PipelineState.values),
                name="history_to_state_in_enum",
            ),
            models.CheckConstraint(
                condition=models.Q(from_state__isnull=True)
                | models.Q(from_state__in=PipelineState.values),
                name="history_from_state_in_enum_or_null",
            ),
            models.CheckConstraint(
                condition=models.Q(from_state__isnull=False)
                | models.Q(to_state=PipelineState.NEW_LEAD),
                name="history_null_from_state_is_genesis",
            ),
            models.CheckConstraint(
                condition=models.Q(actor_kind__in=HistoryActorKind.values),
                name="history_actor_kind_in_enum",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(actor_kind=HistoryActorKind.OPERATOR, actor__isnull=False)
                    | models.Q(
                        actor_kind=HistoryActorKind.ADAPTER_EVENT,
                        actor__isnull=True,
                        source_event_id__isnull=False,
                    )
                ),
                name="history_actor_shape_matches_kind",
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "source_event_id",
                    length_between("source_event_id", 1, 128),
                ),
                name="history_source_event_id_length",
            ),
        ]


class ProcessedEvent(models.Model):
    event_id = models.TextField(primary_key=True)
    event_type = models.TextField(choices=InboundEventType.choices)
    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="processed_events",
        db_column="lead_id",
    )
    received_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)

    class Meta:
        db_table = "processed_events"
        constraints = [
            models.CheckConstraint(
                condition=length_between("event_id", 1, 128),
                name="processed_events_event_id_length",
            ),
            models.CheckConstraint(
                condition=models.Q(event_type__in=InboundEventType.values),
                name="processed_events_event_type_in_enum",
            ),
        ]


class RejectedEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    event_id = models.TextField(null=True, blank=True)
    event_type = models.TextField(null=True, blank=True)
    reported_lead_id = models.BigIntegerField(null=True, blank=True)
    payload = models.JSONField()
    rejection_reason = models.TextField()
    rejected_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)

    class Meta:
        db_table = "rejected_events"
        constraints = [
            models.CheckConstraint(
                condition=length_between("rejection_reason", 1, 1000),
                name="rejected_events_reason_length",
            )
        ]


class AdapterInvocation(models.Model):
    id = models.BigAutoField(primary_key=True)
    operation_name = models.TextField(choices=AdapterOperationName.choices)
    arguments = models.JSONField()
    idempotency_key = models.UUIDField()
    result = models.TextField(choices=AdapterResultStatus.choices)
    failure_reason = models.TextField(null=True, blank=True)
    elapsed_ms = models.IntegerField()
    invoked_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)

    class Meta:
        db_table = "adapter_invocations"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(operation_name__in=AdapterOperationName.values),
                name="adapter_invocations_operation_in_enum",
            ),
            models.CheckConstraint(
                condition=models.Q(result__in=AdapterResultStatus.values),
                name="adapter_invocations_result_in_enum",
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "failure_reason",
                    length_between("failure_reason", 1, 500),
                ),
                name="adapter_invocations_failure_reason_length",
            ),
            models.CheckConstraint(
                condition=models.Q(elapsed_ms__gte=0),
                name="adapter_invocations_elapsed_nonnegative",
            ),
        ]


class EmailBounce(models.Model):
    id = models.BigAutoField(primary_key=True)
    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="email_bounces",
        db_column="lead_id",
    )
    contact_email = models.TextField()
    reason = models.TextField(null=True, blank=True)
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "email_bounces"
        constraints = [
            models.CheckConstraint(
                condition=length_between("contact_email", 1, 320),
                name="email_bounces_contact_email_length",
            ),
            models.CheckConstraint(
                condition=unset_or("reason", length_at_most("reason", 500)),
                name="email_bounces_reason_length",
            ),
        ]


class Notification(models.Model):
    id = models.BigAutoField(primary_key=True)
    event_id = models.TextField()
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="notifications",
        db_column="operator_id",
    )
    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="notifications",
        db_column="lead_id",
    )
    event_type = models.TextField(choices=NotificationEventType.choices)
    payload = models.JSONField(default=dict)
    deep_link = models.TextField()
    created_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)

    class Meta:
        db_table = "notifications"
        constraints = [
            models.UniqueConstraint(
                fields=("event_id", "operator"),
                name="notifications_event_operator_unique",
            ),
            models.CheckConstraint(
                condition=length_between("event_id", 1, 128),
                name="notifications_event_id_length",
            ),
            models.CheckConstraint(
                condition=models.Q(event_type__in=NotificationEventType.values),
                name="notifications_event_type_in_enum",
            ),
            models.CheckConstraint(
                condition=length_between("deep_link", 1, 2048),
                name="notifications_deep_link_length",
            ),
        ]


class NotificationDelivery(models.Model):
    id = models.BigAutoField(primary_key=True)
    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name="deliveries",
        db_column="notification_id",
    )
    channel = models.TextField(choices=NotificationChannel.choices)
    attempt_count = models.SmallIntegerField()
    outcome = models.TextField(choices=DeliveryOutcome.choices)
    failure_reason = models.TextField(null=True, blank=True)
    last_attempt_at = models.DateTimeField()

    class Meta:
        db_table = "notification_deliveries"
        constraints = [
            models.UniqueConstraint(
                fields=("notification", "channel"),
                name="notification_deliveries_notification_channel_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(channel__in=NotificationChannel.values),
                name="notification_deliveries_channel_in_enum",
            ),
            models.CheckConstraint(
                condition=models.Q(attempt_count__range=(1, 4)),
                name="notification_deliveries_attempt_count_range",
            ),
            models.CheckConstraint(
                condition=models.Q(outcome__in=DeliveryOutcome.values),
                name="notification_deliveries_outcome_in_enum",
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "failure_reason",
                    length_between("failure_reason", 1, 500),
                ),
                name="notification_deliveries_failure_reason_length",
            ),
        ]


class NotificationPreference(models.Model):
    id = models.BigAutoField(primary_key=True)
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
        db_column="operator_id",
    )
    event_type = models.TextField(choices=NotificationEventType.choices)
    subscribed = models.BooleanField(default=True, db_default=True)
    email_enabled = models.BooleanField(default=True, db_default=True)
    slack_enabled = models.BooleanField(default=True, db_default=True)

    class Meta:
        db_table = "notification_preferences"
        constraints = [
            models.UniqueConstraint(
                fields=("operator", "event_type"),
                name="notification_preferences_operator_event_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(event_type__in=NotificationEventType.values),
                name="notification_preferences_event_type_in_enum",
            ),
        ]


class LoginAttempt(models.Model):
    id = models.BigAutoField(primary_key=True)
    identifier_hash = models.TextField()
    occurred_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)
    outcome = models.TextField(choices=LoginAttemptOutcome.choices)

    class Meta:
        db_table = "login_attempts"
        constraints = [
            models.CheckConstraint(
                condition=length_between("identifier_hash", 1, 128),
                name="login_attempts_identifier_hash_length",
            ),
            models.CheckConstraint(
                condition=models.Q(outcome__in=LoginAttemptOutcome.values),
                name="login_attempts_outcome_in_enum",
            ),
        ]


class Variant(models.Model):
    id = models.BigAutoField(primary_key=True)
    dimension = models.TextField(choices=VariantDimension.choices)
    value = models.TextField()

    class Meta:
        db_table = "variants"
        constraints = [
            models.UniqueConstraint(
                fields=("dimension", "value"),
                name="variants_dimension_value_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(dimension__in=VariantDimension.values),
                name="variants_dimension_in_enum",
            ),
            models.CheckConstraint(
                condition=length_between("value", 1, 500),
                name="variants_value_length",
            ),
        ]


class EmailVariantAssignment(models.Model):
    id = models.BigAutoField(primary_key=True)
    email = models.ForeignKey(
        "dashboard.Email",
        on_delete=models.CASCADE,
        related_name="variant_assignments",
        db_column="email_id",
    )
    variant = models.ForeignKey(
        Variant,
        on_delete=models.PROTECT,
        related_name="email_assignments",
        db_column="variant_id",
    )
    dimension = models.TextField(choices=VariantDimension.choices)

    class Meta:
        db_table = "email_variant_assignments"
        constraints = [
            models.UniqueConstraint(
                fields=("email", "dimension"),
                name="email_variant_assignments_email_dimension_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(dimension__in=VariantDimension.values),
                name="email_variant_assignments_dimension_in_enum",
            ),
        ]
