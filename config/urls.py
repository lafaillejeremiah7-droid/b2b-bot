from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import path

from dashboard.views import company_dashboard


def health(_request):
    return JsonResponse({"status": "ok", "company": "b2b-bot"})


def home(_request):
    return redirect("company_dashboard")


urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("dashboard/", company_dashboard, name="company_dashboard"),
    path("health/", health, name="health"),
]
