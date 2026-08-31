from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

from .gmail_delivery import GmailDeliveryClient

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GmailOAuthError(RuntimeError):
    """Raised when a Gmail OAuth access token cannot be refreshed."""


@dataclass(frozen=True)
class GmailOAuthConfig:
    client_id: str
    client_secret: str
    refresh_token: str
    timeout_seconds: float = 15.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip() and self.refresh_token.strip())


class GmailOAuthTokenProvider:
    """Thread-safe refresh-token provider for the Gmail REST adapter.

    Long-lived OAuth credentials are supplied only through deployment settings.
    The short-lived access token is cached in process memory and refreshed before
    expiry. No credential is written to the database or repository.
    """

    def __init__(self, config: GmailOAuthConfig | None = None):
        self.config = config or GmailOAuthConfig(
            client_id=settings.GMAIL_OAUTH_CLIENT_ID,
            client_secret=settings.GMAIL_OAUTH_CLIENT_SECRET,
            refresh_token=settings.GMAIL_OAUTH_REFRESH_TOKEN,
            timeout_seconds=settings.GMAIL_OAUTH_TOKEN_TIMEOUT_SECONDS,
        )
        self._access_token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> tuple[str, int]:
        if not self.config.configured:
            raise GmailOAuthError(
                "Gmail OAuth is not configured. Set the Gmail OAuth client ID, client secret, and refresh token in the private deployment environment."
            )

        body = urlencode(
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": self.config.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = Request(
            GOOGLE_TOKEN_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:  # noqa: S310 - fixed Google OAuth host
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise GmailOAuthError(f"Gmail OAuth refresh was rejected with HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GmailOAuthError("Gmail OAuth refresh could not be completed.") from exc

        token = str(payload.get("access_token") or "").strip() if isinstance(payload, dict) else ""
        try:
            expires_in = int(payload.get("expires_in", 3600)) if isinstance(payload, dict) else 3600
        except (TypeError, ValueError):
            expires_in = 3600
        if not token:
            raise GmailOAuthError("Gmail OAuth refresh returned no access token.")
        return token, max(60, expires_in)

    def __call__(self) -> str:
        now = time.monotonic()
        if self._access_token and now < self._expires_at - 60:
            return self._access_token

        with self._lock:
            now = time.monotonic()
            if self._access_token and now < self._expires_at - 60:
                return self._access_token
            token, expires_in = self._refresh()
            self._access_token = token
            self._expires_at = time.monotonic() + expires_in
            return token


def gmail_oauth_configured() -> bool:
    return GmailOAuthConfig(
        client_id=settings.GMAIL_OAUTH_CLIENT_ID,
        client_secret=settings.GMAIL_OAUTH_CLIENT_SECRET,
        refresh_token=settings.GMAIL_OAUTH_REFRESH_TOKEN,
        timeout_seconds=settings.GMAIL_OAUTH_TOKEN_TIMEOUT_SECONDS,
    ).configured


def get_gmail_delivery_client() -> GmailDeliveryClient:
    return GmailDeliveryClient(
        GmailOAuthTokenProvider(),
        sender_email=settings.OUTREACH_EMAIL,
        timeout_seconds=settings.GMAIL_API_TIMEOUT_SECONDS,
    )
