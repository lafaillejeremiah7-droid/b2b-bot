#!/usr/bin/env python
"""CI step: the import-time architecture assertions of design §7.6.

§7.6 names four architecture checks. This script is the second of them:

    Import-time assertions (executed by any test run): `LEGAL_TRANSITIONS` has
    exactly 17 members, no self-pairs, no terminal sources; `EVENT_STATE_MAP` is
    exhaustive over `EventType` and maps nothing to `Released` or
    `Payment_Verified`.

Those assertions matter out of proportion to their size. The two about
`EVENT_STATE_MAP` are, per §3.7.2, the machine-checked form of the claim that
*there is no path from an inbound webhook to a released website or a verified
payment*: "a payment event cannot even ask for either state." An assertion that
lives at module scope fires on any import, so ordinary test collection already
executes it — which is exactly why it needs a step of its own as well. Test
collection can be reduced, skipped, or filtered down to a marker; this step
cannot, so the guarantee does not depend on which subset of the suite ran.

WHY THIS IS A NO-OP TODAY
-------------------------
`LEGAL_TRANSITIONS` and `TERMINAL_STATES` land in task 6.1, `EVENT_STATE_MAP` in
task 6.3. Referencing a module that does not exist yet would make this step fail
from now until then, and a step that is red for twenty tasks gets ignored or
deleted. So the script *discovers* the constants instead of importing a fixed
path: it scans `dashboard/` for the modules that define them, imports each one,
and reports a clean no-op when there are none. It needs no edit at task 6.1 —
declaring the constant is what switches this step on.

Discovery also removes the guess about where task 6.1 will put the module. The
design never states a path.

WHAT IS CHECKED
---------------
1. Every module defining either constant imports cleanly. A module-level
   `assert` that fails raises `AssertionError` during import, which is the check.
2. Each defining module actually *carries* module-level assertions about the
   constant it defines. Point 1 alone would pass on a module that declares
   `LEGAL_TRANSITIONS` with no assertions at all — the assertions cannot fail if
   nobody wrote them — so the shape of the source is checked too, by AST, with
   the minimum counts §3.5.1 and §3.5.5 state (three and two respectively).

Point 2 does not re-derive the invariants; Property 8 and Property 11 (task 6.4)
own that. It asserts that the import-time guard §7.6 relies on exists.
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "dashboard"


@dataclass(frozen=True)
class GuardedConstant:
    """A constant whose module-level assertions §7.6 depends on."""

    name: str
    #: Minimum number of module-level `assert` statements mentioning the
    #: constant, per the design section that specifies them.
    minimum_assertions: int
    design_section: str
    owning_task: str
    expected_assertions: tuple[str, ...]


GUARDED_CONSTANTS: tuple[GuardedConstant, ...] = (
    GuardedConstant(
        name="LEGAL_TRANSITIONS",
        minimum_assertions=3,
        design_section="§3.5.1",
        owning_task="6.1",
        expected_assertions=(
            "exactly 17 members",
            "no self-pairs",
            "no terminal source states",
        ),
    ),
    GuardedConstant(
        name="EVENT_STATE_MAP",
        minimum_assertions=2,
        design_section="§3.5.5",
        owning_task="6.3",
        expected_assertions=(
            "nothing maps to Released (Req 8.10, 8.12)",
            "nothing maps to Payment_Verified (Req 8.5, operator-only)",
        ),
    ),
)


def _module_paths() -> list[Path]:
    """Every importable module file under `dashboard/`, excluding the test suite.

    Tests are excluded because a test module may legitimately reference the
    constants in a string or a helper without being the definition site.
    """
    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and "tests" not in path.parts
    )


def _dotted_name(path: Path) -> str:
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_level_assignments(tree: ast.Module) -> set[str]:
    """Names bound at module scope by `X = ...` or `X: T = ...`."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _module_level_asserts_mentioning(tree: ast.Module, name: str) -> int:
    """Count module-scope `assert` statements whose test mentions `name`.

    Module scope only, and deliberately so: the whole value of these assertions
    is that they fire on *any* import of the module, including a production
    import. The same assertion inside a function body fires only when that
    function is called, which is a different and much weaker guarantee.
    """
    count = 0
    for node in tree.body:
        if not isinstance(node, ast.Assert):
            continue
        mentioned = {
            child.id for child in ast.walk(node.test) if isinstance(child, ast.Name)
        }
        if name in mentioned:
            count += 1
    return count


@dataclass
class Finding:
    module: str
    constant: GuardedConstant
    assertion_count: int


def collect_findings() -> tuple[list[Finding], list[str]]:
    """Return (definition sites found, problems).

    Problems are returned rather than raised so a single run reports every one.
    """
    findings: list[Finding] = []
    problems: list[str] = []

    for path in _module_paths():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(REPO_ROOT)}: syntax error: {exc}")
            continue

        assigned = _module_level_assignments(tree)
        for constant in GUARDED_CONSTANTS:
            if constant.name not in assigned:
                continue
            findings.append(
                Finding(
                    module=_dotted_name(path),
                    constant=constant,
                    assertion_count=_module_level_asserts_mentioning(
                        tree, constant.name
                    ),
                )
            )

    return findings, problems


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")
    sys.path.insert(0, str(REPO_ROOT))

    findings, problems = collect_findings()

    print("Import-time architecture assertions (design §7.6)")
    print("=" * 64)

    if not findings:
        # The switched-off state, reported clearly rather than silently.
        for constant in GUARDED_CONSTANTS:
            print(
                f"  PENDING  {constant.name:<20} not declared yet "
                f"— task {constant.owning_task} ({constant.design_section})"
            )
        print()
        print(
            "No-op: neither constant exists yet, so there are no import-time\n"
            "assertions to execute. This step needs no edit when they land —\n"
            "declaring the constant is what switches it on. Tasks 6.1 and 6.3\n"
            "own the declarations; task 6.4 owns Properties 8 and 11."
        )
        return 0

    # Django has to be configured before importing anything under `dashboard`
    # that touches models or settings.
    import django

    django.setup()

    exit_code = 0
    for finding in findings:
        constant = finding.constant
        label = f"{constant.name} in {finding.module}"

        # (1) Import the module. A failing module-level assert surfaces here.
        try:
            importlib.import_module(finding.module)
        except AssertionError as exc:
            exit_code = 1
            print(f"  FAILED   {label}")
            print(
                f"           an import-time assertion of {constant.design_section} "
                f"fired: {exc or '<no message>'}"
            )
            print(f"           expected invariants: {', '.join(constant.expected_assertions)}")
            continue
        except Exception as exc:  # noqa: BLE001 - any import failure is a failure
            exit_code = 1
            print(f"  FAILED   {label}")
            print(f"           module did not import: {type(exc).__name__}: {exc}")
            continue

        # (2) The assertions have to actually be there.
        if finding.assertion_count < constant.minimum_assertions:
            exit_code = 1
            print(f"  FAILED   {label}")
            print(
                f"           found {finding.assertion_count} module-level assert "
                f"statement(s) mentioning {constant.name}; "
                f"{constant.design_section} specifies {constant.minimum_assertions}."
            )
            print(
                f"           §7.6 relies on these firing on any import. Missing: "
                f"{', '.join(constant.expected_assertions)}"
            )
            print(
                "           They must be at module scope — inside a function they "
                "fire only when called."
            )
            continue

        print(
            f"  OK       {label} "
            f"({finding.assertion_count} module-level assertions, "
            f"{constant.design_section})"
        )

    for problem in problems:
        exit_code = 1
        print(f"  ERROR    {problem}")

    print()
    print("PASS" if exit_code == 0 else "FAIL")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
