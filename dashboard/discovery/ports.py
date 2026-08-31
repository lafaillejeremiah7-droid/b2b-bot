"""Injected model boundary for Bot 1; no provider or network implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class StructuredModelPort(Protocol):
    """A model that returns one JSON-compatible object for an exact schema.

    Provider implementations must place ``payload['directive']`` in a trusted
    system/developer instruction and all ``untrusted_sources`` in user/tool data.
    Concatenating those fields into one prompt would erase the injection boundary
    that the orchestrator preserves and is therefore outside this contract.
    """

    model_id: str

    async def complete(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...
