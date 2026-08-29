"""Behavior coverage for manuscript linter branches left by the PR1 tests.

The cases in this module stay at the Inspector-to-linter boundary.  They use
small Inspector-shaped dictionaries when a producer capability is the thing
under test, and use the real ``inspect_docx`` boundary for producer fixtures
where one already exists.  No assertion is based on a private helper's
return value; each test checks the user-visible finding status, target, or
evidence produced by the manuscript rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.common import RuleContext
from economic_research_formatter.lint.engine import lint_inspection
from economic_research_formatter.lint.manuscript import lint_manuscript_rule

from tests.linter.pr1_manuscript_docx import make_table_latin_docx


def _inspection(*paragraphs: dict, **extra: object) -> dict:
    """Build the minimum stable Inspector envelope used by these tests."""

    result = {
        "schema_version": "1.0",
        "input": {"filename": "pr1-coverage-synthetic.docx"},
        "paragraphs": [
            {"id": f"p-{index:06d}", "index": index, **paragraph}
            for index, paragraph in enumerate(paragraphs)
        ],
        "tables": [],
        "images": [],
        "equations": {
            "omath_count": 0,
            "omath_para_count": 0,
            "paragraph_ids": [],
            "items": [],
        },
        "notes": {"footnotes": {"actual_count": 0, "items": []}},
        "fields": {},
    }
    result.update(extra)
    return result


def _finding(audit: dict, rule_id: str) -> dict:
    return next(item for item in audit["findings"] if item["rule_id"] == rule_id)


def _findings(audit: dict, rule_id: str) -> list[dict]:
    return [item for item in audit["findings"] if item["rule_id"] == rule_id]


def test_valid_heading_hierarchy_accepts_two_ascii_spaces_and_source_sequence() -> None:
    inspection = _inspection(
        {"text": "  （一）理论分析", "role_hint": "heading_level_2"},
        {"text": "1. 机制分析", "role_hint": "heading_level_3"},
        {"text": "（1）变量定义", "role_hint": "heading_level_4"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-HEADING-HIERARCHY-001")

    assert finding["status"] == "PASS"
    assert finding["observed"]["headings"][0]["leading_space_codepoints"] == [
        "U+0020",
        "U+0020",
    ]


def test_title_without_author_information_is_not_applicable_for_marker_check() -> None:
    inspection = _inspection({"text": "题目：没有作者信息目标"})

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "NOT_APPLICABLE"
    assert "作者信息目标" in finding["message"]


def test_title_with_author_but_without_bound_marker_is_an_error() -> None:
    inspection = _inspection(
        {"text": "题目：作者信息应有标记"},
        {"text": "张三", "role_hint": "author_name"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "ERROR"
    assert finding["observed"]["marker_evidence"] == []


def test_title_marker_does_not_accept_star_when_explicitly_marked_non_custom() -> None:
    inspection = _inspection(
        {
            "text": "题目：作者信息*",
            "footnote_references": {
                "kind": "footnote",
                "id": 1,
                "display_marker": "*",
                "custom_mark": False,
            },
        },
        {"text": "张三", "role_hint": "author_name"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "ERROR"
    assert finding["observed"]["marker_evidence"][0]["reason"] == "associated_nonmatching_marker"


def test_title_marker_surfaces_unknown_custom_mark_without_literal_glyph() -> None:
    inspection = _inspection(
        {
            "text": "题目：作者信息",
            "footnote_references": [
                {
                    "kind": "footnote",
                    "id": 1,
                    "marker": "footnoteReference",
                    "custom_mark_follows": "yes",
                    "automatic": "false",
                }
            ],
        },
        {"text": "张三", "role_hint": "author_name"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "MANUAL_REVIEW"
    evidence = finding["observed"]["marker_evidence"][0]
    assert evidence["reason"] == "custom_marker_without_literal_glyph"
    assert evidence["custom_mark_follows"] is True


def test_title_marker_collects_bound_reference_evidence_from_inspector_shapes() -> None:
    title = {
        "text": "题目：多来源脚注*",
        "footnote_references": {"kind": "footnote", "id": 1, "marker": "*"},
        "footnote_refs": [{"kind": "footnote", "id": 2, "marker": "*"}],
        "footnote_reference_evidence": [{"kind": "footnote", "id": 3, "marker": "*"}],
        "note_references": [
            {"kind": "footnote", "id": 4, "marker": "*"},
            {"kind": "endnote", "id": 5, "marker": "1"},
        ],
        "footnote_reference": {"kind": "footnote", "id": 6, "marker": "*"},
    }
    inspection = _inspection(
        title,
        {"text": "张三", "role_hint": "author_name"},
        footnote_references=[
            "not-a-reference",
            {"target_id": "p-000000", "id": 7, "marker": "*"},
        ],
        notes={
            "footnotes": {
                "actual_count": 1,
                "items": [],
                "footnote_references": {
                    "p-000000": [{"kind": "footnote", "id": 8, "marker": "*"}],
                },
            }
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TITLE-002")

    assert finding["status"] == "PASS"
    assert len(finding["observed"]["footnote_reference_evidence"]) >= 5
    assert all(item["status"] == "pass" for item in finding["observed"]["marker_evidence"])


def test_author_information_term_prioritizes_disallowed_occurrence_across_paragraphs() -> None:
    inspection = _inspection(
        {"text": "作者信息：电子信箱：first@example.com", "role_hint": "author_information"},
        {"text": "作者信息：电子邮箱：second@example.com", "role_hint": "author_information"},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-AUTHORINFO-002")

    assert finding["status"] == "ERROR"
    assert finding["target"]["id"] == "p-000001"
    assert finding["observed"]["locations"][0]["span"] == [5, 9]


def test_author_information_footnote_without_author_role_requires_manual_format_review() -> None:
    inspection = _inspection(
        {"text": "正文"},
        notes={"footnotes": {"actual_count": "1", "items": []}},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-AUTHORINFO-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["author_information_evidence"] is False


def test_equation_without_formula_or_object_is_not_applicable() -> None:
    finding = _finding(lint_inspection(_inspection({"text": "正文"})), "ER-MS-EQUATION-001")

    assert finding["status"] == "NOT_APPLICABLE"


def test_equation_unknown_item_and_legacy_counts_are_reported_as_manual_evidence() -> None:
    inspection = _inspection(
        {"text": "正文"},
        equations={
            "omath_count": "3",
            "items": [{"id": "opaque-object", "kind": "ole"}],
            "editors": {
                "": 2,
                "mathtype_or_ole": 99,
                "word_equation": {"count": "2"},
            },
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-EQUATION-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["editors"] == ["Unknown OLE", "Word Equation"]
    assert finding["observed"]["editor_counts"]["Word Equation"] == 3
    assert any(item["evidence"] == ["omath_count"] for item in finding["observed"]["items"])


def test_equation_object_count_without_item_is_unknown_ole() -> None:
    inspection = _inspection(
        {"text": "正文"},
        equations={"object_count": 2, "items": [], "editors": {}},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-EQUATION-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["editor_counts"]["Unknown OLE"] == 2
    assert any(item["evidence"] == ["object_count_without_item"] for item in finding["observed"]["items"])


def test_equation_marker_without_editor_metadata_falls_back_to_unknown_ole() -> None:
    inspection = _inspection(
        {"text": "公式对象", "has_equation": True},
        equations={"omath_count": 0, "items": [], "editors": {}},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-EQUATION-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["target"]["kind"] == "document"
    assert finding["observed"]["editors"] == ["Unknown OLE"]


@pytest.mark.parametrize(
    ("prefix", "status", "expected_spaces"),
    [
        ("  ", "PASS", 2),
        (" ", "ERROR", 1),
    ],
)
def test_equation_where_requires_exact_leading_spaces(
    prefix: str, status: str, expected_spaces: int
) -> None:
    inspection = _inspection(
        {"text": "x = y", "has_equation": True},
        {
            "text": f"{prefix}其中，x表示解释变量",
            "role_hint": "equation_where_paragraph",
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-EQUATION-002")

    assert finding["status"] == status
    assert finding["observed"]["leading_spaces_cn"] == expected_spaces


def test_table_numeric_comparison_is_resolved_from_target_and_baseline_points() -> None:
    inspection = _inspection(
        {"text": "表1", "role_hint": "table_caption"},
        tables=[
            {
                "id": "table-numeric",
                "comparison": {"target_pt": 9, "baseline_pt": 10},
                "cells": [
                    {
                        "id": "cell-1",
                        "paragraphs": [
                            {
                                "id": "table-cell-p",
                                "text": "表格数据",
                                "effective_formatting": {"eastAsia": "仿宋"},
                            }
                        ],
                    }
                ],
            }
        ],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TABLE-001")

    assert finding["status"] == "PASS"
    assert finding["observed"]["font_size_relation_to_body"] == "smaller"
    assert finding["observed"]["font_size_comparison"]["target_pt"] == 9


def test_table_comparison_alias_and_mixed_flag_keep_result_manual_or_observable() -> None:
    inspection = _inspection(
        {"text": "表1", "role_hint": "table_caption"},
        tables=[
            {
                "id": "table-alias",
                "comparison": {"body_relation": {"value": "smaller"}},
                "cells": [
                    {
                        "paragraphs": [
                            {
                                "id": "table-alias-p",
                                "text": "A",
                                "effective_formatting": {"eastAsia": "仿宋"},
                            }
                        ]
                    }
                ],
            },
            {
                "id": "table-mixed-comparison",
                "comparison": {"target_pt": 9, "baseline_pt": 10, "mixed": True},
                "cells": [
                    {
                        "paragraphs": [
                            {
                                "id": "table-mixed-p",
                                "text": "B",
                                "effective_formatting": {"eastAsia": "仿宋"},
                            }
                        ]
                    }
                ],
            },
        ],
    )

    findings = _findings(lint_inspection(inspection), "ER-MS-TABLE-001")

    assert [item["status"] for item in findings] == ["PASS", "MANUAL_REVIEW"]
    assert findings[1]["observed"]["comparison_status"] == "mixed"


def test_table_note_candidate_without_body_binding_is_still_located_by_explicit_preview() -> None:
    inspection = _inspection(
        {"text": "表1", "role_hint": "table_caption"},
        tables=[
            {
                "id": "table-note-preview",
                "cells": [
                    {
                        "paragraphs": [
                            {
                                "id": "table-note-cell",
                                "text": "数据",
                                "effective_formatting": {"eastAsia": "仿宋"},
                            }
                        ]
                    }
                ],
                "note_candidates": [
                    {
                        "id": "synthetic-note",
                        "text_preview": "注：来源说明",
                        "font_size_relation_to_table": "one_cn_size_smaller",
                        "formatting": {"east_asia_font": "宋体"},
                    },
                    {"id": "not-a-note", "text_preview": "普通说明"},
                    "opaque-candidate",
                ],
            }
        ],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TABLE-NOTE-001")

    assert finding["status"] == "PASS"
    assert finding["target"]["id"] == "synthetic-note"
    assert finding["observed"]["table_note_binding"]["source"] == "inspector.note_candidates"


def test_table_note_candidate_binds_only_first_nonempty_body_paragraph_after_table() -> None:
    inspection = _inspection(
        {"text": "表1", "role_hint": "table_caption"},
        {"id": "body-before-note", "text": "这不是表注"},
        {"id": "body-note", "text": "注：真正的表注"},
        tables=[
            {
                "id": "table-binding",
                "cells": [
                    {"paragraphs": [{"id": "cell-p", "text": "数据"}]}
                ],
                "note_candidates": [
                    {
                        "paragraph_id": "body-note",
                        "text_preview": "注：真正的表注",
                        "distance": 2,
                        "font_size_relation_to_table": "one_cn_size_smaller",
                    }
                ],
            }
        ],
        body_blocks=[
            {"kind": "table", "id": "table-binding"},
            {"kind": "paragraph", "id": "body-before-note", "text": "这不是表注"},
            {"kind": "paragraph", "id": "body-note", "text": "注：真正的表注"},
        ],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TABLE-NOTE-001")

    assert finding["status"] == "NOT_APPLICABLE"
    assert finding["target"]["kind"] == "document"


def test_body_latin_font_checks_each_meaningful_run() -> None:
    inspection = _inspection(
        {
            "text": "GDP 2024",
            "runs": [
                {
                    "text": "GDP",
                    "effective_formatting": {
                        "ascii_font": "Times New Roman",
                        "hansi_font": "Times New Roman",
                    },
                },
                {"text": " "},
                {
                    "text": "2024",
                    "effective_formatting": {"ascii_font": "Arial", "hansi_font": "Arial"},
                },
            ],
        }
    )

    findings = _findings(lint_inspection(inspection), "ER-MS-LATIN-FONT-001")

    body_finding = next(item for item in findings if item["target"].get("scope") in {None, "body"})
    assert body_finding["status"] == "ERROR"
    assert len(body_finding["observed"]["font_runs"]) == 2


def test_nested_footnote_latin_target_is_emitted_once_with_note_scope() -> None:
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
                                "id": "fn-latin-p",
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
                            },
                            "not-a-paragraph",
                        ],
                    }
                ],
            }
        },
    )

    findings = _findings(lint_inspection(inspection), "ER-MS-LATIN-FONT-001")

    footnote_findings = [item for item in findings if item["target"].get("scope") == "footnote"]
    assert len(footnote_findings) == 1
    assert footnote_findings[0]["status"] == "PASS"
    assert footnote_findings[0]["target"]["id"] == "fn-latin-p"


def test_real_table_latin_fixture_keeps_table_target_locatable(tmp_path: Path) -> None:
    report = inspect_docx(make_table_latin_docx(tmp_path))

    findings = _findings(lint_inspection(report), "ER-MS-LATIN-FONT-001")

    table_finding = next(item for item in findings if item["target"].get("scope") == "table")
    assert table_finding["status"] == "ERROR"
    assert table_finding["target"]["table_index"] == 0


@pytest.mark.parametrize(
    ("notes", "status", "expected_restart"),
    [
        ({"actual_count": 1, "items": []}, "MANUAL_REVIEW", None),
        ({"actual_count": 1, "items": [], "restart": "continuous"}, "ERROR", "continuous"),
        ({"actual_count": 1, "items": [], "restart": "unknown"}, "MANUAL_REVIEW", None),
        (
            {
                "actual_count": 1,
                "items": [],
                "num_restart": ["page", "each_page"],
            },
            "PASS",
            "each_page",
        ),
        ({"actual_count": 1, "items": [], "restart": ["page", "continuous"]}, "ERROR", None),
        ({"actual_count": "not-a-count", "items": [], "restart": "page"}, "NOT_APPLICABLE", None),
    ],
)
def test_footnote_restart_evidence_fails_closed(
    notes: dict, status: str, expected_restart: str | None
) -> None:
    finding = _finding(
        lint_inspection(_inspection({"text": "正文"}, notes={"footnotes": notes})),
        "ER-MS-FOOTNOTE-001",
    )

    assert finding["status"] == status
    if status == "PASS":
        assert finding["observed"]["restarts_normalized"] == ["each_page", "each_page"]


def test_footnote_restart_reads_nested_section_effective_properties() -> None:
    inspection = _inspection(
        {"text": "正文"},
        notes={"footnotes": {"actual_count": 1, "items": []}},
        sections=[
            {"footnote_settings": {"resolved": {"numRestart": "page"}}},
            {"notes": {"properties": {"restart": ["each-page"]}}},
        ],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-FOOTNOTE-001")

    assert finding["status"] == "PASS"
    assert finding["observed"]["restarts_normalized"] == ["each_page", "each_page"]


def test_footnote_text_with_actual_count_but_no_paragraph_is_manual() -> None:
    inspection = _inspection(
        {"text": "正文"},
        notes={"footnotes": {"actual_count": 1, "items": []}},
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-FOOTNOTE-002")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["actual_count"] == 1


def test_footnote_text_with_partial_formatting_requires_font_and_size_evidence() -> None:
    inspection = _inspection(
        {"text": "正文"},
        notes={
            "footnotes": {
                "actual_count": 1,
                "items": [
                    {
                        "id": 1,
                        "text_preview": "脚注正文",
                        "effective_formatting": {"east_asia_font": "宋体"},
                    }
                ],
            }
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-FOOTNOTE-002")

    assert finding["status"] == "MANUAL_REVIEW"
    assert "font_size_cn" in finding["observed"]["missing_formatting_evidence"]


def test_unknown_figure_color_analysis_requires_manual_review() -> None:
    inspection = _inspection(
        {"text": "图1.1 图题", "role_hint": "figure_caption"},
        images=[{"id": "img-unknown", "color_analysis": {}}],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-FIGURE-002")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["target"]["id"] == "img-unknown"


def test_figure_caption_and_reference_layout_accept_observed_formatting() -> None:
    inspection = _inspection(
        {
            "text": "图1.1 结果",
            "role_hint": "figure_caption",
            "effective_formatting": {
                "east_asia_font": "黑体",
                "size_pt": 9,
                "alignment": "center",
            },
        },
        {
            "text": "  Smith, J. (2019). Article. Journal, 3, 1—9.",
            "role_hint": "reference_entry",
            "effective_formatting": {"east_asia_font": "宋体", "size_pt": 7.5},
        },
    )

    audit = lint_inspection(inspection)

    figure = _finding(audit, "ER-MS-FIGURE-001")
    reference = _finding(audit, "ER-MS-REF-LAYOUT-001")
    assert figure["status"] == "PASS"
    assert reference["status"] == "PASS"
    assert reference["observed"]["east_asia_font"] == "宋体"
    assert reference["observed"]["size_pt"] == 7.5


def test_reference_page_range_with_multiple_unmarked_ranges_requires_manual_review() -> None:
    inspection = _inspection(
        {"text": "Section 12–13; appendix 20–30", "role_hint": "reference_entry"}
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-REF-PAGERANGE-001")

    assert finding["status"] == "MANUAL_REVIEW"
    assert finding["observed"]["selection_reason"] == (
        "multiple non-parenthesized ranges lack a colon/page marker boundary"
    )


def test_manual_reference_rules_expose_review_entry_for_each_semantic_unknown() -> None:
    inspection = _inspection(
        {"text": "Smith, J. (2019). Article. Journal, 1, 1—2.", "role_hint": "reference_entry"}
    )

    for rule_id in (
        "ER-MS-REF-TITLECASE-001",
        "ER-MS-REF-AUTHOR-001",
        "ER-MS-REF-JOURNAL-001",
    ):
        rule = {
            "id": rule_id,
            "target": "reference_entry",
            "requirement": {},
            "lint": {"severity": "manual_review"},
        }
        context = RuleContext(
            inspection,
            classify_inspection(inspection),
            {rule_id: rule},
        )
        finding = lint_manuscript_rule(rule, context)[0]
        assert finding["status"] == "MANUAL_REVIEW"
        assert finding["observed"]["entry_count"] == 1


def test_unknown_manuscript_rule_has_no_handler_result() -> None:
    inspection = _inspection({"text": "正文"})
    context = RuleContext(inspection, classify_inspection(inspection), {})

    assert lint_manuscript_rule({"id": "ER-MS-UNKNOWN-999"}, context) == []
