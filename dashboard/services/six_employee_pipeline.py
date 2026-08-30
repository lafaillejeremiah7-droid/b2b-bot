from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dashboard.services.discovery_handoff import (
    verify_research_handoff,
    verify_scout_handoff,
)
from dashboard.services.outreach_clearance import verify_outreach_clearance
from dashboard.services.outreach_templates import (
    OutreachContext,
    professional_signature,
    render_google_maps_outreach,
)

STATUS_COMPLETE = "complete"
STATUS_BLOCKED = "blocked"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


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


def _stage(employee: str, status: str, output: str, **extra: Any) -> dict[str, Any]:
    return {"employee": employee, "status": status, "output": output, **extra}


def _skip(employee: str, reason: str) -> dict[str, Any]:
    return _stage(employee, STATUS_SKIPPED, reason)


class Scout:
    name = "Scout"

    def run(self, lead: Lead) -> dict[str, Any]:
        if lead.source == "internal_gmail_test":
            lead.notes["scout_verified"] = True
            return _stage(self.name, STATUS_COMPLETE, "Selected approved internal Gmail test lead.")

        if lead.source != "google_maps":
            lead.notes["scout_verified"] = False
            return _stage(self.name, STATUS_BLOCKED, "Unsupported lead source; Scout requires Google Maps discovery evidence.")

        verified = verify_scout_handoff(lead)
        lead.notes["scout_verified"] = verified
        if not verified:
            return _stage(
                self.name,
                STATUS_BLOCKED,
                "Google Maps lead is missing a valid, digest-verified Scout handoff.",
            )
        return _stage(self.name, STATUS_COMPLETE, "Verified Scout Google Maps discovery handoff integrity.")


class Researcher:
    name = "Researcher"

    def run(self, lead: Lead) -> dict[str, Any]:
        if not lead.notes.get("scout_verified"):
            lead.notes["research_verified"] = False
            return _stage(self.name, STATUS_BLOCKED, "Scout handoff was not verified.")

        if lead.source == "internal_gmail_test":
            lead.notes["internal_test"] = True
            lead.notes["research_verified"] = True
            return _stage(self.name, STATUS_COMPLETE, "Marked recipient as an internal test contact.")

        if lead.source != "google_maps":
            lead.notes["research_verified"] = False
            return _stage(self.name, STATUS_BLOCKED, "Researcher only accepts verified Google Maps or internal-test leads.")

        if not verify_research_handoff(lead):
            lead.notes["research_verified"] = False
            return _stage(
                self.name,
                STATUS_BLOCKED,
                "Researcher is missing a valid digest-verified contact/site handoff tied to Scout's discovery.",
            )

        verified_no_website = bool(lead.notes.get("verified_no_website"))
        website_verified = bool(lead.notes.get("website_verified"))
        observations = tuple(
            item.strip()
            for item in lead.notes.get("website_observations", ())
            if isinstance(item, str) and item.strip()
        )

        if verified_no_website:
            valid_site_evidence = not bool(lead.website)
        else:
            valid_site_evidence = bool(lead.website) and website_verified and len(observations) >= 2

        contact_verified = bool(lead.email and lead.notes.get("contact_verified", False))
        research_verified = valid_site_evidence and contact_verified
        lead.notes["website_observations"] = list(observations)
        lead.notes["research_verified"] = research_verified

        if not research_verified:
            return _stage(
                self.name,
                STATUS_BLOCKED,
                "Research evidence failed semantic validation after digest verification.",
            )
        return _stage(self.name, STATUS_COMPLETE, "Verified Researcher contact and website-status evidence.")


class Qualifier:
    name = "Qualifier"

    def run(self, lead: Lead) -> dict[str, Any]:
        if not lead.notes.get("research_verified"):
            lead.notes["qualified"] = False
            return _stage(self.name, STATUS_BLOCKED, "Researcher handoff is not verified.")

        if lead.notes.get("suppressed") or lead.notes.get("opted_out"):
            lead.notes["qualified"] = False
            return _stage(self.name, STATUS_BLOCKED, "Lead is suppressed or opted out.")

        if lead.source == "internal_gmail_test":
            lead.notes["qualified"] = bool(lead.email)
            return _stage(self.name, STATUS_COMPLETE, "Approved safe internal self-test.")

        evidence_score = 0
        evidence_score += int(bool(lead.email))
        evidence_score += int(bool(lead.company or lead.name))
        evidence_score += int(bool(lead.notes.get("research_verified")))
        evidence_score += int(bool(lead.notes.get("verified_no_website") or lead.website))
        qualified = evidence_score == 4
        lead.notes["qualification_score"] = evidence_score
        lead.notes["qualified"] = qualified

        if not qualified:
            return _stage(self.name, STATUS_BLOCKED, f"Lead failed deterministic qualification ({evidence_score}/4).")
        return _stage(self.name, STATUS_COMPLETE, "Lead passed deterministic qualification (4/4).")


class Personalizer:
    name = "Personalizer"

    def run(self, lead: Lead) -> dict[str, Any]:
        if not lead.notes.get("qualified"):
            return _stage(self.name, STATUS_SKIPPED, "Qualifier did not approve this lead.")

        first_name = (lead.name or "there").split()[0]
        try:
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
                output = "Created Google Maps outreach from verified evidence."
            else:
                subject = "B2B Bot: outbound pipeline test"
                body = (
                    f"Hi {first_name},\n\n"
                    "This is an internal test from the B2B Bot company. The six outbound employees "
                    "processed this message before the send step.\n\n"
                    "Outbound pipeline: Scout -> Researcher -> Qualifier -> Personalizer -> Sales Bot -> Manager.\n"
                    "Employee #7, Closer, activates only after a prospect replies.\n\n"
                    "No sales outreach was performed; this message was prepared only to verify the workflow.\n\n"
                    f"{professional_signature()}"
                )
                output = "Created personalized internal test email."
        except ValueError as exc:
            lead.notes.pop("subject", None)
            lead.notes.pop("body", None)
            return _stage(self.name, STATUS_FAILED, f"Personalization failed closed: {exc}")

        lead.notes["subject"] = subject
        lead.notes["body"] = body
        return _stage(self.name, STATUS_COMPLETE, output)


class SalesBot:
    name = "Sales Bot"

    def run(self, lead: Lead) -> dict[str, Any]:
        if not lead.notes.get("qualified"):
            lead.notes["approved_to_send"] = False
            return _stage(self.name, STATUS_SKIPPED, "Lead was not qualified.")
        if not lead.notes.get("subject") or not lead.notes.get("body"):
            lead.notes["approved_to_send"] = False
            return _stage(self.name, STATUS_BLOCKED, "No valid personalized message is available.")
        if lead.notes.get("suppressed") or lead.notes.get("opted_out"):
            lead.notes["approved_to_send"] = False
            return _stage(self.name, STATUS_BLOCKED, "Outreach is suppressed for this lead.")

        internal_test = lead.source == "internal_gmail_test"
        external_clearance = verify_outreach_clearance(lead) if not internal_test else False
        approved = internal_test or external_clearance
        lead.notes["approved_to_send"] = approved
        if not approved:
            return _stage(
                self.name,
                STATUS_BLOCKED,
                "External outreach is missing a valid digest-bound clearance for this recipient and research evidence.",
            )
        return _stage(self.name, STATUS_COMPLETE, "Approved message for delivery adapter submission.")


class Manager:
    name = "Manager"

    def run(self, lead: Lead, prior_stages: list[dict[str, Any]]) -> dict[str, Any]:
        blocking = [
            stage for stage in prior_stages
            if stage["status"] in {STATUS_BLOCKED, STATUS_FAILED, STATUS_SKIPPED}
        ]
        passed = not blocking and bool(lead.notes.get("approved_to_send"))
        lead.notes["pipeline_passed"] = passed
        return _stage(
            self.name,
            STATUS_COMPLETE,
            "Pipeline passed all pre-send controls." if passed else "Pipeline stopped safely before delivery.",
            pipeline_passed=passed,
            blocked_by=[stage["employee"] for stage in blocking],
        )


class SixEmployeePipeline:
    """Fail-closed outbound team within the seven-employee company."""

    def run(self, lead: Lead) -> PipelineResult:
        stages: list[dict[str, Any]] = []
        workers = (Scout(), Researcher(), Qualifier(), Personalizer(), SalesBot())
        blocked_reason = ""

        for worker in workers:
            if blocked_reason:
                stages.append(_skip(worker.name, blocked_reason))
                continue
            result = worker.run(lead)
            stages.append(result)
            if result["status"] != STATUS_COMPLETE:
                blocked_reason = f"Skipped because {worker.name} returned {result['status']}."

        manager_result = Manager().run(lead, stages)
        stages.append(manager_result)

        return PipelineResult(
            lead=lead,
            subject=lead.notes.get("subject", ""),
            body=lead.notes.get("body", ""),
            approved_to_send=bool(manager_result.get("pipeline_passed")),
            stages=stages,
        )
