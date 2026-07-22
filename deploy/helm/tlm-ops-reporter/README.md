# tlm-ops-reporter Helm Chart

TLM 熔断器与向量层运维监控套件，打包 Prometheus 告警规则 + 日报生成器容器。

- **Chart 版本**: `1.2.0`
- **App 版本**: `1.2`
- **Helm 要求**: ≥ 3.0（`apiVersion: v2`）
- **K8s 要求**: ≥ 1.21（NetworkPolicy v1 + apps/v1 稳定支持）

## 组件

| 组件 | 类型 | 说明 |
|------|------|------|
| ConfigMap | `tlm-circuit-breaker-alerts` | 5 组 15 条 Prometheus 告警规则（P0/P1/预警/状态/记录） |
| Deployment | `tlm-ops-reporter` | 运维日报容器（cron 模式，每天 01:00 生成昨天日报） |
| PVC | `tlm-ops-reporter-output` | 日报输出持久化（500Mi 默认） |
| NetworkPolicy | （可选） | 限制 Pod 仅 DNS 出站，拒绝所有入站 |
| ServiceMonitor | （可选） | 主应用 vec_events_total 指标采集 |

## 部署

```bash
# 部署到 monitoring 命名空间
helm install tlm-ops ./deploy/helm/tlm-ops-reporter -n monitoring --create-namespace

# 自定义参数部署
helm install tlm-ops ./deploy/helm/tlm-ops-reporter \
  -n monitoring --create-namespace \
  --set image.tag=v1.2 \
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

> 完整类型约束见 [values.schema.json](values.schema.json)，`helm install --set` 传入非法类型会被 Helm 拒绝。

### 镜像与命名

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `namespace` | string | `monitoring` | 部署命名空间 |
| `nameOverride` | string | `""` | 覆盖资源名前缀 |
| `fullnameOverride` | string | `""` | 覆盖完整资源名 |
| `image.repository` | string | `tlm-ops-reporter` | 日报容器镜像 |
| `image.tag` | string | `v1.2` | 镜像标签 |
| `image.pullPolicy` | string | `IfNotPresent` | 镜像拉取策略 |

### 日报生成器配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `reporter.mode` | string | `cron` | 运行模式（cron/once） |
| `reporter.schedule.hour` | integer | `1` | cron 触发小时（0-23） |
| `reporter.schedule.minute` | integer | `0` | cron 触发分钟（0-59） |
| `reporter.logDir` | string | `/app/logs` | 日志读取目录 |
| `reporter.outputDir` | string | `/app/output` | 日报输出目录 |
| `reporter.resources.limits.cpu` | string | `500m` | CPU 上限 |
| `reporter.resources.limits.memory` | string | `256Mi` | 内存上限 |
| `reporter.resources.requests.cpu` | string | `100m` | CPU 请求 |
| `reporter.resources.requests.memory` | string | `128Mi` | 内存请求 |

### 存储卷配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `logsVolume.existingClaim` | string | `""` | 挂载已有日志 PVC（生产推荐） |
| `logsVolume.create` | boolean | `false` | 创建空日志 PVC（开发测试） |
| `logsVolume.size` | string | `1Gi` | 日志 PVC 大小 |
| `logsVolume.storageClassName` | string | `""` | 存储类名（空=默认） |
| `outputVolume.create` | boolean | `true` | 创建日报输出 PVC |
| `outputVolume.size` | string | `500Mi` | 日报输出 PVC 大小 |
| `outputVolume.storageClassName` | string | `""` | 存储类名（空=默认） |
| `outputVolume.accessMode` | string | `ReadWriteOnce` | 访问模式 |

### 告警与监控

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `alerts.enabled` | boolean | `true` | 是否创建告警规则 ConfigMap |
| `alerts.configMapName` | string | `tlm-circuit-breaker-alerts` | 告警规则 ConfigMap 名称 |
| `alerts.fileName` | string | `circuit_breaker_alerts.yml` | ConfigMap 中的规则文件 key |
| `serviceMonitor.enabled` | boolean | `false` | 是否创建 ServiceMonitor |
| `serviceMonitor.interval` | string | `30s` | 采集间隔 |
| `serviceMonitor.labels` | object | `{}` | ServiceMonitor 标签（用于 Prometheus 匹配） |

### 安全上下文（不易约束 — 不可弱化）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `podSecurityContext.runAsNonRoot` | boolean | `true` | 禁止 root 运行 |
| `podSecurityContext.runAsUser` | integer | `1000` | 非 root UID |
| `podSecurityContext.fsGroup` | integer | `1000` | 文件系统 GID |
| `containerSecurityContext.readOnlyRootFilesystem` | boolean | `true` | 根文件系统只读 |
| `containerSecurityContext.allowPrivilegeEscalation` | boolean | `false` | 禁止提权 |
| `containerSecurityContext.capabilities.drop` | array\<string\> | `["ALL"]` | 丢弃所有 Linux capabilities |

### 网络策略

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `networkPolicy.enabled` | boolean | `false` | 启用 NetworkPolicy（生产推荐 true） |

> 启用后：Ingress 拒绝所有入站，Egress 仅放行 kube-dns UDP/TCP 53。

## values.schema.json 类型约束

Chart 已包含 [values.schema.json](values.schema.json)，启用 Helm 原生 values 结构化校验。

**校验效果**：

```bash
# 合法参数（通过）
helm install tlm-ops ./deploy/helm/tlm-ops-reporter --set networkPolicy.enabled=true

# 非法参数（被 schema 拒绝）
helm install tlm-ops ./deploy/helm/tlm-ops-reporter --set networkPolicy.enabled=invalid
# Error: values don't meet the specifications of the schema(s) in the following chart(s)
# tlm-ops-reporter: - networkPolicy.enabled: Invalid type. Expected: boolean, given: string
```

**重新生成 schema**（values.yaml 变更后）：

```powershell
# 使用辅助脚本自动从 values.yaml 推断类型
.\scripts\verify_values_schema.ps1 -AutoGenerate -Force
```

> 详见 [release_checklist_v1.2.md §3.7](../../docs/ops_daily/release_checklist_v1.2.md) P3 增强建议。

## 不变量（不易约束）

- **日志目录只读挂载**：`readOnly: true`，容器无法污染应用日志
- **非 root 运行**：`runAsNonRoot: true, runAsUser: 1000`
- **PVC ReadWriteOnce + Recreate 策略**：避免多副本写入冲突
- **告警规则文件保持原样**：从 `files/circuit_breaker_alerts.yml` 透传，不重写
- **capabilities drop ALL**：容器仅保留最小权限
- **根文件系统只读**：写入仅限挂载的 volume（logs/output PVC）

## 镜像构建（多架构）

ops-reporter 镜像支持 `linux/amd64` + `linux/arm64` 双架构，适用于 x86 服务器与 ARM 环境（AWS Graviton、Apple Silicon）。

### 前提条件

```bash
# 启用 docker buildx 多架构支持
docker buildx create --use --name multiarch-builder
docker buildx inspect --bootstrap
```

### 构建并推送（多架构）

```bash
# 构建双架构镜像并推送到 registry
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t <registry>/tlm-ops-reporter:v1.2 \
  -f docker/ops-reporter/Dockerfile \
  --push .
```

### 本地加载（单架构测试）

```bash
# 仅构建当前架构，加载到本地镜像库
docker buildx build \
  --platform linux/amd64 \
  -t tlm-ops-reporter:v1.2 \
  -f docker/ops-reporter/Dockerfile \
  --load .
```

### 在 Helm 中使用

```bash
helm install tlm-ops ./deploy/helm/tlm-ops-reporter \
  --set image.repository=<registry>/tlm-ops-reporter \
  --set image.tag=v1.2 \
  --set image.pullPolicy=Always
```

> **注**：多架构镜像通过 manifest list 自动选择匹配节点架构的镜像层，K8s 无需额外配置。

## 生产环境部署

生产环境部署请参考 [production_deployment_guide.md](../../docs/ops_daily/production_deployment_guide.md)，关键差异：

- **镜像仓库**：切换为私有 registry，`pullPolicy: Always`
- **日志 PVC**：使用 `logsVolume.existingClaim` 挂载已有 PVC（非 create）
- **NetworkPolicy**：`networkPolicy.enabled: true`（最小权限出站）
- **资源限制**：根据实际负载调整 `reporter.resources`
- **ServiceMonitor**：启用并配置 `labels` 匹配 Prometheus

## 升级与回滚

```bash
# 升级（PVC 数据保留）
helm upgrade tlm-ops ./deploy/helm/tlm-ops-reporter -n monitoring --set image.tag=v1.2

# 查看历史版本
helm history tlm-ops -n monitoring

# 回滚到上一版本
helm rollback tlm-ops 0 -n monitoring
```

> PVC 使用 `ReadWriteOnce` + `Recreate` 策略，升级时 Pod 重建但 PVC 保留，日报数据不丢失。

## 卸载

```bash
helm uninstall tlm-ops -n monitoring
# PVC 需手动清理
kubectl -n monitoring delete pvc tlm-ops-reporter-output
```

## 相关文档

- [v1.2 发布说明](../../docs/ops_daily/RELEASE_NOTES_v1.2.md)
- [发布清单](../../docs/ops_daily/release_checklist_v1.2.md)
- [本地验证操作手册](../../docs/ops_daily/local_verification_guide.md)
- [K8s 验证检查清单（28 项）](../../docs/ops_daily/k8s_verification_checklist.md)
- [生产环境部署指南](../../docs/ops_daily/production_deployment_guide.md)
- [values.schema.json 校验脚本](../../scripts/verify_values_schema.ps1)

---

> **三义原则校验**:
> - [不易] 保留原有 4 项不变量约束 + 新增 2 项（cap drop ALL + 根 FS 只读），守住安全契约
> - [变易] 配置参数表增加类型列，支持 schema 校验；新增生产部署指引链接
> - [简易] 字段按功能分组（镜像/日报/存储/告警/安全/网络），30s 可定位任意参数
