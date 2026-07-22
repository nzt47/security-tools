# tlm-ops-reporter 生产环境部署指南

> **适用版本**: Chart 1.2.0 / App v1.2
> **目标读者**: 运维工程师、SRE
> **关联文档**: [README.md](../../deploy/helm/tlm-ops-reporter/README.md)、[release_checklist_v1.2.md](release_checklist_v1.2.md)、[local_verification_guide.md](local_verification_guide.md)

---

## 1. 前置条件

### 1.1 基础设施要求

| 项 | 要求 | 验证命令 |
|----|------|----------|
| K8s 集群 | ≥ 1.21（NetworkPolicy v1 + apps/v1 稳定支持） | `kubectl version --short` |
| Helm CLI | ≥ 3.0（Chart apiVersion v2） | `helm version` |
| kubectl | 与集群版本匹配 | `kubectl version --client` |
| 镜像仓库 | 私有 registry（Harbor/ACR/ECR 等） | `docker login <registry>` |
| 存储 | StorageClass（用于日报输出 PVC） | `kubectl get storageclass` |
| Prometheus Operator | 已部署（用于 ServiceMonitor + 告警规则） | `kubectl get prometheus -A` |

### 1.2 命名空间与权限

```bash
# 创建专用命名空间
kubectl create namespace monitoring

# （可选）创建 ImagePullSecret（私有 registry）
kubectl create secret docker-registry regcred \
  --docker-server=<registry> \
  --docker-username=<user> \
  --docker-password=<password> \
  -n monitoring
```

### 1.3 日志 PVC 准备

生产环境**禁止**使用 `logsVolume.create=true`（创建空 PVC），必须挂载应用容器已写入的日志 PVC：

```bash
# 方式 1: 复用主应用的日志 PVC（推荐）
# 主应用部署时已创建 logs PVC，此处直接引用
kubectl get pvc -n monitoring  # 确认日志 PVC 名称

# 方式 2: 手动创建专用日志 PVC
cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: tlm-app-logs
  namespace: monitoring
spec:
  accessModes:
    - ReadOnlyMany  # 多 Pod 只读共享
  storageClassName: <storage-class>
  resources:
    requests:
      storage: 10Gi
EOF
```

> **注意**: 日志 PVC 的 `accessMode` 建议为 `ReadOnlyMany`（多 Pod 只读共享），与主应用写入 PVC 分离。

---

## 2. 镜像准备

### 2.1 构建并推送到私有仓库

```bash
# 多架构构建并推送
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t <registry>/tlm-ops-reporter:v1.2 \
  -f docker/ops-reporter/Dockerfile \
  --push .

# 验证 manifest list
docker buildx imagetools inspect <registry>/tlm-ops-reporter:v1.2
```

### 2.2 镜像签名验证（可选，推荐）

```bash
# 使用 cosign 签名（如已部署 Sigstore）
cosign sign --key cosign.key <registry>/tlm-ops-reporter:v1.2

# 集群侧验证（需部署 Kyverno/Connaisseur）
cosign verify --key cosign.pub <registry>/tlm-ops-reporter:v1.2
```

---

## 3. 生产 values.yaml 配置

### 3.1 推荐 production-values.yaml

```yaml
# ===== 生产环境配置 =====

# 镜像：私有仓库 + Always 拉取（确保滚动更新生效）
image:
  repository: <registry>/tlm-ops-reporter
  tag: "v1.2"
  pullPolicy: Always

# 日报生成器
reporter:
  mode: cron
  schedule:
    hour: 1        # 凌晨 01:00 生成昨天日报
    minute: 0
  logDir: /app/logs
  outputDir: /app/output
  resources:
    limits:
      cpu: 1000m     # 生产环境适当上调
      memory: 512Mi
    requests:
      cpu: 200m
      memory: 256Mi

# 日志来源：必须挂载已有 PVC（禁止 create）
logsVolume:
  existingClaim: "tlm-app-logs"   # 主应用日志 PVC
  create: false
  size: 10Gi
  storageClassName: ""

# 日报输出 PVC
outputVolume:
  create: true
  size: 2Gi                       # 生产环境上调（保留更多历史日报）
  storageClassName: "standard-rwo"
  accessMode: ReadWriteOnce

# 告警规则
alerts:
  enabled: true
  configMapName: tlm-circuit-breaker-alerts
  fileName: circuit_breaker_alerts.yml

# ServiceMonitor（需 Prometheus Operator）
serviceMonitor:
  enabled: true
  interval: 30s
  labels:
    prometheus: kube-prometheus   # 匹配 Prometheus CR 的 selector

# 安全上下文（不易约束 — 不可弱化）
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000

containerSecurityContext:
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL

# NetworkPolicy（生产必须启用）
networkPolicy:
  enabled: true
```

### 3.2 关键配置差异（开发 → 生产）

| 配置项 | 开发默认 | 生产推荐 | 原因 |
|--------|----------|----------|------|
| `image.repository` | `tlm-ops-reporter` | `<registry>/tlm-ops-reporter` | 私有仓库隔离 |
| `image.pullPolicy` | `IfNotPresent` | `Always` | 确保滚动更新立即生效 |
| `logsVolume.create` | `false` | `false` | 必须挂载已有日志 PVC |
| `logsVolume.existingClaim` | `""` | `tlm-app-logs` | 指向主应用日志 PVC |
| `outputVolume.size` | `500Mi` | `2Gi` | 保留更多历史日报 |
| `reporter.resources.limits.cpu` | `500m` | `1000m` | 生产负载更高 |
| `reporter.resources.limits.memory` | `256Mi` | `512Mi` | 避免 OOM |
| `serviceMonitor.enabled` | `false` | `true` | 接入 Prometheus 监控 |
| `networkPolicy.enabled` | `false` | `true` | 最小权限网络隔离 |

---

## 4. 部署流程

### 4.1 部署前校验

```bash
# 1. schema 校验（确保 values 合法）
helm lint deploy/helm/tlm-ops-reporter/

# 2. 模板渲染预览（确认渲染结果符合预期）
helm template tlm-ops deploy/helm/tlm-ops-reporter \
  -n monitoring \
  -f production-values.yaml > /tmp/v1.2-rendered.yaml

# 3. 人工核对渲染产物
grep -E "image:|securityContext|networkPolicy" /tmp/v1.2-rendered.yaml
```

### 4.2 执行部署

```bash
# 使用 production-values.yaml 部署
helm install tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring \
  -f production-values.yaml \
  --create-namespace

# 如需指定 ImagePullSecret，追加：
# --set imagePullSecrets[0].name=regcred
```

### 4.3 部署后验证

```bash
# 1. Pod 状态（90s 内应 Ready）
kubectl -n monitoring wait --for=condition=Ready pod \
  -l app.kubernetes.io/instance=tlm-ops --timeout=90s

# 2. 查看资源清单
kubectl -n monitoring get all -l app.kubernetes.io/instance=tlm-ops

# 3. 验证安全上下文（P1-3 三件套）
POD=$(kubectl -n monitoring get pod -l app.kubernetes.io/instance=tlm-ops -o jsonpath='{.items[0].metadata.name}')

kubectl exec $POD -n monitoring -- id
# 期望: uid=1000 gid=1000

kubectl exec $POD -n monitoring -- sh -c "touch /test 2>&1"
# 期望: Read-only file system

kubectl exec $POD -n monitoring -- cat /proc/1/status | grep CapEff
# 期望: CapEff: 0000000000000000

# 4. 验证 NetworkPolicy（如已启用）
kubectl -n monitoring get networkpolicy
# 期望: tlm-ops-reporter 策略存在

kubectl exec $POD -n monitoring -- python -c \
  "import socket; print(socket.gethostbyname('kubernetes.default.svc.cluster.local'))"
# 期望: 返回 ClusterIP（DNS 放行）

# 5. 验证 PVC 挂载
kubectl exec $POD -n monitoring -- sh -c "touch /app/output/test && echo PVC_OK && rm /app/output/test"
# 期望: PVC_OK

# 6. 手动触发一次日报（验证功能）
kubectl exec deploy/tlm-ops-reporter -n monitoring -- \
  python /app/generate_ops_daily_report.py \
  --log-dir /app/logs --output /app/output/manual.md

kubectl exec deploy/tlm-ops-reporter -n monitoring -- ls -la /app/output/
# 期望: manual.md 存在
```

---

## 5. 监控集成

### 5.1 Prometheus 告警规则

Chart 部署后会创建 ConfigMap `tlm-circuit-breaker-alerts`，需在 Prometheus CR 中引用：

```bash
# 确认 ConfigMap 已创建
kubectl -n monitoring get cm tlm-circuit-breaker-alerts

# 在 Prometheus CR 中添加 ruleFiles（如未自动匹配）
kubectl edit prometheus -n monitoring
```

```yaml
# Prometheus CR 示例片段
spec:
  ruleFiles:
    - /etc/prometheus/rules/tlm-circuit-breaker-alerts/*.yaml
```

### 5.2 ServiceMonitor 验证

```bash
# 确认 ServiceMonitor 被 Prometheus 识别
kubectl -n monitoring get servicemonitor tlm-ops-reporter

# 在 Prometheus UI 检查 target
# 访问 http://<prometheus>/targets，查找 tlm-ops-reporter
```

### 5.3 关键告警指标

| 告警名 | 级别 | 触发条件 | 处理建议 |
|--------|------|----------|----------|
| CircuitBreakerOpen | P0 | 熔断器开启 > 1min | 检查下游服务 + 向量层状态 |
| VectorLayerDegraded | P1 | 向量检索失败率 > 10% | 检查 sqlite-vec + FTS5 降级 |
| OpsReportFailed | P1 | 日报生成失败 | 检查日志 PVC + 容器日志 |
| MemoryStoreHighLatency | 预警 | 内存层延迟 > 500ms | 检查锁竞争 + 连接池 |

---

## 6. 升级与回滚

### 6.1 升级流程

```bash
# 1. 备份当前 values
helm get values tlm-ops -n monitoring > backup-values.yaml

# 2. 更新镜像 tag 或 Chart 版本
helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring \
  -f production-values.yaml \
  --set image.tag=v1.3

# 3. 监控升级过程
kubectl -n monitoring rollout status deployment/tlm-ops-reporter
```

### 6.2 回滚流程

```bash
# 查看历史版本
helm history tlm-ops -n monitoring

# 回滚到指定版本（如版本 1）
helm rollback tlm-ops 1 -n monitoring

# 验证回滚后的镜像 tag
kubectl -n monitoring get deployment tlm-ops-reporter \
  -o jsonpath='{.spec.template.spec.containers[0].image}'
```

> **数据安全**: PVC 使用 `ReadWriteOnce` + `Recreate` 策略，升级/回滚时 Pod 重建但 PVC 保留，日报数据不丢失。

---

## 7. 故障排查

### 7.1 常见问题速查

| 现象 | 可能原因 | 诊断命令 | 解决方案 |
|------|----------|----------|----------|
| Pod CrashLoopBackOff | 镜像拉取失败 | `kubectl describe pod` | 检查 ImagePullSecret + registry 地址 |
| Pod OOMKilled | 内存限制过低 | `kubectl describe pod` | 上调 `reporter.resources.limits.memory` |
| 日报生成失败 | 日志 PVC 未挂载 | `kubectl exec <pod> -- ls /app/logs` | 配置 `logsVolume.existingClaim` |
| 日报输出失败 | output PVC 空间不足 | `kubectl exec <pod> -- df -h /app/output` | 上调 `outputVolume.size` |
| DNS 解析失败 | NetworkPolicy 阻断 DNS | `kubectl exec <pod> -- nslookup kubernetes.default` | 确认 NP egress 放行 53 端口 |
| 外部访问成功 | NetworkPolicy 未生效 | `kubectl get networkpolicy -n monitoring` | 确认 `networkPolicy.enabled=true` |
| ServiceMonitor 未采集 | Prometheus selector 不匹配 | `kubectl get prometheus -o yaml` | 调整 `serviceMonitor.labels` |
| 告警规则未加载 | Prometheus ruleFiles 未配置 | `kubectl get prometheus -o yaml` | 在 Prometheus CR 添加 ruleFiles |

### 7.2 日志查看

```bash
# 容器启动日志
kubectl -n monitoring logs -l app.kubernetes.io/instance=tlm-ops --tail=50

# 日报生成日志（如容器内）
kubectl exec deploy/tlm-ops-reporter -n monitoring -- \
  cat /app/output/manual.md | head -50

# Pod 事件
kubectl -n monitoring describe pod -l app.kubernetes.io/instance=tlm-ops | tail -30
```

### 7.3 调试模式

临时禁用 NetworkPolicy 进行调试（**仅排查用，排查后必须恢复**）：

```bash
# 临时禁用 NP
helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring -f production-values.yaml \
  --set networkPolicy.enabled=false

# 调试完成后恢复
helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring -f production-values.yaml \
  --set networkPolicy.enabled=true
```

---

## 8. 安全检查清单

部署后逐项确认（对应 [k8s_verification_checklist.md](k8s_verification_checklist.md) 28 项中的关键项）：

- [ ] **非 root 运行**: `kubectl exec <pod> -- id` 返回 uid=1000
- [ ] **根文件系统只读**: `kubectl exec <pod> -- touch /test` 返回 Read-only file system
- [ ] **capabilities 已 drop ALL**: `kubectl exec <pod> -- cat /proc/1/status | grep CapEff` 返回全 0
- [ ] **PVC 挂载可写**: `kubectl exec <pod> -- touch /app/output/test` 成功
- [ ] **NetworkPolicy 生效**: DNS 解析成功 + 外部访问被拒
- [ ] **镜像来源可信**: 使用私有 registry + 签名验证（如启用）
- [ ] **资源限制设置**: `kubectl describe pod` 显示 limits/requests
- [ ] **HEALTHCHECK healthy**: `kubectl describe pod` 显示健康检查通过

---

## 9. 清理与卸载

```bash
# 卸载 Helm release
helm uninstall tlm-ops -n monitoring

# 删除 PVC（谨慎操作 — 会丢失历史日报）
kubectl -n monitoring delete pvc tlm-ops-reporter-output

# （可选）删除命名空间（如为专用）
kubectl delete namespace monitoring

# （可选）删除 ImagePullSecret
kubectl -n monitoring delete secret regcred
```

> **数据备份**: 卸载前请备份日报 PVC 中的历史报告：
> ```bash
> kubectl cp monitoring/<pod>:/app/output ./report-backup
> ```

---

## 10. 附录

### 10.1 完整部署命令速查

```bash
# 一键部署（生产环境）
helm install tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring --create-namespace \
  -f production-values.yaml

# 一键升级
helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring -f production-values.yaml

# 一键验证（部署后）
POD=$(kubectl -n monitoring get pod -l app.kubernetes.io/instance=tlm-ops -o jsonpath='{.items[0].metadata.name}') && \
kubectl -n monitoring exec $POD -- id && \
kubectl -n monitoring exec $POD -- sh -c "touch /test 2>&1" && \
kubectl -n monitoring exec $POD -- cat /proc/1/status | grep CapEff
```

### 10.2 相关文档

- [Helm Chart README](../../deploy/helm/tlm-ops-reporter/README.md) — 配置参数全表
- [v1.2 发布说明](RELEASE_NOTES_v1.2.md) — 版本变更详情
- [发布清单](release_checklist_v1.2.md) — 版本一致性核查
- [K8s 验证检查清单](k8s_verification_checklist.md) — 28 项验证清单
- [K8s 验证脚本](../../scripts/verify_k8s_v1.2.ps1) — 28 检查点自动执行
- [values.schema.json 校验脚本](../../scripts/verify_values_schema.ps1) — schema 缺失检测

---

> **三义原则校验**:
> - [不易] 守住 v1.2 安全契约（非 root + 只读根 FS + cap drop ALL + NP 最小权限），所有生产配置不得弱化安全上下文
> - [变易] 覆盖生产场景差异（私有 registry + 已有 PVC + NP + ServiceMonitor + 资源上调），含升级/回滚/故障排查
> - [简易] 10 章节结构化，每章含验证命令，运维工程师 30 分钟内可完成生产部署
