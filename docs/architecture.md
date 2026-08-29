# Architecture

## Pipeline

```text
DOCX
  ↓
Inspector        读取 OOXML、样式、字段、脚注、表图、段落
  ↓
Classifier       为段落/对象赋予语义角色 + confidence
  ↓
Rule Engine      加载 rules/*.yaml
  ↓
Linter           PASS / ERROR / WARNING / MANUAL_REVIEW / NOT_CHECKED
  ↓
Safe Fixer       只执行 safe / 已满足前置条件的 conditional
  ↓
Re-Linter        对输出文件重新检查
  ↓
Renderer QA      页面渲染后检查视觉异常（后续）
```

## Source → rule → implementation

```text
sources/raw
    ↓ 人工拆分、保留证据
rules/*.yaml
    ↓ 程序读取
lint/fix implementation
```

程序不得把格式值硬编码成另一套“隐形规范”。

## DOCX safety principles

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
