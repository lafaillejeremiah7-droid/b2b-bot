from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime


EXEMPT_PREFIXES = (
    "/sign-in/",
    "/health/",
    "/admin/",
    "/static/",
)
PROTECTED_PREFIXES = (
    "/dashboard/",
    "/leads/",
    "/deals/",
    "/analytics/",
    "/audit/",
    "/notifications/",
)


def _session_dt(value) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


class SessionExpiryMiddleware:
    """Server-side absolute + idle expiry and pre-view auth redirect."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)

        protected = path.startswith(PROTECTED_PREFIXES)
        if protected and not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={quote(request.get_full_path())}")

        if request.user.is_authenticated:
            now = timezone.now()
            started = _session_dt(request.session.get("session_started_at"))
            last_seen = _session_dt(request.session.get("last_seen_at"))
            absolute = settings.SESSION_ABSOLUTE_LIFETIME_SECONDS
            idle = settings.SESSION_IDLE_TIMEOUT_SECONDS
            expired = bool(
                (started and (now - started).total_seconds() >= absolute)
                or (last_seen and (now - last_seen).total_seconds() >= idle)
            )
            if expired:
                logout(request)
                return redirect(f"{settings.LOGIN_URL}?next={quote(request.get_full_path())}")
            if started is None:
                request.session["session_started_at"] = now.isoformat()
            request.session["last_seen_at"] = now.isoformat()

        return self.get_response(request)
