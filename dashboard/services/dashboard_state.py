from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from dashboard.models import OutreachSuppression


@dataclass(frozen=True)
class IntegrationReadiness:
    name: str
    configured: bool | None
    purpose: str


def suppression_count() -> int:
    """Return the durable do-not-contact count for the operator dashboard."""
    return OutreachSuppression.objects.count()


def integration_readiness() -> tuple[IntegrationReadiness, ...]:
    """Report configuration truth without exposing or persisting credentials."""
    return (
        IntegrationReadiness(
            name="Google Places",
            configured=bool(settings.GOOGLE_MAPS_API_KEY),
            purpose="Scout discovery",
        ),
        IntegrationReadiness(
            name="Independent web search",
            configured=bool(settings.SERPAPI_API_KEY),
            purpose="No-website verification",
        ),
        IntegrationReadiness(
            name="Outbound identity",
            configured=bool(settings.OUTREACH_EMAIL and settings.OUTREACH_PHONE),
            purpose="Professional email signature",
        ),
        IntegrationReadiness(
            name="Gmail OAuth",
            configured=None,
            purpose="Injected at send runtime; never stored in the repo",
        ),
    )
