from __future__ import annotations

from pathlib import Path

import yaml

from economic_research_formatter.rule_loader import validate_rules_structured


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


def test_schema_required_fields_are_enforced_for_every_rule(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    schema = _read_yaml(root, "schema.yaml")
    schema["rule_schema"]["required_fields"].append("rationale")
    _write_yaml(root, "schema.yaml", schema)

    result = validate_rules_structured(root)

    missing = [
        issue
        for issue in result["errors"]
        if issue["code"] == "missing_field"
        and issue["field_path"].endswith(".rationale")
    ]
    assert result["valid"] is False
    assert len(missing) == result["rule_count"]


def test_schema_required_field_names_must_be_unique_and_supported(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    schema = _read_yaml(root, "schema.yaml")
    required = schema["rule_schema"]["required_fields"]
    required.extend(["id", "not_a_rule_field"])
    _write_yaml(root, "schema.yaml", schema)

    result = validate_rules_structured(root)
    by_code = {issue["code"] for issue in result["errors"]}

    assert "duplicate_required_field" in by_code
    assert "unknown_required_field" in by_code


def test_schema_required_field_names_must_be_nonempty_strings(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    schema = _read_yaml(root, "schema.yaml")
    schema["rule_schema"]["required_fields"].extend(["", 7])
    _write_yaml(root, "schema.yaml", schema)

    result = validate_rules_structured(root)
    paths = {issue["field_path"] for issue in result["errors"]}

    assert "rule_schema.required_fields[8]" in paths
    assert "rule_schema.required_fields[9]" in paths
