# 私有 DOCX 集成测试

公开仓库不保存私有论文、本机绝对路径或完整私有审计报告。集成测试通过环境变量定位本地 DOCX：

```bash
export ER_PRIVATE_FIXTURE='/absolute/path/private-manuscript.docx'
python -m pytest -q tests/integration/test_private_fixture.py
```

如果未设置环境变量，或文件不存在，私有集成测试会明确 skip，不影响普通 CI。

## 本地审计

```bash
er-format inspect "$ER_PRIVATE_FIXTURE" \
  --output artifacts/private/inspection.json

er-format lint "$ER_PRIVATE_FIXTURE" \
  --output-dir artifacts/private/audit \
  --exit-zero
```

`artifacts/`、`tests/private/`、`reports/private/`、`*.private.audit.json` 和 `*.private.audit.md` 已在 `.gitignore` 中排除。

## 隐私检查清单

- 不将私有 DOCX 复制到仓库。
- 不将完整 inspection/audit 或全文快照 commit。
- 不在代码、测试、日志、PR 或 issue 中写入本机绝对路径。
- 默认不使用 `--include-text`。
- 对外报告只分享聚合数量、rule ID、段落 ID 和必要的短预览。
- 提交前使用 `git status --short`、`git diff --cached --name-status` 和 Git history 搜索再次确认。

Inspector 和 Linter 均只读输入 DOCX。集成测试还会比较执行前后的 bytes 与 `mtime_ns`。
