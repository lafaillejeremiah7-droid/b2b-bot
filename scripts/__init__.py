"""Build-check scripts run as their own CI steps (design §7.6).

A package rather than a directory of loose files so the checks are importable —
``dashboard/tests/test_harness.py`` calls into them directly, which means the
checks themselves are exercised by ``pytest`` and not only by the CI workflow.

Not shipped: this package is absent from ``[tool.setuptools] packages`` in
pyproject.toml on purpose. It is repository tooling, not part of the deployable.
"""
