# tlm-ops-reporter Helm Chart

TLM 熔断器与向量层运维监控套件，打包 Prometheus 告警规则 + 日报生成器容器。

## 组件

| 组件 | 类型 | 说明 |
|------|------|------|
| ConfigMap | `tlm-circuit-breaker-alerts` | 5 组 15 条 Prometheus 告警规则（P0/P1/预警/状态/记录） |
| Deployment | `tlm-ops-reporter` | 运维日报容器（cron 模式，每天 01:00 生成昨天日报） |
| PVC | `tlm-ops-reporter-output` | 日报输出持久化（500Mi 默认） |
| ServiceMonitor | （可选） | 主应用 vec_events_total 指标采集 |

## 部署

```bash
# 部署到 monitoring 命名空间
helm install tlm-ops ./deploy/helm/tlm-ops-reporter -n monitoring --create-namespace

# 自定义参数部署
helm install tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring --create-namespace \
  --set image.tag=v1.1 \
  --set reporter.schedule.hour=2 \
  --set logsVolume.existingClaim=app-logs-pvc \
  --set outputVolume.size=1Gi
```

## 验证

```bash
# 查看 Pod
kubectl -n monitoring get pods -l app.kubernetes.io/instance=tlm-ops

# 查看 cron 启动日志
kubectl -n monitoring logs -l app.kubernetes.io/instance=tlm-ops --tail=10

# 查看告警规则 ConfigMap
kubectl -n monitoring get cm tlm-circuit-breaker-alerts -o yaml | head -30

# 手动触发一次日报
kubectl -n monitoring exec deploy/tlm-ops-reporter -- \
  python /app/generate_ops_daily_report.py --log-dir /app/logs --output /app/output/manual.md
```

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `image.repository` | `tlm-ops-reporter` | 日报容器镜像 |
| `image.tag` | `v1.1` | 镜像标签 |
| `reporter.mode` | `cron` | 运行模式（cron/once） |
| `reporter.schedule.hour` | `1` | cron 触发小时 |
| `reporter.schedule.minute` | `0` | cron 触发分钟 |
| `logsVolume.existingClaim` | `""` | 挂载已有日志 PVC（生产推荐） |
| `logsVolume.create` | `false` | 创建空日志 PVC（开发测试） |
| `outputVolume.create` | `true` | 创建日报输出 PVC |
| `outputVolume.size` | `500Mi` | 日报输出 PVC 大小 |
| `alerts.enabled` | `true` | 是否创建告警规则 ConfigMap |
| `alerts.configMapName` | `tlm-circuit-breaker-alerts` | 告警规则 ConfigMap 名称 |
| `serviceMonitor.enabled` | `false` | 是否创建 ServiceMonitor |

## 不变量（不易约束）

- **日志目录只读挂载**：`readOnly: true`，容器无法污染应用日志
- **非 root 运行**：`runAsNonRoot: true, runAsUser: 1000`
- **PVC ReadWriteOnce + Recreate 策略**：避免多副本写入冲突
- **告警规则文件保持原样**：从 `files/circuit_breaker_alerts.yml` 透传，不重写

## 卸载

```bash
helm uninstall tlm-ops -n monitoring
# PVC 需手动清理
kubectl -n monitoring delete pvc tlm-ops-reporter-output
```
