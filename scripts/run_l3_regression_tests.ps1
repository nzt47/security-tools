<#
.SYNOPSIS
    L3 层完整回归测试一键脚本（Linux Docker 环境）

.DESCRIPTION
    在 Linux Docker 容器中运行完整的 L3 层测试，覆盖：
    - sqlite-vec 向量检索后端
    - embedding 模型加载 + KNN 路径
    - VectorStore 完整功能（非 JSON fallback）
    - LongTermMemory 端到端

    解决 Windows 下 torch 0xC0000005 崩溃问题，确保测试在 Linux 环境完整运行。

.PARAMETER Mode
    测试模式：
    - sqlite-vec（默认）：运行 L3 核心测试（5 个文件）
    - all：运行所有 unit + integration 测试
    - integration：仅运行 integration 测试
    - file：运行指定测试文件（配合 -TestFile）

.PARAMETER TestFile
    指定测试文件路径（仅 Mode=file 时有效）
    示例：-Mode file -TestFile tests/unit/test_vector_store_sqlite_vec.py

.PARAMETER Rebuild
    强制重新构建 Docker 镜像（不加此参数则使用缓存）

.PARAMETER Predownload
    运行前先预下载 embedding 模型到缓存卷（首次使用或网络不稳定时推荐）

.PARAMETER NoCache
    Docker 构建时不使用缓存（配合 -Rebuild 使用）

.PARAMETER Clean
    测试完成后清理容器和卷（慎用：会删除模型缓存卷）

.PARAMETER Verbose
    显示详细输出

.EXAMPLE
    # 默认：构建（如需）+ 运行 L3 核心测试
    .\scripts\run_l3_regression_tests.ps1

.EXAMPLE
    # 强制重建镜像 + 预下载模型 + 运行测试
    .\scripts\run_l3_regression_tests.ps1 -Rebuild -Predownload

.EXAMPLE
    # 运行所有测试（unit + integration）
    .\scripts\run_l3_regression_tests.ps1 -Mode all

.EXAMPLE
    # 运行指定测试文件
    .\scripts\run_l3_regression_tests.ps1 -Mode file -TestFile tests/unit/test_vector_store_sqlite_vec.py

.EXAMPLE
    # 完整流程：重建 + 预下载 + 测试 + 清理
    .\scripts\run_l3_regression_tests.ps1 -Rebuild -Predownload -Clean

.NOTES
    前置条件：
    - Docker Desktop 已安装并运行（Linux 引擎模式）
    - 项目根目录有 Dockerfile.linux-test 和 docker-compose.linux-test.yml

    退出码：
    - 0：所有测试通过
    - 1：测试失败或构建失败
    - 2：Docker 引擎未运行
    - 3：缺少必要文件
#>

param(
    [ValidateSet("sqlite-vec", "all", "integration", "file")]
    [string]$Mode = "sqlite-vec",

    [string]$TestFile = "",

    [switch]$Rebuild,
    [switch]$Predownload,
    [switch]$NoCache,
    [switch]$Clean,
    [switch]$Verbose
)

# ── 全局变量 ──
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ComposeFile = Join-Path $ProjectRoot "docker-compose.linux-test.yml"
$Dockerfile = Join-Path $ProjectRoot "Dockerfile.linux-test"
$StartTime = Get-Date

# ── 辅助函数 ──

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-OK([string]$msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Write-Err([string]$msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
}

function Write-Info([string]$msg) {
    Write-Host "[INFO] $msg" -ForegroundColor Gray
}

function Test-DockerEngine {
    try {
        $null = docker info 2>&1
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        return $true
    } catch {
        return $false
    }
}

function Test-Prerequisites {
    if (-not (Test-Path $ComposeFile)) {
        Write-Err "缺少 docker-compose 配置文件: $ComposeFile"
        exit 3
    }
    if (-not (Test-Path $Dockerfile)) {
        Write-Err "缺少 Dockerfile: $Dockerfile"
        exit 3
    }
}

function Get-ImageExists {
    try {
        $result = docker images --filter "reference=agent-test-sqlite-vec" --format "{{.Repository}}:{{.Tag}}" 2>$null
        return ($null -ne $result -and $result.ToString().Trim() -ne "")
    } catch {
        return $false
    }
}

# ── 主流程 ──

Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  L3 层完整回归测试（Linux Docker）" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  项目根目录: $ProjectRoot"
Write-Host "  测试模式:   $Mode"
Write-Host "  开始时间:   $StartTime"
if ($Verbose) { Write-Host "  详细模式:   已启用" }
Write-Host ""

# Step 1: 前置检查
Write-Step "Step 1/5: 前置检查"

Test-Prerequisites
Write-OK "配置文件检查通过"

if (-not (Test-DockerEngine)) {
    Write-Err "Docker 引擎未运行！请先启动 Docker Desktop（Linux 引擎模式）"
    Write-Host ""
    Write-Host "启动方法：" -ForegroundColor Yellow
    Write-Host "  1. 打开 Docker Desktop 应用" -ForegroundColor Yellow
    Write-Host "  2. 等待右下角 Docker 图标变为绿色" -ForegroundColor Yellow
    Write-Host "  3. 重新运行此脚本" -ForegroundColor Yellow
    exit 2
}
Write-OK "Docker 引擎运行中"

# Step 2: 构建 Docker 镜像
Write-Step "Step 2/5: 构建 Docker 镜像"

$imageExists = Get-ImageExists
if ($imageExists -and -not $Rebuild) {
    Write-OK "镜像已存在，跳过构建（使用 -Rebuild 强制重建）"
} else {
    if ($Rebuild) {
        Write-Info "强制重建镜像..."
    } else {
        Write-Info "镜像不存在，开始构建..."
    }

    $buildArgs = @("-f", $ComposeFile, "build")
    if ($Mode -eq "all") {
        $buildArgs += "test-all"
    } elseif ($Mode -eq "integration") {
        $buildArgs += "test-integration"
    } else {
        $buildArgs += "test-sqlite-vec"
    }

    if ($NoCache) {
        $buildArgs += "--no-cache"
    }

    Write-Info "构建命令: docker-compose $($buildArgs -join ' ')"
    $buildStartTime = Get-Date

    & docker-compose @buildArgs
    $buildExitCode = $LASTEXITCODE
    $buildDuration = (Get-Date) - $buildStartTime

    if ($buildExitCode -ne 0) {
        Write-Err "Docker 镜像构建失败（退出码: $buildExitCode）"
        Write-Host "构建耗时: $($buildDuration.ToString('mm\分ss\秒'))" -ForegroundColor Yellow
        exit 1
    }

    Write-OK "镜像构建成功（耗时: $($buildDuration.ToString('mm\分ss\秒'))）"
}

# Step 3: 预下载模型（可选）
Write-Step "Step 3/5: 预下载 embedding 模型"

if ($Predownload) {
    Write-Info "开始预下载 embedding 模型到缓存卷..."
    $predownloadStartTime = Get-Date

    & docker-compose -f $ComposeFile run --rm predownload-models
    $predownloadExitCode = $LASTEXITCODE
    $predownloadDuration = (Get-Date) - $predownloadStartTime

    if ($predownloadExitCode -ne 0) {
        Write-Warn "模型预下载失败（退出码: $predownloadExitCode），测试时可能需要网络访问"
        Write-Warn "这不影响测试运行，但首次加载模型可能较慢"
    } else {
        Write-OK "模型预下载完成（耗时: $($predownloadDuration.ToString('mm\分ss\秒'))）"
    }
} else {
    $cacheExists = docker volume inspect "${ProjectRoot}_hf-cache" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "模型缓存卷已存在，跳过预下载（使用 -Predownload 强制预下载）"
    } else {
        Write-Warn "模型缓存卷不存在，测试时将自动下载模型（首次较慢）"
        Write-Warn "如需预先下载，请添加 -Predownload 参数"
    }
}

# Step 4: 运行测试
Write-Step "Step 4/5: 运行 L3 回归测试"

$testService = switch ($Mode) {
    "sqlite-vec" { "test-sqlite-vec" }
    "all" { "test-all" }
    "integration" { "test-integration" }
    "file" { "test-sqlite-vec" }  # file 模式复用 test-sqlite-vec 服务
}

# [修复] 使用 bash 入口点，运行时安装 pytest-timeout（兼容旧镜像）
# Why: pytest.ini addopts 依赖 pytest-timeout 插件，但旧镜像未包含此包
# 新镜像（requirements-dev.txt 已添加 pytest-timeout）重建后可移除此 workaround
$runArgs = @("-f", $ComposeFile, "run", "--rm", "--entrypoint", "bash")

if ($Mode -eq "file") {
    if ([string]::IsNullOrWhiteSpace($TestFile)) {
        Write-Err "file 模式需要指定 -TestFile 参数"
        exit 1
    }
    Write-Info "测试文件: $TestFile"
    # [修复] 移除 --timeout/--timeout-method，避免与 pytest.ini addopts 重复冲突
    Write-Info "运行命令: docker-compose run --rm --entrypoint bash $testService -c 'pip install pytest-timeout -q && python -m pytest $TestFile -v --tb=short'"
    $testStartTime = Get-Date
    & docker-compose @runArgs $testService -c "pip install pytest-timeout -q && python -m pytest $TestFile -v --tb=short"
} else {
    Write-Info "测试服务: $testService"
    Write-Info "运行命令: docker-compose run --rm --entrypoint bash $testService -c 'pip install pytest-timeout -q && <service default command>'"
    $testStartTime = Get-Date
    # [修复] 通过 bash 安装 pytest-timeout 后执行服务默认命令
    # docker-compose run 不会自动执行 service.command，需手动传入
    if ($Mode -eq "sqlite-vec") {
        & docker-compose @runArgs $testService -c "pip install pytest-timeout -q && python -m pytest tests/unit/test_long_term_memory_embedding.py tests/unit/test_tlm_memory_store.py tests/unit/test_memory_storage_boundary.py tests/unit/test_vector_store_sqlite_vec.py tests/unit/test_memory_vector_store.py -v --tb=short"
    } elseif ($Mode -eq "all") {
        & docker-compose @runArgs $testService -c "pip install pytest-timeout -q && python -m pytest tests/ -v --tb=short -q"
    } elseif ($Mode -eq "integration") {
        & docker-compose @runArgs $testService -c "pip install pytest-timeout -q && python -m pytest tests/integration/ -v --tb=short"
    } else {
        & docker-compose @runArgs $testService
    }
}

$testExitCode = $LASTEXITCODE
$testDuration = (Get-Date) - $testStartTime

# Step 5: 结果报告
Write-Step "Step 5/5: 测试结果报告"

$totalDuration = (Get-Date) - $StartTime

Write-Host ""
Write-Host "┌──────────────────────────────────────────┐" -ForegroundColor Cyan
Write-Host "│            测试结果汇总                  │" -ForegroundColor Cyan
Write-Host "├──────────────────────────────────────────┤" -ForegroundColor Cyan
Write-Host "│ 测试模式:   $Mode".PadRight(42) + "│" -ForegroundColor Cyan
Write-Host "│ 测试退出码: $testExitCode".PadRight(42) + "│" -ForegroundColor $(if ($testExitCode -eq 0) { "Green" } else { "Red" })
Write-Host "│ 测试耗时:   $($testDuration.ToString('mm\分ss\秒'))".PadRight(42) + "│" -ForegroundColor Cyan
Write-Host "│ 总耗时:     $($totalDuration.ToString('mm\分ss\秒'))".PadRight(42) + "│" -ForegroundColor Cyan
Write-Host "└──────────────────────────────────────────┘" -ForegroundColor Cyan
Write-Host ""

if ($testExitCode -eq 0) {
    Write-OK "所有 L3 测试通过！"
} else {
    Write-Err "测试失败（退出码: $testExitCode）"
    Write-Host ""
    Write-Host "排查建议：" -ForegroundColor Yellow
    Write-Host "  1. 查看上方测试输出中的 FAILED 项" -ForegroundColor Yellow
    Write-Host "  2. 单独运行失败测试: .\scripts\run_l3_regression_tests.ps1 -Mode file -TestFile <失败文件>" -ForegroundColor Yellow
    Write-Host "  3. 检查模型缓存: docker-compose -f $ComposeFile run --rm test-sqlite-vec python scripts/predownload_models.py --list" -ForegroundColor Yellow
    Write-Host "  4. 查看容器日志: docker logs <container_id>" -ForegroundColor Yellow
}

# 清理（可选）
if ($Clean) {
    Write-Step "清理 Docker 资源"
    Write-Info "删除停止的容器..."
    docker container prune -f 2>$null | Out-Null
    Write-OK "容器已清理"

    Write-Info "删除模型缓存卷..."
    $volumeName = "${ProjectRoot}_hf-cache"
    docker volume rm $volumeName 2>$null | Out-Null
    Write-OK "卷已清理"
}

Write-Host ""
exit $testExitCode
