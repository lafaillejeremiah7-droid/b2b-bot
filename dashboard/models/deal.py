"""The ``deals`` table — the money record of a Lead (Requirements 13.2, 13.6, 13.12).

One Deal per Lead, at most (Requirement 13.12), holding the agreed price, the
invoice link, the payment facts, the payment-verification record that gates
release, and the payment-anomaly pair.

Like ``leads``, this is a shared-schema table (design §4.2) — though a
deliberately narrow one: the bot never writes the money or delivery columns, and
§4.2 says so twice. That does not make application-level checks sufficient. An
attempt by any writer to store an out-of-range price or a half-populated anomaly
must fail at the database, which is why every bound of §4.3 is a ``CHECK`` here
rather than a form rule.

Scope boundaries, stated so the next tasks do not find their work half-done:

* ``invoice_id`` is declared **without** a ``REFERENCES`` clause. ``invoices``
  does not exist until task 2.3. See the module note below.
* No index. The partial ``idx_deals_verified`` of §4.7 is **task 2.4's**.
* No trigger. The delivery-guard triggers of §4.6 that make ``delivery_sent`` and
  ``delivered_date`` writable only under a Release_Authorization (§3.7.4) are
  **task 3.3's**, and the ``agreed_price``-immutable-once-invoiced trigger of
  Requirement 7.11 (§3.7.6) is task 3.3's too. Nothing here prevents a direct
  write to those columns; the triggers are what will.
* ``release_authorizations`` is **task 2.3's** table and the ``ReleaseAuthorization``
  model is deliberately not defined in this module — ``scripts/check_deferred_activations.py``
  keys the §3.7.2 import contract's activation on that class existing.

WHY FOUR COLUMNS CARRY NO FOREIGN KEY YET
-----------------------------------------
Requirement 13.5 requires a real database ``REFERENCES`` for every reference, and
§4.3 repeats it: "an unresolvable ``lead_id``/``deal_id`` is rejected by the
database rather than by a hopeful application-level existence check." Task 2.2
can honour that for ``lead_id`` and ``verified_by_operator_id``, because ``leads``
and ``operators`` exist. It cannot for ``deals.invoice_id``,
``emails.outreach_request_id``, ``emails.site_project_id`` or
``calls.outreach_request_id``: those four point at tables task 2.3 creates, and
Django will not build a ``ForeignKey`` to a model that does not exist — an
unresolved lazy reference fails ``manage.py check`` and ``makemigrations`` alike,
so there is no "declare it now, wire it later" spelling available in the ORM.

The choice made here is therefore: **declare each of the four as a plain scalar
column carrying its final name, type, nullability and uniqueness, with no
``REFERENCES`` clause, and have task 2.3 attach the foreign key to the existing
column** — not drop and recreate it. In Django terms 2.3 wraps the swap in
:class:`~django.db.migrations.operations.special.SeparateDatabaseAndState`: the
state half replaces the scalar field with the relation field, and the database
half is a single ``ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY``. Without the
split, the autodetector emits ``RemoveField`` + ``AddField``, which drops the
column — taking with it the ``UNIQUE`` index and the ``NOT NULL`` this task
declared, and any row already written.

The alternative — leaving these columns out of task 2.2 entirely and letting 2.3
add them — was rejected because ``emails.clearance_timestamp`` is ``NOT NULL``
and is meaningless without the ``outreach_request_id`` it was copied from; a
table that holds the clearance but not the reservation it came from is not a
schema this task can honestly claim to have built.

``dashboard.models.forward_references.PENDING_FOREIGN_KEYS`` lists the four, and
``dashboard/tests/test_forward_references.py`` turns the list into a failing test
the moment the referenced table appears without its foreign key. So this is a
deferral with an expiry date enforced by the build, not a TODO.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from dashboard.models.constraints import length_between
from dashboard.models.fields import MillisecondDateTimeField


class Deal(models.Model):
    """The commercial record attached to a Lead — at most one per Lead.

    **``payment_verified_at`` is the Payment_Verified_Flag.** Requirement 8.17
    makes the verification timestamp the authoritative record that payment was
    verified and states the flag's reading rule as an equivalence: the flag reads
    as set for exactly those Deals whose ``payment_verified_at`` is set. There is
    no separate boolean, deliberately — a boolean beside a timestamp is two
    representations of one fact, and Requirement 8.11's ordering claim
    (``payment_verified_at ≤ authorized_at ≤ delivered_date``) needs the instant
    anyway. :attr:`payment_verified_flag` below is the single spelling of that
    predicate; every gate should call it rather than testing the column.

    **Requirement 8.20 is the reason that matters.** The Release_Gate evaluates
    the payment-verification precondition of Requirements 8.7, 8.8 and 8.9 by
    reading this flag, *not* the Lead's Pipeline_State. A Deal whose Lead sits at
    ``Payment_Verified`` but whose ``payment_verified_at`` is NULL must still be
    refused release. Requirements 8.18 and 8.19 make that pair unreachable by
    writing both in one transaction, but the gate does not get to assume it: the
    whole point of 8.20 is that the gate's precondition does not depend on
    another component having kept its promise.
    """

    # Requirement 13.2 names the primary key `deal_id`, and §4.1 declares it
    # `bigint`. Explicit BigAutoField rather than inherited from
    # DEFAULT_AUTO_FIELD for the same reason as `leads.id` and `operators.id`:
    # §4.1 references this key as `bigint` from `invoices.deal_id`,
    # `payments.deal_id` and `release_authorizations.deal_id` (all task 2.3), and
    # a later change to that setting must not be able to narrow it to `integer`
    # and silently mismatch those references.
    deal_id = models.BigAutoField(primary_key=True)

    # --- The Lead link: one Deal per Lead (Requirements 13.2, 13.12) -------
    #
    # A OneToOneField, not a ForeignKey plus a UniqueConstraint. The DDL is
    # identical — both render `bigint NOT NULL REFERENCES leads(id)` with a
    # UNIQUE index over the column — so the choice is about which one states
    # Requirement 13.12 to the *readers*.
    #
    # With a OneToOneField, `lead.deal` is a Deal or a RelatedObjectDoesNotExist.
    # With a ForeignKey, `lead.deals` is a manager, and every one of the dozens
    # of readers in tasks 6 through 17 that needs "this Lead's Deal" has to
    # re-encode "at most one" in Python — `.first()`, or `.get()` inside a
    # try/except, chosen independently each time. The database guarantee would be
    # the same and the codebase would be full of restatements of it. Django also
    # actively steers away from the alternative: `ForeignKey(unique=True)` raises
    # check W342 telling you to use a OneToOneField.
    #
    # The cost is the constraint's *name*. A UniqueConstraint would let this task
    # choose one and attach a `violation_error_message`; the OneToOneField's
    # uniqueness is declared inline on the column, so PostgreSQL names it
    # `deals_lead_id_key`. That name still identifies the table and the column,
    # which is what Requirement 13.8 asks of the report, and the 13.12 wording is
    # supplied by the service layer catching the IntegrityError (task 6.2 owns
    # Deal creation). `dashboard/tests/test_deal_model.py` asserts the name, so
    # the error-reporting path has something stable to key on.
    #
    # on_delete=PROTECT: nothing in this specification deletes a Lead, and a
    # cascade would be the wrong answer if something did — the Deal holds the
    # payment and verification record, and Requirement 8.17 calls it
    # authoritative. Deleting a Lead out from under a Deal must fail loudly.
    lead = models.OneToOneField(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="deal",
        db_column="lead_id",
        help_text=(
            "Requirements 13.2, 13.12: required, and UNIQUE — at most one Deal "
            "per Lead. The uniqueness is a database constraint, not a service "
            "convention, so two concurrent create-Deal transactions cannot both "
            "commit (task 2.5's Property 42 asserts exactly that on separate "
            "connections)."
        ),
    )

    # --- Pricing (Requirements 7.5, 7.6, 7.8, 13.2) ------------------------

    agreed_price = models.IntegerField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.2, 7.6: a whole US dollar amount from 550 to 1000, "
            "or unset. OPERATOR-SET ONLY. Requirement 7.8 states this as an "
            "invariant over stored records — every agreed_price was set by an "
            "Operator submission, and no Suggested_Price computation writes this "
            "field. Requirement 7.13 additionally forbids copying the Lead's "
            "preferred_price into it. Requirement 7.11 makes it immutable once "
            "an invoice exists, enforced by task 3.3's trigger."
        ),
    )

    quote_sent_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.2: unset until the quote action records it. Design "
            "§4.1 declares this `timestamptz` despite the column's name, so "
            "Requirement 13.11 applies: UTC, one second or finer."
        ),
    )

    # --- The invoice link: a forward reference, see the module note --------

    invoice_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.2: unset until Requirement 8.1's create-invoice "
            "action records it. Declared as a plain bigint because `invoices` is "
            "task 2.3's table — task 2.3 attaches the REFERENCES to this column "
            "rather than replacing it (see the module docstring). Deliberately "
            "NOT unique: §4.3 places the at-most-one-invoice-per-Deal rule on "
            "`invoices.deal_id UNIQUE`, and declaring the mirror image here as "
            "well would be a second constraint for one rule, reported under a "
            "name Requirement 8.2's message does not expect."
        ),
    )

    # --- Payment facts, recorded unconditionally (Requirements 8.3, 8.23) --

    payment_received = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.2, 8.3: unset until a payment event records it, and "
            "then set UNCONDITIONALLY — irrespective of the Lead's Pipeline_State "
            "and irrespective of whether an invoice exists (§3.7.6: money that "
            "has arrived is a fact about the world). NULLABLE WITH NO DEFAULT, "
            "which is a read of the criteria rather than an oversight: "
            "Requirement 13.6 spells out 'a required boolean defaulting to false' "
            "for `manual_review_flag` and `payment_anomaly_flag` and withholds it "
            "here, while 13.2 lists this column among those 'unset until the "
            "corresponding action in Requirement 8 records them'. So NULL is "
            "unset. Because false is also storable, the set-condition is "
            "`payment_received IS TRUE` and not `IS NOT NULL`; every reader must "
            "use that spelling."
        ),
    )

    paid_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.2, 8.3: the payment date, unset until a payment "
            "event records it. A `date`, not a timestamp — design §4.1 declares "
            "it `date paid_date` while declaring `quote_sent_date` and "
            "`delivered_date` beside it as `timestamptz`, so the contrast is the "
            "design's and Requirement 13.11's UTC-timestamp rule does not reach "
            "this column. The payment *instant* lives on the `payments` record "
            "(task 2.3)."
        ),
    )

    # --- The verification record: the release gate's actual precondition ---

    payment_verified_at = MillisecondDateTimeField(
        null=True,
        blank=True,
        verbose_name="payment verification timestamp",
        help_text=(
            "Requirements 13.2, 8.5, 8.17: `TIMESTAMPTZ(3)` — millisecond "
            "precision, per Requirements 8.5 and 8.8 and design §4.3. THE "
            "AUTHORITATIVE RECORD that the payment was verified (Requirement "
            "8.17), and the field the Payment_Verified_Flag reads: the flag is "
            "set for exactly those Deals whose value here is set. Requirement "
            "8.20 has the Release_Gate evaluate its precondition by reading this "
            "flag RATHER THAN the Deal's Pipeline_State, so a Lead sitting at "
            "Payment_Verified with this column NULL is still refused release. "
            "Written with the Payment_Verified transition in one transaction "
            "(Requirement 8.18) and never later than the Release_Authorization's "
            "authorized_at (Requirement 8.11). Unset until Requirement 8.5's "
            "Verify Payment action records it; task 13.x owns that write."
        ),
    )

    verified_by_operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="verified_deals",
        db_column="verified_by_operator_id",
        help_text=(
            "Requirements 13.2, 8.5: the verifying Operator, identifying an "
            "existing Operator account when set (a real REFERENCES, since "
            "`operators` exists as of task 1.3). on_delete=PROTECT: the "
            "verification record names a human, and Requirement 8.17 calls it "
            "authoritative, so an Operator who has verified a payment cannot be "
            "deleted out from under it — Requirement 1.9 deactivates accounts "
            "rather than deleting them."
        ),
    )

    # --- Delivery, writable only under a Release_Authorization ------------

    delivery_sent = models.BooleanField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.2, 8.15: unset until the Pipeline_Adapter returns "
            "success for a delivery request submitted under a "
            "Release_Authorization. Nullable with no default, on the same reading "
            "as `payment_received`, and with the same consequence: Requirements "
            "8.9, 8.11, 8.12 and 8.16 all turn on 'delivery_sent set', and since "
            "both NULL and false are storable that predicate is "
            "`delivery_sent IS TRUE`. Requirement 8.12's count — zero Deals with "
            "this set absent an accepted Approve Release — is enforced by task "
            "3.3's §4.6 trigger, not by this column."
        ),
    )

    delivered_date = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.2, 8.15: unset until delivery succeeds. `timestamptz` "
            "per §4.1 despite the name; Requirement 8.11 orders it at or after "
            "the Release_Authorization's authorized_at."
        ),
    )

    # --- The payment anomaly pair (Requirements 8.21, 8.22, 13.6) ---------

    payment_anomaly_flag = models.BooleanField(
        default=False,
        db_default=False,
        help_text=(
            "Requirements 13.2, 13.6, 8.21: required, default false. Set when a "
            "payment is recorded that cannot be accompanied by the "
            "Paid_Pending_Verification transition — either the Lead's state forms "
            "no Legal_Transition to it, or the Deal has no invoice. Requirement "
            "8.22 clears it ONLY through an explicit Operator-confirmed "
            "clear-payment-anomaly action by an Agent or Admin, with an "
            "Audit_Entry: no Pipeline_Adapter event and no Pipeline_State change "
            "clears it, so a later legal transition cannot erase the record that "
            "a human still needs to look at this Deal."
        ),
    )

    payment_anomaly_reason = models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.2, 13.6, 8.21: 1 to 500 characters while "
            "payment_anomaly_flag is true, and unset while it is false — a "
            "two-way CHECK, see Meta. Names which of the two anomaly conditions "
            "applied. Requirement 8.22 displays it beside the indicator and "
            "records it as the Audit_Entry's before_value when the flag is "
            "cleared, which is why clearing must null this column in the same "
            "statement that clears the flag."
        ),
    )

    class Meta:
        db_table = "deals"
        verbose_name = "deal"
        verbose_name_plural = "deals"
        constraints = [
            # Requirement 7.6 / 13.2, as a database bound rather than a form
            # rule. Requirement 7.4's rejection message quotes this range, and
            # `payments.amount_usd` (task 2.3) is deliberately WIDER at 1-1000 so
            # a shortfall is recordable — do not "harmonize" the two.
            models.CheckConstraint(
                condition=models.Q(agreed_price__isnull=True)
                | models.Q(agreed_price__range=(550, 1000)),
                name="deals_agreed_price_range",
                violation_error_message=(
                    "agreed_price is a whole US dollar amount from 550 to 1000 "
                    "or is unset (Requirements 13.2, 7.6)."
                ),
            ),
            # --- The two-way anomaly CHECK (Requirements 13.6, 8.21, 8.22) ---
            #
            # Reason present, and 1-500 characters, IF AND ONLY IF the flag is
            # true. Both halves matter, and the second is the one a one-way check
            # would miss: an unflagged Deal carrying a stale reason would surface
            # that reason in Requirement 8.22's Deal_Room_View indicator and
            # Lead_List_View badge for a Deal that has no anomaly — a warning
            # about nothing, which is how operators learn to ignore warnings. It
            # is also exactly the state a partial clear leaves behind, so the
            # constraint forces the clearing statement of Requirement 8.22 to
            # null the reason and clear the flag together or fail.
            #
            # `payment_anomaly_reason__isnull=False` is stated explicitly on the
            # flagged side and is NOT redundant. Without it, a flagged Deal with a
            # NULL reason evaluates `char_length(NULL) >= 1` to NULL, the whole
            # branch to NULL, `NULL OR false` to NULL — and PostgreSQL admits a
            # row whose CHECK evaluated to NULL. The constraint would then permit
            # precisely the first half it exists to forbid. See
            # `dashboard.models.constraints.unset_or` for the same trap stated
            # from the other direction.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        payment_anomaly_flag=True,
                        payment_anomaly_reason__isnull=False,
                    )
                    & length_between("payment_anomaly_reason", 1, 500)
                )
                | models.Q(
                    payment_anomaly_flag=False,
                    payment_anomaly_reason__isnull=True,
                ),
                name="deals_payment_anomaly_reason_matches_flag",
                violation_error_message=(
                    "payment_anomaly_reason holds 1 to 500 characters while "
                    "payment_anomaly_flag is true and is unset while it is "
                    "false (Requirements 13.6, 8.21)."
                ),
            ),
        ]

    def __str__(self) -> str:
        price = "unpriced" if self.agreed_price is None else f"${self.agreed_price}"
        return f"Deal {self.deal_id} ({price})"

    # --- Read-side helpers, so the gates share one spelling of each predicate.

    @property
    def payment_verified_flag(self) -> bool:
        """Requirement 8.17: set for exactly those Deals with a verification timestamp.

        The one spelling of the Payment_Verified_Flag. Requirement 8.20 has the
        Release_Gate read *this* rather than the Lead's Pipeline_State, and
        Requirement 8.7 disables the Approve Release control on it "regardless of
        the Deal's Pipeline_State".
        """
        return self.payment_verified_at is not None

    @property
    def is_delivered(self) -> bool:
        """Requirements 8.11, 8.12: the ``delivery_sent IS TRUE`` set-condition.

        Spelled as an identity against ``True`` because the column is a nullable
        boolean: ``IS NOT NULL`` would count a Deal explicitly recorded as
        not-delivered as delivered.
        """
        return self.delivery_sent is True

    @property
    def has_payment_anomaly(self) -> bool:
        """Requirement 8.22's WHILE condition, for the indicator and the badge."""
        return self.payment_anomaly_flag
