from __future__ import annotations

from pathlib import Path

import pytest

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection
from economic_research_formatter.models.notes import note_linkage

from .pr2_notes_docx import make_notes_docx


def _rule_findings(audit: dict, rule_id: str) -> list[dict]:
    return [item for item in audit["findings"] if item["rule_id"] == rule_id]


def _chain(path: Path, *, include_text: bool = True) -> tuple[dict, dict, dict]:
    inspection = inspect_docx(path, include_text=include_text)
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)
    return inspection, classification, audit


def test_pure_literature_endnote_is_error_with_endnote_target(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "Smith, 2020"),),
        references=((0, "endnote", 3),),
    )

    inspection, classification, audit = _chain(path)

    assert inspection["notes"]["endnotes"]["actual_count"] == 1
    # The classifier stage is intentionally exercised even while its separate
    # public note_items integration is owned by the parent task.
    assert classification["summary"]["paragraph_count"] == len(inspection["paragraphs"])
    findings = _rule_findings(audit, "ER-CIT-GENERAL-001")
    assert len(findings) == 1
    assert findings[0]["status"] == "ERROR"
    assert findings[0]["target"]["kind"] == "endnote"
    assert findings[0]["target"]["endnote_id"] == 3
    assert findings[0]["observed"]["endnote_semantics"] == "pure_literature_index"
    assert "footnote_semantics" not in findings[0]["observed"]
    assert "footnote_paragraph_ids" not in findings[0]["observed"]


def test_content_endnote_with_inline_author_year_is_checked_as_content_note(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "This note explains the sample (Smith, 2020)."),),
        references=((0, "endnote", 3),),
    )

    _, _, audit = _chain(path)

    general = _rule_findings(audit, "ER-CIT-GENERAL-001")
    content = _rule_findings(audit, "ER-REF-CONTENT-FOOTNOTE-001")
    assert {item["status"] for item in general} == {"NOT_APPLICABLE"}
    assert len(content) == 1
    assert content[0]["status"] == "PASS"
    assert content[0]["target"]["kind"] == "endnote"
    assert content[0]["target"]["endnote_id"] == 3


def test_ordinary_content_endnote_is_not_misreported_as_literature_index(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "This note explains the sample."),),
        references=((0, "endnote", 3),),
    )

    _, _, audit = _chain(path)

    assert {item["status"] for item in _rule_findings(audit, "ER-CIT-GENERAL-001")} == {"NOT_APPLICABLE"}
    assert {item["status"] for item in _rule_findings(audit, "ER-REF-CONTENT-FOOTNOTE-001")} == {"NOT_APPLICABLE"}


def test_ambiguous_endnote_requires_manual_review_and_keeps_endnote_identity(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "See Smith, 2020 for details."),),
        references=((0, "endnote", 3),),
    )

    _, _, audit = _chain(path)

    for rule_id in ("ER-CIT-GENERAL-001", "ER-REF-CONTENT-FOOTNOTE-001"):
        finding = _rule_findings(audit, rule_id)[0]
        assert finding["status"] == "MANUAL_REVIEW"
        assert finding["target"]["kind"] == "endnote"
        assert finding["target"]["endnote_id"] == 3


def test_footnote_and_endnote_are_linted_independently(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(
            ("footnote", 1, "Jones, 2019"),
            ("endnote", 3, "Smith, 2020"),
        ),
        references=((0, "footnote", 1), (1, "endnote", 3)),
        body_texts=("正文脚注", "正文尾注"),
    )

    inspection, _, audit = _chain(path)

    assert inspection["summary"]["footnote_count"] == 1
    assert inspection["summary"]["endnote_count"] == 1
    findings = _rule_findings(audit, "ER-CIT-GENERAL-001")
    assert {(item["target"]["kind"], item["target"].get("footnote_id", item["target"].get("endnote_id"))) for item in findings} == {
        ("footnote", 1),
        ("endnote", 3),
    }
    assert all(item["status"] == "ERROR" for item in findings)


def test_document_without_notes_remains_not_applicable(tmp_path: Path) -> None:
    path = make_notes_docx(tmp_path, notes=(), references=())

    _, _, audit = _chain(path)

    findings = _rule_findings(audit, "ER-CIT-GENERAL-001")
    assert len(findings) == 1
    assert findings[0]["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize(
    ("kind", "note_id"),
    [("footnote", 1), ("endnote", 3)],
)
def test_long_note_preview_only_evidence_requires_manual_review_for_hidden_citation(
    tmp_path: Path,
    kind: str,
    note_id: int,
) -> None:
    text = "This note contains a long explanation before the citation. " * 3 + "Smith (2020)"
    path = make_notes_docx(
        tmp_path,
        notes=((kind, note_id, text),),
        references=((0, kind, note_id),),
        filename=f"long-{kind}-preview.docx",
    )

    inspection, _, audit = _chain(path, include_text=False)
    note = inspection["notes"][f"{kind}s"]["items"][0]
    paragraph = note["paragraphs"][0]

    assert note["preview_only"] is True
    assert note["text_truncated"] is True
    assert note["text_length"] == len(text)
    assert paragraph["preview_only"] is True
    assert paragraph["text_truncated"] is True
    assert paragraph["text_length"] == len(text)
    assert "text" not in note
    assert "text" not in paragraph

    expected_kind_id = {"kind": kind, f"{kind}_id": note_id}
    for rule_id in ("ER-CIT-GENERAL-001", "ER-REF-CONTENT-FOOTNOTE-001"):
        findings = _rule_findings(audit, rule_id)
        assert len(findings) == 1
        assert findings[0]["status"] == "MANUAL_REVIEW"
        assert findings[0]["target"]["kind"] == expected_kind_id["kind"]
        assert findings[0]["target"][f"{kind}_id"] == expected_kind_id[f"{kind}_id"]


@pytest.mark.parametrize(
    ("kind", "note_id"),
    [("footnote", 1), ("endnote", 3)],
)
def test_long_note_with_full_text_keeps_citation_detection(
    tmp_path: Path,
    kind: str,
    note_id: int,
) -> None:
    text = "This note contains a long explanation before the citation. " * 3 + "Smith (2020)"
    path = make_notes_docx(
        tmp_path,
        notes=((kind, note_id, text),),
        references=((0, kind, note_id),),
        filename=f"long-{kind}-full.docx",
    )

    inspection, _, audit = _chain(path, include_text=True)
    note = inspection["notes"][f"{kind}s"]["items"][0]

    assert note["preview_only"] is False
    assert note["text_truncated"] is True
    assert note["text"] == text
    general = _rule_findings(audit, "ER-CIT-GENERAL-001")
    content = _rule_findings(audit, "ER-REF-CONTENT-FOOTNOTE-001")
    assert {item["status"] for item in general} == {"NOT_APPLICABLE"}
    assert len(content) == 1
    assert content[0]["status"] == "PASS"
    assert content[0]["target"]["kind"] == kind
    assert content[0]["target"][f"{kind}_id"] == note_id


def test_document_wide_footnote_restart_applies_without_section_override(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "普通脚注"),),
        references=((0, "footnote", 1),),
        settings={"footnote": {"numRestart": "eachPage"}},
    )

    inspection, _, audit = _chain(path)
    reference = inspection["paragraphs"][0]["footnote_reference_evidence"][0]
    collection = inspection["notes"]["footnotes"]

    assert reference["note_properties"]["numRestart"] == "eachPage"
    assert reference["note_properties"]["property_evidence"]["numRestart"]["source"] == "settings"
    assert reference["note_properties"]["section_index"] == 0
    assert reference["section_index"] == 0
    assert reference["document_wide_properties"]["numRestart"] == "eachPage"
    assert reference["section_override"] == {}
    assert reference["effective_properties"]["numRestart"] == "eachPage"
    assert reference["property_evidence"]["numRestart"]["source"] == "settings"
    assert collection["num_restarts"] == ["eachPage"]
    assert collection["section_properties"][0]["property_evidence"]["numRestart"]["source"] == "settings"
    assert inspection["notes"]["footnotes"]["document_wide_properties"]["numRestart"] == "eachPage"
    assert inspection["notes"]["footnotes"]["num_restarts"] == ["eachPage"]
    restart_findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")
    assert len(restart_findings) == 1
    assert restart_findings[0]["status"] == "PASS"
    assert restart_findings[0]["observed"]["restarts_normalized"] == ["each_page"]


def test_document_wide_decimal_and_second_section_lower_roman_bind_per_reference(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "第一节脚注"), ("footnote", 2, "第二节脚注")),
        references=((0, "footnote", 1), (1, "footnote", 2)),
        settings={"footnote": {"numFmt": "decimal"}},
        section_overrides=(None, {"footnote": {"numFmt": "lowerRoman"}}),
        body_texts=("第一节", "第二节"),
    )

    inspection, _, _ = _chain(path)
    first = inspection["paragraphs"][0]["footnote_reference_evidence"][0]
    second = inspection["paragraphs"][1]["footnote_reference_evidence"][0]

    assert first["note_properties"]["numFmt"] == "decimal"
    assert second["note_properties"]["numFmt"] == "lowerRoman"
    assert first["note_properties"]["section_index"] == 0
    assert second["note_properties"]["section_index"] == 1
    assert first["note_properties"]["property_evidence"]["numFmt"]["source"] == "settings"
    assert second["note_properties"]["property_evidence"]["numFmt"]["source"] == "section"
    assert second["note_properties"]["section_override"]["numFmt"] == "lowerRoman"
    sections = inspection["notes"]["footnotes"]["section_properties"]
    assert [item["effective_properties"]["numFmt"] for item in sections] == ["decimal", "lowerRoman"]


def test_footnote_restart_is_linted_per_owning_section_reference(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "第一节脚注"), ("footnote", 2, "第二节脚注")),
        references=((0, "footnote", 1), (1, "footnote", 2)),
        settings={"footnote": {"numRestart": "eachPage"}},
        section_overrides=(None, {"footnote": {"numRestart": "continuous"}}),
        body_texts=("第一节", "第二节"),
    )

    _, _, audit = _chain(path)
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")

    assert [item["target"]["section_index"] for item in findings] == [0, 1]
    assert [item["status"] for item in findings] == ["PASS", "ERROR"]
    assert [item["observed"]["restart_normalized"] for item in findings] == [
        "each_page",
        "continuous",
    ]


def test_section_without_footnote_reference_does_not_affect_restart_lint(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "第一节脚注"),),
        references=((0, "footnote", 1),),
        settings={"footnote": {"numRestart": "eachPage"}},
        section_overrides=(None, {"footnote": {"numRestart": "continuous"}}),
        body_texts=("第一节", "第二节"),
    )

    inspection, _, audit = _chain(path)
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")

    assert inspection["notes"]["footnotes"]["num_restarts"] == ["eachPage"]
    assert len(findings) == 1
    assert findings[0]["status"] == "PASS"
    assert findings[0]["target"]["section_index"] == 0


def test_footnote_restart_rule_evaluates_each_reference_owning_section(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "第一节脚注"), ("footnote", 2, "第二节脚注")),
        references=((0, "footnote", 1), (1, "footnote", 2)),
        settings={"footnote": {"numRestart": "eachPage"}},
        section_overrides=(
            None,
            {"footnote": {"numRestart": "continuous"}},
        ),
        body_texts=("第一节", "第二节"),
    )

    _, _, audit = _chain(path)
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")

    assert len(findings) == 2
    by_section = {item["target"]["section_index"]: item for item in findings}
    assert by_section[0]["status"] == "PASS"
    assert by_section[0]["target"]["footnote_id"] == 1
    assert by_section[0]["observed"]["restart_normalized"] == "each_page"
    assert by_section[1]["status"] == "ERROR"
    assert by_section[1]["target"]["footnote_id"] == 2
    assert by_section[1]["observed"]["restart_normalized"] == "continuous"


@pytest.mark.parametrize("wrapped", [False, True])
def test_table_cell_footnote_reference_uses_its_owning_section_properties(
    tmp_path: Path,
    wrapped: bool,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "表格脚注"),),
        references=(),
        settings={"footnote": {"numRestart": "eachPage"}},
        table_reference=("footnote", 1),
        wrap_table_reference=wrapped,
    )

    inspection, _, audit = _chain(path)
    cell = inspection["tables"][0]["cells"][0]["paragraphs"][0]
    reference = cell["footnote_reference_evidence"][0]

    assert cell["section_index"] == 0
    assert reference["section_index"] == 0
    assert reference["effective_properties"]["numRestart"] == "eachPage"
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")
    assert len(findings) == 1
    assert findings[0]["status"] == "PASS"
    assert findings[0]["target"]["paragraph_id"] == cell["id"]


def test_wrapped_table_row_preserves_note_reference_evidence(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "表格脚注"),),
        references=(),
        settings={"footnote": {"numRestart": "eachPage"}},
        table_reference=("footnote", 1),
        wrap_table_row=True,
    )

    inspection, _, audit = _chain(path)
    table = inspection["tables"][0]
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")

    assert table["row_count"] == 1
    assert len(table["cells"]) == 1
    assert len(table["cells"][0]["paragraphs"]) == 1
    assert len(table["cells"][0]["paragraphs"][0]["footnote_reference_evidence"]) == 1
    assert findings[0]["status"] == "PASS"


def test_table_and_body_restart_summary_follows_document_order(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "表格脚注"), ("footnote", 2, "正文脚注")),
        references=((1, "footnote", 2),),
        settings={"footnote": {"numRestart": "eachPage"}},
        section_overrides=(None, {"footnote": {"numRestart": "continuous"}}),
        body_texts=("第一节结束", "第二节正文"),
        table_reference=("footnote", 1),
        table_before_paragraph_index=0,
    )

    inspection, _, audit = _chain(path)
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")

    assert inspection["notes"]["footnotes"]["num_restarts"] == [
        "eachPage",
        "continuous",
    ]
    assert [item["target"]["section_index"] for item in findings] == [0, 1]


def test_wrapped_section_break_preserves_section_order_and_note_cascade(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "第一节脚注"), ("footnote", 2, "第二节脚注")),
        references=((0, "footnote", 1), (1, "footnote", 2)),
        settings={"footnote": {"numRestart": "eachPage"}},
        section_overrides=(None, {"footnote": {"numRestart": "continuous"}}),
        body_texts=("第一节", "第二节"),
        wrap_first_section_break=True,
    )

    inspection, _, audit = _chain(path)
    first = inspection["paragraphs"][0]["footnote_reference_evidence"][0]
    second = inspection["paragraphs"][1]["footnote_reference_evidence"][0]
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")

    assert inspection["summary"]["section_count"] == 2
    assert first["section_index"] == 0
    assert first["effective_properties"]["numRestart"] == "eachPage"
    assert second["section_index"] == 1
    assert second["effective_properties"]["numRestart"] == "continuous"
    assert [item["status"] for item in findings] == ["PASS", "ERROR"]


def test_endnote_document_wide_properties_and_section_override_are_separate(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "第一节尾注"), ("endnote", 4, "第二节尾注")),
        references=((0, "endnote", 3), (1, "endnote", 4)),
        settings={"endnote": {"numFmt": "upperRoman", "numRestart": "continuous"}},
        section_overrides=(None, {"endnote": {"numFmt": "lowerRoman"}}),
        body_texts=("第一节", "第二节"),
    )

    inspection, _, _ = _chain(path)
    first = inspection["paragraphs"][0]["endnote_reference_evidence"][0]
    second = inspection["paragraphs"][1]["endnote_reference_evidence"][0]

    assert first["note_properties"]["numFmt"] == "upperRoman"
    assert first["note_properties"]["numRestart"] == "continuous"
    assert second["note_properties"]["numFmt"] == "lowerRoman"
    assert second["note_properties"]["numRestart"] == "continuous"
    assert first["note_properties"]["property_evidence"]["numFmt"]["source"] == "settings"
    assert second["note_properties"]["property_evidence"]["numFmt"]["source"] == "section"
    sections = inspection["notes"]["endnotes"]["section_properties"]
    assert [item["effective_properties"]["numFmt"] for item in sections] == ["upperRoman", "lowerRoman"]


def test_note_properties_fall_back_to_explicit_ooxml_defaults(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "默认脚注"),),
        references=((0, "footnote", 1),),
    )

    inspection, _, audit = _chain(path)
    reference = inspection["paragraphs"][0]["footnote_reference_evidence"][0]
    properties = reference["note_properties"]

    assert properties["numFmt"] == "decimal"
    assert properties["numStart"] == "1"
    assert properties["numRestart"] == "continuous"
    assert properties["property_evidence"]["numFmt"]["source"] == "default"
    assert properties["property_evidence"]["numStart"]["source"] == "default"
    assert properties["property_evidence"]["numRestart"]["source"] == "default"
    restart = _rule_findings(audit, "ER-MS-FOOTNOTE-001")
    assert len(restart) == 1
    assert restart[0]["status"] == "ERROR"
    assert restart[0]["observed"]["restart_normalized"] == "continuous"


def test_each_sect_ooxml_value_is_a_deterministic_each_section_mismatch(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "分节编号脚注"),),
        references=((0, "footnote", 1),),
        settings={"footnote": {"numRestart": "eachSect"}},
    )

    _, _, audit = _chain(path)
    finding = _rule_findings(audit, "ER-MS-FOOTNOTE-001")[0]

    assert finding["status"] == "ERROR"
    assert finding["observed"]["restart"] == "eachSect"
    assert finding["observed"]["restart_normalized"] == "each_section"


def test_continuation_notice_is_not_counted_as_an_actual_note(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(),
        references=(),
        special_notes=(("footnote", 5, "continuationNotice"),),
    )

    inspection, _, audit = _chain(path)
    footnotes = inspection["notes"]["footnotes"]

    assert footnotes["actual_count"] == 0
    assert footnotes["separator_count"] == 3
    assert footnotes["ids"] == []
    assert _rule_findings(audit, "ER-MS-FOOTNOTE-001")[0]["status"] == "NOT_APPLICABLE"
    assert _rule_findings(audit, "ER-MS-FOOTNOTE-002")[0]["status"] == "NOT_APPLICABLE"


def test_deleted_note_reference_is_not_treated_as_current_document_evidence(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "已删除引用对应的脚注"),),
        references=(),
        deleted_references=((0, "footnote", 1),),
        settings={"footnote": {"numRestart": "eachPage"}},
    )

    inspection, _, audit = _chain(path)
    paragraph = inspection["paragraphs"][0]
    footnotes = inspection["notes"]["footnotes"]

    assert paragraph["footnote_reference_evidence"] == []
    assert footnotes["references"] == []
    assert footnotes["one_to_one"] is False
    assert footnotes["num_restarts"] == []
    assert _rule_findings(audit, "ER-MS-FOOTNOTE-001")[0]["status"] == "MANUAL_REVIEW"


def test_reference_without_note_part_is_a_linkage_manual_review(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(),
        references=((0, "footnote", 1),),
    )

    inspection, _, audit = _chain(path)
    footnotes = inspection["notes"]["footnotes"]

    assert footnotes["part"] is None
    assert footnotes["actual_count"] == 0
    assert footnotes["references"] == [1]
    assert footnotes["one_to_one"] is False
    assert _rule_findings(audit, "ER-CIT-GENERAL-001")[0]["status"] == "MANUAL_REVIEW"
    assert _rule_findings(audit, "ER-MS-FOOTNOTE-001")[0]["status"] == "MANUAL_REVIEW"
    assert _rule_findings(audit, "ER-MS-FOOTNOTE-002")[0]["status"] == "MANUAL_REVIEW"


def test_unreferenced_note_definition_is_not_a_definite_literature_error(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "Smith, 2020"),),
        references=(),
    )

    inspection, _, audit = _chain(path)
    footnotes = inspection["notes"]["footnotes"]
    general = _rule_findings(audit, "ER-CIT-GENERAL-001")

    assert footnotes["one_to_one"] is False
    assert len(general) == 1
    assert general[0]["status"] == "MANUAL_REVIEW"


def test_chinese_only_note_does_not_create_a_latin_font_target(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "纯中文脚注"),),
        references=((0, "footnote", 1),),
    )

    _, _, audit = _chain(path)
    latin = _rule_findings(audit, "ER-MS-LATIN-FONT-001")
    footnote_text = _rule_findings(audit, "ER-MS-FOOTNOTE-002")[0]

    assert len(latin) == 1
    assert latin[0]["status"] == "NOT_APPLICABLE"
    assert footnote_text["target"]["kind"] == "footnote"
    assert footnote_text["target"]["note_id"] == 1
    assert footnote_text["target"]["footnote_id"] == 1


def test_latin_endnote_finding_preserves_endnote_identity(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "Latin endnote ABC"),),
        references=((0, "endnote", 3),),
    )

    _, _, audit = _chain(path)
    findings = [
        item
        for item in _rule_findings(audit, "ER-MS-LATIN-FONT-001")
        if item["target"].get("scope") == "endnote"
    ]

    assert len(findings) == 1
    assert findings[0]["target"]["kind"] == "endnote"
    assert findings[0]["target"]["note_id"] == 3
    assert findings[0]["target"]["endnote_id"] == 3


def test_wrapped_note_paragraph_remains_visible_to_citation_lint(tmp_path: Path) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "Smith, 2020"),),
        references=((0, "endnote", 3),),
        wrap_note_paragraphs=True,
    )

    inspection, _, audit = _chain(path)
    endnote = inspection["notes"]["endnotes"]["items"][0]
    findings = _rule_findings(audit, "ER-CIT-GENERAL-001")

    assert len(endnote["paragraphs"]) == 1
    assert endnote["text"] == "Smith, 2020"
    assert len(findings) == 1
    assert findings[0]["status"] == "ERROR"
    assert findings[0]["target"]["endnote_id"] == 3


@pytest.mark.parametrize(
    ("kind", "note_id"),
    [("footnote", 1), ("endnote", 3)],
)
def test_unreferenced_latin_note_is_manual_with_linkage_evidence(
    tmp_path: Path,
    kind: str,
    note_id: int,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=((kind, note_id, "Latin note ABC"),),
        references=(),
        filename=f"unreferenced-{kind}-latin.docx",
    )

    inspection, _, audit = _chain(path)
    info = inspection["notes"][f"{kind}s"]
    findings = _rule_findings(audit, "ER-MS-LATIN-FONT-001")

    assert info["ids"] == [note_id]
    assert info["references"] == []
    assert info["one_to_one"] is False
    assert findings
    assert all(item["status"] == "MANUAL_REVIEW" for item in findings)
    assert any(
        item["observed"].get("note_linkage_issues")
        for item in findings
    )

    if kind == "footnote":
        format_findings = _rule_findings(audit, "ER-MS-FOOTNOTE-002")
        assert format_findings
        assert all(item["status"] == "MANUAL_REVIEW" for item in format_findings)
        assert any(item["observed"].get("note_linkage_issues") for item in format_findings)


def test_reference_id_without_matching_definition_cannot_produce_restart_error(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "内容脚注"),),
        references=((0, "footnote", 2),),
        settings={"footnote": {"numRestart": "continuous"}},
        filename="definition-one-reference-two.docx",
    )

    inspection, _, audit = _chain(path)
    info = inspection["notes"]["footnotes"]
    restart = _rule_findings(audit, "ER-MS-FOOTNOTE-001")
    general = _rule_findings(audit, "ER-CIT-GENERAL-001")
    content = _rule_findings(audit, "ER-REF-CONTENT-FOOTNOTE-001")

    assert info["ids"] == [1]
    assert info["references"] == [2]
    assert info["one_to_one"] is False
    assert len(restart) == 1
    assert restart[0]["status"] == "MANUAL_REVIEW"
    assert restart[0]["observed"]["note_linkage_issues"]
    assert general[0]["status"] == "MANUAL_REVIEW"
    assert general[0]["observed"]["note_linkage_issues"]
    assert content[0]["status"] == "MANUAL_REVIEW"
    assert content[0]["observed"]["note_linkage_issues"]


def test_duplicate_active_reference_keeps_each_reference_location_result(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "内容脚注"),),
        references=((0, "footnote", 1), (0, "footnote", 1)),
        settings={"footnote": {"numRestart": "eachPage"}},
        filename="duplicate-active-footnote-reference.docx",
    )

    inspection, _, audit = _chain(path)
    info = inspection["notes"]["footnotes"]
    restart = _rule_findings(audit, "ER-MS-FOOTNOTE-001")

    assert info["ids"] == [1]
    assert info["references"] == [1, 1]
    assert info["one_to_one"] is False
    assert len(restart) == 3
    assert {item["status"] for item in restart} == {"PASS", "MANUAL_REVIEW"}
    assert sum(item["status"] == "PASS" for item in restart) == 2
    manual = next(item for item in restart if item["status"] == "MANUAL_REVIEW")
    assert manual["target"]["kind"] == "document"
    assert manual["observed"]["note_linkage_issues"]


def test_duplicate_footnote_reference_across_sections_keeps_pass_error_and_linkage_manual(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "内容脚注"),),
        references=((0, "footnote", 1), (1, "footnote", 1)),
        settings={"footnote": {"numRestart": "eachPage"}},
        section_overrides=(
            None,
            {"footnote": {"numRestart": "continuous"}},
        ),
        body_texts=("第一节", "第二节"),
        filename="duplicate-footnote-reference-cross-section.docx",
    )

    inspection, _, audit = _chain(path)
    linkage = note_linkage(inspection["notes"]["footnotes"], "footnote")
    findings = _rule_findings(audit, "ER-MS-FOOTNOTE-001")
    reference_findings = [
        item for item in findings if item["target"]["kind"] == "footnote_reference"
    ]
    manual = next(item for item in findings if item["target"]["kind"] == "document")

    assert linkage["uniquely_defined_ids"] == [1]
    assert linkage["uniquely_referenced_ids"] == []
    assert linkage["uniquely_bound_ids"] == []
    assert linkage["duplicate_reference_ids"] == [1]
    assert [(item["target"]["section_index"], item["status"]) for item in reference_findings] == [
        (0, "PASS"),
        (1, "ERROR"),
    ]
    assert manual["status"] == "MANUAL_REVIEW"
    assert manual["observed"]["note_linkage_issues"]


def test_duplicate_endnote_reference_keeps_both_section_evidence_and_linkage_manual(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("endnote", 3, "This note explains the sample (Smith, 2020)."),),
        references=((0, "endnote", 3), (1, "endnote", 3)),
        settings={"endnote": {"numFmt": "upperRoman", "numRestart": "continuous"}},
        section_overrides=(
            None,
            {"endnote": {"numFmt": "lowerRoman", "numRestart": "eachPage"}},
        ),
        body_texts=("第一节", "第二节"),
        filename="duplicate-endnote-reference-cross-section.docx",
    )

    inspection, _, audit = _chain(path)
    info = inspection["notes"]["endnotes"]
    linkage = note_linkage(info, "endnote")
    first = inspection["paragraphs"][0]["endnote_reference_evidence"][0]
    second = inspection["paragraphs"][1]["endnote_reference_evidence"][0]

    assert linkage["uniquely_defined_ids"] == [3]
    assert linkage["uniquely_referenced_ids"] == []
    assert linkage["uniquely_bound_ids"] == []
    assert linkage["duplicate_reference_ids"] == [3]
    assert first["section_index"] == 0
    assert first["effective_properties"]["numFmt"] == "upperRoman"
    assert first["effective_properties"]["numRestart"] == "continuous"
    assert second["section_index"] == 1
    assert second["effective_properties"]["numFmt"] == "lowerRoman"
    assert second["effective_properties"]["numRestart"] == "eachPage"
    general = _rule_findings(audit, "ER-CIT-GENERAL-001")
    assert {(item["status"], item["target"]["kind"]) for item in general} == {
        ("MANUAL_REVIEW", "document")
    }
    content = _rule_findings(audit, "ER-REF-CONTENT-FOOTNOTE-001")
    assert {(item["status"], item["target"]["kind"]) for item in content} == {
        ("PASS", "endnote"),
        ("MANUAL_REVIEW", "document"),
    }
    assert all(
        item["observed"].get("note_linkage_issues")
        for item in [*general, *content]
        if item["target"]["kind"] == "document"
    )
    assert {
        item["status"]
        for item in _rule_findings(audit, "ER-MS-FOOTNOTE-001")
    } == {"NOT_APPLICABLE"}


def test_duplicate_note_definition_is_not_linted_as_a_definite_violation(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(
            ("footnote", 1, "Smith, 2020"),
            ("footnote", 1, "Latin duplicate ABC"),
        ),
        references=((0, "footnote", 1),),
        filename="duplicate-footnote-definition.docx",
    )

    inspection, _, audit = _chain(path)
    info = inspection["notes"]["footnotes"]
    general = _rule_findings(audit, "ER-CIT-GENERAL-001")
    latin = _rule_findings(audit, "ER-MS-LATIN-FONT-001")

    assert info["ids"] == [1, 1]
    assert info["references"] == [1]
    assert info["one_to_one"] is False
    assert general and all(item["status"] == "MANUAL_REVIEW" for item in general)
    assert latin and all(item["status"] == "MANUAL_REVIEW" for item in latin)
    assert any(
        "duplicate_note_definition"
        in item["observed"]["note_linkage_issues"][0]["reasons"]
        for item in general
        if item["observed"].get("note_linkage_issues")
    )


def test_deleted_reference_leaves_note_linkage_manual_for_content_and_format_rules(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(("footnote", 1, "Smith, 2020"),),
        references=(),
        deleted_references=((0, "footnote", 1),),
        filename="deleted-footnote-reference-linkage.docx",
    )

    inspection, _, audit = _chain(path)
    info = inspection["notes"]["footnotes"]

    assert info["references"] == []
    for rule_id in ("ER-CIT-GENERAL-001", "ER-REF-CONTENT-FOOTNOTE-001", "ER-MS-FOOTNOTE-002"):
        findings = _rule_findings(audit, rule_id)
        assert findings
        assert all(item["status"] == "MANUAL_REVIEW" for item in findings)
        assert any(item["observed"].get("note_linkage_issues") for item in findings)


def test_linkage_issue_still_checks_active_footnote_restart_and_text(
    tmp_path: Path,
) -> None:
    path = make_notes_docx(
        tmp_path,
        notes=(
            ("footnote", 1, "  已绑定合规脚注"),
            ("footnote", 2, "  未绑定脚注"),
        ),
        references=((0, "footnote", 1),),
        settings={"footnote": {"numRestart": "eachPage"}},
        note_run_properties={
            ("footnote", 1): {"eastAsia": "宋体", "size_pt": 7.5},
            ("footnote", 2): {"eastAsia": "宋体", "size_pt": 7.5},
        },
        filename="active-and-unreferenced-footnotes.docx",
    )

    inspection, _, audit = _chain(path)
    info = inspection["notes"]["footnotes"]
    restart = _rule_findings(audit, "ER-MS-FOOTNOTE-001")
    text = _rule_findings(audit, "ER-MS-FOOTNOTE-002")

    assert info["ids"] == [1, 2]
    assert info["references"] == [1]
    assert {item["status"] for item in restart} == {"PASS", "MANUAL_REVIEW"}
    assert any(item["target"].get("footnote_id") == 1 for item in restart)
    assert {item["status"] for item in text} == {"PASS", "MANUAL_REVIEW"}
    assert any(item["target"].get("footnote_id") == 1 for item in text)
    assert all(item["target"].get("footnote_id") != 2 for item in [*restart, *text])
