"""Deterministic, read-only DOCX/OOXML inspection.

``inspect_docx`` intentionally returns JSON-compatible dictionaries instead of
python-docx proxy objects.  This makes reports stable across processes and
lets the classifier/linter consume exactly the same evidence that a user can
inspect in ``inspection.json``.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from lxml import etree

from .. import __version__
from ..models.inspection import DocxInspectionError, Inspection
from .equations import inspect_equations
from .fields import fields_in_paragraph, inspect_fields
from .images import inspect_images
from .numbering import NumberingResolver
from .notes import inspect_notes
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


def _note_reference_evidence(paragraph: etree._Element) -> dict[str, Any]:
    """Return note-reference IDs plus stable marker evidence for a paragraph."""

    references: list[dict[str, Any]] = []
    run_positions = {id(run): index for index, run in enumerate(_run_elements(paragraph))}
    for tag, kind in (("footnoteReference", "footnote"), ("endnoteReference", "endnote")):
        for reference in paragraph.iter(qname(W_NS, tag)):
            raw_id = reference.get(qname(W_NS, "id"))
            note_id = _int(raw_id)
            run = next((ancestor for ancestor in reference.iterancestors() if ancestor.tag == qname(W_NS, "r")), None)
            references.append(
                {
                    "kind": kind,
                    "marker": tag,
                    "id": note_id,
                    "id_raw": raw_id,
                    "run_index": run_positions.get(id(run)),
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
    for paragraph in body.findall(qname(W_NS, "p")):
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


def _core_page_count(package: DocxPackage) -> int | None:
    root = package.xml("docProps/app.xml")
    if root is None:
        return None
    element = root.find(qname(_DOC_PROPS_NS, "Pages"))
    return _int(element.text if element is not None else None)


def _core_properties(package: DocxPackage) -> dict[str, Any]:
    root = package.xml("docProps/core.xml")
    if root is None:
        return {}
    values: dict[str, Any] = {}
    for key, namespace in (("title", _DC_NS), ("subject", _DC_NS), ("creator", _DC_NS), ("description", _DC_NS), ("keywords", _CORE_PROPS_NS), ("last_modified_by", _CORE_PROPS_NS)):
        element = root.find(qname(namespace, key.replace("last_modified_by", "lastModifiedBy")))
        if element is not None and element.text is not None:
            values[key] = element.text
    return values


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
) -> dict[str, Any]:
    ppr = paragraph.find(qname(W_NS, "pPr"))
    style_id_element = ppr.find(qname(W_NS, "pStyle")) if ppr is not None else None
    style_id = style_id_element.get(qname(W_NS, "val")) if style_id_element is not None else None
    style_name = resolver.style_name(style_id)
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
    note_evidence = _note_reference_evidence(paragraph)
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
        "section_index": None,
        "style_name": style_name,
        "style_id": style_id,
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
) -> dict[str, Any]:
    table_id = f"table-{table_index:06d}"
    rows: list[dict[str, Any]] = []
    all_cells: list[dict[str, Any]] = []
    for row_index, row in enumerate(table.findall(qname(W_NS, "tr"))):
        cells: list[dict[str, Any]] = []
        for column_index, cell in enumerate(row.findall(qname(W_NS, "tc"))):
            cell_id = f"{table_id}-cell-{len(all_cells):06d}"
            tcpr = cell.find(qname(W_NS, "tcPr"))
            grid_span = tcpr.find(qname(W_NS, "gridSpan")) if tcpr is not None else None
            v_merge = tcpr.find(qname(W_NS, "vMerge")) if tcpr is not None else None
            cell_paragraphs = []
            for paragraph_index, paragraph in enumerate(cell.findall(qname(W_NS, "p"))):
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
                note_candidates.append({"paragraph_id": paragraph["id"], "text_preview": paragraph["text_preview"]})
    return {
        "id": table_id,
        "index": table_index,
        "style": style_name,
        "row_count": len(rows),
        "column_count": max_columns,
        "rows": rows,
        "cells": all_cells,
        "note_candidates": note_candidates,
    }


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


def _add_body_metadata(paragraphs: list[dict[str, Any]], body_paragraphs: list[etree._Element]) -> None:
    section_index = 0
    for body_order, paragraph in enumerate(body_paragraphs):
        if body_order >= len(paragraphs):
            break
        paragraphs[body_order]["body_order"] = body_order
        paragraphs[body_order]["section_index"] = section_index
        if paragraph.find(f"{qname(W_NS, 'pPr')}/{qname(W_NS, 'sectPr')}") is not None:
            section_index += 1


def inspect_docx(path: str | Path, include_text: bool = False) -> Inspection:
    """Inspect a DOCX without modifying its bytes or metadata.

    Parameters
    ----------
    path:
        Path to an existing ``.docx`` file.  Chinese and other Unicode file
        names are supported.
    include_text:
        Include full paragraph/run text in addition to the default 80-character
        previews.  The default keeps reports suitable for sharing.
    """
    package = DocxPackage.open(path)
    source = package.path
    try:
        raw_bytes = source.read_bytes()
    except OSError as exc:
        raise DocxInspectionError(f"Could not read DOCX input: {source.name}", kind="io", path=source) from exc
    resolver = StyleResolver(package)
    numbering = NumberingResolver(package)
    body = package.document_root.find(qname(W_NS, "body"))
    if body is None:
        raise DocxInspectionError("DOCX core part has no document body", kind="core_part", path=source)
    body_paragraphs = _body_paragraphs(body)
    toc_membership = _toc_membership(body_paragraphs, resolver)
    body_tables = _body_tables(body)
    paragraph_ids = {id(element): f"p-{index:06d}" for index, element in enumerate(body_paragraphs)}
    paragraphs: list[dict[str, Any]] = []
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
        )
        paragraphs.append(paragraph_record)
    _add_body_metadata(paragraphs, body_paragraphs)

    tables = [
        _inspect_table(table, index, resolver, numbering, include_text)
        for index, table in enumerate(body_tables)
    ]
    section_records = [
        _section_record(package, section, index)
        for index, section in enumerate(_section_elements(package.document_root))
    ]
    field_report = inspect_fields(package.document_root, package, include_text=include_text)
    equation_report = inspect_equations(package, paragraph_ids)
    paragraph_texts = {id(element): _visible_text(element) for element in body_paragraphs}
    images = inspect_images(package, paragraph_ids, paragraph_texts)
    notes = inspect_notes(package, include_text, resolver)
    objects = _global_object_counts(package, include_text=include_text)
    reported_page_count = _core_page_count(package)
    summary = {
        "section_count": len(section_records),
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
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
        "core_properties": _core_properties(package),
        "summary": summary,
        "sections": section_records,
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
