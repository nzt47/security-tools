<#
.SYNOPSIS
    工具审批的人工通道（CLI）：列出 / 批准 / 驳回待审的工具调用。

.DESCRIPTION
    【为什么需要这个脚本】
      `agent/tool_gate.py` 在拦截执行类工具时，回执写的是"请人工在
      「治理 → 审批收件箱」确认"。那句话本身没错 —— React 工作台里确实有这一项
      （`yunshu-ui/src/workbench/hubNav.tsx`：治理面板 → 审批收件箱），但它**默认
      要浏览器里已经存好 API 令牌**；而"独立审批控制台"那条路实测（2026-09-22）
      在浏览器里是断的：
        ① `GET /api/approval/console` 挂着 `@require_token`，浏览器地址栏导航
           **无法携带** `Authorization` 头 ⇒ 打开即 401；
        ② `static/js/approval_console.js` 当时也从不注入令牌 ⇒ 即便加载了，它的
           `/api/approval/*` 调用照样 401。
      结果：**闸门能挂单，人却可能一条路都走不通**（待办台账 #1 的体感来源）。
      而代码里另一处承诺的出路"改由人工身份（**CLI 交互** / 审批收件箱）执行一次"
      （`agent/capregistry/invoke.py`、`agent/tool_gate.py`）——CLI 通路**从来不存在**。
      本脚本补的就是这一条：不依赖浏览器、不依赖令牌是否已存进 localStorage。

    【它不做权限判定】审批权仍由后端单表判定（`agent/security/actor_matrix.py`）。
    本脚本只是**把同一个安全链按顺序走一遍**：会话 → CSRF 双重提交 →
    一次性链接（会话绑定 + record 绑定 + ≤900s）→ destructive 二次认证 → 矩阵。
    绕过其中任何一步都会被后端拒绝，这正是我们要的。

    【令牌从哪来】优先 `-Token`，其次环境变量 `FLASK_API_TOKEN`，最后读仓库根的
    `.env`。令牌**永不打印**（只打印来源与指纹级别的信息）。

.EXAMPLE
    # 看收件箱（默认动作）
    pwsh -File scripts/approve_tool_call.ps1

.EXAMPLE
    # 批准某张单（批准前会先回显这张单的内容，避免批错）
    pwsh -File scripts/approve_tool_call.ps1 -Approve appr-20260922075152968796-00a4a1be

.EXAMPLE
    # 驳回某张单（必须给理由，后端强制）
    pwsh -File scripts/approve_tool_call.ps1 -Reject appr-xxx -Reason "该命令会删库，禁止"
#>
#Requires -Version 5.1
[CmdletBinding(DefaultParameterSetName = 'List')]
param(
    [Parameter(ParameterSetName = 'List')]
    [switch]$List,

    [Parameter(ParameterSetName = 'Approve', Mandatory = $true)]
    [string]$Approve,

    [Parameter(ParameterSetName = 'Approve')]
    [string]$Note = '',

    [Parameter(ParameterSetName = 'Reject', Mandatory = $true)]
    [string]$Reject,

    [Parameter(ParameterSetName = 'Reject', Mandatory = $true)]
    [string]$Reason,

    [string]$BaseUrl = 'http://127.0.0.1:5678',

    [string]$Token = '',

    # 只走链路、不提交裁决：用于"先确认这张单批得动"与排障（不改任何状态）
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:Base = $BaseUrl.TrimEnd('/')
$script:Tok = ''

function Resolve-ApiToken {
    <#
      【顺序即优先级】显式参数 > 环境变量 > .env。
      读不到就返回空串：后端在"完全没配令牌"时会放行（`server_auth.authorize_token`
      第 3 条），所以空令牌不等于必然失败——由后端说了算，本脚本不替它下结论。
    #>
    param([string]$Explicit)
    if ($Explicit) { return $Explicit.Trim() }
    if ($env:FLASK_API_TOKEN) { return ([string]$env:FLASK_API_TOKEN).Trim() }
    $envPath = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
    if (Test-Path $envPath) {
        $hit = Select-String -Path $envPath -Pattern '^\s*FLASK_API_TOKEN\s*=' |
            Select-Object -First 1
        if ($hit) {
            $value = $hit.Line.Split('=', 2)[1].Trim()
            return $value.Trim('"').Trim("'")
        }
    }
    return ''
}

function Invoke-Api {
    <#
      【为什么自己包一层】后端所有失败都是**结构化 JSON**（code / message /
      requires_second_factor）。Invoke-RestMethod 抛异常时响应体在 ErrorDetails
      里，裸用会把"为什么失败"丢掉——那正是本仓库反复强调的"不要静默失败"。
    #>
    param(
        [string]$Method,
        [string]$Path,
        $Body = $null,
        [hashtable]$ExtraHeaders = $null,
        $WebSession = $null
    )
    $uri = $script:Base + $Path
    $headers = @{}
    if ($script:Tok) { $headers['Authorization'] = 'Bearer ' + $script:Tok }
    if ($ExtraHeaders) { foreach ($k in $ExtraHeaders.Keys) { $headers[$k] = $ExtraHeaders[$k] } }

    $call = @{ Method = $Method; Uri = $uri; Headers = $headers; TimeoutSec = 20 }
    if ($WebSession) { $call['WebSession'] = $WebSession }
    if ($null -ne $Body) {
        $call['Body'] = ($Body | ConvertTo-Json -Depth 6)
        $call['ContentType'] = 'application/json'
    }
    try {
        return @{ ok = $true; data = (Invoke-RestMethod @call) }
    } catch {
        $status = $null
        $raw = $null
        try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = $null }
        try { $raw = $_.ErrorDetails.Message } catch { $raw = $null }
        return @{ ok = $false; status = $status; raw = $raw; message = $_.Exception.Message }
    }
}

function Fail {
    param([string]$Text, $Result = $null)
    Write-Host ('[失败] ' + $Text) -ForegroundColor Red
    if ($Result -and $Result.raw) {
        Write-Host ('        后端回执: ' + $Result.raw) -ForegroundColor DarkYellow
    }
    if ($Result -and $Result.status) {
        Write-Host ('        HTTP ' + $Result.status) -ForegroundColor DarkYellow
    }
    exit 1
}

function Get-Pending {
    $r = Invoke-Api -Method 'GET' -Path '/api/approval/pending'
    if (-not $r.ok) {
        Fail '无法读取待审清单（接口不可达或鉴权失败）' $r
    }
    return $r.data
}

function Show-Pending {
    param($Data)
    $items = @($Data.items)
    Write-Host ''
    Write-Host ('待审 ' + $items.Count + ' 条（' + $script:Base + '）') -ForegroundColor Cyan
    if ($items.Count -eq 0) {
        Write-Host '  （收件箱为空）' -ForegroundColor DarkGray
        return
    }
    foreach ($it in $items) {
        $desc = [string]$it.description
        if ($desc.Length -gt 90) { $desc = $desc.Substring(0, 90) + '…' }
        Write-Host ('  ' + $it.record_id) -ForegroundColor White
        Write-Host ('      对象=' + $it.object_id + '  风险=' + $it.risk + '  级别=' + $it.level + '  创建=' + $it.created_at) -ForegroundColor DarkGray
        Write-Host ('      内容=' + ($desc -replace "\r?\n", ' | ')) -ForegroundColor DarkGray
        if ($it.undo_hint_status -and $it.undo_hint_status -ne 'resolved') {
            Write-Host ('      ⚠ undo_hint 状态=' + $it.undo_hint_status + '（前端可能不显示审批气泡）') -ForegroundColor Yellow
        }
    }
    Write-Host ''
}

# ── 令牌 ────────────────────────────────────────────────────
$script:Tok = Resolve-ApiToken -Explicit $Token
if ($script:Tok) {
    Write-Host '令牌来源: 已获取（不打印原文）' -ForegroundColor DarkGray
} else {
    Write-Host '令牌来源: 未配置（若后端已启用 FLASK_API_TOKEN，本次会 401）' -ForegroundColor Yellow
}

if ($PSCmdlet.ParameterSetName -eq 'List') {
    Show-Pending (Get-Pending)
    Write-Host '批准: -Approve <record_id>      驳回: -Reject <record_id> -Reason "..."' -ForegroundColor Cyan
    exit 0
}

$recordId = if ($PSCmdlet.ParameterSetName -eq 'Approve') { $Approve } else { $Reject }
$approving = ($PSCmdlet.ParameterSetName -eq 'Approve')

# ── 0. 先回显"到底在批什么"（批错单是治理面最贵的错误） ──
$data = Get-Pending
$target = @($data.items) | Where-Object { $_.record_id -eq $recordId } | Select-Object -First 1
if (-not $target) {
    Fail ('待审清单里没有这张单：' + $recordId + '（可能已被裁决 / 已过 TTL 被系统超时清理 / 是另一条流水线的单）')
}
Write-Host ''
Write-Host ('即将裁决: ' + $target.record_id) -ForegroundColor Cyan
Write-Host ('  对象=' + $target.object_id + '  风险=' + $target.risk + '  级别=' + $target.level) -ForegroundColor DarkGray
Write-Host ('  内容=' + ([string]$target.description)) -ForegroundColor DarkGray
if ($approving) {
    Write-Host '  动作=批准（批准后模型需**原样重试**同一次调用；单次有效，L1 可复用）' -ForegroundColor Yellow
} else {
    Write-Host ('  动作=驳回  理由=' + $Reason) -ForegroundColor Yellow
}

# ── 1. 开会话（会话与链接只在服务端进程内，重启即失效） ──
$sess = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$r = Invoke-Api -Method 'POST' -Path '/api/approval/session' -Body @{} -WebSession $sess
if (-not $r.ok) { Fail '开启审批会话失败' $r }
$csrfName = 'X-CSRF-Token'
if ($r.data.csrf_header) { $csrfName = [string]$r.data.csrf_header }

# ── 2. CSRF 双重提交：Cookie 里的令牌必须回到请求头 ──
$csrfValue = ''
try {
    $jar = $sess.Cookies.GetCookies([Uri]$script:Base)
    if ($jar['cp_approval_csrf']) { $csrfValue = [string]$jar['cp_approval_csrf'].Value }
} catch { $csrfValue = '' }
$csrfHeaders = @{}
if ($csrfValue) { $csrfHeaders[$csrfName] = $csrfValue }

# ── 3. 签发一次性链接（校验 CSRF 与 record 存在性） ──
$r = Invoke-Api -Method 'POST' -Path '/api/approval/link' -Body @{ record_id = $recordId } -ExtraHeaders $csrfHeaders -WebSession $sess
if (-not $r.ok) { Fail '签发一次性审批链接失败（CSRF / 会话 / 记录）' $r }
$linkToken = [string]$r.data.link.token
if (-not $linkToken) { Fail '后端未返回链接 token' $r }

if ($DryRun) {
    Write-Host ''
    Write-Host '✓ [DryRun] 会话 / CSRF / 一次性链接三步全部通过；已停在"提交裁决"之前，未改变任何状态。' -ForegroundColor Green
    Write-Host '  去掉 -DryRun 即真的提交这次裁决。' -ForegroundColor DarkGray
    exit 0
}

# ── 4. 裁决（destructive 需要二次认证：先取一次性确认码再重试） ──
function Invoke-Decision {
    param([string]$SecondFactor = '')
    $body = @{ link_token = $linkToken }
    if ($approving) {
        $body['note'] = $Note
    } else {
        $body['reason'] = $Reason
        $body['note'] = $Reason
    }
    if ($SecondFactor) { $body['second_factor'] = $SecondFactor }
    $path = '/api/approval/' + $recordId + ($(if ($approving) { '/approve' } else { '/reject' }))
    return Invoke-Api -Method 'POST' -Path $path -Body $body -ExtraHeaders $csrfHeaders -WebSession $sess
}

$r = Invoke-Decision
if ((-not $r.ok) -and $r.raw -and ($r.raw -match 'requires_second_factor')) {
    Write-Host '该单为 destructive，需要二次认证：正在签发一次性确认码…' -ForegroundColor Yellow
    $sf = Invoke-Api -Method 'POST' -Path '/api/approval/second-factor' -Body @{ record_id = $recordId } -ExtraHeaders $csrfHeaders -WebSession $sess
    if (-not $sf.ok) { Fail '签发二次认证确认码失败' $sf }
    $r = Invoke-Decision -SecondFactor ([string]$sf.data.code)
}
if (-not $r.ok) { Fail '裁决未通过' $r }

Write-Host ''
Write-Host ('✓ 已提交：' + $recordId + '  →  state=' + $r.data.record.state + '  actor=' + $r.data.record.actor) -ForegroundColor Green
if ($approving) {
    Write-Host '下一步：让模型**原样重试**同一次调用（同一工具 + 逐字相同的参数）。' -ForegroundColor Cyan
    Write-Host '参数差一个字（含空格/数字/数组顺序）都会算成另一次调用，需要另挂一张单。' -ForegroundColor DarkGray
} else {
    Write-Host '该次调用已被否决，模型侧会收到 APPROVAL_REJECTED 与驳回理由。' -ForegroundColor Cyan
}
