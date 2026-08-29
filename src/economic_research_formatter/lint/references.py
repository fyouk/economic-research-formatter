"""Structural checks for the reference section."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from .citations import _footnote_items, _footnote_semantics
from .common import (
    RuleContext,
    finding,
    is_chinese_text,
    note_info,
    note_linkage_issues,
    rule_severity,
    text_of,
)


REFERENCE_RULE_IDS = {
    "ER-REF-ORDER-FIELDS-001",
    "ER-REF-SORT-001",
    "ER-REF-SAMEYEAR-001",
    "ER-REF-TRANSLATION-001",
    "ER-REF-FOREIGN-AUTHOR-001",
    "ER-REF-FOREIGN-TITLE-001",
    "ER-REF-FOREIGN-ARTICLE-QUOTE-001",
    "ER-REF-FOREIGN-PUBLICATION-001",
    "ER-REF-FOREIGN-LINE-001",
    "ER-REF-LANGUAGE-GROUP-001",
    "ER-REF-CONTENT-FOOTNOTE-001",
}


def _entries(ctx: RuleContext) -> list[dict[str, Any]]:
    return ctx.classified("reference_entry")


def _is_chinese_entry(paragraph: Mapping[str, Any]) -> bool:
    text = text_of(paragraph)
    explicit = paragraph.get("language") or paragraph.get("language_group")
    if isinstance(explicit, str):
        lower = explicit.casefold()
        if lower in {"foreign", "en", "english", "外文"}:
            return False
        if lower in {"chinese", "zh", "中文"}:
            return True
    return is_chinese_text(text)


def _none(rule: Mapping[str, Any], ctx: RuleContext, message: str) -> list[dict[str, Any]]:
    return [finding(rule, "NOT_APPLICABLE", message, target=ctx.document_target)]


def _language_group(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    entries = _entries(ctx)
    if not entries:
        return _none(rule, ctx, "未识别到参考文献条目。")
    requirement = rule.get("requirement", {})
    requirement = requirement if isinstance(requirement, Mapping) else {}
    expected_order = requirement.get("group_order", [])
    expected_order = list(expected_order) if isinstance(expected_order, Sequence) and not isinstance(expected_order, (str, bytes)) else []
    # Current source vocabulary has an explicit Chinese-before-foreign order.
    # We use the rule value to derive ranks and do not encode that order in the
    # checker itself.
    rank_by_group = {str(group): index for index, group in enumerate(expected_order)}
    chinese_key = next((key for key in rank_by_group if "chinese" in key or "中文" in key), "chinese_including_translations")
    foreign_key = next((key for key in rank_by_group if "foreign" in key or "外文" in key), "foreign")
    observed_groups = [chinese_key if _is_chinese_entry(entry) else foreign_key for entry in entries]
    ranks = [rank_by_group.get(group, index) for index, group in enumerate(observed_groups)]
    violation_index = next((index for index in range(1, len(ranks)) if ranks[index] < ranks[index - 1]), None)
    observed = {"groups": observed_groups, "entry_count": len(entries)}
    if violation_index is not None:
        return [
            finding(
                rule,
                rule_severity(rule),
                "中文参考文献应整体排在外文参考文献之前。",
                paragraph=entries[violation_index],
                observed=observed,
                confidence=0.94,
            )
        ]
    sort_required = bool(requirement.get("sort_each_group_separately"))
    sort_state = _within_group_sort_state(ctx, entries, observed_groups) if sort_required else True
    observed["within_group_sort_verified"] = sort_state is True
    if sort_state is False:
        return [
            finding(
                rule,
                rule_severity(rule),
                "参考文献语言分组内未按要求排序。",
                target=ctx.document_target,
                observed=observed,
                confidence=0.90,
            )
        ]
    if sort_state is None:
        return [
            finding(
                rule,
                "MANUAL_REVIEW",
                "语言分组顺序可观察，但分组内排序缺少可验证证据。",
                target=ctx.document_target,
                observed=observed,
                confidence=0.70,
            )
        ]
    return [finding(rule, "PASS", "参考文献语言分组顺序符合规则。", target=ctx.document_target, observed=observed)]


def _within_group_sort_state(
    ctx: RuleContext,
    entries: Sequence[Mapping[str, Any]],
    groups: Sequence[str],
) -> bool | None:
    """Return explicit within-group sort evidence; unknown stays unknown."""

    values: list[Any] = []
    for key in ("within_group_sort_verified", "sort_verified", "groups_sorted"):
        value = ctx.inspection.get(key)
        if isinstance(value, bool):
            values.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            values.extend(value)
    sort_info = ctx.inspection.get("reference_sort")
    if isinstance(sort_info, Mapping):
        for key in ("within_group_sort_verified", "sort_verified", "groups_sorted"):
            value = sort_info.get(key)
            if isinstance(value, bool):
                values.append(value)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                values.extend(value)
    for key in ("group_sorting", "language_group_sort", "sort_evidence"):
        sort_info = ctx.inspection.get(key)
        if isinstance(sort_info, bool):
            values.append(sort_info)
        elif isinstance(sort_info, Mapping):
            group_values = list(sort_info.values())
            if group_values and all(isinstance(value, bool) for value in group_values):
                values.extend(group_values)
    for entry in entries:
        for key in ("within_group_sorted", "sort_verified", "group_sorted"):
            value = entry.get(key)
            if isinstance(value, bool):
                values.append(value)
    if not values:
        return None
    bool_values = [value for value in values if isinstance(value, bool)]
    if len(bool_values) != len(values):
        return None
    return False if any(value is False for value in bool_values) else True


def _foreign_entries(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    entries = [entry for entry in _entries(ctx) if not _is_chinese_entry(entry)]
    if not entries:
        return []
    return entries


def _expected_separator_values(value: Any) -> set[str]:
    text = str(value or "").strip().casefold()
    return {
        "comma": {",", "，"},
        "逗号": {",", "，"},
        "semicolon": {";", "；"},
        "分号": {";", "；"},
    }.get(text, {str(value)} if value is not None else set())


def _explicit_separator_evidence(entry: Mapping[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key in ("observed_field_separator", "field_delimiter", "delimiter", "separator", "field_separator"):
        value = entry.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            values.extend(value)
        elif value is not None:
            values.append(value)
    for key in ("field_boundaries", "parsed_fields", "fields"):
        value = entry.get(key)
        if isinstance(value, Mapping):
            value = value.get("separators", value.get("delimiters", value.get("field_separators", [])))
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for field in value:
                if isinstance(field, Mapping):
                    for separator_key in ("separator", "delimiter", "separator_after"):
                        if field.get(separator_key) is not None:
                            values.append(field[separator_key])
                            break
                elif isinstance(field, str) and field in {",", "，", ";", "；"}:
                    values.append(field)
    return values


def _unquoted_semicolon_positions(text: str) -> list[int]:
    """Return semicolons outside paired title quotation marks."""

    positions: list[int] = []
    opening_quotes = {"\"", "'", "“", "‘", "「", "『"}
    closing_quotes = {"\"", "'", "”", "’", "」", "』"}
    quote_stack: list[str] = []
    for index, character in enumerate(text):
        if character in opening_quotes:
            if character in {"\"", "'"} and quote_stack and quote_stack[-1] == character:
                quote_stack.pop()
            elif character in {"\"", "'"} and quote_stack:
                continue
            else:
                quote_stack.append(character)
        elif character in closing_quotes and quote_stack:
            if character in {"”", "’", "」", "』"} or quote_stack[-1] == character:
                quote_stack.pop()
        elif character in {";", "；"} and not quote_stack:
            positions.append(index)
    return positions


def _text_scan_proves_field_semicolons(text: str) -> bool:
    """Recognise only the legacy high-confidence multi-field semicolon shape.

    A single semicolon is intentionally never enough: without parsed field
    boundaries it may belong to an article title.  Two or more unquoted
    semicolons bracketing the publication year are the narrower legacy shape
    retained for backwards-compatible diagnostics.
    """

    positions = _unquoted_semicolon_positions(text)
    if len(positions) < 2:
        return False
    year = re.search(r"(?:19|20)\d{2}", text)
    if year is None:
        return False
    return positions[0] < year.start() < positions[1]


def _foreign_separator_parse(entry: Mapping[str, Any], expected: Any) -> tuple[str, dict[str, Any]]:
    expected_values = _expected_separator_values(expected)
    explicit = _explicit_separator_evidence(entry)
    text = text_of(entry)
    text_separators = re.findall(r"[,，;；]", text)
    semantic_values = {"comma": ",", "逗号": ",", "semicolon": ";", "分号": ";"}
    actual_values = [str(value) for value in explicit] if explicit else text_separators
    actual_values = [
        semantic_values[value.strip().casefold()]
        if value.strip().casefold() in semantic_values
        else value
        for value in actual_values
    ]
    observed: dict[str, Any] = {
        "expected_separator": expected,
        "actual_separator": actual_values[0] if actual_values else None,
        "separator_evidence": "inspector_boundaries" if explicit else "text_scan",
    }
    if explicit:
        if any(value in {";", "；"} for value in actual_values):
            return "wrong", observed
        if actual_values and all(value in expected_values for value in actual_values):
            return "ok", observed
        return "wrong", observed
    # A comma in prose is not enough to prove field boundaries (author names,
    # initials, and titles can contain commas).  Without Inspector boundary
    # evidence the safe result is manual review.
    if any(value in {";", "；"} for value in actual_values) and _text_scan_proves_field_semicolons(text):
        return "wrong", observed
    if text_separators:
        return "uncertain", observed
    return "uncertain", observed


def _foreign_line(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    entries = _foreign_entries(rule, ctx)
    if not entries:
        return _none(rule, ctx, "未识别到外文参考文献条目。")
    requirement = rule.get("requirement", {})
    requirement = requirement if isinstance(requirement, Mapping) else {}
    expected_separator = requirement.get("field_separator")
    results: list[dict[str, Any]] = []
    for entry in entries:
        text = text_of(entry)
        line_count = entry.get("line_count")
        try:
            split = int(line_count) > 1
        except (TypeError, ValueError):
            split = False
        split = split or "\n" in text or bool(entry.get("split_lines"))
        if split:
            results.append(finding(rule, rule_severity(rule), "外文参考文献条目未在同一行连续排列。", paragraph=entry, observed={"line_count": line_count, "text_has_newline": "\n" in text}))
        else:
            state, observed = _foreign_separator_parse(entry, expected_separator)
            if state == "wrong":
                results.append(finding(rule, rule_severity(rule), "外文参考文献字段分隔符不符合规则。", paragraph=entry, observed=observed))
            elif state == "ok":
                results.append(finding(rule, "PASS", "外文参考文献字段分隔符已由 Inspector 证据确认。", paragraph=entry, observed=observed))
            else:
                results.append(finding(rule, "MANUAL_REVIEW", "外文参考文献字段边界无法从当前证据可靠解析。", paragraph=entry, observed=observed, confidence=0.65))
    return results


def _foreign_manual(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    entries = _foreign_entries(rule, ctx)
    if not entries:
        return _none(rule, ctx, "未识别到外文参考文献条目。")
    return [finding(rule, "MANUAL_REVIEW", "外文参考文献字段解析仍需人工确认，当前不自动裁决。", paragraph=entries[0], observed={"foreign_entry_count": len(entries)}, confidence=0.65)]


def _content_footnote(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    count = 0
    for kind in ("footnote", "endnote"):
        info = note_info(ctx.inspection, kind)
        value = info.get("actual_count", info.get("count", 0))
        try:
            count += int(value or 0)
        except (TypeError, ValueError):
            continue
    linkage_issues = note_linkage_issues(ctx.inspection)
    if count <= 0 and linkage_issues:
        return [
            finding(
                rule,
                "MANUAL_REVIEW",
                "正文注释引用与脚注或尾注定义无法一一对应。",
                target=ctx.document_target,
                observed={"note_linkage_issues": linkage_issues},
                confidence=0.40,
            )
        ]
    if count <= 0:
        return _none(rule, ctx, "文档没有实际脚注或尾注。")
    notes = _footnote_items(ctx)
    if not notes:
        observed = {"actual_footnote_count": count, "actual_note_count": count}
        if linkage_issues:
            observed["note_linkage_issues"] = linkage_issues
        return [finding(rule, "MANUAL_REVIEW", "文档存在实际脚注或尾注，但 Inspector 未提供可判断语义的注释段落证据。", target=ctx.document_target, observed=observed, confidence=0.40)]
    results: list[dict[str, Any]] = []
    if linkage_issues:
        results.append(
            finding(
                rule,
                "MANUAL_REVIEW",
                "脚注或尾注定义与正文引用无法一一对应；仅对已绑定注释继续检查。",
                target=ctx.document_target,
                observed={"note_linkage_issues": linkage_issues},
                confidence=0.40,
            )
        )
    for note in notes:
        semantics, candidates = _footnote_semantics(note)
        kind = str(note.get("kind", "footnote")).casefold()
        if kind not in {"footnote", "endnote"}:
            kind = "footnote"
        observed = {
            "note_kind": kind,
            f"{kind}_semantics": semantics,
            "candidates": [candidate.to_dict() for candidate in candidates],
            f"{kind}_paragraph_ids": [paragraph.get("id") for paragraph in note["paragraphs"]],
        }
        if kind == "footnote":
            observed["footnote_semantics"] = semantics
        if semantics == "content_note_with_inline_citation":
            results.append(
                finding(
                    rule,
                    "PASS",
                    "内容注释中的文献已采用文内作者-年份引文形式。",
                    paragraph=note["target"],
                    target=note["target"],
                    observed=observed,
                    confidence=0.92,
                )
            )
        elif semantics == "ambiguous_literature":
            results.append(
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "内容注释中的疑似文献引文缺少足够的结构化证据。",
                    paragraph=note["target"],
                    target=note["target"],
                    observed=observed,
                    confidence=0.55,
                )
            )
        elif semantics == "insufficient_evidence":
            results.append(
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "内容注释仅提供截断预览，无法排除预览范围外的文献引文。",
                    paragraph=note["target"],
                    target=note["target"],
                    observed={
                        **observed,
                        "preview_only": True,
                        "text_truncated": True,
                    },
                    confidence=0.40,
                )
            )
    return results or _none(rule, ctx, "未识别到内容注释中的文献引文。")


def _unsupported(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    entries = _entries(ctx)
    if not entries:
        return _none(rule, ctx, "未识别到参考文献条目。")
    return [finding(rule, "NOT_CHECKED", "该参考文献语义规则尚未实现自动裁决。", paragraph=entries[0], observed={"entry_count": len(entries)}, confidence=0.50)]


def lint_reference_rule(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    handlers = {
        "ER-REF-LANGUAGE-GROUP-001": _language_group,
        "ER-REF-FOREIGN-LINE-001": _foreign_line,
        "ER-REF-FOREIGN-PUBLICATION-001": _foreign_manual,
        "ER-REF-FOREIGN-ARTICLE-QUOTE-001": _foreign_manual,
        "ER-REF-FOREIGN-TITLE-001": _foreign_manual,
        "ER-REF-FOREIGN-AUTHOR-001": _foreign_manual,
        "ER-REF-ORDER-FIELDS-001": _unsupported,
        "ER-REF-SORT-001": _unsupported,
        "ER-REF-SAMEYEAR-001": _unsupported,
        "ER-REF-TRANSLATION-001": _unsupported,
        "ER-REF-CONTENT-FOOTNOTE-001": _content_footnote,
    }
    handler = handlers.get(str(rule.get("id", "")))
    if handler is None:
        return []
    return handler(rule, ctx)


__all__ = ["REFERENCE_RULE_IDS", "lint_reference_rule"]
