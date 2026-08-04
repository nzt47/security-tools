<#
.SYNOPSIS
    停止 mock_skill_service 并清理相关临时数据

.DESCRIPTION
    【不易】只清理 mock 服务产生的临时数据，绝不触碰项目代码/配置
    【变易】提供 -KeepReport 选项保留压测报告 baseline_report.json
    【简易】按端口 8080/9091 定位进程，幂等可重复执行

.PARAMETER KeepReport
    保留 baseline_report.json（默认清理）

.EXAMPLE
    .\scripts\stop_mock_service.ps1
    .\scripts\stop_mock_service.ps1 -KeepReport
#>
param(
    [switch]$KeepReport
)

$ErrorActionPreference = "SilentlyContinue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  停止 mock_skill_service 并清理临时数据"
Write-Output "════════════════════════════════════════════════════════════════"

# ═══════════════════════════════════════════════════════════════════
#  1. 停止 mock 服务进程（按监听端口 8080/9091 定位）
# ═══════════════════════════════════════════════════════════════════
Write-Output ""
Write-Output "── 1. 停止 mock 服务进程 ──"

$ports = @(8080, 9091)
$stoppedPids = @()

foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        if ($procId -in $stoppedPids) { continue }  # 同一进程监听多端口，只停一次
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$procId").CommandLine
            # 【不易】只停止 mock_skill_service 进程，避免误杀其他占用 8080 的服务
            if ($cmdLine -match "mock_skill_service") {
                Write-Output "  [STOP] PID=$procId ($($proc.ProcessName)) 端口 $port"
                Write-Output "         cmd: $cmdLine"
                Stop-Process -Id $procId -Force
                $stoppedPids += $procId
            } else {
                Write-Output "  [SKIP] PID=$procId 占用端口 $port 但非 mock_skill_service（cmd: $cmdLine）"
            }
        }
    }
}

if ($stoppedPids.Count -eq 0) {
    Write-Output "  [INFO] 未发现运行中的 mock_skill_service（端口 8080/9091 未被占用）"
} else {
    # 等待端口释放
    Start-Sleep -Milliseconds 500
    Write-Output "  [OK] 已停止 $($stoppedPids.Count) 个进程"
}

# 验证端口已释放
Write-Output ""
Write-Output "── 1.1 端口释放验证 ──"
$stillInUse = $false
foreach ($port in $ports) {
    $check = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($check) {
        Write-Output "  [WARN] 端口 $port 仍被占用（PID=$($check.OwningProcess -join ','))）"
        $stillInUse = $true
    } else {
        Write-Output "  [OK] 端口 $port 已释放"
    }
}

# ═══════════════════════════════════════════════════════════════════
#  2. 清理临时数据
# ═══════════════════════════════════════════════════════════════════
Write-Output ""
Write-Output "── 2. 清理临时数据 ──"

$cleanupItems = @()

# 2.1 baseline_report.json（Python 压测报告）
$report = Join-Path $scriptDir "baseline_report.json"
if (Test-Path $report) {
    if ($KeepReport) {
        Write-Output "  [KEEP] 保留压测报告: $report (-KeepReport)"
    } else {
        $cleanupItems += $report
    }
}

# 2.2 __pycache__（mock 服务运行产生的字节码缓存）
$pycache = Join-Path $scriptDir "__pycache__"
if (Test-Path $pycache) {
    $cleanupItems += $pycache
}

# 2.3 .pyc 文件（脚本目录下的零散字节码）
$pycFiles = Get-ChildItem -Path $scriptDir -Filter "*.pyc" -File -ErrorAction SilentlyContinue
if ($pycFiles) {
    $cleanupItems += $pycFiles.FullName
}

# 执行清理
foreach ($item in $cleanupItems) {
    try {
        Remove-Item -Path $item -Recurse -Force -ErrorAction Stop
        Write-Output "  [CLEAN] 已清理: $item"
    } catch {
        Write-Output "  [FAIL] 清理失败: $item — $($_.Exception.Message)"
    }
}

if ($cleanupItems.Count -eq 0) {
    Write-Output "  [INFO] 无临时数据需要清理"
}

# ═══════════════════════════════════════════════════════════════════
#  3. 汇总
# ═══════════════════════════════════════════════════════════════════
Write-Output ""
Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  清理完成"
Write-Output "════════════════════════════════════════════════════════════════"
Write-Output "  停止进程:   $($stoppedPids.Count) 个"
Write-Output "  清理项目:   $($cleanupItems.Count) 个"
Write-Output "  端口状态:   $(if ($stillInUse) { '部分仍被占用' } else { '全部已释放' })"
Write-Output "  报告保留:   $(if ($KeepReport) { '是' } else { '否' })"
Write-Output ""
Write-Output "  注: 后台 job 日志（C:\Windows\TEMP\trae-agent-toolhost\jobs\）由系统管理"
Write-Output "      不属于 mock 服务临时数据，未清理。如需手动清理："
Write-Output "      Get-ChildItem 'C:\Windows\TEMP\trae-agent-toolhost\jobs' -Recurse -Filter output.log | Where Content -match 'mock_skill' | Remove-Item"
Write-Output "════════════════════════════════════════════════════════════════"
