from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


class GmailDeliveryError(RuntimeError):
    """Raised when Gmail delivery fails closed."""


@dataclass(frozen=True)
class DeliveryReceipt:
    message_id: str
    thread_id: str


AccessTokenProvider = Callable[[], str]
Transport = Callable[[str, dict[str, str], dict[str, str], float], dict[str, object]]


def _default_transport(
    url: str,
    payload: dict[str, str],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed Gmail API host
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise GmailDeliveryError(f"Gmail API HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise GmailDeliveryError(f"Gmail API transport error: {exc.reason}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GmailDeliveryError("Gmail API returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise GmailDeliveryError("Gmail API returned a non-object response.")
    return parsed


def build_raw_message(*, to: str, subject: str, body: str, sender: str = "") -> str:
    if not to.strip() or "@" not in to:
        raise ValueError("A valid recipient email is required.")
    if not subject.strip():
        raise ValueError("Email subject cannot be blank.")
    if not body.strip():
        raise ValueError("Email body cannot be blank.")

    message = EmailMessage()
    message["To"] = to.strip()
    message["Subject"] = subject.strip()
    if sender.strip():
        message["From"] = sender.strip()
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


class GmailDeliveryClient:
    """Small Gmail REST adapter with an injected OAuth access-token provider."""

    def __init__(
        self,
        access_token: AccessTokenProvider,
        *,
        sender_email: str = "",
        timeout_seconds: float = 30.0,
        transport: Transport | None = None,
    ) -> None:
        self._access_token = access_token
        self._sender_email = sender_email.strip()
        self._timeout = timeout_seconds
        self._transport = transport or _default_transport

    def send(self, *, to: str, subject: str, body: str) -> DeliveryReceipt:
        token = self._access_token().strip()
        if not token:
            raise GmailDeliveryError("Gmail OAuth access token is unavailable.")
        raw = build_raw_message(
            to=to,
            subject=subject,
            body=body,
            sender=self._sender_email,
        )
        response = self._transport(
            GMAIL_SEND_URL,
            {"raw": raw},
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            self._timeout,
        )
        message_id = str(response.get("id", "")).strip()
        thread_id = str(response.get("threadId", "")).strip()
        if not message_id or not thread_id:
            raise GmailDeliveryError("Gmail API response is missing message/thread identifiers.")
        return DeliveryReceipt(message_id=message_id, thread_id=thread_id)
