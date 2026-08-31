from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dashboard.services.discovery_handoff import (
    verified_research_handoff,
    verify_scout_handoff,
)
from dashboard.services.outreach_clearance import verify_outreach_clearance
from dashboard.services.outreach_templates import (
    OutreachContext,
    first_name_token,
    professional_signature,
    render_google_maps_outreach,
    render_invoice_link_email,
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
            return _stage(self.name, STATUS_COMPLETE, "Selected approved internal email test lead.")

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

        handoff = verified_research_handoff(lead)
        if handoff is None:
            lead.notes["research_verified"] = False
            return _stage(
                self.name,
                STATUS_BLOCKED,
                "Researcher is missing a valid digest-verified contact/site handoff tied to Scout's discovery.",
            )

        # The signed handoff is canonical. Rebuild the mirrored convenience
        # fields before downstream workers read them so stale/tampered notes
        # cannot drift Qualifier or Personalizer away from Researcher's evidence.
        lead.email = handoff.contact_email
        lead.website = handoff.website
        lead.notes["contact_verified"] = handoff.contact_verified
        lead.notes["website_verified"] = handoff.website_verified
        lead.notes["verified_no_website"] = handoff.verified_no_website
        lead.notes["website_observations"] = list(handoff.website_observations)
        lead.notes["research_evidence_urls"] = list(handoff.evidence_urls)

        if handoff.verified_no_website:
            valid_site_evidence = not bool(handoff.website)
        else:
            valid_site_evidence = bool(
                handoff.website
                and handoff.website_verified
                and len(handoff.website_observations) >= 2
            )

        contact_verified = bool(handoff.contact_email and handoff.contact_verified)
        research_verified = valid_site_evidence and contact_verified
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
            lead.notes["qualified"] = bool(lead.email.strip())
            if not lead.notes["qualified"]:
                return _stage(self.name, STATUS_BLOCKED, "Internal test recipient email is missing.")
            return _stage(self.name, STATUS_COMPLETE, "Approved safe internal self-test.")

        evidence_score = 0
        evidence_score += int(bool(lead.email.strip()))
        evidence_score += int(bool((lead.company or lead.name).strip()))
        evidence_score += int(bool(lead.notes.get("research_verified")))
        evidence_score += int(bool(lead.notes.get("verified_no_website") or lead.website.strip()))
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

        first_name = first_name_token(lead.name)
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
        except (TypeError, ValueError) as exc:
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

    def deliver_outreach(self, result: PipelineResult, *, client):
        """Own the final outbound submission after all six workers revalidate it."""
        lead = result.lead
        if not result.approved_to_send or not lead.notes.get("pipeline_passed"):
            raise ValueError("Sales Bot delivery requires Manager approval.")
        if lead.notes.get("suppressed") or lead.notes.get("opted_out"):
            raise ValueError("Sales Bot delivery rejected: lead is suppressed.")
        destination = (lead.email or "").strip().lower()
        if not destination or "@" not in destination or len(destination) > 320:
            raise ValueError("Sales Bot delivery requires a valid recipient email.")
        if lead.notes.get("subject") != result.subject or lead.notes.get("body") != result.body:
            raise ValueError("Sales Bot delivery rejected: approved message drifted after Personalizer review.")
        if lead.source != "internal_gmail_test" and not verify_outreach_clearance(lead):
            raise ValueError("Sales Bot delivery rejected: outreach clearance is no longer valid.")
        return client.send(
            to=destination,
            subject=result.subject,
            body=result.body,
        )

    def send_invoice_link(
        self,
        *,
        adapter,
        lead_id: int,
        to_email: str,
        first_name: str,
        company_name: str,
        amount_usd: int,
        hosted_invoice_url: str,
        idempotency_key,
    ):
        """Employee #5 sends the operator-approved Stripe invoice link.

        This is a post-win transactional message, not cold prospect outreach, so
        the cold-outreach clearance handoff is not re-run. The human invoice-send
        confirmation is the authorization boundary. The adapter still receives a
        stable idempotency key so retries can collapse safely.
        """
        destination = (to_email or "").strip().lower()
        if not destination or "@" not in destination or len(destination) > 320:
            raise ValueError("Sales Bot requires a valid invoice recipient email.")
        subject, body = render_invoice_link_email(
            first_name=first_name,
            company_name=company_name,
            amount_usd=amount_usd,
            hosted_invoice_url=hosted_invoice_url,
        )
        return adapter.send_prospect_email(
            lead_id=lead_id,
            to_email=destination,
            subject=subject,
            body=body,
            idempotency_key=idempotency_key,
        )


class Manager:
    name = "Manager"

    def run(self, lead: Lead, prior_stages: list[dict[str, Any]]) -> dict[str, Any]:
        root_blockers = [
            stage
            for stage in prior_stages
            if stage["status"] in {STATUS_BLOCKED, STATUS_FAILED}
        ]
        skipped = [stage for stage in prior_stages if stage["status"] == STATUS_SKIPPED]
        passed = not root_blockers and not skipped and bool(lead.notes.get("approved_to_send"))
        lead.notes["pipeline_passed"] = passed
        return _stage(
            self.name,
            STATUS_COMPLETE,
            "Pipeline passed all pre-send controls." if passed else "Pipeline stopped safely before delivery.",
            pipeline_passed=passed,
            blocked_by=[stage["employee"] for stage in root_blockers],
            skipped_employees=[stage["employee"] for stage in skipped],
        )


class SixEmployeePipeline:
    """Fail-closed outbound team within the eight-employee company."""

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
