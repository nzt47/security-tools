<#
.SYNOPSIS
    Release 首次发布引导（WinForms 可视化版）—— 把 release_first_release.ps1 的
    5 步命令行交互替换为 Windows Forms 图形界面。

.DESCRIPTION
    界面元素：
      - 版本号输入框 + 「开始发布流程」按钮
      - 5 步进度标签（待执行/OK/FAIL，FAIL 红字）
      - 日志输出区（RichTextBox，实时滚动）

    与命令行版完全一致的业务逻辑（每步自动校验）：
      Step 1 版本号格式（vX.Y.Z）
      Step 2 工作区与远端同步检查（未提交改动警告 / 落后于远端阻止）
      Step 3 tag 唯一性检查（远端已存在则阻止）+ 打 annotated tag
      Step 4 push tag + 远端确认
      Step 5 引导监控 Actions 与验证 Release

    安全设计：写操作（pull/tag/push）弹出 YesNo 确认框；仅检查项自动执行。

.EXAMPLE
    pwsh -File scripts/dev/release_first_release_gui.ps1

.NOTES
    运行要求：Windows PowerShell 5.1+ 或 pwsh 7+（WinForms 需 STA，pwsh 默认 STA）。
    配套文档: docs/release_quickstart.md / docs/release_checklist.md
    来源: scripts/dev/release_first_release.ps1（命令行版，逻辑等价）
#>

$ErrorActionPreference = 'Stop'

# WinForms 必须运行在 STA 线程（pwsh 默认 STA；若显式 -MTA 启动则拒绝）
if ([System.Threading.Thread]::CurrentThread.ApartmentState -ne 'STA') {
    Write-Host "[FAIL] 当前线程非 STA，WinForms 无法运行。请用 pwsh -Sta 或 powershell.exe 启动。" -ForegroundColor Red
    exit 1
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# ============================================================================
# 全局状态
# ============================================================================
$script:Version = ""
$script:stepLabels = @()   # 5 个步骤的 Label 控件
$script:stepNames = @(
    "Step 1/5 版本号确认",
    "Step 2/5 工作区与远端同步",
    "Step 3/5 创建 annotated tag",
    "Step 4/5 推送 tag 触发发布",
    "Step 5/5 发布后验证引导"
)

# ============================================================================
# UI 辅助函数
# ============================================================================
function Add-Log([string]$msg, [string]$color = "Black") {
    $script:logBox.SelectionStart = $script:logBox.TextLength
    $script:logBox.SelectionLength = 0
    $script:logBox.SelectionColor = [System.Drawing.Color]::FromName($color)
    $script:logBox.AppendText("$msg`r`n")
    $script:logBox.SelectionColor = [System.Drawing.Color]::Black
    $script:logBox.SelectionStart = $script:logBox.TextLength
    $script:logBox.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Set-Step([int]$idx, [string]$status) {
    # status: "待执行" / "OK" / "FAIL"
    $lbl = $script:stepLabels[$idx]
    $lbl.Text = "  $($script:stepNames[$idx])"
    switch ($status) {
        "OK"   { $lbl.Text += "  [OK]";   $lbl.ForeColor = [System.Drawing.Color]::Green }
        "FAIL" { $lbl.Text += "  [FAIL]"; $lbl.ForeColor = [System.Drawing.Color]::Red }
        default { $lbl.Text += "  [待执行]"; $lbl.ForeColor = [System.Drawing.Color]::Gray }
    }
}

function Confirm-Step([string]$desc) {
    $r = [System.Windows.Forms.MessageBox]::Show(
        "将执行:`n  $desc`n`n是否继续？",
        "发布操作确认",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question)
    return ($r -eq 'Yes')
}

function Invoke-Git([string]$argsLine) {
    # 运行 git 命令并返回 stdout（合并 stderr，避免 PS 把 stderr 当异常）
    $out = & git $argsLine.Split(' ') 2>&1
    return ($out -join "`n")
}

# ============================================================================
# 5 步业务逻辑（对齐 release_first_release.ps1）
# ============================================================================
function Step1-Version {
    $script:Version = $script:txtVersion.Text.Trim()
    if ($script:Version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+$') {
        Add-Log "[FAIL] 版本号格式错误: $($script:Version)（必须是 vX.Y.Z 语义化版本）" "Red"
        Set-Step 0 "FAIL"
        return $false
    }
    Add-Log "[OK] 版本号 $($script:Version) 格式正确" "Green"
    Add-Log "提示: tag commit message 不要以 release(pypi) 开头（否则 guard 拦截跳过发布）" "Gray"
    Set-Step 0 "OK"
    return $true
}

function Step2-Sync {
    Add-Log "--- Step 2: 工作区与远端同步检查 ---"
    $dirty = (& git status --porcelain)
    if ($dirty) {
        Add-Log "[WARN] 工作区存在未提交改动（$((@($dirty)).Count) 条），tag 基于当前 HEAD；如需包含改动请先提交" "Orange"
    } else {
        Add-Log "[OK] 工作区干净" "Green"
    }
    & git fetch origin master 2>$null | Out-Null
    $head = (& git rev-parse HEAD).Trim()
    $origin = (& git rev-parse origin/master 2>$null).Trim()
    if ($head -ne $origin) {
        Add-Log "本地 HEAD($head) 与 origin/master($origin) 不一致" "Orange"
        if (Confirm-Step "git pull --rebase origin master") {
            & git pull --rebase origin master 2>&1 | ForEach-Object { Add-Log $_ }
            $head = (& git rev-parse HEAD).Trim()
            $origin = (& git rev-parse origin/master 2>$null).Trim()
        }
        if ($head -ne $origin) {
            Add-Log "[FAIL] 本地 HEAD 与 origin/master 仍不一致，请先 git pull --rebase 后再发布" "Red"
            Set-Step 1 "FAIL"
            return $false
        }
    }
    Add-Log "[OK] 本地与 origin/master 同步 ($head)" "Green"
    Set-Step 1 "OK"
    return $true
}

function Step3-Tag {
    Add-Log "--- Step 3: 创建 annotated tag ---"
    if (& git ls-remote --exit-code origin "refs/tags/$($script:Version)" 2>$null) {
        Add-Log "[FAIL] 远端已存在 tag $($script:Version) —— 重复发布会触发 409/422 幂等失败，禁止再次发布" "Red"
        Set-Step 2 "FAIL"
        return $false
    }
    Add-Log "[OK] 远端无 $($script:Version) tag（唯一性通过）" "Green"
    if (Confirm-Step "git tag -a $($script:Version) -m `"ci(release): 发布 $($script:Version)`"") {
        & git tag -a $($script:Version) -m "ci(release): 发布 $($script:Version)"
        $localTag = & git tag -l $($script:Version)
        if (-not $localTag) {
            Add-Log "[FAIL] tag 创建失败（git tag -l 未找到 $($script:Version)）" "Red"
            Set-Step 2 "FAIL"
            return $false
        }
        Add-Log "[OK] 本地 tag $($script:Version) 已创建: $(git log -1 --format=%s $script:Version)" "Green"
        Set-Step 2 "OK"
        return $true
    } else {
        Add-Log "[FAIL] 未创建 tag，发布中止" "Red"
        Set-Step 2 "FAIL"
        return $false
    }
}

function Step4-Push {
    Add-Log "--- Step 4: 推送 tag 触发发布 ---"
    if (Confirm-Step "git push origin $($script:Version)（触发自动发布工作流）") {
        & git push origin $($script:Version) 2>&1 | ForEach-Object { Add-Log $_ }
        if (& git ls-remote --exit-code origin "refs/tags/$($script:Version)" 2>$null) {
            Add-Log "[OK] 远端已确认存在 tag $($script:Version)，发布已触发" "Green"
            Set-Step 3 "OK"
            return $true
        } else {
            Add-Log "[FAIL] 推送后远端未找到 tag $($script:Version)，请检查网络/权限" "Red"
            Set-Step 3 "FAIL"
            return $false
        }
    } else {
        Add-Log "[FAIL] 未推送 tag，发布中止（本地 tag $($script:Version) 已创建，可用 git tag -d 删除）" "Red"
        Set-Step 3 "FAIL"
        return $false
    }
}

function Step5-Guide {
    Add-Log "--- Step 5: 发布后验证引导 ---"
    $repoPath = (& git config --get remote.origin.url).Trim()
    if ($repoPath) {
        $repoPath = $repoPath -replace '.*[(:/]([^:/]+/[^/.]+)(\.git)?$', '$1'
    } else {
        $repoPath = '<your-repo>'
    }
    $msg = @(
        "1. 打开 Actions → 自动发布 → 本次运行，确认:",
        "   - guard job: skip=false（放行）",
        "   - auto-release: GitHub Release HTTP 201 + Gitee 同步成功",
        "   - 无 alert-on-failure 告警 Issue 触发",
        "2. 验证 Release 页面:",
        "   GitHub: https://github.com/$repoPath/releases/tag/$($script:Version)",
        "   Gitee:  https://gitee.com/$repoPath/releases/$($script:Version)",
        "3. 完整清单核对: docs/release_checklist.md（D/E 段人工确认）"
    )
    foreach ($line in $msg) { Add-Log $line }
    Set-Step 4 "OK"
    [System.Windows.Forms.MessageBox]::Show(
        ($msg -join "`n"),
        "发布后验证引导",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information)
    Add-Log "=== 发布流程引导结束。如任一步失败，对照 docs/release_workflow_manual.md 排查。 ===" "Green"
}

function Start-Release {
    $script:btnStart.Enabled = $false
    try {
        if (-not (Step1-Version)) { return }
        if (-not (Step2-Sync))    { return }
        if (-not (Step3-Tag))     { return }
        if (-not (Step4-Push))    { return }
        Step5-Guide
    } catch {
        Add-Log "[FAIL] 脚本异常: $_" "Red"
        Set-Step 4 "FAIL"
    } finally {
        $script:btnStart.Enabled = $true
    }
}

# ============================================================================
# 构建界面
# ============================================================================
$form = New-Object System.Windows.Forms.Form
$form.Text = "Release 首次发布引导 (WinForms)"
$form.Size = New-Object System.Drawing.Size(680, 560)
$form.StartPosition = 'CenterScreen'
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei", 9)

# --- 版本号输入区 ---
$lblVersion = New-Object System.Windows.Forms.Label
$lblVersion.Text = "版本号 (vX.Y.Z):"
$lblVersion.Location = New-Object System.Drawing.Point(20, 22)
$lblVersion.AutoSize = $true
$form.Controls.Add($lblVersion)

$txtVersion = New-Object System.Windows.Forms.TextBox
$txtVersion.Location = New-Object System.Drawing.Point(130, 18)
$txtVersion.Size = New-Object System.Drawing.Size(180, 23)
$form.Controls.Add($txtVersion)
$script:txtVersion = $txtVersion

$btnStart = New-Object System.Windows.Forms.Button
$btnStart.Text = "开始发布流程"
$btnStart.Location = New-Object System.Drawing.Point(330, 16)
$btnStart.Size = New-Object System.Drawing.Size(140, 28)
$form.Controls.Add($btnStart)
$script:btnStart = $btnStart

$lblHint = New-Object System.Windows.Forms.Label
$lblHint.Text = "写操作（pull/tag/push）会弹出确认框；仅检查项自动执行。"
$lblHint.Location = New-Object System.Drawing.Point(480, 22)
$lblHint.AutoSize = $true
$lblHint.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($lblHint)

# --- 步骤进度区 ---
$grpSteps = New-Object System.Windows.Forms.GroupBox
$grpSteps.Text = "步骤进度"
$grpSteps.Location = New-Object System.Drawing.Point(20, 60)
$grpSteps.Size = New-Object System.Drawing.Size(630, 170)
$form.Controls.Add($grpSteps)

for ($i = 0; $i -lt 5; $i++) {
    $lbl = New-Object System.Windows.Forms.Label
    $lbl.Location = New-Object System.Drawing.Point(15, 20 + $i * 28)
    $lbl.AutoSize = $true
    $lbl.ForeColor = [System.Drawing.Color]::Gray
    $grpSteps.Controls.Add($lbl)
    $script:stepLabels += $lbl
    Set-Step $i "待执行"
}

# --- 日志区 ---
$grpLog = New-Object System.Windows.Forms.GroupBox
$grpLog.Text = "日志"
$grpLog.Location = New-Object System.Drawing.Point(20, 245)
$grpLog.Size = New-Object System.Drawing.Size(630, 265)
$form.Controls.Add($grpLog)

$logBox = New-Object System.Windows.Forms.RichTextBox
$logBox.Location = New-Object System.Drawing.Point(10, 22)
$logBox.Size = New-Object System.Drawing.Size(608, 228)
$logBox.ReadOnly = $true
$logBox.BorderStyle = 'FixedSingle'
$logBox.BackColor = [System.Drawing.Color]::White
$grpLog.Controls.Add($logBox)
$script:logBox = $logBox

# --- 事件绑定 ---
$btnStart.Add_Click({ Start-Release })

Add-Log "=== Release 首次发布引导（WinForms 版）===" "Blue"
Add-Log "输入 vX.Y.Z 版本号后点击「开始发布流程」，将按 5 步依次执行并自动校验。"

# 显示窗口（阻塞至关闭）
[System.Windows.Forms.Application]::Run($form)
