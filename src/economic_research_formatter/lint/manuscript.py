"""Manuscript-focused rule checks.

Every normative value is read from the current rule's ``requirement`` mapping.
The functions here only interpret Inspector evidence and never mutate a DOCX.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


from .common import (
    RuleContext,
    absent_status,
    check_paragraph_format,
    equation_info,
    expected_for,
    finding,
    footnote_info,
    has_formatting_evidence,
    meaningful_runs,
    mismatch_message,
    observed_formatting,
    paragraph_value,
    paragraph_target,
    rule_severity,
    text_has_latin_or_digit,
    text_of,
    value_from,
    run_value,
)


MS_RULE_IDS = {
    "ER-MS-TITLE-001",
    "ER-MS-TITLE-002",
    "ER-MS-AUTHOR-001",
    "ER-MS-AUTHORINFO-001",
    "ER-MS-AUTHORINFO-002",
    "ER-MS-ABSTRACT-001",
    "ER-MS-HEADING1-001",
    "ER-MS-HEADING-HIERARCHY-001",
    "ER-MS-EQUATION-001",
    "ER-MS-EQUATION-002",
    "ER-MS-TABLE-001",
    "ER-MS-TABLE-NOTE-001",
    "ER-MS-TABLE-NOTE-002",
    "ER-MS-FOOTNOTE-001",
    "ER-MS-FOOTNOTE-002",
    "ER-MS-FIGURE-001",
    "ER-MS-FIGURE-002",
    "ER-MS-REF-LAYOUT-001",
    "ER-MS-LATIN-FONT-001",
    "ER-MS-REF-PAGERANGE-001",
    "ER-MS-REF-TITLECASE-001",
    "ER-MS-REF-AUTHOR-001",
    "ER-MS-REF-JOURNAL-001",
}


def _document_or_first(rule: Mapping[str, Any], ctx: RuleContext, paragraphs: Sequence[Mapping[str, Any]], message: str) -> dict[str, Any]:
    if paragraphs:
        paragraph = paragraphs[0]
        return finding(
            rule,
            absent_status(rule),
            message,
            target=paragraph_target(paragraph, ctx.role(paragraph)),
            observed={},
        )
    return finding(rule, "NOT_APPLICABLE", message, target=ctx.document_target)


def _format_targets(
    rule: Mapping[str, Any],
    ctx: RuleContext,
    targets: Sequence[Mapping[str, Any]],
    *,
    missing_message: str,
) -> list[dict[str, Any]]:
    if not targets:
        return [finding(rule, "NOT_APPLICABLE", missing_message, target=ctx.document_target)]
    results: list[dict[str, Any]] = []
    for paragraph in targets:
        role = ctx.role(paragraph)
        is_note = str(paragraph.get("kind", "")).casefold() in {"footnote", "ordinary_footnote", "endnote"}
        if is_note and not has_formatting_evidence(paragraph):
            results.append(
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "已检测到脚注，但 Inspector 尚未提供脚注格式证据。",
                    paragraph=paragraph,
                    target=paragraph_target(paragraph, role),
                    observed={"formatting_evidence": False},
                    confidence=float(paragraph.get("confidence", 0.78) or 0.78),
                )
            )
            continue
        checked = check_paragraph_format(rule, paragraph, role=role)
        if checked:
            if is_note:
                missing_size_or_font = []
                requirement = expected_for(rule)
                for key in ("east_asia_font", "ascii_font", "hansi_font", "font_size_cn", "font_size_allowed_cn"):
                    if key not in requirement:
                        continue
                    value = paragraph_value(paragraph, key)
                    if value is None and key in {"font_size_cn", "font_size_allowed_cn"}:
                        value = paragraph_value(paragraph, "size_pt")
                    if value is None:
                        missing_size_or_font.append(key)
                if missing_size_or_font:
                    observed = dict(checked["observed"])
                    observed["missing_formatting_evidence"] = missing_size_or_font
                    results.append(
                        finding(
                            rule,
                            "MANUAL_REVIEW",
                            "脚注格式缺少字体或字号证据，当前需人工复核。",
                            paragraph=paragraph,
                            target=paragraph_target(paragraph, role),
                            observed=observed,
                            confidence=float(paragraph.get("confidence", 0.80) or 0.80),
                        )
                    )
                    continue
            if checked.get("unchecked"):
                results.append(
                    finding(
                        rule,
                        "MANUAL_REVIEW",
                        "该格式字段缺少可验证的 Inspector 证据，当前需人工复核。",
                        paragraph=paragraph,
                        target=paragraph_target(paragraph, role),
                        observed=checked["observed"],
                    )
                )
            else:
                results.append(
                    finding(
                        rule,
                        rule_severity(rule),
                        mismatch_message(rule, checked["mismatches"]),
                        paragraph=paragraph,
                        target=paragraph_target(paragraph, role),
                        observed=checked["observed"],
                    )
                )
        else:
            results.append(
                finding(
                    rule,
                    "PASS",
                    f"{rule.get('target', '目标')}已满足规则要求。",
                    paragraph=paragraph,
                    target=paragraph_target(paragraph, role),
                    observed=observed_formatting(paragraph) or {"formatting": True},
                )
            )
    return results


def _title_marker(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    titles = ctx.classified("title")
    if not titles:
        return [finding(rule, "NOT_APPLICABLE", "未识别到题名，无法检查题名作者信息标记。", target=ctx.document_target)]
    requirement = expected_for(rule)
    marker = str(requirement.get("marker", ""))
    mechanism = str(requirement.get("mechanism", ""))
    found = False
    observed: dict[str, Any] = {"marker": marker, "mechanism": mechanism}
    for paragraph in titles:
        text = text_of(paragraph)
        references = _title_footnote_reference_evidence(ctx, paragraph)
        marker_in_text = marker and marker in text
        has_reference = bool(references) or bool(paragraph.get("has_footnote_reference"))
        if has_reference and (not mechanism or mechanism.casefold() == "footnote"):
            # A reliable OOXML reference establishes the mechanism even when
            # the marker glyph itself is hidden in a field/run representation.
            found = True
        elif marker_in_text and (not mechanism or paragraph.get("footnote_reference") is not None):
            found = True
        if references:
            observed.setdefault("footnote_reference_evidence", []).extend(references)
        observed.setdefault("title_ids", []).append(paragraph.get("id"))
    if found:
        return [finding(rule, "PASS", "题名作者信息标记已识别。", paragraph=titles[0], observed=observed)]
    # This rule checks a required marker; its absence is a reportable error, but
    # remains read-only and never inserts a footnote.
    return [finding(rule, rule_severity(rule), "题名未检测到要求的作者信息脚注标记。", paragraph=titles[0], observed=observed)]


def _title_footnote_reference_evidence(ctx: RuleContext, paragraph: Mapping[str, Any]) -> list[Any]:
    """Collect only reference evidence explicitly bound to this title."""

    paragraph_id = str(paragraph.get("id", ""))
    values: list[Any] = []

    for key in ("footnote_references", "footnote_refs", "footnote_reference_evidence"):
        local = paragraph.get(key)
        if isinstance(local, Mapping):
            values.append(dict(local))
        elif isinstance(local, Sequence) and not isinstance(local, (str, bytes)):
            values.extend(item for item in local if item)
        elif local:
            values.append(local)
    note_references = paragraph.get("note_references")
    if isinstance(note_references, Sequence) and not isinstance(note_references, (str, bytes)):
        values.extend(
            item
            for item in note_references
            if isinstance(item, Mapping) and str(item.get("kind", "footnote")).casefold() == "footnote"
        )
    if paragraph.get("footnote_reference") is not None:
        values.append(paragraph.get("footnote_reference"))

    top_level = ctx.inspection.get("footnote_references", [])
    if isinstance(top_level, Mapping):
        bound = top_level.get(paragraph_id)
        if isinstance(bound, Sequence) and not isinstance(bound, (str, bytes)):
            values.extend(item for item in bound if item)
        elif bound:
            values.append(bound)
    elif isinstance(top_level, Sequence) and not isinstance(top_level, (str, bytes)):
        for item in top_level:
            if not isinstance(item, Mapping):
                continue
            bound_id = item.get("paragraph_id") or item.get("title_id") or item.get("target_id")
            if bound_id is not None and str(bound_id) == paragraph_id:
                values.append(dict(item))

    # Current Inspector nests raw OOXML reference IDs in notes.  They are only
    # useful here when a future shape also records their owning paragraph.
    info = footnote_info(ctx.inspection)
    nested = info.get("footnote_references", info.get("references_by_paragraph", {}))
    if isinstance(nested, Mapping):
        bound = nested.get(paragraph_id)
        if isinstance(bound, Sequence) and not isinstance(bound, (str, bytes)):
            values.extend(item for item in bound if item)
        elif bound:
            values.append(bound)
    elif isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
        for item in nested:
            if not isinstance(item, Mapping):
                continue
            bound_id = item.get("paragraph_id") or item.get("title_id") or item.get("target_id")
            if bound_id is not None and str(bound_id) == paragraph_id:
                values.append(dict(item))
    deduplicated: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduplicated.append(value)
    return deduplicated


def _authorinfo_term(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = expected_for(rule)
    preferred = str(requirement.get("preferred_term", ""))
    disallowed = str(requirement.get("disallowed_term", ""))
    targets = ctx.classified("author_information")
    matches = [paragraph for paragraph in targets if disallowed and disallowed in text_of(paragraph)]
    if matches:
        return [
            finding(
                rule,
                rule_severity(rule),
                f"作者信息中使用了不推荐术语“{disallowed}”，应使用“{preferred}”。",
                paragraph=paragraph,
                observed={"term": disallowed},
            )
            for paragraph in matches
        ]
    if targets:
        return [finding(rule, "PASS", f"作者信息术语符合“{preferred}”要求。", paragraph=targets[0], observed={"term": preferred})]
    # A missing contact block is not evidence of a wrong term.
    return [finding(rule, "NOT_APPLICABLE", "未识别到作者联系信息段。", target=ctx.document_target)]


def _hierarchy(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    targets = ctx.classified("heading_level_2", "heading_level_3", "heading_level_4")
    if not targets:
        return [finding(rule, "NOT_APPLICABLE", "未识别到二级及以下标题，无法检查标题层级。", target=ctx.document_target)]
    requirement = expected_for(rule)
    level_2_req = requirement.get("level_2", {})
    level_2_req = level_2_req if isinstance(level_2_req, Mapping) else {}
    lower_req = requirement.get("lower_levels", {})
    lower_req = lower_req if isinstance(lower_req, Mapping) else {}
    expected_prefix = str(level_2_req.get("prefix_pattern", ""))
    lower_sequence = lower_req.get("sequence", [])
    if not isinstance(lower_sequence, Sequence) or isinstance(lower_sequence, (str, bytes)):
        lower_sequence = []

    bad: list[dict[str, Any]] = []
    observed_levels: list[dict[str, Any]] = []
    for paragraph in targets:
        role = ctx.role(paragraph)
        level = int(role.rsplit("_", 1)[-1]) if role.rsplit("_", 1)[-1].isdigit() else None
        text = text_of(paragraph).lstrip(" \u3000")
        observed_levels.append({"id": paragraph.get("id"), "level": level, "text_preview": text[:80]})
        valid = True
        if level == 2 and expected_prefix:
            # Requirement values are literal examples in the current YAML.  If
            # a future rule supplies a real regexp, support that too.
            try:
                valid = bool(re.match(expected_prefix, text))
            except re.error:
                valid = text.startswith(expected_prefix)
            if not valid:
                valid = text.startswith(expected_prefix)
        elif level in {3, 4} and lower_sequence:
            valid = any(text.startswith(str(prefix)) for prefix in lower_sequence)
        if not valid:
            bad.append({"id": paragraph.get("id"), "level": level, "text_preview": text[:80]})
    if bad:
        first_bad = next((paragraph for paragraph in targets if paragraph.get("id") == bad[0].get("id")), targets[0])
        return [
            finding(
                rule,
                rule_severity(rule),
                "标题编号层级不符合来源规则；检测到论文式层级编号。",
                paragraph=first_bad,
                target=paragraph_target(first_bad, ctx.role(first_bad)),
                observed={"headings": observed_levels, "violations": bad, "style": "thesis-style numbering"},
            )
            for item in bad[:1]
        ]
    return [finding(rule, "PASS", "标题层级编号符合规则。", target=ctx.document_target, observed={"headings": observed_levels})]


def _equation(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    info = equation_info(ctx.inspection)
    count = info.get("omath_count", info.get("count", 0))
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    if count <= 0 and not ctx.classified("equation"):
        return [finding(rule, "NOT_APPLICABLE", "文档中未检测到 Word OMML 公式。", target=ctx.document_target)]
    ids = info.get("paragraph_ids", [])
    ids = ids if isinstance(ids, Sequence) and not isinstance(ids, (str, bytes)) else []
    targets = [p for p in ctx.paragraph_list if p.get("id") in ids]
    if targets:
        return [
            finding(
                rule,
                "MANUAL_REVIEW",
                "已检测到 Word OMML 公式；允许的公式编辑器仍需人工确认。",
                paragraph=targets[0],
                observed={"omath_count": count, "editor_evidence": "Word Equation"},
                confidence=0.99,
            )
        ]
    return [
        finding(
            rule,
            "MANUAL_REVIEW",
            "已检测到 Word OMML 公式；允许的公式编辑器仍需人工确认。",
            target=ctx.document_target,
            observed={"omath_count": count, "editor_evidence": "Word Equation"},
            confidence=0.99,
        )
    ]


def _equation_where(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = expected_for(rule)
    trigger = str(requirement.get("trigger_text", "其中"))
    # The classifier has already established both the trigger boundary and
    # the immediate preceding real equation.  Re-scanning every paragraph for
    # ``其中`` would turn ordinary prose into an equation target.
    targets = [
        paragraph
        for paragraph in ctx.classified("equation_where_paragraph")
        if text_of(paragraph).lstrip(" \u3000").startswith(trigger)
    ]
    if not targets:
        return [finding(rule, "NOT_APPLICABLE", f"未检测到触发文本“{trigger}”。", target=ctx.document_target)]
    expected_spaces = requirement.get("leading_spaces_cn")
    results: list[dict[str, Any]] = []
    for paragraph in targets:
        text = text_of(paragraph)
        actual_spaces = len(text) - len(text.lstrip(" \u3000"))
        observed = {"leading_spaces_cn": actual_spaces, "trigger_text": trigger}
        if expected_spaces is not None and actual_spaces != expected_spaces:
            results.append(finding(rule, rule_severity(rule), "公式说明段首空格不符合要求。", paragraph=paragraph, observed=observed))
        else:
            results.append(finding(rule, "PASS", "公式说明段格式符合要求。", paragraph=paragraph, observed=observed))
    return results


def _normalize_restart(value: Any) -> str | None:
    if value is None:
        return None
    compact = str(value).strip().casefold().replace("_", "").replace("-", "").replace(" ", "")
    return {
        "eachpage": "each_page",
        "page": "each_page",
        "restartpage": "each_page",
        "eachsection": "each_section",
        "section": "each_section",
        "continuous": "continuous",
    }.get(compact)


def _restart_values(info: Mapping[str, Any], inspection: Mapping[str, Any]) -> list[Any]:
    """Collect effective restart values from the note report and sections."""

    values: list[Any] = []
    restart_keys = {
        "restart",
        "numrestart",
        "numrestarts",
        "num_restart",
        "footnoterestart",
        "footnotenumrestart",
        "effectiverestart",
    }

    def collect(mapping: Mapping[str, Any]) -> None:
        for key, value in mapping.items():
            compact_key = str(key).casefold().replace("_", "")
            if compact_key not in restart_keys:
                continue
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                values.extend(item for item in value if item is not None)
            elif value is not None:
                values.append(value)

    collect(info)
    sections = inspection.get("sections", []) if isinstance(inspection, Mapping) else []
    if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes)):
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            collect(section)
            for nested_key in (
                "footnotes",
                "footnote",
                "footnote_settings",
                "footnote_properties",
                "footnote_pr",
                "footnotePr",
                "notes",
                "note_properties",
            ):
                nested = section.get(nested_key)
                if isinstance(nested, Mapping):
                    collect(nested)
                    # Some Inspector versions retain one more OOXML-style
                    # envelope around the effective note properties.
                    for envelope in ("effective", "resolved", "properties", "formatting"):
                        child = nested.get(envelope)
                        if isinstance(child, Mapping):
                            collect(child)
    return values


def _footnote_numbering(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    info = footnote_info(ctx.inspection)
    count = info.get("actual_count", info.get("count", 0))
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    if count == 0:
        return [finding(rule, "NOT_APPLICABLE", "文档没有实际脚注；分隔符不计入脚注。", target=ctx.document_target)]
    requirement = expected_for(rule)
    expected_restart = requirement.get("restart")
    raw_restarts = _restart_values(info, ctx.inspection)
    normalized = [_normalize_restart(value) for value in raw_restarts]
    observed = {
        "restarts": raw_restarts,
        "restarts_normalized": normalized,
        "actual_count": count,
    }
    expected = _normalize_restart(expected_restart)
    if not raw_restarts or expected is None:
        return [finding(rule, "MANUAL_REVIEW", "检测到实际脚注，但缺少可判定的编号重启证据。", target=ctx.document_target, observed=observed)]
    if any(value is None for value in normalized):
        return [finding(rule, "MANUAL_REVIEW", "脚注编号重启证据包含无法解释的取值，需人工确认。", target=ctx.document_target, observed=observed)]
    if len(set(normalized)) > 1:
        return [finding(rule, rule_severity(rule), "不同节的脚注编号重启方式不一致。", target=ctx.document_target, observed=observed)]
    if normalized[0] != expected:
        return [finding(rule, rule_severity(rule), "脚注编号重启方式不符合要求。", target=ctx.document_target, observed=observed)]
    return [finding(rule, "PASS", "脚注编号重启方式符合要求。", target=ctx.document_target, observed=observed)]


def _footnote_text(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    info = footnote_info(ctx.inspection)
    count = info.get("actual_count", info.get("count", 0))
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    if count == 0:
        return [finding(rule, "NOT_APPLICABLE", "文档没有实际脚注；分隔符不计入脚注。", target=ctx.document_target)]
    targets = [
        paragraph
        for paragraph in ctx.paragraph_list
        if str(paragraph.get("kind", "")).casefold() in {"footnote", "ordinary_footnote"}
    ]
    if not targets:
        values = info.get("paragraphs", info.get("items", []))
        targets = [dict(p) for p in values] if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) else []
        for index, target in enumerate(targets):
            target.setdefault("id", f"footnote-{target.get('id', index)}")
            target.setdefault("kind", "footnote")
    if not targets:
        return [finding(rule, "MANUAL_REVIEW", "检测到实际脚注，但脚注正文格式未在 Inspector 输出中提供。", target=ctx.document_target, observed={"actual_count": count})]
    return _format_targets(rule, ctx, targets, missing_message="未提供脚注正文。")


def _figure_color(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    images = ctx.inspection.get("images", [])
    images = [dict(value) for value in images] if isinstance(images, Sequence) and not isinstance(images, (str, bytes)) else []
    if not images:
        return [finding(rule, "NOT_APPLICABLE", "文档中没有图像对象。", target=ctx.document_target)]
    results: list[dict[str, Any]] = []
    requirement = expected_for(rule)
    expected_mode = requirement.get("color_mode")
    for index, image in enumerate(images):
        analysis = image.get("color_analysis", {})
        analysis = analysis if isinstance(analysis, Mapping) else {}
        gray = analysis.get("is_probably_grayscale")
        target = {
            "kind": "image",
            "id": image.get("id", f"image-{index:04d}"),
            "index": image.get("index", index),
        }
        colored_ratio = analysis.get("colored_nonwhite_pixel_ratio")
        if gray is False or (isinstance(colored_ratio, (int, float)) and colored_ratio > 0):
            results.append(
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "图像含有颜色证据，是否符合黑白图要求需人工确认。",
                    target=target,
                    observed={"is_probably_grayscale": gray, "colored_nonwhite_pixel_ratio": colored_ratio},
                    expected={"color_mode": expected_mode},
                    confidence=0.95,
                )
            )
        elif gray is True:
            results.append(finding(rule, "PASS", "图像颜色分析为大概率黑白。", target=target, observed={"is_probably_grayscale": gray}, expected={"color_mode": expected_mode}, confidence=0.95))
        else:
            results.append(finding(rule, "MANUAL_REVIEW", "缺少足够的图像颜色证据，需人工确认。", target=target, observed={"is_probably_grayscale": gray}, expected={"color_mode": expected_mode}, confidence=0.50))
    return results


def _reference_entries(ctx: RuleContext) -> list[dict[str, Any]]:
    return ctx.classified("reference_entry")


def _reference_layout(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    targets = _reference_entries(ctx)
    return _format_targets(rule, ctx, targets, missing_message="未识别到参考文献条目。")


def _latin_font(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = expected_for(rule)
    targets: list[tuple[Mapping[str, Any], float]] = []
    for paragraph in ctx.paragraph_list:
        text = text_of(paragraph)
        if not text_has_latin_or_digit(text):
            continue
        runs = meaningful_runs(paragraph)
        if runs:
            for run in runs:
                if text_has_latin_or_digit(text_of(run)):
                    targets.append((paragraph, 0.99))
                    break
        else:
            targets.append((paragraph, 0.80))
    if not targets:
        return [finding(rule, "NOT_APPLICABLE", "文档中没有可检查的英文字母或数字。", target=ctx.document_target)]
    results: list[dict[str, Any]] = []
    for paragraph, confidence in targets:
        mismatches: dict[str, Any] = {}
        observed: dict[str, Any] = {}
        runs = meaningful_runs(paragraph)
        for run in runs or [paragraph]:
            run_text = text_of(run)
            if not text_has_latin_or_digit(run_text or text_of(paragraph)):
                continue
            actual_ascii = run_value(run, "ascii_font") if run is not paragraph else value_from(paragraph, "ascii_font")
            actual_hansi = run_value(run, "hansi_font") if run is not paragraph else value_from(paragraph, "hansi_font")
            observed.update({"ascii_font": actual_ascii, "hansi_font": actual_hansi})
            if actual_ascii != requirement.get("ascii_font"):
                mismatches["ascii_font"] = {"observed": actual_ascii, "expected": requirement.get("ascii_font")}
            if actual_hansi != requirement.get("hansi_font"):
                mismatches["hansi_font"] = {"observed": actual_hansi, "expected": requirement.get("hansi_font")}
        if mismatches:
            results.append(finding(rule, rule_severity(rule), "英文字母或数字字体不符合规则要求。", paragraph=paragraph, observed=observed, confidence=confidence))
        else:
            results.append(finding(rule, "PASS", "英文字母和数字字体符合规则。", paragraph=paragraph, observed=observed, confidence=confidence))
    return results


def _page_range(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = expected_for(rule)
    separator = str(requirement.get("separator", ""))
    entries = _reference_entries(ctx)
    if not entries:
        return [finding(rule, "NOT_APPLICABLE", "未识别到参考文献条目。", target=ctx.document_target)]
    pattern = re.compile(r"\b\d+\s*([\-‐‑‒–—―])\s*\d+\b")
    results: list[dict[str, Any]] = []
    for paragraph in entries:
        text = text_of(paragraph)
        match = pattern.search(text)
        if not match:
            continue
        observed_separator = match.group(1)
        observed = {"separator": observed_separator}
        if observed_separator != separator:
            results.append(finding(rule, rule_severity(rule), "参考文献页码范围分隔符不符合规则。", paragraph=paragraph, observed=observed))
        else:
            results.append(finding(rule, "PASS", "参考文献页码范围分隔符符合规则。", paragraph=paragraph, observed=observed))
    return results or [finding(rule, "NOT_APPLICABLE", "参考文献中未检测到页码范围。", target=ctx.document_target)]


def _manual_reference_rule(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    targets = _reference_entries(ctx)
    if not targets:
        return [finding(rule, "NOT_APPLICABLE", "未识别到参考文献条目。", target=ctx.document_target)]
    return [finding(rule, "MANUAL_REVIEW", "该参考文献语义/来源存在冲突，当前仅提供人工复核入口。", paragraph=targets[0], observed={"entry_count": len(targets)}, confidence=0.70)]


def _table_targets(ctx: RuleContext, *, notes: bool) -> list[dict[str, Any]]:
    targets = ctx.table_paragraphs()
    result: list[dict[str, Any]] = []
    for paragraph in targets:
        is_note = bool(re.match(r"^\s*注\s*[：:，,、]", text_of(paragraph)))
        if is_note == notes:
            result.append(paragraph)
    if notes:
        result.extend(_inspector_table_note_targets(ctx, result))
    return result


def _inspector_table_note_targets(
    ctx: RuleContext, existing: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve Inspector table-note candidates, including adjacent body notes.

    We only accept an explicit candidate emitted by Inspector.  A body
    paragraph is never selected merely because it happens to follow a table.
    """

    existing_ids = {str(item.get("id")) for item in existing}
    body_by_id = {str(item.get("id")): item for item in ctx.paragraph_list}
    targets: list[dict[str, Any]] = []
    tables = ctx.inspection.get("tables", [])
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        return targets
    for table_index, raw_table in enumerate(tables):
        if not isinstance(raw_table, Mapping):
            continue
        table_id = str(raw_table.get("id") or f"table-{table_index:06d}")
        candidates = raw_table.get("note_candidates", [])
        if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
            continue
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, Mapping):
                continue
            candidate = dict(raw_candidate)
            nested_bound = None
            for nested_key in ("adjacent_body_paragraph", "body_paragraph", "paragraph"):
                nested = candidate.get(nested_key)
                if isinstance(nested, Mapping):
                    nested_bound = dict(nested)
                    break
            candidate_id = candidate.get("paragraph_id") or candidate.get("id")
            # Prefer explicit adjacent/body IDs when provided, then accept the
            # candidate paragraph ID itself.  This works with both the current
            # cell-only Inspector shape and the newer adjacent-body shape.
            bound_id = (
                candidate.get("adjacent_body_paragraph_id")
                or candidate.get("body_paragraph_id")
                or candidate.get("adjacent_paragraph_id")
                or candidate_id
            )
            if isinstance(bound_id, Sequence) and not isinstance(bound_id, (str, bytes)):
                bound_id = next((value for value in bound_id if value is not None), None)
            if bound_id is None:
                for plural_key in ("adjacent_body_paragraph_ids", "body_paragraph_ids", "adjacent_paragraph_ids"):
                    plural = candidate.get(plural_key)
                    if isinstance(plural, Sequence) and not isinstance(plural, (str, bytes)):
                        bound_id = next((value for value in plural if value is not None), None)
                        if bound_id is not None:
                            break
            bound = body_by_id.get(str(bound_id)) if bound_id is not None else None
            if bound is None and candidate_id is not None:
                bound = body_by_id.get(str(candidate_id))
            if bound is None and nested_bound is not None:
                nested_id = nested_bound.get("id")
                bound = body_by_id.get(str(nested_id)) if nested_id is not None else nested_bound
            candidate_preview = candidate.get("text_preview")
            if not isinstance(candidate_preview, str) and nested_bound is not None:
                candidate_preview = nested_bound.get("text_preview")
            bound_text = text_of(bound) if bound is not None else ""
            if not re.match(r"^\s*注\s*[：:，,、]", str(candidate_preview or bound_text)):
                continue
            if bound is not None:
                target = dict(bound)
            else:
                preview = candidate.get("text_preview")
                if not isinstance(preview, str) or not re.match(r"^\s*注\s*[：:，,、]", preview):
                    continue
                target = {
                    "id": str(candidate_id or f"{table_id}-note-{len(targets):04d}"),
                    "index": candidate.get("index", len(targets)),
                    "text_preview": preview,
                }
            target["table_id"] = table_id
            target["table_note_candidate"] = True
            target.setdefault("in_table", bool(target.get("table_id") == table_id))
            for key in ("formatting", "effective_formatting", "formatting_evidence", "runs"):
                if key in candidate and key not in target:
                    target[key] = candidate[key]
            if str(target.get("id")) not in existing_ids:
                targets.append(target)
                existing_ids.add(str(target.get("id")))
    return targets


def _table_rule(rule: Mapping[str, Any], ctx: RuleContext, *, notes: bool) -> list[dict[str, Any]]:
    if not ctx.inspection.get("tables"):
        return [finding(rule, "NOT_APPLICABLE", "文档中没有表格对象。", target=ctx.document_target)]
    targets = _table_targets(ctx, notes=notes)
    if not targets:
        return [finding(rule, "NOT_APPLICABLE", "未识别到表格内目标段落。", target=ctx.document_target)]
    return _format_targets(rule, ctx, targets, missing_message="未识别到表格内目标段落。")


def _figure_caption(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    return _format_targets(rule, ctx, ctx.classified("figure_caption"), missing_message="未识别到图题。")


def _author(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    return _format_targets(rule, ctx, ctx.classified("author_name"), missing_message="未识别到明确作者姓名段；不从摘要或正文推断作者。")


def _abstract(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    return _format_targets(rule, ctx, ctx.classified("abstract_text"), missing_message="未识别到摘要正文。")


def _heading1(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    return _format_targets(rule, ctx, ctx.classified("heading_level_1"), missing_message="未识别到正文一级标题。")


def _title(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    return _format_targets(rule, ctx, ctx.classified("title"), missing_message="未识别到题名。")


def _author_info_format(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    targets = ctx.classified("author_information")
    if targets:
        return _format_targets(rule, ctx, targets, missing_message="未识别到作者信息/联系方式段。")
    info = footnote_info(ctx.inspection)
    count = info.get("actual_count", info.get("count", 0))
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        return [
            finding(
                rule,
                "MANUAL_REVIEW",
                "检测到脚注，但当前 Inspector 证据不足以判断其中是否为作者信息或其格式。",
                target=ctx.document_target,
                observed={"actual_footnote_count": count, "author_information_evidence": False},
                confidence=0.50,
            )
        ]
    return [finding(rule, "NOT_APPLICABLE", "未识别到作者信息/联系方式段。", target=ctx.document_target)]


def lint_manuscript_rule(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    rule_id = str(rule.get("id", ""))
    handlers = {
        "ER-MS-TITLE-001": _title,
        "ER-MS-TITLE-002": _title_marker,
        "ER-MS-AUTHOR-001": _author,
        "ER-MS-AUTHORINFO-001": _author_info_format,
        "ER-MS-AUTHORINFO-002": _authorinfo_term,
        "ER-MS-ABSTRACT-001": _abstract,
        "ER-MS-HEADING1-001": _heading1,
        "ER-MS-HEADING-HIERARCHY-001": _hierarchy,
        "ER-MS-EQUATION-001": _equation,
        "ER-MS-EQUATION-002": _equation_where,
        "ER-MS-TABLE-001": lambda r, c: _table_rule(r, c, notes=False),
        "ER-MS-TABLE-NOTE-001": lambda r, c: _table_rule(r, c, notes=True),
        "ER-MS-TABLE-NOTE-002": lambda r, c: _table_rule(r, c, notes=True),
        "ER-MS-FOOTNOTE-001": _footnote_numbering,
        "ER-MS-FOOTNOTE-002": _footnote_text,
        "ER-MS-FIGURE-001": _figure_caption,
        "ER-MS-FIGURE-002": _figure_color,
        "ER-MS-REF-LAYOUT-001": _reference_layout,
        "ER-MS-LATIN-FONT-001": _latin_font,
        "ER-MS-REF-PAGERANGE-001": _page_range,
        "ER-MS-REF-TITLECASE-001": _manual_reference_rule,
        "ER-MS-REF-AUTHOR-001": _manual_reference_rule,
        "ER-MS-REF-JOURNAL-001": _manual_reference_rule,
    }
    handler = handlers.get(rule_id)
    if handler is None:
        return []
    return handler(rule, ctx)


__all__ = ["MS_RULE_IDS", "lint_manuscript_rule"]
