from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models.functions import Now
from django.utils.timezone import now as utc_now

from dashboard.models.constraints import length_at_most, length_between, unset_or
from dashboard.models.fields import MillisecondDateTimeField


class SiteReviewState(models.TextChoices):
    GENERATING = "Generating", "Generating"
    READY_FOR_REVIEW = "Ready_For_Review", "Ready for review"
    APPROVED = "Approved", "Approved"
    REJECTED = "Rejected", "Rejected"


class OutreachChannel(models.TextChoices):
    EMAIL = "email", "Email"
    CALL = "call", "Call"


class OutreachRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    INDETERMINATE = "indeterminate", "Indeterminate"


class SiteProject(models.Model):
    id = models.BigAutoField(primary_key=True)
    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="site_projects",
        db_column="lead_id",
    )
    preview_url = models.TextField(null=True, blank=True)
    page_count = models.IntegerField(null=True, blank=True)
    review_state = models.TextField(
        choices=SiteReviewState.choices,
        default=SiteReviewState.GENERATING,
        db_default=SiteReviewState.GENERATING,
    )
    created_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)
    generated_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "site_projects"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(review_state__in=SiteReviewState.values),
                name="site_projects_review_state_in_enum",
            ),
            models.CheckConstraint(
                condition=models.Q(page_count__isnull=True)
                | models.Q(page_count__range=(0, 200)),
                name="site_projects_page_count_range",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        review_state=SiteReviewState.REJECTED,
                        rejection_reason__isnull=False,
                    )
                    & length_between("rejection_reason", 10, 1000)
                )
                | (
                    ~models.Q(review_state=SiteReviewState.REJECTED)
                    & models.Q(rejection_reason__isnull=True)
                ),
                name="site_projects_rejection_reason_matches_state",
            ),
        ]


class SitePage(models.Model):
    id = models.BigAutoField(primary_key=True)
    site_project = models.ForeignKey(
        SiteProject,
        on_delete=models.CASCADE,
        related_name="pages",
        db_column="site_project_id",
    )
    page_index = models.IntegerField()
    text_content = models.TextField()

    class Meta:
        db_table = "site_pages"
        constraints = [
            models.UniqueConstraint(
                fields=("site_project", "page_index"),
                name="site_pages_project_page_unique",
            )
        ]


class Contact(models.Model):
    id = models.BigAutoField(primary_key=True)
    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="contacts",
        db_column="lead_id",
    )
    name = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    title = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "contacts"


class Invoice(models.Model):
    id = models.BigAutoField(primary_key=True)
    deal = models.OneToOneField(
        "dashboard.Deal",
        on_delete=models.PROTECT,
        related_name="invoice_record",
        db_column="deal_id",
    )
    invoice_number = models.TextField(unique=True)
    amount = models.IntegerField()
    issued_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)
    recipient_email = models.TextField(null=True, blank=True)
    provider_invoice_id = models.TextField(null=True, blank=True, unique=True)
    hosted_invoice_url = models.TextField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by_operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sent_invoices",
        db_column="sent_by_operator_id",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "invoices"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__range=(550, 1000)),
                name="invoices_amount_range",
            )
        ]


class Payment(models.Model):
    id = models.BigAutoField(primary_key=True)
    deal = models.ForeignKey(
        "dashboard.Deal",
        on_delete=models.PROTECT,
        related_name="payments",
        db_column="deal_id",
    )
    event_id = models.TextField(unique=True)
    amount_usd = models.IntegerField()
    paid_date = models.DateField()

    class Meta:
        db_table = "payments"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_usd__range=(1, 1000)),
                name="payments_amount_usd_range",
            ),
            models.CheckConstraint(
                condition=length_between("event_id", 1, 128),
                name="payments_event_id_length",
            ),
        ]


class ReleaseAuthorization(models.Model):
    id = models.BigAutoField(primary_key=True)
    deal = models.OneToOneField(
        "dashboard.Deal",
        on_delete=models.PROTECT,
        related_name="release_authorization",
        db_column="deal_id",
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="release_authorizations",
        db_column="operator_id",
    )
    authorized_at = MillisecondDateTimeField()

    class Meta:
        db_table = "release_authorizations"


class OutreachRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead = models.ForeignKey(
        "dashboard.Lead",
        on_delete=models.PROTECT,
        related_name="outreach_requests",
        db_column="lead_id",
    )
    channel = models.TextField(choices=OutreachChannel.choices)
    status = models.TextField(
        choices=OutreachRequestStatus.choices,
        default=OutreachRequestStatus.PENDING,
        db_default=OutreachRequestStatus.PENDING,
    )
    failure_reason = models.TextField(null=True, blank=True)
    clearance_timestamp = MillisecondDateTimeField()
    reserved_at = models.DateTimeField(default=utc_now, db_default=Now(), editable=False)

    class Meta:
        db_table = "outreach_requests"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(channel__in=OutreachChannel.values),
                name="outreach_requests_channel_in_enum",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=OutreachRequestStatus.values),
                name="outreach_requests_status_in_enum",
            ),
            models.CheckConstraint(
                condition=unset_or(
                    "failure_reason", length_at_most("failure_reason", 500)
                ),
                name="outreach_requests_failure_reason_length",
            ),
        ]
