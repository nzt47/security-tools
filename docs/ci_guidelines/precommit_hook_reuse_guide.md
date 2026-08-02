# Pre-commit Hook + CI 复用部署指南

> **目标读者**：需要在**其他仓库**复用「文档链接预检 + 锚点回归测试」的开发者
> **文档版本**：v1.0 | **更新日期**：2026-08-02
> **适用架构**：通用检查脚本 `git_precommit_check.ps1`（本地 hook 与 CI 共用同一入口）
> **前置文档**：[Hook 部署 Runbook](../HOOK_DEPLOYMENT_RUNBOOK.md)（旧版单仓库运维手册）

---

## 一、三件套架构（先理解再复用）

```
┌───────────────────────────── 本地（提交前拦截）────────────────────────────┐
│  git commit                                                            │
│    ↓                                                                    │
│  .git/hooks/pre-commit（bash，UTF-8 无 BOM）                            │
│    ↓ TLM_HOOK_SOURCE_REPO 间接寻址源仓库                                │
│  scripts/dev/git_precommit_check.ps1  ←── 唯一检查入口                   │
│    ├─ 检查1: precheck_docs.ps1 链接预检（阻塞模式，阈值 0）              │
│    └─ 检查2: pytest 锚点链接回归测试（python 可用时）                    │
│    └─ 任一项失败 → exit 1（阻止提交）                                    │
└──────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────── CI（PR 自动拦截）──────────────────────────┐
│  .github/workflows/ci.yml → docs-precheck-tests job（windows-latest）  │
│    ↓ 直接调用                                                           │
│  scripts/dev/git_precommit_check.ps1  ←── 同一入口                       │
│    + tests/regression/test_precommit_hook_blocking.py（坏提交拦截回归）  │
└──────────────────────────────────────────────────────────────────────────┘
```

**核心设计（不易）**：hook 与 CI 调用同一个 `git_precommit_check.ps1`，行为天然一致——
「入口返回非零」即代表「CI 拦截 + hook 阻止提交」。部署方无需维护两套检查逻辑。

---

## 二、快速复用 Git Hook（方式 A：本地拦截）

### 2.1 前置条件

| # | 检查项 | 验证命令 | 期望 |
|---|--------|---------|------|
| 1 | Windows + PowerShell 5.1 | `$PSVersionTable.PSVersion` | ≥ 5.1 |
| 2 | Git for Windows（含 bash） | `git --version` | ≥ 2.30 |
| 3 | 源仓库已 clone | `Test-Path scripts\dev\sync_precommit_hook.ps1` | True |

### 2.2 一条命令部署到任意仓库

```powershell
# 在【源仓库】根目录执行；-Install 指向【目标仓库】
powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Install D:\code\other-repo
```

脚本自动完成：
1. 生成 hook 内容（从 `hook_fail_safe.psm1` 的 `Get-HookContent` 模板）
2. 备份目标仓库已有 hook → `pre-commit.bak.<时间戳>`
3. 写入 hook（UTF-8 无 BOM）
4. 设置 **User 级**环境变量 `TLM_HOOK_SOURCE_REPO = <源仓库路径>`

批量部署（扫描目录下所有 git 仓库）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot D:\code -DryRun   # 先预览
powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Sync -ScanRoot D:\code          # 实际部署
```

查看安装状态：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev\sync_precommit_hook.ps1 -Status
```

### 2.3 验证

```powershell
# 1. 环境变量已写入 User 级
[System.Environment]::GetEnvironmentVariable('TLM_HOOK_SOURCE_REPO','User')
# 2. hook 已部署
Get-Content .git\hooks\pre-commit | Select-Object -First 3
# 期望输出含: # TLM-HOOK v1 source_repo=...
```

> **重要（不易）**：`TLM_HOOK_SOURCE_REPO` 是 hook 运行时寻址源仓库的唯一方式。
> 新开的终端会自动继承 User 级变量；**当前已打开的终端**需手动执行：
> ```powershell
> $env:TLM_HOOK_SOURCE_REPO = "<源仓库路径>"
> ```

### 2.4 工作原理（三道 fail-safe）

hook 内置三道防护，任一不满足即 `exit 1`，宁可误杀不可放过：

| # | 防护 | 触发条件 |
|---|------|---------|
| 1 | 环境变量缺失 | `TLM_HOOK_SOURCE_REPO` 未设置 |
| 2 | 检查脚本缺失 | `$TLM_HOOK_SOURCE_REPO/scripts/dev/git_precommit_check.ps1` 不存在 |
| 3 | 检查失败 | powershell 调用返回非零（链接失效 / 锚点回归未过） |

---

## 三、快速复用 CI 配置（方式 B：PR 自动拦截）

### 3.1 依赖文件清单

复制以下文件到目标仓库（保持相对路径）：

| 文件 | 作用 |
|------|------|
| `scripts/dev/git_precommit_check.ps1` | 检查入口（检查1 链接预检 + 检查2 锚点回归） |
| `scripts/dev/precheck_docs.ps1` | 链接预检脚本（被入口调用） |
| `scripts/dev/hook_fail_safe.psm1` | fail-safe 模块（hook 模板 + 15 个导出函数） |
| `scripts/dev/sync_precommit_hook.ps1` | 部署工具（可选，仅本地 hook 需要） |
| `tests/unit/test_precheck_docs_anchor_links.py` | 锚点链接回归测试（入口的检查2） |
| `tests/regression/test_precommit_hook_blocking.py` | hook 拦截回归测试（CI job 第 2 步） |

### 3.2 ci.yml 添加 job（复制即用）

```yaml
  # ============================================================================
  # 文档链接预检 + 锚点回归测试（PR 自动运行）
  # ============================================================================
  docs-precheck-tests:
    name: 文档链接预检与锚点回归测试
    runs-on: windows-latest          # 【不易】precheck_docs.ps1 是 PS 脚本，需 Windows
    timeout-minutes: 15
    steps:
      - name: 检出代码
        uses: actions/checkout@v4

      - name: 设置Python环境
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: 安装pytest
        run: |
          python -m pip install --upgrade pip
          pip install pytest

      - name: 运行文档链接预检 + 锚点回归测试
        # Why pwsh 外壳 + powershell.exe: 与本地 pre-commit hook 部署方式一致
        shell: pwsh
        run: |
          & powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/git_precommit_check.ps1 -TargetRepo $env:GITHUB_WORKSPACE
          if ($LASTEXITCODE -ne 0) {
            throw "文档链接预检 / 锚点回归测试失败: exit $LASTEXITCODE"
          }

      - name: 运行 hook 拦截回归测试（模拟坏文档提交）
        run: |
          python -m pytest tests/regression/test_precommit_hook_blocking.py -q -p no:randomly
```

### 3.3 触发条件

```yaml
on:
  pull_request:          # PR 自动运行（含依赖分支推送更新）
    branches: [main, master, develop]
```

> **说明**：若只想在改动 docs/ 或 hook 相关文件时触发，可加 `paths` 过滤：
> ```yaml
> pull_request:
>   paths:
>     - 'docs/**'
>     - 'scripts/dev/precheck_docs.ps1'
>     - 'scripts/dev/git_precommit_check.ps1'
>     - '.github/workflows/ci.yml'
> ```

---

## 四、故障排查速查表

| 现象 | 根因 | 解决 |
|------|------|------|
| `[pre-commit][ERROR] TLM_HOOK_SOURCE_REPO 未设置` | 终端未继承 User 级环境变量 | 当前终端执行 `$env:TLM_HOOK_SOURCE_REPO = "<源仓库路径>"`；新终端无需处理 |
| `[pre-commit][ERROR] 通用检查脚本不存在` | `TLM_HOOK_SOURCE_REPO` 指向错误仓库 | 检查变量值是否含 `scripts/dev/git_precommit_check.ps1` |
| commit 直接成功、无任何 pre-commit 输出 | hook 未被 Git 执行 | 检查 `.git/hooks/pre-commit` 是否存在、`git config core.hooksPath` 是否被改 |
| PS 脚本中文乱码 / 解析报 `Missing expression after unary operator '-'` | 文件开头叠加了多个 UTF-8 BOM | 见 [BOM 事故复盘](precommit_hook_bom_incident_report.md)，用单 BOM 重写 |
| 想临时绕过 | 紧急放行 | `git commit --no-verify`（仅应急，事后需补检查） |

---

## 五、卸载与回滚

```powershell
# 1. 删除 hook（或还原备份）
Remove-Item .git\hooks\pre-commit
Copy-Item .git\hooks\pre-commit.bak.<时间戳> .git\hooks\pre-commit

# 2. 清除 User 级环境变量（可选）
[System.Environment]::SetEnvironmentVariable('TLM_HOOK_SOURCE_REPO', $null, 'User')
```

---

## 六、变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-08-02 | v1.0 | 基于通用检查脚本 `git_precommit_check.ps1` 的三件套复用指南（替代旧版直连 `precheck_docs.ps1` 模式） |
