"""Deterministic, read-only DOCX/OOXML inspection.

``inspect_docx`` intentionally returns JSON-compatible dictionaries instead of
python-docx proxy objects.  This makes reports stable across processes and
lets the classifier/linter consume exactly the same evidence that a user can
inspect in ``inspection.json``.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from lxml import etree

from .. import __version__
from ..classify.classifier import classify_inspection
from ..models.formatting import cn_font_size_index
from ..models.inspection import DocxInspectionError, Inspection
from .equations import inspect_equations
from .fields import fields_in_paragraph, inspect_fields
from .images import inspect_images
from .numbering import NumberingResolver
from .notes import inspect_notes, note_properties_for_section
from .package import DocxPackage, R_NS, W_NS, local_name, qname
from .styles import StyleResolver


_MAX_PREVIEW = 80
_DOC_PROPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
_CORE_PROPS_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_LOCAL_PATH_RE = re.compile(
    r"(?:file://|file:)?(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|private|var|tmp|Volumes|mnt|opt|srv)(?:/|$))[^\s\"']*",
    re.I,
)


def _preview(value: str, _include_text: bool) -> str:
    """Return a bounded preview independently of full-text opt-in.

    ``include_text`` controls whether a separate ``text`` field is emitted;
    it must never widen the privacy-conscious preview field itself.
    """

    return value[:_MAX_PREVIEW]


def _redact_path(value: str) -> str:
    return _LOCAL_PATH_RE.sub("[local-path]", value)


def _bounded_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _twips(value: str | None) -> int | None:
    return _int(value)


def _visible_text(element: etree._Element) -> str:
    """Extract rendered text, excluding field instructions and deleted text."""
    chunks: list[str] = []
    for node in element.iter():
        name = local_name(node)
        if name in {"instrText", "delText", "fldChar", "commentRangeStart", "commentRangeEnd", "bookmarkStart", "bookmarkEnd"}:
            continue
        if node.tag == qname(W_NS, "t") and node.text:
            chunks.append(node.text)
        elif node.tag in {qname(W_NS, "tab")}:
            chunks.append("\t")
        elif node.tag in {qname(W_NS, "br"), qname(W_NS, "cr")}:
            chunks.append("\n")
    return "".join(chunks)


def _deleted_text(element: etree._Element) -> str:
    return "".join(node.text or "" for node in element.iter(qname(W_NS, "delText")))


def _run_elements(paragraph: etree._Element) -> list[etree._Element]:
    return list(paragraph.iter(qname(W_NS, "r")))


def _literal_marker(text: str) -> str | None:
    value = text.strip()
    if len(value) == 1 and not value.isalnum() and not value.isspace():
        return value
    return None


def _marker_near_reference(
    reference: etree._Element,
    run: etree._Element | None,
    run_index: int | None,
    runs: list[etree._Element],
) -> tuple[str | None, str | None, int | None]:
    """Return a literal marker, its relative order, and run distance.

    A literal glyph is associated only when it is the sole rendered content
    immediately before/after the reference in the same run or neighboring
    run.  A star elsewhere in a title is deliberately not treated as a
    footnote marker.
    """

    if run is None or run_index is None:
        return None, None, None
    children = [child for child in run if child.tag != qname(W_NS, "rPr")]
    try:
        child_index = children.index(reference)
    except ValueError:
        child_index = -1
    if child_index >= 0:
        before = "".join(_visible_text(child) for child in children[:child_index]).strip()
        after = "".join(_visible_text(child) for child in children[child_index + 1 :]).strip()
        before_marker = _literal_marker(before) if before else None
        after_marker = _literal_marker(after) if after else None
        if after_marker is not None and before_marker is None:
            return after_marker, "after", 0
        if before_marker is not None and after_marker is None:
            return before_marker, "before", 0

    for neighbor_index, order in ((run_index - 1, "before"), (run_index + 1, "after")):
        if not 0 <= neighbor_index < len(runs):
            continue
        neighbor = runs[neighbor_index]
        # A neighboring reference is not a literal marker candidate, even
        # when its rendered text happens to be empty.
        if list(neighbor.iter(qname(W_NS, "footnoteReference"))) or list(neighbor.iter(qname(W_NS, "endnoteReference"))):
            continue
        marker = _literal_marker(_visible_text(neighbor))
        if marker is not None:
            return marker, order, 1
    return None, None, None


def _note_reference_evidence(
    paragraph: etree._Element,
    note_collections: dict[str, dict[str, Any]] | None = None,
    section_index: int = 0,
) -> dict[str, Any]:
    """Return note-reference IDs plus stable marker evidence for a paragraph."""

    references: list[dict[str, Any]] = []
    runs = _run_elements(paragraph)
    run_positions = {id(run): index for index, run in enumerate(runs)}
    note_collections = note_collections or {}
    for tag, kind in (("footnoteReference", "footnote"), ("endnoteReference", "endnote")):
        for reference in paragraph.iter(qname(W_NS, tag)):
            if any(
                ancestor.tag in {qname(W_NS, "del"), qname(W_NS, "moveFrom")}
                for ancestor in reference.iterancestors()
            ):
                continue
            raw_id = reference.get(qname(W_NS, "id"))
            note_id = _int(raw_id)
            run = next((ancestor for ancestor in reference.iterancestors() if ancestor.tag == qname(W_NS, "r")), None)
            run_index = run_positions.get(id(run))
            custom_raw = reference.get(qname(W_NS, "customMarkFollows"))
            custom_mark_follows = custom_raw in {"1", "true", "on"}
            literal_marker, association_order, association_distance = _marker_near_reference(
                reference, run, run_index, runs
            )
            collection = note_collections.get(f"{kind}s", {})
            properties = note_properties_for_section(collection, section_index)
            number_format = str(properties.get("numFmt") or "decimal")
            if custom_mark_follows:
                if literal_marker == "*":
                    marker_type = "custom_mark"
                elif literal_marker is not None:
                    marker_type = "other_marker"
                else:
                    marker_type = "unknown_marker"
            elif literal_marker == "*":
                marker_type = "literal_star"
            elif literal_marker is not None:
                marker_type = "other_marker"
            elif number_format.casefold() in {"decimal", "upperroman", "lowerroman", "upperletter", "lowerletter"}:
                marker_type = "automatic_numbered"
            elif number_format:
                marker_type = "automatic_symbol"
            else:
                marker_type = "unknown_marker"
            references.append(
                {
                    "kind": kind,
                    "marker": tag,
                    "id": note_id,
                    "id_raw": raw_id,
                    "reference_id": note_id,
                    "run_index": run_index,
                    "run_order": run_index,
                    "custom_mark_follows": custom_mark_follows,
                    "custom_mark_follows_raw": custom_raw,
                    "marker_type": marker_type,
                    "marker_kind": marker_type,
                    "automatic": marker_type in {"automatic_numbered", "automatic_symbol"},
                    "automatic_numbered": marker_type == "automatic_numbered",
                    "is_custom_mark": marker_type == "custom_mark",
                    "marker_value": literal_marker,
                    "literal_marker": literal_marker,
                    "association": {
                        "associated": literal_marker is not None,
                        "order": association_order,
                        "run_distance": association_distance,
                    },
                    "section_index": properties.get("section_index"),
                    "document_wide_properties": deepcopy(properties.get("document_wide_properties", {})),
                    "section_override": deepcopy(properties.get("section_override", {})),
                    "effective_properties": deepcopy(properties.get("effective_properties", {})),
                    "property_evidence": deepcopy(properties.get("property_evidence", {})),
                    "note_properties": properties,
                }
            )
    references.sort(key=lambda item: (item["run_index"] if item["run_index"] is not None else 10**9, item["kind"]))
    footnote_ids = [item["id"] for item in references if item["kind"] == "footnote" and item["id"] is not None]
    endnote_ids = [item["id"] for item in references if item["kind"] == "endnote" and item["id"] is not None]
    return {
        "note_references": references,
        "footnote_references": footnote_ids,
        "footnote_refs": footnote_ids,
        "endnote_references": endnote_ids,
        "has_footnote_reference": bool(footnote_ids),
        "has_endnote_reference": bool(endnote_ids),
        "footnote_reference_evidence": [item for item in references if item["kind"] == "footnote"],
        "endnote_reference_evidence": [item for item in references if item["kind"] == "endnote"],
    }


def _property_bool(ppr: etree._Element, name: str) -> bool:
    return ppr.find(qname(W_NS, name)) is not None


def _paragraph_properties(ppr: etree._Element | None) -> dict[str, Any]:
    if ppr is None:
        ppr = etree.Element(qname(W_NS, "pPr"))
    alignment = ppr.find(qname(W_NS, "jc"))
    ind = ppr.find(qname(W_NS, "ind"))
    spacing = ppr.find(qname(W_NS, "spacing"))
    outline = ppr.find(qname(W_NS, "outlineLvl"))
    indents = {
        "left_twips": _twips(ind.get(qname(W_NS, "left"))) if ind is not None else None,
        "right_twips": _twips(ind.get(qname(W_NS, "right"))) if ind is not None else None,
        "first_line_twips": _twips(ind.get(qname(W_NS, "firstLine"))) if ind is not None else None,
        "hanging_twips": _twips(ind.get(qname(W_NS, "hanging"))) if ind is not None else None,
    }
    spacing_data = {
        "before_twips": _twips(spacing.get(qname(W_NS, "before"))) if spacing is not None else None,
        "after_twips": _twips(spacing.get(qname(W_NS, "after"))) if spacing is not None else None,
        "line_raw": _twips(spacing.get(qname(W_NS, "line"))) if spacing is not None else None,
        "line_rule": spacing.get(qname(W_NS, "lineRule")) if spacing is not None else None,
    }
    result = {
        "alignment": alignment.get(qname(W_NS, "val")) if alignment is not None else None,
        "indent": indents,
        "indents": indents,
        "spacing": spacing_data,
        "outline_level": _int(outline.get(qname(W_NS, "val"))) if outline is not None else None,
        "keep_with_next": _property_bool(ppr, "keepNext"),
        "keep_together": _property_bool(ppr, "keepLines"),
        "page_break_before": _property_bool(ppr, "pageBreakBefore"),
        "widow_control": _property_bool(ppr, "widowControl"),
    }
    return result


def _section_start_type(sect_pr: etree._Element) -> str:
    element = sect_pr.find(qname(W_NS, "type"))
    value = element.get(qname(W_NS, "val")) if element is not None else None
    return value or "nextPage"


def _section_record(package: DocxPackage, sect_pr: etree._Element, index: int) -> dict[str, Any]:
    page_size = sect_pr.find(qname(W_NS, "pgSz"))
    width = _twips(page_size.get(qname(W_NS, "w"))) if page_size is not None else None
    height = _twips(page_size.get(qname(W_NS, "h"))) if page_size is not None else None
    orientation_value = page_size.get(qname(W_NS, "orient")) if page_size is not None else None
    orientation = orientation_value or ("landscape" if width is not None and height is not None and width > height else "portrait")
    margins = sect_pr.find(qname(W_NS, "pgMar"))
    margin_data = {
        "top_twips": _twips(margins.get(qname(W_NS, "top"))) if margins is not None else None,
        "bottom_twips": _twips(margins.get(qname(W_NS, "bottom"))) if margins is not None else None,
        "left_twips": _twips(margins.get(qname(W_NS, "left"))) if margins is not None else None,
        "right_twips": _twips(margins.get(qname(W_NS, "right"))) if margins is not None else None,
        "header_twips": _twips(margins.get(qname(W_NS, "header"))) if margins is not None else None,
        "footer_twips": _twips(margins.get(qname(W_NS, "footer"))) if margins is not None else None,
        "gutter_twips": _twips(margins.get(qname(W_NS, "gutter"))) if margins is not None else None,
    }
    margin_data["points"] = {
        key.removesuffix("_twips"): value / 20 if isinstance(value, int) else None
        for key, value in margin_data.items()
        if key.endswith("_twips")
    }
    relationships = package.relationships("word/document.xml")
    headers: list[dict[str, Any]] = []
    footers: list[dict[str, Any]] = []
    for tag, target_list in (("headerReference", headers), ("footerReference", footers)):
        for reference in sect_pr.findall(qname(W_NS, tag)):
            rid = reference.get(qname(R_NS, "id"))
            relation = relationships.get(rid or "")
            target = package.resolve_target("word/document.xml", relation.target) if relation and relation.target_mode != "External" else relation.target if relation else None
            target_list.append({
                "type": reference.get(qname(W_NS, "type")),
                "relationship_id": rid,
                "part": target,
            })
    result = {
        "index": index,
        "orientation": orientation,
        "page_width_twips": width,
        "page_height_twips": height,
        "page_width_pt": width / 20 if width is not None else None,
        "page_height_pt": height / 20 if height is not None else None,
        "page_setup": {
            "width_twips": width,
            "height_twips": height,
            "width_pt": width / 20 if width is not None else None,
            "height_pt": height / 20 if height is not None else None,
            "orientation": orientation,
            "start_type": _section_start_type(sect_pr),
        },
        "margins": margin_data,
        "start_type": _section_start_type(sect_pr),
        "headers": headers,
        "footers": footers,
    }
    return result


def _section_elements(root: etree._Element) -> list[etree._Element]:
    body = root.find(qname(W_NS, "body"))
    if body is None:
        return []
    result: list[etree._Element] = []
    for paragraph in body.iter(qname(W_NS, "p")):
        if any(ancestor.tag == qname(W_NS, "tbl") for ancestor in paragraph.iterancestors()):
            continue
        sect_pr = paragraph.find(f"{qname(W_NS, 'pPr')}/{qname(W_NS, 'sectPr')}")
        if sect_pr is not None:
            result.append(sect_pr)
    final = body.find(qname(W_NS, "sectPr"))
    if final is not None:
        result.append(final)
    return result


def _body_paragraphs(body: etree._Element) -> list[etree._Element]:
    """Return body paragraphs, including paragraphs inside content controls.

    Generated tables of contents are commonly wrapped in ``w:sdt`` and are
    therefore not direct children of ``w:body``.  They are still body content
    and need stable IDs/TOC evidence.  Paragraphs nested in table cells are
    deliberately excluded because those are emitted under ``tables[*]``.
    """
    paragraphs: list[etree._Element] = []
    for paragraph in body.iter(qname(W_NS, "p")):
        if any(ancestor.tag == qname(W_NS, "tbl") for ancestor in paragraph.iterancestors()):
            continue
        paragraphs.append(paragraph)
    return paragraphs


def _body_tables(body: etree._Element) -> list[etree._Element]:
    """Return top-level body tables, including wrapper-contained tables.

    ``w:sdt`` and ``w:customXml`` are transparent body containers in Word.
    Walking the body rather than only its direct children finds those tables;
    filtering table ancestors prevents nested tables from being counted a
    second time as independent body tables.
    """

    return [
        table
        for table in body.iter(qname(W_NS, "tbl"))
        if not any(ancestor.tag == qname(W_NS, "tbl") for ancestor in table.iterancestors())
    ]


def _body_blocks(
    body: etree._Element,
    paragraph_ids: dict[int, str],
    table_ids: dict[int, str],
) -> list[dict[str, Any]]:
    """Serialize top-level body paragraphs/tables in true OOXML order.

    Word commonly wraps body content in ``w:sdt``/``w:customXml``.  Iterating
    the body tree and filtering only descendants of another table keeps those
    wrappers transparent while preventing nested tables/cell paragraphs from
    appearing as independent body blocks.
    """

    blocks: list[dict[str, Any]] = []
    for element in body.iter():
        if element.tag == qname(W_NS, "p"):
            if any(ancestor.tag == qname(W_NS, "tbl") for ancestor in element.iterancestors()):
                continue
            identifier = paragraph_ids.get(id(element))
            if identifier is not None:
                blocks.append({"kind": "paragraph", "id": identifier})
        elif element.tag == qname(W_NS, "tbl"):
            if any(ancestor.tag == qname(W_NS, "tbl") for ancestor in element.iterancestors()):
                continue
            identifier = table_ids.get(id(element))
            if identifier is not None:
                blocks.append({"kind": "table", "id": identifier})
    for index, block in enumerate(blocks):
        block["index"] = index
    return blocks


def _core_page_count(package: DocxPackage) -> int | None:
    root = package.xml("docProps/app.xml")
    if root is None:
        return None
    element = root.find(qname(_DOC_PROPS_NS, "Pages"))
    return _int(element.text if element is not None else None)


def _core_properties(package: DocxPackage, *, include_metadata: bool = False) -> dict[str, Any]:
    root = package.xml("docProps/core.xml")
    if root is None:
        return {}
    values: dict[str, str] = {}
    elements: dict[str, etree._Element] = {}
    for key, namespace in (("title", _DC_NS), ("subject", _DC_NS), ("creator", _DC_NS), ("description", _DC_NS), ("keywords", _CORE_PROPS_NS), ("last_modified_by", _CORE_PROPS_NS)):
        element = root.find(qname(namespace, key.replace("last_modified_by", "lastModifiedBy")))
        if element is not None:
            elements[key] = element
            values[key] = element.text or ""
    if include_metadata:
        return values
    # Compact reports are routinely copied into issues and shared with
    # collaborators.  Keep only presence/hash evidence for the two identity
    # fields and omit free-form title/subject/description/keywords entirely.
    redacted: dict[str, Any] = {}
    for key in ("creator", "last_modified_by"):
        if key not in elements:
            continue
        value = values[key]
        redacted[key] = {
            "present": True,
            "sha256": _bounded_hash(value),
        }
    return redacted


def _inspect_run(
    run: etree._Element,
    paragraph_index: int,
    run_index: int,
    paragraph_style_id: str | None,
    resolver: StyleResolver,
    include_text: bool,
    start: int,
    run_id_prefix: str | None = None,
) -> dict[str, Any]:
    text = _visible_text(run)
    raw_rpr = run.find(qname(W_NS, "rPr"))
    formatting = resolver.resolve_run(raw_rpr, paragraph_style_id)
    record: dict[str, Any] = {
        "id": (
            f"{run_id_prefix}-r-{run_index:04d}"
            if run_id_prefix
            else f"r-{paragraph_index:06d}-{run_index:04d}"
        ),
        "index": run_index,
        "start": start,
        "end": start + len(text),
        "text_preview": _preview(text, include_text),
        "formatting": formatting,
        "is_whitespace_only": bool(text) and not text.strip(),
        "has_field_char": run.find(qname(W_NS, "fldChar")) is not None,
    }
    if include_text:
        record["text"] = text
    deleted = _deleted_text(run)
    if deleted:
        record["deleted_text_preview"] = _preview(deleted, include_text)
        if include_text:
            record["deleted_text"] = deleted
    return record


def _inspect_paragraph(
    paragraph: etree._Element,
    index: int,
    stable_id: str,
    resolver: StyleResolver,
    numbering: NumberingResolver,
    include_text: bool,
    *,
    table_context: bool = False,
    table_id: str | None = None,
    in_toc_context: bool = False,
    note_collections: dict[str, dict[str, Any]] | None = None,
    section_index: int | None = None,
) -> dict[str, Any]:
    ppr = paragraph.find(qname(W_NS, "pPr"))
    style_id_element = ppr.find(qname(W_NS, "pStyle")) if ppr is not None else None
    style_id = style_id_element.get(qname(W_NS, "val")) if style_id_element is not None else None
    style_name = resolver.style_name(style_id)
    effective_style_id = style_id or resolver.default_style_id("paragraph")
    effective_style_name = resolver.style_name(effective_style_id)
    text = _visible_text(paragraph)
    fields = fields_in_paragraph(paragraph, include_text=include_text)
    field_types = [field["type"] for field in fields]
    style_lower = (style_name or style_id or "").lower()
    # A PAGEREF is also used by ordinary cross references.  It becomes TOC
    # evidence only when a TOC style or an already established TOC boundary
    # says that the paragraph belongs to the generated table of contents.
    has_toc_field = "TOC" in field_types
    is_toc = bool(
        has_toc_field
        or style_lower.replace(" ", "").startswith("toc")
        or in_toc_context
    )
    p_props = resolver.resolve_paragraph(ppr)
    effective_p_props = p_props["effective"]
    note_evidence = _note_reference_evidence(
        paragraph,
        note_collections,
        section_index=section_index or 0,
    )
    numpr = numbering.resolve(ppr)
    runs: list[dict[str, Any]] = []
    offset = 0
    for run_index, run in enumerate(_run_elements(paragraph)):
        record = _inspect_run(
            run,
            index,
            run_index,
            style_id,
            resolver,
            include_text,
            offset,
            run_id_prefix=stable_id if table_context else None,
        )
        runs.append(record)
        offset = record["end"]
    meaningful_run = next((run for run in runs if run.get("text_preview", "").strip()), None)
    paragraph_formatting = (
        {
            "raw": deepcopy(meaningful_run["formatting"]["raw"]),
            "effective": deepcopy(meaningful_run["formatting"]["effective"]),
            "source": deepcopy(meaningful_run["formatting"]["source"]),
            "mixed_runs": sum(bool(run.get("text_preview", "").strip()) for run in runs) > 1,
        }
        if meaningful_run is not None
        else {"raw": {}, "effective": {}, "source": {}, "mixed_runs": False}
    )
    record: dict[str, Any] = {
        "id": stable_id,
        "index": index,
        "body_index": index if not table_context else None,
        "body_order": None,
        "section_index": section_index,
        "style_name": style_name,
        "style_id": style_id,
        "effective_style_name": effective_style_name,
        "effective_style_id": effective_style_id,
        "outline_level": effective_p_props["outline_level"],
        "alignment": effective_p_props["alignment"],
        "indent": effective_p_props["indent"],
        "indents": effective_p_props["indent"],
        "spacing": effective_p_props["spacing"],
        "keep_with_next": effective_p_props["keep_with_next"],
        "keep_together": effective_p_props["keep_together"],
        "page_break_before": effective_p_props["page_break_before"],
        "widow_control": effective_p_props["widow_control"],
        "properties": p_props,
        "paragraph_properties": p_props,
        "formatting": paragraph_formatting,
        "effective_formatting": paragraph_formatting["effective"],
        "numPr": numpr,
        "numbering": numpr,
        "text_preview": _preview(text, include_text),
        "runs": runs,
        "fields": fields,
        **note_evidence,
        "toc": {
            "is_toc": is_toc,
            "has_toc_field": has_toc_field,
            "field_types": field_types,
            "is_body_heading": bool(not is_toc and re.match(r"^(?:第\s*\d+章|[一二三四五六七八九十]+、)", text.strip())),
        },
        "in_toc": is_toc,
        "is_in_table": table_context,
        "table_id": table_id,
        "has_omml": bool(list(paragraph.iter(qname("http://schemas.openxmlformats.org/officeDocument/2006/math", "oMath")))),
        "deleted_text_preview": _preview(_deleted_text(paragraph), include_text) if _deleted_text(paragraph) else "",
    }
    if include_text:
        record["text"] = text
    if table_context:
        record["body_index"] = None
    return record


def _inspect_table(
    table: etree._Element,
    table_index: int,
    resolver: StyleResolver,
    numbering: NumberingResolver,
    include_text: bool,
    *,
    note_collections: dict[str, dict[str, Any]] | None = None,
    section_index: int | None = None,
) -> dict[str, Any]:
    table_id = f"table-{table_index:06d}"
    rows: list[dict[str, Any]] = []
    all_cells: list[dict[str, Any]] = []
    table_rows = [
        row
        for row in table.iter(qname(W_NS, "tr"))
        if next(
            (
                ancestor
                for ancestor in row.iterancestors()
                if ancestor.tag == qname(W_NS, "tbl")
            ),
            None,
        )
        is table
    ]
    for row_index, row in enumerate(table_rows):
        cells: list[dict[str, Any]] = []
        row_cells = [
            cell
            for cell in row.iter(qname(W_NS, "tc"))
            if next(
                (
                    ancestor
                    for ancestor in cell.iterancestors()
                    if ancestor.tag == qname(W_NS, "tr")
                ),
                None,
            )
            is row
        ]
        for column_index, cell in enumerate(row_cells):
            cell_id = f"{table_id}-cell-{len(all_cells):06d}"
            tcpr = cell.find(qname(W_NS, "tcPr"))
            grid_span = tcpr.find(qname(W_NS, "gridSpan")) if tcpr is not None else None
            v_merge = tcpr.find(qname(W_NS, "vMerge")) if tcpr is not None else None
            cell_paragraphs = []
            cell_story_paragraphs = [
                paragraph
                for paragraph in cell.iter(qname(W_NS, "p"))
                if next(
                    (
                        ancestor
                        for ancestor in paragraph.iterancestors()
                        if ancestor.tag == qname(W_NS, "tc")
                    ),
                    None,
                )
                is cell
            ]
            for paragraph_index, paragraph in enumerate(cell_story_paragraphs):
                cell_paragraphs.append(
                    _inspect_paragraph(
                        paragraph,
                        paragraph_index,
                        f"{cell_id}-p-{paragraph_index:04d}",
                        resolver,
                        numbering,
                        include_text,
                        table_context=True,
                        table_id=table_id,
                        note_collections=note_collections,
                        section_index=section_index,
                    )
                )
            cell_record = {
                "id": cell_id,
                "row": row_index,
                "column": column_index,
                "grid_span": _int(grid_span.get(qname(W_NS, "val"))) if grid_span is not None else 1,
                "vertical_merge": v_merge.get(qname(W_NS, "val"), "continue") if v_merge is not None else None,
                "paragraphs": cell_paragraphs,
            }
            cells.append(cell_record)
            all_cells.append(cell_record)
        rows.append({"index": row_index, "cells": cells})
    style_element = table.find(qname(W_NS, "tblPr") + "/" + qname(W_NS, "tblStyle"))
    style_name = style_element.get(qname(W_NS, "val")) if style_element is not None else None
    max_columns = max((len(row["cells"]) for row in rows), default=0)
    note_candidates = []
    for cell in all_cells:
        for paragraph in cell["paragraphs"]:
            value = paragraph.get("text", paragraph["text_preview"])
            if value.strip().startswith(("注：", "注:", "Note:")):
                note_candidates.append(
                    {
                        "table_id": table_id,
                        "paragraph_id": paragraph["id"],
                        "text_preview": paragraph["text_preview"],
                        "location": "table_cell",
                        "reason": "table_cell_note_marker",
                        "formatting": deepcopy(paragraph.get("formatting", {})),
                        "effective_formatting": deepcopy(paragraph.get("effective_formatting", {})),
                        "runs": deepcopy(paragraph.get("runs", [])),
                    }
                )
    return {
        "id": table_id,
        "index": table_index,
        "section_index": section_index,
        "style": style_name,
        "row_count": len(rows),
        "column_count": max_columns,
        "rows": rows,
        "cells": all_cells,
        "note_candidates": note_candidates,
    }


def _record_text(record: dict[str, Any]) -> str:
    value = record.get("text")
    if isinstance(value, str):
        return value
    value = record.get("text_preview", "")
    return value if isinstance(value, str) else ""


def _note_marker_text(value: str) -> bool:
    return bool(re.match(r"^\s*注\s*[：:]", value))


def _is_heading_block(record: dict[str, Any]) -> bool:
    if record.get("in_toc") or record.get("toc", {}).get("is_toc"):
        return True
    style = str(record.get("style_name") or record.get("style_id") or "").casefold()
    if "heading" in style or "标题" in style or style.startswith("title"):
        return True
    value = _record_text(record).strip()
    return bool(
        re.match(r"^(?:第\s*\d+\s*章|[一二三四五六七八九十]+、|\d+(?:\.\d+){0,3}\s+|[（(]\s*\d+\s*[）)])", value)
    )


def _is_figure_block(element: etree._Element) -> bool:
    return any(
        node.tag in {qname(W_NS, "drawing"), qname(W_NS, "pict"), qname(W_NS, "object")}
        for node in element.iter()
    )


def _body_note_candidate(
    table: dict[str, Any],
    body_blocks: list[dict[str, Any]],
    body_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    table_id = str(table.get("id"))
    positions = {str(block.get("id")): index for index, block in enumerate(body_blocks)}
    table_position = positions.get(table_id)
    if table_position is None:
        return None
    blocked_reason: str | None = None
    blocking_id: str | None = None
    for position in range(table_position + 1, len(body_blocks)):
        block = body_blocks[position]
        block_id = str(block.get("id"))
        if block.get("kind") == "table":
            blocked_reason = "intervening_table"
            blocking_id = block_id
            break
        paragraph = body_by_id.get(block_id)
        if paragraph is None:
            continue
        element = paragraph.get("_element")
        if isinstance(element, etree._Element) and _is_figure_block(element):
            blocked_reason = "intervening_figure"
            blocking_id = block_id
            break
        text = _record_text(paragraph)
        if not text.strip():
            continue
        if _is_heading_block(paragraph):
            blocked_reason = "intervening_heading"
            blocking_id = block_id
            break
        if not _note_marker_text(text):
            blocked_reason = "first_nonempty_post_table_is_not_note"
            blocking_id = block_id
            break
        intervening = [item.get("id") for item in body_blocks[table_position + 1 : position]]
        candidate: dict[str, Any] = {
            "table_id": table_id,
            "paragraph_id": block_id,
            "adjacent_body_paragraph_id": block_id,
            "text_preview": paragraph.get("text_preview", ""),
            "location": "body",
            "distance": position - table_position,
            "intervening_block_count": max(0, position - table_position - 1),
            "intervening_block_ids": intervening,
            "reason": "first_nonempty_post_table_note",
            "match_reason": "first_nonempty_post_table_paragraph_starts_with_note_marker",
            "formatting": deepcopy(paragraph.get("formatting", {})),
            "effective_formatting": deepcopy(paragraph.get("effective_formatting", {})),
            "formatting_evidence": deepcopy(paragraph.get("formatting_evidence", {})),
            "runs": deepcopy(paragraph.get("runs", [])),
        }
        return candidate
    if blocked_reason is not None:
        table["note_binding"] = {
            "status": "blocked",
            "table_id": table_id,
            "reason": blocked_reason,
            "blocking_block_id": blocking_id,
        }
    return None


def _bind_adjacent_table_notes(
    tables: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    body_blocks: list[dict[str, Any]],
    body_elements: dict[str, etree._Element],
) -> None:
    """Bind only the first eligible post-table body note to each table."""

    body_by_id: dict[str, dict[str, Any]] = {}
    source_by_id: dict[str, dict[str, Any]] = {}
    for paragraph in paragraphs:
        paragraph_id = str(paragraph.get("id"))
        value = dict(paragraph)
        value["_element"] = body_elements.get(paragraph_id)
        body_by_id[paragraph_id] = value
        source_by_id[paragraph_id] = paragraph
    for table in tables:
        candidate = _body_note_candidate(table, body_blocks, body_by_id)
        if candidate is None:
            continue
        table.setdefault("note_candidates", []).append(candidate)
        source_paragraph = source_by_id.get(str(candidate["paragraph_id"]))
        if source_paragraph is not None:
            source_paragraph["role_hint"] = "table_note"
            source_paragraph["table_id"] = table.get("id")
            source_paragraph["table_note_binding"] = {
                "status": "bound",
                "table_id": table.get("id"),
                "distance": candidate["distance"],
                "reason": candidate["reason"],
            }
        table["note_binding"] = {
            "status": "bound",
            "table_id": table.get("id"),
            "paragraph_id": candidate["paragraph_id"],
            "distance": candidate["distance"],
            "reason": candidate["reason"],
        }


def _record_run_sizes(record: dict[str, Any]) -> tuple[float | None, str]:
    values: list[float] = []
    runs = record.get("runs", [])
    if not isinstance(runs, list):
        return None, "missing_runs"
    for run in runs:
        if not isinstance(run, dict):
            continue
        text = run.get("text") or run.get("text_preview") or ""
        if not isinstance(text, str) or not text.strip():
            continue
        formatting = run.get("formatting", {})
        effective = formatting.get("effective", {}) if isinstance(formatting, dict) else {}
        value = effective.get("size_pt") if isinstance(effective, dict) else None
        if value is None and isinstance(effective, dict):
            value = effective.get("size_cs_pt")
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None, "missing_size"
        values.append(number)
    if not values:
        return None, "missing_size"
    rounded = {round(value, 4) for value in values}
    if len(rounded) > 1:
        return None, "mixed_runs"
    return values[0], "resolved"


def _size_key(value: float) -> str:
    return f"{value:g}"


def _baseline_size(
    records: list[dict[str, Any]],
    roles_by_id: dict[str, str],
) -> tuple[float | None, dict[str, Any]]:
    candidates: list[tuple[str, float]] = []
    candidate_count = 0
    exclusion_reasons: Counter[str] = Counter()
    for record in records:
        value = _record_text(record)
        if not value.strip():
            exclusion_reasons["empty"] += 1
            continue
        paragraph_id = str(record.get("id"))
        role = roles_by_id.get(paragraph_id, "unknown")
        if role != "body_text":
            exclusion_reasons[role] += 1
            continue
        candidate_count += 1
        size, status = _record_run_sizes(record)
        if size is None:
            exclusion_reasons[status] += 1
        else:
            candidates.append((paragraph_id, round(size, 4)))

    frequencies: Counter[float] = Counter(size for _, size in candidates)
    size_frequency = {
        _size_key(size): frequencies[size]
        for size in sorted(frequencies)
    }
    common_evidence: dict[str, Any] = {
        "strategy": "classified_body_text_dominant_size",
        "candidate_paragraph_count": candidate_count,
        "resolved_candidate_count": len(candidates),
        "size_frequency": size_frequency,
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "dominance_threshold": 0.70,
    }
    if not candidates or candidate_count == 0:
        return None, {
            "status": "unknown",
            "reason": "missing_body_baseline",
            "dominant_share": 0.0,
            "confidence": 0.0,
            **common_evidence,
        }

    dominant_size, dominant_count = min(
        frequencies.items(),
        key=lambda item: (-item[1], item[0]),
    )
    dominant_share = dominant_count / candidate_count
    dominant_ids = [
        paragraph_id
        for paragraph_id, size in candidates
        if size == dominant_size
    ]
    if dominant_share < common_evidence["dominance_threshold"]:
        return None, {
            "status": "unknown",
            "reason": "no_dominant_body_size",
            "dominant_size_pt": dominant_size,
            "dominant_share": round(dominant_share, 4),
            "confidence": round(dominant_share, 4),
            "paragraph_ids": dominant_ids,
            **common_evidence,
        }
    return dominant_size, {
        "status": "resolved",
        "baseline_pt": dominant_size,
        "dominant_share": round(dominant_share, 4),
        "confidence": round(dominant_share, 4),
        "paragraph_ids": dominant_ids,
        **common_evidence,
    }


def _table_size(table: dict[str, Any]) -> tuple[float | None, dict[str, Any]]:
    candidates: list[tuple[str, float]] = []
    invalid: list[dict[str, Any]] = []
    for cell in table.get("cells", []):
        if not isinstance(cell, dict):
            continue
        for record in cell.get("paragraphs", []):
            if not isinstance(record, dict) or _note_marker_text(_record_text(record)):
                continue
            if not _record_text(record).strip():
                continue
            size, status = _record_run_sizes(record)
            if size is None:
                invalid.append({"paragraph_id": record.get("id"), "reason": status})
            else:
                candidates.append((str(record.get("id")), size))
    unique = {round(value, 4) for _, value in candidates}
    if invalid:
        return None, {"status": "unknown", "reason": "unstable_table_size", "invalid": invalid}
    if not candidates:
        return None, {"status": "unknown", "reason": "missing_table_size"}
    if len(unique) != 1:
        return None, {
            "status": "unknown",
            "reason": "unstable_table_size",
            "sizes_pt": sorted(unique),
            "paragraph_ids": [paragraph_id for paragraph_id, _ in candidates],
        }
    return candidates[0][1], {
        "status": "resolved",
        "table_pt": candidates[0][1],
        "paragraph_ids": [paragraph_id for paragraph_id, _ in candidates],
    }


def _size_relation(
    target: float | None,
    baseline: float | None,
    *,
    reason: str | None = None,
    classify_one_cn_step: bool = False,
) -> tuple[str, dict[str, Any]]:
    comparison: dict[str, Any] = {
        "target_pt": target,
        "baseline_pt": baseline,
        "source": "effective_run_formatting",
    }
    if target is None or baseline is None:
        comparison["reason"] = reason or "missing_or_unstable_size_evidence"
        return "unknown", comparison
    delta = baseline - target
    if classify_one_cn_step:
        baseline_index = cn_font_size_index(baseline)
        target_index = cn_font_size_index(target)
        if baseline_index is None or target_index is None:
            comparison["reason"] = "nonstandard_cn_font_size"
            return "unknown", comparison
        if target_index == baseline_index:
            return "equal", comparison
        if target_index == baseline_index + 1:
            return "one_cn_size_smaller", comparison
        return ("smaller" if target_index > baseline_index else "larger"), comparison
    if abs(delta) < 0.01:
        relation = "equal"
    elif delta > 0:
        relation = "smaller"
    else:
        relation = "larger"
    return relation, comparison


def _apply_size_relation(
    record: dict[str, Any],
    key: str,
    relation: str,
    comparison: dict[str, Any],
) -> None:
    record[key] = relation
    comparison_key = f"{key}_comparison"
    record[comparison_key] = deepcopy(comparison)
    evidence = record.setdefault("formatting_evidence", {})
    if isinstance(evidence, dict):
        evidence[key] = relation
        evidence[comparison_key] = deepcopy(comparison)


def _annotate_font_size_relations(
    paragraphs: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    classification_items: list[dict[str, Any]],
) -> None:
    """Attach only evidence-backed table/body/note size relations."""

    roles_by_id = {
        str(item.get("source_id") or item.get("id")): str(item.get("role", "unknown"))
        for item in classification_items
        if isinstance(item, dict)
    }
    baseline, baseline_evidence = _baseline_size(paragraphs, roles_by_id)
    body_by_id = {str(record.get("id")): record for record in paragraphs}
    for table in tables:
        table_size, table_evidence = _table_size(table)
        relation, comparison = _size_relation(
            table_size,
            baseline,
            reason=(
                baseline_evidence.get("reason")
                if baseline is None
                else table_evidence.get("reason")
                if table_size is None
                else None
            ),
        )
        table["font_size_relation_to_body"] = relation
        table["font_size_relation_to_body_comparison"] = deepcopy(comparison)
        table["font_size_evidence"] = {
            "body_baseline": deepcopy(baseline_evidence),
            "table": deepcopy(table_evidence),
        }
        for cell in table.get("cells", []):
            if not isinstance(cell, dict):
                continue
            for record in cell.get("paragraphs", []):
                if not isinstance(record, dict) or not _record_text(record).strip():
                    continue
                if _note_marker_text(_record_text(record)):
                    note_relation, note_comparison = _size_relation(
                        _record_run_sizes(record)[0],
                        table_size,
                        reason=table_evidence.get("reason") if table_size is None else _record_run_sizes(record)[1],
                        classify_one_cn_step=True,
                    )
                    _apply_size_relation(record, "font_size_relation_to_table", note_relation, note_comparison)
                else:
                    _apply_size_relation(record, "font_size_relation_to_body", relation, comparison)
        for candidate in table.get("note_candidates", []):
            if not isinstance(candidate, dict):
                continue
            paragraph_id = candidate.get("adjacent_body_paragraph_id") or candidate.get("paragraph_id")
            body_record = body_by_id.get(str(paragraph_id))
            if candidate.get("location") == "body" and body_record is not None:
                note_size, note_status = _record_run_sizes(body_record)
                note_relation, note_comparison = _size_relation(
                    note_size,
                    table_size,
                    reason=table_evidence.get("reason") if table_size is None else note_status if note_size is None else None,
                    classify_one_cn_step=True,
                )
                _apply_size_relation(body_record, "font_size_relation_to_table", note_relation, note_comparison)
                candidate["formatting"] = deepcopy(body_record.get("formatting", {}))
                candidate["effective_formatting"] = deepcopy(body_record.get("effective_formatting", {}))
                candidate["formatting_evidence"] = deepcopy(body_record.get("formatting_evidence", {}))
                candidate["runs"] = deepcopy(body_record.get("runs", []))
            elif candidate.get("location") == "table_cell":
                cell_record = next(
                    (record for cell in table.get("cells", []) for record in cell.get("paragraphs", []) if isinstance(record, dict) and str(record.get("id")) == str(paragraph_id)),
                    None,
                )
                if cell_record is not None:
                    for key in ("font_size_relation_to_table", "font_size_relation_to_table_comparison", "formatting_evidence"):
                        if key in cell_record:
                            candidate[key] = deepcopy(cell_record[key])


def _global_object_counts(package: DocxPackage, include_text: bool = False) -> dict[str, Any]:
    root = package.document_root
    bookmarks = []
    for start in root.iter(qname(W_NS, "bookmarkStart")):
        bookmarks.append({"id": start.get(qname(W_NS, "id")), "name": start.get(qname(W_NS, "name"))})
    hyperlinks = []
    rels = package.relationships("word/document.xml")
    for hyperlink in root.iter(qname(W_NS, "hyperlink")):
        rid = hyperlink.get(qname(R_NS, "id"))
        relation = rels.get(rid or "")
        target = relation.target if relation else None
        item: dict[str, Any] = {
            "relationship_id": rid,
            "target_hash": _bounded_hash(target) if target else None,
            "target_preview": _preview(_redact_path(target), False) if target else "",
            "text_preview": _preview(_visible_text(hyperlink), False),
        }
        if include_text and target is not None:
            item["target"] = target
        hyperlinks.append(item)
    comments_root = package.xml("word/comments.xml")
    comment_count = len(comments_root.findall(qname(W_NS, "comment"))) if comments_root is not None else 0
    object_count = len(list(root.iter(qname(W_NS, "object"))))
    relationship_count = sum(
        1 for relation in rels.values()
        if relation.rel_type.endswith("/oleObject") or relation.rel_type.endswith("/package")
    )
    return {
        "bookmarks": {"count": len(bookmarks), "items": bookmarks},
        "hyperlinks": {"count": len(hyperlinks), "items": hyperlinks},
        "comments": {
            "count": comment_count,
            "range_start_count": len(list(root.iter(qname(W_NS, "commentRangeStart")))),
            "range_end_count": len(list(root.iter(qname(W_NS, "commentRangeEnd")))),
        },
        "tracked_changes": {
            "insertions": len(list(root.iter(qname(W_NS, "ins")))),
            "deletions": len(list(root.iter(qname(W_NS, "del")))),
            "move_from": len(list(root.iter(qname(W_NS, "moveFrom")))),
            "move_to": len(list(root.iter(qname(W_NS, "moveTo")))),
        },
        "embedded_objects": {
            # A normal OLE object has both a w:object node and one package
            # relationship; report the object once, not twice.
            "count": max(object_count, relationship_count),
            "relationship_count": relationship_count,
        },
    }


def _toc_membership(body_paragraphs: list[etree._Element], resolver: StyleResolver) -> list[bool]:
    """Determine TOC membership without treating every PAGEREF as a TOC.

    A generated TOC has at least one positive signal: a TOC field or a TOC
    paragraph style.  Once a TOC field opens a contiguous body boundary,
    PAGEREF-only display paragraphs may inherit that boundary until the first
    paragraph with neither a TOC style nor a PAGEREF field.  This keeps normal
    cross-reference paragraphs outside the TOC while preserving the common
    Word layout where display rows carry only PAGEREF fields.
    """

    flags: list[bool] = []
    boundary_open = False
    for paragraph in body_paragraphs:
        ppr = paragraph.find(qname(W_NS, "pPr"))
        style_element = ppr.find(qname(W_NS, "pStyle")) if ppr is not None else None
        style_id = style_element.get(qname(W_NS, "val")) if style_element is not None else None
        style_name = resolver.style_name(style_id) or style_id or ""
        is_toc_style = style_name.lower().replace(" ", "").startswith("toc")
        field_types = {field["type"] for field in fields_in_paragraph(paragraph)}
        has_toc = "TOC" in field_types
        has_pageref = "PAGEREF" in field_types
        explicit = is_toc_style or has_toc
        if explicit:
            flags.append(True)
        elif boundary_open and has_pageref:
            flags.append(True)
        else:
            flags.append(False)

        if has_toc:
            boundary_open = True
        elif boundary_open and (is_toc_style or has_pageref):
            # Continue only over rows that still look like TOC display rows.
            boundary_open = True
        else:
            boundary_open = False
    return flags


def _add_body_metadata(
    paragraphs: list[dict[str, Any]],
    body_paragraphs: list[etree._Element],
    body_blocks: list[dict[str, Any]] | None = None,
) -> None:
    section_index = 0
    body_order_by_id = {
        str(block.get("id")): index
        for index, block in enumerate(body_blocks or [])
        if block.get("kind") == "paragraph"
    }
    for body_order, paragraph in enumerate(body_paragraphs):
        if body_order >= len(paragraphs):
            break
        paragraph_id = str(paragraphs[body_order].get("id"))
        paragraphs[body_order]["body_order"] = body_order_by_id.get(paragraph_id, body_order)
        paragraphs[body_order]["section_index"] = section_index
        if paragraph.find(f"{qname(W_NS, 'pPr')}/{qname(W_NS, 'sectPr')}") is not None:
            section_index += 1


def _add_body_block_sections(
    body_blocks: list[dict[str, Any]],
    paragraph_elements: Mapping[str, etree._Element],
) -> None:
    """Assign every top-level body block to its owning document section."""

    section_index = 0
    for block in body_blocks:
        block["section_index"] = section_index
        if block.get("kind") != "paragraph":
            continue
        paragraph = paragraph_elements.get(str(block.get("id")))
        if paragraph is not None and paragraph.find(
            f"{qname(W_NS, 'pPr')}/{qname(W_NS, 'sectPr')}"
        ) is not None:
            section_index += 1


def _bind_effective_note_restarts(
    notes: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    tables: list[dict[str, Any]],
) -> None:
    """Keep collection restart summaries limited to sections owning references."""

    for kind in ("footnote", "endnote"):
        collection = notes.get(f"{kind}s")
        if not isinstance(collection, dict):
            continue
        values: list[Any] = []
        seen: set[tuple[Any, Any]] = set()
        evidence_key = f"{kind}_reference_evidence"
        story_paragraphs: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for paragraph in paragraphs:
            story_paragraphs.append(
                (
                    (
                        paragraph.get("body_order", 10**12),
                        0,
                        paragraph.get("index", 10**12),
                    ),
                    paragraph,
                )
            )
        for table in tables:
            for cell in table.get("cells", []):
                if not isinstance(cell, dict):
                    continue
                for paragraph in cell.get("paragraphs", []):
                    if not isinstance(paragraph, dict):
                        continue
                    story_paragraphs.append(
                        (
                            (
                                table.get("body_order", 10**12),
                                1,
                                cell.get("row", 10**12),
                                cell.get("column", 10**12),
                                paragraph.get("index", 10**12),
                            ),
                            paragraph,
                        )
                    )
        for _order, paragraph in sorted(story_paragraphs, key=lambda item: item[0]):
            references = paragraph.get(evidence_key, [])
            if not isinstance(references, list):
                continue
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                effective = reference.get("effective_properties", {})
                if not isinstance(effective, dict):
                    continue
                value = effective.get("numRestart")
                if value is None:
                    continue
                identity = (reference.get("section_index"), value)
                if identity in seen:
                    continue
                seen.add(identity)
                values.append(value)
        collection["num_restarts"] = values
        collection["num_restart"] = values[0] if values else None


def inspect_docx(
    path: str | Path,
    include_text: bool = False,
    *,
    include_metadata: bool = False,
) -> Inspection:
    """Inspect a DOCX without modifying its bytes or metadata.

    Parameters
    ----------
    path:
        Path to an existing ``.docx`` file.  Chinese and other Unicode file
        names are supported.
    include_text:
        Include full paragraph/run text in addition to the default 80-character
        previews.  The default keeps reports suitable for sharing.
    include_metadata:
        Explicitly include core-property text.  This is separate from
        ``include_text`` so requesting document text never opts a report into
        personal metadata disclosure.
    """
    package = DocxPackage.open(path)
    source = package.path
    try:
        raw_bytes = source.read_bytes()
    except OSError as exc:
        raise DocxInspectionError(f"Could not read DOCX input: {source.name}", kind="io", path=source) from exc
    resolver = StyleResolver(package)
    numbering = NumberingResolver(package, resolver)
    notes = inspect_notes(package, include_text, resolver)
    body = package.document_root.find(qname(W_NS, "body"))
    if body is None:
        raise DocxInspectionError("DOCX core part has no document body", kind="core_part", path=source)
    body_paragraphs = _body_paragraphs(body)
    toc_membership = _toc_membership(body_paragraphs, resolver)
    body_tables = _body_tables(body)
    paragraph_ids = {id(element): f"p-{index:06d}" for index, element in enumerate(body_paragraphs)}
    paragraphs: list[dict[str, Any]] = []
    section_index = 0
    for index, paragraph in enumerate(body_paragraphs):
        # A TOC display paragraph is marked by TOC1/TOC2 style, a TOC field, or
        # a PAGEREF inside the contiguous boundary established by a TOC field.
        # An isolated PAGEREF is a normal cross-reference and is not enough.
        paragraph_is_toc_context = toc_membership[index]
        paragraph_record = _inspect_paragraph(
            paragraph,
            index,
            paragraph_ids[id(paragraph)],
            resolver,
            numbering,
            include_text,
            in_toc_context=paragraph_is_toc_context,
            note_collections=notes,
            section_index=section_index,
        )
        paragraphs.append(paragraph_record)
        if paragraph.find(f"{qname(W_NS, 'pPr')}/{qname(W_NS, 'sectPr')}") is not None:
            section_index += 1
    table_ids = {id(element): f"table-{index:06d}" for index, element in enumerate(body_tables)}
    paragraph_elements = {
        paragraph_ids[id(element)]: element
        for element in body_paragraphs
    }
    body_blocks = _body_blocks(body, paragraph_ids, table_ids)
    _add_body_metadata(paragraphs, body_paragraphs, body_blocks)
    _add_body_block_sections(body_blocks, paragraph_elements)

    table_section_by_id = {
        str(block.get("id")): block.get("section_index")
        for block in body_blocks
        if block.get("kind") == "table"
    }

    tables = [
        _inspect_table(
            table,
            index,
            resolver,
            numbering,
            include_text,
            note_collections=notes,
            section_index=table_section_by_id.get(f"table-{index:06d}"),
        )
        for index, table in enumerate(body_tables)
    ]
    table_order_by_id = {
        str(block.get("id")): index
        for index, block in enumerate(body_blocks)
        if block.get("kind") == "table"
    }
    for table in tables:
        table["body_order"] = table_order_by_id.get(str(table.get("id")))
    _bind_effective_note_restarts(notes, paragraphs, tables)
    _bind_adjacent_table_notes(tables, paragraphs, body_blocks, paragraph_elements)
    section_records = [
        _section_record(package, section, index)
        for index, section in enumerate(_section_elements(package.document_root))
    ]
    field_report = inspect_fields(package.document_root, package, include_text=include_text)
    equation_report = inspect_equations(package, paragraph_ids)
    baseline_classification = classify_inspection(
        {
            "paragraphs": paragraphs,
            "equations": equation_report,
        }
    )
    classification_items = baseline_classification.get("items", [])
    _annotate_font_size_relations(
        paragraphs,
        tables,
        classification_items if isinstance(classification_items, list) else [],
    )
    paragraph_texts = {id(element): _visible_text(element) for element in body_paragraphs}
    images = inspect_images(package, paragraph_ids, paragraph_texts)
    objects = _global_object_counts(package, include_text=include_text)
    reported_page_count = _core_page_count(package)
    summary = {
        "section_count": len(section_records),
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
        "body_block_count": len(body_blocks),
        "image_count": len(images),
        "equation_count": equation_report["omath_count"],
        "omath_count": equation_report["omath_count"],
        "omath_para_count": equation_report["omath_para_count"],
        "footnote_count": notes["footnotes"]["actual_count"],
        "endnote_count": notes["endnotes"]["actual_count"],
        "field_counts": field_report["counts"],
        "reported_page_count": reported_page_count,
        "reported_page_count_authority": "document_properties_not_realtime",
        "bookmark_count": objects["bookmarks"]["count"],
        "hyperlink_count": objects["hyperlinks"]["count"],
        "comment_count": objects["comments"]["count"],
        "tracked_insertion_count": objects["tracked_changes"]["insertions"],
        "tracked_deletion_count": objects["tracked_changes"]["deletions"],
        "embedded_object_count": objects["embedded_objects"]["count"],
    }
    report: Inspection = {
        "schema_version": "1.0",
        "tool_version": __version__,
        "input": {
            "filename": source.name,
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "size_bytes": len(raw_bytes),
            "reported_page_count": reported_page_count,
        },
        "core_properties": _core_properties(package, include_metadata=include_metadata),
        "summary": summary,
        "sections": section_records,
        "body_blocks": body_blocks,
        "paragraphs": paragraphs,
        "tables": tables,
        "images": images,
        "equations": equation_report,
        "notes": notes,
        "fields": field_report,
        **objects,
    }
    return report


__all__ = ["DocxInspectionError", "inspect_docx"]
