"""Shared, exact formatting vocabularies used by Inspector and Linter."""

from __future__ import annotations

from typing import Any


CN_FONT_SIZE_ORDER = (
    ("初号", 42.0),
    ("小初号", 36.0),
    ("一号", 26.0),
    ("小一号", 24.0),
    ("二号", 22.0),
    ("小二号", 18.0),
    ("三号", 16.0),
    ("小三号", 15.0),
    ("四号", 14.0),
    ("小四号", 12.0),
    ("五号", 10.5),
    ("小五号", 9.0),
    ("六号", 7.5),
    ("小六号", 6.5),
    ("七号", 5.5),
    ("八号", 5.0),
)

CN_SIZE_TO_PT = dict(CN_FONT_SIZE_ORDER)
CN_PT_TO_SIZE = {value: name for name, value in CN_FONT_SIZE_ORDER}


def cn_font_size_index(value: Any) -> int | None:
    """Return the exact ordered Chinese-size index, never a near match."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return next(
        (
            index
            for index, (_name, size) in enumerate(CN_FONT_SIZE_ORDER)
            if numeric == size
        ),
        None,
    )


__all__ = [
    "CN_FONT_SIZE_ORDER",
    "CN_PT_TO_SIZE",
    "CN_SIZE_TO_PT",
    "cn_font_size_index",
]
