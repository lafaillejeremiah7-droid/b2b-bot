from __future__ import annotations

import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from dashboard.services.analytics import AnalyticsService, resolve_reporting_range
from dashboard.services.audit_views import AuditQueryService
from dashboard.services.auth_service import AuthService
from dashboard.services.confirmation import mint_confirmation
from dashboard.services.errors import DashboardError
from dashboard.services.events import EventIntake
from dashboard.services.invoice_send import InvoiceSendGate
from dashboard.services.money import InvoiceManager, PaymentVerifier, ReleaseGate
from dashboard.services.outreach_controller import OutreachController
from dashboard.services.pipeline_state import PipelineStateMachine
from dashboard.services.pricing import PriceService, recommendation_for
from dashboard.services.screens import DealRoomService, LeadListService, NotificationListService
from dashboard.services.site_review import SiteReviewGate


def _redirect_deal(lead_id: int):
    return redirect("deal_room", lead_id=lead_id)


@require_http_methods(["GET", "POST"])
def sign_in(request):
    if request.user.is_authenticated:
        return redirect("lead_list")
    next_path = request.POST.get("next") or request.GET.get("next") or "/leads/"
    if request.method == "POST":
        outcome = AuthService.establish_session(
            request,
            request.POST.get("identifier", ""),
            request.POST.get("password", ""),
            next_path,
        )
        if outcome.established:
            return redirect(outcome.redirect_to or "/leads/")
        return render(
            request,
            "dashboard/sign_in.html",
            {"message": outcome.message, "remaining": outcome.refusal_remaining, "next": next_path},
            status=401,
        )
    return render(request, "dashboard/sign_in.html", {"next": next_path})


@require_POST
def sign_out(request):
    AuthService.sign_out(request)
    return redirect("sign_in")


@login_required
def lead_list(request):
    try:
        result = LeadListService.query(params=request.GET, operator=request.user)
        return render(request, "dashboard/lead_list.html", {"result": result})
    except DashboardError as exc:
        return render(request, "dashboard/lead_list.html", {"error": str(exc)}, status=400)


@login_required
def deal_room(request, lead_id: int):
    try:
        context = DealRoomService.get(lead_id=lead_id, operator=request.user)
    except Exception as exc:
        if exc.__class__.__name__.endswith("DoesNotExist"):
            raise Http404("Lead not found") from exc
        raise
    context["activity"] = DealRoomService.activity(lead_id=lead_id, page=request.GET.get("activity_page", 1))
    context["suggested_price"] = recommendation_for(context["lead"])
    site = context["latest_site"]
    deal = context["deal"]
    invoice = context["invoice"]
    context["tokens"] = {
        "email": mint_confirmation(request.session, action="outreach.send", target_id=lead_id),
        "call": mint_confirmation(request.session, action="outreach.call", target_id=lead_id),
        "duplicate": mint_confirmation(request.session, action="outreach.duplicate", target_id=lead_id),
        "site_approve": mint_confirmation(request.session, action="site.approve", target_id=site.id) if site else "",
        "site_reject": mint_confirmation(request.session, action="site.reject", target_id=site.id) if site else "",
        "invoice_send": mint_confirmation(request.session, action="invoice.send", target_id=invoice.pk) if invoice and invoice.sent_at is None else "",
        "payment_mismatch": mint_confirmation(request.session, action="payment.verify.amount_mismatch", target_id=deal.pk) if deal else "",
        "release": mint_confirmation(request.session, action="release.authorize", target_id=deal.pk) if deal else "",
    }
    return render(request, "dashboard/deal_room.html", context)


def _action_error(request, lead_id: int, exc: Exception):
    messages.error(request, str(exc))
    return _redirect_deal(lead_id)


@login_required
@require_POST
def transition_action(request, lead_id: int):
    try:
        PipelineStateMachine.request(
            lead_id=lead_id,
            to_state=request.POST.get("to_state", ""),
            actor=request.user,
            expected_from_state=request.POST.get("expected_from_state") or None,
            expected_version=int(request.POST.get("state_version", "0")),
        )
        messages.success(request, "Pipeline state updated.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def price_action(request, lead_id: int):
    try:
        PriceService.set_agreed_price(
            lead_id=lead_id,
            operator=request.user,
            submitted_value=request.POST.get("agreed_price"),
        )
        messages.success(request, "Agreed price saved.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def email_action(request, lead_id: int):
    try:
        site_id = request.POST.get("site_project_id")
        outcome = OutreachController.send_email(
            lead_id=lead_id,
            operator=request.user,
            session=request.session,
            confirmation_token=request.POST.get("confirmation_token", ""),
            duplicate_confirmation_token=request.POST.get("duplicate_confirmation_token") or None,
            subject=request.POST.get("subject", ""),
            body=request.POST.get("body", ""),
            site_project_id=int(site_id) if site_id else None,
        )
        if outcome.adapter_result and outcome.adapter_result.status == "failure":
            messages.error(request, outcome.adapter_result.failure_reason or "Email submission failed.")
        else:
            messages.success(request, "Email outreach recorded successfully.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def call_action(request, lead_id: int):
    try:
        outcome = OutreachController.submit_call(
            lead_id=lead_id,
            operator=request.user,
            session=request.session,
            confirmation_token=request.POST.get("confirmation_token", ""),
            duplicate_confirmation_token=request.POST.get("duplicate_confirmation_token") or None,
            outcome=request.POST.get("outcome", ""),
            notes=request.POST.get("notes", ""),
        )
        if outcome.adapter_result and outcome.adapter_result.status == "failure":
            messages.error(request, outcome.adapter_result.failure_reason or "Call submission failed.")
        else:
            messages.success(request, "Call recorded.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def site_approve_action(request, lead_id: int, site_id: int):
    try:
        SiteReviewGate.approve(
            site_id=site_id,
            operator=request.user,
            session=request.session,
            confirmation_token=request.POST.get("confirmation_token", ""),
        )
        messages.success(request, "Site approved.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def site_reject_action(request, lead_id: int, site_id: int):
    try:
        _, result, _ = SiteReviewGate.reject(
            site_id=site_id,
            operator=request.user,
            session=request.session,
            confirmation_token=request.POST.get("confirmation_token", ""),
            reason=request.POST.get("reason", ""),
        )
        if result.status == "failure":
            messages.error(request, result.failure_reason or "Regeneration request failed.")
        else:
            messages.success(request, "Site rejected and regeneration requested.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def invoice_action(request, lead_id: int, deal_id: int):
    try:
        outcome = InvoiceManager.create_invoice(deal_id=deal_id, operator=request.user)
        if outcome.adapter_result.status == "failure":
            messages.error(request, outcome.adapter_result.failure_reason or "Invoice creation failed.")
        else:
            messages.success(request, "Invoice created. Nothing has been emailed; approve the send prompt when ready.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def invoice_send_action(request, lead_id: int, deal_id: int):
    try:
        outcome = InvoiceSendGate.send(
            deal_id=deal_id,
            operator=request.user,
            session=request.session,
            confirmation_token=request.POST.get("confirmation_token", ""),
        )
        if outcome.already_sent:
            messages.info(request, "This invoice was already sent; no duplicate email was submitted.")
        else:
            messages.success(request, f"Invoice sent to {outcome.invoice.recipient_email} through Stripe.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def payment_verify_action(request, lead_id: int, deal_id: int):
    try:
        PaymentVerifier.verify(
            deal_id=deal_id,
            operator=request.user,
            session=request.session,
            mismatch_confirmation_token=request.POST.get("mismatch_confirmation_token") or None,
        )
        messages.success(request, "Payment verified.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
@require_POST
def release_action(request, lead_id: int, deal_id: int):
    try:
        outcome = ReleaseGate.authorize_release(
            deal_id=deal_id,
            operator=request.user,
            session=request.session,
            confirmation_token=request.POST.get("confirmation_token", ""),
            archive_link=request.POST.get("archive_link", ""),
        )
        if outcome.adapter_result and outcome.adapter_result.status == "failure":
            messages.error(request, outcome.adapter_result.failure_reason or "Delivery failed; authorization is retained.")
        elif outcome.already_authorized:
            messages.info(request, "Release was already authorized; no second delivery was submitted.")
        else:
            messages.success(request, "Release authorized and delivered.")
    except Exception as exc:
        return _action_error(request, lead_id, exc)
    return _redirect_deal(lead_id)


@login_required
def analytics_view(request):
    try:
        start = date.fromisoformat(request.GET["start"]) if request.GET.get("start") else None
        end = date.fromisoformat(request.GET["end"]) if request.GET.get("end") else None
        summary = AnalyticsService.summary(reporting_range=resolve_reporting_range(start, end))
        return render(request, "dashboard/analytics.html", {"summary": summary})
    except (ValueError, DashboardError) as exc:
        return render(request, "dashboard/analytics.html", {"error": str(exc)}, status=400)


@login_required
def audit_view(request):
    try:
        result = AuditQueryService.search(operator=request.user, params=request.GET)
        return render(request, "dashboard/audit.html", result)
    except DashboardError as exc:
        return render(request, "dashboard/audit.html", {"error": str(exc)}, status=403)


@login_required
def notifications_view(request):
    page = NotificationListService.list_for(request.user, page=request.GET.get("page", 1))
    return render(request, "dashboard/notifications.html", {"page": page})


@csrf_exempt
@require_POST
def inbound_event(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return JsonResponse({"accepted": False, "reason": str(exc)}, status=400)
    outcome = EventIntake.handle(payload)
    return JsonResponse(
        {
            "accepted": outcome.accepted,
            "duplicate": outcome.duplicate,
            "reason": outcome.rejection_reason,
        },
        status=200 if outcome.accepted else 400,
    )
