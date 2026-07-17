<#
.SYNOPSIS
    Docker 镜像构建 + 推送脚本（自动网络重试）

.DESCRIPTION
    自动构建 Docker 镜像并推送到私有仓库，内置网络重试逻辑。
    适用于网络不稳定环境（如 apt-get 下载失败、docker push 超时等）。

    重试策略：
    - Docker build: 最多重试 3 次（利用 layer cache，后续重试更快）
    - Docker login: 最多重试 3 次
    - Docker push:  最多重试 5 次（push 对网络更敏感）

.PARAMETER Registry
    私有仓库地址（默认: registry.example.com）

.PARAMETER ImageName
    镜像名称（默认: circuit-breaker-alert）

.PARAMETER Tag
    镜像标签（默认: 1.0）

.PARAMETER Dockerfile
    Dockerfile 路径（默认: docker/circuit-breaker-alert/Dockerfile）

.PARAMETER Context
    构建上下文目录（默认: 当前目录）

.PARAMETER BuildRetry
    构建重试次数（默认: 3）

.PARAMETER PushRetry
    推送重试次数（默认: 5）

.PARAMETER SkipLogin
    跳过 docker login（已登录时使用）

.PARAMETER SkipPush
    跳过推送（只构建不推送）

.EXAMPLE
    # 默认构建 + 推送
    .\docker\circuit-breaker-alert\build_and_push.ps1

.EXAMPLE
    # 自定义仓库地址
    .\docker\circuit-breaker-alert\build_and_push.ps1 -Registry ghcr.io -ImageName myorg/circuit-breaker-alert -Tag latest

.EXAMPLE
    # 只构建不推送
    .\docker\circuit-breaker-alert\build_and_push.ps1 -SkipPush

.EXAMPLE
    # 已登录，跳过 login 步骤
    .\docker\circuit-breaker-alert\build_and_push.ps1 -SkipLogin

.NOTES
    退出码:
    0 = 成功
    1 = 构建失败
    2 = 登录失败
    3 = 推送失败
#>

param(
    [string]$ConfigFile = "",
    [string]$Registry = "",
    [string]$ImageName = "",
    [string]$Tag = "",
    [string]$Dockerfile = "",
    [string]$Context = "",
    [int]$BuildRetry = -1,
    [int]$PushRetry = -1,
    [int]$LoginRetry = -1,
    [int]$BackoffBase = -1,
    [int]$BackoffMax = -1,
    [string]$NetworkMode = "",
    [switch]$SkipLogin,
    [switch]$SkipPush
)

# ── 工具函数 ──────────────────────────────────────────────────

function Write-Log {
    <# 输出带时间戳的日志 #>
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

function Write-Step {
    <# 输出步骤分隔线 #>
    param([string]$StepName)
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    Write-Host "  $StepName" -ForegroundColor White
    Write-Host ("=" * 70) -ForegroundColor DarkGray
}

function Test-Command {
    <# 检查命令是否存在 #>
    param([string]$Command)
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Read-YamlConfig {
    <#
    .SYNOPSIS
        轻量 YAML 解析（仅支持两级嵌套 + 标量值，不引入外部依赖）

    .DESCRIPTION
        解析 config.yaml 中的简单键值对和两级嵌套结构。
        不支持列表、多行字符串、锚点等复杂 YAML 特性。
        设计目标：零依赖 + 满足 config.yaml 的解析需求（简易原则）。
    #>
    param([string]$Path)

    if (-not (Test-Path $Path)) { return $null }

    $config = @{}
    $currentSection = $null
    foreach ($line in Get-Content $Path -Encoding UTF8) {
        # 去除注释和尾部空白（保留前导缩进以区分父子级键 — 不易: YAML 缩进语义）
        $line = ($line -replace '#.*$', '').TrimEnd()
        if ($line.Trim() -eq '') { continue }

        # 顶级键: "key: value" 或 "key:"（开始子节）
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
        # 子级键: "  key: value"（缩进 2+ 空格）
        elseif ($line -match '^\s{2,}(\w+):\s*(.+)$' -and $currentSection) {
            $key = $matches[1]
            $value = $matches[2].Trim().Trim('"').Trim("'")
            $config[$currentSection][$key] = $value
        }
    }
    return $config
}

function Invoke-WithRetry {
    <#
    .SYNOPSIS
        带重试逻辑的命令执行器

    .DESCRIPTION
        执行命令，失败时自动重试。每次重试前等待 backoff 秒数。
        适用于网络不稳定场景（docker build / docker push）。

        退避策略（可配置，默认值与 config.yaml 一致）：
        - 第 N 次重试：等待 min(N * BackoffBase, BackoffMax) 秒
        - 默认: BackoffBase=5, BackoffMax=60 → 5s, 10s, 15s, ..., 60s, 60s
    #>
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

# ── 加载配置文件 ──────────────────────────────────────────────
# 优先级: CLI > 环境变量 > config.yaml > 内置默认值
# - CLI 参数:    用户显式传递（最高优先级）
# - 环境变量:    CI/CD 注入，命名约定 DOCKER_<FIELD>（与 Docker CLI 惯例一致）
# - config.yaml: 仓库默认配置
# - 内置默认值:  兜底
# CLI 参数为空/-1 表示未设置

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($ConfigFile -eq "") {
    $ConfigFile = Join-Path $scriptDir "config.yaml"
}
$config = Read-YamlConfig -Path $ConfigFile
if ($config) {
    Write-Log "已加载配置: $ConfigFile"
}

# 1. 环境变量回退（CI/CD 注入，CLI 未设置时）
if ($Registry -eq "")    { $Registry = $env:DOCKER_REGISTRY }
if ($ImageName -eq "")   { $ImageName = $env:DOCKER_IMAGE_NAME }
if ($Tag -eq "")         { $Tag = $env:DOCKER_TAG }
if ($Dockerfile -eq "")  { $Dockerfile = $env:DOCKERFILE_PATH }
if ($Context -eq "")     { $Context = $env:DOCKER_CONTEXT }
if ($BuildRetry -lt 0 -and $env:DOCKER_BUILD_RETRY)    { $BuildRetry = [int]$env:DOCKER_BUILD_RETRY }
if ($LoginRetry -lt 0 -and $env:DOCKER_LOGIN_RETRY)    { $LoginRetry = [int]$env:DOCKER_LOGIN_RETRY }
if ($PushRetry -lt 0 -and $env:DOCKER_PUSH_RETRY)      { $PushRetry = [int]$env:DOCKER_PUSH_RETRY }
if ($BackoffBase -lt 0 -and $env:DOCKER_BACKOFF_BASE)  { $BackoffBase = [int]$env:DOCKER_BACKOFF_BASE }
if ($BackoffMax -lt 0 -and $env:DOCKER_BACKOFF_MAX)    { $BackoffMax = [int]$env:DOCKER_BACKOFF_MAX }
if ($NetworkMode -eq "") { $NetworkMode = $env:DOCKER_NETWORK_MODE }

# 2. config.yaml 回退（CLI 和环境变量都未设置时）
if ($config) {
    if ($Registry -eq "")    { $Registry = $config.registry }
    if ($ImageName -eq "")   { $ImageName = $config.image_name }
    if ($Tag -eq "")         { $Tag = $config.tag }
    if ($Dockerfile -eq "")  { $Dockerfile = $config.dockerfile }
    if ($Context -eq "")     { $Context = $config.context }
    if ($BuildRetry -lt 0)   { $BuildRetry = [int]$config.retry.build }
    if ($LoginRetry -lt 0)   { $LoginRetry = [int]$config.retry.login }
    if ($PushRetry -lt 0)    { $PushRetry = [int]$config.retry.push }
    if ($BackoffBase -lt 0)  { $BackoffBase = [int]$config.backoff.base_seconds }
    if ($BackoffMax -lt 0)   { $BackoffMax = [int]$config.backoff.max_seconds }
    if ($NetworkMode -eq "") { $NetworkMode = $config.network.mode }
}

# 兜底默认值（config.yaml 不存在或字段缺失时）
if ($Registry -eq "")    { $Registry = "registry.example.com" }
if ($ImageName -eq "")   { $ImageName = "circuit-breaker-alert" }
if ($Tag -eq "")         { $Tag = "1.0" }
if ($Dockerfile -eq "")  { $Dockerfile = "docker/circuit-breaker-alert/Dockerfile" }
if ($Context -eq "")     { $Context = "." }
if ($BuildRetry -lt 0)   { $BuildRetry = 3 }
if ($LoginRetry -lt 0)   { $LoginRetry = 3 }
if ($PushRetry -lt 0)    { $PushRetry = 5 }
if ($BackoffBase -lt 0)  { $BackoffBase = 5 }
if ($BackoffMax -lt 0)   { $BackoffMax = 60 }
if ($NetworkMode -eq "") { $NetworkMode = "auto" }

# ── Docker Desktop 网络兼容性（跨平台：Windows/macOS/Linux）───
# --network=host 在 Docker Desktop 上使用的是 VM 网络栈
#   - Windows: WSL2 VM
#   - macOS:   Hypervisor.framework VM
# 而非宿主机网络。若宿主机配置 VPN/代理，apt-get 下载会失败或连接被拒绝。
# auto 模式: Windows/macOS → default (bridge), Linux → host
#
# 平台检测兼容 PS 5.x（仅 Windows，无 $Is* 自动变量）和 PS 7+（跨平台）
$onWindows = if ($null -ne $IsWindows) { [bool]$IsWindows } else { $env:OS -eq "Windows_NT" }
$onMacOS   = if ($null -ne $IsMacOS)   { [bool]$IsMacOS }   else { $false }
$onLinux   = if ($null -ne $IsLinux)   { [bool]$IsLinux }   else { -not $onWindows -and -not $onMacOS }

# platform_override（调试用）：从 config.network.platform_override 读取强制平台覆盖
if ($config -and $config.network.platform_override) {
    $override = $config.network.platform_override
    switch ($override) {
        "windows" { $onWindows = $true;  $onMacOS = $false; $onLinux = $false }
        "macos"   { $onWindows = $false; $onMacOS = $true;  $onLinux = $false }
        "linux"   { $onWindows = $false; $onMacOS = $false; $onLinux = $true }
        default   { Write-Log "未知 platform_override 值: $override，忽略" "WARN" }
    }
    Write-Log "使用 platform_override: $override" "WARN"
}
$platformLabel = if ($onWindows) { "Windows" } elseif ($onMacOS) { "macOS" } else { "Linux" }

$resolvedNetworkMode = $NetworkMode
if ($NetworkMode -eq "auto") {
    if ($onWindows) {
        $resolvedNetworkMode = "default"
        Write-Log "检测到 $platformLabel 平台，网络模式: default (Docker Desktop WSL2 兼容)" "WARN"
    } elseif ($onMacOS) {
        $resolvedNetworkMode = "default"
        Write-Log "检测到 $platformLabel 平台，网络模式: default (Docker Desktop Hypervisor 兼容)" "WARN"
    } else {
        $resolvedNetworkMode = "host"
        Write-Log "检测到 $platformLabel 平台，网络模式: host (加速 apt-get 下载)"
    }
}

# 构造 docker build 网络参数
$networkArg = switch ($resolvedNetworkMode) {
    "host"    { "--network=host" }
    "none"    { "--network=none" }
    "default" { "" }  # default 不传 --network，使用 Docker 默认 bridge
    default   { "" }
}

# ── 前置检查 ──────────────────────────────────────────────────

Write-Step "前置检查"

# 检查 Docker 是否安装
if (-not (Test-Command "docker")) {
    Write-Log "Docker 未安装或不在 PATH 中" "ERROR"
    exit 1
}
Write-Log "Docker 已安装" "SUCCESS"

# 检查 Docker daemon 是否运行
Write-Log "检查 Docker daemon 状态..."
$dockerInfo = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "Docker daemon 未运行，尝试启动 Docker Desktop..." "WARN"
    $dockerDesktopPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerDesktopPath) {
        Start-Process $dockerDesktopPath -ErrorAction SilentlyContinue
        Write-Log "等待 Docker daemon 启动（最多 60s）..."
        $startTime = Get-Date
        $timeout = 60
        while ($true) {
            $elapsed = ((Get-Date) - $startTime).TotalSeconds
            if ($elapsed -gt $timeout) {
                Write-Log "Docker daemon 启动超时（${timeout}s）" "ERROR"
                exit 1
            }
            docker info 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Docker daemon 已就绪（${elapsed}s）" "SUCCESS"
                break
            }
            Start-Sleep -Seconds 3
            Write-Log "  等待中... ${elapsed}s" "WARN"
        }
    } else {
        Write-Log "Docker Desktop 未找到，请手动启动 Docker daemon" "ERROR"
        exit 1
    }
} else {
    $version = ($dockerInfo | Select-String "Server Version").ToString().Replace("Server Version: ", "").Trim()
    Write-Log "Docker daemon 运行中 (Server Version: $version)" "SUCCESS"
}

# 检查 Dockerfile 是否存在
if (-not (Test-Path $Dockerfile)) {
    Write-Log "Dockerfile 不存在: $Dockerfile" "ERROR"
    exit 1
}
Write-Log "Dockerfile: $Dockerfile"

# ── 构建配置 ──────────────────────────────────────────────────

$fullImageName = "${Registry}/${ImageName}:${Tag}"
Write-Log "目标镜像: $fullImageName"
Write-Log "构建上下文: $Context"
Write-Log "重试配置: build=$BuildRetry, push=$PushRetry, login=$LoginRetry, 退避=${BackoffBase}s*${BackoffMax}max"

# ── 步骤 1: Docker Build ─────────────────────────────────────

Write-Step "步骤 1/3: Docker Build（带网络重试）"

$buildSuccess = Invoke-WithRetry -ScriptBlock {
    # 网络模式由 $networkArg 控制（auto/host/default/none）
    # --progress=plain: 输出完整日志（便于排查）
    $buildArgs = @("build", "--progress=plain", "-t", $fullImageName, "-f", $Dockerfile)
    if ($networkArg) { $buildArgs += $networkArg }
    $buildArgs += $Context
    & docker @buildArgs 2>&1 | ForEach-Object {
        # 实时输出构建日志
        Write-Host $_
    }
    if ($LASTEXITCODE -ne 0) { throw "docker build 失败" }
} -Description "docker build $fullImageName" -MaxRetries $BuildRetry -BackoffBase $BackoffBase -BackoffMax $BackoffMax

if (-not $buildSuccess) {
    Write-Log "Docker build 最终失败（已重试 $BuildRetry 次）" "ERROR"
    Write-Log "可能原因:" "ERROR"
    Write-Log "  1. 网络不稳定（apt-get 下载失败）→ 检查网络连接" "ERROR"
    Write-Log "  2. Dockerfile 语法错误 → 检查 $Dockerfile" "ERROR"
    Write-Log "  3. 依赖包不存在 → 检查 requirements.txt" "ERROR"
    exit 1
}

# 验证镜像已构建
$imageExists = docker images --format "{{.Repository}}:{{.Tag}}" | Where-Object { $_ -eq $fullImageName }
if (-not $imageExists) {
    Write-Log "镜像构建后未找到: $fullImageName" "ERROR"
    exit 1
}

# 获取镜像大小
$imageSize = docker images --format "{{.Size}}" $fullImageName
Write-Log "镜像构建成功: $fullImageName (大小: $imageSize)" "SUCCESS"

# ── 步骤 2: Docker Login（可选）──────────────────────────────

if (-not $SkipLogin -and -not $SkipPush) {
    Write-Step "步骤 2/3: Docker Login"

    # 检查是否已登录（尝试拉取一个不存在的镜像，如果返回 401 则未登录）
    Write-Log "检查 $Registry 的登录状态..."

    $loginSuccess = Invoke-WithRetry -ScriptBlock {
        # 交互式登录（会提示输入用户名和密码）
        Write-Log "请输入 $Registry 的凭据:"
        docker login $Registry
        if ($LASTEXITCODE -ne 0) { throw "docker login 失败" }
    } -Description "docker login $Registry" -MaxRetries $LoginRetry -BackoffBase $BackoffBase -BackoffMax $BackoffMax

    if (-not $loginSuccess) {
        Write-Log "Docker login 失败（已重试 $LoginRetry 次）" "ERROR"
        Write-Log "如果已登录，请使用 -SkipLogin 参数跳过此步骤" "ERROR"
        exit 2
    }
    Write-Log "Docker login 成功" "SUCCESS"
} else {
    if ($SkipLogin) {
        Write-Step "步骤 2/3: Docker Login（已跳过）"
        Write-Log "跳过 login（-SkipLogin）" "WARN"
    }
}

# ── 步骤 3: Docker Push ──────────────────────────────────────

if (-not $SkipPush) {
    Write-Step "步骤 3/3: Docker Push（带网络重试）"

    $pushSuccess = Invoke-WithRetry -ScriptBlock {
        docker push $fullImageName 2>&1 | ForEach-Object {
            Write-Host $_
        }
        if ($LASTEXITCODE -ne 0) { throw "docker push 失败" }
    } -Description "docker push $fullImageName" -MaxRetries $PushRetry -BackoffBase $BackoffBase -BackoffMax $BackoffMax

    if (-not $pushSuccess) {
        Write-Log "Docker push 最终失败（已重试 $PushRetry 次）" "ERROR"
        Write-Log "可能原因:" "ERROR"
        Write-Log "  1. 网络不稳定 → 检查网络连接" "ERROR"
        Write-Log "  2. 未登录 → 运行 docker login $Registry" "ERROR"
        Write-Log "  3. 仓库不存在 → 在 $Registry 上创建 $ImageName 仓库" "ERROR"
        Write-Log "  4. 权限不足 → 检查仓库写权限" "ERROR"
        exit 3
    }
    Write-Log "Docker push 成功: $fullImageName" "SUCCESS"
} else {
    Write-Step "步骤 3/3: Docker Push（已跳过）"
    Write-Log "跳过 push（-SkipPush）" "WARN"
}

# ── 总结 ──────────────────────────────────────────────────────

Write-Step "构建总结"

Write-Log "镜像: $fullImageName" "SUCCESS"
Write-Log "大小: $imageSize"
if (-not $SkipPush) {
    Write-Log "状态: 已推送到 $Registry" "SUCCESS"
} else {
    Write-Log "状态: 仅本地构建（未推送）" "WARN"
}

Write-Host ""
Write-Host "使用方式:" -ForegroundColor Cyan
Write-Host "  docker pull $fullImageName"
Write-Host "  docker run --rm $fullImageName"
Write-Host ""
Write-Host "docker-compose 使用:" -ForegroundColor Cyan
Write-Host "  docker-compose -f docker/circuit-breaker-alert/docker-compose.yml up -d"
Write-Host ""

Write-Log "完成!" "SUCCESS"
exit 0
