from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import login, logout
from django.db.models import Q
from django.utils import timezone

from dashboard.models import LoginAttempt, LoginAttemptOutcome, Operator

AUTH_FAILURE_MESSAGE = "The email or password is incorrect."
LOCKOUT_WINDOW = timedelta(minutes=15)
LOCKOUT_FAILURES = 5


@dataclass(frozen=True)
class SignInOutcome:
    established: bool
    operator: Operator | None = None
    redirect_to: str | None = None
    message: str | None = None
    refusal_remaining: timedelta | None = None


def identifier_hash(identifier: str) -> str:
    return hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()


def _recent_failures(identifier: str, *, now=None) -> list[LoginAttempt]:
    now = now or timezone.now()
    digest = identifier_hash(identifier)
    last_success = (
        LoginAttempt.objects.filter(
            identifier_hash=digest,
            outcome=LoginAttemptOutcome.SUCCESS,
        )
        .order_by("-occurred_at", "-id")
        .first()
    )
    query = LoginAttempt.objects.filter(
        identifier_hash=digest,
        outcome=LoginAttemptOutcome.FAILURE,
        occurred_at__gte=now - LOCKOUT_WINDOW,
    )
    if last_success is not None:
        query = query.filter(occurred_at__gt=last_success.occurred_at)
    return list(query.order_by("occurred_at", "id"))


class AuthService:
    @staticmethod
    def sign_in(identifier: str, password: str, retained_screen: str | None = None) -> SignInOutcome:
        normalized = identifier.strip().lower()
        now = timezone.now()
        failures = _recent_failures(normalized, now=now)
        if len(failures) >= LOCKOUT_FAILURES:
            fifth = failures[LOCKOUT_FAILURES - 1]
            remaining = max(timedelta(0), LOCKOUT_WINDOW - (now - fifth.occurred_at))
            return SignInOutcome(
                established=False,
                message=AUTH_FAILURE_MESSAGE,
                refusal_remaining=remaining,
            )

        operator = (
            Operator.objects.filter(
                Q(username__iexact=normalized)
                | Q(email__iexact=normalized)
                | Q(registered_email__iexact=normalized)
            )
            .order_by("id")
            .first()
        )
        accepted = bool(operator and operator.is_active and operator.check_password(password))
        LoginAttempt.objects.create(
            identifier_hash=identifier_hash(normalized),
            outcome=(LoginAttemptOutcome.SUCCESS if accepted else LoginAttemptOutcome.FAILURE),
        )
        if not accepted:
            return SignInOutcome(established=False, message=AUTH_FAILURE_MESSAGE)

        redirect_to = retained_screen if retained_screen and retained_screen.startswith("/") else "/leads/"
        return SignInOutcome(
            established=True,
            operator=operator,
            redirect_to=redirect_to,
        )

    @classmethod
    def establish_session(
        cls,
        request,
        identifier: str,
        password: str,
        retained_screen: str | None = None,
    ) -> SignInOutcome:
        outcome = cls.sign_in(identifier, password, retained_screen)
        if not outcome.established or outcome.operator is None:
            return outcome
        login(request, outcome.operator)
        stamp = timezone.now().isoformat()
        request.session["session_started_at"] = stamp
        request.session["last_seen_at"] = stamp
        return outcome

    @staticmethod
    def sign_out(request) -> None:
        logout(request)
