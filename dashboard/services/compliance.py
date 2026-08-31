from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from dashboard.models import EmailBounce, Lead, SiteProject, SiteReviewState


class BlockingCondition(StrEnum):
    MISSING_EMAIL = "missing contact email"
    MISSING_PHONE = "missing contact phone"
    UNSUBSCRIBED = "unsubscribed"
    DO_NOT_CALL = "do-not-call"
    BOUNCED = "current email address has bounced"
    MANUAL_REVIEW = "manual review required"
    UNKNOWN_TIMEZONE = "timezone cannot be resolved"
    OUTSIDE_CALLING_WINDOW = "outside 08:00–20:00 local calling window"
    PREVIEW_NOT_APPROVED = "site preview is not approved"


@dataclass(frozen=True)
class ComplianceDecision:
    permitted: bool
    evaluated_at: datetime
    blocking: tuple[BlockingCondition, ...]
    timezone_name: str | None = None
    timezone_source: str | None = None
    duplicate_lead_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ClearedOutreach:
    lead_id: int
    channel: str
    evaluated_at: datetime
    timezone_name: str | None
    duplicate_lead_ids: tuple[int, ...]
    site_project_id: int | None = None


REGION_TIMEZONES = {
    "eastern": "America/New_York",
    "northeast": "America/New_York",
    "fl": "America/New_York",
    "florida": "America/New_York",
    "ny": "America/New_York",
    "new york": "America/New_York",
    "ga": "America/New_York",
    "central": "America/Chicago",
    "tx": "America/Chicago",
    "texas": "America/Chicago",
    "il": "America/Chicago",
    "chicago": "America/Chicago",
    "mountain": "America/Denver",
    "co": "America/Denver",
    "colorado": "America/Denver",
    "az": "America/Phoenix",
    "arizona": "America/Phoenix",
    "pacific": "America/Los_Angeles",
    "ca": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "wa": "America/Los_Angeles",
    "washington": "America/Los_Angeles",
}

AREA_CODE_TIMEZONES = {
    "212": "America/New_York", "305": "America/New_York", "321": "America/New_York",
    "347": "America/New_York", "404": "America/New_York", "407": "America/New_York",
    "561": "America/New_York", "646": "America/New_York", "678": "America/New_York",
    "718": "America/New_York", "754": "America/New_York", "772": "America/New_York",
    "786": "America/New_York", "813": "America/New_York", "850": "America/New_York",
    "954": "America/New_York", "312": "America/Chicago", "214": "America/Chicago",
    "281": "America/Chicago", "469": "America/Chicago", "713": "America/Chicago",
    "832": "America/Chicago", "512": "America/Chicago", "210": "America/Chicago",
    "303": "America/Denver", "720": "America/Denver", "602": "America/Phoenix",
    "480": "America/Phoenix", "623": "America/Phoenix", "206": "America/Los_Angeles",
    "213": "America/Los_Angeles", "310": "America/Los_Angeles", "323": "America/Los_Angeles",
    "408": "America/Los_Angeles", "415": "America/Los_Angeles", "424": "America/Los_Angeles",
    "510": "America/Los_Angeles", "619": "America/Los_Angeles", "650": "America/Los_Angeles",
    "702": "America/Los_Angeles", "714": "America/Los_Angeles", "818": "America/Los_Angeles",
    "858": "America/Los_Angeles", "916": "America/Los_Angeles",
}


def _normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    return value or None


def _phone_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


class ComplianceGuard:
    @staticmethod
    def resolve_timezone(lead: Lead) -> tuple[str | None, str | None]:
        explicit = (lead.timezone or "").strip()
        if explicit:
            try:
                ZoneInfo(explicit)
                return explicit, "lead.timezone"
            except ZoneInfoNotFoundError:
                pass

        digits = _phone_digits(lead.contact_phone)
        national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
        if len(national) >= 10:
            zone = AREA_CODE_TIMEZONES.get(national[:3])
            if zone:
                return zone, "contact_phone"

        region = (lead.region or "").strip().lower()
        if region:
            zone = REGION_TIMEZONES.get(region)
            if zone:
                return zone, "region"
        return None, None

    @staticmethod
    def duplicate_leads(lead: Lead) -> tuple[int, ...]:
        query = Lead.objects.exclude(pk=lead.pk)
        email = _normalize_email(lead.contact_email)
        digits = _phone_digits(lead.contact_phone)
        ids: set[int] = set()
        if email:
            ids.update(query.filter(email_normalized=email).values_list("id", flat=True))
        if digits:
            ids.update(query.filter(phone_digits=digits).values_list("id", flat=True))
        return tuple(sorted(ids))

    @classmethod
    def evaluate(
        cls,
        *,
        lead: Lead,
        channel: str,
        at: datetime | None = None,
        site_project_id: int | None = None,
    ) -> ComplianceDecision:
        at = at or timezone.now()
        blocking: list[BlockingCondition] = []
        timezone_name = None
        timezone_source = None

        if lead.manual_review_flag:
            blocking.append(BlockingCondition.MANUAL_REVIEW)

        if channel == "email":
            if not _normalize_email(lead.contact_email):
                blocking.append(BlockingCondition.MISSING_EMAIL)
            if lead.unsubscribed_at is not None and at >= lead.unsubscribed_at:
                blocking.append(BlockingCondition.UNSUBSCRIBED)
            if lead.contact_email and EmailBounce.objects.filter(
                lead_id=lead.id,
                contact_email__iexact=lead.contact_email.strip(),
                occurred_at__lt=at,
            ).exists():
                blocking.append(BlockingCondition.BOUNCED)
            if site_project_id is not None:
                site = SiteProject.objects.filter(pk=site_project_id, lead_id=lead.id).first()
                if (
                    site is None
                    or site.review_state != SiteReviewState.APPROVED
                    or site.approved_at is None
                    or site.approved_at > at
                ):
                    blocking.append(BlockingCondition.PREVIEW_NOT_APPROVED)
        elif channel == "call":
            if not _phone_digits(lead.contact_phone):
                blocking.append(BlockingCondition.MISSING_PHONE)
            if lead.do_not_call_at is not None and at >= lead.do_not_call_at:
                blocking.append(BlockingCondition.DO_NOT_CALL)
            timezone_name, timezone_source = cls.resolve_timezone(lead)
            if timezone_name is None:
                blocking.append(BlockingCondition.UNKNOWN_TIMEZONE)
            else:
                local = at.astimezone(ZoneInfo(timezone_name))
                if not (8 <= local.hour < 20):
                    blocking.append(BlockingCondition.OUTSIDE_CALLING_WINDOW)
        else:
            raise ValueError("channel must be 'email' or 'call'")

        duplicates = cls.duplicate_leads(lead)
        return ComplianceDecision(
            permitted=not blocking,
            evaluated_at=at,
            blocking=tuple(dict.fromkeys(blocking)),
            timezone_name=timezone_name,
            timezone_source=timezone_source,
            duplicate_lead_ids=duplicates,
        )

    @classmethod
    def clear(
        cls,
        *,
        lead: Lead,
        channel: str,
        at: datetime | None = None,
        site_project_id: int | None = None,
    ) -> ClearedOutreach:
        decision = cls.evaluate(
            lead=lead,
            channel=channel,
            at=at,
            site_project_id=site_project_id,
        )
        if not decision.permitted:
            names = "; ".join(condition.value for condition in decision.blocking)
            from dashboard.services.errors import ComplianceRejected

            raise ComplianceRejected(
                f"Outreach blocked: {names}.",
                target_type="lead",
                target_id=lead.id,
                before_snapshot={"blocking_conditions": [c.value for c in decision.blocking]},
            )
        return ClearedOutreach(
            lead_id=lead.id,
            channel=channel,
            evaluated_at=decision.evaluated_at,
            timezone_name=decision.timezone_name,
            duplicate_lead_ids=decision.duplicate_lead_ids,
            site_project_id=site_project_id,
        )
