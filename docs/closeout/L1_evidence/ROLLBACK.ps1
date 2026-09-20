# 🚨 L1 生产审计链修复 — 一键回滚
# 用法：在仓库根执行  pwsh -File _ci_logs\l1_repair\ROLLBACK.ps1
# 作用：把 data\audit\ 下的审计文件恢复为修复前状态（来自独立备份目录）。
# 风险：会丢弃修复后新写入的审计记录。回滚前请先确认没有进程正在写审计链。

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repo

$bk = (Get-Content (Join-Path $repo '_ci_logs\LAST_AUDIT_BACKUP.txt') -Raw).Trim()
if (-not (Test-Path $bk)) { throw "备份目录不存在: $bk" }

Write-Host "回滚源: $bk"
Write-Host "回滚目标: $repo\data\audit"

# 1) 先把「当前（修复后）」状态另存，便于再次前进
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$fwd = Join-Path $repo "_ci_logs\audit_postrepair_$ts"
New-Item -ItemType Directory -Force -Path $fwd | Out-Null
Copy-Item "$repo\data\audit\*" $fwd -Force -Recurse
Write-Host "已另存修复后状态: $fwd"

# 2) 从备份恢复
foreach ($f in @('audit_chain.db','audit_chain.db.seqjournal','audit_chain.db.lock',
                 'daily_roots.jsonl','audit_signing_key.pem')) {
    $src = Join-Path $bk $f
    if (Test-Path $src) {
        try { (Get-Item "$repo\data\audit\$f" -ErrorAction SilentlyContinue).IsReadOnly = $false } catch {}
        Copy-Item $src "$repo\data\audit\$f" -Force
        Write-Host "  restored $f"
    }
}
# 3) 清理修复过程可能留下的 WAL/SHM
Remove-Item "$repo\data\audit\audit_chain.db-wal","$repo\data\audit\audit_chain.db-shm" -Force -ErrorAction SilentlyContinue

Write-Host "`n回滚完成。验证："
python scripts\verify_audit_chain.py
Write-Host "（回滚后预期 exit=1 —— 因为修复前的链本身就是断的）"
