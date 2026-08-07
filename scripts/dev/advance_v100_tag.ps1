<#
.SYNOPSIS
v1.0.0 标签前移脚本（第 8 次及以后前移使用，2026-08-06 建档）

.DESCRIPTION
检测 v1.0.0 标签是否落后 origin/master；落后时执行强制前移并推送。
默认 dry-run 仅输出检查结果（即自动化检查清单）；加 -Execute 执行前移。

检查清单（脚本已自动完成）：
  1. fetch origin/master，比对 v1.0.0 与 origin/master
  2. 相等 → 无需前移，退出
  3. 不等 → 列出落后提交明细，dry-run 时预览命令
  4. -Execute：git tag -f + push --force（origin），可选 -SyncGitee 同步 gitee
  5. ls-remote 验证远程标签 = master

.NOTES
- 本仓库 pre-push hook 需 TLM_HOOK_SOURCE_REPO 环境变量，未配置时 push 必须 --no-verify（脚本已内置）
- 前移为不可逆操作（force push 覆盖远程标签），执行前务必审阅落后提交明细
- 触发判据：git ls-remote origin refs/heads/master refs/tags/v1.0.0 两值不一致

.EXAMPLE
powershell -File scripts/dev/advance_v100_tag.ps1               # dry-run 检查
powershell -File scripts/dev/advance_v100_tag.ps1 -Execute      # 前移 origin
powershell -File scripts/dev/advance_v100_tag.ps1 -Execute -SyncGitee  # 前移 origin + gitee
#>
param(
    [switch]$Execute,   # 执行前移（默认仅 dry-run 报告）
    [switch]$SyncGitee  # 同步 gitee 镜像标签（默认仅 origin）
)

$ErrorActionPreference = 'Stop'

# 1. 更新远程引用
git fetch origin master | Out-Null
if ($LASTEXITCODE -ne 0) { throw "fetch origin/master 失败" }

$tag = (git rev-parse v1.0.0).Trim()
$master = (git rev-parse origin/master).Trim()

Write-Host "v1.0.0       = $tag"
Write-Host "origin/master = $master"

if ($tag -eq $master) {
    Write-Host "OK: v1.0.0 已指向 origin/master 最新，无需前移"
    exit 0
}

# 2. 落后提交明细
Write-Host ""
Write-Host "v1.0.0 落后 origin/master 的提交："
$lag = git log --oneline "v1.0.0..origin/master"
if ($lag) {
    $lag | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host "  (无直接祖先关系，强制前移将覆盖标签指向)"
}

if (-not $Execute) {
    Write-Host ""
    Write-Host "dry-run：未执行前移。确认落后提交无误后，加 -Execute 执行："
    Write-Host "  git tag -f v1.0.0 origin/master"
    Write-Host "  git push --no-verify origin v1.0.0 --force"
    exit 0
}

# 3. 执行前移（origin）
git tag -f v1.0.0 origin/master
if ($LASTEXITCODE -ne 0) { throw "git tag -f v1.0.0 失败" }
git push --no-verify origin v1.0.0 --force
if ($LASTEXITCODE -ne 0) { throw "git push origin v1.0.0 --force 失败" }
Write-Host "OK: v1.0.0 已前移并推送 origin：$tag -> $master"

# 4. 可选同步 gitee 镜像标签
if ($SyncGitee) {
    git push --no-verify gitee v1.0.0 --force
    if ($LASTEXITCODE -ne 0) { throw "git push gitee v1.0.0 --force 失败" }
    Write-Host "OK: gitee 镜像标签已同步：$master"
}

# 5. 远程验证
$remote = ((git ls-remote origin refs/tags/v1.0.0) -split "\s+")[0]
if ($remote -eq $master) {
    Write-Host "OK: 验证通过，远程 v1.0.0 = $remote = master"
} else {
    throw "验证失败：远程 v1.0.0 = $remote，期望 $master"
}
