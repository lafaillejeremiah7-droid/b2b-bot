"""Root URL configuration.

Empty at task 1.1. Dashboard routes are added by the tasks that own the
corresponding views and mounted here via ``include("dashboard.urls")``.
"""

from django.urls import URLPattern, URLResolver

urlpatterns: list[URLPattern | URLResolver] = []
