"""Rule loading and Inspector-shape adapters used by lint modules."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from economic_research_formatter.rule_loader import (
    ensure_rules_valid,
    load_rules as _load_rules,
    project_root as _profile_root,
)


RULE_FILENAMES = ("manuscript.yaml", "citations.yaml", "references.yaml")


def project_root() -> Path:
    return _profile_root()


def _load_yaml(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except (FileNotFoundError, OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load rule metadata: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"rule metadata must be a mapping: {path}")
    return payload


def rules_for(
    root: Path | str | None = None, *, _validated: bool = False
) -> list[dict[str, Any]]:
    """Load rule dictionaries in source-file order.

    The execution-facing registry is strict: callers cannot receive a
    fallback/filtered list when the rule tree is invalid.  ``_validated`` is a
    private fast path used after the linter's explicit validation gate; direct
    callers still receive the same gate by default.
    """

    resolved = Path(root) if root is not None else project_root()
    if not _validated:
        ensure_rules_valid(resolved)
    values = _load_rules(resolved)
    if not isinstance(values, list):
        raise TypeError(f"loaded rules must be a list: {resolved}")
    if any(not isinstance(value, Mapping) for value in values):
        raise ValueError(f"loaded rule entries must be mappings: {resolved}")
    return [dict(value) for value in values]


def conflicts_for(
    root: Path | str | None = None, *, _validated: bool = False
) -> list[dict[str, Any]]:
    resolved = Path(root) if root is not None else project_root()
    if not _validated:
        ensure_rules_valid(resolved)
    payload = _load_yaml(resolved / "rules" / "conflicts.yaml", {})
    values = payload.get("conflicts", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"conflicts must be a list: {resolved}")
    if any(not isinstance(value, Mapping) for value in values):
        raise ValueError(f"conflict entries must be mappings: {resolved}")
    return [dict(value) for value in values]


def unresolved_conflict_rule_ids(
    conflicts: Iterable[Mapping[str, Any]],
) -> set[str]:
    """Return rule IDs referenced by validated unresolved conflicts."""

    result: set[str] = set()
    for conflict in conflicts:
        status = str(conflict.get("status", "")).casefold()
        if status not in {"unresolved", "unresolved_overlap"}:
            continue
        for side in ("source_a", "source_b"):
            source = conflict.get(side)
            if not isinstance(source, Mapping):
                continue
            rule_id = source.get("rule_id")
            if isinstance(rule_id, str) and rule_id:
                result.add(rule_id)
    return result


def effective_rule_id(conflict: Mapping[str, Any]) -> str | None:
    """Read a conflict's single effective rule declaration.

    Historical metadata placed this field under ``execution`` while newer
    records may put it at conflict top level.  Validation guarantees that two
    declarations, when both present for compatibility, identify the same
    rule; this adapter supports both locations.
    """

    value = conflict.get("effective_rule")
    if isinstance(value, str) and value:
        return value
    execution = conflict.get("execution")
    if isinstance(execution, Mapping):
        value = execution.get("effective_rule")
        if isinstance(value, str) and value:
            return value
    return None


def unresolved_for(
    root: Path | str | None = None, *, _validated: bool = False
) -> list[dict[str, Any]]:
    resolved = Path(root) if root is not None else project_root()
    if not _validated:
        ensure_rules_valid(resolved)
    payload = _load_yaml(resolved / "rules" / "unresolved.yaml", {})
    values = payload.get("unknowns", payload.get("unresolved", []))
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"unresolved entries must be a list: {resolved}")
    if any(not isinstance(value, Mapping) for value in values):
        raise ValueError(f"unresolved entries must be mappings: {resolved}")
    return [dict(value) for value in values]


def classification_by_id(classification: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    values = classification.get("items", []) if isinstance(classification, Mapping) else []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return {}
    return {
        str(item.get("source_id") or item.get("id")): dict(item)
        for item in values
        if isinstance(item, Mapping) and (item.get("source_id") is not None or item.get("id") is not None)
    }


def paragraphs(inspection: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = inspection.get("paragraphs", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        item.setdefault("index", index)
        item.setdefault("id", f"p-{index:06d}")
        result.append(item)
    return result


def text_of(paragraph: Mapping[str, Any]) -> str:
    value = paragraph.get("text")
    if isinstance(value, str) and value:
        return value
    value = paragraph.get("text_preview", "")
    return value if isinstance(value, str) else str(value or "")


def nested_formatting(value: object) -> dict[str, Any]:
    """Flatten common Inspector formatting envelopes without guessing values."""

    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"raw", "effective", "resolved", "formatting", "style"} and isinstance(item, Mapping):
            result.update(nested_formatting(item))
        else:
            result[str(key)] = item
    return result


def paragraph_formatting(paragraph: Mapping[str, Any]) -> dict[str, Any]:
    result = nested_formatting(paragraph)
    for key in ("formatting", "effective_formatting", "effective", "resolved_formatting"):
        nested = paragraph.get(key)
        if isinstance(nested, Mapping):
            result.update(nested_formatting(nested))
    return result


def run_formatting(run: Mapping[str, Any]) -> dict[str, Any]:
    result = nested_formatting(run)
    for key in ("formatting", "effective_formatting", "effective", "resolved_formatting"):
        nested = run.get(key)
        if isinstance(nested, Mapping):
            result.update(nested_formatting(nested))
    return result


def runs_of(paragraph: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = paragraph.get("runs", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [dict(value) for value in values if isinstance(value, Mapping)]


def iter_table_paragraphs(inspection: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    tables = inspection.get("tables", [])
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        return
    for table_index, raw_table in enumerate(tables):
        if not isinstance(raw_table, Mapping):
            continue
        cells = raw_table.get("cells", [])
        if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
            continue
        for cell_index, raw_cell in enumerate(cells):
            if not isinstance(raw_cell, Mapping):
                continue
            cell_paragraphs = raw_cell.get("paragraphs", [])
            if not isinstance(cell_paragraphs, Sequence) or isinstance(cell_paragraphs, (str, bytes)):
                continue
            for paragraph_index, raw_paragraph in enumerate(cell_paragraphs):
                if not isinstance(raw_paragraph, Mapping):
                    continue
                paragraph = dict(raw_paragraph)
                paragraph.setdefault("id", f"t-{table_index:04d}-c-{cell_index:04d}-p-{paragraph_index:04d}")
                paragraph.setdefault("index", paragraph_index)
                paragraph["in_table"] = True
                paragraph["table_index"] = table_index
                paragraph["cell_index"] = cell_index
                yield paragraph


def collection(inspection: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    values = inspection.get(key, [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [dict(value) for value in values if isinstance(value, Mapping)]


def role_for(paragraph: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> str:
    identifier = str(paragraph.get("id", ""))
    item = by_id.get(identifier, {})
    value = item.get("role")
    return str(value) if value else "unknown"


__all__ = [
    "RULE_FILENAMES",
    "classification_by_id",
    "collection",
    "conflicts_for",
    "effective_rule_id",
    "iter_table_paragraphs",
    "nested_formatting",
    "paragraph_formatting",
    "paragraphs",
    "project_root",
    "role_for",
    "rules_for",
    "run_formatting",
    "runs_of",
    "text_of",
    "unresolved_conflict_rule_ids",
    "unresolved_for",
]
