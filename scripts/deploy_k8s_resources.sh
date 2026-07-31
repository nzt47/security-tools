#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
#  一键部署 K8s 集群资源 — 5000 技能量级技能检索服务
#
#  部署资源:
#    1. ConfigMap + Deployment + Service + PVC + PDB  (deployment.yaml)
#    2. HPA                                          (hpa.yaml)
#    3. Prometheus Adapter rules ConfigMap            (prometheus-adapter-config.yaml)
#
#  【不易】幂等可重复执行（kubectl apply），失败不中断并给修复建议
#  【变易】参数化: namespace / IMAGE / STORAGE_CLASS / --rollback
#  【简易】分步骤输出 ✓/✗/⚠，含就绪等待与验证
#
#  用法:
#    bash scripts/deploy_k8s_resources.sh                         # 默认部署到 production
#    bash scripts/deploy_k8s_resources.sh my-namespace            # 指定 namespace
#    IMAGE=my-registry/skill-svc:v2 bash scripts/deploy_k8s_resources.sh  # 替换镜像
#    bash scripts/deploy_k8s_resources.sh production --rollback   # 回滚（删除资源）
#
#  前置条件:
#    - kubectl 已配置且集群可达
#    - 集群有可用的 StorageClass（默认 fast-ssd，可通过 STORAGE_CLASS 覆盖）
#    - Prometheus 已部署（Adapter 依赖）
# ════════════════════════════════════════════════════════════════════

set -o pipefail

# ── 参数解析 ──
NAMESPACE="${1:-production}"
ACTION="deploy"
if [[ "$2" == "--rollback" ]]; then
  ACTION="rollback"
fi

# 可覆盖配置
IMAGE="${IMAGE:-}"
STORAGE_CLASS="${STORAGE_CLASS:-fast-ssd}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$PROJECT_ROOT/deploy/k8s"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0; FAIL=0; WARN=0
ok()   { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; WARN=$((WARN+1)); }
step() { echo -e "\n${BLUE}── $1 ──${NC}"; }

# ════════════════════════════════════════════════════════════════════
#  回滚模式：按反向顺序删除资源
# ════════════════════════════════════════════════════════════════════
if [[ "$ACTION" == "rollback" ]]; then
  echo -e "${RED}════════════════════════════════════════════════════════════════${NC}"
  echo -e "${RED}  回滚模式 — 删除 namespace=$NAMESPACE 的资源${NC}"
  echo -e "${RED}════════════════════════════════════════════════════════════════${NC}"

  step "1. 删除 HPA"
  kubectl delete hpa skill-retrieval-hpa -n "$NAMESPACE" --ignore-not-found && ok "HPA 已删除" || warn "HPA 删除跳过"

  step "2. 删除 Prometheus Adapter rules ConfigMap"
  kubectl delete configmap prometheus-adapter-skill-rules -n monitoring --ignore-not-found && ok "Adapter ConfigMap 已删除" || warn "ConfigMap 删除跳过"

  step "3. 删除 Deployment + Service + PDB + PVC + ConfigMap"
  kubectl delete -f "$DEPLOY_DIR/deployment.yaml" --ignore-not-found && ok "deployment.yaml 资源已删除" || warn "部分资源删除失败"

  step "4. 验证清理结果"
  REMAIN=$(kubectl get all -n "$NAMESPACE" -l app=skill-retrieval-service --no-headers 2>/dev/null | wc -l)
  if [[ "$REMAIN" -eq 0 ]]; then
    ok "namespace $NAMESPACE 下 skill-retrieval-service 资源已清空"
  else
    warn "仍有 $REMAIN 个残留资源，手动检查: kubectl get all -n $NAMESPACE"
  fi

  echo -e "\n${GREEN}回滚完成 (通过=$PASS 失败=$FAIL 警告=$WARN)${NC}"
  echo -e "${YELLOW}注: 未删除 namespace 本身（避免误删其他资源）${NC}"
  echo -e "${YELLOW}    未卸载 Prometheus Adapter（其他指标可能依赖）${NC}"
  exit 0
fi

# ════════════════════════════════════════════════════════════════════
#  部署模式
# ════════════════════════════════════════════════════════════════════
echo "════════════════════════════════════════════════════════════════"
echo "  一键部署 K8s 资源 — namespace=$NAMESPACE"
echo "════════════════════════════════════════════════════════════════"
echo "  IMAGE:        ${IMAGE:-（未覆盖，用 deployment.yaml 默认）}"
echo "  STORAGE_CLASS: $STORAGE_CLASS"
echo ""

# ── 1. 预检 ──
step "1. 预检"
if kubectl cluster-info >/dev/null 2>&1; then
  ok "Kubernetes 集群可达"
else
  fail "Kubernetes 集群不可达 — 检查 kubeconfig / 启动 Docker Desktop"
  exit 1
fi

# ── 2. 创建 namespace ──
step "2. 创建 namespace"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1 && ok "namespace $NAMESPACE 就绪" || fail "namespace 创建失败"
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1 && ok "namespace monitoring 就绪" || warn "monitoring namespace 创建失败（可能已存在）"

# ── 3. StorageClass 检查 ──
step "3. StorageClass 检查"
if kubectl get storageclass "$STORAGE_CLASS" >/dev/null 2>&1; then
  ok "StorageClass $STORAGE_CLASS 存在"
else
  warn "StorageClass '$STORAGE_CLASS' 不存在 — PVC 将处于 Pending"
  DEFAULT_SC=$(kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' 2>/dev/null)
  if [[ -n "$DEFAULT_SC" ]]; then
    echo "       默认 StorageClass: $DEFAULT_SC"
    echo "       修复: STORAGE_CLASS=$DEFAULT_SC bash $0 $NAMESPACE"
  else
    echo "       修复: 创建 StorageClass 或用 STORAGE_CLASS=<name> 覆盖"
  fi
fi

# ── 4. 部署 deployment.yaml（ConfigMap/Deployment/Service/PVC/PDB）──
step "4. 部署 deployment.yaml"
if [[ -f "$DEPLOY_DIR/deployment.yaml" ]]; then
  if kubectl apply -f "$DEPLOY_DIR/deployment.yaml" >/dev/null 2>&1; then
    ok "deployment.yaml 应用成功（ConfigMap/Deployment/Service/PVC/PDB）"
  else
    fail "deployment.yaml 应用失败 — 检查: kubectl apply -f $DEPLOY_DIR/deployment.yaml"
    exit 1
  fi
else
  fail "文件不存在: $DEPLOY_DIR/deployment.yaml"
  exit 1
fi

# ── 5. 镜像占位符检查与替换 ──
step "5. 镜像检查"
CURRENT_IMAGE=$(kubectl get deployment skill-retrieval-service -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)
echo "  当前镜像: $CURRENT_IMAGE"
if [[ "$CURRENT_IMAGE" == *"example.com"* ]]; then
  warn "镜像是占位符 (registry.example.com) — Pod 将因 ImagePullBackOff 无法启动"
  if [[ -n "$IMAGE" ]]; then
    echo "       替换为: $IMAGE"
    if kubectl set image deployment/skill-retrieval-service "skill-service=$IMAGE" -n "$NAMESPACE" >/dev/null 2>&1; then
      ok "镜像已替换为 $IMAGE"
    else
      fail "镜像替换失败 — 手动执行: kubectl set image deployment/skill-retrieval-service skill-service=$IMAGE -n $NAMESPACE"
    fi
  else
    echo "       ${YELLOW}修复: IMAGE=<your-registry>/skill-svc:vX bash $0 $NAMESPACE${NC}"
    echo "       或:   kubectl set image deployment/skill-retrieval-service skill-service=<image> -n $NAMESPACE"
  fi
else
  ok "镜像非占位符"
fi

# ── 6. 等待 PVC Bound ──
step "6. 等待 PVC Bound"
PVC_BOUND=true
for pvc in skill-data-pvc model-cache-pvc; do
  if kubectl wait pvc "$pvc" -n "$NAMESPACE" --for=condition=Bound --timeout=30s >/dev/null 2>&1; then
    ok "PVC $pvc Bound"
  else
    warn "PVC $pvc 未 Bound（StorageClass 缺失或容量不足）— Pod 将无法启动"
    PVC_BOUND=false
  fi
done

# ── 7. 等待 Deployment 就绪 ──
step "7. 等待 Deployment 就绪"
if [[ "$PVC_BOUND" == "true" && "$CURRENT_IMAGE" != *"example.com"* ]] || [[ -n "$IMAGE" ]]; then
  echo "  等待 Pod Ready（模型加载最多 5 分钟，startupProbe failureThreshold=30）..."
  if kubectl wait deployment/skill-retrieval-service -n "$NAMESPACE" --for=condition=Available --timeout=360s >/dev/null 2>&1; then
    READY=$(kubectl get deployment skill-retrieval-service -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>/dev/null)
    ok "Deployment 就绪 ($READY)"
  else
    fail "Deployment 未就绪（360s 超时）— 检查: kubectl describe pod -n $NAMESPACE -l app=skill-retrieval-service"
    echo "       常见原因: 镜像拉取失败 / 模型加载超时 / PVC 未 Bound"
  fi
else
  warn "跳过 Deployment 就绪等待（PVC 未 Bound 或镜像为占位符）"
  echo "       修复上述问题后，Pod 会自动启动；或手动: kubectl rollout status deployment/skill-retrieval-service -n $NAMESPACE"
fi

# ── 8. 部署 HPA ──
step "8. 部署 HPA"
if kubectl apply -f "$DEPLOY_DIR/hpa.yaml" >/dev/null 2>&1; then
  ok "HPA skill-retrieval-hpa 应用成功"
  HPA_REPLICAS=$(kubectl get hpa skill-retrieval-hpa -n "$NAMESPACE" -o jsonpath='{.status.currentReplicas}' 2>/dev/null)
  if [[ -n "$HPA_REPLICAS" ]]; then
    echo "       当前副本数: $HPA_REPLICAS (min=2, max=10)"
  fi
else
  fail "HPA 应用失败 — 检查: kubectl apply -f $DEPLOY_DIR/hpa.yaml"
fi

# ── 9. 部署 Prometheus Adapter rules ──
step "9. 部署 Prometheus Adapter rules ConfigMap"
if kubectl apply -f "$DEPLOY_DIR/prometheus-adapter-config.yaml" >/dev/null 2>&1; then
  ok "ConfigMap prometheus-adapter-skill-rules 应用成功 (ns=monitoring)"
else
  fail "Adapter rules ConfigMap 应用失败"
fi

# ── 10. Prometheus Adapter 检查 ──
step "10. Prometheus Adapter 状态检查"
ADAPTER_STATUS=$(kubectl get apiservice v1beta1.custom.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null)
if [[ "$ADAPTER_STATUS" == "True" ]]; then
  ok "Prometheus Adapter Available=True（自定义指标就绪）"
  echo "       注: 新 rules 需重启 adapter Pod 才能加载"
  echo "       kubectl rollout restart deployment/prometheus-adapter -n monitoring"
else
  warn "Prometheus Adapter 不可用 — HPA 的 P99/QPS 自定义指标无法工作"
  echo ""
  echo "  ${YELLOW}部署 Prometheus Adapter（helm 方式）:${NC}"
  echo "  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts"
  echo "  helm repo update"
  echo "  helm install prometheus-adapter prometheus-community/prometheus-adapter \\"
  echo "    -n monitoring \\"
  echo "    --set prometheus.url=http://prometheus-server.monitoring.svc.cluster.local \\"
  echo "    --set prometheus.port=9090 \\"
  echo "    --set rules.default=false"
  echo ""
  echo "  ${YELLOW}部署后加载 skill rules:${NC}"
  echo "  # adapter 需挂载 ConfigMap prometheus-adapter-skill-rules"
  echo "  # 详见 deploy/k8s/prometheus-adapter-config.yaml 注释（helm values 示例）"
  echo "  kubectl rollout restart deployment/prometheus-adapter -n monitoring"
  echo "  kubectl wait --for=condition=Available apiservice/v1beta1.custom.metrics.k8s.io --timeout=120s"
fi

# ── 11. Service 可达性验证 ──
step "11. Service 可达性验证"
SVC_IP=$(kubectl get svc skill-retrieval-service -n "$NAMESPACE" -o jsonpath='{.spec.clusterIP}' 2>/dev/null)
if [[ -n "$SVC_IP" ]]; then
  ok "Service ClusterIP=$SVC_IP:8080"
  # 从集群内 Pod 测试健康检查
  HEALTH=$(kubectl run svc-test-"$RANDOM" -n "$NAMESPACE" --image=curlimages/curl --rm -i --restart=Never --quiet -- \
    curl -s -o /dev/null -w "%{http_code}" "http://skill-retrieval-service:8080/health" 2>/dev/null || echo "000")
  if [[ "$HEALTH" == "200" ]]; then
    ok "健康检查 /health 返回 200"
  else
    warn "健康检查返回 $HEALTH（Pod 可能未就绪）— 稍后重试: kubectl run curl-test --image=curlimages/curl --rm -it --restart=Never -- curl http://skill-retrieval-service:8080/health"
  fi
else
  fail "Service skill-retrieval-service 不存在"
fi

# ── 12. 产生测试指标数据 ──
step "12. 产生测试指标数据（供 Prometheus 采集）"
if [[ -n "$SVC_IP" ]]; then
  kubectl run metric-seed-"$RANDOM" -n "$NAMESPACE" --image=curlimages/curl --rm -i --restart=Never --quiet -- \
    curl -s -X POST "http://skill-retrieval-service:8080/match" \
    -H "Content-Type: application/json" \
    -d '{"query":"PDF解析","top_k":5}' >/dev/null 2>&1 && ok "已发起测试请求，指标将上报 Prometheus（等待 ~30s 采集）" || warn "测试请求失败（Pod 可能未就绪）"
fi

# ── 13. 汇总与下一步 ──
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  部署结果汇总"
echo "════════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}通过: $PASS${NC}  ${RED}失败: $FAIL${NC}  ${YELLOW}警告: $WARN${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo -e "  ${RED}✗ 部署未完全成功，请修复上述失败项${NC}"
  echo ""
  echo "  排查命令:"
  echo "    kubectl get pods -n $NAMESPACE -l app=skill-retrieval-service"
  echo "    kubectl describe pod -n $NAMESPACE -l app=skill-retrieval-service"
  echo "    kubectl logs -n $NAMESPACE -l app=skill-retrieval-service --tail=50"
  exit 1
fi

echo -e "  ${GREEN}✓ 部署完成${NC}"
echo ""
echo "  ── 下一步: 压测 ──"
echo ""
echo "  1. 预检（确认所有依赖就绪）:"
echo "     bash scripts/k8s_preflight_check.sh $NAMESPACE"
echo ""
echo "  2. 等待 30s（Prometheus 采集 + Adapter 缓存指标）"
echo ""
echo "  3. 验证自定义指标可达:"
echo "     kubectl get --raw \"/apis/custom.metrics.k8s.io/v1beta1/namespaces/$NAMESPACE/pods/*/skill_match_latency_p99\""
echo "     kubectl get --raw \"/apis/custom.metrics.k8s.io/v1beta1/namespaces/$NAMESPACE/pods/*/skill_match_qps\""
echo ""
echo "  4. k6 基线压测（在能访问集群的机器上执行）:"
echo "     k6 run \\"
echo "       --out experimental-prometheus-rw=http://prometheus-server.monitoring.svc.cluster.local:9090/api/v1/write \\"
echo "       -e ENDPOINT=http://skill-retrieval-service.$NAMESPACE.svc.cluster.local:8080/match \\"
echo "       -e NAMESPACE=$NAMESPACE \\"
echo "       scripts/k6/k8s_loadtest_skill_match.js"
echo ""
echo "  5. 突发流量场景（验证 HPA 2→6 扩容）:"
echo "     k6 run --out experimental-prometheus-rw=http://prometheus-server:9090/api/v1/write \\"
echo "       -e ENDPOINT=http://skill-retrieval-service.$NAMESPACE.svc.cluster.local:8080/match \\"
echo "       -e SCENARIO=burst \\"
echo "       scripts/k6/k8s_loadtest_skill_match.js"
echo ""
echo "  回滚: bash $0 $NAMESPACE --rollback"
echo "════════════════════════════════════════════════════════════════"
