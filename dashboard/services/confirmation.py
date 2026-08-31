from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from dashboard.services.errors import ConfirmationRejected

SESSION_KEY = "confirmation_tokens_v1"
DEFAULT_TTL_SECONDS = 600


@dataclass(frozen=True)
class ConfirmationScope:
    action: str
    target_id: int


def mint_confirmation(session, *, action: str, target_id: int, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    token = secrets.token_urlsafe(32)
    records = dict(session.get(SESSION_KEY, {}))
    records[token] = {
        "action": str(action),
        "target_id": int(target_id),
        "expires_at": int(time.time()) + max(1, int(ttl_seconds)),
    }
    session[SESSION_KEY] = records
    session.modified = True
    return token


def consume_confirmation(session, *, token: str, action: str, target_id: int) -> None:
    records = dict(session.get(SESSION_KEY, {}))
    record = records.pop(str(token), None)
    # Remove before validating so a guessed/mis-scoped token cannot be replayed.
    session[SESSION_KEY] = records
    session.modified = True
    if not record:
        raise ConfirmationRejected("Confirmation is missing, invalid, or already used.")
    if int(record.get("expires_at", 0)) < int(time.time()):
        raise ConfirmationRejected("Confirmation expired; confirm the action again.")
    if record.get("action") != str(action) or int(record.get("target_id", -1)) != int(target_id):
        raise ConfirmationRejected("Confirmation does not match this action.")
