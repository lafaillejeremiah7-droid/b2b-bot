from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def health(_request):
    return JsonResponse({"status": "ok", "company": "b2b-bot"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
]
