"""Column types design §4.3 names but Django has no field for.

Today that is one field: the ``TIMESTAMPTZ(3)`` columns of Requirements 8.5 and
8.8. Django's :class:`~django.db.models.DateTimeField` always renders
``timestamp with time zone``, which PostgreSQL stores at microsecond precision.
"""

from __future__ import annotations

import datetime as dt

from django.db import models


def truncate_to_millisecond(value: dt.datetime) -> dt.datetime:
    """Drop the sub-millisecond part of ``value``, never rounding upward.

    Exposed as a function, not buried in the field, because task 3.2's triggers
    and task 11.1's Phase 3 copy both need to reason about the exact value a
    ``TIMESTAMPTZ(3)`` column holds, and "truncate, do not round" is the property
    they depend on. See :class:`MillisecondDateTimeField`.
    """
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


class MillisecondDateTimeField(models.DateTimeField):
    """``timestamp(3) with time zone`` — the ms-precision columns of §4.3.

    Design §4.3 declares four columns at this type: ``deals.payment_verified_at``
    and ``emails``/``calls``.``clearance_timestamp`` (task 2.2), plus
    ``outreach_requests.clearance_timestamp`` and
    ``release_authorizations.authorized_at`` (task 2.3). Requirement 13.11 asks
    for one second or finer and Requirements 8.5 and 8.8 ask specifically for
    millisecond precision, so the declared precision is the requirement's own
    number rather than the backend's default.

    WHY THIS TRUNCATES RATHER THAN LETTING POSTGRESQL ROUND
    ------------------------------------------------------
    A ``timestamp(3)`` column rounds an incoming microsecond value to the nearest
    millisecond, half away from zero. Rounding is monotonic, so the non-strict
    chain of Requirement 8.11 (``payment_verified_at ≤ authorized_at ≤
    delivered_date``, §3.7.5) survives it: ``a ≤ b`` implies
    ``round(a) ≤ round(b)``.

    The clearance comparisons are not non-strict, and there rounding is a hazard
    rather than a nuisance. Requirements 5.19 and 5.20 state that a recorded
    row's Clearance_Timestamp is **strictly earlier** than the Lead's
    ``unsubscribed_at`` / ``do_not_call_at`` — and those two columns are plain
    ``timestamptz``, so they keep their microseconds. An action cleared at
    ``12:00:00.000_6`` against an opt-out recorded at ``12:00:00.000_7`` is
    genuinely compliant, but rounding stores the clearance as ``…000_1000`` µs,
    which is *later* than the opt-out. Task 3.2's compliance trigger compares
    exactly these two values, so a rounded-up clearance makes the trigger reject
    a row whose message the adapter has already sent — reintroducing, at
    sub-millisecond scale, the precise defect the clearance model was written to
    remove (design §3.6.4).

    Truncation cannot do that. It moves a stored instant earlier or leaves it
    alone, which is the safe direction for both the strict clearance comparisons
    and the non-strict release chain. So the ORM write path truncates before the
    value reaches the column, and PostgreSQL's rounding becomes a no-op.

    **A raw writer still gets rounding.** This is a Python-side guarantee and
    ``emails``/``calls`` are shared-schema tables (§4.2). The residual exposure is
    a write whose sub-millisecond part is ≥ 500 µs *and* an opt-out landing inside
    the same millisecond, and the outcome is a rejected insert rather than a lost
    row, because the trigger rejects rather than drops. Closing it completely
    would mean a ``CHECK`` that the value is already millisecond-exact, which
    §4.3 does not declare and which would reject the bot's ordinary
    ``clock_timestamp()``.

    Only ``get_db_prep_save`` is overridden, not ``get_prep_value``: truncating
    inside ``get_prep_value`` would also truncate *query bounds*, which silently
    widens a ``__gte`` filter by up to a millisecond. Writes are truncated;
    comparisons are made at the precision the caller asked for.
    """

    description = "Date and time, UTC, at millisecond precision (Requirements 8.5, 8.8)"

    def db_type(self, connection) -> str:
        return "timestamp(3) with time zone"

    def get_db_prep_save(self, value, connection):
        prepared = self.get_prep_value(value)
        if prepared is not None:
            prepared = truncate_to_millisecond(prepared)
        return connection.ops.adapt_datetimefield_value(prepared)
