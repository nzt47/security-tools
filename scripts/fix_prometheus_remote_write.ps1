<#
.SYNOPSIS
    修复 Prometheus Remote Write 404 错误（启用 --web.enable-remote-write-receiver）

.DESCRIPTION
    【不易】k6 通过 --out experimental-prometheus-rw 推送指标到 Prometheus /api/v1/write
           未启用 --web.enable-remote-write-receiver 时该端点返回 404，导致:
             - k6 指标推不进 Prometheus → Grafana 面板无数据
             - Prometheus Adapter 无数据 → HPA 无法读取 skill_match_latency_p99
             - HPA 降级为 CPU 扩容 → 扩容滞后 → P99 延迟超标

    【变易】支持两种部署方式:
             - helm 部署（prometheus-community/kube-prometheus-stack）
             - yaml 部署（prometheus-standalone.yaml）
           自动检测 deployment 并 patch args

    【简易】幂等执行 — 已启用则跳过，未启用则 patch + restart + 验证

.PARAMETER Namespace
    Prometheus 所在 namespace，默认自动检测（monitoring / kube-system / prometheus）

.PARAMETER DeploymentName
    Prometheus deployment 名称，默认自动检测

.EXAMPLE
    # 自动检测并修复
    pwsh scripts/fix_prometheus_remote_write.ps1

.EXAMPLE
    # 指定 namespace 和 deployment
    pwsh scripts/fix_prometheus_remote_write.ps1 -Namespace monitoring -DeploymentName prometheus-server
#>

param(
    [string]$Namespace = "",
    [string]$DeploymentName = ""
)

$ErrorActionPreference = "Stop"

# ═══════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════

function Write-Step([string]$msg) {
    Write-Host "  [STEP] $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg) {
    Write-Host "  [OK]   $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}

function Write-Err([string]$msg) {
    Write-Host "  [ERR]  $msg" -ForegroundColor Red
}

# ═══════════════════════════════════════════════════════════════════
#  阶段 1: 检测 Prometheus Deployment
# ═══════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  Prometheus Remote Write 修复脚本" -ForegroundColor Cyan
Write-Host "  目标: 启用 --web.enable-remote-write-receiver（解决 k6 推送 404）" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# kubectl 可用性检查
Write-Step "检查 kubectl 可用性..."
$null = kubectl version --client 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "kubectl 不可用，请先安装并配置 kubeconfig"
    exit 1
}
Write-Ok "kubectl 就绪"

# 自动检测 namespace 和 deployment
if ([string]::IsNullOrEmpty($Namespace) -or [string]::IsNullOrEmpty($DeploymentName)) {
    Write-Step "自动检测 Prometheus Deployment..."

    # 【变易】常见 namespace 优先级: monitoring > prometheus > kube-system > default
    $candidateNamespaces = @("monitoring", "prometheus", "kube-system", "default")
    $found = $false

    foreach ($ns in $candidateNamespaces) {
        # 查找包含 prometheus 的 deployment
        $deployments = kubectl get deploy -n $ns -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>$null
        if ($LASTEXITCODE -ne 0) { continue }

        foreach ($dep in $deployments) {
            # 匹配 prometheus-server / prometheus / kube-prometheus-stack-prometheus 等
            if ($dep -match "prometheus") {
                # 验证镜像是否为 prom/prometheus
                $image = kubectl get deploy -n $ns $dep -o jsonpath='{.spec.template.spec.containers[0].image}' 2>$null
                if ($image -match "prom/prometheus") {
                    if ([string]::IsNullOrEmpty($Namespace)) { $Namespace = $ns }
                    if ([string]::IsNullOrEmpty($DeploymentName)) { $DeploymentName = $dep }
                    $found = $true
                    break
                }
            }
        }
        if ($found) { break }
    }

    if (-not $found) {
        Write-Err "未找到 Prometheus Deployment，请用 -Namespace 和 -DeploymentName 参数指定"
        Write-Warn "提示: 执行 'kubectl get deploy -A | findstr prometheus' 手动查找"
        exit 1
    }
}

Write-Ok "定位 Prometheus: $Namespace/$DeploymentName"

# ═══════════════════════════════════════════════════════════════════
#  阶段 2: 检查当前 args 是否已启用 remote-write-receiver
# ═══════════════════════════════════════════════════════════════════

Write-Step "检查当前启动参数..."
$currentArgs = kubectl get deploy -n $Namespace $DeploymentName `
    -o jsonpath='{.spec.template.spec.containers[0].args}' 2>$null

Write-Host "  当前 args: $currentArgs" -ForegroundColor DarkGray

if ($currentArgs -match "web.enable-remote-write-receiver") {
    Write-Ok "已启用 --web.enable-remote-write-receiver，无需 patch"
    $alreadyEnabled = $true
} else {
    Write-Warn "未启用 --web.enable-remote-write-receiver，需要 patch"
    $alreadyEnabled = $false
}

# ═══════════════════════════════════════════════════════════════════
#  阶段 3: Patch Deployment 添加启动参数
# ═══════════════════════════════════════════════════════════════════

if (-not $alreadyEnabled) {
    Write-Step "Patch Deployment 添加 --web.enable-remote-write-receiver..."

    # 【不易】JSON patch 在 args 数组末尾追加参数
    # patch 会触发 rollout restart，自动滚动更新
    $patchJson = '{"spec":{"template":{"spec":{"containers":[{"name":"prometheus","args":["--config.file=/etc/prometheus/prometheus.yml","--storage.tsdb.path=/prometheus","--storage.tsdb.retention.time=6h","--web.enable-remote-write-receiver","--web.enable-lifecycle"]}]}}}}'

    # 尝试用 strategic merge patch（保留原有 args 不被覆盖时用 jsonpatch 更安全）
    # 这里用 JSON Patch op=add 追加到 args 数组末尾
    $jsonPatch = '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--web.enable-remote-write-receiver"}]'

    Write-Host "  执行: kubectl patch deploy $DeploymentName -n $Namespace --type=json -p '$jsonPatch'" -ForegroundColor DarkGray
    $patchResult = kubectl patch deploy -n $Namespace $DeploymentName --type=json -p $jsonPatch 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Err "Patch 失败: $patchResult"
        Write-Warn "回退方案: 手动编辑 deployment"
        Write-Warn "  kubectl edit deploy -n $Namespace $DeploymentName"
        Write-Warn "  在 containers[0].args 末尾添加: --web.enable-remote-write-receiver"
        exit 1
    }

    Write-Ok "Patch 成功: $patchResult"

    # 等待 rollout 完成
    Write-Step "等待 rollout 完成（最多 120s）..."
    $rolloutResult = kubectl rollout status deploy -n $Namespace $DeploymentName --timeout=120s 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "rollout 未在 120s 内完成: $rolloutResult"
        Write-Warn "继续验证，可能需要手动等待"
    } else {
        Write-Ok "rollout 完成: $rolloutResult"
    }
}

# ═══════════════════════════════════════════════════════════════════
#  阶段 4: 验证 Remote Write 端点
# ═══════════════════════════════════════════════════════════════════

Write-Step "验证 --web.enable-remote-write-receiver 已生效..."

# 方法 1: 检查 /api/v1/status/flags（Prometheus 2.42+）
Write-Host "  方法 1: 检查 status/flags..." -ForegroundColor DarkGray
$flagsResult = kubectl exec -n $Namespace deploy/$DeploymentName -- `
    wget -qO- http://localhost:9090/api/v1/status/flags 2>$null | Out-String

if ($flagsResult -match "web.enable-remote-write-receiver.*true") {
    Write-Ok "status/flags 确认 remote-write-receiver 已启用"
} else {
    Write-Warn "status/flags 检查未通过（可能 Prometheus 版本 < 2.42），尝试方法 2"
}

# 方法 2: 直接 POST /api/v1/write 验证返回码
Write-Host "  方法 2: 直接验证 /api/v1/write 端点..." -ForegroundColor DarkGray

# port-forward 到本地
Write-Step "启动 port-forward（后台）..."
$pfJob = Start-Job -ScriptBlock {
    param($ns, $dep)
    kubectl port-forward -n $ns "deploy/$dep" 19090:9090 2>&1
} -ArgumentList $Namespace, $DeploymentName

# 等待 port-forward 就绪
Start-Sleep -Seconds 5

# 验证 /api/v1/write
# 【不易】启用 remote-write-receiver 后:
#   - POST 空 body → 204 (No Content) 或 400 (Bad Request，因 body 不是有效 snappy)
#   - 未启用 → 404 (Not Found)
# 只要不是 404 就算成功
Write-Host "  POST http://localhost:19090/api/v1/write ..." -ForegroundColor DarkGray

try {
    $response = Invoke-WebRequest -Uri "http://localhost:19090/api/v1/write" `
        -Method Post -Body ([byte[]](0)) -ContentType "application/x-protobuf" `
        -ErrorAction Stop -UseBasicParsing
    $statusCode = $response.StatusCode
} catch [System.Net.WebException] {
    $statusCode = [int]$_.Exception.Response.StatusCode
} catch {
    # PowerShell 7+ Invoke-WebRequest 抛异常时从 StatusCode 属性取
    $statusCode = $_.Exception.Response.StatusCode.value__
    if (-not $statusCode) {
        Write-Warn "无法获取 HTTP 状态码: $($_.Exception.Message)"
        $statusCode = 0
    }
}

switch ($statusCode) {
    204 { Write-Ok "/api/v1/write 返回 204 — Remote Write 完全就绪" }
    400 { Write-Ok "/api/v1/write 返回 400（期望空 body 报错）— Remote Write 已启用" }
    404 { Write-Err "/api/v1/write 仍返回 404 — Remote Write 未生效，请检查 Prometheus 版本 >= 2.33" }
    default { Write-Warn "/api/v1/write 返回 $statusCode — 请手动确认" }
}

# 清理 port-forward
Write-Step "停止 port-forward..."
Stop-Job $pfJob -ErrorAction SilentlyContinue
Remove-Job $pfJob -ErrorAction SilentlyContinue

# ═══════════════════════════════════════════════════════════════════
#  阶段 5: 输出后续操作指引
# ═══════════════════════════════════════════════════════════════════

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host "  修复完成" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  后续操作:" -ForegroundColor White
Write-Host "    1. 重新执行压测验证指标推送:"
Write-Host "       pwsh scripts/run_full_loadtest.ps1 -RunStress -SkipCluster" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    2. 压测时观察 k6 日志，确认无 'got status code: 404' 错误" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    3. Grafana 面板验证 k6 指标:" -ForegroundColor DarkGray
Write-Host "       rate(k6_http_reqs_total[1m])" -ForegroundColor DarkGray
Write-Host "       histogram_quantile(0.99, rate(k6_http_req_duration_bucket[1m]))" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Prometheus 版本要求: >= 2.33（--web.enable-remote-write-receiver 引入版本）" -ForegroundColor Yellow
Write-Host "  当前镜像: $(kubectl get deploy -n $Namespace $DeploymentName -o jsonpath='{.spec.template.spec.containers[0].image}' 2>$null)" -ForegroundColor DarkGray
Write-Host ""
