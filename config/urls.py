from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import path

from dashboard.formal_views import (
    analytics_view,
    audit_view,
    call_action,
    deal_room,
    email_action,
    inbound_event,
    invoice_action,
    lead_list,
    notifications_view,
    payment_verify_action,
    price_action,
    release_action,
    sign_in,
    sign_out,
    site_approve_action,
    site_reject_action,
    transition_action,
)
from dashboard.views import company_dashboard


def health(_request):
    return JsonResponse({"status": "ok", "company": "b2b-bot"})


def home(_request):
    return redirect("company_dashboard")


urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("sign-in/", sign_in, name="sign_in"),
    path("sign-out/", sign_out, name="sign_out"),
    path("dashboard/", company_dashboard, name="company_dashboard"),
    path("leads/", lead_list, name="lead_list"),
    path("deals/<int:lead_id>/", deal_room, name="deal_room"),
    path("deals/<int:lead_id>/transition/", transition_action, name="transition_action"),
    path("deals/<int:lead_id>/price/", price_action, name="price_action"),
    path("deals/<int:lead_id>/email/", email_action, name="email_action"),
    path("deals/<int:lead_id>/call/", call_action, name="call_action"),
    path("deals/<int:lead_id>/sites/<int:site_id>/approve/", site_approve_action, name="site_approve_action"),
    path("deals/<int:lead_id>/sites/<int:site_id>/reject/", site_reject_action, name="site_reject_action"),
    path("deals/<int:lead_id>/money/<int:deal_id>/invoice/", invoice_action, name="invoice_action"),
    path("deals/<int:lead_id>/money/<int:deal_id>/verify/", payment_verify_action, name="payment_verify_action"),
    path("deals/<int:lead_id>/money/<int:deal_id>/release/", release_action, name="release_action"),
    path("analytics/", analytics_view, name="analytics"),
    path("audit/", audit_view, name="audit_search"),
    path("notifications/", notifications_view, name="notifications"),
    path("events/", inbound_event, name="inbound_event"),
    path("health/", health, name="health"),
]
