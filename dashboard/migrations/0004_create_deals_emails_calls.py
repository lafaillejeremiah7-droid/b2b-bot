"""Create ``deals``, ``emails`` and ``calls`` (Requirements 13.2, 13.3, 13.4, 13.6, 13.12).

One migration, three tables. Every bound of design §4.3 arrives as a named
``CHECK`` and every uniqueness rule as a ``UNIQUE`` constraint, so the constraint
layer is deployed by the same statement that creates the table — there is no
window in which a writer meets these columns without the rules that bound them.

Two things a reviewer should look for and find:

* ``clearance_timestamp`` is ``NOT NULL`` on ``emails`` and nullable on ``calls``,
  guarded there by ``calls_clearance_required_with_reservation``. That asymmetry is
  Requirements 13.3 and 13.4, and it is the schema half of the defect fix
  described in the ``dashboard.models.outreach`` module docstring. The four
  compliance predicates of §4.6 compare this column, so its nullability is load
  bearing rather than cosmetic.
* ``deals_payment_anomaly_reason_matches_flag`` is two-way: reason present iff
  flag true. The ``payment_anomaly_reason IS NOT NULL`` term on the flagged side
  is what stops NULL propagation from admitting a flagged Deal with no reason.

Three columns carry no ``REFERENCES`` clause — ``deals.invoice_id``,
``emails.site_project_id`` and the two ``outreach_request_id`` columns — because
they point at tables task 2.3 creates. ``dashboard.models.deal``'s module
docstring records the decision and the ``SeparateDatabaseAndState`` shape task 2.3
must use to attach the constraint to the existing column instead of dropping it,
and ``dashboard/tests/test_forward_references.py`` fails the build if task 2.3
creates a referenced table without wiring the reference.

Deliberately **not** here, so a reviewer does not go looking:

* the §4.7 indexes over these tables (``idx_emails_sent``, ``idx_emails_lead_sent``,
  ``idx_calls_time_outcome``, the partial ``idx_deals_verified``) — task 2.4;
* the remaining tables of Requirement 13.5 — task 2.3;
* the enforcement layer of §4.6, including the four compliance predicates over
  ``clearance_timestamp`` and the delivery guard over ``deals.delivery_sent`` —
  tasks 3.1 to 3.4. ``scripts/check_deferred_activations.py`` text-searches this
  directory to decide when task 3.5's ``migrations`` pytest marker is overdue, so
  this file must contain no such DDL — and, since the check is a plain text
  search, must not spell one out in prose either.
"""

import dashboard.models.fields
import django.db.models.deletion
import django.db.models.functions.text
import django.db.models.lookups
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0003_create_leads'),
    ]

    operations = [
        migrations.CreateModel(
            name='Call',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('outreach_request_id', models.UUIDField(blank=True, help_text="Requirements 13.4, 5.9: UNIQUE, and UNSET for a call an Operator logged directly under Requirement 3.5 — that path submits nothing to the adapter, so it reserves nothing. Set for a call submitted through the Outreach_Controller, in which case clearance_timestamp is required too (`calls_clearance_required_with_reservation`). A UUID, declared without a REFERENCES clause because `outreach_requests` is task 2.3's table.", null=True, verbose_name='outreach request id')),
                ('attempt_number', models.SmallIntegerField(help_text="Requirement 13.4: required, an integer from 1 to 20. A STORAGE CEILING SHARED WITH EVERY WRITER, and deliberately wider in origin than Requirement 3.5's rule: 3.5 has the Deal_Room_View ASSIGN the value — 1 for a Lead's first call row, otherwise one greater than that Lead's highest existing attempt_number — rather than accept it from the Operator, and 3.5 says in terms that this is 'deliberately narrower in origin than the `calls` attempt_number storage range of 1 through 20'. Both readings are correct at once: the column bounds what any writer may store, the view decides what it stores. Task 9.2 owns the assignment and must not reconcile the two by widening the view or narrowing the column.")),
                ('timestamp', models.DateTimeField(help_text='Requirements 13.4, 3.5, 13.11: required, UTC. The submission timestamp. Read by the Requirement 2.1 activity set and the Requirement 3.3 activity feed. Like `emails.sent_at`, it is NOT the operand of any compliance predicate.')),
                ('outcome', models.TextField(choices=[('answered', 'Answered'), ('busy', 'Busy'), ('no-answer', 'No answer')], help_text='Requirements 13.4, 3.5: required, exactly one of answered, busy, or no-answer. Constrained in the database as well as in `choices` — see the CHECK in Meta.')),
                ('clearance_timestamp', dashboard.models.fields.MillisecondDateTimeField(blank=True, help_text="Requirements 13.4, 5.18: `TIMESTAMPTZ(3)`, copied unchanged from the `outreach_requests` reservation in Phase 3 and REQUIRED whenever outreach_request_id is set. NULL is permitted for exactly one shape of row — the Operator-logged call of Requirement 3.5, which has no reservation — and `calls_clearance_required_with_reservation` is what holds that exception to that one shape. Requirement 5.20 makes it strictly earlier than the Lead's do_not_call_at whenever both are set, and note that 5.20 is scoped to 'call rows that carry a Clearance_Timestamp', so the Operator-logged row is outside it: Requirement 5.4's do-not-call block is what governs that path, checked before the row is written.", null=True, verbose_name='Clearance_Timestamp')),
                ('late_opt_out_marker', models.BooleanField(db_default=False, default=False, help_text="Requirements 13.4, 5.22: required, default false. True on a row whose Lead's do_not_call_at was set after this row's clearance_timestamp and before the row was written — the call had already been placed, so the row is recorded and marked, and Operators are notified within 60 seconds. Same shape as `emails.late_opt_out_marker`, for the same reason.")),
                ('notes', models.TextField(blank=True, help_text="Requirement 13.4: up to 5,000 characters, or unset. A STORAGE CEILING SHARED WITH EVERY WRITER, deliberately WIDER than Requirement 3.5's Deal_Room_View input limit of 2,000 characters — 3.5 states the contrast itself, and Requirement 3.9 rejects an Operator submission over 2,000. So a 3,000-character row is storable and is not submittable through the view, and that is the specified behaviour rather than an inconsistency. Task 9.2 owns the 2,000-character form rule and must not 'fix' this ceiling to match it.", null=True)),
                ('lead', models.ForeignKey(db_column='lead_id', help_text="Requirements 13.4, 13.5: required, and a real REFERENCES (Requirement 13.9). Requirement 13.4 does not restate 'required' the way 13.3 does for `emails.lead_id`, but 13.5 makes every lead_id reference resolve to an existing Lead and Requirement 3.5 stores the lead_id on every submitted call row; a call belonging to no Lead is not a record of anything. on_delete=PROTECT, as for `emails`.", on_delete=django.db.models.deletion.PROTECT, related_name='calls', to='dashboard.lead')),
            ],
            options={
                'verbose_name': 'call',
                'verbose_name_plural': 'calls',
                'db_table': 'calls',
                'constraints': [models.CheckConstraint(condition=models.Q(('attempt_number__range', (1, 20))), name='calls_attempt_number_range', violation_error_message='attempt_number is an integer from 1 to 20 (Requirement 13.4).'), models.CheckConstraint(condition=models.Q(('outcome__in', ['answered', 'busy', 'no-answer'])), name='calls_outcome_in_enum', violation_error_message='outcome must be exactly one of answered, busy, or no-answer (Requirements 13.4, 3.5).'), models.CheckConstraint(condition=models.Q(('notes__isnull', True), django.db.models.lookups.LessThanOrEqual(django.db.models.functions.text.Length('notes'), 5000), _connector='OR'), name='calls_notes_length', violation_error_message='notes holds at most 5,000 characters or is unset (Requirement 13.4).'), models.CheckConstraint(condition=models.Q(('outreach_request_id__isnull', True), ('clearance_timestamp__isnull', False), _connector='OR'), name='calls_clearance_required_with_reservation', violation_error_message='clearance_timestamp is required for a call row carrying an outreach_request_id, and may be unset only for a call logged directly by an Operator (Requirements 13.4, 5.18, 3.5).'), models.UniqueConstraint(fields=('outreach_request_id',), name='calls_outreach_request_id_unique', violation_error_message='a call row already exists for this outreach_request_id (Requirements 5.10, 5.12).')],
            },
        ),
        migrations.CreateModel(
            name='Deal',
            fields=[
                ('deal_id', models.BigAutoField(primary_key=True, serialize=False)),
                ('agreed_price', models.IntegerField(blank=True, help_text="Requirements 13.2, 7.6: a whole US dollar amount from 550 to 1000, or unset. OPERATOR-SET ONLY. Requirement 7.8 states this as an invariant over stored records — every agreed_price was set by an Operator submission, and no Suggested_Price computation writes this field. Requirement 7.13 additionally forbids copying the Lead's preferred_price into it. Requirement 7.11 makes it immutable once an invoice exists, enforced by task 3.3's trigger.", null=True)),
                ('quote_sent_date', models.DateTimeField(blank=True, help_text="Requirement 13.2: unset until the quote action records it. Design §4.1 declares this `timestamptz` despite the column's name, so Requirement 13.11 applies: UTC, one second or finer.", null=True)),
                ('invoice_id', models.BigIntegerField(blank=True, help_text="Requirement 13.2: unset until Requirement 8.1's create-invoice action records it. Declared as a plain bigint because `invoices` is task 2.3's table — task 2.3 attaches the REFERENCES to this column rather than replacing it (see the module docstring). Deliberately NOT unique: §4.3 places the at-most-one-invoice-per-Deal rule on `invoices.deal_id UNIQUE`, and declaring the mirror image here as well would be a second constraint for one rule, reported under a name Requirement 8.2's message does not expect.", null=True)),
                ('payment_received', models.BooleanField(blank=True, help_text="Requirements 13.2, 8.3: unset until a payment event records it, and then set UNCONDITIONALLY — irrespective of the Lead's Pipeline_State and irrespective of whether an invoice exists (§3.7.6: money that has arrived is a fact about the world). NULLABLE WITH NO DEFAULT, which is a read of the criteria rather than an oversight: Requirement 13.6 spells out 'a required boolean defaulting to false' for `manual_review_flag` and `payment_anomaly_flag` and withholds it here, while 13.2 lists this column among those 'unset until the corresponding action in Requirement 8 records them'. So NULL is unset. Because false is also storable, the set-condition is `payment_received IS TRUE` and not `IS NOT NULL`; every reader must use that spelling.", null=True)),
                ('paid_date', models.DateField(blank=True, help_text="Requirements 13.2, 8.3: the payment date, unset until a payment event records it. A `date`, not a timestamp — design §4.1 declares it `date paid_date` while declaring `quote_sent_date` and `delivered_date` beside it as `timestamptz`, so the contrast is the design's and Requirement 13.11's UTC-timestamp rule does not reach this column. The payment *instant* lives on the `payments` record (task 2.3).", null=True)),
                ('payment_verified_at', dashboard.models.fields.MillisecondDateTimeField(blank=True, help_text="Requirements 13.2, 8.5, 8.17: `TIMESTAMPTZ(3)` — millisecond precision, per Requirements 8.5 and 8.8 and design §4.3. THE AUTHORITATIVE RECORD that the payment was verified (Requirement 8.17), and the field the Payment_Verified_Flag reads: the flag is set for exactly those Deals whose value here is set. Requirement 8.20 has the Release_Gate evaluate its precondition by reading this flag RATHER THAN the Deal's Pipeline_State, so a Lead sitting at Payment_Verified with this column NULL is still refused release. Written with the Payment_Verified transition in one transaction (Requirement 8.18) and never later than the Release_Authorization's authorized_at (Requirement 8.11). Unset until Requirement 8.5's Verify Payment action records it; task 13.x owns that write.", null=True, verbose_name='payment verification timestamp')),
                ('delivery_sent', models.BooleanField(blank=True, help_text="Requirements 13.2, 8.15: unset until the Pipeline_Adapter returns success for a delivery request submitted under a Release_Authorization. Nullable with no default, on the same reading as `payment_received`, and with the same consequence: Requirements 8.9, 8.11, 8.12 and 8.16 all turn on 'delivery_sent set', and since both NULL and false are storable that predicate is `delivery_sent IS TRUE`. Requirement 8.12's count — zero Deals with this set absent an accepted Approve Release — is enforced by task 3.3's §4.6 trigger, not by this column.", null=True)),
                ('delivered_date', models.DateTimeField(blank=True, help_text="Requirements 13.2, 8.15: unset until delivery succeeds. `timestamptz` per §4.1 despite the name; Requirement 8.11 orders it at or after the Release_Authorization's authorized_at.", null=True)),
                ('payment_anomaly_flag', models.BooleanField(db_default=False, default=False, help_text="Requirements 13.2, 13.6, 8.21: required, default false. Set when a payment is recorded that cannot be accompanied by the Paid_Pending_Verification transition — either the Lead's state forms no Legal_Transition to it, or the Deal has no invoice. Requirement 8.22 clears it ONLY through an explicit Operator-confirmed clear-payment-anomaly action by an Agent or Admin, with an Audit_Entry: no Pipeline_Adapter event and no Pipeline_State change clears it, so a later legal transition cannot erase the record that a human still needs to look at this Deal.")),
                ('payment_anomaly_reason', models.TextField(blank=True, help_text="Requirements 13.2, 13.6, 8.21: 1 to 500 characters while payment_anomaly_flag is true, and unset while it is false — a two-way CHECK, see Meta. Names which of the two anomaly conditions applied. Requirement 8.22 displays it beside the indicator and records it as the Audit_Entry's before_value when the flag is cleared, which is why clearing must null this column in the same statement that clears the flag.", null=True)),
                ('lead', models.OneToOneField(db_column='lead_id', help_text="Requirements 13.2, 13.12: required, and UNIQUE — at most one Deal per Lead. The uniqueness is a database constraint, not a service convention, so two concurrent create-Deal transactions cannot both commit (task 2.5's Property 42 asserts exactly that on separate connections).", on_delete=django.db.models.deletion.PROTECT, related_name='deal', to='dashboard.lead')),
                ('verified_by_operator', models.ForeignKey(blank=True, db_column='verified_by_operator_id', help_text='Requirements 13.2, 8.5: the verifying Operator, identifying an existing Operator account when set (a real REFERENCES, since `operators` exists as of task 1.3). on_delete=PROTECT: the verification record names a human, and Requirement 8.17 calls it authoritative, so an Operator who has verified a payment cannot be deleted out from under it — Requirement 1.9 deactivates accounts rather than deleting them.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='verified_deals', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'deal',
                'verbose_name_plural': 'deals',
                'db_table': 'deals',
                'constraints': [models.CheckConstraint(condition=models.Q(('agreed_price__isnull', True), ('agreed_price__range', (550, 1000)), _connector='OR'), name='deals_agreed_price_range', violation_error_message='agreed_price is a whole US dollar amount from 550 to 1000 or is unset (Requirements 13.2, 7.6).'), models.CheckConstraint(condition=models.Q(models.Q(('payment_anomaly_flag', True), ('payment_anomaly_reason__isnull', False), django.db.models.lookups.GreaterThanOrEqual(django.db.models.functions.text.Length('payment_anomaly_reason'), 1), django.db.models.lookups.LessThanOrEqual(django.db.models.functions.text.Length('payment_anomaly_reason'), 500)), models.Q(('payment_anomaly_flag', False), ('payment_anomaly_reason__isnull', True)), _connector='OR'), name='deals_payment_anomaly_reason_matches_flag', violation_error_message='payment_anomaly_reason holds 1 to 500 characters while payment_anomaly_flag is true and is unset while it is false (Requirements 13.6, 8.21).')],
            },
        ),
        migrations.CreateModel(
            name='Email',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('outreach_request_id', models.UUIDField(help_text="Requirements 13.3, 5.9: required and UNIQUE. The value generated once per Operator-confirmed action before the first submission attempt and reused unchanged on every retry, which is what makes a retry idempotent (Requirement 5.10 discards a submission whose id already has a row and displays the existing record). A UUID because §4.1 declares `outreach_requests.id` as `uuid`. Declared without a REFERENCES clause because `outreach_requests` is task 2.3's table — see the `dashboard.models.deal` module docstring.", verbose_name='outreach request id')),
                ('subject', models.TextField(help_text='Requirements 13.3, 5.1: required, 1 to 200 characters. Storage bound and input bound agree here, unlike `body`.')),
                ('body', models.TextField(help_text="Requirement 13.3: required, 1 to 50,000 characters. A STORAGE CEILING SHARED WITH EVERY WRITER, deliberately five times Requirement 5.1's composed-message limit of 1 to 10,000 characters. The two numbers are not in conflict and the wider one is not a bug: 13.3 bounds what the shared-schema table will hold for any writer, including a bot-originated send arriving as an adapter event, while 5.1 bounds what the Outreach_Controller will submit. Whoever notices the mismatch should widen neither and narrow neither.")),
                ('site_project_id', models.BigIntegerField(blank=True, help_text="Design §3.8: set whenever the body contains that Site_Project's preview_url, so Requirement 6.7 — every email carrying a preview URL references a Site_Project that was Approved — can be a database invariant rather than a service-layer hope. Task 3.2's trigger compares the referenced Site_Project's approved_at against this row's clearance_timestamp (NOT its sent_at: §3.8 explains that the sent_at form of the predicate has the same destroys-a-sent-record defect the clearance model removed). Nullable: most emails carry no preview link. A plain bigint because `site_projects` is task 2.3's table.", null=True)),
                ('clearance_timestamp', dashboard.models.fields.MillisecondDateTimeField(help_text="Requirements 13.3, 5.18: REQUIRED, `TIMESTAMPTZ(3)`. The instant the Compliance_Guard found no blocking condition, recorded on the `outreach_requests` reservation in Phase 1 — before the adapter is called — and COPIED here unchanged in Phase 3. Never re-derived from the clock at record time: §3.6.4 is explicit that Phase 3 does not consult the clock, and a retry reuses both the id and this value. Requirement 5.19 makes it strictly earlier than the Lead's unsubscribed_at whenever that is set, and task 3.2's trigger compares THIS column rather than sent_at, which is the whole reason the trigger cannot reject a row the adapter has already sent.", verbose_name='Clearance_Timestamp')),
                ('late_opt_out_marker', models.BooleanField(db_default=False, default=False, help_text="Requirements 13.3, 5.21: required, default false. True on a row whose Lead's unsubscribed_at was set AFTER this row's clearance_timestamp and before the row was written — the message had already left, so the row is recorded and marked rather than lost, and the Notification_Service tells Operators within 60 seconds. The marker is the distinction the requirements care about: a marked row was sent before the opt-out was processed and is compliant; an unmarked row was sent while the Lead was cleared. Neither is a row that quietly disappeared.")),
                ('sent_at', models.DateTimeField(help_text="Requirements 13.3, 13.11: required, UTC. The Phase 3 record time. Read by the Requirement 2.1 activity timestamp set, the Requirement 3.3 activity feed, and Requirement 5.8's greatest-sent_at attribution rule. Deliberately NOT the operand of any compliance predicate — that is clearance_timestamp's job, and the module docstring says why.")),
                ('opened_at', models.DateTimeField(blank=True, help_text='Requirement 13.3: unset until the email-opened event is processed (task 7.3). UTC.', null=True)),
                ('clicked_at', models.DateTimeField(blank=True, help_text='Requirement 13.3: unset until the email-clicked event is processed (task 7.3). UTC.', null=True)),
                ('reply_at', models.DateTimeField(blank=True, help_text='Requirement 13.3: unset until the prospect-replied event is processed (task 7.3). UTC.', null=True)),
                ('unsubscribed', models.BooleanField(db_default=False, default=False, help_text="Requirements 13.3, 5.8: required, default false. Row-level opt-out, distinct from the Lead-level `leads.unsubscribed_at`: the event sets this on the row named by the event's email identifier when it carries one, otherwise on that Lead's row with the greatest sent_at, otherwise on no row at all (Requirement 5.23, for a Lead with no email rows). It is also the numerator of Requirement 10.3's unsubscribe rate. THE WRITER IS TASK 7.3's EVENT HANDLER AND NOTHING ELSE — stated explicitly because the audit found this column with a reader and no writer, which made the 10.3 metric permanently zero while looking implemented. If task 7.3's unsubscribe handler does not set this, the metric is still broken.")),
                ('lead', models.ForeignKey(db_column='lead_id', help_text="Requirements 13.3, 13.5: required, and a real REFERENCES so an unresolvable lead_id is rejected by the database (Requirement 13.9). on_delete=PROTECT: an email row is a compliance record of a message sent to a real business, and Requirement 5.11's count over a Lead's rows is not computable if deleting the Lead silently empties it.", on_delete=django.db.models.deletion.PROTECT, related_name='emails', to='dashboard.lead')),
            ],
            options={
                'verbose_name': 'email',
                'verbose_name_plural': 'emails',
                'db_table': 'emails',
                'constraints': [models.CheckConstraint(condition=models.Q(django.db.models.lookups.GreaterThanOrEqual(django.db.models.functions.text.Length('subject'), 1), django.db.models.lookups.LessThanOrEqual(django.db.models.functions.text.Length('subject'), 200)), name='emails_subject_length', violation_error_message='subject is required and holds 1 to 200 characters (Requirements 13.3, 5.1).'), models.CheckConstraint(condition=models.Q(django.db.models.lookups.GreaterThanOrEqual(django.db.models.functions.text.Length('body'), 1), django.db.models.lookups.LessThanOrEqual(django.db.models.functions.text.Length('body'), 50000)), name='emails_body_length', violation_error_message='body is required and holds 1 to 50,000 characters (Requirement 13.3).'), models.UniqueConstraint(fields=('outreach_request_id',), name='emails_outreach_request_id_unique', violation_error_message='an email row already exists for this outreach_request_id (Requirements 5.10, 5.12).')],
            },
        ),
    ]
