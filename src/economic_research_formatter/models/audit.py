"""Small, dependency-free audit model helpers.

The inspector and linter exchange dictionaries deliberately.  A dictionary is
easy to persist, but keeping the status vocabulary and finding construction in
one place prevents each rule implementation from drifting slightly from the
published audit schema.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


AUDIT_SCHEMA_VERSION = "1.0"

STATUSES = (
    "PASS",
    "ERROR",
    "WARNING",
    "INFO",
    "MANUAL_REVIEW",
    "NOT_CHECKED",
    "NOT_APPLICABLE",
)


_MAX_CONTENT_SNIPPET = 80
_CONTENT_SNIPPET_KEYS = {
    "candidate",
    "content_preview",
    "deleted_text_preview",
    "excerpt",
    "observed_excerpt",
    "text",
    "text_excerpt",
    "text_preview",
}


_UNKNOWN_GROUP_VALUES = {
    "",
    "unknown",
    "unresolved",
    "unavailable",
    "ambiguous",
    "mixed",
    "missing",
    "insufficient_evidence",
    "not_checked",
    "not-checked",
}


def _bound_content_snippets(value: Any, *, key: str | None = None) -> Any:
    """Bound content snippets while leaving structural values untouched."""

    if isinstance(value, str):
        normalized_key = str(key or "").casefold().replace("-", "_")
        is_snippet = (
            normalized_key in _CONTENT_SNIPPET_KEYS
            or normalized_key.endswith("_preview")
            or normalized_key.endswith("_excerpt")
            or normalized_key in {"candidates", "excerpts"}
        )
        return value[:_MAX_CONTENT_SNIPPET] if is_snippet else value
    if isinstance(value, Mapping):
        return {
            item_key: _bound_content_snippets(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_bound_content_snippets(item, key=key) for item in value]
    if isinstance(value, tuple):
        return tuple(_bound_content_snippets(item, key=key) for item in value)
    return value


@dataclass(frozen=True)
class Finding:
    """A serialisable rule finding.

    The class is intentionally a thin value object.  Rule code can continue to
    pass ordinary dictionaries around, while callers that want a typed helper
    can use :meth:`to_dict`.
    """

    rule_id: str
    status: str
    message: str
    target: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "status": self.status,
            "message": self.message,
            "target": _bound_content_snippets(dict(self.target)),
            "observed": _bound_content_snippets(dict(self.observed)),
            "expected": dict(self.expected),
            "source": dict(self.source),
            "confidence": max(0.0, min(1.0, float(self.confidence))),
        }


def make_finding(
    rule_id: str,
    status: str,
    message: str,
    *,
    target: Mapping[str, Any] | None = None,
    observed: Mapping[str, Any] | None = None,
    expected: Mapping[str, Any] | None = None,
    source: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Construct a finding with stable key order and validated status.

    Unknown statuses are retained as ``NOT_CHECKED`` rather than being allowed
    to masquerade as a successful check.  This is useful when a new rule file
    is used with an older linter.
    """

    normalized_status = status if status in STATUSES else "NOT_CHECKED"
    return Finding(
        rule_id=str(rule_id),
        status=normalized_status,
        message=str(message),
        target=_bound_content_snippets(dict(target or {})),
        observed=_bound_content_snippets(dict(observed or {})),
        expected=dict(expected or {}),
        source=dict(source or {}),
        confidence=confidence,
    ).to_dict()


def target_for_paragraph(paragraph: Mapping[str, Any] | None, *, role: str | None = None) -> dict[str, Any]:
    """Return a privacy-conscious target while preserving note identity.

    Note-part records have historically been passed through several envelopes
    (raw item, normalized ``NoteTarget``, and classifier item).  Read the
    nested target as a fallback, but never turn an endnote into a footnote or
    discard its ``source_id``/numeric identity when a finding is constructed.
    """

    paragraph = paragraph or {}
    nested_target = paragraph.get("target")
    nested_target = nested_target if isinstance(nested_target, Mapping) else {}
    paragraph_kind = str(paragraph.get("kind") or nested_target.get("kind") or "").casefold()
    note_kinds = {"footnote", "ordinary_footnote", "endnote"}
    target: dict[str, Any] = {
        "kind": paragraph_kind if paragraph_kind in note_kinds else "paragraph"
    }
    # The outer normalized record wins over the nested target.  The latter is
    # still consulted so a caller can pass a raw NoteTarget envelope directly.
    for key in ("id", "index", "text_preview", "source_id", "note_id", "footnote_id", "endnote_id"):
        value = paragraph.get(key)
        if value is None:
            value = nested_target.get(key)
        if value is not None:
            target[key] = value[:_MAX_CONTENT_SNIPPET] if key == "text_preview" and isinstance(value, str) else value
    if "text_preview" not in target:
        text = paragraph.get("text", nested_target.get("text"))
        if isinstance(text, str):
            target["text_preview"] = text[:_MAX_CONTENT_SNIPPET]
    # A normalized note item may carry only ``kind`` + numeric ID.  Fill the
    # stable source ID deterministically rather than leaving consumers to
    # reconstruct it differently.
    if target["kind"] in note_kinds and target.get("source_id") is None:
        note_id = target.get("note_id")
        if note_id is None:
            note_id = target.get(f"{target['kind']}_id")
        if note_id is not None:
            target["source_id"] = f"{target['kind']}-{note_id}"
    if role:
        target["role"] = role
    return target


def target_for_document(inspection: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a stable document target without persisting an absolute path."""

    inspection = inspection or {}
    input_info = inspection.get("input") if isinstance(inspection, Mapping) else {}
    input_info = input_info if isinstance(input_info, Mapping) else {}
    target: dict[str, Any] = {"kind": "document"}
    if input_info.get("filename"):
        target["filename"] = input_info["filename"]
    return target


def _message_for_group(group: Iterable[Mapping[str, Any]]) -> str:
    for finding in group:
        message = finding.get("message")
        if message:
            return str(message)
    return ""


def target_sort_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return a deterministic key for targets with heterogeneous index types."""

    target = value.get("target")
    target = target if isinstance(target, Mapping) else {}
    raw_index = target.get("index")
    try:
        index_key: tuple[int, Any] = (0, int(raw_index))
    except (TypeError, ValueError, OverflowError):
        index_key = (1, str(raw_index or ""))
    return (*index_key, str(target.get("id", "")))


def _is_table_unknown_finding(finding: Mapping[str, Any]) -> bool:
    """Identify repeated table-level uncertainty suitable for compacting.

    Only uncertainty findings are grouped.  A concrete table error remains a
    normal per-target result because collapsing distinct violations would hide
    an actionable location from the caller.
    """

    target = finding.get("target")
    if not isinstance(target, Mapping) or target.get("table_id") is None:
        return False
    status = str(finding.get("status", "")).upper()
    if status not in {"MANUAL_REVIEW", "NOT_CHECKED"}:
        return False
    observed = finding.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}

    def contains_unknown(value: Any, key: str = "") -> bool:
        normalized_key = key.casefold().replace("-", "_")
        if isinstance(value, str):
            normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
            if normalized in _UNKNOWN_GROUP_VALUES:
                return True
            return any(token in normalized_key for token in ("status", "relation", "evidence")) and normalized in {
                "none",
                "null",
            }
        if isinstance(value, Mapping):
            return any(contains_unknown(item, str(item_key)) for item_key, item in value.items())
        if isinstance(value, (list, tuple, set)):
            return any(contains_unknown(item, normalized_key) for item in value)
        return False

    if contains_unknown(observed):
        return True
    if any(key in observed for key in ("unchecked_fields", "missing_formatting_evidence")):
        return True
    # Inspector/linter messages are stable rule-facing text.  This fallback
    # catches older producer envelopes that omitted comparison_status while
    # still explicitly stating that the evidence is unavailable.
    message = str(finding.get("message", ""))
    return any(token in message for token in ("未知", "无法", "缺少", "混合"))


def _finding_affected_count(finding: Mapping[str, Any]) -> int:
    """Return raw target impact represented by one emitted finding."""

    observed = finding.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    if observed.get("aggregation") == "table_unknown":
        value = observed.get("count", 1)
    else:
        value = observed.get("affected_target_count", 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


_UNKNOWN_EVIDENCE_KEY_TOKENS = (
    "reason",
    "status",
    "relation",
    "unchecked",
    "missing",
    "unresolved",
    "evidence",
)


def table_unknown_evidence_signature(
    finding: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Return a stable semantic signature for one table uncertainty."""

    observed = finding.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    entries: list[tuple[str, str]] = []

    def visit(value: Any, path: tuple[str, ...], selected: bool = False) -> None:
        if isinstance(value, Mapping):
            for key, child in sorted(value.items(), key=lambda item: str(item[0])):
                normalized = str(key).casefold().replace("-", "_")
                visit(
                    child,
                    (*path, str(key)),
                    selected or any(token in normalized for token in _UNKNOWN_EVIDENCE_KEY_TOKENS),
                )
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)), selected)
            return
        if isinstance(value, set):
            for index, child in enumerate(sorted(value, key=repr)):
                visit(child, (*path, str(index)), selected)
            return
        if selected:
            entries.append((".".join(path), repr(value)))

    visit(observed, ())
    if not entries:
        return (("message_fallback", str(finding.get("message", ""))),)
    return tuple(entries)


def _table_unknown_aggregates(materialized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, tuple[tuple[str, str], ...]],
        list[dict[str, Any]],
    ] = defaultdict(list)
    for item in materialized:
        if not _is_table_unknown_finding(item):
            continue
        target = item.get("target")
        if not isinstance(target, Mapping):
            continue
        key = (
            str(item.get("rule_id", "")),
            str(item.get("status", "NOT_CHECKED")),
            str(target.get("table_id", "")),
            table_unknown_evidence_signature(item),
        )
        grouped[key].append(item)

    result: list[dict[str, Any]] = []
    for (rule_id, status, table_id, _evidence_signature), group in sorted(
        grouped.items(), key=lambda pair: pair[0]
    ):
        examples: list[dict[str, Any]] = []
        seen_targets: set[tuple[Any, ...]] = set()
        for item in sorted(
            group,
            key=target_sort_key,
        ):
            observed = item.get("observed")
            observed = observed if isinstance(observed, Mapping) else {}
            supplied_examples = observed.get("examples")
            candidates = (
                supplied_examples
                if isinstance(supplied_examples, Sequence)
                and not isinstance(supplied_examples, (str, bytes))
                else [item.get("target")]
            )
            for candidate in candidates:
                if not isinstance(candidate, Mapping):
                    continue
                identity = (candidate.get("kind"), candidate.get("id"), candidate.get("index"))
                if identity in seen_targets:
                    continue
                seen_targets.add(identity)
                examples.append(_bound_content_snippets(dict(candidate)))
                if len(examples) == 3:
                    break
            if len(examples) == 3:
                break
        counts = [_finding_affected_count(item) for item in group]
        result.append(
            {
                "rule_id": rule_id,
                "status": status,
                "table_id": table_id,
                "count": sum(counts),
                "finding_count": len(group),
                "affected_count": sum(counts),
                "message": _message_for_group(group),
                "grouping": "table_unknown",
                "examples": examples,
            }
        )
    return result


# Kept public for the linter's output-stage compactor.  Both the summary and
# detailed finding paths must use exactly the same conservative unknown-table
# predicate, otherwise their counts diverge.
is_table_unknown_finding = _is_table_unknown_finding


def build_summary(findings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build status counts and compact, deterministic per-rule aggregates."""

    materialized = [dict(item) for item in findings]
    by_status_counter: Counter[str] = Counter(
        str(item.get("status", "NOT_CHECKED")) for item in materialized
    )
    by_status_affected_counter: Counter[str] = Counter()
    for item in materialized:
        by_status_affected_counter[str(item.get("status", "NOT_CHECKED"))] += _finding_affected_count(
            item
        )
    # Preserve the complete status vocabulary in the output.  A consumer can
    # therefore distinguish a zero count from an old producer omitting a key.
    by_status = {status: int(by_status_counter.get(status, 0)) for status in STATUSES}
    by_status_affected = {
        status: int(by_status_affected_counter.get(status, 0))
        for status in STATUSES
    }

    by_rule_status_counter: dict[str, Counter[str]] = defaultdict(Counter)
    by_rule_status_affected_counter: dict[str, Counter[str]] = defaultdict(Counter)
    for item in materialized:
        rule_id = str(item.get("rule_id", ""))
        status = str(item.get("status", "NOT_CHECKED"))
        by_rule_status_counter[rule_id][status] += 1
        by_rule_status_affected_counter[rule_id][status] += _finding_affected_count(item)
    by_rule_and_status = {
        rule_id: {
            status: int(counter[status])
            for status in STATUSES
            if counter.get(status, 0)
        }
        for rule_id, counter in sorted(by_rule_status_counter.items())
    }
    by_rule_and_status_affected = {
        rule_id: {
            status: int(counter[status])
            for status in STATUSES
            if counter.get(status, 0)
        }
        for rule_id, counter in sorted(by_rule_status_affected_counter.items())
    }

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in materialized:
        grouped[(str(item.get("rule_id", "")), str(item.get("status", "NOT_CHECKED")))].append(item)

    aggregates: list[dict[str, Any]] = []
    for (rule_id, status), group in sorted(grouped.items(), key=lambda pair: pair[0]):
        examples: list[dict[str, Any]] = []
        seen_targets: set[tuple[Any, ...]] = set()
        for item in sorted(
            group,
            key=target_sort_key,
        ):
            target = item.get("target")
            if not isinstance(target, Mapping):
                continue
            identity = (
                target.get("kind"),
                target.get("id"),
                target.get("index"),
            )
            if identity in seen_targets:
                continue
            seen_targets.add(identity)
            examples.append(_bound_content_snippets(dict(target)))
            if len(examples) == 3:
                break
        aggregates.append(
            {
                "rule_id": rule_id,
                "status": status,
                "count": len(group),
                "finding_count": len(group),
                "affected_count": sum(_finding_affected_count(item) for item in group),
                "message": _message_for_group(group),
                "examples": examples,
            }
        )

    affected_target_count = sum(_finding_affected_count(item) for item in materialized)
    return {
        "total_findings": len(materialized),
        "finding_count": len(materialized),
        "affected_target_count": affected_target_count,
        "total_affected_targets": affected_target_count,
        "by_status": by_status,
        "by_status_affected": by_status_affected,
        "by_rule_and_status": by_rule_and_status,
        "by_rule_and_status_affected": by_rule_and_status_affected,
        "aggregates": aggregates,
        "table_aggregates": _table_unknown_aggregates(materialized),
    }
