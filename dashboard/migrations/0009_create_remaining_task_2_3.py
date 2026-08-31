import dashboard.models.constraints
import dashboard.models.fields
import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0008_create_core_task_2_3"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdapterInvocation",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "operation_name",
                    models.TextField(
                        choices=[
                            ("generate_site_preview", "Generate site preview"),
                            ("send_prospect_email", "Send prospect email"),
                            ("send_delivery_email", "Send delivery email"),
                            ("create_invoice", "Create invoice"),
                            ("log_outbound_call", "Log outbound call"),
                        ]
                    ),
                ),
                ("arguments", models.JSONField()),
                ("idempotency_key", models.UUIDField()),
                (
                    "result",
                    models.TextField(
                        choices=[("success", "Success"), ("failure", "Failure")]
                    ),
                ),
                ("failure_reason", models.TextField(blank=True, null=True)),
                ("elapsed_ms", models.IntegerField()),
                (
                    "invoked_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
            ],
            options={
                "db_table": "adapter_invocations",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "operation_name__in",
                                [
                                    "generate_site_preview",
                                    "send_prospect_email",
                                    "send_delivery_email",
                                    "create_invoice",
                                    "log_outbound_call",
                                ],
                            )
                        ),
                        name="adapter_invocations_operation_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("result__in", ["success", "failure"])),
                        name="adapter_invocations_result_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.unset_or(
                            "failure_reason",
                            dashboard.models.constraints.length_between(
                                "failure_reason", 1, 500
                            ),
                        ),
                        name="adapter_invocations_failure_reason_length",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("elapsed_ms__gte", 0)),
                        name="adapter_invocations_elapsed_nonnegative",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AuditEntry",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "action_type",
                    models.TextField(
                        choices=[
                            ("outreach_send", "Outreach send"),
                            ("pipeline_state_change", "Pipeline state change"),
                            ("agreed_price_change", "Agreed price change"),
                            ("site_approval", "Site approval"),
                            ("site_rejection", "Site rejection"),
                            ("invoice_creation", "Invoice creation"),
                            ("payment_verification", "Payment verification"),
                            ("payment_anomaly_clearing", "Payment anomaly clearing"),
                            ("release_authorization", "Release authorization"),
                            ("lead_field_edit", "Lead field edit"),
                            ("rejected_action_attempt", "Rejected action attempt"),
                        ]
                    ),
                ),
                ("target_type", models.TextField()),
                ("target_id", models.BigIntegerField()),
                ("before_value", models.JSONField(blank=True, null=True)),
                ("after_value", models.JSONField(blank=True, null=True)),
                (
                    "occurred_at",
                    dashboard.models.fields.MillisecondDateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        db_column="actor_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="audit_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "audit_entries",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "action_type__in",
                                [
                                    "outreach_send",
                                    "pipeline_state_change",
                                    "agreed_price_change",
                                    "site_approval",
                                    "site_rejection",
                                    "invoice_creation",
                                    "payment_verification",
                                    "payment_anomaly_clearing",
                                    "release_authorization",
                                    "lead_field_edit",
                                    "rejected_action_attempt",
                                ],
                            )
                        ),
                        name="audit_entries_action_type_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "target_type", 1, 100
                        ),
                        name="audit_entries_target_type_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="EmailBounce",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("contact_email", models.TextField()),
                ("reason", models.TextField(blank=True, null=True)),
                ("occurred_at", models.DateTimeField()),
                (
                    "lead",
                    models.ForeignKey(
                        db_column="lead_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="email_bounces",
                        to="dashboard.lead",
                    ),
                ),
            ],
            options={
                "db_table": "email_bounces",
                "constraints": [
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "contact_email", 1, 320
                        ),
                        name="email_bounces_contact_email_length",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.unset_or(
                            "reason",
                            dashboard.models.constraints.length_at_most("reason", 500),
                        ),
                        name="email_bounces_reason_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="LoginAttempt",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("identifier_hash", models.TextField()),
                (
                    "occurred_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                (
                    "outcome",
                    models.TextField(
                        choices=[("success", "Success"), ("failure", "Failure")]
                    ),
                ),
            ],
            options={
                "db_table": "login_attempts",
                "constraints": [
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "identifier_hash", 1, 128
                        ),
                        name="login_attempts_identifier_hash_length",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("outcome__in", ["success", "failure"])),
                        name="login_attempts_outcome_in_enum",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("event_id", models.TextField()),
                (
                    "event_type",
                    models.TextField(
                        choices=[
                            ("prospect_replied", "Prospect replied"),
                            ("payment_received", "Payment received"),
                            ("site_ready", "Site ready"),
                            ("compliance_event", "Compliance event"),
                        ]
                    ),
                ),
                ("payload", models.JSONField(default=dict)),
                ("deep_link", models.TextField()),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                (
                    "lead",
                    models.ForeignKey(
                        db_column="lead_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="notifications",
                        to="dashboard.lead",
                    ),
                ),
                (
                    "operator",
                    models.ForeignKey(
                        db_column="operator_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "notifications",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("event_id", "operator"),
                        name="notifications_event_operator_unique",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "event_id", 1, 128
                        ),
                        name="notifications_event_id_length",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "event_type__in",
                                [
                                    "prospect_replied",
                                    "payment_received",
                                    "site_ready",
                                    "compliance_event",
                                ],
                            )
                        ),
                        name="notifications_event_type_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "deep_link", 1, 2048
                        ),
                        name="notifications_deep_link_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "event_type",
                    models.TextField(
                        choices=[
                            ("prospect_replied", "Prospect replied"),
                            ("payment_received", "Payment received"),
                            ("site_ready", "Site ready"),
                            ("compliance_event", "Compliance event"),
                        ]
                    ),
                ),
                ("subscribed", models.BooleanField(db_default=True, default=True)),
                ("email_enabled", models.BooleanField(db_default=True, default=True)),
                ("slack_enabled", models.BooleanField(db_default=True, default=True)),
                (
                    "operator",
                    models.ForeignKey(
                        db_column="operator_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "notification_preferences",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("operator", "event_type"),
                        name="notification_preferences_operator_event_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "event_type__in",
                                [
                                    "prospect_replied",
                                    "payment_received",
                                    "site_ready",
                                    "compliance_event",
                                ],
                            )
                        ),
                        name="notification_preferences_event_type_in_enum",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ProcessedEvent",
            fields=[
                ("event_id", models.TextField(primary_key=True, serialize=False)),
                (
                    "event_type",
                    models.TextField(
                        choices=[
                            ("email_opened", "Email opened"),
                            ("email_clicked", "Email clicked"),
                            ("prospect_replied", "Prospect replied"),
                            ("email_bounced", "Email bounced"),
                            ("unsubscribed", "Unsubscribed"),
                            ("payment_received", "Payment received"),
                            ("site_generation_finished", "Site generation finished"),
                        ]
                    ),
                ),
                (
                    "received_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                (
                    "lead",
                    models.ForeignKey(
                        db_column="lead_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="processed_events",
                        to="dashboard.lead",
                    ),
                ),
            ],
            options={
                "db_table": "processed_events",
                "constraints": [
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "event_id", 1, 128
                        ),
                        name="processed_events_event_id_length",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "event_type__in",
                                [
                                    "email_opened",
                                    "email_clicked",
                                    "prospect_replied",
                                    "email_bounced",
                                    "unsubscribed",
                                    "payment_received",
                                    "site_generation_finished",
                                ],
                            )
                        ),
                        name="processed_events_event_type_in_enum",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="RejectedEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("event_id", models.TextField(blank=True, null=True)),
                ("event_type", models.TextField(blank=True, null=True)),
                ("reported_lead_id", models.BigIntegerField(blank=True, null=True)),
                ("payload", models.JSONField()),
                ("rejection_reason", models.TextField()),
                (
                    "rejected_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
            ],
            options={
                "db_table": "rejected_events",
                "constraints": [
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "rejection_reason", 1, 1000
                        ),
                        name="rejected_events_reason_length",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Variant",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "dimension",
                    models.TextField(
                        choices=[
                            ("subject_line", "Subject line"),
                            ("body_length", "Body length"),
                            ("cta_style", "CTA style"),
                            ("send_timing", "Send timing"),
                            ("price_anchor", "Price anchor"),
                        ]
                    ),
                ),
                ("value", models.TextField()),
            ],
            options={
                "db_table": "variants",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("dimension", "value"),
                        name="variants_dimension_value_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "dimension__in",
                                [
                                    "subject_line",
                                    "body_length",
                                    "cta_style",
                                    "send_timing",
                                    "price_anchor",
                                ],
                            )
                        ),
                        name="variants_dimension_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "value", 1, 500
                        ),
                        name="variants_value_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="NotificationDelivery",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "channel",
                    models.TextField(choices=[("email", "Email"), ("slack", "Slack")]),
                ),
                ("attempt_count", models.SmallIntegerField()),
                (
                    "outcome",
                    models.TextField(
                        choices=[("delivered", "Delivered"), ("failed", "Failed")]
                    ),
                ),
                ("failure_reason", models.TextField(blank=True, null=True)),
                ("last_attempt_at", models.DateTimeField()),
                (
                    "notification",
                    models.ForeignKey(
                        db_column="notification_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="deliveries",
                        to="dashboard.notification",
                    ),
                ),
            ],
            options={
                "db_table": "notification_deliveries",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("notification", "channel"),
                        name="notification_deliveries_notification_channel_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("channel__in", ["email", "slack"])),
                        name="notification_deliveries_channel_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("attempt_count__range", (1, 4))),
                        name="notification_deliveries_attempt_count_range",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("outcome__in", ["delivered", "failed"])),
                        name="notification_deliveries_outcome_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.unset_or(
                            "failure_reason",
                            dashboard.models.constraints.length_between(
                                "failure_reason", 1, 500
                            ),
                        ),
                        name="notification_deliveries_failure_reason_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="PipelineStateHistory",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "from_state",
                    models.TextField(
                        blank=True,
                        choices=[
                            ("New_Lead", "New Lead"),
                            ("Contacted", "Contacted"),
                            ("Replied", "Replied"),
                            ("Scheduled", "Scheduled"),
                            ("Quoted", "Quoted"),
                            ("Won", "Won"),
                            ("Invoiced", "Invoiced"),
                            ("Paid_Pending_Verification", "Paid, pending verification"),
                            ("Payment_Verified", "Payment verified"),
                            ("Released", "Released"),
                            ("Closed_Lost", "Closed lost"),
                        ],
                        null=True,
                    ),
                ),
                (
                    "to_state",
                    models.TextField(
                        choices=[
                            ("New_Lead", "New Lead"),
                            ("Contacted", "Contacted"),
                            ("Replied", "Replied"),
                            ("Scheduled", "Scheduled"),
                            ("Quoted", "Quoted"),
                            ("Won", "Won"),
                            ("Invoiced", "Invoiced"),
                            ("Paid_Pending_Verification", "Paid, pending verification"),
                            ("Payment_Verified", "Payment verified"),
                            ("Released", "Released"),
                            ("Closed_Lost", "Closed lost"),
                        ]
                    ),
                ),
                (
                    "occurred_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                (
                    "actor_kind",
                    models.TextField(
                        choices=[("operator", "Operator"), ("adapter_event", "Adapter event")]
                    ),
                ),
                ("source_event_id", models.TextField(blank=True, null=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        db_column="actor_id",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="state_history_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "audit_entry",
                    models.ForeignKey(
                        blank=True,
                        db_column="audit_entry_id",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="state_history_entries",
                        to="dashboard.auditentry",
                    ),
                ),
                (
                    "lead",
                    models.ForeignKey(
                        db_column="lead_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="state_history",
                        to="dashboard.lead",
                    ),
                ),
            ],
            options={
                "db_table": "pipeline_state_history",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "to_state__in",
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
                        ),
                        name="history_to_state_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("from_state__isnull", True), ("from_state__in", ["New_Lead", "Contacted", "Replied", "Scheduled", "Quoted", "Won", "Invoiced", "Paid_Pending_Verification", "Payment_Verified", "Released", "Closed_Lost"]), _connector="OR"),
                        name="history_from_state_in_enum_or_null",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("from_state__isnull", False), ("to_state", "New_Lead"), _connector="OR"),
                        name="history_null_from_state_is_genesis",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("actor_kind__in", ["operator", "adapter_event"])),
                        name="history_actor_kind_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("actor_kind", "operator"), ("actor__isnull", False))
                        | models.Q(("actor_kind", "adapter_event"), ("actor__isnull", True), ("source_event_id__isnull", False)),
                        name="history_actor_shape_matches_kind",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.unset_or(
                            "source_event_id",
                            dashboard.models.constraints.length_between(
                                "source_event_id", 1, 128
                            ),
                        ),
                        name="history_source_event_id_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="EmailVariantAssignment",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "dimension",
                    models.TextField(
                        choices=[
                            ("subject_line", "Subject line"),
                            ("body_length", "Body length"),
                            ("cta_style", "CTA style"),
                            ("send_timing", "Send timing"),
                            ("price_anchor", "Price anchor"),
                        ]
                    ),
                ),
                (
                    "email",
                    models.ForeignKey(
                        db_column="email_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="variant_assignments",
                        to="dashboard.email",
                    ),
                ),
                (
                    "variant",
                    models.ForeignKey(
                        db_column="variant_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="email_assignments",
                        to="dashboard.variant",
                    ),
                ),
            ],
            options={
                "db_table": "email_variant_assignments",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("email", "dimension"),
                        name="email_variant_assignments_email_dimension_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "dimension__in",
                                [
                                    "subject_line",
                                    "body_length",
                                    "cta_style",
                                    "send_timing",
                                    "price_anchor",
                                ],
                            )
                        ),
                        name="email_variant_assignments_dimension_in_enum",
                    ),
                ],
            },
        ),
    ]
