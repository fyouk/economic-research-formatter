"""Small, dependency-free audit model helpers.

The inspector and linter exchange dictionaries deliberately.  A dictionary is
easy to persist, but keeping the status vocabulary and finding construction in
one place prevents each rule implementation from drifting slightly from the
published audit schema.
"""

from __future__ import annotations

from collections import Counter, defaultdict
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
    """Return a privacy-conscious target object for an inspected paragraph."""

    paragraph = paragraph or {}
    paragraph_kind = str(paragraph.get("kind", "")).casefold()
    target: dict[str, Any] = {
        "kind": paragraph_kind if paragraph_kind in {"footnote", "ordinary_footnote", "endnote"} else "paragraph"
    }
    for key in ("id", "index", "text_preview"):
        if key in paragraph:
            value = paragraph[key]
            target[key] = value[:_MAX_CONTENT_SNIPPET] if key == "text_preview" and isinstance(value, str) else value
    if "text_preview" not in target and isinstance(paragraph.get("text"), str):
        target["text_preview"] = paragraph["text"][:_MAX_CONTENT_SNIPPET]
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


def build_summary(findings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build status counts and compact, deterministic per-rule aggregates."""

    materialized = [dict(item) for item in findings]
    by_status_counter: Counter[str] = Counter(
        str(item.get("status", "NOT_CHECKED")) for item in materialized
    )
    # Preserve the complete status vocabulary in the output.  A consumer can
    # therefore distinguish a zero count from an old producer omitting a key.
    by_status = {status: int(by_status_counter.get(status, 0)) for status in STATUSES}

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in materialized:
        grouped[(str(item.get("rule_id", "")), str(item.get("status", "NOT_CHECKED")))].append(item)

    aggregates: list[dict[str, Any]] = []
    for (rule_id, status), group in sorted(grouped.items(), key=lambda pair: pair[0]):
        examples: list[dict[str, Any]] = []
        seen_targets: set[tuple[Any, ...]] = set()
        for item in sorted(
            group,
            key=lambda value: (
                value.get("target", {}).get("index", 10**12),
                value.get("target", {}).get("id", ""),
            ),
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
                "message": _message_for_group(group),
                "examples": examples,
            }
        )

    return {
        "total_findings": len(materialized),
        "by_status": by_status,
        "aggregates": aggregates,
    }
