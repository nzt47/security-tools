# Git 发布说明摘要 — observability 指标链路修复

> **发布日期**: 2026-07-23
> **分支**: feature/tlm-step3-vectorstore-sqlite-vec
> **远程**: gitee.com/nzt47/security-tools

## 修复概要

本次修复解决了技能幻觉指标（`yunshu_skill_hallucination_total`）从埋点到 Grafana 告警触发全链路静默丢失的问题。根因涉及两个独立的 bug，导致所有通用埋点 100% 丢失，告警规则无法触发。

## 修复内容

### 1. observability.py 单例化修复

| 项目 | 内容 |
|------|------|
| Commit | `55926ced` |
| 文件 | `agent/skills_mgmt/observability.py` |
| 变更 | 6 insertions, 2 deletions |

**问题**: `BusinessMetricsCollector()` 直接实例化而非 `get_business_metrics_collector()` 单例，导致埋点写入独立实例，`/metrics` 端点导出的单例为空。

**修复**:
```python
# 修复前
_metrics = BusinessMetricsCollector()

# 修复后
_metrics = get_business_metrics_collector()
```

### 2. business_metrics.py 公共方法恢复

| 项目 | 内容 |
|------|------|
| Commit | `c3286be0` |
| 文件 | `agent/monitoring/business_metrics.py` |
| 变更 | 30 insertions, 2 deletions |

**问题**: `inc_counter`/`observe_histogram`/`set_gauge` 3 个公共方法曾由 commit 1a7009a3 添加，但被后续 commit 覆盖丢失。`emit_metric` 通过 `hasattr` 探测方法是否存在，缺失导致所有 `if` 分支静默跳过，通用埋点 100% 丢失。

**修复**: 恢复 3 个公共包装方法，委托现有私有方法 `_increment_counter`/`_observe_histogram`/`_set_gauge`。

### 3. test_categories_valid 测试同步

| 项目 | 内容 |
|------|------|
| Commit | `c3286be0` |
| 文件 | `tests/unit/test_business_metrics_comprehensive.py` |
| 变更 | 1 insertion, 1 deletion |

**问题**: `valid_categories` 集合缺少 `skill_quality`（commit 2611f66a 添加技能质量指标时遗漏）。

**修复**: 在 `valid_categories` 集合中添加 `"skill_quality"`。

## 验证结果

### 回归测试

```
248 passed, 0 failed, 0 skipped in 7.55s
```

测试范围: `test_business_metrics_comprehensive.py` + `test_business_metrics_tracking.py` + `test_observability_config.py` + `test_observability_track_event.py` + `test_alert_rules_config.py`

### 激增验证（3 次独立验证）

| # | skill_id | burst | sustain | increase[5m] | critical Firing | warning Firing |
|---|----------|-------|---------|-------------|-----------------|----------------|
| 1 | surge-template-test | 25 | 5s | 26.76 | ✅ 16:22:30 UTC | ✅ 16:22:34 UTC |
| 2 | stability-test-002 | 25 | 5s | 42.79 | ✅ 16:32:00 UTC | ✅ 16:32:04 UTC |
| 3 | production-peak-003 | 50 | 3s | 42.96 | ✅ 16:38:00 UTC (T+61s) | ✅ 16:38:05 UTC |

### 稳定性指标

| 指标 | 结果 |
|------|------|
| counter 写入单例 | 3/3 次成功 |
| Prometheus 抓取 | 3/3 次成功 (target=up) |
| critical Firing | 3/3 次成功 |
| warning Firing | 3/3 次成功 |
| 误报 | 0 次 |
| 漏报 | 0 次 |

### 生产峰值场景验证

`surge_metrics_template.py` 默认参数已配置为生产环境常见峰值场景:
- **BURST_COUNT=50**: 高峰期连续幻觉 50 条（critical 阈值 20 的 2.5 倍）
- **SUSTAIN_INTERVAL=3s**: 密集维持（模拟高峰期技能调用频率）

验证 3 (production-peak-003) 使用此参数，burst 50 → T+61s Firing，与 30s for + 30s 评估精确匹配（偏差 1s）。

## Commit 列表

| Commit | 类型 | 说明 | 文件变更 |
|--------|------|------|---------|
| [`55926ced`](https://gitee.com/nzt47/security-tools/commit/55926ced) | fix(observability) | observability.py 单例化 + 激增模板脚本 + 测试模板文档 | 3 files, 656+/2- |
| [`c3286be0`](https://gitee.com/nzt47/security-tools/commit/c3286be0) | fix(monitoring) | business_metrics.py 公共方法恢复 + test_categories_valid 修复 | 2 files, 30+/2- |
| [`5f30e823`](https://gitee.com/nzt47/security-tools/commit/5f30e823) | docs(monitoring) | 告警验证报告归档 + 发布说明 | 2 files, 480+ |
| [`b8215957`](https://gitee.com/nzt47/security-tools/commit/b8215957) | docs(monitoring) | 稳定性测试完整日志 + 生产峰值参数 + 发布说明摘要 | 3 files, 186+/3- |
| [`942b0fab`](https://gitee.com/nzt47/security-tools/commit/942b0fab) | docs(monitoring) | release-notes 追加生产峰值验证 + 最终验收报告 | 2 files, 59+/2- |

> **注**: commit 链接指向 gitee 远程仓库，全部 5 个 commit 已推送至 `feature/tlm-step3-vectorstore-sqlite-vec` 分支。

## 数据流（修复后）

```
emit_eval_score_metric(skill_id, eval_score)
  → emit_metric("yunshu_skill_hallucination_total", kind="counter")
    → hasattr(_metrics, "inc_counter") → True  [修复2: 方法恢复]
    → _metrics.inc_counter(name, labels, value)
      → _metrics is get_business_metrics_collector()  [修复1: 单例化]
      → _increment_counter → _counters[metric_name][label_key] += 1
  → /metrics 端点 export_prometheus() 读取同一单例 _counters
  → Prometheus scrape (interval 5s)
  → Grafana alert evaluation (interval 30s, for=30s)
  → ngalert.sender.router → "Sending alerts"
```

## 核心结论

修复后告警全链路通畅、可重复、可回归。3 次独立激增验证（含生产峰值场景 burst=50）均成功触发 critical + warning Firing，30s for 优化在保证低误报风险的前提下将 critical 响应时间缩短 33%（从 1m for 的 ~90s 降至 30s for 的 ~60s）。

## 相关文档

- [alert-verification.md](./alert-verification.md) — 完整验证报告（含 10 节）
- [release-notes.md](./release-notes.md) — 详细发布说明（含经验教训）
- [alert-surge-test-template.md](./alert-surge-test-template.md) — 标准测试模板文档
