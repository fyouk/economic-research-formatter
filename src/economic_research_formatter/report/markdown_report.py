"""Human-facing Chinese Markdown audit report rendering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import html
from pathlib import Path
from typing import Any

from economic_research_formatter.models.audit import STATUSES, build_summary


STATUS_LABELS = {
    "PASS": "通过",
    "ERROR": "错误",
    "WARNING": "警告",
    "INFO": "信息",
    "MANUAL_REVIEW": "人工复核",
    "NOT_CHECKED": "未检查",
    "NOT_APPLICABLE": "不适用",
}


def _cell(value: object) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    # Escape both Markdown control characters and HTML metacharacters.  This
    # applies to filenames, target IDs, and any user-derived finding text that
    # reaches a table cell; static report prose is not passed through here.
    text = html.escape(text, quote=False)
    for character in ("`", "*", "_", "[", "]", "(", ")", "#", "!", "~"):
        text = text.replace(character, f"\\{character}")
    return text


def _examples(aggregate: Mapping[str, Any]) -> str:
    examples = aggregate.get("examples", [])
    if not isinstance(examples, Sequence) or isinstance(examples, (str, bytes)):
        return ""
    labels: list[str] = []
    for target in examples:
        if not isinstance(target, Mapping):
            continue
        identifier = target.get("id") or target.get("filename") or target.get("kind")
        if identifier:
            labels.append(str(identifier))
    return ", ".join(labels)


def render_markdown(audit: Mapping[str, Any]) -> str:
    if not isinstance(audit, Mapping):
        raise TypeError("audit must be a mapping")
    raw_summary = audit.get("summary", {})
    raw_summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else {}
    findings = audit.get("findings", [])
    findings = [dict(item) for item in findings if isinstance(item, Mapping)] if isinstance(findings, Sequence) and not isinstance(findings, (str, bytes)) else []
    if findings:
        # Detailed findings are authoritative.  Mixing stale caller-provided
        # aggregates with freshly derived totals creates internally impossible
        # reports, so recompute the complete summary as one coherent unit.
        summary = build_summary(findings)
    else:
        # Summary-only legacy callers have no detailed source from which to
        # recompute.  Preserve their aggregates while deriving the newer dual
        # count fields from one consistent legacy finding count.
        summary = dict(raw_summary)
        counts = summary.get("by_status")
        counts = dict(counts) if isinstance(counts, Mapping) else {}
        aggregates = summary.get("aggregates")
        if not isinstance(aggregates, Sequence) or isinstance(aggregates, (str, bytes)):
            aggregates = []
        else:
            aggregates = list(aggregates)
        finding_count = summary.get("finding_count", summary.get("total_findings"))
        if finding_count is None:
            finding_count = sum(int(value or 0) for value in counts.values())
        try:
            finding_count = max(0, int(finding_count))
        except (TypeError, ValueError):
            finding_count = 0
        affected_target_count = summary.get(
            "affected_target_count", summary.get("total_affected_targets", finding_count)
        )
        try:
            affected_target_count = max(0, int(affected_target_count))
        except (TypeError, ValueError):
            affected_target_count = finding_count
        summary.update(
            {
                "total_findings": finding_count,
                "finding_count": finding_count,
                "affected_target_count": affected_target_count,
                "total_affected_targets": affected_target_count,
                "by_status": counts,
                "by_status_affected": summary.get("by_status_affected", dict(counts)),
                "by_rule_and_status": summary.get("by_rule_and_status", {}),
                "by_rule_and_status_affected": summary.get(
                    "by_rule_and_status_affected", {}
                ),
                "aggregates": aggregates,
                "table_aggregates": summary.get("table_aggregates", []),
            }
        )
    counts = summary.get("by_status", {})
    counts = counts if isinstance(counts, Mapping) else {}
    affected_counts = summary.get("by_status_affected", {})
    affected_counts = affected_counts if isinstance(affected_counts, Mapping) else {}
    input_info = audit.get("input", {})
    input_info = input_info if isinstance(input_info, Mapping) else {}
    capabilities = audit.get("capabilities", {})
    capabilities = capabilities if isinstance(capabilities, Mapping) else {}

    lines = ["# 《经济研究》格式审计报告", ""]
    filename = input_info.get("filename")
    if filename:
        lines.extend([f"- 文件：{_cell(filename)}", ""])
    lines.extend(
        [
            "## 总结",
            "",
            f"共记录 {int(summary.get('finding_count', summary.get('total_findings', len(findings))) or 0)} 条规则结果，涉及 {int(summary.get('affected_target_count', len(findings)) or 0)} 个受影响目标。",
            "",
            "| 状态 | finding 数 | 受影响目标 |",
            "| --- | ---: | ---: |",
        ]
    )
    for status in STATUSES:
        count = counts.get(status, 0)
        affected_count = affected_counts.get(status, count)
        if count or affected_count:
            lines.append(
                f"| {_cell(STATUS_LABELS.get(status, status))}（{status}） | "
                f"{_cell(count)} | {_cell(affected_count)} |"
            )

    lines.extend(["", "## 按规则聚合", "", "| 规则 | 状态 | finding 数 | 受影响目标 | 说明 | 示例目标 |", "| --- | --- | ---: | ---: | --- | --- |"])
    aggregates = summary.get("aggregates", [])
    aggregate_rows = 0
    if isinstance(aggregates, Sequence) and not isinstance(aggregates, (str, bytes)):
        for aggregate in aggregates:
            if not isinstance(aggregate, Mapping):
                continue
            # Callers may provide a compact summary without precomputed
            # examples.  Recover at most three deterministic targets from the
            # detailed findings so Markdown remains useful without expanding
            # into one line per repeated violation.
            aggregate = dict(aggregate)
            if not aggregate.get("examples"):
                matching_targets = [
                    dict(item.get("target", {}))
                    for item in findings
                    if item.get("rule_id") == aggregate.get("rule_id")
                    and item.get("status") == aggregate.get("status")
                    and isinstance(item.get("target"), Mapping)
                ]
                seen: set[tuple[object, ...]] = set()
                examples: list[dict[str, Any]] = []
                for target in matching_targets:
                    key = (target.get("kind"), target.get("id"), target.get("index"))
                    if key in seen:
                        continue
                    seen.add(key)
                    examples.append(target)
                    if len(examples) == 3:
                        break
                aggregate["examples"] = examples
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(aggregate.get("rule_id", "")),
                        _cell(aggregate.get("status", "")),
                        _cell(aggregate.get("finding_count", aggregate.get("count", 0))),
                        _cell(aggregate.get("affected_count", aggregate.get("count", 0))),
                        _cell(aggregate.get("message", "")),
                        _cell(_examples(aggregate)),
                    )
                )
                + " |"
            )
            aggregate_rows += 1
    if aggregate_rows == 0:
        lines.append("| - | - | 0 | 0 | 没有规则结果 | - |")

    table_aggregates = summary.get("table_aggregates", [])
    if isinstance(table_aggregates, Sequence) and not isinstance(table_aggregates, (str, bytes)) and table_aggregates:
        lines.extend(
            [
                "",
                "## 按表格聚合",
                "",
                "同一表格内由相同未知证据触发的结果合并展示；普通 finding 保留逐目标明细，折叠结果在 observed/examples 中保留最多三个示例。",
                "",
                "| 规则 | 状态 | 表格 | finding 数 | 受影响目标 | 说明 | 示例目标 |",
                "| --- | --- | --- | ---: | ---: | --- | --- |",
            ]
        )
        for aggregate in table_aggregates:
            if not isinstance(aggregate, Mapping):
                continue
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(aggregate.get("rule_id", "")),
                        _cell(aggregate.get("status", "")),
                        _cell(aggregate.get("table_id", "")),
                        _cell(aggregate.get("finding_count", aggregate.get("count", 0))),
                        _cell(aggregate.get("affected_count", aggregate.get("count", 0))),
                        _cell(aggregate.get("message", "")),
                        _cell(_examples(aggregate)),
                    )
                )
                + " |"
            )

    lines.extend(["", "## 能力边界", ""])
    labels = (
        ("已实现检查", "implemented"),
        ("人工复核", "manual_review"),
        ("未检查（含未决项）", "not_checked"),
        ("不适用", "not_applicable"),
    )
    for label, key in labels:
        values = capabilities.get(key, [])
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            text = "、".join(str(value) for value in values) if values else "无"
        else:
            text = str(values) if values else "无"
        lines.append(f"- {label}：{_cell(text)}")

    lines.extend(["", "## 逐条目标明细", "", "JSON 报告保留每条 finding 的目标、观察值、期望值和来源；本 Markdown 仅展示每条规则的最多三个示例目标，以避免重复问题淹没报告。", ""])
    return "\n".join(lines).rstrip() + "\n"


def write_markdown_report(audit: Mapping[str, Any], output: Path | str) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(audit), encoding="utf-8")
    return path


render_audit_markdown = render_markdown
write_audit_markdown = write_markdown_report


__all__ = ["render_audit_markdown", "render_markdown", "write_audit_markdown", "write_markdown_report"]
