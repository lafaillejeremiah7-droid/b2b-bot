from __future__ import annotations

import pytest
from django.utils import timezone

from dashboard.models import InboundEventType, Lead, PipelineState, ProcessedEvent, RejectedEvent
from dashboard.services.events import EventIntake


def _lead(company: str) -> Lead:
    return Lead.objects.create(
        company_name=company,
        contact_email=f"{company.lower().replace(' ', '')}@example.com",
        researched_score=4,
        status=PipelineState.CONTACTED,
        last_activity_at=timezone.now(),
    )


@pytest.mark.django_db
def test_event_id_duplicate_requires_same_lead_and_type():
    first = _lead("First Co")
    second = _lead("Second Co")
    ProcessedEvent.objects.create(
        event_id="evt-fixed-1",
        event_type=InboundEventType.EMAIL_OPENED,
        lead=first,
    )
    timestamp = timezone.now().isoformat()

    same = EventIntake.handle(
        {
            "event_id": "evt-fixed-1",
            "event_type": InboundEventType.EMAIL_OPENED,
            "lead_id": first.pk,
            "event_timestamp": timestamp,
        }
    )
    wrong_type = EventIntake.handle(
        {
            "event_id": "evt-fixed-1",
            "event_type": InboundEventType.EMAIL_CLICKED,
            "lead_id": first.pk,
            "event_timestamp": timestamp,
        }
    )
    wrong_lead = EventIntake.handle(
        {
            "event_id": "evt-fixed-1",
            "event_type": InboundEventType.EMAIL_OPENED,
            "lead_id": second.pk,
            "event_timestamp": timestamp,
        }
    )

    assert same.accepted is True
    assert same.duplicate is True
    assert wrong_type.accepted is False
    assert "different Lead or event type" in wrong_type.rejection_reason
    assert wrong_lead.accepted is False
    assert "different Lead or event type" in wrong_lead.rejection_reason
    assert RejectedEvent.objects.filter(event_id="evt-fixed-1").count() == 2
