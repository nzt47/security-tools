<#
.SYNOPSIS
    启动 Docker Desktop K8s 集群并等待所有服务就绪

.DESCRIPTION
    【不易】幂等可重复执行，每步含超时与失败诊断
    【变易】参数化: 等待超时、是否自动部署资源
    【简易】分阶段输出 ✓/✗/⚠，含就绪轮询

    流程:
      1. 检查 Docker Desktop 安装
      2. 启动 Docker Desktop（如未运行）
      3. 等待 Docker daemon 就绪
      4. 检查 K8s 启用状态（首次需手动启用，脚本检测并提示）
      5. 等待 kubectl 集群就绪
      6. 等待核心组件（kube-apiserver/etcd/scheduler/controller）就绪
      7. 可选: 调用 deploy_k8s_resources.sh 部署业务资源
      8. 等待业务 Pod Ready
      9. 输出集群状态摘要

.PARAMETER DeployResources
    集群就绪后自动调用 deploy_k8s_resources.sh 部署业务资源

.PARAMETER Namespace
    业务资源命名空间（默认 production，配合 -DeployResources 使用）

.PARAMETER TimeoutSec
    各阶段等待超时秒数（默认 180）

.EXAMPLE
    .\scripts\start_k8s_cluster.ps1
    .\scripts\start_k8s_cluster.ps1 -DeployResources
    .\scripts\start_k8s_cluster.ps1 -DeployResources -Namespace production -TimeoutSec 300
#>
param(
    [switch]$DeployResources,
    [string]$Namespace = "production",
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = "SilentlyContinue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step($msg) { Write-Output ""; Write-Output "── $msg ──" }
function Write-Ok($msg)   { Write-Output "  [OK] $msg" }
function Write-Warn($msg) { Write-Output "  [WARN] $msg" }
function Write-Fail($msg) { Write-Output "  [FAIL] $msg" }

Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  启动 Docker Desktop K8s 集群并等待就绪"
Write-Output "  TimeoutSec=$TimeoutSec  DeployResources=$DeployResources"
Write-Output "════════════════════════════════════════════════════════════════"

# ═══════════════════════════════════════════════════════════════════
#  1. 检查 Docker Desktop 安装
# ═══════════════════════════════════════════════════════════════════
Write-Step "1. 检查 Docker Desktop 安装"

$dockerExe = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerExe) {
    # 尝试常见安装路径
    $dockerPaths = @(
        "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe",
        "${env:ProgramFiles}\Docker\Docker\resources\bin\docker.exe"
    )
    $found = $false
    foreach ($p in $dockerPaths) {
        if (Test-Path $p) {
            $env:Path += ";$(Split-Path $p)"
            $found = $true
            Write-Ok "Docker 发现于: $p"
            break
        }
    }
    if (-not $found) {
        Write-Fail "Docker 未安装 — 请安装 Docker Desktop: https://www.docker.com/products/docker-desktop"
        exit 1
    }
} else {
    Write-Ok "Docker 已在 PATH: $($dockerExe.Source)"
}

$dockerDesktopExe = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
if (-not (Test-Path $dockerDesktopExe)) {
    $dockerDesktopExe = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
}

# ═══════════════════════════════════════════════════════════════════
#  2. 启动 Docker Desktop（如未运行）
# ═══════════════════════════════════════════════════════════════════
Write-Step "2. 启动 Docker Desktop"

$dockerRunning = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerRunning = $true }
} catch {}

if ($dockerRunning) {
    Write-Ok "Docker daemon 已在运行"
} else {
    if (Test-Path $dockerDesktopExe) {
        Write-Output "  启动 Docker Desktop..."
        Start-Process $dockerDesktopExe
        Write-Output "  等待 Docker daemon 就绪（最多 $TimeoutSec 秒）..."
    } else {
        Write-Fail "Docker Desktop 可执行文件未找到 — 请手动启动"
        exit 1
    }

    # 轮询等待 Docker daemon
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = docker info 2>&1
            if ($LASTEXITCODE -eq 0) {
                $dockerRunning = $true
                break
            }
        } catch {}
        Start-Sleep -Seconds 3
        $remain = [int]($deadline - (Get-Date)).TotalSeconds
        Write-Output "  ...等待 Docker daemon (剩余 ${remain}s)"
    }

    if ($dockerRunning) {
        Write-Ok "Docker daemon 就绪"
    } else {
        Write-Fail "Docker daemon 在 ${TimeoutSec}s 内未就绪 — 检查 Docker Desktop 启动状态"
        Write-Output "       手动启动 Docker Desktop 后重跑此脚本"
        exit 1
    }
}

# ═══════════════════════════════════════════════════════════════════
#  3. 检查 K8s 启用状态
# ═══════════════════════════════════════════════════════════════════
Write-Step "3. 检查 Kubernetes 启用状态"

$kubeContext = kubectl config current-context 2>&1
$clusterReady = $false

if ($kubeContext -match "docker-desktop") {
    Write-Ok "kubectl 当前 context: docker-desktop"
    # 验证集群真正可达
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $clusterInfo = kubectl cluster-info --request-timeout=5s 2>&1
        if ($clusterInfo -match "Kubernetes control plane.*is running") {
            $clusterReady = $true
            break
        }
        Start-Sleep -Seconds 3
    }
    if ($clusterReady) {
        Write-Ok "Kubernetes control plane 运行中"
    } else {
        Write-Warn "context 是 docker-desktop 但 control plane 不可达 — K8s 可能正在启动"
    }
} else {
    Write-Warn "kubectl context 不是 docker-desktop (当前: $kubeContext)"
}

# ═══════════════════════════════════════════════════════════════════
#  4. 等待 K8s 集群完全就绪
# ═══════════════════════════════════════════════════════════════════
Write-Step "4. 等待 Kubernetes 集群就绪"

if (-not $clusterReady) {
    Write-Output "  轮询等待集群就绪（最多 $TimeoutSec 秒）..."
    Write-Output "  注: 首次使用需在 Docker Desktop 设置中手动启用 Kubernetes"
    Write-Output "      Settings → Kubernetes → Enable Kubernetes → Apply & Restart"
    Write-Output ""

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $clusterInfo = kubectl cluster-info --request-timeout=5s 2>&1
        if ($clusterInfo -match "Kubernetes control plane.*is running") {
            $clusterReady = $true
            break
        }
        Start-Sleep -Seconds 5
        $remain = [int]($deadline - (Get-Date)).TotalSeconds
        Write-Output "  ...等待 K8s 就绪 (剩余 ${remain}s)"
    }

    if (-not $clusterReady) {
        Write-Fail "Kubernetes 集群在 ${TimeoutSec}s 内未就绪"
        Write-Output ""
        Write-Output "  排查步骤:"
        Write-Output "    1. 确认 Docker Desktop 已启动（托盘图标稳定不再转圈）"
        Write-Output "    2. 打开 Docker Desktop → Settings → Kubernetes"
        Write-Output "    3. 勾选 'Enable Kubernetes' → Apply & Restart"
        Write-Output "    4. 等待 K8s 镜像拉取完成（首次约 3-5 分钟）"
        Write-Output "    5. 重跑此脚本"
        exit 1
    }
}

Write-Ok "Kubernetes 集群就绪"

# ═══════════════════════════════════════════════════════════════════
#  5. 等待核心组件就绪
# ═══════════════════════════════════════════════════════════════════
Write-Step "5. 等待核心组件就绪（kube-apiserver/etcd/scheduler/controller-manager）"

$coreComponents = @("kube-apiserver", "etcd", "kube-scheduler", "kube-controller-manager")
$deadline = (Get-Date).AddSeconds(60)
$coreReady = $false

while ((Get-Date) -lt $deadline) {
    $notReady = @()
    foreach ($comp in $coreComponents) {
        $status = kubectl get pods -n kube-system -l component=$comp -o jsonpath='{.items[*].status.phase}' 2>&1
        if ($status -notmatch "Running") {
            $notReady += $comp
        }
    }
    if ($notReady.Count -eq 0) {
        $coreReady = $true
        break
    }
    Start-Sleep -Seconds 3
}

if ($coreReady) {
    Write-Ok "核心组件全部 Running"
} else {
    Write-Warn "部分核心组件未就绪: $($notReady -join ', ') — 可能仍在启动"
    Write-Output "       检查: kubectl get pods -n kube-system"
}

# ═══════════════════════════════════════════════════════════════════
#  6. 等待 kube-system 所有 Pod 就绪
# ═══════════════════════════════════════════════════════════════════
Write-Step "6. 等待 kube-system Pod 就绪"

$deadline = (Get-Date).AddSeconds(90)
$systemReady = $false
while ((Get-Date) -lt $deadline) {
    $notReadyCount = kubectl get pods -n kube-system --field-selector=status.phase!=Running --no-headers 2>&1 | Measure-Object -Line | Select-Object -ExpandProperty Lines
    if ([int]$notReadyCount -eq 0) {
        $systemReady = $true
        break
    }
    Start-Sleep -Seconds 3
}

if ($systemReady) {
    $systemPods = kubectl get pods -n kube-system --no-headers 2>&1
    $podCount = ($systemPods | Measure-Object -Line).Lines
    Write-Ok "kube-system 所有 Pod Running ($podCount 个)"
} else {
    Write-Warn "kube-system 部分 Pod 未就绪 — kubectl get pods -n kube-system"
}

# ═══════════════════════════════════════════════════════════════════
#  7. 集群状态摘要
# ═══════════════════════════════════════════════════════════════════
Write-Step "7. 集群状态摘要"

Write-Output ""
Write-Output "  集群信息:"
kubectl cluster-info 2>&1 | ForEach-Object { Write-Output "    $_" }

Write-Output ""
Write-Output "  节点状态:"
kubectl get nodes 2>&1 | ForEach-Object { Write-Output "    $_" }

Write-Output ""
Write-Output "  kube-system Pod:"
kubectl get pods -n kube-system 2>&1 | ForEach-Object { Write-Output "    $_" }

# ═══════════════════════════════════════════════════════════════════
#  8. 可选: 部署业务资源
# ═══════════════════════════════════════════════════════════════════
if ($DeployResources) {
    Write-Step "8. 部署业务资源（-DeployResources）"

    $deployScript = Join-Path $scriptDir "deploy_k8s_resources.sh"
    if (Test-Path $deployScript) {
        # 【不易】bash 不识别 Windows 反斜杠路径，必须转为正斜杠（否则反斜杠被当转义符吞掉）
        $deployScriptUnix = $deployScript -replace '\\', '/'
        Write-Output "  调用: bash $deployScriptUnix $Namespace"
        bash $deployScriptUnix $Namespace 2>&1 | ForEach-Object { Write-Output "    $_" }

        if ($LASTEXITCODE -eq 0) {
            Write-Ok "业务资源部署完成"

            # 等待业务 Pod Ready
            Write-Step "9. 等待业务 Pod Ready"
            $deadline = (Get-Date).AddSeconds(360)
            $bizReady = $false
            while ((Get-Date) -lt $deadline) {
                $depStatus = kubectl get deployment skill-retrieval-service -n $Namespace -o jsonpath='{.status.readyReplicas}/{.status.replicas}' 2>&1
                if ($depStatus -match "^(\d+)/\1$" -and $depStatus -notmatch "^0/") {
                    $bizReady = $true
                    break
                }
                Start-Sleep -Seconds 5
                $remain = [int]($deadline - (Get-Date)).TotalSeconds
                Write-Output "  ...等待业务 Pod Ready (剩余 ${remain}s, 当前: $depStatus)"
            }

            if ($bizReady) {
                Write-Ok "业务 Pod 就绪 ($depStatus)"
            } else {
                Write-Warn "业务 Pod 未就绪（模型加载可能需要更长时间）"
                Write-Output "       手动检查: kubectl get pods -n $Namespace -l app=skill-retrieval-service"
            }
        } else {
            Write-Fail "业务资源部署失败（exit=$LASTEXITCODE）"
        }
    } else {
        Write-Warn "部署脚本不存在: $deployScript"
    }
}

# ═══════════════════════════════════════════════════════════════════
#  9. 完成
# ═══════════════════════════════════════════════════════════════════
Write-Output ""
Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  集群启动完成"
Write-Output "════════════════════════════════════════════════════════════════"
Write-Output ""
Write-Output "  下一步:"
if (-not $DeployResources) {
    Write-Output "    部署业务资源: bash scripts/deploy_k8s_resources.sh $Namespace"
    Write-Output "    或带部署启动: .\scripts\start_k8s_cluster.ps1 -DeployResources"
}
Write-Output "    预检:         bash scripts/k8s_preflight_check.sh $Namespace"
Write-Output "    压测:         k6 run -e ENDPOINT=http://skill-retrieval-service.${Namespace}.svc.cluster.local:8080/match scripts/k6/k8s_loadtest_skill_match.js"
Write-Output "════════════════════════════════════════════════════════════════"
