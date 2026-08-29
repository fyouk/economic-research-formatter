"""Shared note identity helpers for footnote and endnote evidence.

The inspector deliberately exposes JSON-compatible dictionaries.  This small
value object gives lint code one typed way to iterate both note parts without
turning an endnote into a footnote or losing its numeric target identity.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Literal


NoteKind = Literal["footnote", "endnote"]


@dataclass(frozen=True)
class NoteTarget:
    """A note part item together with its stable target identity."""

    kind: NoteKind
    note_id: int | str
    paragraphs: tuple[Mapping[str, Any], ...]
    target: Mapping[str, Any]
    item: Mapping[str, Any] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        return str(self.target.get("id") or f"{self.kind}-{self.note_id}")

    def to_mapping(self) -> dict[str, Any]:
        """Return a JSON-compatible note mapping retaining source evidence."""

        value = dict(self.item)
        value.update(
            {
                "kind": self.kind,
                "id": self.source_id,
                "source_id": self.source_id,
                "note_id": self.note_id,
                f"{self.kind}_id": self.note_id,
                "target": dict(self.target),
                "paragraphs": [dict(paragraph) for paragraph in self.paragraphs],
            }
        )
        if "text" not in value:
            text = "\n".join(_text_of(paragraph) for paragraph in self.paragraphs).strip()
            if text:
                value["text"] = text
        if "text_preview" not in value:
            text = _text_of(value)
            value["text_preview"] = text[:80]
        return value


def _text_of(value: Mapping[str, Any]) -> str:
    text = value.get("text")
    if isinstance(text, str) and text:
        return text
    preview = value.get("text_preview", "")
    return preview if isinstance(preview, str) else str(preview or "")


def _note_id(raw: Mapping[str, Any], kind: NoteKind, fallback: int) -> int | str:
    for key in (f"{kind}_id", "note_id"):
        value = raw.get(key)
        if value is not None:
            return value
    value = raw.get("id", fallback)
    if isinstance(value, str):
        match = re.fullmatch(rf"{kind}-(.+)", value)
        if match:
            value = match.group(1)
    return value


def _items(info: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    values = info.get("items")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        values = info.get("paragraphs", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(value for value in values if isinstance(value, Mapping))


def note_info(inspection: Mapping[str, Any], kind: NoteKind) -> Mapping[str, Any]:
    """Return one note collection, accepting legacy footnote-only shapes."""

    notes = inspection.get("notes", {}) if isinstance(inspection, Mapping) else {}
    if not isinstance(notes, Mapping):
        return {}
    value = notes.get(f"{kind}s")
    if isinstance(value, Mapping):
        return value
    if kind == "footnote" and any(key in notes for key in ("actual_count", "count", "items", "paragraphs")):
        return notes
    return {}


def note_infos(inspection: Mapping[str, Any]) -> dict[NoteKind, Mapping[str, Any]]:
    """Return available footnote/endnote collections in stable kind order."""

    return {
        kind: info
        for kind in ("footnote", "endnote")
        if (info := note_info(inspection, kind))
    }


def note_linkage(info: Mapping[str, Any], kind: NoteKind) -> dict[str, Any]:
    """Describe active note/reference identity intersection and mismatches."""

    raw_note_ids = info.get("ids", [])
    note_ids = (
        list(raw_note_ids)
        if isinstance(raw_note_ids, Sequence) and not isinstance(raw_note_ids, (str, bytes))
        else []
    )
    raw_reference_ids = info.get("references")
    has_reference_evidence = isinstance(raw_reference_ids, Sequence) and not isinstance(
        raw_reference_ids, (str, bytes)
    )
    reference_ids = list(raw_reference_ids) if has_reference_evidence else []
    note_keys = {str(value) for value in note_ids}
    reference_keys = {str(value) for value in reference_ids}
    note_counts = Counter(str(value) for value in note_ids)
    reference_counts = Counter(str(value) for value in reference_ids)

    def unique_in_order(values: Sequence[Any]) -> list[Any]:
        seen: set[str] = set()
        result: list[Any] = []
        for value in values:
            key = str(value)
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    active_note_ids = unique_in_order(
        [
            value
            for value in note_ids
            if str(value) in reference_keys and note_counts[str(value)] == 1
        ]
    )
    active_reference_ids = unique_in_order(
        [
            value
            for value in reference_ids
            if str(value) in note_keys and note_counts[str(value)] == 1
        ]
    )
    unreferenced_note_ids = unique_in_order([value for value in note_ids if str(value) not in reference_keys])
    missing_definition_reference_ids = unique_in_order(
        [value for value in reference_ids if str(value) not in note_keys]
    )
    duplicate_note_ids = unique_in_order(
        [value for value in note_ids if note_counts[str(value)] > 1]
    )
    duplicate_reference_ids = unique_in_order(
        [value for value in reference_ids if reference_counts[str(value)] > 1]
    )
    reasons: list[str] = []
    if unreferenced_note_ids:
        reasons.append("unreferenced_note_definition")
    if missing_definition_reference_ids:
        reasons.append("reference_without_note_definition")
    if duplicate_reference_ids:
        reasons.append("duplicate_active_reference")
    if duplicate_note_ids:
        reasons.append("duplicate_note_definition")
    if not has_reference_evidence and info.get("one_to_one") is False:
        reasons.append("missing_reference_evidence")
    return {
        "kind": kind,
        "has_reference_evidence": has_reference_evidence,
        "note_ids": note_ids,
        "reference_ids": reference_ids,
        "active_note_ids": active_note_ids,
        "active_reference_ids": active_reference_ids,
        "unreferenced_note_ids": unreferenced_note_ids,
        "missing_definition_reference_ids": missing_definition_reference_ids,
        "duplicate_note_ids": duplicate_note_ids,
        "duplicate_reference_ids": duplicate_reference_ids,
        "reasons": reasons,
        "has_issue": bool(reasons) or info.get("one_to_one") is False,
    }


def iter_note_targets(inspection: Mapping[str, Any]) -> Iterator[NoteTarget]:
    """Yield actual footnote and endnote items with separate target kinds."""

    for kind in ("footnote", "endnote"):
        info = note_info(inspection, kind)
        if not info:
            continue
        linkage = note_linkage(info, kind)
        has_reference_evidence = linkage["has_reference_evidence"]
        referenced_ids = {str(value) for value in linkage["active_reference_ids"]}
        for index, raw in enumerate(_items(info)):
            note_id = _note_id(raw, kind, index)
            if (has_reference_evidence or linkage.get("has_issue")) and str(note_id) not in referenced_ids:
                continue
            source_id = str(raw.get("source_id") or f"{kind}-{note_id}")
            nested = raw.get("paragraphs", ())
            paragraphs = (
                tuple(value for value in nested if isinstance(value, Mapping))
                if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes))
                else ()
            )
            target: dict[str, Any] = {
                "kind": kind,
                "id": source_id,
                "source_id": source_id,
                "note_id": note_id,
                f"{kind}_id": note_id,
                "index": raw.get("index", index),
            }
            for key in ("text_preview", "text"):
                if key in raw:
                    target[key] = raw[key]
            yield NoteTarget(
                kind=kind,
                note_id=note_id,
                paragraphs=paragraphs,
                target=target,
                item=raw,
            )


__all__ = ["NoteKind", "NoteTarget", "iter_note_targets", "note_info", "note_infos", "note_linkage"]
