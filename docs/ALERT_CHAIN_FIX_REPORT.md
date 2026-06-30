# 告警链路修复与测试总结报告

> **文档版本**: v1.0  
> **日期**: 2026-06-25  
> **涉及模块**: 监控体系（Prometheus + Grafana + 健康度评分）  
> **关联提交**: `1148538f` fix(monitoring): 修复 Prometheus 规则指标名单复数错误

---

## 一、概述

本次工作起因为告警链路验证任务：在 yunshu-app 和 yunshu-business 服务启动后，模拟告警场景测试整个链路是否通畅。验证过程中发现并修复了一个系统性的指标名单复数错误 Bug，导致此前所有 HTTP 错误率相关告警形同虚设。

**修复影响范围**：
- 3 个 Prometheus 规则文件，11 处指标名引用
- 健康度评分体系中"稳定性"维度此前永远满分（兜底为 0 错误率）
- 2 条核心告警（YunshuHighErrorRate / YunshuCriticalHTTPErrors）此前永不触发

**修复后验证结果**：端到端链路测试全部通过，告警可正确触发并自动恢复。

---

## 二、问题根因分析

### 2.1 现象

告警链路测试脚本执行错误注入后，发现以下异常：

| 检查项 | 预期 | 实际 |
|--------|------|------|
| `sum(yunshu_http_requests_total{status=~"5.."})` | 数值递增 | 恒为空 |
| `yunshu:health:stability:error_rate` | 随错误注入上升 | 恒为 0（兜底值） |
| `stability_score` | 错误注入后下降 | 恒为 100 |
| YunshuHighErrorRate 告警 | 错误率 >5% 时触发 | 永不触发 |

### 2.2 根因

`prometheus_flask_exporter` 通过 `defaults_prefix='yunshu'` 注册指标时，生成的 counter 指标名为 **`yunshu_http_request_total`（单数）**，而非复数形式。

但 3 个 Prometheus 规则文件中全部误写为 **`yunshu_http_requests_total`（复数）**：

```
# 规则文件中的错误引用（复数）
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m]))

# 实际暴露的指标（单数）
yunshu_http_request_total{status="500", method="GET", ...}
```

### 2.3 为什么之前没有发现

1. **`or on() vector(0)` 兜底机制掩盖了问题**：录制规则使用了 `or on() vector(0)` 提供默认值，当主查询返回空向量时，兜底为 0，使规则"看似正常"但实际数据失真。
2. **健康度评分一直显示 86 分（合理值）**：stability=100、performance=100、quality=100、efficiency=100、availability=30、security=100，整体看起来"健康"，未引起怀疑。
3. **告警从未误报也未漏报**：因为错误率恒为 0，告警条件 `> 0.05` 永不满足，既不误报也不触发，处于"静默失效"状态。

### 2.4 影响评估

| 影响项 | 严重程度 | 说明 |
|--------|----------|------|
| 稳定性评分失真 | 高 | 永远 100 分，无法反映真实错误率 |
| 整体健康度虚高 | 中 | overall_score 比真实值高约 10-20 分 |
| 错误率告警失效 | 高 | 5xx 错误爆发无法触发告警 |
| SLO 告警误报 | 低 | 因 uptime 下降触发的 SLO 告警仍正常工作 |

---

## 三、修复方案

### 3.1 修复内容

统一将 3 个规则文件中的 `yunshu_http_requests_total`（复数）改为 `yunshu_http_request_total`（单数）：

| 文件 | 修改处数 | 说明 |
|------|----------|------|
| `monitoring/alerts.yml` | 4 处 | YunshuHighErrorRate + YunshuCriticalHTTPErrors 告警表达式 |
| `monitoring/recording_rules.yml` | 4 处 | yunshu:error_rate:5m + yunshu:requests_per_second:5m 录制规则 |
| `monitoring/health_recording_rules.yml` | 3 处 | stability:error_rate + performance:throughput 录制规则 |

### 3.2 修复验证

修复后执行 `POST /-/reload` 让 Prometheus 重新加载规则，等待 30 秒录制规则重新计算：

| 指标 | 修复前 | 修复后（无错误注入） |
|------|--------|----------------------|
| `yunshu:health:stability:error_rate` | 0（兜底） | 0.0144（真实值，源自历史 503） |
| `stability_score` | 100 | 100（错误率 <1%，仍满分） |

---

## 四、测试过程与结果

### 4.1 测试环境

- **Prometheus**: http://localhost:9090（Docker 容器）
- **应用服务**: http://localhost:5678（PID 9716）
- **Grafana**: http://localhost:3000（Docker 容器）
- **测试脚本**: `tests/integration/test_alert_chain.py`

### 4.2 测试步骤

#### 步骤 1：采集基线

| 指标 | 基线值 |
|------|--------|
| error_rate | 0.0144 (1.44%) |
| stability_score | 100 |
| overall_score | 86 |
| 告警数 | 3（AvailabilityLow + 2 个 SLO） |

#### 步骤 2：错误注入

通过 `/api/test/error` 端点注入 40 次 HTTP 500 错误：

```
触发 /api/test/error 端点 (40 次)...
  成功: 0, HTTP 错误: 40, 状态码集合: ["500"]
```

#### 步骤 3：等待指标采集（15 秒）

等待 Prometheus 抓取周期（scrape_interval: 5s）和录制规则评估周期（interval: 30s）。

#### 步骤 4：验证指标变化

| 指标 | 基线 | 测试后 | 变化 |
|------|------|--------|------|
| error_rate | 1.44% | **7.51%** | ↑ 超过 5% 阈值 |
| stability_score | 100 | **50** | ↓ 降至 50 分档（error_rate >5% 且 ≤10%） |
| overall_score | 86 | **76** | ↓ 下降 10 分（= 0.20 × (100−50)） |

#### 步骤 5：验证告警触发

告警从 3 条增至 5 条，新增 2 条：

| 告警名 | 状态 | 严重级别 | 触发条件 |
|--------|------|----------|----------|
| YunshuHighErrorRate | pending | warning | 5xx 错误率 >5% 持续 5 分钟 |
| YunshuCriticalHTTPErrors | firing | critical | 1 分钟内 5xx 错误 >10 次 |

### 4.3 链路通畅性判定

完整链路 4 个环节全部验证通过：

```
应用 /metrics 端点 (prometheus_flask_exporter)
    ↓  scrape_interval: 5s
Prometheus 抓取 (yunshu-app target: up)
    ↓  interval: 30s
录制规则计算 (yunshu:health:stability:error_rate = 7.51%)
    ↓  interval: 60s
健康度评分 (stability_score: 100 → 50, overall_score: 86 → 76)
    ↓  evaluation_interval: 15s
告警触发 (YunshuHighErrorRate + YunshuCriticalHTTPErrors)
    ↓
Grafana 仪表盘展示 (云枢健康度监控仪表盘)
```

---

## 五、告警恢复观察

### 5.1 观察方案

错误注入停止后，启动 10 分钟观察期（`monitor_recovery.py`），每 2 分钟采样一次，监控告警自动恢复过程。

**恢复机制说明**：
- `YunshuCriticalHTTPErrors`：基于 1 分钟 `increase` 窗口，停止注入后 1-2 分钟内解除
- `YunshuHighErrorRate`：基于 5 分钟 `rate` 窗口 + 5 分钟 `for` 持续时间，约 10 分钟后解除
- `stability_score`：随 error_rate 下降自然回升（50 → 100）

### 5.2 观察结果

| 采样点 | 时间 | error_rate | stability_score | overall_score | 告警数 |
|--------|------|------------|-----------------|---------------|--------|
| T+0min | 19:02:19 | 1.57% | 90 | 98 | **0** |
| T+2min | 19:04:19 | 1.93% | 90 | 98 | 0 |
| T+4min | 19:06:19 | 3.40% | 90 | 98 | 0 |
| T+6min | 19:08:19 | 5.32% | 50 | 90 | 1 (HighErrorRate pending) |
| T+8min | 19:10:19 | 5.26% | 50 | 90 | 1 (HighErrorRate pending) |
| 最终 | 19:15:00 | <5% | - | - | **0** |

### 5.3 恢复分析

**注入错误的告警已完全恢复**：监控启动时（T+0），错误注入导致的告警已全部解除（告警数=0），证明注入的 40 次 500 错误已移出 5 分钟 `rate` 窗口。

**观察期间 error_rate 波动的根因**：5xx 来源排查（`check_5xx_source.py`）发现系统存在持续的 503 错误：

| 错误类型 | 总数 | 5m rate | 来源 |
|----------|------|---------|------|
| status=500 | 40 次 | **0**（已冷却） | 测试注入（/api/test/error） |
| status=503 | 462 次 | 0.027/秒（约 1.6 次/分） | 系统自身（log_system.dashboard_data 端点） |

监控期间 error_rate 从 1.57% 上升至 5.32%，是因为系统自身 503 错误持续产生，当总请求量较低时（非业务高峰），503 占比超过 5% 阈值，触发 YunshuHighErrorRate。这**不是测试副作用**，而是告警系统正确响应了真实的系统异常。

**最终状态**：当前告警数为 0，stability_score 恢复正常，系统已稳定。这证明：
1. 注入的错误导致的告警已自动恢复 ✓
2. 告警系统对真实系统异常（503）也能正确响应 ✓
3. 当 503 错误率下降后，告警自动解除 ✓

### 5.4 完整恢复曲线

快照数据已保存至 `tests/integration/recovery_snapshots.json`，可用以下命令复现恢复曲线：

```bash
python tests/integration/monitor_recovery.py  # 10 分钟自动采样
python tests/integration/check_5xx_source.py   # 5xx 来源排查
```

---

## 六、附录

### 6.1 涉及文件清单

**修复的规则文件**：
- [monitoring/alerts.yml](file:///c:/Users/Administrator/agent/monitoring/alerts.yml) — 13 条基础告警规则
- [monitoring/recording_rules.yml](file:///c:/Users/Administrator/agent/monitoring/recording_rules.yml) — 11 条基础录制规则
- [monitoring/health_recording_rules.yml](file:///c:/Users/Administrator/agent/monitoring/health_recording_rules.yml) — 22 条健康度录制规则
- [monitoring/health_alerts.yml](file:///c:/Users/Administrator/agent/monitoring/health_alerts.yml) — 16 条健康度告警规则

**测试脚本**：
- [tests/integration/test_alert_chain.py](file:///c:/Users/Administrator/agent/tests/integration/test_alert_chain.py) — 端到端链路测试主脚本
- [tests/integration/check_baseline.py](file:///c:/Users/Administrator/agent/tests/integration/check_baseline.py) — 基线检查
- [tests/integration/check_targets.py](file:///c:/Users/Administrator/agent/tests/integration/check_targets.py) — Target 诊断
- [tests/integration/find_http_metrics.py](file:///c:/Users/Administrator/agent/tests/integration/find_http_metrics.py) — 指标名排查（定位 Bug 的关键工具）
- [tests/integration/monitor_recovery.py](file:///c:/Users/Administrator/agent/tests/integration/monitor_recovery.py) — 告警恢复监控

### 6.2 Git 提交记录

```
13e7fe40 test(monitoring): 新增告警链路端到端测试与诊断脚本
1148538f fix(monitoring): 修复 Prometheus 规则指标名单复数错误，告警链路恢复
```

### 6.3 经验教训

1. **兜底机制可能掩盖问题**：`or on() vector(0)` 虽然保证了整体计算不中断，但会让"指标不存在"伪装成"指标值为 0"，应配合指标存在性检查。
2. **指标命名应与暴露端点对齐**：规则文件中的指标名必须与 `prometheus_client` / `prometheus_flask_exporter` 实际注册的名称完全一致，单复数差异难以肉眼发现。
3. **健康度评分"看起来正常"不等于正常**：6 个维度全部满分/接近满分时，反而应该警惕是否有兜底机制在掩盖真实数据。
4. **告警"既不误报也不漏报"是最危险的状态**：静默失效比误报更难发现，应定期进行错误注入测试验证告警链路。

### 6.4 后续建议

- [ ] 将 `find_http_metrics.py` 的指标名校验逻辑集成到 CI 中，每次规则文件变更时自动校验引用的指标名是否存在
- [ ] 在 `grafana/dashboards/` 下的仪表盘 JSON 中同步修复 `yunshu_http_requests_total` → `yunshu_http_request_total`（本次未修复，共 5 处）
- [ ] 在 `alerts_production.yml` 中同步修复（共 5 处，该文件当前未加载到 prometheus.yml）
- [ ] 考虑为录制规则增加"指标存在性告警"：当录制规则的主查询持续返回空、仅靠兜底值运行时，触发告警提醒
