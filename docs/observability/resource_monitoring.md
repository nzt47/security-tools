# 资源生命周期监控与可观测性配置治理

本文档介绍云枢项目资源监控能力与可观测性配置集中化方案，对齐"资源生命周期监控"与"实时效果看板"要求。

## 一、架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    可观测性统一入口                            │
│            ObservabilityConfig (observability_config.py)     │
│  ┌─────────────┬─────────────┬─────────────┬───────────────┐ │
│  │ 追踪配置     │ 日志配置     │ 指标开关     │ 资源监控配置  │ │
│  │ (tracing)   │ (logging)  │ (metrics)  │(resource_mon)│ │
│  └─────────────┴─────────────┴─────────────┴───────────────┘ │
│         ▲ ValidationRule 声明式验证 + 热加载（ConfigHotReloader)│
└─────────┼───────────────────────────────────────────────────┘
          │ 委托
          ▼
┌─────────────────────────────────────────────────────────────┐
│              资源泄漏检测 (resource_monitor.py)               │
│  ┌──────────┬───────────┬───────────┬────────────────────┐  │
│  │ 内存       │ 线程池     │ 文件句柄    │ 数据库连接          │  │
│  │tracemalloc│ threading │ psutil   │ provider 注册       │  │
│  └──────────┴───────────┴───────────┴────────────────────┘  │
│         ▲ 周期采样(60s)/压测模式(1s) + 线性回归趋势检测        │
└─────────┼───────────────────────────────────────────────────┘
          │ 上报 yunshu_resource_usage gauge
          ▼
┌─────────────────────────────────────────────────────────────┐
│          BusinessMetricsCollector → Prometheus → Grafana    │
│                                                             │
│  看板模板 feature_template.json + 生成脚本 generate_dashboard│
└─────────────────────────────────────────────────────────────┘
```

## 二、模块清单

| 模块 | 路径 | 职责 |
|---|---|---|
| 可观测性配置 | `agent/monitoring/observability_config.py` | 配置收拢、ValidationRule 验证、热加载 |
| 资源监控 | `agent/monitoring/resource_monitor.py` | 内存/线程/句柄/连接池采样与泄漏检测 |
| 监控路由 | `agent/server_routes/routes_monitoring.py` | `/api/monitoring/resources` 端点 |
| 看板模板 | `monitoring/grafana_dashboards/templates/feature_template.json` | 4 标准面板模板 |
| 看板生成 | `scripts/generate_dashboard.py` | 按模块名生成看板 JSON |
| 配置单元测试 | `tests/unit/test_observability_config.py` | 配置验证/回滚/热加载测试 |
| 资源监控测试 | `tests/unit/test_resource_monitor.py` | 采样/趋势/降级测试 |
| 压测验证 | `tests/stress/test_resource_leak.py` | 资源释放曲线与泄漏检测 |

## 三、资源监控使用指南

### 3.1 启动周期采样

```python
from agent.monitoring.resource_monitor import get_resource_monitor

monitor = get_resource_monitor()
monitor.start()  # 默认 60 秒采样一次
```

### 3.2 获取当前快照

```python
snapshot = monitor.get_snapshot()
print(f"内存: {snapshot.memory.current_bytes} bytes")
print(f"线程: {snapshot.thread_pool.active_threads}")
print(f"文件句柄: {snapshot.file_handles.open_count}")
```

### 3.3 趋势分析与泄漏检测

```python
trend = monitor.get_trend("memory")
if trend and trend.is_leaking:
    print(f"警告: 内存泄漏！斜率={trend.slope}, 阈值={trend.threshold}")
```

### 3.4 压测模式

```python
monitor.enable_stress_mode()   # 切换到 1 秒高频采样
# ... 执行压测 ...
monitor.disable_stress_mode()  # 恢复常规采样
```

### 3.5 注册外部资源池

```python
# 线程池
monitor.register_pool_provider("worker_pool", lambda: {
    "active": pool.active_count,
    "queued": pool.queue.qsize(),
    "size": pool.max_workers,
}, pool_type="thread")

# 数据库连接池
monitor.register_pool_provider("main_db", lambda: {
    "active": db_pool.active,
    "idle": db_pool.idle,
    "size": db_pool.size,
}, pool_type="db")
```

### 3.6 注册泄漏告警回调

```python
def on_leak(trend_result):
    # 触发钉钉/邮件告警
    send_alert(f"资源 {trend_result.resource_type} 泄漏，斜率={trend_result.slope}")

monitor.register_leak_callback(on_leak)
```

## 四、API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/monitoring/resources` | GET | 获取当前快照 + 历史趋势（参数 `limit`/`trend`） |
| `/api/monitoring/resources/start` | POST | 启动周期采样 |
| `/api/monitoring/resources/stop` | POST | 停止周期采样 |
| `/api/monitoring/resources/stress-mode` | POST | 切换压测模式（body: `{"enabled": true}`） |

**响应示例**：

```json
{
  "ok": true,
  "snapshot": {
    "timestamp": 1719400000.0,
    "iso_time": "2026-06-26T03:33:20+00:00",
    "memory": {"current_bytes": 10485760, "peak_bytes": 20971520, "top_allocations": [...]},
    "thread_pool": {"active_threads": 12, "registered_pools": {...}},
    "file_handles": {"open_count": 45, "available": true},
    "db_connections": {"pools": {"main_db": {"active": 2, "idle": 3, "size": 5}}},
    "sample_duration_ms": 3.2
  },
  "history": [...],
  "trend": {
    "resource_type": "memory",
    "slope": 1024.5,
    "intercept": 1000000,
    "r_squared": 0.95,
    "sample_count": 60,
    "is_leaking": true,
    "threshold": 1.0
  },
  "status": {...}
}
```

## 五、可观测性配置指南

### 5.1 统一配置入口

```python
from agent.monitoring.observability_config import get_observability_config

config = get_observability_config()

# 读取
interval = config.get("resource_monitor.sample_interval_sec")
log_level = config.get("logging.level")

# 修改（运行时热生效，原子性变更）
config.set("resource_monitor.sample_interval_sec", 30)
```

### 5.2 配置项清单

| 路径 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `tracing.env` | 枚举 | development | 追踪环境 |
| `tracing.log_level` | 枚举 | INFO | 追踪日志级别 |
| `tracing.sampler_ratio` | 范围 | 0.1 | 采样比例 (0.0-1.0) |
| `logging.level` | 枚举 | INFO | 全局日志级别 |
| `logging.output_path` | 路径 | "" (stdout) | 日志输出路径 |
| `metrics.enabled` | 布尔 | True | 指标采集开关 |
| `health_check.interval_sec` | 范围 | 60 | 健康检查频率 (5-3600s) |
| `resource_monitor.enabled` | 布尔 | True | 资源监控开关 |
| `resource_monitor.sample_interval_sec` | 范围 | 60 | 采样间隔 (1-3600s) |
| `resource_monitor.stress_test_interval_sec` | 范围 | 1.0 | 压测采样间隔 (0.5-10s) |
| `resource_monitor.leak_slope_threshold` | 范围 | 1.0 | 泄漏斜率阈值 |
| `resource_monitor.history_size` | 范围 | 1440 | 历史保留数量 |

### 5.3 热加载配置文件

```python
config = get_observability_config()
config.watch_config_file("/path/to/observability.yaml")
# 文件变更时自动重载，无需重启服务
```

### 5.4 配置变更回调

```python
def on_resource_config_change(key, value):
    if key.startswith("resource_monitor.sample_interval"):
        # 联动调整采样器
        monitor = get_resource_monitor()
        # ... 重新应用间隔

config.register_callback("resource_monitor", on_resource_config_change)
```

### 5.5 向后兼容

原 `TracingConfig` 接口保留，内部委托到 `ObservabilityConfig`：

```python
from agent.monitoring.observability_config import get_tracing_config_compat
compat = get_tracing_config_compat()
print(compat.env)            # 读取
print(compat.sampler_type)  # 根据 env 推导
```

## 六、看板模板使用

### 6.1 生成模块看板

```bash
# 生成 chat 模块看板
python scripts/generate_dashboard.py --module chat

# 指定输出路径
python scripts/generate_dashboard.py --module chat --output ./chat_dashboard.json

# 预览引用指标
python scripts/generate_dashboard.py --module tool_call --dry-run
```

### 6.2 导入 Grafana

1. 打开 Grafana → Dashboards → Import
2. 上传生成的 `yunshu_<module>_dashboard.json`
3. 选择 Prometheus 数据源
4. 点击 Import

### 6.3 标准面板说明

模板包含 4 个标准面板：

| 面板 | 类型 | 指标 | 说明 |
|---|---|---|---|
| 调用量 / QPS | timeseries | `yunshu_<module>_total` | 1m/5m QPS 趋势 |
| 成功率 / 失败计数 | timeseries | `yunshu_<module>_total{success=...}` | 成功率百分比 + 失败计数双 Y 轴 |
| P50/P90/P99 耗时 | timeseries | `yunshu_<module>_duration_seconds` | histogram 分位数 |
| 转化漏斗 | piechart | `yunshu_<module>_total` | 24h 调用/成功/失败分布 |

## 七、指标命名规范

遵循硬约束 `yunshu_<模块>_<动作>` 格式：

- `yunshu_resource_usage`（gauge）：资源使用量，标签 `resource_type` ∈ {memory, thread, file_handle, db_connection}
- `yunshu_<module>_total`（counter）：模块调用量，标签含 `success`
- `yunshu_<module>_duration_seconds`（histogram）：模块耗时分布

## 八、历史数据持久化（跨重启趋势分析）

### 8.1 工作原理

资源监控默认启用历史采样落盘，支持服务重启后恢复历史趋势分析：

- **存储格式**：JSON Lines（每行一个快照 JSON），追加写入避免全量重写
- **默认路径**：`./data/resource_monitor_history.jsonl`
- **批量写入**：达到 `persist_batch_size`（默认 100）触发落盘，降低 IO 频率
- **过期清理**：启动加载时按 `persist_max_age_hours`（默认 168h=7天）过滤过期数据并重写文件
- **原子性**：重写使用临时文件 + `os.replace` 原子替换，避免崩溃损坏
- **降级**：落盘失败仅日志记录，不影响采样主流程

### 8.2 配置项

| 配置路径 | 默认值 | 说明 |
|---|---|---|
| `resource_monitor.persist_enabled` | true | 是否启用持久化 |
| `resource_monitor.persist_path` | "" (默认路径) | 持久化文件路径 |
| `resource_monitor.persist_max_age_hours` | 168 | 数据最大保留时长（小时） |
| `resource_monitor.persist_batch_size` | 100 | 批量写入缓冲条数 |

### 8.3 使用方式

```python
from agent.monitoring.resource_monitor import get_resource_monitor

monitor = get_resource_monitor()
monitor.start()  # 启动时自动加载持久化历史

# 手动刷新缓冲到磁盘
monitor.flush_persist()

# 手动清理过期数据
kept = monitor.cleanup_persisted_history()

# 查看持久化状态
status = monitor.get_persist_status()
print(f"文件大小: {status['file_size_bytes']} bytes, 缓冲: {status['buffer_count']}")
```

## 九、告警规则

### 9.1 已配置的告警

在 `monitoring/alerts.yml` 的 `yunshu_resources` 组新增 6 条基于 `yunshu_resource_usage` 的告警：

| 告警名称 | 触发条件 | 严重级别 |
|---|---|---|
| `YunshuMemoryLeakDetected` | 内存 10 分钟持续增长 > 50MB（deriv > 83333 bytes/s） | critical |
| `YunshuMemoryHighUsage` | 内存使用量 > 1GB 持续 5 分钟 | warning |
| `YunshuFileHandleExhaustion` | 文件句柄数 > 800 持续 5 分钟 | warning |
| `YunshuFileHandleCritical` | 文件句柄数 > 950 持续 1 分钟 | critical |
| `YunshuThreadCountHigh` | 活动线程数 > 200 持续 5 分钟 | warning |
| `YunshuDbConnectionPoolExhaustion` | 数据库连接 active > 80 持续 5 分钟 | warning |

### 9.2 告警 PromQL 示例

```promql
# 内存泄漏检测（10 分钟内变化率）
deriv(yunshu_resource_usage{resource_type="memory"}[10m]) > 50000000 / 600

# 文件句柄即将耗尽
yunshu_resource_usage{resource_type="file_handle"} > 950
```

## 十、资源释放曲线看板

### 10.1 看板位置

`monitoring/grafana_dashboards/yunshu_resource_release_dashboard.json`

### 10.2 面板说明

| 面板 | 类型 | 内容 |
|---|---|---|
| 资源概览（当前值） | stat | 内存/线程/句柄/连接实时数值 |
| 内存使用趋势 | timeseries | 当前内存 + 1h 峰值回溯 |
| 内存释放速率 | timeseries | deriv(5m) 增长/释放曲线 |
| 线程数变化曲线 | timeseries | 活动线程趋势 |
| 文件句柄变化曲线 | timeseries | 句柄数趋势 |
| 数据库连接池 active 趋势 | timeseries | 按池名分组 |
| 资源释放热点 | timeseries(柱状) | 1h delta（负值=释放） |

### 10.3 导入方式

1. Grafana → Dashboards → Import
2. 上传 `yunshu_resource_release_dashboard.json`
3. 选择 Prometheus 数据源 → Import

## 十一、质量约束达成

| 约束 | 达成方式 |
|---|---|
| 不引入付费第三方依赖 | tracemalloc（标准库）、psutil（开源 BSD） |
| 监控性能开销 < 1% | 单次采样 < 50ms，60s 间隔占比 < 0.08% |
| 埋点单次耗时 < 1ms | `_set_gauge` 仅内存写入，无 IO |
| 埋点失败不影响主流程 | `_report_metrics` 全异常捕获，仅日志记录 |
| 新增代码覆盖率 ≥ 80% | 单元测试覆盖验证/采样/趋势/降级/并发 |
| 异常降级 | 子监控失败仅记录日志，不影响业务与其他子监控 |
| 配置热加载原子性 | `reload_from_dict` 验证失败回滚到旧配置 |
| 中文注释 | 所有模块/方法/关键逻辑含中文注释 |

## 九、扩展方向

1. **告警规则**：在 `monitoring/alerts.yml` 增加基于 `yunshu_resource_usage` 的告警规则
2. **持久化**：将历史采样落盘，支持跨重启趋势分析
3. **Goroutine/协程监控**：扩展支持 asyncio 任务计数
4. **可视化曲线**：在 dashboard 集成资源释放曲线图表
