"""Reusable ``CHECK`` condition builders shared by every model module.

Extracted from ``dashboard.models.lead`` at task 2.2, unchanged. Task 2.1
declared these three helpers privately because it was the only module with
length bounds; tasks 2.2 and 2.3 add nineteen more tables whose bounds are
spelled identically in design §4.3, and three helpers copied into four modules
is three helpers that can drift into four spellings. The rendered SQL is
byte-identical to what task 2.1's migration already deployed, so moving them
changes no migration state.

Everything here builds a :class:`~django.db.models.Q` suitable for a
:class:`~django.db.models.CheckConstraint` condition. Nothing here names a
constraint or writes a message: those belong beside the column, in the model's
``Meta.constraints``, so that Requirement 13.8's "identify the field and the
violated constraint" reads off one place.
"""

from __future__ import annotations

from django.db import models
from django.db.models.functions import Length
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual


def length_between(field: str, low: int, high: int) -> models.Q:
    """``char_length(field) BETWEEN low AND high`` as a constraint condition.

    Spelled with an explicit :class:`~django.db.models.functions.Length` and
    comparison lookups rather than the tidier ``Q(field__length__range=…)``,
    because Django does not register ``Length`` as a lookup: making that spelling
    work needs ``TextField.register_lookup(Length)``, a mutation of Django's
    global field registry executed as an import side effect of a model module.
    A local helper is the smaller thing. The rendered SQL is identical —
    PostgreSQL resolves ``length()`` and the standard's ``char_length()`` to the
    same function, so the constraint reads in ``pg_constraint`` exactly as §4.3
    writes it.

    **The lower bound is what excludes the empty string**, which is how NULL
    becomes the single representation of "unset" on every nullable text column in
    this schema. See :func:`unset_or`.
    """
    return models.Q(
        GreaterThanOrEqual(Length(field), low),
        LessThanOrEqual(Length(field), high),
    )


def length_at_most(field: str, high: int) -> models.Q:
    """``char_length(field) <= high`` as a constraint condition."""
    return models.Q(LessThanOrEqual(Length(field), high))


def unset_or(field: str, bound: models.Q) -> models.Q:
    """``field IS NULL OR <bound>``.

    Written out even though it is, today, redundant: PostgreSQL counts a ``CHECK``
    that evaluates to NULL as satisfied, so ``length(industry) >= 1`` already
    admits a NULL ``industry`` — by NULL propagation rather than by anything the
    schema says. Two reasons to say it anyway.

    First, it is what §4.3 writes, and writing it identically means the text in
    ``pg_constraint`` can be diffed against the design instead of reasoned about.
    Second, the redundancy is not stable: rewrite a bound into any non-propagating
    form (``coalesce(length(x), 0) >= 1``, a ``CASE``, a comparison against a
    function that is not strict) and the accidental permission disappears, turning
    a nullable column into a required one with no line of the diff mentioning
    nullability. Stating it makes each column's nullability a property of the
    constraint rather than of the operator that happens to be in it.

    **The converse trap is live in this schema and is not solved by this
    helper.** Where a bound must hold *conditionally* — ``deals``'
    ``payment_anomaly_reason``, which is required while the flag is true — NULL
    propagation works against the constraint rather than for it: a length test
    over a NULL column yields NULL, the enclosing ``AND`` yields NULL, and
    PostgreSQL admits the row. Such a constraint has to state
    ``IS NOT NULL`` explicitly on the required side. See
    ``deals_payment_anomaly_reason_matches_flag``.
    """
    return models.Q(**{f"{field}__isnull": True}) | bound
