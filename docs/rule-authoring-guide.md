# Rule authoring guide

每条规则应尽可能原子化：一个规则只回答一个可检查问题。

## 必填字段

```yaml
id: ER-MS-TITLE-001
domain: manuscript
target: title
normativity: mandatory
source:
  source_id: ER-MS-IMG-NOTES
  source_type: red_annotation
  locator: "附录/图片第1页/批注1"
  evidence: "标题：宋体三或小三，居中。"
requirement:
  ...
lint:
  severity: error
autofix: conditional
```

## normativity

### mandatory

来源明确要求，违反时一般输出 `ERROR`。

### recommended

来源使用“建议”等措辞。违反时一般输出 `WARNING`，不能伪装成强制要求。

### example_only

只从示例可见。默认 `INFO` 或 `MANUAL_REVIEW`，不能自动修复。

## autofix

### safe

不依赖复杂语义识别或内容补写的确定性修改，例如把“电子邮箱”替换为“电子信箱”（前提是确实位于作者联系方式字段）。

### conditional

只有当目标对象已被高置信度识别后才修改，例如标题/图题/参考文献条目样式。

### never

可能改变信息、破坏字段、需要理解语义或来源存在冲突时使用。

## 来源证据

`evidence` 要短且忠实，不要把模型解释写成原文。
完整上下文保留在 `sources/raw/`。
