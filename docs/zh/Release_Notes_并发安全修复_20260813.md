# Release Notes — 并发安全修复（中危 12 处 + 低危 7 处）

**版本**: `ed53d567`（2026-08-13）
**分支**: develop
**主题**: 并发安全修复系列收口 — 中危 12 处 + 低危 7 处

---

## 变更摘要

本版本完成并发安全修复系列的最终收口：将此前扫描发现的中危 12 处与低危 7 处并发风险全部修复并验证。至此，系列扫描（LockFreeRingBuffer → 熔断/采样率 → AlertNotifier → SessionManager → 批量低危 → 高危 3 处 → 中危 12 处 → 低危 7 处）全部闭环。

## 修复内容

### 中危（12 处）

| 类别 | 文件 | 修复模式 |
|---|---|---|
| 回调列表竞态 | `agent/monitoring/performance.py` | 回调 append 持锁；`_sample_loop` 锁内快照 `list(...)` 锁外遍历；`PerformanceAlertManager` 独立锁 |
| 锁外遍历共享容器 | `agent/monitoring/business_metrics.py` | 无锁入口 `get_metric_by_name`/`export_prometheus` 持锁访问 defaultdict |
| 统计无锁自增/快照 | `agent/monitoring/alert_evaluator.py`、`agent/log_system/optimized_storage.py` | 独立 `_stats_lock`；`get_stats` 锁内快照 |
| 单例 fallback TOCTOU ×8 | optimized_storage / safe_logger / alert_evaluator / alert_manager / loki / self_healer / index_manager / routes_logging | 统一"模块级锁 + 双检锁 DCL" |
| 缓存无锁读写 | `agent/server_routes/routes_logging.py` | `_alert_rules_lock` + 临时文件 + `os.replace` 原子替换 |
| 锁内大分配/join | `agent/monitoring/chaos_injector.py` | join 与大分配移出锁外 |
| 启动 check-then-act ×6 | performance / alert_evaluator / alert_manager / observability / optimized_storage / storage | 锁内"检查-置位"原子化 |
| 采样适配锁外读 | `agent/monitoring/observability_optimizations.py` | elapsed 判断整体移入锁内；`_init_lock` 双检 |

### 低危（7 处）

| 文件 | 修复模式 |
|---|---|
| `agent/monitoring/utils.py` | `SingletonMeta` 锁预建（去除锁懒创建竞态） |
| `agent/log_system/safe_logger.py` + `agent/logging_utils.py` | `record_iteration`/`check_state` 锁内 logger 移出锁外 |
| `agent/log_system/introspection.py` | `_get_llm_service` 实例锁双检 |
| `agent/monitoring/self_healer.py` | 自愈回调锁内构建记录、锁外触发 |
| `agent/utils/decision_logger.py` | 共享可变 `current_log` 独立 `_log_lock` 保护 |
| `agent/log_system/optimized_storage.py` | `ShardedLogStorage` 锁内 `os.makedirs`/`close` 移出锁外 |
| `agent/llm_response_cache.py` | `get`/`put`/`clear`/`end_save` 锁内 logger 移出锁外 |

## 验证结果

- 低危回归：**290/290 通过**（24.94s）— llmcache / safe_logger / self_healer / introspection / optimized_storage / audit_safety / monitoring_utils / decision_logger / 6 个并发测试文件
- 系列累计：高危回归 499/499 + 并发全量 180/180 + 低危回归 290/290，**无回退**
- pre-commit 全过（关键字冲突扫描 / 工具索引同步 / 敏感信息检测 / 知识卡片校验）

## 兼容性

- 无 API 签名变更；无配置变更
- `SingletonMeta._lock` 由懒创建改为类属性预建（行为等价，消除竞态）
- 锁内 logger 均改为"锁内收集信息 → 锁外记录"，日志内容与级别不变

## 涉及文件（18 个）

`agent/monitoring/performance.py`、`business_metrics.py`、`alert_evaluator.py`、`alert_manager.py`、`chaos_injector.py`、`observability_optimizations.py`、`self_healer.py`、`loki.py`、`utils.py`、`agent/log_system/optimized_storage.py`、`safe_logger.py`、`storage.py`、`introspection.py`、`agent/logging_utils.py`、`agent/utils/decision_logger.py`、`index_manager.py`、`agent/server_routes/routes_logging.py`、`agent/llm_response_cache.py`

## 后续建议

- 系列全部风险点已闭环（高 3 / 中 12 / 低 7）
- 建议在持续集成中纳入全部并发测试文件（6 个并发测试文件共 27 用例）作为回归基线
