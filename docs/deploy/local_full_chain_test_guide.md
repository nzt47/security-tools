# 本地完整数据链路测试指南

## 概述

本文档描述如何在本地启动完整可观测性数据链路，验证从指标采集到趋势报告生成的端到端流程。

## 数据链路架构

```
┌─────────────────────────┐
│ visibility_report.py    │
│ --serve-metrics         │  指标生产者（exporter）
│ 端口 9101 /metrics      │
└──────────┬──────────────┘
           │ Prometheus scrape (30s 间隔)
           ▼
┌─────────────────────────┐
│ Prometheus              │
│ 端口 9091               │  指标存储 + 查询 API
│ /api/v1/query_range     │
└──────────┬──────────────┘
           │ HTTP 查询
           ▼
┌─────────────────────────┐
│ generate_visibility_    │
│ trend.py                │  趋势报告消费者
│ --prometheus-url        │
└─────────────────────────┘
```

## 前置条件

- Docker Desktop 已安装并运行
- Python 3.11+ 已安装
- 项目根目录：`c:\Users\Administrator\agent`

## 方案一：Docker Compose 完整链路（推荐生产验证）

### 步骤 1：启动 Docker Compose

```powershell
cd c:\Users\Administrator\agent
docker compose -f deploy/monitoring/docker-compose.yml up -d
```

### 步骤 2：验证服务健康

```powershell
# 等待 30 秒让服务启动
Start-Sleep -Seconds 30

# 检查 4 个容器状态
docker compose -f deploy/monitoring/docker-compose.yml ps

# 验证 visibility-exporter /metrics 端点
curl http://localhost:9101/metrics | Select-Object -First 5

# 验证 Prometheus /-/healthy
curl http://localhost:9091/-/healthy

# 验证 Prometheus 抓取目标
curl http://localhost:9091/api/v1/targets | python -m json.tool | Select-String "health"
```

### 步骤 3：等待数据采集

```powershell
# visibility-exporter 每 5 分钟刷新一次指标
# Prometheus 每 30 秒抓取一次
# 等待至少 2 分钟确保有数据
Start-Sleep -Seconds 120

# 验证 Prometheus 已存储指标
curl "http://localhost:9091/api/v1/query?query=yunshu_visibility_overall_status"
```

### 步骤 4：生成趋势报告

```powershell
$env:PYTHONUTF8=1
python scripts/generate_visibility_trend.py `
    --prometheus-url http://localhost:9091 `
    --period weekly `
    --output docs/observability/trends/ `
    --non-interactive `
    --verbose
```

### 步骤 5：验证报告

```powershell
# 检查生成的文件
Get-ChildItem docs/observability/trends/visibility_trend_weekly_*.md
Get-ChildItem docs/observability/trends/visibility_trend_weekly_*.html
Get-ChildItem docs/observability/trends/visibility_trend_weekly_*.json
```

### 步骤 6：停止服务

```powershell
docker compose -f deploy/monitoring/docker-compose.yml down
```

## 方案二：Mock 服务 + 真实采集（推荐本地开发验证）

当 Docker Prometheus 历史数据不足时，使用 Mock 服务模拟 7 天历史数据，
同时通过 `--real-source` 参数采集真实当前指标。

### 步骤 1：启动 Mock 服务（带真实采集）

```powershell
$env:PYTHONUTF8=1
# --real-source 启动时调用 MetricCollector 采集真实指标，替换模拟数据最新点
python scripts/mock_prometheus_server.py --port 9099 --real-source
```

### 步骤 2：验证 Mock 服务

```powershell
# 在另一个终端验证
curl http://localhost:9099/-/healthy
# 预期输出: Prometheus Server is Healthy.

# 验证真实指标已替换（structured_log_coverage 应接近 63.7）
curl "http://localhost:9099/api/v1/query?query=yunshu_visibility_runtime_structured_log_coverage"
```

### 步骤 3：生成趋势报告

```powershell
$env:PYTHONUTF8=1
python scripts/generate_visibility_trend.py `
    --prometheus-url http://localhost:9099 `
    --period weekly `
    --output docs/observability/trends_mock/ `
    --non-interactive `
    --verbose
```

### 步骤 4：验证报告

```powershell
# 预期：16 个指标全部 success，169 个数据点/指标
Get-ChildItem docs/observability/trends_mock/visibility_trend_weekly_*.html
# 在浏览器中打开 HTML 文件查看 SVG 趋势图
Start-Process docs/observability\trends_mock\visibility_trend_weekly_*.html
```

### 步骤 5：停止 Mock 服务

```powershell
# 在 Mock 服务终端按 Ctrl+C
# 或通过端口查找进程停止
Get-NetTCPConnection -LocalPort 9099 | Select-Object -ExpandProperty OwningProcess | ForEach-Object { Stop-Process -Id $_ -Force }
```

## 方案三：纯 Mock 服务（最快验证）

不采集真实指标，使用纯模拟数据（适合快速验证趋势图渲染逻辑）。

```powershell
# 启动纯 Mock 服务
$env:PYTHONUTF8=1
python scripts/mock_prometheus_server.py --port 9099

# 生成周报
python scripts/generate_visibility_trend.py `
    --prometheus-url http://localhost:9099 `
    --period weekly `
    --output docs/observability/trends_mock/ `
    --non-interactive

# 生成月报
python scripts/generate_visibility_trend.py `
    --prometheus-url http://localhost:9099 `
    --period monthly `
    --output docs/observability/trends_mock/ `
    --non-interactive
```

## 端口规划

| 服务 | 端口 | 用途 |
|------|------|------|
| visibility-exporter | 9101 | 四层可见性指标 /metrics |
| Prometheus | 9091 | 指标存储 + 查询 API |
| Grafana | 3001 | 可视化看板 |
| node-exporter | 9100 | 主机指标 |
| Mock Prometheus | 9099 | 本地验证用 Mock 服务 |

## 排错

### visibility-exporter 启动失败

```powershell
# 查看日志
docker logs yunshu-prod-visibility-exporter

# 常见原因：
# 1. PyYAML 未安装 — docker-compose.yml 已配置 pip install pyyaml
# 2. config.yaml 不存在 — 确保项目根目录有 config.yaml
# 3. 权限问题 — 确保挂载的目录可读
```

### Prometheus 抓取失败

```powershell
# 检查抓取目标状态
curl http://localhost:9091/api/v1/targets | python -m json.tool

# visibility-exporter 应显示 "health": "up"
# 如果显示 "down"，检查 visibility-exporter 容器日志
```

### 趋势报告生成失败

```powershell
# 检查退出码
# 0 = 成功
# 1 = 降级（部分指标失败）
# 2 = 失败（Prometheus 不可达或全部查询失败）

# 查看详细日志
$env:PYTHONUTF8=1
python scripts/generate_visibility_trend.py `
    --prometheus-url http://localhost:9091 `
    --period weekly `
    --non-interactive `
    --verbose 2>&1 | Select-String "ERROR|WARN|TREND_ERR"
```

## 验收清单

- [ ] Docker Compose 4 个容器全部 healthy
- [ ] visibility-exporter /metrics 返回 yunshu_visibility_ 前缀指标
- [ ] Prometheus /api/v1/targets 显示 visibility-exporter 为 up
- [ ] generate_visibility_trend.py 退出码为 0
- [ ] 生成 .md / .html / .json 三个文件
- [ ] HTML 文件包含 16 个 SVG 趋势图
- [ ] JSON 元数据 series_count=16, errors=[]
