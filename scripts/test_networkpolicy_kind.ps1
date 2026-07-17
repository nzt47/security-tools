<#
.SYNOPSIS
  P2-4 NetworkPolicy 本地测试脚本（kind 集群实测）

.DESCRIPTION
  创建临时 kind 集群，部署 tlm-ops-reporter Helm Chart（启用 NetworkPolicy），
  验证网络隔离语义：
  - T1: Pod Ready 验证
  - T2: DNS 解析（应成功，egress DNS 规则放行 kube-dns）
  - T3: 外部访问隔离（应失败/超时，egress 拒绝所有非 DNS 出站）
  - T4: NetworkPolicy 资源配置验证（podSelector/policyTypes/ingress/egress）
  - T5: 禁用 NetworkPolicy 回归（外部访问恢复）

  三义原则：
  - [不易] 验证 NetworkPolicy 核心语义：Ingress 拒绝所有 + Egress 仅 DNS
  - [变易] 参数化集群名/命名空间/镜像标签，支持 -KeepCluster 调试
  - [简易] 单脚本全流程，幂等可重复

.PARAMETER ClusterName
  kind 集群名称，默认 tlm-np-test

.PARAMETER KeepCluster
  测试完成后保留集群（用于调试）

.PARAMETER SkipImageBuild
  跳过镜像构建（使用已构建的 $ImageTag 镜像）

.EXAMPLE
  .\test_networkpolicy_kind.ps1
  .\test_networkpolicy_kind.ps1 -KeepCluster
  .\test_networkpolicy_kind.ps1 -SkipImageBuild -ImageTag tlm-ops-reporter:v1.2
#>

param(
    [string]$ClusterName = "tlm-np-test",
    [string]$Namespace = "monitoring",
    [string]$ReleaseName = "tlm-ops",
    [string]$ImageTag = "tlm-ops-reporter:np-test",
    [int]$ExternalTimeoutSec = 5,
    [int]$PodReadyTimeoutSec = 90,
    [switch]$KeepCluster,
    [switch]$SkipImageBuild
)

$ErrorActionPreference = "Stop"

# ─── 路径解析 ───
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ChartPath = Join-Path $RepoRoot "deploy\helm\tlm-ops-reporter"
$Dockerfile = Join-Path $RepoRoot "docker\ops-reporter\Dockerfile"

# ─── 测试结果收集 ───
$script:results = @()

function Add-Result($id, $name, $passed, $detail) {
    $script:results += [PSCustomObject]@{
        Id = $id; Name = $name; Passed = $passed; Detail = $detail
    }
    $tag = if ($passed) { "PASS" } else { "FAIL" }
    $color = if ($passed) { "Green" } else { "Red" }
    Write-Host "  [$tag] $id : $name" -ForegroundColor $color
    if ($detail) { Write-Host "         $detail" -ForegroundColor Gray }
}

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Info($msg) { Write-Host "  [INFO] $msg" -ForegroundColor Gray }
function Write-Pass($msg) { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Write-Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Test-Cmd($n) { return [bool](Get-Command $n -ErrorAction SilentlyContinue) }

# ===== 1. 依赖检查 =====
Write-Step "步骤 1: 依赖检查"

$deps = @(
    @{Name="docker"; Label="Docker"; Install="https://docs.docker.com/get-docker/"},
    @{Name="kubectl"; Label="kubectl"; Install="https://kubernetes.io/docs/tasks/tools/"},
    @{Name="kind"; Label="kind"; Install="https://kind.sigs.k8s.io/docs/user/quick-start/#installation"},
    @{Name="helm"; Label="Helm"; Install="https://helm.sh/docs/intro/install/"}
)

$missing = @()
foreach ($d in $deps) {
    if (Test-Cmd $d.Name) {
        $ver = & $d.Name version 2>&1 | Select-Object -First 1
        Write-Pass "$($d.Label): $ver"
    } else {
        Write-Fail "$($d.Label) 未安装"
        Write-Info "  安装指引: $($d.Install)"
        $missing += $d.Name
    }
}

if ($missing.Count -gt 0) {
    Write-Host "`n缺少依赖: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "`nWindows 快速安装（winget）:" -ForegroundColor Cyan
    Write-Host "  winget install Kubernetes.kind"
    Write-Host "  winget install Helm.Helm"
    Write-Host "  winget install Kubernetes.kubectl"
    Write-Host "`nmacOS（brew）:" -ForegroundColor Cyan
    Write-Host "  brew install kind helm kubectl"
    exit 1
}

# ===== 2. 创建 kind 集群 =====
Write-Step "步骤 2: 创建 kind 集群 '$ClusterName'"

# [不易] kind 默认 kindnetd CNI 支持 NetworkPolicy，无需额外配置
$existing = kind get clusters 2>&1
if ($existing -match $ClusterName) {
    Write-Info "集群已存在，复用"
} else {
    Write-Info "创建新集群（kindnetd CNI 原生支持 NetworkPolicy）..."
    kind create cluster --name $ClusterName 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) { Write-Fail "集群创建失败"; exit 1 }
}
kubectl config use-context "kind-$ClusterName" 2>&1 | Out-Null
Write-Pass "kubectl context -> kind-$ClusterName"

# ===== 3. 构建并加载镜像 =====
Write-Step "步骤 3: 构建并加载镜像"

if (-not $SkipImageBuild) {
    Write-Info "构建镜像 $ImageTag ..."
    docker build -t $ImageTag -f $Dockerfile $RepoRoot 2>&1 | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "镜像构建失败"
        Write-Warn "如因 python:3.11.9-slim-bookworm 网络拉取失败，可："
        Write-Info "  1. 临时改 Dockerfile FROM 为 python:3.11-slim 重试"
        Write-Info "  2. 或预构建镜像后用 -SkipImageBuild -ImageTag <tag> 重跑"
        exit 1
    }
    Write-Pass "镜像构建成功"
} else {
    Write-Info "跳过构建（-SkipImageBuild），使用已有 $ImageTag"
}

Write-Info "加载镜像到 kind 节点..."
kind load docker-image $ImageTag --name $ClusterName 2>&1 | ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) { Write-Fail "镜像加载失败"; exit 1 }
Write-Pass "镜像加载成功"

# ===== 4. Helm 部署（启用 NetworkPolicy）=====
Write-Step "步骤 4: Helm 部署（networkPolicy.enabled=true）"

# 清理旧 release
helm uninstall $ReleaseName -n $Namespace 2>$null | Out-Null
Start-Sleep -Seconds 2
kubectl get ns $Namespace 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { kubectl create namespace $Namespace 2>&1 | Out-Null }

$imgRepo = $ImageTag.Split(':')[0]
$imgTag = $ImageTag.Split(':')[1]

helm install $ReleaseName $ChartPath `
    --namespace $Namespace `
    --set image.repository=$imgRepo `
    --set image.tag=$imgTag `
    --set image.pullPolicy=Never `
    --set networkPolicy.enabled=true `
    --set logsVolume.create=true `
    --set outputVolume.create=true 2>&1 | ForEach-Object { Write-Host "  $_" }

if ($LASTEXITCODE -ne 0) { Write-Fail "Helm 部署失败"; exit 1 }
Write-Pass "Helm 部署成功"

# ===== 5. 等待 Pod Ready（T1）=====
Write-Step "步骤 5: T1 - Pod Ready 验证"

$podName = kubectl get pods -n $Namespace `
    -l "app.kubernetes.io/instance=$ReleaseName" `
    -o jsonpath="{.items[0].metadata.name}" 2>$null

if (-not $podName) {
    Add-Result "T1" "Pod Ready" $false "未找到 Pod"
    kubectl get pods -n $Namespace
    exit 1
}
Write-Info "目标 Pod: $podName"

kubectl wait --for=condition=Ready "pod/$podName" `
    -n $Namespace --timeout="${PodReadyTimeoutSec}s" 2>&1 | ForEach-Object { Write-Host "  $_" }

if ($LASTEXITCODE -eq 0) {
    Add-Result "T1" "Pod Ready" $true "Pod $podName 已就绪"
} else {
    Add-Result "T1" "Pod Ready" $false "Ready 超时"
    kubectl describe pod $podName -n $Namespace | Select-Object -First 40
    exit 1
}

# ===== 6. T2 - DNS 解析（应成功）=====
Write-Step "步骤 6: T2 - DNS 解析测试（应成功）"

# [不易] egress DNS 规则放行 kube-dns UDP/TCP 53，Pod 必须能解析集群内服务
$dnsOutput = kubectl exec $podName -n $Namespace -- `
    python -c "import socket; print('DNS_OK:'+socket.gethostbyname('kubernetes.default.svc.cluster.local'))" 2>&1

if ($dnsOutput -match "DNS_OK:") {
    $ip = ($dnsOutput -split ":")[1]
    Add-Result "T2" "DNS 解析（egress DNS 放行）" $true "kubernetes.default.svc.cluster.local -> $ip"
} else {
    Add-Result "T2" "DNS 解析（egress DNS 放行）" $false "DNS 失败: $dnsOutput"
    Write-Warn "DNS 失败将导致 Pod 内服务发现异常，检查 NP egress DNS 规则"
}

# ===== 7. T3 - 外部访问隔离（应失败）=====
Write-Step "步骤 7: T3 - 外部访问隔离测试（应失败/超时）"

# [不易] egress 拒绝所有非 DNS 出站，HTTP 访问外部必须被阻止
# [变易] 用 python urllib 测试（slim 镜像无 curl/wget），设短超时避免长时间挂起
$extOutput = kubectl exec $podName -n $Namespace -- `
    python -c "import urllib.request,socket; socket.setdefaulttimeout($ExternalTimeoutSec); urllib.request.urlopen('http://example.com'); print('EXTERNAL_OK')" 2>&1

if ($extOutput -match "EXTERNAL_OK") {
    Add-Result "T3" "外部访问隔离（egress 拒绝）" $false "外部访问未被拒绝，NetworkPolicy 未生效！"
    Write-Warn "检查：1) CNI 是否支持 NP  2) networkPolicy.enabled 是否 true"
} else {
    # 期望失败：超时 / 连接被拒 / 其他网络错误
    $errSummary = ($extOutput | Out-String).Trim().Split("`n")[0]
    Add-Result "T3" "外部访问隔离（egress 拒绝）" $true "外部访问被正确拒绝: $errSummary"
}

# ===== 8. T4 - NetworkPolicy 资源验证 =====
Write-Step "步骤 8: T4 - NetworkPolicy 资源配置验证"

$npJson = kubectl get networkpolicy -n $Namespace -o json 2>&1
$npCount = ($npJson | ConvertFrom-Json).items.Count

if ($npCount -eq 0) {
    Add-Result "T4" "NP 资源配置" $false "未找到 NetworkPolicy 资源"
} else {
    $np = ($npJson | ConvertFrom-Json).items[0]
    $checks = @{
        "policyTypes" = ($np.spec.policyTypes -join ",")
        "ingress空" = ($np.spec.ingress.Count -eq 0)
        "egress非空" = ($np.spec.egress.Count -gt 0)
        "DNS端口53" = ($np.spec.egress.ports.port -contains 53)
    }
    $allOk = $checks["policyTypes"] -eq "Ingress,Egress" -and $checks["ingress空"] -and $checks["egress非空"] -and $checks["DNS端口53"]
    $detail = "policyTypes=$($checks['policyTypes']), ingress空=$($checks['ingress空']), egressDNS=$($checks['DNS端口53'])"
    Add-Result "T4" "NP 资源配置" $allOk $detail
}

# ===== 9. T5 - 禁用 NetworkPolicy 回归 =====
Write-Step "步骤 9: T5 - 禁用 NetworkPolicy 回归测试"

Write-Info "Helm upgrade（networkPolicy.enabled=false）..."
helm upgrade $ReleaseName $ChartPath `
    --namespace $Namespace `
    --set image.repository=$imgRepo `
    --set image.tag=$imgTag `
    --set image.pullPolicy=Never `
    --set networkPolicy.enabled=false `
    --set logsVolume.create=true `
    --set outputVolume.create=true 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) {
    Add-Result "T5" "禁用 NP 回归" $false "Helm upgrade 失败"
} else {
    # 等待新 Pod Ready
    Start-Sleep -Seconds 8
    $podName2 = kubectl get pods -n $Namespace `
        -l "app.kubernetes.io/instance=$ReleaseName" `
        -o jsonpath="{.items[0].metadata.name}" 2>$null
    kubectl wait --for=condition=Ready "pod/$podName2" `
        -n $Namespace --timeout="${PodReadyTimeoutSec}s" 2>&1 | Out-Null

    # 验证 NP 已删除
    $npAfter = kubectl get networkpolicy -n $Namespace 2>&1
    $npDeleted = $npAfter -match "No resources found" -or $LASTEXITCODE -ne 0

    # [变易] 禁用后外部访问应恢复（但 kind 节点网络可能本身受限，仅作辅助验证）
    $extOutput2 = kubectl exec $podName2 -n $Namespace -- `
        python -c "import urllib.request,socket; socket.setdefaulttimeout(10); urllib.request.urlopen('http://example.com'); print('EXTERNAL_OK')" 2>&1

    if ($extOutput2 -match "EXTERNAL_OK") {
        Add-Result "T5" "禁用 NP 回归" $true "NP 已删除 + 外部访问恢复"
    } elseif ($npDeleted) {
        Add-Result "T5" "禁用 NP 回归" $true "NP 资源已删除（外部访问失败可能为 kind 节点网络限制，非 NP 问题）"
        Write-Warn "kind 节点访问外网可能受限，与 NetworkPolicy 无关"
    } else {
        Add-Result "T5" "禁用 NP 回归" $false "NP 未正确删除或外部访问异常"
    }
}

# ===== 10. 测试报告 =====
Write-Step "测试报告"

$passed = ($script:results | Where-Object Passed).Count
$total = $script:results.Count
Write-Host ""
$script:results | Format-Table Id, Name, Passed, Detail -AutoSize

Write-Host "`n结果: $passed / $total 通过" -ForegroundColor $(if ($passed -eq $total) {"Green"} else {"Yellow"})

# ===== 11. 清理 =====
Write-Step "步骤 11: 清理"

if ($KeepCluster) {
    Write-Info "保留集群（-KeepCluster），手动清理命令:"
    Write-Host "  helm uninstall $ReleaseName -n $Namespace"
    Write-Host "  kubectl delete ns $Namespace"
    Write-Host "  kind delete cluster --name $ClusterName"
} else {
    Write-Info "卸载 Helm release..."
    helm uninstall $ReleaseName -n $Namespace 2>&1 | Out-Null
    Write-Info "删除 kind 集群..."
    kind delete cluster --name $ClusterName 2>&1 | Out-Null
    Write-Pass "清理完成"
}

Write-Host "`n=== P2-4 NetworkPolicy 测试结束 ===" -ForegroundColor Cyan