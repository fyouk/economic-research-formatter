"""TDD regressions for PR #1 second-round formatting findings."""

from __future__ import annotations

from pathlib import Path

import pytest

from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.lint.engine import lint_inspection
from economic_research_formatter.lint.common import CN_SIZE_TO_PT, check_paragraph_format
from economic_research_formatter.lint.manuscript import _mark_numbering_evidence
from economic_research_formatter.models.formatting import CN_SIZE_TO_PT as CANONICAL_CN_SIZE_TO_PT

from tests.linter.pr2_formatting_docx import (
    make_concrete_override_theme_title_docx,
    make_doc_defaults_theme_title_docx,
    make_font_only_table_note_docx,
    make_mixed_mismatch_and_unresolved_theme_title_docx,
    make_mixed_latin_mismatch_and_unresolved_theme_docx,
    make_mismatched_table_note_docx,
    make_repeated_unknown_table_notes_docx,
    make_title_star_docx,
    make_unresolved_latin_theme_docx,
    make_unresolved_theme_title_docx,
)
from tests.linter.pr2_notes_docx import make_notes_docx


def _finding(audit: dict, rule_id: str) -> dict:
    return next(item for item in audit["findings"] if item["rule_id"] == rule_id)


def _chain(path: Path) -> tuple[dict, dict, dict]:
    inspection = inspect_docx(path, include_text=True)
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)
    return inspection, classification, audit


def _table_inspection(*, font: str, relation: str) -> dict:
    paragraph: dict = {
        "id": "table-000000-cell-000000-p-0000",
        "text": "表格正文",
        "font_size_relation_to_body": relation,
    }
    if font == "unresolved_theme":
        paragraph["runs"] = [
            {
                "text": "表格正文",
                "formatting": {
                    "effective": {
                        "eastAsia": None,
                        "font": {
                            "theme": {"eastAsia": "majorEastAsia"},
                            "theme_evidence": {"eastAsia": {"resolved": False}},
                        },
                    },
                    "source": {"font": {"eastAsia": "unknown"}},
                },
            }
        ]
    else:
        paragraph["effective_formatting"] = {"eastAsia": font}
    return {
        "schema_version": "1.0",
        "input": {"filename": "pr3-table-matrix.docx"},
        "paragraphs": [],
        "tables": [
            {
                "id": "table-000000",
                "cells": [
                    {
                        "id": "table-000000-cell-000000",
                        "row": 0,
                        "column": 0,
                        "paragraphs": [paragraph],
                    }
                ],
            }
        ],
        "images": [],
        "equations": {"omath_count": 0, "paragraph_ids": []},
        "notes": {"footnotes": {"actual_count": 0, "items": []}},
        "fields": {},
    }


def test_unresolved_theme_font_is_manual_review_not_error_from_real_docx(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_unresolved_theme_title_docx(tmp_path)
    )
    finding = _finding(audit, "ER-MS-TITLE-001")

    assert classification["items"][0]["role"] == "title"
    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["unresolved_formatting_fields"] == ["east_asia_font"]


def test_definite_mismatch_is_not_hidden_by_another_unresolved_run(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_mixed_mismatch_and_unresolved_theme_title_docx(tmp_path)
    )
    finding = _finding(audit, "ER-MS-TITLE-001")

    assert classification["items"][0]["role"] == "title"
    assert finding["status"] == "ERROR"
    assert finding["observed"]["unresolved_formatting_fields"] == ["east_asia_font"]


@pytest.mark.parametrize(
    ("language", "script"),
    [("zh-CN", "Hans"), ("zh-TW", "Hant")],
)
def test_doc_defaults_language_resolves_theme_font_in_real_docx(
    tmp_path: Path, language: str, script: str
) -> None:
    inspection, classification, audit = _chain(
        make_doc_defaults_theme_title_docx(tmp_path, language=language)
    )
    run_formatting = inspection["paragraphs"][0]["runs"][0]["formatting"]
    evidence = run_formatting["effective"]["font"]["theme_evidence"]["eastAsia"]
    finding = _finding(audit, "ER-MS-TITLE-001")

    assert classification["items"][0]["role"] == "title"
    assert evidence["resolved"] is True
    assert evidence["script"] == script
    assert run_formatting["effective"]["eastAsia"] == "宋体"
    assert finding["status"] == "PASS"


def test_direct_concrete_font_overrides_unresolved_lower_theme_in_real_docx(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_concrete_override_theme_title_docx(tmp_path)
    )
    formatting = inspection["paragraphs"][0]["runs"][0]["formatting"]
    finding = _finding(audit, "ER-MS-TITLE-001")

    assert classification["items"][0]["role"] == "title"
    assert formatting["effective"]["eastAsia"] == "宋体"
    assert formatting["source"]["font"]["eastAsia"] == "direct"
    assert "eastAsia" not in formatting["effective"]["font"].get("theme", {})
    assert finding["status"] == "PASS"


def test_unresolved_latin_theme_font_is_manual_review_not_error_from_real_docx(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_unresolved_latin_theme_docx(tmp_path)
    )
    finding = _finding(audit, "ER-MS-LATIN-FONT-001")

    assert classification["summary"]["paragraph_count"] == len(inspection["paragraphs"])
    assert finding["status"] == "MANUAL_REVIEW"
    assert set(finding["observed"]["unresolved_formatting_fields"]) == {"ascii_font", "hansi_font"}


def test_latin_definite_mismatch_is_not_hidden_by_an_unresolved_run(
    tmp_path: Path,
) -> None:
    inspection, classification, audit = _chain(
        make_mixed_latin_mismatch_and_unresolved_theme_docx(tmp_path)
    )
    finding = _finding(audit, "ER-MS-LATIN-FONT-001")

    assert classification["summary"]["paragraph_count"] == len(inspection["paragraphs"])
    assert finding["status"] == "ERROR"
    assert set(finding["observed"]["unresolved_formatting_fields"]) == {
        "ascii_font",
        "hansi_font",
    }


def test_endnote_latin_finding_preserves_note_identity_and_scope(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_notes_docx(
            tmp_path,
            notes=(("endnote", 3, "Smith, 2020"),),
            references=((0, "endnote", 3),),
        )
    )
    finding = _finding(audit, "ER-MS-LATIN-FONT-001")
    target = finding["target"]

    assert classification["summary"]["note_count"] == 1
    assert target["kind"] == "endnote"
    assert target["scope"] == "endnote"
    assert target["note_id"] == 3
    assert target["endnote_id"] == 3
    assert target["source_id"] == "endnote-3"


def test_author_information_endnote_format_finding_preserves_note_identity(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_notes_docx(
            tmp_path,
            notes=(("endnote", 3, "作者信息：电子信箱：a@example.com"),),
            references=((0, "endnote", 3),),
        )
    )
    finding = _finding(audit, "ER-MS-AUTHORINFO-001")
    target = finding["target"]

    assert classification["summary"]["note_count"] == 1
    assert target["kind"] == "endnote"
    assert target["note_id"] == 3
    assert target["endnote_id"] == 3
    assert target["source_id"] == "endnote-3"


@pytest.mark.parametrize("reference_first", [True, False])
def test_literal_star_with_custom_mark_false_is_error_from_real_docx(
    tmp_path: Path, reference_first: bool
) -> None:
    inspection, classification, audit = _chain(
        make_title_star_docx(tmp_path, reference_first=reference_first)
    )
    finding = _finding(audit, "ER-MS-TITLE-002")

    assert classification["items"][0]["role"] == "title"
    assert finding["status"] == "ERROR"
    marker = finding["observed"]["marker_evidence"][0]
    assert marker["marker"] == "*"
    assert marker["custom_mark_follows"] is False
    assert marker["status"] == "wrong"


def test_font_only_table_note_rule_does_not_gate_on_unknown_size_relation_from_real_docx(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_font_only_table_note_docx(tmp_path)
    )
    finding = _finding(audit, "ER-MS-TABLE-NOTE-002")

    assert classification["summary"]["paragraph_count"] == len(
        inspection["paragraphs"]
    )
    assert finding["status"] == "PASS"
    assert finding["observed"]["east_asia_font"] == "宋体"


def test_repeated_unknown_table_notes_are_aggregated_by_table_after_real_docx_lint(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_repeated_unknown_table_notes_docx(tmp_path)
    )
    assert classification["summary"]["paragraph_count"] == 0

    assert len(inspection["tables"][0]["cells"]) == 4
    findings = [item for item in audit["findings"] if item["rule_id"] == "ER-MS-TABLE-NOTE-001"]
    assert len(findings) == 1
    assert {item["status"] for item in findings} == {"MANUAL_REVIEW"}
    assert findings[0]["target"]["kind"] == "table"
    assert findings[0]["observed"]["count"] == 4
    assert len(findings[0]["observed"]["examples"]) == 3
    assert audit["summary"]["by_rule_and_status"]["ER-MS-TABLE-NOTE-001"]["MANUAL_REVIEW"] == 1
    assert audit["summary"]["by_rule_and_status_affected"]["ER-MS-TABLE-NOTE-001"]["MANUAL_REVIEW"] == 4

    aggregates = audit["summary"]["table_aggregates"]
    matching = [item for item in aggregates if item["rule_id"] == "ER-MS-TABLE-NOTE-001"]
    assert len(matching) == 1
    assert matching[0]["count"] == 4
    assert matching[0]["table_id"] == inspection["tables"][0]["id"]
    assert len(matching[0]["examples"]) == 3

    font_finding = _finding(audit, "ER-MS-TABLE-NOTE-002")
    assert font_finding["status"] == "MANUAL_REVIEW"
    assert font_finding["target"]["kind"] == "table"
    assert font_finding["observed"]["count"] == 4


def test_resolved_concrete_table_note_font_mismatch_remains_warning(tmp_path: Path) -> None:
    inspection, classification, audit = _chain(
        make_mismatched_table_note_docx(tmp_path)
    )
    finding = _finding(audit, "ER-MS-TABLE-NOTE-002")

    assert classification["summary"]["paragraph_count"] == len(inspection["paragraphs"])
    assert finding["status"] == "WARNING"
    assert finding["observed"]["east_asia_font"] == "黑体"


@pytest.mark.parametrize(
    ("font", "relation", "expected_status"),
    [
        ("黑体", "unknown", "WARNING"),
        ("仿宋", "unknown", "MANUAL_REVIEW"),
        ("unresolved_theme", "larger", "WARNING"),
        ("仿宋", "smaller", "PASS"),
        ("黑体", "larger", "WARNING"),
    ],
)
def test_table_multifield_outcome_keeps_definite_mismatch_over_unknown(
    font: str,
    relation: str,
    expected_status: str,
) -> None:
    audit = lint_inspection(_table_inspection(font=font, relation=relation))
    finding = _finding(audit, "ER-MS-TABLE-001")

    assert finding["status"] == expected_status
    if relation == "unknown":
        assert "font_size_relation_to_body" in finding["observed"]["unresolved_fields"]
    if font == "unresolved_theme":
        assert finding["observed"]["unresolved_formatting_fields"] == [
            "east_asia_font"
        ]


def test_linter_uses_canonical_chinese_font_size_mapping() -> None:
    assert CN_SIZE_TO_PT is CANONICAL_CN_SIZE_TO_PT


def test_sparse_numbering_without_explicit_state_is_unknown() -> None:
    checked = check_paragraph_format(
        {"id": "ER-MS-REF-LAYOUT-001", "requirement": {"numbered": False}},
        {"numbering": {"num_id": 7, "ilvl": 1}},
    )

    assert checked is not None
    assert checked["observed"]["numbered"] is None
    assert "numbered" in checked["observed"]["unchecked_fields"]


def test_resolved_numbering_envelope_keeps_legacy_complete_state() -> None:
    checked = _mark_numbering_evidence(
        {"observed": {"numbered": True}, "mismatches": {"numbered": {}}, "unchecked": []},
        {"numbering": {"num_id": 7, "resolved": True}},
        {"numbered": False},
    )

    assert checked is not None
    assert checked["observed"]["numbered"] is True
    assert checked["unchecked"] == []
    assert checked["mismatches"]["numbered"]["observed"] is True
