# 云枢智能体 - 可观测性常见问题排查手册

## 概述

本文档汇总了云枢智能体可观测性体系的常见问题及其排查方法，帮助运维人员快速定位和解决问题。

---

## 目录

1. [追踪相关问题](#1-追踪相关问题)
2. [指标相关问题](#2-指标相关问题)
3. [日志相关问题](#3-日志相关问题)
4. [告警相关问题](#4-告警相关问题)
5. [性能相关问题](#5-性能相关问题)
6. [网络相关问题](#6-网络相关问题)
7. [配置相关问题](#7-配置相关问题)
8. [诊断命令汇总](#8-诊断命令汇总)

---

## 1. 追踪相关问题

### 1.1 追踪上下文丢失

**问题现象**:
- 日志中缺少 `trace_id` 字段
- 追踪链路中断
- 跨服务调用时追踪上下文未传递

**排查步骤**:

1. **检查 OpenTelemetry 是否可用**
   ```bash
   curl http://localhost:5678/api/diagnostics/health | jq '.opentelemetry_available'
   ```

2. **检查当前追踪上下文**
   ```bash
   curl http://localhost:5678/api/diagnostics/trace
   ```

3. **验证上下文提取功能**
   ```bash
   curl -X POST http://localhost:5678/api/diagnostics/trace/extract \
     -H "Content-Type: application/json" \
     -d '{"headers": {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}}'
   ```

4. **检查代码中是否正确使用 TraceContext**
   - 确保所有入口点都包装在 `TraceContext` 中
   - 确保跨服务调用时传递 `traceparent` 头

5. **检查诊断报告**
   ```bash
   python -c "from agent.monitoring.tracing import print_diagnosis_report; print_diagnosis_report()"
   ```

**常见原因**:
- OpenTelemetry SDK 未安装或版本不兼容
- TraceContext 未正确初始化
- 跨服务调用时未传递追踪上下文
- 采样器配置导致 Span 未被记录

---

### 1.2 追踪数据不显示在 Jaeger

**问题现象**:
- 追踪数据没有显示在 Jaeger UI 中
- Jaeger 查询不到任何服务

**排查步骤**:

1. **检查环境变量配置**
   ```bash
   echo $TRACING_EXPORTER
   echo $TRACING_EXPORTER_ENDPOINT
   ```

2. **检查 Jaeger 服务状态**
   ```bash
   curl http://localhost:16686/api/services
   ```

3. **检查 OTLP Collector 状态**
   ```bash
   # 如果使用 gRPC
   grpcurl -d '{}' localhost:4317 grpc.health.v1.Health/Check
   
   # 如果使用 HTTP
   curl http://localhost:4318/health
   ```

4. **检查网络连接**
   ```bash
   telnet localhost 4317
   ```

5. **查看服务日志**
   ```bash
   tail -f logs/server_output.log | grep -i trace
   ```

**常见原因**:
- 导出器类型配置错误（应为 `OTLP` 而非 `CONSOLE`）
- Jaeger/OTLP Collector 未运行
- 网络连接问题
- 采样比例设置过低

---

### 1.3 采样比例不生效

**问题现象**:
- 采样比例配置后没有效果
- 开发环境期望全量采样但实际不是

**排查步骤**:

1. **确认环境变量设置**
   ```bash
   echo $TRACING_SAMPLER
   echo $TRACING_SAMPLER_RATIO
   ```

2. **检查调试模式是否覆盖配置**
   ```bash
   echo $TRACING_ENV
   ```
   - 如果 `TRACING_ENV=development`，默认使用 `ALWAYS_ON` 采样器

3. **查看诊断报告中的采样器信息**
   ```bash
   python -c "from agent.monitoring.tracing import diagnose_opentelemetry_config; print(diagnose_opentelemetry_config()['sampler_info'])"
   ```

**解决方案**:
- 设置 `TRACING_ENV=production` 以使用配置的采样器
- 确保 `TRACING_SAMPLER_RATIO` 在 0.0-1.0 范围内

---

## 2. 指标相关问题

### 2.1 Prometheus 指标不显示

**问题现象**:
- `/metrics` 端点返回空或不完整
- Prometheus Server 无法采集指标

**排查步骤**:

1. **检查 Prometheus 端点**
   ```bash
   curl http://localhost:5678/metrics | head -20
   ```

2. **检查运行时指标**
   ```bash
   curl http://localhost:5678/api/diagnostics/metrics
   ```

3. **检查 prometheus_client 是否安装**
   ```bash
   python -c "import prometheus_client; print('OK')"
   ```

4. **检查端口是否可访问**
   ```bash
   telnet localhost 5678
   ```

5. **检查 Prometheus 配置**
   - 确认 `prometheus.yml` 中配置了正确的抓取目标
   - 确认抓取间隔和超时设置合理

**常见原因**:
- prometheus_client 未安装或版本不兼容
- 防火墙阻止了访问
- Prometheus 配置错误

---

### 2.2 指标数据不准确

**问题现象**:
- 指标数值与实际不符
- 计数器不递增

**排查步骤**:

1. **检查指标定义**
   - 确认使用正确的指标类型（Counter/Gauge/Histogram）
   - 确认指标名称符合规范

2. **检查指标注册**
   ```python
   from agent.monitoring.metrics import get_metric
   metric = get_metric("yunshu_http_requests_total")
   print(metric._value.get())
   ```

3. **检查指标标签**
   - 确认标签值正确设置
   - 避免标签值过多导致基数爆炸

**解决方案**:
- 使用正确的指标类型
- 定期清理过期的指标标签

---

## 3. 日志相关问题

### 3.1 日志中缺少 trace_id

**问题现象**:
- 日志记录不包含 `trace_id` 字段
- 无法关联日志到追踪

**排查步骤**:

1. **检查日志配置**
   - 确保使用了 `SafeLogger`
   - 确保在 `TraceContext` 上下文中记录日志

2. **验证日志端点**
   ```bash
   curl http://localhost:5678/api/diagnostics/logs?limit=10 | jq '.[].trace_id'
   ```

3. **检查日志格式**
   - 确保日志使用 JSON 格式输出
   - 确保包含必要的追踪字段

**解决方案**:
- 在所有业务代码中使用 `SafeLogger`
- 确保关键业务逻辑包装在 `TraceContext` 中

---

### 3.2 日志输出过多/过少

**问题现象**:
- 日志量过大导致磁盘空间不足
- 日志量过少无法定位问题

**排查步骤**:

1. **检查日志级别配置**
   ```bash
   echo $TRACING_LOG_LEVEL
   ```

2. **调整日志级别**
   ```bash
   # 开发环境
   export TRACING_LOG_LEVEL=DEBUG
   
   # 生产环境
   export TRACING_LOG_LEVEL=WARN
   ```

3. **检查日志轮转配置**
   - 确认日志文件大小限制
   - 确认日志保留时间

---

### 3.3 敏感数据泄露

**问题现象**:
- 日志中包含敏感信息（密码、token等）

**排查步骤**:

1. **检查 SafeLogger 配置**
   ```python
   from agent.log_system.safe_logger import SafeLogger
   logger = SafeLogger("Test")
   logger.info("Test", {"password": "secret"})  # 应被过滤
   ```

2. **验证敏感数据过滤规则**
   - 确认邮箱、手机号、身份证号等规则生效
   - 确认自定义规则已配置

**解决方案**:
- 确保所有日志记录使用 `SafeLogger`
- 根据业务需求添加自定义过滤规则

---

## 4. 告警相关问题

### 4.1 告警规则不生效

**问题现象**:
- 配置的告警规则未触发
- Prometheus Alertmanager 未收到告警

**排查步骤**:

1. **检查告警规则配置**
   ```bash
   curl http://localhost:5678/api/observability/alerts
   ```

2. **验证告警表达式**
   ```bash
   curl -X POST http://localhost:5678/api/observability/alerts/validate \
     -H "Content-Type: application/json" \
     -d '{"expr": "sum(rate(yunshu_http_requests_total[5m])) > 100"}'
   ```

3. **检查 Prometheus 配置**
   - 确认 `prometheus.yml` 中配置了正确的告警规则文件路径
   - 确认规则文件存在且格式正确

4. **检查 Prometheus 服务状态**
   ```bash
   docker-compose -f monitoring/docker-compose.yml ps
   ```

**常见原因**:
- 告警表达式语法错误
- Prometheus 配置错误
- 告警规则文件不存在

---

### 4.2 告警频繁触发

**问题现象**:
- 告警频繁触发导致告警疲劳
- 正常波动被误判为异常

**排查步骤**:

1. **检查告警阈值设置**
   - 确认阈值是否合理
   - 考虑调整 `for` 持续时间

2. **检查指标数据**
   ```promql
   # 查看历史数据趋势
   yunshu_http_requests_total
   ```

3. **调整告警规则**
   - 增加阈值
   - 延长持续时间
   - 添加告警抑制规则

---

## 5. 性能相关问题

### 5.1 高延迟问题

**问题现象**:
- API 响应缓慢
- 95th percentile 延迟过高

**排查步骤**:

1. **查看延迟指标**
   ```promql
   histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))
   ```

2. **查看各端点延迟**
   ```promql
   histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket{endpoint="/api/chat"}[5m]))
   ```

3. **检查系统资源**
   ```bash
   curl http://localhost:5678/api/diagnostics/metrics | jq '.histograms'
   ```

4. **检查 CPU 和内存使用率**
   ```promql
   yunshu_cpu_usage_percent
   yunshu_memory_usage_percent
   ```

5. **查看追踪数据**
   - 在 Jaeger 中查看慢请求的完整链路
   - 定位耗时最长的 Span

**解决方案**:
- 优化慢查询
- 增加资源限制
- 启用缓存机制

---

### 5.2 内存泄漏

**问题现象**:
- 内存使用率持续上升
- 服务频繁重启

**排查步骤**:

1. **监控内存使用趋势**
   ```promql
   yunshu_memory_usage_percent
   ```

2. **检查垃圾回收**
   ```bash
   python -c "import gc; print(gc.collect())"
   ```

3. **使用内存分析工具**
   ```bash
   pip install memory_profiler
   mprof run main.py
   ```

4. **检查对象池配置**
   - 确认对象池大小合理
   - 确认对象正确释放

**解决方案**:
- 修复内存泄漏代码
- 优化对象池配置
- 增加内存限制

---

## 6. 网络相关问题

### 6.1 服务不可达

**问题现象**:
- API 端点无法访问
- 连接被拒绝

**排查步骤**:

1. **检查服务状态**
   ```bash
   curl http://localhost:5678/api/health
   ```

2. **检查端口监听**
   ```bash
   netstat -an | findstr 5678  # Windows
   ss -tlnp | grep 5678        # Linux
   ```

3. **检查防火墙规则**
   ```bash
   # Windows
   netsh advfirewall show allprofiles
   
   # Linux
   iptables -L
   ```

4. **检查 Docker 网络**
   ```bash
   docker network inspect bridge
   ```

---

### 6.2 跨服务调用失败

**问题现象**:
- 服务间调用超时
- 追踪链路中断

**排查步骤**:

1. **检查网络连通性**
   ```bash
   ping service-b.example.com
   telnet service-b.example.com 5678
   ```

2. **检查追踪上下文传递**
   ```bash
   curl -H "traceparent: 00-abc123def4567890-1234567812345678-01" \
     http://service-b.example.com/api/process
   ```

3. **检查超时配置**
   - 确认请求超时时间设置合理
   - 确认连接池配置正确

---

## 7. 配置相关问题

### 7.1 配置不生效

**问题现象**:
- 修改配置后没有效果
- 配置文件未被正确加载

**排查步骤**:

1. **检查配置文件路径**
   ```bash
   echo $CONFIG_PATH
   ls -la config.yaml
   ```

2. **验证配置加载**
   ```bash
   curl http://localhost:5678/api/diagnostics/config
   ```

3. **检查配置格式**
   ```bash
   python -c "import yaml; yaml.safe_load(open('config.yaml'))"
   ```

4. **检查环境变量优先级**
   - 环境变量 > 配置文件 > 默认值
   - 确认环境变量没有覆盖配置

**解决方案**:
- 确保配置文件路径正确
- 确保配置格式正确（YAML 缩进）
- 检查环境变量设置

---

### 7.2 配置验证失败

**问题现象**:
- 服务启动时配置验证失败
- 配置项被重置为默认值

**排查步骤**:

1. **查看启动日志**
   ```bash
   tail -f logs/server_output.log | grep -i config
   ```

2. **检查配置值范围**
   - max_workers: 1-32
   - pool_size: 1-100
   - max_concurrency: 1-20

3. **修复配置值**
   ```yaml
   performance:
     max_workers: 8
     pool_size: 20
     max_concurrency: 10
   ```

---

## 8. 诊断命令汇总

### 8.1 健康检查

```bash
# 快速健康检查
curl http://localhost:5678/api/health

# 综合健康检查
curl http://localhost:5678/api/diagnostics/health

# 心跳检查
curl http://localhost:5678/api/heartbeat
```

### 8.2 追踪诊断

```bash
# 获取当前追踪上下文
curl http://localhost:5678/api/diagnostics/trace

# 生成追踪上下文
curl http://localhost:5678/api/diagnostics/trace/inject

# 验证上下文提取
curl -X POST http://localhost:5678/api/diagnostics/trace/extract \
  -H "Content-Type: application/json" \
  -d '{"headers": {"traceparent": "00-abc123def4567890abc123def4567890-1234567812345678-01"}}'

# 生成诊断报告
python -c "from agent.monitoring.tracing import print_diagnosis_report; print_diagnosis_report()"
```

### 8.3 指标查询

```bash
# 获取 Prometheus 格式指标
curl http://localhost:5678/metrics

# 获取 JSON 格式指标
curl http://localhost:5678/api/diagnostics/metrics
```

### 8.4 日志查询

```bash
# 获取最近日志
curl http://localhost:5678/api/diagnostics/logs?limit=50

# 按级别过滤
curl "http://localhost:5678/api/observability/logs?level=ERROR"

# 按服务过滤
curl "http://localhost:5678/api/observability/logs?service=DigitalLife"
```

### 8.5 配置检查

```bash
# 获取配置状态
curl http://localhost:5678/api/diagnostics/config

# 检查已注册工具
curl http://localhost:5678/api/diagnostics/tools
```

### 8.6 服务状态

```bash
# 检查监控服务
docker-compose -f monitoring/docker-compose.yml ps

# 检查 Jaeger 服务
curl http://localhost:16686/api/services

# 检查 Prometheus 状态
curl http://localhost:9090/api/v1/status/config
```

---

**文档版本**: v1.0  
**最后更新**: 2026年6月  
**适用版本**: 云枢智能体 v2.x