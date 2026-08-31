"""Pure contract tests for Bot 1's sealed discovery handoff."""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given, strategies as st

from dashboard.discovery.contracts import (
    ContractError,
    DiscoveryRequest,
    DiscoverySource,
    Extraction,
)
from dashboard.discovery.provenance import canonical_json, content_digest
from dashboard.tests.hypothesis_profiles import Profile, use


def source(content: str = "Acme Plumbing has an outdated website.") -> DiscoverySource:
    return DiscoverySource(
        url="https://example.test/acme",
        title="Acme Plumbing",
        content=content,
        retrieved_at="2026-08-29T12:00:00+00:00",
    )


def request(**changes) -> DiscoveryRequest:
    values = {
        "idempotency_key": "source:acme-plumbing",
        "brief": "Find public facts about Acme Plumbing.",
        "sources": (source(),),
    }
    values.update(changes)
    return DiscoveryRequest(**values)


def extraction_json(item: DiscoveryRequest) -> dict:
    return {
        "schema_version": "discovery-handoff-v1",
        "parent_digest": item.request_digest,
        "claims": [
            {
                "field_name": "company_name",
                "value": "Acme Plumbing",
                "source_indexes": [0],
            },
            {
                "field_name": "researched_score",
                "value": 4,
                "source_indexes": [0],
            },
        ],
        "notes": ["PUBLIC_BUSINESS_SOURCE"],
    }


def test_source_requires_an_absolute_web_url_and_aware_timestamp() -> None:
    with pytest.raises(ContractError, match="absolute http or https"):
        replace(source(), url="file:///etc/passwd")
    with pytest.raises(ContractError, match="timezone"):
        replace(source(), retrieved_at="2026-08-29T12:00:00")
    with pytest.raises(ContractError, match="retrieved_at"):
        replace(source(), retrieved_at=None)
    with pytest.raises(ContractError, match="tuple of DiscoverySource"):
        request(sources=[source()])


def test_luna_schema_rejects_extra_fields_and_authority_escalation() -> None:
    item = request()
    extra = extraction_json(item)
    extra["send_email"] = True
    with pytest.raises(ContractError, match="fields mismatch"):
        Extraction.from_json(extra, request=item)

    forbidden = extraction_json(item)
    forbidden["claims"][0]["field_name"] = "status"
    with pytest.raises(ContractError, match="unauthorized"):
        Extraction.from_json(forbidden, request=item)


def test_luna_schema_rejects_wrong_types_bounds_and_parent_hash() -> None:
    item = request()
    bad_score = extraction_json(item)
    bad_score["claims"][1]["value"] = 99
    with pytest.raises(ContractError, match="allowed range"):
        Extraction.from_json(bad_score, request=item)

    wrong_parent = extraction_json(item)
    wrong_parent["parent_digest"] = "0" * 64
    with pytest.raises(ContractError, match="parent digest"):
        Extraction.from_json(wrong_parent, request=item)


def test_request_identity_is_stable_but_binds_every_source_byte() -> None:
    first = request()
    same = request()
    changed = request(sources=(source("Different public evidence"),))

    assert first.request_digest == same.request_digest
    assert first.operation_id == same.operation_id
    assert changed.request_digest != first.request_digest
    assert changed.operation_id != first.operation_id


@use(Profile.PURE)
@given(st.dictionaries(st.text(min_size=1, max_size=20), st.integers(), max_size=20))
def test_canonical_hash_is_independent_of_mapping_insertion_order(data) -> None:
    reversed_data = dict(reversed(list(data.items())))
    assert canonical_json(data) == canonical_json(reversed_data)
    assert content_digest(data) == content_digest(reversed_data)
