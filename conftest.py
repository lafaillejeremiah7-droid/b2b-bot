"""Root pytest configuration for the Deal Room Dashboard (task 1.4).

Three jobs, and deliberately nothing else:

1. Register design §7.2's Hypothesis profiles and load the default one.
2. Guard the two pytest options that can silently weaken the suite —
   ``--reuse-db`` and a mis-selected Hypothesis profile.
3. Assert, for every test marked ``concurrency``, that no outer transaction is
   open. Design §7.1 requires ``TransactionTestCase`` with threads on separate
   connections for Properties 10, 24, 42 and 45, and the failure mode this
   guards against is silent: a concurrency test written with the wrong base
   class still *passes*, because a single wrapping transaction makes the racers
   invisible to each other and the race it was written to detect never happens.

No fixtures for feature data live here. Factories are ``factory_boy``'s and are
owned by the tasks that introduce the models they build.
"""

from __future__ import annotations

import os

import pytest

# --------------------------------------------------------------------------
# Hypothesis profiles (design §7.2)
# --------------------------------------------------------------------------
# Importing the module registers every profile, and it happens here — at conftest
# import — so the profiles exist before any test module is imported. That
# ordering is load-bearing: `@use(Profile.PURE)` at module scope in a test file
# resolves the profile at decoration time, which is during collection.
from dashboard.tests.hypothesis_profiles import DEFAULT_PROFILE, Profile


def pytest_configure(config: pytest.Config) -> None:
    from hypothesis import settings as hypothesis_settings
    from hypothesis.errors import InvalidArgument

    # --- Hypothesis profile selection -------------------------------------
    # Precedence, highest first:
    #   --hypothesis-profile=<name>   (handled by Hypothesis's own plugin)
    #   HYPOTHESIS_PROFILE=<name>     (env, for CI matrix jobs)
    #   DEFAULT_PROFILE               ("ci" — design §7.2's 100-example floor)
    #
    # Only fill in the default when the CLI flag is absent, so an explicit
    # `--hypothesis-profile` is never overwritten by this hook.
    if not config.getoption("hypothesis_profile", default=None):
        requested = os.environ.get("HYPOTHESIS_PROFILE", DEFAULT_PROFILE.value)
        try:
            hypothesis_settings.load_profile(requested)
        except (InvalidArgument, KeyError):
            # Reported as a pytest UsageError rather than allowed to propagate:
            # Hypothesis raises out of a plugin hook, which pytest surfaces as an
            # INTERNALERROR traceback with no hint that a profile name was the
            # problem. A typo'd HYPOTHESIS_PROFILE in a CI job should read as a
            # configuration mistake, and should list the valid names.
            known = ", ".join(member.value for member in Profile)
            raise pytest.UsageError(
                f"HYPOTHESIS_PROFILE={requested!r} is not a registered profile. "
                f"Registered profiles (see dashboard/tests/hypothesis_profiles.py, "
                f"design §7.2): {known}."
            ) from None

    # --- --reuse-db guards ------------------------------------------------
    # pytest.ini explains at length why --reuse-db is not enabled by default.
    # It stays *available* for a local edit loop, with two guards.
    if config.getoption("reuse_db", default=False):
        selected_markers = config.getoption("markexpr", default="") or ""
        if "migrations" in selected_markers:
            # Hard error, not a warning. The task 3.5 migration test asserts
            # that a *fresh* migrate installs the ten triggers of §4.6, the §4.3
            # CHECKs, the two generated columns, the genesis partial unique
            # index and the §4.7 indexes. --reuse-db skips migrations entirely,
            # so the test would assert those objects exist in a database some
            # earlier revision built — passing while proving nothing about the
            # migrations under test. That is the exact failure it exists to
            # catch, so refuse the combination rather than report a green run.
            raise pytest.UsageError(
                "--reuse-db cannot be combined with `-m migrations`: the task 3.5 "
                "migration test asserts what a fresh `migrate` installs, and "
                "--reuse-db does not run migrations. Drop --reuse-db (or add "
                "--create-db) for this selection."
            )
        # For every other selection it is merely a foot-gun, so say so loudly.
        # A schema change with a stale reused database shows up as a confusing
        # data error inside an unrelated property rather than as a migration
        # failure, and the concurrency tests' TRUNCATE-based cleanup makes the
        # symptom non-deterministic.
        terminal = config.pluginmanager.get_plugin("terminalreporter")
        message = (
            "--reuse-db is in effect: migrations are NOT being applied. Re-run "
            "with --create-db after any migration change, and never trust a "
            "schema assertion from this run."
        )
        if terminal is not None:
            terminal.write_line(f"WARNING: {message}", yellow=True, bold=True)
        else:  # pragma: no cover - terminal reporter is always present under pytest
            print(f"WARNING: {message}")


# --------------------------------------------------------------------------
# Concurrency guard (design §7.1)
# --------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _concurrency_tests_run_outside_any_transaction(request: pytest.FixtureRequest):
    """Fail a ``concurrency``-marked test that runs inside a wrapping transaction.

    Design §7.1: *"Properties 10, 24, 42 need genuine concurrent transactions,
    which ``TestCase``'s single wrapping transaction cannot provide."*

    Two things can put such a test inside one:

    * a Django ``TestCase`` (or ``hypothesis.extra.django.TestCase``) base class
      instead of ``TransactionTestCase``;
    * ``@pytest.mark.django_db`` without ``transaction=True``.

    Both leave ``connection.in_atomic_block`` true for the test body. A worker
    thread opening its own connection then cannot see anything the test set up —
    it is uncommitted in another connection's transaction — so the racers never
    contend, every racer "wins", and the property passes without having tested
    concurrency at all. Checking the flag turns that silent pass into a failure
    naming the fix.

    The check runs after the test's own database setup, hence a fixture rather
    than a collection-time hook: the base class is what opens the transaction,
    and it has not opened it yet at collection.
    """
    if request.node.get_closest_marker("concurrency") is None:
        yield
        return

    from django.db import connection

    if connection.in_atomic_block:
        pytest.fail(
            f"{request.node.nodeid} is marked `concurrency` but runs inside a "
            f"wrapping transaction, so threads on separate connections cannot "
            f"observe its data and the race under test cannot occur (design "
            f"§7.1). Use django.test.TransactionTestCase / "
            f"hypothesis.extra.django.TransactionTestCase, or "
            f"@pytest.mark.django_db(transaction=True).",
            pytrace=False,
        )
    yield
