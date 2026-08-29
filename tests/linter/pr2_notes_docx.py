"""Real OOXML DOCX fixtures for PR2 note regressions.

The note and settings parts used here are deliberately written into a normal
DOCX ZIP package after ``tests.inspector.pr1_docx_factory.write_docx`` creates
the core document.  This keeps the fixtures small while exercising the same
package/relationship/content-type path as a producer-generated DOCX.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from tests.inspector.pr1_docx_factory import paragraph, run, write_docx


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _qname(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _note_part(
    kind: str,
    notes: Sequence[tuple[int, str]],
    special_notes: Sequence[tuple[int, str]] = (),
    wrap_paragraphs: bool = False,
    run_properties: Mapping[int, Mapping[str, object]] | None = None,
) -> bytes:
    root = etree.Element(_qname(W, f"{kind}s"), nsmap={"w": W})
    separator = etree.SubElement(root, _qname(W, kind))
    separator.set(_qname(W, "type"), "separator")
    separator.set(_qname(W, "id"), "-1")
    etree.SubElement(separator, _qname(W, "p"))
    continuation = etree.SubElement(root, _qname(W, kind))
    continuation.set(_qname(W, "type"), "continuationSeparator")
    continuation.set(_qname(W, "id"), "0")
    etree.SubElement(continuation, _qname(W, "p"))
    for note_id, note_type in special_notes:
        special = etree.SubElement(root, _qname(W, kind))
        special.set(_qname(W, "type"), note_type)
        special.set(_qname(W, "id"), str(note_id))
        etree.SubElement(special, _qname(W, "p"))
    for note_id, text in notes:
        note = etree.SubElement(root, _qname(W, kind))
        note.set(_qname(W, "id"), str(note_id))
        paragraph_parent = note
        if wrap_paragraphs:
            wrapper = etree.SubElement(note, _qname(W, "sdt"))
            paragraph_parent = etree.SubElement(wrapper, _qname(W, "sdtContent"))
        note_paragraph = etree.SubElement(paragraph_parent, _qname(W, "p"))
        note_run = etree.SubElement(note_paragraph, _qname(W, "r"))
        properties = (run_properties or {}).get(note_id, {})
        if properties:
            rpr = etree.SubElement(note_run, _qname(W, "rPr"))
            east_asia = properties.get("eastAsia")
            if east_asia is not None:
                fonts = etree.SubElement(rpr, _qname(W, "rFonts"))
                fonts.set(_qname(W, "eastAsia"), str(east_asia))
            size_pt = properties.get("size_pt")
            if size_pt is not None:
                size = etree.SubElement(rpr, _qname(W, "sz"))
                size.set(_qname(W, "val"), f"{float(size_pt) * 2:g}")
        text_node = etree.SubElement(note_run, _qname(W, "t"))
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_node.text = text
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _note_properties_xml(properties: Mapping[str, str]) -> etree._Element:
    note_pr = etree.Element(_qname(W, "notePr"))
    for key in ("numFmt", "numStart", "numRestart"):
        value = properties.get(key)
        if value is None:
            continue
        child = etree.SubElement(note_pr, _qname(W, key))
        child.set(_qname(W, "val"), str(value))
    return note_pr


def _section_xml(overrides: Mapping[str, Mapping[str, str]] | None) -> str:
    section = etree.Element(_qname(W, "sectPr"))
    for kind in ("footnote", "endnote"):
        properties = (overrides or {}).get(kind)
        if not properties:
            continue
        note_pr = _note_properties_xml(properties)
        note_pr.tag = _qname(W, f"{kind}Pr")
        section.append(note_pr)
    return etree.tostring(section, encoding="unicode")


def _settings_xml(settings: Mapping[str, Mapping[str, str]]) -> bytes:
    root = etree.Element(_qname(W, "settings"), nsmap={"w": W})
    for kind in ("footnote", "endnote"):
        properties = settings.get(kind)
        if not properties:
            continue
        note_pr = _note_properties_xml(properties)
        note_pr.tag = _qname(W, f"{kind}Pr")
        root.append(note_pr)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def make_notes_docx(
    tmp_path: Path,
    *,
    notes: Sequence[tuple[str, int, str]],
    references: Sequence[tuple[int, str, int]],
    deleted_references: Sequence[tuple[int, str, int]] = (),
    settings: Mapping[str, Mapping[str, str]] | None = None,
    section_overrides: Sequence[Mapping[str, Mapping[str, str]] | None] = (),
    body_texts: Sequence[str] = ("正文内容。",),
    table_reference: tuple[str, int] | None = None,
    wrap_table_reference: bool = False,
    wrap_table_row: bool = False,
    table_before_paragraph_index: int | None = None,
    wrap_first_section_break: bool = False,
    special_notes: Sequence[tuple[str, int, str]] = (),
    wrap_note_paragraphs: bool = False,
    note_run_properties: Mapping[tuple[str, int], Mapping[str, object]] | None = None,
    filename: str = "pr2-notes.docx",
) -> Path:
    """Write a genuine DOCX with footnotes/endnotes and optional properties.

    ``references`` contains ``(body paragraph index, kind, note id)`` entries.
    A section break is attached to every body paragraph except the last one;
    the final ``sectPr`` belongs to the last body paragraph's section.  Thus a
    two-paragraph fixture can express distinct owning-section properties for
    the first and second references.
    """

    body_parts: list[str] = []
    refs_by_paragraph: dict[int, list[tuple[str, int]]] = {}
    for paragraph_index, kind, note_id in references:
        refs_by_paragraph.setdefault(paragraph_index, []).append((kind, note_id))
    deleted_refs_by_paragraph: dict[int, list[tuple[str, int]]] = {}
    for paragraph_index, kind, note_id in deleted_references:
        deleted_refs_by_paragraph.setdefault(paragraph_index, []).append((kind, note_id))
    for index, text in enumerate(body_texts):
        reference_xml = "".join(
            f'<w:r><w:{kind}Reference w:id="{note_id}"/></w:r>'
            for kind, note_id in refs_by_paragraph.get(index, [])
        )
        reference_xml += "".join(
            f'<w:del><w:r><w:{kind}Reference w:id="{note_id}"/></w:r></w:del>'
            for kind, note_id in deleted_refs_by_paragraph.get(index, [])
        )
        section_xml = ""
        if index < len(body_texts) - 1:
            override = section_overrides[index] if index < len(section_overrides) else None
            section_xml = f"<w:sectPr>{_section_xml_children(override)}</w:sectPr>"
        body_parts.append(paragraph(run(text) + reference_xml, extra_ppr=section_xml))
    if table_reference is not None:
        kind, note_id = table_reference
        table_paragraph = (
            f"<w:p><w:r><w:t>单元格注释</w:t></w:r><w:r><w:{kind}Reference w:id=\"{note_id}\"/></w:r></w:p>"
        )
        if wrap_table_reference:
            table_paragraph = f"<w:sdt><w:sdtContent>{table_paragraph}</w:sdtContent></w:sdt>"
        table_row = f"<w:tr><w:tc>{table_paragraph}</w:tc></w:tr>"
        if wrap_table_row:
            table_row = f"<w:sdt><w:sdtContent>{table_row}</w:sdtContent></w:sdt>"
        table_block = (
            "<w:tbl><w:tblPr/><w:tblGrid/>"
            f"{table_row}"
            "</w:tbl>"
        )
        if table_before_paragraph_index is None:
            body_parts.append(table_block)
        else:
            body_parts.insert(table_before_paragraph_index, table_block)

    path = write_docx(tmp_path, body="".join(body_parts), filename=filename)
    with ZipFile(path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}

    document = etree.fromstring(members["word/document.xml"])
    body = document.find(_qname(W, "body"))
    assert body is not None
    if wrap_first_section_break:
        first_paragraph = body.find(_qname(W, "p"))
        assert first_paragraph is not None
        paragraph_position = list(body).index(first_paragraph)
        body.remove(first_paragraph)
        wrapper = etree.Element(_qname(W, "sdt"))
        content = etree.SubElement(wrapper, _qname(W, "sdtContent"))
        content.append(first_paragraph)
        body.insert(paragraph_position, wrapper)
    final_section = body.find(_qname(W, "sectPr"))
    assert final_section is not None
    final_override = section_overrides[-1] if section_overrides else None
    for child in list(final_section):
        final_section.remove(child)
    if final_override:
        final_section.extend(etree.fromstring(_section_xml(final_override)).getchildren())

    relationships_name = "word/_rels/document.xml.rels"
    relationships = etree.fromstring(members.get(relationships_name, f'<Relationships xmlns="{PR}"/>'.encode()))
    content_types = etree.fromstring(members["[Content_Types].xml"])
    existing_ids = [
        int(item.get("Id", "rId0")[3:])
        for item in relationships
        if item.get("Id", "").startswith("rId") and item.get("Id", "")[3:].isdigit()
    ]
    next_id = max(existing_ids + [0]) + 1

    def add_relationship(target: str, rel_type: str) -> None:
        nonlocal next_id
        relation = etree.SubElement(relationships, _qname(PR, "Relationship"))
        relation.set("Id", f"rId{next_id}")
        relation.set("Type", rel_type)
        relation.set("Target", target)
        next_id += 1

    def add_override(part_name: str, content_type: str) -> None:
        override = etree.SubElement(content_types, _qname(CT, "Override"))
        override.set("PartName", part_name)
        override.set("ContentType", content_type)

    note_kinds = {kind for kind, _, _ in notes} | {kind for kind, _, _ in special_notes}
    if "footnote" in note_kinds:
        add_relationship("footnotes.xml", f"{R}/footnotes")
        add_override(
            "/word/footnotes.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
        )
        members["word/footnotes.xml"] = _note_part(
            "footnote",
            [(note_id, text) for kind, note_id, text in notes if kind == "footnote"],
            [(note_id, note_type) for kind, note_id, note_type in special_notes if kind == "footnote"],
            wrap_note_paragraphs,
            {
                note_id: properties
                for (kind, note_id), properties in (note_run_properties or {}).items()
                if kind == "footnote"
            },
        )
    if "endnote" in note_kinds:
        add_relationship("endnotes.xml", f"{R}/endnotes")
        add_override(
            "/word/endnotes.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml",
        )
        members["word/endnotes.xml"] = _note_part(
            "endnote",
            [(note_id, text) for kind, note_id, text in notes if kind == "endnote"],
            [(note_id, note_type) for kind, note_id, note_type in special_notes if kind == "endnote"],
            wrap_note_paragraphs,
            {
                note_id: properties
                for (kind, note_id), properties in (note_run_properties or {}).items()
                if kind == "endnote"
            },
        )
    if settings:
        add_relationship("settings.xml", f"{R}/settings")
        add_override(
            "/word/settings.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
        )
        members["word/settings.xml"] = _settings_xml(settings)

    members["word/document.xml"] = etree.tostring(document, xml_declaration=True, encoding="UTF-8", standalone=True)
    members[relationships_name] = etree.tostring(relationships, xml_declaration=True, encoding="UTF-8", standalone=True)
    members["[Content_Types].xml"] = etree.tostring(content_types, xml_declaration=True, encoding="UTF-8", standalone=True)
    with ZipFile(path, "w", ZIP_DEFLATED) as target:
        for name, value in members.items():
            target.writestr(name, value)
    return path


def _section_xml_children(overrides: Mapping[str, Mapping[str, str]] | None) -> str:
    """Return only section children for the paragraph-level ``sectPr``."""

    value = _section_xml(overrides)
    root = etree.fromstring(value.encode())
    return "".join(etree.tostring(child, encoding="unicode") for child in root)


__all__ = ["make_notes_docx"]
