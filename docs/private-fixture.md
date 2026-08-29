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

私有集成验收还会核对：

- narrative citation 不被 GENERAL 规则误报，连接词规则仍独立生效；
- 固定论文中已知普通 parenthetical 样例精确保持 `kind=parenthetical`，`ER-CIT-NARRATIVE-001` 的 `ERROR` 为 0；
- `ER-MS-HEADING-HIERARCHY-001` 至少保留一个具有独立结构证据的确定性 `ERROR`，局部 ambiguous jump 可另有 `MANUAL_REVIEW`；
- 多来源 ASCII 分号定位到实际候选段落；
- 53 个参考文献条目和 53 个可解析自动编号；
- 页码范围 finding 选中真实 pages span，不把括号内卷期范围当页码；
- 11 张表的 cell paragraph 进入 Latin-font 检查；
- 表注绑定 evidence 由 Inspector `body_blocks`/相邻关系产生；
- compact report 不包含核心属性明文、绝对路径或超过 80 字的 preview。
