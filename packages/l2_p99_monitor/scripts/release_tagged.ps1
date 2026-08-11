<#
l2-p99-monitor 子包一键发布（打 tag + 推送 + PyPI 上传）

流程（遵守仓库既有惯例）：
  1. 版本号校验（SemVer：X.Y.Z）
  2. 预检：子包源码目录无未提交改动；tag 尚未创建；twine 与 PyPI 凭据就绪
  3. 更新版本号（pyproject.toml + __init__.py）并提交——commit 必须以 release(pypi) 开头，
     release-auto 的 guard 以此判定"子包发布"并跳过主项目自动发布（契约，不得改动）
  4. 打 v<version> annotated tag，先推分支再推 tag
  5. 构建 + twine 上传（复用同目录 release.ps1，不复制实现）

【不易】commit 前缀 release(pypi) 是主项目发布守卫的判定契约，不得改动
【变易】-DryRun 全程只打印不执行；-TestPyPI 上传到 TestPyPI；预检项逐项容错（不因一项失败中断后续检查）
【简易】打 tag/提交逻辑新写，构建上传复用既有 release.ps1

用法：
  .\scripts\release_tagged.ps1 -Version 1.0.3                    # 完整发布
  .\scripts\release_tagged.ps1 -Version 1.0.3 -DryRun            # 预演（不执行任何写操作）
  .\scripts\release_tagged.ps1 -Version 1.0.3 -TestPyPI          # 上传到 TestPyPI

注意：PyPI 上传是外部不可逆操作，正式执行前请先 -DryRun 核对流程。
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [switch]$DryRun,
    [switch]$TestPyPI
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent $PSScriptRoot    # packages/l2_p99_monitor
$RepoRoot = Split-Path -Parent $PackageDir         # 仓库根目录
$env:PYTHONIOENCODING = "utf-8"

# ── 1. 版本号校验 ──────────────────────────────────────────────
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "[预检✗] 版本号格式非法（需 X.Y.Z）: $Version" -ForegroundColor Red
    exit 1
}
$Tag = "v$Version"

Write-Host "l2-p99-monitor $Tag 发布（DryRun=$DryRun TestPyPI=$TestPyPI）" -ForegroundColor Cyan

# ── 2. 预检（逐项容错，失败即退出） ─────────────────────────────
# 2.1 子包源码目录无未提交改动（scripts/ 为发布工具本身，不参与检查）
$dirty = git -C $PackageDir status --porcelain -- . ':(exclude)scripts'
if ($dirty) {
    Write-Host "[预检✗] 子包源码目录存在未提交改动，先提交再发布:" -ForegroundColor Red
    $dirty | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}
# 2.2 tag 尚未创建（防覆盖既有发布）
if (git -C $RepoRoot tag --list $Tag) {
    Write-Host "[预检✗] tag $Tag 已存在，放弃发布（防覆盖）" -ForegroundColor Red
    exit 1
}
# 2.3 twine 与 PyPI 凭据（~/.pypirc 或 TWINE_USERNAME/TWINE_PASSWORD/TWINE_API_TOKEN）
if (-not (Get-Command twine -ErrorAction SilentlyContinue)) {
    Write-Host "[预检✗] twine 未安装，先执行: pip install twine" -ForegroundColor Red
    exit 1
}
$hasPypirc = Test-Path (Join-Path $env:USERPROFILE ".pypirc")
$hasTokenEnv = [bool]$env:TWINE_USERNAME -or [bool]$env:TWINE_PASSWORD -or [bool]$env:TWINE_API_TOKEN
if (-not ($hasPypirc -or $hasTokenEnv)) {
    Write-Host "[预检✗] 未发现 PyPI 凭据（~/.pypirc 或 TWINE_* 环境变量），twine upload 将失败" -ForegroundColor Red
    exit 1
}
# 2.4 仓库其他未提交改动仅警告（tag 内容以 HEAD 为准，不阻断）
$repoDirty = git -C $RepoRoot status --porcelain
if ($repoDirty) {
    Write-Host "[预检⚠] 仓库存在其他未提交改动（不阻断，tag 内容以 HEAD 为准）" -ForegroundColor Yellow
}
Write-Host "[预检✓] 版本 $Version / 子包目录干净 / tag 可用 / twine+凭据就绪" -ForegroundColor Green

# ── 3. 更新版本号并提交（release(pypi) 前缀） ─────────────────────
Write-Host "[1/3] 更新版本号到 $Version 并提交 ..."
$PyprojectPath = Join-Path $PackageDir "pyproject.toml"
$InitPath = Join-Path $PackageDir "l2_p99_monitor\__init__.py"
$CommitMsg = "release(pypi): $Tag 发布 l2-p99-monitor 到 PyPI"
if (-not $DryRun) {
    $c1 = Get-Content $PyprojectPath -Raw
    $c1 = $c1 -replace 'version = "[\d.]+"', "version = `"$Version`""
    Set-Content -Path $PyprojectPath -Value $c1 -NoNewline
    $c2 = Get-Content $InitPath -Raw
    $c2 = $c2 -replace '__version__ = "[\d.]+"', "__version__ = `"$Version`""
    Set-Content -Path $InitPath -Value $c2 -NoNewline
    git -C $RepoRoot add packages/l2_p99_monitor/pyproject.toml packages/l2_p99_monitor/l2_p99_monitor/__init__.py
    git -C $RepoRoot commit -m $CommitMsg
    Write-Host "  ✓ 版本号已更新并提交（release-auto guard 将判定为子包发布并跳过主项目）" -ForegroundColor Green
} else {
    Write-Host "  (DryRun) 将更新 pyproject/__init__ 版本号并提交: $CommitMsg" -ForegroundColor Yellow
}

# ── 4. 打 tag 并推送 ──────────────────────────────────────────
Write-Host "[2/3] 打 tag $Tag 并推送 ..."
if (-not $DryRun) {
    git -C $RepoRoot tag -a $Tag -m "l2-p99-monitor $Tag PyPI 发布"
    git -C $RepoRoot push origin HEAD
    git -C $RepoRoot push origin $Tag
    Write-Host "  ✓ 分支与 tag 已推送" -ForegroundColor Green
} else {
    Write-Host "  (DryRun) 将创建 annotated tag $Tag 并推送分支 + tag" -ForegroundColor Yellow
}

# ── 5. 构建 + 上传（复用既有 release.ps1） ───────────────────────
Write-Host "[3/3] 构建并上传到 $(if ($TestPyPI) {'TestPyPI'} else {'PyPI'}) ..."
if (-not $DryRun) {
    $relArgs = @("-Version", $Version)
    if ($TestPyPI) { $relArgs += "-TestPyPI" }
    & (Join-Path $PSScriptRoot "release.ps1") @relArgs
    exit $LASTEXITCODE
} else {
    $rel = Join-Path $PSScriptRoot "release.ps1"
    $extra = if ($TestPyPI) { " -TestPyPI" } else { "" }
    Write-Host "  (DryRun) 将执行: $rel -Version $Version$extra" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "(DryRun) 预演结束，未执行任何写操作。" -ForegroundColor Green
}
