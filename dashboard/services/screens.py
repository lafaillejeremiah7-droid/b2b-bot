from __future__ import annotations

from dataclasses import dataclass

from django.core.paginator import Paginator
from django.db.models import BooleanField, Exists, F, OuterRef, Q, Subquery, Value
from django.db.models.expressions import OrderBy

from dashboard.models import (
    AuditActionType,
    AuditEntry,
    Call,
    Deal,
    Email,
    EmailBounce,
    Invoice,
    Lead,
    Notification,
    Payment,
    PipelineState,
    PipelineStateHistory,
    ReleaseAuthorization,
    SiteProject,
)
from dashboard.services.authz import available_actions
from dashboard.services.errors import ValidationRejected


@dataclass(frozen=True)
class LeadListResult:
    page: object
    total: int
    query_state: dict


SORT_FIELDS = {
    "company": "company_name",
    "industry": "industry",
    "state": "status",
    "score": "researched_score",
    "price": "preferred_price",
    "activity": "last_activity_at",
}


class LeadListService:
    @staticmethod
    def query(*, params, operator) -> LeadListResult:
        qs = Lead.objects.all()
        status = (params.get("status") or "").strip()
        if status:
            if status not in PipelineState.values:
                raise ValidationRejected("Unknown Pipeline_State filter.")
            qs = qs.filter(status=status)
        score = (params.get("score") or "").strip()
        if score:
            try:
                score_int = int(score)
            except ValueError as exc:
                raise ValidationRejected("Score filter must be 1 through 5.") from exc
            if score_int not in range(1, 6):
                raise ValidationRejected("Score filter must be 1 through 5.")
            qs = qs.filter(researched_score=score_int)

        search = (params.get("q") or "").strip()
        if search:
            if len(search) > 100:
                raise ValidationRejected("Search text may contain at most 100 characters.")
            qs = qs.filter(
                Q(company_name__icontains=search)
                | Q(contact_name__icontains=search)
                | Q(contact_email__icontains=search)
                | Q(contact_phone__icontains=search)
            )

        latest_site = SiteProject.objects.filter(lead_id=OuterRef("pk")).order_by("-created_at", "-id")
        dup_email = Lead.objects.exclude(pk=OuterRef("pk")).filter(email_normalized=OuterRef("email_normalized")).exclude(email_normalized__isnull=True)
        dup_phone = Lead.objects.exclude(pk=OuterRef("pk")).filter(phone_digits=OuterRef("phone_digits")).exclude(phone_digits="")
        bounce = EmailBounce.objects.filter(lead_id=OuterRef("pk"), contact_email__iexact=OuterRef("contact_email"))
        qs = qs.annotate(
            latest_site_state=Subquery(latest_site.values("review_state")[:1]),
            duplicate_email=Exists(dup_email),
            duplicate_phone=Exists(dup_phone),
            bounced=Exists(bounce),
        )

        sort_key = params.get("sort") or "activity"
        field = SORT_FIELDS.get(sort_key)
        if field is None:
            raise ValidationRejected("Unknown sort field.")
        direction = (params.get("dir") or "desc").lower()
        if direction not in {"asc", "desc"}:
            raise ValidationRejected("Sort direction must be asc or desc.")
        order = OrderBy(F(field), descending=direction == "desc", nulls_last=True)
        qs = qs.order_by(order, "id")

        paginator = Paginator(qs, 50)
        try:
            requested_page = max(1, int(params.get("page") or 1))
        except ValueError:
            requested_page = 1
        page_number = min(requested_page, max(1, paginator.num_pages))
        page = paginator.get_page(page_number)
        # Attach the single source-of-truth availability to rendered rows.
        for lead in page.object_list:
            lead.action_availability = available_actions(lead, operator)
            lead.compliance_badges = tuple(
                name
                for name, active in (
                    ("Unsubscribed", lead.unsubscribed_at is not None),
                    ("Do Not Call", lead.do_not_call_at is not None),
                    ("Bounced", bool(lead.bounced)),
                    ("Duplicate Contact", bool(lead.duplicate_email or lead.duplicate_phone)),
                )
                if active
            )
        return LeadListResult(page, paginator.count, dict(params.items()))


class DealRoomService:
    @staticmethod
    def get(*, lead_id: int, operator) -> dict:
        lead = Lead.objects.get(pk=lead_id)
        deal = Deal.objects.filter(lead_id=lead.id).first()
        latest_site = SiteProject.objects.filter(lead_id=lead.id).order_by("-created_at", "-id").first()
        invoice = Invoice.objects.filter(deal_id=deal.pk).first() if deal else None
        payment = Payment.objects.filter(deal_id=deal.pk).order_by("-paid_date", "-id").first() if deal else None
        authorization = ReleaseAuthorization.objects.filter(deal_id=deal.pk).first() if deal else None
        return {
            "lead": lead,
            "deal": deal,
            "latest_site": latest_site,
            "invoice": invoice,
            "payment": payment,
            "authorization": authorization,
            "release_status": "Released" if authorization else "Locked",
            "actions": available_actions(lead, operator),
        }

    @staticmethod
    def activity(*, lead_id: int, page: int = 1) -> dict:
        deal_id = Deal.objects.filter(lead_id=lead_id).values_list("deal_id", flat=True).first()
        items: list[dict] = []
        for email in Email.objects.filter(lead_id=lead_id).order_by("-sent_at")[:500]:
            items.append({"occurred_at": email.sent_at, "kind": "email", "summary": email.subject, "id": email.id})
        for call in Call.objects.filter(lead_id=lead_id).order_by("-timestamp")[:500]:
            items.append({"occurred_at": call.timestamp, "kind": "call", "summary": call.outcome, "id": call.id})
        for history in PipelineStateHistory.objects.filter(lead_id=lead_id).order_by("-occurred_at")[:500]:
            items.append({"occurred_at": history.occurred_at, "kind": "state", "summary": f"{history.from_state or '∅'} → {history.to_state}", "id": history.id})
        audit_q = Q(target_type="lead", target_id=lead_id)
        if deal_id is not None:
            audit_q |= Q(target_type="deal", target_id=deal_id)
        for audit in AuditEntry.objects.filter(audit_q).order_by("-occurred_at", "-id")[:500]:
            items.append({"occurred_at": audit.occurred_at, "kind": "action", "summary": audit.action_type, "id": audit.id})
        items.sort(key=lambda item: (item["occurred_at"], item["kind"]), reverse=True)
        paginator = Paginator(items, 50)
        page_obj = paginator.get_page(max(1, int(page or 1)))
        return {"page": page_obj, "total": paginator.count}


class NotificationListService:
    @staticmethod
    def list_for(operator, *, page: int = 1):
        qs = Notification.objects.filter(
            operator=operator,
            created_at__gte=__import__("django.utils.timezone", fromlist=["now"]).now() - __import__("datetime").timedelta(days=30),
        ).prefetch_related("deliveries").order_by("-created_at", "-id")
        return Paginator(qs, 50).get_page(max(1, int(page or 1)))
