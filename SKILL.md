# 《经济研究》DOCX 格式化 Skill

## 目标

根据仓库中的已验证来源和原子规则，对 Word 论文执行“检查 → 安全修复 → 再检查”。

## 不可违反的原则

1. 不修改论文实质观点和表述，除非用户明确要求内容编辑。
2. 不凭空补作者、年份、卷期、页码、出版社等元数据。
3. 不把 `unresolved.yaml` 中的未知项转化成期刊硬性要求。
4. 不把 `example_only` 的观察结果自动升级为 `mandatory`。
5. 不静默解决 `conflicts.yaml` 中尚未裁决的冲突。
6. 能保留 Zotero / EndNote / Word 字段时，不得把动态字段扁平化为普通文本。
7. 不重建整篇 DOCX 来实现普通格式修改；应尽量原位修改样式/OOXML。
8. 原始文件不得被覆盖；输出新文件。
9. 修改后必须重新运行 Linter。
10. 当规范无法支持某项判断时，输出 `NOT_CHECKED` 或 `MANUAL_REVIEW`，而不是猜测。

## 规则来源

处理前依次读取：

1. `rules/schema.yaml`
2. `rules/conflicts.yaml`
3. `rules/unresolved.yaml`
4. 与当前任务相关的 `manuscript.yaml` / `citations.yaml` / `references.yaml`

仅在需要核对原文时读取 `sources/raw/`。

## 推荐执行流程

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

低置信度结构不得直接应用高风险修复。

### Step 3 — Lint

规则结果限定为：

- `PASS`
- `ERROR`
- `WARNING`
- `INFO`
- `MANUAL_REVIEW`
- `NOT_CHECKED`

### Step 4 — Safe Fix

只自动执行：

- `autofix: safe`
- 或已满足规则中全部 `preconditions` 的 `autofix: conditional`

`autofix: never` 只能报告。

### Step 5 — Re-lint

重新读取修改后的 DOCX，不能用“调用成功”代替验收。

### Step 6 — Visual verification

当实现渲染能力后，将 DOCX 渲染为页面图像，检查：

- 分页异常
- 表格截断
- 图片遮挡
- 公式裁切
- 标题孤立
- 字体回退
- 页眉页脚异常

## 输出

至少生成：

```text
paper_economic_research.docx
paper_audit.json
paper_audit.md
paper_manual_review.md
```
