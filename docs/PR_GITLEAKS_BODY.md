## Summary

修复 Gitleaks 硬编码密码扫描持续失败（master 连续 3 天 schedule 失败 + 所有 PR 场景失败）：`openai-api-key` 规则误命中 LLM key 校验脚本的测试占位符黑名单。

## 根因

`scripts/guard_llm_api_key.py` 的 `PLACEHOLDER` 集合（第 35-40 行）是 LLM API key 校验脚本的**测试占位符黑名单**（非真实密钥），其中两个占位符满足 `openai-api-key` 规则（`.github/gitleaks-config.toml` 第 160-165 行，`sk-` 后 ≥20 字符）：

| 行 | 占位符 | 命中 |
|---|---|---|
| 36 | `sk-test-1234567890abcdef`（后 21 字符） | ✅ |
| 40 | `sk-instance-key-12345`（后 20 字符） | ✅ |

现有 allowlist 未覆盖：paths 仅豁免 `tests/.*\.py$`（该文件在 `scripts/`）；regexes 仅含 `sk-\.\.\..*`（文档占位符）。

## 修复

`.github/gitleaks-config.toml` 的 `[allowlist] regexes` 追加锚定占位符模式（`regexTarget = "match"`，对捕获的 Match 文本匹配，`^...$` 锚定不宽放真实密钥）：

```toml
    # guard_llm_api_key.py 的 PLACEHOLDER 测试占位符 (非真实密钥)
    '''^sk-(test|secret|real|instance)[-A-Za-z0-9_]*$''',
    '''^sk-1234567890abcdef$''',
```

## 验证

- [ ] 本地：`gitleaks detect --config .github/gitleaks-config.toml --source .` → exit 0
- [ ] push/PR 触发 Gitleaks → success
- [ ] 次日 schedule（04:00 UTC）通过

## 变更文件

- `.github/gitleaks-config.toml`（+2 行 allowlist regex）

## 关联

此 PR 是 PR #786（Skills Check 扫描优化）合并的**前置阻塞项**：分支保护的 Gitleaks required check 失败会阻止 #786 合并。
