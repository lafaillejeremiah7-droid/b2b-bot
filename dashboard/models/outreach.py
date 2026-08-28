"""The ``emails`` and ``calls`` tables — the two recorded outreach row types.

Requirements 13.3 and 13.4 declare them separately, and this module holds them
together for one reason: they are the two halves of a single rule. Requirement
5.12 bounds the count of ``emails`` rows **plus** ``calls`` rows carrying a given
``outreach_request_id`` at one, and Requirements 5.18 through 5.22 give both the
same Clearance_Timestamp / Late_Opt_Out_Marker pair. Splitting them into two
modules would put one rule in two places and invite the two column sets to drift.

THE CLEARANCE COLUMNS ARE A DEFECT FIX. GET THEIR NULLABILITY RIGHT.
--------------------------------------------------------------------
The earlier version of this schema had the compliance trigger compare the row's
``sent_at`` against the Lead's ``unsubscribed_at`` on ``BEFORE INSERT``. That is
a predicate over a value assigned in Phase 3 of §3.6.4's sequence — *after* the
adapter has already returned success and the message has already left. If an
opt-out landed in the window between the adapter succeeding and the row being
written, the trigger rejected the insert, Phase 3 rolled back, and the record of
an email that had genuinely been sent was destroyed. The dashboard's own audit
trail then said no email existed, which is worse than a compliance breach: it is
a compliance breach the operator cannot see.

Requirements 5.18 to 5.22 replace that with a Clearance_Timestamp recorded on the
``outreach_requests`` reservation **before** the adapter is called (Phase 1) and
copied unchanged onto this row (Phase 3). Every compliance predicate is then a
function of pre-submission values only, so no predicate can newly become false
after the send. Design §3.6.4 has the full sequence and the reasoning.

What that costs this task is exactness about nullability, because task 3.2's
triggers compare this column and a column that is nullable when it should not be
silently disables the comparison rather than failing it:

* ``emails.clearance_timestamp`` is **NOT NULL**. Requirement 13.3 lists it among
  the four required ``emails`` columns. Every email row has a reservation behind
  it, so there is no email row with nothing to compare.
* ``calls.clearance_timestamp`` is **nullable, and only just**: Requirement 13.4
  makes it required "for a call row carrying an outreach_request_id", and the one
  row permitted to carry neither is the call an Operator logged directly through
  the Deal_Room_View under Requirement 3.5 — which reserves nothing because it
  submits nothing to the adapter. ``calls_clearance_required_with_reservation``
  is what keeps that exception exactly that narrow.

Scope boundaries, stated so the next tasks do not find their work half-done:

* ``outreach_request_id`` and ``site_project_id`` carry no ``REFERENCES`` yet —
  ``outreach_requests`` and ``site_projects`` are task 2.3's tables. The reasoning
  and the mechanism task 2.3 must use are in the ``dashboard.models.deal`` module
  docstring; the four columns are listed in
  ``dashboard.models.forward_references``.
* No index: ``idx_emails_sent``, ``idx_emails_lead_sent`` and
  ``idx_calls_time_outcome`` are **task 2.4's**.
* No trigger: the four compliance triggers of §4.6 — the two clearance
  comparisons, Requirement 5.12's cross-table channel check, and Requirement
  6.7's preview-link assertion over ``site_project_id`` — are **task 3.2's**.
  Nothing in this module blocks an insert for an opted-out Lead; the triggers are
  what will, and they can only do it because these columns exist.
* No writer. ``Email.unsubscribed`` in particular has no writer here — see its
  ``help_text``.
"""

from __future__ import annotations

from django.db import models

from dashboard.models.constraints import length_at_most, length_between, unset_or
from dashboard.models.fields import MillisecondDateTimeField


class CallOutcome(models.TextChoices):
    """The three call outcomes of Requirements 13.4 and 3.5.

    Declared as :class:`~dashboard.models.operator.Role` and
    :class:`~dashboard.models.lead.PipelineState` are: the stored values are the
    requirement's own spellings — note ``no-answer`` with a hyphen, which is how
    both criteria write it — so the database ``CHECK`` reads against the
    requirement text without a translation table.
    """

    ANSWERED = "answered", "Answered"
    BUSY = "busy", "Busy"
    NO_ANSWER = "no-answer", "No answer"


class Email(models.Model):
    """One recorded prospect email (Requirements 13.3, 5.1, 5.18, 5.21).

    A row exists here **only** because the Pipeline_Adapter returned success for
    a cleared submission (Requirement 5.1, §3.6.4 Phase 3). A failed submission
    records no row — it records a ``failed`` status on the reservation — so this
    table is the set of messages that were actually sent, and nothing weaker.
    Requirement 5.21 is the sharp edge of that: a late opt-out marks the row, it
    never removes it.
    """

    id = models.BigAutoField(primary_key=True)

    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="emails",
        db_column="lead_id",
        help_text=(
            "Requirements 13.3, 13.5: required, and a real REFERENCES so an "
            "unresolvable lead_id is rejected by the database (Requirement 13.9). "
            "on_delete=PROTECT: an email row is a compliance record of a message "
            "sent to a real business, and Requirement 5.11's count over a Lead's "
            "rows is not computable if deleting the Lead silently empties it."
        ),
    )

    # --- The reservation link: a forward reference, see the module note ----

    outreach_request_id = models.UUIDField(
        verbose_name="outreach request id",
        help_text=(
            "Requirements 13.3, 5.9: required and UNIQUE. The value generated "
            "once per Operator-confirmed action before the first submission "
            "attempt and reused unchanged on every retry, which is what makes a "
            "retry idempotent (Requirement 5.10 discards a submission whose id "
            "already has a row and displays the existing record). A UUID because "
            "§4.1 declares `outreach_requests.id` as `uuid`. Declared without a "
            "REFERENCES clause because `outreach_requests` is task 2.3's table — "
            "see the `dashboard.models.deal` module docstring."
        ),
    )

    # --- Message content --------------------------------------------------

    subject = models.TextField(
        help_text=(
            "Requirements 13.3, 5.1: required, 1 to 200 characters. Storage "
            "bound and input bound agree here, unlike `body`."
        ),
    )

    body = models.TextField(
        help_text=(
            "Requirement 13.3: required, 1 to 50,000 characters. A STORAGE "
            "CEILING SHARED WITH EVERY WRITER, deliberately five times "
            "Requirement 5.1's composed-message limit of 1 to 10,000 characters. "
            "The two numbers are not in conflict and the wider one is not a bug: "
            "13.3 bounds what the shared-schema table will hold for any writer, "
            "including a bot-originated send arriving as an adapter event, while "
            "5.1 bounds what the Outreach_Controller will submit. Whoever "
            "notices the mismatch should widen neither and narrow neither."
        ),
    )

    site_project_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Design §3.8: set whenever the body contains that Site_Project's "
            "preview_url, so Requirement 6.7 — every email carrying a preview URL "
            "references a Site_Project that was Approved — can be a database "
            "invariant rather than a service-layer hope. Task 3.2's trigger "
            "compares the referenced Site_Project's approved_at against this "
            "row's clearance_timestamp (NOT its sent_at: §3.8 explains that the "
            "sent_at form of the predicate has the same destroys-a-sent-record "
            "defect the clearance model removed). Nullable: most emails carry no "
            "preview link. A plain bigint because `site_projects` is task 2.3's "
            "table."
        ),
    )

    # --- The clearance pair (Requirements 5.18, 5.21) ----------------------

    clearance_timestamp = MillisecondDateTimeField(
        verbose_name="Clearance_Timestamp",
        help_text=(
            "Requirements 13.3, 5.18: REQUIRED, `TIMESTAMPTZ(3)`. The instant the "
            "Compliance_Guard found no blocking condition, recorded on the "
            "`outreach_requests` reservation in Phase 1 — before the adapter is "
            "called — and COPIED here unchanged in Phase 3. Never re-derived from "
            "the clock at record time: §3.6.4 is explicit that Phase 3 does not "
            "consult the clock, and a retry reuses both the id and this value. "
            "Requirement 5.19 makes it strictly earlier than the Lead's "
            "unsubscribed_at whenever that is set, and task 3.2's trigger "
            "compares THIS column rather than sent_at, which is the whole reason "
            "the trigger cannot reject a row the adapter has already sent."
        ),
    )

    late_opt_out_marker = models.BooleanField(
        default=False,
        db_default=False,
        help_text=(
            "Requirements 13.3, 5.21: required, default false. True on a row "
            "whose Lead's unsubscribed_at was set AFTER this row's "
            "clearance_timestamp and before the row was written — the message had "
            "already left, so the row is recorded and marked rather than lost, "
            "and the Notification_Service tells Operators within 60 seconds. The "
            "marker is the distinction the requirements care about: a marked row "
            "was sent before the opt-out was processed and is compliant; an "
            "unmarked row was sent while the Lead was cleared. Neither is a row "
            "that quietly disappeared."
        ),
    )

    # --- Event-driven engagement columns (Requirement 13.3) ----------------

    sent_at = models.DateTimeField(
        help_text=(
            "Requirements 13.3, 13.11: required, UTC. The Phase 3 record time. "
            "Read by the Requirement 2.1 activity timestamp set, the Requirement "
            "3.3 activity feed, and Requirement 5.8's greatest-sent_at "
            "attribution rule. Deliberately NOT the operand of any compliance "
            "predicate — that is clearance_timestamp's job, and the module "
            "docstring says why."
        ),
    )

    opened_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.3: unset until the email-opened event is processed "
            "(task 7.3). UTC."
        ),
    )

    clicked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.3: unset until the email-clicked event is processed "
            "(task 7.3). UTC."
        ),
    )

    reply_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.3: unset until the prospect-replied event is "
            "processed (task 7.3). UTC."
        ),
    )

    unsubscribed = models.BooleanField(
        default=False,
        db_default=False,
        help_text=(
            "Requirements 13.3, 5.8: required, default false. Row-level opt-out, "
            "distinct from the Lead-level `leads.unsubscribed_at`: the event sets "
            "this on the row named by the event's email identifier when it carries "
            "one, otherwise on that Lead's row with the greatest sent_at, "
            "otherwise on no row at all (Requirement 5.23, for a Lead with no "
            "email rows). It is also the numerator of Requirement 10.3's "
            "unsubscribe rate. THE WRITER IS TASK 7.3's EVENT HANDLER AND NOTHING "
            "ELSE — stated explicitly because the audit found this column with a "
            "reader and no writer, which made the 10.3 metric permanently zero "
            "while looking implemented. If task 7.3's unsubscribe handler does not "
            "set this, the metric is still broken."
        ),
    )

    class Meta:
        db_table = "emails"
        verbose_name = "email"
        verbose_name_plural = "emails"
        constraints = [
            models.CheckConstraint(
                condition=length_between("subject", 1, 200),
                name="emails_subject_length",
                violation_error_message=(
                    "subject is required and holds 1 to 200 characters "
                    "(Requirements 13.3, 5.1)."
                ),
            ),
            models.CheckConstraint(
                condition=length_between("body", 1, 50000),
                name="emails_body_length",
                violation_error_message=(
                    "body is required and holds 1 to 50,000 characters "
                    "(Requirement 13.3)."
                ),
            ),
            # Requirement 5.12's `emails` half. Declared as an explicit
            # UniqueConstraint rather than `unique=True` on the field so that the
            # name and the message are this task's, not PostgreSQL's: Requirement
            # 5.10 catches exactly this violation and has to display the existing
            # record, so the handler needs a stable name to key on. The other
            # half is `calls_outreach_request_id_unique`; the cross-table count
            # 5.12 actually states cannot be an index and is task 3.2's trigger,
            # which rejects a row whose channel disagrees with the reservation.
            models.UniqueConstraint(
                fields=["outreach_request_id"],
                name="emails_outreach_request_id_unique",
                violation_error_message=(
                    "an email row already exists for this outreach_request_id "
                    "(Requirements 5.10, 5.12)."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"Email {self.id} to lead {self.lead_id}: {self.subject[:40]}"

    @property
    def was_sent_before_a_late_opt_out(self) -> bool:
        """Requirement 5.21's marked condition, as one spelling."""
        return self.late_opt_out_marker


class Call(models.Model):
    """One recorded outbound call (Requirements 13.4, 3.5, 5.18, 5.22).

    Two writers, and the difference between them is the only reason
    ``clearance_timestamp`` is nullable at all:

    1. the Outreach_Controller, submitting through the adapter under a
       reservation — such a row carries both an ``outreach_request_id`` and the
       ``clearance_timestamp`` copied from it;
    2. an Operator logging a call directly in the Deal_Room_View under
       Requirement 3.5 — no adapter submission, therefore no reservation,
       therefore nothing to copy.

    ``calls_clearance_required_with_reservation`` is the database's statement that
    those are the only two shapes: a row with a reservation and no clearance is
    unstorable.
    """

    id = models.BigAutoField(primary_key=True)

    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="calls",
        db_column="lead_id",
        help_text=(
            "Requirements 13.4, 13.5: required, and a real REFERENCES "
            "(Requirement 13.9). Requirement 13.4 does not restate 'required' the "
            "way 13.3 does for `emails.lead_id`, but 13.5 makes every lead_id "
            "reference resolve to an existing Lead and Requirement 3.5 stores the "
            "lead_id on every submitted call row; a call belonging to no Lead is "
            "not a record of anything. on_delete=PROTECT, as for `emails`."
        ),
    )

    outreach_request_id = models.UUIDField(
        null=True,
        blank=True,
        verbose_name="outreach request id",
        help_text=(
            "Requirements 13.4, 5.9: UNIQUE, and UNSET for a call an Operator "
            "logged directly under Requirement 3.5 — that path submits nothing to "
            "the adapter, so it reserves nothing. Set for a call submitted through "
            "the Outreach_Controller, in which case clearance_timestamp is "
            "required too (`calls_clearance_required_with_reservation`). A UUID, "
            "declared without a REFERENCES clause because `outreach_requests` is "
            "task 2.3's table."
        ),
    )

    attempt_number = models.SmallIntegerField(
        help_text=(
            "Requirement 13.4: required, an integer from 1 to 20. A STORAGE "
            "CEILING SHARED WITH EVERY WRITER, and deliberately wider in origin "
            "than Requirement 3.5's rule: 3.5 has the Deal_Room_View ASSIGN the "
            "value — 1 for a Lead's first call row, otherwise one greater than "
            "that Lead's highest existing attempt_number — rather than accept it "
            "from the Operator, and 3.5 says in terms that this is 'deliberately "
            "narrower in origin than the `calls` attempt_number storage range of "
            "1 through 20'. Both readings are correct at once: the column bounds "
            "what any writer may store, the view decides what it stores. Task "
            "9.2 owns the assignment and must not reconcile the two by widening "
            "the view or narrowing the column."
        ),
    )

    timestamp = models.DateTimeField(
        help_text=(
            "Requirements 13.4, 3.5, 13.11: required, UTC. The submission "
            "timestamp. Read by the Requirement 2.1 activity set and the "
            "Requirement 3.3 activity feed. Like `emails.sent_at`, it is NOT the "
            "operand of any compliance predicate."
        ),
    )

    outcome = models.TextField(
        choices=CallOutcome.choices,
        help_text=(
            "Requirements 13.4, 3.5: required, exactly one of answered, busy, or "
            "no-answer. Constrained in the database as well as in `choices` — see "
            "the CHECK in Meta."
        ),
    )

    # --- The clearance pair (Requirements 5.18, 5.22) ---------------------

    clearance_timestamp = MillisecondDateTimeField(
        null=True,
        blank=True,
        verbose_name="Clearance_Timestamp",
        help_text=(
            "Requirements 13.4, 5.18: `TIMESTAMPTZ(3)`, copied unchanged from the "
            "`outreach_requests` reservation in Phase 3 and REQUIRED whenever "
            "outreach_request_id is set. NULL is permitted for exactly one shape "
            "of row — the Operator-logged call of Requirement 3.5, which has no "
            "reservation — and `calls_clearance_required_with_reservation` is what "
            "holds that exception to that one shape. Requirement 5.20 makes it "
            "strictly earlier than the Lead's do_not_call_at whenever both are "
            "set, and note that 5.20 is scoped to 'call rows that carry a "
            "Clearance_Timestamp', so the Operator-logged row is outside it: "
            "Requirement 5.4's do-not-call block is what governs that path, "
            "checked before the row is written."
        ),
    )

    late_opt_out_marker = models.BooleanField(
        default=False,
        db_default=False,
        help_text=(
            "Requirements 13.4, 5.22: required, default false. True on a row whose "
            "Lead's do_not_call_at was set after this row's clearance_timestamp "
            "and before the row was written — the call had already been placed, so "
            "the row is recorded and marked, and Operators are notified within 60 "
            "seconds. Same shape as `emails.late_opt_out_marker`, for the same "
            "reason."
        ),
    )

    notes = models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.4: up to 5,000 characters, or unset. A STORAGE "
            "CEILING SHARED WITH EVERY WRITER, deliberately WIDER than "
            "Requirement 3.5's Deal_Room_View input limit of 2,000 characters — "
            "3.5 states the contrast itself, and Requirement 3.9 rejects an "
            "Operator submission over 2,000. So a 3,000-character row is storable "
            "and is not submittable through the view, and that is the specified "
            "behaviour rather than an inconsistency. Task 9.2 owns the 2,000-"
            "character form rule and must not 'fix' this ceiling to match it."
        ),
    )

    class Meta:
        db_table = "calls"
        verbose_name = "call"
        verbose_name_plural = "calls"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(attempt_number__range=(1, 20)),
                name="calls_attempt_number_range",
                violation_error_message=(
                    "attempt_number is an integer from 1 to 20 "
                    "(Requirement 13.4)."
                ),
            ),
            # In the database, not only in `choices`. `choices` is a
            # form-validation hint that a raw INSERT, a data migration, or the
            # bot's own connection never consults, and §4.2 lists `calls` as a
            # table the bot writes. A fourth outcome is unstorable, not merely
            # unvalidated.
            models.CheckConstraint(
                condition=models.Q(outcome__in=CallOutcome.values),
                name="calls_outcome_in_enum",
                violation_error_message=(
                    "outcome must be exactly one of answered, busy, or no-answer "
                    "(Requirements 13.4, 3.5)."
                ),
            ),
            models.CheckConstraint(
                condition=unset_or("notes", length_at_most("notes", 5000)),
                name="calls_notes_length",
                violation_error_message=(
                    "notes holds at most 5,000 characters or is unset "
                    "(Requirement 13.4)."
                ),
            ),
            # --- Requirement 13.4's clearance rule, as §4.3 writes it -------
            #
            # `CHECK (outreach_request_id IS NULL OR clearance_timestamp IS NOT
            # NULL)`. One-way on purpose, and the asymmetry is the requirement's:
            # a reservation without a clearance is forbidden, while a clearance
            # without a reservation is merely pointless. Making it two-way would
            # forbid a row that carries a clearance and no reservation, and
            # nothing in Requirements 3.5, 5.18 or 13.4 forbids that — an
            # Operator-logged call for which the Compliance_Guard happened to
            # record an evaluation instant is not a compliance problem, whereas
            # an adapter-submitted call whose clearance was lost is exactly one,
            # because task 3.2's trigger has nothing to compare and a NULL
            # comparison is a passing CHECK.
            models.CheckConstraint(
                condition=models.Q(outreach_request_id__isnull=True)
                | models.Q(clearance_timestamp__isnull=False),
                name="calls_clearance_required_with_reservation",
                violation_error_message=(
                    "clearance_timestamp is required for a call row carrying an "
                    "outreach_request_id, and may be unset only for a call "
                    "logged directly by an Operator (Requirements 13.4, 5.18, "
                    "3.5)."
                ),
            ),
            # Requirement 5.12's `calls` half — see the note on
            # `emails_outreach_request_id_unique`. A UNIQUE index counts NULLs as
            # distinct in PostgreSQL, so this constrains the reserved rows and
            # leaves any number of Operator-logged rows storable, which is what
            # 13.4 wants.
            models.UniqueConstraint(
                fields=["outreach_request_id"],
                name="calls_outreach_request_id_unique",
                violation_error_message=(
                    "a call row already exists for this outreach_request_id "
                    "(Requirements 5.10, 5.12)."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"Call {self.id} to lead {self.lead_id}: attempt {self.attempt_number}"

    @property
    def outcome_enum(self) -> CallOutcome:
        """The stored outcome as its enum member."""
        return CallOutcome(self.outcome)

    @property
    def was_operator_logged(self) -> bool:
        """Requirement 3.5: logged directly, with no adapter reservation behind it.

        The one predicate that distinguishes the two writers of this table, and
        therefore the one row shape for which ``clearance_timestamp`` is legally
        NULL.
        """
        return self.outreach_request_id is None
