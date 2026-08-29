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

段落使用稳定 ID，例如 `p-000017`。默认只有 `text_preview`，最多 80 字；显式使用 `--include-text` 后才增加 `text`。Run 记录文本范围、direct formatting、effective formatting、resolution source 和解析链。

表格 cell 的段落只出现在 `tables` 中，不会重复加入 body `paragraphs`。TOC 显示段落保留稳定 ID 并标记 `in_toc: true`。

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
    "by_status": {},
    "aggregates": []
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

## 状态

- `PASS`：目标存在且已确定满足规则。
- `ERROR`：目标存在且确定违反 mandatory 规则。
- `WARNING`：目标违反 recommended 规则。
- `INFO`：可追溯的观察信息。
- `MANUAL_REVIEW`：不能安全自动裁决。
- `NOT_CHECKED`：未实现，或规则冲突/未决项禁止自动执行。
- `NOT_APPLICABLE`：文档中不存在该类目标。

## 聚合

`summary.aggregates` 按 `rule_id + status` 聚合重复结果，记录数量和最多三个示例目标。`audit.json` 仍保留所有逐目标明细；`audit.md` 只呈现紧凑聚合，避免重复问题淹没报告。
