from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class OperatorManager(UserManager):
    """Runtime-compatible manager with the formal schema's create_operator API."""

    def create_operator(self, email: str, password: str | None = None, **extra_fields):
        normalized = email.strip().lower()
        if not normalized:
            raise ValueError("An Operator requires an email address.")
        username = extra_fields.pop("username", normalized)
        extra_fields.setdefault("registered_email", normalized)
        return self.create_user(
            username=username,
            email=normalized,
            password=password,
            **extra_fields,
        )


class Operator(AbstractUser):
    class Role(models.TextChoices):
        VIEWER = "Viewer", "Viewer"
        AGENT = "Agent", "Agent"
        ADMIN = "Admin", "Admin"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    registered_email = models.EmailField(blank=True)
    slack_webhook_target = models.URLField(blank=True)

    objects = OperatorManager()
