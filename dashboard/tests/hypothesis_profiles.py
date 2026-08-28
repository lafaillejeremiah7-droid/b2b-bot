"""Hypothesis settings profiles encoding design §7.2's iteration budgets.

§7.2 states the budgets once, as policy:

    100 is the floor. Cheap pure-function properties run 200-1000.
    Database-backed stateful machines run 100 with ``stateful_step_count``
    between 10 and 50. Concurrency properties (10, 24, 42, 45) run 100 with
    ``N`` drawn per example.

Restating those numbers in a ``@settings(max_examples=...)`` decorator on each of
the 46 property tests would turn one policy into 46 independent copies of it,
and the first one someone writes as ``max_examples=50`` is a property that
quietly searches half as hard as the design requires. So the budgets live here,
as named profiles, and a property test *selects* one:

    from dashboard.tests.hypothesis_profiles import Profile, use

    @use(Profile.PURE)
    @given(page_count=st.integers(0, 200), ...)
    def test_property_20_suggested_price_matches_formula_and_bounds(...): ...

``use()`` is a ``@settings`` decorator built from the named profile, so a test
can still override one knob for a local reason while inheriting the rest::

    @use(Profile.STATEFUL, stateful_step_count=25)

Selecting a profile for a whole *run* is separate and goes through Hypothesis's
own machinery — ``--hypothesis-profile=<name>`` or ``HYPOTHESIS_PROFILE=<name>``
(see ``conftest.py``). A run-level profile changes the defaults; a test-level
``use()`` pins that test regardless. That is intentional: the point of the
per-test selection is that Property 20 runs 200 examples even in a run someone
launched with the default profile.
"""

from __future__ import annotations

import enum
from typing import Any, Callable, TypeVar

from hypothesis import HealthCheck, Phase, settings

F = TypeVar("F", bound=Callable[..., Any])


class Profile(str, enum.Enum):
    """The registered profile names. A string enum so ``Profile.PURE`` and the
    literal ``"pure"`` are interchangeable at the ``load_profile`` boundary."""

    CI = "ci"
    PURE = "pure"
    PURE_THOROUGH = "pure_thorough"
    STATEFUL = "stateful"
    STATEFUL_DEEP = "stateful_deep"
    CONCURRENCY = "concurrency"

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


#: Design §7.2's floor. Every registered profile must meet it; the harness test
#: in ``test_harness.py`` asserts that, so a profile added later cannot drop
#: below the floor without failing the build.
MAX_EXAMPLES_FLOOR = 100

#: §7.2's band for cheap pure-function properties.
PURE_EXAMPLES_BAND = (200, 1000)

#: §7.2's band for the database-backed stateful machines.
STATEFUL_STEP_COUNT_BAND = (10, 50)

# Health checks suppressed on the database-backed profiles, and only there.
#
# `too_slow` fires because a Lead with its genesis history row is several INSERTs
# per example and Hypothesis's data-generation budget is tuned for pure
# functions. `function_scoped_fixture` fires because pytest-django's `db` fixture
# is function-scoped while Hypothesis reuses the test body across examples — the
# warning is correct in general and wrong here, because the database-backed
# properties are written against `TransactionTestCase`/`django_db(transaction=True)`
# where each example's writes are flushed rather than carried over.
#
# Nothing else is suppressed. In particular `filter_too_much` and
# `data_too_large` stay live: those signal a generator that cannot reach the
# input space it claims to cover, which for a compliance or money property is a
# test that passes because it never looked.
_DB_BACKED_SUPPRESSIONS = (
    HealthCheck.too_slow,
    HealthCheck.function_scoped_fixture,
)

_COMMON = {
    # No per-example deadline anywhere. §7.2's properties are database-backed or
    # concurrent; a wall-clock deadline on those produces flaky failures that
    # say nothing about correctness. Timing claims are §7.5's job and are
    # asserted explicitly there against a seeded dataset.
    "deadline": None,
    # Print the @reproduce_failure blob with a falsifying example, so a CI log
    # carries everything needed to replay that exact example locally.
    "print_blob": True,
    # Explicit rather than inherited: shrinking is what turns a 400-line
    # generated Lead into the two-field counterexample a reviewer can read.
    "phases": tuple(Phase),
}


def _register() -> None:
    """Register every profile. Idempotent — Hypothesis overwrites by name."""

    # ------------------------------------------------------------------
    # ci — the default profile, and §7.2's floor.
    # ------------------------------------------------------------------
    # Every property gets at least this. Database-backed properties that need
    # nothing beyond the floor use it directly.
    settings.register_profile(
        Profile.CI.value,
        settings(
            max_examples=MAX_EXAMPLES_FLOOR,
            suppress_health_check=_DB_BACKED_SUPPRESSIONS,
            **_COMMON,
        ),
    )

    # ------------------------------------------------------------------
    # pure / pure_thorough — §7.2's 200-1000 band.
    # ------------------------------------------------------------------
    # For properties over pure functions: Property 20 (suggested_price),
    # Property 16's normalization, Property 30's bucket partition. Cheap enough
    # per example that the extra order of magnitude costs seconds, and these are
    # the properties where a rare arithmetic edge hides.
    #
    # No health-check suppression: a pure-function property that trips
    # `too_slow` is touching the database and belongs on `ci` instead.
    settings.register_profile(
        Profile.PURE.value,
        settings(max_examples=PURE_EXAMPLES_BAND[0], **_COMMON),
    )
    settings.register_profile(
        Profile.PURE_THOROUGH.value,
        settings(max_examples=PURE_EXAMPLES_BAND[1], **_COMMON),
    )

    # ------------------------------------------------------------------
    # stateful / stateful_deep — §7.2's nine RuleBasedStateMachines.
    # ------------------------------------------------------------------
    # 100 examples with `stateful_step_count` at each end of §7.2's 10-50 band.
    # `stateful` is the routine setting; `stateful_deep` is for the machines
    # whose invariant only becomes falsifiable after a long interleaving —
    # ReleaseSafetyMachine and VerificationConsistencyMachine reach a delivered
    # Deal only after price, invoice, payment, verification and authorization
    # have all fired, which is already five steps of setup before the invariant
    # has anything to say.
    settings.register_profile(
        Profile.STATEFUL.value,
        settings(
            max_examples=MAX_EXAMPLES_FLOOR,
            stateful_step_count=STATEFUL_STEP_COUNT_BAND[0],
            suppress_health_check=_DB_BACKED_SUPPRESSIONS,
            **_COMMON,
        ),
    )
    settings.register_profile(
        Profile.STATEFUL_DEEP.value,
        settings(
            max_examples=MAX_EXAMPLES_FLOOR,
            stateful_step_count=STATEFUL_STEP_COUNT_BAND[1],
            suppress_health_check=_DB_BACKED_SUPPRESSIONS,
            **_COMMON,
        ),
    )

    # ------------------------------------------------------------------
    # concurrency — Properties 10, 24, 42, 45 and the §7.3 attempt_number example.
    # ------------------------------------------------------------------
    # 100 examples, with the racer count N drawn per example by the test's own
    # strategy rather than fixed here — §7.2 is explicit that N varies.
    #
    # `differing_executors` is suppressed in addition to the database-backed
    # pair: these tests run their rules on worker threads with their own
    # connections, which is precisely the shape that check reports.
    settings.register_profile(
        Profile.CONCURRENCY.value,
        settings(
            max_examples=MAX_EXAMPLES_FLOOR,
            suppress_health_check=(
                *_DB_BACKED_SUPPRESSIONS,
                HealthCheck.differing_executors,
            ),
            **_COMMON,
        ),
    )


_register()

#: The profile loaded when nothing selects one. Named rather than inlined so
#: conftest.py and the harness test agree on it.
DEFAULT_PROFILE = Profile.CI


def use(profile: Profile | str, **overrides: Any) -> Callable[[F], F]:
    """Return a ``@settings`` decorator carrying ``profile``'s budgets.

    ``overrides`` are applied on top, for the occasional test that needs one
    knob moved without abandoning the profile. Prefer adding a profile over
    scattering overrides: an override is invisible to the §7.2 floor assertions
    in ``test_harness.py``, and a profile is not.
    """
    name = profile.value if isinstance(profile, Profile) else profile
    parent = settings.get_profile(name)  # KeyError here means a typo'd name
    return settings(parent=parent, **overrides)


def registered_profiles() -> dict[str, settings]:
    """Every profile this module registers, by name. Used by the harness test."""
    return {member.value: settings.get_profile(member.value) for member in Profile}
