"""Focused regression tests for Inspector/OOXML review findings.

These fixtures are deliberately small and package only the OOXML parts needed
for the behavior under test.  They avoid relying on a real/private manuscript
while still exercising the ZIP and XML boundaries that the Inspector owns.
"""

from __future__ import annotations

from pathlib import Path
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.models.inspection import DocxInspectionError


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
O_NS = "urn:schemas-microsoft-com:office:office"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _document(body: str) -> bytes:
    return (
        f'<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:o="{O_NS}" xmlns:m="{M}">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    ).encode()


def _content_types(*overrides: tuple[str, str]) -> bytes:
    values = "".join(
        f'<Override PartName="{part}" ContentType="{content_type}"/>'
        for part, content_type in overrides
    )
    return (
        f'<Types xmlns="{CT}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        f"{values}</Types>"
    ).encode()


def _rels(*relations: tuple[str, str, str, str | None]) -> bytes:
    values = []
    for rel_id, rel_type, target, target_mode in relations:
        mode = f' TargetMode="{target_mode}"' if target_mode else ""
        values.append(f'<Relationship Id="{rel_id}" Type="{rel_type}" Target="{target}"{mode}/>')
    return (
        f'<Relationships xmlns="{PR}">' + "".join(values) + "</Relationships>"
    ).encode()


def _write_docx(
    tmp_path: Path,
    *,
    body: str,
    filename: str = "fixture.docx",
    parts: dict[str, bytes] | None = None,
    relations: tuple[tuple[str, str, str, str | None], ...] = (),
    content_type_overrides: tuple[tuple[str, str], ...] = (),
) -> Path:
    path = tmp_path / filename
    members = {
        "[Content_Types].xml": _content_types(*content_type_overrides),
        "word/document.xml": _document(body),
    }
    if relations:
        members["word/_rels/document.xml.rels"] = _rels(*relations)
    if parts:
        members.update(parts)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_pageref_alone_does_not_make_an_ordinary_paragraph_toc(tmp_path: Path) -> None:
    body = (
        '<w:p><w:r><w:t>普通段落</w:t></w:r></w:p>'
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText>PAGEREF _Ref123</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )
    report = inspect_docx(_write_docx(tmp_path, body=body))

    assert report["summary"]["field_counts"]["PAGEREF"] == 1
    assert report["paragraphs"][1]["toc"]["is_toc"] is False
    assert report["paragraphs"][1]["in_toc"] is False


def test_pageref_after_toc_field_is_in_established_toc_boundary(tmp_path: Path) -> None:
    body = (
        '<w:p><w:pPr><w:pStyle w:val="TOC1"/></w:pPr>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText>TOC \\o "1-3"</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        '<w:p><w:r><w:t>第一章</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText>PAGEREF _Toc123</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        '<w:p><w:r><w:t>正文</w:t></w:r></w:p>'
    )
    report = inspect_docx(_write_docx(tmp_path, body=body))

    assert [p["in_toc"] for p in report["paragraphs"]] == [True, True, False]


def test_complex_field_fragments_are_assembled_and_boundary_is_retained(tmp_path: Path) -> None:
    body = (
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText>TO</w:instrText></w:r>'
        '<w:r><w:instrText>C \\o "1-3"</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>目录结果</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )
    report = inspect_docx(_write_docx(tmp_path, body=body), include_text=True)
    field = report["paragraphs"][0]["fields"][0]

    assert field["type"] == "TOC"
    assert field["instruction"] == 'TOC \\o "1-3"'
    assert field["boundary"]["complete"] is True
    assert field["boundary"]["begin"] is True
    assert field["boundary"]["separate"] is True
    assert field["boundary"]["end"] is True
    assert field["instruction_fragments"] == ["TO", 'C \\o "1-3"']
    assert report["fields"]["counts"]["TOC"] == 1


def test_mixed_simple_and_complex_fields_stay_in_document_order(tmp_path: Path) -> None:
    body = (
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText>PAGEREF _Ref123</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:fldSimple w:instr="PAGE"><w:r><w:t>2</w:t></w:r></w:fldSimple></w:p>'
    )
    report = inspect_docx(_write_docx(tmp_path, body=body), include_text=True)

    assert [field["type"] for field in report["paragraphs"][0]["fields"]] == ["PAGEREF", "PAGE"]


def test_default_field_and_hyperlink_evidence_is_bounded_and_path_safe(tmp_path: Path) -> None:
    body = (
        '<w:p><w:hyperlink r:id="rId1"><w:r><w:t>链接</w:t></w:r></w:hyperlink>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText>HYPERLINK "/Users/alice/private/report.docx"</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    )
    relations = (
        (
            "rId1",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
            "/Users/alice/private/report.docx",
            "External",
        ),
    )
    compact = inspect_docx(
        _write_docx(tmp_path, body=body, relations=relations),
    )
    compact_json = repr(compact)

    assert "/Users/alice" not in compact_json
    field = compact["paragraphs"][0]["fields"][0]
    assert "instruction" not in field
    assert len(field["instruction_preview"]) <= 80
    assert len(compact["hyperlinks"]["items"][0]["target_preview"]) <= 80
    assert "target" not in compact["hyperlinks"]["items"][0]

    expanded = inspect_docx(_write_docx(tmp_path, body=body, relations=relations), include_text=True)
    assert expanded["paragraphs"][0]["fields"][0]["instruction"].startswith("HYPERLINK")


def test_effective_paragraph_properties_include_style_basedon_defaults_and_sources(tmp_path: Path) -> None:
    styles = (
        f'<w:styles xmlns:w="{W}">'
        '<w:docDefaults><w:pPrDefault><w:pPr>'
        '<w:jc w:val="both"/><w:spacing w:after="80"/><w:keepNext w:val="0"/>'
        '</w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:styleId="Base"><w:name w:val="Base"/>'
        '<w:pPr><w:ind w:left="120"/><w:spacing w:before="100"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Child"><w:name w:val="Child"/>'
        '<w:basedOn w:val="Base"/><w:pPr><w:jc w:val="center"/>'
        '<w:keepNext w:val="1"/></w:pPr></w:style>'
        '</w:styles>'
    ).encode()
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Child"/><w:ind w:right="240"/>'
        '<w:spacing w:after="200"/></w:pPr><w:r><w:t>属性</w:t></w:r></w:p>'
    )
    report = inspect_docx(
        _write_docx(tmp_path, body=body, parts={"word/styles.xml": styles}),
    )
    paragraph = report["paragraphs"][0]
    properties = paragraph["properties"]

    assert properties["raw"]["indent"]["right_twips"] == 240
    assert properties["effective"]["alignment"] == "center"
    assert properties["effective"]["indent"] == {
        "left_twips": 120,
        "right_twips": 240,
        "first_line_twips": None,
        "hanging_twips": None,
    }
    assert properties["effective"]["spacing"]["before_twips"] == 100
    assert properties["effective"]["spacing"]["after_twips"] == 200
    assert properties["effective"]["keep_with_next"] is True
    assert properties["source"]["alignment"] == "paragraph_style:Child"
    assert properties["source"]["indent"]["left_twips"] == "basedOn:Base"
    assert properties["source"]["spacing"]["after_twips"] == "direct"


def test_body_paragraph_emits_footnote_reference_id_and_marker_evidence(tmp_path: Path) -> None:
    body = '<w:p><w:r><w:t>题目：标题</w:t></w:r><w:r><w:footnoteReference w:id="7"/></w:r></w:p>'
    relations = (
        (
            "rId1",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
            "footnotes.xml",
            None,
        ),
    )
    notes = f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="7"><w:p><w:r><w:t>注释</w:t></w:r></w:p></w:footnote></w:footnotes>'.encode()
    report = inspect_docx(
        _write_docx(
            tmp_path,
            body=body,
            relations=relations,
            parts={"word/footnotes.xml": notes},
            content_type_overrides=(
                (
                    "/word/footnotes.xml",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
                ),
            ),
        )
    )
    paragraph = report["paragraphs"][0]

    assert paragraph["footnote_references"] == [7]
    assert paragraph["has_footnote_reference"] is True
    assert paragraph["footnote_reference_evidence"][0]["marker"] == "footnoteReference"
    assert paragraph["footnote_reference_evidence"][0]["id"] == 7


def test_actual_note_items_include_paragraph_and_run_formatting_but_exclude_separators(tmp_path: Path) -> None:
    body = '<w:p><w:r><w:t>正文</w:t></w:r></w:p>'
    relations = (
        (
            "rId1",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes",
            "footnotes.xml",
            None,
        ),
    )
    notes = f'''<w:footnotes xmlns:w="{W}">
      <w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:t>分隔符</w:t></w:r></w:p></w:footnote>
      <w:footnote w:id="2"><w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>真实脚注</w:t></w:r></w:p></w:footnote>
    </w:footnotes>'''.encode()
    report = inspect_docx(
        _write_docx(
            tmp_path,
            body=body,
            relations=relations,
            parts={"word/footnotes.xml": notes},
            content_type_overrides=(
                (
                    "/word/footnotes.xml",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml",
                ),
            ),
        )
    )
    info = report["notes"]["footnotes"]

    assert info["actual_count"] == 1
    assert info["separator_count"] == 1
    assert [item["id"] for item in info["items"]] == [2]
    note_paragraph = info["items"][0]["paragraphs"][0]
    assert note_paragraph["text_preview"] == "真实脚注"
    assert note_paragraph["runs"][0]["formatting"]["effective"]["bold"] is True
    assert info["paragraphs"][0]["formatting"]["effective"]["bold"] is True
    assert "分隔符" not in repr(info)


def test_tables_inside_body_wrappers_are_in_document_order_without_nested_duplicates(tmp_path: Path) -> None:
    body = (
        '<w:p><w:r><w:t>前</w:t></w:r></w:p>'
        '<w:sdt><w:sdtContent><w:tbl><w:tr><w:tc><w:p><w:r><w:t>一</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:sdtContent></w:sdt>'
        '<w:customXml><w:tbl><w:tr><w:tc><w:p><w:r><w:t>二</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:customXml>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>三</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>嵌套</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:tc></w:tr></w:tbl>'
    )
    report = inspect_docx(_write_docx(tmp_path, body=body))

    assert report["summary"]["table_count"] == 3
    assert [table["cells"][0]["paragraphs"][0]["text_preview"] for table in report["tables"]] == ["一", "二", "三"]


def test_numbering_resolves_abstract_number_zero_and_handles_override_and_missing_level(tmp_path: Path) -> None:
    numbering = f'''<w:numbering xmlns:w="{W}">
      <w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>
      <w:num w:numId="4"><w:abstractNumId w:val="0"/><w:lvlOverride w:ilvl="0"><w:lvl><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%1)"/></w:lvl></w:lvlOverride></w:num>
      <w:num w:numId="5"><w:abstractNumId w:val="0"/></w:num>
    </w:numbering>'''.encode()
    body = (
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/></w:numPr></w:pPr><w:r><w:t>覆盖</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="5"/></w:numPr></w:pPr><w:r><w:t>缺失</w:t></w:r></w:p>'
    )
    report = inspect_docx(_write_docx(tmp_path, body=body, parts={"word/numbering.xml": numbering}))

    assert report["paragraphs"][0]["numPr"] == {
        "num_id": 4,
        "ilvl": 0,
        "abstract_num_id": 0,
        "format": "lowerLetter",
        "text": "%1)",
        "resolved": True,
    }
    assert report["paragraphs"][1]["numPr"]["abstract_num_id"] == 0
    assert report["paragraphs"][1]["numPr"]["resolved"] is False


@pytest.mark.parametrize(
    ("prog_id", "target", "content_type", "expected"),
    [
        ("MathType 7.0", "embeddings/math.bin", "application/vnd.ms-package", "mathtype"),
        ("Package", "embeddings/ole.bin", "application/vnd.openxmlformats-officedocument.oleObject", "ole_unknown"),
    ],
)
def test_ole_and_mathtype_evidence_is_distinguished(
    tmp_path: Path,
    prog_id: str,
    target: str,
    content_type: str,
    expected: str,
) -> None:
    body = (
        '<w:p><w:r><w:object><o:OLEObject r:id="rId1" ProgID="'
        + prog_id
        + '" Type="Embed"/></w:object></w:r></w:p>'
    )
    relations = (("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject", target, None),)
    report = inspect_docx(
        _write_docx(
            tmp_path,
            body=body,
            relations=relations,
            parts={"word/" + target: b"object bytes"},
            content_type_overrides=(("/word/" + target, content_type),),
        )
    )

    assert report["equations"]["editors"][expected] == 1
    assert report["equations"]["items"][0]["editor"] == expected
    assert report["equations"]["editors"]["mathtype_or_ole"] == 1


def test_corrupt_optional_declared_part_is_not_treated_as_absent(tmp_path: Path) -> None:
    relations = (
        (
            "rId1",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
            "comments.xml",
            None,
        ),
    )
    path = _write_docx(
        tmp_path,
        body='<w:p><w:r><w:t>正文</w:t></w:r></w:p>',
        relations=relations,
        parts={"word/comments.xml": b"<w:comments xmlns:w=\"" + W.encode() + b"\"><broken>"},
        content_type_overrides=(("/word/comments.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"),),
    )

    with pytest.raises(DocxInspectionError, match="comments.xml"):
        inspect_docx(path)


def test_duplicate_zip_members_are_rejected_before_inspection(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.docx"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", _content_types())
            archive.writestr("word/document.xml", _document("<w:p/>"))
            archive.writestr("word/document.xml", _document("<w:p/>"))

    with pytest.raises(DocxInspectionError, match="duplicate"):
        inspect_docx(path)


def test_excessive_image_pixels_are_rejected_before_pixel_list_allocation(tmp_path: Path) -> None:
    from economic_research_formatter.docx.images import MAX_IMAGE_PIXELS

    # A tiny PNG header with a deliberately oversized logical canvas is enough
    # to exercise the pre-conversion guard; PIL never allocates the full image.
    from io import BytesIO
    from PIL import Image

    stream = BytesIO()
    image = Image.new("RGB", (int(MAX_IMAGE_PIXELS**0.5) + 2, int(MAX_IMAGE_PIXELS**0.5) + 2), "white")
    image.save(stream, format="PNG")
    image_bytes = stream.getvalue()
    drawing = (
        '<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData>'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:blipFill>'
        '<a:blip r:embed="rId1"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>'
        '</wp:inline></w:drawing></w:r></w:p>'
    )
    path = _write_docx(
        tmp_path,
        body=drawing,
        relations=(("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", "media/huge.png", None),),
        parts={"word/media/huge.png": image_bytes},
        content_type_overrides=(("/word/media/huge.png", "image/png"),),
    )

    with pytest.raises(DocxInspectionError, match="pixel"):
        inspect_docx(path)
