"""Structural authority boundary for Company Bot 4."""

from __future__ import annotations

import ast
from pathlib import Path


def test_deal_compliance_has_no_action_or_persistence_imports() -> None:
    package = Path(__file__).resolve().parents[1] / "deal_compliance"
    forbidden = ("django", "dashboard.models", "dashboard.services", "dashboard.adapter", "dashboard.views")
    violations = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                if any(name == item or name.startswith(f"{item}.") for item in forbidden):
                    violations.append(f"{path.name}:{node.lineno}:{name}")
    assert violations == []


def test_deal_orchestrator_exposes_only_run() -> None:
    from dashboard.deal_compliance.orchestrator import DealComplianceOrchestrator
    assert {name for name in dir(DealComplianceOrchestrator) if not name.startswith("_")} == {"run"}
