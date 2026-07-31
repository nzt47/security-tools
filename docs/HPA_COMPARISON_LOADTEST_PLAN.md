# HPA 调整前后对比压测方案

> 生成时间: 2026-07-31
> 目标: 验证 HPA 参数调整（minReplicas 2→3, CPU 70%→50%）对突发流量扩容响应的改善

## 1. 调整背景

基于 `simulate_hpa_burst_scale_up.py` 仿真结论：旧方案（minReplicas=2）受 Pod 启动延迟物理限制，30s 内无法完成 2→6 扩容。优化建议：预热副本（minReplicas=3）+ 提前触发（CPU 70%→50%）。

## 2. A/B 组配置差异

| 参数 | A 组（调整前） | B 组（调整后） | 差异说明 |
|---|---|---|---|
| `minReplicas` | 2 | **3** | 预热 1 副本，减少冷启动 |
| CPU `averageUtilization` | 70% | **50%** | 提前 ~10-15s 触发扩容 |
| `maxReplicas` | 10 | 10 | 不变 |
| scaleUp Pods +4/30s | 是 | 是 | 已验证正确，保持 |
| scaleDown 600s 稳定窗口 | 是 | 是 | 保持 |
| P99 阈值 | 40ms | 40ms | 业务指标不变 |
| QPS 阈值 | 200/副本 | 200/副本 | 业务指标不变 |

## 3. 测试设计

### 控制变量
- **相同流量**: k6 burst 场景（5s→10 VU, 10s→40 VU, 30s→40 VU, 10s→20 VU, 5s→0）
- **相同查询集**: 8 个测试查询（baseline TEST_QUERIES）
- **相同镜像/资源配置**: 不变
- **唯一变量**: HPA 配置（minReplicas + CPU 阈值）

### 测试流程
1. 部署 A 组配置 → 等待稳态 → 执行 burst 压测 → 记录指标
2. 回滚 → 部署 B 组配置 → 等待稳态 → 执行 burst 压测 → 记录指标
3. 对比两组指标

## 4. 执行步骤

### 4.1 准备 HPA 配置文件

```bash
# 当前 hpa.yaml 已是 B 组配置（minReplicas=3, CPU=50%）
# 生成 A 组配置（回退到旧值）
cp deploy/k8s/hpa.yaml deploy/k8s/hpa.yaml.bak-groupB
sed 's/minReplicas: 3/minReplicas: 2/; s/averageUtilization: 50/averageUtilization: 70/' \
  deploy/k8s/hpa.yaml.bak-groupB > deploy/k8s/hpa.yaml.groupA
```

### 4.2 A 组测试（调整前）

```bash
# 1. 部署 A 组 HPA 配置
cp deploy/k8s/hpa.yaml.groupA deploy/k8s/hpa.yaml
kubectl apply -f deploy/k8s/hpa.yaml
kubectl rollout restart deployment/skill-retrieval-service -n production

# 2. 等待稳态（副本数稳定在 minReplicas）
kubectl wait --for=condition=Available deployment/skill-retrieval-service -n production --timeout=360s
sleep 60  # 等 HPA 稳定 + Prometheus 采集

# 3. 执行 burst 压测（A 组）
k6 run \
  --out experimental-prometheus-rw=http://prometheus-server.monitoring.svc.cluster.local:9090/api/v1/write \
  -e ENDPOINT=http://skill-retrieval-service.production.svc.cluster.local:8080/match \
  -e NAMESPACE=production \
  -e SCENARIO=burst \
  --tag test_group=A \
  scripts/k6/k8s_loadtest_skill_match.js

# 4. 记录 A 组报告
cp k8s_burst_report.json k8s_burst_report_groupA.json

# 5. 等 HPA 缩容回稳态（10 分钟稳定窗口）
echo "等待 10 分钟 HPA 缩容..."
sleep 600
```

### 4.3 B 组测试（调整后）

```bash
# 1. 部署 B 组 HPA 配置（恢复当前版本）
cp deploy/k8s/hpa.yaml.bak-groupB deploy/k8s/hpa.yaml
kubectl apply -f deploy/k8s/hpa.yaml
kubectl rollout restart deployment/skill-retrieval-service -n production

# 2. 等待稳态
kubectl wait --for=condition=Available deployment/skill-retrieval-service -n production --timeout=360s
sleep 60

# 3. 执行 burst 压测（B 组）
k6 run \
  --out experimental-prometheus-rw=http://prometheus-server.monitoring.svc.cluster.local:9090/api/v1/write \
  -e ENDPOINT=http://skill-retrieval-service.production.svc.cluster.local:8080/match \
  -e NAMESPACE=production \
  -e SCENARIO=burst \
  --tag test_group=B \
  scripts/k6/k8s_loadtest_skill_match.js

# 4. 记录 B 组报告
cp k8s_burst_report.json k8s_burst_report_groupB.json

# 5. 清理临时文件
rm deploy/k8s/hpa.yaml.groupA deploy/k8s/hpa.yaml.bak-groupB
```

## 5. 预期指标变化

### 5.1 扩容响应速度（核心指标）

| 指标 | A 组预期 | B 组预期 | 改善幅度 |
|---|---|---|---|
| 突发流量开始到 HPA 决策 | ~5-10s | ~3-7s | 提前 ~2-3s（CPU 50% 更早触发） |
| HPA 决策到 Pod 创建 | ~2s | ~2s | 不变（K8s 调度） |
| Pod 创建到 Ready | ~20-30s | ~20-30s | 不变（模型加载） |
| **达到 6 副本总耗时** | ~30-45s | **~25-35s** | **提前 ~5-10s** |
| 最终副本数 | 6（2+4） | **7**（3+4） | +1 副本（预热） |

### 5.2 延迟指标

| 指标 | A 组预期 | B 组预期 | 改善 |
|---|---|---|---|
| P99 峰值（扩容期） | 80-120ms | **60-90ms** | -20~30ms（3 副本分摊） |
| P95 峰值 | 50-70ms | **40-55ms** | -10~15ms |
| P99 稳态（扩容后） | 30-40ms | **25-35ms** | -5ms |
| HPA 触发率（超 40ms） | 25-35% | **15-25%** | -10% |

### 5.3 资源指标

| 指标 | A 组预期 | B 组预期 | 说明 |
|---|---|---|---|
| CPU 峰值使用率 | 85-95%（2 副本时） | **70-80%**（3 副本分摊） | -15% |
| CPU 触发扩容时刻 | ~70% 时 | ~50% 时 | 更早 |
| 稳态副本数 | 2 | **3** | 成本 +50% |
| 稳态 CPU 使用率 | 40-50% | **30-40%** | 更低（余量更大） |

### 5.4 错误率

| 指标 | A 组预期 | B 组预期 | 改善 |
|---|---|---|---|
| 错误率峰值（扩容期） | 2-5% | **1-3%** | -1~2% |
| 错误率稳态 | <0.1% | <0.1% | 持平 |

## 6. Grafana 观察点

压测期间在 `skill-hpa-monitor` 面板观察：

1. **HPA 副本数面板**: 对比 A/B 组从稳态到 6/7 副本的斜率（B 组应更陡）
2. **P99 延迟面板**: 对比扩容期峰值（B 组红线 40ms 以上区域应更小）
3. **CPU 使用率面板**: 对比触发扩容的 CPU 水平（A 组 ~70%, B 组 ~50%）
4. **HPA 扩缩容决策事件**: 对比状态切换时间点

## 7. 判定标准

B 组相比 A 组需满足以下全部条件才算优化生效：

- [ ] 达到 6 副本耗时减少 ≥5s
- [ ] P99 峰值降低 ≥15ms
- [ ] HPA 触发率（超 40ms 占比）降低 ≥8%
- [ ] 错误率峰值降低 ≥1%
- [ ] 稳态无异常（缩容后回到 minReplicas）

## 8. 风险与回滚

- **成本风险**: B 组稳态 3 副本（+50%），如成本敏感可回退 minReplicas=2 + CPU=60%（折中）
- **测试隔离**: 每组测试后等 10 分钟 HPA 缩容，避免上一组影响下一组
- **配置回滚**: 测试完成后恢复 `hpa.yaml.bak-groupB` 为正式 `hpa.yaml`
