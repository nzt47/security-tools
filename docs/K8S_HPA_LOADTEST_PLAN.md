# K8s 集群 HPA 压测执行计划 — 5000 技能量级突发流量验证

> 基于 `deploy/k8s/hpa.yaml` 策略 + `scripts/simulate_hpa_burst_scale_up.py` 仿真结论
> 仿真结论：理想场景（Pod 启动=0s）30s 内 2→6 ✓；真实场景（Pod 启动=20s）t≈45s 达到 6 就绪副本
> 本计划目标：在真实 K8s 集群验证 HPA 扩缩容行为，量化 Pod 启动延迟对"30s 2→6"目标的影响

## 前置条件

| 依赖 | 验证命令 | 预期输出 |
|---|---|---|
| K8s 集群可达 | `kubectl cluster-info` | Kubernetes control plane is running |
| Metrics Server | `kubectl top pods` | 显示 CPU/内存数据 |
| Prometheus Adapter | `kubectl get apiservice v1beta1.custom.metrics.k8s.io` | Available=True |
| kube-state-metrics | `kubectl get svc -n kube-system \| grep kube-state-metrics` | 服务存在 |
| HPA 已部署 | `kubectl get hpa -n production` | skill-retrieval-hpa |

## 阶段 0：部署确认与基线检查（5 分钟）

### 0.1 确认部署状态
```bash
# 确认 Deployment 副本数 = 2（HPA minReplicas）
kubectl get deployment skill-retrieval-service -n production

# 确认 HPA 配置（关注 TARGETS 列是否有值）
kubectl get hpa skill-retrieval-hpa -n production

# 确认所有 Pod 就绪
kubectl get pods -n production -l app=skill-retrieval-service -o wide
```

### 0.2 确认自定义指标可用（关键）
```bash
# 验证 Prometheus Adapter 能查到 skill_match_latency_p99
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/production/pods/*/skill_match_latency_p99"

# 验证 skill_match_qps
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/production/pods/*/skill_match_qps"
```
> ⚠ 若返回 NotFound 或空白，说明 Prometheus Adapter 未正确配置自定义指标，HPA 的 P99/QPS 触发条件不会生效。需先修复 Adapter rules 配置（见 `deploy/k8s/hpa.yaml` 注释段）。

### 0.3 确认服务可访问
```bash
# 获取 Service ClusterIP
SVC_IP=$(kubectl get svc skill-retrieval-service -n production -o jsonpath='{.spec.clusterIP}')
echo "Service IP: $SVC_IP"

# 从集群内 Pod 测试健康检查
kubectl run -n production curl-test --image=curlimages/curl --rm -it --restart=Never -- \
  curl -s http://$SVC_IP:8080/health

# 测试检索接口（替换为实际 API 路径）
kubectl run -n production curl-test --image=curlimages/curl --rm -it --restart=Never -- \
  curl -s -X POST http://$SVC_IP:8080/match -H "Content-Type: application/json" -d '{"query":"帮我解析PDF文件","top_k":5}'
```

## 阶段 1：基线压测 — 稳态容量确认（3 分钟）

### 1.1 低流量基线（确认单副本 200 QPS 容量）
```bash
# 使用 k6 发起 100 QPS 持续 60s（低于 HPA 阈值，不应触发扩容）
k6 run --vus 20 --duration 60s -e ENDPOINT=http://$SVC_IP:8080/match scripts/k6_skill_match.js
```
> 备选（无 k6 时用 hey）：
> ```bash
> hey -z 60s -c 20 -q 5 -m POST -H "Content-Type: application/json" -d '{"query":"解析PDF","top_k":5}' http://$SVC_IP:8080/match
> ```

### 1.2 基线观察指标
```bash
# 实时观察 HPA 状态（应保持 2 副本）
kubectl get hpa skill-retrieval-hpa -n production -w

# 实时观察 Pod CPU/内存
kubectl top pods -n production -l app=skill-retrieval-service

# 观察 P99 延迟指标（应 < 40ms）
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/production/pods/*/skill_match_latency_p99"
```

| 指标 | 预期值 | 通过判据 |
|---|---|---|
| 副本数 | 2（不变） | 未触发扩容 |
| 每副本 QPS | ~50 | < 200 阈值 |
| P99 延迟 | < 40ms | < HPA 阈值 |
| CPU 利用率 | < 70% | < HPA 阈值 |

## 阶段 2：突发流量压测 — 验证 2→6 扩容（核心，5 分钟）

### 2.1 发起突发流量（QPS 1200，对应仿真场景）
```bash
# 方式 A: k6 阶段化压测（0→1200 QPS 瞬时跳变）
k6 run -e ENDPOINT=http://$SVC_IP:8080/match scripts/k6_burst_1200qps.js

# 方式 B: hey 突发流量（500 并发，持续 120s）
hey -z 120s -c 500 -q 2.4 -m POST -H "Content-Type: application/json" \
  -d '{"query":"帮我解析PDF文件并提取表格数据","top_k":5}' \
  http://$SVC_IP:8080/match
```
> `-q 2.4` × 500 并发 = 1200 QPS（对齐仿真参数）

### 2.2 实时观察扩容过程（关键）
```bash
# 终端 1: 观察 HPA 决策（每 15s 刷新一次）
kubectl get hpa skill-retrieval-hpa -n production -w

# 终端 2: 观察 Pod 创建与就绪过程
kubectl get pods -n production -l app=skill-retrieval-service -w

# 终端 3: 观察 HPA Events（扩容原因）
kubectl describe hpa skill-retrieval-hpa -n production
```

### 2.3 扩容时序记录（对照仿真结论）
```bash
# 记录扩容事件时间戳
kubectl get events -n production --sort-by='.lastTimestamp' \
  --field-selector reason=ScalingReplicaSet | tail -20

# 记录 Pod 就绪时间（用于计算 Pod 启动延迟）
kubectl get pods -n production -l app=skill-retrieval-service \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].state.running.startedAt}{"\n"}{end}'
```

### 2.4 核心验证矩阵

| 时间点 | 期望状态（仿真预测） | 实际记录 | 通过判据 |
|---|---|---|---|
| t=0s | 2 副本，QPS 跳升到 1200 | _______ | HPA 检测到超阈值 |
| t=15s | HPA 决策 SCALE_UP，desired=6 | _______ | desiredReplicas=6 |
| t=15-30s | 4 个新 Pod 创建中 | _______ | 4 个 Pod 处于 ContainerCreating |
| t=30s | 理想：6 就绪 / 真实：2 就绪+4 启动中 | _______ | 记录实际就绪数 |
| t=45s | 真实场景：6 副本就绪 | _______ | 6 个 Pod Ready |
| t=60s | 稳态：每副本 QPS=200，P99≈42ms | _______ | 负载分摊完成 |

### 2.5 关键指标采集
```bash
# 采集 P99 延迟时序（用于绘制延迟下降曲线）
for i in $(seq 1 12); do
  echo "=== t=$((i*10))s ==="
  kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/production/pods/*/skill_match_latency_p99"
  kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/production/pods/*/skill_match_qps"
  kubectl get hpa skill-retrieval-hpa -n production --no-headers
  sleep 10
done
```

## 阶段 3：持续高负载 — 稳定性验证（5 分钟）

### 3.1 持续 1000 QPS 压测
```bash
# 持续 300s，验证 6 副本能稳定承载
hey -z 300s -c 400 -q 2.5 -m POST -H "Content-Type: application/json" \
  -d '{"query":"帮我解析PDF文件","top_k":5}' \
  http://$SVC_IP:8080/match
```

### 3.2 稳定性观察
```bash
# 观察 Pod 重启次数（应为 0）
kubectl get pods -n production -l app=skill-retrieval-service -o wide

# 观察 OOMKilled / CrashLoopBackOff
kubectl get pods -n production -l app=skill-retrieval-service \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].restartCount}{"\n"}{end}'

# 观察 P99 延迟稳定性（应持续 < 40ms）
watch -n 5 'kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/production/pods/*/skill_match_latency_p99"'
```

| 指标 | 通过判据 |
|---|---|
| Pod 重启次数 | = 0（无 OOM/Crash） |
| P99 延迟 | 持续 < 40ms |
| 错误率 | < 1% |
| HPA 副本数 | 稳定在 6（无频繁扩缩） |

## 阶段 4：缩容验证（10 分钟）

### 4.1 停止压测，触发缩容
```bash
# 停止 hey/k6 后，等待 10 分钟稳定窗口（scaleDown.stabilizationWindowSeconds=600）
echo "停止压测，等待 10 分钟稳定窗口..."

# 每 2 分钟观察一次缩容过程
for i in 1 2 3 4 5; do
  echo "=== 缩容观察 $((i*2)) 分钟 ==="
  kubectl get hpa skill-retrieval-hpa -n production
  kubectl get pods -n production -l app=skill-retrieval-service --no-headers | wc -l
  sleep 120
done
```

### 4.2 缩容验证矩阵

| 时间点 | 期望状态 | 通过判据 |
|---|---|---|
| t=0（停压测） | 6 副本 | - |
| t=2min | 6 副本（稳定窗口内） | 未立即缩容 |
| t=6min | 5 副本（每 120s 减 1） | selectPolicy=Min 保守缩容 |
| t=10min | 3-4 副本 | 持续缩容中 |
| t=14min | 2 副本（minReplicas） | 达到下限，稳定 |

## 阶段 5：结果收集与分析

### 5.1 收集 HPA 扩缩容事件
```bash
# 导出所有 ScalingReplicaSet 事件
kubectl get events -n production --sort-by='.lastTimestamp' \
  --field-selector reason=ScalingReplicaSet > /tmp/hpa_scale_events.log

# 导出 HPA 状态变化
kubectl describe hpa skill-retrieval-hpa -n production > /tmp/hpa_describe.log
```

### 5.2 从 Prometheus 查询历史指标
```bash
# 通过 port-forward 访问 Prometheus
kubectl port-forward -n monitoring svc/prometheus 9090:9090 &

# 在浏览器查询（http://localhost:9090/graph）:
# 1. 副本数变化
#    kube_deployment_status_replicas{deployment="skill-retrieval-service"}
# 2. P99 延迟时序
#    histogram_quantile(0.99, sum(rate(skill_match_latency_ms_bucket[5m])) by (le))
# 3. QPS 时序
#    sum(rate(skill_match_count_total[1m]))
# 4. CPU 利用率
#    avg(rate(container_cpu_usage_seconds_total{pod=~"skill-retrieval.*"}[5m])) * 100
```

### 5.3 压测报告模板

```markdown
## HPA 压测报告 — [日期]

### 扩容验证
| 指标 | 仿真预测 | 实际值 | 偏差 |
|---|---|---|---|
| HPA 决策延迟 | t=15s | _____ | _____ |
| Pod 创建完成 | t=15-30s | _____ | _____ |
| 6 副本就绪 | 理想 t=30s / 真实 t=45s | _____ | _____ |
| Pod 启动延迟 | 20s（假设） | _____ | _____ |

### 结论
- [ ] HPA 策略配置正确（desired=6 计算符合预期）
- [ ] 30s 内完成扩容决策（是/否）
- [ ] 6 副本就绪时间：_____s
- [ ] Pod 启动延迟是主要瓶颈（是/否）

### 优化建议
- 若 Pod 启动延迟 > 30s：考虑预热镜像 / 调整 startupProbe
- 若 P99 持续 > 40ms：确认 candidate_limit=200 已启用
- 若频繁扩缩：调整 scaleDown.stabilizationWindowSeconds
```

## 附录：k6 压测脚本参考

### k6_skill_match.js（基线测试）
```javascript
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 20,
  duration: '60s',
};

export default function () {
  const res = http.post(__ENV.ENDPOINT, JSON.stringify({
    query: '帮我解析PDF文件',
    top_k: 5,
  }), { headers: { 'Content-Type': 'application/json' } });

  check(res, {
    'status 200': (r) => r.status === 200,
    'latency < 100ms': (r) => r.timings.duration < 100,
  });
}
```

### k6_burst_1200qps.js（突发流量测试）
```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  stages: [
    { duration: '0s', target: 0 },     // t=0: 瞬时跳变到 1200 QPS
    { duration: '120s', target: 600 }, // 持续 120s（600 vus × 2 qps = 1200 QPS）
    { duration: '0s', target: 0 },     // 瞬时停止
  ],
};

export default function () {
  const res = http.post(__ENV.ENDPOINT, JSON.stringify({
    query: '帮我解析PDF文件并提取表格数据',
    top_k: 5,
  }), { headers: { 'Content-Type': 'application/json' } });

  check(res, {
    'status 200': (r) => r.status === 200,
  });
}
```

## 安全注意事项

1. **命名空间隔离**：所有操作在 `production` 命名空间，避免影响其他服务
2. **流量上限**：maxReplicas=10 已限制最大 10 副本，避免资源耗尽
3. **回滚预案**：若 HPA 异常扩容，立即执行：
   ```bash
   # 临时暂停 HPA
   kubectl patch hpa skill-retrieval-hpa -n production -p '{"spec":{"scaleTargetRef":{}}}'

   # 手动固定副本数
   kubectl scale deployment skill-retrieval-service -n production --replicas=3
   ```
4. **资源配额**：确认集群有足够资源支撑 10 副本（10 × 2CPU = 20 核，10 × 2Gi = 20Gi）
