from __future__ import annotations

from typing import Protocol

from dashboard.models.suppression import OutreachSuppression, SuppressionReason


class SuppressionStore(Protocol):
    def is_suppressed(self, email: str) -> bool: ...

    def suppress(
        self,
        email: str,
        *,
        reason: str,
        lead_reference: str = "",
        thread_id: str = "",
    ) -> None: ...


class DjangoSuppressionStore:
    """PostgreSQL-backed do-not-contact registry."""

    @staticmethod
    def normalize(email: str) -> str:
        return email.strip().lower()

    def is_suppressed(self, email: str) -> bool:
        normalized = self.normalize(email)
        if not normalized:
            return False
        return OutreachSuppression.objects.filter(normalized_email=normalized).exists()

    def suppress(
        self,
        email: str,
        *,
        reason: str,
        lead_reference: str = "",
        thread_id: str = "",
    ) -> None:
        normalized = self.normalize(email)
        if not normalized or "@" not in normalized:
            raise ValueError("A valid email is required to create suppression.")
        valid_reasons = set(SuppressionReason.values)
        if reason not in valid_reasons:
            raise ValueError(f"Unsupported suppression reason: {reason}")
        OutreachSuppression.objects.update_or_create(
            normalized_email=normalized,
            defaults={
                "reason": reason,
                "lead_reference": lead_reference,
                "thread_id": thread_id,
            },
        )
