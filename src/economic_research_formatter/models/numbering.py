"""Canonical numbering-state semantics shared by all pipeline consumers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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


__all__ = ["numbering_state"]
