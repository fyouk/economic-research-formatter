# 《经济研究》DOCX 格式化 Skill

## 目标

根据仓库中的已验证来源和原子规则，对 Word 论文执行“只读扫描 → 语义分类 → 规则审计”。当前版本不修改 DOCX。

## 不可违反的原则

1. 不修改论文实质观点和表述，除非用户明确要求内容编辑。
2. 不凭空补作者、年份、卷期、页码、出版社等元数据。
3. 不把 `unresolved.yaml` 中的未知项转化成期刊硬性要求。
4. 不把 `example_only` 的观察结果自动升级为 `mandatory`。
5. 不静默解决 `conflicts.yaml` 中尚未裁决的冲突。
6. 能保留 Zotero / EndNote / Word 字段时，不得把动态字段扁平化为普通文本。
7. 不重建整篇 DOCX 来实现普通格式修改；应尽量原位修改样式/OOXML。
8. 原始文件不得被覆盖或改写。
9. 当规范无法支持某项判断时，输出 `NOT_CHECKED` 或 `MANUAL_REVIEW`，而不是猜测。
10. 当文档中不存在该类目标时，输出 `NOT_APPLICABLE`，不得写成 `PASS`。
11. 默认报告只保留截断文本预览；全文仅在显式 `--include-text` 时落盘。

## 规则来源

处理前依次读取：

1. `rules/schema.yaml`
2. `rules/conflicts.yaml`
3. `rules/unresolved.yaml`
4. 与当前任务相关的 `manuscript.yaml` / `citations.yaml` / `references.yaml`

仅在需要核对原文时读取 `sources/raw/`。

## 当前可执行流程

### Step 1 — Inspect

扫描 DOCX：

- sections / page setup
- paragraphs / runs / styles
- title / authors / abstract / keywords
- heading hierarchy
- tables / captions
- figures / captions
- equations
- footnotes / endnotes
- fields / hyperlinks / bookmarks
- citation manager fields
- reference section

不得在此阶段修改。

### Step 2 — Classify

对每个结构单元标记语义角色和置信度，例如：

```yaml
role: heading_level_2
confidence: 0.94
```

低置信度结构不得作为确定性违规结论，应进入人工复核。

### Step 3 — Lint

规则结果限定为：

- `PASS`
- `ERROR`
- `WARNING`
- `INFO`
- `MANUAL_REVIEW`
- `NOT_CHECKED`
- `NOT_APPLICABLE`

`NOT_APPLICABLE` 表示文档中没有该类目标；`NOT_CHECKED` 表示能力、未决项或冲突阻止了自动检查。

### Step 4 — Report

输出：

```text
inspection.json
audit.json
audit.md
```

JSON 对普通 finding 保留逐目标明细；同一表格内同规则、同状态、同未知证据的重复单元格结果折叠为一个 table-level finding，并保留数量和最多三个示例。Markdown 按 rule ID、状态和表格聚合展示。

## 未开放流程

Safe Fix、Re-lint 和页面渲染验收属于后续里程碑。当前不得执行 `autofix: safe/conditional`，也不得生成修改后 DOCX。

## 输出

至少生成：

```text
inspection.json
audit.json
audit.md
```
