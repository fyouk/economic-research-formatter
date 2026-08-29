"""Consumer-side regression tests for PR1 manuscript rules.

These tests are intentionally written against the Inspector-to-linter
boundary.  They do not synthesize a final PASS finding or bypass the
classifier; every real-DOCX case starts with ``inspect_docx``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection

from tests.linter.pr1_manuscript_docx import (
    make_mathtype_docx,
    make_mixed_table_docx,
    make_table_latin_docx,
    make_valid_table_relation_docx,
)


def _inspection(*paragraphs: dict, **extra: object) -> dict:
    result = {
        "schema_version": "1.0",
        "input": {"filename": "pr1-synthetic.docx"},
        "paragraphs": [
            {"id": f"p-{index:06d}", "index": index, **paragraph}
            for index, paragraph in enumerate(paragraphs)
        ],
        "tables": [],
        "images": [],
        "equations": {"omath_count": 0, "omath_para_count": 0, "paragraph_ids": [], "items": []},
        "notes": {"footnotes": {"actual_count": 0, "items": []}},
        "fields": {},
    }
    result.update(extra)
    return result


def _finding(audit: dict, rule_id: str) -> dict:
    return next(item for item in audit["findings"] if item["rule_id"] == rule_id)


def _findings(audit: dict, rule_id: str) -> list[dict]:
    return [item for item in audit["findings"] if item["rule_id"] == rule_id]


def test_hierarchy_requires_l2_two_ascii_spaces_and_exact_l3_l4_prefixes() -> None:
    inspection = _inspection(
        {"text": "（一）理论分析", "role_hint": "heading_level_2"},
        {"text": "（1）机制分析", "role_hint": "heading_level_3"},
        {"text": "1. 变量定义", "role_hint": "heading_level_4"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-HEADING-HIERARCHY-001")

    assert finding["status"] == "ERROR"
    assert {item["level"] for item in finding["observed"]["violations"]} == {2, 3, 4}
    assert finding["observed"]["headings"][0]["text_preview"].startswith("（一）")


def test_hierarchy_uses_conservative_u3000_policy() -> None:
    inspection = _inspection(
        {"text": "　（一）理论分析", "role_hint": "heading_level_2"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-HEADING-HIERARCHY-001")

    assert finding["status"] == "ERROR"
    assert finding["observed"]["headings"][0]["leading_space_codepoints"] == ["U+3000"]
    assert "U+0020" in finding["observed"]["leading_space_policy"]


def test_table_mixed_relative_size_is_manual_review() -> None:
    inspection = _inspection(
        {"text": "表1", "role_hint": "table_caption"},
        tables=[
            {
                "id": "table-000000",
                "cells": [
                    {
                        "id": "table-000000-cell-000000",
                        "paragraphs": [
                            {
                                "id": "table-000000-cell-000000-p-0000",
                                "text": "表格正文",
                                "effective_formatting": {"eastAsia": "仿宋"},
                                "font_size_relation_to_body": "mixed",
                            }
                        ],
                    }
                ],
            }
        ],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TABLE-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["font_size_relation_to_body"] == "mixed"


def test_real_mixed_table_without_valid_relation_stays_manual(tmp_path: Path) -> None:
    report = inspect_docx(make_mixed_table_docx(tmp_path))

    finding = _finding(lint_inspection(report), "ER-MS-TABLE-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert "font_size_relation_to_body" in finding["observed"].get("unchecked_fields", []) or finding["observed"].get("comparison_status") in {"mixed", "unknown"}


def test_real_inspector_table_and_adjacent_note_relations_can_pass(tmp_path: Path) -> None:
    report = inspect_docx(make_valid_table_relation_docx(tmp_path))
    audit = lint_inspection(report)

    table_finding = _finding(audit, "ER-MS-TABLE-001")
    note_finding = _finding(audit, "ER-MS-TABLE-NOTE-001")

    assert table_finding["status"] == "PASS"
    assert table_finding["observed"]["font_size_relation_to_body"] == "smaller"
    assert note_finding["status"] == "PASS"
    assert note_finding["target"].get("scope") is None
    assert note_finding["observed"]["font_size_relation_to_table"] == "one_cn_size_smaller"
    assert note_finding["observed"]["table_note_binding"]["source"] == "inspector.note_candidates"


def test_pure_omml_equation_is_passed_when_editor_is_allowed(tmp_path: Path) -> None:
    report = inspect_docx(
        __import__("tests.docx_factory", fromlist=["make_synthetic_docx"]).make_synthetic_docx(
            tmp_path,
            with_real_footnote=False,
            with_metadata_objects=False,
        )
    )

    finding = _finding(lint_inspection(report), "ER-MS-EQUATION-001")

    assert finding["status"] == "PASS"
    assert finding["observed"]["editors"] == ["Word Equation"]


def test_pure_mathtype_ole_is_passed_and_not_reported_as_absent(tmp_path: Path) -> None:
    report = inspect_docx(make_mathtype_docx(tmp_path))

    finding = _finding(lint_inspection(report), "ER-MS-EQUATION-001")

    assert finding["status"] == "PASS"
    assert finding["observed"]["editors"] == ["MathType"]


def test_mixed_omml_and_mathtype_are_both_listed(tmp_path: Path) -> None:
    report = inspect_docx(make_mathtype_docx(tmp_path, with_omml=True))

    finding = _finding(lint_inspection(report), "ER-MS-EQUATION-001")

    assert finding["status"] == "PASS"
    assert finding["observed"]["editors"] == ["MathType", "Word Equation"]


def test_unknown_ole_requires_manual_equation_review(tmp_path: Path) -> None:
    from tests.docx_factory import make_synthetic_docx

    report = inspect_docx(make_synthetic_docx(tmp_path, with_real_footnote=False, with_metadata_objects=True))

    finding = _finding(lint_inspection(report), "ER-MS-EQUATION-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert "Unknown OLE" in finding["observed"]["editors"]


@pytest.mark.parametrize(
    ("reference", "status", "span"),
    [
        ("Journal, 58(1-2): 3—27.", "PASS", "3—27"),
        ("Journal, 58(1-2): 3-27.", "ERROR", "3-27"),
        ("Journal, Vol. 58, No. 1: 3—27.", "PASS", "3—27"),
        ("pp. 15—25", "PASS", "15—25"),
        ("15—25", "PASS", "15—25"),
        ("Title 2019-2020. Journal, 58(1-2): 3—27.", "PASS", "3—27"),
    ],
)
def test_page_range_selects_actual_page_span(reference: str, status: str, span: str) -> None:
    inspection = _inspection({"text": reference, "role_hint": "reference_entry"})

    finding = _finding(lint_inspection(inspection), "ER-MS-REF-PAGERANGE-001")

    assert finding["status"] == status
    assert finding["observed"]["selected_span"] == span
    assert finding["observed"]["selected_span_start"] == reference.index(span)
    assert finding["observed"]["selection_reason"]


def test_issue_range_without_pages_is_not_used_as_page_range() -> None:
    inspection = _inspection({"text": "Journal, 58(1-2).", "role_hint": "reference_entry"})

    finding = _finding(lint_inspection(inspection), "ER-MS-REF-PAGERANGE-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["candidates"][0]["in_parenthesized_range"] is True


def test_publication_year_range_without_pages_requires_manual_review() -> None:
    inspection = _inspection({"text": "Article covering 2019-2020.", "role_hint": "reference_entry"})

    finding = _finding(lint_inspection(inspection), "ER-MS-REF-PAGERANGE-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["candidates"][0]["likely_year_range"] is True


def test_real_table_cell_non_tnr_is_a_locatable_latin_finding(tmp_path: Path) -> None:
    report = inspect_docx(make_table_latin_docx(tmp_path))

    findings = _findings(lint_inspection(report), "ER-MS-LATIN-FONT-001")

    assert any(item["status"] == "ERROR" and str(item["target"].get("id", "")).startswith("table-") for item in findings)


def test_footnote_paragraphs_are_checked_once_for_latin_font() -> None:
    inspection = _inspection(
        {"text": "正文"},
        notes={
            "footnotes": {
                "actual_count": 1,
                "items": [
                    {
                        "id": 1,
                        "paragraphs": [
                            {
                                "id": "footnotes-1-p-0000",
                                "text": "alpha 123",
                                "runs": [
                                    {
                                        "text": "alpha 123",
                                        "effective_formatting": {
                                            "ascii_font": "Arial",
                                            "hansi_font": "Arial",
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        },
    )

    findings = _findings(lint_inspection(inspection), "ER-MS-LATIN-FONT-001")

    footnote_findings = [item for item in findings if item["target"]["kind"] == "footnote"]
    assert len(footnote_findings) == 1
    assert footnote_findings[0]["target"]["id"] == "footnotes-1-p-0000"
    assert footnote_findings[0]["status"] == "ERROR"


def test_legacy_note_item_without_nested_paragraphs_still_checks_latin_font() -> None:
    inspection = _inspection(
        {"text": "正文"},
        notes={
            "footnotes": {
                "actual_count": 1,
                "items": [
                    {
                        "id": 1,
                        "text": "alpha 123",
                        "runs": [
                            {
                                "text": "alpha 123",
                                "effective_formatting": {
                                    "ascii_font": "Times New Roman",
                                    "hansi_font": "Times New Roman",
                                },
                            }
                        ],
                    }
                ],
            }
        },
    )

    findings = _findings(lint_inspection(inspection), "ER-MS-LATIN-FONT-001")
    footnotes = [item for item in findings if item["target"].get("scope") == "footnote"]

    assert len(footnotes) == 1
    assert footnotes[0]["status"] == "PASS"


def test_title_numbered_footnote_marker_is_error() -> None:
    inspection = _inspection(
        {
            "text": "题目：研究标题",
            "footnote_references": [
                {
                    "kind": "footnote",
                    "id": 1,
                    "marker": "footnoteReference",
                    "automatic": True,
                    "display_marker": "1",
                }
            ],
        },
        {"text": "张三", "role_hint": "author_name"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "ERROR"
    assert finding["observed"]["marker_evidence"][0]["status"] == "wrong"


def test_title_footnote_reference_without_marker_evidence_is_manual() -> None:
    inspection = _inspection(
        {
            "text": "题目：研究标题",
            "footnote_references": [{"kind": "footnote", "id": 1, "marker": "footnoteReference"}],
        },
        {"text": "张三", "role_hint": "author_name"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "MANUAL_REVIEW"


def test_title_custom_star_marker_is_passed_only_with_association() -> None:
    inspection = _inspection(
        {
            "text": "题目：研究标题*",
            "footnote_references": [
                {
                    "kind": "footnote",
                    "id": 1,
                    "marker": "*",
                    "customMarkFollows": True,
                    "literal_marker": "*",
                }
            ],
        },
        {"text": "张三", "role_hint": "author_name"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "PASS"


def test_author_contact_term_requires_observed_preferred_term() -> None:
    inspection = _inspection(
        {
            "text": "作者信息：电子邮箱：a@example.com；电子信箱：b@example.com",
            "role_hint": "author_information",
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-AUTHORINFO-002")

    assert finding["status"] == "ERROR"
    locations = finding["observed"]["locations"]
    assert {location["term"] for location in locations} == {"电子邮箱", "电子信箱"}


def test_author_contact_term_without_either_label_is_not_applicable() -> None:
    inspection = _inspection(
        {"text": "作者信息：张三，中国某大学", "role_hint": "author_information"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-AUTHORINFO-002")

    assert finding["status"] == "NOT_APPLICABLE"


def test_author_contact_preferred_term_is_passed_when_observed() -> None:
    inspection = _inspection(
        {"text": "作者信息：电子信箱：author@example.com", "role_hint": "author_information"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-AUTHORINFO-002")

    assert finding["status"] == "PASS"
    assert finding["observed"]["term"] == "电子信箱"
