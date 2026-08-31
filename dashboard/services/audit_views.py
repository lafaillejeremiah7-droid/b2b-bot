from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Q

from dashboard.models import AuditActionType, AuditEntry, Deal
from dashboard.services.authz import Action, Authz
from dashboard.services.errors import ValidationRejected


class AuditQueryService:
    @staticmethod
    def for_lead(*, lead_id: int, page: int = 1):
        deal_id = Deal.objects.filter(lead_id=lead_id).values_list("deal_id", flat=True).first()
        query = Q(target_type="lead", target_id=lead_id)
        if deal_id is not None:
            query |= Q(target_type="deal", target_id=deal_id)
        qs = AuditEntry.objects.filter(query).select_related("actor").order_by("-occurred_at", "-id")
        return Paginator(qs, 50).get_page(max(1, int(page or 1)))

    @staticmethod
    def search(*, operator, params):
        Authz.check(operator, Action.AUDIT_SEARCH)
        qs = AuditEntry.objects.select_related("actor").all()
        actor_id = (params.get("actor_id") or "").strip()
        if actor_id:
            try:
                qs = qs.filter(actor_id=int(actor_id))
            except ValueError as exc:
                raise ValidationRejected("actor_id must be an integer") from exc
        action_type = (params.get("action_type") or "").strip()
        if action_type:
            if action_type not in AuditActionType.values:
                raise ValidationRejected("unknown audit action type")
            qs = qs.filter(action_type=action_type)
        start = params.get("start")
        end = params.get("end")
        if start:
            qs = qs.filter(occurred_at__date__gte=start)
        if end:
            qs = qs.filter(occurred_at__date__lte=end)
        qs = qs.order_by("-occurred_at", "-id")
        try:
            page = max(1, int(params.get("page") or 1))
        except ValueError:
            page = 1
        paginator = Paginator(qs, 50)
        return {"page": paginator.get_page(page), "total": paginator.count}
