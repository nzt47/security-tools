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
    [string]$Registry = "registry.example.com",
    [string]$ImageName = "circuit-breaker-alert",
    [string]$Tag = "1.0",
    [string]$Dockerfile = "docker/circuit-breaker-alert/Dockerfile",
    [string]$Context = ".",
    [int]$BuildRetry = 3,
    [int]$PushRetry = 5,
    [switch]$SkipLogin,
    [switch]$SkipPush,
    [int]$LoginRetry = 3
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

function Invoke-WithRetry {
    <#
    .SYNOPSIS
        带重试逻辑的命令执行器

    .DESCRIPTION
        执行命令，失败时自动重试。每次重试前等待 backoff 秒数。
        适用于网络不稳定场景（docker build / docker push）。

        重试策略：
        - 第 1 次重试：等待 5s
        - 第 2 次重试：等待 10s
        - 第 3 次重试：等待 20s
        - 第 N 次重试：等待 min(N*5, 60)s
    #>
    param(
        [scriptblock]$ScriptBlock,
        [string]$Description,
        [int]$MaxRetries = 3,
        [string]$ExitCodeOnFailure = 1
    )

    $attempt = 0
    while ($attempt -lt $MaxRetries) {
        $attempt++
        $backoff = [Math]::Min($attempt * 5, 60)

        Write-Log "尝试 $attempt/${MaxRetries}: $Description"

        try {
            & $ScriptBlock
            $exitCode = $LASTEXITCODE
            if ($exitCode -eq 0) {
                Write-Log "成功: $Description (第 $attempt 次尝试)" "SUCCESS"
                return $true
            } else {
                throw "退出码 $exitCode"
            }
        } catch {
            $errorMsg = $_.Exception.Message
            if ($attempt -lt $MaxRetries) {
                Write-Log "失败: $Description - $errorMsg" "WARN"
                Write-Log "等待 ${backoff}s 后重试..." "WARN"
                Start-Sleep -Seconds $backoff
            } else {
                Write-Log "最终失败: $Description - $errorMsg (已重试 $MaxRetries 次)" "ERROR"
                return $false
            }
        }
    }
    return $false
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
Write-Log "重试配置: build=$BuildRetry, push=$PushRetry, login=$LoginRetry"

# ── 步骤 1: Docker Build ─────────────────────────────────────

Write-Step "步骤 1/3: Docker Build（带网络重试）"

$buildSuccess = Invoke-WithRetry -ScriptBlock {
    # --network=host: 使用宿主机网络（加速 apt-get 下载）
    # --progress=plain: 输出完整日志（便于排查）
    docker build `
        --network=host `
        --progress=plain `
        -t $fullImageName `
        -f $Dockerfile `
        $Context 2>&1 | ForEach-Object {
            # 实时输出构建日志
            Write-Host $_
        }
    if ($LASTEXITCODE -ne 0) { throw "docker build 失败" }
} -Description "docker build $fullImageName" -MaxRetries $BuildRetry

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
    } -Description "docker login $Registry" -MaxRetries $LoginRetry

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
    } -Description "docker push $fullImageName" -MaxRetries $PushRetry

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
