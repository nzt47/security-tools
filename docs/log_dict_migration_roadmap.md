# 日志双重序列化反模式迁移路线图

> 生成时间：2026-07-04
> 状态：基线审计完成，CI 守护规则已上线，存量迁移分批进行

## 一、背景

项目完成了 `log_dict()` 重构，消除了调用方 `json.dumps` + formatter `json.loads` 的双重序列化开销。
但代码库中仍有大量 `logger.X(json.dumps(...))` 反模式，需要逐步迁移到 `logger.X(log_dict(...))`。

## 二、现状基线（2026-07-04 全量扫描）

| 指标 | 数值 |
|------|------|
| 违规文件数 | 182 |
| 违规总数 | 1865 处 |
| 已豁免（核心模块） | 4 个文件（logging_utils.py / perf_monitor.py / migrate_to_log_dict.py / check_double_serialization.py） |
| 守护规则 | ✅ 已上线（`scripts/check_double_serialization.py` + `.github/workflows/log-perf-guard.yml`） |
| 豁免清单 | `.trae/double_serialization_exemptions.json` |

## 三、Top 20 重灾区文件（占总量 60%）

| 排名 | 文件 | 违规数 |
|------|------|--------|
| 1 | agent/p6/snapshot.py | 147 |
| 2 | agent/p6_snapshot.py | 112 |
| 3 | agent/orchestrator/lifecycle_manager.py | 86 |
| 4 | agent/network_config.py | 79 |
| 5 | agent/network/config_manager.py | 79 |
| 6 | agent/tools/file_tools.py | 75 |
| 7 | agent/web/search.py | 72 |
| 8 | agent/state_manager.py | 49 |
| 9 | agent/tool_calling.py | 48 |
| 10 | agent/orchestrator/orchestrator.py | 44 |
| 11 | agent/error_handler.py | 44 |
| 12 | scripts/visibility_report.py | 43 |
| 13 | agent/server_routes/routes_dashboard.py | 38 |
| 14 | agent/monitoring/resource_monitor.py | 36 |
| 15 | agent/monitoring/trace_http_client.py | 30 |
| 16 | agent/digital_life.py | 29 |
| 17 | agent/scheduling.py | 25 |
| 18 | agent/task_scheduler.py | 24 |
| 19 | agent/monitoring/self_healer.py | 22 |
| 20 | agent/weekly_report_generator.py | 22 |

## 四、迁移策略（分批进行）

### 批次 1：高风险模块优先（建议本周完成）
- 目标：Top 5 文件（累计 503 处，占 27%）
- 文件：`p6/snapshot.py`, `p6_snapshot.py`, `orchestrator/lifecycle_manager.py`, `network_config.py`, `network/config_manager.py`
- 风险：这些模块涉及核心调度和网络配置，迁移后需重点测试
- 工具：`python scripts/migrate_to_log_dict.py <file>`
- 验证：迁移后跑全量 `tests/unit/` 测试

### 批次 2：工具与 Web 模块（建议下周完成）
- 目标：Top 6-10 文件（累计 288 处）
- 文件：`tools/file_tools.py`, `web/search.py`, `state_manager.py`, `tool_calling.py`, `orchestrator/orchestrator.py`
- 风险：工具调用和 Web 搜索路径，需验证日志输出格式

### 批次 3：监控与报告模块（建议第三周）
- 目标：Top 11-20 文件（累计 308 处）
- 文件：`error_handler.py`, `visibility_report.py`, `routes_dashboard.py`, `monitoring/*` 等
- 风险：监控数据采集路径，需验证可观测性指标不受影响

### 批次 4：剩余文件（建议一个月内完成）
- 目标：剩余 162 个文件（累计 ~766 处）
- 工具：批量迁移 + 抽样验证

## 五、迁移命令示例

```bash
# 单文件迁移（dry-run 预览）
python scripts/migrate_to_log_dict.py --dry-run agent/p6/snapshot.py

# 单文件迁移（实际写入）
python scripts/migrate_to_log_dict.py agent/p6/snapshot.py

# 批量迁移（Top 5 文件）
python scripts/migrate_to_log_dict.py \
    agent/p6/snapshot.py \
    agent/p6_snapshot.py \
    agent/orchestrator/lifecycle_manager.py \
    agent/network_config.py \
    agent/network/config_manager.py

# 迁移后验证
python -m pytest tests/unit/ -v --tb=short -q
```

## 六、无法迁移的场景说明

以下场景的 `json.dumps` 是必要的，不应迁移到 `log_dict`：

1. **`agent/utils/perf_monitor.py`**：性能监控模块本身需要直接输出 JSON 字符串，
   避免与 `log_dict` 形成循环依赖。
2. **`agent/logging_utils.py`**：日志核心模块，`DictToJsonFilter` 需要 `json.dumps`
   将 dict 序列化为 JSON 字符串供文件 handler 输出。
3. **`scripts/migrate_to_log_dict.py` / `scripts/check_double_serialization.py`**：
   迁移工具和守护脚本本身，避免自我引用。
4. **第三方库兼容**：某些场景需要将日志作为字符串传递给外部系统（如 syslog、
   logstash），此时必须使用 `json.dumps` 序列化。

## 七、CI 守护规则

### 增量扫描（每次 PR）
- 触发：`pull_request` 事件
- 命令：`python scripts/check_double_serialization.py --diff-scan --base origin/<base_ref> --head HEAD`
- 行为：扫描 PR 中变更的文件，发现新增 `logger.X(json.dumps(...))` 即阻断合并
- 豁免：存量违规已在 `.trae/double_serialization_exemptions.json` 中记录

### 全量扫描（每日定时）
- 触发：`schedule` cron `0 4 * * *`
- 命令：`python scripts/check_double_serialization.py --full-scan --update-exemptions`
- 行为：全量扫描所有文件，更新豁免清单（用于跟踪迁移进度）

### 日志性能压力测试（每次提交）
- 触发：`push` / `pull_request` 事件
- 命令：`python scripts/run_log_perf_stress_test.py --quick`
- 行为：运行 `stress_test()` 验证日志管道无错误，对比新旧模式性能
- 阈值：吞吐量 ≥5000 ops/sec，p99 ≤500us，错误率 ≤1%，加速比 ≥1.2x

## 八、验证清单

- [x] 守护脚本 `check_double_serialization.py` 已创建并验证
- [x] 压力测试脚本 `run_log_perf_stress_test.py` 已创建并验证
- [x] CI workflow `log-perf-guard.yml` 已创建
- [x] 基线豁免清单已生成（1865 处违规）
- [x] 模式识别准确性已验证（`p6/snapshot.py` 识别 147 处）
- [ ] 批次 1 迁移完成（待执行）
- [ ] 批次 2 迁移完成（待执行）
- [ ] 批次 3 迁移完成（待执行）
- [ ] 批次 4 迁移完成（待执行）
