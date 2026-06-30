# 可观测性 CI/CD 集成文档

## 1. 概述

本文档描述了如何将可观测性检查集成到持续集成/持续部署（CI/CD）流程中，实现自动化质量保障，确保每次部署都经过可观测性验证。

### 1.1 目标

- **代码提交时**：自动运行可观测性相关单元测试
- **构建时**：验证可观测性配置文件完整性
- **部署前**：运行端到端可观测性验证
- **部署后**：自动触发健康检查和告警验证
- **质量门禁**：设定明确的通过阈值，不达标禁止部署

### 1.2 适用范围

- GitHub Actions 工作流
- 可观测性配置验证
- Prometheus 指标验证
- 追踪系统验证
- 部署后自动验证

---

## 2. 整体架构

### 2.1 工作流阶段

```
代码提交 → 配置验证 → 单元测试 → 集成测试 → E2E验证 → 质量门禁 → 告警通知
```

### 2.2 各阶段说明

| 阶段 | 触发时机 | 主要检查项 | 失败处理 |
|------|----------|------------|----------|
| 配置验证 | 每次提交 | Prometheus配置、告警规则、追踪配置、日志配置 | 终止流水线 |
| 单元测试 | 每次提交 | 可观测性模块单元测试、覆盖率 | 终止流水线 |
| 集成测试 | Push/定时 | 追踪集成、审计追踪 | 终止流水线 |
| E2E验证 | Push/定时 | 服务健康、指标端点、追踪端点、Prometheus集成 | 终止流水线 |
| 质量门禁 | Push/定时 | 通过率、覆盖率、E2E结果综合评估 | 禁止部署 |
| 告警通知 | 所有阶段 | 构建结果通知 | 仅通知，不阻塞 |

---

## 3. GitHub Actions 配置

### 3.1 工作流文件

工作流配置文件位于：`.github/workflows/observability-ci.yml`

### 3.2 触发条件

```yaml
on:
  push:
    branches: [main, develop, release/**]
    paths:
      - 'agent/monitoring/**'
      - 'agent/observability/**'
      - 'monitoring/**'
      - 'tests/**/test_*tracing*.py'
      - 'tests/**/test_*prometheus*.py'
      - '.github/workflows/observability-ci.yml'
      - 'scripts/observability_*.py'
  pull_request:
    branches: [main, develop]
  schedule:
    - cron: '0 3 * * *'  # 每天凌晨3点全量验证
```

### 3.3 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| PYTHON_VERSION | 3.11 | Python 版本 |
| OBSERVABILITY_TEST_PATHS | 见配置 | 可观测性测试文件路径 |

### 3.4 密钥配置

在 GitHub 仓库 Settings → Secrets and variables → Actions 中配置：

| 密钥名 | 说明 | 必填 |
|--------|------|------|
| SLACK_WEBHOOK_URL | Slack Webhook 地址 | 否 |
| DINGTALK_WEBHOOK | 钉钉机器人 Webhook 地址 | 否 |
| DINGTALK_SECRET | 钉钉机器人加签密钥 | 否 |

---

## 4. 验证脚本使用说明

所有脚本位于 `scripts/` 目录下，均可独立执行。

### 4.1 配置验证脚本

**文件**：`scripts/observability_config_validator.py`

**功能**：验证可观测性配置文件的完整性和正确性

**使用方法**：

```bash
# 基本使用
python scripts/observability_config_validator.py

# 指定配置目录
python scripts/observability_config_validator.py --config-dir monitoring/

# 指定输出文件
python scripts/observability_config_validator.py --output report.json
```

**检查项**：

| 检查项 | 说明 |
|--------|------|
| prometheus_config | Prometheus 主配置文件格式和必要字段 |
| alert_rules | 告警规则配置完整性 |
| tracing_config | 追踪模块和 OpenTelemetry 依赖 |
| metrics_config | Prometheus exporter 模块 |
| logging_config | 日志系统配置 |
| dashboard_config | Grafana 仪表盘配置 |

### 4.2 Prometheus 验证脚本

**文件**：`scripts/observability_prometheus_verify.py`

**功能**：验证 Prometheus 指标抓取和查询功能

**使用方法**：

```bash
python scripts/observability_prometheus_verify.py \
  --prometheus-url http://localhost:9090 \
  --app-url http://localhost:5678 \
  --output report.json
```

**检查项**：

| 检查项 | 说明 |
|--------|------|
| app_metrics_endpoint | 应用 /metrics 端点可用性 |
| key_metrics_exist | 关键业务指标存在性 |
| prometheus_targets | Prometheus 目标抓取状态 |
| prometheus_query | Prometheus 查询功能验证 |

### 4.3 质量门禁脚本

**文件**：`scripts/observability_quality_gate.py`

**功能**：综合评估所有验证结果，判定是否达到部署标准

**使用方法**：

```bash
python scripts/observability_quality_gate.py \
  --results-dir all-observability-results/ \
  --min-unit-test-pass-rate 95 \
  --min-coverage 60 \
  --require-e2e-pass true \
  --output quality_gate_report.json
```

**门禁阈值**：

| 指标 | 默认阈值 | 说明 |
|------|----------|------|
| 单元测试通过率 | ≥ 95% | 可通过 --min-unit-test-pass-rate 调整 |
| 测试覆盖率 | ≥ 60% | 可通过 --min-coverage 调整 |
| E2E 测试通过 | 必须通过 | 可通过 --require-e2e-pass false 关闭 |

### 4.4 部署后验证脚本

**文件**：`scripts/observability_post_deploy.py`

**功能**：部署后自动验证所有可观测性功能是否正常

**使用方法**：

```bash
# 基本验证
python scripts/observability_post_deploy.py --app-url http://localhost:5678

# 完整验证（含 Prometheus 集成）
python scripts/observability_post_deploy.py --full \
  --app-url http://localhost:5678 \
  --prometheus-url http://localhost:9090

# 失败时自动通知
python scripts/observability_post_deploy.py \
  --app-url http://localhost:5678 \
  --notify-webhook <钉钉webhook> \
  --notify-secret <加签密钥>
```

**检查项**：

| 检查项 | 说明 | 失败处理 |
|--------|------|----------|
| service_health | 服务健康检查接口 | 失败 |
| diagnostics_health | 诊断健康端点 | 跳过（警告） |
| metrics_endpoint | Prometheus 指标端点 | 失败 |
| tracing_endpoints | 追踪系统端点 | 跳过（警告） |
| logs_endpoint | 日志系统端点 | 跳过（警告） |
| observability_state | 可观测性状态端点 | 跳过（警告） |
| heartbeat | 心跳端点 | 跳过（警告） |
| prometheus_integration | Prometheus 集成验证 | 失败（完整模式） |

### 4.5 钉钉通知脚本

**文件**：`scripts/observability_dingtalk_notify.py`

**功能**：发送 CI/CD 结果到钉钉群

**使用方法**：

```bash
python scripts/observability_dingtalk_notify.py \
  --webhook "https://oapi.dingtalk.com/robot/send?access_token=xxx" \
  --secret "SECxxx" \
  --status success \
  --message "构建成功" \
  --branch main \
  --commit abc123 \
  --actor username \
  --workflow "CI/CD"
```

---

## 5. 告警通知配置

### 5.1 Slack 通知

**配置方式**：

1. 在 Slack 中创建 Incoming Webhook
2. 将 Webhook URL 添加到 GitHub Secrets：`SLACK_WEBHOOK_URL`
3. 工作流会自动在质量门禁阶段发送通知

**通知内容**：
- 构建状态（成功/失败）
- 分支名称
- 提交哈希
- 触发者
- 工作流名称

### 5.2 钉钉通知

**配置方式**：

1. 在钉钉群中添加自定义机器人
2. 获取 Webhook 地址和加签密钥
3. 添加到 GitHub Secrets：
   - `DINGTALK_WEBHOOK`
   - `DINGTALK_SECRET`

**安全设置**：
- 支持加签模式（推荐）
- 支持关键词模式
- 支持 IP 白名单模式

---

## 6. 质量门禁详解

### 6.1 门禁规则

质量门禁综合以下维度进行评估：

1. **配置验证结果**
   - 所有配置文件格式正确
   - 必要字段完整

2. **单元测试结果**
   - 测试通过率 ≥ 95%（可配置）
   - 测试覆盖率 ≥ 60%（可配置）

3. **集成测试结果**
   - 追踪上下文传播正常
   - 审计追踪功能正常

4. **E2E 验证结果**
   - 服务健康检查通过
   - 指标端点可用
   - 追踪系统可访问（可选）

### 6.2 报告结构

质量门禁报告 JSON 结构：

```json
{
  "check_time": "2024-01-01T00:00:00",
  "overall_status": "passed/failed",
  "passed": 5,
  "failed": 1,
  "skipped": 0,
  "thresholds": {
    "min_unit_test_pass_rate": 95,
    "min_coverage": 60,
    "require_e2e_pass": true
  },
  "checks": {
    "config_validation": { "status": "passed", "details": {} },
    "unit_tests": { "status": "passed", "details": {} },
    "test_coverage": { "status": "passed", "details": {} },
    "integration_tests": { "status": "passed", "details": {} },
    "e2e_tests": { "status": "failed", "details": {}, "error": "..." },
    "prometheus_integration": { "status": "passed", "details": {} }
  },
  "recommendations": []
}
```

---

## 7. 本地验证指南

### 7.1 本地运行配置验证

```bash
cd project-root
python scripts/observability_config_validator.py --config-dir monitoring/
```

### 7.2 本地运行部署后验证

1. 启动应用服务
2. 运行验证脚本：

```bash
python scripts/observability_post_deploy.py \
  --app-url http://localhost:5678 \
  --full
```

### 7.3 本地运行单元测试

```bash
python -m pytest \
  tests/unit/test_monitoring_tracing.py \
  tests/unit/test_prometheus_exporter.py \
  -v --cov=agent.monitoring
```

---

## 8. 常见问题排查

### 8.1 配置验证失败

**问题**：Prometheus 配置文件验证失败

**排查步骤**：
1. 检查 `monitoring/prometheus.yml` 是否存在
2. 验证 YAML 格式是否正确
3. 确认包含 `global` 和 `scrape_configs` 字段

### 8.2 单元测试失败

**问题**：可观测性单元测试不通过

**排查步骤**：
1. 检查依赖是否安装完整：`pip install prometheus-client opentelemetry-api`
2. 查看详细错误信息：`pytest -v --tb=long`
3. 确认测试文件路径正确

### 8.3 E2E 验证失败

**问题**：服务健康检查失败

**排查步骤**：
1. 确认服务已启动：`curl http://localhost:5678/api/health`
2. 检查端口是否被占用
3. 查看服务日志确认启动状态

### 8.4 通知发送失败

**问题**：钉钉/Slack 通知未收到

**排查步骤**：
1. 确认 Webhook URL 配置正确
2. 检查加签密钥是否匹配
3. 确认网络连接正常
4. 查看 GitHub Actions 日志中的错误信息

---

## 9. 自定义扩展

### 9.1 添加新的检查项

1. 在相应的验证脚本中添加新的检查方法
2. 在 `run_all_validations` 方法中注册新检查
3. 更新本文档的检查项列表

### 9.2 调整质量门禁阈值

修改工作流配置中的参数：

```yaml
- name: 执行质量门禁检查
  run: |
    python scripts/observability_quality_gate.py \
      --min-unit-test-pass-rate 90 \
      --min-coverage 70 \
      --require-e2e-pass false
```

### 9.3 添加新的通知渠道

1. 在 `scripts/` 目录下创建新的通知脚本
2. 在工作流的 `observability-alert-notification` 阶段添加调用步骤

---

## 10. 相关文件清单

| 文件路径 | 说明 |
|----------|------|
| `.github/workflows/observability-ci.yml` | GitHub Actions 工作流配置 |
| `scripts/observability_config_validator.py` | 配置验证脚本 |
| `scripts/observability_prometheus_verify.py` | Prometheus 验证脚本 |
| `scripts/observability_quality_gate.py` | 质量门禁脚本 |
| `scripts/observability_post_deploy.py` | 部署后验证脚本 |
| `scripts/observability_dingtalk_notify.py` | 钉钉通知脚本 |
| `monitoring/prometheus.yml` | Prometheus 配置 |
| `monitoring/alerts.yml` | 告警规则配置 |

---

## 11. 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.0 | 2024-01-01 | 初始版本，包含完整的 CI/CD 可观测性集成方案 |
