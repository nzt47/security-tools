
# Prometheus 告警规则部署检查清单

## 1. 配置文件验证

- [ ] monitoring/alerts.yml 存在且格式正确
- [ ] monitoring/alerts_production.yml 存在且格式正确
- [ ] monitoring/prometheus.yml 已配置 rule_files
- [ ] 所有告警规则 YAML 语法验证通过

## 2. Docker 环境准备

- [ ] Docker Desktop 已启动
- [ ] Docker 版本 >= 20.10
- [ ] Docker Compose 版本 >= 2.0

## 3. 启动监控栈

```bash
# 启动服务
docker-compose -f docker-compose.monitoring.yml up -d

# 查看日志
docker-compose -f docker-compose.monitoring.yml logs -f prometheus

# 验证服务
docker-compose -f docker-compose.monitoring.yml ps
```

- [ ] Prometheus 容器运行正常
- [ ] Grafana 容器运行正常
- [ ] 无错误日志

## 4. Prometheus 验证

访问 http://localhost:9090

- [ ] Prometheus UI 可访问
- [ ] Status → Rules 页面显示 18 个告警规则
- [ ] 所有规则状态为 OK（无触发）
- [ ] Targets 页面显示 yunshu 和 prometheus 为 UP

## 5. 告警规则验证

在 Prometheus UI (http://localhost:9090) 执行以下查询：

### 5.1 检查规则加载
```promql
ALERTS
```
- [ ] 显示所有 18 个告警规则
- [ ] alertstate 为 inactive（未触发）

### 5.2 测试错误率告警
```promql
sum(rate(yunshu_http_requests_total{status=~"5.."}[5m])) / sum(rate(yunshu_http_requests_total[5m]))
```
- [ ] 当前错误率 < 5%（正常）
- [ ] 如果 > 5%，应触发 warning 告警

### 5.3 测试延迟告警
```promql
histogram_quantile(0.95, rate(yunshu_http_request_duration_seconds_bucket[5m]))
```
- [ ] 当前 95 分位延迟 < 500ms（正常）
- [ ] 如果 > 500ms，应触发 warning 告警

### 5.4 测试安全告警
```promql
sum(rate(yunshu_security_blocks_total[5m]))
```
- [ ] 当前拦截速率 < 3 次/分（正常）
- [ ] 如果 > 3 次/分，应触发 warning 告警

## 6. Grafana 验证

访问 http://localhost:3000 (admin/admin123)

- [ ] Grafana UI 可访问
- [ ] Prometheus 数据源配置正确
- [ ] Yunshu Monitor 仪表盘已导入
- [ ] 所有面板显示数据

## 7. 告警通知测试

### 7.1 手动触发测试告警

在 Prometheus 执行：
```promql
# 临时触发高 CPU 告警（如果当前 CPU < 70%）
yunshu_cpu_usage_percent > 0
```

- [ ] 告警状态变为 pending
- [ ] 2 分钟后告警状态变为 firing
- [ ] 收到通知（邮件/IM/电话）

### 7.2 验证通知渠道

- [ ] 邮件通知正常
- [ ] Slack/钉钉/企业微信通知正常
- [ ] 电话通知正常（如有配置）

## 8. 性能测试

### 8.1 并发请求测试

```bash
# 使用 ab 或 wrk 进行压力测试
ab -n 1000 -c 10 http://localhost:5678/api/health
```

- [ ] 错误率告警未触发（正常情况）
- [ ] 延迟告警可能触发（如果响应慢）
- [ ] Prometheus 抓取正常

### 8.2 安全拦截测试

```bash
# 发送危险指令
curl -X POST http://localhost:5678/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"rm -rf /","voice":false}'
```

- [ ] 返回 403 Forbidden
- [ ] 安全拦截计数 +1
- [ ] 如果频率高，触发安全告警

## 9. 故障恢复测试

### 9.1 模拟服务宕机

```bash
# 停止 Yunshu 服务
# 在另一个终端执行 Ctrl+C
```

- [ ] 1 分钟后触发 YunshuDown 告警
- [ ] Prometheus Target 显示 DOWN

### 9.2 恢复服务

```bash
# 重新启动 Yunshu
python app_server.py
```

- [ ] 告警自动恢复
- [ ] Target 恢复为 UP

## 10. 文档和监控

- [ ] 告警规则文档已更新
- [ ] 运维手册已包含告警响应流程
- [ ] 值班表已配置
- [ ] 联系人列表已更新

---

**检查完成时间**: ___________
**检查人**: ___________
**备注**: ___________
