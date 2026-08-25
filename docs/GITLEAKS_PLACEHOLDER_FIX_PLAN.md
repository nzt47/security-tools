# Gitleaks 硬编码密码扫描修复方案（独立 PR）

## 问题

PR 场景 / schedule 场景下 Gitleaks（`hardcoded-password-scan.yml`）失败：

```
RuleID:      openai-api-key
File:        scripts/guard_llm_api_key.py
Line:        36
Fingerprint: scripts/guard_llm_api_key.py:openai-api-key:36
```

**master 上连续 3 天 schedule 失败**（run 32618363727 / 32552032852 / 32447702918），属预存问题，与任何 PR 改动无关。

## 根因

`scripts/guard_llm_api_key.py` 是 LLM API key 校验脚本，其 `PLACEHOLDER` 集合（第 35-40 行）是**测试占位符黑名单**（用于识别/兜底已知测试 key），其中两个占位符满足 `openai-api-key` 规则的匹配条件：

| 行 | 占位符 | 长度（`sk-` 后） | 是否命中（规则要求 ≥20 字符） |
|---|---|---|---|
| 36 | `sk-test-1234567890abcdef` | 21 | ✅ 命中 |
| 40 | `sk-instance-key-12345` | 20 | ✅ 命中 |

`openai-api-key` 规则（`.github/gitleaks-config.toml` 第 160-165 行）：
```toml
regex = '''\b(sk|sk-ant)-[A-Za-z0-9_\-]{20,}\b'''
```

现有 allowlist（paths + regexes）未覆盖 `scripts/guard_llm_api_key.py` 与 `sk-test-*` 占位符：
- allowlist paths 只豁免 `tests/.*\.py$`（该文件在 `scripts/`）
- allowlist regexes 只含 `sk-\.\.\..*`（文档占位符），不含 `sk-test-*`

## 修复方案（推荐：config 层 allowlist）

在 `.github/gitleaks-config.toml` 的 `[allowlist] regexes` 追加精确占位符模式（与现有 CHG 修复风格一致）：

```toml
    # guard_llm_api_key.py 的 PLACEHOLDER 测试占位符 (CHG-2026-xxxx):
    # LLM key 校验脚本的占位符黑名单, 非真实密钥. 精确锚定, 不宽放真实 sk- 密钥.
    '''^sk-(test|secret|real|instance)[-A-Za-z0-9_]*$''',
    '''^sk-1234567890abcdef$''',
```

> 说明：`regexTarget = "match"`（第 18 行），allowlist regex 对 Gitleaks 捕获的 Match 文本做匹配。`^...$` 锚定保证只放行占位符本身，不会放行 `sk-proj-...` 等真实密钥。

## 备选方案

**方案 B：代码行内 `gitleaks:allow` 注释**（不动 config，分散在代码）：

```python
PLACEHOLDER = {
    "sk-test-1234567890abcdef",          # gitleaks:allow 并行会话测试 key
    ...
    "sk-instance-key-12345",             # gitleaks:allow 实例级测试 key
}
```

不推荐：豁免逻辑散落在代码中，且需在多个命中行重复标注。

## 验证

1. 本地：`gitleaks detect --config .github/gitleaks-config.toml --source .` → exit 0，无 openai-api-key 命中。
2. 修改后 push 触发 PR/push Gitleaks → success。
3. schedule 触发（每日 04:00）连续通过。

## 变更文件

| 文件 | 改动 |
|---|---|
| `.github/gitleaks-config.toml` | `[allowlist] regexes` 追加 2 条占位符模式 |
