# Release Notes — v1.2.0-task1234

- **发布标签**: `v1.2.0-task1234`（annotated，指向 `3cb68a4a`）
- **日期**: 2026-08-15
- **关联任务**: Jira #TASK-1234（监控指标命名规范修复 + Prometheus 验证工具链）
- **分支**: develop（已推送 origin/develop，HEAD=`20eca1a6`）

## 一、背景

全量单元测试（11749 passed）中发现命名规范回归：并行会话引入的锁看门狗模块注册了 3 个不符合 `^yunshu_[a-z_]+_[a-z_]+$` 约束的业务指标，触发 `test_naming_pattern` 失败。本版本完成指标改名与配套工具链交付，恢复全库命名规范零违规。

## 二、主要变更

### 2.1 指标命名规范修复（`db70b097`）
- 3 个违规指标补齐 `yunshu_` 前缀：
  - `lock_hold_timeouts_total` → `yunshu_lock_hold_timeouts_total`
  - `lock_wait_timeouts_total` → `yunshu_lock_wait_timeouts_total`
  - `lock_hold_duration_ms` → `yunshu_lock_hold_duration_ms`
- 同步更新引用点：`agent/monitoring/lock_watchdog.py`（注册定义 + `get_metrics()` 计数键）、`monitoring/prometheus/rules/lock_watchdog_alerts.yml`（2 条 PromQL expr）、`tests/unit/test_lock_watchdog.py`（9 处断言）、`scripts/run_optimization_workflow.ps1`、`docs/zh/P1B1_C2C3_实施计划_20260814.md`

### 2.2 测试修复（`6964d441`）
- 修复 `TestCallLLMV2` 全量回归失败：`_run_llm_bounded` 在 MagicMock 上自动返回 Mock 导致 `_re.search` 收到非字符串，改为 `lambda fn, timeout=0: fn()` 同步执行

### 2.3 Prometheus 配置与验证工具链（`661d3b74`）
- `monitoring/prometheus.yml` 与 `monitoring/prometheus/prometheus.yml` 的 `rule_files` 补入 `lock_watchdog_alerts.yml`
- 新增 `scripts/verify_prometheus_checklist.py`：C1-C5 一键验证（promtool 优先 / 降级 YAML 校验、`/-/reload`、`/api/v1/rules`、`/metrics`、`/api/v1/query`）
- 阶段5手册新增 §2.3 部署验证 Checklist（C1-C6 命令与完成标准）

### 2.4 Jira 集成脚本（`a2a4e4cf`、`bad5f867`、`3cb68a4a`）
- `scripts/jira_update_task_status.py`：上传审计 zip 附件（multipart/form-data + `JIRA_ATTACH`）、添加备注、状态流转为 Done
- 新增 `--dry-run` 演练模式（不发真实 API 请求，审计合规）
- `scripts/run_jira_task1234_update.ps1`：一键执行入口（凭证交互输入 + 默认附件）

### 2.5 文档与归档（`622fdf5f`）
- 任务8结案报告、变更影响分析、任务单、任务6最终报告验收状态同步
- 审计 zip：`task8_close_audit_20260815.zip`（12 文件：文档 5 / 脚本 3 / 配置 2 / 源码 2）

## 三、验证结果

| 验证项 | 结果 |
|---|---|
| 专项单元测试（test_lock_watchdog + test_business_metrics_tracking） | **45 passed**（含 test_naming_pattern） |
| 全量单元测试 | **11749 passed / 2 failed / 305 skipped / 14 xfailed / 3 xpassed**（2 failed 已由 6964d441 修复，复跑 2 passed） |
| 命名规范违规数 | **全库 0** |
| 验证脚本本地运行 | C1 PASS（降级校验）、C2-C5 SKIP（本地无 Prometheus 实例） |

## 四、待外部验证项

- [ ] **Jira #TASK-1234 真实执行**（上传附件 / 备注 / Done）：需凭证，运行 `.\scripts\run_jira_task1234_update.ps1`（演练见 `--dry-run`）
- [ ] **C2-C6 端到端采集验证**：部署环境运行 `python scripts/verify_prometheus_checklist.py --prom-url http://<prom> --metrics-url http://<app>`
- [ ] **C4 服务指标确认**：确认部署实例已启用 LockWatchdog（本地 5678 实例未暴露 `yunshu_lock` 指标）

## 五、风险与注意

1. **指标改名影响**：Prometheus 历史序列 `lock_*` 中断、counter 归零；告警规则已同步无悬空引用，注意监控基线比对口径。
2. **历史文档保留旧名**：TASK-07 变更说明、变更摘要等 4 份历史记录保留旧名（记录事实，不追溯修改）。
3. **外部依赖**：Jira 凭证与部署环境验证为移交项，完成后才可整体闭环。

## 六、涉及文件清单

| 类别 | 文件 |
|---|---|
| 源码 | `agent/monitoring/lock_watchdog.py` |
| 测试 | `tests/unit/test_lock_watchdog.py`、`tests/unit/test_digital_life_comprehensive.py` |
| 配置 | `monitoring/prometheus.yml`、`monitoring/prometheus/prometheus.yml`、`monitoring/prometheus/rules/lock_watchdog_alerts.yml` |
| 脚本 | `scripts/verify_prometheus_checklist.py`、`scripts/jira_update_task_status.py`、`scripts/run_jira_task1234_update.ps1` |
| 文档 | 任务8结案报告、任务8任务单、变更影响分析、任务6最终报告、阶段5手册 |
