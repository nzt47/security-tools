﻿<#
.SYNOPSIS
    验证环境变量优先级: CLI > 环境变量 > config.yaml > 默认值

.DESCRIPTION
    模拟 build_and_push.ps1 的配置加载逻辑，验证 CI/CD 环境变量注入是否能正确覆盖。
    使用 mock config 字典，不依赖真实 config.yaml 文件。

    验证场景:
    1. 环境变量覆盖 config.yaml（CI/CD 注入场景）
    2. CLI 参数覆盖环境变量（显式传参优先）
    3. 无环境变量时回退 config.yaml（本地开发场景）
    4. 全部未设置时回退默认值（兜底）

.EXAMPLE
    .\verify_env_override.ps1
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 模拟 config.yaml 加载结果（与 config.yaml 字段一致）
$mockConfig = @{
    registry    = "config-registry.com"
    image_name  = "config-image"
    tag         = "config-tag"
    retry       = @{ build = "3"; login = "3"; push = "5" }
    backoff     = @{ base_seconds = "5"; max_seconds = "60" }
    network     = @{ mode = "host" }
}

$script:testResults = @()

function Assert-Equal {
    param($Expected, $Actual, $Message)
    if ($Expected -eq $Actual) {
        return $true
    }
    throw "断言失败: $Message`n  期望: $Expected`n  实际: $Actual"
}

function Run-Test {
    param([string]$Name, [scriptblock]$Test)
    Write-Host ""
    Write-Host "── $Name ──" -ForegroundColor White
    try {
        & $Test
        $script:testResults += @{ Name = $Name; Passed = $true }
        Write-Host "  [PASS]" -ForegroundColor Green
    } catch {
        $script:testResults += @{ Name = $Name; Passed = $false; Error = $_.Exception.Message }
        Write-Host "  [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
}

# 模拟 build_and_push.ps1 的配置加载逻辑（优先级: CLI > env > config.yaml > 默认值）
function Resolve-Config {
    param(
        [hashtable]$Config,
        [string]$CliRegistry = "",
        [string]$CliNetworkMode = "",
        [int]$CliBackoffBase = -1
    )
    # [string] 强制转换: $env:VAR 不存在时返回 $null，需转为 "" 才能与 "" 比较
    # 否则 $null -eq "" 为 false，导致回退链断裂（不易: 类型契约）
    $Registry = $CliRegistry
    $NetworkMode = $CliNetworkMode
    $BackoffBase = $CliBackoffBase

    # 1. 环境变量回退（CLI 未设置时）
    if ($Registry -eq "")    { $Registry = [string]$env:DOCKER_REGISTRY }
    if ($NetworkMode -eq "") { $NetworkMode = [string]$env:DOCKER_NETWORK_MODE }
    if ($BackoffBase -lt 0 -and $env:DOCKER_BACKOFF_BASE) { $BackoffBase = [int]$env:DOCKER_BACKOFF_BASE }

    # 2. config.yaml 回退（CLI 和环境变量都未设置时）
    if ($Config) {
        if ($Registry -eq "")    { $Registry = $Config.registry }
        if ($NetworkMode -eq "") { $NetworkMode = $Config.network.mode }
        if ($BackoffBase -lt 0)  { $BackoffBase = [int]$Config.backoff.base_seconds }
    }

    # 3. 兜底默认值
    if ($Registry -eq "")    { $Registry = "registry.example.com" }
    if ($NetworkMode -eq "") { $NetworkMode = "auto" }
    if ($BackoffBase -lt 0)  { $BackoffBase = 5 }

    return @{
        Registry    = $Registry
        NetworkMode = $NetworkMode
        BackoffBase = $BackoffBase
    }
}

# ── 测试用例 ──────────────────────────────────────────────────

# 测试 1: 环境变量覆盖 config.yaml
Run-Test "测试1: 环境变量覆盖 config.yaml" {
    $env:DOCKER_REGISTRY = "env-registry.com"
    $env:DOCKER_NETWORK_MODE = "default"
    $env:DOCKER_BACKOFF_BASE = "15"

    $result = Resolve-Config -Config $mockConfig

    Assert-Equal "env-registry.com" $result.Registry "Registry 应取环境变量"
    Assert-Equal "default" $result.NetworkMode "NetworkMode 应取环境变量"
    Assert-Equal 15 $result.BackoffBase "BackoffBase 应取环境变量"

    Remove-Item env:DOCKER_REGISTRY -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_NETWORK_MODE -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_BACKOFF_BASE -ErrorAction SilentlyContinue
}

# 测试 2: CLI 参数覆盖环境变量
Run-Test "测试2: CLI 参数覆盖环境变量" {
    $env:DOCKER_REGISTRY = "env-registry.com"
    $env:DOCKER_NETWORK_MODE = "default"

    $result = Resolve-Config -Config $mockConfig -CliRegistry "cli-registry.com" -CliNetworkMode "none"

    Assert-Equal "cli-registry.com" $result.Registry "Registry 应取 CLI"
    Assert-Equal "none" $result.NetworkMode "NetworkMode 应取 CLI"

    Remove-Item env:DOCKER_REGISTRY -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_NETWORK_MODE -ErrorAction SilentlyContinue
}

# 测试 3: 无环境变量时回退 config.yaml
Run-Test "测试3: 无环境变量时回退 config.yaml" {
    Remove-Item env:DOCKER_REGISTRY -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_NETWORK_MODE -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_BACKOFF_BASE -ErrorAction SilentlyContinue

    $result = Resolve-Config -Config $mockConfig

    Assert-Equal "config-registry.com" $result.Registry "Registry 应回退 config"
    Assert-Equal "host" $result.NetworkMode "NetworkMode 应回退 config"
    Assert-Equal 5 $result.BackoffBase "BackoffBase 应回退 config"
}

# 测试 4: 全部未设置时回退默认值
Run-Test "测试4: 全部未设置时回退默认值" {
    Remove-Item env:DOCKER_REGISTRY -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_NETWORK_MODE -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_BACKOFF_BASE -ErrorAction SilentlyContinue

    $result = Resolve-Config -Config $null

    Assert-Equal "registry.example.com" $result.Registry "Registry 应取默认值"
    Assert-Equal "auto" $result.NetworkMode "NetworkMode 应取默认值"
    Assert-Equal 5 $result.BackoffBase "BackoffBase 应取默认值"
}

# 测试 5: 环境变量为空字符串时不覆盖 config.yaml
Run-Test "测试5: 环境变量为空字符串时不覆盖" {
    $env:DOCKER_REGISTRY = ""
    $env:DOCKER_NETWORK_MODE = ""

    $result = Resolve-Config -Config $mockConfig

    Assert-Equal "config-registry.com" $result.Registry "空环境变量应回退 config"
    Assert-Equal "host" $result.NetworkMode "空环境变量应回退 config"

    Remove-Item env:DOCKER_REGISTRY -ErrorAction SilentlyContinue
    Remove-Item env:DOCKER_NETWORK_MODE -ErrorAction SilentlyContinue
}

# ── 总结 ──────────────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 60) -ForegroundColor DarkGray
$passed = ($script:testResults | Where-Object { $_.Passed }).Count
$failed = ($script:testResults | Where-Object { -not $_.Passed }).Count
Write-Host ("  通过: $passed / $($script:testResults.Count)") -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })
if ($failed -gt 0) {
    Write-Host ("  失败: $failed") -ForegroundColor Red
    exit 1
} else {
    Write-Host "  全部通过!" -ForegroundColor Green
    exit 0
}
