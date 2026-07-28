# Pre-commit Hook 运维部署操作手册

> **目标读者**：运维工程师、新入职开发者
> **适用场景**：新机器初始化、新仓库接入、hook 故障排查、回滚恢复
> **文档版本**：v1.0 | **更新日期**：2026-07-29

---

## 一、环境准备

### 1.1 前置条件检查

| # | 检查项 | 验证命令 | 期望结果 |
|---|--------|---------|---------|
| 1 | Windows 10/11 或 Windows Server | `winver` | 1903+ |
| 2 | PowerShell 5.1+ | `$PSVersionTable.PSVersion` | 5.1 或更高 |
| 3 | Git for Windows | `git --version` | 2.30+ |
| 4 | bash 可执行 | `bash --version` | 任意版本 |
| 5 | 源仓库已 clone | `Test-Path C:\Users\Administrator\agent\scripts\dev\sync_precommit_hook.ps1` | True |
| 6 | precheck_docs.ps1 存在 | `Test-Path C:\Users\Administrator\agent\scripts\dev\precheck_docs.ps1` | True |
| 7 | fix_broken_links.ps1 存在 | `Test-Path C:\Users\Administrator\agent\scripts\dev\fix_broken_links.ps1` | True |
| 8 | hook_fail_safe.psm1 存在 | `Test-Path C:\Users\Administrator\agent\scripts\dev\hook_fail_safe.psm1` | True |

**一键检查脚本**：
```powershell
$checks = @(
    @{Item='PowerShell'; Command='$PSVersionTable.PSVersion.ToString()'; Expect='5.1'},
    @{Item='Git'; Command='git --version'; Expect='git version'},
    @{Item='sync script'; Command='Test-Path C:\Users\Administrator\agent\scripts\dev\sync_precommit_hook.ps1'; Expect='True'},
    @{Item='fail-safe module'; Command='Test-Path C:\Users\Administrator\agent\scripts\dev\hook_fail_safe.psm1'; Expect='True'}
)
foreach ($c in $checks) {
    $result = Invoke-Expression $c.Command
    $ok = $result -match $c.Expect
    Write-Host "[$(if($ok){'OK'}else{'FAIL'})] $($c.Item): $result" -ForegroundColor $(if($ok){'Green'}else{'Red'})
}
```

### 1.2 首次部署步骤

```powershell
# 切换到源仓库根目录
cd C:\Users\Administrator\agent

# 步骤 1：安装到当前仓库（设置环境变量 + 部署 hook）
powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1

# 步骤 2：验证环境变量已写入
[System.Environment]::GetEnvironmentVariable('TLM_HOOK_SOURCE_REPO','User')
# 应输出: C:\Users\Administrator\agent

# 步骤 3：验证 hook 已部署
Test-Path .git\hooks\pre-commit
# 应输出: True

# 步骤 4：重启所有已打开的终端（让 User 级环境变量生效）
# 或在当前终端手动执行：
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"
```

### 1.3 批量部署到本机其他仓库

```powershell
# 扫描 D:\code 下所有 git 仓库并部署
powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot D:\code

# 扫描默认目录（c:\Users\Administrator 下一层）
powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Sync
```

### 1.4 新 clone 仓库后部署

```powershell
# clone 新仓库后
cd D:\code\new-repo
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\agent\scripts\dev\sync_precommit_hook.ps1 -Install D:\code\new-repo
```

---

## 二、常见报错排查

### 2.1 速查表

| # | 症状 / 报错信息 | 根因 | 修复方案 | 严重程度 |
|---|----------------|------|---------|---------|
| 1 | `git commit` 无输出直接成功 | hook 文件不存在 | `sync_precommit_hook.ps1 -Install <repo>` | 高 |
| 2 | `cannot spawn .git/hooks/pre-commit` | hook 文件有 BOM | 用 .NET API 移除 BOM（见 2.2） | 高 |
| 3 | 中文文件被误报 `[BROKEN]` | precheck_docs.ps1 丢失 BOM | 给脚本重新加 BOM（见 2.3） | 中 |
| 4 | 脚本报 `ParserError` / `MissingCatchOrFinally` | 脚本无 BOM | 同 #3 | 中 |
| 5 | 失效链接数与实际不符 | 未用 .NET API 处理中文路径 | 确认 precheck_docs.ps1 使用 `[System.IO.File]` 系列 API | 中 |
| 6 | `TLM_HOOK_SOURCE_REPO 未设置` | sync 脚本未运行过 / 终端是部署前打开的 | 重跑 sync 或手动设置环境变量（见 2.4） | 高 |
| 7 | `源仓库脚本不存在: <path>` | 源仓库被移动/重命名 | 重新定位源仓库后重跑 sync（见 2.5） | 高 |
| 8 | `[pre-commit] 预检失败，提交被阻止` | docs/ 含失效 Markdown 链接 | 运行 fix_broken_links.ps1 修复（见 2.6） | 正常（预期行为） |
| 9 | sync 后 commit 仍检测源仓库的 docs | precheck_docs.ps1 缺少 -TargetRepo 参数 | 确认 precheck_docs.ps1 已更新支持 -TargetRepo | 高 |
| 10 | `Import-Module` 失败 | hook_fail_safe.psm1 不存在或无 BOM | 确认模块文件存在且有 BOM（见 2.7） | 高 |

### 2.2 hook 文件有 BOM 的修复

```powershell
# 移除 hook 文件的 UTF-8 BOM
$hookPath = "<repo>\.git\hooks\pre-commit"
$bytes = [System.IO.File]::ReadAllBytes($hookPath)
if ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $newBytes = $bytes[3..($bytes.Length - 1)]
    [System.IO.File]::WriteAllBytes($hookPath, $newBytes)
    Write-Host "[OK] BOM removed"
}
```

### 2.3 脚本丢失 BOM 的修复

```powershell
# 给 precheck_docs.ps1 加 UTF-8 BOM
$utf8WithBom = New-Object System.Text.UTF8Encoding $true
$path = "C:\Users\Administrator\agent\scripts\dev\precheck_docs.ps1"
$content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($path, $content, $utf8WithBom)
```

### 2.4 TLM_HOOK_SOURCE_REPO 未设置的修复

```powershell
# 方案 A：重跑 sync 脚本（推荐，会自动写入 User 级 + 当前进程）
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\agent\scripts\dev\sync_precommit_hook.ps1 -Install <repo>

# 方案 B：当前终端临时设置（重启后失效）
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"

# 方案 C：永久设置（User 级，需重启终端生效）
[System.Environment]::SetEnvironmentVariable('TLM_HOOK_SOURCE_REPO', 'C:\Users\Administrator\agent', 'User')
```

### 2.5 源仓库被移动的修复

```powershell
# 1. 确认源仓库新路径
$newPath = "D:\new-location\agent"

# 2. 重跑 sync 指定新路径（会更新环境变量 + 重写所有 hook 的 marker 行）
powershell -ExecutionPolicy Bypass -File "$newPath\scripts\dev\sync_precommit_hook.ps1" -Sync -ScanRoot D:\code -SourceRepo $newPath

# 3. 重启终端验证
[System.Environment]::GetEnvironmentVariable('TLM_HOOK_SOURCE_REPO','User')
# 应输出: D:\new-location\agent
```

### 2.6 失效链接修复

```powershell
# 1. 预览失效链接列表（DryRun）
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\agent\scripts\dev\fix_broken_links.ps1 -DryRun

# 2. 实际修复
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\agent\scripts\dev\fix_broken_links.ps1

# 3. 验证
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\agent\scripts\dev\precheck_docs.ps1 -SkipChart
```

### 2.7 Import-Module 失败的修复

```powershell
# 1. 检查模块文件存在
Test-Path C:\Users\Administrator\agent\scripts\dev\hook_fail_safe.psm1
# 应输出: True

# 2. 检查模块文件有 BOM
$bytes = [System.IO.File]::ReadAllBytes("C:\Users\Administrator\agent\scripts\dev\hook_fail_safe.psm1")
$hasBom = $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
# 应输出: True

# 3. 若无 BOM，加上
$utf8WithBom = New-Object System.Text.UTF8Encoding $true
$path = "C:\Users\Administrator\agent\scripts\dev\hook_fail_safe.psm1"
$content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($path, $content, $utf8WithBom)
```

---

## 三、回滚步骤

### 3.1 单仓库回滚（恢复备份的 hook）

```powershell
$repo = "D:\code\some-repo"
$hooksDir = "$repo\.git\hooks"

# 1. 查看可用备份
$bakFiles = Get-ChildItem $hooksDir -Filter 'pre-commit.bak.*' | Sort-Object LastWriteTime -Descending
$bakFiles | Format-Table Name, LastWriteTime

# 2. 恢复最新的备份
if ($bakFiles.Count -gt 0) {
    $latest = $bakFiles[0]
    Copy-Item $latest.FullName "$hooksDir\pre-commit" -Force
    Write-Host "[OK] 已恢复备份: $($latest.Name)" -ForegroundColor Green
} else {
    Write-Host "[WARN] 无备份可恢复，直接删除 hook" -ForegroundColor Yellow
    Remove-Item "$hooksDir\pre-commit" -Force -ErrorAction SilentlyContinue
}

# 3. 验证
git -C $repo commit --allow-empty -m "test hook restored" 2>&1
# 若 hook 正常则提交成功，否则根据报错进一步排查
git -C $repo reset --mixed HEAD~1  # 撤销测试提交
```

### 3.2 批量回滚（所有仓库恢复备份）

```powershell
# 扫描所有仓库，恢复最新备份
$scanRoot = "D:\code"
$repos = Get-ChildItem $scanRoot -Directory | Where-Object { Test-Path "$($_.FullName)\.git" }

foreach ($repo in $repos) {
    $hooksDir = "$($repo.FullName)\.git\hooks"
    $baks = @(Get-ChildItem $hooksDir -Filter 'pre-commit.bak.*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($baks.Count -gt 0) {
        Copy-Item $baks[0].FullName "$hooksDir\pre-commit" -Force
        Write-Host "[OK] $($repo.Name) <- $($baks[0].Name)" -ForegroundColor Green
    } else {
        Remove-Item "$hooksDir\pre-commit" -Force -ErrorAction SilentlyContinue
        Write-Host "[DEL] $($repo.Name) hook 已删除（无备份）" -ForegroundColor Yellow
    }
}
```

### 3.3 完全卸载 hook

```powershell
# 1. 删除所有目标仓库的 hook
$scanRoot = "D:\code"
$repos = Get-ChildItem $scanRoot -Directory | Where-Object { Test-Path "$($_.FullName)\.git" }
foreach ($repo in $repos) {
    $hookPath = "$($repo.FullName)\.git\hooks\pre-commit"
    if (Test-Path $hookPath) {
        Remove-Item $hookPath -Force
        Write-Host "[DEL] $($repo.Name) hook 已删除" -ForegroundColor Yellow
    }
}

# 2. 删除环境变量
[System.Environment]::SetEnvironmentVariable('TLM_HOOK_SOURCE_REPO', $null, 'User')
$env:TLM_HOOK_SOURCE_REPO = $null
Write-Host "[OK] 环境变量已删除" -ForegroundColor Green

# 3. 验证
[System.Environment]::GetEnvironmentVariable('TLM_HOOK_SOURCE_REPO','User')
# 应输出空
```

### 3.4 紧急跳过 hook（不推荐，仅应急）

```powershell
# 单次提交跳过 hook
git commit --no-verify -m "emergency hotfix"

# 注意：跳过后请立即修复失效链接并补提交，避免技术债
```

---

## 四、状态查询与日常维护

### 4.1 查看所有仓库 hook 状态

```powershell
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\agent\scripts\dev\sync_precommit_hook.ps1 -Status -ScanRoot D:\code
```

输出示例：
```
Repo       Status    Backup Threshold
----       ------    ------ ---------
repo-a     INSTALLED -      0
repo-b     OTHER_HOOK -     ?
repo-c     NOT_INSTALLED -  -
```

状态说明：
| 状态 | 含义 | 建议操作 |
|------|------|---------|
| INSTALLED | 本工具部署的 hook，阈值 0 | 无需操作 |
| OK_LATEST | 已是最新版本，跳过 | 无需操作 |
| OTHER_HOOK | 存在非本工具的 hook | 评估是否需要替换 |
| NOT_INSTALLED | 无 hook | 运行 sync 安装 |
| SKIP | 非 git 仓库 | 无需操作 |
| DRYRUN | DryRun 预览 | 实际部署去掉 -DryRun |

### 4.2 定期检查（建议每周）

```powershell
# 1. 检查所有仓库状态
powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\agent\scripts\dev\sync_precommit_hook.ps1 -Status

# 2. 检查源仓库文档链接
cd C:\Users\Administrator\agent
powershell -ExecutionPolicy Bypass -File scripts\dev\precheck_docs.ps1 -SkipChart

# 3. 运行边界测试（10 场景）
powershell -ExecutionPolicy Bypass -File scripts\dev\test_hook_chinese_path_boundary.ps1
```

### 4.3 运行场景测试（建议变更后）

```powershell
# 5 场景测试（删除/重命名/移动/基线/跳过）
powershell -ExecutionPolicy Bypass -File scripts\dev\test_hook_chinese_path.ps1

# 10 场景边界测试
powershell -ExecutionPolicy Bypass -File scripts\dev\test_hook_chinese_path_boundary.ps1

# 批量同步模拟测试
powershell -ExecutionPolicy Bypass -File scripts\dev\simulate_batch_sync.ps1
```

---

## 五、架构与文件清单

### 5.1 文件结构

```
scripts/dev/
├── hook_fail_safe.psm1          # fail-safe 核心模块（9 个导出函数）
├── sync_precommit_hook.ps1      # 部署编排脚本（Import-Module 复用模块）
├── precheck_docs.ps1            # hook 调用的检测脚本（含 -TargetRepo）
├── fix_broken_links.ps1         # 失效链接批量修复脚本
├── test_hook_chinese_path.ps1   # 5 场景测试脚本
├── test_hook_chinese_path_boundary.ps1  # 10 场景边界测试
├── simulate_batch_sync.ps1      # 批量同步模拟测试
└── logs/                        # 测试日志归档
    └── hook_chinese_path_test_YYYYMMDD_HHMMSS.md

docs/
├── PRECOMMIT_HOOK_GUIDE.md      # hook 配置与中文路径修复总结
└── HOOK_DEPLOYMENT_RUNBOOK.md   # 本文档（运维操作手册）
```

### 5.2 模块依赖关系

```
sync_precommit_hook.ps1
    ├── Import-Module hook_fail_safe.psm1
    │   ├── Get-HookContent        生成 hook bash 内容
    │   ├── Write-HookNoBom        写 hook（无 BOM）
    │   ├── Backup-ExistingHook    备份已有 hook
    │   ├── Test-HookUpToDate      幂等检测
    │   ├── Set-SourceRepoEnv      设置环境变量
    │   ├── Test-SourceRepoEnv     验证环境变量
    │   ├── Resolve-GitDir        解析 .git 路径
    │   └── Test-HookMarker        检测 hook marker
    └── Find-GitRepos              扫描 git 仓库（本脚本独有）

.git/hooks/pre-commit
    └── 调用 precheck_docs.ps1 -TargetRepo <repo>
        └── 检测 docs/ Markdown 链接
```

### 5.3 编码契约速记

| 文件类型 | BOM 要求 | 原因 |
|---------|---------|------|
| `.ps1` / `.psm1` 脚本（含中文） | ✅ UTF-8 with BOM | PowerShell 5.1 中文系统兼容 |
| `.git/hooks/*` bash 脚本 | ❌ UTF-8 无 BOM | BOM 破坏 `#!/bin/bash` shebang |
| Markdown / JSON / YAML | ❌ UTF-8 无 BOM | 工具兼容性 |

---

## 六、三义校验

| 义 | 体现 |
|----|------|
| **不易** | hook 阈值 0 不可绕过；hook 无 BOM / PS1 有 BOM 编码契约；已有 hook 必先备份；fail-safe 三道防护 |
| **变易** | 四模式正交（install/sync/status/dryrun）；环境变量可重指向；fail-safe 模块可被其他脚本复用 |
| **简易** | 单命令部署；输出自解释含汇总表；故障速查表 10 类症状一键定位 |

---

## 七、变更历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-07-29 | v1.0 | 初始版本：环境准备 / 报错排查 / 回滚步骤 / 状态查询 / 架构说明 |
