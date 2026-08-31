"""Pytest fixtures that keep older schema tests valid as later tasks add FKs.

Task 2.2 deliberately introduced ``emails.outreach_request_id`` and
``calls.outreach_request_id`` before the ``outreach_requests`` table existed.
Task 2.3 then attached real deferred foreign keys. The Task 2.2 tests generate
request UUIDs directly because, at the time they were written, there was no
reservation table to seed.

This fixture supplies that newly-required parent row only for
``test_outreach_models``. It does not weaken or disable the FK; PostgreSQL still
checks every child row against a real ``outreach_requests`` record. Keeping the
compatibility setup here lets the Task 2.2 tests continue testing their own
column/check/unique invariants while Task 2.3 tests the new referential layer.
"""

from __future__ import annotations

import uuid as uuid_module

import pytest

from dashboard.models import OutreachRequest


@pytest.fixture(autouse=True)
def seed_outreach_request_parent_for_legacy_schema_tests(request, monkeypatch):
    """Create a matching reservation whenever the Task 2.2 tests mint a UUID.

    The fixture is intentionally scoped by test-module name. No production code
    is patched and no other test module receives synthetic reservations.
    """

    instance = request.instance
    if instance is None or instance.__class__.__module__ != "dashboard.tests.test_outreach_models":
        return

    # Import after collection so this fixture can reuse the test module's exact
    # clearance instant without duplicating another timestamp constant.
    from dashboard.tests import test_outreach_models as outreach_tests

    original_uuid4 = uuid_module.uuid4

    def uuid4_with_reservation():
        request_id = original_uuid4()
        lead = getattr(instance, "lead", None)
        table = getattr(instance, "table", None)

        if lead is not None and table in {"emails", "calls"}:
            OutreachRequest.objects.get_or_create(
                id=request_id,
                defaults={
                    "lead": lead,
                    "channel": "email" if table == "emails" else "call",
                    "clearance_timestamp": outreach_tests.CLEARED_AT,
                },
            )

        return request_id

    # ``test_outreach_models`` imports the stdlib uuid module, so patching its
    # uuid4 attribute covers both helper-generated and direct raw-SQL UUIDs.
    monkeypatch.setattr(outreach_tests.uuid, "uuid4", uuid4_with_reservation)
