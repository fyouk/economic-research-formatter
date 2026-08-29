"""Footnote and endnote part inspection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from lxml import etree

from .package import DocxPackage, W_NS, local_name, qname
from .styles import StyleResolver


NORMAL_NOTE_TYPES = {None, "normal"}
_MAX_PREVIEW = 80
_NOTE_PROPERTY_NAMES = ("numFmt", "numStart", "numRestart")
_DEFAULT_NOTE_PROPERTIES = {
    "numFmt": "decimal",
    "numStart": "1",
    "numRestart": "continuous",
}


def _preview(value: str) -> str:
    return value[:_MAX_PREVIEW]


def _visible_text(element: etree._Element) -> str:
    chunks: list[str] = []
    for node in element.iter():
        name = local_name(node)
        if name in {"instrText", "delText", "fldChar", "commentRangeStart", "commentRangeEnd", "bookmarkStart", "bookmarkEnd"}:
            continue
        if node.tag == qname(W_NS, "t") and node.text:
            chunks.append(node.text)
        elif node.tag == qname(W_NS, "tab"):
            chunks.append("\t")
        elif node.tag in {qname(W_NS, "br"), qname(W_NS, "cr")}:
            chunks.append("\n")
    return "".join(chunks)


def _inspect_note_paragraph(
    paragraph: etree._Element,
    *,
    note_id: int,
    paragraph_index: int,
    part_type: str,
    resolver: StyleResolver,
    include_text: bool,
) -> dict[str, Any]:
    ppr = paragraph.find(qname(W_NS, "pPr"))
    style_element = ppr.find(qname(W_NS, "pStyle")) if ppr is not None else None
    style_id = style_element.get(qname(W_NS, "val")) if style_element is not None else None
    properties = resolver.resolve_paragraph(ppr)
    effective = properties["effective"]
    text = _visible_text(paragraph)
    runs: list[dict[str, Any]] = []
    offset = 0
    for run_index, run in enumerate(paragraph.iter(qname(W_NS, "r"))):
        run_text = _visible_text(run)
        formatting = resolver.resolve_run(run.find(qname(W_NS, "rPr")), style_id)
        record: dict[str, Any] = {
            "id": f"{part_type}-{note_id}-p-{paragraph_index:04d}-r-{run_index:04d}",
            "index": run_index,
            "start": offset,
            "end": offset + len(run_text),
            "text_preview": _preview(run_text),
            "text_length": len(run_text),
            "text_truncated": len(run_text) > _MAX_PREVIEW,
            "preview_only": not include_text,
            "formatting": formatting,
            "is_whitespace_only": bool(run_text) and not run_text.strip(),
            "has_field_char": run.find(qname(W_NS, "fldChar")) is not None,
        }
        if include_text:
            record["text"] = run_text
        deleted = "".join(node.text or "" for node in run.iter(qname(W_NS, "delText")))
        if deleted:
            record["deleted_text_preview"] = _preview(deleted)
            if include_text:
                record["deleted_text"] = deleted
        runs.append(record)
        offset = record["end"]

    meaningful = next((run for run in runs if run.get("text_preview", "").strip()), None)
    formatting = (
        {
            "raw": deepcopy(meaningful["formatting"]["raw"]),
            "effective": deepcopy(meaningful["formatting"]["effective"]),
            "source": deepcopy(meaningful["formatting"]["source"]),
            "mixed_runs": sum(bool(run.get("text_preview", "").strip()) for run in runs) > 1,
        }
        if meaningful is not None
        else {"raw": {}, "effective": {}, "source": {}, "mixed_runs": False}
    )
    record = {
        "id": f"{part_type}-{note_id}-p-{paragraph_index:04d}",
        "index": paragraph_index,
        "style_name": resolver.style_name(style_id),
        "style_id": style_id,
        "outline_level": effective["outline_level"],
        "alignment": effective["alignment"],
        "indent": effective["indent"],
        "indents": effective["indent"],
        "spacing": effective["spacing"],
        "keep_with_next": effective["keep_with_next"],
        "keep_together": effective["keep_together"],
        "page_break_before": effective["page_break_before"],
        "widow_control": effective["widow_control"],
        "properties": properties,
        "paragraph_properties": properties,
        "formatting": formatting,
        "effective_formatting": formatting["effective"],
        "text_preview": _preview(text),
        "text_length": len(text),
        "text_truncated": len(text) > _MAX_PREVIEW,
        "preview_only": not include_text,
        "runs": runs,
    }
    if include_text:
        record["text"] = text
    return record


def _note_part(package: DocxPackage, part_type: str) -> tuple[str, Any] | None:
    relation_suffix = f"/{part_type}"
    for relation in package.relationships("word/document.xml").values():
        if relation.rel_type.endswith(relation_suffix):
            target = package.resolve_target("word/document.xml", relation.target)
            root = package.xml(target)
            if root is not None:
                return target, root
    conventional = f"word/{part_type}.xml"
    root = package.xml(conventional)
    return (conventional, root) if root is not None else None


def _document_note_references(package: DocxPackage, part_type: str) -> list[int]:
    refs: list[int] = []
    tag = "footnoteReference" if part_type == "footnotes" else "endnoteReference"
    for reference in package.document_root.iter(qname(W_NS, tag)):
        if any(
            ancestor.tag in {qname(W_NS, "del"), qname(W_NS, "moveFrom")}
            for ancestor in reference.iterancestors()
        ):
            continue
        try:
            refs.append(int(reference.get(qname(W_NS, "id"))))
        except (TypeError, ValueError):
            continue
    return refs


def _section_elements(root: etree._Element) -> list[etree._Element]:
    """Return document sections in body order, including the final section."""

    body = root.find(qname(W_NS, "body"))
    if body is None:
        return []
    sections: list[etree._Element] = []
    for paragraph in body.iter(qname(W_NS, "p")):
        if any(ancestor.tag == qname(W_NS, "tbl") for ancestor in paragraph.iterancestors()):
            continue
        section = paragraph.find(f"{qname(W_NS, 'pPr')}/{qname(W_NS, 'sectPr')}")
        if section is not None:
            sections.append(section)
    final = body.find(qname(W_NS, "sectPr"))
    if final is not None:
        sections.append(final)
    return sections


def _settings_root(package: DocxPackage) -> etree._Element | None:
    """Locate settings through its relationship, with a safe conventional fallback."""

    for relation in package.relationships("word/document.xml").values():
        if relation.rel_type.endswith("/settings"):
            target = package.resolve_target("word/document.xml", relation.target)
            root = package.xml(target)
            if root is not None:
                return root
    return package.xml("word/settings.xml")


def _raw_note_properties(parent: etree._Element | None, kind: str) -> dict[str, str]:
    if parent is None:
        return {}
    note_pr = parent.find(qname(W_NS, f"{kind}Pr"))
    if note_pr is None:
        return {}
    result: dict[str, str] = {}
    for property_name in _NOTE_PROPERTY_NAMES:
        child = note_pr.find(qname(W_NS, property_name))
        value = child.get(qname(W_NS, "val")) if child is not None else None
        if value is not None:
            result[property_name] = value
    return result


def _section_note_properties(
    document_wide: Mapping[str, Any],
    section_override: Mapping[str, Any],
    section_index: int,
) -> dict[str, Any]:
    effective = dict(_DEFAULT_NOTE_PROPERTIES)
    effective.update(document_wide)
    effective.update(section_override)
    sources = {
        property_name: (
            "section"
            if property_name in section_override
            else "settings"
            if property_name in document_wide
            else "default"
        )
        for property_name in _NOTE_PROPERTY_NAMES
    }
    return {
        "section_index": section_index,
        "section_override": dict(section_override),
        "effective_properties": effective,
        "property_sources": sources,
        "property_evidence": {
            property_name: {
                "value": effective.get(property_name),
                "source": sources[property_name],
            }
            for property_name in _NOTE_PROPERTY_NAMES
        },
    }


def _effective_note_properties(package: DocxPackage, part_type: str) -> dict[str, Any]:
    """Compute defaults -> settings -> section effective note properties."""

    kind = part_type.removesuffix("s")
    document_wide = _raw_note_properties(_settings_root(package), kind)
    sections: list[dict[str, Any]] = []
    for section_index, section in enumerate(_section_elements(package.document_root)):
        section_override = _raw_note_properties(section, kind)
        sections.append(_section_note_properties(document_wide, section_override, section_index))
    if not sections:
        sections.append(_section_note_properties(document_wide, {}, 0))
    effective_values = [section["effective_properties"] for section in sections]
    restarts = [value["numRestart"] for value in effective_values if value.get("numRestart") is not None]
    return {
        "document_wide_properties": document_wide,
        "section_properties": sections,
        "effective_properties": effective_values,
        "num_restart": restarts[0] if restarts else None,
        "num_restarts": restarts,
    }


def note_properties_for_section(
    report: Mapping[str, Any],
    section_index: int,
) -> dict[str, Any]:
    """Return the effective note-property evidence for one owning section."""

    document_wide_raw = report.get("document_wide_properties", {})
    document_wide = dict(document_wide_raw) if isinstance(document_wide_raw, Mapping) else {}
    section_records = report.get("section_properties", [])
    selected: dict[str, Any] | None = None
    if isinstance(section_records, Sequence) and not isinstance(section_records, (str, bytes)):
        for raw_record in section_records:
            if not isinstance(raw_record, Mapping):
                continue
            if raw_record.get("section_index") == section_index:
                selected = dict(raw_record)
                break
    if selected is None:
        selected = _section_note_properties(document_wide, {}, section_index)
    effective_raw = selected.get("effective_properties", {})
    effective = dict(effective_raw) if isinstance(effective_raw, Mapping) else {}
    section_override_raw = selected.get("section_override", {})
    section_override = dict(section_override_raw) if isinstance(section_override_raw, Mapping) else {}
    property_sources_raw = selected.get("property_sources", {})
    property_sources = (
        dict(property_sources_raw)
        if isinstance(property_sources_raw, Mapping)
        else _section_note_properties(document_wide, section_override, section_index)["property_sources"]
    )
    property_evidence_raw = selected.get("property_evidence", {})
    property_evidence = (
        deepcopy(property_evidence_raw)
        if isinstance(property_evidence_raw, Mapping)
        else {
            property_name: {
                "value": effective.get(property_name),
                "source": property_sources.get(property_name),
            }
            for property_name in _NOTE_PROPERTY_NAMES
        }
    )
    return {
        **effective,
        "section_index": section_index,
        "document_wide_properties": document_wide,
        "section_override": section_override,
        "effective_properties": effective,
        "property_sources": property_sources,
        "property_evidence": property_evidence,
    }


def _inspect_note_part(
    package: DocxPackage,
    part_type: str,
    include_text: bool = False,
    resolver: StyleResolver | None = None,
) -> dict[str, Any]:
    property_report = _effective_note_properties(package, part_type)
    refs = _document_note_references(package, part_type)
    found = _note_part(package, part_type)
    if found is None:
        return {
            "part": None,
            "actual_count": 0,
            "separator_count": 0,
            "ids": [],
            "items": [],
            "paragraphs": [],
            "references": refs,
            "one_to_one": not refs,
            **property_report,
        }
    part_name, root = found
    if resolver is None:
        resolver = StyleResolver(package)
    notes = root.findall(qname(W_NS, "footnote")) if part_type == "footnotes" else root.findall(qname(W_NS, "endnote"))
    actual: list[int] = []
    separators: list[int] = []
    items: list[dict[str, Any]] = []
    for note in notes:
        value = note.get(qname(W_NS, "id"))
        try:
            note_id = int(value) if value is not None else None
        except (TypeError, ValueError):
            note_id = None
        if note.get(qname(W_NS, "type")) not in NORMAL_NOTE_TYPES or note_id in (-1, 0):
            if note_id is not None:
                separators.append(note_id)
            continue
        if note_id is None:
            continue
        actual.append(note_id)
        paragraphs = [
            _inspect_note_paragraph(
                paragraph,
                note_id=note_id,
                paragraph_index=paragraph_index,
                part_type=part_type,
                resolver=resolver,
                include_text=include_text,
            )
            for paragraph_index, paragraph in enumerate(note.iter(qname(W_NS, "p")))
        ]
        full_text = "".join(_visible_text(paragraph) for paragraph in note.iter(qname(W_NS, "p")))
        text = full_text if include_text else _preview(full_text)
        first = paragraphs[0] if paragraphs else None
        item: dict[str, Any] = {
            "id": note_id,
            "kind": part_type.removesuffix("s"),
            "note_id": note_id,
            f"{part_type.removesuffix('s')}_id": note_id,
            "text_preview": _preview(text),
            "text_length": len(full_text),
            "text_truncated": len(full_text) > _MAX_PREVIEW,
            "preview_only": not include_text,
            "paragraphs": paragraphs,
            "runs": [run for paragraph in paragraphs for run in paragraph["runs"]],
            "formatting": deepcopy(first["formatting"]) if first else {"raw": {}, "effective": {}, "source": {}, "mixed_runs": False},
            "effective_formatting": deepcopy(first["effective_formatting"]) if first else {},
            "properties": deepcopy(first["properties"]) if first else {"raw": {}, "effective": {}, "source": {}, "resolution_chain": ["direct", "docDefaults"]},
        }
        if include_text:
            item["text"] = text
        items.append(item)
    return {
        "part": part_name,
        "actual_count": len(actual),
        "separator_count": len(separators),
        "ids": actual,
        "items": items,
        # ``paragraphs`` is a compatibility-friendly view for lint rules:
        # each actual note is represented by its first paragraph's formatting
        # while the full paragraph/run tree remains under ``items``.
        "paragraphs": items,
        "separator_ids": separators,
        "references": refs,
        "one_to_one": sorted(actual) == sorted(refs),
        **property_report,
    }


def inspect_notes(
    package: DocxPackage,
    include_text: bool = False,
    resolver: StyleResolver | None = None,
) -> dict[str, Any]:
    return {
        "footnotes": _inspect_note_part(package, "footnotes", include_text, resolver),
        "endnotes": _inspect_note_part(package, "endnotes", include_text, resolver),
    }


__all__ = ["inspect_notes", "note_properties_for_section"]
