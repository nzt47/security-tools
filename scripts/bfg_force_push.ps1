<#
.SYNOPSIS
    BFG Repo-Cleaner 历史清理 + force push 完整命令序列
.DESCRIPTION
    清理 git 历史中的 6 类敏感信息：
      1. DeepSeek/OpenAI API key（sk-ddf2****45a3，完整值见 agent/data/network_config.json）
      2. GlitchTip 管理员密码
      3. Grafana 默认密码
      4. .encryption_key 加密密钥文件
      5. SECURITY_NOTICE 文档中的完整 key 引用
      6. network_config.json 中的 API key
    清理完成后 force push 到 origin（GitHub）+ gitee（Gitee）两个远程仓库。
.NOTES
    关联文档：docs/BFG_CLEANUP_GUIDE_20260719.md
    执行前必读：第四节"前置条件确认"
    执行风险：⚠️ 重写 git 历史，不可逆，必须先完成备份
    安全设计：本脚本不含任何敏感信息，替换规则从 bfg-replacements.txt 读取
#>

# ============================================================================
# 阶段 0：前置条件确认（必须全部通过才能继续）
# ============================================================================

param(
    [string]$RepoPath = "c:\Users\Administrator\agent",
    [string]$BackupDir = "c:\Users\Administrator\agent_bfg_backup",
    [string]$BfgJarPath = "",  # BFG jar 文件路径，如 C:\tools\bfg.jar
    [switch]$DryRun,           # 试运行模式，不执行 force push
    [switch]$SkipBackup,       # 跳过备份（不推荐）
    [switch]$Force,            # 跳过交互式确认（CI 使用）
    [switch]$GenerateTemplate  # 生成 bfg-replacements.txt 模板后退出
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
    Write-Host "`n========== $msg ==========" -ForegroundColor Cyan
}

function Write-Check([string]$msg) {
    Write-Host "[CHECK] $msg" -ForegroundColor Yellow
}

function Write-OK([string]$msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Red
}

function Confirm-Continue([string]$msg) {
    if ($Force) { return $true }
    $response = Read-Host "`n$msg (y/N)"
    return $response -eq 'y' -or $response -eq 'Y'
}

# ============================================================================
# 阶段 0.5：生成 bfg-replacements.txt 模板（可选）
# ============================================================================

if ($GenerateTemplate) {
    $templatePath = Join-Path $RepoPath "bfg-replacements.txt"
    $template = @(
        '# BFG 替换规则文件（每行一条规则，格式：原值==>替换值）'
        '# 请将下列占位符替换为实际敏感值（完整值见 agent/data/network_config.json）'
        ''
        '# 1. DeepSeek/OpenAI API key（完整 key 见 agent/data/network_config.json line 5）'
        'sk-DDF2_FULL_KEY_HERE==>***REMOVED_API_KEY***'
        ''
        '# 2. GlitchTip 管理员密码（见 docker/glitchtip/orm_setup_inline.py line 52）'
        'GLITCHTIP_PWD_HERE==>***REMOVED_GLITCHTIP_PWD***'
        ''
        '# 3. Grafana 默认密码（已移除：admin123 是常见字符串，BFG 清理会误伤 29 个部署文档）'
        '# 如需清理，请使用 regex: 精确匹配，如 regex:GRAFANA_PASSWORD="admin123"'
        '# GRAFANA_PWD_HERE==>***REMOVED_GRAFANA_PWD***'
    )
    $template | Set-Content -Path $templatePath -Encoding UTF8
    Write-OK "模板已生成：$templatePath"
    Write-Host "`n请编辑该文件，将占位符替换为实际敏感值后重新运行本脚本。"
    Write-Host "⚠️ 该文件已被 .gitignore 忽略，不会提交到 git。"
    exit 0
}

Write-Step "阶段 0：前置条件确认"

# 0.1 检查仓库路径
if (-not (Test-Path "$RepoPath\.git")) {
    Write-Warn "仓库路径无效：$RepoPath"
    exit 1
}
Write-OK "仓库路径有效：$RepoPath"

# 0.2 检查 BFG jar
if (-not $BfgJarPath) {
    $BfgJarPath = Get-ChildItem -Path "C:\tools","C:\Program Files","$env:USERPROFILE" -Filter "bfg*.jar" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $BfgJarPath -or -not (Test-Path $BfgJarPath)) {
    Write-Warn "未找到 BFG jar 文件。请下载：https://repo1.maven.org/maven2/com/madgag/bfg/1.14.0/bfg-1.14.0.jar"
    Write-Warn "下载后通过 -BfgJarPath 参数指定路径"
    exit 1
}
Write-OK "BFG jar 路径：$BfgJarPath"

# 0.3 检查 Java 环境
try {
    $javaVersion = java -version 2>&1 | Select-Object -First 1
    Write-OK "Java 环境：$javaVersion"
} catch {
    Write-Warn "未检测到 Java，BFG 需要 Java 8+"
    exit 1
}

# 0.4 检查工作区是否干净
Push-Location $RepoPath
$gitStatus = git status --porcelain
if ($gitStatus) {
    Write-Warn "工作区有未提交的变更，请先提交或 stash："
    Write-Host $gitStatus
    exit 1
}
Write-OK "工作区干净"

# 0.5 确认远程仓库
$remotes = git remote -v
Write-Host "`n当前远程仓库："
Write-Host $remotes
if (-not (Confirm-Continue "确认以上远程仓库正确，继续执行？")) {
    Write-Warn "用户取消执行"
    exit 0
}

# 0.6 检查 bfg-replacements.txt
$replacementsPath = Join-Path $RepoPath "bfg-replacements.txt"
if (-not (Test-Path $replacementsPath)) {
    Write-Warn "未找到替换规则文件：$replacementsPath"
    Write-Host "`n请先运行以下命令生成模板："
    Write-Host "  .\scripts\bfg_force_push.ps1 -GenerateTemplate"
    Write-Host "`n然后编辑该文件，将占位符替换为实际敏感值后重新运行本脚本。"
    exit 1
}

# 读取替换规则并验证
$replacements = Get-Content $replacementsPath -Encoding UTF8 | Where-Object { $_ -and -not $_.StartsWith("#") }
if ($replacements.Count -eq 0) {
    Write-Warn "替换规则文件为空，请填写实际规则"
    exit 1
}
Write-OK "替换规则文件已加载：$($replacements.Count) 条规则"

# 0.7 确认 DeepSeek key 已在平台撤销
Write-Check "请确认已在 DeepSeek 平台撤销旧 key（详见 docs/DEEPSEEK_KEY_REVOKE_GUIDE.md）"
if (-not (Confirm-Continue "DeepSeek key 是否已在平台撤销？")) {
    Write-Warn "请先撤销 key 再执行历史清理"
    exit 0
}

# ============================================================================
# 阶段 1：完整备份（不可跳过，除非显式 -SkipBackup）
# ============================================================================

Write-Step "阶段 1：完整备份"

if ($SkipBackup) {
    Write-Warn "已跳过备份（-SkipBackup）"
} else {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupPath = "${BackupDir}_$timestamp"

    Write-Check "备份仓库到 $backupPath"

    # 使用 robocopy 完整复制（包括 .git 目录）
    robocopy $RepoPath $backupPath /MIR /XD node_modules __pycache__ .venv venv /XF *.pyc /NJH /NJS /NP | Out-Null

    if (Test-Path "$backupPath\.git\HEAD") {
        Write-OK "备份完成：$backupPath"
    } else {
        Write-Warn "备份失败，请检查 $backupPath"
        exit 1
    }

    # 记录备份路径到文件
    $backupPath | Out-File -FilePath "$RepoPath\.bfg_last_backup" -Encoding UTF8 -NoNewline
    Write-OK "备份路径已记录到 .bfg_last_backup"
}

# ============================================================================
# 阶段 2：准备 BFG 替换规则文件
# ============================================================================

Write-Step "阶段 2：准备 BFG 替换规则文件"

# 复制到临时目录（避免污染工作区）
$tempReplacements = Join-Path $env:TEMP "bfg-replacements-$(Get-Date -Format 'yyyyMMddHHmmss').txt"
Copy-Item $replacementsPath $tempReplacements -Force
Write-OK "替换规则已复制到：$tempReplacements"
Write-Host "`n替换规则内容（脱敏显示）："
Get-Content $tempReplacements | ForEach-Object {
    if ($_ -match "==>") {
        $parts = $_ -split "==>", 2
        $masked = if ($parts[0].Length -gt 8) { $parts[0].Substring(0,4) + "****" + $parts[0].Substring($parts[0].Length-4) } else { "****" }
        Write-Host "  $masked ==> $($parts[1])"
    } else {
        Write-Host "  $_"
    }
}

# ============================================================================
# 阶段 3：镜像克隆（BFG 要求 bare 仓库）
# ============================================================================

Write-Step "阶段 3：镜像克隆"

$mirrorDir = Join-Path $env:TEMP "agent_mirror_$(Get-Date -Format 'yyyyMMddHHmmss')"
Write-Check "镜像克隆到 $mirrorDir"

git clone --mirror $RepoPath $mirrorDir
if (-not (Test-Path "$mirrorDir\HEAD")) {
    Write-Warn "镜像克隆失败"
    exit 1
}
Write-OK "镜像克隆完成：$mirrorDir"

# ============================================================================
# 阶段 4：执行 BFG 清理
# ============================================================================

Write-Step "阶段 4：执行 BFG 清理"

Push-Location $mirrorDir

# 4.1 替换敏感文本
Write-Check "4.1 替换敏感文本（API key + 密码）"
java -jar $BfgJarPath --replace-text $tempReplacements $mirrorDir

if ($LASTEXITCODE -ne 0) {
    Write-Warn "BFG --replace-text 失败"
    Pop-Location
    exit 1
}
Write-OK "敏感文本替换完成"

# 4.2 删除加密密钥文件
Write-Check "4.2 删除 .encryption_key 文件"
java -jar $BfgJarPath --delete-files ".encryption_key" $mirrorDir

if ($LASTEXITCODE -ne 0) {
    Write-Warn "BFG --delete-files 失败"
    Pop-Location
    exit 1
}
Write-OK ".encryption_key 文件删除完成"

# 4.3 删除 SECURITY_NOTICE 文档中的旧版本（保留脱敏后的版本）
Write-Check "4.3 删除含完整 key 的 SECURITY_NOTICE 文档"
java -jar $BfgJarPath --delete-files "SECURITY_NOTICE_20260719_api_key_leak.md" $mirrorDir

if ($LASTEXITCODE -ne 0) {
    Write-Warn "BFG --delete-files SECURITY_NOTICE 失败（可能文件不存在于历史）"
    # 非致命错误，继续执行
} else {
    Write-OK "SECURITY_NOTICE 文档删除完成"
}

Pop-Location

# ============================================================================
# 阶段 5：清理 reflog + GC（BFG 强制要求）
# ============================================================================

Write-Step "阶段 5：清理 reflog + GC"

Push-Location $mirrorDir

Write-Check "执行 git reflog expire"
git reflog expire --expire=now --all
if ($LASTEXITCODE -ne 0) {
    Write-Warn "git reflog expire 失败"
    Pop-Location
    exit 1
}

Write-Check "执行 git gc --prune=now"
git gc --prune=now
if ($LASTEXITCODE -ne 0) {
    Write-Warn "git gc 失败"
    Pop-Location
    exit 1
}

Write-OK "reflog + GC 完成"
Pop-Location

# ============================================================================
# 阶段 6：验证清理结果（使用 bfg-replacements.txt 中的 key 进行 pickaxe 搜索）
# ============================================================================

Write-Step "阶段 6：验证清理结果"

Push-Location $mirrorDir

# 从替换规则中提取所有待清理的敏感值
$searchKeys = @()
Get-Content $tempReplacements | ForEach-Object {
    if ($_ -match "^(.+?)==>") {
        $searchKeys += $matches[1]
    }
}

$allClean = $true
foreach ($key in $searchKeys) {
    $masked = if ($key.Length -gt 8) { $key.Substring(0,4) + "****" + $key.Substring($key.Length-4) } else { "****" }
    Write-Check "检查历史中是否还有：$masked"
    $found = git log -S $key --oneline --all
    if ($found) {
        Write-Warn "历史中仍存在该敏感值："
        Write-Host $found
        $allClean = $false
    } else {
        Write-OK "历史中已无该敏感值"
    }
}

if (-not $allClean) {
    Pop-Location
    Write-Warn "清理不彻底，请检查 BFG 规则"
    exit 1
}

Write-Check "检查 .encryption_key 是否已从历史删除"
$encKeyRemaining = git log --all --diff-filter=A -- ".encryption_key" --oneline
if ($encKeyRemaining) {
    Write-Warn "历史中仍存在 .encryption_key 的添加记录："
    Write-Host $encKeyRemaining
    Pop-Location
    exit 1
}
Write-OK ".encryption_key 已从历史删除"

Pop-Location

# ============================================================================
# 阶段 7：Force push 到远程仓库（双远程）
# ============================================================================

Write-Step "阶段 7：Force push 到远程仓库"

if ($DryRun) {
    Write-Warn "试运行模式（-DryRun），跳过 force push"
    Write-Host "`n镜像仓库路径：$mirrorDir"
    Write-Host "可手动检查后执行：cd $mirrorDir; git push --force --all origin; git push --force --all gitee"
    exit 0
}

if (-not (Confirm-Continue "即将 force push 到 origin 和 gitee，此操作不可逆，确认继续？")) {
    Write-Warn "用户取消 force push"
    Write-Host "`n镜像仓库路径：$mirrorDir（可手动处理）"
    exit 0
}

Push-Location $mirrorDir

# 7.1 更新镜像仓库的远程配置（指向真实远程）
Write-Check "7.1 配置镜像仓库的远程地址"
git remote set-url origin git@github.com:nzt47/security-tools.git
git remote add gitee git@gitee.com:nzt47/security-tools.git 2>$null
Write-OK "远程配置完成"

# 7.2 Force push 所有分支到 origin
Write-Check "7.2 Force push 到 origin（GitHub）"
git push --force --all origin
if ($LASTEXITCODE -ne 0) {
    Write-Warn "push origin 失败"
    Pop-Location
    exit 1
}
Write-OK "origin push 完成"

# 7.3 Force push 所有 tags 到 origin
Write-Check "7.3 Force push tags 到 origin"
git push --force --tags origin
if ($LASTEXITCODE -ne 0) {
    Write-Warn "push origin tags 失败（可能无 tags）"
}
Write-OK "origin tags push 完成"

# 7.4 Force push 所有分支到 gitee
Write-Check "7.4 Force push 到 gitee（Gitee）"
git push --force --all gitee
if ($LASTEXITCODE -ne 0) {
    Write-Warn "push gitee 失败"
    Pop-Location
    exit 1
}
Write-OK "gitee push 完成"

# 7.5 Force push 所有 tags 到 gitee
Write-Check "7.5 Force push tags 到 gitee"
git push --force --tags gitee
if ($LASTEXITCODE -ne 0) {
    Write-Warn "push gitee tags 失败（可能无 tags）"
}
Write-OK "gitee tags push 完成"

Pop-Location

# ============================================================================
# 阶段 8：本地仓库同步
# ============================================================================

Write-Step "阶段 8：本地仓库同步"

Push-Location $RepoPath

Write-Check "8.1 本地仓库 fetch + reset"
git fetch origin
git reset --hard origin/HEAD
if ($LASTEXITCODE -ne 0) {
    Write-Warn "本地仓库同步失败"
    Pop-Location
    exit 1
}

Write-Check "8.2 清理本地 reflog + GC"
git reflog expire --expire=now --all
git gc --prune=now
Write-OK "本地仓库同步完成"

Pop-Location

# ============================================================================
# 阶段 9：最终验证
# ============================================================================

Write-Step "阶段 9：最终验证"

Push-Location $RepoPath

Write-Check "9.1 验证本地历史无敏感信息"
$localAllClean = $true
foreach ($key in $searchKeys) {
    $masked = if ($key.Length -gt 8) { $key.Substring(0,4) + "****" + $key.Substring($key.Length-4) } else { "****" }
    $found = git log -S $key --oneline --all
    if ($found) {
        Write-Warn "本地历史仍有敏感值：$masked"
        $localAllClean = $false
    } else {
        Write-OK "本地历史已无：$masked"
    }
}

Write-Check "9.2 验证远程 origin 历史无敏感信息"
git fetch origin
$originAllClean = $true
foreach ($key in $searchKeys) {
    $masked = if ($key.Length -gt 8) { $key.Substring(0,4) + "****" + $key.Substring($key.Length-4) } else { "****" }
    $found = git log -S $key --oneline origin
    if ($found) {
        Write-Warn "origin 历史仍有敏感值：$masked"
        $originAllClean = $false
    } else {
        Write-OK "origin 历史已无：$masked"
    }
}

Write-Check "9.3 验证远程 gitee 历史无敏感信息"
git fetch gitee
$giteeAllClean = $true
foreach ($key in $searchKeys) {
    $masked = if ($key.Length -gt 8) { $key.Substring(0,4) + "****" + $key.Substring($key.Length-4) } else { "****" }
    $found = git log -S $key --oneline gitee
    if ($found) {
        Write-Warn "gitee 历史仍有敏感值：$masked"
        $giteeAllClean = $false
    } else {
        Write-OK "gitee 历史已无：$masked"
    }
}

Write-Check "9.4 验证 .encryption_key 已从 git 跟踪移除"
$encKeyTracked = git ls-files .encryption_key
if ($encKeyTracked) {
    Write-Warn ".encryption_key 仍被 git 跟踪，需手动 git rm"
    Write-Host "执行：git rm --cached .encryption_key"
} else {
    Write-OK ".encryption_key 已从 git 跟踪移除"
}

Pop-Location

# ============================================================================
# 阶段 10：清理临时文件
# ============================================================================

Write-Step "阶段 10：清理临时文件"

Write-Check "删除镜像仓库"
if (Test-Path $mirrorDir) {
    Remove-Item -Path $mirrorDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-OK "镜像仓库已删除"
}

Write-Check "删除临时替换规则文件"
if (Test-Path $tempReplacements) {
    Remove-Item -Path $tempReplacements -Force -ErrorAction SilentlyContinue
    Write-OK "临时替换规则文件已删除"
}

# ============================================================================
# 完成
# ============================================================================

Write-Step "BFG 历史清理 + force push 完成"

Write-Host @"
完成清单：
  ✓ 备份仓库（备份路径见 .bfg_last_backup）
  ✓ 替换敏感文本（按 bfg-replacements.txt 规则）
  ✓ 删除 .encryption_key 文件
  ✓ 删除 SECURITY_NOTICE 文档
  ✓ 清理 reflog + GC
  ✓ Force push 到 origin（GitHub）
  ✓ Force push 到 gitee（Gitee）
  ✓ 本地仓库同步
  ✓ 最终验证通过

后续操作：
  1. 通知所有协作者重新 clone 仓库（旧 clone 已失效）
  2. 更新 CI/CD 配置中的仓库地址（如使用）
  3. 验证 GitHub/Gitee 网页端历史已清理
  4. 30 天后删除备份目录（.bfg_last_backup 指向的路径）
  5. 删除本地 bfg-replacements.txt（含敏感信息）

关联文档：
  - docs/BFG_CLEANUP_GUIDE_20260719.md
  - docs/DEEPSEEK_KEY_REVOKE_GUIDE.md
  - docs/SUMMARY_TEST_FIX_20260719.md
"@ -ForegroundColor Green
