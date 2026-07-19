<#
.SYNOPSIS
    一键清理 TEMP 目录中 BFG/GitHub Actions 清理过程产生的非敏感残留文件
    生成日期：2026-07-20
    安全设计：白名单机制，只删除已知非敏感文件，不误删用户其他文件

.DESCRIPTION
    本脚本清理以下类别的 TEMP 残留文件：
      1. BFG 清理过程脚本/快照（bfg_*.ps1, bfg_*.txt, bfg_*.py）
      2. BFG 备份产物（patch, mirror_path, master_protection.json）
      3. GitHub Actions 清理过程文件（cleanup_github_actions.ps1, generate_cleanup_report.ps1）
      4. 密钥验证报告（key_revocation_report_*.txt）
      5. 日志文件（gh_actions_cleanup_*.log）

    安全特性：
      - 白名单机制：只删除预定义的文件模式，不扫描/删除其他文件
      - -DryRun 预演模式：仅显示将删除什么，不实际删除
      - -KeepUseful 保留有用文件：cleanup_github_actions.ps1 等可复用脚本
      - 每个文件删除前显示大小和修改时间

.PARAMETER DryRun
    仅显示将要删除的文件，不实际删除（推荐先用此模式预演）

.PARAMETER KeepUseful
    保留可复用脚本（cleanup_github_actions.ps1, generate_cleanup_report.ps1）
    默认为 $false（全部清理）

.PARAMETER TempPath
    TEMP 目录路径，默认 $env:TEMP

.EXAMPLE
    # 预演（推荐先执行）
    .\cleanup_temp_files.ps1 -DryRun

.EXAMPLE
    # 正式执行（保留可复用脚本）
    .\cleanup_temp_files.ps1 -KeepUseful

.EXAMPLE
    # 正式执行（全部清理）
    .\cleanup_temp_files.ps1

.NOTES
    关联文档：docs/security/GH_ACTIONS_CLEANUP_REPORT_20260720.md
    前置条件：BFG 清理 + GitHub Actions 清除已完成
#>

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$KeepUseful,
    [string]$TempPath = $env:TEMP
)

# ========== 白名单定义 ==========
# 只删除以下预定义模式的文件，不扫描其他文件
$script:Whitelist = @(
    # --- BFG 清理过程脚本 ---
    @{ Pattern = "bfg_before_snapshot.ps1";       Category = "BFG-script";      Sensitive = $false }
    @{ Pattern = "bfg_before_snapshot.txt";       Category = "BFG-snapshot";    Sensitive = $false }
    @{ Pattern = "bfg_backup_and_mirror.ps1";     Category = "BFG-script";      Sensitive = $false }
    @{ Pattern = "bfg_verify_cleanup.ps1";        Category = "BFG-script";      Sensitive = $false }
    @{ Pattern = "bfg_after_snapshot.txt";        Category = "BFG-snapshot";    Sensitive = $false }
    @{ Pattern = "bfg_collect_final_metrics.ps1"; Category = "BFG-script";      Sensitive = $false }
    @{ Pattern = "bfg_local_after_metrics.txt";   Category = "BFG-metrics";     Sensitive = $false }
    @{ Pattern = "check_commit_reachability.ps1"; Category = "BFG-script";      Sensitive = $false }
    @{ Pattern = "analyze_workflows.ps1";         Category = "CI-CD-analysis";  Sensitive = $false }

    # --- BFG 备份产物 ---
    @{ Pattern = "bfg_local_changes_20260719.patch"; Category = "BFG-patch";    Sensitive = $false }
    @{ Pattern = "bfg_mirror_path.txt";          Category = "BFG-path-record";  Sensitive = $false }
    @{ Pattern = "master_protection.json";       Category = "GitHub-protection"; Sensitive = $false }

    # --- GitHub Actions 清理过程 ---
    @{ Pattern = "gh_actions_cleanup_*.log";     Category = "GA-cleanup-log";   Sensitive = $false }
    @{ Pattern = "generate_cleanup_report.ps1";  Category = "GA-report-script"; Sensitive = $false }
    @{ Pattern = "cleanup_github_actions.ps1";   Category = "GA-cleanup-script"; Sensitive = $false }

    # --- 密钥验证报告 ---
    @{ Pattern = "key_revocation_report_*.txt";  Category = "key-revocation-report"; Sensitive = $false }
)

# 可复用脚本白名单（-KeepUseful 时保留）
$script:UsefulScripts = @(
    "cleanup_github_actions.ps1",
    "generate_cleanup_report.ps1"
)

# ========== 统计 ==========
$script:Stats = @{
    TotalFound   = 0
    TotalDeleted = 0
    TotalFailed  = 0
    TotalSize    = 0
    ByCategory   = @{}
    StartTime    = Get-Date
}

# ========== 工具函数 ==========
function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Msg)
    Write-Host "  [OK]   $Msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Msg)
    Write-Host "  [WARN] $Msg" -ForegroundColor DarkYellow
}

function Write-Err {
    param([string]$Msg)
    Write-Host "  [FAIL] $Msg" -ForegroundColor Red
}

function Get-FileList {
    param([string]$Path, [array]$Patterns)
    $results = @()
    foreach ($p in $Patterns) {
        $files = Get-ChildItem -Path $Path -Filter $p.Pattern -File -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            # -KeepUseful 时跳过可复用脚本
            if ($KeepUseful -and $script:UsefulScripts -contains $f.Name) {
                continue
            }
            $results += [PSCustomObject]@{
                File     = $f
                Pattern  = $p.Pattern
                Category = $p.Category
            }
        }
    }
    return $results
}

# ========== 主流程 ==========
Write-Section "TEMP Cleanup Script"
Write-Host "  TempPath:  $TempPath"
Write-Host "  Mode:      $(if ($DryRun) { 'DRY-RUN (no delete)' } else { 'EXECUTE' })"
Write-Host "  KeepUseful: $(if ($KeepUseful) { 'Yes' } else { 'No' })"

if (-not (Test-Path $TempPath)) {
    Write-Err "TEMP path not found: $TempPath"
    exit 1
}

Write-Section "Stage 1: Scan Whitelist Files"
$fileList = Get-FileList -Path $TempPath -Patterns $script:Whitelist

if ($fileList.Count -eq 0) {
    Write-Ok "No whitelist files found, nothing to clean"
    Write-Section "Summary"
    Write-Host "  Scanned: 0"
    Write-Host "  Elapsed: $([math]::Round(((Get-Date) - $script:Stats.StartTime).TotalSeconds, 1)) s"
    exit 0
}

Write-Host "  Found $($fileList.Count) file(s):" -ForegroundColor Yellow
Write-Host ""
$headerLine = "  {0,-40} {1,10} {2,-22} {3,-20}" -f "FileName", "Size", "Category", "Modified"
Write-Host $headerLine -ForegroundColor DarkGray
Write-Host ("  " + ("-" * 95)) -ForegroundColor DarkGray

foreach ($item in $fileList) {
    $f = $item.File
    $sizeKB = [math]::Round($f.Length / 1KB, 1)
    $modTime = $f.LastWriteTime.ToString("MM-dd HH:mm")
    $rowLine = "  {0,-40} {1,8} KB {2,-22} {3,-20}" -f $f.Name, $sizeKB, $item.Category, $modTime
    Write-Host $rowLine
    $script:Stats.TotalFound++
    $script:Stats.TotalSize += $f.Length
    if (-not $script:Stats.ByCategory.ContainsKey($item.Category)) {
        $script:Stats.ByCategory[$item.Category] = 0
    }
    $script:Stats.ByCategory[$item.Category]++
}

$totalSizeMB = [math]::Round($script:Stats.TotalSize / 1MB, 2)
Write-Host ""
Write-Host "  Total: $($fileList.Count) file(s), $totalSizeMB MB" -ForegroundColor Yellow

# 按分类汇总
Write-Host ""
Write-Host "  By Category:" -ForegroundColor DarkGray
foreach ($cat in $script:Stats.ByCategory.Keys | Sort-Object) {
    Write-Host "    - $cat : $($script:Stats.ByCategory[$cat]) file(s)" -ForegroundColor DarkGray
}

if ($KeepUseful) {
    Write-Host ""
    Write-Warn "Kept useful scripts (-KeepUseful):"
    foreach ($s in $script:UsefulScripts) {
        $path = Join-Path $TempPath $s
        if (Test-Path $path) {
            Write-Host "    - $s ($([math]::Round((Get-Item $path).Length / 1KB, 1)) KB)" -ForegroundColor DarkYellow
        }
    }
}

if ($DryRun) {
    Write-Section "Stage 2: DRY-RUN (no actual delete)"
    Write-Warn ">>> Preview complete, no files deleted <<<"
    Write-Host ""
    Write-Host "  To execute for real, remove -DryRun:" -ForegroundColor Yellow
    Write-Host "    pwsh -File $($MyInvocation.MyCommand.Path)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  To keep useful scripts:" -ForegroundColor Yellow
    Write-Host "    pwsh -File $($MyInvocation.MyCommand.Path) -KeepUseful" -ForegroundColor Yellow
} else {
    Write-Section "Stage 2: Delete Files"
    $idx = 0
    $total = $fileList.Count
    foreach ($item in $fileList) {
        $idx++
        $f = $item.File
        try {
            Remove-Item -Path $f.FullName -Force -ErrorAction Stop
            Write-Ok "[$idx/$total] Deleted $($f.Name)"
            $script:Stats.TotalDeleted++
        } catch {
            Write-Err "[$idx/$total] Failed to delete $($f.Name) : $_"
            $script:Stats.TotalFailed++
        }
    }
}

# ========== 汇总 ==========
Write-Section "Cleanup Summary"
$elapsed = [math]::Round(((Get-Date) - $script:Stats.StartTime).TotalSeconds, 1)
Write-Host "  Scanned:      $($script:Stats.TotalFound)"
Write-Host "  Deleted:      $($script:Stats.TotalDeleted)"
Write-Host "  Failed:       $($script:Stats.TotalFailed)"
Write-Host "  Space freed:  $totalSizeMB MB"
Write-Host "  Elapsed:      $elapsed s"

if ($DryRun) {
    Write-Host ""
    Write-Warn ">>> DRY-RUN complete, nothing actually deleted <<<"
} else {
    Write-Host ""
    Write-Ok ">>> Cleanup complete <<<"
}

# ========== 验证 ==========
if (-not $DryRun) {
    Write-Section "Stage 3: Verify Cleanup"
    $remaining = Get-FileList -Path $TempPath -Patterns $script:Whitelist
    if ($remaining.Count -eq 0) {
        Write-Ok "All whitelist files cleaned"
    } else {
        Write-Warn "Still $($remaining.Count) file(s) remaining:"
        $remaining | ForEach-Object {
            Write-Host "    - $($_.File.Name)" -ForegroundColor DarkYellow
        }
    }
}

Write-Host ""
