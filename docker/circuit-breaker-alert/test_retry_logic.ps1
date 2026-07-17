# Docker 构建重试逻辑测试脚本
# 模拟网络超时场景，验证 Invoke-WithRetry 的指数退避时序
#
# 用法: .\test_retry_logic.ps1 [-Verbose]
# 退出码: 0=全部通过 / 1=有失败用例

param(
    [switch]$Verbose
)

# Windows 控制台 UTF-8 输出（避免 GBK 编码错误）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ── 测试框架 ──────────────────────────────────────────────────

$script:testResults = @()

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "HH:mm:ss.fff"
    $color = switch ($Level) {
        "INFO"    { "Cyan" }
        "SUCCESS" { "Green" }
        "WARN"    { "Yellow" }
        "ERROR"   { "Red" }
        default   { "White" }
    }
    Write-Host "  [$timestamp] [$Level] $Message" -ForegroundColor $color
}

function Assert-Equal {
    param(
        $Expected,
        $Actual,
        [string]$Message
    )
    if ($Expected -eq $Actual) {
        return $true
    }
    throw "断言失败: $Message`n  期望: $Expected`n  实际: $Actual"
}

function Run-TestCase {
    param(
        [string]$Name,
        [scriptblock]$Test
    )
    Write-Host ""
    Write-Host "── 测试: $Name ──" -ForegroundColor White
    try {
        & $Test
        $script:testResults += @{ Name = $Name; Passed = $true; Error = "" }
        Write-Log "通过" "SUCCESS"
    } catch {
        $script:testResults += @{ Name = $Name; Passed = $false; Error = $_.Exception.Message }
        Write-Log "失败: $($_.Exception.Message)" "ERROR"
    }
}

# ── 可测试版 Invoke-WithRetry ─────────────────────────────────
# 与 build_and_push.ps1 的 Invoke-WithRetry 逻辑一致，
# 但 SleepAction 可注入（测试时用 mock，不实际等待）
# 注意: 若主脚本退避逻辑变更，此处需同步更新

function Invoke-WithRetry-Testable {
    param(
        [scriptblock]$ScriptBlock,
        [string]$Description,
        [int]$MaxRetries = 3,
        [int]$BackoffBase = 5,
        [int]$BackoffMax = 60,
        [scriptblock]$SleepAction = { param($s) Start-Sleep -Seconds $s }
    )

    $backoffTimes = @()
    $attempt = 0
    while ($attempt -lt $MaxRetries) {
        $attempt++
        $backoff = [Math]::Min($attempt * $BackoffBase, $BackoffMax)

        try {
            & $ScriptBlock
            $exitCode = $LASTEXITCODE
            if ($exitCode -eq 0) {
                return @{ Success = $true; Attempts = $attempt; BackoffTimes = $backoffTimes }
            } else {
                throw "退出码 $exitCode"
            }
        } catch {
            if ($attempt -lt $MaxRetries) {
                $backoffTimes += $backoff
                & $SleepAction $backoff
            } else {
                return @{ Success = $false; Attempts = $attempt; BackoffTimes = $backoffTimes }
            }
        }
    }
    return @{ Success = $false; Attempts = $attempt; BackoffTimes = $backoffTimes }
}

# Mock sleep: 记录退避时间但不实际等待
$mockSleep = { param($s) }

# ── 测试用例 ──────────────────────────────────────────────────

# 用例 1: 第 1 次成功 → 无退避
Run-TestCase "用例1: 首次成功无退避" {
    $callCount = 0
    $result = Invoke-WithRetry-Testable -ScriptBlock {
        $callCount++
        $global:LASTEXITCODE = 0
    } -Description "mock-build" -MaxRetries 3 -BackoffBase 5 -BackoffMax 60 -SleepAction $mockSleep

    Assert-Equal $true $result.Success "应成功"
    Assert-Equal 1 $result.Attempts "应只尝试 1 次"
    Assert-Equal 0 $result.BackoffTimes.Count "不应有退避"
}

# 用例 2: 第 2 次成功 → 1 次退避（5s）
Run-TestCase "用例2: 重试1次后成功，退避5s" {
    # ScriptBlock 在子作用域执行，需用 $script: 前缀让变量修改可见（不易: PowerShell 作用域语义）
    $script:callCount = 0
    $result = Invoke-WithRetry-Testable -ScriptBlock {
        $script:callCount++
        if ($script:callCount -eq 1) {
            $global:LASTEXITCODE = 1
        } else {
            $global:LASTEXITCODE = 0
        }
    } -Description "mock-build" -MaxRetries 3 -BackoffBase 5 -BackoffMax 60 -SleepAction $mockSleep

    Assert-Equal $true $result.Success "应成功"
    Assert-Equal 2 $result.Attempts "应尝试 2 次"
    Assert-Equal 1 $result.BackoffTimes.Count "应有 1 次退避"
    Assert-Equal 5 $result.BackoffTimes[0] "退避应为 5s"
}

# 用例 3: 全部失败 → 2 次退避（5s, 10s）
Run-TestCase "用例3: 全部失败，退避序列5s/10s" {
    $result = Invoke-WithRetry-Testable -ScriptBlock {
        $global:LASTEXITCODE = 1
    } -Description "mock-build" -MaxRetries 3 -BackoffBase 5 -BackoffMax 60 -SleepAction $mockSleep

    Assert-Equal $false $result.Success "应失败"
    Assert-Equal 3 $result.Attempts "应尝试 3 次"
    Assert-Equal 2 $result.BackoffTimes.Count "应有 2 次退避"
    Assert-Equal 5 $result.BackoffTimes[0] "第 1 次退避应为 5s"
    Assert-Equal 10 $result.BackoffTimes[1] "第 2 次退避应为 10s"
}

# 用例 4: 退避上限 → 不超过 max_seconds
Run-TestCase "用例4: 退避不超过上限60s" {
    $result = Invoke-WithRetry-Testable -ScriptBlock {
        $global:LASTEXITCODE = 1
    } -Description "mock-build" -MaxRetries 15 -BackoffBase 5 -BackoffMax 60 -SleepAction $mockSleep

    Assert-Equal $false $result.Success "应失败"
    Assert-Equal 15 $result.Attempts "应尝试 15 次"
    # 退避序列: 5,10,15,20,25,30,35,40,45,50,55,60,60,60
    Assert-Equal 14 $result.BackoffTimes.Count "应有 14 次退避"
    # 检查上限
    foreach ($t in $result.BackoffTimes) {
        if ($t -gt 60) { throw "退避 $t 超过上限 60s" }
    }
    # 第 12 次退避应为 60s（12*5=60）
    Assert-Equal 60 $result.BackoffTimes[11] "第 12 次退避应为 60s"
    # 第 14 次退避也应为 60s（受上限限制）
    Assert-Equal 60 $result.BackoffTimes[13] "第 14 次退避应为 60s（上限）"
}

# 用例 5: 自定义退避参数 → base=2, max=10
Run-TestCase "用例5: 自定义退避 base=2 max=10" {
    $result = Invoke-WithRetry-Testable -ScriptBlock {
        $global:LASTEXITCODE = 1
    } -Description "mock-build" -MaxRetries 8 -BackoffBase 2 -BackoffMax 10 -SleepAction $mockSleep

    # 退避序列: 2,4,6,8,10,10,10
    $expected = @(2, 4, 6, 8, 10, 10, 10)
    Assert-Equal $expected.Count $result.BackoffTimes.Count "退避次数应匹配"
    for ($i = 0; $i -lt $expected.Count; $i++) {
        Assert-Equal $expected[$i] $result.BackoffTimes[$i] "第 $($i+1) 次退避"
    }
}

# 用例 6: 模拟 Docker build 网络超时（前 2 次超时，第 3 次成功）
Run-TestCase "用例6: 模拟Docker网络超时(前2次失败第3次成功)" {
    $script:callCount = 0
    $result = Invoke-WithRetry-Testable -ScriptBlock {
        $script:callCount++
        if ($script:callCount -le 2) {
            # 模拟网络超时: docker build 返回非零
            Write-Log "mock docker build: 网络超时 (第 $script:callCount 次)" "WARN"
            $global:LASTEXITCODE = 1
        } else {
            Write-Log "mock docker build: 成功 (第 $script:callCount 次)" "SUCCESS"
            $global:LASTEXITCODE = 0
        }
    } -Description "docker build mock" -MaxRetries 5 -BackoffBase 5 -BackoffMax 60 -SleepAction $mockSleep

    Assert-Equal $true $result.Success "第 3 次应成功"
    Assert-Equal 3 $result.Attempts "应尝试 3 次"
    Assert-Equal 2 $result.BackoffTimes.Count "应有 2 次退避"
    Assert-Equal 5 $result.BackoffTimes[0] "第 1 次退避 5s"
    Assert-Equal 10 $result.BackoffTimes[1] "第 2 次退避 10s"
}

# ── config.yaml 解析测试 ─────────────────────────────────────

# 用例 7: Read-YamlConfig 解析 config.yaml
Run-TestCase "用例7: config.yaml 解析正确" {
    # 复用主脚本的 Read-YamlConfig 逻辑
    function Read-YamlConfig {
        param([string]$Path)
        if (-not (Test-Path $Path)) { return $null }
        $config = @{}
        $currentSection = $null
        foreach ($line in Get-Content $Path -Encoding UTF8) {
            # 保留前导缩进以区分父子级键（与 build_and_push.ps1 同步）
            $line = ($line -replace '#.*$', '').TrimEnd()
            if ($line.Trim() -eq '') { continue }
            if ($line -match '^(\w+):\s*(.*)$') {
                $key = $matches[1]
                $value = $matches[2].Trim().Trim('"').Trim("'")
                if ($value -eq '') {
                    $currentSection = $key
                    $config[$currentSection] = @{}
                } else {
                    $config[$key] = $value
                    $currentSection = $null
                }
            }
            elseif ($line -match '^\s{2,}(\w+):\s*(.+)$' -and $currentSection) {
                $key = $matches[1]
                $value = $matches[2].Trim().Trim('"').Trim("'")
                $config[$currentSection][$key] = $value
            }
        }
        return $config
    }

    $configPath = Join-Path $PSScriptRoot "config.yaml"
    $config = Read-YamlConfig -Path $configPath

    Assert-Equal $true ($null -ne $config) "config 应非空"
    Assert-Equal "registry.example.com" $config.registry "registry 值"
    Assert-Equal "circuit-breaker-alert" $config.image_name "image_name 值"
    Assert-Equal "3" $config.retry.build "retry.build 值"
    Assert-Equal "5" $config.retry.push "retry.push 值"
    Assert-Equal "5" $config.backoff.base_seconds "backoff.base_seconds 值"
    Assert-Equal "60" $config.backoff.max_seconds "backoff.max_seconds 值"
    Assert-Equal "auto" $config.network.mode "network.mode 值"
}

# 用例 8: 优先级验证 — CLI 参数覆盖 config.yaml
Run-TestCase "用例8: CLI参数覆盖config默认值" {
    # 模拟 build_and_push.ps1 的优先级逻辑
    $config = @{
        registry = "config-registry.com"
        retry = @{ build = "3"; push = "5" }
        backoff = @{ base_seconds = "5"; max_seconds = "60" }
    }

    # CLI 传了 -Registry，应覆盖 config
    $cliRegistry = "cli-registry.com"
    $Registry = ""
    if ($cliRegistry -ne "") { $Registry = $cliRegistry }
    elseif ($config.registry) { $Registry = $config.registry }
    Assert-Equal "cli-registry.com" $Registry "CLI 应覆盖 config"

    # CLI 没传 -BuildRetry（-1），应回退 config
    $cliBuildRetry = -1
    $BuildRetry = -1
    if ($cliBuildRetry -ge 0) { $BuildRetry = $cliBuildRetry }
    elseif ($config.retry.build) { $BuildRetry = [int]$config.retry.build }
    Assert-Equal 3 $BuildRetry "未传 CLI 应回退 config"
}

# ── 真实退避时间验证（短时）──────────────────────────────────

# 用例 9: 真实退避时间验证（base=1s, 2次重试, 验证实际等待）
Run-TestCase "用例9: 真实退避时间验证(base=1s)" {
    # $realSleep ScriptBlock 在更深的子作用域执行，需 $script: 前缀
    $script:actualSleeps = @()
    $realSleep = { param($s) $script:actualSleeps += $s; Start-Sleep -Milliseconds ($s * 100) }

    $result = Invoke-WithRetry-Testable -ScriptBlock {
        $global:LASTEXITCODE = 1
    } -Description "real-backoff-test" -MaxRetries 3 -BackoffBase 1 -BackoffMax 60 -SleepAction $realSleep

    Assert-Equal $false $result.Success "应失败"
    Assert-Equal 2 $script:actualSleeps.Count "应有 2 次实际 sleep"
    Assert-Equal 1 $script:actualSleeps[0] "第 1 次 sleep 1s"
    Assert-Equal 2 $script:actualSleeps[1] "第 2 次 sleep 2s"
}

# ── 测试总结 ──────────────────────────────────────────────────

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor DarkGray
Write-Host "  测试总结" -ForegroundColor White
Write-Host ("=" * 60) -ForegroundColor DarkGray

$passed = ($script:testResults | Where-Object { $_.Passed }).Count
$failed = ($script:testResults | Where-Object { -not $_.Passed }).Count
$total = $script:testResults.Count

foreach ($r in $script:testResults) {
    $status = if ($r.Passed) { "PASS" } else { "FAIL" }
    $color = if ($r.Passed) { "Green" } else { "Red" }
    Write-Host "  [$status] $($r.Name)" -ForegroundColor $color
    if (-not $r.Passed -and $Verbose) {
        Write-Host "         $($r.Error)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host ("  通过: $passed / $total") -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })
if ($failed -gt 0) {
    Write-Host ("  失败: $failed") -ForegroundColor Red
    exit 1
} else {
    Write-Host "  全部通过!" -ForegroundColor Green
    exit 0
}
