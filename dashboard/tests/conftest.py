"""Shared pytest compatibility fixtures for schema evolution.

Older task-local tests intentionally asserted that later-task indexes/triggers did
not exist yet. Once those later tasks are implemented, those negative assertions
are historical rather than product requirements. They are skipped explicitly
below and replaced by positive enforcement tests in ``test_schema_enforcement``.
"""

from __future__ import annotations

import uuid as uuid_module

import pytest

from dashboard.models import OutreachRequest


OBSOLETE_SCOPE_TESTS = {
    "dashboard/tests/test_lead_model.py::NoIndexOrTriggerYetTests::test_only_the_primary_key_index_exists",
    "dashboard/tests/test_lead_model.py::NoIndexOrTriggerYetTests::test_no_trigger_exists_on_leads",
    "dashboard/tests/test_outreach_models.py::EmailIdempotencyTests::test_the_cross_table_half_of_5_12_is_not_enforced_here",
}


def pytest_collection_modifyitems(items):
    marker = pytest.mark.skip(
        reason="superseded by completed Task 2.4/3 database enforcement tests"
    )
    for item in items:
        if item.nodeid in OBSOLETE_SCOPE_TESTS:
            item.add_marker(marker)


@pytest.fixture(autouse=True)
def seed_outreach_request_parent_for_legacy_schema_tests(request, monkeypatch):
    """Create a matching reservation whenever the Task 2.2 tests mint a UUID.

    Task 2.2 introduced child UUID columns before Task 2.3 attached real foreign
    keys. This fixture supplies the parent row so those tests can continue to
    isolate their original column/check/unique invariants without weakening the
    actual FK in PostgreSQL.
    """

    instance = request.instance
    if instance is None or instance.__class__.__module__ != "dashboard.tests.test_outreach_models":
        return

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

    monkeypatch.setattr(outreach_tests.uuid, "uuid4", uuid4_with_reservation)
