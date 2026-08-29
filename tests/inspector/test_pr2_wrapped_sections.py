from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from lxml import etree

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection

from tests.linter.pr2_notes_docx import W, make_notes_docx


def _qname(local: str) -> str:
    return f"{{{W}}}{local}"


def _wrap_first_body_paragraph(path: Path, wrapper: str) -> Path:
    with ZipFile(path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    root = etree.fromstring(members["word/document.xml"])
    body = root.find(_qname("body"))
    assert body is not None
    paragraph = body.find(_qname("p"))
    assert paragraph is not None
    position = body.index(paragraph)
    body.remove(paragraph)
    container = etree.Element(_qname(wrapper))
    content = (
        etree.SubElement(container, _qname("sdtContent"))
        if wrapper == "sdt"
        else container
    )
    content.append(paragraph)
    body.insert(position, container)
    members["word/document.xml"] = etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as target:
        for name, value in members.items():
            target.writestr(name, value)
    return path


@pytest.mark.parametrize("wrapper", ["sdt", "customXml"])
def test_wrapped_section_break_uses_same_body_order_for_note_properties(
    tmp_path: Path,
    wrapper: str,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "Smith, 2020"),),
        references=((0, "footnote", 1),),
        settings={"footnote": {"numFmt": "decimal"}},
        section_overrides=(
            {"footnote": {"numFmt": "lowerRoman"}},
            None,
        ),
        body_texts=("包装节", "第二节"),
        filename="wrapped-section.docx",
    )
    inspection = inspect_docx(
        _wrap_first_body_paragraph(path, wrapper),
        include_text=True,
    )
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)

    first = inspection["paragraphs"][0]
    reference = first["footnote_reference_evidence"][0]
    assert first["section_index"] == 0
    assert reference["section_index"] == 0
    assert reference["section_override"]["numFmt"] == "lowerRoman"
    assert reference["effective_properties"]["numFmt"] == "lowerRoman"
    assert classification["summary"]["note_count"] == 1
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-CIT-GENERAL-001"
    )
    assert finding["status"] == "ERROR"
    assert finding["target"]["footnote_id"] == 1
