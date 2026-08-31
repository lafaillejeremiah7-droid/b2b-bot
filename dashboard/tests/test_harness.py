"""Tests for the test harness itself (task 1.4).

Not feature tests. Every assertion here is about a piece of machinery later tasks
depend on, and each one exists because the failure it catches is *silent*:

* a Hypothesis profile below design §7.2's 100-example floor searches less than
  the design requires and still reports a pass;
* an empty-sourced import-linter contract reports KEPT forever, including on the
  day it is violated;
* a SQLite fallback introduced "just for tests" makes the entire §4.6 plpgsql
  enforcement layer untestable while the suite stays green.

The checks in ``scripts/`` are exercised here as well as in CI, so a developer
running ``pytest`` locally sees the same verdict the build will give.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import settings as hypothesis_settings

from dashboard.tests.hypothesis_profiles import (
    DEFAULT_PROFILE,
    MAX_EXAMPLES_FLOOR,
    PURE_EXAMPLES_BAND,
    STATEFUL_STEP_COUNT_BAND,
    Profile,
    registered_profiles,
    use,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ==========================================================================
# Hypothesis profiles (design §7.2)
# ==========================================================================
class TestHypothesisProfiles:
    def test_every_profile_is_registered_and_selectable(self):
        """`--hypothesis-profile=<name>` must work for every declared name."""
        for member in Profile:
            assert hypothesis_settings.get_profile(member.value) is not None

    @pytest.mark.parametrize("member", list(Profile), ids=lambda m: m.value)
    def test_no_profile_falls_below_the_100_example_floor(self, member: Profile):
        """§7.2: "100 is the floor."

        Parametrized over the enum rather than written as one loop so a new
        profile that drops below the floor names itself in the failure.
        """
        profile = hypothesis_settings.get_profile(member.value)
        assert profile.max_examples >= MAX_EXAMPLES_FLOOR, (
            f"profile {member.value!r} runs {profile.max_examples} examples; "
            f"design §7.2 sets {MAX_EXAMPLES_FLOOR} as the floor for every property."
        )

    def test_pure_profiles_sit_at_the_ends_of_the_200_to_1000_band(self):
        """§7.2: "Cheap pure-function properties (20) run 200-1000."."""
        low, high = PURE_EXAMPLES_BAND
        assert hypothesis_settings.get_profile(Profile.PURE.value).max_examples == low
        assert (
            hypothesis_settings.get_profile(Profile.PURE_THOROUGH.value).max_examples
            == high
        )

    def test_stateful_profiles_sit_at_the_ends_of_the_10_to_50_step_band(self):
        """§7.2: "Database-backed stateful machines run 100 with
        `stateful_step_count` between 10 and 50"."""
        low, high = STATEFUL_STEP_COUNT_BAND
        shallow = hypothesis_settings.get_profile(Profile.STATEFUL.value)
        deep = hypothesis_settings.get_profile(Profile.STATEFUL_DEEP.value)
        assert shallow.stateful_step_count == low
        assert deep.stateful_step_count == high
        assert shallow.max_examples == deep.max_examples == MAX_EXAMPLES_FLOOR

    def test_no_profile_imposes_a_deadline(self):
        """A per-example wall-clock deadline on a database-backed or concurrent
        property produces flaky failures that say nothing about correctness.
        Timing claims belong to §7.5, asserted against a seeded dataset."""
        for member in Profile:
            assert hypothesis_settings.get_profile(member.value).deadline is None

    def test_the_loaded_profile_honours_the_floor(self):
        """Whatever conftest.py loaded for this run must meet the floor.

        Covers the case where someone exports `HYPOTHESIS_PROFILE` to something
        weaker in CI: the run itself then fails rather than quietly searching
        less.
        """
        assert hypothesis_settings.default.max_examples >= MAX_EXAMPLES_FLOOR

    def test_default_profile_is_the_ci_floor_profile(self):
        assert DEFAULT_PROFILE is Profile.CI

    def test_use_applies_the_named_profile_to_a_test(self):
        @use(Profile.PURE)
        def a_property():  # pragma: no cover - never executed
            pass

        applied = a_property._hypothesis_internal_use_settings
        assert applied.max_examples == PURE_EXAMPLES_BAND[0]

    def test_use_allows_a_single_knob_override_without_losing_the_profile(self):
        @use(Profile.STATEFUL, stateful_step_count=25)
        def a_property():  # pragma: no cover - never executed
            pass

        applied = a_property._hypothesis_internal_use_settings
        assert applied.stateful_step_count == 25
        assert applied.max_examples == MAX_EXAMPLES_FLOOR  # inherited

    def test_use_rejects_an_unregistered_profile_name(self):
        """A typo must fail at decoration time, not silently fall back to the
        default — a property that thinks it runs 1000 examples and runs 100 is a
        test whose strength is a fiction."""
        from hypothesis.errors import InvalidArgument

        with pytest.raises(InvalidArgument):
            use("thorough-ish")

    def test_a_bogus_profile_env_var_is_reported_as_a_usage_error(self):
        """A typo'd `HYPOTHESIS_PROFILE` in a CI job must read as a configuration
        mistake and list the valid names.

        Left unhandled, Hypothesis's `InvalidArgument` escapes a plugin hook and
        pytest renders it as an INTERNALERROR traceback that never mentions
        profiles at all. Run in a subprocess because the failure is in
        `pytest_configure`, which cannot be re-entered in-process.
        """
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "HYPOTHESIS_PROFILE": "thorough-ish"},
            check=False,
        )
        combined = completed.stdout + completed.stderr
        assert completed.returncode != 0
        assert "INTERNALERROR" not in combined, combined[-2000:]
        assert "not a registered profile" in combined, combined[-2000:]
        # The message has to name the alternatives, or it is only half a message.
        assert "pure_thorough" in combined, combined[-2000:]


# ==========================================================================
# PostgreSQL only — design §2.2 (there is no SQLite fallback)
# ==========================================================================
#: Every way SQLite gets configured for a Django project or a URL-style DSN.
_SQLITE_CONFIGURATIONS = re.compile(
    r"backends\.sqlite3|sqlite3?:/{2,3}|['\"]sqlite3?['\"]", re.IGNORECASE
)

#: Everything a developer or CI could plausibly configure a database from.
_CONFIGURABLE_FILES = (
    "conftest.py",
    "pytest.ini",
    "pyproject.toml",
    "Makefile",
    ".importlinter",
)


def _configuration_sources() -> list[Path]:
    paths = [REPO_ROOT / name for name in _CONFIGURABLE_FILES]
    for package in ("config", "dashboard", "scripts"):
        paths.extend(
            path
            for path in (REPO_ROOT / package).rglob("*.py")
            if "__pycache__" not in path.parts
        )
    paths.extend(sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")))
    return [path for path in paths if path.is_file()]


class TestNoSqliteFallback:
    """Design §2.2 and §4.6 make PostgreSQL 16 load-bearing, not preferred.

    The schema needs partial unique indexes, ``GENERATED ALWAYS AS ... STORED``
    columns, ten plpgsql triggers, JSONB, ``TIMESTAMPTZ(3)`` and
    ``INSERT ... ON CONFLICT``. SQLite provides none of them. A SQLite test
    fallback would not make the suite faster — it would make every constraint,
    trigger and privilege assertion in tasks 2.x and 3.x silently unenforceable
    while the suite reported green, which is strictly worse than a suite that
    cannot run at all.
    """

    def test_the_configured_engine_is_postgresql(self):
        from django.conf import settings

        assert list(settings.DATABASES) == ["default"]
        assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"

    def test_no_test_time_engine_override(self):
        from django.conf import settings

        test_overrides = settings.DATABASES["default"].get("TEST", {})
        assert "ENGINE" not in test_overrides

    def test_the_engine_is_a_literal_not_an_environment_choice(self):
        """A configurable ENGINE is a SQLite fallback with extra steps."""
        source = (REPO_ROOT / "config" / "settings" / "base.py").read_text(
            encoding="utf-8"
        )
        engine_lines = [
            line for line in source.splitlines() if '"ENGINE"' in line or "'ENGINE'" in line
        ]
        assert len(engine_lines) == 1, engine_lines
        assert "django.db.backends.postgresql" in engine_lines[0]
        assert "os.environ" not in engine_lines[0]

    def test_nothing_in_the_repository_configures_sqlite(self):
        offenders = {}
        for path in _configuration_sources():
            hits = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if _SQLITE_CONFIGURATIONS.search(line)
            ]
            if hits:
                offenders[str(path.relative_to(REPO_ROOT))] = hits
        assert not offenders, (
            f"SQLite configuration found: {offenders}. Design §2.2 makes "
            f"PostgreSQL 16 the only supported backend and §4.6's enforcement "
            f"layer is plpgsql; there is no fallback."
        )


# ==========================================================================
# Architecture checks (design §7.6)
# ==========================================================================
class TestImportLinterContracts:
    """The three §3.0.1 / §3.7.2 contracts are declared, whether or not sourced."""

    EXPECTED_CONTRACTS = {
        "views-never-import-models",
        "adapter-send-chokepoint",
        "release-gate-isolation",
    }

    def _contracts(self) -> dict[str, dict[str, str]]:
        """Read the config through import-linter's own parser.

        Deliberately not configparser here: the point is that *import-linter*
        can read what we wrote. `scripts/check_deferred_activations.py` uses
        configparser, and `test_the_two_parsers_agree` below pins the two views
        together so a config that only one of them understands cannot slip past.
        """
        from importlinter import api

        configuration = api.read_configuration(str(REPO_ROOT / ".importlinter"))
        return {
            options["id"]: options for options in configuration["contracts_options"]
        }

    def test_all_three_contracts_are_declared(self):
        assert set(self._contracts()) == self.EXPECTED_CONTRACTS

    def test_every_contract_is_a_forbidden_contract(self):
        for name, body in self._contracts().items():
            assert body["type"] == "forbidden", name

    def test_the_two_parsers_agree(self):
        from scripts.check_deferred_activations import _contract_sections

        assert set(_contract_sections()) == set(self._contracts())

    def test_lint_imports_actually_evaluates_all_three_contracts(self):
        """Guard against a check that passes by doing nothing.

        `lint-imports` exiting 0 is not evidence on its own — a config naming no
        root packages, or a `python -m` invocation that resolves to a click group
        with no command attached, also exits 0 in silence. Assert the linter
        reports the expected number of contracts *kept*, which it can only do by
        having built the import graph and evaluated each one.
        """
        executable = shutil.which("lint-imports") or str(
            Path(sys.executable).parent / "lint-imports"
        )
        assert Path(executable).exists(), (
            "the `lint-imports` console script is missing; install the [dev] extras"
        )

        completed = subprocess.run(
            [executable],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        expected = f"Contracts: {len(self.EXPECTED_CONTRACTS)} kept, 0 broken."
        assert expected in completed.stdout, completed.stdout

    def test_every_declared_contract_has_a_deferred_activation_entry(self):
        """A contract with no activation entry is one that can pass forever.

        This is the coupling that makes the empty-sourced state safe: a contract
        may be switched off only while something is on the hook for switching it
        on. Adding a fourth contract without an entry fails here.
        """
        from scripts.check_deferred_activations import DEFERRED

        tracked = {
            item.id.split(":", 1)[1]
            for item in DEFERRED
            if item.id.startswith("importlinter:")
        }
        sourced = {
            name
            for name, body in self._contracts().items()
            if body.get("source_modules", "").strip()
        }
        untracked = set(self._contracts()) - tracked - sourced
        assert not untracked, (
            f"contract(s) {sorted(untracked)} are neither sourced nor tracked in "
            f"scripts/check_deferred_activations.py, so nothing will ever notice "
            f"they are switched off."
        )


class TestDeferredActivations:
    def test_no_activation_is_overdue(self):
        from scripts.check_deferred_activations import OVERDUE, evaluate

        overdue = [item.id for item, state in evaluate() if state == OVERDUE]
        assert not overdue, (
            f"{overdue} now have subjects but are still switched off. Run "
            f"`python scripts/check_deferred_activations.py` for the required edit."
        )

    def test_the_check_reports_every_deferred_item(self):
        from scripts.check_deferred_activations import DEFERRED, evaluate

        assert len(evaluate()) == len(DEFERRED)
        assert {item.owning_task for item in DEFERRED} >= {"3.5", "8.1", "11.1", "14.3"}

    def test_an_unsourced_contract_with_subjects_is_reported_overdue(self, monkeypatch):
        """Exercise the OVERDUE branch itself.

        Without this the check could be inverted — reporting PENDING for
        everything forever — and every other assertion here would still pass,
        which is precisely the silent-green failure the check exists to prevent.
        """
        from scripts import check_deferred_activations as check

        overdue = dataclasses.replace(
            self._entry("views-never-import-models"),
            is_due=lambda: True,
            is_activated=lambda: False,
        )
        monkeypatch.setattr(check, "DEFERRED", (overdue,))

        states = {item.id: state for item, state in check.evaluate()}
        assert states[overdue.id] == check.OVERDUE
        assert check.main() == 1

    def test_a_sourced_contract_is_reported_active_even_before_it_is_due(
        self, monkeypatch
    ):
        """Switching a rule on ahead of its owning task is never a failure."""
        from scripts import check_deferred_activations as check

        early = dataclasses.replace(
            self._entry("release-gate-isolation"),
            is_due=lambda: False,
            is_activated=lambda: True,
        )
        monkeypatch.setattr(check, "DEFERRED", (early,))

        states = {item.id: state for item, state in check.evaluate()}
        assert states[early.id] == check.ACTIVE
        assert check.main() == 0

    @staticmethod
    def _entry(suffix: str):
        from scripts.check_deferred_activations import DEFERRED

        return next(item for item in DEFERRED if item.id.endswith(suffix))


class TestImportTimeAssertionCheck:
    """§7.6's import-time assertion step, wired now and a no-op until task 6.1."""

    def test_the_check_passes_today(self):
        from scripts.check_import_time_assertions import main

        assert main() == 0

    def test_it_knows_which_constants_it_is_waiting_for(self):
        from scripts.check_import_time_assertions import GUARDED_CONSTANTS

        by_name = {constant.name: constant for constant in GUARDED_CONSTANTS}
        assert set(by_name) == {"LEGAL_TRANSITIONS", "EVENT_STATE_MAP"}
        assert by_name["LEGAL_TRANSITIONS"].minimum_assertions == 3  # §3.5.1
        assert by_name["EVENT_STATE_MAP"].minimum_assertions == 2  # §3.5.5

    def test_it_counts_module_scope_assertions_only(self, tmp_path):
        """An assertion inside a function fires only when called, which is not
        the guarantee §7.6 claims. The counter must not accept one."""
        import ast

        from scripts.check_import_time_assertions import (
            _module_level_asserts_mentioning,
        )

        module_scope = ast.parse(
            "LEGAL_TRANSITIONS = frozenset()\n"
            "assert len(LEGAL_TRANSITIONS) == 17\n"
        )
        function_scope = ast.parse(
            "LEGAL_TRANSITIONS = frozenset()\n"
            "def check():\n"
            "    assert len(LEGAL_TRANSITIONS) == 17\n"
        )
        assert _module_level_asserts_mentioning(module_scope, "LEGAL_TRANSITIONS") == 1
        assert _module_level_asserts_mentioning(function_scope, "LEGAL_TRANSITIONS") == 0


# ==========================================================================
# pytest configuration
# ==========================================================================
class TestPytestConfiguration:
    def test_markers_used_in_the_suite_are_declared(self, pytestconfig):
        """`--strict-markers` makes this a build failure anyway; asserting it
        here names the marker instead of failing at collection."""
        declared = {
            line.split(":", 1)[0] for line in pytestconfig.getini("markers")
        }
        assert {"migrations", "concurrency", "property", "e2e", "performance"} <= declared

    def test_a_fixed_hypothesis_seed_is_configured(self, pytestconfig):
        """§7.2's iteration floors only buy reproducibility if the seed is fixed:
        a CI counterexample has to be replayable locally."""
        addopts = pytestconfig.getini("addopts")
        assert any(opt.startswith("--hypothesis-seed=") for opt in addopts), addopts

    def test_reuse_db_is_not_enabled_by_default(self, pytestconfig):
        """--reuse-db skips migrations, which would make task 3.5's fresh-migrate
        assertions pass against a schema an older revision built."""
        assert pytestconfig.getoption("reuse_db") is False

    def test_django_settings_module_is_the_real_one(self, pytestconfig):
        assert pytestconfig.getini("DJANGO_SETTINGS_MODULE") == "config.settings.base"


class TestConcurrencyGuard:
    """The §7.1 requirement that concurrency tests run outside any transaction."""

    @pytest.mark.concurrency
    @pytest.mark.django_db(transaction=True)
    def test_a_transaction_true_test_satisfies_the_guard(self):
        """Also a live check that the harness can actually give a concurrency
        test what §7.1 asks for: a real, committed, non-wrapped connection."""
        from django.db import connection

        assert not connection.in_atomic_block

    @pytest.mark.django_db
    def test_an_ordinary_db_test_is_wrapped(self):
        """The contrast case, which is what makes the guard above meaningful."""
        from django.db import connection

        assert connection.in_atomic_block
