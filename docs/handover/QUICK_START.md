# 云枢智能体 - 快速上手指南

## 概述

本文档帮助新维护人员快速上手云枢智能体可观测性体系的开发和调试工作。

---

## 目录

1. [环境搭建](#1-环境搭建)
2. [依赖安装](#2-依赖安装)
3. [服务启动](#3-服务启动)
4. [开发调试](#4-开发调试)
5. [常用命令](#5-常用命令)
6. [测试流程](#6-测试流程)

---

## 1. 环境搭建

### 1.1 系统要求

| 项目 | 要求 |
|------|------|
| Python | >= 3.8 |
| Docker | >= 20.10 |
| Git | >= 2.30 |
| Node.js | >= 16.0 (前端) |

### 1.2 克隆仓库

```bash
git clone https://github.com/your-repo/yunshu-agent.git
cd yunshu-agent
```

### 1.3 配置文件

**复制示例配置**:
```bash
cp .env.example .env
cp config.yaml.example config.yaml
```

**配置说明**:
```yaml
# config.yaml 关键配置

# 服务配置
server:
  port: 5678
  host: 0.0.0.0

# 日志配置
logging:
  level: DEBUG
  format: json

# 追踪配置
tracing:
  enabled: true
  exporter: CONSOLE
  sampler: ALWAYS_ON
```

---

## 2. 依赖安装

### 2.1 Python 依赖

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2.2 OpenTelemetry 依赖

```bash
# 安装核心 SDK
pip install opentelemetry-api opentelemetry-sdk

# 安装导出器
pip install opentelemetry-exporter-otlp-proto-grpc
pip install opentelemetry-exporter-jaeger-thrift
```

### 2.3 监控服务依赖

**Docker Compose 方式**:
```bash
cd monitoring
docker-compose up -d
```

---

## 3. 服务启动

### 3.1 开发模式启动

```bash
# 设置开发环境
export TRACING_ENV=development
export TRACING_LOG_LEVEL=DEBUG

# 启动主服务
python main.py
```

### 3.2 生产模式启动

```bash
# 设置生产环境
export TRACING_ENV=production
export TRACING_LOG_LEVEL=WARN
export TRACING_SAMPLER=PARENT_BASED_RATIO
export TRACING_SAMPLER_RATIO=0.1
export TRACING_EXPORTER=OTLP

# 使用 Gunicorn 启动
gunicorn --config gunicorn_config.py app_server:app
```

### 3.3 启动监控服务

```bash
cd monitoring
docker-compose up -d

# 查看服务状态
docker-compose ps
```

### 3.4 服务访问地址

| 服务 | URL |
|------|-----|
| 云枢 API | http://localhost:5678 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Jaeger | http://localhost:16686 |

---

## 4. 开发调试

### 4.1 调试配置

**VS Code launch.json**:
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: Main",
      "type": "python",
      "request": "launch",
      "program": "${workspaceFolder}/main.py",
      "console": "integratedTerminal",
      "env": {
        "TRACING_ENV": "development",
        "TRACING_LOG_LEVEL": "DEBUG"
      }
    }
  ]
}
```

### 4.2 追踪调试

**启用调试模式**:
```bash
export TRACING_ENV=development
export TRACING_LOG_LEVEL=DEBUG
python main.py
```

**查看追踪诊断**:
```python
from agent.monitoring.tracing import print_diagnosis_report
print_diagnosis_report()
```

### 4.3 日志调试

**查看实时日志**:
```bash
tail -f logs/server_output.log
```

**过滤日志**:
```bash
# 只查看 ERROR 级别
tail -f logs/server_output.log | grep -i error

# 按服务过滤
tail -f logs/server_output.log | grep -i DigitalLife
```

### 4.4 断点调试

**在代码中设置断点**:
```python
from agent.monitoring.tracing import TraceContext

def handle_request(request):
    # 设置断点
    import pdb; pdb.set_trace()
    
    with TraceContext("API", "request") as ctx:
        # 业务逻辑
        pass
```

---

## 5. 常用命令

### 5.1 服务管理

```bash
# 启动服务
python main.py

# 检查健康状态
curl http://localhost:5678/api/health

# 检查状态
curl http://localhost:5678/api/status
```

### 5.2 追踪管理

```bash
# 获取追踪上下文
curl http://localhost:5678/api/diagnostics/trace

# 生成追踪上下文
curl http://localhost:5678/api/diagnostics/trace/inject

# 提取追踪上下文
curl -X POST http://localhost:5678/api/diagnostics/trace/extract \
  -H "Content-Type: application/json" \
  -d '{"headers": {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}}'
```

### 5.3 指标管理

```bash
# 获取 Prometheus 指标
curl http://localhost:5678/metrics

# 获取 JSON 指标
curl http://localhost:5678/api/diagnostics/metrics
```

### 5.4 日志管理

```bash
# 获取最近日志
curl http://localhost:5678/api/diagnostics/logs?limit=50

# 按级别过滤
curl "http://localhost:5678/api/observability/logs?level=ERROR"
```

### 5.5 监控服务管理

```bash
# 启动监控服务
cd monitoring
docker-compose up -d

# 停止监控服务
docker-compose down

# 查看监控日志
docker-compose logs -f
```

---

## 6. 测试流程

### 6.1 运行单元测试

```bash
# 运行所有单元测试
python -m pytest tests/unit/ -v

# 运行特定测试文件
python -m pytest tests/unit/test_tracing.py -v

# 生成测试报告
python -m pytest tests/unit/ -v --tb=short > test_report.txt
```

### 6.2 运行集成测试

```bash
# 运行集成测试
python -m pytest tests/e2e/ -v

# 指定服务 URL
python -m pytest tests/e2e/ -v --url=http://localhost:5678
```

### 6.3 运行可观测性测试

```bash
# 运行可观测性端到端测试
python tests/test_observability_e2e.py

# 生成详细报告
python tests/test_observability_e2e.py --report
```

### 6.4 测试验证项

| 验证项 | 命令 |
|--------|------|
| 健康端点 | `curl http://localhost:5678/api/health` |
| 追踪端点 | `curl http://localhost:5678/api/diagnostics/trace` |
| 指标端点 | `curl http://localhost:5678/metrics` |
| 日志端点 | `curl http://localhost:5678/api/diagnostics/logs` |
| 工具诊断 | `curl http://localhost:5678/api/diagnostics/tools` |

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x