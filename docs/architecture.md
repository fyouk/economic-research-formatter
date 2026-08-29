# Architecture

## Pipeline

```text
DOCX
  ↓
Inspector        读取 OOXML、样式、字段、脚注、表图、段落
  ↓
Classifier       为段落/对象赋予语义角色 + confidence
  ↓
Citation model   parenthetical / narrative / unknown + span + confidence
  ↓
Rule Engine      加载 rules/*.yaml
  ↓
Linter           7 类状态 + 逐目标 finding（同表同类 unknown 折叠）+ capability summary
  ↓
Report           inspection.json + audit.json + audit.md
```

当前管线截止于 Report，不包含 Safe Fixer、Re-Linter 或 Renderer QA。

为计算只读 inspection 中的正文 dominant-size 证据，Inspector 会对已生成的 body paragraph 结构运行一次确定性的轻量角色分类，并据此排除摘要、标题、图题、参考文献等非正文目标。完整 Classifier 阶段随后仍对最终 inspection 统一生成公开的角色与 confidence；该内部 enrichment 不修改 DOCX，也不执行规则裁决。

## Source → rule → implementation

```text
sources/raw
    ↓ 人工拆分、保留证据
rules/*.yaml
    ↓ 程序读取
lint implementation
```

程序不得把格式值硬编码成另一套“隐形规范”。

Linter 执行前必须通过严格规则校验。非法规则根直接终止执行；被未决 conflict 引用的规则不进入确定性 handler，而是输出 `NOT_CHECKED`。

## Profile 与分发

```text
rules/ + sources/normalized/source-index.yaml   # 人工维护源树
                    ↕ byte-for-byte sync test
src/economic_research_formatter/profiles/economic_research/
                    ↓ importlib.resources
installed wheel / console script
```

运行时从 package profile 加载规则，因此不依赖当前工作目录或仓库顶层路径。CI 在 Python 3.10–3.12 上执行 lint/test/validate/build，并在 checkout 外的临时 venv 运行 wheel smoke test。

## 当前模块

```text
cli.py
├── rule_loader.py              # 严格规则与跨文件校验
├── docx/
│   ├── package.py          # ZIP/part/relationship 边界
│   ├── inspector.py        # 只读编排与稳定 ID
│   ├── styles.py           # direct/style/default/theme 解析
│   ├── numbering.py        # numPr 解析
│   ├── fields.py           # TOC/PAGEREF/PAGE/REF
│   ├── notes.py            # footnote/endnote
│   ├── images.py           # 图像、尺寸、颜色分析
│   └── equations.py        # OMML 与 OLE 证据
├── classify/                    # 确定性 heuristic + evidence
├── lint/                        # manuscript/citation/reference handlers
├── models/                      # inspection/audit/rule validation + shared note/numbering/font-size semantics
├── profiles/                    # bundled journal profile data
└── report/                      # 确定性 JSON 和中文 Markdown
```

Inspector 另输出统一 `body_blocks`，保留 paragraph/table 的真实 OOXML 顺序。表后第一个非空 `注：` 段只在无标题/新表/新图阻断时绑定，并保留 table ID、paragraph ID、距离和原因。表格/正文/表注相对字号由 effective run formatting 计算；混合或缺失证据为 unknown。

## 双视图隐私边界

`lint` 在内存中使用全文执行 citation/reference 识别。默认落盘前删除内容型 `text` / `deleted_text` / `full_text`，只保留最多 80 字的预览，同时保留编号等结构性文本。本地 hyperlink 与 field instruction 默认只保留类型、hash 和脱敏预览。绝对输入路径不进入 inspection 或 audit。

## 输入安全边界

- 拒绝重复 ZIP member。
- 单一解压 part 上限 64 MiB，总解压大小上限 256 MiB，member 数上限 10,000。
- 图像解码像素上限 25,000,000，在分配完整像素列表前验证。
- XML 解析显式禁用外部实体和网络，不启用 `huge_tree` 或容错解析。
- 已被关系或 Content Type 声明的 optional part 如果损坏，报告明确错误，不当作“对象不存在”。

## 未来 Safe Formatter 的 DOCX 安全原则

后续实现必须优先原位修改，尽量避免“抽文本 → 新建 Word → 写回文本”的方式，因为后者容易破坏：

- Word fields
- Zotero / EndNote fields
- equations
- footnotes / endnotes
- hyperlinks / bookmarks
- comments / revisions
- cross references
- section/page setup
- embedded charts
