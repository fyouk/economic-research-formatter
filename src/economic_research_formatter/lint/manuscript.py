"""Manuscript-focused rule checks.

Every normative value is read from the current rule's ``requirement`` mapping.
The functions here only interpret Inspector evidence and never mutate a DOCX.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from economic_research_formatter.models.numbering import numbering_state

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
    note_info,
    note_linkage_issues,
    observed_formatting,
    paragraph_value,
    paragraph_target,
    rule_severity,
    text_has_latin_or_digit,
    text_of,
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


_FORMAT_FONT_FIELDS = {
    "east_asia_font": "eastAsia",
    "ascii_font": "ascii",
    "hansi_font": "hAnsi",
}
_UNKNOWN_FORMAT_STATUS_VALUES = {
    "unknown",
    "unresolved",
    "unavailable",
    "missing",
    "insufficient_evidence",
}


def _formatting_envelopes(paragraph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Collect known Inspector formatting envelopes without guessing fields."""

    envelopes: list[Mapping[str, Any]] = []
    if not isinstance(paragraph, Mapping):
        return envelopes
    envelopes.append(paragraph)
    for key in ("formatting", "effective_formatting", "formatting_evidence", "formatting_observed"):
        value = paragraph.get(key)
        if isinstance(value, Mapping):
            envelopes.append(value)
    runs = paragraph.get("runs")
    if isinstance(runs, Sequence) and not isinstance(runs, (str, bytes)):
        for run in runs:
            if not isinstance(run, Mapping):
                continue
            envelopes.append(run)
            for key in ("formatting", "effective_formatting", "formatting_evidence", "formatting_observed"):
                value = run.get(key)
                if isinstance(value, Mapping):
                    envelopes.append(value)
    return envelopes


def _unresolved_formatting_fields(
    paragraph: Mapping[str, Any], requirement: Mapping[str, Any]
) -> list[str]:
    """Return required font fields occupied by unresolved theme evidence.

    ``common.format_mismatches`` intentionally treats a missing observed value
    as a mismatch for ordinary absent evidence.  Theme tokens are different:
    the Inspector has positively observed a value, but cannot resolve its
    concrete face.  This adapter keeps that distinction local to the
    manuscript rules without weakening ordinary missing-field checks.
    """

    required = {
        canonical: attribute
        for canonical, attribute in _FORMAT_FONT_FIELDS.items()
        if canonical in requirement
    }
    if not required:
        return []
    unresolved: set[str] = set()
    for envelope in _formatting_envelopes(paragraph):
        source = envelope.get("source")
        source_font = source.get("font") if isinstance(source, Mapping) else None
        if not isinstance(source_font, Mapping):
            source_font = {}
        effective = envelope.get("effective")
        effective = effective if isinstance(effective, Mapping) else envelope
        font = effective.get("font") if isinstance(effective, Mapping) else None
        font = font if isinstance(font, Mapping) else {}
        theme_evidence = font.get("theme_evidence")
        theme_evidence = theme_evidence if isinstance(theme_evidence, Mapping) else {}
        theme_tokens = font.get("theme")
        theme_tokens = theme_tokens if isinstance(theme_tokens, Mapping) else {}
        for canonical, attribute in required.items():
            evidence = theme_evidence.get(attribute)
            source_value = source_font.get(attribute)
            resolved_value = font.get(attribute)
            if source_value is not None and str(source_value).strip().casefold() in _UNKNOWN_FORMAT_STATUS_VALUES:
                unresolved.add(canonical)
            if isinstance(evidence, Mapping) and evidence.get("resolved") is False:
                unresolved.add(canonical)
            if isinstance(resolved_value, Mapping):
                status = str(resolved_value.get("status", "")).strip().casefold().replace("-", "_")
                if status in _UNKNOWN_FORMAT_STATUS_VALUES:
                    unresolved.add(canonical)
            # A few producer versions expose only the token and source, not the
            # nested evidence envelope.  A concrete value of ``None`` together
            # with that explicit token is still unresolved, never a violation.
            if attribute in theme_tokens and effective.get(attribute) is None:
                unresolved.add(canonical)
    return sorted(unresolved)


def _mark_unresolved_formatting(
    checked: Mapping[str, Any] | None,
    paragraph: Mapping[str, Any],
    requirement: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Annotate unresolved theme fields as unchecked/manual-review evidence."""

    fields = _unresolved_formatting_fields(paragraph, requirement)
    if not fields:
        return dict(checked) if isinstance(checked, Mapping) else None
    if isinstance(checked, Mapping):
        observed = dict(checked.get("observed", {}))
        mismatches = dict(checked.get("mismatches", {}))
        unchecked = set(checked.get("unchecked", []))
    else:
        observed = observed_formatting(paragraph)
        mismatches = {}
        unchecked = set()
    observed["unresolved_formatting_fields"] = fields
    resolved_mismatch_fields: set[str] = set()
    run_mismatches = mismatches.get("runs")
    if isinstance(run_mismatches, Sequence) and not isinstance(run_mismatches, (str, bytes)):
        runs = meaningful_runs(paragraph)
        filtered = []
        for item in run_mismatches:
            if not isinstance(item, Mapping):
                filtered.append(item)
                continue
            field = str(item.get("field", ""))
            if field not in fields:
                filtered.append(item)
                continue
            try:
                run_index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if not 0 <= run_index < len(runs):
                continue
            run_unresolved = set(_unresolved_formatting_fields(runs[run_index], requirement))
            if field in run_unresolved:
                continue
            filtered.append(item)
            resolved_mismatch_fields.add(field)
        if filtered:
            mismatches["runs"] = filtered
        else:
            mismatches.pop("runs", None)
    manual_fields = set(fields) - resolved_mismatch_fields
    unchecked.update(manual_fields)
    for field in manual_fields:
        # A missing value caused only by unresolved theme evidence must not
        # remain in the deterministic-violation bucket.
        mismatches.pop(field, None)
    return {
        "observed": observed,
        "mismatches": mismatches,
        "unchecked": sorted(unchecked),
    }


def _missing_formatting_fields(
    paragraph: Mapping[str, Any], requirement: Mapping[str, Any]
) -> list[str]:
    """Return required font fields for which no concrete evidence exists."""

    unresolved = set(_unresolved_formatting_fields(paragraph, requirement))
    missing: list[str] = []
    for canonical in _FORMAT_FONT_FIELDS:
        if canonical not in requirement or canonical in unresolved:
            continue
        if paragraph_value(paragraph, canonical) is None:
            missing.append(canonical)
    return missing


def _numbering_mapping(paragraph: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("numbering", "numPr"):
        value = paragraph.get(key)
        if isinstance(value, Mapping):
            return value
    return None


def _mark_numbering_evidence(
    checked: Mapping[str, Any] | None,
    paragraph: Mapping[str, Any],
    requirement: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Use producer numbering state instead of truthiness of a mapping."""

    if "numbered" not in requirement:
        return dict(checked) if isinstance(checked, Mapping) else None
    numbering = _numbering_mapping(paragraph)
    if numbering is None:
        return dict(checked) if isinstance(checked, Mapping) else None

    numbered = numbering_state(numbering)
    removes = _as_bool(numbering.get("removes_numbering"))
    resolved = _as_bool(numbering.get("resolved"))
    expected = bool(requirement.get("numbered"))
    if isinstance(checked, Mapping):
        observed = dict(checked.get("observed", {}))
        mismatches = dict(checked.get("mismatches", {}))
        unchecked = set(checked.get("unchecked", []))
    else:
        observed = observed_formatting(paragraph)
        mismatches = {}
        unchecked = set()
    observed["numbered"] = numbered
    observed["numbering_evidence"] = {
        "numbered": numbered,
        "removes_numbering": removes,
        "resolved": resolved,
    }
    mismatches.pop("numbered", None)
    if numbered is None:
        unchecked.add("numbered")
    elif numbered != expected:
        mismatches["numbered"] = {"observed": numbered, "expected": expected}
    return {
        "observed": observed,
        "mismatches": mismatches,
        "unchecked": sorted(unchecked),
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
        checked = _mark_numbering_evidence(checked, paragraph, expected_for(rule))
        checked = _mark_unresolved_formatting(checked, paragraph, expected_for(rule))
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
            if checked.get("mismatches"):
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
            elif checked.get("unchecked"):
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
                        "PASS",
                        f"{rule.get('target', '目标')}已满足规则要求。",
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
    observed: dict[str, Any] = {"marker": marker, "mechanism": mechanism}
    marker_evidence: list[dict[str, Any]] = []
    has_author_target = bool(ctx.classified("author_name", "author_information"))
    for paragraph in titles:
        references = _title_footnote_reference_evidence(ctx, paragraph)
        has_author_target = has_author_target or bool(references) or bool(paragraph.get("has_footnote_reference"))
        for reference in references or ([{"kind": "footnote", "marker": "unknown"}] if paragraph.get("has_footnote_reference") else []):
            marker_evidence.append(_classify_title_marker_reference(reference, marker))
        if references:
            observed.setdefault("footnote_reference_evidence", []).extend(references)
        observed.setdefault("title_ids", []).append(paragraph.get("id"))
    # A title without any author-information target is not enough evidence to
    # assert a missing marker.  In particular, do not infer an author from
    # abstract/body prose merely because a title exists.
    if not has_author_target:
        return [
            finding(
                rule,
                "NOT_APPLICABLE",
                "未识别到题名作者信息目标，无法检查脚注标记。",
                paragraph=titles[0],
                observed=observed,
            )
        ]
    if marker_evidence:
        observed["marker_evidence"] = marker_evidence
        if any(item["status"] == "wrong" for item in marker_evidence):
            return [finding(rule, rule_severity(rule), "题名作者信息脚注使用了非要求的标记。", paragraph=titles[0], observed=observed)]
        if any(item["status"] == "unknown" for item in marker_evidence):
            return [finding(rule, "MANUAL_REVIEW", "题名存在脚注引用，但无法证明其显示标记为要求的自定义星号。", paragraph=titles[0], observed=observed)]
        return [finding(rule, "PASS", "题名作者信息脚注已证明使用要求的自定义星号标记。", paragraph=titles[0], observed=observed)]
    # This rule checks a required marker; its absence is a reportable error, but
    # remains read-only and never inserts a footnote.
    observed["marker_evidence"] = []
    return [finding(rule, rule_severity(rule), "题名未检测到要求的作者信息脚注标记。", paragraph=titles[0], observed=observed)]


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "是"}:
        return True
    if text in {"0", "false", "no", "n", "否"}:
        return False
    return None


def _classify_title_marker_reference(reference: Any, expected_marker: str) -> dict[str, Any]:
    """Classify Inspector marker evidence without guessing hidden glyphs.

    ``w:footnoteReference`` is the OOXML element name, not the displayed
    marker.  It therefore cannot by itself establish compliance.  A literal
    marker is accepted only when the Inspector associates it with this
    reference; otherwise the result remains ``unknown``.
    """

    if not isinstance(reference, Mapping):
        return {"status": "unknown", "reason": "reference_without_marker_metadata", "reference": reference}
    explicit_values: list[tuple[str, Any]] = []
    for key in (
        "display_marker",
        "literal_marker",
        "marker_glyph",
        "marker_text",
        "custom_marker",
        "customMark",
        "symbol",
    ):
        if reference.get(key) is not None:
            explicit_values.append((key, reference.get(key)))
    marker_value = reference.get("marker")
    if marker_value is not None and str(marker_value) not in {"footnoteReference", "endnoteReference"}:
        explicit_values.append(("marker", marker_value))
    value = next(((key, str(raw).strip()) for key, raw in explicit_values if str(raw).strip()), None)
    custom = next(
        (
            _as_bool(reference.get(key))
            for key in ("custom_mark", "customMark", "custom_mark_follows", "customMarkFollows", "is_custom_mark")
            if _as_bool(reference.get(key)) is not None
        ),
        None,
    )
    automatic = next(
        (
            _as_bool(reference.get(key))
            for key in ("automatic", "automatic_numbered", "numbered", "is_numbered")
            if _as_bool(reference.get(key)) is not None
        ),
        None,
    )
    result: dict[str, Any] = {
        "id": reference.get("id", reference.get("reference_id")),
        "kind": reference.get("kind", "footnote"),
        "status": "unknown",
        "reason": "marker_not_exposed",
    }
    if custom is not None:
        result["custom_mark_follows"] = custom
    if value is not None:
        key, glyph = value
        result["marker"] = glyph
        result["marker_source"] = key
        if glyph == expected_marker and custom is True:
            result.update(status="pass", reason="associated_custom_marker")
            return result
        if glyph == expected_marker and custom is None:
            result.update(reason="marker_without_custom_mark_evidence")
            return result
        result.update(status="wrong", reason="associated_nonmatching_marker")
        return result
    if automatic is True:
        result.update(status="wrong", reason="automatic_numbered_marker")
    elif custom is not None:
        result["custom_mark_follows"] = custom
        result["reason"] = "custom_marker_without_literal_glyph"
    return result


def _reference_id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("id", "reference_id", "note_id"):
            identifier = value.get(key)
            if identifier is not None and str(identifier).strip():
                return str(identifier).strip()
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return value.strip()
    return None


def _reference_kind(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("kind", "footnote")).casefold()
    return "footnote"


def _merge_reference_values(values: Sequence[Any]) -> list[Any]:
    """Merge aliases and detailed note evidence by reference ID.

    Inspector intentionally exposes a compact ID list alongside detailed
    evidence.  The ID list proves existence only; once a structured record for
    the same reference arrives, it is the sole marker evidence used by the
    title-marker rule.
    """

    merged: dict[str, Any] = {}
    unkeyed: list[Any] = []
    unkeyed_seen: set[str] = set()
    for value in values:
        if _reference_kind(value) != "footnote":
            continue
        identifier = _reference_id(value)
        if identifier is None:
            marker = repr(value)
            if marker not in unkeyed_seen:
                unkeyed_seen.add(marker)
                unkeyed.append(value)
            continue
        if isinstance(value, Mapping):
            incoming = dict(value)
            existing = merged.get(identifier)
            if isinstance(existing, Mapping):
                combined = dict(existing)
                combined.update({key: item for key, item in incoming.items() if item is not None})
                merged[identifier] = combined
            elif existing is None:
                merged[identifier] = incoming
            # A naked ID already present is replaced by structured evidence.
            else:
                merged[identifier] = incoming
        elif identifier not in merged:
            merged[identifier] = {"id": value}
    return [*merged.values(), *unkeyed]


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
    return _merge_reference_values(values)


def _authorinfo_term(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = expected_for(rule)
    preferred = str(requirement.get("preferred_term", ""))
    disallowed = str(requirement.get("disallowed_term", ""))
    targets = ctx.classified("author_information")
    term_locations: list[tuple[Mapping[str, Any], list[dict[str, Any]], list[dict[str, Any]]]] = []
    for paragraph in targets:
        text = text_of(paragraph)
        locations: list[dict[str, Any]] = []
        for term in (preferred, disallowed):
            if not term:
                continue
            start = 0
            while True:
                index = text.find(term, start)
                if index < 0:
                    break
                locations.append(
                    {
                        "term": term,
                        "span": [index, index + len(term)],
                        "text_preview": text[max(0, index - 20) : index + len(term) + 20],
                    }
                )
                start = index + max(1, len(term))
        locations.sort(key=lambda item: (item["span"][0], item["term"]))
        disallowed_locations = [item for item in locations if item["term"] == disallowed]
        if locations:
            for item in locations:
                item["paragraph_id"] = paragraph.get("id")
        term_locations.append((paragraph, locations, disallowed_locations))
    bad = [item for item in term_locations if item[2]]
    if bad:
        return [
            finding(
                rule,
                rule_severity(rule),
                f"作者信息中使用了不推荐术语“{disallowed}”，应使用“{preferred}”。",
                paragraph=paragraph,
                observed={"term": disallowed, "locations": locations},
            )
            for paragraph, locations, _ in bad
        ]
    preferred_observed = [item for _, locations, _ in term_locations for item in locations if item["term"] == preferred]
    if preferred_observed:
        preferred_paragraph = next(
            paragraph
            for paragraph, locations, _ in term_locations
            if any(item["term"] == preferred for item in locations)
        )
        return [
            finding(
                rule,
                "PASS",
                f"作者信息术语符合“{preferred}”要求。",
                paragraph=preferred_paragraph,
                observed={"term": preferred, "locations": preferred_observed},
            )
        ]
    # An author-information role without either spelling is not evidence that
    # the preferred term was used.  Keep the result explicitly non-applicable.
    if targets:
        return [
            finding(
                rule,
                "NOT_APPLICABLE",
                "已识别作者信息段，但未观察到“电子信箱”或“电子邮箱”术语。",
                paragraph=targets[0],
                observed={"term": None, "locations": []},
            )
        ]
    return [finding(rule, "NOT_APPLICABLE", "未识别到作者联系信息段。", target=ctx.document_target)]


def _heading_has_independent_level_evidence(paragraph: Mapping[str, Any]) -> bool:
    classification = paragraph.get("_classification")
    evidence = classification.get("evidence", []) if isinstance(classification, Mapping) else []
    if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)):
        if any(
            str(token).startswith("structural_level=")
            for token in evidence
        ):
            return True
    # Keep this adapter tolerant of older classification envelopes when raw
    # Inspector structure is still present on the paragraph.
    style = str(paragraph.get("style_name") or paragraph.get("effective_style_name") or "").casefold()
    if re.search(r"(?:heading|标题)[-_ ]*[2-4]\b", style):
        return True
    if paragraph.get("outline_level") is not None:
        return True
    numbering = paragraph.get("numbering") or paragraph.get("numPr")
    if not isinstance(numbering, Mapping):
        return False
    if numbering_state(numbering) is not True:
        return False
    return any(numbering.get(key) is not None for key in ("ilvl", "level", "outline_level"))


def _visible_only_level_jump(targets: Sequence[Mapping[str, Any]], ctx: RuleContext) -> bool:
    """Return whether visible markers leave a non-adjacent hierarchy jump."""

    if not targets:
        return False
    levels: list[tuple[int, bool]] = []
    for paragraph in targets:
        role = ctx.role(paragraph)
        suffix = role.rsplit("_", 1)[-1]
        if suffix.isdigit():
            levels.append((int(suffix), _heading_has_independent_level_evidence(paragraph)))
    return any(
        abs(current_level - previous_level) > 1
        and not (previous_independent and current_independent)
        for (previous_level, previous_independent), (current_level, current_independent) in zip(
            levels, levels[1:]
        )
    )


def _hierarchy(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    targets = ctx.classified("heading_level_2", "heading_level_3", "heading_level_4")
    if not targets:
        return [finding(rule, "NOT_APPLICABLE", "未识别到二级及以下标题，无法检查标题层级。", target=ctx.document_target)]
    requirement = expected_for(rule)
    level_2_req = requirement.get("level_2", {})
    level_2_req = level_2_req if isinstance(level_2_req, Mapping) else {}
    lower_req = requirement.get("lower_levels", {})
    lower_req = lower_req if isinstance(lower_req, Mapping) else {}
    expected_by_level: dict[int, dict[str, Any]] = {
        2: {
            "prefix": str(level_2_req.get("prefix_pattern", "")),
            "leading_spaces_cn": level_2_req.get("leading_spaces_cn", 2),
        },
        3: {"prefix": str(lower_req.get("level_3_prefix", "1."))},
        4: {"prefix": str(lower_req.get("level_4_prefix", "（1）"))},
    }
    lower_sequence = lower_req.get("sequence", [])
    if isinstance(lower_sequence, Sequence) and not isinstance(lower_sequence, (str, bytes)):
        if len(lower_sequence) > 0 and not lower_req.get("level_3_prefix"):
            expected_by_level[3]["prefix"] = str(lower_sequence[0])
        if len(lower_sequence) > 1 and not lower_req.get("level_4_prefix"):
            expected_by_level[4]["prefix"] = str(lower_sequence[1])

    bad: list[dict[str, Any]] = []
    observed_levels: list[dict[str, Any]] = []
    for paragraph in targets:
        role = ctx.role(paragraph)
        level = int(role.rsplit("_", 1)[-1]) if role.rsplit("_", 1)[-1].isdigit() else None
        text = text_of(paragraph)
        leading_chars: list[str] = []
        for character in text:
            if character not in {" ", "\u3000"}:
                break
            leading_chars.append(character)
        text_without_leading = text[len(leading_chars) :]
        heading_observed = {
            "id": paragraph.get("id"),
            "level": level,
            "text_preview": text[:80],
            "leading_space_count": len(leading_chars),
            "leading_space_codepoints": [f"U+{ord(character):04X}" for character in leading_chars],
            "leading_space_policy": "exactly two U+0020 characters; U+3000 is not silently treated as two U+0020 characters",
            "prefix_observed": text_without_leading[:20],
        }
        observed_levels.append(heading_observed)
        valid = True
        expected = expected_by_level.get(level or -1, {})
        expected_prefix = str(expected.get("prefix", ""))
        if level == 2:
            valid = len(leading_chars) == int(expected.get("leading_spaces_cn", 2)) and all(
                character == " " for character in leading_chars
            )
            if expected_prefix:
                valid = valid and text_without_leading.startswith(expected_prefix)
        elif level in {3, 4} and expected_prefix:
            valid = text_without_leading.startswith(expected_prefix)
        if not valid:
            violation = dict(heading_observed)
            violation["expected"] = expected
            bad.append(violation)
    if _visible_only_level_jump(targets, ctx):
        first = targets[0]
        return [
            finding(
                rule,
                "MANUAL_REVIEW",
                "仅检测到可见标题前缀，且层级发生跳级；无法可靠确定其真实层级，当前需人工复核。",
                paragraph=first,
                target=paragraph_target(first, ctx.role(first)),
                observed={
                    "headings": observed_levels,
                    "hierarchy_evidence": "visible_prefix_only",
                    "ambiguous_level_jump": True,
                    "leading_space_policy": "exactly two U+0020 characters; U+3000 is not silently treated as two U+0020 characters",
                },
                confidence=0.50,
            )
        ]
    if bad:
        first_bad = next((paragraph for paragraph in targets if paragraph.get("id") == bad[0].get("id")), targets[0])
        return [
            finding(
                rule,
                rule_severity(rule),
                "标题编号层级不符合来源规则；检测到论文式层级编号。",
                paragraph=first_bad,
                target=paragraph_target(first_bad, ctx.role(first_bad)),
                observed={
                    "headings": observed_levels,
                    "violations": bad,
                    "style": "thesis-style numbering",
                    "leading_space_policy": "exactly two U+0020 characters; U+3000 is not silently treated as two U+0020 characters",
                },
            )
            for item in bad[:1]
        ]
    return [finding(rule, "PASS", "标题层级编号符合规则。", target=ctx.document_target, observed={"headings": observed_levels})]


def _equation(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    info = equation_info(ctx.inspection)
    requirement = expected_for(rule)
    allowed_values = requirement.get("allowed_editors", [])
    if not isinstance(allowed_values, Sequence) or isinstance(allowed_values, (str, bytes)):
        allowed_values = []
    allowed = {_canonical_equation_editor(value) for value in allowed_values}
    allowed.discard(None)

    def count_value(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    omath_count = count_value(info.get("omath_count", info.get("count", 0)))
    raw_items = info.get("items", [])
    items = [dict(item) for item in raw_items if isinstance(item, Mapping)] if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)) else []
    evidence_items: list[dict[str, Any]] = []
    counts: dict[str, int] = {"Word Equation": 0, "MathType": 0, "Unknown OLE": 0}
    for item in items:
        raw_editor = item.get("editor") or item.get("type") or item.get("kind")
        editor = _canonical_equation_editor(raw_editor)
        if editor is None:
            # An object item with no editor metadata is still an unknown OLE;
            # do not let an opaque item disappear into NOT_APPLICABLE.
            if str(item.get("kind", "")).casefold() in {"ole", "ole_object", "object", "embedded_object"}:
                editor = "Unknown OLE"
            else:
                continue
        counts[editor] = counts.get(editor, 0) + 1
        evidence_items.append(
            {
                "id": item.get("id"),
                "editor": editor,
                "paragraph_id": item.get("paragraph_id"),
                "evidence": item.get("evidence", []),
            }
        )
    editor_counts = info.get("editors", {})
    if isinstance(editor_counts, Mapping):
        for raw_name, raw_count in editor_counts.items():
            if str(raw_name).strip().casefold().replace("_", "") in {"mathtypeorole", "formulaorole"}:
                # Legacy aggregate; concrete ``items``/editor counters above
                # are the only safe source for distinguishing MathType from
                # another OLE object.
                continue
            editor = _canonical_equation_editor(raw_name)
            if editor is None:
                continue
            if isinstance(raw_count, Mapping):
                raw_count = raw_count.get("count", raw_count.get("value", raw_count.get("items", 0)))
            count = count_value(raw_count)
            if count > counts.get(editor, 0):
                counts[editor] = count
                evidence_items.append(
                    {
                        "id": None,
                        "editor": editor,
                        "paragraph_id": None,
                        "evidence": [f"editors.{raw_name}"],
                    }
                )
    if omath_count > counts["Word Equation"]:
        counts["Word Equation"] = omath_count
        existing = sum(item["editor"] == "Word Equation" for item in evidence_items)
        for index in range(omath_count - existing):
            evidence_items.append({"id": f"omml-{index:06d}", "editor": "Word Equation", "paragraph_id": None, "evidence": ["omath_count"]})

    editor_names = sorted(name for name, count in counts.items() if count > 0)
    object_count = count_value(info.get("object_count", 0))
    if object_count > sum(counts.get(name, 0) for name in ("MathType", "Unknown OLE")):
        # The count proves an object exists but not which editor owns it.
        counts["Unknown OLE"] += object_count - sum(counts.get(name, 0) for name in ("MathType", "Unknown OLE"))
        editor_names = sorted(name for name, count in counts.items() if count > 0)
        evidence_items.append({"id": None, "editor": "Unknown OLE", "paragraph_id": None, "evidence": ["object_count_without_item"]})

    if not editor_names and not ctx.classified("equation"):
        return [finding(rule, "NOT_APPLICABLE", "文档中未检测到公式或相关嵌入对象。", target=ctx.document_target)]
    if not editor_names:
        editor_names = ["Unknown OLE"]
        counts["Unknown OLE"] = 1

    observed = {
        "editors": editor_names,
        "editor_counts": {name: counts[name] for name in editor_names},
        "items": evidence_items,
        "omath_count": omath_count,
    }
    paragraph_id = next((item.get("paragraph_id") for item in evidence_items if item.get("paragraph_id")), None)
    target = next((paragraph for paragraph in ctx.paragraph_list if paragraph.get("id") == paragraph_id), None)
    if "Unknown OLE" in editor_names or not set(editor_names).issubset(allowed):
        status = "MANUAL_REVIEW"
        message = "公式编辑器证据包含未知或不在允许列表中的对象，当前需人工复核。"
    else:
        status = "PASS"
        message = "公式编辑器均已由 Inspector 证据确认并属于允许列表。"
    kwargs: dict[str, Any] = {"observed": observed, "confidence": 0.99}
    if target is not None:
        kwargs["paragraph"] = target
    else:
        kwargs["target"] = ctx.document_target
    return [finding(rule, status, message, **kwargs)]


def _canonical_equation_editor(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().casefold().replace("_", " ").replace("-", " ")
    compact = re.sub(r"\s+", "", text)
    if compact in {"wordequation", "omml", "omath", "omathpara", "officeequation"} or "wordequation" in compact:
        return "Word Equation"
    if ("mathtype" in compact and "orole" not in compact) or compact in {"equation.3", "equation3", "mtef"}:
        return "MathType"
    if compact in {"oleunknown", "unknownole", "unknownobject", "ole", "embeddedobject"} or "unknownole" in compact:
        return "Unknown OLE"
    if compact in {"word", "equation"}:
        return "Word Equation"
    return str(value).strip() or None


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
        "eachsect": "each_section",
        "section": "each_section",
        "continuous": "continuous",
    }.get(compact)


def _restart_values(info: Mapping[str, Any], inspection: Mapping[str, Any]) -> list[Any]:
    """Collect effective restart values from the note report and sections."""

    if "num_restarts" in info:
        effective = info.get("num_restarts")
        if isinstance(effective, Sequence) and not isinstance(effective, (str, bytes)):
            return [value for value in effective if value is not None]
        return [effective] if effective is not None else []
    if "num_restart" in info:
        effective = info.get("num_restart")
        if isinstance(effective, Sequence) and not isinstance(effective, (str, bytes)):
            return [value for value in effective if value is not None]
        return [effective] if effective is not None else []

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


def _footnote_reference_restart_evidence(
    inspection: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return one effective restart record per concrete footnote reference."""

    paragraphs = inspection.get("paragraphs", [])
    ordered_records: list[tuple[tuple[Any, ...], Mapping[str, Any]]] = []
    if isinstance(paragraphs, Sequence) and not isinstance(paragraphs, (str, bytes)):
        for value in paragraphs:
            if not isinstance(value, Mapping):
                continue
            ordered_records.append(
                (
                    (value.get("body_order", 10**12), 0, value.get("index", 10**12)),
                    value,
                )
            )
    tables = inspection.get("tables", [])
    if isinstance(tables, Sequence) and not isinstance(tables, (str, bytes)):
        for table in tables:
            if not isinstance(table, Mapping):
                continue
            cells = table.get("cells", [])
            if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
                continue
            for cell in cells:
                if not isinstance(cell, Mapping):
                    continue
                values = cell.get("paragraphs", [])
                if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                    for value in values:
                        if not isinstance(value, Mapping):
                            continue
                        ordered_records.append(
                            (
                                (
                                    table.get("body_order", 10**12),
                                    1,
                                    cell.get("row", 10**12),
                                    cell.get("column", 10**12),
                                    value.get("index", 10**12),
                                ),
                                value,
                            )
                        )
    paragraph_records = []
    for order, value in sorted(ordered_records, key=lambda item: item[0]):
        record = dict(value)
        record["_story_order"] = order
        record["_story_index"] = order[0]
        paragraph_records.append(record)
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw_paragraph in paragraph_records:
        paragraph_id = str(raw_paragraph.get("id", ""))
        values = raw_paragraph.get("footnote_reference_evidence", [])
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            values = []
        for reference_index, raw_reference in enumerate(values):
            if not isinstance(raw_reference, Mapping):
                continue
            note_id = raw_reference.get("reference_id", raw_reference.get("id"))
            run_index = raw_reference.get("run_index", reference_index)
            identity = (paragraph_id, str(note_id), str(run_index))
            if identity in seen:
                continue
            seen.add(identity)
            effective = raw_reference.get("effective_properties")
            if not isinstance(effective, Mapping):
                note_properties = raw_reference.get("note_properties")
                note_properties = note_properties if isinstance(note_properties, Mapping) else {}
                effective = note_properties.get("effective_properties")
                if not isinstance(effective, Mapping):
                    effective = note_properties
            property_evidence = raw_reference.get("property_evidence")
            if not isinstance(property_evidence, Mapping):
                note_properties = raw_reference.get("note_properties")
                note_properties = note_properties if isinstance(note_properties, Mapping) else {}
                property_evidence = note_properties.get("property_evidence", {})
            section_index = raw_reference.get(
                "section_index", raw_paragraph.get("section_index")
            )
            result.append(
                {
                    "target": {
                        "kind": "footnote_reference",
                        "id": (
                            f"{paragraph_id}-footnote-{note_id}-run-{run_index}"
                            if paragraph_id
                            else f"footnote-{note_id}-reference-{reference_index}"
                        ),
                        "paragraph_id": paragraph_id or None,
                        "source_id": f"footnote-{note_id}",
                        "note_id": note_id,
                        "footnote_id": note_id,
                        "index": raw_paragraph.get("_story_index"),
                        "story_order": list(raw_paragraph.get("_story_order", ())),
                        "section_index": section_index,
                        "run_index": run_index,
                    },
                    "restart": effective.get("numRestart"),
                    "property_evidence": dict(property_evidence),
                }
            )
    return result


def _footnote_numbering(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    info = footnote_info(ctx.inspection)
    count = info.get("actual_count", info.get("count", 0))
    try:
        count = int(count or 0)
    except (TypeError, ValueError):
        count = 0
    linkage_issues = note_linkage_issues(ctx.inspection, ("footnote",))
    linkage_findings = (
        [
            finding(
                rule,
                "MANUAL_REVIEW",
                "正文脚注引用与脚注定义无法一一对应。",
                target=ctx.document_target,
                observed={"note_linkage_issues": linkage_issues},
            )
        ]
        if linkage_issues
        else []
    )
    active_reference_ids = {
        str(value)
        for issue in linkage_issues
        for value in issue.get("active_reference_ids", [])
    }
    if count == 0:
        if linkage_findings:
            return linkage_findings
        return [finding(rule, "NOT_APPLICABLE", "文档没有实际脚注；分隔符不计入脚注。", target=ctx.document_target)]
    requirement = expected_for(rule)
    expected_restart = requirement.get("restart")
    expected = _normalize_restart(expected_restart)
    reference_records = _footnote_reference_restart_evidence(ctx.inspection)
    if linkage_issues:
        reference_records = [
            record
            for record in reference_records
            if str(record.get("target", {}).get("footnote_id"))
            in active_reference_ids
        ]
        unique_records: list[dict[str, Any]] = []
        seen_active_ids: set[str] = set()
        for record in reference_records:
            active_id = str(record.get("target", {}).get("footnote_id"))
            if active_id in seen_active_ids:
                continue
            seen_active_ids.add(active_id)
            unique_records.append(record)
        reference_records = unique_records
    if reference_records:
        results: list[dict[str, Any]] = []
        for record in reference_records:
            raw_restart = record["restart"]
            normalized = _normalize_restart(raw_restart)
            observed = {
                "restart": raw_restart,
                "restart_normalized": normalized,
                "restarts": [raw_restart] if raw_restart is not None else [],
                "restarts_normalized": [normalized] if normalized is not None else [],
                "section_index": record["target"].get("section_index"),
                "property_evidence": record["property_evidence"],
            }
            if raw_restart is None or expected is None:
                status = "MANUAL_REVIEW"
                message = "脚注引用缺少可判定的有效编号重启证据。"
            elif normalized is None:
                status = "MANUAL_REVIEW"
                message = "脚注引用的编号重启证据无法解释，需人工确认。"
            elif normalized != expected:
                status = rule_severity(rule)
                message = "该脚注引用所属节的编号重启方式不符合要求。"
            else:
                status = "PASS"
                message = "该脚注引用所属节的编号重启方式符合要求。"
            results.append(
                finding(
                    rule,
                    status,
                    message,
                    target=record["target"],
                    observed=observed,
                )
            )
        return [*results, *linkage_findings]
    if linkage_findings:
        return linkage_findings
    raw_restarts = _restart_values(info, ctx.inspection)
    normalized = [_normalize_restart(value) for value in raw_restarts]
    observed = {
        "restarts": raw_restarts,
        "restarts_normalized": normalized,
        "actual_count": count,
    }
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
    linkage_issues = note_linkage_issues(ctx.inspection, ("footnote",))
    linkage_findings = (
        [
            finding(
                rule,
                "MANUAL_REVIEW",
                "正文脚注引用与脚注定义无法一一对应。",
                target=ctx.document_target,
                observed={"note_linkage_issues": linkage_issues},
            )
        ]
        if linkage_issues
        else []
    )
    if count == 0:
        if linkage_findings:
            return linkage_findings
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
        if linkage_findings:
            return linkage_findings
        return [finding(rule, "MANUAL_REVIEW", "检测到实际脚注，但脚注正文格式未在 Inspector 输出中提供。", target=ctx.document_target, observed={"actual_count": count})]
    return [
        *_format_targets(rule, ctx, targets, missing_message="未提供脚注正文。"),
        *linkage_findings,
    ]


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
    targets: list[tuple[Mapping[str, Any], float, str]] = []
    seen_ids: set[tuple[str, str]] = set()
    nested_note_ids: set[str] = set()
    linkage_issues = note_linkage_issues(ctx.inspection)
    linkage_by_kind = {
        str(issue.get("kind")): issue
        for issue in linkage_issues
        if issue.get("kind")
    }

    # Prefer note-part paragraphs when available.  ``RuleContext`` also keeps
    # one compatibility-level item per note; skipping that envelope avoids a
    # duplicate finding for every nested footnote paragraph.
    for note_kind in ("footnote", "endnote"):
        collection = note_info(ctx.inspection, note_kind)
        raw_note_items = collection.get("items", collection.get("paragraphs", []))
        if not isinstance(raw_note_items, Sequence) or isinstance(raw_note_items, (str, bytes)):
            continue
        for note_index, raw_note in enumerate(raw_note_items):
            if not isinstance(raw_note, Mapping):
                continue
            raw_note_id = raw_note.get(f"{note_kind}_id", raw_note.get("note_id", raw_note.get("id", note_index)))
            linkage = linkage_by_kind.get(note_kind)
            if linkage is not None:
                active_note_ids = {
                    str(value)
                    for value in linkage.get("active_note_ids", [])
                }
                if not linkage.get("has_reference_evidence") or str(raw_note_id) not in active_note_ids:
                    continue
            note_id = str(raw_note_id)
            nested = raw_note.get("paragraphs", [])
            if not isinstance(nested, Sequence) or isinstance(nested, (str, bytes)) or not nested:
                continue
            nested_paragraphs = [
                raw_paragraph
                for raw_paragraph in nested
                if isinstance(raw_paragraph, Mapping)
            ]
            if not nested_paragraphs:
                continue
            nested_note_ids.add(f"{note_kind}-{note_id}")
            for paragraph_index, raw_paragraph in enumerate(nested_paragraphs):
                paragraph = dict(raw_paragraph)
                paragraph.setdefault("id", f"{note_kind}s-{note_id}-p-{paragraph_index:04d}")
                paragraph.setdefault("index", paragraph_index)
                paragraph.setdefault("kind", note_kind)
                paragraph.setdefault("note_id", raw_note_id)
                paragraph.setdefault(f"{note_kind}_id", raw_note_id)
                if not text_has_latin_or_digit(text_of(paragraph)):
                    continue
                _append_latin_target(targets, seen_ids, paragraph, 0.99, note_kind)

    for paragraph in ctx.paragraph_list:
        kind = str(paragraph.get("kind", "")).casefold()
        if kind in {"footnote", "ordinary_footnote", "endnote"} and str(paragraph.get("id")) in nested_note_ids:
            continue
        text = text_of(paragraph)
        if not text_has_latin_or_digit(text):
            continue
        scope = kind if kind in {"footnote", "endnote"} else "footnote" if kind == "ordinary_footnote" else "body"
        _append_latin_target(targets, seen_ids, paragraph, 0.99 if meaningful_runs(paragraph) else 0.80, scope)
    for paragraph in ctx.table_paragraphs():
        if text_has_latin_or_digit(text_of(paragraph)):
            _append_latin_target(targets, seen_ids, paragraph, 0.99 if meaningful_runs(paragraph) else 0.80, "table")
    if not targets:
        if linkage_issues:
            return [
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "脚注或尾注定义与正文引用无法一一对应，相关字体证据需人工复核。",
                    target=ctx.document_target,
                    observed={"note_linkage_issues": linkage_issues},
                    confidence=0.40,
                )
            ]
        return [finding(rule, "NOT_APPLICABLE", "文档中没有可检查的英文字母或数字。", target=ctx.document_target)]
    results: list[dict[str, Any]] = []
    if linkage_issues:
        results.append(
            finding(
                rule,
                "MANUAL_REVIEW",
                "脚注或尾注定义与正文引用无法一一对应；仅对已绑定目标继续检查。",
                target=ctx.document_target,
                observed={"note_linkage_issues": linkage_issues},
                confidence=0.40,
            )
        )
    for paragraph, confidence, scope in targets:
        mismatches: dict[str, Any] = {}
        observed: dict[str, Any] = {"scope": scope, "font_runs": []}
        unresolved_field_set: set[str] = set()
        runs = meaningful_runs(paragraph)
        for run in runs or [paragraph]:
            run_text = text_of(run)
            if not text_has_latin_or_digit(run_text or text_of(paragraph)):
                continue
            run_unresolved = set(_unresolved_formatting_fields(run, requirement))
            unresolved_field_set.update(run_unresolved)
            actual_ascii = run_value(run, "ascii_font")
            actual_hansi = run_value(run, "hansi_font")
            run_observed = {"ascii_font": actual_ascii, "hansi_font": actual_hansi}
            if run_unresolved:
                run_observed["unresolved_formatting_fields"] = sorted(run_unresolved)
            observed["font_runs"].append(run_observed)
            observed.setdefault("ascii_font", actual_ascii)
            observed.setdefault("hansi_font", actual_hansi)
            if (
                "ascii_font" not in run_unresolved
                and not _font_equal(actual_ascii, requirement.get("ascii_font"))
            ):
                mismatches.setdefault("ascii_font", {"observed": actual_ascii, "expected": requirement.get("ascii_font")})
            if (
                "hansi_font" not in run_unresolved
                and not _font_equal(actual_hansi, requirement.get("hansi_font"))
            ):
                mismatches.setdefault("hansi_font", {"observed": actual_hansi, "expected": requirement.get("hansi_font")})
        unresolved_fields = sorted(unresolved_field_set)
        if unresolved_fields:
            observed["unresolved_formatting_fields"] = unresolved_fields
        target = paragraph_target(paragraph, ctx.role(paragraph))
        if scope != "body":
            target["scope"] = scope
            for key in (
                "table_id",
                "table_index",
                "cell_id",
                "cell_index",
                "note_id",
                "footnote_id",
                "endnote_id",
            ):
                if paragraph.get(key) is not None:
                    target[key] = paragraph[key]
        if mismatches:
            results.append(finding(rule, rule_severity(rule), "英文字母或数字字体不符合规则要求。", target=target, paragraph=paragraph, observed=observed, confidence=confidence))
        elif unresolved_fields:
            results.append(
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "英文字体包含无法解析的 theme evidence，当前需人工复核。",
                    target=target,
                    paragraph=paragraph,
                    observed=observed,
                    confidence=confidence,
                )
            )
        else:
            results.append(finding(rule, "PASS", "英文字母和数字字体符合规则。", target=target, paragraph=paragraph, observed=observed, confidence=confidence))
    return results


def _append_latin_target(
    targets: list[tuple[Mapping[str, Any], float, str]],
    seen_ids: set[tuple[str, str]],
    paragraph: Mapping[str, Any],
    confidence: float,
    scope: str,
) -> None:
    paragraph_id = str(paragraph.get("id", ""))
    # Inspector IDs are globally stable across body/table/note parts.  Use the
    # ID alone when available so a malformed producer cannot create duplicate
    # findings by exposing the same target through two scopes.
    key = ("id", paragraph_id) if paragraph_id else (scope, f"anonymous-{len(targets)}")
    if key in seen_ids:
        return
    seen_ids.add(key)
    targets.append((paragraph, confidence, scope))


def _font_equal(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual == expected
    return str(actual).strip().casefold() == str(expected).strip().casefold()


def _page_range(rule: Mapping[str, Any], ctx: RuleContext) -> list[dict[str, Any]]:
    requirement = expected_for(rule)
    separator = str(requirement.get("separator", ""))
    entries = _reference_entries(ctx)
    if not entries:
        return [finding(rule, "NOT_APPLICABLE", "未识别到参考文献条目。", target=ctx.document_target)]
    pattern = re.compile(r"(?<!\d)\d+\s*([\-‐‑‒–—―])\s*\d+(?!\d)")
    results: list[dict[str, Any]] = []
    for paragraph in entries:
        text = text_of(paragraph)
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        parenthesized_ranges = _parenthesized_spans(text)
        candidates: list[dict[str, Any]] = []
        for match in matches:
            span = [match.start(), match.end()]
            before = text[: match.start()]
            after_last_colon = max(before.rfind(":"), before.rfind("："))
            page_marker = bool(re.search(r"(?:\bpp?\.?|\bpages?)\s*$", before, re.IGNORECASE))
            in_parenthesized = any(start <= match.start() and match.end() <= end for start, end in parenthesized_ranges)
            number_parts = re.split(r"\s*[-‐‑‒–—―]\s*", match.group(0))
            is_year_range = (
                len(number_parts) == 2
                and all(len(part) == 4 and part[:2] in {"19", "20"} for part in number_parts)
            )
            candidates.append(
                {
                    "span": span,
                    "observed_span": match.group(0),
                    "separator": match.group(1),
                    "context": text[max(0, match.start() - 24) : min(len(text), match.end() + 24)],
                    "in_parenthesized_range": in_parenthesized,
                    "likely_year_range": is_year_range,
                    "after_last_colon": bool(after_last_colon >= 0 and match.start() > after_last_colon),
                    "after_page_marker": page_marker,
                    "reason": (
                        "inside_parenthesized_range"
                        if in_parenthesized
                        else "likely_publication_year_range"
                        if is_year_range
                        else "candidate_after_page_marker"
                        if page_marker
                        else "candidate_after_colon"
                        if after_last_colon >= 0 and match.start() > after_last_colon
                        else "unmarked_numeric_range"
                    ),
                }
            )
        eligible = [item for item in candidates if not item["in_parenthesized_range"] and not item["likely_year_range"]]
        if not eligible:
            observed = {
                "candidates": candidates,
                "selection_reason": "all numeric ranges are parenthesized issue/volume or likely publication-year candidates; pages field is not observable",
            }
            results.append(finding(rule, "MANUAL_REVIEW", "检测到疑似卷期范围，但无法可靠定位页码字段。", paragraph=paragraph, observed=observed))
            continue
        marked = [item for item in eligible if item["after_last_colon"] or item["after_page_marker"]]
        if marked:
            selected = max(marked, key=lambda item: item["span"][0])
            reason = "selected the last non-parenthesized range after the final colon or page marker"
        elif len(eligible) == 1:
            selected = eligible[0]
            reason = "single non-parenthesized numeric range treated as a bare page range"
        else:
            observed = {
                "candidates": candidates,
                "selection_reason": "multiple non-parenthesized ranges lack a colon/page marker boundary",
            }
            results.append(finding(rule, "MANUAL_REVIEW", "检测到多个数字范围，但无法可靠判断哪一处是页码。", paragraph=paragraph, observed=observed))
            continue
        for item in candidates:
            item["selected"] = item is selected
        observed = {
            "separator": selected["separator"],
            "selected_span": selected["observed_span"],
            "selected_span_start": selected["span"][0],
            "selected_span_end": selected["span"][1],
            "selection_reason": reason,
            "candidates": candidates,
        }
        if selected["separator"] != separator:
            results.append(finding(rule, rule_severity(rule), "参考文献页码范围分隔符不符合规则。", paragraph=paragraph, observed=observed))
        else:
            results.append(finding(rule, "PASS", "参考文献页码范围分隔符符合规则。", paragraph=paragraph, observed=observed))
    return results or [finding(rule, "NOT_APPLICABLE", "参考文献中未检测到页码范围。", target=ctx.document_target)]


def _parenthesized_spans(text: str) -> list[tuple[int, int]]:
    """Return simple nested-free ASCII/fullwidth parenthesized spans."""

    spans: list[tuple[int, int]] = []
    for opening, closing in (("(", ")"), ("（", "）")):
        start = 0
        while True:
            left = text.find(opening, start)
            if left < 0:
                break
            right = text.find(closing, left + 1)
            if right < 0:
                break
            spans.append((left, right + 1))
            start = right + 1
    return sorted(spans)


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
            target = dict(paragraph)
            _attach_table_relation_evidence(ctx, target, relation_key="font_size_relation_to_table" if notes else "font_size_relation_to_body")
            result.append(target)
    if notes:
        result.extend(_inspector_table_note_targets(ctx, result))
    return result


_UNKNOWN_RELATION_VALUES = {
    "",
    "unknown",
    "mixed",
    "ambiguous",
    "unavailable",
    "not_checked",
    "not-checked",
    "missing",
    "insufficient_evidence",
}


def _normalized_relation(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("relation", "value", "status", "classification", "result"):
            if value.get(key) is not None:
                return _normalized_relation(value.get(key))
        return None
    if value is None:
        return None
    return str(value).strip().casefold().replace("-", "_").replace(" ", "_")


def _comparison_relation(comparison: Any, relation_key: str) -> tuple[Any, dict[str, Any] | None]:
    if not isinstance(comparison, Mapping):
        return None, None
    direct = comparison.get(relation_key)
    if direct is not None:
        return direct, dict(comparison)
    aliases = {
        "font_size_relation_to_body": ("body_relation", "relation_to_body", "body", "table_to_body"),
        "font_size_relation_to_table": ("table_relation", "relation_to_table", "note_to_table", "note"),
    }
    for key in aliases.get(relation_key, ()):
        if comparison.get(key) is not None:
            value = comparison.get(key)
            if isinstance(value, Mapping):
                nested = value.get("relation") or value.get("value") or value.get("status")
                return nested if nested is not None else value, dict(comparison)
            return value, dict(comparison)
    target_pt = _numeric(comparison.get("target_pt"))
    baseline_pt = _numeric(comparison.get("baseline_pt"))
    if target_pt is None or baseline_pt is None:
        return None, dict(comparison) if comparison else None
    if comparison.get("mixed") is True or comparison.get("mixed_runs") is True or comparison.get("status") in {"mixed", "unknown"}:
        return "mixed", dict(comparison)
    if abs(target_pt - baseline_pt) < 0.01:
        return "same", dict(comparison)
    return ("smaller" if target_pt < baseline_pt else "larger"), dict(comparison)


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _table_by_target(ctx: RuleContext, target: Mapping[str, Any]) -> Mapping[str, Any] | None:
    tables = ctx.inspection.get("tables", [])
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        return None
    table_id = target.get("table_id")
    table_index = target.get("table_index")
    for index, table in enumerate(tables):
        if not isinstance(table, Mapping):
            continue
        if table_id is not None and str(table.get("id", "")) == str(table_id):
            return table
        if table_index is not None and index == table_index:
            return table
    return None


def _attach_table_relation_evidence(ctx: RuleContext, target: dict[str, Any], *, relation_key: str) -> None:
    """Attach only explicit Inspector comparison evidence to a table target."""

    direct = paragraph_value(target, relation_key)
    table = _table_by_target(ctx, target)
    comparisons: list[Mapping[str, Any]] = []
    if table is not None:
        comparison_keys = (
            ("comparison", "font_size_comparison", "size_comparison", "comparison_evidence")
            if relation_key == "font_size_relation_to_body"
            else ("note_comparison", "table_note_comparison", "note_font_size_comparison")
        )
        for key in comparison_keys:
            value = table.get(key)
            if isinstance(value, Mapping):
                comparisons.append(value)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                comparisons.extend(item for item in value if isinstance(item, Mapping))
        for key in (relation_key, "font_size_relation", "relation"):
            if direct is None and table.get(key) is not None:
                direct = table.get(key)
    if direct is None:
        for comparison in comparisons:
            relation, evidence = _comparison_relation(comparison, relation_key)
            if relation is not None:
                direct = relation
                if evidence is not None:
                    target["font_size_comparison"] = evidence
                break
    if direct is not None:
        target[relation_key] = direct
    if comparisons and "font_size_comparison" not in target:
        target["font_size_comparison"] = dict(comparisons[0])
    comparison = target.get("font_size_comparison")
    if isinstance(comparison, Mapping):
        relation, _ = _comparison_relation(comparison, relation_key)
        if relation is not None and target.get(relation_key) is None:
            target[relation_key] = relation
        normalized = _normalized_relation(relation)
        if normalized in _UNKNOWN_RELATION_VALUES:
            target["comparison_status"] = normalized or "unknown"


def _table_finding_target(target: Mapping[str, Any], ctx: RuleContext) -> dict[str, Any]:
    """Preserve the table/cell identity on findings emitted for table text."""

    result = paragraph_target(target, ctx.role(target))
    for key in ("table_id", "table_index", "cell_id", "cell_index", "table_note_candidate"):
        if target.get(key) is not None:
            result[key] = target[key]
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
    body_blocks = ctx.inspection.get("body_blocks", [])
    body_block_positions: dict[str, int] = {}
    table_block_positions: dict[str, int] = {}
    if isinstance(body_blocks, Sequence) and not isinstance(body_blocks, (str, bytes)):
        for block_index, block in enumerate(body_blocks):
            if not isinstance(block, Mapping):
                continue
            block_id = block.get("id") or block.get("paragraph_id") or block.get("table_id")
            if block_id is None:
                continue
            kind = str(block.get("kind", "")).casefold()
            if kind == "table" or block.get("table_id") is not None:
                table_block_positions[str(block.get("table_id") or block_id)] = block_index
            elif kind in {"paragraph", "body_paragraph"}:
                body_block_positions[str(block_id)] = block_index
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
            table_block_index = table_block_positions.get(table_id)
            bound_block_index = body_block_positions.get(str(bound_id)) if bound_id is not None else None
            if table_block_index is not None and bound_block_index is not None:
                # The producer contract binds only the first non-empty body
                # paragraph immediately after this table.  Empty body blocks
                # may occur between the table and that paragraph, but a new
                # table/figure/heading closes the binding window.
                first_nonempty_index: int | None = None
                for block_index in range(table_block_index + 1, len(body_blocks)):
                    block = body_blocks[block_index]
                    if not isinstance(block, Mapping):
                        continue
                    block_kind = str(block.get("kind", "")).casefold()
                    block_id = block.get("id") or block.get("paragraph_id") or block.get("table_id")
                    block_text = text_of(block)
                    if block_kind in {"table", "figure", "image", "heading", "heading_level_1", "heading_level_2", "heading_level_3", "heading_level_4"}:
                        break
                    if block_kind in {"paragraph", "body_paragraph"} and block_text.strip():
                        first_nonempty_index = block_index
                        break
                    if block_id is not None and str(block_id) == str(bound_id):
                        first_nonempty_index = block_index
                        break
                if first_nonempty_index is None or bound_block_index != first_nonempty_index:
                    continue
                candidate_distance = candidate.get("distance", candidate.get("body_distance"))
                if candidate_distance is not None:
                    try:
                        if int(candidate_distance) != bound_block_index - table_block_index:
                            continue
                    except (TypeError, ValueError):
                        continue
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
            target.setdefault("in_table", bool(target.get("in_table", False)))
            target["table_note_binding"] = {
                "source": "inspector.note_candidates",
                "table_id": table_id,
                "paragraph_id": target.get("id"),
                "distance": candidate.get("distance", candidate.get("body_distance", 1)),
                "body_block_index": bound_block_index,
                "table_block_index": table_block_index,
                "reason": candidate.get("reason") or candidate.get("binding_reason") or "first_nonempty_paragraph_after_table",
            }
            for key in ("formatting", "effective_formatting", "formatting_evidence", "runs"):
                if key in candidate and key not in target:
                    target[key] = candidate[key]
            for key in (
                "font_size_relation_to_body",
                "font_size_relation_to_table",
                "font_size_comparison",
                "comparison",
                "comparison_evidence",
                "comparison_status",
            ):
                if key in candidate and key not in target:
                    target[key] = candidate[key]
            _attach_table_relation_evidence(
                ctx,
                target,
                relation_key="font_size_relation_to_table",
            )
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
    relation_key = "font_size_relation_to_table" if notes else "font_size_relation_to_body"
    requirement = expected_for(rule)
    relation_required = relation_key in requirement
    results: list[dict[str, Any]] = []
    for target in targets:
        relation = paragraph_value(target, relation_key)
        normalized = _normalized_relation(relation)
        observed = observed_formatting(target)
        for key in ("table_note_binding", "font_size_comparison", "table_id", "table_note_candidate"):
            if target.get(key) is not None:
                observed[key] = target[key]
        if relation is not None:
            observed[relation_key] = relation
        if relation_required and (
            normalized in _UNKNOWN_RELATION_VALUES
            or target.get("comparison_status") in _UNKNOWN_RELATION_VALUES
        ):
            observed["comparison_status"] = normalized or target.get("comparison_status") or "unknown"
            if target.get("font_size_comparison") is not None:
                observed["font_size_comparison"] = target["font_size_comparison"]
            results.append(
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "表格字号比较证据显示混合/未知状态，当前需人工复核。",
                    paragraph=target,
                    target=_table_finding_target(target, ctx),
                    observed=observed,
                    confidence=0.80,
                )
            )
            continue
        missing_fields = _missing_formatting_fields(target, requirement) if notes else []
        if missing_fields:
            observed["missing_formatting_evidence"] = missing_fields
            results.append(
                finding(
                    rule,
                    "MANUAL_REVIEW",
                    "表格目标缺少规则要求的字体证据，当前需人工复核。",
                    paragraph=target,
                    target=_table_finding_target(target, ctx),
                    observed=observed,
                    confidence=0.80,
                )
            )
            continue
        checked = check_paragraph_format(rule, target, role=ctx.role(target))
        checked = _mark_unresolved_formatting(checked, target, requirement)
        if checked:
            if checked.get("unchecked"):
                results.append(
                    finding(
                        rule,
                        "MANUAL_REVIEW",
                        "表格字号缺少 Inspector 产生的可验证比较关系，当前需人工复核。",
                        paragraph=target,
                        target=_table_finding_target(target, ctx),
                        observed=checked["observed"],
                        confidence=0.80,
                    )
                )
            else:
                results.append(
                    finding(
                        rule,
                        rule_severity(rule),
                        mismatch_message(rule, checked["mismatches"]),
                        paragraph=target,
                        target=_table_finding_target(target, ctx),
                        observed=checked["observed"],
                    )
                )
        else:
            results.append(
                finding(
                    rule,
                    "PASS",
                    f"{rule.get('target', '目标')}已满足规则要求。",
                    paragraph=target,
                    target=_table_finding_target(target, ctx),
                    observed=observed or {"formatting": True},
                )
            )
    return results


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
