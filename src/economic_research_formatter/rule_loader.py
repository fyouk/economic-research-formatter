from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

RULE_FILES = (
    "manuscript.yaml",
    "citations.yaml",
    "references.yaml",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at top level: {path}")
    return data


def load_rules(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or project_root()
    all_rules: list[dict[str, Any]] = []
    for filename in RULE_FILES:
        data = load_yaml(root / "rules" / filename)
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError(f"rules must be a list: {filename}")
        all_rules.extend(rules)
    return all_rules


def validate_rules(root: Path | None = None) -> list[str]:
    root = root or project_root()
    schema = load_yaml(root / "rules" / "schema.yaml")["rule_schema"]
    required = schema["required_fields"]
    allowed_domains = set(schema["enums"]["domain"])
    allowed_normativity = set(schema["enums"]["normativity"])
    allowed_source_types = set(schema["enums"]["source_type"])
    allowed_autofix = set(schema["enums"]["autofix"])
    allowed_severity = set(schema["enums"]["lint_severity"])

    errors: list[str] = []
    seen: set[str] = set()

    for rule in load_rules(root):
        rid = rule.get("id", "<missing-id>")
        if rid in seen:
            errors.append(f"{rid}: duplicate id")
        seen.add(rid)

        for field in required:
            if field not in rule:
                errors.append(f"{rid}: missing required field {field}")

        if rule.get("domain") not in allowed_domains:
            errors.append(f"{rid}: invalid domain {rule.get('domain')!r}")
        if rule.get("normativity") not in allowed_normativity:
            errors.append(f"{rid}: invalid normativity {rule.get('normativity')!r}")
        if rule.get("autofix") not in allowed_autofix:
            errors.append(f"{rid}: invalid autofix {rule.get('autofix')!r}")

        source = rule.get("source", {})
        if source.get("source_type") not in allowed_source_types:
            errors.append(f"{rid}: invalid source_type {source.get('source_type')!r}")

        lint = rule.get("lint", {})
        if lint.get("severity") not in allowed_severity:
            errors.append(f"{rid}: invalid lint severity {lint.get('severity')!r}")

    return errors
