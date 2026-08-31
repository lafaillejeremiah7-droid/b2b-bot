from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from dashboard.models import AuditActionType, Deal, Invoice, Lead, Operator, SiteProject
from dashboard.services.audit import AuditLogger
from dashboard.services.authz import Action, Authz
from dashboard.services.errors import ValidationRejected

PRICE_FLOOR = 550
PRICE_ANCHOR = 850
PRICE_CAP = 1000


@dataclass(frozen=True)
class SuggestedPrice:
    amount: int
    is_fallback: bool
    missing: tuple[str, ...] = ()


def suggested_price(page_count: int, website_condition: int, urgency: int) -> int:
    return min(
        PRICE_CAP,
        PRICE_FLOOR
        + 150 * max(0, int(page_count) - 3)
        + 150 * (1 if int(website_condition) <= 2 else 0)
        + 100 * (1 if int(urgency) >= 4 else 0),
    )


def resolve_inputs(lead: Lead) -> tuple[int | None, int | None, int | None]:
    latest = SiteProject.objects.filter(lead_id=lead.id).order_by("-created_at", "-id").first()
    page_count = latest.page_count if latest and latest.page_count is not None else lead.estimated_page_count
    return page_count, lead.website_condition, lead.urgency


def recommendation_for(lead: Lead) -> SuggestedPrice:
    page_count, condition, urgency = resolve_inputs(lead)
    names = ("page_count", "website_condition", "urgency")
    values = (page_count, condition, urgency)
    missing = tuple(name for name, value in zip(names, values) if value is None)
    if missing:
        return SuggestedPrice(PRICE_ANCHOR, True, missing)
    return SuggestedPrice(suggested_price(int(page_count), int(condition), int(urgency)), False)


class PriceService:
    @staticmethod
    @transaction.atomic
    def set_agreed_price(*, lead_id: int, operator: Operator, submitted_value) -> Deal:
        Authz.check(operator, Action.PRICE_SET)
        if isinstance(submitted_value, bool):
            raise ValidationRejected("Agreed price must be a whole dollar value from 550 to 1000.")
        try:
            amount = int(str(submitted_value).strip())
        except (TypeError, ValueError) as exc:
            raise ValidationRejected("Agreed price must be a whole dollar value from 550 to 1000.") from exc
        if str(submitted_value).strip() != str(amount) or not PRICE_FLOOR <= amount <= PRICE_CAP:
            raise ValidationRejected("Agreed price must be a whole dollar value from 550 to 1000.")

        lead = Lead.objects.select_for_update().get(pk=lead_id)
        deal, _ = Deal.objects.get_or_create(lead=lead)
        deal = Deal.objects.select_for_update().get(pk=deal.pk)
        if Invoice.objects.filter(deal_id=deal.pk).exists():
            raise ValidationRejected(
                "Agreed price cannot change after an invoice exists.",
                target_type="deal",
                target_id=deal.pk,
                before_snapshot={"agreed_price": deal.agreed_price},
            )
        before = deal.agreed_price
        recommendation = recommendation_for(lead)
        deal.agreed_price = amount
        deal.save(update_fields=["agreed_price"])
        AuditLogger.record(
            operator,
            AuditActionType.AGREED_PRICE_CHANGE,
            deal,
            {"agreed_price": before, "suggested_price": recommendation.amount},
            {"agreed_price": amount, "suggested_price": recommendation.amount},
        )
        return deal
