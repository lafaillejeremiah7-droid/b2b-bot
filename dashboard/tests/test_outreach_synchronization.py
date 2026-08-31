from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from django.utils import timezone

from dashboard.adapter.pipeline import AdapterResult
from dashboard.models import (
    AdapterInvocation,
    AdapterOperationName,
    AdapterResultStatus,
    Email,
    Lead,
    Operator,
    OutreachChannel,
    OutreachRequest,
    OutreachRequestStatus,
    PipelineState,
    SiteProject,
    SiteReviewState,
)
from dashboard.services.confirmation import mint_confirmation
from dashboard.services.errors import ValidationRejected
from dashboard.services.outreach_controller import OutreachController


class Session(dict):
    modified = False


@dataclass
class FakeEmailAdapter:
    calls: list[dict] = field(default_factory=list)

    def send_prospect_email(self, **kwargs):
        self.calls.append(kwargs)
        return AdapterResult("success", payload={"message_id": "msg-1"})


def _operator() -> Operator:
    return Operator.objects.create_operator("agent@example.com", "pw", role=Operator.Role.AGENT)


def _lead(email: str, company: str = "Example Co") -> Lead:
    return Lead.objects.create(
        company_name=company,
        contact_name="Owner",
        contact_email=email,
        researched_score=4,
        status=PipelineState.NEW_LEAD,
        last_activity_at=timezone.now(),
    )


@pytest.mark.django_db
def test_reused_request_id_must_match_same_lead_and_channel():
    operator = _operator()
    first = _lead("first@example.com", "First Co")
    second = _lead("second@example.com", "Second Co")
    request_id = uuid4()
    OutreachRequest.objects.create(
        id=request_id,
        lead=first,
        channel=OutreachChannel.EMAIL,
        status=OutreachRequestStatus.FAILED,
        failure_reason="old failure",
        clearance_timestamp=timezone.now(),
    )
    session = Session()
    token = mint_confirmation(session, action="outreach.send", target_id=second.pk)

    with pytest.raises(ValidationRejected, match="different Lead or channel"):
        OutreachController.send_email(
            lead_id=second.pk,
            operator=operator,
            session=session,
            confirmation_token=token,
            subject="Hello",
            body="A normal outreach message.",
            outreach_request_id=request_id,
        )


@pytest.mark.django_db
def test_failed_retry_cannot_change_original_message_or_recipient():
    operator = _operator()
    lead = _lead("buyer@example.com")
    request = OutreachRequest.objects.create(
        id=uuid4(),
        lead=lead,
        channel=OutreachChannel.EMAIL,
        status=OutreachRequestStatus.FAILED,
        failure_reason="temporary failure",
        clearance_timestamp=timezone.now(),
    )
    AdapterInvocation.objects.create(
        operation_name=AdapterOperationName.SEND_PROSPECT_EMAIL,
        arguments={
            "lead_id": lead.pk,
            "to_email": "buyer@example.com",
            "subject": "Original subject",
            "body": "Original body",
        },
        idempotency_key=request.id,
        result=AdapterResultStatus.FAILURE,
        failure_reason="temporary failure",
        elapsed_ms=1,
    )

    with pytest.raises(ValidationRejected, match="cannot change the original subject or body"):
        OutreachController.retry_email(
            outreach_request_id=request.id,
            operator=operator,
            subject="Changed subject",
            body="Original body",
        )

    lead.contact_email = "changed@example.com"
    lead.save(update_fields=["contact_email"])
    with pytest.raises(ValidationRejected, match="Lead email changed"):
        OutreachController.retry_email(
            outreach_request_id=request.id,
            operator=operator,
            subject="Original subject",
            body="Original body",
        )


@pytest.mark.django_db
def test_pending_email_request_cannot_be_retried_concurrently():
    operator = _operator()
    lead = _lead("buyer@example.com")
    request = OutreachRequest.objects.create(
        id=uuid4(),
        lead=lead,
        channel=OutreachChannel.EMAIL,
        status=OutreachRequestStatus.PENDING,
        clearance_timestamp=timezone.now(),
    )

    with pytest.raises(ValidationRejected, match="definitively failed"):
        OutreachController.retry_email(
            outreach_request_id=request.id,
            operator=operator,
            subject="Original subject",
            body="Original body",
        )


@pytest.mark.django_db
def test_unreferenced_latest_site_does_not_poison_plain_email(monkeypatch):
    operator = _operator()
    lead = _lead("buyer@example.com")
    site = SiteProject.objects.create(
        lead=lead,
        preview_url="https://preview.example/site-1",
        page_count=3,
        review_state=SiteReviewState.READY_FOR_REVIEW,
        generated_at=timezone.now(),
    )
    adapter = FakeEmailAdapter()
    monkeypatch.setattr("dashboard.services.outreach_controller.get_pipeline_adapter", lambda: adapter)
    session = Session()
    token = mint_confirmation(session, action="outreach.send", target_id=lead.pk)

    outcome = OutreachController.send_email(
        lead_id=lead.pk,
        operator=operator,
        session=session,
        confirmation_token=token,
        subject="Quick question",
        body="I wanted to introduce myself. This email contains no preview link.",
        site_project_id=site.pk,
    )

    assert outcome.adapter_result.status == "success"
    assert len(adapter.calls) == 1
    row = Email.objects.get(pk=outcome.recorded_row_id)
    assert row.site_project_id is None
