"""Stable JSON serialization for audit dictionaries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def serialize_audit(audit: Mapping[str, Any]) -> str:
    """Serialize an audit as deterministic UTF-8-compatible JSON text.

    No timestamp or process-specific field is added.  This makes two runs on
    the same inspection byte-for-byte comparable and keeps private document
    reports free of accidental path enrichment.
    """

    if not isinstance(audit, Mapping):
        raise TypeError("audit must be a mapping")
    return json.dumps(dict(audit), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json_report(audit: Mapping[str, Any], output: Path | str) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_audit(audit), encoding="utf-8")
    return path


# Friendly aliases for callers that use the report format in the name.
dump_audit_json = serialize_audit
write_audit_json = write_json_report


__all__ = ["dump_audit_json", "serialize_audit", "write_audit_json", "write_json_report"]
