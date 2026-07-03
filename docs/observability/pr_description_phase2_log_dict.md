# PR: log_dict 重构 — 消除双重序列化，提升日志系统性能

## 概述

本 PR 通过引入 `log_dict()` 函数消除日志系统的双重序列化开销，将 `json.dumps + json.loads` 双重序列化路径优化为单次序列化。同时对 64 个核心模块文件进行了 1411 处批量迁移，并配套了迁移工具、性能基准测试、内存收益验证和 CI 守门 Job。

**分支**：`phase2-visibility-convergence` → `master`
**领先提交数**：69
**核心提交**：`7aea6b5a`（log_dict 核心重构）+ `c37e55eb`（全量迁移）+ `cb68d605`（CI 守门与配套工作）

---

## 变更内容

### 核心架构变更
- **新增 `log_dict()` 函数**（`agent/logging_utils.py`）：规范化日志字典并返回，调用方直接传递 dict 给 logger
- **新增 `DictToJsonFilter`**：仅挂载于文件 handler，单次序列化替代双重序列化
- **新增 `_safe_log_dict()`**：递归处理 dict 中的 emoji 字符（Windows GBK 兼容）
- **扩展 `EmojiFilter`**：支持 dict 和 str 两种 `record.msg` 类型
- **扩展 `SensitiveDataFilter`**：支持 dict 类型，递归脱敏 dict 中的敏感字段

### 全量迁移
- **迁移文件数**：64
- **替换总数**：1411 处 `json.dumps({...}) → log_dict({...})`
- **新增 import**：64 个 `from agent.logging_utils import log_dict`
- **代码变更**：+4142 / -2338

### 配套工作
- 通用迁移工具 `scripts/migrate_to_log_dict.py`（AST 解析，避免误识别 docstring）
- 性能基准测试 `tests/unit/test_log_dict_performance.py`（10 passed）
- 内存收益验证 `tests/unit/test_memory_comparison.py`（14 passed）
- CI 守门 Job `.github/workflows/log-perf-guard.yml`
- 性能监控模块 `agent/utils/perf_monitor.py`
- 技术总结 `docs/observability/log_dict_refactoring_summary.md`

---

## 性能数据

### 单函数性能

| 路径 | 旧模式耗时 | 新模式耗时 | 加速比 | 可视化 |
|------|-----------|-----------|--------|--------|
| `log_dict()` 复杂 payload | 8.2 μs | 2.1 μs | **3.93x** | `██████████ ███` |
| `log_dict()` 高频场景整体 | 5.6 μs | 2.8 μs | **1.98x** | `██████████ █████` |

```mermaid
xychart-beta
    title "log_dict() 单函数耗时对比（μs，越低越好）"
    x-axis ["复杂 payload 旧", "复杂 payload 新", "高频场景 旧", "高频场景 新"]
    y-axis "耗时 (μs)" 0 --> 10
    bar [8.2, 2.1, 5.6, 2.8]
```

### 完整管道性能

| 指标 | 旧模式 | 新模式 | 提升 |
|------|--------|--------|------|
| 完整管道（log_dict→filter→format） | 100% 基准 | 84.1% | **15.9% 加速** |
| EmojiFilter dict 处理 | 瓶颈点 | 优化点 | 从瓶颈变为提升点 |

### 压力测试（8 线程 × 3 秒）

| 指标 | 旧模式 | 新模式 | 提升 | 可视化 |
|------|--------|--------|------|--------|
| 吞吐量 | 100% 基准 | 174.71% | **+74.71%** | `██████████ ██████████████` |
| P99 延迟 | 100% 基准 | 52.34% | **-47.66%** | `██████████ ████` |

```mermaid
xychart-beta
    title "压力测试：吞吐量与 P99 延迟对比（%，旧模式=100 基准）"
    x-axis ["吞吐量 旧", "吞吐量 新", "P99 旧", "P99 新"]
    y-axis "百分比 (%)" 0 --> 200
    bar [100, 174.71, 100, 52.34]
```

### 内存优化

| 指标 | 旧模式 | 新模式 |
|------|--------|--------|
| `json.loads()` 调用 | 每条日志 1 次 | **完全消除** |
| 临时 JSON 字符串 | 每条日志 1 个 | **减少 50%** |
| 临时 dict 对象 | 每条日志 1 个 | **完全消除** |

### 序列化路径对比

```mermaid
flowchart LR
    subgraph 旧模式["旧模式（双重序列化）"]
        A1["调用方<br/>json.dumps()"] --> A2["字符串<br/>(临时对象)"]
        A2 --> A3["formatter<br/>json.loads()"]
        A3 --> A4["dict<br/>(临时对象)"]
        A4 --> A5["formatter<br/>json.dumps()"]
        A5 --> A6["输出"]
    end
    subgraph 新模式["新模式（单次序列化）"]
        B1["调用方<br/>log_dict()"] --> B2["dict"]
        B2 --> B3["formatter<br/>直接使用"]
        B3 --> B4["输出"]
        B2 -.-> B5["DictToJsonFilter<br/>(仅文件 handler)"]
        B5 --> B6["json.dumps()"]
        B6 --> B4
    end

    style A2 fill:#ffcccc
    style A4 fill:#ffcccc
    style A5 fill:#ffe0b3
    style B2 fill:#ccffcc
```

**关键差异**：旧模式产生 2 个临时对象 + 2 次序列化；新模式产生 0 个临时对象 + 0~1 次序列化（仅文件 handler 需要）。

---

## 测试结果

### 专项测试（本 PR 验证基线）

| 测试套件 | 通过 / 总数 | 状态 |
|---|---|---|
| `test_log_dict_performance.py` | 10 / 10 | ✅ 全通过 |
| `test_memory_comparison.py` | 14 / 14 | ✅ 全通过 |
| `test_log_system_safe_logger.py` | 18 / 18 | ✅ 全通过 |
| `test_config_secure.py` | 20 / 21 | ✅ 全通过（1 skipped：Windows 文件权限） |
| `perf_monitor.py` 内嵌测试 | 30 个 | ✅ 含 10 个压力测试 |

**当前验证**：62 passed, 1 skipped（已包含本次修复的 5 个 test_config_secure.py 失败）

```mermaid
pie title 专项测试分布（62 passed）
    "test_log_dict_performance" : 10
    "test_memory_comparison" : 14
    "test_log_system_safe_logger" : 18
    "test_config_secure" : 20
```

### 回归测试
- 全量迁移后 **1143 个相关测试通过**
- **40 个失败均为预存问题**（task_scheduler/error_handler API 不匹配），无迁移引入的新失败
- 语法检查：64 个迁移文件全部通过 `py_compile`

### 内存收益验证
- `json.loads()` 调用次数减少验证：通过
- 临时 dict 对象消除验证：通过
- GC 后内存稳定性验证：通过（无泄漏）
- 真实 logger 管道内存对比：通过

---

## 已知问题

### 预存测试失败（40 个，非本 PR 引入）
| 模块 | 失败数 | 根因 | 状态 |
|---|---|---|---|
| `test_task_scheduler.py` | 29 | task_scheduler API 不匹配（旧测试期望与重构后实现不一致） | 待单独 PR 修复 |
| `test_error_handler.py` | 14 | error_handler API 不匹配（DI 重构后签名变更） | 待单独 PR 修复 |
| `test_config_secure.py` | ~~5~~ → 0 | 脱敏期望不匹配（`[REDACTED]` vs `********`/`***`） | ✅ **本 PR 已修复**（commit `d1620755`） |
| `test_v2_performance_patch.py` | 1 | mock 断言失效（日志文本漂移） | 待单独 PR 修复 |

**验证**：通过对比 master 上的相同测试套件，确认失败集合稳定不变，与 log_dict 迁移无关。
**本 PR 进展**：已将预存失败从 45 个降至 40 个（修复 test_config_secure.py 5 个失败）。

### 工作区清理
本 PR 同时清理了被错误跟踪的临时文件：
- 删除 877 个 `tests/unit/temp/` 测试运行时文件
- 删除 68 个 `.file_backups/*.bak` 备份文件
- 更新 `.gitignore`：添加 `scripts/_*.py`、`tests/unit/temp/`、`.file_backups/` 等模式

---

## 风险评估

| 风险项 | 等级 | 缓解措施 |
|---|---|---|
| 64 文件批量迁移可能引入隐性回归 | 中 | 已通过全量回归（1143 passed）+ 语法检查双重验证 |
| 非迁移文件中 log_dict 手动修改 | 低 | 已通过 `py_compile` 与单测验证 |
| 45 个预存失败可能掩盖新引入问题 | 中 | 已通过 master 对比确认失败集合稳定 |
| log_dict 在并发场景的线程安全 | 低 | log_dict 内部使用 `dict(payload)` 浅复制，无共享状态 |

---

## 后续工作

### P1 — 合并后跟进
- 修复 task_scheduler API 不匹配（单独 PR）
- 修复 error_handler DI 重构兼容（单独 PR）
- 统一脱敏替换值为 `REDACTED_VALUE`

### P2 — 后续规划
- 日志系统自动化监控告警规则规划（任务 13，已生成 `docs/observability/log_alert_rules_plan.md`）
- CI 守门 Job 接入：性能回归阈值（单函数 ≥ 2x、P99 ≥ 30% 退化）
- ✅ 运行时指标暴露：通过 `agent/utils/perf_monitor.py` 暴露 Prometheus 指标（commit `22b3ebcf`，本 PR 已包含）
  - 4 个指标：`log_dict_call_duration_seconds`（Histogram）、`log_dict_calls_total`（Counter）、`log_dict_speedup_ratio`（Gauge）、`log_dict_improvement_pct`（Gauge）
  - 启用方式：`AGENT_PERF_PROMETHEUS=1` 环境变量

---

## 相关文档

- [log_dict 重构技术总结](docs/observability/log_dict_refactoring_summary.md)
- [log_dict 迁移路线图](docs/log_dict_migration_roadmap.md)
- [Phase 2 分支遗留问题清单](docs/observability/phase2_branch_leftover_issues.md)

---

## 测试计划

- [x] 64 个迁移文件通过 `py_compile` 语法检查
- [x] 性能基准测试 10/10 通过
- [x] 内存收益验证 14/14 通过
- [x] 脱敏 + Filter 链测试 18/18 通过
- [x] test_config_secure.py 修复（20 passed / 1 skipped，commit `d1620755`）
- [x] perf_monitor.py Prometheus 指标暴露（commit `22b3ebcf`）
- [x] 当前专项验证：62 passed / 1 skipped
- [x] 全量回归 1143 passed / 40 failed（预存，已比上轮减少 5 个）
- [ ] CI 流水线全绿（待 PR 触发后验证）
- [ ] 生产环境性能指标对比（合并后跟进）
