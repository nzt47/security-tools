# 迁移指南: port-forward → 集群内直连压测模式

> **目标**: 将现有的 `kubectl port-forward` 压测任务安全切换为集群内 Pod 直连模式，消除代理层开销，提升压测准确性。
>
> **适用范围**: 技能检索服务（skill-retrieval-service）的所有压测、HPA 扩容验证、SLO 巡检任务。
>
> **验证日期**: 2026-08-01 | **预期收益**: P99 延迟降低 ~10.8ms，资源成本降低 89%，QPS 上限提升 788%

---

## 1. 背景与动机

### 1.1 问题: port-forward 代理开销

原有压测架构通过 `kubectl port-forward` 将本地端口映射到集群内 Service：

```
本地 k6/Python 客户端 → kubectl port-forward (kube-proxy) → Pod
```

**实测开销**（见 `docs/PORT_FORWARD_OVERHEAD_COMPARISON.md`）:

| 指标 | port-forward | 集群内直连 | 差异 |
|------|-------------|-----------|------|
| P99 延迟 | 43.32 ms | 32.31 ms | **-10.8 ms（-25%）** |
| QPS 上限 | 300 | 2665+ | **+788%** |
| 主机内存占用 | 61 MB | 0 MB | **-100%** |
| 总资源成本 | 基准 | 降低 89% | — |

### 1.2 根因

`kubectl port-forward` 基于 `kube-proxy` 的 HTTP 隧道，存在以下固有开销：

1. **协议层**: HTTP/1.1 隧道封装/解封，每请求多一次 copy
2. **进程层**: kube-proxy 进程作为中间人，CPU 调度延迟
3. **内存层**: 每个端口映射维护 spdy 连接，~61MB 常驻内存
4. **稳定性**: 连接断开后需手动重启 port-forward，影响长时压测

### 1.3 集群内直连方案

```
压测 Pod（集群内）→ Service ClusterIP DNS → Pod（直连，无代理）
```

**核心优势**: 流量在集群网络命名空间内直接路由，无用户态代理，延迟接近物理网络极限。

---

## 2. 迁移前置检查（必做）

### 2.1 环境检查清单

```powershell
# 1. 确认 Service 已部署且 ClusterIP 可解析
kubectl get svc skill-retrieval-service -n production
# 期望: TYPE=ClusterIP, CLUSTER-IP=10.x.x.x, PORT(S)=8080/TCP

# 2. 确认 DNS 解析正常（在集群内 Pod 中执行）
kubectl run dns-test --rm -it --image=busybox --restart=Never -- \
  nslookup skill-retrieval-service.production.svc.cluster.local
# 期望: 解析到 Service ClusterIP

# 3. 确认 Pod 间网络连通（CNI 正常）
kubectl run net-test --rm -it --image=curlimages/curl --restart=Never -- \
  curl -s -o /dev/null -w "%{http_code}" \
  http://skill-retrieval-service.production.svc.cluster.local:8080/health
# 期望: 200

# 4. 确认 HPA 已部署（压测目标依赖）
kubectl get hpa -n production
# 期望: skill-retrieval-hpa REF=Deployment/skill-retrieval-service

# 5. 确认 metrics-server 正常（HPA 扩容依赖）
kubectl get apiservice | Select-String "v1beta1.metrics.k8s.io"
# 期望: True
```

### 2.2 压测镜像准备

集群内压测需要镜像可在集群内拉取。本地 kind 集群需提前加载：

```powershell
# 方式 1: kind 集群加载本地镜像
kind load docker-image skill-retrieval:local --name <cluster-name>

# 方式 2: 推送到私有 registry（生产环境推荐）
docker tag skill-retrieval:local <registry>/skill-retrieval:v1.0.0
docker push <registry>/skill-retrieval:v1.0.0

# 验证镜像在集群内可用
kubectl run img-test --rm -it --image=skill-retrieval:local --restart=Never -- echo "image ok"
```

### 2.3 RBAC 权限检查（如压测 Pod 需查询 HPA 状态）

```powershell
# 确认 default ServiceAccount 可读取 HPA 状态（巡检脚本依赖）
kubectl auth can-i get hpa --as=system:serviceaccount:production:default -n production
# 期望: yes
# 若 no，需创建 RoleBinding（见第 5 节）
```

---

## 3. 迁移步骤（灰度切换）

### 3.1 阶段 1: 准备集群内压测 Pod（不切换流量）

部署一个压测 Pod，但**不停止** port-forward 任务，形成双跑对照：

```yaml
# deploy/k8s/loadtest-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: in-cluster-loadtest
  namespace: production
  labels:
    app: in-cluster-loadtest
spec:
  restartPolicy: Never
  containers:
    - name: loadtest
      image: skill-retrieval:local  # 复用服务镜像（含 Python 压测脚本）
      command: ["sleep", "3600"]   # 保持运行，手动 exec 执行压测
      env:
        - name: TARGET_ENDPOINT
          value: "http://skill-retrieval-service.production.svc.cluster.local:8080/match"
```

```powershell
kubectl apply -f deploy/k8s/loadtest-pod.yaml
kubectl wait --for=condition=Ready pod/in-cluster-loadtest -n production --timeout=60s
```

### 3.2 阶段 2: 集群内压测基线验证（对照测试）

在集群内 Pod 中执行与 port-forward **完全相同参数**的压测，对比结果：

```powershell
# 集群内压测（新方案）
kubectl exec -n production pod/in-cluster-loadtest -- python /app/scripts/hpa_scale_test.py `
  --endpoint http://skill-retrieval-service.production.svc.cluster.local:8080/match `
  --vu 50 --duration 120 --scenario stress

# 同时（另开终端）执行 port-forward 压测（旧方案）
kubectl port-forward -n production svc/skill-retrieval-service 18080:8080
python scripts/hpa_scale_test.py --endpoint http://localhost:18080/match `
  --vu 50 --duration 120 --scenario stress-old
```

**对照验收标准**（必须全部满足）:

| 指标 | 验收条件 | 不通过处理 |
|------|---------|-----------|
| P99 延迟 | 集群内 ≤ port-forward - 5ms | 检查 CNI / Pod 调度 |
| QPS | 集群内 ≥ port-forward + 10% | 检查网络 MTU / CPU 调度 |
| 错误率 | 双方均 < 0.1% | 检查服务健康状态 |
| HPA 扩容触发 | 双方均触发扩容 | 检查 metrics-server |

### 3.3 阶段 3: 切换 CI/CD 压测任务

验收通过后，更新 CI/CD 配置，将压测任务从 port-forward 切换为集群内直连：

```yaml
# .github/workflows/loadtest.yml（修改前 - port-forward）
- name: Run loadtest via port-forward
  run: |
    kubectl port-forward -n production svc/skill-retrieval-service 18080:8080 &
    sleep 5
    python scripts/hpa_scale_test.py --endpoint http://localhost:18080/match ...

# .github/workflows/loadtest.yml（修改后 - 集群内直连）
- name: Run loadtest via in-cluster Pod
  run: |
    kubectl apply -f deploy/k8s/loadtest-pod.yaml
    kubectl wait --for=condition=Ready pod/in-cluster-loadtest -n production --timeout=60s
    kubectl exec -n production pod/in-cluster-loadtest -- python /app/scripts/hpa_scale_test.py \
      --endpoint http://skill-retrieval-service.production.svc.cluster.local:8080/match ...
    kubectl delete pod -n production in-cluster-loadtest --ignore-not-found
```

### 3.4 阶段 4: 停用旧 port-forward 任务

确认新任务稳定运行 **3 个周期**（如每日压测则观察 3 天）后，移除旧任务：

```powershell
# 删除旧的 port-forward 相关脚本/任务（保留代码以便回滚）
git mv scripts/run_port_forward_loadtest.ps1 scripts/_archived/run_port_forward_loadtest.ps1.bak
```

---

## 4. 回滚方案

### 4.1 快速回滚（5 分钟内）

若集群内直连方案出现异常，立即回滚到 port-forward：

```powershell
# 1. 恢复 CI/CD 配置
git revert <commit-hash>  # 回滚 loadtest.yml 修改

# 2. 删除集群内压测 Pod
kubectl delete pod -n production in-cluster-loadtest --ignore-not-found

# 3. 恢复 port-forward 任务
kubectl port-forward -n production svc/skill-retrieval-service 18080:8080 &
```

### 4.2 回滚触发条件

出现以下任一情况立即回滚：

| 触发条件 | 阈值 | 说明 |
|---------|------|------|
| 压测 P99 延迟 | > 50ms（持续 3 次） | 高于 port-forward 方案 |
| 压测错误率 | > 1% | 集群网络异常 |
| HPA 未触发扩容 | 压测期间副本数无变化 | 流量未到达服务 |
| 压测 Pod 启动失败 | 连续 2 次 CrashLoopBackOff | 镜像/权限问题 |

---

## 5. 潜在风险点与缓解措施

### 5.1 高风险（必须缓解）

#### 风险 1: 压测 Pod 与服务 Pod 调度到同一节点 → 资源竞争

**影响**: 压测 Pod 抢占服务 Pod 的 CPU/内存，导致服务性能下降，压测结果失真。

**缓解**:
```yaml
# 在压测 Pod 中添加反亲和性，避免与服务 Pod 同节点
spec:
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchExpressions:
              - key: app
                operator: In
                values: ["skill-retrieval-service"]
          topologyKey: kubernetes.io/hostname
```

**验证**:
```powershell
# 确认压测 Pod 与服务 Pod 不在同一节点
kubectl get pod -n production -o wide | Select-String "skill-retrieval|in-cluster-loadtest"
```

#### 风险 2: 压测流量在集群内"短路"，绕过 Ingress/网关

**影响**: 压测未经过生产网关的限流/鉴权，无法反映真实用户体验。

**缓解**:
- 若需测试网关层，压测端点改为 Ingress 域名: `http://<ingress-host>/match`
- 若仅测试服务本身（HPA 扩容验证），直连 Service 是正确选择
- **明确压测目标**: HPA 扩容验证 → 直连 Service；端到端 SLO → 经 Ingress

#### 风险 3: 压测 Pod 残留 → 资源泄漏

**影响**: 压测 Pod 未清理，长期占用集群资源，影响生产 Pod 调度。

**缓解**:
```yaml
# 设置 TTL 或使用 Job（自动清理）
spec:
  restartPolicy: Never
  activeDeadlineSeconds: 600  # 10 分钟超时强制终止
  ttlSecondsAfterFinished: 300  # 完成后 5 分钟自动清理
```

```powershell
# 巡检脚本中增加残留 Pod 检测
kubectl get pod -n production -l app=in-cluster-loadtest --no-headers | 
  Where-Object { $_ -match "Completed|Error" } | 
  ForEach-Object { kubectl delete pod -n production ($_.Split()[0]) }
```

### 5.2 中风险（建议缓解）

#### 风险 4: DNS 解析延迟 → 首次请求慢

**影响**: 集群内 DNS（CoreDNS）首次解析 Service 域名可能耗时 10-50ms，影响首次压测请求。

**缓解**:
```python
# 压测脚本增加预热请求（已实现）
def warmup(endpoint, count=10):
    for _ in range(count):
        requests.get(endpoint.replace("/match", "/health"))
```

#### 风险 5: 压测 Pod 缺少 RBAC 权限 → 无法查询 HPA 状态

**影响**: 巡检脚本需要读取 HPA 副本数，若 ServiceAccount 无权限会失败。

**缓解**:
```yaml
# deploy/k8s/loadtest-rbac.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: loadtest-hpa-reader
  namespace: production
rules:
  - apiGroups: ["autoscaling"]
    resources: ["horizontalpodautoscalers"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods", "services"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: loadtest-hpa-reader-binding
  namespace: production
subjects:
  - kind: ServiceAccount
    name: default
    namespace: production
roleRef:
  kind: Role
  name: loadtest-hpa-reader
  apiGroup: rbac.authorization.k8s.io
```

#### 风险 6: 压测 Pod 镜像版本与服务不一致 → 行为差异

**影响**: 压测脚本依赖服务镜像内的 Python 脚本，若镜像版本不一致，压测逻辑可能过期。

**缓解**:
- 压测脚本单独打包为独立镜像（推荐）: `<registry>/skill-loadtest:v1.0.0`
- 或在 CI/CD 中同步构建服务镜像和压测镜像，确保版本一致

### 5.3 低风险（知晓即可）

#### 风险 7: 集群内压测无法模拟外部网络延迟

**说明**: 集群内直连无法测试跨可用区/跨地域的网络延迟场景。

**适用场景**: 若需测试外部用户体验，仍需保留 port-forward 或使用 LoadBalancer + 外部压测节点。

#### 风险 8: CoreDNS 单点故障 → 压测失败

**说明**: CoreDNS 不可用时，Service 域名解析失败，压测无法执行。

**缓解**: CoreDNS 通常多副本部署，风险极低。可监控 `coredns_dns_request_total` 指标。

---

## 6. 验证清单（迁移完成后执行）

```powershell
# ═══ 迁移验证清单（逐项确认）═══

# 1. 集群内压测 Pod 正常运行
kubectl get pod -n production -l app=in-cluster-loadtest
# [ ] STATUS=Running, READY=1/1

# 2. 压测 Pod 可解析 Service DNS
kubectl exec -n production deploy/in-cluster-loadtest -- \
  nslookup skill-retrieval-service.production.svc.cluster.local
# [ ] 解析到 ClusterIP

# 3. 压测 Pod 可访问服务 /health
kubectl exec -n production deploy/in-cluster-loadtest -- \
  curl -s -o /dev/null -w "%{http_code}" \
  http://skill-retrieval-service.production.svc.cluster.local:8080/health
# [ ] 200

# 4. 集群内压测 P99 < port-forward P99
kubectl exec -n production deploy/in-cluster-loadtest -- \
  python /app/scripts/hpa_scale_test.py --duration 60 --vu 20
# [ ] P99 < 35ms（port-forward 基准 43ms）

# 5. HPA 扩容正常触发
kubectl exec -n production deploy/in-cluster-loadtest -- \
  python /app/scripts/hpa_scale_test.py --vu 100 --duration 120
kubectl get hpa -n production
# [ ] 副本数从 3 扩容到 9+

# 6. 旧 port-forward 任务已停用
Get-Process | Where-Object { $_.ProcessName -eq "kubectl" }
# [ ] 无残留 port-forward 进程

# 7. CI/CD 压测任务使用新方案
Get-Content .github/workflows/loadtest.yml | Select-String "in-cluster-loadtest"
# [ ] 匹配到新方案

# 8. 回滚脚本可用
Test-Path scripts/_archived/run_port_forward_loadtest.ps1.bak
# [ ] True
```

---

## 7. 附录: 资源成本对比

详细数据见 `docs/PORT_FORWARD_OVERHEAD_COMPARISON.md`，关键指标:

| 维度 | port-forward | 集群内直连 | 改善 |
|------|-------------|-----------|------|
| **P99 延迟** | 43.32 ms | 32.31 ms | -25% |
| **QPS 上限** | 300 | 2665+ | +788% |
| **主机 CPU** | 0.5 核（kube-proxy） | 0 核 | -100% |
| **主机内存** | 61 MB | 0 MB | -100% |
| **集群资源** | 0（压测在主机） | 200m CPU + 256Mi（压测 Pod） | 新增 |
| **总成本指数** | 100（基准） | 11 | **-89%** |

---

## 8. 变更记录

| 日期 | 版本 | 变更 | 验证人 |
|------|------|------|--------|
| 2026-08-01 | v1.0 | 初始版本，基于压测验证数据创建 | — |

---

**相关文档**:
- [HPA 对比压测计划](HPA_COMPARISON_LOADTEST_PLAN.md)
- [port-forward 开销对比报告](PORT_FORWARD_OVERHEAD_COMPARISON.md)
- [HPA 生产配置](../deploy/k8s/hpa-production.yaml)
- [HPA 扩容巡检脚本](../scripts/hpa_scale_patrol.py)
