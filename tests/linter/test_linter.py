from __future__ import annotations

import json
from pathlib import Path
import shutil

import yaml

from economic_research_formatter.lint.engine import lint_inspection


def _run(font: str = "宋体", size: str = "三号", *, bold: bool | None = None) -> dict:
    direct = {"eastAsia": font, "font_size_cn": size, "alignment": "left"}
    if bold is not None:
        direct["bold"] = bold
    return {"text": "题目：合成标题", "effective_formatting": direct}


def _inspection() -> dict:
    return {
        "schema_version": "1.0",
        "input": {"filename": "synthetic.docx", "sha256": "a" * 64, "size_bytes": 1},
        "paragraphs": [
            {"id": "p-000000", "index": 0, **_run(font="仿宋", size="五号")},
            {"id": "p-000001", "index": 1, "text": "摘要", "style_name": "Heading 1"},
            {
                "id": "p-000002",
                "index": 2,
                "text": "这是摘要。",
                "effective_formatting": {"eastAsia": "宋体", "font_size_cn": "小四号"},
            },
            {"id": "p-000003", "index": 3, "text": "关键词：测试"},
            {"id": "p-000004", "index": 4, "text": "第一章 绪论", "style_name": "Heading 1"},
            {"id": "p-000005", "index": 5, "text": "参考文献", "style_name": "Heading 1"},
            {
                "id": "p-000006",
                "index": 6,
                "text": "Smith, J. (2020). Title. Journal, 15-25.",
                "numbering": {"numId": 4, "ilvl": 0},
                "in_reference": True,
                "effective_formatting": {"eastAsia": "宋体", "font_size_cn": "小四号"},
            },
            {"id": "p-000007", "index": 7, "text": "第一章 绪论", "in_toc": True, "toc": True},
        ],
        "tables": [],
        "images": [],
        "equations": {"omath_count": 0, "omath_para_count": 0, "paragraph_ids": []},
        "notes": {"footnotes": {"actual_count": 0}},
        "fields": {"counts": {"TOC": 1}},
    }


def _rule(rule_id: str, requirement: dict, *, severity: str = "error") -> dict:
    return {
        "id": rule_id,
        "domain": "manuscript",
        "target": "title",
        "normativity": "mandatory",
        "source": {
            "source_id": "ER-MS-IMG-NOTES",
            "source_type": "red_annotation",
            "locator": "synthetic",
            "evidence": "synthetic evidence",
        },
        "requirement": requirement,
        "lint": {"severity": severity},
        "autofix": "never",
    }


def _write_custom_rules(root: Path, requirement: dict) -> None:
    rules_dir = root / "rules"
    rules_dir.mkdir()
    repository_root = Path(__file__).resolve().parents[2]
    shutil.copy2(repository_root / "rules" / "schema.yaml", rules_dir / "schema.yaml")
    source_index_dir = root / "sources" / "normalized"
    source_index_dir.mkdir(parents=True)
    shutil.copy2(
        repository_root / "sources" / "normalized" / "source-index.yaml",
        source_index_dir / "source-index.yaml",
    )
    payloads = {
        "manuscript.yaml": {"version": 1, "rules": [_rule("ER-MS-TITLE-001", requirement)]},
        "citations.yaml": {"version": 1, "rules": []},
        "references.yaml": {"version": 1, "rules": []},
        "conflicts.yaml": {"version": 1, "conflicts": []},
        "unresolved.yaml": {"version": 1, "unknowns": []},
    }
    for name, payload in payloads.items():
        (rules_dir / name).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )


def test_linter_reads_expected_values_from_rule_requirement(tmp_path: Path) -> None:
    _write_custom_rules(
        tmp_path,
        {"east_asia_font": "仿宋", "font_size_cn": "五号", "alignment": "left"},
    )

    audit = lint_inspection(_inspection(), root=tmp_path)
    findings = [item for item in audit["findings"] if item["rule_id"] == "ER-MS-TITLE-001"]

    assert len(findings) == 1
    assert findings[0]["status"] == "PASS"
    assert findings[0]["expected"]["east_asia_font"] == "仿宋"
    assert findings[0]["expected"]["font_size_cn"] == "五号"
    assert findings[0]["expected"]["alignment"] == "left"


def test_linter_compares_chinese_size_requirement_to_inspector_point_value(tmp_path: Path) -> None:
    _write_custom_rules(
        tmp_path,
        {"east_asia_font": "仿宋", "font_size_cn": "五号", "alignment": "left"},
    )
    inspection = _inspection()
    inspection["paragraphs"][0]["effective_formatting"] = {
        "eastAsia": "仿宋",
        "size_pt": 10.5,
        "alignment": "left",
    }
    audit = lint_inspection(inspection, root=tmp_path)
    finding = next(item for item in audit["findings"] if item["rule_id"] == "ER-MS-TITLE-001")

    assert finding["status"] == "PASS"
    # The Inspector exposes points, while the rule uses Chinese size names;
    # the point value must still be surfaced in observed evidence.
    assert finding["observed"]["size_pt"] == 10.5


def test_linter_excludes_toc_and_references_from_body_citation_checks() -> None:
    audit = lint_inspection(_inspection())
    citation_findings = [
        finding
        for finding in audit["findings"]
        if finding["rule_id"].startswith("ER-CIT-")
        and finding["target"]["kind"] == "paragraph"
    ]
    paragraph_by_id = {p["id"]: p for p in _inspection()["paragraphs"]}

    assert all(not paragraph_by_id[f["target"]["id"]].get("in_toc") for f in citation_findings)
    assert all(f["target"].get("role") != "reference_entry" for f in citation_findings)


def test_linter_uses_status_semantics_and_capability_summary() -> None:
    audit = lint_inspection(_inspection())
    by_rule: dict[str, set[str]] = {}
    for finding in audit["findings"]:
        by_rule.setdefault(finding["rule_id"], set()).add(finding["status"])

    assert by_rule["ER-MS-FOOTNOTE-001"] == {"NOT_APPLICABLE"}
    assert by_rule["ER-MS-FOOTNOTE-002"] == {"NOT_APPLICABLE"}
    assert by_rule["ER-CONFLICT-001"] == {"MANUAL_REVIEW"}
    assert by_rule["ER-CONFLICT-002"] == {"MANUAL_REVIEW"}
    assert "ER-UNKNOWN-001" in audit["capabilities"]["not_checked"]
    assert "NOT_CHECKED" in audit["summary"]["by_status"]
    assert "ERROR" in audit["summary"]["by_status"]


def test_linter_finding_schema_is_json_serializable_and_deterministic() -> None:
    first = lint_inspection(_inspection())
    second = lint_inspection(_inspection())

    assert first == second
    json.dumps(first, ensure_ascii=False, sort_keys=True)
    for finding in first["findings"]:
        assert set(("rule_id", "status", "message", "target", "observed", "expected", "source", "confidence")) <= set(finding)
        assert 0 <= finding["confidence"] <= 1


def test_citation_candidates_accept_fullwidth_parentheses_without_spacing() -> None:
    inspection = _inspection()
    inspection["paragraphs"] = [
        {
            "id": "p-000100",
            "index": 100,
            "text": "何威风和刘启亮（2010）发现了治理效应。",
        },
        {
            "id": "p-000101",
            "index": 101,
            "text": "Smith and Jones（2020）也有类似结论。",
        },
    ]

    audit = lint_inspection(inspection)
    by_rule = {
        rule_id: {item["status"] for item in audit["findings"] if item["rule_id"] == rule_id}
        for rule_id in (
            "ER-CIT-ZH-TWOAUTHORS-001",
            "ER-CIT-EN-TWOAUTHORS-001",
        )
    }

    assert by_rule["ER-CIT-ZH-TWOAUTHORS-001"] == {"PASS"}
    assert by_rule["ER-CIT-EN-TWOAUTHORS-001"] == {"ERROR"}


def test_citation_surface_check_uses_truncated_preview_when_closing_parenthesis_is_missing() -> None:
    inspection = _inspection()
    inspection["paragraphs"] = [
        {
            "id": "p-000102",
            "index": 102,
            "text_preview": "借鉴已有研究（张三，2020;李四，2021",
        }
    ]

    audit = lint_inspection(inspection)
    statuses = {
        item["status"]
        for item in audit["findings"]
        if item["rule_id"] == "ER-CIT-MULTI-AUTHORSOURCES-001"
    }

    assert statuses == {"ERROR"}
