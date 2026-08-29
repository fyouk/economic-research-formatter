from __future__ import annotations

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.lint.citations import _candidate_paragraphs
from economic_research_formatter.lint.common import RuleContext
from economic_research_formatter.lint.engine import lint_inspection
from economic_research_formatter.lint.manuscript import lint_manuscript_rule
from economic_research_formatter.lint.references import lint_reference_rule


def _inspection(*paragraphs: dict, **extra: object) -> dict:
    result = {
        "schema_version": "1.0",
        "input": {"filename": "review-fixture.docx"},
        "paragraphs": [
            {"id": f"p-{index:06d}", "index": index, **paragraph}
            for index, paragraph in enumerate(paragraphs)
        ],
        "tables": [],
        "images": [],
        "equations": {"omath_count": 0, "paragraph_ids": []},
        "notes": {"footnotes": {"actual_count": 0, "items": []}},
        "fields": {},
    }
    result.update(extra)
    return result


def _finding(audit: dict, rule_id: str) -> dict:
    return next(item for item in audit["findings"] if item["rule_id"] == rule_id)


def _ctx(inspection: dict) -> RuleContext:
    classification = classify_inspection(inspection)
    return RuleContext(inspection, classification, {})


def test_plain_prose_year_range_is_not_treated_as_a_narrative_citation() -> None:
    inspection = _inspection({"text": "本文使用2010年至2020年的数据。"})

    assert _candidate_paragraphs(_ctx(inspection)) == []
    audit = lint_inspection(inspection)
    assert _finding(audit, "ER-CIT-GENERAL-001")["status"] == "NOT_APPLICABLE"
    assert _finding(audit, "ER-CIT-GENERAL-002")["status"] == "NOT_APPLICABLE"


def test_narrative_author_year_with_parentheses_remains_a_high_confidence_candidate() -> None:
    inspection = _inspection({"text": "张三（2020）发现了治理效应。"})

    candidates = _candidate_paragraphs(_ctx(inspection))

    assert [item["id"] for item in candidates] == ["p-000000"]
    assert lint_inspection(inspection)["classification"]["items"][0]["role"] == "body_text"


def test_equation_where_requires_a_real_equation_context_and_accepts_chinese_punctuation() -> None:
    inspection = _inspection(
        {"text": "其中，x表示处理变量。"},
        {"text": "公式", "has_omml": True},
        {"text": "  其中，x表示处理变量。"},
    )

    classification = classify_inspection(inspection)
    roles = {item["id"]: item["role"] for item in classification["items"]}
    assert roles["p-000000"] != "equation_where_paragraph"
    assert roles["p-000002"] == "equation_where_paragraph"

    generic_audit = lint_inspection(_inspection({"text": "其中，x表示处理变量。"}))
    assert _finding(generic_audit, "ER-MS-EQUATION-002")["status"] == "NOT_APPLICABLE"

    audit = lint_inspection(inspection)
    findings = [item for item in audit["findings"] if item["rule_id"] == "ER-MS-EQUATION-002"]
    assert {item["status"] for item in findings} == {"PASS"}
    assert all(item["target"].get("id") != "p-000000" for item in findings)


def test_equation_role_hint_without_a_real_equation_is_not_authoritative() -> None:
    inspection = _inspection(
        {"text": "其中：x表示处理变量。", "role_hint": "equation_where_paragraph"},
    )

    item = classify_inspection(inspection)["items"][0]

    assert item["role"] == "body_text"


def test_low_confidence_author_information_is_manual_and_carries_classifier_evidence() -> None:
    inspection = _inspection({"text": "作者信息：电子邮箱：author@example.com"})

    audit = lint_inspection(inspection)
    finding = _finding(audit, "ER-MS-AUTHORINFO-002")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["confidence"] < 0.85
    assert finding["observed"]["classification"]["role"] == "author_information"
    assert finding["observed"]["classification"]["evidence"]


def test_title_marker_without_custom_mark_evidence_requires_review() -> None:
    inspection = _inspection(
        {"text": "题目：研究标题", "footnote_references": [{"id": 1, "marker": "*"}]},
        {"text": "张三", "role_hint": "author_name"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["footnote_reference_evidence"]
    assert finding["observed"]["marker_evidence"][0]["reason"] == "marker_without_custom_mark_evidence"


def test_author_information_note_is_classified_separately_and_missing_formatting_is_manual() -> None:
    inspection = _inspection(
        {"text": "题目：研究标题"},
        notes={
            "footnotes": {
                "actual_count": 1,
                "items": [{"id": 1, "text_preview": "作者信息：电子信箱 author@example.com"}],
            }
        },
    )

    classification = classify_inspection(inspection)
    note_items = classification.get("note_items", [])
    assert any(item["role"] == "author_information" for item in note_items)

    finding = _finding(lint_inspection(inspection), "ER-MS-AUTHORINFO-001")
    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["target"]["kind"] == "footnote"


def test_footnote_restart_aliases_are_normalized_and_all_section_values_are_checked() -> None:
    rule = {
        "id": "ER-MS-FOOTNOTE-001",
        "requirement": {"restart": "each_page"},
        "lint": {"severity": "error"},
    }
    inspection = _inspection(
        notes={
            "footnotes": {
                "actual_count": 2,
                "num_restarts": ["eachPage", "each_page"],
            }
        },
    )

    result = lint_manuscript_rule(rule, _ctx(inspection))

    assert result[0]["status"] == "PASS"
    assert result[0]["observed"]["restarts_normalized"] == ["each_page", "each_page"]


def test_mixed_effective_footnote_restarts_are_not_reported_as_a_pass() -> None:
    rule = {
        "id": "ER-MS-FOOTNOTE-001",
        "requirement": {"restart": "each_page"},
        "lint": {"severity": "error"},
    }
    inspection = _inspection(
        notes={"footnotes": {"actual_count": 2, "num_restarts": ["eachPage", "continuous"]}},
    )

    result = lint_manuscript_rule(rule, _ctx(inspection))

    assert result[0]["status"] == "ERROR"


def test_table_note_candidate_may_bind_an_adjacent_body_paragraph_but_not_an_arbitrary_note() -> None:
    inspection = _inspection(
        {"text": "表格前正文"},
        {"text": "注：与表1相邻的说明。"},
        tables=[
            {
                "id": "table-000000",
                "cells": [],
                "note_candidates": [
                    {
                        "paragraph_id": "p-000001",
                        "adjacent_body_paragraph_id": "p-000001",
                        "text_preview": "注：与表1相邻的说明。",
                    }
                ],
            }
        ],
    )
    rule = {
        "id": "ER-MS-TABLE-NOTE-002",
        "requirement": {"east_asia_font": "宋体"},
        "lint": {"severity": "warning"},
    }

    result = lint_manuscript_rule(rule, _ctx(inspection))

    assert result[0]["target"]["id"] == "p-000001"
    assert result[0]["status"] in {"MANUAL_REVIEW", "WARNING"}


def test_foreign_line_does_not_echo_expected_separator_as_observed() -> None:
    inspection = _inspection(
        {"text": "参考文献"},
        {
            "text": "Smith; 2020; A paper; Journal.",
            "in_reference": True,
            "language": "foreign",
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-REF-FOREIGN-LINE-001")

    assert finding["status"] == "ERROR"
    assert finding["observed"].get("actual_separator") in {";", "；"}
    assert finding["observed"].get("field_separator") != "comma"


def test_foreign_line_with_unreliable_delimiter_boundaries_is_manual() -> None:
    inspection = _inspection(
        {"text": "参考文献"},
        {
            "text": "Smith 2020 A paper Journal",
            "in_reference": True,
            "language": "foreign",
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-REF-FOREIGN-LINE-001")

    assert finding["status"] == "MANUAL_REVIEW"


def test_language_grouping_does_not_pass_without_within_group_sort_evidence() -> None:
    inspection = _inspection(
        {"text": "参考文献"},
        {"text": "张三，2020。中文文献。", "in_reference": True, "language": "chinese"},
        {"text": "Smith, 2020. Foreign paper.", "in_reference": True, "language": "foreign"},
    )
    rule = {
        "id": "ER-REF-LANGUAGE-GROUP-001",
        "requirement": {
            "group_order": ["chinese_including_translations", "foreign"],
            "sort_each_group_separately": True,
            "sort_key": "author_surname_initial_latin",
        },
        "lint": {"severity": "error"},
    }

    result = lint_reference_rule(rule, _ctx(inspection))

    assert result[0]["status"] == "MANUAL_REVIEW"
    assert result[0]["observed"]["within_group_sort_verified"] is False
