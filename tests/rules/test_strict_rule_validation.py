from __future__ import annotations

import json
from pathlib import Path

import yaml

from economic_research_formatter.rule_loader import (
    load_executable_rules,
    validate_rules,
    validate_rules_structured,
)


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


def _issues(result: dict, *, code: str | None = None) -> list[dict]:
    issues = result["errors"]
    if code is not None:
        issues = [issue for issue in issues if issue["code"] == code]
    return issues


def test_valid_repository_has_machine_readable_zero_error_result():
    result = validate_rules_structured()

    json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["rule_count"] == 49
    assert result["conflict_count"] == 3
    assert result["unresolved_count"] == 13


def test_validation_aggregates_filename_rule_id_and_field_path(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    rule = data["rules"][0]
    rule["domain"] = "not-a-domain"
    rule["unknown_field"] = True
    del rule["source"]["evidence"]
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)

    assert result["valid"] is False
    assert len(result["errors"]) >= 3
    assert all(issue["filename"] == "manuscript.yaml" for issue in result["errors"])
    assert all(issue["rule_id"] == "ER-MS-TITLE-001" for issue in result["errors"])
    paths = {issue["field_path"] for issue in result["errors"]}
    assert "rules[0].domain" in paths
    assert "rules[0].unknown_field" in paths
    assert "rules[0].source.evidence" in paths

    legacy = validate_rules(root)
    assert len(legacy) == len(result["errors"])
    assert any(
        "manuscript.yaml" in message
        and "ER-MS-TITLE-001" in message
        and "rules[0].domain" in message
        for message in legacy
    )


def test_validation_checks_top_level_shape_and_unknown_fields(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    data["version"] = "1"
    data["rules"] = {}
    data["unexpected"] = True
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)

    paths = {issue["field_path"] for issue in result["errors"]}
    assert "version" in paths
    assert "rules" in paths
    assert "unexpected" in paths


def test_validation_requires_rule_schema_version_one(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    data["version"] = 2
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)

    assert any(
        issue["code"] == "unsupported_version"
        and issue["field_path"] == "version"
        for issue in result["errors"]
    )


def test_schema_definition_rejects_unknown_fields_and_wrong_enum_shapes(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "schema.yaml")
    data["rule_schema"]["untrusted"] = True
    data["rule_schema"]["enums"]["domain"] = "manuscript"
    _write_yaml(root, "schema.yaml", data)

    result = validate_rules_structured(root)
    paths = {issue["field_path"] for issue in result["errors"]}

    assert "rule_schema.untrusted" in paths
    assert "rule_schema.enums.domain" in paths


def test_conflict_status_must_be_declared_in_schema(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "conflicts.yaml")
    data["conflicts"][0]["status"] = "resolved"
    _write_yaml(root, "conflicts.yaml", data)

    result = validate_rules_structured(root)

    assert any(
        issue["code"] == "invalid_conflict_status"
        and issue["field_path"] == "conflicts[0].status"
        for issue in result["errors"]
    )


def test_validation_checks_id_pattern_and_repository_wide_uniqueness(tmp_path):
    root = _copy_rule_tree(tmp_path)
    manuscript = _read_yaml(root, "manuscript.yaml")
    manuscript["rules"][0]["id"] = "ER-MS-title-1"
    _write_yaml(root, "manuscript.yaml", manuscript)

    citations = _read_yaml(root, "citations.yaml")
    citations["rules"][0]["id"] = "ER-MS-TITLE-002"
    _write_yaml(root, "citations.yaml", citations)

    result = validate_rules_structured(root)

    assert _issues(result, code="invalid_id")
    duplicate = _issues(result, code="duplicate_id")
    assert duplicate
    assert duplicate[0]["filename"] == "citations.yaml"
    assert duplicate[0]["rule_id"] == "ER-MS-TITLE-002"


def test_validation_checks_enum_types_non_empty_fields_and_source_resolution(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    rule = data["rules"][0]
    rule["domain"] = 7
    rule["normativity"] = "unsupported"
    rule["autofix"] = "sometimes"
    rule["lint"]["severity"] = "loud"
    rule["source"]["source_type"] = "invented"
    rule["source"]["source_id"] = "ER-NOT-IN-INDEX"
    rule["source"]["locator"] = "  "
    rule["source"]["evidence"] = ""
    rule["requirement"] = []
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)
    codes = {issue["code"] for issue in result["errors"]}

    assert {
        "invalid_enum",
        "invalid_type",
        "empty_value",
        "unknown_source_id",
    } <= codes


def test_recommended_error_requires_explicit_rationale(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    rule = data["rules"][3]
    assert rule["normativity"] == "recommended"
    rule["lint"]["severity"] = "error"
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)

    issues = _issues(result, code="missing_rationale")
    assert issues
    assert issues[0]["rule_id"] == "ER-MS-AUTHORINFO-001"
    assert issues[0]["field_path"] == "rules[3].rationale"


def test_recommended_error_with_nonempty_rationale_is_allowed(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    rule = data["rules"][3]
    rule["lint"]["severity"] = "error"
    rule["rationale"] = "This exception is explicitly approved for this target."
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)

    assert not _issues(result, code="missing_rationale")
    assert result["valid"] is True


def test_example_only_cannot_be_autofixed_or_silently_escalated(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    rule = data["rules"][0]
    rule["normativity"] = "example_only"
    rule["lint"]["severity"] = "error"
    rule["autofix"] = "safe"
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)
    codes = {issue["code"] for issue in result["errors"]}

    assert "example_only_error" in codes
    assert "example_only_autofix" in codes


def test_manual_review_rejects_safe_and_conditional_autofix(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    rule = data["rules"][0]
    rule["lint"]["severity"] = "manual_review"
    rule["autofix"] = "conditional"
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)

    issues = _issues(result, code="manual_review_autofix")
    assert issues
    assert issues[0]["field_path"] == "rules[0].autofix"


def test_conflicts_validate_rule_references_effective_rule_and_execution_semantics(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "conflicts.yaml")
    data["conflicts"][0]["source_a"]["rule_id"] = "ER-MS-MISSING-999"
    data["conflicts"][0]["execution"]["lint"] = "error"
    data["conflicts"][0]["execution"]["autofix"] = "safe"
    data["conflicts"][2]["effective_rule"] = "ER-MS-MISSING-999"
    _write_yaml(root, "conflicts.yaml", data)

    result = validate_rules_structured(root)
    codes = {issue["code"] for issue in result["errors"]}

    assert "unknown_rule_reference" in codes
    assert "unknown_effective_rule" in codes
    assert "unresolved_conflict_executable" in codes


def test_unresolved_conflict_without_execution_is_not_executable(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "conflicts.yaml")
    del data["conflicts"][0]["execution"]
    _write_yaml(root, "conflicts.yaml", data)

    result = validate_rules_structured(root)

    assert _issues(result, code="unresolved_conflict_executable")


def test_executable_rule_loader_excludes_rules_in_unresolved_conflicts():
    rules = load_executable_rules()
    ids = {rule["id"] for rule in rules}

    assert "ER-MS-REF-TITLECASE-001" not in ids
    assert "ER-MS-REF-AUTHOR-001" not in ids
    assert "ER-MS-REF-PAGERANGE-001" in ids


def test_unresolved_entries_require_unique_id_valid_status_and_topic(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "unresolved.yaml")
    data["unknowns"][0]["topic"] = ""
    data["unknowns"][1]["status"] = "resolved"
    data["unknowns"][2]["id"] = data["unknowns"][0]["id"]
    _write_yaml(root, "unresolved.yaml", data)

    result = validate_rules_structured(root)
    codes = {issue["code"] for issue in result["errors"]}

    assert "empty_value" in codes
    assert "invalid_unresolved_status" in codes
    assert "duplicate_unresolved_id" in codes


def test_malformed_conflict_status_is_reported_without_crashing(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "conflicts.yaml")
    data["conflicts"][0]["status"] = {"unexpected": "mapping"}
    _write_yaml(root, "conflicts.yaml", data)

    result = validate_rules_structured(root)

    assert any(issue["code"] == "invalid_type" for issue in result["errors"])


def test_malformed_rule_autofix_type_is_reported_without_crashing(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    data["rules"][0]["autofix"] = ["safe"]
    data["rules"][0]["normativity"] = "example_only"
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)

    assert any(
        issue["code"] == "invalid_type"
        and issue["field_path"] == "rules[0].autofix"
        for issue in result["errors"]
    )


def test_unknown_rule_and_unknown_conflict_fields_are_rejected(tmp_path):
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "conflicts.yaml")
    data["conflicts"][0]["untrusted_field"] = True
    _write_yaml(root, "conflicts.yaml", data)

    result = validate_rules_structured(root)

    assert any(
        issue["code"] == "unknown_field"
        and issue["field_path"] == "conflicts[0].untrusted_field"
        for issue in result["errors"]
    )
