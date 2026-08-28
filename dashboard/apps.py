"""App configuration."""

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "dashboard"
    verbose_name = "Deal Room Dashboard"
    default_auto_field = "django.db.models.BigAutoField"
