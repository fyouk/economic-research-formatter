"""Deterministic, explainable semantic roles for Inspector dictionaries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import re
from typing import Any

from .patterns import (
    ABSTRACT_RE,
    CHAPTER_HEADING_RE,
    CHINESE_LEVEL_1_RE,
    CHINESE_LEVEL_2_RE,
    DECIMAL_HEADING_RE,
    FIGURE_CAPTION_RE,
    KEYWORDS_RE,
    ONE_DOT_HEADING_RE,
    PAREN_LEVEL_RE,
    REFERENCE_HEADING_RE,
    TABLE_CAPTION_RE,
    TITLE_MARKER_RE,
    clean_text,
    is_probable_reference_text,
    lower_style,
)


_SUPPORTED_ROLES = {
    "title",
    "author_name",
    "author_information",
    "abstract_heading",
    "abstract_text",
    "keywords",
    "toc",
    "heading_level_1",
    "heading_level_2",
    "heading_level_3",
    "heading_level_4",
    "body_text",
    "equation",
    "equation_where_paragraph",
    "table_caption",
    "table_note",
    "figure_caption",
    "reference_heading",
    "reference_entry",
    "ordinary_footnote",
    "unknown",
}


def _text(paragraph: Mapping[str, Any]) -> str:
    value = paragraph.get("text")
    if isinstance(value, str) and value:
        return clean_text(value)
    return clean_text(paragraph.get("text_preview", ""))


def _preview(paragraph: Mapping[str, Any], text: str) -> str:
    value = paragraph.get("text_preview")
    if isinstance(value, str):
        return value[:80]
    return text[:80]


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _in_toc(paragraph: Mapping[str, Any]) -> bool:
    if bool(paragraph.get("in_toc")) or bool(paragraph.get("is_toc")):
        return True
    toc = paragraph.get("toc")
    if isinstance(toc, Mapping):
        return bool(toc.get("is_toc") or toc.get("in_toc") or toc.get("field"))
    fields = paragraph.get("fields")
    if isinstance(fields, Mapping):
        names = fields.get("types") or fields.get("names") or fields.get("field_types")
    else:
        names = fields
    if isinstance(names, str):
        names = [names]
    if isinstance(names, Sequence) and not isinstance(names, (str, bytes)):
        return any("toc" in str(name).casefold() for name in names)
    return False


def _field_names(paragraph: Mapping[str, Any]) -> set[str]:
    names: list[object] = []
    for key in ("field", "field_type", "field_name", "field_instruction"):
        if paragraph.get(key) is not None:
            names.append(paragraph[key])
    fields = paragraph.get("fields")
    if isinstance(fields, Mapping):
        for key in ("types", "names", "field_types", "instructions"):
            value = fields.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                names.extend(value)
            elif value is not None:
                names.append(value)
    elif isinstance(fields, Sequence) and not isinstance(fields, (str, bytes)):
        names.extend(fields)
    return {str(name).casefold() for name in names}


def _numbering(paragraph: Mapping[str, Any]) -> Mapping[str, Any]:
    value: object = paragraph.get("numbering")
    if not isinstance(value, Mapping):
        value = paragraph.get("numPr")
    result = dict(value) if isinstance(value, Mapping) else {}
    nested = result.get("numPr")
    if isinstance(nested, Mapping):
        merged = dict(nested)
        merged.update({key: value for key, value in result.items() if key != "numPr"})
        result = merged
    return result


def _numbering_level(paragraph: Mapping[str, Any]) -> int | None:
    numbering = _numbering(paragraph)
    for key in ("ilvl", "level", "outline_level"):
        value = numbering.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _role_hint(paragraph: Mapping[str, Any]) -> str | None:
    value = paragraph.get("role_hint") or paragraph.get("role")
    if isinstance(value, Mapping):
        value = value.get("role") or value.get("name")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value in _SUPPORTED_ROLES else None


def _style_heading_level(style: object) -> int | None:
    value = lower_style(style)
    if not value:
        return None
    match = re.search(r"(?:heading|标题|heading[-_ ]*)([1-4])\b", value)
    if match:
        return int(match.group(1))
    return None


def _has_equation(paragraph: Mapping[str, Any], equation_ids: set[str]) -> bool:
    paragraph_id = str(paragraph.get("id", ""))
    if paragraph_id and paragraph_id in equation_ids:
        return True
    for key in ("has_equation", "contains_omath", "has_omml", "omath", "equation"):
        value = paragraph.get(key)
        if isinstance(value, Mapping):
            if value.get("count") or value.get("present") or value.get("is_omml"):
                return True
        elif bool(value):
            return True
    equations = paragraph.get("equations")
    if isinstance(equations, Sequence) and not isinstance(equations, (str, bytes)):
        return bool(equations)
    runs = paragraph.get("runs")
    if isinstance(runs, Sequence) and any(_mapping(run).get("is_equation") for run in runs):
        return True
    return False


def _is_table_paragraph(paragraph: Mapping[str, Any]) -> bool:
    return bool(
        paragraph.get("in_table")
        or paragraph.get("table_id") is not None
        or paragraph.get("cell_id") is not None
        or paragraph.get("container") == "table"
    )


def _is_footnote_paragraph(paragraph: Mapping[str, Any]) -> bool:
    kind = str(paragraph.get("kind", "")).casefold()
    return bool(
        paragraph.get("in_footnote")
        or paragraph.get("footnote_id") is not None
        or kind in {"footnote", "ordinary_footnote"}
    )


_EQUATION_WHERE_RE = re.compile(r"^\s*其中(?=$|\s|[：:，,、；;])")
_AUTHOR_INFORMATION_RE = re.compile(
    r"(?:电子信箱|电子邮箱|通讯作者|作者简介|作者信息|作者单位|作者联系)"
)


def _is_equation_where_text(text: str) -> bool:
    """Return whether text has the equation-explanation trigger boundary.

    The punctuation look-ahead is intentionally narrow.  It accepts common
    Chinese punctuation (including ``其中，``) while avoiding a substring
    match in ordinary prose such as ``其中位数``.
    """

    return bool(_EQUATION_WHERE_RE.match(text))


def _heading_role(text: str, paragraph: Mapping[str, Any]) -> tuple[str | None, list[str], float]:
    """Return a heading role based on shape, style, and numbering metadata."""

    if not text.strip():
        return None, [], 0.0
    style_level = _style_heading_level(paragraph.get("style_name") or paragraph.get("style"))
    numbering_level = _numbering_level(paragraph)
    # Visible numbering is stronger semantic evidence than a generic Word
    # style.  Real-world thesis documents often apply Heading 1 to every
    # numbered heading; letting the style win would collapse 1.1/1.1.1 into
    # level one and make hierarchy checks meaningless.
    if CHAPTER_HEADING_RE.match(text) or CHINESE_LEVEL_1_RE.match(text):
        evidence = ["regex=chapter_heading" if CHAPTER_HEADING_RE.match(text) else "regex=chinese_level_1"]
        if numbering_level is not None:
            evidence.append(f"numbering_ilvl={numbering_level}")
        return "heading_level_1", evidence, 0.96
    if CHINESE_LEVEL_2_RE.match(text):
        return "heading_level_2", ["regex=chinese_level_2"], 0.94
    if DECIMAL_HEADING_RE.match(text):
        match = DECIMAL_HEADING_RE.match(text)
        assert match is not None
        level = min(4, max(2, len(match.group(1).split("."))))
        evidence = ["regex=thesis_style_numbering", f"decimal_depth={level}"]
        if numbering_level is not None:
            evidence.append(f"numbering_ilvl={numbering_level}")
        return f"heading_level_{level}", evidence, 0.94
    if ONE_DOT_HEADING_RE.match(text):
        return "heading_level_3", ["regex=one_dot_heading"], 0.88
    if PAREN_LEVEL_RE.match(text):
        return "heading_level_4", ["regex=parenthesized_number_heading"], 0.88
    if style_level is not None:
        return (
            f"heading_level_{style_level}",
            [f"paragraph_style_heading_{style_level}"],
            0.97,
        )
    # Numbering without a visible marker is only useful when another heading
    # signal is present.  Body prose in imported theses may carry a list
    # ``numPr`` (sometimes even a resolved Chinese counter); treating that as a
    # heading would inflate chapter counts and create false hierarchy errors.
    if numbering_level is not None and numbering_level in range(4) and (style_level is not None or paragraph.get("outline_level") is not None) and len(text) <= 120:
        if numbering_level == 0:
            return "heading_level_1", ["numbering_ilvl=0"], 0.84
        return f"heading_level_{min(4, numbering_level + 1)}", [f"numbering_ilvl={numbering_level}"], 0.82
    return None, [], 0.0


def _paragraph_id(paragraph: Mapping[str, Any], fallback_index: int) -> str:
    value = paragraph.get("id")
    if value is not None and str(value):
        return str(value)
    return f"p-{fallback_index:06d}"


def _classify_paragraphs(inspection: Mapping[str, Any]) -> list[dict[str, Any]]:
    paragraphs = inspection.get("paragraphs", [])
    if not isinstance(paragraphs, Sequence) or isinstance(paragraphs, (str, bytes)):
        return []
    equation_info = inspection.get("equations")
    equation_ids: set[str] = set()
    if isinstance(equation_info, Mapping):
        values = equation_info.get("paragraph_ids", [])
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            equation_ids = {str(value) for value in values}

    items: list[dict[str, Any]] = []
    first_text_index: int | None = None
    abstract_open = False
    reference_open = False
    previous_real_equation = False

    for fallback_index, raw_paragraph in enumerate(paragraphs):
        paragraph = _mapping(raw_paragraph)
        paragraph_id = _paragraph_id(paragraph, fallback_index)
        text = _text(paragraph)
        toc = _in_toc(paragraph)
        role_hint = _role_hint(paragraph)
        evidence: list[str] = []
        confidence = 0.0
        role: str

        if text and first_text_index is None:
            first_text_index = fallback_index

        if toc:
            role, confidence = "toc", 0.99
            evidence.append("toc_field_or_marker")
        elif role_hint is not None:
            # A role hint for an equation explanation is meaningful only when
            # it follows an actual equation.  Inspector metadata can be
            # incomplete, so a generic ``其中`` paragraph cannot become an
            # equation target merely because a hint was copied onto it.
            if role_hint == "equation_where_paragraph" and (
                not previous_real_equation or not _is_equation_where_text(text)
            ):
                role, confidence = "body_text", 0.70
                evidence.append("equation_where_without_previous_equation")
            else:
                role, confidence = role_hint, 0.99
                evidence.append("explicit_role_hint")
        elif _is_footnote_paragraph(paragraph):
            role, confidence = "ordinary_footnote", 0.99
            evidence.append("footnote_container")
        elif REFERENCE_HEADING_RE.match(text):
            role, confidence = "reference_heading", 0.99
            evidence.append("regex=reference_heading")
            reference_open = True
            abstract_open = False
        elif reference_open and text:
            # A reference section is a strong semantic boundary.  Require at
            # least one bibliographic signal unless Inspector explicitly marked
            # the paragraph as a reference; this keeps trailing blank/heading
            # paragraphs from becoming phantom entries.
            numbering = _numbering(paragraph)
            if numbering or paragraph.get("in_reference") or paragraph.get("reference_entry") or is_probable_reference_text(text):
                role, confidence = "reference_entry", 0.98
                evidence.append("after_reference_heading")
                if numbering:
                    evidence.append("automatic_numbering")
                if paragraph.get("in_reference") or paragraph.get("reference_entry"):
                    evidence.append("inspector_reference_marker")
            else:
                role, confidence = "body_text", 0.60
                evidence.append("after_reference_heading_without_entry_signal")
        elif ABSTRACT_RE.match(text):
            role, confidence = "abstract_heading", 0.99
            evidence.append("regex=abstract_heading")
            abstract_open = True
        elif KEYWORDS_RE.match(text):
            role, confidence = "keywords", 0.99
            evidence.append("regex=keywords")
            abstract_open = False
        elif abstract_open and text:
            # A malformed/abbreviated manuscript may omit the keyword line.
            # Do not let that make every subsequent numbered heading look like
            # abstract prose; visible heading evidence closes the abstract
            # region as a safe boundary.
            heading_role, heading_evidence, heading_confidence = _heading_role(text, paragraph)
            if heading_role:
                abstract_open = False
                role, evidence, confidence = heading_role, heading_evidence, heading_confidence
            else:
                role, confidence = "abstract_text", 0.95
                evidence.append("between_abstract_and_keywords")
        elif first_text_index == fallback_index and (TITLE_MARKER_RE.match(text) or _style_heading_level(paragraph.get("style_name") or paragraph.get("style")) is None and "title" in lower_style(paragraph.get("style_name") or paragraph.get("style"))):
            role, confidence = "title", 0.99
            evidence.append("regex=title_marker" if TITLE_MARKER_RE.match(text) else "paragraph_style=title")
        elif FIGURE_CAPTION_RE.match(text):
            role, confidence = "figure_caption", 0.97
            evidence.append("regex=figure_caption")
        elif TABLE_CAPTION_RE.match(text):
            role, confidence = "table_caption", 0.97
            evidence.append("regex=table_caption")
        elif _is_table_paragraph(paragraph) and re.match(r"^\s*注\s*[：:]", text):
            role, confidence = "table_note", 0.97
            evidence.append("table_container_and_note_marker")
        elif _has_equation(paragraph, equation_ids):
            role, confidence = "equation", 0.99
            evidence.append("omml_or_equation_marker")
        elif _is_equation_where_text(text) and previous_real_equation:
            role, confidence = "equation_where_paragraph", 0.94
            evidence.append("after_equation_and_trigger_text=其中")
        else:
            heading_role, heading_evidence, heading_confidence = _heading_role(text, paragraph)
            if heading_role:
                role, evidence, confidence = heading_role, heading_evidence, heading_confidence
            elif re.search(r"(?:电子信箱|电子邮箱|通讯作者|作者简介|作者信息|作者单位)", text) and not abstract_open:
                role, confidence = "author_information", 0.78
                evidence.append("author_information_marker")
            elif "author" in lower_style(paragraph.get("style_name") or paragraph.get("style")) or "作者" in clean_text(paragraph.get("style_name") or paragraph.get("style")):
                role, confidence = "author_name", 0.80
                evidence.append("paragraph_style=author")
            else:
                role, confidence = ("body_text", 0.70) if text else ("unknown", 0.20)
                evidence.append("default_text_role" if text else "empty_paragraph")

        previous_real_equation = _has_equation(paragraph, equation_ids)
        index_value = paragraph.get("index", fallback_index)
        try:
            index_value = int(index_value)
        except (TypeError, ValueError):
            index_value = fallback_index
        item: dict[str, Any] = {
            "source_id": paragraph_id,
            "kind": "paragraph",
            "id": paragraph_id,
            "index": index_value,
            "role": role,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "evidence": evidence,
            "text_preview": _preview(paragraph, text),
            "in_toc": toc,
        }
        numbering = _numbering(paragraph)
        if numbering:
            item["numbering"] = dict(numbering)
        items.append(item)
    return items


def _classify_note_items(inspection: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Classify note-part records separately from body paragraphs.

    The Inspector currently exposes note text but not effective formatting.
    Keeping these records in a separate collection lets the linter attach a
    review finding to the actual footnote without pretending it is a body
    paragraph or treating missing formatting as a clean result.
    """

    notes = inspection.get("notes", {})
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

    result: list[dict[str, Any]] = []
    for index, raw_item in enumerate(values):
        item = _mapping(raw_item)
        if not item:
            continue
        note_id = item.get("id", index)
        source_id = str(item.get("source_id") or f"footnote-{note_id}")
        text = _text(item)
        nested_paragraphs = item.get("paragraphs", [])
        if isinstance(nested_paragraphs, Sequence) and not isinstance(nested_paragraphs, (str, bytes)):
            nested_text = "".join(_text(_mapping(value)) for value in nested_paragraphs)
            if nested_text and nested_text not in text:
                text = f"{text}{nested_text}"
        role_hint = (
            item.get("note_role")
            or item.get("note_type")
            or item.get("role_hint")
            or item.get("role")
        )
        if isinstance(role_hint, Mapping):
            role_hint = role_hint.get("role") or role_hint.get("name")
        role_hint = str(role_hint).strip() if isinstance(role_hint, str) else ""
        if role_hint in {"author_information", "ordinary_footnote", "unknown"}:
            role = role_hint
            confidence = 0.99 if role_hint != "unknown" else 0.20
            evidence = ["explicit_note_role"]
        elif _AUTHOR_INFORMATION_RE.search(text):
            role = "author_information"
            confidence = 0.78
            evidence = ["author_information_marker", "footnote_item"]
        else:
            role = "ordinary_footnote"
            confidence = 0.90
            evidence = ["footnote_item"]
        record: dict[str, Any] = {
            "source_id": source_id,
            "kind": "footnote",
            "id": source_id,
            "footnote_id": note_id,
            "index": item.get("index", index),
            "role": role,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "evidence": evidence,
            "text_preview": _preview(item, text),
        }
        # Formatting evidence, when a future Inspector supplies it, is kept
        # on the note target so formatter checks can use it directly.
        for key in ("formatting", "effective_formatting", "formatting_evidence", "runs"):
            if key in item:
                record[key] = item[key]
        result.append(record)
    return result


def classify_inspection(inspection: Mapping[str, Any] | None) -> dict[str, Any]:
    """Classify an Inspector dictionary without modifying it.

    The output contains exactly one paragraph item per input paragraph.  This
    makes source IDs stable and lets the linter attach findings to the original
    paragraph while retaining the explicit evidence used for the role.
    """

    if not isinstance(inspection, Mapping):
        inspection = {}
    items = _classify_paragraphs(inspection)
    note_items = _classify_note_items(inspection)
    counts = Counter(item["role"] for item in items)
    return {
        "schema_version": "1.0",
        "items": items,
        "note_items": note_items,
        "summary": {
            "paragraph_count": len(items),
            "note_count": len(note_items),
            "by_role": {key: counts[key] for key in sorted(counts)},
            "note_by_role": dict(sorted(Counter(item["role"] for item in note_items).items())),
        },
    }


__all__ = ["classify_inspection"]
