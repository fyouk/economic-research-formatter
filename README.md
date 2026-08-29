# economic-research-formatter

面向《经济研究》投稿论文的 **DOCX 格式检查与安全格式化工具**。

当前阶段的目标不是“让 Agent 读完规范后自由发挥”，而是把现有规范材料编译为：

1. 可追溯的原始来源（`sources/raw/`）
2. 机器可读的原子规则（`rules/`）
3. 冲突与未知项登记（`rules/conflicts.yaml`、`rules/unresolved.yaml`）
4. Agent 执行工作流（`SKILL.md`）
5. 后续 DOCX Linter / Formatter 的代码骨架（`src/`）

> **核心原则**：工具只能执行来源材料明确支持的规则。未规定、仅由示例推断、或来源之间存在冲突的项目，不得静默补全为“《经济研究》要求”。

## 当前状态

**Milestone 1 / Source normalization + Rule specification（进行中）**

已经完成第一轮：

- 原始 Markdown 与 DOCX 归档
- 规则 Schema
- 稿件版式规则拆分
- 文内引文规则拆分
- 文后参考文献规则拆分
- 冲突登记
- 未规定项目登记
- 最小规则加载/校验 CLI

尚未实现：

- DOCX 结构识别
- Word 样式应用
- 引用字段保护
- 自动修复
- 页面渲染验收
- CSL 生成/适配

## 目录

```text
economic-research-formatter/
├── README.md
├── SKILL.md
├── pyproject.toml
├── sources/
│   ├── raw/                  # 原始资料，只归档，不改写
│   └── normalized/           # 规范索引与来源映射
├── rules/
│   ├── schema.yaml           # 规则字段规范
│   ├── manuscript.yaml       # 题名、作者、摘要、标题、公式、表图脚注等
│   ├── citations.yaml        # 文内引用
│   ├── references.yaml       # 文后参考文献
│   ├── conflicts.yaml        # 跨来源冲突/重叠
│   └── unresolved.yaml       # 当前资料未规定或无法确定的项目
├── docs/
│   ├── architecture.md
│   ├── source-priority.md
│   └── rule-authoring-guide.md
├── src/economic_research_formatter/
│   ├── __init__.py
│   ├── cli.py
│   └── rule_loader.py
└── tests/
    └── rules/
```

## 设计原则

### 1. Source of truth 是来源 + 规则，不是 Prompt

`SKILL.md` 只负责规定 Agent 的操作顺序。具体格式值必须来自 `rules/*.yaml`，而 `rules/*.yaml` 必须能追溯到 `sources/raw/`。

### 2. 红色批注是明确来源标签，不只是视觉颜色

红色批注统一编码为：

```yaml
source_type: red_annotation
```

它代表“原图片中的显式编辑批注”。但它与另一份独立来源发生冲突时，不自动假定时间先后，必须进入 `conflicts.yaml`。

### 3. 区分强制、建议、示例和未知

```text
mandatory       明确要求
recommended     “建议”等软性要求
example_only    只从示例观察到，不能当硬规则
unknown         当前来源未规定
```

### 4. Lint 优先于 Fix

第一版工具先回答：

> “这篇 DOCX 哪些地方确定违反现有规范？”

而不是立即修改全部内容。

自动修复只允许作用于 `autofix: safe` 或满足条件后的 `autofix: conditional` 规则。

## 计划

### M1 — Source normalization
- [x] 归档现有规范
- [x] 建立来源等级与冲突策略
- [x] 登记未知项

### M2 — Rule specification
- [x] 第一轮拆分 manuscript / citation / reference rules
- [ ] 对每条规则做逐条人工复核
- [ ] 建立规则单元测试

### M3 — DOCX linter
- [ ] DOCX 结构扫描
- [ ] 语义角色识别
- [ ] 字体、字号、段落、脚注、图表与参考文献检查
- [ ] 输出可定位的审计报告

### M4 — Safe formatter
- [ ] 低风险格式自动修复
- [ ] 保留字段、公式、脚注、超链接、批注等对象
- [ ] 修改后重新 lint

### M5 — Citation / Reference engine
- [ ] 参考文献解析
- [ ] 文内—文后一致性检查
- [ ] CSL 或确定性格式器

## 本地验证规则文件

```bash
python -m economic_research_formatter.cli validate-rules
```

开发安装：

```bash
pip install -e .
```
