"""Load and strictly validate the repository's declarative rule files.

The rule files are the source of truth for format values.  This module only
validates their *shape*, provenance, and cross-file references; it deliberately
does not encode a second set of manuscript-format requirements.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from .models.rules import RuleValidationIssue, RuleValidationResult

RULE_FILES = (
    "manuscript.yaml",
    "citations.yaml",
    "references.yaml",
)
CONFLICT_FILE = "conflicts.yaml"
UNRESOLVED_FILE = "unresolved.yaml"
SOURCE_INDEX_FILE = "source-index.yaml"
SUPPORTED_VERSION = 1

RULE_ID_PATTERN = re.compile(r"^ER-(MS|CIT|REF)-[A-Z0-9-]+-\d{3}$")

# These are schema/record states rather than formatting rules.  They are also
# mirrored in rules/schema.yaml where authors can discover the accepted values.
DEFAULT_CONFLICT_STATUSES = frozenset(
    {
        "unresolved",
        "unresolved_overlap",
        "source_example_vs_explicit_annotation",
        "resolved",
        "resolved_by_effective_rule",
    }
)
DEFAULT_UNRESOLVED_STATUSES = frozenset(
    {
        "not_specified_in_current_sources",
        "example_present_but_red_annotation_not_explicit",
    }
)

RULE_REQUIRED_FIELDS = (
    "id",
    "domain",
    "target",
    "normativity",
    "source",
    "requirement",
    "lint",
    "autofix",
)
RULE_OPTIONAL_FIELDS = frozenset({"rationale"})
RULE_ALLOWED_FIELDS = frozenset(RULE_REQUIRED_FIELDS) | RULE_OPTIONAL_FIELDS
SOURCE_FIELDS = frozenset({"source_id", "source_type", "locator", "evidence"})
LINT_FIELDS = frozenset({"severity"})
CONFLICT_FIELDS = frozenset(
    {
        "id",
        "topic",
        "status",
        "source_a",
        "source_b",
        "analysis",
        "execution",
        "effective_rule",
    }
)
CONFLICT_SOURCE_FIELDS = frozenset({"rule_id", "source_id", "statement"})
CONFLICT_EXECUTION_FIELDS = frozenset({"lint", "autofix", "effective_rule"})
UNRESOLVED_FIELDS = frozenset({"id", "topic", "status", "source_note"})
SOURCE_INDEX_FIELDS = frozenset(
    {"id", "path", "medium", "provenance", "description", "special_handling"}
)
SCHEMA_RULE_SCHEMA_FIELDS = frozenset(
    {"required_fields", "enums", "conventions", "semantics", "statuses"}
)
SCHEMA_ENUM_FIELDS = frozenset(
    {"domain", "normativity", "source_type", "lint_severity", "autofix"}
)
SCHEMA_STATUS_FIELDS = frozenset({"conflict", "unresolved"})
UNRESOLVED_CONFLICT_STATUSES = frozenset({"unresolved", "unresolved_overlap"})


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    """Load one YAML mapping, preserving the historical exception behavior."""

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at top level: {path}")
    return data


def load_rules(root: Path | None = None) -> list[dict[str, Any]]:
    """Return all rule records in file order (backwards-compatible API)."""

    root = Path(root) if root is not None else project_root()
    all_rules: list[dict[str, Any]] = []
    for filename in RULE_FILES:
        data = load_yaml(root / "rules" / filename)
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError(f"rules must be a list: {filename}")
        all_rules.extend(rules)
    return all_rules


class RuleValidationError(ValueError):
    """Raised when rule metadata is unsafe to use for lint execution.

    ``validate_rules_structured`` remains a non-raising inspection API.  The
    execution-facing loaders use this exception to ensure callers cannot
    accidentally turn an invalid rule tree into an apparently successful
    audit by falling back to a partial YAML read.
    """

    def __init__(self, result: dict[str, Any], *, root: Path | None = None) -> None:
        self.result = result
        self.root = root
        errors = result.get("errors", []) if isinstance(result, dict) else []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        location = "::".join(
            str(value)
            for value in (
                first.get("filename", "<unknown>"),
                first.get("field_path", "<unknown>"),
            )
            if value
        )
        detail = first.get("message") or first.get("code") or "invalid rule metadata"
        prefix = "Rule validation failed"
        if root is not None:
            prefix += f" for {root}"
        super().__init__(f"{prefix}: {location}: {detail}")


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _is_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_int_version(value: Any) -> bool:
    # bool is an int subclass, but is not a meaningful schema version.
    return isinstance(value, int) and not isinstance(value, bool)


class _ValidationContext:
    """Collect issues while retaining enough cross-file state for references."""

    def __init__(self, root: Path):
        self.root = root
        self.issues: list[RuleValidationIssue] = []
        self.rules: list[dict[str, Any]] = []
        self.rule_ids: set[str] = set()
        self.source_ids: set[str] = set()
        self.conflicts: list[dict[str, Any]] = []
        self.unresolved: list[dict[str, Any]] = []
        self.rule_count = 0
        self.conflict_count = 0
        self.unresolved_count = 0

    def add(
        self,
        *,
        code: str,
        message: str,
        filename: str,
        field_path: str,
        rule_id: str | None = None,
    ) -> None:
        self.issues.append(
            RuleValidationIssue(
                code=code,
                message=message,
                filename=filename,
                field_path=field_path,
                rule_id=rule_id,
            )
        )

    def file_path(self, filename: str) -> Path:
        if filename == SOURCE_INDEX_FILE:
            return self.root / "sources" / "normalized" / filename
        return self.root / "rules" / filename


def _read_yaml(
    context: _ValidationContext, filename: str
) -> dict[str, Any] | None:
    path = context.file_path(filename)
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except FileNotFoundError:
        context.add(
            code="file_missing",
            message="required YAML file does not exist",
            filename=filename,
            field_path="<file>",
        )
        return None
    except (OSError, yaml.YAMLError, UnicodeError) as exc:
        context.add(
            code="yaml_error",
            message=f"cannot parse YAML: {exc}",
            filename=filename,
            field_path="<file>",
        )
        return None
    if not _is_mapping(value):
        context.add(
            code="invalid_type",
            message="top-level value must be a mapping",
            filename=filename,
            field_path="<root>",
        )
        return None
    return value


def _check_unknown_fields(
    context: _ValidationContext,
    mapping: Any,
    allowed: Iterable[str],
    *,
    filename: str,
    prefix: str,
    rule_id: str | None = None,
) -> None:
    if not _is_mapping(mapping):
        return
    allowed_set = set(allowed)
    for field in mapping:
        if field not in allowed_set:
            context.add(
                code="unknown_field",
                message=f"unknown field {field!r}",
                filename=filename,
                field_path=f"{prefix}.{field}" if prefix else str(field),
                rule_id=rule_id,
            )


def _check_version_and_collection(
    context: _ValidationContext,
    data: dict[str, Any],
    *,
    filename: str,
    collection_name: str,
) -> list[Any]:
    """Validate the common ``version`` + list top-level shape."""

    _check_unknown_fields(
        context,
        data,
        {"version", collection_name},
        filename=filename,
        prefix="",
    )
    if "version" not in data:
        context.add(
            code="missing_field",
            message="required field is missing",
            filename=filename,
            field_path="version",
        )
    elif not _is_int_version(data["version"]):
        context.add(
            code="invalid_type",
            message="version must be an integer",
            filename=filename,
            field_path="version",
        )
    elif data["version"] != SUPPORTED_VERSION:
        context.add(
            code="unsupported_version",
            message=f"version must be {SUPPORTED_VERSION}",
            filename=filename,
            field_path="version",
        )
    values = data.get(collection_name)
    if collection_name not in data:
        context.add(
            code="missing_field",
            message="required collection is missing",
            filename=filename,
            field_path=collection_name,
        )
        return []
    if not isinstance(values, list):
        context.add(
            code="invalid_type",
            message=f"{collection_name} must be a list",
            filename=filename,
            field_path=collection_name,
        )
        return []
    return values


def _schema_enums(schema: dict[str, Any] | None) -> dict[str, set[str]]:
    """Read enum declarations from schema.yaml, with safe structural defaults."""

    defaults = {
        "domain": {"manuscript", "citation", "reference"},
        "normativity": {"mandatory", "recommended", "example_only"},
        "source_type": {
            "red_annotation",
            "explicit_instruction",
            "operational_summary",
            "example",
        },
        "lint_severity": {"error", "warning", "info", "manual_review"},
        "autofix": {"safe", "conditional", "never"},
    }
    if not schema or not _is_mapping(schema.get("rule_schema")):
        return defaults
    declared = schema["rule_schema"].get("enums")
    if not _is_mapping(declared):
        return defaults
    result: dict[str, set[str]] = {}
    for key, fallback in defaults.items():
        value = declared.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            result[key] = set(value)
        else:
            result[key] = set(fallback)
    return result


def _validate_schema_definition(
    context: _ValidationContext, schema: dict[str, Any] | None
) -> tuple[str, ...]:
    """Validate schema metadata before using it to validate rule records."""

    if schema is None or not _is_mapping(schema.get("rule_schema")):
        return RULE_REQUIRED_FIELDS
    filename = "schema.yaml"
    definition = schema["rule_schema"]
    _check_unknown_fields(
        context,
        definition,
        SCHEMA_RULE_SCHEMA_FIELDS,
        filename=filename,
        prefix="rule_schema",
    )

    required_fields = definition.get("required_fields")
    validated_required_fields: list[str] = []
    if not isinstance(required_fields, list):
        context.add(
            code="invalid_type",
            message="required_fields must be a list",
            filename=filename,
            field_path="rule_schema.required_fields",
        )
    else:
        if not required_fields:
            context.add(
                code="empty_value",
                message="required_fields must not be empty",
                filename=filename,
                field_path="rule_schema.required_fields",
            )
        seen_required_fields: set[str] = set()
        for index, value in enumerate(required_fields):
            if not isinstance(value, str) or not value.strip():
                context.add(
                    code="invalid_type" if not isinstance(value, str) else "empty_value",
                    message="required field names must be non-empty strings",
                    filename=filename,
                    field_path=f"rule_schema.required_fields[{index}]",
                )
                continue
            if value in seen_required_fields:
                context.add(
                    code="duplicate_required_field",
                    message=f"required field {value!r} is listed more than once",
                    filename=filename,
                    field_path=f"rule_schema.required_fields[{index}]",
                )
                continue
            seen_required_fields.add(value)
            if value not in RULE_ALLOWED_FIELDS:
                context.add(
                    code="unknown_required_field",
                    message=f"required field {value!r} is not a supported rule field",
                    filename=filename,
                    field_path=f"rule_schema.required_fields[{index}]",
                )
                continue
            validated_required_fields.append(value)

    enums = definition.get("enums")
    if not isinstance(enums, dict):
        context.add(
            code="invalid_type",
            message="enums must be a mapping",
            filename=filename,
            field_path="rule_schema.enums",
        )
    else:
        _check_unknown_fields(
            context,
            enums,
            SCHEMA_ENUM_FIELDS,
            filename=filename,
            prefix="rule_schema.enums",
        )
        for name in sorted(SCHEMA_ENUM_FIELDS):
            values = enums.get(name)
            if not isinstance(values, list):
                context.add(
                    code="invalid_type",
                    message=f"enum {name} must be a list",
                    filename=filename,
                    field_path=f"rule_schema.enums.{name}",
                )
            elif not values:
                context.add(
                    code="empty_value",
                    message=f"enum {name} must not be empty",
                    filename=filename,
                    field_path=f"rule_schema.enums.{name}",
                )
            else:
                for index, value in enumerate(values):
                    if not isinstance(value, str) or not value.strip():
                        context.add(
                            code="invalid_type" if not isinstance(value, str) else "empty_value",
                            message=f"enum {name} values must be non-empty strings",
                            filename=filename,
                            field_path=f"rule_schema.enums.{name}[{index}]",
                        )

    statuses = definition.get("statuses")
    if not isinstance(statuses, dict):
        context.add(
            code="invalid_type",
            message="statuses must be a mapping",
            filename=filename,
            field_path="rule_schema.statuses",
        )
    else:
        _check_unknown_fields(
            context,
            statuses,
            SCHEMA_STATUS_FIELDS,
            filename=filename,
            prefix="rule_schema.statuses",
        )
        for name in sorted(SCHEMA_STATUS_FIELDS):
            values = statuses.get(name)
            if not isinstance(values, list):
                context.add(
                    code="invalid_type",
                    message=f"status enum {name} must be a list",
                    filename=filename,
                    field_path=f"rule_schema.statuses.{name}",
                )
            elif not values:
                context.add(
                    code="empty_value",
                    message=f"status enum {name} must not be empty",
                    filename=filename,
                    field_path=f"rule_schema.statuses.{name}",
                )
            else:
                for index, value in enumerate(values):
                    if not isinstance(value, str) or not value.strip():
                        context.add(
                            code="invalid_type" if not isinstance(value, str) else "empty_value",
                            message=f"status enum {name} values must be non-empty strings",
                            filename=filename,
                            field_path=f"rule_schema.statuses.{name}[{index}]",
                        )

    # Keep the built-in contract available when the schema itself is malformed;
    # the collected schema issues still make validation fail, while the rest of
    # the file receives useful diagnostics instead of being silently skipped.
    return tuple(validated_required_fields) or RULE_REQUIRED_FIELDS


def _schema_statuses(schema: dict[str, Any] | None) -> tuple[set[str], set[str]]:
    conflict = set(DEFAULT_CONFLICT_STATUSES)
    unresolved = set(DEFAULT_UNRESOLVED_STATUSES)
    if not schema or not _is_mapping(schema.get("rule_schema")):
        return conflict, unresolved
    values = schema["rule_schema"].get("statuses")
    if not _is_mapping(values):
        return conflict, unresolved
    configured_conflict = values.get("conflict")
    configured_unresolved = values.get("unresolved")
    if isinstance(configured_conflict, list) and all(
        isinstance(item, str) for item in configured_conflict
    ):
        conflict = set(configured_conflict)
    if isinstance(configured_unresolved, list) and all(
        isinstance(item, str) for item in configured_unresolved
    ):
        unresolved = set(configured_unresolved)
    return conflict, unresolved


def _validate_source_index(context: _ValidationContext) -> None:
    filename = SOURCE_INDEX_FILE
    data = _read_yaml(context, filename)
    if data is None:
        return
    values = _check_version_and_collection(
        context, data, filename=filename, collection_name="sources"
    )
    seen: set[str] = set()
    for index, item in enumerate(values):
        prefix = f"sources[{index}]"
        if not _is_mapping(item):
            context.add(
                code="invalid_type",
                message="source entry must be a mapping",
                filename=filename,
                field_path=prefix,
            )
            continue
        _check_unknown_fields(
            context, item, SOURCE_INDEX_FIELDS, filename=filename, prefix=prefix
        )
        source_id = item.get("id")
        if not _is_nonempty_text(source_id):
            context.add(
                code="empty_value" if isinstance(source_id, str) else "invalid_type",
                message="source id must be a non-empty string",
                filename=filename,
                field_path=f"{prefix}.id",
            )
        elif source_id in seen:
            context.add(
                code="duplicate_source_id",
                message=f"duplicate source id {source_id!r}",
                filename=filename,
                field_path=f"{prefix}.id",
            )
        else:
            seen.add(source_id)
        path = item.get("path")
        if not _is_nonempty_text(path):
            context.add(
                code="empty_value" if isinstance(path, str) else "invalid_type",
                message="source path must be a non-empty string",
                filename=filename,
                field_path=f"{prefix}.path",
            )
    context.source_ids = seen


def _validate_rule(
    context: _ValidationContext,
    item: Any,
    *,
    filename: str,
    index: int,
    enums: dict[str, set[str]],
    required_fields: tuple[str, ...],
) -> None:
    prefix = f"rules[{index}]"
    if not _is_mapping(item):
        context.add(
            code="invalid_type",
            message="rule entry must be a mapping",
            filename=filename,
            field_path=prefix,
            rule_id="<missing-id>",
        )
        return

    raw_rule_id = item.get("id")
    rule_id = raw_rule_id if isinstance(raw_rule_id, str) else "<missing-id>"
    _check_unknown_fields(
        context,
        item,
        RULE_ALLOWED_FIELDS,
        filename=filename,
        prefix=prefix,
        rule_id=rule_id,
    )

    for field in required_fields:
        if field not in item:
            context.add(
                code="missing_field",
                message="required field is missing",
                filename=filename,
                field_path=f"{prefix}.{field}",
                rule_id=rule_id,
            )

    if not isinstance(raw_rule_id, str):
        context.add(
            code="invalid_type",
            message="id must be a string",
            filename=filename,
            field_path=f"{prefix}.id",
            rule_id=rule_id,
        )
    elif not RULE_ID_PATTERN.fullmatch(raw_rule_id):
        context.add(
            code="invalid_id",
            message="id does not match the rule id pattern",
            filename=filename,
            field_path=f"{prefix}.id",
            rule_id=raw_rule_id,
        )
    if isinstance(raw_rule_id, str):
        if raw_rule_id in context.rule_ids:
            context.add(
                code="duplicate_id",
                message=f"duplicate rule id {raw_rule_id!r}",
                filename=filename,
                field_path=f"{prefix}.id",
                rule_id=raw_rule_id,
            )
        context.rule_ids.add(raw_rule_id)

    _validate_enum(
        context,
        item,
        key="domain",
        allowed=enums["domain"],
        filename=filename,
        field_path=f"{prefix}.domain",
        rule_id=rule_id,
    )
    _validate_enum(
        context,
        item,
        key="normativity",
        allowed=enums["normativity"],
        filename=filename,
        field_path=f"{prefix}.normativity",
        rule_id=rule_id,
    )
    _validate_enum(
        context,
        item,
        key="autofix",
        allowed=enums["autofix"],
        filename=filename,
        field_path=f"{prefix}.autofix",
        rule_id=rule_id,
    )

    target = item.get("target")
    if not isinstance(target, str):
        context.add(
            code="invalid_type",
            message="target must be a string",
            filename=filename,
            field_path=f"{prefix}.target",
            rule_id=rule_id,
        )
    elif not target.strip():
        context.add(
            code="empty_value",
            message="target must be non-empty",
            filename=filename,
            field_path=f"{prefix}.target",
            rule_id=rule_id,
        )

    source = item.get("source")
    if not _is_mapping(source):
        context.add(
            code="invalid_type",
            message="source must be a mapping",
            filename=filename,
            field_path=f"{prefix}.source",
            rule_id=rule_id,
        )
    else:
        _check_unknown_fields(
            context,
            source,
            SOURCE_FIELDS,
            filename=filename,
            prefix=f"{prefix}.source",
            rule_id=rule_id,
        )
        for field in ("source_id", "source_type", "locator", "evidence"):
            path = f"{prefix}.source.{field}"
            if field not in source:
                context.add(
                    code="missing_field",
                    message="required source field is missing",
                    filename=filename,
                    field_path=path,
                    rule_id=rule_id,
                )
                continue
            value = source[field]
            if field == "source_type":
                _validate_enum(
                    context,
                    source,
                    key=field,
                    allowed=enums["source_type"],
                    filename=filename,
                    field_path=path,
                    rule_id=rule_id,
                )
            elif not isinstance(value, str):
                context.add(
                    code="invalid_type",
                    message=f"{field} must be a string",
                    filename=filename,
                    field_path=path,
                    rule_id=rule_id,
                )
            elif not value.strip():
                context.add(
                    code="empty_value",
                    message=f"{field} must be non-empty",
                    filename=filename,
                    field_path=path,
                    rule_id=rule_id,
                )
        source_id = source.get("source_id")
        if isinstance(source_id, str) and source_id.strip():
            if source_id not in context.source_ids:
                context.add(
                    code="unknown_source_id",
                    message=f"source id {source_id!r} is not in source-index.yaml",
                    filename=filename,
                    field_path=f"{prefix}.source.source_id",
                    rule_id=rule_id,
                )

    requirement = item.get("requirement")
    if not _is_mapping(requirement):
        context.add(
            code="invalid_type",
            message="requirement must be a mapping",
            filename=filename,
            field_path=f"{prefix}.requirement",
            rule_id=rule_id,
        )
    elif not requirement:
        context.add(
            code="empty_value",
            message="requirement must be a non-empty mapping",
            filename=filename,
            field_path=f"{prefix}.requirement",
            rule_id=rule_id,
        )

    lint = item.get("lint")
    if not _is_mapping(lint):
        context.add(
            code="invalid_type",
            message="lint must be a mapping",
            filename=filename,
            field_path=f"{prefix}.lint",
            rule_id=rule_id,
        )
    else:
        _check_unknown_fields(
            context,
            lint,
            LINT_FIELDS,
            filename=filename,
            prefix=f"{prefix}.lint",
            rule_id=rule_id,
        )
        if "severity" not in lint:
            context.add(
                code="missing_field",
                message="lint severity is required",
                filename=filename,
                field_path=f"{prefix}.lint.severity",
                rule_id=rule_id,
            )
        else:
            _validate_enum(
                context,
                lint,
                key="severity",
                allowed=enums["lint_severity"],
                filename=filename,
                field_path=f"{prefix}.lint.severity",
                rule_id=rule_id,
            )

    rationale = item.get("rationale")
    if "rationale" in item and not _is_nonempty_text(rationale):
        context.add(
            code="empty_value" if isinstance(rationale, str) else "invalid_type",
            message="rationale must be a non-empty string",
            filename=filename,
            field_path=f"{prefix}.rationale",
            rule_id=rule_id,
        )

    normativity = item.get("normativity")
    severity = lint.get("severity") if _is_mapping(lint) else None
    autofix = item.get("autofix")
    if normativity == "recommended" and severity == "error" and not _is_nonempty_text(
        rationale
    ):
        context.add(
            code="missing_rationale",
            message="recommended rule with error severity needs explicit rationale",
            filename=filename,
            field_path=f"{prefix}.rationale",
            rule_id=rule_id,
        )
    if normativity == "example_only":
        if severity == "error":
            context.add(
                code="example_only_error",
                message="example_only rule cannot silently use error severity",
                filename=filename,
                field_path=f"{prefix}.lint.severity",
                rule_id=rule_id,
            )
        if isinstance(autofix, str) and autofix in {"safe", "conditional"}:
            context.add(
                code="example_only_autofix",
                message="example_only rule cannot be automatically fixed",
                filename=filename,
                field_path=f"{prefix}.autofix",
                rule_id=rule_id,
            )
    if (
        severity == "manual_review"
        and isinstance(autofix, str)
        and autofix in {"safe", "conditional"}
    ):
        context.add(
            code="manual_review_autofix",
            message="manual_review rules must use autofix=never",
            filename=filename,
            field_path=f"{prefix}.autofix",
            rule_id=rule_id,
        )


def _validate_enum(
    context: _ValidationContext,
    mapping: dict[str, Any],
    *,
    key: str,
    allowed: set[str],
    filename: str,
    field_path: str,
    rule_id: str | None,
) -> None:
    if key not in mapping:
        return
    value = mapping[key]
    if not isinstance(value, str):
        context.add(
            code="invalid_type",
            message=f"{key} must be a string",
            filename=filename,
            field_path=field_path,
            rule_id=rule_id,
        )
    elif value not in allowed:
        context.add(
            code="invalid_enum",
            message=f"{key} must be one of {sorted(allowed)!r}",
            filename=filename,
            field_path=field_path,
            rule_id=rule_id,
        )


def _validate_rule_files(
    context: _ValidationContext,
    schema: dict[str, Any] | None,
    required_fields: tuple[str, ...],
) -> None:
    enums = _schema_enums(schema)
    for filename in RULE_FILES:
        data = _read_yaml(context, filename)
        if data is None:
            continue
        values = _check_version_and_collection(
            context, data, filename=filename, collection_name="rules"
        )
        context.rule_count += len(values)
        for index, item in enumerate(values):
            _validate_rule(
                context,
                item,
                filename=filename,
                index=index,
                enums=enums,
                required_fields=required_fields,
            )
            if isinstance(item, dict):
                context.rules.append(item)


def _validate_conflicts(
    context: _ValidationContext,
    *,
    conflict_statuses: set[str],
    enums: dict[str, set[str]],
) -> None:
    filename = CONFLICT_FILE
    data = _read_yaml(context, filename)
    if data is None:
        return
    values = _check_version_and_collection(
        context, data, filename=filename, collection_name="conflicts"
    )
    context.conflict_count = len(values)
    seen: set[str] = set()
    for index, item in enumerate(values):
        prefix = f"conflicts[{index}]"
        if not _is_mapping(item):
            context.add(
                code="invalid_type",
                message="conflict entry must be a mapping",
                filename=filename,
                field_path=prefix,
            )
            continue
        context.conflicts.append(item)
        _check_unknown_fields(
            context, item, CONFLICT_FIELDS, filename=filename, prefix=prefix
        )
        conflict_id = item.get("id")
        if not _is_nonempty_text(conflict_id):
            context.add(
                code="empty_value" if isinstance(conflict_id, str) else "invalid_type",
                message="conflict id must be a non-empty string",
                filename=filename,
                field_path=f"{prefix}.id",
            )
        elif conflict_id in seen:
            context.add(
                code="duplicate_conflict_id",
                message=f"duplicate conflict id {conflict_id!r}",
                filename=filename,
                field_path=f"{prefix}.id",
            )
        else:
            seen.add(conflict_id)

        status = item.get("status")
        is_unresolved_status = (
            isinstance(status, str)
            and status.casefold() in UNRESOLVED_CONFLICT_STATUSES
        )
        if not isinstance(status, str):
            context.add(
                code="invalid_type",
                message="conflict status must be a string",
                filename=filename,
                field_path=f"{prefix}.status",
            )
        elif status not in conflict_statuses:
            context.add(
                code="invalid_conflict_status",
                message=f"status must be one of {sorted(conflict_statuses)!r}",
                filename=filename,
                field_path=f"{prefix}.status",
            )

        for side in ("source_a", "source_b"):
            source_ref = item.get(side)
            side_path = f"{prefix}.{side}"
            if not _is_mapping(source_ref):
                context.add(
                    code="invalid_type",
                    message=f"{side} must be a mapping",
                    filename=filename,
                    field_path=side_path,
                )
                continue
            _check_unknown_fields(
                context,
                source_ref,
                CONFLICT_SOURCE_FIELDS,
                filename=filename,
                prefix=side_path,
            )
            if "rule_id" in source_ref:
                ref = source_ref["rule_id"]
                if not isinstance(ref, str):
                    context.add(
                        code="invalid_type",
                        message="rule_id reference must be a string",
                        filename=filename,
                        field_path=f"{side_path}.rule_id",
                    )
                elif ref not in context.rule_ids:
                    context.add(
                        code="unknown_rule_reference",
                        message=f"rule id reference {ref!r} does not exist",
                        filename=filename,
                        field_path=f"{side_path}.rule_id",
                    )
            if "source_id" in source_ref:
                ref = source_ref["source_id"]
                if not isinstance(ref, str):
                    context.add(
                        code="invalid_type",
                        message="source_id reference must be a string",
                        filename=filename,
                        field_path=f"{side_path}.source_id",
                    )
                elif ref not in context.source_ids:
                    context.add(
                        code="unknown_source_id",
                        message=f"source id reference {ref!r} does not exist",
                        filename=filename,
                        field_path=f"{side_path}.source_id",
                    )

        execution = item.get("execution")
        execution_path = f"{prefix}.execution"
        if execution is not None:
            if not _is_mapping(execution):
                context.add(
                    code="invalid_type",
                    message="execution must be a mapping",
                    filename=filename,
                    field_path=execution_path,
                )
                if is_unresolved_status:
                    context.add(
                        code="unresolved_conflict_executable",
                        message=(
                            "unresolved conflicts must declare manual_review "
                            "execution with autofix=never"
                        ),
                        filename=filename,
                        field_path=execution_path,
                    )
            else:
                _check_unknown_fields(
                    context,
                    execution,
                    CONFLICT_EXECUTION_FIELDS,
                    filename=filename,
                    prefix=execution_path,
                )
                lint = execution.get("lint")
                if lint is not None:
                    if not isinstance(lint, str):
                        context.add(
                            code="invalid_type",
                            message="execution.lint must be a string",
                            filename=filename,
                            field_path=f"{execution_path}.lint",
                        )
                    elif lint not in enums["lint_severity"]:
                        context.add(
                            code="invalid_enum",
                            message="execution.lint is not a valid severity",
                            filename=filename,
                            field_path=f"{execution_path}.lint",
                        )
                autofix = execution.get("autofix")
                if autofix is not None:
                    if not isinstance(autofix, str):
                        context.add(
                            code="invalid_type",
                            message="execution.autofix must be a string",
                            filename=filename,
                            field_path=f"{execution_path}.autofix",
                        )
                    elif autofix not in enums["autofix"]:
                        context.add(
                            code="invalid_enum",
                            message="execution.autofix is not a valid autofix mode",
                            filename=filename,
                            field_path=f"{execution_path}.autofix",
                        )
                if is_unresolved_status:
                    if lint != "manual_review" or autofix != "never":
                        context.add(
                            code="unresolved_conflict_executable",
                            message=(
                                "unresolved conflicts must be manual_review with "
                                "autofix=never"
                            ),
                            filename=filename,
                            field_path=execution_path,
                        )
        elif is_unresolved_status:
            context.add(
                code="unresolved_conflict_executable",
                message=(
                    "unresolved conflicts must declare manual_review execution "
                    "with autofix=never"
                ),
                filename=filename,
                field_path=execution_path,
            )

        effective_rule_values: list[tuple[str, Any]] = []
        if "effective_rule" in item:
            effective_rule_values.append((f"{prefix}.effective_rule", item["effective_rule"]))
        if isinstance(execution, dict) and "effective_rule" in execution:
            effective_rule_values.append(
                (f"{execution_path}.effective_rule", execution["effective_rule"])
            )
        if len(effective_rule_values) > 1 and any(
            value != effective_rule_values[0][1]
            for _, value in effective_rule_values[1:]
        ):
            context.add(
                code="conflicting_effective_rule",
                message="effective_rule declarations must identify the same rule",
                filename=filename,
                field_path=effective_rule_values[1][0],
            )
        for effective_path, effective_rule in effective_rule_values:
            if not isinstance(effective_rule, str):
                context.add(
                    code="invalid_type",
                    message="effective_rule must be a string",
                    filename=filename,
                    field_path=effective_path,
                )
            elif effective_rule not in context.rule_ids:
                context.add(
                    code="unknown_effective_rule",
                    message=f"effective rule {effective_rule!r} does not exist",
                    filename=filename,
                    field_path=effective_path,
                )
            elif is_unresolved_status:
                context.add(
                    code="unresolved_conflict_executable",
                    message="unresolved conflicts cannot declare an effective rule",
                    filename=filename,
                    field_path=effective_path,
                )


def _validate_unresolved(
    context: _ValidationContext,
    *,
    unresolved_statuses: set[str],
) -> None:
    filename = UNRESOLVED_FILE
    data = _read_yaml(context, filename)
    if data is None:
        return
    values = _check_version_and_collection(
        context, data, filename=filename, collection_name="unknowns"
    )
    context.unresolved_count = len(values)
    seen: set[str] = set()
    for index, item in enumerate(values):
        prefix = f"unknowns[{index}]"
        if not _is_mapping(item):
            context.add(
                code="invalid_type",
                message="unresolved entry must be a mapping",
                filename=filename,
                field_path=prefix,
            )
            continue
        context.unresolved.append(item)
        _check_unknown_fields(
            context, item, UNRESOLVED_FIELDS, filename=filename, prefix=prefix
        )
        unresolved_id = item.get("id")
        if not _is_nonempty_text(unresolved_id):
            context.add(
                code="empty_value" if isinstance(unresolved_id, str) else "invalid_type",
                message="unresolved id must be a non-empty string",
                filename=filename,
                field_path=f"{prefix}.id",
            )
        elif unresolved_id in seen:
            context.add(
                code="duplicate_unresolved_id",
                message=f"duplicate unresolved id {unresolved_id!r}",
                filename=filename,
                field_path=f"{prefix}.id",
            )
        else:
            seen.add(unresolved_id)
        topic = item.get("topic")
        if not _is_nonempty_text(topic):
            context.add(
                code="empty_value" if isinstance(topic, str) else "invalid_type",
                message="topic must be a non-empty string",
                filename=filename,
                field_path=f"{prefix}.topic",
            )
        status = item.get("status")
        if not isinstance(status, str):
            context.add(
                code="invalid_type",
                message="unresolved status must be a string",
                filename=filename,
                field_path=f"{prefix}.status",
            )
        elif status not in unresolved_statuses:
            context.add(
                code="invalid_unresolved_status",
                message=f"status must be one of {sorted(unresolved_statuses)!r}",
                filename=filename,
                field_path=f"{prefix}.status",
            )


def validate_rules_structured(root: Path | None = None) -> dict[str, Any]:
    """Validate all rule metadata and return a JSON-serializable result.

    The returned mapping has an ``errors`` list containing records with
    ``filename``, ``rule_id`` (when applicable), ``field_path``, ``code``, and
    ``message``.  All files are inspected before returning, so callers can show
    the complete correction set in one pass.
    """

    root = Path(root) if root is not None else project_root()
    context = _ValidationContext(root)
    required_fields = RULE_REQUIRED_FIELDS
    schema = _read_yaml(context, "schema.yaml")
    if schema is not None:
        _check_unknown_fields(
            context,
            schema,
            {"version", "rule_schema"},
            filename="schema.yaml",
            prefix="",
        )
        if "version" not in schema:
            context.add(
                code="missing_field",
                message="required field is missing",
                filename="schema.yaml",
                field_path="version",
            )
        elif not _is_int_version(schema["version"]):
            context.add(
                code="invalid_type",
                message="version must be an integer",
                filename="schema.yaml",
                field_path="version",
            )
        elif schema["version"] != SUPPORTED_VERSION:
            context.add(
                code="unsupported_version",
                message=f"version must be {SUPPORTED_VERSION}",
                filename="schema.yaml",
                field_path="version",
            )
        if not _is_mapping(schema.get("rule_schema")):
            context.add(
                code="invalid_type",
                message="rule_schema must be a mapping",
                filename="schema.yaml",
                field_path="rule_schema",
            )
        required_fields = _validate_schema_definition(context, schema)

    _validate_source_index(context)
    enums = _schema_enums(schema)
    _validate_rule_files(context, schema, required_fields)
    conflict_statuses, unresolved_statuses = _schema_statuses(schema)
    _validate_conflicts(
        context,
        conflict_statuses=conflict_statuses,
        enums=enums,
    )
    _validate_unresolved(context, unresolved_statuses=unresolved_statuses)

    result = RuleValidationResult(
        valid=not context.issues,
        errors=context.issues,
        rule_count=context.rule_count,
        conflict_count=context.conflict_count,
        unresolved_count=context.unresolved_count,
        source_count=len(context.source_ids),
    )
    return result.to_dict()


# Descriptive aliases make the structured API discoverable while retaining one
# implementation and one output contract.
validate_rules_detailed = validate_rules_structured
validate_rules_report = validate_rules_structured
validate_rules_json = validate_rules_structured


def ensure_rules_valid(root: Path | None = None) -> dict[str, Any]:
    """Validate a rule tree or raise before any execution-facing read.

    The structured validator is deliberately useful for diagnostics and thus
    does not raise.  Lint execution must use this gate so an invalid custom
    rule root cannot be reduced to a partial, seemingly valid rule list.
    """

    resolved = Path(root) if root is not None else project_root()
    result = validate_rules_structured(resolved)
    if not result.get("valid", False):
        raise RuleValidationError(result, root=resolved)
    return result


def validate_rules(root: Path | None = None) -> list[str]:
    """Return legacy one-line errors while using strict aggregate validation."""

    result = validate_rules_structured(root)
    return [
        RuleValidationIssue(
            code=issue["code"],
            message=issue["message"],
            filename=issue["filename"],
            field_path=issue["field_path"],
            rule_id=issue.get("rule_id"),
        ).legacy_message()
        for issue in result["errors"]
    ]


def load_executable_rules(root: Path | None = None) -> list[dict[str, Any]]:
    """Return rules safe for deterministic execution under conflict metadata.

    This helper is intentionally conservative: if strict validation fails, a
    :class:`RuleValidationError` is raised before any executable list is
    returned.  Rules referenced by an unresolved conflict are also excluded.
    ``load_rules`` remains unchanged for callers that need the raw declarative
    records.
    """

    root = Path(root) if root is not None else project_root()
    ensure_rules_valid(root)
    all_rules = load_rules(root)
    conflict_data = load_yaml(root / "rules" / CONFLICT_FILE)
    unresolved_ids = _unresolved_conflict_rule_ids(conflict_data.get("conflicts", []))
    return [rule for rule in all_rules if rule.get("id") not in unresolved_ids]


def _unresolved_conflict_rule_ids(values: Iterable[Any]) -> set[str]:
    """Return source rule IDs blocked by validated unresolved conflicts."""

    result: set[str] = set()
    for conflict in values:
        if not isinstance(conflict, dict):
            continue
        if str(conflict.get("status", "")).casefold() not in UNRESOLVED_CONFLICT_STATUSES:
            continue
        for side in ("source_a", "source_b"):
            source = conflict.get(side)
            if not isinstance(source, dict):
                continue
            rule_id = source.get("rule_id")
            if isinstance(rule_id, str) and rule_id:
                result.add(rule_id)
    return result
