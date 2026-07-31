<#
.SYNOPSIS
    一键执行完整压测验证流程

.DESCRIPTION
    【不易】串联已有脚本，每步含检查点，失败可定位
    【变易】参数化: 场景选择、是否启动集群、超时
    【简易】分阶段输出，含报告汇总

    流程:
      1. 启动集群 + 部署资源（可选，-SkipCluster 跳过）
      2. 预检（k8s_preflight_check.sh）
      3. 等待指标就绪（30s 采集缓冲）
      4. k6 baseline 基线压测
      5. k6 burst 突发流量压测（验证 HPA 扩容）
      6. 可选: k6 stress 压力测试
      7. 报告汇总

.PARAMETER Namespace
    命名空间（默认 production）

.PARAMETER SkipCluster
    跳过集群启动+部署（集群已就绪时用）

.PARAMETER SkipStress
    跳过 stress 场景（默认跳过，-RunStress 启用）

.PARAMETER RunStress
    启用 stress 场景

.PARAMETER Endpoint
    压测端点（默认用集群内 Service DNS）

.EXAMPLE
    .\scripts\run_full_loadtest.ps1                              # 完整流程
    .\scripts\run_full_loadtest.ps1 -SkipCluster                 # 集群已就绪，仅压测
    .\scripts\run_full_loadtest.ps1 -RunStress                   # 含压力测试
    .\scripts\run_full_loadtest.ps1 -Endpoint http://localhost:8080/match  # 自定义端点
#>
param(
    [string]$Namespace = "production",
    [switch]$SkipCluster,
    [switch]$SkipStress,
    [switch]$RunStress,
    [string]$Endpoint = "http://skill-retrieval-service.$Namespace.svc.cluster.local:8080/match",
    [string]$PrometheusUrl = "http://prometheus-server.monitoring.svc.cluster.local:9090/api/v1/write"
)

$ErrorActionPreference = "SilentlyContinue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$reports = @()

function Write-Stage($msg) { Write-Output ""; Write-Output "════ $msg ════" }
function Write-Ok($msg)    { Write-Output "  [OK] $msg" }
function Write-Warn($msg)  { Write-Output "  [WARN] $msg" }
function Write-Fail($msg)  { Write-Output "  [FAIL] $msg" }
function Write-Info($msg)  { Write-Output "  [INFO] $msg" }

Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  完整压测验证流程"
Write-Output "  Namespace: $Namespace"
Write-Output "  Endpoint:  $Endpoint"
Write-Output "  SkipCluster: $SkipCluster  RunStress: $RunStress"
Write-Output "════════════════════════════════════════════════════════════════"

# 刷新 PATH（choco 安装的 k6 可能未在当前会话 PATH）
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# ═══════════════════════════════════════════════════════════════════
#  1. 启动集群 + 部署资源
# ═══════════════════════════════════════════════════════════════════
if (-not $SkipCluster) {
    Write-Stage "阶段 1: 启动集群 + 部署资源"
    $startScript = Join-Path $scriptDir "start_k8s_cluster.ps1"
    if (Test-Path $startScript) {
        & $startScript -DeployResources -Namespace $Namespace
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "集群启动或部署失败"
            exit 1
        }
        Write-Ok "集群就绪 + 资源部署完成"
    } else {
        Write-Fail "启动脚本不存在: $startScript"
        exit 1
    }
} else {
    Write-Stage "阶段 1: 跳过集群启动（-SkipCluster）"
    Write-Info "假设集群已就绪且资源已部署"
}

# ═══════════════════════════════════════════════════════════════════
#  2. 预检
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 2: K8s 预检"
$preflightScript = Join-Path $scriptDir "k8s_preflight_check.sh"
if (Test-Path $preflightScript) {
    # 【不易】bash 不识别 Windows 反斜杠路径，必须转为正斜杠
    $preflightScriptUnix = $preflightScript -replace '\\', '/'
    bash $preflightScriptUnix $Namespace 2>&1 | ForEach-Object { Write-Output "  $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "预检未全部通过（可能有警告项）— 继续压测但关注失败项"
    } else {
        Write-Ok "预检通过"
    }
} else {
    Write-Warn "预检脚本不存在: $preflightScript"
}

# ═══════════════════════════════════════════════════════════════════
#  3. 等待指标就绪
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 3: 等待 Prometheus 指标就绪"
Write-Info "发起测试请求产生指标数据..."
# 【不易】用 kubectl exec 在现有 Pod 内发起请求，避免 curlimages/curl 镜像拉取问题
kubectl exec -n $Namespace deploy/skill-retrieval-service -- python -c "import urllib.request,json; req=urllib.request.Request('http://localhost:8080/match',data=json.dumps({'query':'PDF解析测试','top_k':5}).encode(),headers={'Content-Type':'application/json'}); urllib.request.urlopen(req).read()" 2>&1 | Out-Null

Write-Info "等待 30s（Prometheus 采集 + Adapter 缓存）..."
Start-Sleep -Seconds 30

# 验证自定义指标可达
Write-Info "验证 skill_match_latency_p99 自定义指标可达:"
$p99Raw = kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/$Namespace/pods/*/skill_match_latency_p99" 2>&1
if ($p99Raw -match "value") {
    Write-Ok "skill_match_latency_p99 指标可达"
} else {
    Write-Warn "skill_match_latency_p99 指标不可达 — HPA 自定义指标扩容可能不工作"
    Write-Info "可能原因: Adapter 未加载 rules / 无指标数据 / Adapter 未部署"
}

# ═══════════════════════════════════════════════════════════════════
#  4. k6 工具检查
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 4: k6 工具检查"
$k6 = Get-Command k6 -ErrorAction SilentlyContinue
if ($k6) {
    $k6Version = & k6 version 2>&1
    Write-Ok "k6 就绪: $k6Version"
} else {
    Write-Fail "k6 未安装 — 运行: choco install k6 -y"
    exit 1
}

$k6Script = Join-Path $scriptDir "k6\k8s_loadtest_skill_match.js"
if (-not (Test-Path $k6Script)) {
    Write-Fail "k6 脚本不存在: $k6Script"
    exit 1
}

# ═══════════════════════════════════════════════════════════════════
#  5. baseline 基线压测
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 5: baseline 基线压测（20 VU × 60s × 100 QPS）"
Write-Info "验证稳态容量，预期不触发 HPA 扩容"
Write-Info "Grafana 面板: skill-hpa-monitor（观察 P99/QPS/副本数）"
Write-Output ""

$k6Args = @(
    "run",
    "--out", "experimental-prometheus-rw=$PrometheusUrl",
    "-e", "ENDPOINT=$Endpoint",
    "-e", "NAMESPACE=$Namespace",
    "-e", "SCENARIO=baseline",
    "--tag", "test_run=full_loadtest",
    $k6Script
)
Write-Info "命令: k6 $($k6Args -join ' ')"
Write-Output ""

Push-Location $scriptDir
& k6 @k6Args 2>&1 | ForEach-Object { Write-Output "  $_" }
$baselineExit = $LASTEXITCODE
Pop-Location

if (Test-Path "k8s_baseline_report.json") {
    $reports += "k8s_baseline_report.json"
    Write-Ok "baseline 报告生成: k8s_baseline_report.json"
} else {
    Write-Warn "baseline 报告未生成"
}

# ═══════════════════════════════════════════════════════════════════
#  6. 等待 HPA 缩容回稳态
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 6: 等待 HPA 稳定（60s）"
Write-Info "baseline 不应触发扩容，等待 60s 确保 HPA 稳态"
Start-Sleep -Seconds 60

# ═══════════════════════════════════════════════════════════════════
#  7. burst 突发流量压测（验证 HPA 扩容）
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 7: burst 突发流量压测（验证 HPA 3→7 扩容）"
Write-Info "ramp-up 到 40 VU（200 QPS），观察 HPA 30s 内扩容"
Write-Info "Grafana 重点观察: HPA 副本数面板 + P99 延迟面板"
Write-Output ""

$k6BurstArgs = @(
    "run",
    "--out", "experimental-prometheus-rw=$PrometheusUrl",
    "-e", "ENDPOINT=$Endpoint",
    "-e", "NAMESPACE=$Namespace",
    "-e", "SCENARIO=burst",
    "--tag", "test_run=full_loadtest",
    $k6Script
)
Write-Info "命令: k6 $($k6BurstArgs -join ' ')"
Write-Output ""

Push-Location $scriptDir
& k6 @k6BurstArgs 2>&1 | ForEach-Object { Write-Output "  $_" }
Pop-Location

if (Test-Path "k8s_burst_report.json") {
    $reports += "k8s_burst_report.json"
    Write-Ok "burst 报告生成: k8s_burst_report.json"
} else {
    Write-Warn "burst 报告未生成"
}

# ═══════════════════════════════════════════════════════════════════
#  8. 可选: stress 压力测试
# ═══════════════════════════════════════════════════════════════════
if ($RunStress -and -not $SkipStress) {
    Write-Stage "阶段 8: stress 压力测试（50 VU × 120s）"
    Write-Info "验证 candidate_limit=200 降级方案"
    Write-Output ""

    # 等待 HPA 缩容
    Write-Info "等待 120s HPA 缩容..."
    Start-Sleep -Seconds 120

    $k6StressArgs = @(
        "run",
        "--out", "experimental-prometheus-rw=$PrometheusUrl",
        "-e", "ENDPOINT=$Endpoint",
        "-e", "NAMESPACE=$Namespace",
        "-e", "SCENARIO=stress",
        "--tag", "test_run=full_loadtest",
        $k6Script
    )
    Write-Info "命令: k6 $($k6StressArgs -join ' ')"
    Write-Output ""

    Push-Location $scriptDir
    & k6 @k6StressArgs 2>&1 | ForEach-Object { Write-Output "  $_" }
    Pop-Location

    if (Test-Path "k8s_stress_report.json") {
        $reports += "k8s_stress_report.json"
        Write-Ok "stress 报告生成: k8s_stress_report.json"
    }
}

# ═══════════════════════════════════════════════════════════════════
#  9. 报告汇总
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 9: 报告汇总"
Write-Output ""
Write-Output "  生成的报告:"
foreach ($r in $reports) {
    $fullPath = Join-Path $scriptDir $r
    if (Test-Path $fullPath) {
        $content = Get-Content $fullPath -Raw | ConvertFrom-Json
        Write-Output ""
        Write-Output "  ── $r ──"
        Write-Output "    场景:        $($content.config.scenario)"
        Write-Output "    总请求:      $($content.results.total_requests)"
        Write-Output "    实际 QPS:    $($content.results.actual_qps)"
        Write-Output "    P99 延迟:    $($content.results.latency_p99)ms"
        Write-Output "    P95 延迟:    $($content.results.latency_p95)ms"
        Write-Output "    错误率:      $([math]::Round($content.results.error_rate * 100, 2))%"
        Write-Output "    HPA 触发率:  $([math]::Round($content.results.hpa_threshold_exceeded_rate * 100, 2))%"
        $passStr = if ($content.thresholds_all_passed) { "PASS" } else { "FAIL" }
        Write-Output "    Thresholds:  $passStr"
    }
}

Write-Output ""
Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  压测验证流程完成"
Write-Output "════════════════════════════════════════════════════════════════"
Write-Output ""
Write-Output "  Grafana 面板查看: skill-hpa-monitor（uid=skill-hpa-monitor）"
Write-Output "  Prometheus 查询:  k6_http_reqs_total / skill_match_latency_ms_bucket"
Write-Output "  HPA 状态:         kubectl get hpa -n $Namespace -w"
Write-Output ""
Write-Output "  对比压测方案:     docs/HPA_COMPARISON_LOADTEST_PLAN.md"
Write-Output "════════════════════════════════════════════════════════════════"
