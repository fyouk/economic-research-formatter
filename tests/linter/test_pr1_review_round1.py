"""Real producer-chain regressions for PR1 review round one."""

from __future__ import annotations

from pathlib import Path

import pytest

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection

from tests.linter.pr1_review_round1_docx import (
    make_author_information_docx,
    make_heading_prefix_swap_docx,
    make_title_marker_docx,
    make_visible_only_heading_prefix_swap_docx,
)


def _finding(audit: dict, rule_id: str) -> dict:
    return next(item for item in audit["findings"] if item["rule_id"] == rule_id)


def test_real_heading_structure_prevents_swapped_l3_l4_prefix_pass(tmp_path: Path) -> None:
    report = inspect_docx(make_heading_prefix_swap_docx(tmp_path), include_text=True)
    classification = classify_inspection(report)
    audit = lint_inspection(report)

    records = report["paragraphs"]
    assert [record["outline_level"] for record in records] == [1, 2, 3]
    assert [record["numbering"]["ilvl"] for record in records] == [1, 2, 3]
    assert [item["role"] for item in classification["items"]] == [
        "heading_level_2",
        "heading_level_3",
        "heading_level_4",
    ]

    hierarchy_findings = [
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-HEADING-HIERARCHY-001"
    ]
    error_findings = [
        item for item in hierarchy_findings if item["status"] == "ERROR"
    ]
    assert {item["status"] for item in hierarchy_findings} == {"PASS", "ERROR"}
    assert len(error_findings) == 2
    assert {
        violation["level"]
        for finding in error_findings
        for violation in finding["observed"]["violations"]
    } == {3, 4}
    assert all(
        any(token.startswith(("style=", "outline_level=", "numbering_ilvl=")) for token in item["evidence"])
        for item in classification["items"]
    )


def test_visible_only_level_jump_never_passes_when_hierarchy_is_ambiguous(tmp_path: Path) -> None:
    report = inspect_docx(make_visible_only_heading_prefix_swap_docx(tmp_path), include_text=True)
    finding = _finding(lint_inspection(report), "ER-MS-HEADING-HIERARCHY-001")

    assert finding["status"] in {"MANUAL_REVIEW", "ERROR"}
    assert finding["status"] != "PASS"


@pytest.mark.parametrize(
    ("marker_case", "expected_status"),
    [("custom", "PASS"), ("numbered", "ERROR"), ("unknown", "MANUAL_REVIEW")],
)
def test_real_title_marker_evidence_merges_naked_ids_by_reference_id(
    tmp_path: Path, marker_case: str, expected_status: str
) -> None:
    report = inspect_docx(make_title_marker_docx(tmp_path, marker_case), include_text=True)
    audit = lint_inspection(report)

    finding = _finding(audit, "ER-MS-TITLE-002")
    assert finding["status"] == expected_status
    assert len(finding["observed"]["marker_evidence"]) == 1
    assert finding["observed"]["marker_evidence"][0]["id"] == 1
    assert len(finding["observed"]["footnote_reference_evidence"]) == 1


@pytest.mark.parametrize(
    ("label", "text", "expected_status"),
    [
        ("preferred", "作者信息：电子信箱：author@example.com", "PASS"),
        ("disallowed", "作者信息：电子邮箱：author@example.com", "ERROR"),
        ("both", "作者信息：电子邮箱：a@example.com；电子信箱：b@example.com", "ERROR"),
        ("neither", "作者信息：张三，中国某大学", None),
    ],
)
def test_real_author_information_spans_have_confidence_for_explicit_terms(
    tmp_path: Path, label: str, text: str, expected_status: str | None
) -> None:
    report = inspect_docx(
        make_author_information_docx(tmp_path, text, f"pr1-author-info-{label}.docx"),
        include_text=True,
    )
    audit = lint_inspection(report)
    finding = _finding(audit, "ER-MS-AUTHORINFO-002")
    item = audit["classification"]["items"][0]

    assert "role_hint" not in report["paragraphs"][0]
    assert item["role"] == "author_information"
    if expected_status is None:
        assert finding["status"] in {"NOT_APPLICABLE", "MANUAL_REVIEW"}
    else:
        assert finding["status"] == expected_status
        assert item["confidence"] >= 0.85
        assert finding["observed"]["locations"]


def test_real_non_contact_author_information_heuristic_stays_low_confidence(tmp_path: Path) -> None:
    report = inspect_docx(
        make_author_information_docx(tmp_path, "通讯作者：张三", "pr1-author-info-non-contact.docx"),
        include_text=True,
    )

    item = classify_inspection(report)["items"][0]
    assert item["role"] == "author_information"
    assert item["confidence"] < 0.85
