# metrics-server 生产环境配置调整方案（50-200 节点）

> **版本**: v1.0（2026-08-01）
> **目标场景**: 生产环境 50-200 节点集群
> **前置文档**: [METRICS_SERVER_TUNING_BEST_PRACTICES.md](./METRICS_SERVER_TUNING_BEST_PRACTICES.md)（场景 C）
> **目标产物**: [deploy/k8s/metrics-server-production-30s.yaml](../deploy/k8s/metrics-server-production-30s.yaml)
> **回滚产物**: [deploy/k8s/metrics-server.yaml](../deploy/k8s/metrics-server.yaml)（15s/kind 配置）

---

## 1. 背景与目标

### 1.1 现状

当前 `deploy/k8s/metrics-server.yaml` 为 **kind 开发集群**配置：

- 采集间隔 **15s**（4x 频率，kind 单节点可承受）
- 单副本，`--kubelet-insecure-tls`（跳过 TLS 验证）
- 资源配额偏低（requests 100m/128Mi）

### 1.2 问题

将此配置直接套用到 **50-200 节点生产集群**存在风险：

| 风险 | 影响 | 严重度 |
|------|------|--------|
| 15s 采集间隔 × 200 节点 | metrics-server CPU 瓶颈/OOM，采集延迟反升 | 🔴 高 |
| `--kubelet-insecure-tls` | 生产环境明文通信，不符合安全合规 | 🔴 高 |
| 单副本 | 滚动更新/节点故障期间 HPA 指标中断 | 🟡 中 |
| 资源配额偏低 | 高负载下 OOMKill，指标采集中断 | 🟡 中 |

### 1.3 目标

按最佳实践文档场景 C 调整为生产配置，确保：

- **【不易】** HPA 决策时效 SLO ≤ 60s（指标延迟 ≤ 45s）
- **【不易】** 生产环境 TLS 安全（移除 `--kubelet-insecure-tls`）
- **【变易】** 2 副本高可用，单点故障不影响指标采集
- **【简易】** 资源配额按 2x 采集频率比例提升

---

## 2. 配置变更对比

| 配置项 | 现状（kind/15s） | 目标（生产/30s） | 变更理由 |
|--------|-----------------|-----------------|----------|
| `--metric-resolution` | `15s` | **`30s`** | 50-200 节点平衡时效与负载（最佳实践场景 C） |
| `replicas` | `1` | **`2`** | 高可用，滚动更新零中断 |
| `--kubelet-insecure-tls` | 启用 | **移除** | 生产环境使用 CA 证书，符合安全合规 |
| 反亲和性 | 无 | **required（跨节点）** | 2 副本调度到不同节点 |
| `requests.cpu` | `100m` | **`200m`** | 2x 采集频率按比例提升 |
| `requests.memory` | `128Mi` | **`256Mi`** | 2x 采集频率按比例提升 |
| `limits.cpu` | `500m` | **`1`** | 应对 200 节点采集峰值 |
| `limits.memory` | `512Mi` | **`1Gi`** | 避免 OOMKill |
| CA 证书挂载 | 无 | **hostPath /etc/kubernetes/pki** | 替代 insecure-tls |
| 滚动更新策略 | maxUnavailable=0 | **maxUnavailable=1, maxSurge=1** | 2 副本下保留可用 |
| APIService caBundle | 无 | 待配置（见 §5 风险点 R3） | insecureSkipTLSVerify 仍为 true |

### 2.1 SLO 影响分析

```
指标延迟（最坏）≈ metric_resolution + hpa_sync_period
              = 30s + 15s = 45s  （满足 ≤45s 目标）

决策时效 = 指标延迟 + HPA 计算 ≈ 45s + 1s = 46s  （满足 ≤60s SLO）
端到端耗时 ≈ 决策时效 + Pod 调度 + 就绪 ≈ 46s + 30s = 76s
```

> 注: 30s 间隔较 15s 牺牲约 15s 指标延迟余量，但换取 metrics-server 负载减半，
> 在 50-200 节点规模下是必要权衡。配合 `warmup_before_patrol.py` 预热脚本
> 消除冷启动后，实际指标延迟可进一步降低。

---

## 3. 迁移步骤（灰度部署）

### 3.1 前置准备

```bash
# 1. 确认当前 metrics-server 状态
kubectl get deploy metrics-server -n kube-system
kubectl top nodes                                    # 验证当前可用
kubectl get hpa -n production                        # 确认 ScalingActive=True

# 2. 备份当前配置
kubectl get deploy metrics-server -n kube-system -o yaml > /tmp/metrics-server-backup.yaml

# 3. 确认目标文件就绪
cat deploy/k8s/metrics-server-production-30s.yaml | grep metric-resolution
# 预期: - --metric-resolution=30s
```

### 3.2 阶段 1：应用新配置（滚动更新）

```bash
# 应用生产配置（2 副本会滚动创建，旧单副本逐步替换）
kubectl apply -f deploy/k8s/metrics-server-production-30s.yaml

# 观察 rollout（反亲和性确保 2 副本跨节点）
kubectl rollout status deploy/metrics-server -n kube-system --timeout=180s

# 预期输出: deployment "metrics-server" successfully rolled out
```

### 3.3 阶段 2：功能验证

```bash
# 2.1 验证副本数与调度
kubectl get pod -n kube-system -l k8s-app=metrics-server -o wide
# 预期: 2 个 Pod 分布在不同节点

# 2.2 验证采集间隔已生效
kubectl get deploy metrics-server -n kube-system -o jsonpath='{.spec.template.spec.containers[0].args}'
# 预期包含: --metric-resolution=30s，且不含 --kubelet-insecure-tls

# 2.3 验证指标可采集（等待 30-60s 冷启动）
kubectl top nodes
kubectl top pods -n production

# 2.4 验证 HPA 拿到指标
kubectl describe hpa skill-retrieval-hpa -n production | grep -A5 "Metrics:"
# 预期: currentMetrics 显示 CPU utilization，AbleToScale=True
```

### 3.4 阶段 3：SLO 验证（含预热）

```bash
# 3.1 运行预热 + 巡检一体化流程
bash scripts/patrol_with_warmup.sh

# 3.2 检查预热效果（对比冷热启动延迟）
cat /tmp/warmup-result.json | python -m json.tool | grep -A3 latency_improvement

# 3.3 检查 HPA 扩容时效
cat /tmp/patrol-result.json | python -m json.tool | grep scale_time_sec
# 预期: scale_time_sec ≤ 60（SLO 达标）
```

### 3.5 阶段 4：观察期（24h 灰度）

```bash
# 持续监控 metrics-server 资源使用
kubectl top pod -n kube-system -l k8s-app=metrics-server

# 监控指标:
#   - CPU 持续 >80% → 需进一步调优（见 §5 R1）
#   - 内存接近 1Gi limit → 考虑提升 limit 或增加副本
#   - 采集失败率（kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes）
```

---

## 4. 回滚预案

### 4.1 快速回滚（5 分钟内）

若新配置导致 HPA 失效或指标中断，立即回滚：

```bash
# 回滚到 kind/15s 配置
kubectl apply -f deploy/k8s/metrics-server.yaml

# 验证回滚
kubectl rollout status deploy/metrics-server -n kube-system
kubectl top nodes   # 恢复采集
```

> **【不易】** 回滚不丢数据: metrics-server 无状态，回滚后 30-60s 内恢复采集。

### 4.2 回滚触发条件

| 条件 | 阈值 | 动作 |
|------|------|------|
| HPA `ScalingActive` | 变为 False 持续 >5min | 立即回滚 |
| `kubectl top nodes` | 连续 3 次失败 | 立即回滚 |
| metrics-server Pod | OOMKill 重启 >2 次 | 回滚并提升 memory limit |
| 巡检 scale_time_sec | >60s 持续 2 次 | 评估回滚或调优 |

---

## 5. 风险评估与缓解

| 编号 | 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|------|----------|
| **R1** | 30s 间隔在 200 节点上限时 metrics-server CPU 瓶颈 | 中 | 指标延迟上升 | 监控 CPU >80% 告警；预案: 增至 3 副本或降回 15s |
| **R2** | 移除 `--kubelet-insecure-tls` 后 TLS 握手失败 | 中 | 全部节点采集中断 | §3.3 阶段 2.3 验证；若失败先恢复 insecure-tls 排查 CA |
| **R3** | APIService `insecureSkipTLSVerify: true` 未替换为 caBundle | 高 | 安全审计不通过 | 见 §6 后续优化；当前为兼容性保留 |
| **R4** | 反亲和性导致 2 副本无法调度（单节点集群） | 低 | Pod Pending | 仅生产多节点集群适用；kind 集群用原 15s 配置 |
| **R5** | CA 证书 hostPath 路径因发行版不同而异 | 中 | TLS 验证失败 | 路径 `/etc/kubernetes/pki` 适用于 kubeadm；RKE2/EKS 需调整 |
| **R6** | 采集间隔 15s→30s 使指标延迟 +15s | 中 | HPA 决策变慢 | SLO 仍达标（46s < 60s）；预热脚本补偿冷启动 |

---

## 6. 后续优化（未在本期完成）

### 6.1 APIService TLS 强化（R3）

当前 `metrics-server-production-30s.yaml` 的 APIService 仍保留 `insecureSkipTLSVerify: true`，后续应替换为集群 CA:

```yaml
apiVersion: apiregistration.k8s.io/v1
kind: APIService
metadata:
  name: v1beta1.metrics.k8s.io
spec:
  service:
    name: metrics-server
    namespace: kube-system
  group: metrics.k8s.io
  version: v1beta1
  insecureSkipTLSVerify: false   # ← 改为 false
  caBundle: <base64 编码的集群 CA 证书>  # ← 注入 caBundle
```

获取 caBundle:
```bash
kubectl get configmap extension-apiserver-authentication -n kube-system \
  -o jsonpath='{.data.client-ca-file}' | base64 | tr -d '\n'
```

### 6.2 大规模集群横向扩展（>200 节点）

当节点数增长超过 200，参考最佳实践文档场景 D:

- 增至 3+ 副本
- 评估是否需要分片（`--watch-resource-list` 按命名空间拆分）
- 考虑采集间隔回调至 60s

### 6.3 兼容性校验自动化

`warmup_before_patrol.py` 已内置 `check_k8s_compatibility()`，CronJob 每次巡检自动校验:

- K8s 版本 ≥ 1.19（metrics-server v0.7 要求）
- metrics-server API 注册状态
- kubectl client/server 版本偏差 ≤ 2 minor

校验失败默认阻断预热（exit 2），巡检编排脚本继续执行（预热失败不阻断巡检）。

---

## 7. 验收清单

部署完成后逐项确认:

- [ ] `kubectl get deploy metrics-server -n kube-system` 显示 `replicas=2`
- [ ] 2 个 Pod 分布在不同节点（`-o wide` 查看 NODE 列）
- [ ] `--metric-resolution=30s` 已生效
- [ ] 不含 `--kubelet-insecure-tls` 参数
- [ ] `kubectl top nodes` 正常返回节点指标
- [ ] `kubectl top pods -n production` 正常返回 Pod 指标
- [ ] `kubectl describe hpa skill-retrieval-hpa -n production` 显示 `ScalingActive=True`
- [ ] 巡检 `scale_time_sec ≤ 60s`（SLO 达标）
- [ ] 预热脚本 `latency_improvement_pct > 0`（预热有效）
- [ ] 24h 观察: metrics-server 无 OOMKill，CPU <80%

---

## 8. 变更记录

| 日期 | 版本 | 变更内容 | 作者 |
|------|------|---------|------|
| 2026-08-01 | v1.0 | 初版: 50-200 节点生产配置调整方案 | 巡检优化项目 |
