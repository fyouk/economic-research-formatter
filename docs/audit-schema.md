# Audit schema v1

`er-format lint` 生成三个确定性 UTF-8 文件：

```text
inspection.json
audit.json
audit.md
```

输出不附加当前时间或进程相关字段，因此同一输入和同一版本的结果可以逐字节比较。

## inspection.json

顶层结构：

```json
{
  "schema_version": "1.0",
  "tool_version": "0.1.0",
  "input": {
    "filename": "paper.docx",
    "sha256": "...",
    "size_bytes": 0,
    "reported_page_count": 0
  },
  "summary": {},
  "body_blocks": [],
  "sections": [],
  "paragraphs": [],
  "tables": [],
  "images": [],
  "equations": {},
  "notes": {},
  "fields": {},
  "bookmarks": {},
  "hyperlinks": {},
  "comments": {},
  "tracked_changes": {},
  "embedded_objects": {}
}
```

`reported_page_count` 来自 Word 文档属性，不是当前环境实时渲染的权威页数。

`body_blocks` 以稳定 ID 保留顶层 paragraph/table 的真实文档顺序，用于表注绑定和跨对象上下文。

段落使用稳定 ID，例如 `p-000017`。默认只有 `text_preview`，最多 80 字；显式使用 `--include-text` 后才增加 `text`。Run 记录文本范围、direct formatting、effective formatting、resolution source 和解析链。

表格 cell 的段落只出现在 `tables` 中，不会重复加入 body `paragraphs`。TOC 显示段落保留稳定 ID 并标记 `in_toc: true`。

`core_properties` 默认不保存姓名或标题等明文；creator/last-modified-by 仅记录 presence + SHA-256，其他文本属性默认省略。只有显式 `--include-metadata` 才保留明文，`--include-text` 不会隐式开启该能力。

## audit.json

顶层结构：

```json
{
  "schema_version": "1.0",
  "input": {
    "filename": "paper.docx",
    "sha256": "...",
    "size_bytes": 0
  },
  "summary": {
    "total_findings": 0,
    "finding_count": 0,
    "affected_target_count": 0,
    "by_status": {},
    "by_status_affected": {},
    "by_rule_and_status": {},
    "by_rule_and_status_affected": {},
    "aggregates": [],
    "table_aggregates": []
  },
  "capabilities": {
    "implemented": [],
    "manual_review": [],
    "not_checked": [],
    "not_applicable": []
  },
  "findings": [],
  "classification": {}
}
```

`input` 不包含绝对路径。`classification.items` 保留角色、置信度、证据和来源 ID。

`finding_count`（并保持兼容的 `total_findings`）统计输出中的 finding 数量；`affected_target_count` 统计这些 finding 所代表的原始受影响目标数量。`by_status` 与 `by_rule_and_status` 统计 finding 数，`by_status_affected` 与 `by_rule_and_status_affected` 统计受影响目标数，两组口径不可混用。

## Finding

每条 finding 至少包含：

```json
{
  "rule_id": "ER-MS-TITLE-001",
  "status": "ERROR",
  "message": "...",
  "target": {
    "kind": "paragraph",
    "id": "p-000000",
    "index": 0,
    "text_preview": "...",
    "role": "title"
  },
  "observed": {},
  "expected": {},
  "source": {
    "source_id": "...",
    "locator": "...",
    "evidence": "..."
  },
  "confidence": 0.99
}
```

`expected` 直接来自当前 `rules/*.yaml` 的 `requirement`。Linter 不在代码中维护第二套字体、字号、连接符或横线要求。

引文 finding 的 `observed.candidate` 可包含 `kind` (`parenthetical` / `narrative` / `unknown`)、paragraph ID、span、authors、year、page、最多 80 字的 preview 与 confidence。合法 narrative citation 的作者在正文，括号内只保留年份/必要页码。

参考文献版式 finding 的编号证据同时保留 `automatic_numbering_state` 与段首高置信 `visible_numbering`。可见序号还包含 `visible_marker`、基于原始文本的半开区间 `visible_marker_span` 和 `evidence_source=reference_text_prefix`。自动编号未知但可见序号明确时仍可确定违反“不标序号”；两者都存在时只生成一个可解释 finding。

标题层级按证据确定性输出逐目标结果。同一 `ER-MS-HEADING-HIERARCHY-001` 可同时包含 definite target 的 `PASS` / `ERROR` 与 visible-only jump target 的 `MANUAL_REVIEW`；局部未知不会覆盖其他 target 的确定性违规。

多字段表格规则以一个 target finding 组合各字段结果：任何 definite mismatch 都优先保留规则的 `ERROR` / `WARNING`，未知字段继续记录在 `unresolved_fields` / `unchecked_fields`；只有不存在确定性 mismatch 时，未知证据才产生 `MANUAL_REVIEW`。

## 状态

- `PASS`：目标存在且已确定满足规则。
- `ERROR`：目标存在且确定违反 mandatory 规则。
- `WARNING`：目标违反 recommended 规则。
- `INFO`：可追溯的观察信息。
- `MANUAL_REVIEW`：不能安全自动裁决。
- `NOT_CHECKED`：未实现，或规则冲突/未决项禁止自动执行。
- `NOT_APPLICABLE`：文档中不存在该类目标。

## 聚合

`summary.aggregates` 按 `rule_id + status` 汇总结果，记录数量和最多三个示例目标。普通 finding 在 `audit.json` 中保留逐目标明细；同一表格内由同一规则、状态和未知证据触发的重复单元格 finding 会折叠成一个 table-level finding，并在 `observed.count` 与最多三个 `observed.examples` 中保留影响规模和定位样例。`summary.table_aggregates` 与 `audit.md` 使用同一聚合语义，避免重复问题淹没报告。
