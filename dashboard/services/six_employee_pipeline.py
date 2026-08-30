from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from dashboard.services.outreach_templates import OutreachContext, render_google_maps_outreach


@dataclass
class Lead:
    name: str
    email: str
    company: str = ""
    website: str = ""
    source: str = "manual"
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    lead: Lead
    subject: str
    body: str
    approved_to_send: bool
    stages: list[dict[str, Any]]


def outbound_signature() -> str:
    sender = getattr(settings, "OUTREACH_SENDER_NAME", "Jeremiah Lafaille").strip()
    phone = getattr(settings, "OUTREACH_PHONE", "").strip()
    email = getattr(settings, "OUTREACH_EMAIL", "").strip()
    lines = ["Best,", sender, "Website Design & Digital Presence"]
    if phone:
        lines.append(f"Phone Number: {phone}")
    if email:
        lines.append(f"Email: {email}")
    return "\n".join(lines)


class Scout:
    name = "Scout"

    def run(self, lead: Lead) -> dict[str, Any]:
        return {
            "employee": self.name,
            "status": "complete",
            "output": f"Selected {lead.email} from {lead.source} for processing.",
        }


class Researcher:
    name = "Researcher"

    def run(self, lead: Lead) -> dict[str, Any]:
        internal_test = lead.source == "internal_gmail_test"
        lead.notes["internal_test"] = internal_test
        return {
            "employee": self.name,
            "status": "complete",
            "output": "Marked recipient as an internal test contact." if internal_test else "Research record created.",
        }


class Qualifier:
    name = "Qualifier"

    def run(self, lead: Lead) -> dict[str, Any]:
        qualified = bool(lead.email) and (lead.notes.get("internal_test") is True)
        lead.notes["qualified"] = qualified
        return {
            "employee": self.name,
            "status": "complete",
            "output": "Approved safe self-test." if qualified else "Rejected: this minimal pipeline only permits internal tests.",
        }


class Personalizer:
    name = "Personalizer"

    def run(self, lead: Lead) -> dict[str, Any]:
        first_name = (lead.name or "there").split()[0]

        if lead.source == "google_maps":
            context = OutreachContext(
                business_name=lead.company or lead.name,
                first_name=first_name,
                source="google_maps",
                website=lead.website,
                verified_no_website=bool(lead.notes.get("verified_no_website", False)),
                observations=tuple(lead.notes.get("website_observations", ())),
                preview_url=str(lead.notes.get("preview_url", "")),
            )
            subject, body = render_google_maps_outreach(context)
            output = "Created Google Maps outreach using the verified website-status template."
        else:
            subject = "B2B Bot: outbound pipeline test"
            body = (
                f"Hi {first_name},\n\n"
                "This is an internal test from the B2B Bot company. The six outbound employees "
                "processed this message before the send step.\n\n"
                "Outbound pipeline: Scout -> Researcher -> Qualifier -> Personalizer -> Sales Bot -> Manager.\n"
                "Employee #7, Closer, activates only after a prospect replies.\n\n"
                "No sales outreach was performed; this message was prepared only to verify the workflow.\n\n"
                f"{outbound_signature()}"
            )
            output = "Created personalized internal test email."

        lead.notes["subject"] = subject
        lead.notes["body"] = body
        return {
            "employee": self.name,
            "status": "complete",
            "output": output,
        }


class SalesBot:
    name = "Sales Bot"

    def run(self, lead: Lead) -> dict[str, Any]:
        approved = bool(lead.notes.get("qualified") and lead.notes.get("subject") and lead.notes.get("body"))
        lead.notes["approved_to_send"] = approved
        return {
            "employee": self.name,
            "status": "complete",
            "output": "Approved message for Gmail submission." if approved else "Blocked message before send.",
        }


class Manager:
    name = "Manager"

    def run(self, lead: Lead, prior_stages: list[dict[str, Any]]) -> dict[str, Any]:
        passed = all(stage["status"] == "complete" for stage in prior_stages) and bool(lead.notes.get("approved_to_send"))
        return {
            "employee": self.name,
            "status": "complete",
            "output": "Outbound pipeline passed all six pre-send stages." if passed else "Pipeline requires review.",
            "pipeline_passed": passed,
        }


class SixEmployeePipeline:
    """Outbound team within the seven-employee company.

    Employees 1-6 prepare and approve outreach. Employee #7 (Closer) lives in
    ``dashboard.services.closer`` and is invoked only when an inbound reply exists.
    External delivery remains the responsibility of an adapter.
    """

    def run(self, lead: Lead) -> PipelineResult:
        stages: list[dict[str, Any]] = []
        stages.append(Scout().run(lead))
        stages.append(Researcher().run(lead))
        stages.append(Qualifier().run(lead))
        stages.append(Personalizer().run(lead))
        stages.append(SalesBot().run(lead))
        manager_result = Manager().run(lead, stages)
        stages.append(manager_result)

        return PipelineResult(
            lead=lead,
            subject=lead.notes.get("subject", ""),
            body=lead.notes.get("body", ""),
            approved_to_send=bool(manager_result.get("pipeline_passed")),
            stages=stages,
        )
