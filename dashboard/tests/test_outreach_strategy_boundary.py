"""Structural authority boundary for Company Bot 3."""

from __future__ import annotations

import ast
from pathlib import Path


def test_outreach_strategy_has_no_action_or_persistence_imports() -> None:
    package = Path(__file__).resolve().parents[1] / "outreach_strategy"
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


def test_outreach_strategy_exposes_no_send_or_approval_method() -> None:
    from dashboard.outreach_strategy.orchestrator import OutreachStrategyOrchestrator

    public = {
        name for name in dir(OutreachStrategyOrchestrator) if not name.startswith("_")
    }
    assert public == {"run"}
