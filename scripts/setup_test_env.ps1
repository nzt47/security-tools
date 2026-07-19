<#
.SYNOPSIS
  构造本地 Kind 测试环境 — 用于端到端运行 verify_production_deployment.ps1

.DESCRIPTION
  一键构造本地 K8s 测试环境，包含：
  1. 安装 kind + helm（如未安装）
  2. 创建 kind 集群（含 NetworkPolicy 支持的 CNI 配置）
  3. 构建 ops-reporter 镜像并加载到 kind
  4. 创建测试 namespace + 日志 PVC（模拟生产前置条件）
  5. 部署 Prometheus Operator（可选，用于 P7 监控集成验证）
  6. 输出环境就绪信息 + verify_production_deployment.ps1 运行命令

  三义原则：
  - [不易] 守住 verify_production_deployment.ps1 的前置条件（K8s/Helm/PVC/镜像）
  - [变易] 支持 -SkipPrometheus 跳过监控部署；支持 -ClusterName 自定义
  - [简易] 单脚本一键完成，失败时给出明确指引

.PARAMETER ClusterName
  kind 集群名称, 默认 tlm-prod-test

.PARAMETER K8sVersion
  K8s 版本, 默认 v1.27.3（kind node 镜像 tag）

.PARAMETER SkipPrometheus
  跳过 Prometheus Operator 部署（P7 组将 SKIP/FAIL）

.PARAMETER SkipImageBuild
  跳过镜像构建（使用已构建的镜像）

.PARAMETER ImageTag
  镜像 tag, 默认 v1.2

.EXAMPLE
  .\setup_test_env.ps1
  .\setup_test_env.ps1 -SkipPrometheus
  .\setup_test_env.ps1 -K8sVersion v1.28.0
#>

param(
    [string]$ClusterName = "tlm-prod-test",
    [string]$K8sVersion = "v1.27.3",
    [switch]$SkipPrometheus,
    [switch]$SkipImageBuild,
    [string]$ImageTag = "v1.2"
)

$ErrorActionPreference = "Continue"
$binDir = "C:\Users\Administrator\bin"

# ===== 辅助函数 =====
function Write-Step { param([string]$Msg) Write-Host "`n[STEP] $Msg" -ForegroundColor Cyan }
function Write-OK { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "  [WARN] $Msg" -ForegroundColor Yellow }
function Write-Err { param([string]$Msg) Write-Host "  [ERR] $Msg" -ForegroundColor Red }

# ===== Step 1: 安装 kind =====
function Install-KindCli {
    Write-Step "Step 1: 安装 kind CLI"

    if (Get-Command kind -ErrorAction SilentlyContinue) {
        Write-OK "kind 已安装: $(kind version 2>&1)"
        return $true
    }

    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }

    $kindUrl = "https://kind.sigs.k8s.io/dl/v0.23.0/kind-windows-amd64"
    $kindPath = "$binDir\kind.exe"

    Write-Host "  尝试下载 kind v0.23.0..." -ForegroundColor Gray
    $mirrors = @(
        "https://kind.sigs.k8s.io/dl/v0.23.0/kind-windows-amd64",
        "https://ghproxy.com/https://kind.sigs.k8s.io/dl/v0.23.0/kind-windows-amd64",
        "https://mirror.ghproxy.com/https://kind.sigs.k8s.io/dl/v0.23.0/kind-windows-amd64"
    )

    foreach ($url in $mirrors) {
        Write-Host "  尝试镜像: $url" -ForegroundColor Gray
        try {
            Invoke-WebRequest -Uri $url -OutFile $kindPath -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
            if (Test-Path $kindPath) {
                # 加入 PATH
                $env:Path += ";$binDir"
                $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
                if ($userPath -notlike "*$binDir*") {
                    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
                }
                Write-OK "kind 下载成功: $kindPath"
                return $true
            }
        } catch {
            Write-Warn "镜像下载失败: $($_.Exception.Message)"
        }
    }

    Write-Err "kind 下载失败（所有镜像源均不可用）"
    Write-Host "  手动下载指引:" -ForegroundColor Yellow
    Write-Host "  1. 浏览器访问 https://kind.sigs.k8s.io/docs/user/quick-start/" -ForegroundColor Yellow
    Write-Host "  2. 下载 kind-windows-amd64.exe 重命名为 kind.exe" -ForegroundColor Yellow
    Write-Host "  3. 放置到 $binDir" -ForegroundColor Yellow
    return $false
}

# ===== Step 2: 安装 helm =====
function Install-HelmCli {
    Write-Step "Step 2: 安装 helm CLI"

    if (Get-Command helm -ErrorAction SilentlyContinue) {
        Write-OK "helm 已安装: $(helm version --short 2>&1)"
        return $true
    }

    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }

    $helmVersion = "v3.14.0"
    $helmUrl = "https://get.helm.sh/helm-$helmVersion-windows-amd64.zip"
    $helmZip = "$binDir\helm.zip"
    $helmPath = "$binDir\helm.exe"

    Write-Host "  尝试下载 helm $helmVersion..." -ForegroundColor Gray
    $mirrors = @(
        "https://get.helm.sh/helm-$helmVersion-windows-amd64.zip",
        "https://ghproxy.com/https://get.helm.sh/helm-$helmVersion-windows-amd64.zip"
    )

    foreach ($url in $mirrors) {
        Write-Host "  尝试镜像: $url" -ForegroundColor Gray
        try {
            Invoke-WebRequest -Uri $url -OutFile $helmZip -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
            Expand-Archive -Path $helmZip -DestinationPath "$binDir\helm-tmp" -Force
            Copy-Item "$binDir\helm-tmp\windows-amd64\helm.exe" $helmPath -Force
            Remove-Item "$binDir\helm-tmp" -Recurse -Force
            Remove-Item $helmZip -Force

            $env:Path += ";$binDir"
            Write-OK "helm 下载成功: $helmPath"
            return $true
        } catch {
            Write-Warn "镜像下载失败: $($_.Exception.Message)"
        }
    }

    Write-Err "helm 下载失败"
    Write-Host "  手动下载指引: https://helm.sh/docs/intro/install/" -ForegroundColor Yellow
    return $false
}

# ===== Step 3: 创建 kind 集群 =====
function New-KindCluster {
    Write-Step "Step 3: 创建 kind 集群 ($ClusterName)"

    # 检查集群是否已存在
    $existingClusters = kind get clusters 2>&1
    if ($existingClusters -match $ClusterName) {
        Write-OK "集群 $ClusterName 已存在，跳过创建"
        return $true
    }

    # 生成集群配置（启用 NetworkPolicy 支持的 CNI）
    $configFile = [System.IO.Path]::GetTempFileName() + ".yaml"
    $config = @"
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: $ClusterName
nodes:
- role: control-plane
  image: kindest/node:$K8sVersion
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        node-labels: "ingress-ready=true"
networking:
  disableDefaultCNI: false
  kubeProxyMode: "iptables"
"@
    Set-Content -Path $configFile -Value $config -Encoding UTF8

    Write-Host "  创建集群（K8s $K8sVersion）..." -ForegroundColor Gray
    $createOut = & kind create cluster --config $configFile --name $ClusterName 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "集群创建成功"
        # 设置 kubectl context
        kubectl config use-context "kind-$ClusterName" 2>&1 | Out-Null
        Remove-Item $configFile -Force
        return $true
    } else {
        Write-Err "集群创建失败"
        Write-Host $createOut -ForegroundColor Gray
        Remove-Item $configFile -Force
        return $false
    }
}

# ===== Step 4: 构建并加载镜像 =====
function Build-AndLoadImage {
    Write-Step "Step 4: 构建并加载 ops-reporter 镜像"

    if ($SkipImageBuild) {
        Write-OK "跳过镜像构建（-SkipImageBuild）"
    } else {
        $dockerfilePath = "docker/ops-reporter/Dockerfile"
        if (-not (Test-Path $dockerfilePath)) {
            Write-Err "Dockerfile 不存在: $dockerfilePath"
            return $false
        }

        Write-Host "  构建镜像 tlm-ops-reporter:$ImageTag..." -ForegroundColor Gray
        $buildOut = & docker build -t "tlm-ops-reporter:$ImageTag" -f $dockerfilePath . 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Err "镜像构建失败"
            Write-Host ($buildOut | Out-String) -ForegroundColor Gray
            return $false
        }
        Write-OK "镜像构建成功"
    }

    # 加载镜像到 kind
    Write-Host "  加载镜像到 kind 集群..." -ForegroundColor Gray
    $loadOut = & kind load docker-image "tlm-ops-reporter:$ImageTag" --name $ClusterName 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "镜像加载成功"
        return $true
    } else {
        Write-Err "镜像加载失败"
        Write-Host ($loadOut | Out-String) -ForegroundColor Gray
        return $false
    }
}

# ===== Step 5: 创建测试 namespace + 日志 PVC =====
function New-TestNamespace {
    Write-Step "Step 5: 创建测试 namespace + 日志 PVC"

    # namespace
    kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - 2>&1 | Out-Null
    Write-OK "namespace monitoring 已就绪"

    # 日志 PVC（模拟生产前置条件）
    $pvcYaml = @"
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: tlm-app-logs
  namespace: monitoring
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
"@
    $pvcFile = [System.IO.Path]::GetTempFileName() + ".yaml"
    Set-Content -Path $pvcFile -Value $pvcYaml -Encoding UTF8
    kubectl apply -f $pvcFile 2>&1 | Out-Null
    Remove-Item $pvcFile -Force

    # 等待 PVC Bound
    Write-Host "  等待 PVC Bound..." -ForegroundColor Gray
    $bound = $false
    for ($i = 0; $i -lt 30; $i++) {
        $status = kubectl get pvc tlm-app-logs -n monitoring -o jsonpath="{.status.phase}" 2>$null
        if ($status -eq "Bound") { $bound = $true; break }
        Start-Sleep -Seconds 2
    }
    if ($bound) {
        Write-OK "日志 PVC tlm-app-logs 已 Bound"
    } else {
        Write-Warn "PVC 未 Bound（可能需要 StorageClass），P1-4 将 FAIL"
    }

    # 创建 regcred secret（模拟生产镜像仓库凭证）
    kubectl create secret docker-registry regcred `
        --docker-server=registry.local `
        --docker-username=test `
        --docker-password=test123 `
        -n monitoring --dry-run=client -o yaml | kubectl apply -f - 2>&1 | Out-Null
    Write-OK "regcred secret 已创建（测试用）"

    return $true
}

# ===== Step 6: 部署 Prometheus Operator（可选）=====
function Install-PrometheusOperator {
    Write-Step "Step 6: 部署 Prometheus Operator（可选）"

    if ($SkipPrometheus) {
        Write-OK "跳过 Prometheus Operator 部署（-SkipPrometheus）"
        return $true
    }

    # 添加 prometheus-community 仓库
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>&1 | Out-Null
    helm repo update 2>&1 | Out-Null

    # 部署 kube-prometheus-stack（精简版）
    Write-Host "  部署 kube-prometheus-stack（可能需要 2-3 分钟）..." -ForegroundColor Gray
    $installOut = & helm install prometheus prometheus-community/kube-prometheus-stack `
        -n monitoring `
        --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false `
        --set alertmanager.enabled=false `
        --set grafana.enabled=false `
        --timeout 5m 2>&1

    if ($LASTEXITCODE -eq 0) {
        Write-OK "Prometheus Operator 部署成功"
        return $true
    } else {
        Write-Warn "Prometheus Operator 部署失败（P7 组将 FAIL）"
        Write-Host ($installOut | Out-String) -ForegroundColor Gray
        return $false
    }
}

# ===== 主流程 =====
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  构造 Kind 测试环境" -ForegroundColor Cyan
Write-Host "  集群: $ClusterName | K8s: $K8sVersion" -ForegroundColor Cyan
Write-Host "  镜像: tlm-ops-reporter:$ImageTag" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# 前置检查
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err "docker 未安装，无法继续"
    exit 1
}

# Step 1-2: 安装 kind + helm
$kindOk = Install-KindCli
if (-not $kindOk) { Write-Host "`n[FATAL] kind 安装失败，请手动安装后重试" -ForegroundColor Red; exit 1 }

$helmOk = Install-HelmCli
if (-not $helmOk) { Write-Host "`n[FATAL] helm 安装失败，请手动安装后重试" -ForegroundColor Red; exit 1 }

# Step 3: 创建集群
$clusterOk = New-KindCluster
if (-not $clusterOk) { Write-Host "`n[FATAL] 集群创建失败" -ForegroundColor Red; exit 1 }

# Step 4: 构建并加载镜像
$imageOk = Build-AndLoadImage

# Step 5: namespace + PVC
$nsOk = New-TestNamespace

# Step 6: Prometheus Operator（可选）
$promOk = Install-PrometheusOperator

# ===== 输出就绪信息 =====
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  环境就绪" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green

Write-Host "`n集群信息:" -ForegroundColor Cyan
kubectl cluster-info 2>&1 | Select-Object -First 2

Write-Host "`n节点:" -ForegroundColor Cyan
kubectl get nodes

Write-Host "`nPVC:" -ForegroundColor Cyan
kubectl get pvc -n monitoring

Write-Host "`n运行验收脚本命令:" -ForegroundColor Yellow
Write-Host "  .\scripts\verify_production_deployment.ps1 -LogsPVC tlm-app-logs -ImageTag $ImageTag" -ForegroundColor White
Write-Host ""
Write-Host "  如需跳过升级回滚（避免影响集群）:" -ForegroundColor Yellow
Write-Host "  .\scripts\verify_production_deployment.ps1 -LogsPVC tlm-app-logs -SkipGroupP8" -ForegroundColor White
Write-Host ""
Write-Host "  如 Prometheus 未部署:" -ForegroundColor Yellow
Write-Host "  .\scripts\verify_production_deployment.ps1 -LogsPVC tlm-app-logs -SkipGroupP7 -SkipGroupP8" -ForegroundColor White

Write-Host "`n清理命令:" -ForegroundColor Yellow
Write-Host "  kind delete cluster --name $ClusterName" -ForegroundColor White

exit 0
