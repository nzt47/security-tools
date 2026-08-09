# 技术改进提案：PowerShell 脚本质量保障体系

> **状态**: 已实施 ✅
> **日期**: 2026-08-01
> **关联 commit**: `2542974c` fix(scripts): 修复 fix_broken_links.ps1 三个 bug
> **范围**: `scripts/dev/` 下 PowerShell 脚本

---

## 1. 背景与问题

### 1.1 事件回顾

2026-08-01 修复 `fix_broken_links.ps1` 时发现三个 Bug，均因**缺乏自动化质量保障**而长期潜伏：

| Bug | 根因 | 影响 |
|-----|------|------|
| 1 正则跨行贪婪匹配 | `[^\]]+`/`[^)]+` 匹配换行符，全角括号触发跨行 | 把表格/标题/多链接吞成一个"链接"，误报失效 |
| 2 Get-Content 未指定编码 | PS 5.x 默认 GBK 读无 BOM UTF-8，中文路径乱码 | `Test-Path` 检查乱码路径失败，有效链接被误判失效 |
| 3 单引号 `` `n `` 不转义 | 单引号字符串中 `` `n `` 是字面量非换行 | 扫描提示行原样输出 `` `n `` 而非换行 |

### 1.2 根本原因

核查 CI/CD 流水线后发现 `scripts/dev/` 下的 PowerShell 脚本**完全缺乏自动化保障**：

- `.github/workflows/ci.yml` 无 ps1 脚本 lint/测试步骤
- `.pre-commit-config.yaml` 无 PowerShell 相关 hook
- `tests/` 无针对 `fix_broken_links.ps1` 的单元测试

三个 Bug 中，Bug 1（跨行匹配）和 Bug 2（编码乱码）本可被静态分析或单元测试拦截，但因无任何检查而进入仓库。

---

## 2. 改进目标

1. **回归守卫**：三个 Bug 的修复不被改回 buggy 版本
2. **静态分析**：提交前拦截 PowerShell 语法错误与不良实践
3. **CI 兼容**：本地(Windows pwsh)与 CI(ubuntu-latest pwsh)行为一致
4. **最小侵入**：不改动现有测试（AdminDependencyChecker.Tests.ps1 用 Pester 4.10.1），新测试独立运行

---

## 3. 实施方案

采用三层防护：**单元测试（行为+契约）+ 静态分析（PSScriptAnalyzer）+ pre-commit hook（自动触发）**。

### 3.1 Pester 单元测试

**文件**: [tests/unit/fix_broken_links.Tests.ps1](../../tests/unit/fix_broken_links.Tests.ps1)

**框架**: Pester 5.0+（本地 6.0.1 验证），与现有 AdminDependencyChecker.Tests.ps1（Pester 4.10.1）独立运行互不影响。

**覆盖策略**: 每个 Bug 双层覆盖

| 层 | 作用 | 示例 |
|----|------|------|
| 行为验证 | 构造触发场景，断言修复后正确行为 | 构造含全角 `】` 的多行内容，验证正则不跨行匹配 |
| 源码契约 | 验证脚本源码包含修复关键字符 | 验证源码含 `[^\]\r\n]`，防止改回跨行版本 |

**dot-source 副作用控制**: 测试需加载脚本函数，但脚本含主流程会扫描 `docs/`。通过 `Mock Get-ChildItem { }` 在 dot-source 前拦截，避免扫描副作用。

**14 个测试用例分布**:

| Describe | 行为验证 | 源码契约 | 小计 |
|----------|---------|---------|------|
| Bug 1 正则跨行 | 3 | 1 | 4 |
| Bug 2 编码乱码 | 3 | 1 | 4 |
| Bug 3 单引号转义 | 2 | 1 | 3 |
| 附加 BOM 写入 | 0 | 3 | 3 |
| **合计** | **8** | **6** | **14** |

### 3.2 PSScriptAnalyzer 静态分析

**文件**: [scripts/dev/run_psscriptanalyzer.ps1](../../scripts/dev/run_psscriptanalyzer.ps1)

**包装脚本职责**:
- 自动检测并安装 PSScriptAnalyzer 模块（本地首次 + CI ubuntu-latest）
- 扫描 `scripts/dev/` 下所有 `.ps1`，默认 `Severity=Error` 只拦截严重问题
- 发现问题 `exit 1`，无问题 `exit 0`

**设计取舍**:
- `Severity=Error`（默认）只阻断严重问题，避免噪声阻断开发；严格模式可用 `-Severity Warning`
- 路径不存在时 `exit 0` 静默跳过（新克隆仓库无 `scripts/dev/` 时不阻断）

### 3.3 pre-commit hook 集成

**文件**: [.pre-commit-config.yaml](../../.pre-commit-config.yaml)（新增 `ps-script-analyzer` hook）

```yaml
- id: ps-script-analyzer
  name: PowerShell 静态分析 (PSScriptAnalyzer)
  entry: pwsh -NoProfile -File scripts/dev/run_psscriptanalyzer.ps1
  language: system
  pass_filenames: false
  files: '^scripts/dev/.*\.ps1$'
  stages: [commit]
```

**触发条件**: 仅当 `scripts/dev/*.ps1` 文件变更时触发（`files` 过滤），避免无关提交运行 PowerShell。

---

## 4. 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `tests/unit/fix_broken_links.Tests.ps1` | 新增 | Pester 单元测试，14 例 |
| `scripts/dev/run_psscriptanalyzer.ps1` | 新增 | PSScriptAnalyzer 包装脚本 |
| `.pre-commit-config.yaml` | 修改 | 新增 ps-script-analyzer hook |

---

## 5. 验证结果

### 5.1 单元测试

```
pwsh -Command "Invoke-Pester tests/unit/fix_broken_links.Tests.ps1 -Output Detailed"
```

```
Tests Passed: 14, Failed: 0, Skipped: 0
```

### 5.2 静态分析

```
pwsh -File scripts/dev/run_psscriptanalyzer.ps1
```

```
[INFO] PSScriptAnalyzer v1.25.0 | 扫描: scripts/dev | Severity: Error
[PASS] 无 Error 级别问题
```

### 5.3 pre-commit 配置语法

```python
python -c "import yaml; yaml.safe_load(open('.pre-commit-config.yaml')); print('YAML OK')"
# → YAML OK
```

---

## 6. 使用指南

### 6.1 本地运行单元测试

```powershell
# 需 Pester 5.0+（pwsh 内置或 Install-Module Pester）
pwsh -Command "Invoke-Pester tests/unit/fix_broken_links.Tests.ps1 -Output Detailed"
```

### 6.2 本地运行静态分析

```powershell
# 首次运行自动安装 PSScriptAnalyzer
pwsh -File scripts/dev/run_psscriptanalyzer.ps1

# 严格模式（拦截 Warning）
pwsh -File scripts/dev/run_psscriptanalyzer.ps1 -Severity Warning
```

### 6.3 pre-commit 自动触发

```bash
pre-commit install   # 安装 hook（一次性）
git commit           # 修改 scripts/dev/*.ps1 后自动触发 ps-script-analyzer
```

---

## 7. 后续建议

| 优先级 | 建议 | 说明 |
|--------|------|------|
| P1 | CI 增加 Pester 测试 job | 在 `.github/workflows/ci.yml` 增加 pwsh job 运行 `fix_broken_links.Tests.ps1`，PR 时自动验证 |
| P2 | 扩大 PSScriptAnalyzer 覆盖范围 | 将 `files` 过滤从 `scripts/dev/` 扩展到 `scripts/` 全目录 |
| P2 | 统一 Pester 版本 | 现有 AdminDependencyChecker 用 4.10.1，新测试用 5+；建议 CI 同时安装两版本或统一迁移 |
| P3 | 增加 PSScriptAnalyzer 规则配置 | 新增 `PSScriptAnalyzerSettings.psd1` 自定义规则白名单（如允许中文注释） |

---

## 8. 经验教训

1. **PowerShell 编码是高频陷阱**：PS 5.x 默认 GBK 与 UTF-8 无 BOM 文件不兼容，中文路径处理必须显式 `-Encoding UTF8`
2. **正则字符类默认跨行**：`[^]` 字符类匹配换行符，处理多行文本时必须显式排除 `\r\n`
3. **单/双引号语义差异**：PowerShell 单引号无转义，`` `n `` 只在双引号中是换行符
4. **dot-source 副作用**：测试脚本时 dot-source 会执行主流程，需 Mock 拦截外部调用
5. **Edit 工具 BOM 叠加**：编辑含 BOM 的 ps1 文件时 BOM 会叠加，需定期检查（PSScriptAnalyzer 可拦截多 BOM 导致的语法错误）
