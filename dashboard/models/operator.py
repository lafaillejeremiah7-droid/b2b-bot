from django.contrib.auth.models import AbstractUser
from django.db import models


class Operator(AbstractUser):
    class Role(models.TextChoices):
        VIEWER = "Viewer", "Viewer"
        AGENT = "Agent", "Agent"
        ADMIN = "Admin", "Admin"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    registered_email = models.EmailField(blank=True)
    slack_webhook_target = models.URLField(blank=True)
