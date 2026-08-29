"""Small real-DOCX producers used by the PR #1 citation regressions."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document
from lxml import etree


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


def _qname(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def make_footnote_docx(tmp_path: Path, text: str | None, filename: str = "citation-footnote.docx") -> Path:
    """Build a DOCX whose footnote text comes from a real ``footnotes.xml`` part.

    ``None`` deliberately omits the footnote part and reference.  Every other
    value is serialized as a note paragraph and linked from the body so tests
    exercise ``inspect_docx`` rather than injecting inspection dictionaries.
    """

    path = Path(tmp_path) / filename
    document = Document()
    document.add_paragraph("正文内容。")
    document.save(path)
    if text is None:
        return path

    with ZipFile(path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}

    relationships = etree.fromstring(members["word/_rels/document.xml.rels"])
    content_types = etree.fromstring(members["[Content_Types].xml"])
    document_xml = etree.fromstring(members["word/document.xml"])

    existing_ids = [
        int(item.get("Id", "rId0")[3:])
        for item in relationships
        if item.get("Id", "").startswith("rId") and item.get("Id", "")[3:].isdigit()
    ]
    relationship_id = f"rId{max(existing_ids + [0]) + 1}"
    relationship = etree.Element(_qname(REL_NS, "Relationship"))
    relationship.set("Id", relationship_id)
    relationship.set("Type", f"{OFFICE_REL_NS}/footnotes")
    relationship.set("Target", "footnotes.xml")
    relationships.append(relationship)

    override = etree.Element(_qname(CT_NS, "Override"))
    override.set("PartName", "/word/footnotes.xml")
    override.set("ContentType", "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml")
    content_types.append(override)

    body_paragraph = document_xml.find(f".//{_qname(W_NS, 'body')}/{_qname(W_NS, 'p')}")
    assert body_paragraph is not None
    reference_run = etree.SubElement(body_paragraph, _qname(W_NS, "r"))
    reference = etree.SubElement(reference_run, _qname(W_NS, "footnoteReference"))
    reference.set(_qname(W_NS, "id"), "1")

    footnotes = etree.Element(_qname(W_NS, "footnotes"), nsmap={"w": W_NS})
    separator = etree.SubElement(footnotes, _qname(W_NS, "footnote"))
    separator.set(_qname(W_NS, "type"), "separator")
    separator.set(_qname(W_NS, "id"), "-1")
    etree.SubElement(separator, _qname(W_NS, "p"))
    continuation = etree.SubElement(footnotes, _qname(W_NS, "footnote"))
    continuation.set(_qname(W_NS, "type"), "continuationSeparator")
    continuation.set(_qname(W_NS, "id"), "0")
    etree.SubElement(continuation, _qname(W_NS, "p"))
    note = etree.SubElement(footnotes, _qname(W_NS, "footnote"))
    note.set(_qname(W_NS, "id"), "1")
    paragraph = etree.SubElement(note, _qname(W_NS, "p"))
    run = etree.SubElement(paragraph, _qname(W_NS, "r"))
    text_node = etree.SubElement(run, _qname(W_NS, "t"))
    text_node.text = text

    members["word/_rels/document.xml.rels"] = etree.tostring(
        relationships, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    members["[Content_Types].xml"] = etree.tostring(
        content_types, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    members["word/document.xml"] = etree.tostring(
        document_xml, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    members["word/footnotes.xml"] = etree.tostring(
        footnotes, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as target:
        for name, value in members.items():
            target.writestr(name, value)
    return path


__all__ = ["make_footnote_docx"]
