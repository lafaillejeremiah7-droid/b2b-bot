"""Django project package for the Deal Room Dashboard.

The Celery app is imported here so that it is always constructed when Django
loads, which is what lets `@shared_task` bind to it (design §2.7).
"""

from .celery import app as celery_app

__all__ = ["celery_app"]
