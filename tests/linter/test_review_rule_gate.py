from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from economic_research_formatter.lint.engine import lint_inspection


RULE_FILENAMES = (
    "schema.yaml",
    "manuscript.yaml",
    "citations.yaml",
    "references.yaml",
    "conflicts.yaml",
    "unresolved.yaml",
)


def _copy_rule_tree(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "rules").mkdir(parents=True)
    (root / "sources" / "normalized").mkdir(parents=True)
    source_root = Path(__file__).parents[2]
    for filename in RULE_FILENAMES:
        source = source_root / "rules" / filename
        (root / "rules" / filename).write_bytes(source.read_bytes())
    source_index = source_root / "sources" / "normalized" / "source-index.yaml"
    (root / "sources" / "normalized" / "source-index.yaml").write_bytes(
        source_index.read_bytes()
    )
    return root


def _read_yaml(root: Path, filename: str) -> dict:
    return yaml.safe_load((root / "rules" / filename).read_text(encoding="utf-8"))


def _write_yaml(root: Path, filename: str, value: dict) -> None:
    (root / "rules" / filename).write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _inspection() -> dict:
    return {
        "schema_version": "1.0",
        "input": {"filename": "synthetic.docx", "sha256": "a" * 64, "size_bytes": 1},
        "paragraphs": [
            {
                "id": "p-reference-heading",
                "index": 0,
                "text": "参考文献",
                "style_name": "Heading 1",
            },
            {
                "id": "p-reference",
                "index": 1,
                "text": "Smith, J. (2020). Title. Journal, 15-25.",
                "in_reference": True,
                "effective_formatting": {"eastAsia": "宋体", "font_size_cn": "小四号"},
            }
        ],
        "tables": [],
        "images": [],
        "equations": {"omath_count": 0, "omath_para_count": [], "paragraph_ids": []},
        "notes": {"footnotes": {"actual_count": 0}},
        "fields": {"counts": {}},
    }


def test_invalid_custom_rule_root_is_a_hard_lint_gate(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    manuscript = _read_yaml(root, "manuscript.yaml")
    manuscript["rules"][0]["domain"] = "not-a-domain"
    _write_yaml(root, "manuscript.yaml", manuscript)

    with pytest.raises(ValueError, match=r"[Rr]ule validation"):
        lint_inspection(_inspection(), root=root)


def test_unresolved_conflict_sources_are_not_executed_and_are_not_checked(tmp_path: Path) -> None:
    audit = lint_inspection(_inspection(), root=_copy_rule_tree(tmp_path))
    conflicted_rule_ids = {
        "ER-MS-REF-TITLECASE-001",
        "ER-MS-REF-AUTHOR-001",
        "ER-REF-FOREIGN-TITLE-001",
        "ER-REF-FOREIGN-AUTHOR-001",
    }

    for rule_id in conflicted_rule_ids:
        findings = [item for item in audit["findings"] if item["rule_id"] == rule_id]
        assert findings
        assert {item["status"] for item in findings} == {"NOT_CHECKED"}
        assert rule_id in audit["capabilities"]["not_checked"]
        assert rule_id not in audit["capabilities"]["implemented"]


def test_effective_rule_can_be_top_level_and_executes_once_for_example_conflict(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    conflicts = _read_yaml(root, "conflicts.yaml")
    execution = conflicts["conflicts"][2]["execution"]
    del execution["effective_rule"]
    conflicts["conflicts"][2]["effective_rule"] = "ER-MS-REF-PAGERANGE-001"
    _write_yaml(root, "conflicts.yaml", conflicts)

    audit = lint_inspection(_inspection(), root=root)
    page_range_findings = [
        item
        for item in audit["findings"]
        if item["rule_id"] == "ER-MS-REF-PAGERANGE-001"
    ]
    conflict_info = [
        item for item in audit["findings"] if item["rule_id"] == "ER-CONFLICT-003"
    ]

    assert len(page_range_findings) == 1
    assert page_range_findings[0]["status"] == "ERROR"
    assert len(conflict_info) == 1
    assert conflict_info[0]["status"] == "INFO"
    assert conflict_info[0]["expected"]["effective_rule"] == "ER-MS-REF-PAGERANGE-001"
