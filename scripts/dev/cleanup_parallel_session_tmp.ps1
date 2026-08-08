# 一键清理并行会话遗留的临时目录与未追踪文件
#
# 背景: 并行会话(知识库重构/release 模拟)在 master 工作区留下临时产物:
#   .tmp-release-merge/  (游离 worktree 残留, 已不再被 git 识别, 含 Edge 配置/部署脚本)
#   .sim-local/          (push 竞争模拟脚本遗留)
#   .commit_msg_rdme.md  (提交消息草稿)
# 这些文件均未追踪, 且确认不再需要; 清理前先归档到 backup/ 目录以防误删。
#
# 设计(三义):
# - 不易: 清理清单显式声明(见 $TARGETS), 仅含审计确认的临时产物;
#        知识库重构的功能文件(ingest.py / verify_knowledge_plan_deps.py /
#        docs/zh/知识库重构计划*/ 等)不属于清理范围, 禁止加入清单。
# - 变易: -DryRun 预览 / -SkipBackup 跳过归档 / -Targets 覆盖清单。
# - 简易: 清单驱动, 不存在的路径自动跳过, 幂等可重复执行。
#
# 用法:
#   powershell -File scripts/dev/cleanup_parallel_session_tmp.ps1            # 预览 + 归档 + 清理
#   powershell -File scripts/dev/cleanup_parallel_session_tmp.ps1 -DryRun    # 仅预览
#   powershell -File scripts/dev/cleanup_parallel_session_tmp.ps1 -SkipBackup # 不归档直接清理

[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipBackup,
    [string[]]$Targets = @(
        ".tmp-release-merge",
        ".sim-local",
        ".commit_msg_rdme.md"
    )
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# 归档目录: backup/parallel_session_tmp_YYYYMMDD
$backupDir = Join-Path $repoRoot ("backup/parallel_session_tmp_" + (Get-Date -Format "yyyyMMdd"))

# ── 1. 存在性检查(幂等: 不存在的路径跳过) ──
$existing = $Targets | Where-Object { Test-Path (Join-Path $repoRoot $_) }
if (-not $existing) {
    Write-Host "[OK] 无残留目标, 无需清理" -ForegroundColor Green
    exit 0
}

Write-Host "=== 待清理清单 ===" -ForegroundColor Cyan
$existing | ForEach-Object { Write-Host "  - $_" }

# ── 2. 归档到 backup 目录 ──
if (-not $SkipBackup) {
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
    foreach ($t in $existing) {
        $src = Join-Path $repoRoot $t
        $isDir = (Get-Item $src).PSIsContainer
        if ($isDir) {
            $archive = Join-Path $backupDir ("dot-" + $t.TrimStart('.') + ".tar.gz")
            if (-not $DryRun) {
                & tar -czf $archive -C $repoRoot $t
                if ($LASTEXITCODE -ne 0) { throw "归档失败: $t (tar exit $LASTEXITCODE)" }
            }
            Write-Host "[BACKUP] $t -> $archive" -ForegroundColor Yellow
        } else {
            $dest = Join-Path $backupDir $t
            if (-not $DryRun) { Copy-Item -Force $src $dest }
            Write-Host "[BACKUP] $t -> $dest" -ForegroundColor Yellow
        }
    }
    Write-Host "[BACKUP] 归档目录: $backupDir" -ForegroundColor Green
}

# ── 3. 清理 ──
if ($DryRun) {
    Write-Host "[DRY-RUN] 未执行删除, 以上为将清理项" -ForegroundColor Cyan
    exit 0
}
foreach ($t in $existing) {
    $src = Join-Path $repoRoot $t
    Remove-Item -Recurse -Force $src
    Write-Host "[CLEAN] 已删除: $t" -ForegroundColor Green
}

Write-Host "[DONE] 清理完成, 残留项可还原自: $backupDir" -ForegroundColor Green
