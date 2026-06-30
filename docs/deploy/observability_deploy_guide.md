# 生产环境可观测性部署指南

> **适用范围**：任务 5（P3 — 生产环境可观测性部署 + 可见性看板自助化）交付的完整部署套件
> **版本**：v1.0.0（2026-06-28）
> **维护者**：可观测性团队
> **关联文件**：`deploy/glitchtip/`、`deploy/monitoring/`、`scripts/visibility_report.py`、`scripts/generate_visibility_trend.py`

---

## 目录

1. [架构总览](#1-架构总览)
2. [前置条件](#2-前置条件)
3. [GlitchTip 部署（错误上报）](#3-glitchtip-部署错误上报)
4. [rrweb Session Replay 上线（前端回放）](#4-rrweb-session-replay-上线前端回放)
5. [Prometheus + Grafana 部署（指标看板）](#5-prometheus--grafana-部署指标看板)
6. [可见性看板自助化（四层指标接入）](#6-可见性看板自助化四层指标接入)
7. [自动周报/月报趋势图](#7-自动周报月报趋势图)
8. [端口规划与冲突避免](#8-端口规划与冲突避免)
9. [健康检查与告警](#9-健康检查与告警)
10. [排错手册](#10-排错手册)
11. [验收清单](#11-验收清单)

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                         生产环境可观测性栈                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    Sentry SDK     ┌──────────────────────┐   │
│  │  yunshu-ui   │ ─────────────────▶│   GlitchTip (8001)   │   │
│  │  (前端)      │    rrweb 1% 采样   │  错误 + Replay 上报   │   │
│  └──────────────┘ ─────────────────▶│                      │   │
│         │                            └──────────────────────┘   │
│         │ /metrics (5678)                       │                │
│         ▼                                        │                │
│  ┌──────────────┐    /api/replay/upload         │                │
│  │  yunshu-app  │ ◀─────────────────────────────┘                │
│  │  (后端 API)  │                                                 │
│  └──────────────┘                                                 │
│         │                                                         │
│         │ /metrics (5678)                  ┌──────────────────┐  │
│         └────────────────────────────────▶│  Prometheus (9091)│  │
│                                           │  指标采集 + 告警   │  │
│                                           └──────────────────┘  │
│                                                    │             │
│  ┌──────────────────────┐                         │             │
│  │ visibility-exporter  │ ──── /metrics (9101) ───┘             │
│  │ (四层可见性指标服务)  │                                       │
│  └──────────────────────┘                                       │
│                                                    │             │
│                                                    ▼             │
│                                           ┌──────────────────┐  │
│                                           │  Grafana (3001)   │  │
│                                           │  看板 + 报警规则  │  │
│                                           └──────────────────┘  │
│                                                    │             │
│                                                    ▼             │
│                                           ┌──────────────────┐  │
│                                           │ generate_visibility│ │
│                                           │ _trend.py (cron)  │  │
│                                           │ 周报/月报趋势图    │  │
│                                           └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

**四层可见性指标流向**：
```
scripts/visibility_report.py 采集
    ↓ export_to_prometheus()
visibility-exporter 容器暴露 /metrics
    ↓ Prometheus scrape (job: visibility-exporter)
Prometheus TSDB 存储
    ↓ Grafana datasource
Grafana 看板渲染（visibility_four_layers.json + business_metrics.json）
    ↓ query_range API
scripts/generate_visibility_trend.py 生成周报/月报
```

---

## 2. 前置条件

### 2.1 系统要求

| 组件 | 最低版本 | 推荐配置 |
| --- | --- | --- |
| Docker Engine | 20.10+ | 24.0+ |
| Docker Compose | v2.0+ | v2.20+ |
| 可用内存 | 4 GB | 8 GB |
| 可用磁盘 | 20 GB | 50 GB（含 30 天指标保留） |
| Python | 3.11+ | 3.11+（visibility-exporter 容器使用） |

### 2.2 网络端口

生产环境端口规划（与开发环境完全隔离，可并行运行）：

| 服务 | 生产端口 | 开发环境端口 | 说明 |
| --- | --- | --- | --- |
| GlitchTip Web | 8001 | 8000 | 错误上报 UI |
| GlitchTip Postgres | 5434 | 5433 | 数据库 |
| GlitchTip Redis | 6381 | 6380 | 缓存 |
| Prometheus | 9091 | 9090 | 指标采集 |
| Grafana | 3001 | 3000 | 看板 UI |
| visibility-exporter | 9101 | — | 四层可见性指标 |
| node-exporter | 9100 | — | 主机指标 |
| yunshu-app | 5678 | 5678 | 业务后端（已存在） |

### 2.3 准备配置文件

```bash
# 1. GlitchTip 环境变量
cp deploy/glitchtip/.env.example deploy/glitchtip/.env
# 编辑 .env，设置 SECRET_KEY、POSTGRES_PASSWORD、DJANGO_SUPERUSER 等

# 2. Grafana 管理员密码（可选，默认 admin/admin）
export GF_SECURITY_ADMIN_PASSWORD=your_secure_password
```

---

## 3. GlitchTip 部署（错误上报）

### 3.1 启动 GlitchTip

```bash
cd deploy/glitchtip

# 启动 4 个服务：postgres / redis / web / worker
docker compose up -d

# 等待服务就绪（约 30 秒）
docker compose ps

# 预期输出：
# NAME                 STATUS     PORTS
# glitchtip-postgres   healthy    5432→5434
# glitchtip-redis      healthy    6379→6381
# glitchtip-web        healthy    8000→8001
# glitchtip-worker     running
```

### 3.2 配置 Sentry SDK DSN

1. 访问 `http://localhost:8001`，使用 `.env` 中配置的 `DJANGO_SUPERUSER_*` 登录
2. 创建组织（Organization）→ 创建项目（Project，选择 Platform = Python）
3. 获取 DSN，格式如：`http://<public-key>@localhost:8001/1`
4. 在后端配置中注入 DSN：

```bash
# 方式一：环境变量（推荐）
export SENTRY_DSN="http://<public-key>@localhost:8001/1"

# 方式二：写入 error_reporting_config.py
# 参考 agent/error_reporting_config.py
```

### 3.3 验证错误上报

```bash
# 使用 deploy/glitchtip/verify_glitchtip_integration.py 验证
python deploy/glitchtip/verify_glitchtip_integration.py --dsn "$SENTRY_DSN"

# 预期输出：
# [OK] Sentry SDK 初始化成功
# [OK] 测试错误已上报
# [OK] 事件可在 GlitchTip UI 查看
```

### 3.4 后端集成

`agent/error_reporting_config.py` 已内置 Sentry SDK 集成，自动捕获：
- 未处理异常（Exception）
- HTTP 请求错误（4xx/5xx）
- 性能事务（Tracing，采样率可配）

**关键配置项**（通过环境变量）：
- `SENTRY_DSN`：必填，GlitchTip DSN
- `SENTRY_ENVIRONMENT`：环境标识（production / staging）
- `SENTRY_TRACES_SAMPLE_RATE`：性能采样率（生产建议 0.1）

---

## 4. rrweb Session Replay 上线（前端回放）

### 4.1 前端依赖

`yunshu-ui/package.json` 已添加依赖：
```json
{
  "dependencies": {
    "rrweb": "^2.0.0-alpha.17",
    "rrweb-player": "^2.0.0-alpha.4",
    "@sentry/react": "^8.0.0",
    "pako": "^2.1.0"
  }
}
```

安装依赖：
```bash
cd yunshu-ui
npm install
# 或 pnpm install / yarn install
```

### 4.2 环境变量配置

在 `yunshu-ui/.env` 中配置：
```bash
# Sentry DSN（与后端共用 GlitchTip）
VITE_SENTRY_DSN=http://<public-key>@localhost:8001/1

# 环境标识
VITE_SENTRY_ENVIRONMENT=production

# 性能采样率（Sentry Tracing）
VITE_SENTRY_TRACES_SAMPLE_RATE=0.1

# Sentry Replay 采样率（Sentry 内置 Replay）
VITE_SENTRY_REPLAYS_SAMPLE_RATE=0.01

# rrweb 独立采样率（1% 生产环境）
VITE_REPLAY_SAMPLE_RATE=0.01

# rrweb 录制数据上传端点
VITE_REPLAY_UPLOAD_URL=/api/replay/upload

# 应用版本
VITE_APP_VERSION=1.0.0
```

### 4.3 集成入口

`yunshu-ui/src/main.tsx` 已集成：
```typescript
import { initObservability } from './observability';
initObservability();
```

可观测性模块结构：
- `src/observability/index.ts`：统一初始化入口
- `src/observability/sentry.ts`：Sentry SDK 配置
- `src/observability/sessionReplay.ts`：rrweb 录制 + 上传逻辑

### 4.4 采样策略

| 场景 | 采样率 | 说明 |
| --- | --- | --- |
| 生产环境 rrweb | 1% | `VITE_REPLAY_SAMPLE_RATE=0.01` |
| 生产环境 Sentry Replay | 1% | `VITE_SENTRY_REPLAYS_SAMPLE_RATE=0.01` |
| 性能 Tracing | 10% | `VITE_SENTRY_TRACES_SAMPLE_RATE=0.1` |
| 错误事件 | 100% | 错误始终上报 |

### 4.5 后端接收端点

录制数据上传到 `POST /api/replay/upload`（参考 `agent/server_routes/routes_replay.py`）：
- 请求体：`{ "events": "<base64-gzip-data>", "session_id": "...", "event_count": N }`
- 响应：`{ "ok": true, "stored": N }`
- 存储：`agent/monitoring/replay_storage.py` 负责持久化

### 4.6 验证

```bash
# 1. 启动前端
cd yunshu-ui && npm run dev

# 2. 访问应用，操作页面
# 3. 检查 GlitchTip UI → Replay 标签页
# 4. 检查后端日志是否收到 /api/replay/upload 请求
```

---

## 5. Prometheus + Grafana 部署（指标看板）

### 5.1 启动监控栈

```bash
cd deploy/monitoring

# 启动 4 个服务：prometheus / grafana / visibility-exporter / node-exporter
docker compose up -d

# 检查服务状态
docker compose ps

# 预期输出：
# NAME                    STATUS     PORTS
# prometheus              healthy    9090→9091
# grafana                 healthy    3000→3001
# visibility-exporter     healthy    9101→9101
# node-exporter           running    9100→9100
```

### 5.2 Prometheus 抓取配置

`deploy/monitoring/prometheus/prometheus.yml` 配置了 5 个 scrape job：

| Job Name | Target | 间隔 | 说明 |
| --- | --- | --- | --- |
| visibility-exporter | visibility-exporter:9101 | 15s | 四层可见性指标 |
| yunshu-app | host.docker.internal:5678 | 15s | 业务后端指标 |
| prometheus | localhost:9090 | 30s | 自监控 |
| node-exporter | node-exporter:9100 | 30s | 主机指标 |
| grafana | grafana:3000 | 60s | Grafana 自监控 |

验证抓取状态：
```bash
# 访问 Prometheus UI
open http://localhost:9091/targets

# 所有 job 状态应为 UP
```

### 5.3 Grafana 看板

Grafana 通过 provisioning 自动加载看板：
- 配置目录：`deploy/monitoring/grafana/provisioning/`
- 数据源：`datasources/prometheus.yml`（自动配置 Prometheus 数据源）
- 看板目录：`dashboards/dashboard.yml`（自动加载 JSON 看板）

**预置看板**：

| 看板文件 | UID | 面板数 | 说明 |
| --- | --- | --- | --- |
| `visibility_four_layers.json` | visibility-four-layers | 12 | 四层可见性指标趋势 |
| `business_metrics.json` | business-metrics | 8 | 业务指标（HTTP/熔断/限流等） |

访问 Grafana：
```bash
# 默认账号：admin / admin（或通过 GF_SECURITY_ADMIN_PASSWORD 配置）
open http://localhost:3001

# 在 Dashboards 页面应能看到两个预置看板
```

### 5.4 visibility-exporter 容器

`deploy/monitoring/docker-compose.yml` 中的 `visibility-exporter` 服务：
- 镜像：`python:3.11-slim`
- 启动命令：`python scripts/visibility_report.py --serve-metrics --port 9101 --host 0.0.0.0 --refresh-interval 300`
- 挂载项目根目录到容器内 `/app`
- 每 5 分钟重新采集四层可见性指标并刷新快照

**暴露端点**：
- `GET /metrics`：Prometheus exposition 格式指标
- `GET /health`：健康检查 JSON（含依赖项状态）

---

## 6. 可见性看板自助化（四层指标接入）

### 6.1 指标导出机制

`scripts/visibility_report.py` 新增 `--export-metrics` 参数，支持三种模式：

```bash
# 模式一：一次性导出到 stdout
python scripts/visibility_report.py --export-metrics

# 模式二：导出到文件
python scripts/visibility_report.py --export-metrics --metrics-output /tmp/visibility_metrics.txt

# 模式三：启动 HTTP 服务持续暴露（生产推荐）
python scripts/visibility_report.py --serve-metrics --port 9101 --host 0.0.0.0 --refresh-interval 300
```

### 6.2 导出的指标清单

所有指标统一前缀 `yunshu_visibility_`，遵循 `yunshu_<模块>_<动作>` 命名规范：

| 指标名 | 类型 | 标签 | 说明 |
| --- | --- | --- | --- |
| `yunshu_visibility_up` | gauge | — | 服务存活探针（恒为 1） |
| `yunshu_visibility_overall_status` | gauge | status | 总体状态（0=pass, 1=fail, 2=degraded） |
| `yunshu_visibility_threshold_violations_total` | gauge | — | 阈值违规项总数 |
| `yunshu_visibility_report_duration_seconds` | gauge | — | 报告生成耗时 |
| `yunshu_visibility_report_timestamp_seconds` | gauge | — | 报告生成时间戳（用于过期检测） |
| `yunshu_visibility_layer_passed` | gauge | layer, success | 各层是否达标（0/1） |
| `yunshu_visibility_runtime_*` | gauge | layer, success | 运行时层明细（3 项） |
| `yunshu_visibility_verification_*` | gauge | layer, success | 验证层明细（3 项） |
| `yunshu_visibility_business_*` | gauge | layer, success | 业务层明细（3 项） |
| `yunshu_visibility_architecture_*` | gauge | layer, success | 架构层明细（4 项） |

**success 标签语义**：
- `success="true"`：指标达标（或逆向指标未超阈）
- `success="false"`：指标未达标

### 6.3 Grafana 看板变量

`visibility_four_layers.json` 看板支持变量筛选：

| 变量名 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `datasource` | datasource | Prometheus | 数据源选择 |
| `layer` | query | runtime | 按层筛选（runtime/verification/business/architecture） |

**按日期范围筛选**：Grafana 看板顶部时间选择器支持相对时间（如「过去 7 天」「过去 30 天」）和绝对时间范围。

### 6.4 告警规则

`deploy/monitoring/prometheus/alert_rules.yml` 定义了 10 条告警：

| 告警名 | 触发条件 | 严重度 |
| --- | --- | --- |
| `VisibilityExporterDown` | `up{job="visibility-exporter"} == 0` 持续 1m | critical |
| `VisibilityReportStale` | `time() - yunshu_visibility_report_timestamp_seconds > 600` | critical |
| `VisibilityOverallFailed` | `yunshu_visibility_overall_status == 1` 持续 5m | critical |
| `VisibilityLayerRuntimeFailed` | `yunshu_visibility_layer_passed{layer="runtime",success="false"} == 1` | warning |
| `VisibilityLayerVerificationFailed` | 同上（verification 层） | warning |
| `VisibilityLayerBusinessFailed` | 同上（business 层） | warning |
| `VisibilityLayerArchitectureFailed` | 同上（architecture 层） | warning |
| `VisibilityThresholdViolationsHigh` | `yunshu_visibility_threshold_violations_total > 5` | warning |
| `VisibilityReportDurationHigh` | `yunshu_visibility_report_duration_seconds > 10` | warning |
| `VisibilityOverallDegraded` | `yunshu_visibility_overall_status == 2` 持续 10m | warning |

**预聚合规则**（`recording_rules.yml`）：
- `yunshu_visibility_health_score`：综合健康分（0-100）
- `yunshu_visibility_passing_layers_count`：通过层数（0-4）

---

## 7. 自动周报/月报趋势图

### 7.1 脚本说明

`scripts/generate_visibility_trend.py` 从 Prometheus 拉取历史数据，生成趋势报告：

```bash
# 周报（默认，7 天，1 小时一个点）
python scripts/generate_visibility_trend.py \
    --prometheus-url http://localhost:9091 \
    --output docs/observability/trends/

# 月报（30 天，6 小时一个点）
python scripts/generate_visibility_trend.py \
    --period monthly \
    --prometheus-url http://localhost:9091 \
    --output docs/observability/trends/

# CI 非交互模式（失败时输出降级报告，不抛异常）
python scripts/generate_visibility_trend.py \
    --prometheus-url http://prometheus:9090 \
    --output docs/observability/trends/ \
    --non-interactive
```

### 7.2 输出文件

每次运行生成 3 个文件（`YYYYMMDD` 为当日日期）：

| 文件 | 格式 | 说明 |
| --- | --- | --- |
| `visibility_trend_weekly_YYYYMMDD.md` | Markdown | 含概览表格 + 按层详情 + 健康检查 |
| `visibility_trend_weekly_YYYYMMDD.html` | HTML | 含 SVG 折线图（无 JS 依赖） |
| `visibility_trend_weekly_YYYYMMDD.json` | JSON | 元数据（便于工具消费） |

### 7.3 查询清单

脚本并行查询 16 个指标（5 worker 线程），覆盖：

- **总体（4 项）**：overall_status / threshold_violations / passing_layers / report_duration
- **运行时层（3 项）**：structured_log_coverage / trace_coverage / health_endpoints
- **验证层（3 项）**：test_coverage / boundary_test_coverage / contract_test_count
- **业务层（3 项）**：track_event_coverage / dashboard_count / alert_rules_count
- **架构层（3 项）**：dependency_graph_nodes / rule_violations / impact_analysis_coverage

### 7.4 定时任务配置

#### 方式一：CI 定时（推荐）

在 `.github/workflows/observability-ci.yml` 中添加（参考任务 7）：
```yaml
jobs:
  weekly-trend-report:
    runs-on: ubuntu-latest
    # 每周一 02:00 UTC（北京时间 10:00）执行
    schedule:
      - cron: '0 2 * * 1'
    steps:
      - uses: actions/checkout@v4
      - name: Generate weekly trend report
        run: |
          python scripts/generate_visibility_trend.py \
            --prometheus-url ${{ secrets.PROMETHEUS_URL }} \
            --output docs/observability/trends/ \
            --non-interactive
      - name: Commit report
        run: |
          git add docs/observability/trends/
          git commit -m "docs: weekly visibility trend report"
          git push
```

#### 方式二：服务器 cron

```bash
# 编辑 crontab
crontab -e

# 每周一 02:00 生成周报
0 2 * * 1 cd /path/to/agent && python scripts/generate_visibility_trend.py \
    --prometheus-url http://localhost:9091 \
    --output docs/observability/trends/ \
    --non-interactive >> /var/log/visibility-trend.log 2>&1

# 每月 1 日 02:00 生成月报
0 2 1 * * cd /path/to/agent && python scripts/generate_visibility_trend.py \
    --period monthly \
    --prometheus-url http://localhost:9091 \
    --output docs/observability/trends/ \
    --non-interactive >> /var/log/visibility-trend-monthly.log 2>&1
```

### 7.5 退出码语义

| 退出码 | 含义 | CI 处理 |
| --- | --- | --- |
| 0 | 成功（所有指标查询成功） | 通过 |
| 1 | 降级（部分指标失败，但仍生成报告） | 通过（带 warning） |
| 2 | 失败（Prometheus 不可达或全部查询失败） | 失败（但 `--non-interactive` 会生成降级报告） |

---

## 8. 端口规划与冲突避免

生产环境（`deploy/`）与开发环境（`docker/`、`monitoring/`）端口完全隔离：

| 服务 | 生产端口（deploy/） | 开发端口（docker/、monitoring/） |
| --- | --- | --- |
| GlitchTip Postgres | 5434 | 5433 |
| GlitchTip Redis | 6381 | 6380 |
| GlitchTip Web | 8001 | 8000 |
| Prometheus | 9091 | 9090 |
| Grafana | 3001 | 3000 |

**并行运行**：两套环境可同时启动，互不冲突。

**切换环境**：
- 开发调试用 `docker/glitchtip/` 和 `monitoring/`
- 生产部署用 `deploy/glitchtip/` 和 `deploy/monitoring/`

---

## 9. 健康检查与告警

### 9.1 健康检查端点

| 服务 | 端点 | 预期响应 |
| --- | --- | --- |
| GlitchTip | `GET http://localhost:8001/health/` | 200 OK |
| Prometheus | `GET http://localhost:9091/-/healthy` | 200 OK |
| Grafana | `GET http://localhost:3001/api/health` | 200 OK + JSON |
| visibility-exporter | `GET http://localhost:9101/health` | 200 OK + JSON（含依赖状态） |
| visibility-exporter | `GET http://localhost:9101/metrics` | 200 OK + text/plain |
| yunshu-app | `GET http://localhost:5678/health` | 200 OK + JSON |

### 9.2 告警通知渠道

在 Grafana 中配置 Alerting → Contact points：
- **邮件**：SMTP 配置（参考 `grafana.ini` 或环境变量 `GF_SMTP_*`）
- **Webhook**：转发到企业微信/钉钉/Slack
- **PagerDuty**：紧急告警升级

### 9.3 告警抑制规则

`alert_rules.yml` 已配置 inhibition（避免告警风暴）：
- `VisibilityExporterDown` 触发时，抑制所有依赖它的告警
- `VisibilityOverallFailed` 触发时，抑制各层失败告警

---

## 10. 排错手册

### 10.1 GlitchTip 相关

#### 问题：GlitchTip Web 容器启动失败

**症状**：`docker compose ps` 显示 `glitchtip-web` 状态为 `unhealthy`

**排查步骤**：
```bash
# 1. 查看日志
docker compose logs glitchtip-web

# 2. 常见原因
# - POSTGRES_PASSWORD 未配置或与 .env 不一致
# - SECRET_KEY 未设置
# - 端口 8001 被占用（检查：netstat -ano | findstr 8001）

# 3. 修复后重启
docker compose down
docker compose up -d
```

#### 问题：Sentry SDK 上报失败

**症状**：后端日志显示 `Sentry SDK initialized` 但 GlitchTip UI 无事件

**排查步骤**：
```bash
# 1. 验证 DSN 可达
curl -X POST "$SENTRY_DSN" -H "Content-Type: application/json" -d '{"message":"test"}'

# 2. 检查网络
# GlitchTip Web 容器需在 yunshu-app 可达网络中
# 如 yunshu-app 运行在宿主机，DSN 应使用 host.docker.internal

# 3. 手动触发测试错误
python deploy/glitchtip/verify_glitchtip_integration.py --dsn "$SENTRY_DSN"
```

### 10.2 rrweb 相关

#### 问题：前端录制数据未上传

**症状**：操作页面后 `/api/replay/upload` 无请求

**排查步骤**：
```bash
# 1. 检查采样率配置
# VITE_REPLAY_SAMPLE_RATE=0.01 表示 1% 采样，需多次刷新页面才可能命中

# 2. 检查浏览器控制台
# 应看到 "[rrweb] recording started" 日志

# 3. 检查后端路由
# 确认 agent/server_routes/routes_replay.py 已注册
curl -X POST http://localhost:5678/api/replay/upload -H "Content-Type: application/json" -d '{"events":"test","session_id":"test","event_count":1}'

# 4. 检查 CORS 配置（如前后端跨域）
```

### 10.3 Prometheus + Grafana 相关

#### 问题：Prometheus 抓取失败（target DOWN）

**症状**：访问 `http://localhost:9091/targets` 显示某 job 状态为 `DOWN`

**排查步骤**：
```bash
# 1. 检查 target 容器是否运行
docker compose ps

# 2. 检查网络连通性
# Prometheus 容器内访问 target
docker exec -it prometheus wget -qO- http://visibility-exporter:9101/metrics

# 3. host.docker.internal 不可达（Linux 环境）
# 解决：在 docker-compose.yml 中添加
# extra_hosts:
#   - "host.docker.internal:host-gateway"
```

#### 问题：Grafana 看板无数据

**症状**：看板面板显示 "No data"

**排查步骤**：
```bash
# 1. 验证 Prometheus 数据源
# Grafana → Connections → Data Sources → Prometheus → Test

# 2. 在 Prometheus 中查询指标是否存在
curl "http://localhost:9091/api/v1/query?query=yunshu_visibility_up"

# 3. 检查时间范围
# 看板时间选择器应设置为「过去 1 小时」或更大范围

# 4. 检查指标命名
# visibility-exporter 首次启动后需等待 1 个抓取周期（15s）才有数据
```

#### 问题：visibility-exporter 容器崩溃

**症状**：`docker compose logs visibility-exporter` 显示 Python 异常

**排查步骤**：
```bash
# 1. 检查项目根目录挂载
# docker-compose.yml 中 visibility-exporter 服务的 volumes 应挂载项目根目录

# 2. 检查 Python 依赖
docker exec -it visibility-exporter pip list | grep -E "prometheus|sentry"

# 3. 手动运行脚本验证
docker exec -it visibility-exporter python /app/scripts/visibility_report.py --export-metrics
```

### 10.4 趋势报告相关

#### 问题：`generate_visibility_trend.py` 报错 `TREND_ERR_001`

**症状**：`Prometheus 不可达: http://localhost:9091`

**排查步骤**：
```bash
# 1. 验证 Prometheus 可达
curl http://localhost:9091/-/healthy

# 2. 检查 URL 是否正确
# 生产环境默认端口 9091（非 9090）

# 3. 网络问题（CI 环境）
# CI 中应使用 Prometheus 容器名或服务名
# --prometheus-url http://prometheus:9090
```

#### 问题：报告生成成功但所有指标为空

**症状**：报告中所有 series 状态为 `empty`

**排查步骤**：
```bash
# 1. 检查 visibility-exporter 是否运行
curl http://localhost:9101/metrics | grep yunshu_visibility

# 2. 检查 Prometheus 是否抓取到数据
curl "http://localhost:9091/api/v1/query?query=yunshu_visibility_up"

# 3. 检查抓取配置
# deploy/monitoring/prometheus/prometheus.yml 中 visibility-exporter job 配置
```

---

## 11. 验收清单

部署完成后，按以下清单逐项验证：

### 11.1 GlitchTip

- [ ] `docker compose ps` 显示 4 个服务均 healthy
- [ ] 访问 `http://localhost:8001` 可登录
- [ ] 运行 `verify_glitchtip_integration.py` 全部通过
- [ ] 后端日志显示 `Sentry SDK initialized`
- [ ] 触发测试错误后，GlitchTip UI 在 30 秒内显示事件

### 11.2 rrweb Session Replay

- [ ] `yunshu-ui/node_modules/` 包含 rrweb 包
- [ ] 浏览器控制台显示 `[rrweb] recording started`
- [ ] 多次刷新页面后（1% 采样），后端收到 `/api/replay/upload` 请求
- [ ] GlitchTip UI Replay 标签页可查看回放

### 11.3 Prometheus + Grafana

- [ ] `docker compose ps` 显示 4 个服务均 healthy
- [ ] 访问 `http://localhost:9091/targets` 所有 job 状态为 UP
- [ ] `curl http://localhost:9101/metrics` 返回 `yunshu_visibility_*` 指标
- [ ] 访问 `http://localhost:3001` 可登录 Grafana
- [ ] Dashboards 页面显示 `visibility_four_layers` 和 `business_metrics` 两个看板
- [ ] 看板面板有数据（非 "No data"）

### 11.4 可见性看板自助化

- [ ] `python scripts/visibility_report.py --export-metrics` 输出合法 Prometheus 格式
- [ ] 输出包含 19+ 个指标（含 `yunshu_visibility_up`、`yunshu_visibility_overall_status` 等）
- [ ] `yunshu_visibility_report_timestamp_seconds` 指标存在（用于过期检测）
- [ ] Grafana 看板变量 `layer` 可切换层筛选
- [ ] 告警规则在 Prometheus → Alerts 页面可见

### 11.5 自动周报/月报

- [ ] `python scripts/generate_visibility_trend.py --help` 显示帮助信息
- [ ] 运行周报生成命令，输出 3 个文件（.md / .html / .json）
- [ ] Markdown 报告包含概览统计、按层详情、健康检查章节
- [ ] HTML 报告包含 SVG 折线图
- [ ] `--non-interactive` 模式下，Prometheus 不可达时生成降级报告（退出码 2）

### 11.6 测试覆盖

- [ ] `python -m pytest tests/unit/test_visibility_export.py -v` 全部通过
- [ ] `python -m pytest tests/unit/test_generate_visibility_trend.py -v` 全部通过
- [ ] 测试覆盖 export_to_prometheus / serve_metrics / PrometheusClient / TrendReportGenerator / TrendReportRenderer / main() 等核心组件

---

## 附录 A：文件清单

| 路径 | 类型 | 说明 |
| --- | --- | --- |
| `deploy/glitchtip/docker-compose.yml` | 部署配置 | GlitchTip 4 服务编排 |
| `deploy/glitchtip/.env.example` | 配置模板 | 环境变量示例 |
| `deploy/glitchtip/verify_glitchtip_integration.py` | 验证脚本 | Sentry DSN 连通性验证 |
| `deploy/monitoring/docker-compose.yml` | 部署配置 | 监控栈 4 服务编排 |
| `deploy/monitoring/prometheus/prometheus.yml` | 抓取配置 | 5 个 scrape job |
| `deploy/monitoring/prometheus/alert_rules.yml` | 告警规则 | 10 条告警 |
| `deploy/monitoring/prometheus/recording_rules.yml` | 预聚合规则 | 5 条 recording rule |
| `deploy/monitoring/grafana/provisioning/datasources/prometheus.yml` | 数据源 | 自动配置 Prometheus |
| `deploy/monitoring/grafana/provisioning/dashboards/dashboard.yml` | 看板加载器 | 自动加载 JSON 看板 |
| `deploy/monitoring/grafana/dashboards/visibility_four_layers.json` | 看板 | 四层可见性（12 面板） |
| `deploy/monitoring/grafana/dashboards/business_metrics.json` | 看板 | 业务指标（8 面板） |
| `yunshu-ui/src/observability/index.ts` | 前端入口 | 可观测性初始化 |
| `yunshu-ui/src/observability/sentry.ts` | 前端模块 | Sentry SDK 配置 |
| `yunshu-ui/src/observability/sessionReplay.ts` | 前端模块 | rrweb 录制 + 上传 |
| `yunshu-ui/.env.example` | 配置模板 | 前端环境变量示例 |
| `scripts/visibility_report.py` | 核心脚本 | 新增 `--export-metrics` / `--serve-metrics` |
| `scripts/generate_visibility_trend.py` | 新增脚本 | 周报/月报趋势生成 |
| `tests/unit/test_visibility_export.py` | 测试 | 导出功能单元测试 |
| `tests/unit/test_generate_visibility_trend.py` | 测试 | 趋势脚本单元测试 |

## 附录 B：错误码参考

### GlitchTip 验证脚本错误码

| 错误码 | 含义 |
| --- | --- |
| `GT_ERR_001` | Sentry SDK 未安装 |
| `GT_ERR_002` | DSN 格式无效 |
| `GT_ERR_003` | SDK 初始化失败 |
| `GT_ERR_100` | 测试错误上报失败 |
| `GT_ERR_200` | 事件查询失败 |
| `GT_ERR_300` | 事件持久化失败 |
| `GT_ERR_400` | trace_id 注入失败 |
| `GT_ERR_500` | 敏感字段过滤失败 |

### 趋势报告错误码

| 错误码 | 含义 |
| --- | --- |
| `TREND_ERR_001` | Prometheus 不可达 |
| `TREND_ERR_002` | 查询失败（HTTP 错误或超时） |
| `TREND_ERR_003` | 查询返回空数据 |
| `TREND_ERR_004` | 周期参数无效 |
| `TREND_ERR_005` | 报告渲染失败 |
| `TREND_ERR_006` | 输出文件写入失败 |

---

**文档结束** | 如有疑问，请参考排错章节或联系可观测性团队。
