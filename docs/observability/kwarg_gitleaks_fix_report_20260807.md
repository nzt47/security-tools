# kwarg 冲突 + gitleaks 白名单修复报告（2026-08-07）

## 背景

`pre-commit run --all-files` 全量扫描暴露 2 个既有 hook 失败（与 2026-08-07 提交 bf79d705 无关，均为仓库存量问题）：

| Hook | 失败点 | 风险 |
|---|---|---|
| kwarg-conflict-scan | `agent/orchestrator/routing_observability.py:255` | HIGH（`add_layer` 显式 kwargs 与 `**fields` 展开冲突） |
| scan-sensitive-data | `.github/gitleaks-config.toml:191` | PEM 私钥块误报（gitleaks 配置自带测试样例） |

## 修复 1：routing_observability.py kwarg 冲突（HIGH）

**原因**：`log_layer_result` 调用 `ctx.add_layer(layer, decision, duration_ms=..., score=..., **fields)`。`add_layer` 显式参数 `duration_ms`/`score` 若与 `**fields` 中的同名键碰撞，Python 抛 `TypeError: got multiple values for keyword argument`，被外层 try/except 静默降级吞掉（既有【不易】契约：埋点失败不阻断主链路）。

**修复**：展开前过滤保留键（kwarg 扫描器建议的 `_RESERVED` 方案）。

```diff
         ctx = RouteContext.get()
         if ctx is not None:
+            # 【不易】duration_ms/score 是 add_layer 的结构化字段，调用方若经
+            # **fields 误传同名键会与显式参数冲突（TypeError 被外层降级吞掉）。
+            # 展开前过滤保留键，保证层日志契约字段不被 fields 覆盖。
+            _reserved = {"duration_ms", "score"}
+            safe_fields = {k: v for k, v in fields.items()
+                           if k not in _reserved}
             ctx.add_layer(layer, decision, duration_ms=duration_ms,
-                          score=score, **fields)
+                          score=score, **safe_fields)
```

**行为影响**：冲突键被过滤，`add_layer` 使用显式参数值；`fields` 中其余字段原样透传。不再因误传同名键触发 TypeError 降级路径。

## 修复 2：scan_sensitive_data.py gitleaks 配置白名单

**原因**：`scan_sensitive_data.py` 把 `.github/gitleaks-config.toml` 内的 PEM 测试样例（`-----BEGIN PRIVATE KEY-----`，gitleaks 配置自带测试样例，注释声明"测试样例由 allowlist 兜底放行"）误判为真实私钥。

**修复**：`WHITELIST_PATHS` 增加路径白名单（路径子串精确匹配该配置文件）。

```diff
     # 备份文件
     '.backups/',
     '_backup',
+    # gitleaks 扫描配置本身（含 PEM 测试样例与正则示例，非真实密钥；
+    # 注释已声明"测试样例由 allowlist 兜底放行"）
+    'gitleaks-config.toml',
     # 测试输出报告（含 mock key）
```

## 验证结果

| 验证项 | 结果 |
|---|---|
| `pre-commit run kwarg-conflict-scan --all-files` | **Passed**（HIGH 归零） |
| `pre-commit run scan-sensitive-data --all-files` | **Passed**（误报消除） |
| `pre-commit run --files`（本次 2 文件 × 全部 hooks） | **4/4 Passed**（kwarg / tool-index-sync / 敏感信息 / knowledge-cli-verify） |
| 单测回归（routing_observability + 知识引擎 7 文件） | **205 passed, 0 failed** |

## 变更文件

- `agent/orchestrator/routing_observability.py`（+8/-1）
- `scripts/scan_sensitive_data.py`（+3）

状态：未提交（待用户确认）。
