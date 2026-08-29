"""Canonical numbering-state semantics shared by all pipeline consumers."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


_VISIBLE_REFERENCE_NUMBER_RE = re.compile(
    r"^(?P<leading>[ \u3000]*)"
    r"(?P<marker>"
    r"\[[1-9]\d*\]"
    r"|［[1-9]\d*］"
    r"|【[1-9]\d*】"
    r"|\([1-9]\d*\)"
    r"|（[1-9]\d*）"
    r"|[1-9]\d*[.．、]"
    r")"
    r"(?=\s|[A-Za-z\u4e00-\u9fff])"
)


def numbering_state(value: Any) -> bool | None:
    """Return active, removed/absent, or unresolved numbering state."""

    if value is None:
        return False
    if not isinstance(value, Mapping):
        return None if value else False
    if not value:
        return False
    if (
        value.get("num_id", value.get("numId")) == 0
        or value.get("removes_numbering") is True
        or value.get("numbered") is False
    ):
        return False
    if value.get("resolved") is False:
        return None
    if isinstance(value.get("numbered"), bool):
        return bool(value["numbered"])
    if value.get("resolved") is True:
        return True
    return None


def visible_reference_numbering_evidence(text: Any) -> dict[str, Any]:
    """Return conservative visible numbering evidence from reference text."""

    raw = text if isinstance(text, str) else str(text or "")
    match = _VISIBLE_REFERENCE_NUMBER_RE.match(raw)
    if match is None:
        return {
            "visible_numbering": False,
            "visible_marker": None,
            "visible_marker_span": None,
            "evidence_source": "reference_text_prefix",
        }
    return {
        "visible_numbering": True,
        "visible_marker": match.group("marker"),
        "visible_marker_span": [match.start("marker"), match.end("marker")],
        "evidence_source": "reference_text_prefix",
    }


__all__ = ["numbering_state", "visible_reference_numbering_evidence"]
