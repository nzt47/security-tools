# Pre-commit Hook 配置与中文路径修复总结

> **文档目的**：记录 pre-commit hook 阻塞模式的配置方法、中文路径修复的关键决策，以及日常维护操作。
> **更新日期**：2026-07-28
> **相关提交**：`fef8c52e` fix(dev): 修复预检脚本中文路径误报失效链接

---

## 一、架构概览

### 1.1 三层组件

```
┌─────────────────────────────────────────────────────────┐
│  git commit                                             │
│     ↓                                                   │
│  .git/hooks/pre-commit           ← bash 钩子（无 BOM）  │
│     ↓ 调用                                              │
│  scripts/dev/precheck_docs.ps1   ← PowerShell 预检脚本  │
│     ↓ 调用                                              │
│  [System.IO.File]::Exists()      ← .NET API 路径校验    │
│     ↓                                                   │
│  exit 0 (PASS) / exit 1 (BLOCK)                        │
└─────────────────────────────────────────────────────────┘
```

### 1.2 关键文件清单

| 文件 | 作用 | 编码 |
|------|------|------|
| [.git/hooks/pre-commit](file:///c:/Users/Administrator/agent/.git/hooks/pre-commit) | bash 钩子入口，调用 PowerShell 脚本 | **UTF-8 无 BOM**（bash 兼容） |
| [scripts/dev/precheck_docs.ps1](file:///c:/Users/Administrator/agent/scripts/dev/precheck_docs.ps1) | 预检脚本，检查 docs/ 下 Markdown 链接 | **UTF-8 with BOM**（PS 5.1 兼容） |
| [scripts/dev/fix_broken_links.ps1](file:///c:/Users/Administrator/agent/scripts/dev/fix_broken_links.ps1) | 批量修复失效链接工具 | **UTF-8 with BOM** |

---

## 二、阻塞模式工作原理

### 2.1 阈值控制

```powershell
# precheck_docs.ps1 关键逻辑（line 163-176）
if ($BlockMode) {
    if ($brokenLinks -gt $AllowBroken) {
        Write-Host "[BLOCK] 阻塞模式：失效链接 $brokenLinks > 阈值 $AllowBroken" -ForegroundColor Red
        exit 1   # 阻塞 commit
    } else {
        Write-Host "[PASS] 阻塞模式：失效链接 $brokenLinks <= 阈值 $AllowBroken" -ForegroundColor Green
        exit 0   # 允许 commit
    }
}
```

- **当前阈值**：`-AllowBroken 0`（严格模式，任何失效链接都阻塞）
- **历史阈值演进**：98（初始容忍）→ 10（过渡）→ 0（严格质量门）

### 2.2 三种行为矩阵

| 场景 | 失效链接数 | 阈值 | hook 行为 | exit code |
|------|----------|------|---------|-----------|
| PASS | 0 | 0 | 允许提交 | 0 |
| BLOCK | N > 0 | 0 | 阻塞提交 | 1 |
| BYPASS | 任意 | 任意 | `git commit --no-verify` 跳过 | 0 |

### 2.3 Hook 触发流程

1. `git commit` 触发 `.git/hooks/pre-commit`
2. bash 脚本调用 `powershell -ExecutionPolicy Bypass -File scripts/dev/precheck_docs.ps1 -SkipChart -BlockMode -AllowBroken 0`
3. PowerShell 扫描 `docs/` 下所有 `.md` 文件，提取 Markdown 链接（正则匹配 "方括号文本 + 圆括号路径" 形式）
4. 跳过 `http(s)`/`mailto`/`file:///`/`#`/`/` 开头的绝对链接
5. 用 .NET API 检查相对路径是否存在
6. 根据失效链接数 vs 阈值返回退出码

---

## 三、中文路径修复关键决策

### 3.1 问题表现

Windows PowerShell 5.1 默认按 GBK 解码 UTF-8 文件，导致：

```powershell
# 修复前（错误行为）
$content = Get-Content $file.FullName -Raw
# → 中文路径 "使用指南.md" 被解码为乱码 "浣跨敤鎸囧崡.md"

$fullPath = Join-Path $file.DirectoryName $linkPath
if (-not (Test-Path $fullPath)) { ... }
# → Test-Path 用乱码路径匹配 → 误报 BROKEN
```

**误报范围**：104 个中文路径链接被漏检或误报，3 个真实存在的中文文件被报为失效。

### 3.2 修复方案

```powershell
# 修复后（正确行为）
# 1. 显式 UTF-8 解码文件内容
$content = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)

# 2. 用 .NET API 处理 Unicode 路径
$fullPath = [System.IO.Path]::Combine($file.DirectoryName, $linkPath)
if (-not ([System.IO.File]::Exists($fullPath) -or [System.IO.Directory]::Exists($fullPath))) {
    # 真正的失效链接
}
```

### 3.3 脚本编码要求

**核心约束**：PowerShell 5.1 在 Windows 中文系统下默认按 GBK 解码无 BOM 的 UTF-8 文件。

| 文件类型 | 必须 BOM | 原因 |
|---------|---------|------|
| `.ps1` 脚本（含中文注释/字符串） | ✅ UTF-8 with BOM | 让 PS 5.1 正确按 UTF-8 解析 |
| bash 脚本（`.git/hooks/*`） | ❌ 无 BOM | BOM 会破坏 `#!/bin/bash` shebang |

### 3.4 修复验证

修复后链接检测能力恢复：
- 检测链接数：314 → 418（+104 个之前漏掉的中文路径链接）
- 失效链接：3（误报）→ 0（真实值）
- 中文路径阻塞测试：存在的文件 `使用指南.md` PASS，不存在的 `中文不存在_测试用.md` 正确 BLOCK

---

## 四、日常维护操作

### 4.1 检查失效链接（不阻塞）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev\precheck_docs.ps1 -SkipChart
```

### 4.2 模拟阻塞场景

```powershell
# 测试阈值 0 下的阻塞行为
powershell -ExecutionPolicy Bypass -File scripts\dev\precheck_docs.ps1 -SkipChart -BlockMode -AllowBroken 0
```

### 4.3 批量修复失效链接

```powershell
# DryRun 预览（不修改文件）
powershell -ExecutionPolicy Bypass -File scripts\dev\fix_broken_links.ps1 -DryRun

# 实际执行修复
powershell -ExecutionPolicy Bypass -File scripts\dev\fix_broken_links.ps1
```

### 4.4 重新安装 / 更新 Hook

```powershell
# 自动生成并安装 hook（阈值已硬编码为 0）
powershell -ExecutionPolicy Bypass -File scripts\dev\precheck_docs.ps1 -InstallHook
```

**注意**：`-InstallHook` 模板中 `-AllowBroken` 阈值已写死为 0（[precheck_docs.ps1:57](file:///c:/Users/Administrator/agent/scripts/dev/precheck_docs.ps1#L57)）。若需调整阈值，需同时修改：

1. [precheck_docs.ps1](file:///c:/Users/Administrator/agent/scripts/dev/precheck_docs.ps1) 第 57 行的 InstallHook 模板
2. [.git/hooks/pre-commit](file:///c:/Users/Administrator/agent/.git/hooks/pre-commit) 实际文件（注意保持无 BOM）

### 4.5 跳过 Hook（紧急情况）

```bash
git commit --no-verify -m "..."
```

**风险提示**：跳过 hook 后失效链接会进入仓库历史，下次其他开发者提交时会被阻塞。建议跳过后立即修复并补提交。

### 4.6 场景化测试（中文路径删除/重命名）

```powershell
# 模拟文件被删除/重命名/移动 5 个场景，验证 hook 实时阻塞
powershell -ExecutionPolicy Bypass -File scripts\dev\test_hook_chinese_path.ps1

# 仅清理上次测试残留
powershell -ExecutionPolicy Bypass -File scripts\dev\test_hook_chinese_path.ps1 -Cleanup
```

测试场景：基线（PASS）→ 删除（BLOCK）→ 重命名（BLOCK）→ 移动（BLOCK）→ `--no-verify` 跳过（PASS）。脚本自动回滚 HEAD 与测试目录，不污染工作区。

### 4.7 边界测试（10 场景自动化）

```powershell
# 运行 10 个中文路径边界测试，输出 Markdown 日志到 scripts/dev/logs/
powershell -ExecutionPolicy Bypass -File scripts\dev\test_hook_chinese_path_boundary.ps1

# 调试时保留测试文件
powershell -ExecutionPolicy Bypass -File scripts\dev\test_hook_chinese_path_boundary.ps1 -KeepTestFiles
```

覆盖场景：纯中文、中英混合、中文+数字、中文+空格、全角括号、深度路径、多 dots、重命名失效、生僻 Unicode。日志命名 `hook_chinese_path_test_YYYYMMDD_HHMMSS.md`。

---

## 五、故障排查

### 5.1 速查表

按下表对照报错信息快速定位根因与修复方案。

| # | 症状 / 报错信息 | 根因 | 修复方案 | 影响范围 |
|---|----------------|------|---------|---------|
| 1 | `git commit` 无任何输出直接成功 | hook 文件不存在或无执行权限 | 重装 hook：`powershell -ExecutionPolicy Bypass -File scripts\dev\precheck_docs.ps1 -InstallHook` | 仅本地 |
| 2 | `error: cannot spawn .git/hooks/pre-commit: No such file or directory` | hook 文件被加了 UTF-8 BOM，bash 不识别带 BOM 的 shebang `#!/bin/bash` | 用 .NET API 移除 BOM（见 5.2 修复代码） | 仅本地 hook 文件 |
| 3 | 实际存在的中文文件被报为 `[BROKEN]` | precheck_docs.ps1 丢失 BOM，PowerShell 5.1 按 GBK 解码导致中文路径乱码 | 给 precheck_docs.ps1 重新加 UTF-8 BOM（见 5.3 修复代码） | 仅本地脚本 |
| 4 | 脚本执行报 `ParserError` / `MissingCatchOrFinally` / `Array index expression is missing` | 脚本无 BOM，中文注释/字符串被 GBK 错误解码，字符串引号失配 | 同 #3，给脚本加 BOM | 仅本地脚本 |
| 5 | hook 输出显示失效链接数与实际不符 | Get-Content/Test-Path 在 PS 5.1 下对中文路径处理有缺陷 | 确认 precheck_docs.ps1 已使用 .NET API（`[System.IO.File]::ReadAllText` + `[System.IO.File]::Exists`） | 仅本地脚本 |

### 5.2 Hook 报 "cannot spawn" 的修复代码

```powershell
# 移除 hook 文件的 UTF-8 BOM（bash 无法识别带 BOM 的 shebang）
$hookPath = "C:\Users\Administrator\agent\.git\hooks\pre-commit"
$bytes = [System.IO.File]::ReadAllBytes($hookPath)
if ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $newBytes = $bytes[3..($bytes.Length - 1)]
    [System.IO.File]::WriteAllBytes($hookPath, $newBytes)
    Write-Host "[OK] BOM removed from pre-commit hook"
}
```

### 5.3 中文路径误报失效的修复代码

```powershell
# 给 precheck_docs.ps1 加 UTF-8 BOM（PowerShell 5.1 中文系统兼容）
$utf8WithBom = New-Object System.Text.UTF8Encoding $true
$content = [System.IO.File]::ReadAllText('scripts\dev\precheck_docs.ps1', [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText((Resolve-Path 'scripts\dev\precheck_docs.ps1').Path, $content, $utf8WithBom)
Write-Host "[OK] BOM added to precheck_docs.ps1"
```

### 5.4 编码规则速记

| 文件类型 | 必须 BOM | 原因 |
|---------|---------|------|
| `.ps1` 脚本（含中文注释/字符串） | ✅ UTF-8 with BOM | 让 PowerShell 5.1 正确按 UTF-8 解析 |
| `.git/hooks/*` bash 脚本 | ❌ UTF-8 无 BOM | BOM 会破坏 `#!/bin/bash` shebang |
| Markdown / JSON / YAML | ❌ UTF-8 无 BOM | 部分工具不兼容 BOM |

---

## 六、设计原则（三义校验）

| 义 | 体现 |
|----|------|
| **不易** | hook 阈值 0 不可绕过（除 `--no-verify`）；.NET API 路径校验为单一真相源；hook 与模板双写同步 |
| **变易** | 阈值参数化支持渐进式修复（98→10→0）；InstallHook 模板可重新生成；跳过机制保留紧急通道 |
| **简易** | 三层组件职责单一；故障排查有明确症状→根因→修复映射；文档自包含可独立维护 |

---

## 七、变更历史

| 日期 | 提交 | 变更 |
|------|------|------|
| 2026-07-28 | `f9687300` | 预检脚本增加阻塞模式 + 失效链接批量修复脚本 |
| 2026-07-28 | `b3cd60a7` | 批量修复 98 个失效 Markdown 链接 |
| 2026-07-28 | `fef8c52e` | 修复预检脚本中文路径误报失效链接 + 阈值降到 0 |
| 2026-07-29 | 待提交 | 新增场景测试脚本 + 10 场景边界测试脚本 + 故障排查表格化 |

---

## 八、相关文档

- [TLM_OVERVIEW.md](file:///c:/Users/Administrator/agent/docs/TLM_OVERVIEW.md) — TLM 架构总览
- [DEPLOYMENT_HISTORY.md](file:///c:/Users/Administrator/agent/docs/DEPLOYMENT_HISTORY.md) — 部署历史
- [V65_ONNX_DEPLOYMENT_PLAYBOOK.md](file:///c:/Users/Administrator/agent/docs/V65_ONNX_DEPLOYMENT_PLAYBOOK.md) — ONNX 部署手册
