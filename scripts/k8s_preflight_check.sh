#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
#  K8s HPA 压测预检脚本 — 阶段 0 部署确认与基线检查
#
#  对应 K8S_HPA_LOADTEST_PLAN.md 阶段 0
#  用法: bash scripts/k8s_preflight_check.sh [namespace]
#
#  【不易】覆盖所有前置依赖: 集群/Metrics Server/Adapter/kube-state-metrics/HPA
#  【变易】参数化 namespace（默认 production）
#  【简易】每步输出 ✓/✗ + 修复建议，失败不中断（收集所有问题）
# ════════════════════════════════════════════════════════════════════

set -o pipefail

NAMESPACE="${1:-production}"
DEPLOYMENT="skill-retrieval-service"
HPA="skill-retrieval-hpa"
PASS=0
FAIL=0
WARN=0

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'  # No Color

ok()   { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; WARN=$((WARN+1)); }

echo "════════════════════════════════════════════════════════════════"
echo "  K8s HPA 压测预检 — 命名空间: $NAMESPACE"
echo "════════════════════════════════════════════════════════════════"

# ────────────────────────────────────────────────────────────────────
#  1. 集群连通性
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 1. 集群连通性 ──"
if kubectl cluster-info >/dev/null 2>&1; then
  ok "Kubernetes 集群可达"
else
  fail "Kubernetes 集群不可达 — 检查 kubeconfig 配置"
fi

# ────────────────────────────────────────────────────────────────────
#  2. 命名空间存在
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 2. 命名空间检查 ──"
if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  ok "命名空间 $NAMESPACE 存在"
else
  fail "命名空间 $NAMESPACE 不存在 — 执行: kubectl create namespace $NAMESPACE"
fi

# ────────────────────────────────────────────────────────────────────
#  3. Deployment 状态
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 3. Deployment 状态 ──"
DEP_STATUS=$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null)
if [ -n "$DEP_STATUS" ]; then
  READY=$(echo "$DEP_STATUS" | cut -d'/' -f1)
  TOTAL=$(echo "$DEP_STATUS" | cut -d'/' -f2)
  if [ "$READY" = "$TOTAL" ] && [ "$READY" != "0" ]; then
    ok "Deployment $DEPLOYMENT 就绪 ($DEP_STATUS)"
  else
    fail "Deployment $DEPLOYMENT 未就绪 ($DEP_STATUS) — 检查: kubectl describe deployment $DEPLOYMENT -n $NAMESPACE"
  fi
else
  fail "Deployment $DEPLOYMENT 不存在 — 检查: kubectl get deployment -n $NAMESPACE"
fi

# ────────────────────────────────────────────────────────────────────
#  4. Pod 状态
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 4. Pod 状态 ──"
POD_COUNT=$(kubectl get pods -n "$NAMESPACE" -l app="$DEPLOYMENT" --no-headers 2>/dev/null | wc -l)
if [ "$POD_COUNT" -gt 0 ]; then
  RUNNING=$(kubectl get pods -n "$NAMESPACE" -l app="$DEPLOYMENT" --no-headers 2>/dev/null | grep -c "Running")
  if [ "$RUNNING" = "$POD_COUNT" ]; then
    ok "所有 Pod Running ($RUNNING/$POD_COUNT)"
  else
    fail "部分 Pod 非 Running ($RUNNING/$POD_COUNT) — 检查: kubectl get pods -n $NAMESPACE -l app=$DEPLOYMENT"
  fi
else
  fail "无匹配 Pod — 检查 label: kubectl get pods -n $NAMESPACE --show-labels"
fi

# ────────────────────────────────────────────────────────────────────
#  5. HPA 配置
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 5. HPA 配置 ──"
if kubectl get hpa "$HPA" -n "$NAMESPACE" >/dev/null 2>&1; then
  HPA_TARGETS=$(kubectl get hpa "$HPA" -n "$NAMESPACE" -o jsonpath='{.status.currentMetrics[*].resource.current.averageUtilization}' 2>/dev/null)
  HPA_REPLICAS=$(kubectl get hpa "$HPA" -n "$NAMESPACE" -o jsonpath='{.status.currentReplicas}' 2>/dev/null)
  if [ -n "$HPA_REPLICAS" ]; then
    ok "HPA $HPA 存在, 当前副本=$HPA_REPLICAS"
  else
    warn "HPA $HPA 存在但状态未就绪 — 检查: kubectl describe hpa $HPA -n $NAMESPACE"
  fi
else
  fail "HPA $HPA 不存在 — 部署: kubectl apply -f deploy/k8s/hpa.yaml"
fi

# ────────────────────────────────────────────────────────────────────
#  6. Metrics Server（CPU/内存指标）
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 6. Metrics Server ──"
if kubectl top pods -n "$NAMESPACE" >/dev/null 2>&1; then
  ok "Metrics Server 可用 (kubectl top 正常)"
else
  fail "Metrics Server 不可用 — HPA CPU 指标无法工作"
  echo "       修复: kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml"
fi

# ────────────────────────────────────────────────────────────────────
#  7. Prometheus Adapter（自定义指标）
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 7. Prometheus Adapter（自定义指标） ──"
ADAPTER_STATUS=$(kubectl get apiservice v1beta1.custom.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null)
if [ "$ADAPTER_STATUS" = "True" ]; then
  ok "Prometheus Adapter Available=True"
else
  fail "Prometheus Adapter 不可用 — HPA 的 P99/QPS 自定义指标无法工作"
  echo "       修复: 部署 prometheus-adapter + 配置 rules (deploy/k8s/prometheus-adapter-config.yaml)"
fi

# 验证 skill_match_latency_p99 自定义指标可达
echo ""
echo "── 7.1 自定义指标可达性验证 ──"
P99_RAW=$(kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/$NAMESPACE/pods/*/skill_match_latency_p99" 2>/dev/null)
if [ -n "$P99_RAW" ] && echo "$P99_RAW" | grep -q "value"; then
  ok "skill_match_latency_p99 指标可达"
else
  warn "skill_match_latency_p99 指标不可达 — 可能未配置 Adapter rules 或无数据"
  echo "       提示: 需先发起请求产生指标数据，再查询"
fi

QPS_RAW=$(kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/$NAMESPACE/pods/*/skill_match_qps" 2>/dev/null)
if [ -n "$QPS_RAW" ] && echo "$QPS_RAW" | grep -q "value"; then
  ok "skill_match_qps 指标可达"
else
  warn "skill_match_qps 指标不可达 — 可能未配置 Adapter rules 或无数据"
fi

# ────────────────────────────────────────────────────────────────────
#  8. kube-state-metrics（副本数指标）
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 8. kube-state-metrics ──"
KSM_SVC=$(kubectl get svc -n kube-system --no-headers 2>/dev/null | grep -c "kube-state-metrics")
if [ "$KSM_SVC" -gt 0 ]; then
  ok "kube-state-metrics 服务存在"
else
  warn "kube-state-metrics 未找到 — dashboard 副本数面板无数据"
  echo "       修复: helm install kube-state-metrics bitnami/kube-state-metrics -n kube-system"
fi

# ────────────────────────────────────────────────────────────────────
#  9. Service 可达性
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 9. Service 可达性 ──"
SVC_IP=$(kubectl get svc "$DEPLOYMENT" -n "$NAMESPACE" -o jsonpath='{.spec.clusterIP}' 2>/dev/null)
if [ -n "$SVC_IP" ]; then
  ok "Service $DEPLOYMENT ClusterIP=$SVC_IP"

  # 从集群内 Pod 测试健康检查
  HEALTH_RESULT=$(kubectl run curl-test -n "$NAMESPACE" --image=curlimages/curl --rm -it --restart=Never --quiet -- \
    curl -s -o /dev/null -w "%{http_code}" "http://$SVC_IP:8080/health" 2>/dev/null || echo "000")

  if [ "$HEALTH_RESULT" = "200" ]; then
    ok "健康检查 /health 返回 200"
  else
    fail "健康检查失败 (HTTP $HEALTH_RESULT) — 检查 Pod 日志: kubectl logs -n $NAMESPACE -l app=$DEPLOYMENT --tail=50"
  fi
else
  fail "Service $DEPLOYMENT 不存在 — 检查: kubectl get svc -n $NAMESPACE"
fi

# ────────────────────────────────────────────────────────────────────
#  10. 资源配额检查
# ────────────────────────────────────────────────────────────────────
echo ""
echo "── 10. 集群资源余量 ──"
NODE_CPU=$(kubectl top nodes --no-headers 2>/dev/null | awk '{sum+=$3} END {print int(sum)}')
NODE_MEM=$(kubectl top nodes --no-headers 2>/dev/null | awk '{sum+=$5} END {print int(sum)}')
if [ -n "$NODE_CPU" ] && [ "$NODE_CPU" != "0" ]; then
  ok "集群节点 CPU 使用率合计: ${NODE_CPU}%"
  if [ "$NODE_CPU" -gt 70 ]; then
    warn "CPU 使用率较高 — HPA 扩容到 10 副本可能资源不足"
  fi
else
  warn "无法获取节点 CPU 使用率 — kubectl top nodes 不可用"
fi

# ────────────────────────────────────────────────────────────────────
#  汇总
# ────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  预检结果汇总"
echo "════════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}通过: $PASS${NC}  ${RED}失败: $FAIL${NC}  ${YELLOW}警告: $WARN${NC}"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo -e "  ${RED}✗ 预检未通过，请修复上述失败项后再执行压测${NC}"
  echo "  压测命令: k6 run -e ENDPOINT=http://$SVC_IP:8080/match scripts/k6/baseline_skill_match.js"
  exit 1
elif [ "$WARN" -gt 0 ]; then
  echo -e "  ${YELLOW}⚠ 预检通过（含警告），建议关注上述警告项${NC}"
  echo "  压测命令: k6 run -e ENDPOINT=http://$SVC_IP:8080/match scripts/k6/baseline_skill_match.js"
  exit 0
else
  echo -e "  ${GREEN}✓ 预检全部通过，可以开始压测${NC}"
  echo "  压测命令: k6 run -e ENDPOINT=http://$SVC_IP:8080/match scripts/k6/baseline_skill_match.js"
  exit 0
fi
