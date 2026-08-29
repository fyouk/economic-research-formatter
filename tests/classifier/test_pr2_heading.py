from __future__ import annotations

from pathlib import Path

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.docx.inspector import inspect_docx
from economic_research_formatter.lint.engine import lint_inspection

from inspector.pr2_numbering_docx import paragraph, run, write_numbering_docx


def _chain(path: Path) -> tuple[dict, dict, dict]:
    """Exercise the complete producer → classifier → linter boundary."""

    inspection = inspect_docx(path)
    classification = classify_inspection(inspection)
    audit = lint_inspection(inspection)
    return inspection, classification, audit


def test_generic_heading_one_outline_zero_does_not_hide_decimal_depth(tmp_path: Path) -> None:
    styles = (
        '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="Heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
        '</w:style>'
    )
    body = (
        paragraph(run("1.1 研究背景"), style="Heading1")
        + paragraph(run("1.1.1 研究问题"), style="Heading1")
        + paragraph(run("第1章 绪论"), style="Heading1")
    )
    path = write_numbering_docx(tmp_path, body=body, styles=styles)

    report, result, audit = _chain(path)
    items = result["items"]

    assert [item["role"] for item in items] == [
        "heading_level_2",
        "heading_level_3",
        "heading_level_1",
    ]
    assert any("style_conflict" in str(value) for value in items[0]["evidence"])
    assert any("style_conflict" in str(value) for value in items[1]["evidence"])
    hierarchy = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-HEADING-HIERARCHY-001"
    )
    assert hierarchy["status"] == "ERROR"
    assert hierarchy["observed"]["classification"]["role"] == "heading_level_2"
    assert any("style_conflict" in str(value) for value in hierarchy["observed"]["classification"]["evidence"])


def test_classifier_keeps_footnote_and_endnote_targets_separate() -> None:
    inspection = {
        "paragraphs": [],
        "notes": {
            "footnotes": {
                "actual_count": 1,
                "items": [{"id": 1, "text_preview": "脚注内容"}],
            },
            "endnotes": {
                "actual_count": 1,
                "items": [{"id": 3, "text_preview": "尾注内容"}],
            },
        },
    }

    classification = classify_inspection(inspection)

    assert classification["summary"]["note_count"] == 2
    assert {(item["kind"], item["footnote_id"] if item["kind"] == "footnote" else item["endnote_id"]) for item in classification["note_items"]} == {
        ("footnote", 1),
        ("endnote", 3),
    }
    assert all(item["target"]["kind"] == item["kind"] for item in classification["note_items"])


def test_sparse_numbering_without_explicit_state_is_unknown_to_classifier() -> None:
    inspection = {
        "paragraphs": [
            {"id": "p-heading", "text": "1.1 研究背景", "numbering": {"ilvl": 1}},
            {"id": "p-reference-heading", "text": "参考文献"},
            {
                "id": "p-reference",
                "text": "Smith, J. (2020). A paper.",
                "numbering": {"numId": 8},
            },
        ]
    }

    result = classify_inspection(inspection)
    heading, _, reference = result["items"]

    assert heading["role"] == "heading_level_2"
    assert not any(str(value).startswith("numbering_ilvl=") for value in heading["evidence"])
    assert any("unresolved_numbering" in str(value) for value in heading["evidence"])
    assert reference["role"] == "reference_entry"
    assert "automatic_numbering" not in reference["evidence"]


def test_classifier_distinguishes_explicit_resolved_and_cancelled_numbering_states() -> None:
    inspection = {
        "paragraphs": [
            {
                "id": "p-resolved",
                "text": "1.1 有效层级",
                "numbering": {
                    "num_id": 7,
                    "ilvl": 1,
                    "resolved": True,
                    "numbered": True,
                    "removes_numbering": False,
                },
            },
            {
                "id": "p-cancelled",
                "text": "1.1 取消编号",
                "numbering": {
                    "num_id": 0,
                    "ilvl": 1,
                    "resolved": True,
                    "numbered": False,
                    "removes_numbering": True,
                },
            },
        ]
    }

    items = classify_inspection(inspection)["items"]

    assert "numbering_ilvl=1" in items[0]["evidence"]
    assert "automatic_numbering" not in items[1]["evidence"]
