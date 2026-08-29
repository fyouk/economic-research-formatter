from __future__ import annotations

from economic_research_formatter.lint.engine import lint_inspection
from economic_research_formatter.report.markdown_report import render_markdown


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


def test_unsupported_table_font_relation_never_passes_without_inspector_evidence() -> None:
    inspection = _inspection(
        {"text": "表1", "role_hint": "table_caption"},
        tables=[
            {
                "id": "table-000000",
                "cells": [
                    {
                        "paragraphs": [
                            {
                                "id": "t-0000-c-0000-p-0000",
                                "text": "表格正文",
                                "effective_formatting": {"eastAsia": "仿宋"},
                            }
                        ]
                    }
                ],
            }
        ],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TABLE-001")

    assert finding["status"] in {"NOT_CHECKED", "MANUAL_REVIEW"}


def test_observed_table_font_relation_can_support_a_pass() -> None:
    inspection = _inspection(
        tables=[
            {
                "id": "table-000000",
                "cells": [
                    {
                        "paragraphs": [
                            {
                                "id": "t-0000-c-0000-p-0000",
                                "text": "表格正文",
                                "effective_formatting": {
                                    "eastAsia": "仿宋",
                                    "font_size_relation_to_body": "smaller",
                                },
                            }
                        ]
                    }
                ],
            }
        ],
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-TABLE-001")

    assert finding["status"] == "PASS"


def test_missing_actual_footnote_font_and_size_evidence_is_manual_review() -> None:
    inspection = _inspection(
        notes={
            "footnotes": {
                "actual_count": 1,
                "items": [{"id": 1, "text_preview": "普通脚注内容"}],
            }
        },
    )

    finding = _finding(lint_inspection(inspection), "ER-MS-FOOTNOTE-002")

    assert finding["status"] == "MANUAL_REVIEW"


def test_markdown_escapes_untrusted_filename_and_content() -> None:
    audit = {
        "schema_version": "1.0",
        "input": {"filename": "bad|name`<script>[x](javascript:alert(1)).docx"},
        "summary": {
            "total_findings": 1,
            "by_status": {"ERROR": 1},
            "aggregates": [
                {
                    "rule_id": "ER-X",
                    "status": "ERROR",
                    "count": 1,
                    "message": "内容 <b>bad</b>",
                    "examples": [
                        {
                            "kind": "paragraph",
                            "id": "[x](javascript:alert(1))",
                            "text_preview": "<script>alert('x')</script>",
                        }
                    ],
                }
            ],
        },
        "findings": [],
        "capabilities": {},
    }

    markdown = render_markdown(audit)

    assert "<script>" not in markdown
    assert "[x](javascript:alert(1))" not in markdown
    assert "\\|" in markdown


def test_finding_snippets_are_bounded_without_truncating_structural_fields() -> None:
    from economic_research_formatter.models.audit import make_finding

    finding = make_finding(
        "ER-X",
        "INFO",
        "message",
        target={"kind": "paragraph", "id": "stable-id", "text_preview": "x" * 120},
        observed={
            "candidate": "a" * 120,
            "observed_excerpt": "b" * 120,
            "structural_label": "c" * 120,
        },
    )

    assert len(finding["target"]["text_preview"]) == 80
    assert len(finding["observed"]["candidate"]) == 80
    assert len(finding["observed"]["observed_excerpt"]) == 80
    assert len(finding["observed"]["structural_label"]) == 120
