from __future__ import annotations

from django.core.checks import Error, Tags, register

from dashboard.services.runtime_readiness import live_configuration_failures


@register(Tags.security, deploy=True)
def check_live_runtime_configuration(app_configs, **kwargs):
    """Fail a production deploy before startup when required live wiring is absent."""
    failures = live_configuration_failures()
    if not failures:
        return []
    return [
        Error(
            "B2B live runtime is not ready: " + "; ".join(failures),
            hint=(
                "Set the missing values in the private deployment environment. "
                "Do not put credentials in the repository."
            ),
            id="dashboard.E900",
        )
    ]
