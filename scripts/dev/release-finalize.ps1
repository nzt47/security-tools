# release-finalize.ps1 — 发布收尾一键脚本（合并归档 PR → 前移 v1.0.0 → 双端同步）
# 用法:
#   pwsh -File scripts/dev/release-finalize.ps1                     # dry-run: 仅检查 PR 状态与前移预览
#   pwsh -File scripts/dev/release-finalize.ps1 -Execute            # 全绿则合并 + 前移（GitHub）
#   pwsh -File scripts/dev/release-finalize.ps1 -Execute -SyncGitee # 合并 + 前移 + gitee 同步
#   pwsh -File scripts/dev/release-finalize.ps1 -PrNumber 371 -Execute -SyncGitee
#
# 行为:
#   - 检查 PR 合并状态与 checks（fail/pending/pass/skip 汇总）
#   - 未全绿时轮询（-WatchMinutes，默认 30 分钟）；基础设施故障（Service Unavailable/
#     Failed to resolve action download info）自动 rerun（最多 3 次），PR 相关失败立即停止
#   - 全绿后 gh pr merge --squash → 调用 advance_v100_tag.ps1 -Execute [-SyncGitee]
#   - 全程日志写入 .tmp/release-finalize-<pr>.log（相对仓库根）

param(
    [int]$PrNumber = 371,
    [int]$WatchMinutes = 30,
    [switch]$Execute,
    [switch]$SyncGitee
)

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repoRoot '.tmp'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "release-finalize-$PrNumber.log"
$ts = { param($m) "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" }
"$(& $ts "release-finalize started (PR #$PrNumber, Execute=$Execute, SyncGitee=$SyncGitee, watch=${WatchMinutes}min)")" | Out-File $log -Encoding utf8

function Log([string]$m) { "$(& $ts $m)" | Out-File $log -Append -Encoding utf8 }

# ---------- 1. 检查 PR 状态 ----------
$prInfo = gh pr view $PrNumber --json state,mergeable,mergeStateStatus 2>&1 | ConvertFrom-Json
Log "PR #$PrNumber state=$($prInfo.state) mergeable=$($prInfo.mergeable) mergeStateStatus=$($prInfo.mergeStateStatus)"
if ($prInfo.state -ne 'OPEN') { Log "PR 非 OPEN，退出"; exit 1 }

# ---------- 2. 前移预览（始终执行 dry-run 逻辑，-Execute 后才会真正改引用） ----------
Log "前移预览:"
pwsh -NoProfile -File (Join-Path $PSScriptRoot 'advance_v100_tag.ps1') 2>&1 | ForEach-Object { Log "  $_" }

if (-not $Execute) {
    Log "dry-run 模式，未做任何修改。合并前移命令: pwsh -File scripts/dev/release-finalize.ps1 -Execute -SyncGitee"
    Get-Content $log | Out-String
    exit 0
}

# ---------- 3. 轮询至全绿（或超时/失败） ----------
$deadline = (Get-Date).AddMinutes($WatchMinutes)
$rerunCount = 0
$state = 'PENDING'
while ($state -eq 'PENDING' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 60
    $checks = (gh pr checks $PrNumber 2>&1 | Out-String)
    $hasPending = $checks -match 'pending'
    $hasFail = $checks -match '\bfail\b|\berror\b'
    Log "poll pending=$hasPending fail=$hasFail rerunCount=$rerunCount"
    if ($hasFail) {
        $infraOnly = $true
        foreach ($fl in ($checks -split "`n" | Where-Object { $_ -match '^\S+\s+fail\s+' })) {
            $jobId = (($fl -split '\s+')[-1]) -replace '^https://github.com/nzt47/security-tools/actions/runs/.*/job/', ''
            if ($jobId -match '^\d+$') {
                $jobLog = (gh run view --job $jobId --log-failed 2>&1 | Out-String)
                if ($jobLog -notmatch 'Service Unavailable|Failed to resolve action download info') {
                    $infraOnly = $false
                    Log "NON-INFRA failure in job $jobId — 停止"
                }
            }
        }
        if ($infraOnly -and $rerunCount -lt 3) {
            $rerunCount++
            Log "基础设施故障，rerun #$rerunCount ..."
            foreach ($fl in ($checks -split "`n" | Where-Object { $_ -match '^\S+\s+fail\s+' })) {
                $jobId = (($fl -split '\s+')[-1]) -replace '^https://github.com/nzt47/security-tools/actions/runs/.*/job/', ''
                if ($jobId -match '^\d+$') { gh run rerun --job $jobId 2>&1 | ForEach-Object { Log "  rerun: $_" } }
            }
            continue
        }
        $state = 'FAILED'; break
    }
    if (-not $hasPending) { $state = 'GREEN'; break }
}
if ($state -ne 'GREEN') {
    Log "最终状态: $state — 未合并（非全绿），请稍后重试或手动 gh pr merge $PrNumber --squash"
    Get-Content $log | Out-String
    exit 2
}

# ---------- 4. 合并 + 前移 ----------
Log "=== CI 全绿，合并 PR #$PrNumber (squash) ==="
gh pr merge $PrNumber --squash 2>&1 | ForEach-Object { Log "  $_" }
Log "merge rc=$LASTEXITCODE"
if ($LASTEXITCODE -ne 0) { exit 3 }
Start-Sleep -Seconds 5
Log "=== 前移 v1.0.0 (GitHub $(if ($SyncGitee) {'+ gitee'} else {'仅 GitHub'})) ==="
if ($SyncGitee) {
    pwsh -NoProfile -File (Join-Path $PSScriptRoot 'advance_v100_tag.ps1') -Execute -SyncGitee 2>&1 | ForEach-Object { Log "  $_" }
} else {
    pwsh -NoProfile -File (Join-Path $PSScriptRoot 'advance_v100_tag.ps1') -Execute 2>&1 | ForEach-Object { Log "  $_" }
}
Log "advance rc=$LASTEXITCODE"
Log "=== DONE (PR #$PrNumber merged, tag advanced) ==="
Get-Content $log | Out-String
exit 0
