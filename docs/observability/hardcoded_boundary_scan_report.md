# 硬编码边界值扫描报告

> **生成时间**：2026-07-02 01:50:00
> **扫描工具**：`scripts/check_timedelta_overflow.py` + Grep 模式匹配
> **扫描范围**：`agent/` 目录下所有 `.py` 文件
> **扫描目的**：识别硬编码边界值，为统一配置化改造提供清单

---

## 一、扫描概览

| 类别 | 扫描模式 | 命中数 | 需配置化数 |
|------|----------|--------|------------|
| timedelta 溢出 | `timedelta(days=参数)` AST 分析 | 16 | 0（Phase 1 已修复） |
| 重试次数 | `max_retries/retry_count = N` | 8 | 3 |
| 超时值 | `timeout = N` | 13 | 5 |
| 容量/并发限制 | `max_workers/pool_size/max_size = N` | 43 | 6（排除测试文件） |
| 模块级边界常量 | `MAX_*/MIN_*/LIMIT_*/DEFAULT_*` | 13 | 4 |
| **合计** | — | **93** | **18** |

---

## 二、timedelta 溢出风险（16 处）

**扫描工具**：`scripts/check_timedelta_overflow.py`

| 风险等级 | 数量 | 状态 |
|----------|------|------|
| 高风险（函数参数传入） | 3 | ✅ 均已添加参数校验 |
| 中风险（变量/表达式） | 6 | ⚠ 需人工核实（实际安全） |
| 低风险（硬编码常量） | 7 | ✅ 无需处理 |

### 高风险详情（均已校验）

| 文件 | 行号 | 方法 | 校验状态 |
|------|------|------|----------|
| [data_analytics.py](file:///c:/Users/Administrator/agent/agent/data_analytics.py#L109) | 109 | `analyze_event_trends(days)` | ✅ 已校验 + 配置化 |
| [replay_storage.py](file:///c:/Users/Administrator/agent/agent/monitoring/replay_storage.py#L643) | 643 | `cleanup_old_records(days)` | ✅ 已校验 + 配置化 |
| [defect_tracker.py](file:///c:/Users/Administrator/agent/agent/quality/defect_tracker.py#L185) | 185 | `calculate_escape_rate(period_days)` | ✅ 已校验 + 配置化 |

---

## 三、硬编码重试次数（8 处）

| 文件 | 行号 | 硬编码值 | 需配置化 | 说明 |
|------|------|----------|----------|------|
| [error_handler.py](file:///c:/Users/Administrator/agent/agent/error_handler.py#L642) | 642 | `max_retries=3` | ⚠ 建议配置化 | RetryPolicy 统一管理 |
| [error_handler.py](file:///c:/Users/Administrator/agent/agent/error_handler.py#L696) | 696 | `max_retries=3` | ⚠ 建议配置化 | 同上（async 版本） |
| [reflection.py](file:///c:/Users/Administrator/agent/agent/cognitive/reflection.py#L61) | 61 | `MAX_RETRIES = 3` | ⚠ 建议配置化 | 认知循环重试 |
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L23) | 23 | `DEFAULT_MAX_RETRIES = 3` | ✅ 已有默认常量 | 可进一步提升到 Config |
| [browser_agent.py](file:///c:/Users/Administrator/agent/agent/web/browser_agent.py#L60) | 60 | `max_retries=2` | ❌ 保持现状 | 浏览器特定，非业务关键 |
| [prometheus.py](file:///c:/Users/Administrator/agent/agent/monitoring/prometheus.py#L242) | 242 | `max_retries=3` | ❌ 保持现状 | 监控上报，已有退避策略 |
| [prometheus.py](file:///c:/Users/Administrator/agent/agent/monitoring/prometheus.py#L290) | 290 | `max_retries=3` | ❌ 保持现状 | 同上 |
| [mcp_connector.py](file:///c:/Users/Administrator/agent/agent/tools/mcp_connector.py#L57) | 57 | `max_retries=2` | ❌ 保持现状 | MCP 协议特定 |

---

## 四、硬编码超时值（13 处）

| 文件 | 行号 | 硬编码值 | 需配置化 | 说明 |
|------|------|----------|----------|------|
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L22) | 22 | `DEFAULT_TIMEOUT = 30` | ⚠ 建议配置化 | 全局 HTTP 超时 |
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L24) | 24 | `DEFAULT_CONNECT_TIMEOUT = 10` | ⚠ 建议配置化 | 连接超时 |
| [loki.py](file:///c:/Users/Administrator/agent/agent/monitoring/loki.py#L96) | 96 | `timeout=10` | ⚠ 建议配置化 | Loki 日志推送 |
| [loki.py](file:///c:/Users/Administrator/agent/agent/monitoring/loki.py#L152) | 152 | `timeout=30` | ⚠ 建议配置化 | 同上 |
| [alert_notifier.py](file:///c:/Users/Administrator/agent/agent/monitoring/alert_notifier.py#L350) | 350 | `timeout=30` | ⚠ 建议配置化 | 告警通知 |
| [cognitive/logging_integration.py](file:///c:/Users/Administrator/agent/agent/cognitive/logging_integration.py#L244) | 244 | `timeout=10` | ❌ 保持现状 | 日志集成，非业务关键 |
| [dependency_manager.py](file:///c:/Users/Administrator/agent/agent/extensions/dependency_manager.py#L201) | 201 | `timeout=120` | ❌ 保持现状 | 依赖安装，合理 |
| [self_healer.py](file:///c:/Users/Administrator/agent/agent/monitoring/self_healer.py#L408) | 408 | `timeout=60` | ❌ 保持现状 | 自愈逻辑 |
| [self_healer.py](file:///c:/Users/Administrator/agent/agent/monitoring/self_healer.py#L661) | 661 | `timeout=5` | ❌ 保持现状 | 同上 |
| [search.py](file:///c:/Users/Administrator/agent/agent/monitoring/search.py#L219) | 219 | `timeout=30` | ❌ 保持现状 | 搜索聚合 |
| [crawler_control.py](file:///c:/Users/Administrator/agent/agent/web/crawler_control.py#L266) | 266 | `timeout = 30` | ❌ 保持现状 | 爬虫控制 |

---

## 五、硬编码容量/并发限制（6 处，排除测试文件）

| 文件 | 行号 | 硬编码值 | 需配置化 | 说明 |
|------|------|----------|----------|------|
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L25) | 25 | `DEFAULT_POOL_SIZE = 20` | ⚠ 建议配置化 | HTTP 连接池 |
| [multi_level_cache.py](file:///c:/Users/Administrator/agent/agent/caching/multi_level_cache.py#L22) | 22 | `l1_max_size=1000` | ⚠ 建议配置化 | L1 缓存容量 |
| [short_term_memory.py](file:///c:/Users/Administrator/agent/agent/memory/short_term_memory.py#L60) | 60 | `max_size=100` | ⚠ 建议配置化 | 短期记忆容量 |
| [tracing_cache.py](file:///c:/Users/Administrator/agent/agent/monitoring/tracing_cache.py#L322) | 322 | `max_size=4096` | ⚠ 建议配置化 | 追踪上下文缓存 |
| [tracing_cache.py](file:///c:/Users/Administrator/agent/agent/monitoring/tracing_cache.py#L325) | 325 | `max_size=2048` | ⚠ 建议配置化 | Span 缓存 |
| [tracing_cache.py](file:///c:/Users/Administrator/agent/agent/monitoring/tracing_cache.py#L328) | 328 | `pool_size=500` | ⚠ 建议配置化 | Span 对象池 |

---

## 六、模块级边界常量（13 处）

| 文件 | 行号 | 常量名 | 值 | 需配置化 | 说明 |
|------|------|--------|-----|----------|------|
| [data_analytics.py](file:///c:/Users/Administrator/agent/agent/data_analytics.py#L21) | 21 | `MAX_ANALYZE_DAYS` | 36500 | ✅ 已配置化 | Task 1 完成 |
| [diff_tools.py](file:///c:/Users/Administrator/agent/agent/diff_tools.py#L22) | 22 | `MAX_DIFF_FILE_SIZE` | 10MB | ❌ 保持现状 | 文件大小限制，合理 |
| [llm_monitor.py](file:///c:/Users/Administrator/agent/agent/llm_monitor.py#L18) | 18 | `MAX_RECORDS` | 500 | ⚠ 建议配置化 | 环形缓冲区大小 |
| [search_aggregator.py](file:///c:/Users/Administrator/agent/agent/search_aggregator.py#L44) | 44 | `MAX_SCORE_CAP` | 1.5 | ❌ 保持现状 | 评分上限，业务常量 |
| [task_scheduler.py](file:///c:/Users/Administrator/agent/agent/task_scheduler.py#L39) | 39 | `DEFAULT_CHECK_INTERVAL` | 10 | ⚠ 建议配置化 | 调度检查间隔 |
| [task_scheduler.py](file:///c:/Users/Administrator/agent/agent/task_scheduler.py#L41) | 41 | `MAX_HISTORY_LINES` | 1000 | ⚠ 建议配置化 | 历史行数上限 |
| [task_scheduler.py](file:///c:/Users/Administrator/agent/agent/task_scheduler.py#L43) | 43 | `MAX_HEARTBEAT_HISTORY` | 1440 | ⚠ 建议配置化 | 心跳历史条数 |
| [file_tools.py](file:///c:/Users/Administrator/agent/agent/tools/file_tools.py#L90) | 90 | `DEFAULT_MAX_READ_SIZE` | 10MB | ❌ 保持现状 | 文件读取限制 |
| [file_tools.py](file:///c:/Users/Administrator/agent/agent/tools/file_tools.py#L92) | 92 | `DEFAULT_MAX_WRITE_SIZE` | 50MB | ❌ 保持现状 | 文件写入限制 |
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L22) | 22 | `DEFAULT_TIMEOUT` | 30 | ⚠ 建议配置化 | 见第四节 |
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L23) | 23 | `DEFAULT_MAX_RETRIES` | 3 | ⚠ 建议配置化 | 见第三节 |
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L24) | 24 | `DEFAULT_CONNECT_TIMEOUT` | 10 | ⚠ 建议配置化 | 见第四节 |
| [http_client.py](file:///c:/Users/Administrator/agent/agent/web/http_client.py#L25) | 25 | `DEFAULT_POOL_SIZE` | 20 | ⚠ 建议配置化 | 见第五节 |

---

## 七、统一改造优先级清单

### P1 — 高优先级（影响生产稳定性）

| # | 模块 | 硬编码项 | 当前值 | 建议配置路径 | 工时 |
|---|------|----------|--------|--------------|------|
| 1 | `http_client.py` | DEFAULT_TIMEOUT | 30 | `http.timeout_sec` | 0.5h |
| 2 | `http_client.py` | DEFAULT_MAX_RETRIES | 3 | `http.max_retries` | 0.5h |
| 3 | `http_client.py` | DEFAULT_CONNECT_TIMEOUT | 10 | `http.connect_timeout_sec` | 0.5h |
| 4 | `http_client.py` | DEFAULT_POOL_SIZE | 20 | `http.pool_size` | 0.5h |
| 5 | `error_handler.py` | max_retries=3 (×2) | 3 | `retry.default_max_retries` | 1h |
| 6 | `reflection.py` | MAX_RETRIES | 3 | `cognitive.reflection_max_retries` | 0.5h |

### P2 — 中优先级（影响性能调优）

| # | 模块 | 硬编码项 | 当前值 | 建议配置路径 | 工时 |
|---|------|----------|--------|--------------|------|
| 7 | `multi_level_cache.py` | l1_max_size | 1000 | `cache.l1_max_size` | 0.5h |
| 8 | `tracing_cache.py` | max_size (×2) | 4096/2048 | `tracing.context_cache_size` | 1h |
| 9 | `short_term_memory.py` | max_size | 100 | `memory.stm_max_size` | 0.5h |
| 10 | `task_scheduler.py` | 3 个常量 | 10/1000/1440 | `scheduler.*` | 1h |
| 11 | `llm_monitor.py` | MAX_RECORDS | 500 | `llm_monitor.max_records` | 0.5h |
| 12 | `loki.py` | timeout (×2) | 10/30 | `loki.timeout_sec` | 0.5h |
| 13 | `alert_notifier.py` | timeout | 30 | `alert.timeout_sec` | 0.5h |

### P3 — 低优先级（可延后）

其余 37 处硬编码值（测试文件中的 max_workers/max_size 等）无需配置化，保持现状。

---

## 八、改造建议

### 8.1 统一改造模式

参照 Task 1 的 `time_window.max_analyze_days` 模式：

1. 在 `observability_config.py` 的 `OBSERVABILITY_VALIDATION_RULES` 中新增 `ValidationRule`
2. 使用 `_range_validator(min, max)` 限定合理范围
3. 新增便捷函数（如 `get_http_timeout()`）
4. 修改业务模块从 Config 读取
5. 保留 `DEFAULT_*` 常量作为向后兼容别名

### 8.2 改造批次

| 批次 | 范围 | 预计工时 | 依赖 |
|------|------|----------|------|
| 批次 1 | P1 第 1~6 项（HTTP + 重试） | 3.5h | 无 |
| 批次 2 | P2 第 7~9 项（缓存 + 记忆） | 2h | 无 |
| 批次 3 | P2 第 10~13 项（调度 + 监控） | 2.5h | 无 |
| **合计** | 13 项配置化 | **8h（1 个工作日）** | — |

### 8.3 验证方式

每批次改造后：
1. 运行 `python scripts/check_timedelta_overflow.py --target agent/` 确认无回归
2. 运行相关模块的单元测试
3. 修改 Config 值验证行为变化
4. 恢复默认值

---

## 九、附录：扫描命令

```bash
# timedelta 溢出扫描
python scripts/check_timedelta_overflow.py --target agent/ --json

# 重试次数扫描
grep -rn "(max_retries|max_retry|retry_count)\s*[:=]\s*\d+" agent/

# 超时值扫描
grep -rn "^\s*(DEFAULT_)?\(timeout\|TIMEOUT\|_timeout\)\s*[:=]\s*\d+" agent/

# 容量限制扫描
grep -rn "(max_workers|pool_size|max_size|max_concurrency)\s*[:=]\s*\d+" agent/

# 模块级常量扫描
grep -rn "^\(MAX_\|MIN_\|LIMIT_\|DEFAULT_\)[A-Z_]\+\s*=\s*\d+" agent/
```
