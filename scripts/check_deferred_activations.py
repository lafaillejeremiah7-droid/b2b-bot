#!/usr/bin/env python
"""CI step: nothing deferred by task 1.4 is allowed to stay deferred silently.

THE PROBLEM THIS EXISTS TO SOLVE
--------------------------------
Task 1.4 builds the enforcement machinery for rules whose subjects do not exist
yet. Three import-linter contracts name modules that tasks 8.1, 11.1 and 14.3
will create; one pytest marker selects the migration tests task 3.5 will write.
Each is therefore declared in its switched-off form:

  * a `forbidden` contract with an empty `source_modules` list is vacuously
    true — import-linter reports it KEPT and `lint-imports` exits 0;
  * `pytest -m migrations` with nothing carrying the marker collects zero tests.

Both are the *correct* state today and both are indistinguishable, in CI output,
from the state where the rule has real subjects and is not being enforced. That
is the whole failure mode: `lint-imports` would go on printing "3 kept, 0 broken"
on the day a view starts importing a model, and the migration step would go on
printing a green skip on the day the ten triggers of §4.6 ship untested.

Comments in `.importlinter` name the owning task, but a comment is not a build
step. This script is.

HOW IT WORKS
------------
Each deferred item declares two predicates over the working tree:

  `is_due`        — have the things this rule constrains appeared?
  `is_activated`  — has the rule been switched on?

Due and not activated is a build failure, reported with the owning task number
and the precise edit required. Nothing else fails: not-due is PENDING, activated
is ACTIVE, and activated-before-due is ACTIVE too (switching a rule on early is
never wrong).

The `is_due` predicates are deliberately coarse — "does any module exist under
`dashboard/views/`", "does any migration contain CREATE TRIGGER" — rather than
matching file names the later tasks have not chosen yet. A coarse predicate can
fire a task early, which costs a two-line edit. A predicate keyed on a guessed
filename fails to fire at all, which costs the guarantee.
"""

from __future__ import annotations

import ast
import configparser
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
IMPORTLINTER_CONFIG = REPO_ROOT / ".importlinter"
DASHBOARD = REPO_ROOT / "dashboard"


# --------------------------------------------------------------------------
# Working-tree predicates
# --------------------------------------------------------------------------
def _python_modules_in(package: Path) -> list[Path]:
    """Real modules in a package — anything but `__init__.py` and caches."""
    if not package.is_dir():
        return []
    return sorted(
        path
        for path in package.rglob("*.py")
        if path.name != "__init__.py" and "__pycache__" not in path.parts
    )


def _source_files(under: Path | None = None) -> list[Path]:
    root = under if under is not None else DASHBOARD
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _any_source_matches(pattern: str, *, under: Path | None = None) -> bool:
    """Text search across the app's Python sources.

    Only used where the thing being looked for is *text* — SQL inside a
    migration, a pytest marker. Never for "does this symbol exist": prose in a
    docstring matches a text search, and task 1.1's placeholder ``__init__``
    docstrings describe every symbol these predicates care about. Symbol
    existence goes through the AST helpers below.
    """
    regex = re.compile(pattern, re.IGNORECASE)
    for path in _source_files(under):
        if regex.search(path.read_text(encoding="utf-8")):
            return True
    return False


def _defines(names: frozenset[str], node_types: tuple[type, ...]) -> bool:
    """True when any source file *defines* one of `names` as one of `node_types`.

    AST-based rather than textual, because the alternative gives false positives
    on documentation. `dashboard/services/__init__.py` already contains the
    sentence "the sole writer of ``release_authorizations``", and a text search
    for that name would report the release-gate contract overdue at task 1.1 —
    a check that cries wolf on its own scaffolding gets switched off in a week.
    A definition is the thing a contract can actually constrain.
    """
    for path in _source_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, node_types) and getattr(node, "name", None) in names:
                return True
    return False


def _defines_class(*names: str) -> bool:
    return _defines(frozenset(names), (ast.ClassDef,))


def _defines_callable(*names: str) -> bool:
    return _defines(frozenset(names), (ast.FunctionDef, ast.AsyncFunctionDef))


def _contract_sections() -> dict[str, dict[str, str]]:
    parser = configparser.ConfigParser()
    parser.read(IMPORTLINTER_CONFIG, encoding="utf-8")
    return {
        section.split(":", 2)[2]: dict(parser[section])
        for section in parser.sections()
        if section.startswith("importlinter:contract:")
    }


def _contract_is_sourced(contract_id: str) -> bool:
    """True when the named contract has at least one entry in `source_modules`."""
    sections = _contract_sections()
    if contract_id not in sections:
        raise SystemExit(
            f"BUG in {Path(__file__).name}: .importlinter declares no contract "
            f"`{contract_id}`. Known contracts: {', '.join(sorted(sections)) or '(none)'}. "
            f"If a contract was renamed, rename it here too — do not delete the entry."
        )
    return bool(sections[contract_id].get("source_modules", "").strip())


def _marker_is_used(marker: str) -> bool:
    """True when any test module applies `marker`.

    Matches both `@pytest.mark.<marker>` and the `pytestmark = pytest.mark.<marker>`
    module-level form, since task 3.5's assertions are Django `TestCase`
    subclasses and the module-level form is the idiomatic way to mark those.
    """
    return _any_source_matches(rf"mark\.{re.escape(marker)}\b")


# --------------------------------------------------------------------------
# The deferred items
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DeferredActivation:
    id: str
    owning_task: str
    what: str
    #: Have the things this rule constrains appeared?
    is_due: Callable[[], bool]
    #: Human-readable form of `is_due`, for the failure message.
    due_when: str
    #: Has the rule been switched on?
    is_activated: Callable[[], bool]
    #: The exact edit required, printed on failure.
    activation: str


DEFERRED: tuple[DeferredActivation, ...] = (
    DeferredActivation(
        id="importlinter:views-never-import-models",
        owning_task="8.1",
        what="Views may not import models directly (design §3.0.1 rule 1, §7.6)",
        # Due as soon as there is a view at all. `dashboard/views/__init__.py` is
        # task 1.1's docstring-only placeholder, so any *other* module under that
        # package means the view layer has started.
        is_due=lambda: bool(_python_modules_in(DASHBOARD / "views")),
        due_when="any module other than __init__.py exists under dashboard/views/",
        is_activated=lambda: _contract_is_sourced("views-never-import-models"),
        activation=(
            "in .importlinter, set\n"
            "               source_modules =\n"
            "                   dashboard.views\n"
            "               forbidden_modules =\n"
            "                   dashboard.models\n"
            "           Views needing model types for annotations use\n"
            "           `if TYPE_CHECKING:` plus one enumerated `ignore_imports`\n"
            "           entry each — never a widened contract."
        ),
    ),
    DeferredActivation(
        id="importlinter:adapter-send-chokepoint",
        owning_task="11.1",
        what=(
            "Only outreach_controller may reach the adapter send operations "
            "(design §3.6.1 layer 2, §7.6)"
        ),
        # Due once either send operation of §3.14.1 is *defined* — that is the
        # first moment a module could import and call it. Task 7.1 defines them
        # on the ABC; task 11.1 adds the single permitted caller. Firing at 7.1
        # rather than 11.1 is the safe direction: it costs a two-line edit and
        # means the contract is live before the caller exists.
        is_due=lambda: _defines_callable("send_prospect_email", "log_outbound_call"),
        due_when="send_prospect_email or log_outbound_call is defined in dashboard/",
        is_activated=lambda: _contract_is_sourced("adapter-send-chokepoint"),
        activation=(
            "in .importlinter, list every layer that must not reach the send\n"
            "           operations in `source_modules` and the adapter send module in\n"
            "           `forbidden_modules`. Leave `outreach_controller` OUT of the\n"
            "           source list rather than exempting it: an `ignore_imports`\n"
            "           hole gets copied, an omission states the rule."
        ),
    ),
    DeferredActivation(
        id="importlinter:release-gate-isolation",
        owning_task="14.3",
        what=(
            "Only release_gate may authorize or deliver "
            "(design §3.7.2 layer 1, Requirement 8.10, §7.6)"
        ),
        # Due once the `ReleaseAuthorization` model class exists (task 2.3) —
        # from that moment some module could import it, and §3.7.2's "exactly one
        # writer" claim stops being vacuous. Keyed on the class definition, not
        # on the name appearing in text: task 1.1's own docstrings mention
        # `release_authorizations`.
        is_due=lambda: _defines_class("ReleaseAuthorization"),
        due_when="the ReleaseAuthorization model class is defined in dashboard/",
        is_activated=lambda: _contract_is_sourced("release-gate-isolation"),
        activation=(
            "in .importlinter, copy the contract design §3.7.2 already writes out\n"
            "           in full: source_modules = dashboard.views,\n"
            "           dashboard.adapter.events, dashboard.services.payment_verifier,\n"
            "           dashboard.services.outreach_controller,\n"
            "           dashboard.services.notification_service; forbidden_modules =\n"
            "           dashboard.models.release_authorization."
        ),
    ),
    DeferredActivation(
        id="pytest-marker:migrations",
        owning_task="3.5",
        what=(
            "The fresh-migrate schema and privilege assertions "
            "(design §7.4, §7.6) run as their own CI step"
        ),
        # Due once a migration installs a trigger. That is the point at which
        # there is an enforcement layer whose deployment can be asserted — and
        # §7.4 is blunt about why it must be: "the enforcement layers are only
        # real if they are actually deployed."
        is_due=lambda: _any_source_matches(
            r"CREATE\s+(OR\s+REPLACE\s+)?TRIGGER", under=DASHBOARD / "migrations"
        ),
        due_when="any migration contains CREATE TRIGGER",
        is_activated=lambda: _marker_is_used("migrations"),
        activation=(
            "mark task 3.5's tests with `@pytest.mark.migrations` (or a\n"
            "           module-level `pytestmark = pytest.mark.migrations` for the\n"
            "           TestCase form). The CI step `pytest -m migrations` already\n"
            "           exists and currently tolerates an empty selection; once the\n"
            "           marker is in use it stops being empty and that tolerance\n"
            "           becomes unreachable."
        ),
    ),
)


PENDING, ACTIVE, OVERDUE = "PENDING", "ACTIVE", "OVERDUE"


def evaluate() -> list[tuple[DeferredActivation, str]]:
    """Classify every deferred item. Importable, so pytest exercises this too."""
    results: list[tuple[DeferredActivation, str]] = []
    for item in DEFERRED:
        if item.is_activated():
            state = ACTIVE
        elif item.is_due():
            state = OVERDUE
        else:
            state = PENDING
        results.append((item, state))
    return results


def main() -> int:
    results = evaluate()

    print("Deferred activations declared by task 1.4")
    print("=" * 72)
    for item, state in results:
        print(f"  {state:<8} task {item.owning_task:<5} {item.id}")
        print(f"           {item.what}")
        if state == PENDING:
            print(f"           not due yet: due when {item.due_when}")
        elif state == OVERDUE:
            print(f"           DUE: {item.due_when}")
            print(f"           REQUIRED EDIT (task {item.owning_task}): {item.activation}")
        print()

    overdue = [item for item, state in results if state == OVERDUE]
    if overdue:
        print(
            f"FAIL: {len(overdue)} rule(s) now have subjects but are still switched\n"
            f"off. Each was left switched off by task 1.4 because its subjects did\n"
            f"not exist; they exist now, so the rule is not being enforced. Switch\n"
            f"it on as described above.\n\n"
            f"Do not silence this check by deleting the entry. An empty-sourced\n"
            f"contract reports KEPT and `lint-imports` exits 0 — this step is the\n"
            f"only thing standing between that and a rule nobody notices is off."
        )
        return 1

    pending = sum(1 for _, state in results if state == PENDING)
    active = sum(1 for _, state in results if state == ACTIVE)
    print(f"PASS: {active} active, {pending} pending, 0 overdue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
