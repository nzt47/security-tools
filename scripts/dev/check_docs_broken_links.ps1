<#
.SYNOPSIS
  文档链接预检诊断：运行链接预检，解析失效链接并输出修复建议

.DESCRIPTION
  背景（2026-08-10 docs 失效链接修复复盘）：
  - CI docs-precheck-tests 的链接预检（阻塞阈值 0）在无 [skip ci] 的 push 时全量触发，
    失效链接根因通常是"引用的文档只在 develop 分支存在"或"从未创建"。
  - 本脚本将 precheck 输出中的 [BROKEN] 行解析为结构化诊断表：
      目标文件本地是否存在 / develop 分支是否存在 / 建议处置
  - 用法（参数对齐 CI 的 git_precommit_check.ps1 内部调用 -SkipChart）:
      pwsh -File scripts/dev/check_docs_broken_links.ps1 [-TargetRepo <path>]

  - 退出码：0 = 无失效链接；1 = 存在失效链接（便于接入 hook / CI）

  - 关联文档：docs/wiki/docs_link_precheck_fix_wiki.md
#>
param(
    [string]$TargetRepo = (Get-Location).Path,
    [switch]$SkipCheck   # 跳过 precheck 运行，仅解析上次输出（调试用）
)

$ErrorActionPreference = "Stop"

function Write-Info  { Write-Host $args[0] }
function Write-Ok    { Write-Host $args[0] -ForegroundColor Green }
function Write-Warn  { Write-Host $args[0] -ForegroundColor Yellow }
function Write-Fail  { Write-Host $args[0] -ForegroundColor Red }

if (-not (Test-Path (Join-Path $TargetRepo "scripts/dev/precheck_docs.ps1"))) {
    Write-Fail "[FAIL] 未找到 precheck_docs.ps1，请确认在仓库根目录执行：pwsh -File scripts/dev/check_docs_broken_links.ps1"
    exit 1
}

# ── 1. 运行链接预检（参数对齐 CI）──────────────────────────────
$precheck = Join-Path $TargetRepo "scripts/dev/precheck_docs.ps1"
if (-not $SkipCheck) {
    Write-Info "=== 运行链接预检（precheck_docs.ps1 -BlockMode -AllowBroken 0 -SkipChart）==="
    # 【变易】跨平台兼容：ubuntu runner（ci.yml code-quality）无 powershell.exe，
    # 优先用 pwsh（PowerShell 7），Windows PowerShell 5.1 环境回退 powershell。
    $shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }
    $output = & $shell -NoProfile -ExecutionPolicy Bypass -File $precheck `
        -TargetRepo $TargetRepo -BlockMode -AllowBroken 0 -SkipChart 2>&1
    $output | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Warn "[WARN] precheck 退出码非 0（$LASTEXITCODE），继续解析输出诊断" }
} else {
    Write-Info "=== 跳过 precheck（-SkipCheck），解析上次输出 ==="
    $output = @()
}

# ── 2. 解析 [BROKEN] 行 ────────────────────────────────────────
$broken = @()
foreach ($line in $output) {
    # 兼容格式：`[ERROR] [BROKEN] host: target` 或 `[BROKEN] host: target`
    if ($line -match "\[BROKEN\]\s*(.+?):\s*(\S.*)$") {
        $broken += [pscustomobject]@{
            Host   = $Matches[1].Trim()
            Target = $Matches[2].Trim()
        }
    }
}

if ($broken.Count -eq 0) {
    Write-Ok "`n[PASS] 未发现失效链接，文档链接全部有效"
    exit 0
}

# ── 3. 逐条诊断：本地存在? / develop 分支存在? / 建议 ───────────
Write-Warn "`n[BLOCK] 发现 $($broken.Count) 个失效链接，诊断如下：`n"
$repoRoot = (Resolve-Path $TargetRepo).Path.TrimEnd("\")
$rows = foreach ($b in $broken) {
    # precheck 输出的 host 多为纯文件名（如 tmp_diag_test.md），需全仓库定位真实目录；
    # 若 host 本身含路径分隔符则直接使用。
    $hostPath = if ($b.Host -match "[\\/]") {
        Join-Path $TargetRepo $b.Host
    } else {
        $found = Get-ChildItem -Path $TargetRepo -Recurse -Filter $b.Host -File -ErrorAction SilentlyContinue
        if ($found.Count -eq 0) {
            Join-Path $TargetRepo $b.Host   # 找不到时兜底按仓库根解析
        } else {
            $found[0].FullName
        }
    }
    # 目标绝对化：相对 host 所在目录解析（与 precheck 的 Path::Combine 规则一致）
    $hostDir    = Split-Path $hostPath
    $targetAbs  = [System.IO.Path]::GetFullPath((Join-Path $hostDir $b.Target))
    $localExists = Test-Path $targetAbs

    # 目标不在仓库内（外部/绝对路径）时跳过 develop 检查
    $repoRel = ""
    if ($targetAbs.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        $repoRel = $targetAbs.Substring($repoRoot.Length + 1).Replace("\", "/")
    }
    $devExists = $false
    if ($repoRel) {
        git -C $TargetRepo cat-file -e "develop:$repoRel" 2>$null
        $devExists = ($LASTEXITCODE -eq 0)
    }

    $suggestion = if (-not $repoRel) {
        "目标不在仓库内（外部路径），检查引用是否应指向外部 URL/资源"
    } elseif ($localExists) {
        "本地存在（检查路径解析是否与 precheck 一致）"
    } elseif ($devExists) {
        "从 develop 检出：git checkout develop -- $repoRel"
    } else {
        "develop 也不存在：删除 $($b.Host) 中的引用，或补建目标文档"
    }

    [pscustomobject]@{
        "失效链接(host)"   = $b.Host
        "目标"             = $repoRel
        "本地"             = $(if ($localExists) { "✓" } else { "✗" })
        "develop 分支"     = $(if ($devExists) { "✓" } else { "✗" })
        "建议"             = $suggestion
    }
}
$rows | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Host

# ── 4. 汇总 ────────────────────────────────────────────────────
$checkoutable = ($rows | Where-Object { $_.建议 -like "从 develop 检出*" }).Count
$missingBoth  = ($rows | Where-Object { $_.建议 -like "develop 也不存在*" }).Count
Write-Warn "`n汇总：共 $($broken.Count) 个失效链接 | $checkoutable 个可检出 | $missingBoth 个需删除/补建引用"
Write-Fail "[BLOCK] 链接预检未通过（见上方诊断表），按建议处置后重跑本脚本验证"
exit 1
