<#
.SYNOPSIS
    在 L3 Docker 容器内预下载 HuggingFace 模型（bge + MiniLM）到 hf-cache 卷

.DESCRIPTION
    修复 L3 回归"存储后端降级 json"的根因：镜像内 hf-cache 卷无模型缓存。
    通过国内镜像站 hf-mirror.com（HF_ENDPOINT）在容器内下载模型到
    /app/.hf_cache/hub，使 vector_store._is_model_fully_cached 命中缓存 →
    model_fully_cached=True → st_ok=True → sqlite_vec 后端启用。

    复用仓库现成脚本（无需新写 Python 逻辑）：
      /app/scripts/predownload_models.py  （镜像内已打包）

.PARAMETER Models
    要下载的模型列表（默认：VectorStore 默认 MiniLM + 中文 bge-small）
    说明：paraphrase-multilingual-MiniLM-L12-v2 是 vector_store.py 默认
    编码模型（MODEL_NAME），bge-small-zh-v1.5 是中文 embedding 路径。

.PARAMETER Mirror
    HuggingFace 国内镜像站（默认 hf-mirror.com；huggingface.co 直连不通）

.PARAMETER SkipVerify
    跳过下载后的诊断验证（默认会运行 diag_sqlite_vec_fallback.py 校验
    model_fully_cached / st_ok / backend）

.EXAMPLE
    # 默认下载 MiniLM + bge-small-zh 并验证
    powershell -ExecutionPolicy Bypass -File scripts/predownload_l3_hf_cache.ps1

.EXAMPLE
    # 追加下载 all-MiniLM-L6-v2
    powershell -ExecutionPolicy Bypass -File scripts/predownload_l3_hf_cache.ps1 `
        -Models "paraphrase-multilingual-MiniLM-L12-v2","all-MiniLM-L6-v2","BAAI/bge-small-zh-v1.5"

.NOTES
    依赖：Docker daemon 已启动、agent-test 镜像已构建（docker-compose.linux-test.yml）
#>
[CmdletBinding()]
param(
    [string[]]$Models = @(
        "paraphrase-multilingual-MiniLM-L12-v2",
        "BAAI/bge-small-zh-v1.5"
    ),
    [string]$Mirror = "https://hf-mirror.com",
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$ComposeFile = "docker-compose.linux-test.yml"
$LogFile = Join-Path $env:TEMP "l3_predownload.log"

function Write-Step([string]$Msg) {
    Write-Host "`n=== $Msg ===" -ForegroundColor Cyan
}

function Assert-Docker {
    docker version --format "{{.Server.Version}}" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon 未就绪，请先启动 Docker Desktop"
    }
}

# ── 0. 前置检查 ──
Write-Step "前置检查"
Assert-Docker
Write-Host "[OK] Docker daemon 已就绪"

$image = docker images agent-test --format "{{.Repository}}:{{.Tag}}" 2>$null | Select-Object -First 1
if (-not $image) {
    Write-Host "[WARN] 未找到 agent-test 镜像，先构建 L3 镜像（耗时较长）..." -ForegroundColor Yellow
    docker compose -f $ComposeFile build --progress=plain test *> $LogFile
    if ($LASTEXITCODE -ne 0) { throw "镜像构建失败，详见 $LogFile" }
}
Write-Host "[OK] L3 镜像: $image"

# ── 1. 下载前：查看卷内已有缓存 ──
Write-Step "下载前缓存状态"
docker compose -f $ComposeFile run --rm --no-deps --entrypoint python test `
    /app/scripts/predownload_models.py --list 2>&1 | Select-Object -Last 10

# ── 2. 容器内预下载（HF_ENDPOINT 走国内镜像） ──
# 【不易】HF_ENDPOINT 必须显式传入，否则 huggingface_hub 直连 huggingface.co
# （容器内 Connection refused → 下载失败 → 缓存缺失 → 降级 json）。
# predownload_models.py 内部 _set_cache_env 会正确设置
# TRANSFORMERS_CACHE/SENTENCE_TRANSFORMERS_HOME = {HF_HOME}/hub（覆盖 compose 的
# /app/.hf_cache 无 hub 后缀），与 _is_model_fully_cached 检查路径一致。
Write-Step "容器内预下载模型（镜像: $Mirror）"
Write-Host "模型列表: $($Models -join ', ')" -ForegroundColor Gray

$modelArgs = $Models | ForEach-Object { "`"$_`"" } | Join-String -Separator " "
$cmd = "docker compose -f $ComposeFile run --rm --no-deps " +
       "-e HF_ENDPOINT=$Mirror -e HF_HOME=/app/.hf_cache " +
       "--entrypoint python test /app/scripts/predownload_models.py --models $modelArgs"
Write-Host "执行: $cmd" -ForegroundColor Gray

# 直接执行（不用 Invoke-Expression，模型名安全但保持可读）
docker compose -f $ComposeFile run --rm --no-deps `
    -e "HF_ENDPOINT=$Mirror" `
    -e "HF_HOME=/app/.hf_cache" `
    --entrypoint python test `
    /app/scripts/predownload_models.py --models @Models

if ($LASTEXITCODE -ne 0) {
    throw "预下载失败（exit=$LASTEXITCODE），检查网络或镜像站可用性"
}

# ── 3. 下载后：确认缓存落盘 ──
Write-Step "下载后缓存状态"
docker compose -f $ComposeFile run --rm --no-deps --entrypoint python test `
    /app/scripts/predownload_models.py --list 2>&1 | Select-Object -Last 12

# ── 4. 验证判定逻辑（st_ok / model_fully_cached / backend） ──
if (-not $SkipVerify) {
    Write-Step "运行诊断脚本验证判定逻辑"
    docker compose -f $ComposeFile run --rm --no-deps --entrypoint python test `
        /app/scripts/diag_sqlite_vec_fallback.py 2>&1 | Select-String `
        -Pattern "model_fully_cached|encoder_ok|st_ok|backend|结论|存储后端" `
        -Context 0,1
}

Write-Host "`n[OK] 预下载完成。后续 L3 测试将命中模型缓存（st_ok=True → sqlite_vec）。" -ForegroundColor Green
