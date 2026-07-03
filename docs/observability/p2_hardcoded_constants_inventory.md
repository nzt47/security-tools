# P2 硬编码常量清单 — 缓存容量与调度器常量

> 生成时间：2026-07-03
> 扫描范围：`agent/` 目录下缓存、调度器、监控告警相关模块
> 用途：为 Phase 3 后续迭代（Task 4/5）提供改造候选项清单

## 一、扫描结果总览

| 优先级 | 模块数 | 配置项数 | 说明 |
|--------|--------|----------|------|
| P2-高 | 2 | 4 | 调度器核心参数（影响任务执行与心跳检测） |
| P2-中 | 2 | 5 | 追踪缓存与历史保留（影响内存占用与可观测性） |
| P2-低 | 3 | 5 | 外部服务超时与重试（影响告警/Loki/短期记忆） |
| **合计** | **7** | **14** | — |

## 二、详细清单

### 2.1 P2-高：调度器核心参数（task_scheduler.py）

| 常量名 | 当前值 | 建议配置路径 | 建议范围 | 说明 |
|--------|--------|-------------|----------|------|
| `DEFAULT_CHECK_INTERVAL` | 10 | `scheduler.check_interval_sec` | 1-300 | tick 检查间隔（秒），影响任务调度精度 |
| `COMMAND_TIMEOUT` | 300 | `scheduler.command_timeout_sec` | 10-3600 | 系统命令执行超时（秒），防止僵尸进程 |
| `HEARTBEAT_INTERVAL` | 60 | `scheduler.heartbeat_interval_sec` | 10-600 | 心跳检测间隔（秒），影响健康检查灵敏度 |
| `MAX_HISTORY_LINES` | 1000 | `scheduler.max_history_lines` | 100-10000 | 执行历史最大行数，影响磁盘占用 |

**改造风险**：低。常量仅在 `task_scheduler.py` 内部使用，改为 Config 读取后支持热加载。

### 2.2 P2-中：追踪缓存容量（tracing_cache.py）

| 常量名 | 当前值 | 建议配置路径 | 建议范围 | 说明 |
|--------|--------|-------------|----------|------|
| `context_cache.max_size` | 4096 | `tracing_cache.context_max_size` | 256-16384 | 上下文缓存容量，影响追踪深度 |
| `span_cache.max_size` | 2048 | `tracing_cache.span_max_size` | 128-8192 | Span 缓存容量，影响并发追踪数 |
| `span_pool.pool_size` | 500 | `tracing_cache.span_pool_size` | 50-2000 | Span 对象池大小，影响 GC 压力 |

**改造风险**：中。缓存容量调整可能影响追踪精度和内存占用，需配合混沌测试验证。

### 2.3 P2-中：多级缓存容量（multi_level_cache.py）

| 常量名 | 当前值 | 建议配置路径 | 建议范围 | 说明 |
|--------|--------|-------------|----------|------|
| `l1_max_size` | 1000 | `cache.l1_max_size` | 100-10000 | L1 内存缓存最大条目数 |

**改造风险**：低。构造函数默认参数，改为 Config 读取后向后兼容。

### 2.4 P2-低：短期记忆容量（short_term_memory.py）

| 常量名 | 当前值 | 建议配置路径 | 建议范围 | 说明 |
|--------|--------|-------------|----------|------|
| `max_size` | 100 | `memory.short_term_max_size` | 10-1000 | 短期记忆最大条目数 |
| `default_ttl` | 300 | `memory.short_term_default_ttl_sec` | 60-3600 | 短期记忆默认 TTL（秒） |

**改造风险**：低。构造函数默认参数。

### 2.5 P2-低：Loki 日志查询（loki.py）

| 常量名 | 当前值 | 建议配置路径 | 建议范围 | 说明 |
|--------|--------|-------------|----------|------|
| `timeout`（查询） | 30 | `loki.query_timeout_sec` | 5-120 | Loki 查询超时 |
| `timeout`（推送） | 10 | `loki.push_timeout_sec` | 5-60 | Loki 推送超时 |
| `limit`（默认） | 100 | `loki.default_query_limit` | 10-1000 | 默认查询条数上限 |

**改造风险**：低。外部服务超时参数。

### 2.6 P2-低：告警通知器（alert_notifier.py）

| 常量名 | 当前值 | 建议配置路径 | 建议范围 | 说明 |
|--------|--------|-------------|----------|------|
| `timeout`（SMTP/HTTP） | 30 | `alert_notifier.timeout_sec` | 5-120 | 告警发送超时 |
| `base_delay` | 1.0 | `alert_notifier.retry_base_delay` | 0.1-10 | 重试基础退避延迟（秒） |
| `_max_history` | 100 | `alert_notifier.max_history` | 10-1000 | 告警历史保留条数 |

**改造风险**：低。外部服务参数。

## 三、改造优先级建议

### 第一批（Phase 3 Task 4 — 缓存容量配置化）
1. `cache.l1_max_size`（multi_level_cache.py）
2. `tracing_cache.context_max_size` / `span_max_size` / `span_pool_size`（tracing_cache.py）

**理由**：缓存容量直接影响内存占用，配置化后可根据部署环境动态调整。

### 第二批（Phase 3 Task 5 — 调度器常量配置化）
3. `scheduler.check_interval_sec`
4. `scheduler.command_timeout_sec`
5. `scheduler.heartbeat_interval_sec`
6. `scheduler.max_history_lines`
7. `scheduler.max_heartbeat_history`

**理由**：调度器参数影响任务执行精度和资源占用，是运维调优的高频需求。

### 第三批（后续迭代 — 外部服务参数）
8. `loki.query_timeout_sec` / `push_timeout_sec` / `default_query_limit`
9. `alert_notifier.timeout_sec` / `retry_base_delay` / `max_history`
10. `memory.short_term_max_size` / `short_term_default_ttl_sec`

**理由**：外部服务参数通常在部署时一次性配置，热加载需求较低。

## 四、扫描覆盖范围

### 已扫描模块（7 个）
- `agent/caching/multi_level_cache.py` ✓
- `agent/monitoring/tracing_cache.py` ✓
- `agent/task_scheduler.py` ✓
- `agent/memory/short_term_memory.py` ✓
- `agent/llm_monitor.py` ✓（无硬编码值）
- `agent/monitoring/loki.py` ✓
- `agent/monitoring/alert_notifier.py` ✓

### 未扫描但建议后续检查
- `agent/monitoring/metrics.py`（指标采集缓冲区）
- `agent/monitoring/performance.py`（性能采样器）
- `agent/caching/l2_cache.py`（L2 磁盘缓存）
- `agent/disaster_recovery.py`（灾备恢复参数）
