# ════════════════════════════════════════════════════════════════════
#  一键部署监控组件 (PowerShell) — 告警规则 + CronJob + Mock 服务
#
#  与 scripts/deploy_monitoring_stack.sh（bash/Linux/CI 版）功能等价。
#  本机（Windows PowerShell）建议使用本脚本，避免 WSL bash 与 Windows
#  kubectl 的 kubeconfig 不一致问题。
#
#  部署资源（deploy/k8s/）:
#    1. grafana-alerting.yaml         4 条告警规则 + webhook 通知策略
#    2. log-injector-cronjob.yaml     日志注入 CronJob（每 5 分钟）+ 脚本 ConfigMap
#    3. mock-webhook-pod.yaml         Mock Alertmanager（告警接收验证器）
#    4. grafana.yaml                  （可选）Grafana 部署 + 数据源 + 看板
#
#  【不易】幂等可重复执行（kubectl apply），失败不中断并给修复建议
#  【变易】参数化: NAMESPACE / -SkipGrafana / -VerifyOnly
#  【简易】分步骤输出 [OK]/[FAIL]/[WARN]，含就绪等待与端到端验证
#
#  用法:
#    powershell -ExecutionPolicy Bypass -File scripts\deploy_monitoring_stack.ps1
#    powershell -ExecutionPolicy Bypass -File scripts\deploy_monitoring_stack.ps1 -SkipGrafana
#    powershell -ExecutionPolicy Bypass -File scripts\deploy_monitoring_stack.ps1 -VerifyOnly
#    $env:NAMESPACE="monitoring"; powershell ... scripts\deploy_monitoring_stack.ps1
#
#  前置条件:
#    - kubectl 已配置且集群可达（当前上下文即目标集群）
#    - Loki 数据源 uid 为 loki（见 deploy/k8s/grafana.yaml）
#    - 节点有本地镜像 docker.io/library/skill-retrieval:local（CronJob/Webhook 依赖）
# ════════════════════════════════════════════════════════════════════

param(
    [switch]$SkipGrafana,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$Namespace = if ($env:NAMESPACE) { $env:NAMESPACE } else { "monitoring" }
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DeployDir = Join-Path $ProjectRoot "deploy\k8s"

$script:Pass = 0; $script:Fail = 0; $script:Warn = 0
function OK   { Write-Host "  [OK]   $args" -ForegroundColor Green;   $script:Pass++ }
function FAIL { Write-Host "  [FAIL] $args" -ForegroundColor Red;     $script:Fail++ }
function WARN { Write-Host "  [WARN] $args" -ForegroundColor Yellow;  $script:Warn++ }
function STEP { Write-Host "`n-- $args --" -ForegroundColor Cyan }

Write-Host ("=" * 64)
Write-Host "  监控组件一键部署 (PowerShell) - namespace=$Namespace"
Write-Host ("=" * 64)

# ── 0. 预检 ──
STEP "0. 预检"
kubectl cluster-info 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    OK "Kubernetes 集群可达 (context: $(kubectl config current-context))"
} else {
    FAIL "Kubernetes 集群不可达 - 检查 kubeconfig / 集群状态"
    exit 1
}
if (Test-Path $DeployDir) {
    OK "deploy/k8s 目录存在"
} else {
    FAIL "deploy/k8s 目录不存在: $DeployDir"
    exit 1
}

# ── 验证模式 ──
if ($VerifyOnly) {
    STEP "验证模式（仅检查已部署组件状态）"
    kubectl get cronjob log-injector -n $Namespace 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { OK "CronJob log-injector 存在" } else { WARN "CronJob log-injector 未部署" }
    kubectl get cm grafana-alert-rules -n $Namespace 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { OK "ConfigMap grafana-alert-rules 存在" } else { WARN "ConfigMap grafana-alert-rules 未部署" }
    kubectl get pod mock-alert-webhook -n $Namespace 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { OK "Pod mock-alert-webhook 存在" } else { WARN "Pod mock-alert-webhook 未部署" }
    Write-Host "`n通过=$($script:Pass) 失败=$($script:Fail) 警告=$($script:Warn)"
    exit 0
}

# ── 1. 创建 namespace ──
STEP "1. 创建 namespace"
kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null
if ($LASTEXITCODE -eq 0) { OK "namespace $Namespace 就绪" } else { FAIL "namespace 创建失败" }

# ── 2. 部署告警规则 ──
STEP "2. 部署告警规则（grafana-alerting.yaml）"
$f = Join-Path $DeployDir "grafana-alerting.yaml"
if (Test-Path $f) {
    kubectl apply -f $f | Out-Null
    if ($LASTEXITCODE -eq 0) { OK "ConfigMap grafana-alert-rules 应用成功（4 规则 + 通知策略）" }
    else { FAIL "grafana-alerting.yaml 应用失败" }
} else { FAIL "文件不存在: $f" }

# ── 3. 部署日志注入 CronJob ──
STEP "3. 部署日志注入 CronJob（log-injector-cronjob.yaml）"
$f = Join-Path $DeployDir "log-injector-cronjob.yaml"
if (Test-Path $f) {
    kubectl apply -f $f | Out-Null
    if ($LASTEXITCODE -eq 0) { OK "CronJob log-injector 应用成功（*/5 * * * *）" }
    else { FAIL "log-injector-cronjob.yaml 应用失败" }
} else { FAIL "文件不存在: $f" }

# ── 4. 部署 Mock Alertmanager ──
STEP "4. 部署 Mock Alertmanager（mock-webhook-pod.yaml）"
$f = Join-Path $DeployDir "mock-webhook-pod.yaml"
if (Test-Path $f) {
    kubectl apply -f $f | Out-Null
    if ($LASTEXITCODE -eq 0) { OK "Pod mock-alert-webhook 应用成功" }
    else { FAIL "mock-webhook-pod.yaml 应用失败" }
} else { FAIL "文件不存在: $f" }

# ── 5. 部署 Grafana（可选）──
if (-not $SkipGrafana) {
    STEP "5. 部署 Grafana（grafana.yaml，含数据源/看板/告警挂载）"
    $f = Join-Path $DeployDir "grafana.yaml"
    if (Test-Path $f) {
        kubectl apply -f $f | Out-Null
        if ($LASTEXITCODE -eq 0) {
            OK "Grafana 应用成功"
            kubectl rollout restart deploy/grafana -n $Namespace | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Host "       Grafana 已重启（加载告警规则）" }
            else { WARN "Grafana 重启失败（可手动: kubectl rollout restart deploy/grafana -n $Namespace）" }
        } else { FAIL "grafana.yaml 应用失败" }
    } else { WARN "grafana.yaml 不存在，跳过 Grafana 部署（可用 -SkipGrafana 明确跳过）" }
} else {
    Write-Host "  （已跳过 Grafana 部署，-SkipGrafana）"
    STEP "5. 重启 Grafana 以加载告警规则"
    kubectl rollout restart deploy/grafana -n $Namespace | Out-Null
    if ($LASTEXITCODE -eq 0) { OK "Grafana 已重启（加载新挂载的告警规则）" }
    else { WARN "Grafana 重启失败（可手动执行）" }
}

# ── 6. 就绪等待 ──
STEP "6. 等待资源就绪"
kubectl wait --for=condition=Ready pod/mock-alert-webhook -n $Namespace --timeout=60s 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { OK "mock-alert-webhook Pod Ready" } else { WARN "mock-alert-webhook 未 Ready（检查镜像/事件）" }
kubectl get cronjob log-injector -n $Namespace 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { OK "CronJob log-injector 已创建" }

# ── 7. 端到端验证 ──
STEP "7. 端到端验证"
# 7.1 Webhook 健康
$health = kubectl exec -n $Namespace mock-alert-webhook -- python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:9093/health', timeout=5).read().decode())" 2>$null
if ($health -eq "ok") { OK "Mock Alertmanager /health 返回 ok" } else { WARN "Mock Alertmanager 健康检查失败（$health）" }

# 7.2 CronJob 手动触发一次自检
Write-Host "  手动触发 CronJob 验证（log-injector-verify）..."
kubectl create job --from=cronjob/log-injector log-injector-verify -n $Namespace 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { WARN "验证 Job 创建失败（CronJob 可能未就绪）" }
else {
    kubectl wait --for=condition=Complete job/log-injector-verify -n $Namespace --timeout=120s 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        OK "CronJob 验证 Job 执行成功（自检 PASS）"
        kubectl logs -n $Namespace job/log-injector-verify --tail=3 2>$null | ForEach-Object { Write-Host "       $_" }
    } else {
        $jobPod = kubectl get pods -n $Namespace -l job-name=log-injector-verify -o jsonpath='{.items[0].metadata.name}' 2>$null
        if ($jobPod) { kubectl logs -n $Namespace $jobPod --tail=5 2>&1 | ForEach-Object { Write-Host "       $_" } }
        WARN "验证 Job 失败（自检未通过）"
    }
}
# 清理验证 Job（保留 CronJob）
kubectl delete job log-injector-verify -n $Namespace --ignore-not-found 2>$null | Out-Null

# ── 8. 汇总 ──
Write-Host ("`n" + "=" * 64)
Write-Host "  部署结果汇总"
Write-Host ("=" * 64)
Write-Host "  通过=$($script:Pass)  失败=$($script:Fail)  警告=$($script:Warn)" -ForegroundColor Cyan
Write-Host ""
if ($script:Fail -gt 0) {
    Write-Host "  [FAIL] 存在失败项，请按提示修复后重试" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK]   监控组件部署完成" -ForegroundColor Green
Write-Host ""
Write-Host "  -- 验证清单 --"
Write-Host "  1. 告警规则: kubectl get -n $Namespace cm grafana-alert-rules"
Write-Host "  2. CronJob:  kubectl get cronjob log-injector -n $Namespace"
Write-Host "  3. Webhook:  kubectl logs -n $Namespace mock-alert-webhook --tail=5"
Write-Host "  4. Grafana:  kubectl logs deploy/grafana -n $Namespace | Select-String alert"
Write-Host "  5. 手工触发: kubectl create job --from=cronjob/log-injector manual-1 -n $Namespace"
Write-Host ("=" * 64)
