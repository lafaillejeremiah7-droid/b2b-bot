from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings


@dataclass(frozen=True)
class RuntimeRequirement:
    key: str
    name: str
    configured: bool
    purpose: str


def _nonempty(value: object) -> bool:
    return bool(str(value or "").strip())


def _external_redis_url(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    host = (parsed.hostname or "").strip().lower()
    return parsed.scheme in {"redis", "rediss"} and host not in {
        "",
        "localhost",
        "127.0.0.1",
        "::1",
    }


def runtime_requirements() -> tuple[RuntimeRequirement, ...]:
    yahoo_ready = bool(
        _nonempty(getattr(settings, "YAHOO_SMTP_HOST", ""))
        and int(getattr(settings, "YAHOO_SMTP_PORT", 0) or 0) > 0
        and _nonempty(getattr(settings, "YAHOO_SMTP_USERNAME", ""))
        and _nonempty(getattr(settings, "YAHOO_SMTP_APP_PASSWORD", ""))
    )
    queue_ready = bool(
        _external_redis_url(getattr(settings, "CELERY_BROKER_URL", ""))
        and _external_redis_url(getattr(settings, "CELERY_RESULT_BACKEND", ""))
    )
    return (
        RuntimeRequirement(
            "maps",
            "Google Places",
            _nonempty(getattr(settings, "GOOGLE_MAPS_API_KEY", "")),
            "Scout discovery",
        ),
        RuntimeRequirement(
            "search",
            "Independent web search",
            _nonempty(getattr(settings, "SERPAPI_API_KEY", "")),
            "Researcher no-website verification",
        ),
        RuntimeRequirement(
            "identity",
            "Outbound identity",
            bool(
                _nonempty(getattr(settings, "OUTREACH_EMAIL", ""))
                and _nonempty(getattr(settings, "OUTREACH_PHONE", ""))
            ),
            "Professional customer identity",
        ),
        RuntimeRequirement(
            "smtp",
            "Yahoo Business SMTP",
            yahoo_ready,
            "Sales Bot outreach, invoices, delivery, and operator email alerts",
        ),
        RuntimeRequirement(
            "stripe",
            "Stripe",
            bool(
                _nonempty(getattr(settings, "STRIPE_SECRET_KEY", ""))
                and _nonempty(getattr(settings, "STRIPE_WEBHOOK_SECRET", ""))
            ),
            "Closer invoice link + authenticated payment events",
        ),
        RuntimeRequirement(
            "events",
            "Pipeline event authentication",
            _nonempty(getattr(settings, "PIPELINE_EVENT_SECRET", "")),
            "Authenticated generic event intake",
        ),
        RuntimeRequirement(
            "queue",
            "Background queue",
            queue_ready,
            "Celery worker and scheduled maintenance",
        ),
        RuntimeRequirement(
            "live",
            "Live adapter mode",
            getattr(settings, "PIPELINE_ADAPTER_MODE", "stub") == "live",
            "External side effects enabled only when explicitly live",
        ),
    )


def live_configuration_failures() -> tuple[str, ...]:
    failures = [
        f"{item.name} is not configured"
        for item in runtime_requirements()
        if not item.configured
    ]
    if bool(getattr(settings, "DEBUG", False)):
        failures.append("DJANGO_DEBUG must be 0")
    secret = str(getattr(settings, "SECRET_KEY", "") or "")
    if not secret or secret == "dev-only-change-me":
        failures.append("DJANGO_SECRET_KEY must be a production secret")
    return tuple(failures)


def company_is_ready() -> bool:
    return not live_configuration_failures()
