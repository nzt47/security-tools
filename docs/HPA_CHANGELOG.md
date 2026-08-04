# HPA 配置变更日志 (Change Log)

> **服务**: skill-retrieval-service (技能检索服务)
> **命名空间**: production
> **演进周期**: 2026-07 ~ 2026-08
> **当前版本**: v3.0 (2026-08-01，基于集群内直连压测验证)

---

## 版本概览

| 版本 | 日期 | CPU 阈值 | maxReplicas | scaleUp | scaleDown | 验证方式 |
|------|------|---------|-------------|---------|-----------|---------|
| v1.0 | 2026-07-初 | 50% | 10 | Pods +4/30s | 600s | 未压测（经验值） |
| v2.0 | 2026-07-末 | 10% | 10 | Pods +4/30s | 60s | port-forward 压测 |
| **v3.0** | **2026-08-01** | **5%** | **15** | **Pods +6/30s** | **300s** | **集群内直连压测** |

---

## v3.0 (2026-08-01) — 当前生产配置

### 变更摘要

基于集群内直连压测验证，全面优化 HPA 扩缩容策略，目标：**3→15 副本扩容 ≤ 60s，P99 < 40ms**。

### 详细变更

#### 1. CPU 阈值: 10% → 5%

| 项目 | 旧值 (v2.0) | 新值 (v3.0) | 变更依据 |
|------|------------|------------|---------|
| CPU averageUtilization | 10% | 5% | aiohttp I/O 密集，空闲 CPU 仅 1%，10% 阈值在 I/O 场景不触发扩容 |
| 触发时机 | ~150 QPS | ~50 QPS | 更早扩容，避免突发流量导致 P99 超标 |

**根因分析**:
- 服务基于 aiohttp 异步框架，CPU 开销主要在 I/O 等待而非计算
- 实测空闲状态 CPU 仅 1%（requests.cpu=100m → 实际 1m）
- 10% 阈值对应 10m (0.01 核)，在 230 QPS 时 CPU 才到 50%
- 5% 阈值对应 5m (0.005 核)，轻微负载（~50 QPS）即触发

#### 2. maxReplicas: 10 → 15

| 项目 | 旧值 (v2.0) | 新值 (v3.0) | 变更依据 |
|------|------------|------------|---------|
| maxReplicas | 10 | 15 | 单副本容量 ~77 QPS，15 副本 = ~1,155 QPS |
| QPS 上限 | ~770 | ~1,155 | +50% 容量提升 |
| 资源占用 | 1.0 CPU / 1.28GB | 1.5 CPU / 1.9GB | 单节点可承载 |

**变更依据**: 集群内直连压测实测单副本可支撑 77 QPS（P99=32ms），15 副本理论上限 1,155 QPS，满足业务增长需求。

#### 3. scaleUp 策略: Pods +4/30s → Pods +6/30s

| 项目 | 旧值 (v2.0) | 新值 (v3.0) | 变更依据 |
|------|------------|------------|---------|
| Pods policy | +4/30s | +6/30s | 3→9→15 两步完成（51.4s 实测） |
| 理论扩容路径 | 3→7→10 (60s) | 3→9→15 (60s) | 更快达到 maxReplicas |
| 实测扩容时间 | ~90s | **51.4s** | -43% 扩容时效 |

**扩容时间线**（v3.0 实测）:
```
T+0s:   流量突增开始（100 VU 紧循环）
T+17s:  HPA 评估完成，触发扩容（3→9）
T+23s:  第一批 Pod 启动（+6 副本）
T+51.4s: 第二批扩容完成（9→15），达到 maxReplicas
```

#### 4. scaleDown 策略: 60s → 300s

| 项目 | 旧值 (v2.0，压测用) | 新值 (v3.0，生产) | 变更依据 |
|------|-------------------|------------------|---------|
| stabilizationWindowSeconds | 60s | 300s | 生产环境避免流量波动导致频繁扩缩 |
| Pods policy | -2/60s | -1/60s | 保守缩容，避免雪崩 |
| Percent policy | -15%/60s | -10%/60s | 保守缩容 |
| selectPolicy | Min | Min | 取较小值，保守缩容 |

**变更依据**: 压测环境需快速缩容观察，生产环境必须保守（5 分钟稳定窗口）避免流量抖动导致扩缩循环。

#### 5. 新增 Memory 辅助指标

| 项目 | v2.0 | v3.0 | 变更依据 |
|------|------|------|---------|
| Memory 指标 | 无 | averageUtilization: 70% | aiohttp 连接池高并发内存增长监控 |

#### 6. 新增 PrometheusRule 告警规则

v3.0 新增 5 条告警规则（v2.0 无告警）:

| 告警名 | 触发条件 | 持续时间 | 严重级别 |
|--------|---------|---------|---------|
| SkillRetrievalP99SLOBreach | P99 > 40ms | 2m | critical |
| SkillRetrievalP99Warning | P99 > 35ms | 5m | warning |
| SkillRetrievalHPAAtMaxReplicas | 副本数 == 15 | 5m | warning |
| SkillRetrievalHPAScalingStuck | CPU>5% 且副本数无变化 | 3m | critical |
| SkillRetrievalHPAMetricsUnavailable | ScalingActive=false | 2m | critical |
| SkillRetrievalHPABelowMinReplicas | 副本数 < 3 | 1m | critical |

---

## v2.0 (2026-07-末) — 已废弃

### 变更摘要

首次引入 port-forward 压测验证，将 CPU 阈值从 50% 降至 10%。

### 详细变更

- CPU 阈值: 50% → 10%（经验调整，未充分验证）
- maxReplicas: 保持 10
- scaleUp: 保持 Pods +4/30s
- scaleDown: 600s → 60s（压测观察用）

### 废弃原因

1. **port-forward 代理开销**: 实测引入 ~10.8ms 延迟（P99 43ms vs 真实 32ms），压测数据失真
2. **CPU 10% 阈值仍偏高**: aiohttp I/O 密集场景下 10% 阈值在 230 QPS 时才触发，扩容滞后
3. **maxReplicas=10 容量不足**: 业务增长需支撑 1,000+ QPS

---

## v1.0 (2026-07-初) — 初始版本

### 变更摘要

基于经验值初始配置，未压测验证。

### 配置详情

- CPU 阈值: 50%（K8s 默认推荐值）
- maxReplicas: 10
- scaleUp: Pods +4/30s, stabilizationWindow=0
- scaleDown: stabilizationWindow=600s
- 无 Memory 指标
- 无 PrometheusRule 告警

### 问题

1. **CPU 50% 阈值过高**: aiohttp 服务 CPU 永远达不到 50%，HPA 形同虚设
2. **scaleDown 600s 过长**: 流量回落后 10 分钟才缩容，资源浪费
3. **无告警机制**: HPA 失效时无感知

---

## 演进决策点

### 决策 1: 为何从 port-forward 切换到集群内直连？

**背景**: v2.0 使用 port-forward 压测，P99 始终 43ms（超过 40ms SLO）。

**根因**: `kubectl port-forward` 基于 kube-proxy HTTP 隧道，引入 ~10.8ms 代理开销。

**决策**: 切换为集群内 Pod 直连 Service ClusterIP，消除代理层。

**验证结果**:
- P99: 43ms → 32ms（-25%）
- QPS 上限: 300 → 2665+（+788%）
- 资源成本: 降低 89%

### 决策 2: 为何 CPU 阈值从 10% 降至 5%？

**背景**: v2.0 的 10% 阈值在压测中未及时触发扩容。

**根因**: aiohttp 异步框架 I/O 密集，CPU 使用率低（230 QPS 时仅 50%）。

**决策**: 降至 5%，对应 5m (0.005 核)，轻微负载即触发。

**验证结果**: 5% 阈值下，100 VU 流量突增后 17s 内触发扩容，51.4s 完成 3→15。

### 决策 3: 为何 maxReplicas 从 10 提升至 15？

**背景**: v2.0 的 maxReplicas=10 上限 ~770 QPS，无法满足业务增长。

**决策**: 提升至 15，上限 ~1,155 QPS。

**资源评估**: 15×100m=1.5 CPU, 15×128Mi=1.9GB，单节点可承载。

### 决策 4: 为何 scaleDown 从 60s 改为 300s？

**背景**: v2.0 的 60s 稳定窗口为压测观察用，不适合生产。

**决策**: 生产环境使用 300s（5 分钟）稳定窗口，避免流量波动导致频繁扩缩。

---

## 回滚方案

若 v3.0 出现问题，可回滚到 v2.0:

```powershell
# 回滚到 v2.0 配置
kubectl apply -f deploy/k8s/mock-hpa.yaml  # v2.0 压测配置（CPU 10%, max=10）

# 或手动 patch
kubectl patch hpa skill-retrieval-hpa -n production --type=merge -p '{
  "spec": {
    "maxReplicas": 10,
    "metrics": [{"type":"Resource","resource":{"name":"cpu","target":{"type":"Utilization","averageUtilization":10}}}]
  }
}'
```

**回滚触发条件**:
- P99 持续 > 50ms（超过 v2.0 基线）
- HPA 频繁扩缩循环（5 分钟内扩缩 ≥ 3 次）
- 副本数无法稳定在期望值

---

## 验证记录

| 日期 | 版本 | 验证方式 | 结果 | 验证人 |
|------|------|---------|------|--------|
| 2026-08-01 | v3.0 | 集群内直连压测（100 VU, 90s） | ✅ 3→15 副本 51.4s，P99=32ms | — |
| 2026-08-01 | v3.0 | 基线压测（20 VU, 60s） | ✅ P99=32.82ms，QPS=92.2 | — |
| 2026-08-01 | v3.0 | 突发压测（40 VU, 60s） | ✅ P99=32.29ms，QPS=183.8 | — |
| 2026-08-01 | v3.0 | 压力压测（50 VU, 120s） | ✅ P99=32.31ms，QPS=230 | — |

---

## 相关文档

- [生产 HPA 配置](../deploy/k8s/hpa-production.yaml)
- [迁移指南: port-forward → 集群内直连](MIGRATION_PORT_FORWARD_TO_IN_CLUSTER.md)
- [HPA 扩容巡检脚本](../scripts/hpa_scale_patrol.py)
- [port-forward 开销对比报告](PORT_FORWARD_OVERHEAD_COMPARISON.md)
- [HPA 对比压测计划](HPA_COMPARISON_LOADTEST_PLAN.md)

---

## 变更审批

| 角色 | 姓名 | 日期 | 状态 |
|------|------|------|------|
| 变更发起人 | — | 2026-08-01 | 待评审 |
| 架构评审 | — | — | 待评审 |
| 运维评审 | — | — | 待评审 |
| 安全评审 | — | — | 待评审 |
