"""Orchestration for the read-only manuscript/citation/reference linter."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from economic_research_formatter.classify.classifier import classify_inspection
from economic_research_formatter.models.audit import AUDIT_SCHEMA_VERSION, build_summary, make_finding, target_for_document
from economic_research_formatter.rule_loader import ensure_rules_valid

from .citations import lint_citation_rule
from .common import RuleContext, finding
from .manuscript import lint_manuscript_rule
from .references import lint_reference_rule
from .registry import (
    conflicts_for,
    effective_rule_id,
    rules_for,
    unresolved_conflict_rule_ids,
    unresolved_for,
)


_DOMAIN_HANDLERS = {
    "manuscript": lint_manuscript_rule,
    "citation": lint_citation_rule,
    "reference": lint_reference_rule,
}


def _safe_input(inspection: Mapping[str, Any]) -> dict[str, Any]:
    value = inspection.get("input", {})
    if not isinstance(value, Mapping):
        return {}
    # Inspector input intentionally contains only non-sensitive identity
    # metadata.  In particular, do not copy a source path into an audit.
    allowed = ("filename", "sha256", "size_bytes", "reported_page_count")
    return {key: value[key] for key in allowed if key in value}


def _not_checked(
    rule: Mapping[str, Any],
    ctx: RuleContext,
    message: str,
    *,
    observed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return finding(
        rule,
        "NOT_CHECKED",
        message,
        target=ctx.document_target,
        observed=observed,
        confidence=0.0,
    )


def _blocked_rule_finding(
    rule: Mapping[str, Any],
    ctx: RuleContext,
    conflict_ids: set[str],
) -> dict[str, Any]:
    ordered_conflicts = sorted(conflict_ids)
    return _not_checked(
        rule,
        ctx,
        "规则被未裁决冲突引用，当前不执行自动检查。",
        observed={"conflict_ids": ordered_conflicts, "execution": "blocked"},
    )


def _conflict_finding(conflict: Mapping[str, Any], ctx: RuleContext) -> dict[str, Any]:
    conflict_id = str(conflict.get("id", "<unknown-conflict>"))
    source_a = conflict.get("source_a", {})
    source_b = conflict.get("source_b", {})
    source_a = source_a if isinstance(source_a, Mapping) else {}
    source_b = source_b if isinstance(source_b, Mapping) else {}
    source = {
        "source_id": source_a.get("rule_id") or source_a.get("source_id") or "rules/conflicts.yaml",
        "locator": conflict.get("topic", "conflict"),
        "evidence": conflict.get("analysis", "来源存在未裁决冲突。"),
        "conflict_source_b": source_b.get("rule_id") or source_b.get("source_id"),
    }
    target = target_for_document(ctx.inspection)
    return make_finding(
        conflict_id,
        "MANUAL_REVIEW",
        "来源冲突尚未裁决，当前不自动执行确定性结论。",
        target=target,
        observed={"topic": conflict.get("topic"), "status": conflict.get("status")},
        expected={"execution": "manual_review"},
        source=source,
        confidence=0.50,
    )


def _conflict_info(conflict: Mapping[str, Any], ctx: RuleContext) -> dict[str, Any]:
    effective_rule = effective_rule_id(conflict)
    return make_finding(
        str(conflict.get("id", "ER-CONFLICT-003")),
        "INFO",
        "保留来源示例差异；运行时按 effective_rule 执行。",
        target=target_for_document(ctx.inspection),
        observed={"example_difference": "外文文献示例可能出现普通连字符"},
        expected={"effective_rule": effective_rule},
        source={
            "source_id": conflict.get("source_b", {}).get("source_id", "rules/conflicts.yaml") if isinstance(conflict.get("source_b"), Mapping) else "rules/conflicts.yaml",
            "locator": conflict.get("topic", "conflict"),
            "evidence": conflict.get("analysis", "示例与明确规则存在差异。"),
        },
        confidence=1.0,
    )


def _unresolved_finding(unresolved: Mapping[str, Any], ctx: RuleContext) -> dict[str, Any]:
    unknown_id = str(unresolved.get("id", "<unknown-unresolved>"))
    return make_finding(
        unknown_id,
        "NOT_CHECKED",
        "当前来源未规定该项目，未纳入自动检查。",
        target=target_for_document(ctx.inspection),
        observed={"topic": unresolved.get("topic"), "status": unresolved.get("status")},
        expected={"status": "NOT_CHECKED"},
        source={
            "source_id": "rules/unresolved.yaml",
            "locator": str(unresolved.get("topic", "unresolved")),
            "evidence": str(unresolved.get("source_note", "当前来源未规定。")),
        },
        confidence=1.0,
    )


def _capabilities(rules: list[Mapping[str, Any]], findings: list[Mapping[str, Any]], unresolved: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    by_rule: dict[str, set[str]] = {}
    for item in findings:
        by_rule.setdefault(str(item.get("rule_id", "")), set()).add(str(item.get("status", "NOT_CHECKED")))
    implemented: list[str] = []
    manual: list[str] = []
    not_checked: list[str] = []
    not_applicable: list[str] = []
    rule_ids = [str(rule.get("id", "")) for rule in rules]
    for rule_id in rule_ids:
        statuses = by_rule.get(rule_id, {"NOT_CHECKED"})
        if "MANUAL_REVIEW" in statuses:
            manual.append(rule_id)
        if "NOT_CHECKED" in statuses:
            not_checked.append(rule_id)
        if statuses <= {"NOT_APPLICABLE"}:
            not_applicable.append(rule_id)
        elif "NOT_CHECKED" not in statuses:
            implemented.append(rule_id)
    for item in unresolved:
        value = str(item.get("id", ""))
        if value and value not in not_checked:
            not_checked.append(value)
    return {
        "implemented": implemented,
        "manual_review": manual,
        "not_checked": not_checked,
        "not_applicable": not_applicable,
    }


def lint_inspection(inspection: Mapping[str, Any] | None, root: Path | str | None = None) -> dict[str, Any]:
    """Run all supported rules over an Inspector dictionary.

    The function is intentionally path-free: ``root`` only chooses which rule
    YAML files to read and is never copied into the resulting audit.
    """

    if not isinstance(inspection, Mapping):
        inspection = {}
    resolved_root = Path(root) if root is not None else None
    # Validation is a hard execution gate.  It must happen before the
    # registry reads any rule or conflict record so malformed custom roots
    # cannot fall through to a partial YAML audit.
    ensure_rules_valid(resolved_root)
    classification = classify_inspection(inspection)
    rules = rules_for(resolved_root, _validated=True)
    conflicts = conflicts_for(resolved_root, _validated=True)
    blocked_by_conflict: dict[str, set[str]] = {}
    unresolved_ids = unresolved_conflict_rule_ids(conflicts)
    for conflict in conflicts:
        status = str(conflict.get("status", "")).casefold()
        if status not in {"unresolved", "unresolved_overlap"}:
            continue
        conflict_id = str(conflict.get("id", "<unknown-conflict>"))
        for side_name in ("source_a", "source_b"):
            side = conflict.get(side_name)
            if not isinstance(side, Mapping):
                continue
            rule_id = side.get("rule_id")
            if isinstance(rule_id, str) and rule_id in unresolved_ids:
                blocked_by_conflict.setdefault(rule_id, set()).add(conflict_id)

    rules_by_id = {str(rule.get("id", "")): rule for rule in rules}
    context = RuleContext(inspection, classification, rules_by_id, root=root)
    findings: list[dict[str, Any]] = []
    for rule in rules:
        rule_id = str(rule.get("id", ""))
        if rule_id in blocked_by_conflict:
            findings.append(_blocked_rule_finding(rule, context, blocked_by_conflict[rule_id]))
            continue
        domain = str(rule.get("domain", "")).casefold()
        handler = _DOMAIN_HANDLERS.get(domain)
        if handler is None:
            findings.append(_not_checked(rule, context, "该规则领域尚未实现自动检查。"))
            continue
        result = handler(rule, context)
        if not result:
            result = [_not_checked(rule, context, "该规则尚未实现自动检查。")]
        findings.extend(result)

    for conflict in conflicts:
        status = str(conflict.get("status", "")).casefold()
        if status in {"unresolved", "unresolved_overlap"}:
            findings.append(_conflict_finding(conflict, context))
        elif effective_rule_id(conflict):
            findings.append(_conflict_info(conflict, context))
        else:
            findings.append(_conflict_finding(conflict, context))

    unresolved = unresolved_for(resolved_root, _validated=True)
    for item in unresolved:
        findings.append(_unresolved_finding(item, context))

    # Every output list is stable even if a custom rule loader returns an
    # unordered mapping.  Findings are first grouped by source rule order, then
    # by target position, and finally by status/message.
    rule_order = {str(rule.get("id", "")): index for index, rule in enumerate(rules)}
    findings.sort(
        key=lambda item: (
            rule_order.get(str(item.get("rule_id", "")), len(rule_order) + 1),
            item.get("target", {}).get("index", 10**12) if isinstance(item.get("target"), Mapping) else 10**12,
            str(item.get("target", {}).get("id", "")) if isinstance(item.get("target"), Mapping) else "",
            str(item.get("status", "")),
            str(item.get("message", "")),
        )
    )
    summary = build_summary(findings)
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "input": _safe_input(inspection),
        "summary": summary,
        "capabilities": _capabilities(rules, findings, unresolved),
        "findings": findings,
        "classification": classification,
    }


__all__ = ["lint_inspection"]
