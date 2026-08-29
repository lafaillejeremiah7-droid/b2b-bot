"""Structural authority boundary for Company Bot 2."""

from __future__ import annotations

import ast
from pathlib import Path


def test_qualification_package_has_no_persistence_or_action_imports() -> None:
    package = Path(__file__).resolve().parents[1] / "qualification"
    forbidden = (
        "django",
        "dashboard.models",
        "dashboard.services",
        "dashboard.adapter",
        "dashboard.views",
    )
    violations = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == item or name.startswith(f"{item}.") for item in forbidden):
                    violations.append(f"{path.name}:{node.lineno}:{name}")
    assert violations == []


def test_qualification_orchestrator_exposes_only_run() -> None:
    from dashboard.qualification.orchestrator import QualificationOrchestrator

    public = {name for name in dir(QualificationOrchestrator) if not name.startswith("_")}
    assert public == {"run"}
