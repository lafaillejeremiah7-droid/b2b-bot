"""Structural authority boundary for Bot 1."""

from __future__ import annotations

import ast
from pathlib import Path


def test_discovery_package_has_no_django_model_service_or_adapter_imports() -> None:
    package = Path(__file__).resolve().parents[1] / "discovery"
    forbidden = (
        "django",
        "dashboard.models",
        "dashboard.services",
        "dashboard.adapter",
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


def test_discovery_orchestrator_exposes_no_outreach_or_money_methods() -> None:
    from dashboard.discovery.orchestrator import DiscoveryOrchestrator

    public = {name for name in dir(DiscoveryOrchestrator) if not name.startswith("_")}
    assert public == {"run"}
