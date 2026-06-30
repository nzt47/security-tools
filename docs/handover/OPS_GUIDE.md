# 云枢智能体 - 日常运维指南

## 概述

本文档提供云枢智能体可观测性体系的日常运维操作指南，帮助运维人员完成日常监控、维护和管理工作。

---

## 目录

1. [服务管理](#1-服务管理)
2. [监控管理](#2-监控管理)
3. [日志管理](#3-日志管理)
4. [配置管理](#4-配置管理)
5. [数据管理](#5-数据管理)
6. [安全管理](#6-安全管理)
7. [备份与恢复](#7-备份与恢复)
8. [日常巡检清单](#8-日常巡检清单)

---

## 1. 服务管理

### 1.1 服务启动

**开发环境**:
```bash
# 启动主服务
python main.py

# 启动监控服务
cd monitoring
docker-compose up -d
```

**生产环境**:
```bash
# 使用 Gunicorn 启动
gunicorn --config gunicorn_config.py app_server:app

# 或使用 systemd
systemctl start yunshu-agent
```

### 1.2 服务停止

```bash
# 停止主服务
Ctrl+C (开发环境)
kill -TERM <pid> (生产环境)

# 停止监控服务
cd monitoring
docker-compose down
```

### 1.3 服务重启

```bash
# 重启主服务
systemctl restart yunshu-agent

# 重启监控服务
cd monitoring
docker-compose restart
```

### 1.4 服务状态检查

```bash
# 检查主服务状态
systemctl status yunshu-agent

# 检查监控服务状态
cd monitoring
docker-compose ps

# 检查健康端点
curl http://localhost:5678/api/health
```

### 1.5 日志查看

```bash
# 查看服务日志
tail -f logs/server_output.log

# 查看错误日志
tail -f logs/errors.log

# 查看 audit 日志
tail -f logs/audit.log
```

---

## 2. 监控管理

### 2.1 监控服务管理

**启动监控服务**:
```bash
cd monitoring
docker-compose up -d
```

**查看监控服务状态**:
```bash
docker-compose ps
```

**停止监控服务**:
```bash
docker-compose down
```

**重启监控服务**:
```bash
docker-compose restart
```

### 2.2 监控仪表盘访问

| 服务 | URL | 用户名 | 密码 |
|------|-----|--------|------|
| Grafana | http://localhost:3000 | admin | admin |
| Jaeger | http://localhost:16686 | - | - |
| Prometheus | http://localhost:9090 | - | - |

### 2.3 指标查询

**常用 PromQL 查询**:

```promql
# API 请求总数
sum(yunshu_http_requests_total)

# 错误率
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m]))

# 平均响应时间
avg(yunshu_http_request_duration_seconds_sum / yunshu_http_request_duration_seconds_count)

# CPU 使用率
avg(yunshu_cpu_usage_percent)

# 内存使用率
avg(yunshu_memory_usage_percent)

# LLM 调用次数
sum(yunshu_llm_calls_total)

# 安全拦截次数
sum(yunshu_security_blocks_total)
```

### 2.4 告警管理

**查看告警规则**:
```bash
curl http://localhost:5678/api/observability/alerts
```

**验证告警表达式**:
```bash
curl -X POST http://localhost:5678/api/observability/alerts/validate \
  -H "Content-Type: application/json" \
  -d '{"expr": "sum(rate(yunshu_http_requests_total[5m])) > 100"}'
```

**创建告警规则**:
```bash
curl -X POST http://localhost:5678/api/observability/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "alert": "HighErrorRate",
    "expr": "sum(rate(yunshu_http_requests_total{status=~\"5..\"}[5m])) / sum(rate(yunshu_http_requests_total[5m])) > 0.1",
    "for": "5m",
    "labels": {"severity": "critical"},
    "annotations": {"summary": "高错误率告警"}
  }'
```

---

## 3. 日志管理

### 3.1 日志查看

```bash
# 查看最近日志
curl http://localhost:5678/api/diagnostics/logs?limit=50

# 按级别过滤
curl "http://localhost:5678/api/observability/logs?level=ERROR"

# 按服务过滤
curl "http://localhost:5678/api/observability/logs?service=DigitalLife"

# 按 trace_id 过滤
curl "http://localhost:5678/api/observability/logs?trace_id=abc123def4567890"
```

### 3.2 日志轮转配置

日志轮转配置位于 `config.yaml`:

```yaml
logging:
  max_size: 100MB        # 单个日志文件最大大小
  backup_count: 10        # 保留备份数量
  rotation_interval: daily # 轮转间隔 (daily/hourly/weekly)
  max_age: 30             # 日志保留天数
```

### 3.3 日志级别调整

```bash
# 开发环境
export TRACING_LOG_LEVEL=DEBUG

# 生产环境
export TRACING_LOG_LEVEL=WARN

# 临时调整（不重启服务）
curl -X POST http://localhost:5678/api/diagnostics/logs/level \
  -H "Content-Type: application/json" \
  -d '{"level": "DEBUG"}'
```

---

## 4. 配置管理

### 4.1 配置文件位置

| 配置项 | 路径 |
|--------|------|
| 主配置 | `config.yaml` |
| 监控配置 | `agent/monitoring/tracing_config.py` |
| Prometheus | `monitoring/prometheus.yml` |
| 告警规则 | `monitoring/alerts.yml` |
| Docker Compose | `monitoring/docker-compose.yml` |

### 4.2 配置修改流程

1. **备份原配置**:
   ```bash
   cp config.yaml config.yaml.bak
   ```

2. **修改配置**:
   ```bash
   vi config.yaml
   ```

3. **验证配置**:
   ```bash
   python -c "import yaml; yaml.safe_load(open('config.yaml'))"
   ```

4. **应用配置**:
   ```bash
   # 对于需要重启的配置
   systemctl restart yunshu-agent
   
   # 对于支持热重载的配置
   curl -X POST http://localhost:5678/api/diagnostics/config/reload
   ```

### 4.3 配置验证

```bash
# 验证配置加载
curl http://localhost:5678/api/diagnostics/config

# 检查配置完整性
python -c "from config import Config; Config.validate()"
```

---

## 5. 数据管理

### 5.1 数据备份

**备份内存数据**:
```bash
cp data/memory/* backup/memory/
```

**备份日志数据**:
```bash
cp data/logs/*.db backup/logs/
```

**备份配置文件**:
```bash
cp config.yaml backup/config.yaml
```

### 5.2 数据清理

**清理旧日志**:
```bash
find data/logs -name "*.jsonl" -mtime +30 -delete
```

**清理旧追踪数据**:
```bash
# Jaeger 数据清理（Docker 环境）
docker exec -it jaeger find /tmp/jaeger -type f -mtime +7 -delete
```

**清理缓存**:
```bash
rm -rf data/cache/*
```

### 5.3 数据迁移

```bash
# 停止服务
systemctl stop yunshu-agent

# 复制数据到新位置
rsync -av data/ /new/path/data/

# 更新配置
sed -i 's|data/|/new/path/data/|g' config.yaml

# 启动服务
systemctl start yunshu-agent
```

---

## 6. 安全管理

### 6.1 安全日志检查

```bash
# 查看安全拦截日志
grep -i "security" logs/server_output.log

# 查看 audit 日志
tail -f logs/audit.log
```

### 6.2 敏感数据检查

```bash
# 检查日志中是否包含敏感信息
grep -E "(password|token|secret)" logs/server_output.log
```

### 6.3 访问控制

**API 认证状态检查**:
```bash
curl http://localhost:5678/api/diagnostics/config | jq '.security'
```

**更新 API 密钥**:
```bash
curl -X POST http://localhost:5678/api/diagnostics/config/security \
  -H "Content-Type: application/json" \
  -d '{"api_key": "new-secret-key"}'
```

---

## 7. 备份与恢复

### 7.1 定期备份计划

| 备份内容 | 频率 | 保留期限 |
|----------|------|---------|
| 配置文件 | 每日 | 30天 |
| 日志数据 | 每日 | 7天 |
| 内存数据 | 每日 | 30天 |
| 完整备份 | 每周 | 90天 |

### 7.2 备份命令

```bash
# 创建备份目录
mkdir -p backup/$(date +%Y%m%d)

# 备份配置
cp config.yaml backup/$(date +%Y%m%d)/

# 备份数据
cp -r data/ backup/$(date +%Y%m%d)/

# 备份日志
cp -r logs/ backup/$(date +%Y%m%d)/

# 打包备份
tar -czf backup_$(date +%Y%m%d).tar.gz backup/$(date +%Y%m%d)
```

### 7.3 恢复命令

```bash
# 停止服务
systemctl stop yunshu-agent

# 解压备份
tar -xzf backup_20260624.tar.gz

# 恢复数据
cp -r backup/20260624/data/* data/

# 恢复配置
cp backup/20260624/config.yaml .

# 启动服务
systemctl start yunshu-agent
```

---

## 8. 日常巡检清单

### 8.1 每日巡检

| 检查项 | 命令/操作 | 正常状态 |
|--------|----------|---------|
| 服务状态 | `systemctl status yunshu-agent` | active (running) |
| 健康检查 | `curl http://localhost:5678/api/health` | overall_health: 1.0 |
| CPU 使用率 | `curl http://localhost:5678/api/diagnostics/metrics` | < 80% |
| 内存使用率 | `curl http://localhost:5678/api/diagnostics/metrics` | < 80% |
| 错误日志 | `tail -50 logs/errors.log` | 无新增错误 |
| 监控服务 | `docker-compose -f monitoring/docker-compose.yml ps` | 所有服务 running |

### 8.2 每周巡检

| 检查项 | 操作 | 频率 |
|--------|------|------|
| 日志文件大小 | 检查 `data/logs/` 目录 | 每周 |
| 备份完整性 | 验证备份文件 | 每周 |
| 证书有效期 | 检查 SSL 证书 | 每周 |
| 依赖更新 | 检查 `requirements.txt` | 每周 |
| 安全日志 | 审查安全事件 | 每周 |

### 8.3 每月巡检

| 检查项 | 操作 | 频率 |
|--------|------|------|
| 性能分析 | 分析 Prometheus 指标 | 每月 |
| 告警规则 | 审查和优化 | 每月 |
| 配置审查 | 检查配置变更 | 每月 |
| 容量规划 | 评估资源使用趋势 | 每月 |
| 安全审计 | 全面安全检查 | 每月 |

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x