"""Structural authority boundary for Company Bot 5."""

from __future__ import annotations

import ast
from pathlib import Path


def test_site_production_has_no_publish_or_persistence_imports() -> None:
    package = Path(__file__).resolve().parents[1] / "site_production"
    forbidden = ("django", "dashboard.models", "dashboard.services", "dashboard.adapter", "dashboard.views")
    violations = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                if any(name == item or name.startswith(f"{item}.") for item in forbidden): violations.append(f"{path.name}:{node.lineno}:{name}")
    assert violations == []


def test_site_production_orchestrator_exposes_only_run() -> None:
    from dashboard.site_production.orchestrator import SiteProductionOrchestrator
    assert {name for name in dir(SiteProductionOrchestrator) if not name.startswith("_")} == {"run"}
