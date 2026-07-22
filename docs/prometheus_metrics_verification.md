# Prometheus 指标端到端验证指南

> 目标：验证 `yunshu_skill_hallucination_total` / `yunshu_skill_eval_score` 指标是否成功从应用发射到 Prometheus

## 验证链路

```
app_server.py /metrics 端点  →  Prometheus scrape  →  Grafana / Alertmanager
       ↑ emit_metric()               ↑ /api/v1/query
```

## 前置条件

- Docker Desktop 已启动（daemon 就绪）
- 项目根目录：`c:\Users\Administrator\agent`

## 步骤 1：启动监控栈（Prometheus + Grafana）

```powershell
# 在项目根目录执行
docker-compose -f docker-compose.monitoring.yml up -d

# 验证服务就绪
docker-compose -f docker-compose.monitoring.yml ps
# 期望输出: prometheus Up, grafana Up

# 检查 Prometheus 健康
curl http://localhost:9090/-/healthy
# 期望: Prometheus is Healthy.
```

## 步骤 2：启动应用（暴露 /metrics 端点）

```powershell
# 后台启动 app_server（监听 127.0.0.1:5678）
# 注意：首次启动可能触发 SentenceTransformer 模型下载，网络不通会卡住
Start-Process python -ArgumentList "app_server.py" -NoNewWindow

# 等待就绪（最多 30s）
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    try { $r = Invoke-WebRequest "http://127.0.0.1:5678/api/skills-mgmt/health" -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { break } } catch {}
    Start-Sleep -Seconds 2
}

# 检查 /metrics 端点是否暴露 yunshu_skill_* 指标
curl http://127.0.0.1:5678/metrics | Select-String "yunshu_skill_"
```

**期望输出**（部分）：
```
# HELP yunshu_skill_eval_score ...
# TYPE yunshu_skill_eval_score histogram
yunshu_skill_eval_score_bucket{...,le="0.5"} 0
...
# HELP yunshu_skill_hallucination_total ...
# TYPE yunshu_skill_hallucination_total counter
yunshu_skill_hallucination_total{skill_id="..."} 0
```

## 步骤 3：触发技能调用产生指标

```powershell
# 通过 /api/skills-mgmt/record_execution 触发 eval_score 上报
$body = @{
    skill_id = "memory_summary"
    success = $true
    latency_ms = 100
    eval_score = @{
        task_success = $true
        instruction_followed = $true
        hallucination_detected = $false
        score = 0.92
    }
} | ConvertTo-Json

Invoke-WebRequest "http://127.0.0.1:5678/api/skills-mgmt/record_execution" `
    -Method POST -Body $body -ContentType "application/json" -UseBasicParsing

# 触发幻觉场景（hallucination_detected=true → yunshu_skill_hallucination_total +1）
$bodyHalu = @{
    skill_id = "memory_summary"
    success = $false
    latency_ms = 200
    eval_score = @{
        task_success = $false
        instruction_followed = $false
        hallucination_detected = $true
        score = 0.3
    }
} | ConvertTo-Json

Invoke-WebRequest "http://127.0.0.1:5678/api/skills-mgmt/record_execution" `
    -Method POST -Body $bodyHalu -ContentType "application/json" -UseBasicParsing
```

## 步骤 4：查询 Prometheus 验证指标抓取

```powershell
# 4.1 查询 yunshu_skill_hallucination_total（幻觉计数器）
curl "http://localhost:9090/api/v1/query?query=yunshu_skill_hallucination_total" | ConvertFrom-Json | ConvertTo-Json -Depth 10

# 期望: status="success", result[0].value[1] >= "1"

# 4.2 查询 yunshu_skill_eval_score_count（评估次数）
curl "http://localhost:9090/api/v1/query?query=yunshu_skill_eval_score_count" | ConvertFrom-Json | ConvertTo-Json -Depth 10

# 期望: status="success", result[0].value[1] >= "2"

# 4.3 查询 eval_score histogram 分位数（P50）
curl "http://localhost:9090/api/v1/query?query=histogram_quantile(0.5,sum(rate(yunshu_skill_eval_score_bucket[5m]))by(le))" | ConvertFrom-Json | ConvertTo-Json -Depth 10

# 4.4 检查 Prometheus 是否成功抓取 app_server
curl "http://localhost:9090/api/v1/targets" | ConvertFrom-Json | ConvertTo-Json -Depth 10 | Select-String "yunshu-app"
```

## 步骤 5：验证 Grafana 仪表盘

```powershell
# 打开 Grafana（admin/admin）
Start-Process "http://localhost:3000"

# 在 Grafana 中：
# 1. 左侧菜单 → Dashboards
# 2. 找到 "云枢技能质量与幻觉监控" 仪表盘
# 3. 验证 6 个 panel 是否显示数据
```

## 验证结果判定

| 检查项 | 期望 | 失败排查 |
|-------|------|---------|
| `/metrics` 含 `yunshu_skill_*` | ✅ 看到 3+ 指标 | app_server.py 未注册指标，查 business_metrics.py |
| Prometheus target up | ✅ yunshu-app=up | Prometheus scrape_config 未配置，查 prometheus.yml |
| `hallucination_total` 查询有值 | ✅ value >= 1 | 指标未被触发，重新调用 record_execution |
| Grafana 仪表盘有数据 | ✅ panel 显示曲线 | datasource 未配置，查 datasources/prometheus.yml |

## 清理

```powershell
# 停止 app_server
Get-Process python | Stop-Process -Force

# 停止监控栈
docker-compose -f docker-compose.monitoring.yml down
```

## 备注

- **验证脚本 `scripts/verify_observability_fields.py`** 用 mock 捕获 `emit_metric` 调用，验证 metric name/value/labels/kind —— 这是**逻辑层验证**，等同于"metrics 是否成功发射"
- **本指南**验证的是**物理层**：metrics 是否从 `/metrics` 端点暴露 → Prometheus 抓取 → 可查询
- 两层验证互补：逻辑层快速回归，物理层端到端确认
