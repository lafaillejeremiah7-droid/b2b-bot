from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from dashboard.services.confirmation import mint_confirmation
from dashboard.services.dashboard_state import (
    company_kitchen_snapshot,
    first_pending_invoice,
    integration_readiness,
    recent_lead_dishes,
    suppression_count,
)
from dashboard.services.runtime_readiness import company_is_ready


@dataclass(frozen=True)
class EmployeeCard:
    number: int
    name: str
    kitchen_role: str
    actual_role: str
    status: str
    status_label: str
    detail: str


def _employee_cards() -> tuple[EmployeeCard, ...]:
    maps_ready = bool(settings.GOOGLE_MAPS_API_KEY)
    no_site_ready = bool(settings.SERPAPI_API_KEY)
    identity_ready = bool(settings.OUTREACH_EMAIL and settings.OUTREACH_PHONE)
    yahoo_ready = bool(
        settings.YAHOO_SMTP_USERNAME and settings.YAHOO_SMTP_APP_PASSWORD
    )
    stripe_ready = bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET)
    live_mode = settings.PIPELINE_ADAPTER_MODE == "live"

    return (
        EmployeeCard(
            1,
            "Scout",
            "Ingredient Scout",
            "Discovery",
            "ready" if maps_ready else "setup",
            "Ready" if maps_ready else "Needs Maps key",
            "Finds real businesses through Google Places and filters closed locations.",
        ),
        EmployeeCard(
            2,
            "Researcher",
            "Prep Analyst",
            "Evidence",
            "ready" if no_site_ready else "partial",
            "Ready" if no_site_ready else "Partial",
            "Inspects public evidence; no-website verification needs independent search.",
        ),
        EmployeeCard(
            3,
            "Qualifier",
            "Quality Checker",
            "Quality gate",
            "ready",
            "Ready",
            "Scores verified evidence deterministically. Weak leads stop instead of advancing.",
        ),
        EmployeeCard(
            4,
            "Personalizer",
            "Recipe Designer",
            "Message builder",
            "ready" if identity_ready else "setup",
            "Ready" if identity_ready else "Needs identity",
            "Builds grounded outreach from verified Researcher evidence and sender identity.",
        ),
        EmployeeCard(
            5,
            "Sales Bot",
            "Line Cook",
            "Delivery gate",
            "ready" if yahoo_ready and identity_ready and live_mode else "setup",
            "Live" if yahoo_ready and identity_ready and live_mode else "Not live",
            "Revalidates the full chain, checks suppression again, then sends through Yahoo SMTP.",
        ),
        EmployeeCard(
            6,
            "Manager",
            "Service Coordinator",
            "Pipeline control",
            "ready",
            "Ready",
            "Keeps the eight-employee sequence ordered and exposes the true root blocker.",
        ),
        EmployeeCard(
            7,
            "Closer",
            "Finishing Chef",
            "Reply + invoice",
            "ready" if stripe_ready and yahoo_ready else "partial",
            "Ready" if stripe_ready and yahoo_ready else "Needs integrations",
            "Classifies replies, persists suppression, and prepares approved Stripe invoice links.",
        ),
        EmployeeCard(
            8,
            "Boss",
            "Head Chef",
            "Supervisor",
            "ready",
            "Ready",
            "Makes the final operating review without bypassing evidence, approvals, or safety gates.",
        ),
    )


@login_required
def company_dashboard(request):
    cards = _employee_cards()
    integrations = integration_readiness()
    configured_integrations = sum(item.configured is True for item in integrations)
    snapshot = company_kitchen_snapshot()
    pending_invoice = first_pending_invoice()
    invoice_send_token = (
        mint_confirmation(
            request.session,
            action="invoice.send",
            target_id=pending_invoice.invoice_id,
        )
        if pending_invoice
        else ""
    )
    return render(
        request,
        "dashboard/company.html",
        {
            "employees": cards,
            "employee_count": len(cards),
            "suppression_count": suppression_count(),
            "configured_integrations": configured_integrations,
            "integration_count": len(integrations),
            "integrations": integrations,
            "company_live_configured": company_is_ready(),
            "snapshot": snapshot,
            "recent_dishes": recent_lead_dishes(),
            "pending_invoice": pending_invoice,
            "invoice_send_token": invoice_send_token,
        },
    )
