# Jira 任务卡片内容（告警链路修复后续行动项）

> **来源**: 告警链路修复技术分享 · 2026-06-25  
> **关联提交**: `1148538f` fix(monitoring): 修复 Prometheus 规则指标名单复数错误  
> **完整背景**: 见 `docs/TECH_SHARING_ALERT_SILENT_FAILURE.md`  
> **使用方式**: 逐条复制到 Jira 创建任务，字段已按团队模板预填

---

## 任务 1：CI 集成 Prometheus 指标名校验

### Summary
CI 集成指标名校验：规则文件变更时自动校验引用的指标是否存在

### Issue Type
Task

### Priority
P1 — High

### Components
monitoring / CI-CD

### Labels
`tech-debt` `monitoring` `ci` `auto-verification`

### Due Date
2026-07-09（2 周内）

### Estimate
2-3 人日

### Description

**背景**

本次告警链路 Bug 的根因是规则文件引用了不存在的指标名 `yunshu_http_requests_total`（复数），实际暴露的是 `yunshu_http_request_total`（单数）。该问题潜伏数周未被发现，因为 `or on() vector(0)` 兜底机制将"指标不存在"伪装成"指标值为 0"。

如果 CI 能在规则文件变更时自动校验引用的指标名是否真实存在，这类问题可以在提交阶段拦截，而不是等到线上告警失效后才被发现。

**目标**

将现有的诊断脚本 `tests/integration/find_http_metrics.py` 改造为 CI 可执行的校验工具，集成到 GitHub Actions workflow，在每次 `monitoring/*.yml` 变更时自动运行。

**技术方案**

1. 改造 `find_http_metrics.py` 为可独立运行的校验脚本，支持 `--strict` 模式：
   - 解析 `monitoring/` 下所有 `.yml` 规则文件
   - 提取 PromQL 表达式中引用的所有指标名
   - 启动本地 Prometheus 实例（或连接 staging 环境）抓取 `/metrics` 端点
   - 对比规则文件引用的指标名与实际暴露的指标名
   - 发现不匹配时输出详细报告并返回非零退出码

2. 在 `.github/workflows/ci.yml` 中新增 job：
   ```yaml
   metric-name-check:
     runs-on: ubuntu-latest
     steps:
       - uses: actions/checkout@v4
       - name: Verify metric names in rule files
         run: python tests/integration/find_http_metrics.py --strict
   ```

3. 校验范围覆盖 `alerts.yml`、`recording_rules.yml`、`health_recording_rules.yml`、`health_alerts.yml` 中所有 PromQL 表达式。

### Acceptance Criteria

- [ ] `find_http_metrics.py` 支持 `--strict` 参数，发现不匹配指标名时返回非零退出码
- [ ] 校验脚本能解析 YAML 规则文件中所有 `expr` 字段的 PromQL，提取被引用的指标名
- [ ] CI workflow 在 PR 阶段自动运行指标名校验
- [ ] 故意引入一个错误指标名（如 `yunshu_fake_metric_total`）能被 CI 拦截，PR 无法合并
- [ ] 校验脚本输出格式清晰，包含：文件名、行号、错误指标名、建议的相似指标名
- [ ] 单元测试覆盖 YAML 解析、指标名提取、对比逻辑

### Links
- 关联文档: `docs/TECH_SHARING_ALERT_SILENT_FAILURE.md`
- 参考脚本: `tests/integration/find_http_metrics.py`
- 关联提交: `1148538f`

---

## 任务 2：Grafana 仪表盘 JSON 同步修复指标名

### Summary
Grafana 仪表盘 JSON 同步修复 yunshu_http_requests_total → yunshu_http_request_total（5 处）

### Issue Type
Bug

### Priority
P1 — High

### Components
monitoring / grafana

### Labels
`bug` `monitoring` `grafana` `quick-fix`

### Due Date
2026-07-02（1 周内）

### Estimate
0.5 人日

### Description

**背景**

本次修复了 Prometheus 规则文件中的指标名单复数错误，但 Grafana 仪表盘 JSON 文件中同样引用了错误的复数指标名 `yunshu_http_requests_total`，尚未同步修复。这会导致仪表盘对应面板无数据展示。

**需要修复的文件和位置**

1. `monitoring/grafana/dashboards/yunshu-monitor.json`
   - 第 60 行: `"expr": "rate(yunshu_http_requests_total[5m])"`
   - 第 375 行: `"expr": "rate(yunshu_http_requests_total[5m])"`

2. `monitoring/grafana/dashboards/yunshu-alerts-monitor.json`
   - 第 339 行: `"expr": "sum(rate(yunshu_http_requests_total{status=~\"5..\"}[5m])) / sum(rate(yunshu_http_requests_total[5m]))"`

3. `monitoring/grafana/dashboards/yunshu-full-monitoring.json`
   - 第 540 行: `"expr": "sum(rate(yunshu_http_requests_total[5m])) by (endpoint)"`
   - 第 2164 行: `"definition": "label_values(yunshu_http_requests_total, endpoint)"`
   - 第 2172 行: `"query": "label_values(yunshu_http_requests_total, endpoint)"`

**修复方式**

全局替换 `yunshu_http_requests_total` → `yunshu_http_request_total`（注意不要影响 `yunshu_http_request_duration_seconds_bucket` 等其他指标名）。

**修复后验证**

- 重启 Grafana 容器或等待 provisioning 自动刷新（`updateIntervalSeconds: 10`）
- 打开每个仪表盘，确认对应面板有数据展示
- 特别关注：请求速率趋势面板、5xx 错误率面板、endpoint 筛选下拉框

### Acceptance Criteria

- [ ] 3 个 Grafana 仪表盘 JSON 文件中的 5 处 `yunshu_http_requests_total` 全部改为 `yunshu_http_request_total`
- [ ] 替换不影响 `yunshu_http_request_duration_seconds_bucket` 等正确指标名
- [ ] Grafana 重启后所有仪表盘面板正常展示数据
- [ ] endpoint 筛选下拉框能正确加载选项（依赖 `label_values` 查询）
- [ ] 截图对比修复前后的仪表盘展示效果

### Links
- 关联修复: `1148538f`（已修复规则文件，本任务修复仪表盘）
- 关联文档: `docs/TECH_SHARING_ALERT_SILENT_FAILURE.md`

---

## 任务 3：录制规则增加"兜底值使用中"告警

### Summary
录制规则增加"指标存在性告警"：当主查询持续返回空、仅靠兜底值运行时触发提醒

### Issue Type
Story

### Priority
P2 — Medium

### Components
monitoring / alerting

### Labels
`enhancement` `monitoring` `observability` `silent-failure`

### Due Date
2026-07-25（1 月内）

### Estimate
2 人日

### Description

**背景**

本次 Bug 能潜伏数周的根本原因是 `or on() vector(0)` 兜底机制让"指标不存在"伪装成"指标值为 0"。兜底机制本身是好的（保证计算不中断），但缺少可观测性——当兜底值被使用时，没有任何提醒。

**目标**

为关键录制规则增加"指标存在性告警"，当主查询持续返回空向量、仅靠 `or on() vector(...)` 兜底运行时，触发告警提醒运维人员排查指标缺失原因。

**技术方案**

1. 在 `health_recording_rules.yml` 中为每个使用兜底机制的录制规则，新增一个配套的存在性检查告警：

   ```yaml
   - alert: YunshuMetricMissing
     expr: |
       absent(yunshu_http_request_total)
       or
       (yunshu:health:stability:error_rate and on() vector(0))
     # 当主查询返回空时，absent() 返回 1，触发告警
     for: 10m
     labels:
       severity: warning
       category: monitoring
     annotations:
       summary: "录制规则依赖的指标缺失"
       description: "yunshu_http_request_total 指标在过去 10 分钟内未被抓取，录制规则 yunshu:health:stability:error_rate 正在使用兜底值，可能掩盖真实数据"
       action: "检查 Prometheus targets 状态，确认应用 /metrics 端点正常暴露该指标"
   ```

2. 覆盖所有使用 `or on() vector(...)` 的关键录制规则：
   - `yunshu:health:stability:error_rate` → 依赖 `yunshu_http_request_total`
   - `yunshu:health:performance:p99_latency` → 依赖 `yunshu_http_request_duration_seconds_bucket`
   - `yunshu:health:quality:task_success_rate` → 依赖 `yunshu_task_total`
   - `yunshu:health:efficiency:llm_success_rate` → 依赖 `yunshu_llm_calls_total`
   - `yunshu:health:availability:uptime` → 依赖 `up`

3. 在 Grafana 健康度仪表盘新增"指标存在性"面板，展示当前哪些录制规则正在使用兜底值。

### Acceptance Criteria

- [ ] 为至少 5 个关键录制规则新增配套的指标存在性告警
- [ ] 告警在指标缺失 10 分钟后触发，severity=warning
- [ ] 告警 annotation 包含：缺失的指标名、受影响的录制规则、排查建议
- [ ] Grafana 健康度仪表盘新增"指标存在性状态"面板
- [ ] 测试验证：停止应用后 10 分钟内告警触发；恢复后告警自动解除
- [ ] 不产生误报：正常指标波动（如短时间无请求）不应触发存在性告警

### Links
- 关联文档: `docs/TECH_SHARING_ALERT_SILENT_FAILURE.md` 教训 1
- 关联规则文件: `monitoring/health_recording_rules.yml`

---

## 任务 4：错误注入演练纳入定期巡检

### Summary
告警链路错误注入演练纳入定期巡检，验证告警系统能否发现故障

### Issue Type
Story

### Priority
P2 — Medium

### Components
monitoring / ops

### Labels
`enhancement` `monitoring` `chaos-engineering` `alert-verification`

### Due Date
2026-07-25（1 月内）

### Estimate
1.5 人日

### Description

**背景**

本次 Bug 暴露了一个深层问题：告警系统"看起来在正常工作"（规则加载成功、health=ok），但实际上对所有异常视而不见。这种"静默失效"无法通过常规巡检发现，必须通过主动错误注入验证告警链路是否真正通畅。

**目标**

将 `tests/integration/test_alert_chain.py` 改造为可定期执行的巡检脚本，纳入运维巡检流程，每周自动运行一次，验证告警链路端到端通畅性。

**技术方案**

1. 改造 `test_alert_chain.py` 为可配置的巡检脚本：
   - 支持配置注入的端点、次数、间隔
   - 支持配置预期触发的告警名、阈值
   - 输出结构化巡检报告（JSON + Markdown）
   - 巡检失败时发送通知（邮件/钉钉/Slack）

2. 配置定时任务（cron 或 GitHub Actions scheduled workflow）：
   ```yaml
   # .github/workflows/alert-chain-patrol.yml
   name: Alert Chain Patrol
   schedule:
     - cron: '0 9 * * 1'  # 每周一 9:00 执行
   ```

3. 巡检报告归档至 `docs/patrol-reports/` 目录，保留 12 周。

4. 巡检覆盖范围：
   - HTTP 5xx 错误注入 → 验证 YunshuHighErrorRate / YunshuCriticalHTTPErrors
   - 高延迟模拟 → 验证 YunshuHighLatencyP99
   - 服务停止 → 验证 YunshuServiceDown
   - 健康度评分变化 → 验证 stability_score / overall_score 正确响应

### Acceptance Criteria

- [ ] `test_alert_chain.py` 支持配置文件（端点、次数、预期告警）
- [ ] 巡检脚本输出 JSON + Markdown 双格式报告
- [ ] GitHub Actions 每周定时执行巡检
- [ ] 巡检失败时自动通知（至少一种渠道：邮件/钉钉/Slack）
- [ ] 巡检报告归档至 `docs/patrol-reports/`，保留 12 周
- [ ] 至少覆盖 3 种故障场景（5xx 错误、高延迟、服务停止）
- [ ] 首次巡检执行成功并产出报告
- [ ] 巡检过程不影响生产环境正常服务（使用测试端点 /api/test/*）

### Links
- 关联脚本: `tests/integration/test_alert_chain.py`
- 关联文档: `docs/TECH_SHARING_ALERT_SILENT_FAILURE.md` 教训 4
- 关联恢复监控: `tests/integration/monitor_recovery.py`

---

## 附：任务汇总

| 序号 | 类型 | 优先级 | 标题 | 估算 | 截止 |
|------|------|--------|------|------|------|
| 1 | Task | P1 | CI 集成指标名校验 | 2-3 人日 | 07-09 |
| 2 | Bug | P1 | Grafana 仪表盘 JSON 同步修复 | 0.5 人日 | 07-02 |
| 3 | Story | P2 | 录制规则增加"兜底值使用中"告警 | 2 人日 | 07-25 |
| 4 | Story | P2 | 错误注入演练纳入定期巡检 | 1.5 人日 | 07-25 |

**总估算**: 6-7.5 人日  
**建议分配**: 任务 2 优先处理（快速修复），任务 1/3/4 可并行推进
