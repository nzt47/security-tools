# 合并报告：tracing 测试导入路径修复 + CI 真实阻断恢复

- **日期**: 2026-08-09
- **分支**: `develop`
- **提交**: `d140153a`
- **PR 关联**: PR #7（已合并，merge commit `20277571`）——本次修复为其收尾验证

---

## 1. 修复内容

### 1.1 根因

CI 上 `observability-unit-tests` 失败，`ImportError`：

```
ImportError: cannot import name 'record_request_metrics' from 'agent.monitoring.tracing'
ImportError: cannot import name 'get_logger_with_context' from 'agent.monitoring.tracing'
```

**根因**：`tests/unit/test_tracing_middleware.py` 在测试方法内部从 `agent.monitoring.tracing`
导入 `record_request_metrics` 和 `get_logger_with_context`，但这两个函数实际定义在
`agent.server_routes.tracing_middleware`（作为降级 stub），从未在 `tracing.py` 中定义。

### 1.2 修复（方案 A）

[test_tracing_middleware.py](file:///c:/Users/Administrator/agent/tests/unit/test_tracing_middleware.py) 两处导入路径修正：

| 位置 | 修复前 | 修复后 |
|------|--------|--------|
| `TestMetricsIntegration.test_record_request_metrics` | `from agent.monitoring.tracing import record_request_metrics` | `from agent.server_routes.tracing_middleware import record_request_metrics` |
| `TestLoggingIntegration.test_get_logger_with_context` | `from agent.monitoring.tracing import get_logger_with_context` | `from agent.server_routes.tracing_middleware import get_logger_with_context` |

### 1.3 CI 配置恢复

[observability-ci.yml](file:///c:/Users/Administrator/agent/.github/workflows/observability-ci.yml) 中
`observability-unit-tests` job 的 3 处临时 `continue-on-error: true` 已移除：

- `运行可观测性单元测试`（原 line 335）
- `生成可观测性测试覆盖率报告`（原 line 366）
- `运行优化脚本单元测试`（原 line 428）

恢复"单元测试失败即阻断下游集成测试与 E2E"的真实语义。
其他 `continue-on-error: true`（混沌测试/趋势报告等既有配置）未改动。

---

## 2. CI 验证结果（run `31315181479`，commit `d140153a`）

| Job | 状态 |
|-----|------|
| 可观测性单元测试 (3.10) | ✅ success（**真实通过**，无 continue-on-error 掩盖） |
| 可观测性单元测试 (3.11) | ✅ success |
| 可观测性单元测试 (3.12) | ✅ success |
| 可观测性集成测试 | ✅ success |
| 可观测性端到端验证 | ✅ success |
| 可观测性质量门禁 | ❌ failure（**存量问题**，与本次修复无关） |

本地验证：`pytest tests/unit/test_tracing_middleware.py` → **9 passed, 0 failed**。

---

## 3. 合并条件评估

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 单元测试 | ✅ 通过 | 3 个 Python 版本全部真实通过 |
| 集成测试 | ✅ 通过 | |
| E2E 验证 | ✅ 通过 | |
| 代码冲突 | ✅ 无 | PR #7 已合并，本次提交为独立收尾 |
| 质量门禁 | ⚠️ 存量失败 | 覆盖率 22.40% < 60% 阈值（存量问题，非本次引入） |

**结论**：本次修复可以进入合并流程。质量门禁失败为存量覆盖率问题
（详见下方覆盖率分析），不构成本次修复的合并阻塞。

---

## 4. 存量问题：覆盖率分析（22.40%）

质量门禁读取 `coverage.xml` 的 line-rate，当前值 22.40%（CI 口径），
低于 60% 阈值。全项目 6-shard 合并后整体 line-rate 为 31.27%。

### 4.1 最大拖累：`scripts/` 目录

- **386 个模块，平均覆盖率仅 3.7%**
- **363 个模块零覆盖**
- 典型零覆盖大文件：`check_boundary_coverage.py`（515 行）、
  `generate_visibility_trend.py`（474 行）、`run_chaos_test.py`（409 行）等
- 这些是运维/验证类脚本，大量为一次性工具，未纳入测试

### 4.2 `agent/` 包内零覆盖/低覆盖模块

| 模块 | 覆盖率 | 行数 |
|------|--------|------|
| `subagent/barrier.py` | 0% | 112 |
| `subagent/summarizer.py` | 0% | 155 |
| `subagent/observability.py` | 0% | 30 |
| `server_routes/routes_chat.py` | 0% | 339 |
| `prompt_manager/`（6 模块） | 0% | — |
| `preflight/`（3 模块） | 0% | — |
| `handoff/`（2 模块） | 0% | — |
| `task_planner/`（8 模块） | 12.5% | — |

### 4.3 相对健康模块

- `knowledge/`：92.2%
- `model_router/`：90.8%
- `observability/`：83.9%
- `monitoring/`：56.5%（29 模块，3 个零覆盖）
- `skills_mgmt/`：59.8%

### 4.4 结论

覆盖率低的主要原因是：
1. `scripts/` 大量运维脚本零测试（占行数大头）
2. `agent/` 若干核心子包（prompt_manager、preflight、handoff、task_planner）缺测试
3. `monitoring/tracing.py`（751 行）虽有测试覆盖，但仍有提升空间

提升覆盖率的建议优先级：`scripts/` 工具脚本 > `prompt_manager`/`preflight`/`handoff` > `tracing.py` 补测。
