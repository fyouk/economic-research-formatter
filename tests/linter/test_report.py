from __future__ import annotations

import json

from economic_research_formatter.report.json_report import serialize_audit
from economic_research_formatter.report.markdown_report import render_markdown


def _audit() -> dict:
    return {
        "schema_version": "1.0",
        "summary": {
            "total_findings": 2,
            "by_status": {"ERROR": 2},
            "aggregates": [
                {
                    "rule_id": "ER-MS-REF-LAYOUT-001",
                    "status": "ERROR",
                    "count": 53,
                    "message": "参考文献条目格式不符合要求",
                }
            ],
        },
        "findings": [
            {
                "rule_id": "ER-MS-REF-LAYOUT-001",
                "status": "ERROR",
                "message": "参考文献条目格式不符合要求",
                "target": {"kind": "paragraph", "id": "p-1", "index": 1, "text_preview": "条目"},
                "observed": {},
                "expected": {},
                "source": {},
                "confidence": 0.99,
            },
            {
                "rule_id": "ER-MS-REF-LAYOUT-001",
                "status": "ERROR",
                "message": "参考文献条目格式不符合要求",
                "target": {"kind": "paragraph", "id": "p-2", "index": 2, "text_preview": "条目"},
                "observed": {},
                "expected": {},
                "source": {},
                "confidence": 0.99,
            },
        ],
        "capabilities": {"implemented": ["ER-MS-REF-LAYOUT-001"], "not_checked": []},
    }


def test_json_serialization_is_utf8_deterministic_and_does_not_add_time() -> None:
    audit = _audit()
    first = serialize_audit(audit)
    second = serialize_audit(audit)

    assert first == second
    assert json.loads(first) == audit
    assert "timestamp" not in json.loads(first)


def test_markdown_aggregates_repeated_findings_but_keeps_rule_and_examples() -> None:
    markdown = render_markdown(_audit())

    assert markdown.startswith("# 《经济研究》格式审计报告")
    assert markdown.count("ER-MS-REF-LAYOUT-001") >= 1
    assert "53" in markdown
    assert "p-1" in markdown or "p-2" in markdown
    assert markdown.count("参考文献条目格式不符合要求") <= 2
