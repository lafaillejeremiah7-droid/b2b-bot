"""Deterministic, content-addressed provenance for discovery actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def _json_value(value: Any) -> Any:
    """Convert supported immutable contract values into canonical JSON data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _json_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported provenance value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Encode a value without whitespace or key-order ambiguity."""
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def content_digest(value: Any) -> str:
    """Return the SHA-256 digest of canonical JSON data."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
