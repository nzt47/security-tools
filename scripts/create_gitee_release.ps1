<#
.SYNOPSIS
  在 Gitee 上基于已推送 tag 创建 Release（Gitee API v5）

.DESCRIPTION
  可复用脚本：参数化 tag/标题/正文，内置 -Diagnose 诊断模式与错误分类提示。

  令牌获取步骤（一次性，约 2 分钟）:
    1. 登录 https://gitee.com → 右上角头像 → 设置
    2. 左侧「安全设置」→「私人令牌」
    3. 点击「生成新令牌」→ 输入描述 → 勾选 projects（仓库/Release 读写必需）
    4. 复制令牌（40 位十六进制），设置环境变量:
       $env:GITEE_TOKEN = "<令牌>"
    5. 令牌只显示一次，丢失需重新生成；按最小权限勾选

  Gitee API v5 必填参数（易踩坑）:
    - target_commitish: 创建 Release 必填（分支名或 commit SHA），漏填报
      400 "target_commitish is missing"（本脚本已内置 master 兜底）
    - tag_name: 必须已推送到 Gitee 远程，否则报 404 "Not Found Project"
    - 无效 token 会报 401 "Access token does not exist"；Gitee 对无权限仓库
      统一返回 404（不暴露资源存在性），故 404 需同时排查 token 与路径

.PARAMETER TagName      版本标签（须已推送 gitee）
.PARAMETER Title        Release 标题
.PARAMETER BodyFile     发布说明 Markdown 文件路径（可空）
.PARAMETER Owner        仓库归属，默认 nzt47
.PARAMETER Repo         仓库名，默认 security-tools
.PARAMETER Prerelease   是否预发布，默认 $false
.PARAMETER Diagnose     诊断模式：校验 token/仓库/已有 releases，不创建

.EXAMPLE
  $env:GITEE_TOKEN = "<令牌>"
  .\create_gitee_release.ps1 -TagName v1.0.0 -Title "v1.0.0 发布说明" -BodyFile .\notes.md

.EXAMPLE
  .\create_gitee_release.ps1 -Diagnose   # 先诊断环境再创建
#>
param(
    [string]$TagName,
    [string]$Title,
    [string]$BodyFile = "",
    [string]$Owner = "nzt47",
    [string]$Repo = "security-tools",
    [switch]$Prerelease,
    [switch]$Diagnose
)
$ErrorActionPreference = "Stop"
$Base = "https://gitee.com/api/v5"

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Invoke-Gitee($Method, $Path, $Body = $null) {
    $params = @{ Method = $Method; Uri = "$Base$Path" }
    if ($Body) {
        $params.ContentType = "application/json;charset=UTF-8"
        $params.Body = ($Body | ConvertTo-Json -Depth 3)
    }
    Invoke-RestMethod @params
}

$token = $env:GITEE_TOKEN
if (-not $token) {
    Write-Host "[BLOCK] GITEE_TOKEN 未设置。获取步骤见本脚本头部注释。" -ForegroundColor Red
    exit 1
}

if ($Diagnose) {
    Write-Step "Token 有效性 (GET /user)"
    try { $u = Invoke-Gitee GET "/user?access_token=$token"; Write-Host "OK: $($u.login) / $($u.name)" -ForegroundColor Green }
    catch { Write-Host "FAIL: $($_.ErrorDetails.Message)" -ForegroundColor Red; exit 1 }

    Write-Step "仓库可访问性 (GET /repos/$Owner/$Repo)"
    try { $r = Invoke-Gitee GET "/repos/$Owner/$($Repo)?access_token=$($token)"; Write-Host "OK: $($r.full_name)" -ForegroundColor Green }
    catch { Write-Host "FAIL: $($_.ErrorDetails.Message)" -ForegroundColor Red; exit 1 }

    Write-Step "已有 releases (GET /repos/$Owner/$Repo/releases)"
    try {
        $ls = Invoke-Gitee GET "/repos/$Owner/$($Repo)/releases?access_token=$($token)"
        Write-Host "共 $($ls.Count) 个:"
        $ls | ForEach-Object { Write-Host "  - $($_.tag_name): $($_.name)" }
    }
    catch { Write-Host "FAIL: $($_.ErrorDetails.Message)" -ForegroundColor Red; exit 1 }

    Write-Host "`n诊断完成，环境正常。" -ForegroundColor Green
    exit 0
}

if (-not $TagName -or -not $Title) {
    Write-Host "[BLOCK] 缺少参数：-TagName 与 -Title 必填（-Diagnose 除外）" -ForegroundColor Red
    exit 1
}
$body = ""
if ($BodyFile) {
    if (-not (Test-Path $BodyFile)) { Write-Host "[BLOCK] 正文文件不存在: $BodyFile" -ForegroundColor Red; exit 1 }
    $body = Get-Content $BodyFile -Raw -Encoding UTF8
}

Write-Step "创建 Release (POST /repos/$Owner/$Repo/releases)"
$payload = @{
    access_token     = $token
    tag_name         = $TagName
    name             = $Title
    body             = $body
    prerelease       = [bool]$Prerelease
    target_commitish = "master"   # Gitee API 必填（分支名或 commit SHA）
}
try {
    $resp = Invoke-Gitee POST "/repos/$Owner/$Repo/releases" $payload
    Write-Host "成功: $($resp.html_url) (id=$($resp.id))" -ForegroundColor Green
} catch {
    $msg = $_.ErrorDetails.Message
    Write-Host "失败: $msg" -ForegroundColor Red
    if ($msg -match "401") { Write-Host "排查: token 无效/已过期，重新生成（见头部注释）" }
    elseif ($msg -match "404") { Write-Host "排查: 仓库路径或 tag 不存在；或 token 无该仓库权限（Gitee 对无权限统一返回 404）" }
    elseif ($msg -match "400.*target_commitish") { Write-Host "排查: target_commitish 缺失（本脚本已内置 master 兜底）" }
    elseif ($msg -match "403") { Write-Host "排查: 权限不足或触发限流" }
    elseif ($msg -match "409|422") { Write-Host "排查: 该 tag 已存在 Release，先 GET /releases 确认" }
    exit 1
}
