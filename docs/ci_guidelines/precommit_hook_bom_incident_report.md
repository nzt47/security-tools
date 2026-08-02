# Pre-commit Hook BOM 事故复盘与 Hook 逻辑说明

> **文档目的**：记录 2026-08-02 排查并修复的「UTF-8 BOM 叠加」事故全过程，
> 以及修复后 Hook 的检查逻辑与编码契约，供团队后续排查同类问题参考。
> **文档版本**：v1.0 | **更新日期**：2026-08-02

---

## 一、事件摘要

| 项 | 内容 |
|----|------|
| 影响范围 | `sync_precommit_hook.ps1`、`hook_fail_safe.psm1`、`packages/tlm-hook-failsafe/` 下 5 个 PS 脚本 |
| 根因 | 文件开头叠加了多个 UTF-8 BOM（`EF BB BF` 连续出现 3~9 个） |
| 直接后果 | PowerShell 5.1 解析块注释失败，把 `<#` 内的 `- 不易：…` 当代码执行，抛 `Missing expression after unary operator '-'` |
| 连带后果 | ① `Import-Module hook_fail_safe.psm1` 失败 → 部署的 hook 仍是**旧模板**（直连 `precheck_docs.ps1`）；② post-commit 同步 `sync-from-source.ps1` 失败 |
| 修复 | `TrimStart([char]0xFEFF)` 去除全部 BOM + 用 `UTF8Encoding($true)` 写回**单个** BOM |
| 验证 | `Import-Module` 0 错误；`Get-HookContent` 返回新模板；包测试 50/50 通过；两次提交 pre-commit 全量预检通过 |

---

## 二、编码契约（不易，先记住结论）

| 文件类型 | 编码要求 | 原因 |
|----------|---------|------|
| `.git/hooks/pre-commit`（bash） | **UTF-8 无 BOM** | bash 不识别 BOM，`#!/bin/bash` 前有 BOM 会直接报错 |
| `.ps1` / `.psm1`（PowerShell） | **UTF-8 单个 BOM** | PS 5.1 在中文系统默认按 GBK 解码，无 BOM 中文乱码 |
| `.py`（Python） | UTF-8 无 BOM | Python 源码约定 |

**关键规则**：PS 脚本必须是「恰好 1 个 BOM」。0 个会乱码；≥2 个会破坏 `<#` 块注释。

### 为什么叠加 BOM 会破坏块注释？

```
正常：EF BB BF 3C 23 0D 0A .SYNOPSIS ...
       [BOM]  <#  CRLF
叠加：EF BB BF EF BB BF 3C 23 0D 0A .SYNOPSIS ...
       [BOM]  [BOM]  <#
```

PowerShell 5.1 按 UTF-8 解码时，**第一个** BOM 被识别为文件标记，但**后续的 BOM 字节序列**（`EF BB BF` 解码为 U+FEFF，此时它位于行首、紧贴 `<#`）会让 `<#` 不再处于第 1 行行首，块注释解析器认为 `<#` 不是行首标记——于是 `<# ... #>` 内的注释内容（如 `- 不易：…`）被当成 PowerShell 代码解析，报语法错误。

---

## 三、事故时间线

### 事故 1：`sync_precommit_hook.ps1` 开头 9 个叠加 BOM

- **现象**：运行部署脚本直接报 `Missing expression after unary operator '-'`（第 8-10 行 `- 不易：…` 被当代码）
- **诊断**：
  ```powershell
  [System.IO.File]::ReadAllBytes('scripts\dev\sync_precommit_hook.ps1')[0..8] -join ' '
  # 输出: 239 187 191 239 187 191 239 187 191 239 187 191 ...（连续 9 个 BOM）
  ```
- **修复**：`$c.TrimStart([char]0xFEFF)` + `UTF8Encoding($true)` 写回单 BOM
  ```powershell
  $c = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
  $trimmed = $c.TrimStart([char]0xFEFF)
  [System.IO.File]::WriteAllText($path, $trimmed, (New-Object System.Text.UTF8Encoding($true)))
  ```

### 事故 2：`hook_fail_safe.psm1` 开头 5 个叠加 BOM → 部署旧模板

- **现象（最隐蔽）**：`sync_precommit_hook.ps1` 部署成功（`DONE agent`），但生成的 hook 仍是**旧模板**（调用 `precheck_docs.ps1`，而非新的 `git_precommit_check.ps1`）
- **排查**：
  ```powershell
  # 1. 验证模块磁盘内容已含新模板（grep CHECK_PATH）
  #    → 磁盘文件 OK（第 65 行有 CHECK_PATH）
  # 2. 验证 PS 运行时行为（Import-Module + Get-HookContent）
  Import-Module hook_fail_safe.psm1 -Force
  $c = Get-HookContent -SourceRepo 'C:\...\agent'
  ([regex]::Matches($c, 'git_precommit_check\.ps1')).Count   # 期望 ≥1
  #    → 实际 CHECK_PATH count: 0，PS1_PATH count: 4（旧模板！）
  # 3. 关键证据：Import-Module 时报
  #    Missing expression after unary operator '-'（第 8-10 行 - 不易：…）
  #    → 模块解析失败，$Error.Count > 0
  ```
- **根因**：`hook_fail_safe.psm1` 第 1 行叠加 BOM → `Import-Module` 解析失败 → 模块未加载成功（或加载了内存缓存旧版）→ `Get-HookContent` 返回旧模板
- **修复**：同事故 1 的 TrimStart 方案。修复后验证：
  ```
  ImportModule Errors: 0
  CHECK_PATH count: 2    # 新模板生效
  PS1_PATH count: 0
  ```

### 事故 3：`packages/tlm-hook-failsafe/` 5 个文件叠加 BOM → post-commit 同步失败

- **现象**：提交成功但 post-commit 报 `sync-from-source.ps1` 解析失败
- **扫描**（一次扫出全部）：
  ```powershell
  Get-ChildItem packages\tlm-hook-failsafe -Recurse -Include *.ps1,*.psm1 |
    ForEach-Object {
      $b = [System.IO.File]::ReadAllBytes($_.FullName)
      if ($b[0] -eq 239 -and $b[1] -eq 187 -and $b[2] -eq 191 -and $b[3] -eq 239) {
        Write-Host "STACKED BOM: $($_.FullName)"
      }
    }
  # 命中 5 个：install.ps1 / sync-from-source.ps1 / tests/test_*.ps1 ×3
  ```
- **修复**：批量 TrimStart + 单 BOM 写回 → `sync-from-source.ps1` 运行成功（15 个导出函数校验 + hash 匹配）

> **经验教训**：对「无 BOM」文件执行「TrimStart + 写 BOM」会**误加** BOM。
> 修复前先确认原文件是「无 BOM / 单 BOM / 叠加 BOM」，只处理叠加 BOM 的文件。

---

## 四、根因：BOM 为什么会被叠加？

常见来源：编辑工具反复执行「按 UTF-8 读入 + 按 UTF-8（含 BOM）写出」，每次写都会在内容前**追加**一个 BOM，而读入时若工具不剥离已有 BOM，就会 `1 → 2 → 3 …` 累积。本项目 `hook_fail_safe.psm1` 曾被 `sync-from-source.ps1` 复制、又被人工编辑多次，叠加到 5 层。

---

## 五、修复后 Hook 逻辑说明

### 5.1 入口编排（`git_precommit_check.ps1`）

```
git_precommit_check.ps1 -TargetRepo <仓库根>
  ├─ 检查1: precheck_docs.ps1 -SkipChart -BlockMode -AllowBroken 0
  │    扫描 <仓库根>/docs/ 下 Markdown 链接：
  │    - 剥离 #锚点 部分（./target.md#四、告警规则 → ./target.md），只校验文件部分
  │    - 归一化 ./ 前缀；纯锚点(#…) / 外链(http…) / mailto 跳过
  │    - 任何失效链接 → exit 1（[BROKEN] + [BLOCK]）
  └─ 检查2: pytest tests/unit/test_precheck_docs_anchor_links.py（4 用例）
       仅当 python 可用且测试文件存在时运行（避免阻断未配置测试环境的仓库）
  └─ 汇总「通过: N | 失败: M」；fail > 0 → exit 1
```

### 5.2 三道 fail-safe（hook 内）

| # | 防护 | 错误信息 |
|---|------|---------|
| 1 | `TLM_HOOK_SOURCE_REPO` 未设置 → exit 1 | `[pre-commit][ERROR] TLM_HOOK_SOURCE_REPO 未设置` |
| 2 | `git_precommit_check.ps1` 不存在 → exit 1 | `[pre-commit][ERROR] 通用检查脚本不存在` |
| 3 | powershell 调用失败 → exit 1 | `[pre-commit] 预检失败，提交被阻止` |

### 5.3 环境变量间接寻址

hook 不写死脚本路径，通过 `TLM_HOOK_SOURCE_REPO`（User 级 + 当前进程）间接定位源仓库，因此**同一份 hook 可复制到任意仓库**：

```bash
CHECK_PATH="$TLM_HOOK_SOURCE_REPO/scripts/dev/git_precommit_check.ps1"
powershell -ExecutionPolicy Bypass -File "$CHECK_PATH" -TargetRepo "$(git rev-parse --show-toplevel)"
```

---

## 六、防复发措施

1. **编码契约沉淀**：PS 脚本单 BOM / hook 无 BOM（见第二节），新增/编辑脚本后自查
2. **一键扫描**（检出叠加 BOM 文件）：
   ```powershell
   Get-ChildItem scripts\dev,packages -Recurse -Include *.ps1,*.psm1 |
     Where-Object { $b = [System.IO.File]::ReadAllBytes($_.FullName); $b[0] -eq 239 -and $b[3] -eq 239 } |
     ForEach-Object { $_.FullName }
   ```
3. **回归测试**：
   - `tests/regression/test_precommit_hook_blocking.py`：模拟坏文档提交，验证 hook/CI 拦截
   - `tests/unit/test_precheck_docs_anchor_links.py`：锚点链接预检 4 用例
   - `packages/tlm-hook-failsafe/tests/`：模块解析 + 15 函数导出校验
4. **相关提交**（含修复与模板切换）：
   - `7d70b1ec` feat(dev): 封装通用 git pre-commit 检查脚本并集成锚点测试到 CI
   - `a417b3dc` fix(dev): 修复 tlm-hook-failsafe 包叠加 BOM 并同步 hook 模板

---

## 七、变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-02 | v1.0 | 首次成文：BOM 叠加事故复盘 + Hook 逻辑说明 |
