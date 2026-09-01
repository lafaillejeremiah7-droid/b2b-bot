from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"

    def ready(self) -> None:
        # Import deploy-only system checks after Django's app registry is ready.
        # This module performs registration only; it does not call external APIs.
        from dashboard import checks  # noqa: F401
