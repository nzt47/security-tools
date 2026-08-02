#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
#  一键部署监控组件 — 告警规则 + CronJob + Mock 服务
#
#  部署资源（deploy/k8s/）:
#    1. grafana-alerting.yaml         4 条告警规则 + webhook 通知策略
#    2. log-injector-cronjob.yaml     日志注入 CronJob（每 5 分钟）+ 脚本 ConfigMap
#    3. mock-webhook-pod.yaml         Mock Alertmanager（告警接收验证器）
#    4. grafana.yaml                  （可选）Grafana 部署 + 数据源 + 看板
#
#  【不易】幂等可重复执行（kubectl apply），失败不中断并给修复建议
#  【变易】参数化: NAMESPACE / --skip-grafana / --verify-only
#  【简易】分步骤输出 ✓/✗/⚠，含就绪等待与端到端验证
#
#  用法:
#    bash scripts/deploy_monitoring_stack.sh                    # 部署全部监控组件
#    bash scripts/deploy_monitoring_stack.sh --skip-grafana     # 跳过 Grafana（假定已部署）
#    bash scripts/deploy_monitoring_stack.sh --verify-only       # 仅验证已部署组件
#    NAMESPACE=monitoring bash scripts/deploy_monitoring_stack.sh
#
#  前置条件:
#    - kubectl 已配置且集群可达（kind / 生产集群均可）
#    - Loki 数据源 uid 为 loki（见 deploy/k8s/grafana.yaml）
#    - 节点有本地镜像 docker.io/library/skill-retrieval:local（CronJob/Webhook 依赖）
# ════════════════════════════════════════════════════════════════════

set -o pipefail

# ── 参数解析 ──
NAMESPACE="${NAMESPACE:-monitoring}"
ACTION="deploy"
VERIFY_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --skip-grafana) SKIP_GRAFANA=true ;;
    --verify-only)  VERIFY_ONLY=true ;;
    --help|-h)
      grep '^#' "$0" | head -n 40
      exit 0
      ;;
    *) echo "未知参数: $arg （支持 --skip-grafana / --verify-only）"; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$PROJECT_ROOT/deploy/k8s"

# 颜色
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
PASS=0; FAIL=0; WARN=0
ok()   { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; WARN=$((WARN+1)); }
step() { echo -e "\n${BLUE}── $1 ──${NC}"; }

echo "════════════════════════════════════════════════════════════════"
echo "  监控组件一键部署 — namespace=$NAMESPACE"
echo "════════════════════════════════════════════════════════════════"

# ── 0. 预检 ──
step "0. 预检"
if kubectl cluster-info >/dev/null 2>&1; then
  ok "Kubernetes 集群可达"
else
  fail "Kubernetes 集群不可达 — 检查 kubeconfig / 集群状态"
  exit 1
fi

if [[ -d "$DEPLOY_DIR" ]]; then
  ok "deploy/k8s 目录存在"
else
  fail "deploy/k8s 目录不存在: $DEPLOY_DIR"
  exit 1
fi

if [[ "$VERIFY_ONLY" == "true" ]]; then
  step "── 验证模式（仅检查已部署组件状态）──"
  kubectl get cronjob log-injector -n "$NAMESPACE" >/dev/null 2>&1 && ok "CronJob log-injector 存在" || warn "CronJob log-injector 未部署"
  kubectl get cm grafana-alert-rules -n "$NAMESPACE" >/dev/null 2>&1 && ok "ConfigMap grafana-alert-rules 存在" || warn "ConfigMap grafana-alert-rules 未部署"
  kubectl get pod mock-alert-webhook -n "$NAMESPACE" >/dev/null 2>&1 && ok "Pod mock-alert-webhook 存在" || warn "Pod mock-alert-webhook 未部署"
  exit 0
fi

# ── 1. 创建 namespace ──
step "1. 创建 namespace"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1 && ok "namespace $NAMESPACE 就绪" || fail "namespace 创建失败"

# ── 2. 部署告警规则 + 通知策略 ──
step "2. 部署告警规则（grafana-alerting.yaml）"
if [[ -f "$DEPLOY_DIR/grafana-alerting.yaml" ]]; then
  if kubectl apply -f "$DEPLOY_DIR/grafana-alerting.yaml" >/dev/null 2>&1; then
    ok "ConfigMap grafana-alert-rules 应用成功（4 规则 + 通知策略）"
  else
    fail "grafana-alerting.yaml 应用失败"
  fi
else
  fail "文件不存在: $DEPLOY_DIR/grafana-alerting.yaml"
fi

# ── 3. 部署日志注入 CronJob ──
step "3. 部署日志注入 CronJob（log-injector-cronjob.yaml）"
if [[ -f "$DEPLOY_DIR/log-injector-cronjob.yaml" ]]; then
  if kubectl apply -f "$DEPLOY_DIR/log-injector-cronjob.yaml" >/dev/null 2>&1; then
    ok "CronJob log-injector 应用成功（*/5 * * * *）"
  else
    fail "log-injector-cronjob.yaml 应用失败"
  fi
else
  fail "文件不存在: $DEPLOY_DIR/log-injector-cronjob.yaml"
fi

# ── 4. 部署 Mock Alertmanager ──
step "4. 部署 Mock Alertmanager（mock-webhook-pod.yaml）"
if [[ -f "$DEPLOY_DIR/mock-webhook-pod.yaml" ]]; then
  if kubectl apply -f "$DEPLOY_DIR/mock-webhook-pod.yaml" >/dev/null 2>&1; then
    ok "Pod mock-alert-webhook 应用成功"
  else
    fail "mock-webhook-pod.yaml 应用失败"
  fi
else
  fail "文件不存在: $DEPLOY_DIR/mock-webhook-pod.yaml"
fi

# ── 5. 部署 Grafana（可选）──
if [[ "$SKIP_GRAFANA" != "true" ]]; then
  step "5. 部署 Grafana（grafana.yaml，含数据源/看板/告警挂载）"
  if [[ -f "$DEPLOY_DIR/grafana.yaml" ]]; then
    if kubectl apply -f "$DEPLOY_DIR/grafana.yaml" >/dev/null 2>&1; then
      ok "Grafana 应用成功"
      # 告警规则 ConfigMap 变更需重启 Grafana 加载
      kubectl rollout restart deploy/grafana -n "$NAMESPACE" >/dev/null 2>&1 \
        && echo "       Grafana 已重启（加载告警规则）" \
        || warn "Grafana 重启失败（可手动: kubectl rollout restart deploy/grafana -n $NAMESPACE）"
    else
      fail "grafana.yaml 应用失败"
    fi
  else
    warn "grafana.yaml 不存在，跳过 Grafana 部署（可用 --skip-grafana 明确跳过）"
  fi
else
  echo "  （已跳过 Grafana 部署，--skip-grafana）"
  step "5. 重启 Grafana 以加载告警规则"
  kubectl rollout restart deploy/grafana -n "$NAMESPACE" >/dev/null 2>&1 \
    && ok "Grafana 已重启（加载新挂载的告警规则）" \
    || warn "Grafana 重启失败（可手动执行）"
fi

# ── 6. 就绪等待 ──
step "6. 等待资源就绪"
kubectl wait --for=condition=Ready pod/mock-alert-webhook -n "$NAMESPACE" --timeout=60s >/dev/null 2>&1 \
  && ok "mock-alert-webhook Pod Ready" || warn "mock-alert-webhook 未 Ready（检查镜像/事件）"
if kubectl get cronjob log-injector -n "$NAMESPACE" >/dev/null 2>&1; then
  ok "CronJob log-injector 已创建（下次触发见 kubectl get cronjob -n $NAMESPACE）"
fi

# ── 7. 端到端验证 ──
step "7. 端到端验证"
# 7.1 Webhook 健康
WEBHOOK_HEALTH=$(kubectl exec -n "$NAMESPACE" mock-alert-webhook -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:9093/health', timeout=5).read().decode())" 2>/dev/null)
if [[ "$WEBHOOK_HEALTH" == "ok" ]]; then
  ok "Mock Alertmanager /health 返回 ok"
else
  warn "Mock Alertmanager 健康检查失败（$WEBHOOK_HEALTH）"
fi

# 7.2 CronJob 手动触发一次自检
echo "  手动触发 CronJob 验证（log-injector-verify）..."
kubectl create job --from=cronjob/log-injector log-injector-verify -n "$NAMESPACE" >/dev/null 2>&1 \
  && echo "       已创建验证 Job" || warn "验证 Job 创建失败（CronJob 可能未就绪）"
if kubectl wait --for=condition=Complete job/log-injector-verify -n "$NAMESPACE" --timeout=120s >/dev/null 2>&1; then
  ok "CronJob 验证 Job 执行成功（自检 PASS）"
  kubectl logs -n "$NAMESPACE" job/log-injector-verify --tail=3 2>/dev/null | while read -r line; do
    echo "       $line"
  done
else
  JOB_POD=$(kubectl get pods -n "$NAMESPACE" -l job-name=log-injector-verify -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [[ -n "$JOB_POD" ]]; then
    kubectl logs -n "$NAMESPACE" "$JOB_POD" --tail=5 2>&1 | while read -r line; do echo "       $line"; done
  fi
  warn "验证 Job 失败（自检未通过）— 检查: kubectl logs -n $NAMESPACE -l job-name=log-injector-verify"
fi
# 清理验证 Job（保留 CronJob）
kubectl delete job log-injector-verify -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1

# ── 8. 汇总 ──
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  部署结果汇总"
echo "════════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}通过: $PASS${NC}  ${RED}失败: $FAIL${NC}  ${YELLOW}警告: $WARN${NC}"
echo ""
if [[ $FAIL -gt 0 ]]; then
  echo -e "  ${RED}✗ 存在失败项，请按提示修复后重试${NC}"
  exit 1
fi
echo -e "  ${GREEN}✓ 监控组件部署完成${NC}"
echo ""
echo "  ── 验证清单 ──"
echo "  1. 告警规则: kubectl get -n $NAMESPACE cm grafana-alert-rules"
echo "  2. CronJob:  kubectl get cronjob log-injector -n $NAMESPACE"
echo "  3. Webhook:  kubectl logs -n $NAMESPACE mock-alert-webhook --tail=5"
echo "  4. Grafana:  kubectl logs deploy/grafana -n $NAMESPACE | grep -i alert"
echo "  5. 手工触发: kubectl create job --from=cronjob/log-injector manual-1 -n $NAMESPACE"
echo "════════════════════════════════════════════════════════════════"
