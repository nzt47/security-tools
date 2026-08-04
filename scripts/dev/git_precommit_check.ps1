<#
.SYNOPSIS
    通用 pre-commit 检查脚本：文档链接预检 + 锚点链接回归测试

.DESCRIPTION
    封装两类自动化检查，供 git pre-commit hook 调用（经 sync_precommit_hook.ps1 部署，
    通过 TLM_HOOK_SOURCE_REPO 间接寻址，可复制到任意仓库）：
    1. 文档链接预检：调用 precheck_docs.ps1 -BlockMode（失效链接超过阈值即阻断）
    2. 锚点链接回归测试：pytest tests/unit/test_precheck_docs_anchor_links.py
       仅当目标仓库存在测试文件且 python 可用时运行，避免阻断未配置测试环境的仓库

    任一项检查失败 → exit 1（阻止提交）。

.PARAMETER TargetRepo
    被检查的仓库根目录（默认当前目录）

.EXAMPLE
    .\scripts\dev\git_precommit_check.ps1 -TargetRepo D:\code\my-repo
    .\scripts\dev\git_precommit_check.ps1
#>
param(
    [string]$TargetRepo,
    # 字节级调试模式（即 -Verbose 模式）：PS 5.1 的 -File 调用会把 -Verbose 作为保留参数名
    # 处理、不绑定到显式 switch，因此用 -BomDiag 实现（hook 经 TLM_HOOK_VERBOSE=1 透传）
    [switch]$BomDiag,
    # JSON 结构化输出（面向 ELK/Filebeat 采集）：自身日志 + 透传下游 precheck_docs.ps1，
    # 每条日志输出单行 JSON {"ts","level","event","msg","data"}，
    # msg 字段保留 [BROKEN]/[BLOCK]/[OK] 等文本标记，回归测试断言不受影响。
    [switch]$JsonOutput
)

$ErrorActionPreference = 'Continue'

# -BomDiag → 提升 Verbose 流偏好，并向下游 precheck_docs.ps1 传递
if ($BomDiag) {
    $VerbosePreference = 'Continue'
}

# ── 统一日志输出（与 precheck_docs.ps1 同构；-JsonOutput 时单行 JSON 供 ELK 采集） ──
function Write-Log {
    param(
        # 允许空串（人类可读模式的排版空行）；JSON 模式自动跳过空行
        [string]$Message,
        [ValidateSet('INFO','WARN','ERROR','OK','DEBUG')][string]$Level = 'INFO',
        [string]$Event = 'log',
        [hashtable]$Data = $null,
        [string]$ForegroundColor = $null
    )
    if ($Level -eq 'DEBUG' -and $VerbosePreference -ne 'Continue') { return }
    if ($JsonOutput) {
        if ([string]::IsNullOrWhiteSpace($Message)) { return }
        $entry = [ordered]@{
            ts    = (Get-Date).ToUniversalTime().ToString('o')
            level = $Level
            event = $Event
            msg   = $Message
        }
        if ($Data) { $entry.data = $Data }
        [Console]::Out.WriteLine(($entry | ConvertTo-Json -Compress -Depth 6))
    } else {
        if (-not $ForegroundColor) {
            $ForegroundColor = switch ($Level) {
                'ERROR' { 'Red' }
                'WARN'  { 'Yellow' }
                'OK'    { 'Green' }
                default { $null }
            }
        }
        if ($ForegroundColor) {
            Write-Host $Message -ForegroundColor $ForegroundColor
        } else {
            Write-Host $Message
        }
    }
}

if (-not $TargetRepo) {
    $TargetRepo = (Get-Location).Path
}
if (-not (Test-Path $TargetRepo)) {
    Write-Log "[pre-commit][ERROR] 目标仓库不存在: $TargetRepo" -Level ERROR -Event target_repo
    exit 1
}
Set-Location $TargetRepo

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pass = 0
$fail = 0

# -BomDiag 时向子进程传递调试开关，输出字节级 BOM/路径诊断；-JsonOutput 时一并透传
$verboseArgs = @()
if ($VerbosePreference -eq 'Continue') {
    $verboseArgs = @('-BomDiag')
    Write-Log "字节级调试开启：失效链接将输出 BOM 状态 / 锚点剥离 / 路径解析" -Level DEBUG -Event bomdiag
}
if ($JsonOutput) {
    $verboseArgs += @('-JsonOutput')
}

# ── 检查 1: 文档链接预检（阻塞模式，阈值 0 不可绕过） ──
$precheck = Join-Path $scriptDir 'precheck_docs.ps1'
if (-not (Test-Path $precheck)) {
    Write-Log "[pre-commit][ERROR] precheck_docs.ps1 不存在: $precheck" -Level ERROR -Event missing_precheck
    exit 1
}
Write-Log "`n[1/2] 文档链接预检..." -Level INFO -Event check1_start
& powershell -ExecutionPolicy Bypass -File $precheck -SkipChart -BlockMode -AllowBroken 0 -TargetRepo $TargetRepo @verboseArgs
if ($LASTEXITCODE -eq 0) {
    $pass++
    Write-Log "  [OK] 链接预检通过" -Level OK -Event check1_pass
} else {
    $fail++
    Write-Log "  [FAIL] 链接预检未通过（见上方输出）" -Level ERROR -Event check1_fail
}

# ── 检查 2: 锚点链接回归测试（python + 测试文件可用才运行） ──
$testFile = Join-Path $TargetRepo 'tests\unit\test_precheck_docs_anchor_links.py'
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd -and (Test-Path $testFile)) {
    Write-Log "`n[2/2] 锚点链接回归测试..." -Level INFO -Event check2_start
    & python -m pytest $testFile -q 2>&1 | Select-Object -Last 5
    if ($LASTEXITCODE -eq 0) {
        $pass++
        Write-Log "  [OK] 锚点回归测试通过" -Level OK -Event check2_pass
    } else {
        $fail++
        Write-Log "  [FAIL] 锚点回归测试未通过（见上方输出）" -Level ERROR -Event check2_fail
    }
} else {
    Write-Log "`n[2/2] 锚点回归测试跳过（python 或测试文件不可用）" -Level INFO -Event check2_skip
}

# ── 汇总 ──
Write-Log "`n=== pre-commit 检查汇总 ===" -Level INFO -Event header
Write-Log "  通过: $pass | 失败: $fail" -Event summary -Data @{ pass = $pass; fail = $fail }
if ($fail -gt 0) {
    Write-Log "[BLOCK] 提交被阻止" -Level ERROR -Event block
    exit 1
}
Write-Log "[OK] 预检通过" -Level OK -Event pass
exit 0
