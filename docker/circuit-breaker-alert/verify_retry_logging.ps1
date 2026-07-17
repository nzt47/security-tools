<#
.SYNOPSIS
    验证 Invoke-WithRetry 日志增强效果

.DESCRIPTION
    模拟 Docker 构建网络超时场景，观察增强后的日志输出：
    - 指数退避序列（base * attempt，受 max 限制）
    - 预计下次重试时间计算（now + backoff）
    - 累计退避统计
    - 本次耗时 / 总耗时

    验证用例：
    1. docker build 前 2 次网络超时，第 3 次成功
    2. docker push 持续失败（4 次重试，验证退避序列）
    3. 退避上限验证（base=2, max=5，验证 2s/4s/5s/5s）

    使用短退避参数（base=1, max=5）避免长时间等待。

.EXAMPLE
    .\verify_retry_logging.ps1
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ── 日志函数（与 build_and_push.ps1 一致）──────────────────────
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $color = switch ($Level) {
        "INFO"    { "Cyan" }
        "SUCCESS" { "Green" }
        "WARN"    { "Yellow" }
        "ERROR"   { "Red" }
        default   { "White" }
    }
    Write-Host "[$timestamp] [$Level] $Message" -ForegroundColor $color
}

# ── Invoke-WithRetry（与 build_and_push.ps1 同步）──────────────
function Invoke-WithRetry {
    param(
        [scriptblock]$ScriptBlock,
        [string]$Description,
        [int]$MaxRetries = 3,
        [int]$BackoffBase = 5,
        [int]$BackoffMax = 60,
        [string]$ExitCodeOnFailure = 1
    )

    # [TLM-L1] 重试执行器 - 网络不稳定场景的统一重试入口
    # 详细日志：每次尝试耗时、累计退避、预计下次重试时间，便于排查网络超时
    $startTime = Get-Date
    $totalBackoff = 0
    $attempt = 0
    Write-Log "重试任务启动: $Description (最大=${MaxRetries}, 退避=${BackoffBase}s*${BackoffMax}max)"

    while ($attempt -lt $MaxRetries) {
        $attempt++
        $backoff = [Math]::Min($attempt * $BackoffBase, $BackoffMax)
        $attemptStart = Get-Date

        Write-Log "尝试 $attempt/${MaxRetries}: $Description"

        try {
            & $ScriptBlock
            $exitCode = $LASTEXITCODE
            $attemptSecs = [Math]::Round(((Get-Date) - $attemptStart).TotalSeconds, 2)
            if ($exitCode -eq 0) {
                $totalSecs = [Math]::Round(((Get-Date) - $startTime).TotalSeconds, 2)
                Write-Log "成功: $Description (第 $attempt 次, 本次 ${attemptSecs}s, 总耗时 ${totalSecs}s, 累计退避 ${totalBackoff}s)" "SUCCESS"
                return $true
            } else {
                throw "退出码 $exitCode"
            }
        } catch {
            $errorMsg = $_.Exception.Message
            $attemptSecs = [Math]::Round(((Get-Date) - $attemptStart).TotalSeconds, 2)
            if ($attempt -lt $MaxRetries) {
                $nextRetryAt = (Get-Date).AddSeconds($backoff).ToString("HH:mm:ss")
                Write-Log "失败: $Description - $errorMsg (本次 ${attemptSecs}s)" "WARN"
                Write-Log "  退避 ${backoff}s 后重试 (累计将达 $($totalBackoff + $backoff)s, 预计 $nextRetryAt)" "WARN"
                Start-Sleep -Seconds $backoff
                $totalBackoff += $backoff
            } else {
                $totalSecs = [Math]::Round(((Get-Date) - $startTime).TotalSeconds, 2)
                Write-Log "最终失败: $Description - $errorMsg (已重试 $MaxRetries 次, 总耗时 ${totalSecs}s, 累计退避 ${totalBackoff}s)" "ERROR"
                return $false
            }
        }
    }
    return $false
}

# ── 辅助函数：打印验证断言 ────────────────────────────────────
function Assert-Label {
    param([string]$Label, [string]$Expected)
    Write-Host "  [验证点] $Label = $Expected" -ForegroundColor DarkGray
}

# ══════════════════════════════════════════════════════════════
# 场景 1: docker build 前 2 次网络超时，第 3 次成功
# 期望退避序列: 1s, 2s（base=1, max=5）
# ══════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "════════ 场景 1: docker build 前 2 次超时，第 3 次成功 ════════" -ForegroundColor White
Assert-Label "退避基数" "1s"
Assert-Label "退避上限" "5s"
Assert-Label "期望退避序列" "1s, 2s"
Assert-Label "预计下次重试时间" "now + backoff"

$script:callCount = 0
$result1 = Invoke-WithRetry -ScriptBlock {
    $script:callCount++
    Start-Sleep -Milliseconds 200  # 模拟 docker build 耗时
    if ($script:callCount -le 2) {
        $global:LASTEXITCODE = 1
    } else {
        $global:LASTEXITCODE = 0
    }
} -Description "docker build registry.example.com/circuit-breaker-alert:1.0" -MaxRetries 3 -BackoffBase 1 -BackoffMax 5

Write-Host ""
Write-Host ("  场景 1 结果: {0} (期望: True)" -f $result1) -ForegroundColor $(if ($result1) { "Green" } else { "Red" })

# ══════════════════════════════════════════════════════════════
# 场景 2: docker push 持续失败（4 次重试）
# 期望退避序列: 1s, 2s, 3s（base=1, max=5）
# ══════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "════════ 场景 2: docker push 持续失败（4 次重试）════════" -ForegroundColor White
Assert-Label "退避基数" "1s"
Assert-Label "退避上限" "5s"
Assert-Label "期望退避序列" "1s, 2s, 3s"
Assert-Label "累计退避" "1+2+3=6s"

$result2 = Invoke-WithRetry -ScriptBlock {
    Start-Sleep -Milliseconds 150  # 模拟 docker push 耗时
    $global:LASTEXITCODE = 1
} -Description "docker push registry.example.com/circuit-breaker-alert:1.0" -MaxRetries 4 -BackoffBase 1 -BackoffMax 5

Write-Host ""
Write-Host ("  场景 2 结果: {0} (期望: False)" -f $result2) -ForegroundColor $(if (-not $result2) { "Green" } else { "Red" })

# ══════════════════════════════════════════════════════════════
# 场景 3: 退避上限验证（base=2, max=5）
# 期望退避序列: 2s, 4s, 5s, 5s（第 3 次起被 max 限制）
# ══════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "════════ 场景 3: 退避上限验证（base=2, max=5）════════" -ForegroundColor White
Assert-Label "退避基数" "2s"
Assert-Label "退避上限" "5s"
Assert-Label "期望退避序列" "2s, 4s, 5s, 5s"
Assert-Label "退避上限生效" "第 3 次起 = 5s（2*3=6 → min(6,5)=5）"

$result3 = Invoke-WithRetry -ScriptBlock {
    Start-Sleep -Milliseconds 100
    $global:LASTEXITCODE = 1
} -Description "docker push (上限验证)" -MaxRetries 5 -BackoffBase 2 -BackoffMax 5

Write-Host ""
Write-Host ("  场景 3 结果: {0} (期望: False)" -f $result3) -ForegroundColor $(if (-not $result3) { "Green" } else { "Red" })

# ══════════════════════════════════════════════════════════════
# 总结
# ══════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "════════ 验证总结 ════════" -ForegroundColor White
Write-Host "  请人工核对日志中的以下字段："
Write-Host "    1. 退避时间 = min(attempt * base, max)"
Write-Host "    2. 预计下次重试时间 = 当前时间 + 退避秒数"
Write-Host "    3. 累计退避 = 之前所有退避之和"
Write-Host "    4. 本次耗时 / 总耗时 统计正确"
Write-Host ""
Write-Host "  场景 1 (build 成功): $result1"
Write-Host "  场景 2 (push 失败):  $result2"
Write-Host "  场景 3 (上限验证):   $result3"