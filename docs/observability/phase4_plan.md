# 边界治理 Phase 4 行动计划 — 配置化收官 + 可观测性升级

> **迭代周期**：2026-07-05 ~ 2026-07-12（预计 1 周）
> **分支**：`phase4-config-observability`（待创建）
> **前置条件**：Phase 3 全部完成（6/6 任务 ✅）
> **上一阶段报告**：[Phase 3 最终执行总结报告](phase3_final_summary.md)
> **文档版本**：v1.0
> **生成时间**：2026-07-04

---

## 一、迭代目标

### 1.1 核心目标

1. **P2 配置化 100% 完成**：收尾剩余 3 项（llm_monitor / loki / alert_notifier）
2. **P3 配置化启动**：从 99 个 high 风险项中筛选高 ROI 项目改造
3. **配置变更可观测性**：将 `_change_log` 推送至外部可观测系统
4. **配置漂移检测 MVP**：运行时配置 vs 配置文件对比

### 1.2 验收标准

- [ ] P2 配置化 100% 完成（11/11 项）
- [ ] P3 至少完成 10 项配置化（累计 ≥ 42 个配置项）
- [ ] 配置变更事件推送至 Loki/Prometheus
- [ ] 配置漂移检测脚本 + CI 集成
- [ ] 白名单自动从 observability_config.py 推导（取代手动维护）

---

## 二、任务分解

### Task 1: P2 配置化收尾（3 项，1.5h）

**优先级**：P0
**依赖**：无

| # | 模块 | 当前值 | 配置路径 | 范围 |
|---|------|--------|----------|------|
| 1 | `agent/llm_monitor.py:18` | `MAX_RECORDS = 500` | `llm_monitor.max_records` | 100-5000 |
| 2 | `agent/monitoring/loki.py:96,152` | `timeout=10/30` | `loki.timeout_sec` | 1-120 |
| 3 | `agent/monitoring/alert_notifier.py:350` | `timeout=30` | `alert.timeout_sec` | 1-120 |

**实施步骤**：
1. 在 `observability_config.py` 新增 3 个 ValidationRule + 3 个便捷函数
2. 修改业务模块从 Config 读取（`Optional[int] = None` 模式）
3. 更新 `CONFIGURED_MODULES` 白名单（新增 3 个模块）
4. 运行混沌测试验证无回归
5. 重新扫描硬编码边界值，更新基线（预计从 99 降至 96）

**验收**：
- [ ] 3 项配置化功能验证通过
- [ ] 混沌测试 17/17 通过
- [ ] 硬编码基线降低到 ≤ 96

---

### Task 2: P3 配置化 — monitoring 系列批次（10 项，4h）

**优先级**：P1
**依赖**：Task 1 完成

从 `hardcoded_boundary_baseline_report.json` 中筛选 monitoring 模块的高 ROI 改造项：

| # | 模块 | 硬编码项 | 当前值 | 建议配置路径 |
|---|------|----------|--------|--------------|
| 1 | `prometheus.py:242,290,334` | `max_retries=3`（3 处） | 3 | `prometheus.max_retries` |
| 2 | `prometheus.py` | 上报超时 | 10 | `prometheus.timeout_sec` |
| 3 | `loki.py:265` | 批量推送超时 | 30 | `loki.batch_timeout_sec` |
| 4 | `chaos_injector.py:245,585,600` | 注入超时（3 处） | 5/10/15 | `chaos.inject_timeout_sec` |
| 5 | `resource_monitor.py:217` | 采集超时 | 30 | `resource_monitor.collect_timeout_sec` |
| 6 | `alert_notifier.py` | 重试次数 | 3 | `alert.max_retries` |
| 7 | `self_healer.py:408,661` | 自愈超时（2 处） | 60/5 | `self_healer.timeout_sec` |
| 8 | `search.py:219` | 搜索超时 | 30 | `search.timeout_sec` |

**实施步骤**：参照 Phase 3 Task 1 模式（ValidationRule + 便捷函数 + 业务模块改造 + 白名单更新）

**验收**：
- [ ] 10 项配置化完成
- [ ] 硬编码基线降低到 ≤ 86
- [ ] monitoring 模块单元测试全部通过

---

### Task 3: 配置变更可观测性（3h）

**优先级**：P1
**依赖**：无（可与 Task 1/2 并行）

**目标**：将 `_change_log` 从内存记录升级为可观测事件流。

**实施步骤**：

1. **Loki 推送**（1h）：
   - 在 `observability_config.py` 的 `set()` 方法中，增加 Loki 推送钩子
   - 推送格式：`{timestamp, config_path, old_value, new_value, operator, trace_id}`
   - 异步推送，失败时降级到本地日志

2. **Prometheus 指标**（1h）：
   - 新增 `config_changes_total` Counter（按 config_path 分维度）
   - 新增 `config_value` Gauge（暴露当前值，便于 Grafana 监控）
   - 在 `prometheus.py` 注册指标

3. **Alert 触发**（1h）：
   - 高风险配置变更触发 alert（如 `pool_size` 改为 > 50）
   - 在 `alert_rules.yaml` 新增配置变更规则
   - 集成到 `alert_notifier.py`

**验收**：
- [ ] 配置变更后 5 秒内能在 Loki 查询到事件
- [ ] Prometheus 指标暴露 `/metrics` 端点
- [ ] 高风险变更触发 alert 通知

---

### Task 4: 配置漂移检测 MVP（2h）

**优先级**：P2
**依赖**：Task 1 完成

**目标**：对比运行时配置与配置文件，发现未授权变更。

**实施步骤**：

1. **配置快照**（0.5h）：
   - 新增 `scripts/config_snapshot.py`
   - 启动时导出当前配置 JSON 快照到 `~/.agent/config_snapshot.json`
   - 包含 32 个配置项的 path/value/default/timestamp

2. **漂移检测脚本**（1h）：
   - 新增 `scripts/check_config_drift.py`
   - 对比运行时配置与快照，输出差异
   - 支持 `--json` 输出 + `--fail-on-drift` CI 模式

3. **CI 集成**（0.5h）：
   - 新增 `.github/workflows/config-drift-guard.yml`
   - PR 触发时运行漂移检测
   - 检测到非配置化路径的变更时警告

**验收**：
- [ ] 快照文件正确生成
- [ ] 漂移检测脚本能识别配置差异
- [ ] CI workflow 集成完成

---

### Task 5: 白名单自动化（1h）

**优先级**：P2
**依赖**：Task 1 完成

**目标**：从 `observability_config.py` 自动推导 `CONFIGURED_MODULES`，取代手动维护。

**实施步骤**：

1. 解析 `observability_config.py` 中所有 `ValidationRule` 的 `description` 字段
2. 提取业务模块路径（如 `error_handler.py` / `task_scheduler.py`）
3. 自动生成 `CONFIGURED_MODULES` 集合
4. 在 `check_hardcoded_boundaries.py` 启动时调用自动推导函数

**验收**：
- [ ] 自动推导出的白名单包含所有已配置化模块
- [ ] 硬编码扫描结果与手动白名单一致
- [ ] 新增配置化模块时无需手动更新白名单

---

### Task 6: 文档收尾与 PR（0.5h）

**优先级**：P0
**依赖**：Task 1-5 完成

- 更新 `phase3_final_summary.md` 添加 Phase 4 进展链接
- 创建 `phase4_final_summary.md` 最终报告
- 创建 PR 合并到 master

---

## 三、时间安排

| Day | 时段 | 任务 | 工时 |
|-----|------|------|------|
| Day 1 上午 | Task 1: P2 收尾 | 1.5h | 3 项配置化 + 混沌测试 |
| Day 1 下午 | Task 3: 配置变更可观测性 | 3h | Loki + Prometheus + Alert |
| Day 2 上午 | Task 2: P3 monitoring 批次 | 2h | 5 项配置化 |
| Day 2 下午 | Task 2 续 + Task 5 白名单自动化 | 3h | 5 项 + 自动推导 |
| Day 3 上午 | Task 4: 配置漂移检测 | 2h | 快照 + 检测 + CI |
| Day 3 下午 | Task 6: 文档收尾 + PR | 0.5h | 总结报告 + PR |
| **合计** | — | **12h（~1.5 个工作日）** | — |

---

## 四、风险评估

### 4.1 兼容性风险

| 改造项 | 风险 | 缓解措施 |
|--------|------|----------|
| Loki 推送钩子 | 推送失败影响 `config.set()` 性能 | 异步推送 + 失败降级 |
| Prometheus 指标 | 指标爆炸（32 个 Gauge） | 仅暴露高频访问的 8 个配置项 |
| 配置漂移检测 | 误报合法变更 | 白名单允许 PR 中显式声明的变更 |

### 4.2 性能风险

- `config.set()` 增加 Loki 推送：异步线程，主流程 < 1ms
- `config.get()` 增加 Prometheus 指标更新：受 RLock 保护，< 0.1ms
- 漂移检测脚本：仅在 CI 运行，不影响生产

### 4.3 已知限制

1. **配置 A/B 测试**：本阶段不实现，列入 Phase 5
2. **配置推荐系统**：本阶段不实现，列入 Phase 5
3. **白名单自动化**：依赖 `description` 字段格式一致，需规范化

---

## 五、成功指标

### 5.1 量化指标

| 指标 | Phase 3 末 | Phase 4 目标 |
|------|-----------|-------------|
| 配置项总数 | 32 | **45+**（+13） |
| P2 完成率 | 82% | **100%** |
| P3 完成率 | 0% | **10%+**（10 项） |
| 硬编码基线 | 99 | **≤ 86** |
| 配置变更可观测 | 内存 `_change_log` | **Loki + Prometheus + Alert** |
| 配置漂移检测 | 无 | **MVP（CI 集成）** |
| 白名单维护 | 手动 | **自动推导** |

### 5.2 质性指标

- 配置变更全链路可观测（变更 → 推送 → 监控 → 告警）
- CI 防护从"硬编码检测"升级为"配置漂移检测"
- 配置系统从"被动响应"升级为"主动监控"

---

## 六、附录

### 6.1 相关文档

- [Phase 3 最终执行总结报告](phase3_final_summary.md)
- [Phase 2 技术总结 + Phase 3 进展补充](iteration_summary_boundary_governance_phase2.md)
- [硬编码边界值基线报告](hardcoded_boundary_baseline_report.json)
- [P2 改造候选清单](p2_hardcoded_constants_inventory.md)

### 6.2 验证命令

```bash
# P2 收尾后基线验证
python scripts/check_hardcoded_boundaries.py --target agent/ --baseline 96

# 配置变更可观测性验证
curl http://localhost:3100/loki/api/v1/query?query={app="agent-config"}

# 配置漂移检测
python scripts/check_config_drift.py --json --fail-on-drift

# 混沌测试回归
pytest tests/chaos/test_config_boundary_chaos.py --timeout=300
```
