from __future__ import annotations

from pathlib import Path

from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection

from inspector.pr1_docx_factory import (
    footnotes_xml,
    numbering_xml,
    paragraph,
    run,
    styles_xml,
    table,
    theme_xml,
    write_docx,
)


def _find_run(paragraph_record: dict, text: str) -> dict:
    return next(item for item in paragraph_record["runs"] if item.get("text_preview") == text)


def test_real_body_order_binds_only_the_first_adjacent_post_table_note(tmp_path: Path) -> None:
    body = (
        paragraph(run("正文", size_pt=10.5))
        + table((run("表格", size_pt=9.0, font="仿宋"),))
        + paragraph(run("注：表下注释", size_pt=7.5, font="宋体"))
    )
    report = inspect_docx(write_docx(tmp_path, body=body))

    blocks = report["body_blocks"]
    assert [item["kind"] for item in blocks] == ["paragraph", "table", "paragraph"]
    assert [item["id"] for item in blocks] == ["p-000000", "table-000000", "p-000001"]
    candidate = report["tables"][0]["note_candidates"][0]
    assert candidate["table_id"] == "table-000000"
    assert candidate["paragraph_id"] == "p-000001"
    assert candidate["adjacent_body_paragraph_id"] == "p-000001"
    assert candidate["distance"] == 1
    assert candidate["reason"] == "first_nonempty_post_table_note"


def test_real_table_and_note_sizes_emit_traceable_relations_and_lint_passes(tmp_path: Path) -> None:
    body = (
        paragraph(run("正文", size_pt=10.5))
        + table((run("表格", size_pt=9.0, font="仿宋"),))
        + paragraph(run("注：表下注释", size_pt=7.5, font="宋体"))
    )
    parts = {
        "word/styles.xml": styles_xml(doc_defaults='<w:docDefaults xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:rPrDefault><w:rPr/></w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault></w:docDefaults>'),
    }
    report = inspect_docx(write_docx(tmp_path, body=body, parts=parts))
    cell_paragraph = report["tables"][0]["cells"][0]["paragraphs"][0]
    body_note = report["paragraphs"][1]
    table_candidate = report["tables"][0]["note_candidates"][0]

    assert cell_paragraph["font_size_relation_to_body"] == "smaller"
    assert cell_paragraph["font_size_relation_to_body_comparison"] == {
        "target_pt": 9.0,
        "baseline_pt": 10.5,
        "source": "effective_run_formatting",
    }
    assert body_note["font_size_relation_to_table"] == "one_cn_size_smaller"
    assert body_note["font_size_relation_to_table_comparison"] == {
        "target_pt": 7.5,
        "baseline_pt": 9.0,
        "source": "effective_run_formatting",
    }
    assert table_candidate["formatting_evidence"]["font_size_relation_to_table"] == "one_cn_size_smaller"
    audit = lint_inspection(report)
    table_finding = next(item for item in audit["findings"] if item["rule_id"] == "ER-MS-TABLE-001")
    note_finding = next(item for item in audit["findings"] if item["rule_id"] == "ER-MS-TABLE-NOTE-001")
    assert table_finding["status"] == "PASS"
    assert note_finding["status"] == "PASS"


def test_real_table_note_binding_rejects_nonadjacent_or_blocked_notes(tmp_path: Path) -> None:
    body = (
        table((run("表格", size_pt=9.0),))
        + paragraph(run("普通段落", size_pt=10.5))
        + paragraph(run("注：不应绑定", size_pt=7.5))
        + table((run("第二张表", size_pt=9.0),))
        + paragraph(run("注：应绑定", size_pt=7.5))
    )
    report = inspect_docx(write_docx(tmp_path, body=body))

    assert report["tables"][0]["note_candidates"] == []
    assert report["tables"][1]["note_candidates"][0]["paragraph_id"] == "p-000002"


def test_real_mixed_run_or_missing_baseline_never_fabricates_a_size_relation(tmp_path: Path) -> None:
    body = (
        paragraph(run("正文A", size_pt=10.5) + run("正文B", size_pt=11.0))
        + table((run("表格", size_pt=9.0),))
        + paragraph(run("注：表下注释", size_pt=7.5))
    )
    report = inspect_docx(write_docx(tmp_path, body=body))

    cell_paragraph = report["tables"][0]["cells"][0]["paragraphs"][0]
    body_note = report["paragraphs"][1]
    assert cell_paragraph["font_size_relation_to_body"] == "unknown"
    assert cell_paragraph["font_size_relation_to_body_comparison"]["source"] == "effective_run_formatting"
    assert body_note["font_size_relation_to_table"] == "one_cn_size_smaller"


def test_default_paragraph_and_character_styles_and_theme_scripts_are_retained(tmp_path: Path) -> None:
    styles = styles_xml(
        default_paragraph="Normal",
        default_character="DefaultParagraphFont",
        styles=(
            '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:type="paragraph" w:styleId="Base">'
            '<w:name w:val="Base"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr>'
            '<w:rPr><w:sz w:val="24"/><w:rFonts w:eastAsiaTheme="majorEastAsia"/></w:rPr></w:style>'
        ),
    )
    # The default paragraph style itself carries the size; there is no pStyle
    # or rStyle on this paragraph/run in the document part.
    styles = styles.replace(
        b'<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>',
        b'<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="21"/><w:rFonts w:eastAsiaTheme="majorEastAsia"/></w:rPr></w:style>',
    )
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=paragraph(run("默认样式")),
            parts={"word/styles.xml": styles, "word/theme/theme1.xml": theme_xml(hans="简体主题", hant="繁體主題")},
        )
    )
    record = report["paragraphs"][0]
    formatting = record["runs"][0]["formatting"]

    assert record["style_id"] is None
    assert formatting["effective"]["size_pt"] == 10.5
    assert formatting["source"]["size_pt"].startswith(("paragraph_style", "default_paragraph_style", "docDefaults"))
    assert formatting["effective"]["font"]["theme"]["eastAsia"] == "majorEastAsia"
    assert formatting["effective"]["font"]["eastAsia"] is None
    assert formatting["source"]["font"]["eastAsia"] == "unknown"


def test_direct_run_formatting_overrides_default_and_default_character_chain(tmp_path: Path) -> None:
    styles = styles_xml(
        default_paragraph="Normal",
        default_character="DefaultParagraphFont",
        styles=(
            '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
            '<w:name w:val="Default Paragraph Font"/><w:rPr><w:sz w:val="22"/></w:rPr></w:style>'
        ),
    )
    body = paragraph(run("覆盖", size_pt=16.0))
    report = inspect_docx(write_docx(tmp_path, body=body, parts={"word/styles.xml": styles}))
    formatting = report["paragraphs"][0]["runs"][0]["formatting"]
    assert formatting["effective"]["size_pt"] == 16.0
    assert formatting["source"]["size_pt"] == "direct"
    assert "default_character_style:DefaultParagraphFont" in formatting["resolution_chain"]


def test_numbering_is_resolved_from_paragraph_style_and_based_on_chain(tmp_path: Path) -> None:
    styles = styles_xml(
        styles=(
            '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:type="paragraph" w:styleId="ListChild">'
            '<w:name w:val="List Child"/><w:basedOn w:val="ListBase"/></w:style>'
            '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:type="paragraph" w:styleId="ListBase">'
            '<w:name w:val="List Base"/><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/></w:numPr></w:pPr></w:style>'
        )
    )
    numbering = numbering_xml(
        '<w:abstractNum xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:pStyle w:val="ListBase"/></w:lvl></w:abstractNum>',
        nums='<w:num xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:numId="4"><w:abstractNumId w:val="0"/></w:num>',
    )
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=paragraph(run("样式编号"), style="ListChild"),
            parts={"word/styles.xml": styles, "word/numbering.xml": numbering},
        )
    )
    numbering_record = report["paragraphs"][0]["numbering"]
    assert numbering_record == {
        "num_id": 4,
        "ilvl": 0,
        "abstract_num_id": 0,
        "format": "decimal",
        "text": "%1.",
        "resolved": True,
        "numbered": True,
        "removes_numbering": False,
        "level_source": "abstract_pStyle",
        "level_evidence": {
            "style_id": "ListBase",
            "style_chain": ["ListChild", "ListBase"],
            "abstract_num_id": 0,
            "ilvl": 0,
        },
        "source": "basedOn:ListBase",
    }


def test_direct_numbering_overrides_style_and_style_cycles_are_bounded(tmp_path: Path) -> None:
    styles = styles_xml(
        styles=(
            '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:type="paragraph" w:styleId="CycleA">'
            '<w:name w:val="Cycle A"/><w:basedOn w:val="CycleB"/><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="4"/></w:numPr></w:pPr></w:style>'
            '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:type="paragraph" w:styleId="CycleB">'
            '<w:name w:val="Cycle B"/><w:basedOn w:val="CycleA"/></w:style>'
        )
    )
    numbering = numbering_xml(
        '<w:abstractNum xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl><w:lvl w:ilvl="1"><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%2)"/></w:lvl></w:abstractNum>',
        nums='<w:num xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:numId="4"><w:abstractNumId w:val="0"/></w:num>',
    )
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=paragraph(run("直接覆盖"), style="CycleA", num_id=4, ilvl=0),
            parts={"word/styles.xml": styles, "word/numbering.xml": numbering},
        )
    )
    record = report["paragraphs"][0]["numbering"]
    assert record["resolved"] is True
    assert record["ilvl"] == 0


def test_footnote_marker_evidence_distinguishes_automatic_custom_literal_and_other(tmp_path: Path) -> None:
    body = (
        paragraph(run("题名") + '<w:r><w:footnoteReference w:id="1"/></w:r>')
        + paragraph(run("自定义") + '<w:r><w:footnoteReference w:id="2" w:customMarkFollows="1"/>' + '<w:t>*</w:t></w:r>')
        + paragraph('<w:r><w:t>*</w:t><w:footnoteReference w:id="3"/></w:r>')
        + paragraph('<w:r><w:footnoteReference w:id="4" w:customMarkFollows="1"/></w:r><w:r><w:t>†</w:t></w:r>')
    )
    report = inspect_docx(
        write_docx(
            tmp_path,
            body=body,
            parts={
                "word/footnotes.xml": footnotes_xml(
                    (1, run("普通")),
                    (2, run("自定义")),
                    (3, run("文字")),
                    (4, run("其他")),
                )
            },
        )
    )
    evidence = [item["footnote_reference_evidence"][0] for item in report["paragraphs"]]

    assert evidence[0]["marker_type"] == "automatic_numbered"
    assert evidence[0]["reference_id"] == 1
    assert evidence[1]["marker_type"] == "custom_mark"
    assert evidence[1]["marker_value"] == "*"
    assert evidence[1]["association"]["order"] == "after"
    assert evidence[2]["marker_type"] == "literal_star"
    assert evidence[2]["association"]["order"] == "before"
    assert evidence[3]["marker_type"] == "other_marker"
    assert evidence[3]["marker_value"] == "†"
    assert evidence[3]["association"]["order"] == "after"


def test_compact_core_properties_are_redacted_even_when_text_is_expanded(tmp_path: Path) -> None:
    path = write_docx(
        tmp_path,
        body=paragraph(run("正文")),
        core_properties={
            "creator": "Private Author",
            "last_modified_by": "Private Editor",
            "title": "Private Title",
            "subject": "Private Subject",
            "description": "Private Description",
            "keywords": "private-keywords",
        },
    )
    compact = inspect_docx(path)
    expanded = inspect_docx(path, include_text=True)

    for report in (compact, expanded):
        rendered = repr(report)
        for secret in ("Private Author", "Private Editor", "Private Title", "Private Subject", "Private Description", "private-keywords"):
            assert secret not in rendered
        assert report["core_properties"]["creator"]["present"] is True
        assert report["core_properties"]["last_modified_by"]["present"] is True
        assert "title" not in report["core_properties"]
        assert "subject" not in report["core_properties"]
        assert "description" not in report["core_properties"]
        assert "keywords" not in report["core_properties"]


def test_metadata_is_only_exposed_by_explicit_api_flag(tmp_path: Path) -> None:
    path = write_docx(tmp_path, body=paragraph(run("正文")), core_properties={"title": "Explicit Title"})
    report = inspect_docx(path, include_metadata=True)
    assert report["core_properties"]["title"] == "Explicit Title"
