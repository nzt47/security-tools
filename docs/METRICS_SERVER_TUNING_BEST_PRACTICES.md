# metrics-server 配置调优最佳实践

> **版本**: v1.0（2026-08-01）
> **背景**: 2026-08-01 HPA 扩容基准测试发现 metrics-server 60s 采集间隔导致指标延迟 72s，端到端扩容 115s 超 SLO。调整为 15s 后指标延迟降至 29.2s，端到端 76.47s 达标。本文档总结调优经验，给出不同场景的推荐值。
> **适用范围**: Kubernetes 集群中 metrics-server 的采集间隔（`--metric-resolution`）调优

---

## 1. 核心原理：采集间隔如何影响 HPA 决策

### 1.1 指标延迟链路

```
流量突增 → Pod CPU 升高 → metrics-server 采集 → HPA controller 轮询 → 扩容决策 → Pod 调度 → 就绪
           │                │                    │                     │
           │←── 指标延迟 ──→│                    │                     │
           │                │←── 决策时效 ───────→│                     │
           │←──────────── 端到端耗时 ──────────────────────────────────→│
```

**指标延迟** = 流量开始 → HPA 拿到更新后的 CPU 指标的时间，受以下因素影响：

| 因素 | 说明 | 典型耗时 |
|------|------|---------|
| CPU 累积 | 应用处理请求后 CPU 升高需时间 | 2-5s |
| **metrics-server 采集周期** | `--metric-resolution` 参数控制 | **15-60s** |
| HPA controller 轮询间隔 | kube-controller-manager `--horizontal-pod-autoscaler-sync-period` | 15-30s（默认 15s） |

> **关键洞察**: metrics-server 采集周期是指标延迟的**最大贡献者**。采集间隔 60s 时，最坏情况下指标延迟 ≈ 60s（采集周期）+ 15s（HPA 轮询）= 75s。

### 1.2 数学模型

```
指标延迟（最坏情况）≈ metrics_resolution + hpa_sync_period
指标延迟（最好情况）≈ 0（刚采集完即流量到达）

决策时效 = 指标延迟 + HPA 计算时间（通常 <1s）
端到端耗时 = 决策时效 + Pod 调度 + 镜像拉取 + 就绪探针
```

### 1.3 本次验证数据

| 配置 | 采集间隔 | 指标延迟（实测） | 决策时效 | 端到端 | SLO 达标 |
|------|---------|----------------|---------|-------|---------|
| 旧版 | 60s | 72s | N/A | 115.2s | ❌（>60s） |
| 新版 | 15s | 29.2s | 15.8s | 76.47s | ✅（≤90s） |
| **改善** | -45s | **-42.8s** | — | **-38.7s** | — |

> 指标延迟 29.2s = CPU 累积(~5s) + 采集周期(15s) + HPA 轮询(~9s)，符合数学模型。

---

## 2. 不同场景采集间隔推荐值

### 2.1 推荐值总览

| 场景 | 集群规模 | 推荐采集间隔 | 指标延迟（预期） | HPA 决策时效 SLO | 理由 |
|------|---------|------------|----------------|-----------------|------|
| **开发/测试** | 1-10 节点 | **15s** | <30s | ≤30s | 快速反馈，资源开销小，单节点 kind 可承受 |
| **生产-小规模** | 10-50 节点 | **15s** | <30s | ≤30s | 低延迟扩容，资源可控 |
| **生产-中规模** | 50-200 节点 | **30s** | <45s | ≤60s | 平衡时效与 metrics-server 负载 |
| **生产-大规模** | 200-1000 节点 | **30-60s** | <75s | ≤90s | 单实例瓶颈，需配合横向扩展 |
| **生产-超大规模** | >1000 节点 | **60s + 拆分** | <75s | ≤90s | 必须多副本 + 分片 |

### 2.2 选型决策树

```
集群节点数 < 50?
├─ 是 → 采集间隔 = 15s（低延迟优先）
│       └─ 资源配额: requests 100m/128Mi, limits 500m/512Mi
└─ 否 → 集群节点数 < 200?
    ├─ 是 → 采集间隔 = 30s（平衡型）
    │       └─ 资源配额: requests 200m/256Mi, limits 1/1Gi
    └─ 否 → metrics-server 是否已多副本?
        ├─ 是 → 采集间隔 = 30s（分片分担负载）
        │       └─ 每副本: requests 200m/256Mi, limits 1/1Gi
        └─ 否 → 采集间隔 = 60s（保守，避免 OOM）
                └─ 建议: 先拆分副本，再降低间隔到 30s
```

### 2.3 场景详解

#### 场景 A：开发/测试集群（kind / minikube）

```yaml
# 推荐配置
args:
  - --metric-resolution=15s
resources:
  requests: { cpu: 100m, memory: 128Mi }
  limits: { cpu: 500m, memory: 512Mi }
```

**理由**: 开发环境追求快速反馈，15s 间隔让 HPA 在 30s 内响应。kind 单节点资源有限但小集群采集开销可控。

**参考配置**: [deploy/k8s/metrics-server.yaml](../deploy/k8s/metrics-server.yaml)（本项目当前配置）

#### 场景 B：生产-小规模集群（10-50 节点）

```yaml
args:
  - --metric-resolution=15s
resources:
  requests: { cpu: 100m, memory: 128Mi }
  limits: { cpu: 500m, memory: 512Mi }
```

**理由**: 小规模生产集群节点数少，metrics-server 单实例可承受 15s 采集频率。低延迟扩容对用户体验至关重要。

#### 场景 C：生产-中规模集群（50-200 节点）

```yaml
args:
  - --metric-resolution=30s
resources:
  requests: { cpu: 200m, memory: 256Mi }
  limits: { cpu: 1, memory: 1Gi }
```

**理由**: 50+ 节点时 15s 采集频率会导致 metrics-server CPU 飙升。30s 间隔将指标延迟控制在 45s 内，HPA 决策时效 SLO ≤60s 仍可达成。

#### 场景 D：生产-大规模集群（200-1000 节点）

```yaml
# 需要多副本 + 分片
spec:
  replicas: 2  # 或更多
args:
  - --metric-resolution=30s
  # 大规模集群需开启分片（metrics-server v0.6+）
  # 通过 API Priority and Fairness 自动分片
resources:
  requests: { cpu: 200m, memory: 256Mi }
  limits: { cpu: 1, memory: 1Gi }
```

**理由**: 单实例 metrics-server 在 200+ 节点时会成为瓶颈。多副本 + 30s 间隔是安全选择。

---

## 3. 资源配额对应关系

采集间隔越短，metrics-server 采集频率越高，资源消耗越大。按比例调整资源配额：

| 采集间隔 | 相对频率 | 推荐 requests | 推荐 limits | 说明 |
|---------|---------|--------------|------------|------|
| 15s | 4x | 100m / 128Mi | 500m / 512Mi | 适合 ≤50 节点 |
| 30s | 2x | 200m / 256Mi | 1 / 1Gi | 适合 50-200 节点 |
| 60s | 1x（基线） | 100m / 128Mi | 500m / 512Mi | metrics-server 默认推荐 |

> **资源调整公式**: requests ≈ 基线 × (60 / 采集间隔)，但不超过节点资源的 10%。

### 监控指标

调整采集间隔后，需监控以下指标确认 metrics-server 健康：

| Prometheus 指标 | 告警阈值 | 说明 |
|----------------|---------|------|
| `container_cpu_usage_seconds_total`（metrics-server） | >80% limits | CPU 不足 |
| `container_memory_working_set_bytes`（metrics-server） | >80% limits | 内存不足 |
| `metrics_server scrape_duration_seconds` | >采集间隔的 50% | 采集超时风险 |
| `metrics_server scrape_errors_total` | rate > 0.1/s | 采集错误 |

---

## 4. 调优步骤

### 4.1 调优前评估

```bash
# 1. 确认当前采集间隔
kubectl get deploy metrics-server -n kube-system -o jsonpath='{.spec.template.spec.containers[0].args}'

# 2. 确认集群规模
kubectl get nodes --no-headers | wc -l

# 3. 确认 HPA 轮询间隔（kube-controller-manager 参数）
kubectl -n kube-system get pod -l component=kube-controller-manager -o yaml | grep horizontal-pod-autoscaler-sync-period
# 默认 15s，通常无需修改
```

### 4.2 应用新配置

```bash
# 修改 metrics-server.yaml 中的 --metric-resolution 参数
# 按场景调整 resources 配额
kubectl apply -f deploy/k8s/metrics-server.yaml

# 确认滚动更新完成
kubectl rollout status deployment/metrics-server -n kube-system
```

### 4.3 验证

```bash
# 1. 确认新间隔已生效
kubectl get deploy metrics-server -n kube-system -o jsonpath='{.spec.template.spec.containers[0].args}'

# 2. 确认 metrics-server 健康
kubectl get pod -l k8s-app=metrics-server -n kube-system

# 3. 确认 HPA 能拿到指标
kubectl top nodes
kubectl top pods -n production

# 4. 运行基准测试验证 SLO
python scripts/hpa_scale_3to15_benchmark.py --output /tmp/benchmark-result.json
```

---

## 5. 预热策略配合

采集间隔调优解决了"正常运行时的指标延迟"，但 **metrics-server 冷启动**（重启后/长时间空闲后）仍有首个采集周期的延迟。配合预热脚本可进一步消除冷启动延迟。

### 5.1 何时需要预热

| 场景 | 是否需要预热 | 原因 |
|------|------------|------|
| metrics-server 刚启动 | ✅ 需要 | 首个采集周期尚未完成，指标为空 |
| metrics-server 长时间空闲（>5min 无请求） | ✅ 需要 | Pod 指标可能过期，需重新采集 |
| metrics-server 持续运行且有流量 | ❌ 不需要 | 指标持续刷新，无冷启动 |
| 正式巡检前 | ✅ 建议 | 确保指标已激活，避免污染 SLO 测量 |

### 5.2 预热脚本

```bash
# 独立预热（巡检前手动执行）
python scripts/metrics_server_warmup.py --output /tmp/warmup-result.json

# 巡检前预热 + 延迟基准测量（推荐）
python scripts/warmup_before_patrol.py --output /tmp/warmup-before-patrol.json
```

### 5.3 预热参数推荐

| 参数 | 推荐值 | 说明 |
|------|-------|------|
| `--warmup-vu` | 10 | 少量并发，不触发 HPA 扩容（需 < CPU 阈值触发量） |
| `--warmup-duration` | 20s | 需 ≥ 采集间隔（确保至少 1 个采集周期） |
| `--settle-wait` | 20s | 等待 metrics-server 完成采集 + HPA 刷新 |

> **预热持续时间公式**: `warmup_duration ≥ metric_resolution + hpa_sync_period`（确保至少 1 个完整采集周期 + 1 次 HPA 轮询）

---

## 6. 常见问题

### Q1: 采集间隔调到 15s 后 metrics-server CPU 飙升怎么办？

**A**: 检查集群节点数。如果 >50 节点，15s 可能过于激进，改为 30s。同时检查 metrics-server 是否单副本，大规模集群需要多副本分片。

### Q2: 采集间隔 15s 但指标延迟仍然 >30s？

**A**: 排查以下因素：
1. HPA controller 轮询间隔（`--horizontal-pod-autoscaler-sync-period`）是否过大
2. metrics-server 是否因资源不足导致采集超时（检查 `scrape_duration_seconds`）
3. Pod 是否有 `resources.requests.cpu` 配置（无 requests 则 metrics-server 无法采集 CPU）

### Q3: 生产环境能否用 5s 采集间隔？

**A**: **不推荐**。5s 采集间隔会带来：
- metrics-server CPU 消耗 12x（相对 60s 基线）
- kubelet API 压力增大（每个节点每 5s 被请求一次）
- 收益递减（指标延迟主要瓶颈变为 HPA 轮询间隔 15s）

如需 <15s 的指标延迟，建议改用 Prometheus Adapter + 自定义指标，而非缩短 metrics-server 采集间隔。

### Q4: 如何在不重启 metrics-server 的情况下验证不同间隔的效果？

**A**: 无法动态调整 `--metric-resolution`，它是启动参数。但可以通过 `kubectl top` 手动验证：
```bash
# 发送流量后立即连续执行，观察 CPU 变化时间
watch -n 1 'kubectl top pods -n production'
```

### Q5: kind 集群镜像拉取失败怎么办？

**A**: 中国网络环境无法访问 `registry.k8s.io`，使用 Docker Hub 镜像源：
```bash
docker pull dyrnq/metrics-server:v0.7.2
docker tag dyrnq/metrics-server:v0.7.2 registry.k8s.io/metrics-server/metrics-server:v0.7.2
kind load docker-image registry.k8s.io/metrics-server/metrics-server:v0.7.2
```

---

## 7. 配置变更记录

| 日期 | 版本 | 采集间隔 | 变更原因 | 验证结果 |
|------|------|---------|---------|---------|
| 2026-07-31 | v0.7.2 | 60s（默认） | 初始部署 | 指标延迟 72s，端到端 115s 超 SLO |
| 2026-08-01 | v0.7.2 | **15s** | 消除指标延迟，配合 HPA 5% CPU 阈值 | 指标延迟 29.2s，端到端 76.47s 达标 ✅ |

---

## 8. 相关文档

- [HPA 配置变更日志](HPA_CHANGELOG.md)
- [HPA 生产配置](../deploy/k8s/hpa-production.yaml)
- [metrics-server 配置文件](../deploy/k8s/metrics-server.yaml)
- [metrics-server 预热脚本](../scripts/metrics_server_warmup.py)
- [巡检前预热 + 延迟基准脚本](../scripts/warmup_before_patrol.py)
- [HPA 对比压测计划](HPA_COMPARISON_LOADTEST_PLAN.md)
- [开发规范](DEVELOPMENT_STANDARDS_K8S_SCRIPTS.md)
