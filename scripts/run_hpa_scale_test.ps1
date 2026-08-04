<#
.SYNOPSIS
    HPA 扩容压测验证 — 验证 3→15 副本能否在 60s 内完成

.DESCRIPTION
    【不易】验证目标: 流量突增时 3→15 副本 ≤60s（新 HPA: CPU 5%, scaleUp 6 Pods/30s）
    【变易】双组件架构: 集群内 Pod 生成流量 + 主机端每 5s 监控 HPA 状态
    【简易】自动创建/清理临时 Pod，输出时间线 + pass/fail 判定

    流程:
      1. 确认 HPA 初始状态（3 副本）
      2. 创建临时压测 Pod
      3. 启动流量突增（100 VU → 1000 QPS）
      4. 每 5s 记录: 时间戳 | 副本数 | CPU% | 事件
      5. 120s 后停止，分析时间线
      6. 判定: 15 副本是否在 60s 内达成
      7. 清理临时 Pod

.PARAMETER Namespace
    命名空间（默认 production）

.PARAMETER TargetReplicas
    目标副本数（默认 15）

.PARAMETER ScaleTimeoutS
    扩容超时阈值（默认 60s）

.EXAMPLE
    .\scripts\run_hpa_scale_test.ps1
    .\scripts\run_hpa_scale_test.ps1 -TargetReplicas 10 -ScaleTimeoutS 90
#>
param(
    [string]$Namespace = "production",
    [int]$TargetReplicas = 15,
    [int]$ScaleTimeoutS = 60,
    [int]$TotalDurationS = 120
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Stage($msg) { Write-Output ""; Write-Output "════ $msg ════" }
function Write-Ok($msg)    { Write-Output "  [OK] $msg" }
function Write-Warn($msg)  { Write-Output "  [WARN] $msg" }
function Write-Fail($msg)  { Write-Output "  [FAIL] $msg" }
function Write-Info($msg)  { Write-Output "  [INFO] $msg" }

Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  HPA 扩容压测验证"
Write-Output "  目标: $TargetReplicas 副本 ≤ ${ScaleTimeoutS}s"
Write-Output "  HPA: CPU 5% | scaleUp 6 Pods/30s | maxReplicas 15"
Write-Output "════════════════════════════════════════════════════════════════"

# ═══════════════════════════════════════════════════════════════════
#  1. 前置检查
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 1: 前置检查"

# 检查 metrics-server
$msPod = kubectl get pod -l k8s-app=metrics-server -n kube-system --no-headers 2>&1
if ($LASTEXITCODE -ne 0 -or $msPod -notmatch "Running") {
    Write-Fail "metrics-server 未运行 — HPA 无法获取 CPU 指标"
    Write-Info "部署: kubectl apply -f deploy/k8s/metrics-server.yaml"
    exit 1
}
Write-Ok "metrics-server 运行中"

# 检查 HPA 初始状态
$hpaYaml = kubectl get hpa skill-retrieval-hpa -n $Namespace -o jsonpath='{.spec.maxReplicas}' 2>&1
if ($hpaYaml -ne "$TargetReplicas") {
    Write-Warn "HPA maxReplicas=$hpaYaml (期望 $TargetReplicas) — 继续测试"
}

# 等待 HPA 回到稳态（3 副本）
Write-Info "等待 HPA 回到稳态（3 副本）..."
$stableCount = 0
for ($i = 0; $i -lt 12; $i++) {
    $replicas = kubectl get deploy skill-retrieval-service -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>&1
    if ($replicas -eq "3") {
        $stableCount++
        if ($stableCount -ge 2) { break }
    } else {
        $stableCount = 0
        Write-Info "  当前副本数: $replicas，等待缩容..."
    }
    Start-Sleep -Seconds 10
}

$currentReplicas = kubectl get deploy skill-retrieval-service -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>&1
Write-Ok "初始状态: $currentReplicas 副本就绪"

if ([int]$currentReplicas -ne 3) {
    Write-Warn "初始副本数 $currentReplicas ≠ 3，扩容起点非标准 — 继续测试"
}

# ═══════════════════════════════════════════════════════════════════
#  2. 创建临时压测 Pod
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 2: 创建临时压测 Pod"

$podName = "hpa-scale-tester"
kubectl delete pod $podName -n $Namespace --force --grace-period=0 2>&1 | Out-Null
Start-Sleep -Seconds 2

kubectl run $podName -n $Namespace --image=skill-retrieval:local --restart=Never --command -- sleep 300 2>&1 | Out-Null
kubectl wait pod/$podName -n $Namespace --for=condition=Ready --timeout=60s 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Fail "压测 Pod 创建失败"
    exit 1
}
Write-Ok "压测 Pod 就绪: $podName"

# 复制压测脚本
$scriptPath = Join-Path $scriptDir "hpa_scale_test.py"
Get-Content $scriptPath -Raw | kubectl exec -i -n $Namespace $podName -- sh -c 'cat > /tmp/hpa_scale_test.py'
Write-Ok "压测脚本已复制"

# ═══════════════════════════════════════════════════════════════════
#  3. 启动流量突增 + HPA 监控
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 3: 启动流量突增 + HPA 监控（${TotalDurationS}s）"
Write-Info "流量: 100 VU → 1000 QPS | 监控间隔: 5s"
Write-Output ""

# 后台启动流量生成
$trafficJob = Start-Job -ScriptBlock {
    param($ns, $pod)
    kubectl exec -n $ns $pod -- python -u /tmp/hpa_scale_test.py 2>&1
} -ArgumentList $Namespace, $podName

$startTime = Get-Date
$timeline = @()
$scaledToTarget = $false
$scaleToTargetTime = $null

Write-Output "  时间(s)  副本数  就绪数  CPU(%)  事件"
Write-Output "  ───────  ──────  ──────  ──────  ─────────────────────"

for ($t = 0; $t -le $TotalDurationS; $t += 5) {
    $elapsed = ((Get-Date) - $startTime).TotalSeconds

    # 获取副本数
    $replicas = kubectl get deploy skill-retrieval-service -n $Namespace -o jsonpath='{.status.replicas}' 2>&1
    $readyReplicas = kubectl get deploy skill-retrieval-service -n $Namespace -o jsonpath='{.status.readyReplicas}' 2>&1
    if ([string]::IsNullOrEmpty($readyReplicas)) { $readyReplicas = "0" }

    # 获取 CPU 使用率（从 HPA status）
    $hpaTargets = kubectl get hpa skill-retrieval-hpa -n $Namespace -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>&1
    if ([string]::IsNullOrEmpty($hpaTargets)) { $hpaTargets = "N/A" }

    # 获取 Pod 列表（检测新 Pod 创建）
    $podList = kubectl get pods -n $Namespace -l app=skill-retrieval-service --no-headers 2>&1
    $podCount = ($podList | Measure-Object).Count

    # 事件检测
    $event = ""
    if ($t -eq 0) {
        $event = "▶ 流量突增开始"
    } elseif ([int]$replicas -gt [int]$prevReplicas) {
        $event = "↑ 扩容: $prevReplicas→$replicas"
    } elseif ([int]$replicas -lt [int]$prevReplicas) {
        $event = "↓ 缩容: $prevReplicas→$replicas"
    } elseif ([int]$readyReplicas -gt [int]$prevReady) {
        $event = "✓ 新 Pod 就绪: $prevReady→$readyReplicas"
    }

    $entry = [PSCustomObject]@{
        Time = [math]::Round($elapsed, 1)
        Replicas = $replicas
        Ready = $readyReplicas
        CPU = $hpaTargets
        Event = $event
    }
    $timeline += $entry

    Write-Output ("  {0,7:F1}s  {1,6}  {2,6}  {3,5}%  {4}" -f $elapsed, $replicas, $readyReplicas, $hpaTargets, $event)

    # 检测是否达到目标副本数
    if ([int]$readyReplicas -ge $TargetReplicas -and -not $scaledToTarget) {
        $scaledToTarget = $true
        $scaleToTargetTime = $elapsed
        Write-Output "  ★★★ 达到 $TargetReplicas 副本！耗时 ${elapsed}s ★★★" -ForegroundColor Green
    }

    $prevReplicas = $replicas
    $prevReady = $readyReplicas

    if ($t -lt $TotalDurationS) {
        Start-Sleep -Seconds 5
    }
}

# 停止流量生成
Stop-Job $trafficJob -ErrorAction SilentlyContinue
$trafficOutput = Receive-Job $trafficJob -ErrorAction SilentlyContinue
Remove-Job $trafficJob -ErrorAction SilentlyContinue

Write-Output ""
Write-Info "流量生成器输出（最后 10 行）:"
$trafficOutput | Select-Object -Last 10 | ForEach-Object { Write-Output "  $_" }

# ═══════════════════════════════════════════════════════════════════
#  4. 分析与判定
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 4: 分析与判定"

# 最大副本数
$maxReplicas = ($timeline | ForEach-Object { [int]$_.Ready } | Measure-Object -Maximum).Maximum
$timeToMax = ($timeline | Where-Object { [int]$_.Ready -eq $maxReplicas } | Select-Object -First 1).Time

Write-Output ""
Write-Output "  ── 扩容时间线摘要 ──"
Write-Output "  初始副本数:     3"
Write-Output "  最大副本数:     $maxReplicas"
Write-Output "  达到最大耗时:   ${timeToMax}s"
if ($scaledToTarget) {
    Write-Output "  达到 $TargetReplicas 副本耗时: ${scaleToTargetTime}s"
} else {
    Write-Output "  达到 $TargetReplicas 副本: 未达成"
}

# 扩容步骤
Write-Output ""
Write-Output "  ── 扩容步骤 ──"
$prevR = 3
foreach ($entry in $timeline) {
    $r = [int]$entry.Ready
    if ($r -gt $prevR) {
        Write-Output ("    {0,6:F1}s: {1} → {2} (+{3})" -f $entry.Time, $prevR, $r, ($r - $prevR))
        $prevR = $r
    }
}

# 判定
Write-Output ""
Write-Output "  ── 判定 ──"
$pass = $false
if ($scaledToTarget -and $scaleToTargetTime -le $ScaleTimeoutS) {
    Write-Output "  ✓ PASS: ${scaleToTargetTime}s 内完成 3→${TargetReplicas} 扩容（≤${ScaleTimeoutS}s）"
    $pass = $true
} elseif ($scaledToTarget) {
    Write-Output "  ✗ FAIL: 扩容完成但耗时 ${scaleToTargetTime}s > ${ScaleTimeoutS}s"
} else {
    Write-Output "  ✗ FAIL: ${TotalDurationS}s 内未达到 $TargetReplicas 副本（最大 $maxReplicas）"
    if ($maxReplicas -lt $TargetReplicas) {
        Write-Output "    可能原因:"
        Write-Output "    1. CPU 未达到 5% 阈值（aiohttp I/O 密集，CPU 开销低）"
        Write-Output "    2. 新 Pod 启动慢（镜像拉取/探针等待）"
        Write-Output "    3. HPA 评估间隔（默认 15s）+ scaleUp 策略限制"
        Write-Output "    4. 单节点资源限制（maxReplicas=15 需 ~1.5 CPU）"
    }
}

# CPU 分析
Write-Output ""
Write-Output "  ── CPU 使用率分析 ──"
$cpuValues = $timeline | Where-Object { $_.CPU -ne "N/A" } | ForEach-Object { [int]$_.CPU }
if ($cpuValues) {
    $maxCpu = ($cpuValues | Measure-Object -Maximum).Maximum
    $avgCpu = [math]::Round(($cpuValues | Measure-Object -Average).Average, 1)
    Write-Output "  CPU 峰值: ${maxCpu}% | 均值: ${avgCpu}% | HPA 阈值: 5%"
    if ($maxCpu -lt 5) {
        Write-Output "  [WARN] CPU 峰值 ${maxCpu}% < 5% 阈值 — HPA 可能未触发扩容"
        Write-Output "  建议: 降低 CPU request 或改用自定义指标（QPS/P99）驱动 HPA"
    }
} else {
    Write-Output "  CPU 数据不可用"
}

# ═══════════════════════════════════════════════════════════════════
#  5. 清理
# ═══════════════════════════════════════════════════════════════════
Write-Stage "阶段 5: 清理"
kubectl delete pod $podName -n $Namespace --force --grace-period=0 2>&1 | Out-Null
Write-Ok "临时 Pod 已清理"

Write-Output ""
Write-Output "════════════════════════════════════════════════════════════════"
if ($pass) {
    Write-Output "  HPA 扩容验证: PASS ✓"
} else {
    Write-Output "  HPA 扩容验证: FAIL ✗"
}
Write-Output "════════════════════════════════════════════════════════════════"
