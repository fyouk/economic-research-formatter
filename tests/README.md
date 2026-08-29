# Tests

后续测试分两层：

1. `tests/rules/`：规则结构和规则逻辑测试。
2. `tests/fixtures/`：DOCX 输入/预期诊断结果的回归样本。

`fixtures/valid` 和 `fixtures/invalid` 当前只占位；在 Linter 实现前不制造“标准论文样本”，避免把尚未确认的格式当成期刊规则。
