# CI 安全扫描 Wiki

本文档是云枢项目 CI 安全扫描的团队内部技术 Wiki, 涵盖 gitleaks 硬编码密码扫描工作流的使用方法、配置说明、故障排查与 FAQ。

> **新增背景**: v1.3.1 引入 gitleaks 全分支扫描 workflow, 防止 P1 硬编码密码问题复发。本文档随修复链路一并落地, 作为团队知识沉淀。

---

## 目录

1. [概述](#概述)
2. [工作流触发条件](#工作流触发条件)
3. [gitleaks 配置说明](#gitleaks-配置说明)
4. [本地验证](#本地验证)
5. [故障排查](#故障排查)
6. [添加新规则](#添加新规则)
7. [添加白名单路径](#添加白名单路径)
8. [新仓库复用](#新仓库复用)
9. [常见问题](#常见问题)
10. [关联文档](#关联文档)

---

## 概述

### 工作流文件

- `.github/workflows/hardcoded-password-scan.yml` — GitHub Actions 工作流
- `.github/gitleaks-config.toml` — gitleaks 规则与白名单配置

### 设计目标

1. **P1 防复发**: 防止硬编码密码进入仓库（参考 P1 修复 commit `9d51c406`）
2. **全分支覆盖**: 在所有分支（除 gh-pages）触发, 包括 feature 分支
3. **轻量独立**: 独立 workflow, 不在 feature 分支触发完整安全套件, 减少 CI 负担
4. **路径过滤**: 仅在修改监控/安全相关文件时触发, 避免无效运行

### 与 p0-security.yml 的区别

| 维度 | hardcoded-password-scan.yml | p0-security.yml |
|------|----------------------------|------------------|
| 触发分支 | 所有分支（除 gh-pages） | main/develop/phase2/release |
| Job 数量 | 1（仅 gitleaks） | 6（完整安全套件） |
| 触发条件 | paths 过滤（监控/安全/代码文件） | paths 过滤（敏感数据模块） |
| 适用场景 | 日常开发防密码进入 | 关键分支深度安全验证 |

---

## 工作流触发条件

### 触发事件

| 事件 | 触发条件 |
|------|---------|
| `push` | 除 gh-pages 外所有分支, paths 过滤匹配 |
| `pull_request` | 所有 PR, paths 过滤匹配 |
| `schedule` | 每天 04:00 (与 p0-security.yml 的 03:00 错开) |
| `workflow_dispatch` | 手动触发, 支持 `scan_all_files` 参数 |

### paths 过滤规则

**触发路径**（任一匹配即触发）:
- 监控相关: `scripts/_import_dashboards.py`, `docker-compose.monitoring*.yml`, `docker/glitchtip/**`
- 配置文件: `.env.example`, `.github/gitleaks-config.toml`
- 通用代码: `**/*.py`, `**/*.yml`, `**/*.yaml`, `**/*.sh`, `**/*.ps1`

**排除路径**（用 `!` 前缀, GitHub Actions 推荐）:
- `!docs/**`, `!**/*.md` — 文档不触发
- `!LICENSE`, `!.gitignore` — 元文件不触发

> ⚠️ **重要**: GitHub Actions 禁止在同一事件中混用 `paths` + `paths-ignore`, 必须用 `!` 模式嵌入 `paths` 列表。

---

## gitleaks 配置说明

### 规则清单

共 7 条自定义规则, 全部标记 `p1` tag:

| # | id | 描述 | 关键字 |
|---|-----|------|--------|
| 1 | `hardcoded-password-assignment` | 硬编码密码赋值（`password = 'xxx'`） | password/passwd/pwd |
| 2 | `grafana-admin-password` | Grafana admin 密码硬编码 | GF_SECURITY_ADMIN_PASSWORD |
| 3 | `glitchtip-password` | GlitchTip admin 密码硬编码 | GLITCHTIP_ADMIN_PASSWORD |
| 4 | `postgres-password` | PostgreSQL 密码硬编码 | POSTGRES_PASSWORD |
| 5 | `django-secret-key` | Django SECRET_KEY 硬编码 | SECRET_KEY |
| 6 | `http-basic-auth-password` | HTTP Basic Auth 密码硬编码 | auth= |
| 7 | `common-weak-password` | 常见弱密码（admin123 等） | admin123/password123/12345678 |

### 白名单（allowlist）

#### paths 排除（13 条）

| 类别 | 路径模式 | 说明 |
|------|---------|------|
| 示例文件 | `\.env\.example$`, `\.env\.backup` | 示例配置允许占位符 |
| 文档 | `docs/.*\.md$`, `README.*\.md$` | 文档允许示例密码 |
| 配置自身 | `\.github/gitleaks-config\.toml$` | 配置文件本身不扫描 |
| 测试代码 | `tests/.*\.py$` | 测试文件中的 mock 密码 |
| 虚拟环境 | `venv/.*`, `\.venv/.*` | 第三方依赖不扫描 |
| 测试日志 | `test_reports/.*` | 历史测试运行日志 |
| 通用日志 | `\.log$` | 任意 .log 文件 |
| 测试脱敏器 | `scripts/quick_test\.py$` | 测试脱敏器输入数据 |
| HTML 报告 | `scripts/test_report_pdf\.html$` | HTML 测试报告展示 |
| BFG 脚本 | `scripts/bfg_force_push\.ps1$` | 注释中的示例字符串 |

#### regexes 白名单（12 条）

- Python 环境变量读取: `os.environ.get(...)`, `os.getenv(...)`
- Docker 变量插值: `${VAR_PASSWORD:-default}`, `${VAR_USER:-default}`
- 占位符: `CHANGE_ME`, `YOUR_PASSWORD_HERE`, `<your-password>`, `<your-secret>`, ` Placeholder `
- 测试域名: `example.com`, `local.test`
- 注释说明: `# ...密码...环境变量`, `# ...password...env`

---

## 本地验证

### 安装 gitleaks v8.18.1

**Linux**:
```bash
wget https://github.com/gitleaks/gitleaks/releases/download/v8.18.1/gitleaks_8.18.1_linux_x64.tar.gz
tar -xzf gitleaks_8.18.1_linux_x64.tar.gz
sudo mv gitleaks /usr/local/bin/
```

**Windows**:
```powershell
# 下载 gitleaks_8.18.1_windows_x64.zip
# 解压后放入 PATH
```

**macOS**:
```bash
brew install gitleaks
```

### 本地扫描命令

```bash
# 与 CI 完全一致的扫描方式
gitleaks detect \
  --config .github/gitleaks-config.toml \
  --source . \
  --no-git \
  --report-format json \
  --report-path scan-reports/gitleaks-report.json \
  --verbose

# 查看退出码
echo "Exit code: $?"
# 0 = 无密码, 1 = 发现密码, 其他 = 配置错误
```

### 预期结果

| 退出码 | 含义 | 输出 |
|--------|------|------|
| 0 | 无硬编码密码 | `✅ 未发现硬编码密码` |
| 1 | 发现硬编码密码 | `❌ 发现硬编码密码: N 处` + 详情 |
| 2 | 配置错误（panic） | gitleaks 报错, 需检查 TOML 语法与正则兼容性 |

---

## 故障排查

### 问题 1: workflow 0s 失败, 无 jobs 执行

**症状**: `gh run view` 显示 "This run likely failed because of a workflow file issue"

**根因**: YAML 在加载阶段失败, 常见原因:
- `paths` + `paths-ignore` 混用（GitHub Actions 禁止）
- YAML 语法错误（中文标点 / 缩进）
- action 版本已废弃

**排查**:
```bash
# 检查 workflow 是否被 GitHub 识别
gh api repos/<owner>/<repo>/actions/workflows --jq '.workflows[] | {name, state}'

# 如果 name 显示为文件路径而非 YAML 中的 name, 说明 schema 校验失败
```

**修复**: 参考 commit `192c4a70`, 移除 `paths-ignore`, 改用 `!` 模式。

### 问题 2: gitleaks panic: `invalid or unsupported Perl syntax`

**症状**: gitleaks 报错 `panic: regexp: Compile(...): error parsing regexp`

**根因**: gitleaks v8 使用 Go RE2 正则引擎, 不支持 Perl 风格的 lookahead `(?!...)` / lookbehind `(?<!...)`。

**修复**: 用 gitleaks 原生 `[rules.allowlist]` 替代 lookahead。参考 commit `ab608151`:

```toml
# 错误（RE2 不支持）
regex = '''SECRET_KEY\s*[:=]\s*['"](?!dev-secret-key)[^'"\s]{20,}['"]'''

# 正确（用 allowlist 排除占位符）
[[rules]]
id = "django-secret-key"
regex = '''SECRET_KEY\s*[:=]\s*['"][^'"\s]{20,}['"]'''

  [rules.allowlist]
  regexes = [
    '''dev-secret-key''',
  ]
```

### 问题 3: 扫描发现大量误报

**症状**: 报告中存在 venv / test_reports / 日志文件的告警

**根因**: allowlist.paths 未覆盖项目辅助目录

**修复**: 在 `[allowlist].paths` 添加路径模式。参考 commit `dea18ba9`:

```toml
[allowlist]
paths = [
    # ...
    '''venv/.*''',
    '''test_reports/.*''',
    '''\.log$''',
]
```

### 问题 4: 真实硬编码密码修复

**症状**: 扫描发现真实密码（如 `Yunshu@P1Verify2026!`）

**修复方案**: 改用环境变量读取, **不要**用 allowlist 掩盖。参考 commit `18795a2a`:

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

### 问题 5: 错误提示文本触发误报

**症状**: 自己编写的错误提示文本被识别为硬编码密码

**根因**: 提示文本中包含 `PASSWORD = 'xxx'` 模式

**修复**: 简化提示, 避免示例语法。参考 commit `32bd26db`:

```powershell
# 错误（示例语法触发规则）
Write-Host "  请设置: \$env:GRAFANA_OLD_PASSWORD = '<旧密码>'"

# 正确（简化提示）
Write-Host "  请在执行前设置环境变量 GRAFANA_OLD_PASSWORD 后重试"
```

---

## 添加新规则

### 步骤

1. 在 `.github/gitleaks-config.toml` 添加 `[[rules]]` 块
2. 本地用 `gitleaks detect --config ... --dry-run` 验证语法
3. 提交并推送, CI 自动触发

### 示例

```toml
# 添加 Redis 密码规则
[[rules]]
id = "redis-password"
description = "Redis 密码硬编码"
regex = '''REDIS_PASSWORD\s*[:=]\s*['"][^'"\s]{4,}['"]'''
tags = ["redis", "password", "p1"]
keywords = ["REDIS_PASSWORD"]
```

### 注意事项

- ⚠️ **禁用 Perl lookahead**: 不要使用 `(?!...)` / `(?<=...)`, Go RE2 不支持
- ✅ **必填字段**: `id`, `description`, `regex`, `keywords`
- ✅ **tag 约定**: 至少包含 `p1` 标签便于过滤
- ✅ **keyword 优化**: `keywords` 数组加速扫描, 只在含关键字的行匹配正则

---

## 添加白名单路径

### 步骤

1. 评估是否真的需要白名单（确认是误报而非真实密码）
2. 在 `[allowlist].paths` 添加路径模式
3. 本地验证: `gitleaks detect --config ... --source .`
4. 提交并推送

### 示例

```toml
[allowlist]
paths = [
    # ...
    '''scripts/my_test_script\.py$''',  # 测试脚本包含测试密码
]
```

### 决策原则

| 情况 | 处理方式 |
|------|---------|
| 真实硬编码密码 | **修复代码**, 改用环境变量, 不允许加入白名单 |
| 测试代码中的测试密码 | 加入 allowlist.paths |
| 第三方依赖中的密码 | 加入 allowlist.paths（venv/ 已默认排除） |
| 注释中的示例字符串 | 加入 allowlist.paths 或 allowlist.regexes |
| 文档中的示例 | 已默认排除（docs/*.md, README*.md） |

---

## 新仓库复用

### 步骤

1. **复制配置文件**:
   ```bash
   cp .github/workflows/hardcoded-password-scan.yml <new-repo>/.github/workflows/
   cp .github/gitleaks-config.toml <new-repo>/.github/
   ```

2. **调整 paths 过滤**: 检查 `on.push.paths` 中的项目特定路径（如 `scripts/_import_dashboards.py`）, 按需调整或删除

3. **调整 allowlist**: 检查 `[allowlist].paths` 中的项目特定路径（如 `venv/.*`, `test_reports/.*`）, 按需调整

4. **本地验证**:
   ```bash
   cd <new-repo>
   gitleaks detect --config .github/gitleaks-config.toml --source . --no-git --verbose
   ```

5. **推送触发 CI**: push 后 GitHub Actions 自动触发

### 完整配置参考

详见归档导出: [../ci/2026-07-26-gitleaks-ci-workflow-export.md](../ci/2026-07-26-gitleaks-ci-workflow-export.md)

---

## 常见问题

### Q1: workflow 没有触发？

**A**: 检查 paths 过滤。如果 push 只修改了 `docs/*.md` 或 `LICENSE`, 不会触发（已被 `!` 排除）。可用 `workflow_dispatch` 手动触发:

```bash
gh workflow run hardcoded-password-scan.yml --ref <branch> -f scan_all_files=false
```

### Q2: 扫描报告在哪里？

**A**: 在 CI run 详情页右侧 "Artifacts" 区域, 下载 `gitleaks-scan-report-<branch>` artifact。保留期 30 天。

### Q3: 如何跳过某次 CI 扫描？

**A**: 在 commit message 中添加 `[skip ci]` 或 `[ci skip]`:

```
docs: 更新 README [skip ci]
```

> ⚠️ 不建议日常使用, 仅限文档/配置类提交

### Q4: gitleaks 报告了真实密码但我不想立即修复？

**A**: **不允许**。P1 硬编码密码必须立即修复, 不能用 allowlist 掩盖。临时方案:
1. 立即将密码改为环境变量读取
2. 在 `.env` 文件中存放真实值（`.env` 已被 `.gitignore` 排除）
3. 在 CI secrets 中配置生产环境密码

### Q5: 如何在 pre-commit 阶段拦截硬编码密码？

**A**: 引入 gitleaks pre-commit hook:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.1
    hooks:
      - id: gitleaks
        args: [--config, .github/gitleaks-config.toml]
```

详见后续行动项 (待落地): [../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md](../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md)

### Q6: actions/* 的 Node.js 20 deprecation 警告如何处理？

**A**: 当前 actions v4 仍基于 Node.js 20, GitHub 自动强制运行在 Node.js 24, **不阻塞 CI**。建议:
- 关注 GitHub 官方 actions v5 发布
- 升级时一并 review 所有 workflow 的 action 版本

---

## 关联文档

- **配置归档**: [../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md](../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md)
- **复盘报告**: [../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md](../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md)
- **workflow 导出**: [../ci/2026-07-26-gitleaks-ci-workflow-export.md](../ci/2026-07-26-gitleaks-ci-workflow-export.md)
- **安全配置 Wiki**: [security_config_wiki.md](security_config_wiki.md)
- **P1 修复参考 commit**: `9d51c406`
- **gitleaks 官方文档**: https://github.com/gitleaks/gitleaks
- **GitHub Actions workflow syntax**: https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions
- **GitHub Actions filter pattern cheat sheet**: https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#filter-pattern-cheat-sheet

---

**最后更新**: 2026-07-26
**作者**: 安全团队
**适用版本**: 云枢智能体 v1.3.1
