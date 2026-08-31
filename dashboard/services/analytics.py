from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Avg, Count, Q
from django.utils import timezone

from dashboard.models import (
    Call,
    Deal,
    Email,
    EmailVariantAssignment,
    Invoice,
    Lead,
    Payment,
    PipelineState,
    PipelineStateHistory,
    VariantDimension,
)
from dashboard.services.errors import ValidationRejected

FUNNEL_STAGES = (
    PipelineState.NEW_LEAD,
    PipelineState.CONTACTED,
    PipelineState.REPLIED,
    PipelineState.SCHEDULED,
    PipelineState.QUOTED,
    PipelineState.WON,
    PipelineState.RELEASED,
)


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self) -> Decimal | None:
        if self.denominator == 0:
            return None
        return Decimal(self.numerator) / Decimal(self.denominator)


@dataclass(frozen=True)
class ReportingRange:
    start: datetime
    end_exclusive: datetime
    start_date: date
    end_date: date


def resolve_reporting_range(start_date: date | None = None, end_date: date | None = None) -> ReportingRange:
    zone = ZoneInfo(settings.REPORTING_TIMEZONE)
    local_today = timezone.now().astimezone(zone).date()
    end_date = end_date or local_today
    start_date = start_date or (end_date - timedelta(days=29))
    if start_date > end_date:
        raise ValidationRejected("Start date must not be after end date.")
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if months > 24 or (months == 24 and end_date.day > start_date.day):
        raise ValidationRejected("Reporting ranges may span at most 24 months.")
    start_local = datetime.combine(start_date, time.min, tzinfo=zone)
    end_local = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    return ReportingRange(
        start=start_local.astimezone(dt_timezone.utc),
        end_exclusive=end_local.astimezone(dt_timezone.utc),
        start_date=start_date,
        end_date=end_date,
    )


def _in_range(field: str, rr: ReportingRange) -> Q:
    return Q(**{f"{field}__gte": rr.start, f"{field}__lt": rr.end_exclusive})


class AnalyticsService:
    @staticmethod
    def summary(*, reporting_range: ReportingRange | None = None) -> dict:
        rr = reporting_range or resolve_reporting_range()

        reached = {
            state.value: PipelineStateHistory.objects.filter(
                to_state=state,
                occurred_at__gte=rr.start,
                occurred_at__lt=rr.end_exclusive,
            ).values("lead_id").distinct().count()
            for state in PipelineState
        }
        current = {
            state.value: Lead.objects.filter(
                status=state,
                created_at__gte=rr.start,
                created_at__lt=rr.end_exclusive,
            ).count()
            for state in PipelineState
        }

        cohort_ids = list(
            PipelineStateHistory.objects.filter(
                from_state__isnull=True,
                to_state=PipelineState.NEW_LEAD,
                occurred_at__gte=rr.start,
                occurred_at__lt=rr.end_exclusive,
            ).values_list("lead_id", flat=True)
        )
        cohort_counts: dict[str, int] = {}
        previous = len(set(cohort_ids))
        funnel = []
        for index, stage in enumerate(FUNNEL_STAGES):
            count = PipelineStateHistory.objects.filter(
                lead_id__in=cohort_ids,
                to_state=stage,
            ).values("lead_id").distinct().count()
            cohort_counts[stage.value] = count
            if index == 0:
                drop = 0
                rate = Rate(0, count)
            else:
                drop = max(0, previous - count)
                rate = Rate(drop, previous)
            funnel.append({"stage": stage.value, "count": count, "drop_off": drop, "drop_off_rate": rate.value})
            previous = count

        emails = Email.objects.filter(sent_at__gte=rr.start, sent_at__lt=rr.end_exclusive)
        email_count = emails.count()
        email_rates = {
            "open": Rate(emails.exclude(opened_at__isnull=True).count(), email_count),
            "click": Rate(emails.exclude(clicked_at__isnull=True).count(), email_count),
            "reply": Rate(emails.exclude(reply_at__isnull=True).count(), email_count),
            "unsubscribe": Rate(emails.filter(unsubscribed=True).count(), email_count),
        }

        calls = Call.objects.filter(timestamp__gte=rr.start, timestamp__lt=rr.end_exclusive)
        call_rate = Rate(calls.filter(outcome="answered").count(), calls.count())

        post_won = (
            PipelineState.WON,
            PipelineState.INVOICED,
            PipelineState.PAID_PENDING_VERIFICATION,
            PipelineState.PAYMENT_VERIFIED,
            PipelineState.RELEASED,
        )
        deals = Deal.objects.filter(lead__status__in=post_won)
        won_count = PipelineStateHistory.objects.filter(
            to_state=PipelineState.WON,
            occurred_at__gte=rr.start,
            occurred_at__lt=rr.end_exclusive,
        ).values("lead_id").distinct().count()
        released_count = PipelineStateHistory.objects.filter(
            to_state=PipelineState.RELEASED,
            occurred_at__gte=rr.start,
            occurred_at__lt=rr.end_exclusive,
        ).values("lead_id").distinct().count()
        close_rate = Rate(released_count, won_count)
        mean_price = deals.aggregate(v=Avg("agreed_price"))["v"]

        invoices = Invoice.objects.filter(issued_at__gte=rr.start, issued_at__lt=rr.end_exclusive)
        verified_deals = Deal.objects.filter(
            payment_verified_at__gte=rr.start,
            payment_verified_at__lt=rr.end_exclusive,
        ).exclude(agreed_price__isnull=True)
        revenue = sum(verified_deals.values_list("agreed_price", flat=True))

        days_to_payment: list[int] = []
        for invoice in invoices.iterator():
            payment = Payment.objects.filter(deal_id=invoice.deal_id).order_by("paid_date", "id").first()
            if payment:
                issued_local = invoice.issued_at.astimezone(ZoneInfo(settings.REPORTING_TIMEZONE)).date()
                days_to_payment.append((payment.paid_date - issued_local).days)

        variants = []
        for dimension in VariantDimension.values:
            assignments = EmailVariantAssignment.objects.filter(
                dimension=dimension,
                email__sent_at__gte=rr.start,
                email__sent_at__lt=rr.end_exclusive,
            ).select_related("email", "variant")
            by_variant: dict[int, dict] = {}
            for assignment in assignments:
                bucket = by_variant.setdefault(
                    assignment.variant_id,
                    {
                        "dimension": dimension,
                        "value": assignment.variant.value,
                        "sends": 0,
                        "replies": 0,
                        "meetings": 0,
                        "closes": 0,
                    },
                )
                bucket["sends"] += 1
                if assignment.email.reply_at is not None:
                    bucket["replies"] += 1
                lead = assignment.email.lead
                if PipelineStateHistory.objects.filter(lead=lead, to_state=PipelineState.SCHEDULED).exists():
                    bucket["meetings"] += 1
                if PipelineStateHistory.objects.filter(lead=lead, to_state=PipelineState.RELEASED).exists():
                    bucket["closes"] += 1
            for bucket in by_variant.values():
                sends = bucket["sends"]
                bucket.update(
                    reply_rate=Rate(bucket["replies"], sends).value,
                    meeting_rate=Rate(bucket["meetings"], sends).value,
                    close_rate=Rate(bucket["closes"], sends).value,
                    insufficient_sample=sends < 30,
                )
                variants.append(bucket)

        return {
            "range": rr,
            "activity_reached": reached,
            "current_state": current,
            "current_state_total": sum(current.values()),
            "cohort_size": len(set(cohort_ids)),
            "cohort_counts": cohort_counts,
            "funnel": funnel,
            "email_count": email_count,
            "email_rates": email_rates,
            "call_connect_rate": call_rate,
            "close_rate": close_rate,
            "mean_agreed_price": mean_price,
            "revenue": revenue,
            "invoice_count": invoices.count(),
            "median_days_to_payment": median(days_to_payment) if days_to_payment else None,
            "variants": variants,
        }
