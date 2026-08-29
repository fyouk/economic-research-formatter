"""TDD regressions for audit summary and repeated table finding reporting."""

from __future__ import annotations

from economic_research_formatter.lint.engine import _aggregate_table_unknown_findings
from economic_research_formatter.models.audit import build_summary
from economic_research_formatter.report.markdown_report import render_markdown


RULE_ID = "ER-MS-TABLE-NOTE-001"


def _unknown_table_findings() -> list[dict]:
    return [
        {
            "rule_id": RULE_ID,
            "status": "MANUAL_REVIEW",
            "message": "表格字号比较证据未知。",
            "target": {
                "kind": "paragraph",
                "id": f"cell-note-{index}",
                "index": index,
                "table_id": "table-000001",
                "cell_id": f"cell-{index}",
            },
            "observed": {"comparison_status": "unknown"},
        }
        for index in range(7)
    ]


def test_summary_exposes_rule_and_status_counts_and_table_unknown_group() -> None:
    summary = build_summary(_unknown_table_findings())

    assert summary["finding_count"] == 7
    assert summary["affected_target_count"] == 7
    assert summary["by_rule_and_status"][RULE_ID]["MANUAL_REVIEW"] == 7
    assert summary["by_rule_and_status_affected"][RULE_ID]["MANUAL_REVIEW"] == 7
    assert summary["by_status_affected"]["MANUAL_REVIEW"] == 7
    assert len(summary["table_aggregates"]) == 1
    aggregate = summary["table_aggregates"][0]
    assert aggregate["rule_id"] == RULE_ID
    assert aggregate["status"] == "MANUAL_REVIEW"
    assert aggregate["table_id"] == "table-000001"
    assert aggregate["count"] == 7
    assert len(aggregate["examples"]) == 3


def test_summary_distinguishes_folded_finding_count_from_affected_targets() -> None:
    finding = _unknown_table_findings()[0]
    finding["target"] = {"kind": "table", "id": "table-000001", "table_id": "table-000001"}
    finding["observed"] = {
        "aggregation": "table_unknown",
        "count": 7,
        "examples": _unknown_table_findings()[:3],
        "comparison_status": "unknown",
    }
    summary = build_summary([finding])

    assert summary["finding_count"] == 1
    assert summary["affected_target_count"] == 7
    assert summary["by_rule_and_status"][RULE_ID]["MANUAL_REVIEW"] == 1
    assert summary["by_rule_and_status_affected"][RULE_ID]["MANUAL_REVIEW"] == 7
    assert summary["by_status"]["MANUAL_REVIEW"] == 1
    assert summary["by_status_affected"]["MANUAL_REVIEW"] == 7
    assert summary["aggregates"][0]["finding_count"] == 1
    assert summary["aggregates"][0]["affected_count"] == 7


def test_markdown_surfaces_table_group_without_repeating_each_unknown_cell() -> None:
    findings = _unknown_table_findings()
    summary = build_summary(findings)
    markdown = render_markdown({"summary": summary, "findings": findings})

    assert "按表格聚合" in markdown
    assert "table-000001" in markdown
    assert "finding 数" in markdown
    assert "受影响目标" in markdown
    assert "| 人工复核（MANUAL_REVIEW） | 7 | 7 |" in markdown
    assert "共记录 7 条规则结果，涉及 7 个受影响目标" in markdown
    assert markdown.count("表格字号比较证据未知") <= 2


def test_table_fold_keeps_distinct_unknown_evidence_in_separate_groups() -> None:
    findings = _unknown_table_findings()[:2]
    findings[0]["observed"] = {"comparison_status": "unknown", "reason": "mixed_runs"}
    findings[1]["observed"] = {
        "comparison_status": "unknown",
        "reason": "missing_table_size",
    }

    folded = _aggregate_table_unknown_findings(findings)

    assert len(folded) == 2
    assert {item["observed"]["reason"] for item in folded} == {
        "mixed_runs",
        "missing_table_size",
    }


def test_table_fold_uses_unknown_evidence_not_message_as_group_key() -> None:
    findings = _unknown_table_findings()[:2]
    findings[0]["message"] = "first wording"
    findings[1]["message"] = "second wording"
    findings[0]["observed"] = findings[1]["observed"] = {
        "comparison_status": "unknown",
        "reason": "mixed_runs",
    }

    folded = _aggregate_table_unknown_findings(findings)

    assert len(folded) == 1
    assert folded[0]["observed"]["count"] == 2


def test_markdown_recomputes_stale_legacy_summary_from_detailed_findings() -> None:
    findings = _unknown_table_findings()[:2]
    audit = {
        "summary": {
            "total_findings": 53,
            "by_status": {"MANUAL_REVIEW": 53},
            "aggregates": [
                {
                    "rule_id": RULE_ID,
                    "status": "MANUAL_REVIEW",
                    "count": 53,
                    "message": "stale",
                }
            ],
        },
        "findings": findings,
    }

    markdown = render_markdown(audit)

    assert "共记录 2 条规则结果，涉及 2 个受影响目标。" in markdown
    assert "| 人工复核（MANUAL_REVIEW） | 2 | 2 |" in markdown
    assert "| 53 |" not in markdown


def test_markdown_derives_coherent_counts_from_summary_only_legacy_shape() -> None:
    audit = {
        "summary": {
            "total_findings": 1,
            "by_status": {"ERROR": 1},
            "aggregates": [
                {
                    "rule_id": "ER-LEGACY-001",
                    "status": "ERROR",
                    "count": 1,
                    "message": "legacy summary only",
                }
            ],
        },
        "findings": [],
    }

    markdown = render_markdown(audit)

    assert "共记录 1 条规则结果，涉及 1 个受影响目标。" in markdown
    assert "| 错误（ERROR） | 1 | 1 |" in markdown
