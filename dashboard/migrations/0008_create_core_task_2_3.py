import uuid

import dashboard.models.constraints
import dashboard.models.fields
import django.db.models.deletion
import django.db.models.functions.datetime
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0007_alter_operator_table"),
    ]

    operations = [
        migrations.CreateModel(
            name="Contact",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("name", models.TextField(blank=True, null=True)),
                ("email", models.TextField(blank=True, null=True)),
                ("phone", models.TextField(blank=True, null=True)),
                ("title", models.TextField(blank=True, null=True)),
                (
                    "lead",
                    models.ForeignKey(
                        db_column="lead_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="contacts",
                        to="dashboard.lead",
                    ),
                ),
            ],
            options={"db_table": "contacts"},
        ),
        migrations.CreateModel(
            name="Invoice",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("invoice_number", models.TextField(unique=True)),
                ("amount", models.IntegerField()),
                (
                    "issued_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                (
                    "deal",
                    models.OneToOneField(
                        db_column="deal_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="invoice_record",
                        to="dashboard.deal",
                    ),
                ),
            ],
            options={
                "db_table": "invoices",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("amount__range", (550, 1000))),
                        name="invoices_amount_range",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="OutreachRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "channel",
                    models.TextField(choices=[("email", "Email"), ("call", "Call")]),
                ),
                (
                    "status",
                    models.TextField(
                        choices=[
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("indeterminate", "Indeterminate"),
                        ],
                        db_default="pending",
                        default="pending",
                    ),
                ),
                ("failure_reason", models.TextField(blank=True, null=True)),
                (
                    "clearance_timestamp",
                    dashboard.models.fields.MillisecondDateTimeField(),
                ),
                (
                    "reserved_at",
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
                        related_name="outreach_requests",
                        to="dashboard.lead",
                    ),
                ),
            ],
            options={
                "db_table": "outreach_requests",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("channel__in", ["email", "call"])),
                        name="outreach_requests_channel_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("status__in", ["pending", "succeeded", "failed", "indeterminate"])
                        ),
                        name="outreach_requests_status_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.unset_or(
                            "failure_reason",
                            dashboard.models.constraints.length_at_most(
                                "failure_reason", 500
                            ),
                        ),
                        name="outreach_requests_failure_reason_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("event_id", models.TextField(unique=True)),
                ("amount_usd", models.IntegerField()),
                ("paid_date", models.DateField()),
                (
                    "deal",
                    models.ForeignKey(
                        db_column="deal_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payments",
                        to="dashboard.deal",
                    ),
                ),
            ],
            options={
                "db_table": "payments",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("amount_usd__range", (1, 1000))),
                        name="payments_amount_usd_range",
                    ),
                    models.CheckConstraint(
                        condition=dashboard.models.constraints.length_between(
                            "event_id", 1, 128
                        ),
                        name="payments_event_id_length",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ReleaseAuthorization",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "authorized_at",
                    dashboard.models.fields.MillisecondDateTimeField(),
                ),
                (
                    "deal",
                    models.OneToOneField(
                        db_column="deal_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="release_authorization",
                        to="dashboard.deal",
                    ),
                ),
                (
                    "operator",
                    models.ForeignKey(
                        db_column="operator_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="release_authorizations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "release_authorizations"},
        ),
        migrations.CreateModel(
            name="SiteProject",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("preview_url", models.TextField(blank=True, null=True)),
                ("page_count", models.IntegerField(blank=True, null=True)),
                (
                    "review_state",
                    models.TextField(
                        choices=[
                            ("Generating", "Generating"),
                            ("Ready_For_Review", "Ready for review"),
                            ("Approved", "Approved"),
                            ("Rejected", "Rejected"),
                        ],
                        db_default="Generating",
                        default="Generating",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_default=django.db.models.functions.datetime.Now(),
                        default=django.utils.timezone.now,
                        editable=False,
                    ),
                ),
                ("generated_at", models.DateTimeField(blank=True, null=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("rejection_reason", models.TextField(blank=True, null=True)),
                (
                    "lead",
                    models.ForeignKey(
                        db_column="lead_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="site_projects",
                        to="dashboard.lead",
                    ),
                ),
            ],
            options={
                "db_table": "site_projects",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "review_state__in",
                                ["Generating", "Ready_For_Review", "Approved", "Rejected"],
                            )
                        ),
                        name="site_projects_review_state_in_enum",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("page_count__isnull", True), ("page_count__range", (0, 200)), _connector="OR"),
                        name="site_projects_page_count_range",
                    ),
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                ("review_state", "Rejected"),
                                ("rejection_reason__isnull", False),
                            )
                            & dashboard.models.constraints.length_between(
                                "rejection_reason", 10, 1000
                            )
                        )
                        | (
                            ~models.Q(("review_state", "Rejected"))
                            & models.Q(("rejection_reason__isnull", True))
                        ),
                        name="site_projects_rejection_reason_matches_state",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="SitePage",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("page_index", models.IntegerField()),
                ("text_content", models.TextField()),
                (
                    "site_project",
                    models.ForeignKey(
                        db_column="site_project_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pages",
                        to="dashboard.siteproject",
                    ),
                ),
            ],
            options={
                "db_table": "site_pages",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("site_project", "page_index"),
                        name="site_pages_project_page_unique",
                    )
                ],
            },
        ),
        migrations.RunSQL(
            sql="ALTER TABLE deals ADD CONSTRAINT deals_invoice_id_fk FOREIGN KEY (invoice_id) REFERENCES invoices(id) DEFERRABLE INITIALLY DEFERRED",
            reverse_sql="ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_invoice_id_fk",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE emails ADD CONSTRAINT emails_outreach_request_id_fk FOREIGN KEY (outreach_request_id) REFERENCES outreach_requests(id) DEFERRABLE INITIALLY DEFERRED",
            reverse_sql="ALTER TABLE emails DROP CONSTRAINT IF EXISTS emails_outreach_request_id_fk",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE emails ADD CONSTRAINT emails_site_project_id_fk FOREIGN KEY (site_project_id) REFERENCES site_projects(id) DEFERRABLE INITIALLY DEFERRED",
            reverse_sql="ALTER TABLE emails DROP CONSTRAINT IF EXISTS emails_site_project_id_fk",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE calls ADD CONSTRAINT calls_outreach_request_id_fk FOREIGN KEY (outreach_request_id) REFERENCES outreach_requests(id) DEFERRABLE INITIALLY DEFERRED",
            reverse_sql="ALTER TABLE calls DROP CONSTRAINT IF EXISTS calls_outreach_request_id_fk",
        ),
    ]
