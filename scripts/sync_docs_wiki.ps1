<#
# 同步 docs/wiki Markdown 到 GitHub Wiki
# ------------------------------------------------------
# 前置：远程仓库需已创建 Wiki（GitHub 上启用 Wiki 后，仓库 <owner>/<repo> 的
#       Wiki 即为独立 git 仓库 <owner>/<repo>.wiki，默认分支 master）。
#
# 用法（Windows PowerShell）：
#   ./scripts/sync_docs_wiki.ps1                          # 默认参数（clone 到 .wiki 并推送）
#   ./scripts/sync_docs_wiki.ps1 -NoPush                  # 只提交不推送（先检查差异）
#   ./scripts/sync_docs_wiki.ps1 -RepoUrl "git@github.com:nzt47/security-tools.wiki.git"
#
# 说明：
#   - GitHub Wiki 页面名 = 文件相对路径（去 .md 扩展名），如 system-log.openapi.md → 页面 "system-log.openapi"
#   - 重复执行幂等：覆盖同名页面，新增/删除同步（删除需手动 git rm，见下方注释）
#   - 推送到 master 分支（GitHub Wiki 固定分支名）
#>
param(
    [string]$SourceDir = "$PSScriptRoot\..\docs\wiki",
    [string]$WikiDir   = "$PSScriptRoot\..\.wiki",
    [string]$RepoUrl   = "https://github.com/nzt47/security-tools.wiki.git",
    [string]$Branch    = "master",
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$SourceDir = (Resolve-Path $SourceDir).Path

# 1) 确保 Wiki 仓库已克隆
if (-not (Test-Path "$WikiDir\.git")) {
    Write-Host "[wiki] 克隆 Wiki 仓库: $RepoUrl"
    git clone $RepoUrl $WikiDir
    if ($LASTEXITCODE -ne 0) { throw "Wiki 仓库克隆失败（确认远程已启用 Wiki 且权限正确）" }
}

# 2) 复制 docs/wiki 下所有 .md（保持相对路径 → Wiki 页面结构）
$copied = 0
Get-ChildItem $SourceDir -Filter "*.md" -Recurse | ForEach-Object {
    $rel = $_.FullName.Substring($SourceDir.Length).TrimStart('\', '/')
    $dest = Join-Path $WikiDir $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item $_.FullName $dest -Force
    Write-Host "[wiki] 同步: $rel"
    $copied++
}
Write-Host "[wiki] 共复制 $copied 个 Markdown 文件"

# 3) 提交并推送（GitHub Wiki 默认分支 master）
git -C $WikiDir add -A
if ($LASTEXITCODE -ne 0) { throw "git add 失败" }
git -C $WikiDir commit -m "docs(wiki): 同步 docs/wiki 更新 $(Get-Date -Format 'yyyy-MM-dd HH:mm')" | Out-Null

if ($NoPush) {
    Write-Host "[wiki] （-NoPush）已提交未推送，检查后执行: git -C $WikiDir push origin $Branch"
} else {
    git -C $WikiDir push origin $Branch
    if ($LASTEXITCODE -ne 0) { throw "push 失败" }
    Write-Host "[wiki] 已推送到 GitHub Wiki (master)"
}

# 注：删除已不再存在的页面，需手动执行（脚本不做破坏性删除）：
#   git -C $WikiDir rm <页面文件名>.md && git -C $WikiDir commit -m "..." && git -C $WikiDir push origin master
