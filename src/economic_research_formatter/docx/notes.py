"""Footnote and endnote part inspection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lxml import etree

from .package import DocxPackage, W_NS, local_name, qname
from .styles import StyleResolver


SEPARATOR_TYPES = {"separator", "continuationSeparator"}
_MAX_PREVIEW = 80


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


def _inspect_note_part(
    package: DocxPackage,
    part_type: str,
    include_text: bool = False,
    resolver: StyleResolver | None = None,
) -> dict[str, Any]:
    found = _note_part(package, part_type)
    if found is None:
        return {
            "part": None,
            "actual_count": 0,
            "separator_count": 0,
            "ids": [],
            "items": [],
            "paragraphs": [],
            "references": [],
            "one_to_one": True,
            "num_restart": None,
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
        if note.get(qname(W_NS, "type")) in SEPARATOR_TYPES or note_id in (-1, 0):
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
            for paragraph_index, paragraph in enumerate(note.findall(qname(W_NS, "p")))
        ]
        text = "".join(paragraph.get("text", paragraph["text_preview"]) for paragraph in paragraphs)
        first = paragraphs[0] if paragraphs else None
        item: dict[str, Any] = {
            "id": note_id,
            "text_preview": _preview(text),
            "paragraphs": paragraphs,
            "runs": [run for paragraph in paragraphs for run in paragraph["runs"]],
            "formatting": deepcopy(first["formatting"]) if first else {"raw": {}, "effective": {}, "source": {}, "mixed_runs": False},
            "effective_formatting": deepcopy(first["effective_formatting"]) if first else {},
            "properties": deepcopy(first["properties"]) if first else {"raw": {}, "effective": {}, "source": {}, "resolution_chain": ["direct", "docDefaults"]},
        }
        if include_text:
            item["text"] = text
        items.append(item)
    refs = []
    tag = "footnoteReference" if part_type == "footnotes" else "endnoteReference"
    for reference in package.document_root.iter(qname(W_NS, tag)):
        try:
            refs.append(int(reference.get(qname(W_NS, "id"))))
        except (TypeError, ValueError):
            continue
    restarts: list[str] = []
    note_tag = "footnotePr" if part_type == "footnotes" else "endnotePr"
    for sect_pr in package.document_root.iter(qname(W_NS, "sectPr")):
        note_pr = sect_pr.find(qname(W_NS, note_tag))
        if note_pr is None:
            continue
        restart_el = note_pr.find(qname(W_NS, "numRestart"))
        if restart_el is not None and restart_el.get(qname(W_NS, "val")):
            restarts.append(restart_el.get(qname(W_NS, "val")))
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
        "num_restart": restarts[0] if restarts else None,
        "num_restarts": restarts,
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


__all__ = ["inspect_notes"]
