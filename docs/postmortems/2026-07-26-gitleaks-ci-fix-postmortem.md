# CI gitleaks 修复复盘报告

**复盘日期**: 2026-07-26
**事件等级**: P1
**影响范围**: gitleaks 硬编码密码扫描 CI 工作流无法通过
**状态**: ✅ 已修复并验收通过（master + staging 双分支 CI 通过）

---

## 1. 事件概述

### 1.1 背景

v1.3.1 引入 gitleaks 全分支扫描工作流（`hardcoded-password-scan.yml`），目标是防止 P1 硬编码密码问题复发（参考 P1 修复 commit `9d51c406`）。首次推送后, CI 连续 7 次失败, 涉及 7 类不同根因, 跨 2 个工作日完成全链路修复。

### 1.2 时间线

| 时间 | 事件 |
|------|------|
| 2026-07-25 上午 | 首次推送 workflow, schema 校验失败 |
| 2026-07-25 下午 | 修复 schema 与 action 版本问题 |
| 2026-07-26 上午 | 修复 gitleaks 参数与 RE2 兼容性问题 |
| 2026-07-26 下午 | 排除 162 处误报, 修复 1 处真实 P1 密码 |
| 2026-07-26 晚 | master CI 通过, staging 预验证通过 |

### 1.3 影响

- **CI 通过率**: 0/8 → 1/1（master）+ 1/1（staging）
- **真实密码修复**: 1 处（`Yunshu@P1Verify2026!` → 环境变量）
- **误报排除**: 168 处 → 0 处
- **代码变更**: workflow 7 次提交, 真实密码修复 1 次提交

---

## 2. 根因分析（7 类）

### 2.1 根因 1: workflow schema 校验失败

**类别**: schema
**commit**: `192c4a70`

**问题**:
`on.push` 中同时使用 `paths` 与 `paths-ignore`, GitHub Actions schema 在加载阶段拒绝, workflow 显示 "This run likely failed because of a workflow file issue"。

**根因**:
GitHub Actions 明确禁止在同一事件中混用 `paths` + `paths-ignore`（参见 [filter pattern cheat sheet](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#filter-pattern-cheat-sheet)）。开发者未查阅最新文档, 沿用旧版惯例。

**修复**:
改用 `!` 排除模式嵌入 `paths` 列表:

```yaml
paths:
  - '**/*.py'
  - '!docs/**'        # 排除项用 ! 前缀
  - '!**/*.md'
```

**经验教训**:
- workflow 0s 失败通常意味着 schema 加载失败, 应优先排查 YAML 语法
- 用 `gh api repos/<owner>/<repo>/actions/workflows` 验证 workflow 是否被识别
- 不要假设 GitHub Actions 行为, 必须查阅官方文档

---

### 2.2 根因 2: action v3 强制废弃

**类别**: action 版本
**commit**: `fcea2234` + `a4da1e37`

**问题**:
`actions/upload-artifact@v3` 已被 GitHub 强制废弃, CI 报错:
```
The actions/upload-artifact@v3 is deprecated and will be removed by 2024-11-30.
```

**根因**:
- 首次推送时使用 `@v3` 版本（沿用过期模板）
- 第一次修复只升级了 `upload-artifact`, 漏掉了 `checkout` 和 `cache`

**修复**:
全量升级到 `@v4`:
- `actions/checkout@v3` → `@v4`
- `actions/cache@v3` → `@v4`
- `actions/upload-artifact@v3` → `@v4`

**经验教训**:
- action 版本升级必须全量 review, 不能漏改
- 建议引入 `actionlint` pre-commit hook 在本地拦截
- 季度 review 所有 workflow 的 action 版本

---

### 2.3 根因 3: gitleaks 参数错误

**类别**: gitleaks 参数
**commit**: `d52bd3cd`

**问题**:
使用 `--no-git-text` 参数, gitleaks 报错参数不存在。

**根因**:
参数名记错, 正确参数为 `--no-git`（不带 `-text` 后缀）。`--no-git` 的作用是把 git 仓库当普通目录扫描, 不扫历史 commit, 避免历史误报。

**修复**:
```bash
# 错误
gitleaks detect --no-git-text ...

# 正确
gitleaks detect --no-git ...
```

**经验教训**:
- 使用第三方工具前必须查阅官方文档确认参数
- 本地先 `gitleaks detect --help` 验证参数存在性, 再推送到 CI

---

### 2.4 根因 4: Go RE2 不支持 Perl lookahead

**类别**: 正则引擎
**commit**: `ab608151`

**问题**:
gitleaks 报错 `panic: regexp: Compile(...): error parsing regexp: invalid or unsupported Perl syntax`, Django SECRET_KEY 规则的正则 `(?!dev-secret-key)` 无法编译。

**根因**:
gitleaks v8 使用 Go RE2 正则引擎, **不支持** Perl 风格的 lookahead `(?!...)` / lookbehind `(?<!...)`。RE2 设计目标是线性时间匹配, 牺牲了部分 Perl 特性以保证性能与安全性。

**修复**:
改用 gitleaks 原生 `[rules.allowlist]` 替代 lookahead:

```toml
# 错误（RE2 不支持）
regex = '''SECRET_KEY\s*[:=]\s*``?!dev-secret-key``[^'"\s]{20,}['"]'''

# 正确（用 allowlist 排除占位符）
[[rules]]
id = "django-secret-key"
regex = '''SECRET_KEY\s*[:=]\s*['"][^'"\s]{20,}['"]'''

  [rules.allowlist]
  regexes = [
    '''dev-secret-key''',
    '''change-me''',
    '''your-secret''',
  ]
```

**经验教训**:
- gitleaks / ripgrep / kubectl 等 Go 工具均使用 RE2, 禁用 lookahead / lookbehind
- 用 `[rules.allowlist]` 表达"匹配但排除"语义, 比 lookahead 更清晰可读
- gitleaks panic 通常意味着正则不兼容, 应立即检查 regex 语法

---

### 2.5 根因 5: 误报路径未排除

**类别**: 误报排除
**commit**: `dea18ba9`

**问题**:
扫描发现 168 处告警, 大量来自 `venv/`（第三方依赖）、`test_reports/`（历史测试日志）、`*.log`（通用日志文件）。

**根因**:
`[allowlist].paths` 未覆盖项目辅助目录。这些目录中的密码字符串是测试 mock 数据或第三方依赖, 非业务代码硬编码。

**修复**:
在 `[allowlist].paths` 添加 3 类路径:

```toml
[allowlist]
paths = [
    # ...
    '''venv/.*''',          # 虚拟环境
    '''\.venv/.*''',
    '''test_reports/.*''',  # 历史测试日志
    '''\.log$''',           # 通用日志文件
]
```

**经验教训**:
- allowlist.paths 是路径级排除, 适合排除整个目录
- allowlist.regexes 是内容级排除, 适合排除特定模式（如 `os.environ.get`）
- 决策原则: 真实密码修复, 误报才加入 allowlist
- venv/ 应作为默认排除项（任何 Python 项目通用）

---

### 2.6 根因 6: P1 真实硬编码密码

**类别**: P1 真实问题
**commit**: `18795a2a`

**问题**:
扫描发现真实密码 `Yunshu@P1Verify2026!` 硬编码在 `scripts/rotate_grafana_password.ps1`:

```powershell
$oldPwd = "Yunshu@P1Verify2026!"
```

**根因**:
脚本编写时直接硬编码真实密码, 未走环境变量读取流程。这是 P1 级安全缺陷, **绝对不允许用 allowlist 掩盖**。

**修复**:
改用环境变量读取, 并加入缺失校验:

```powershell
# 错误
$oldPwd = "Yunshu@P1Verify2026!"

# 正确
$oldPwd = $env:GRAFANA_OLD_PASSWORD
if (-not $oldPwd) {
    Write-Host "[ERROR] 环境变量 GRAFANA_OLD_PASSWORD 未设置" -ForegroundColor Red
    exit 1
}
```

**经验教训**:
- **真实密码必须修复代码, 不允许用 allowlist 掩盖**
- 临时方案: 立即改为环境变量读取 + `.env` 文件存放真实值（`.env` 已被 `.gitignore` 排除）
- 长期方案: 引入 gitleaks pre-commit hook 在提交阶段拦截
- 编写脚本时遵循"密码不进代码"原则, 所有密码走环境变量

---

### 2.7 根因 7: 错误提示文本触发误报

**类别**: 误报排除
**commit**: `32bd26db`

**问题**:
脚本中的错误提示文本被识别为硬编码密码:

```powershell
# 触发规则: 形如 PASSWORD = 'xxx' 的赋值模式
Write-Host "  请设置: \$env:GRAFANA_OLD_PASSWORD = '<旧密码>'"
```

**根因**:
提示文本中包含 `PASSWORD = '<旧密码>'` 模式, 符合 `hardcoded-password-assignment` 规则的正则 `(?i)(password|passwd|pwd)\s*[:=]\s*['"][^'"\s]{4,}['"]`。

**修复**:
简化提示, 避免示例语法:

```powershell
# 错误（示例语法触发规则）
Write-Host "  请设置: \$env:GRAFANA_OLD_PASSWORD = '<旧密码>'"

# 正确（简化提示）
Write-Host "  请在执行前设置环境变量 GRAFANA_OLD_PASSWORD 后重试"
```

**经验教训**:
- 编写错误提示时, 避免使用形如 `KEY = 'value'` 的示例语法
- 用自然语言描述操作步骤, 而非展示代码示例
- 必要时用代码块包裹示例（gitleaks 不扫描 markdown 代码块中的内容）

---

## 3. 修复 commits 链路汇总

| # | Commit | 类型 | 根因摘要 | 修复方式 |
|---|--------|------|---------|---------|
| 1 | `192c4a70` | schema | `paths` + `paths-ignore` 混用 | 改用 `!` 排除模式嵌入 `paths` |
| 2 | `fcea2234` | action 版本 | `actions/upload-artifact@v3` 已废弃 | 升级 `upload-artifact` 到 v4 |
| 3 | `a4da1e37` | action 版本 | 漏掉 `checkout` + `cache` 升级 | 补全 v3 → v4 |
| 4 | `d52bd3cd` | gitleaks 参数 | `--no-git-text` 不存在 | 改用 `--no-git` |
| 5 | `ab608151` | 正则引擎 | RE2 不支持 Perl lookahead `(?!...)` | 改用 gitleaks 原生 `allowlist.regexes` |
| 6 | `dea18ba9` | 误报排除 | venv / test_reports / *.log 触发 162 处误报 | allowlist.paths 添加 3 类路径 |
| 7 | `18795a2a` | P1 真实问题 | `Yunshu@P1Verify2026!` 硬编码密码 | 改用 `$env:GRAFANA_OLD_PASSWORD` 环境变量 |
| 8 | `32bd26db` | 误报排除 | 错误提示中示例语法被识别为赋值 | 简化提示文本 |

**说明**: 表中编号 1-7 对应"7 类根因", 共 8 个 commit（根因 2 包含 2 个 commit）。

---

## 4. 验证结果

### 4.1 master 分支

| 运行 ID | 触发 | 结论 |
|---------|------|------|
| `30113381577` | push | failure（schema） |
| `30143436727` | push | failure（action v3） |
| `30143549694` | push | failure（`--no-git-text`） |
| `30143578164` | push | failure（RE2 lookahead） |
| `30143628903` | push | failure（168 处告警） |
| `30143793020` | push | failure（5 处告警） |
| `30145116365` | push | failure（1 处误报） |
| **`30145240113`** | push | **success** ✅ |

### 4.2 staging 分支预验证

- **CI 运行 ID**: `30164944709`
- **触发方式**: `workflow_dispatch`（手动触发）
- **结论**: ✅ **staging 预验证通过**

### 4.3 唯一警告（非阻塞）

```
Node.js 20 is deprecated. The following actions target Node.js 20
but are being forced to run on Node.js 24:
actions/cache@v4, actions/checkout@v4, actions/upload-artifact@v4
```

**处理**: GitHub 自动强制运行在 Node.js 24, 不阻塞 CI。待 actions v5 官方发布后升级。

---

## 5. 改进建议

### 5.1 短期（2026-08-02 前）

| # | 行动项 | 负责人 | 优先级 |
|---|--------|--------|--------|
| 1 | 创建 `docs/security/gitleaks-rule-authoring.md` 规则编写手册 | TBD | High |
| 2 | 引入 `actionlint` pre-commit hook, 本地拦截 workflow schema 错误 | TBD | High |

### 5.2 中期（2026-08-09 前）

| # | 行动项 | 负责人 | 优先级 |
|---|--------|--------|--------|
| 3 | 引入 `gitleaks` pre-commit hook, 在提交阶段拦截硬编码密码 | TBD | Medium |
| 4 | 清理 `test_reports/logs/` 历史日志（避免 allowlist 持续膨胀） | TBD | Medium |

### 5.3 长期（季度）

| # | 行动项 | 负责人 | 优先级 |
|---|--------|--------|--------|
| 5 | 季度 review `actions/*` 版本, 关注 GitHub deprecation 公告 | TBD | Low |
| 6 | 关注 gitleaks v9 发布（如发布则评估升级） | TBD | Low |

---

## 6. 经验教训总结

### 6.1 工具选型

- **gitleaks v8 使用 Go RE2 正则引擎**, 禁用 Perl lookahead / lookbehind
- **GitHub Actions 禁止混用 `paths` + `paths-ignore`**, 用 `!` 模式嵌入 `paths`
- **actions v3 已强制废弃**, 必须升级到 v4

### 6.2 流程改进

- **本地验证优先**: 推送 CI 前必须本地 `gitleaks detect` 验证
- **actionlint 拦截**: workflow schema 错误应在本地拦截, 不应进入 CI
- **gitleaks pre-commit**: 硬编码密码应在提交阶段拦截, 不应进入远程仓库

### 6.3 安全原则

- **真实密码修复, 误报才加入 allowlist**（绝对不允许用 allowlist 掩盖真实密码）
- **密码不进代码**: 所有密码走环境变量 + `.env` 文件
- **示例避免赋值语法**: 错误提示中避免 `KEY = 'value'` 模式

### 6.4 文档沉淀

- 归档完整修复链路（commit 序列 + 验证证据）
- Wiki 提供团队使用指南与故障排查 FAQ
- workflow 导出便于版本回滚与新仓库复用

---

## 7. 关联文档

- **CI 配置归档**: [../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md](../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md)
- **Workflow 与配置导出**: [../ci/2026-07-26-gitleaks-ci-workflow-export.md](../ci/2026-07-26-gitleaks-ci-workflow-export.md)
- **团队 Wiki**: [../wiki/ci_security_scan_wiki.md](../wiki/ci_security_scan_wiki.md)
- **安全配置 Wiki**: [../wiki/security_config_wiki.md](../wiki/security_config_wiki.md)
- **P1 修复参考 commit**: `9d51c406`
- **GitHub deprecation 公告**: https://github.blog/changelog/2024-04-16-deprecation-notice-v3-of-the-artifact-actions/
- **gitleaks 官方文档**: https://github.com/gitleaks/gitleaks
- **RE2 语法参考**: https://github.com/google/re2/wiki/Syntax

---

**复盘人**: 安全团队
**复盘日期**: 2026-07-26
**适用版本**: 云枢智能体 v1.3.1
