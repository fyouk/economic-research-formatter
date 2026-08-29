from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from economic_research_formatter.lint import registry
from economic_research_formatter.rule_loader import (
    RuleValidationError,
    ensure_rules_valid,
    load_executable_rules,
    load_rules,
    load_yaml,
    validate_rules_structured,
)


RULE_FILES = (
    "schema.yaml",
    "manuscript.yaml",
    "citations.yaml",
    "references.yaml",
    "conflicts.yaml",
    "unresolved.yaml",
)


def _copy_rule_tree(tmp_path: Path) -> Path:
    root = tmp_path / "rule-root"
    (root / "rules").mkdir(parents=True)
    (root / "sources" / "normalized").mkdir(parents=True)
    repository = Path(__file__).resolve().parents[2]
    for filename in RULE_FILES:
        (root / "rules" / filename).write_bytes(
            (repository / "rules" / filename).read_bytes()
        )
    (root / "sources" / "normalized" / "source-index.yaml").write_bytes(
        (repository / "sources" / "normalized" / "source-index.yaml").read_bytes()
    )
    return root


def _read_yaml(root: Path, filename: str) -> dict:
    return yaml.safe_load((root / "rules" / filename).read_text(encoding="utf-8"))


def _write_yaml(root: Path, filename: str, value: object) -> None:
    (root / "rules" / filename).write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _write_source_index(root: Path, value: object) -> None:
    (root / "sources" / "normalized" / "source-index.yaml").write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _codes(result: dict) -> set[str]:
    return {issue["code"] for issue in result["errors"]}


def _paths(result: dict) -> set[str]:
    return {issue["field_path"] for issue in result["errors"]}


def test_load_yaml_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    path = tmp_path / "scalar.yaml"
    path.write_text("- item\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Expected mapping at top level"):
        load_yaml(path)


def test_load_rules_rejects_a_non_list_rules_collection(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    data["rules"] = {"not": "a list"}
    _write_yaml(root, "manuscript.yaml", data)

    with pytest.raises(ValueError, match="rules must be a list: manuscript.yaml"):
        load_rules(root)


def test_validation_aggregates_missing_and_malformed_yaml_files(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    (root / "rules" / "citations.yaml").unlink()
    (root / "rules" / "references.yaml").write_text("rules: [\n", encoding="utf-8")

    result = validate_rules_structured(root)

    issues = result["errors"]
    assert any(
        issue["code"] == "file_missing"
        and issue["filename"] == "citations.yaml"
        and issue["field_path"] == "<file>"
        for issue in issues
    )
    assert any(
        issue["code"] == "yaml_error" and issue["filename"] == "references.yaml"
        for issue in issues
    )
    assert result["valid"] is False


def test_validation_reports_non_mapping_yaml_and_missing_collections(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    (root / "rules" / "schema.yaml").write_text("- scalar\n", encoding="utf-8")

    manuscript = _read_yaml(root, "manuscript.yaml")
    manuscript.pop("version")
    manuscript.pop("rules")
    _write_yaml(root, "manuscript.yaml", manuscript)

    source_index = yaml.safe_load(
        (root / "sources" / "normalized" / "source-index.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_index.pop("version")
    source_index.pop("sources")
    _write_source_index(root, source_index)

    result = validate_rules_structured(root)
    paths = _paths(result)

    assert "<root>" in paths
    assert "version" in paths
    assert "rules" in paths
    assert "sources" in paths
    assert result["valid"] is False


def test_malformed_schema_metadata_reports_shape_issues_and_uses_safe_defaults(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    schema = _read_yaml(root, "schema.yaml")
    schema["rule_schema"]["required_fields"] = None
    schema["rule_schema"]["enums"] = {
        "domain": None,
        "normativity": [],
        "source_type": [1],
        "lint_severity": [""],
        "autofix": ["safe", 1],
    }
    schema["rule_schema"]["statuses"] = {
        "conflict": None,
        "unresolved": [],
    }
    _write_yaml(root, "schema.yaml", schema)

    result = validate_rules_structured(root)
    paths = _paths(result)

    assert "rule_schema.required_fields" in paths
    assert "rule_schema.enums.domain" in paths
    assert "rule_schema.enums.normativity" in paths
    assert "rule_schema.enums.source_type[0]" in paths
    assert "rule_schema.enums.lint_severity[0]" in paths
    assert "rule_schema.enums.autofix[1]" in paths
    assert "rule_schema.statuses.conflict" in paths
    assert "rule_schema.statuses.unresolved" in paths
    assert result["valid"] is False


def test_missing_schema_definition_falls_back_to_builtin_contract(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    schema = _read_yaml(root, "schema.yaml")
    schema["rule_schema"] = None
    _write_yaml(root, "schema.yaml", schema)

    result = validate_rules_structured(root)

    assert any(
        issue["code"] == "invalid_type" and issue["field_path"] == "rule_schema"
        for issue in result["errors"]
    )
    assert result["rule_count"] == 49


def test_schema_enum_and_status_collections_must_be_mappings(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    schema = _read_yaml(root, "schema.yaml")
    schema["rule_schema"]["enums"] = []
    schema["rule_schema"]["statuses"] = "not a mapping"
    _write_yaml(root, "schema.yaml", schema)

    result = validate_rules_structured(root)
    paths = _paths(result)

    assert "rule_schema.enums" in paths
    assert "rule_schema.statuses" in paths


def test_source_index_rejects_bad_entries_and_duplicate_ids(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    source_index = yaml.safe_load(
        (root / "sources" / "normalized" / "source-index.yaml").read_text(
            encoding="utf-8"
        )
    )
    source_index["sources"].extend(
        [
            "not a mapping",
            {"id": "", "path": "  "},
            {"id": 7, "path": None},
            {"id": "DUPLICATE", "path": "first"},
            {"id": "DUPLICATE", "path": "second", "untrusted": True},
        ]
    )
    _write_source_index(root, source_index)

    result = validate_rules_structured(root)
    codes = _codes(result)

    assert {
        "invalid_type",
        "empty_value",
        "duplicate_source_id",
        "unknown_field",
    } <= codes
    assert any(
        issue["code"] == "unknown_field"
        and issue["field_path"] == "sources[7].untrusted"
        for issue in result["errors"]
    )


def test_rule_entries_cover_non_mapping_missing_enum_and_type_paths(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    malformed = data["rules"][0]
    malformed["id"] = None
    malformed.pop("domain")
    malformed.pop("normativity")
    malformed.pop("autofix")
    malformed["target"] = 7
    malformed["source"] = None
    malformed["requirement"] = {}
    malformed["lint"] = []
    malformed["rationale"] = 9
    malformed_entry_index = len(data["rules"])
    data["rules"].append("not a mapping")
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)
    codes = _codes(result)

    assert {"missing_field", "invalid_type", "empty_value"} <= codes
    assert any(
        issue["code"] == "invalid_type" and issue["field_path"] == "rules[0].target"
        for issue in result["errors"]
    )
    assert any(
        issue["code"] == "invalid_type"
        and issue["field_path"] == f"rules[{malformed_entry_index}]"
        and issue["rule_id"] == "<missing-id>"
        for issue in result["errors"]
    )


def test_rule_fields_report_empty_values_and_nested_type_errors(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    rule = data["rules"][1]
    rule["target"] = "  "
    rule["source"] = {
        "source_id": "",
        "source_type": None,
        "locator": "  ",
        "evidence": 9,
    }
    rule["requirement"] = {}
    rule["lint"] = {}
    rule["rationale"] = ""
    _write_yaml(root, "manuscript.yaml", data)

    result = validate_rules_structured(root)
    paths = _paths(result)

    assert "rules[1].target" in paths
    assert "rules[1].source.source_id" in paths
    assert "rules[1].source.source_type" in paths
    assert "rules[1].source.locator" in paths
    assert "rules[1].source.evidence" in paths
    assert "rules[1].requirement" in paths
    assert "rules[1].lint.severity" in paths
    assert "rules[1].rationale" in paths


def test_conflicts_report_bad_references_execution_and_effective_rule_conflicts(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "conflicts.yaml")
    unresolved = data["conflicts"][0]
    unresolved["status"] = "unresolved"
    unresolved["source_a"] = None
    unresolved["source_b"] = {"rule_id": 7, "source_id": 8}
    unresolved["execution"] = "not a mapping"
    unresolved["effective_rule"] = "ER-MS-REF-PAGERANGE-001"

    resolved_example = data["conflicts"][2]
    resolved_example["execution"] = {
        "lint": 9,
        "autofix": "unsupported",
        "effective_rule": "ER-MS-MISSING-999",
    }
    resolved_example["effective_rule"] = "ER-MS-REF-PAGERANGE-001"
    data["conflicts"].append("not a mapping")
    _write_yaml(root, "conflicts.yaml", data)

    result = validate_rules_structured(root)
    codes = _codes(result)

    assert {
        "invalid_type",
        "unresolved_conflict_executable",
        "conflicting_effective_rule",
        "unknown_effective_rule",
    } <= codes
    assert any(
        issue["code"] == "invalid_type"
        and issue["field_path"] == "conflicts[0].source_b.rule_id"
        for issue in result["errors"]
    )
    assert any(
        issue["code"] == "invalid_type" and issue["field_path"] == "conflicts[3]"
        for issue in result["errors"]
    )


def test_unresolved_entries_report_non_mapping_and_bad_field_types(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "unresolved.yaml")
    data["unknowns"][0]["id"] = 1
    data["unknowns"][0]["topic"] = 2
    data["unknowns"][0]["status"] = None
    malformed_entry_index = len(data["unknowns"])
    data["unknowns"].append("not a mapping")
    _write_yaml(root, "unresolved.yaml", data)

    result = validate_rules_structured(root)

    assert any(
        issue["code"] == "invalid_type" and issue["field_path"] == "unknowns[0].id"
        for issue in result["errors"]
    )
    assert any(
        issue["code"] == "invalid_type" and issue["field_path"] == "unknowns[0].topic"
        for issue in result["errors"]
    )
    assert any(
        issue["code"] == "invalid_type"
        and issue["field_path"] == f"unknowns[{malformed_entry_index}]"
        for issue in result["errors"]
    )


def test_ensure_rules_valid_raises_with_structured_diagnostics(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)
    data = _read_yaml(root, "manuscript.yaml")
    data["rules"][0]["target"] = None
    _write_yaml(root, "manuscript.yaml", data)

    with pytest.raises(RuleValidationError) as raised:
        ensure_rules_valid(root)

    error = raised.value
    assert error.root == root
    assert error.result["valid"] is False
    assert "manuscript.yaml" in str(error)
    assert "rules[0].target" in str(error)


def test_rule_validation_error_has_safe_fallback_message_without_issues() -> None:
    error = RuleValidationError({"errors": []})

    assert (
        str(error)
        == "Rule validation failed: <unknown>::<unknown>: invalid rule metadata"
    )
    assert error.root is None


def test_custom_root_executable_rules_exclude_only_unresolved_conflicts(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)

    rules = load_executable_rules(root)
    ids = {rule["id"] for rule in rules}

    assert "ER-MS-REF-TITLECASE-001" not in ids
    assert "ER-MS-REF-AUTHOR-001" not in ids
    assert "ER-MS-REF-PAGERANGE-001" in ids


def test_registry_reads_valid_rule_metadata_and_returns_copies(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)

    rules = registry.rules_for(root)
    conflicts = registry.conflicts_for(root)
    unresolved = registry.unresolved_for(root)

    assert len(rules) == 49
    assert len(conflicts) == 3
    assert len(unresolved) == 13
    rules[0]["id"] = "changed in caller"
    assert registry.rules_for(root)[0]["id"] != "changed in caller"


def test_registry_fast_path_rejects_non_mapping_entries(tmp_path: Path) -> None:
    root = _copy_rule_tree(tmp_path)

    rules = _read_yaml(root, "manuscript.yaml")
    rules["rules"].append(7)
    _write_yaml(root, "manuscript.yaml", rules)
    with pytest.raises(ValueError, match="loaded rule entries must be mappings"):
        registry.rules_for(root, _validated=True)

    conflicts = _read_yaml(root, "conflicts.yaml")
    conflicts["conflicts"] = [7]
    _write_yaml(root, "conflicts.yaml", conflicts)
    with pytest.raises(ValueError, match="conflict entries must be mappings"):
        registry.conflicts_for(root, _validated=True)

    unresolved = _read_yaml(root, "unresolved.yaml")
    unresolved["unknowns"] = [7]
    _write_yaml(root, "unresolved.yaml", unresolved)
    with pytest.raises(ValueError, match="unresolved entries must be mappings"):
        registry.unresolved_for(root, _validated=True)


@pytest.mark.parametrize(
    ("adapter", "filename", "payload", "message"),
    [
        (
            registry.conflicts_for,
            "conflicts.yaml",
            "not a mapping",
            "rule metadata must be a mapping",
        ),
        (
            registry.unresolved_for,
            "unresolved.yaml",
            {"unknowns": "not a list"},
            "entries must be a list",
        ),
    ],
)
def test_registry_adapters_surface_invalid_metadata(
    tmp_path: Path,
    adapter,
    filename: str,
    payload: object,
    message: str,
) -> None:
    root = _copy_rule_tree(tmp_path)
    _write_yaml(root, filename, payload)

    with pytest.raises(ValueError, match=message):
        adapter(root, _validated=True)


def test_registry_unresolved_adapter_supports_legacy_collection_name(
    tmp_path: Path,
) -> None:
    root = _copy_rule_tree(tmp_path)
    _write_yaml(
        root,
        "unresolved.yaml",
        {"version": 1, "unresolved": [{"id": "legacy"}]},
    )

    assert registry.unresolved_for(root, _validated=True) == [{"id": "legacy"}]


def test_registry_conflict_and_effective_rule_adapters_handle_legacy_shapes() -> None:
    conflicts = [
        {"status": "UNRESOLVED", "source_a": {"rule_id": "A"}},
        {"status": "resolved", "source_a": {"rule_id": "B"}},
        {"status": "unresolved_overlap", "source_a": {}, "source_b": None},
    ]

    assert registry.unresolved_conflict_rule_ids(conflicts) == {"A"}
    assert registry.effective_rule_id({"effective_rule": "top-level"}) == "top-level"
    assert (
        registry.effective_rule_id({"execution": {"effective_rule": "legacy"}})
        == "legacy"
    )
    assert registry.effective_rule_id({"effective_rule": ""}) is None
    assert registry.effective_rule_id({"execution": {"effective_rule": 7}}) is None


def test_registry_inspection_adapters_normalize_shape_variants() -> None:
    assert registry.classification_by_id(
        {
            "items": [
                {"source_id": "S1", "role": "primary"},
                {"id": "S2", "role": "secondary"},
                {"id": None},
                "skip",
            ]
        }
    ) == {
        "S1": {"source_id": "S1", "role": "primary"},
        "S2": {"id": "S2", "role": "secondary"},
    }
    assert registry.classification_by_id({"items": "not a list"}) == {}
    assert registry.classification_by_id([]) == {}

    normalized = registry.paragraphs(
        {
            "paragraphs": [
                {"text": "provided", "id": "p-custom", "index": 99},
                "skip",
                {"text": "defaulted"},
            ]
        }
    )
    assert normalized[0]["id"] == "p-custom"
    assert normalized[0]["index"] == 99
    assert normalized[1]["id"] == "p-000002"
    assert registry.paragraphs({"paragraphs": "not a list"}) == []

    assert registry.text_of({"text": "full"}) == "full"
    assert registry.text_of({"text": "", "text_preview": "preview"}) == "preview"
    assert registry.text_of({"text_preview": 7}) == "7"
    assert registry.text_of({}) == ""

    assert registry.runs_of({"runs": [{"text": "run"}, 4]}) == [{"text": "run"}]
    assert registry.runs_of({"runs": "not a list"}) == []


def test_registry_formatting_adapters_flatten_known_envelopes() -> None:
    assert registry.nested_formatting(7) == {}
    assert registry.nested_formatting(
        {"raw": {"font": "宋体"}, "effective": {"size": 12}, "custom": "x"}
    ) == {"font": "宋体", "size": 12, "custom": "x"}

    paragraph = {
        "formatting": {"raw": {"font": "宋体"}},
        "effective_formatting": {"size": 12},
        "effective": {"size": 14},
        "resolved_formatting": {"color": "black"},
    }
    run = {
        "style": {"font": "仿宋"},
        "formatting": {"effective": {"size": 10}},
        "resolved_formatting": {"italic": True},
    }
    paragraph_result = registry.paragraph_formatting(paragraph)
    assert paragraph_result["font"] == "宋体"
    assert paragraph_result["size"] == 14
    assert paragraph_result["color"] == "black"

    run_result = registry.run_formatting(run)
    assert run_result["font"] == "仿宋"
    assert run_result["size"] == 10
    assert run_result["italic"] is True


def test_registry_table_and_collection_adapters_skip_malformed_nodes() -> None:
    inspection = {
        "tables": [
            "skip table",
            {"cells": "skip cells"},
            {
                "cells": [
                    "skip cell",
                    {"paragraphs": "skip paragraphs"},
                    {
                        "paragraphs": [
                            "skip paragraph",
                            {"text": "cell text"},
                            {"id": "existing", "index": 8},
                        ]
                    },
                ]
            },
        ]
    }
    paragraphs = list(registry.iter_table_paragraphs(inspection))

    assert paragraphs[0]["id"] == "t-0002-c-0002-p-0001"
    assert paragraphs[0]["index"] == 1
    assert paragraphs[0]["in_table"] is True
    assert paragraphs[0]["table_index"] == 2
    assert paragraphs[0]["cell_index"] == 2
    assert paragraphs[1]["id"] == "existing"
    assert paragraphs[1]["index"] == 8

    assert list(registry.iter_table_paragraphs({"tables": "not a list"})) == []
    assert registry.collection({"items": [{"x": 1}, "skip"]}, "items") == [{"x": 1}]
    assert registry.collection({"items": "not a list"}, "items") == []

    by_id = {"p-1": {"role": "heading"}, "p-2": {"role": ""}}
    assert registry.role_for({"id": "p-1"}, by_id) == "heading"
    assert registry.role_for({"id": "p-2"}, by_id) == "unknown"
    assert registry.role_for({"id": "missing"}, by_id) == "unknown"
