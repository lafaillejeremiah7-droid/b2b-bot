from __future__ import annotations

from django.db import models
from django.db.models.functions import Lower, Trim


class SuppressionReason(models.TextChoices):
    UNSUBSCRIBE = "unsubscribe", "Unsubscribe"
    NOT_INTERESTED = "not_interested", "Not interested"
    BOUNCE = "bounce", "Bounce"
    MANUAL = "manual", "Manual"


class OutreachSuppression(models.Model):
    """Durable do-not-contact record checked immediately before every send."""

    id = models.BigAutoField(primary_key=True)
    normalized_email = models.EmailField(max_length=320, unique=True)
    reason = models.CharField(max_length=32, choices=SuppressionReason.choices)
    lead_reference = models.CharField(max_length=128, blank=True)
    thread_id = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "outreach_suppressions"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(normalized_email=Lower(Trim("normalized_email"))),
                name="suppression_email_normalized",
            ),
        ]

    def save(self, *args, **kwargs) -> None:
        self.normalized_email = self.normalized_email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.normalized_email} ({self.reason})"
