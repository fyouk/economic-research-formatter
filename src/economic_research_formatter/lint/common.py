"""Shared, deliberately conservative lint helpers."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

from economic_research_formatter.models.audit import make_finding, target_for_document, target_for_paragraph

from .registry import (
    classification_by_id,
    iter_table_paragraphs,
    paragraph_formatting,
    paragraphs,
    role_for,
    run_formatting,
    runs_of,
    text_of,
)


CN_SIZE_TO_PT = {
    "初号": 42.0,
    "小初号": 36.0,
    "一号": 26.0,
    "小一号": 24.0,
    "二号": 22.0,
    "小二号": 18.0,
    "三号": 16.0,
    "小三号": 15.0,
    "四号": 14.0,
    "小四号": 12.0,
    "五号": 10.5,
    "小五号": 9.0,
    "六号": 7.5,
    "小六号": 6.5,
    "七号": 5.5,
    "八号": 5.0,
}


# Heuristic author/author-information roles are intentionally below this
# threshold.  Findings attached to such roles remain useful evidence, but a
# rule must not turn an uncertain role into a deterministic pass/error.
CLASSIFICATION_MANUAL_REVIEW_THRESHOLD = 0.85


def _raw_footnote_items(inspection: Mapping[str, Any]) -> list[dict[str, Any]]:
    notes = inspection.get("notes", {}) if isinstance(inspection, Mapping) else {}
    if not isinstance(notes, Mapping):
        return []
    footnotes = notes.get("footnotes", notes)
    if not isinstance(footnotes, Mapping):
        return []
    values = footnotes.get("items")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        values = footnotes.get("paragraphs", [])
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [dict(value) for value in values if isinstance(value, Mapping)]


@dataclass
class RuleContext:
    inspection: Mapping[str, Any]
    classification: Mapping[str, Any]
    rules_by_id: Mapping[str, Mapping[str, Any]]
    root: Any = None

    def __post_init__(self) -> None:
        self.by_id = classification_by_id(self.classification)
        note_values = self.classification.get("note_items", []) if isinstance(self.classification, Mapping) else []
        if isinstance(note_values, Sequence) and not isinstance(note_values, (str, bytes)):
            self.by_id.update(
                {
                    str(item.get("source_id") or item.get("id")): dict(item)
                    for item in note_values
                    if isinstance(item, Mapping) and (item.get("source_id") is not None or item.get("id") is not None)
                }
            )

        self.paragraph_list = []
        for paragraph in paragraphs(self.inspection):
            self.paragraph_list.append(self._with_classification(paragraph))

        # Note-part items are not body paragraphs and are therefore kept out of
        # ``classification.items`` by the classifier.  They are nevertheless
        # first-class lint targets when the Inspector exposes them.
        raw_notes = _raw_footnote_items(self.inspection)
        classified_notes = note_values if isinstance(note_values, Sequence) and not isinstance(note_values, (str, bytes)) else []
        notes_by_source = {
            str(item.get("source_id") or item.get("id")): item
            for item in classified_notes
            if isinstance(item, Mapping) and (item.get("source_id") is not None or item.get("id") is not None)
        }
        for index, raw_note in enumerate(raw_notes):
            note = dict(raw_note)
            note_id = note.get("id", index)
            source_id = str(note.get("source_id") or f"footnote-{note_id}")
            note.setdefault("source_id", source_id)
            note["id"] = source_id
            note["kind"] = "footnote"
            note.setdefault("footnote_id", note_id)
            classification_item = notes_by_source.get(source_id)
            if classification_item is not None:
                note.update(
                    {
                        key: value
                        for key, value in classification_item.items()
                        if key not in {"text_preview", "index"} or key not in note
                    }
                )
            self.paragraph_list.append(self._with_classification(note))

    def _with_classification(self, paragraph: Mapping[str, Any]) -> dict[str, Any]:
        item = self.by_id.get(str(paragraph.get("id", "")))
        result = dict(paragraph)
        if item:
            result["_classification"] = {
                key: item[key]
                for key in ("source_id", "role", "confidence", "evidence")
                if key in item
            }
        return result

    @property
    def document_target(self) -> dict[str, Any]:
        return target_for_document(self.inspection)

    def role(self, paragraph: Mapping[str, Any]) -> str:
        value = role_for(paragraph, self.by_id)
        if value != "unknown":
            return value
        return str(paragraph.get("role") or "unknown")

    def classified(self, *roles: str) -> list[dict[str, Any]]:
        wanted = set(roles)
        return [p for p in self.paragraph_list if self.role(p) in wanted]

    def table_paragraphs(self) -> list[dict[str, Any]]:
        return list(iter_table_paragraphs(self.inspection))


def source_for(rule: Mapping[str, Any]) -> dict[str, Any]:
    source = rule.get("source", {})
    return dict(source) if isinstance(source, Mapping) else {}


def expected_for(rule: Mapping[str, Any]) -> dict[str, Any]:
    value = rule.get("requirement", {})
    return dict(value) if isinstance(value, Mapping) else {}


def paragraph_target(paragraph: Mapping[str, Any], role: str | None = None) -> dict[str, Any]:
    return target_for_paragraph(paragraph, role=role)


def finding(
    rule: Mapping[str, Any],
    status: str,
    message: str,
    *,
    paragraph: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    observed: Mapping[str, Any] | None = None,
    expected: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    if target is None:
        target = paragraph_target(paragraph or {}, None) if paragraph is not None else {"kind": "document"}
    role = None
    classification: Mapping[str, Any] = {}
    if paragraph is not None:
        role = paragraph.get("role")
        value = paragraph.get("_classification") or paragraph.get("classification")
        if isinstance(value, Mapping):
            classification = value
        elif paragraph.get("classification_confidence") is not None:
            classification = {
                "role": paragraph.get("role"),
                "confidence": paragraph.get("classification_confidence"),
                "evidence": paragraph.get("classification_evidence", []),
            }
    classification_role = classification.get("role") if classification else None
    # Classifier metadata is authoritative when present; an Inspector role
    # hint may be stale or merely copied from a producer-side envelope.
    effective_role = classification_role or role
    if role and isinstance(target, dict) and "role" not in target:
        target = dict(target)
        target["role"] = role
    observed_value = dict(observed or {})
    class_confidence: float | None = None
    try:
        if classification.get("confidence") is not None:
            class_confidence = float(classification["confidence"])
    except (TypeError, ValueError):
        class_confidence = None
    if classification:
        observed_value.setdefault(
            "classification",
            {
                key: classification[key]
                for key in ("source_id", "role", "confidence", "evidence")
                if key in classification
            },
        )
    normalized_status = status
    if (
        effective_role in {"author_name", "author_information"}
        and class_confidence is not None
        and class_confidence < CLASSIFICATION_MANUAL_REVIEW_THRESHOLD
        and status in {"PASS", "ERROR", "WARNING", "INFO"}
    ):
        normalized_status = "MANUAL_REVIEW"
    effective_confidence = confidence
    if class_confidence is not None:
        effective_confidence = min(float(confidence), class_confidence)
    return make_finding(
        str(rule.get("id", "<unknown-rule>")),
        normalized_status,
        message,
        target=target,
        observed=observed_value,
        expected=expected if expected is not None else expected_for(rule),
        source=source_for(rule),
        confidence=effective_confidence,
    )


def document_finding(
    rule: Mapping[str, Any],
    status: str,
    message: str,
    *,
    inspection: Mapping[str, Any],
    observed: Mapping[str, Any] | None = None,
    expected: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    return finding(
        rule,
        status,
        message,
        target=target_for_document(inspection),
        observed=observed,
        expected=expected,
        confidence=confidence,
    )


def rule_severity(rule: Mapping[str, Any]) -> str:
    lint = rule.get("lint", {})
    severity = lint.get("severity") if isinstance(lint, Mapping) else None
    value = str(severity or "info").casefold()
    return {
        "error": "ERROR",
        "warning": "WARNING",
        "info": "INFO",
        "manual_review": "MANUAL_REVIEW",
    }.get(value, "NOT_CHECKED")


def absent_status(rule: Mapping[str, Any]) -> str:
    """Absence means not applicable, except where the rule is itself unknown."""

    severity = rule_severity(rule)
    return "NOT_APPLICABLE" if severity != "NOT_CHECKED" else "NOT_CHECKED"


def meaningful_runs(paragraph: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [run for run in runs_of(paragraph) if text_of(run).strip()]


_ALIASES: dict[str, tuple[str, ...]] = {
    "east_asia_font": (
        "east_asia_font",
        "eastAsia",
        "eastasia",
        "east_asia",
        "font_eastAsia",
        "font_east_asia",
        "font_name_eastAsia",
    ),
    "ascii_font": ("ascii_font", "ascii", "font_ascii", "asciiFont"),
    "hansi_font": ("hansi_font", "hAnsi", "hansi", "font_hAnsi", "font_hansi"),
    "font_size_cn": ("font_size_cn", "size_cn", "cn_size", "fontSizeCn"),
    "size_pt": ("size_pt", "font_size_pt", "fontSizePt", "pt"),
    "size_cs_pt": ("size_cs_pt", "font_size_cs_pt", "fontSizeCsPt"),
    "font_size_relation_to_body": (
        "font_size_relation_to_body",
        "size_relation_to_body",
        "fontSizeRelationToBody",
    ),
    "font_size_relation_to_table": (
        "font_size_relation_to_table",
        "size_relation_to_table",
        "fontSizeRelationToTable",
    ),
    "alignment": ("alignment", "align", "paragraph_alignment"),
    "italic": ("italic", "is_italic"),
    "bold": ("bold", "is_bold"),
}


def value_from(mapping: Mapping[str, Any] | None, canonical: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    aliases = _ALIASES.get(canonical, (canonical,))
    lowered = {str(key).casefold(): value for key, value in mapping.items()}
    for alias in aliases:
        if alias in mapping:
            return mapping[alias]
        if alias.casefold() in lowered:
            return lowered[alias.casefold()]
    # Inspector versions may keep run formatting under an envelope.  Recurse
    # only through known envelopes; arbitrary nested values are not guessed.
    for key in ("effective", "resolved", "formatting", "raw"):
        nested = mapping.get(key)
        if isinstance(nested, Mapping):
            value = value_from(nested, canonical)
            if value is not None:
                return value
    return None


def paragraph_value(paragraph: Mapping[str, Any], canonical: str) -> Any:
    value = value_from(paragraph, canonical)
    if value is not None:
        return value
    value = value_from(paragraph_formatting(paragraph), canonical)
    if value is not None:
        return value
    for key in ("formatting_evidence", "formatting_observed", "evidence"):
        nested = paragraph.get(key)
        value = value_from(nested, canonical)
        if value is not None:
            return value
    return None


def run_value(run: Mapping[str, Any], canonical: str) -> Any:
    value = value_from(run, canonical)
    if value is not None:
        return value
    value = value_from(run_formatting(run), canonical)
    if value is not None:
        return value
    for key in ("formatting_evidence", "formatting_observed", "evidence"):
        nested = run.get(key)
        value = value_from(nested, canonical)
        if value is not None:
            return value
    return None


def observed_formatting(paragraph: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "east_asia_font",
        "ascii_font",
        "hansi_font",
        "font_size_cn",
        "size_pt",
        "size_cs_pt",
        "font_size_relation_to_body",
        "font_size_relation_to_table",
        "alignment",
        "italic",
        "bold",
    ):
        value = paragraph_value(paragraph, key)
        if value is not None:
            result[key] = value
    return result


def has_formatting_evidence(paragraph: Mapping[str, Any]) -> bool:
    """Return whether effective/direct formatting evidence is actually present."""

    if observed_formatting(paragraph):
        return True
    for run in meaningful_runs(paragraph):
        if any(run_value(run, key) is not None for key in _ALIASES):
            return True
    value = paragraph.get("formatting_evidence")
    if isinstance(value, Mapping):
        return any(item is not None for item in value.values())
    return bool(value is True)


def _normalized_font(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().casefold()


def _normalized_alignment(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip().casefold().replace("_", "-")
    return {
        "wd-align-paragraph-center": "center",
        "center": "center",
        "centre": "center",
        "居中": "center",
        "left": "left",
        "左对齐": "left",
        "right": "right",
        "两端对齐": "justify",
        "justify": "justify",
    }.get(value, value)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _size_matches(actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if isinstance(expected, str):
        actual_text = str(actual).strip()
        if actual_text == expected:
            return True
        expected_pt = CN_SIZE_TO_PT.get(expected)
        actual_pt = _number(actual)
        return expected_pt is not None and actual_pt is not None and abs(expected_pt - actual_pt) < 0.01
    if isinstance(expected, (int, float)):
        actual_pt = _number(actual)
        return actual_pt is not None and abs(float(expected) - actual_pt) < 0.01
    return actual == expected


def _relation_matches(actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        return any(_relation_matches(actual, candidate) for candidate in expected)
    def normalize(value: Any) -> str:
        return str(value).strip().casefold().replace("-", "_").replace(" ", "_")

    return normalize(actual) == normalize(expected)


_DIRECT_FORMAT_REQUIREMENTS = {
    "east_asia_font",
    "ascii_font",
    "hansi_font",
    "font_size_cn",
    "font_size_allowed_cn",
    "alignment",
    "italic",
    "bold",
    "leading_spaces_cn",
    "numbered",
}
_OBSERVABLE_RELATION_REQUIREMENTS = {
    "font_size_relation_to_body",
    "font_size_relation_to_table",
}


def format_mismatches(paragraph: Mapping[str, Any], requirement: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compare requirement fields with effective formatting.

    For mixed runs every non-whitespace run is checked.  A missing effective
    value is a mismatch for a present target: reporting ``ERROR`` is safer than
    claiming a format was observed when it was not.
    """

    observed: dict[str, Any] = observed_formatting(paragraph)
    mismatches: dict[str, Any] = {}
    relevant_keys = [
        key
        for key in (
            "east_asia_font",
            "ascii_font",
            "hansi_font",
            "font_size_cn",
            "font_size_allowed_cn",
            "alignment",
            "italic",
            "bold",
        )
        if key in requirement
    ]
    unchecked: list[str] = []
    for key in requirement:
        key = str(key)
        if key in _DIRECT_FORMAT_REQUIREMENTS or key in _OBSERVABLE_RELATION_REQUIREMENTS:
            continue
        # Formatting rules should fail closed for an unknown capability.  The
        # caller turns this marker into MANUAL_REVIEW/NOT_CHECKED rather than
        # allowing an empty comparison to become PASS.
        unchecked.append(key)
    for key in sorted(_OBSERVABLE_RELATION_REQUIREMENTS.intersection(requirement)):
        actual = paragraph_value(paragraph, key)
        if actual is None:
            run_values = [run_value(run, key) for run in meaningful_runs(paragraph)]
            run_values = [value for value in run_values if value is not None]
            if run_values and all(_relation_matches(value, run_values[0]) for value in run_values):
                actual = run_values[0]
        if actual is None:
            unchecked.append(key)
            continue
        expected = requirement.get(key)
        observed[key] = actual
        if not _relation_matches(actual, expected):
            mismatches[key] = {"observed": actual, "expected": expected}
    for key in relevant_keys:
        expected = requirement.get(key)
        actual = observed.get(key)
        if actual is None and key in {"font_size_cn", "font_size_allowed_cn"}:
            # Inspector keeps the lossless point value even when a Chinese
            # size label cannot be inferred.  Comparing against that value is
            # safe because the mapping is merely an implementation aid; the
            # normative expected value still comes from the rule YAML.
            actual = observed.get("size_pt")
        if key == "east_asia_font":
            matches = _normalized_font(actual) == _normalized_font(expected)
        elif key in {"ascii_font", "hansi_font"}:
            matches = _normalized_font(actual) == _normalized_font(expected)
        elif key == "font_size_allowed_cn":
            allowed = expected if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)) else [expected]
            matches = any(_size_matches(actual, candidate) for candidate in allowed)
        elif key == "font_size_cn":
            matches = _size_matches(actual, expected)
        elif key == "alignment":
            matches = _normalized_alignment(actual) == _normalized_alignment(expected)
        else:
            matches = actual is not None and actual == expected
        if not matches:
            mismatches[key] = {"observed": actual, "expected": expected}

    runs = meaningful_runs(paragraph)
    for run_index, run in enumerate(runs):
        run_observed = {}
        for key in relevant_keys:
            value = run_value(run, key)
            if value is not None:
                run_observed[key] = value
        # A run with no formatting envelope inherits paragraph formatting.  It
        # should not be reported as a second violation merely because the
        # Inspector omitted redundant inherited values.
        if not run_observed:
            continue
        observed.setdefault("runs", []).append({"index": run_index, **run_observed})
        for key in relevant_keys:
            actual = run_observed.get(key, observed.get(key))
            if actual is None and key in {"font_size_cn", "font_size_allowed_cn"}:
                actual = run_observed.get("size_pt", observed.get("size_pt"))
            expected = requirement.get(key)
            if key == "east_asia_font" or key in {"ascii_font", "hansi_font"}:
                matches = _normalized_font(actual) == _normalized_font(expected)
            elif key == "font_size_allowed_cn":
                allowed = expected if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)) else [expected]
                matches = any(_size_matches(actual, candidate) for candidate in allowed)
            elif key == "font_size_cn":
                matches = _size_matches(actual, expected)
            elif key == "alignment":
                matches = _normalized_alignment(actual) == _normalized_alignment(expected)
            else:
                matches = actual is not None and actual == expected
            if not matches:
                mismatches.setdefault("runs", []).append(
                    {"index": run_index, "field": key, "observed": actual, "expected": expected}
                )
    if unchecked:
        observed["unchecked_fields"] = sorted(set(unchecked))
    return observed, mismatches


def check_paragraph_format(
    rule: Mapping[str, Any],
    paragraph: Mapping[str, Any],
    *,
    role: str | None = None,
    check_runs: bool = True,
) -> dict[str, Any] | None:
    requirement = expected_for(rule)
    observed, mismatches = format_mismatches(paragraph, requirement)
    # Leading spaces and automatic numbering are structural fields rather than
    # style properties, but they are still driven by the rule requirement.
    if "leading_spaces_cn" in requirement:
        text = text_of(paragraph)
        actual = len(text) - len(text.lstrip(" \u3000"))
        expected = requirement.get("leading_spaces_cn")
        if actual != expected:
            observed["leading_spaces_cn"] = actual
            mismatches["leading_spaces_cn"] = {"observed": actual, "expected": expected}
    if "numbered" in requirement:
        numbered = bool(paragraph.get("numbering") or paragraph.get("numPr"))
        expected = bool(requirement.get("numbered"))
        observed["numbered"] = numbered
        if numbered != expected:
            mismatches["numbered"] = {"observed": numbered, "expected": expected}
    unchecked = observed.get("unchecked_fields", [])
    if not mismatches and not unchecked:
        return None
    return {"observed": observed, "mismatches": mismatches, "unchecked": unchecked}


def mismatch_message(rule: Mapping[str, Any], mismatches: Mapping[str, Any]) -> str:
    labels = {
        "east_asia_font": "东亚字体",
        "ascii_font": "ASCII 字体",
        "hansi_font": "HAnsi 字体",
        "font_size_cn": "字号",
        "font_size_allowed_cn": "字号",
        "alignment": "对齐方式",
        "italic": "斜体",
        "bold": "粗体",
        "leading_spaces_cn": "段首空格",
        "numbered": "自动编号",
        "runs": "混合 run 格式",
    }
    fields = [labels.get(str(key), str(key)) for key in mismatches]
    return f"{rule.get('target', '目标')}不符合规则要求：" + "、".join(fields)


def all_nonempty(items: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [item for item in items if text_of(item).strip()]


def text_has_latin_or_digit(value: str) -> bool:
    return any(char.isascii() and char.isalnum() for char in value)


def footnote_info(inspection: Mapping[str, Any]) -> Mapping[str, Any]:
    notes = inspection.get("notes", {})
    if not isinstance(notes, Mapping):
        return {}
    value = notes.get("footnotes", notes)
    return value if isinstance(value, Mapping) else {}


def equation_info(inspection: Mapping[str, Any]) -> Mapping[str, Any]:
    value = inspection.get("equations", {})
    return value if isinstance(value, Mapping) else {}


def fields_info(inspection: Mapping[str, Any]) -> Mapping[str, Any]:
    value = inspection.get("fields", {})
    return value if isinstance(value, Mapping) else {}


def is_chinese_text(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def contains_reference_signal(text: str) -> bool:
    import re

    return bool(re.search(r"(?:19|20)\d{2}|doi\s*[:.]|https?://", text, re.IGNORECASE))


__all__ = [
    "CLASSIFICATION_MANUAL_REVIEW_THRESHOLD",
    "CN_SIZE_TO_PT",
    "RuleContext",
    "absent_status",
    "all_nonempty",
    "check_paragraph_format",
    "contains_reference_signal",
    "document_finding",
    "equation_info",
    "expected_for",
    "finding",
    "fields_info",
    "footnote_info",
    "has_formatting_evidence",
    "is_chinese_text",
    "meaningful_runs",
    "mismatch_message",
    "observed_formatting",
    "paragraph_target",
    "paragraph_value",
    "rule_severity",
    "source_for",
    "text_has_latin_or_digit",
    "text_of",
    "value_from",
]
