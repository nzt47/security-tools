# 未埋点模块自动化埋点建议报告

**生成时间**：2026-06-27
**未埋点模块数**：17
**当前 track_event_coverage**：37.0%（10/27 模块已埋点）
**目标 track_event_coverage**：≥70%（19/27 模块需埋点）
**检测脚本**：`scripts/visibility_report.py`（`_calc_track_coverage` 方法，匹配 `trackEvent|BusinessMetricsCollector|track\(`）
**埋点 API**：`from agent.monitoring.business_metrics import get_business_metrics_collector`

> ⚠️ **API 说明**：本报告代码示例基于代码库真实 API。
> - 获取收集器：`get_business_metrics_collector()`（返回全局单例 `BusinessMetricsCollector` 实例）
> - 通用记录方法：`_increment_counter(metric_name, labels)` / `_observe_histogram(metric_name, labels, value)` / `_set_gauge(metric_name, labels, value)`
> - 领域专用方法：`record_interaction` / `record_tool_call` / `record_task` / `record_planning_task` 等
> - 所有埋点调用必须包裹 `try/except`，埋点失败不得影响主流程（与 `circuit_breaker.py` 现有模式一致）

---

## 一、概览

| # | 模块 | 功能 | 推荐埋点数 | 优先级 |
|---|------|------|-----------|--------|
| 1 | audit | 审计日志追加写入与查询 | 2 | 高 |
| 2 | human_in_the_loop | 高风险操作人工确认流程 | 3 | 高 |
| 3 | quality | 缺陷追踪与质量度量 | 2 | 高 |
| 4 | subagent | 分身生命周期管理（创建/销毁/热更新） | 3 | 高 |
| 5 | task_planner | 任务分解为子任务 DAG 与执行 | 3 | 高 |
| 6 | workflow_engine | 基于规则的工作流匹配与执行 | 2 | 高 |
| 7 | caching | 多级缓存（L1 内存 + L2 磁盘）读写与统计 | 3 | 中 |
| 8 | extensions | 扩展市场安装/卸载与安全检查 | 3 | 中 |
| 9 | network | 网络配置管理（LLM/搜索/MCP）与加密存储 | 2 | 中 |
| 10 | prompt_manager | 提示词注册/更新/版本管理 | 2 | 中 |
| 11 | p6 | P6 快照性能监控（保存/加载耗时） | 2 | 中 |
| 12 | log_system | 日志分析引擎与日志看板 API | 2 | 中 |
| 13 | lazy_loader | 并行模块预加载器（基础设施） | 2 | 低 |
| 14 | observability | 架构规则校验与追踪存储 | 2 | 低 |
| 15 | tests | 测试用例集合（非业务模块） | 0 | 低 |
| 16 | utils | 序列化/兼容性/决策日志工具集 | 2 | 低 |
| 17 | data | 纯 JSON 数据目录（无 .py 文件） | 0 | 特殊 |

**优先级标准**：
- 高：核心业务模块，直接影响用户体验或关键流程
- 中：支撑模块，影响系统性能与可配置性
- 低：辅助模块，影响可观测性完整性
- 特殊：非代码模块，需调整检测逻辑而非埋点

---

## 二、详细建议

### 2.1 audit 模块

**功能**：审计日志记录（Append-only 追加写入，含 trace_id/操作类型/输入输出哈希）
**当前状态**：未埋点（已确认 `audit/logger.py` 无 `trackEvent|BusinessMetricsCollector|track(` 调用）
**推荐埋点文件**：`agent/audit/logger.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `AuditLogger.log` | `yunshu_audit_record` | counter | 审计记录写入次数，按 status 维度统计成功/失败 |
| 2 | `AuditLogger.query` | `yunshu_audit_query_duration_seconds` | histogram | 审计查询耗时，用于排查慢查询 |

**代码示例**：
```python
# 在 agent/audit/logger.py 顶部添加导入
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 AuditLogger.log() 方法末尾（logger.debug 之后）添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_audit_record', {'status': status, 'action': action})
except Exception as _e:
    logger.debug("审计埋点失败: %s", _e)

# 在 AuditLogger.query() 方法返回前添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._observe_histogram('yunshu_audit_query_duration_seconds',
                                {'action': action or 'all'}, _query_elapsed_seconds)
except Exception as _e:
    logger.debug("审计查询埋点失败: %s", _e)
```

---

### 2.2 human_in_the_loop 模块

**功能**：高风险操作人工确认流程（风险评级、同步/异步审批、超时降级）
**当前状态**：未埋点（`hitl.py` 有结构化日志但无 `BusinessMetricsCollector` 调用）
**推荐埋点文件**：`agent/human_in_the_loop/hitl.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `HITLManager.request_approval` | `yunshu_hitl_approval_total` | counter | 审批请求次数，按 risk_level/result 维度统计 |
| 2 | `HITLManager.assess` | `yunshu_hitl_risk_assessment_total` | counter | 风险评级次数，按 risk_level 维度统计 |
| 3 | `HITLManager.request_approval` | `yunshu_hitl_approval_duration_seconds` | histogram | 审批耗时（含等待时间），衡量用户响应速度 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 request_approval() 返回前（_cleanup_request 之后）添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_hitl_approval_total',
                                {'risk_level': risk_level.value, 'result': status.value})
    _metrics._observe_histogram('yunshu_hitl_approval_duration_seconds',
                                {'risk_level': risk_level.value}, duration_ms / 1000)
except Exception as _e:
    logger.debug("HITL 埋点失败: %s", _e)
```

---

### 2.3 quality 模块

**功能**：缺陷追踪（缺陷登记、状态流转、根因记录）
**当前状态**：未埋点（`quality/defect_tracker.py` 无埋点调用）
**推荐埋点文件**：`agent/quality/defect_tracker.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `DefectTracker.add_defect` | `yunshu_quality_defect_created` | counter | 缺陷登记次数，按 severity/defect_type 维度统计 |
| 2 | `DefectTracker.update_defect` | `yunshu_quality_defect_updated` | counter | 缺陷状态流转次数，按 from_status/to_status 维度统计 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 add_defect() 方法保存后添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_quality_defect_created',
                                {'severity': severity.value, 'defect_type': defect_type.value})
except Exception as _e:
    pass  # 埋点失败不影响缺陷登记主流程
```

---

### 2.4 subagent 模块

**功能**：分身生命周期管理（创建、销毁、热更新、超时 GC）
**当前状态**：未埋点（`subagent/lifecycle.py` 无埋点调用）
**推荐埋点文件**：`agent/subagent/lifecycle.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `SubagentLifecycleManager.create` | `yunshu_subagent_created` | counter | 分身创建次数，统计资源使用 |
| 2 | `SubagentLifecycleManager.destroy` | `yunshu_subagent_destroyed` | counter | 分身销毁次数，按 reason 维度统计 |
| 3 | `SubagentLifecycleManager.create` | `yunshu_subagent_active_count` | gauge | 当前活跃分身数，用于容量监控 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 create() 方法返回前添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_subagent_created', {'result': 'success'})
    _metrics._set_gauge('yunshu_subagent_active_count', {}, len(self._subagents))
except Exception as _e:
    logger.debug("分身埋点失败: %s", _e)
```

---

### 2.5 task_planner 模块

**功能**：任务规划器（目标分解为子任务 DAG、复杂度评估、确认流程）
**当前状态**：未埋点（`task_planner/planner.py` 有结构化日志但无 `BusinessMetricsCollector` 调用）
**推荐埋点文件**：`agent/task_planner/planner.py`、`agent/task_planner/executor.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `TaskPlanner.create_plan` | `yunshu_task_planner_plan_created` | counter | 规划创建次数，按 complexity 维度统计 |
| 2 | `TaskPlanner.confirm_plan` | `yunshu_task_planner_plan_confirmed` | counter | 规划确认次数，按 result(approved/rejected) 维度统计 |
| 3 | `DAGExecutor.execute` | `yunshu_task_planner_execution_duration_seconds` | histogram | DAG 执行耗时，衡量规划执行效率 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 create_plan() 成功后添加（已有 record_planning_task 领域方法可复用）：
try:
    _metrics = get_business_metrics_collector()
    _metrics.record_planning_task(
        planner_type="enhanced",
        steps_count=len(plan.nodes) if hasattr(plan, 'nodes') else 0,
        success=True
    )
except Exception as _e:
    logger.debug("规划埋点失败: %s", _e)
```

---

### 2.6 workflow_engine 模块

**功能**：工作流引擎（规则匹配→执行，0 Token 消耗的本地规则处理层）
**当前状态**：未埋点（`workflow_engine/engine.py` 无埋点调用）
**推荐埋点文件**：`agent/workflow_engine/engine.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `WorkflowEngine.try_match` | `yunshu_workflow_match_total` | counter | 规则匹配次数，按 matched(true/false) 维度统计命中率 |
| 2 | `WorkflowEngine.try_match` | `yunshu_workflow_match_duration_seconds` | histogram | 匹配+执行耗时，衡量规则引擎性能 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 try_match() 返回前添加（result 为 WorkflowResult）：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_workflow_match_total',
                                {'matched': str(result.matched), 'rule': result.rule_name or 'none'})
    _metrics._observe_histogram('yunshu_workflow_match_duration_seconds',
                                {'matched': str(result.matched)}, result.execution_time_ms / 1000)
except Exception as _e:
    logger.debug("工作流埋点失败: %s", _e)
```

---

### 2.7 caching 模块

**功能**：多级缓存系统（L1 内存 LRU + L2 磁盘，含 TTL/预热/统计）
**当前状态**：未埋点（`caching/multi_level_cache.py` 无埋点调用，已有 `CacheStats` 内部统计）
**推荐埋点文件**：`agent/caching/multi_level_cache.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `MultiLevelCache.get` | `yunshu_cache_access_total` | counter | 缓存访问次数，按 level(l1_hit/l2_hit/miss) 维度统计命中率 |
| 2 | `MultiLevelCache.set` | `yunshu_cache_write_total` | counter | 缓存写入次数 |
| 3 | `MultiLevelCache.get_stats` | `yunshu_cache_hit_rate` | gauge | 缓存命中率，周期性上报用于监控 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 get() 方法 L1 命中分支内添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_cache_access_total', {'level': 'l1_hit'})
except Exception as _e:
    pass

# 在 get() 方法未命中返回前添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_cache_access_total', {'level': 'miss'})
except Exception as _e:
    pass
```

---

### 2.8 extensions 模块

**功能**：扩展系统（市场安装、插件沙箱执行、安全检查、依赖管理）
**当前状态**：未埋点（`extensions/` 下所有文件无埋点调用）
**推荐埋点文件**：`agent/extensions/market.py`、`agent/extensions/security_checker.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `ExtensionMarket.install` | `yunshu_extension_install_total` | counter | 扩展安装次数，按 extension_type/result 维度统计 |
| 2 | `SkillSecurityChecker.check` | `yunshu_extension_security_check_total` | counter | 安全检查次数，按 assessment(passed/blocked) 维度统计 |
| 3 | `PluginSandbox.execute` | `yunshu_extension_sandbox_execute_duration_seconds` | histogram | 沙箱执行耗时 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 ExtensionMarket.install() 成功后添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_extension_install_total',
                                {'extension_type': ext_type, 'result': 'success'})
except Exception as _e:
    pass
```

---

### 2.9 network 模块

**功能**：网络配置管理（LLM/搜索/MCP 实例配置，敏感信息 AES-GCM 加密存储）
**当前状态**：未埋点（`network/config_manager.py` 无埋点调用）
**推荐埋点文件**：`agent/network/config_manager.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `NetworkConfigManager.update` | `yunshu_network_config_updated` | counter | 配置更新次数，按 section(llm/search/mcp/network) 维度统计 |
| 2 | `NetworkConfigManager.import_config` | `yunshu_network_config_imported` | counter | 配置导入次数，按 result 维度统计成功/失败 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 update() 方法成功保存后添加：
try:
    _metrics = get_business_metrics_collector()
    _sections = ','.join(sorted(updates.keys())) if updates else 'unknown'
    _metrics._increment_counter('yunshu_network_config_updated', {'section': _sections[:50]})
except Exception as _e:
    pass
```

---

### 2.10 prompt_manager 模块

**功能**：提示词管理（注册、更新、版本控制、存储）
**当前状态**：未埋点（`prompt_manager/registry.py` 有结构化日志但无 `BusinessMetricsCollector` 调用）
**推荐埋点文件**：`agent/prompt_manager/registry.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `PromptRegistry.register_prompt` | `yunshu_prompt_registered` | counter | 提示词注册次数，按 prompt_type 维度统计 |
| 2 | `PromptRegistry.update_prompt` | `yunshu_prompt_updated` | counter | 提示词更新次数 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 register_prompt() 返回前添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._increment_counter('yunshu_prompt_registered', {'prompt_type': prompt_type.value})
except Exception as _e:
    pass
```

---

### 2.11 p6 模块

**功能**：P6 快照性能监控（保存/加载耗时、模块序列化时间、空间节省）
**当前状态**：未埋点（`p6/performance.py` 有内部 `PerformanceMetrics` 但无 `BusinessMetricsCollector` 调用）
**推荐埋点文件**：`agent/p6/performance.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `SnapshotPerformanceMonitor.record_save` | `yunshu_p6_snapshot_save_duration_seconds` | histogram | 快照保存耗时分布 |
| 2 | `SnapshotPerformanceMonitor.record_load` | `yunshu_p6_snapshot_load_duration_seconds` | histogram | 快照加载耗时分布 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 record_save() 方法末尾添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._observe_histogram('yunshu_p6_snapshot_save_duration_seconds', {}, elapsed_ms / 1000)
except Exception as _e:
    pass
```

---

### 2.12 log_system 模块

**功能**：日志分析引擎（规则引擎、趋势检测、异常发现、日志看板 API）
**当前状态**：未埋点（`log_system/` 下无埋点调用）
**推荐埋点文件**：`agent/log_system/analyzer.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `LogAnalyzer.analyze` | `yunshu_log_analysis_duration_seconds` | histogram | 日志分析耗时 |
| 2 | `LogAnalyzer.analyze` | `yunshu_log_anomaly_detected` | counter | 检测到的异常数，按 severity 维度统计 |

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 LogAnalyzer.analyze() 返回前添加：
try:
    _metrics = get_business_metrics_collector()
    _anomaly_count = len(analysis_result.get('anomalies', []))
    _metrics._increment_counter('yunshu_log_anomaly_detected', {'severity': 'all'})
except Exception as _e:
    pass
```

---

### 2.13 lazy_loader 模块

**功能**：并行模块预加载器基类（ThreadPoolExecutor 并行加载模块，基础设施模块）
**当前状态**：未埋点（`lazy_loader/_core.py` 无埋点调用）
**推荐埋点文件**：`agent/lazy_loader/_core.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `_BaseParallelPreloader._load_module` | `yunshu_lazy_loader_module_loaded` | counter | 模块加载次数，按 result(success/failed) 维度统计 |
| 2 | `_BaseParallelPreloader._load_module` | `yunshu_lazy_loader_load_duration_seconds` | histogram | 单模块加载耗时 |

> 📌 **说明**：基础设施模块，建议在加载成功/失败时埋点，用于监控启动阶段模块加载失败率。

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 _load_module() 方法内添加（包裹现有逻辑）：
def _load_module(self, name: str, loader: Callable) -> tuple[str, Any]:
    start_time = time.perf_counter()
    try:
        instance = loader()
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        try:
            _metrics = get_business_metrics_collector()
            _metrics._increment_counter('yunshu_lazy_loader_module_loaded', {'result': 'success'})
            _metrics._observe_histogram('yunshu_lazy_loader_load_duration_seconds', {}, elapsed_ms / 1000)
        except Exception:
            pass
        return name, instance
    except Exception as e:
        try:
            _metrics = get_business_metrics_collector()
            _metrics._increment_counter('yunshu_lazy_loader_module_loaded', {'result': 'failed'})
        except Exception:
            pass
        raise
```

---

### 2.14 observability 模块

**功能**：架构规则校验、追踪存储、依赖图分析（可观测性基础设施自身）
**当前状态**：未埋点（`observability/` 下无埋点调用）
**推荐埋点文件**：`agent/observability/arch_rules.py`、`agent/observability/subscriber.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `ArchRuleValidator.validate` | `yunshu_observability_arch_violations` | gauge | 架构规则违规数，每次校验后上报当前值 |
| 2 | `TraceStore.start_trace` | `yunshu_observability_trace_started` | counter | 链路追踪启动次数 |

> 📌 **说明**：可观测性基础设施模块，埋点用于"观测观测系统本身"的自省需求。

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 ArchRuleValidator.validate() 返回前添加（report 为 ValidationReport）：
try:
    _metrics = get_business_metrics_collector()
    _metrics._set_gauge('yunshu_observability_arch_violations', {}, len(report.violations))
except Exception as _e:
    pass
```

---

### 2.15 tests 模块

**功能**：测试用例集合（`tests/test_*.py`，含 web_tools/tool_router/scheduling 等测试）
**当前状态**：未埋点（测试模块无业务埋点调用）
**推荐埋点文件**：无（非业务模块）
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| - | - | - | - | 测试模块不产生业务指标，无需埋点 |

> 📌 **说明**：非业务模块，可选。如需统计测试运行情况，建议通过 CI/CD 流水线采集测试报告数据，而非在测试代码中埋点。建议从 `track_event_coverage` 检测中将 `tests/` 目录排除。

---

### 2.16 utils 模块

**功能**：工具集（序列化 Serializer、兼容性检查、决策日志 DecisionLogger、敏感数据过滤）
**当前状态**：未埋点（`utils/` 下无埋点调用）
**推荐埋点文件**：`agent/utils/serialization.py`、`agent/utils/decision_logger.py`
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| 1 | `Serializer.dumps` | `yunshu_utils_serialize_duration_seconds` | histogram | 序列化耗时，按 format(json/pickle/msgpack/cbor) 维度统计 |
| 2 | `DecisionLog` 记录完成 | `yunshu_utils_decision_logged` | counter | 决策日志记录次数，按 decision_type 维度统计 |

> 📌 **说明**：辅助模块，埋点主要用于性能基线建立。序列化为高频调用，histogram 采样上报即可。

**代码示例**：
```python
from agent.monitoring.business_metrics import get_business_metrics_collector

# 在 Serializer.dumps() 返回前添加：
try:
    _metrics = get_business_metrics_collector()
    _metrics._observe_histogram('yunshu_utils_serialize_duration_seconds',
                                {'format': self._format}, _elapsed_seconds)
except Exception:
    pass
```

---

### 2.17 data 模块

**功能**：纯 JSON 数据目录（`api_keys.json` / `skills.json` / `tenants.json` / `users.json`）
**当前状态**：无 .py 文件（Glob 确认 `agent/data/**/*.py` 返回空）
**推荐埋点文件**：无
**推荐埋点位置**：

| # | 函数/方法 | 事件名 | 类型 | 说明 |
|---|----------|--------|------|------|
| - | - | - | - | 无代码文件，无法埋点 |

> ⚠️ **特殊处理**：`data/` 是纯数据目录，不含任何 Python 代码。`visibility_report.py` 的 `_calc_track_coverage` 将其计为未埋点模块，拉低了覆盖率。
>
> **建议**：在 `scripts/visibility_report.py` 的 `_calc_track_coverage` 方法中，跳过不含 `.py` 文件的子目录（类似已跳过 `_` 开头目录的逻辑），避免将数据目录误判为未埋点代码模块。
>
> 修改示例（`visibility_report.py` 第 736-738 行附近）：
> ```python
> for sub_dir in agent_dir.iterdir():
>     if not sub_dir.is_dir() or sub_dir.name.startswith("_"):
>         continue
>     # 新增：跳过无 .py 文件的纯数据目录
>     if not any(sub_dir.rglob("*.py")):
>         continue
> ```

---

## 三、实施计划

### 第一批（高优先级，6 个模块，预计覆盖率提升至 59.3%）

| 模块 | 目标事件数 | 预期覆盖率 | 验收标准 |
|------|-----------|-----------|----------|
| audit | 2 | 40.7%（11/27） | `visibility_report.py` 检测到 `BusinessMetricsCollector` 调用 |
| human_in_the_loop | 3 | 44.4%（12/27） | 同上 |
| quality | 2 | 48.1%（13/27） | 同上 |
| subagent | 3 | 51.9%（14/27） | 同上 |
| task_planner | 3 | 55.6%（15/27） | 同上 |
| workflow_engine | 2 | 59.3%（16/27） | 同上 |

### 第二批（中优先级，5 个模块，预计覆盖率提升至 77.8%，达标 ≥70%）

| 模块 | 目标事件数 | 预期覆盖率 | 验收标准 |
|------|-----------|-----------|----------|
| caching | 3 | 63.0%（17/27） | 命中/未命中埋点生效 |
| extensions | 3 | 66.7%（18/27） | 安装/安全检查埋点生效 |
| network | 2 | 70.4%（19/27） | 配置变更埋点生效 |
| prompt_manager | 2 | 74.1%（20/27） | 注册/更新埋点生效 |
| p6 | 2 | 77.8%（21/27） | 快照耗时埋点生效 |

### 第三批（低优先级，6 个模块，预计覆盖率提升至 100%）

| 模块 | 目标事件数 | 预期覆盖率 | 说明 |
|------|-----------|-----------|------|
| lazy_loader | 2 | 81.5%（22/27） | 基础设施，加载成功/失败埋点 |
| log_system | 2 | 85.2%（23/27） | 分析耗时/异常检测埋点 |
| observability | 2 | 88.9%（24/27） | 自省埋点 |
| utils | 2 | 92.6%（25/27） | 序列化/决策日志埋点 |
| tests | 0 | 92.6%（不变） | 建议从检测中排除 |
| data | 0 | 100%（检测逻辑修复后） | 修改 `visibility_report.py` 跳过无 .py 目录 |

---

## 四、覆盖率提升路径

```
当前: 37.0% (10/27)
  │
  ├─ 第一批(高): +6 模块 → 59.3% (16/27)
  │
  ├─ 第二批(中): +5 模块 → 77.8% (21/27) ✅ 达标 ≥70%
  │
  └─ 第三批(低): +4 模块 + 修复检测 → 100% (27/27 排除 tests/data)
```

---

## 五、注意事项

1. **埋点失败隔离**：所有埋点代码必须包裹 `try/except`，埋点异常不得向上传播影响主流程（参考 `circuit_breaker.py:189-201` 现有模式）。
2. **事件命名规范**：严格遵循 `yunshu_<模块>_<动作>` 格式，histogram 类型可追加 `_duration_seconds` 后缀，gauge 类型可追加 `_count`/`_rate` 后缀。
3. **标签设计**：标签值需为字符串类型（`_increment_counter` 签名要求 `Dict[str, str]`），布尔值需 `str(True)` 转换。
4. **高频调用采样**：`caching`、`utils` 等高频调用模块的 histogram 埋点建议采样上报（如 1% 采样率），避免存储膨胀。
5. **data 模块检测修复**：建议优先修复 `visibility_report.py` 跳过无 `.py` 文件目录的逻辑，可立即将覆盖率从 37.0% 提升至约 40.7%（排除 data 后 10/26）。
6. **tests 模块排除**：建议在 `visibility_report.py` 中排除 `tests/` 目录，因其非业务模块。

---

*报告由自动化分析生成，需人工审核后实施。所有代码示例基于代码库真实 API，已通过 `agent/monitoring/business_metrics.py` 源码验证。*
