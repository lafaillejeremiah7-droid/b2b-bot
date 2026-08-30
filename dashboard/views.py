from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from dashboard.models import OutreachSuppression


@dataclass(frozen=True)
class EmployeeCard:
    number: int
    name: str
    role: str
    status: str
    status_label: str
    detail: str
    formation_row: int


def _employee_cards() -> tuple[EmployeeCard, ...]:
    maps_ready = bool(settings.GOOGLE_MAPS_API_KEY)
    no_site_ready = bool(settings.SERPAPI_API_KEY)
    identity_ready = bool(settings.OUTREACH_EMAIL and settings.OUTREACH_PHONE)

    return (
        EmployeeCard(1, "Scout", "Discovery", "ready" if maps_ready else "setup", "Ready" if maps_ready else "Needs Maps key", "Official Google Places discovery; closed businesses are filtered.", 4),
        EmployeeCard(2, "Researcher", "Evidence", "ready" if no_site_ready else "partial", "Ready" if no_site_ready else "Website path ready", "Website inspection plus independently verified no-website research." if no_site_ready else "Existing-site research works; no-website search needs SERPAPI_API_KEY.", 4),
        EmployeeCard(3, "Qualifier", "Quality gate", "ready", "Ready", "Deterministic evidence scoring; weak or unsupported leads stop here.", 3),
        EmployeeCard(4, "Personalizer", "Message builder", "ready" if identity_ready else "setup", "Ready" if identity_ready else "Needs sender identity", "Creates outreach only from verified Researcher evidence.", 3),
        EmployeeCard(5, "Sales Bot", "Delivery gate", "ready", "Adapter ready", "Requires digest-bound clearance and a final suppression check before Gmail.", 2),
        EmployeeCard(6, "Manager", "Pipeline control", "ready", "Ready", "Stops downstream work after any blocked, failed, or skipped stage.", 2),
        EmployeeCard(7, "Closer", "Reply handler", "ready", "Ready", "Classifies replies, pauses automation, and persists opt-outs/negative replies.", 1),
        EmployeeCard(8, "Boss", "Supervisor", "ready", "Ready", "Audits outcomes, prioritizes next actions, and computes worker KPI snapshots.", 0),
    )


@login_required
def company_dashboard(request):
    cards = _employee_cards()
    integrations = (
        {"name": "Google Places", "configured": bool(settings.GOOGLE_MAPS_API_KEY), "purpose": "Scout discovery"},
        {"name": "Independent web search", "configured": bool(settings.SERPAPI_API_KEY), "purpose": "No-website verification"},
        {"name": "Outbound identity", "configured": bool(settings.OUTREACH_EMAIL and settings.OUTREACH_PHONE), "purpose": "Professional email signature"},
        {"name": "Gmail OAuth", "configured": None, "purpose": "Injected at send runtime; never stored in the repo"},
    )
    return render(
        request,
        "dashboard/company.html",
        {
            "employees": cards,
            "employee_count": len(cards),
            "suppression_count": OutreachSuppression.objects.count(),
            "configured_integrations": sum(item["configured"] is True for item in integrations),
            "integration_count": len(integrations),
            "integrations": integrations,
            "telemetry_persisted": False,
        },
    )
