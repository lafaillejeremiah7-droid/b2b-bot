"""Create the ``operators`` table — the AUTH_USER_MODEL (Requirements 1.5, 9.5, 9.6).

The first migration in the project that defines a model, and therefore the one
that resolves the ``AUTH_USER_MODEL`` swappable dependency. It has to come
before every other model migration: task 1.1's ``0001`` creates the ``pg_trgm``
extension with no model state attached, so nothing had resolved the swappable
reference yet, and tasks 2.1–2.3's tables reference ``operators`` from
``deals.verified_by_operator_id``, ``release_authorizations.operator_id``, and
``audit_entries.actor_id``.

The three-value role constraint and the ``Viewer`` column default are both
declared here, in the database, so they hold for every writer of this schema and
not only for code going through the ORM.
"""

import django.db.models.functions.datetime
import django.db.models.functions.text
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('dashboard', '0001_enable_pg_trgm'),
    ]

    operations = [
        migrations.CreateModel(
            name='Operator',
            fields=[
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('email', models.EmailField(help_text='The sign-in identifier (Requirement 1.2) and the address the email notification channel delivers to (Requirement 9.6). Stored trimmed and lowercased.', max_length=320, unique=True, verbose_name='registered email address')),
                ('role', models.CharField(choices=[('Viewer', 'Viewer'), ('Agent', 'Agent'), ('Admin', 'Admin')], db_default='Viewer', default='Viewer', help_text='Requirement 1.5: exactly one of Viewer, Agent, or Admin.', max_length=16)),
                ('slack_webhook_url', models.URLField(blank=True, help_text='Requirement 9.5: optional. NULL means no Slack target is recorded, which is the condition Requirement 9.12 (task 16.1) tests before letting Slack delivery be enabled.', max_length=2048, null=True, verbose_name='Slack webhook target')),
                ('is_active', models.BooleanField(db_default=True, default=True, help_text='A deactivated account cannot establish a session. Account management is the Admin-only action of Requirement 1.9.')),
                ('created_at', models.DateTimeField(db_default=django.db.models.functions.datetime.Now(), default=django.utils.timezone.now, editable=False, help_text='UTC, per Requirement 13.11.')),
            ],
            options={
                'verbose_name': 'operator',
                'verbose_name_plural': 'operators',
                'db_table': 'operators',
                'constraints': [models.CheckConstraint(condition=models.Q(('role__in', ['Viewer', 'Agent', 'Admin'])), name='operators_role_in_enum', violation_error_message='role must be exactly one of Viewer, Agent, or Admin (Requirement 1.5).'), models.CheckConstraint(condition=models.Q(('email', django.db.models.functions.text.Lower(django.db.models.functions.text.Trim('email')))), name='operators_email_normalized', violation_error_message='the registered email address must be stored trimmed and lowercased.'), models.CheckConstraint(condition=models.Q(('email__gt', '')), name='operators_email_present', violation_error_message='a registered email address is required (Requirement 9.6).'), models.CheckConstraint(condition=models.Q(('slack_webhook_url__isnull', True), ('slack_webhook_url__gt', ''), _connector='OR'), name='operators_slack_webhook_null_or_present', violation_error_message='the Slack webhook target is either unset or a non-empty target; the empty string is not a recorded target (Requirement 9.5).')],
            },
        ),
    ]
