"""The ``leads`` table — the root record of the pipeline (Requirements 13.1, 13.6, 13.7, 13.11).

Everything Requirement 13 declares about a Lead is declared *here*, in the
database, because ``leads`` is a shared-schema table (design §4.2): the future
bot writes the discovery columns over its own connection, event intake writes the
compliance columns, and the dashboard writes ``status`` and ``last_activity_at``.
A rule expressed only in a Django form or a ``clean()`` method is a rule the bot
never meets. So every bound of §4.3 is a database ``CHECK`` and every
"required" of 13.1 is a column-level ``NOT NULL``.

Scope boundaries, stated so the next tasks do not find their work half-done:

* :class:`PipelineState` declares the eleven values of Requirement 13.7 and the
  ``CHECK`` that makes a twelfth unstorable. It deliberately does **not** declare
  ``TERMINAL_STATES`` or ``LEGAL_TRANSITIONS`` — the 17-edge transition table and
  its three import-time assertions are **task 6.1's**, and design §3.5.1 wants
  exactly one definition of legality in the codebase. Task 6.1 imports this enum
  rather than restating the value set.
* ``state_version`` is declared here with its non-negativity ``CHECK``; the
  conditional ``UPDATE`` that increments it is **task 6.2's** optimistic
  concurrency guard (Requirement 4.13).
* The two generated normalization columns are declared here; the duplicate-contact
  *query* that reads them (Requirement 5.7) belongs to the Compliance_Guard.
  Read the warning on :attr:`Lead.phone_digits` before writing it.
* No index is declared here. The nine ``leads`` indexes of §4.7 — including the
  two over the generated columns and the ``gin_trgm_ops`` search index — are
  **task 2.4's**.
* No trigger is declared here. §4.6's ten triggers are **tasks 3.1–3.4's**.
* ``last_activity_at``'s initialization from the genesis ``pipeline_state_history``
  row is **task 8.2's**. Read the next section before adding a default to it.

WHY ``last_activity_at`` IS ``NOT NULL`` WITH NO DEFAULT AT ALL
--------------------------------------------------------------
Requirement 13.14 makes ``last_activity_at`` an invariant of the *stored data*:
it equals the latest of that Lead's Requirement 2.1 source timestamps, and it is
set at Lead creation from the ``occurred_at`` of the Lead's genesis
``pipeline_state_history`` row. Since that row is written in the same transaction
as the Lead (Requirement 13.13), the source set is non-empty from the instant the
Lead exists, so the column has no unset state to represent and §4.3 declares it
``NOT NULL``.

That produces a bootstrapping problem at this task: ``pipeline_state_history``
does not exist until task 2.3, task 6.2 owns the genesis write, and task 8.2 owns
the initialization — yet tasks 2.2 through 2.5 need creatable Leads *now*. The
tempting fix is ``db_default=Now()``. It is rejected here, for two reasons:

1. **It would let task 8.2's invariant be satisfied by accident.** Requirement
   13.14's claim at creation time is an *equality*: ``last_activity_at`` equals
   the genesis row's ``occurred_at``. A creation-time default makes that equality
   true for reasons unrelated to task 8.2 having done anything.

   With SQL ``now()`` (``transaction_timestamp()``) it is worse than merely
   likely: ``now()`` returns one value for the whole transaction, and Requirement
   13.13 puts the Lead and its genesis row in the *same* transaction, so two
   independent sources produce byte-identical timestamps every time. With
   ``statement_timestamp()`` — which is in fact what Django's ``Now()`` renders
   on PostgreSQL — the collision drops to improbable rather than impossible,
   which is the same defect wearing a better disguise: a test that usually
   passes for the wrong reason.

   Either way the assertion "``last_activity_at`` came from the genesis row"
   would hold whether or not the code that copies it was ever written, and the
   one test proving 13.14 holds at creation would prove nothing.
2. **A default fabricates a value for a denormalized column.** ``created_at``
   defaults to ``Now()`` legitimately: the creation instant has no other source,
   so the default *is* the fact. ``last_activity_at`` is a projection of rows in
   other tables. A default does not record a fact; it invents one that no source
   produced, and 13.14 is stated as an equality against those sources.

So the column is ``NOT NULL`` with no database default and no Python default, and
every writer supplies it explicitly — which is exactly what task 8.2's real
writer will do. A Lead created without it raises ``IntegrityError`` at the
database, loudly, rather than acquiring a plausible-looking wrong value; and
because there is no default to fall back on, task 8.2's initialization cannot be
omitted without every Lead-creation path failing. Callers in the interim (tests,
factories) pass the timestamp they want.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Func
from django.db.models.functions import Now
from django.utils.timezone import now as utc_now  # NOT `from django.utils import
# timezone`: this model has a `timezone` field, and the class body would shadow
# the module for every reference below it.

# Task 2.2 moved these three, unchanged, into `dashboard.models.constraints`, so
# that the nineteen tables of tasks 2.2 and 2.3 spell §4.3's bounds the way this
# table already does instead of each re-deriving them. The rendered SQL is
# identical, so migration 0003 is unaffected.
from dashboard.models.constraints import length_at_most, length_between, unset_or


class PipelineState(models.TextChoices):
    """The eleven Pipeline_State values of Requirement 13.7.

    Declared exactly as :class:`~dashboard.models.operator.Role` is: the stored
    values are the requirement's own spellings, so the database ``CHECK`` reads
    against the requirement text without a translation table, and §4.3's other
    closed value sets (``site_projects.review_state``) store their literal names
    the same way.

    This is the *value set* only. Which ordered pairs are legal is
    ``LEGAL_TRANSITIONS`` in task 6.1, and this module states no ordering,
    adjacency, or terminality — declaration order below follows the happy path
    for readability and carries no semantics. Nothing may infer legality from it.
    """

    NEW_LEAD = "New_Lead", "New Lead"
    CONTACTED = "Contacted", "Contacted"
    REPLIED = "Replied", "Replied"
    SCHEDULED = "Scheduled", "Scheduled"
    QUOTED = "Quoted", "Quoted"
    WON = "Won", "Won"
    INVOICED = "Invoiced", "Invoiced"
    PAID_PENDING_VERIFICATION = "Paid_Pending_Verification", "Paid, pending verification"
    PAYMENT_VERIFIED = "Payment_Verified", "Payment verified"
    RELEASED = "Released", "Released"
    CLOSED_LOST = "Closed_Lost", "Closed lost"


class NormalizedEmail(Func):
    """``lower(btrim(x))`` — the email half of design §3.6.5, verbatim.

    A ``Func`` subclass carrying the design's own SQL as its template rather than
    ``Lower(Trim(...))``, for one reason: §3.6.5 fixes the *SQL text*, and this
    keeps that text in the codebase exactly once, where it can be diffed against
    the design. ``Lower(Trim(...))`` compiles to ``LOWER(TRIM(x))``, which
    PostgreSQL parses to the same ``lower(btrim(x))`` — but that equivalence is a
    fact about PostgreSQL's grammar rather than something the source states.
    """

    template = "lower(btrim(%(expressions)s))"
    output_field = models.TextField()


class PhoneDigits(Func):
    """``regexp_replace(coalesce(x, ''), '\\D', '', 'g')`` — the phone half of §3.6.5.

    Note ``coalesce``, which is the design's: the result for a NULL argument is
    the **empty string, not NULL**. See the warning on
    :attr:`Lead.phone_digits`.
    """

    template = r"regexp_replace(coalesce(%(expressions)s, ''), '\D', '', 'g')"
    output_field = models.TextField()


class Lead(models.Model):
    """A prospective business record — the pipeline's root entity.

    **Opt-out is represented by a timestamp, never by a boolean.** Requirement
    13.1 states the bridge to the compliance requirements explicitly, and the
    later compliance tasks must read the same rule from here:

    * the *unsubscribed set* condition of Requirements 5.3, 5.8 and 5.11 holds
      **exactly when** ``unsubscribed_at IS NOT NULL``;
    * the *do_not_call set* condition of Requirements 5.4 and 5.16 holds
      **exactly when** ``do_not_call_at IS NOT NULL``.

    NULL means "not opted out" in both cases — there is no second
    representation, so no writer can produce a Lead that is opted out by one
    predicate and not by another. The stored value is *when* it happened, which
    is what the compliance triggers of task 3.2 compare a reservation's
    Clearance_Timestamp against; a boolean would carry no such instant and those
    triggers could not be written at all.
    """

    # Explicit BigAutoField rather than inherited from DEFAULT_AUTO_FIELD: design
    # §4.1 references this key as `bigint` from eight tables (`deals.lead_id`,
    # `emails.lead_id`, `calls.lead_id`, `site_projects.lead_id`,
    # `pipeline_state_history.lead_id`, `email_bounces.lead_id`,
    # `contacts.lead_id`, `outreach_requests.lead_id`), and a later change to
    # that setting must not be able to narrow it to `integer` and silently
    # mismatch every one of those references.
    id = models.BigAutoField(primary_key=True)

    # --- Discovery fields: bot-written, dashboard-read (§4.2) --------------
    # Text columns are `text` + a `char_length` CHECK rather than
    # `varchar(n)`, matching §4.1's declared types and §4.3's declared
    # constraints. The reason is Requirement 13.8: a violation must report "the
    # field and the violated constraint". A `varchar` overflow raises a
    # PostgreSQL type error carrying no message this project wrote, while a named
    # CHECK carries `violation_error_message`. It also lets the 1-200 bounds be
    # *one* constraint with one message instead of a type limit for the ceiling
    # and a CHECK for the floor.

    company_name = models.TextField(
        help_text=(
            "Requirement 13.1: required, 1-200 characters. The only Lead text "
            "field that is required."
        ),
    )

    industry = models.TextField(
        null=True,
        blank=True,
        help_text="Requirement 13.1: 1-200 characters, or unset. NULL is the only unset form.",
    )

    website_url = models.TextField(
        null=True,
        blank=True,
        help_text="Requirement 13.1: up to 2,048 characters, or unset.",
    )

    owner = models.TextField(
        null=True,
        blank=True,
        verbose_name="decision-maker",
        help_text=(
            "The decision-maker recorded by the originating research plan, "
            "displayed by the Deal_Room_View under Requirement 3.1 — text, not a "
            "boolean. Requirement 13.1 enumerates length bounds for exactly six "
            "columns (company_name, industry, contact_name at 1-200; website_url "
            "at 2,048; contact_email at 320; contact_phone at 32) and `owner` is "
            "deliberately not among them, nor does design §4.3 declare any "
            "`owner` constraint. It is therefore unbounded `text`, and that is a "
            "read of the criterion rather than an omission here: inventing a "
            "bound would create a constraint Requirement 13.8 cannot name and "
            "task 2.5's Property 41 table has no row for."
        ),
    )

    researched_score = models.SmallIntegerField(
        help_text=(
            "Requirement 13.6: the integer range 1 through 5. Required: 13.6 "
            "writes 'or unset' for preferred_price, website_condition, urgency, "
            "estimated_page_count, timezone and region and withholds it here, and "
            "§4.3 writes `IS NULL OR` on exactly the nullable bounds and writes "
            "this one as a bare `BETWEEN 1 AND 5`."
        ),
    )

    preferred_price = models.IntegerField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.6: whole US dollars, 550-1000, or unset. A BOT-OWNED "
            "RESEARCH HINT ONLY. Requirement 7.13 excludes it from every "
            "Suggested_Price computation, the Deal_Room_View renders it read-only, "
            "and no component may copy it into `deals.agreed_price` — which "
            "Requirement 7.8 reserves to an Operator. Do not wire this into "
            "pricing."
        ),
    )

    # --- Contact fields: dashboard-writable, audited under Requirement 3.6 ---

    contact_name = models.TextField(
        null=True,
        blank=True,
        help_text="Requirement 13.1: 1-200 characters, or unset.",
    )

    contact_email = models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.1: up to 320 characters, or unset. Normalized for "
            "duplicate detection by the generated `email_normalized` column."
        ),
    )

    contact_phone = models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.1: up to 32 characters, or unset. Normalized for "
            "duplicate detection by the generated `phone_digits` column."
        ),
    )

    # --- The generated normalization columns (design §3.6.5) ---------------
    # §3.6.5: "Normalization is defined once, in the database, as stored
    # generated columns so that application code cannot normalize differently
    # than the index does." That is the whole point of them being GENERATED
    # rather than maintained: a Python `email.strip().lower()` beside an index
    # built on `lower(btrim(...))` is two definitions that agree until one is
    # edited, and the symptom of disagreement is a *missed* duplicate warning —
    # invisible, because nothing reports a comparison that did not match.
    # PostgreSQL rejects any write to these columns, so there is no way to store
    # a value inconsistent with the expression.

    email_normalized = models.GeneratedField(
        expression=NormalizedEmail("contact_email"),
        output_field=models.TextField(),
        db_persist=True,
        help_text=(
            "GENERATED ALWAYS AS (lower(btrim(contact_email))) STORED — design "
            "§3.6.5. Requirement 5.7's case-insensitive, trimmed email "
            "comparison. NULL when contact_email is NULL, so a NULL-email Lead "
            "never equality-matches another."
        ),
    )

    phone_digits = models.GeneratedField(
        expression=PhoneDigits("contact_phone"),
        output_field=models.TextField(),
        db_persist=True,
        help_text=(
            "GENERATED ALWAYS AS (regexp_replace(coalesce(contact_phone,''), "
            "'\\D', '', 'g')) STORED — design §3.6.5. Requirement 5.7's "
            "digits-only phone comparison. WARNING: because §3.6.5's expression "
            "coalesces, this is the EMPTY STRING and never NULL for a Lead with "
            "no phone, or with a phone containing no digits. Every duplicate "
            "query over this column must therefore exclude the empty string "
            "explicitly, or it will report every phoneless Lead as a duplicate of "
            "every other. The expression is reproduced from the design verbatim "
            "and is not the place to fix that; the query is."
        ),
    )

    # --- Pipeline state, exclusively dashboard-written (§4.2) --------------

    status = models.TextField(
        choices=PipelineState.choices,
        help_text=(
            "Requirement 13.7: exactly one of the eleven Pipeline_State values. "
            "Required, and deliberately WITHOUT a default. Design §4.1 annotates "
            "`site_projects.review_state` as 'default Generating' and annotates "
            "this column with no default at all, and the contrast is load-bearing: "
            "Requirements 4.12 and 13.13 pair every status value with a "
            "`pipeline_state_history` row written in the same transaction, "
            "including the genesis row at creation. A column default would let a "
            "Lead exist at New_Lead with no history row recording how it got "
            "there, which is precisely the state Requirement 4.6's legal-path "
            "invariant cannot describe. Task 6.2's Lead creation supplies "
            "New_Lead explicitly alongside that genesis row."
        ),
    )

    state_version = models.IntegerField(
        default=0,
        db_default=0,
        help_text=(
            "Requirements 13.1, 13.6, 4.13: a required non-negative integer "
            "defaulting to 0, holding the count of accepted Pipeline_State "
            "changes. The optimistic-concurrency guard of task 6.2 reads it, "
            "increments it in the same transaction as the status write, and "
            "rejects a submission carrying a stale value. Nothing here increments "
            "it; a writer that does so outside that guard defeats it."
        ),
    )

    # --- Pricing inputs (Requirement 7.12), bot-written research fields ----

    website_condition = models.SmallIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.1, 13.6: the integer range 1 through 5, or unset. A "
            "Pricing_Advisor input; Requirement 7.12 treats unset as absent."
        ),
    )

    urgency = models.SmallIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.1, 13.6: the integer range 1 through 5, or unset. A "
            "Pricing_Advisor input; Requirement 7.12 treats unset as absent."
        ),
    )

    estimated_page_count = models.IntegerField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.1, 13.6: the integer range 0 through 200, or unset. "
            "The Requirement 7.12 page_count fallback, used only when the Lead "
            "has no Site_Project."
        ),
    )

    # --- Calling_Window inputs (Requirements 5.15, 5.17) ------------------

    timezone = models.TextField(
        null=True,
        blank=True,
        verbose_name="IANA timezone name",
        help_text=(
            "Requirements 13.1, 13.6: an IANA timezone name of at most 64 "
            "characters, or unset. Unset is the 'unknown timezone' input that "
            "Requirement 5.15 blocks a call on, and the first step of Requirement "
            "5.17's Calling_Window resolution order. The name is not validated "
            "against the IANA database here — 13.6 constrains the length, and "
            "resolution is Requirement 5.17's concern."
        ),
    )

    region = models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Requirements 13.1, 13.6: text of at most 200 characters, or unset. "
            "The Requirement 5.17 Calling_Window fallback consulted when "
            "`timezone` is unset."
        ),
    )

    # --- Compliance state, written by event intake (§4.2) -----------------

    unsubscribed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.1: the instant the Lead unsubscribed; NULL means the "
            "Lead has not unsubscribed. The unsubscribed-set condition of "
            "Requirements 5.3, 5.8 and 5.11 holds exactly when this is set. UTC "
            "(Requirement 13.11)."
        ),
    )

    do_not_call_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Requirement 13.1: the instant the Lead was recorded do-not-call; "
            "NULL means the Lead remains callable. The do_not_call-set condition "
            "of Requirements 5.4 and 5.16 holds exactly when this is set. UTC "
            "(Requirement 13.11)."
        ),
    )

    manual_review_flag = models.BooleanField(
        default=False,
        db_default=False,
        help_text=(
            "Requirements 13.1, 13.6, 5.6: required, default false. Set by the "
            "Compliance_Guard when a bounce is recorded for this Lead."
        ),
    )

    # --- Denormalized activity (Requirements 2.1, 13.14) ------------------

    last_activity_at = models.DateTimeField(
        help_text=(
            "Requirements 13.1, 13.6, 13.14: REQUIRED. The most-recent-activity "
            "timestamp of Requirement 2.1, read by the Requirement 2.4 sort. UTC. "
            "Has no default, by design — see the module note; task 8.2 "
            "initializes it from the genesis pipeline_state_history row and "
            "maintains it in the same transaction as any write that advances it."
        ),
    )

    created_at = models.DateTimeField(
        default=utc_now,
        db_default=Now(),
        editable=False,
        help_text="Requirements 13.1, 13.11: required, UTC.",
    )

    class Meta:
        db_table = "leads"
        verbose_name = "lead"
        verbose_name_plural = "leads"
        constraints = [
            # --- Requirement 13.1's length rules -------------------------
            # Each nullable bound is written `IS NULL OR <bound>` because NULL
            # must remain storable: 13.1 requires only company_name,
            # last_activity_at and created_at. The 1-200 columns exclude the
            # empty string by their lower bound, which is deliberate and matches
            # `operators.slack_webhook_url`: NULL is the single representation of
            # unset, so "has an industry" is one predicate rather than two.
            models.CheckConstraint(
                condition=length_between("company_name", 1, 200),
                name="leads_company_name_length",
                violation_error_message=(
                    "company_name is required and holds 1 to 200 characters "
                    "(Requirement 13.1)."
                ),
            ),
            models.CheckConstraint(
                condition=unset_or("industry", length_between("industry", 1, 200)),
                name="leads_industry_length",
                violation_error_message=(
                    "industry holds 1 to 200 characters or is unset "
                    "(Requirement 13.1)."
                ),
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "contact_name", length_between("contact_name", 1, 200)
                ),
                name="leads_contact_name_length",
                violation_error_message=(
                    "contact_name holds 1 to 200 characters or is unset "
                    "(Requirement 13.1)."
                ),
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "website_url", length_at_most("website_url", 2048)
                ),
                name="leads_website_url_length",
                violation_error_message=(
                    "website_url holds at most 2,048 characters or is unset "
                    "(Requirement 13.1)."
                ),
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "contact_email", length_at_most("contact_email", 320)
                ),
                name="leads_contact_email_length",
                violation_error_message=(
                    "contact_email holds at most 320 characters or is unset "
                    "(Requirement 13.1)."
                ),
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "contact_phone", length_at_most("contact_phone", 32)
                ),
                name="leads_contact_phone_length",
                violation_error_message=(
                    "contact_phone holds at most 32 characters or is unset "
                    "(Requirement 13.1)."
                ),
            ),
            # --- Requirement 13.6's numeric ranges ------------------------
            models.CheckConstraint(
                condition=models.Q(researched_score__range=(1, 5)),
                name="leads_researched_score_range",
                violation_error_message=(
                    "researched_score is an integer from 1 to 5 "
                    "(Requirement 13.6)."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(preferred_price__isnull=True)
                | models.Q(preferred_price__range=(550, 1000)),
                name="leads_preferred_price_range",
                violation_error_message=(
                    "preferred_price is a whole US dollar amount from 550 to 1000 "
                    "or is unset (Requirement 13.6)."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(website_condition__isnull=True)
                | models.Q(website_condition__range=(1, 5)),
                name="leads_website_condition_range",
                violation_error_message=(
                    "website_condition is an integer from 1 to 5 or is unset "
                    "(Requirement 13.6)."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(urgency__isnull=True)
                | models.Q(urgency__range=(1, 5)),
                name="leads_urgency_range",
                violation_error_message=(
                    "urgency is an integer from 1 to 5 or is unset "
                    "(Requirement 13.6)."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(estimated_page_count__isnull=True)
                | models.Q(estimated_page_count__range=(0, 200)),
                name="leads_estimated_page_count_range",
                violation_error_message=(
                    "estimated_page_count is an integer from 0 to 200 or is unset "
                    "(Requirement 13.6)."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(state_version__gte=0),
                name="leads_state_version_non_negative",
                violation_error_message=(
                    "state_version is a non-negative integer "
                    "(Requirements 13.6, 4.13)."
                ),
            ),
            # --- Requirement 13.6's Calling_Window input lengths ----------
            models.CheckConstraint(
                condition=unset_or("timezone", length_at_most("timezone", 64)),
                name="leads_timezone_length",
                violation_error_message=(
                    "timezone is an IANA name of at most 64 characters or is "
                    "unset (Requirement 13.6)."
                ),
            ),
            models.CheckConstraint(
                condition=unset_or("region", length_at_most("region", 200)),
                name="leads_region_length",
                violation_error_message=(
                    "region holds at most 200 characters or is unset "
                    "(Requirement 13.6)."
                ),
            ),
            # --- Requirement 13.7's closed value set ----------------------
            # In the database, not only in `choices`. `choices` is a
            # form/validation-time hint that a raw INSERT, a data migration, or
            # the future bot's connection (§4.2) never consults, and
            # `leads.status` is the column Requirement 4.6's legal-history
            # invariant is stated over. A twelfth state is therefore unstorable,
            # not merely unvalidated.
            models.CheckConstraint(
                condition=models.Q(status__in=PipelineState.values),
                name="leads_status_in_enum",
                violation_error_message=(
                    "status must be exactly one of the eleven Pipeline_State "
                    "values (Requirement 13.7)."
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.company_name} ({self.status})"

    # --- Read-side helpers for the compliance rule stated in the class
    # docstring. They exist so the later compliance tasks share one spelling of
    # the predicate instead of each writing `is not None` against the column.

    @property
    def is_unsubscribed(self) -> bool:
        """Requirement 13.1: the unsubscribed-set condition of 5.3, 5.8, 5.11."""
        return self.unsubscribed_at is not None

    @property
    def is_do_not_call(self) -> bool:
        """Requirement 13.1: the do_not_call-set condition of 5.4 and 5.16."""
        return self.do_not_call_at is not None

    @property
    def status_enum(self) -> PipelineState:
        """The stored status as its enum member."""
        return PipelineState(self.status)
