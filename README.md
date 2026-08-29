# economic-research-formatter

面向《经济研究》投稿论文的 **DOCX 只读结构检查与格式审计工具**。

当前阶段将现有规范材料编译为可追溯资产和确定性程序，避免依赖临时提示或主观补全：

1. 可追溯的原始来源（`sources/raw/`）
2. 机器可读的原子规则（`rules/`）
3. 冲突与未知项登记（`rules/conflicts.yaml`、`rules/unresolved.yaml`）
4. 可复用工作流说明（`SKILL.md`）
5. DOCX Inspector / Linter 的可追溯实现（`src/`）

> **核心原则**：工具只能执行来源材料明确支持的规则。未规定、仅由示例推断、或来源之间存在冲突的项目，不得静默补全为“《经济研究》要求”。

## 当前状态

**Milestone 2–3 / Strict rules + Read-only Inspector/Linter v1（已实现）**

已经完成第一轮：

- 原始 Markdown 与 DOCX 归档
- 规则 Schema
- 稿件版式规则拆分
- 文内引文规则拆分
- 文后参考文献规则拆分
- 冲突登记
- 未规定项目登记
- 严格规则结构、引用与跨文件校验
- Lint 执行前的严格规则门禁与未决冲突隔离
- DOCX/OOXML 只读结构扫描
- 确定性语义分类
- Manuscript / Citation / Reference Linter v1
- 确定性 JSON 和中文 Markdown 审计报告
- ZIP/XML/图像资源上限与默认隐私脱敏
- 合成测试与可选私有样本集成测试

尚未实现：

- Word 样式应用
- 自动修复
- 页面渲染验收
- CSL 生成/适配

> 当前版本不会修改 DOCX，也不提供 `fix` 命令。

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
│   ├── audit-schema.md
│   ├── limitations.md
│   ├── private-fixture.md
│   ├── source-priority.md
│   └── rule-authoring-guide.md
├── src/economic_research_formatter/
│   ├── docx/                 # OOXML Inspector
│   ├── classify/             # 确定性语义分类
│   ├── lint/                 # 规则执行
│   ├── models/               # 稳定输出模型
│   ├── report/               # JSON / Markdown
│   ├── cli.py
│   └── rule_loader.py
└── tests/
    ├── rules/
    ├── inspector/
    ├── classifier/
    ├── linter/
    └── integration/
```

## 设计原则

### 1. Source of truth 是来源 + 规则，不是 Prompt

`SKILL.md` 只描述操作顺序。具体格式值必须来自 `rules/*.yaml`，而 `rules/*.yaml` 必须能追溯到 `sources/raw/`。

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

规则文件为后续 Safe Formatter 保留 `autofix` 元数据，但当前版本不执行任何自动修复。

## 安装

Python 要求：`>=3.10`。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## CLI

### 校验规则

```bash
er-format validate-rules
er-format validate-rules --json
```

### 只读扫描 DOCX

```bash
er-format inspect INPUT.docx --output inspection.json
```

默认只保存最多 80 字的文本预览。只有当输出确实可以保留全文时才使用：

```bash
er-format inspect INPUT.docx --output inspection.json --include-text
```

### 执行格式审计

```bash
er-format lint INPUT.docx --output-dir reports/audit
```

输出：

```text
reports/audit/inspection.json
reports/audit/audit.json
reports/audit/audit.md
```

`lint` 会在内存中读取全文以识别文内引用，但默认写出的 `inspection.json` 仍仅包含截断预览。如果审计发现 `ERROR`，命令返回 1；执行失败返回 2。探索性审计可使用 `--exit-zero`。

## 结果状态

| 状态 | 含义 |
| --- | --- |
| `PASS` | 目标存在且已确定满足规则 |
| `ERROR` | 目标确定违反 mandatory 规则 |
| `WARNING` | 目标违反 recommended 规则 |
| `INFO` | 可追溯的观察信息 |
| `MANUAL_REVIEW` | 存在目标，但冲突、语义或能力边界阻止安全自动判断 |
| `NOT_CHECKED` | 尚未实现，或 unresolved/conflict 禁止自动裁决 |
| `NOT_APPLICABLE` | 文档中不存在该类目标 |

`NOT_APPLICABLE` 不等于 `PASS`，`NOT_CHECKED` 也不会伪装成已检查。

## 私有文档

本地私有集成测试通过环境变量定位：

```bash
export ER_PRIVATE_FIXTURE='/absolute/path/private-manuscript.docx'
python -m pytest -q tests/integration/test_private_fixture.py
```

私有 DOCX、完整私有审计报告和本机绝对路径均不应进入公开仓库。详见 [`docs/private-fixture.md`](docs/private-fixture.md)。

## 计划

### M1 — Source normalization
- [x] 归档现有规范
- [x] 建立来源等级与冲突策略
- [x] 登记未知项

### M2 — Rule specification
- [x] 第一轮拆分 manuscript / citation / reference rules
- [x] 建立严格结构、引用和跨文件校验
- [x] 建立规则正向/负向单元测试
- [ ] 对每条来源规则做逐条人工内容复核

### M3 — DOCX linter
- [x] DOCX 结构扫描
- [x] 语义角色识别
- [x] 字体、字号、段落、脚注、图表与参考文献检查 v1
- [x] 输出可定位的 JSON + Markdown 审计报告

### M4 — Safe formatter
- [ ] 低风险格式自动修复
- [ ] 保留字段、公式、脚注、超链接、批注等对象
- [ ] 修改后重新 lint

### M5 — Citation / Reference engine
- [ ] 参考文献解析
- [ ] 文内—文后一致性检查
- [ ] CSL 或确定性格式器

## 开发验证

```bash
python -m pytest -q
python -m economic_research_formatter.cli validate-rules
```
