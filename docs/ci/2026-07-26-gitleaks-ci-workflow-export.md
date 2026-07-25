# CI gitleaks Workflow 与配置导出

**导出日期**: 2026-07-26
**版本**: v1.3.1
**来源分支**: master (HEAD: `32bd26db`)
**用途**: 完整保留 gitleaks 安全扫描工作流与配置的可执行版本, 便于版本回滚查阅与新仓库复用

---

## 1. 导出概述

本文件导出 master 分支上 gitleaks 硬编码密码扫描 CI 的完整配置, 包括：

| 文件 | 路径 | 行数 | 说明 |
|------|------|------|------|
| workflow | `.github/workflows/hardcoded-password-scan.yml` | 168 | GitHub Actions 工作流（全分支触发） |
| gitleaks config | `.github/gitleaks-config.toml` | 122 | gitleaks 规则与白名单配置 |

> **复用提示**: 将这两个文件复制到新仓库后, 仅需按需调整 `paths` 过滤中的项目特定路径（如 `scripts/_import_dashboards.py`）。

---

## 2. Workflow 全文

**文件**: `.github/workflows/hardcoded-password-scan.yml`

```yaml
# 硬编码密码扫描 CI 工作流（全分支触发）
#
# 目的：在所有分支上触发 gitleaks 硬编码密码扫描，防止 P1 问题复发
#
# 与 p0-security.yml 的区别：
# - p0-security.yml: 完整安全验证套件（6 Job），仅在 main/develop/phase2/release 分支触发
# - 本工作流: 仅 gitleaks 扫描，在所有分支触发（含 feature 分支）
#
# 设计理由：
# - feature 分支也需要密码扫描，避免硬编码密码进入 PR
# - 独立 workflow 避免在 feature 分支触发完整安全套件（减少 CI 负担）
# - 修改监控/安全相关文件时才触发（paths 过滤）

name: 硬编码密码扫描（全分支）

on:
  push:
    # 【v1.3.0 改进】所有分支触发，不再限制 main/develop/phase2/release
    # 【v1.3.1 修复】GitHub Actions 禁止在同一事件混用 paths + paths-ignore
    # 改用 ! 排除模式嵌入 paths（参见 workflow-syntax-for-github-actions#filter-pattern-cheat-sheet）
    branches-ignore:
      - 'gh-pages'    # 仅排除文档页面分支
    paths:
      # 监控相关文件（P1 修复范围）
      - 'scripts/_import_dashboards.py'
      - 'docker-compose.monitoring*.yml'
      - 'docker/glitchtip/**'
      # 配置文件
      - '.env.example'
      - '.github/gitleaks-config.toml'
      # 通用代码（Python/YAML/Shell 中的密码模式）
      - '**/*.py'
      - '**/*.yml'
      - '**/*.yaml'
      - '**/*.sh'
      - '**/*.ps1'
      # 排除项（原 paths-ignore 内容，用 ! 模式替代）
      - '!docs/**'
      - '!**/*.md'
      - '!LICENSE'
      - '!.gitignore'

  pull_request:
    # 所有 PR 都触发（确保 PR 不引入硬编码密码）
    paths:
      - 'scripts/_import_dashboards.py'
      - 'docker-compose.monitoring*.yml'
      - 'docker/glitchtip/**'
      - '.env.example'
      - '.github/gitleaks-config.toml'
      - '**/*.py'
      - '**/*.yml'
      - '**/*.yaml'
      - '**/*.sh'
      - '**/*.ps1'
      # 排除项
      - '!docs/**'
      - '!**/*.md'

  # 定时扫描（每天凌晨 4 点，与 p0-security.yml 的 3 点错开）
  schedule:
    - cron: '0 4 * * *'

  # 手动触发
  workflow_dispatch:
    inputs:
      scan_all_files:
        description: '扫描全部文件（忽略 paths 过滤）'
        required: false
        default: 'false'

env:
  GITLEAKS_VERSION: '8.18.1'

jobs:
  gitleaks-scan:
    name: Gitleaks 硬编码密码扫描
    runs-on: ubuntu-22.04
    timeout-minutes: 10
    steps:
      - name: 检出代码（完整历史）
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: 缓存 gitleaks 二进制
        uses: actions/cache@v4
        with:
          path: /usr/local/bin/gitleaks
          key: gitleaks-${{ env.GITLEAKS_VERSION }}-linux-x64

      - name: 安装 gitleaks
        run: |
          if ! command -v gitleaks &>/dev/null; then
            echo "=== 安装 gitleaks v${GITLEAKS_VERSION} ==="
            wget -q "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" -O /tmp/gitleaks.tar.gz
            tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks
            chmod +x /usr/local/bin/gitleaks
          fi
          gitleaks version

      - name: 运行 gitleaks 扫描
        run: |
          echo "=== 硬编码密码扫描（全分支）==="
          echo "分支: ${{ github.ref_name }}"
          echo "触发事件: ${{ github.event_name }}"
          echo "扫描配置: .github/gitleaks-config.toml"
          echo ""

          mkdir -p scan-reports

          # 扫描当前代码（--no-git: 把 git 仓库当普通目录, 不扫历史 commit, 避免历史误报）
          gitleaks detect \
            --config .github/gitleaks-config.toml \
            --source . \
            --no-git \
            --report-format json \
            --report-path scan-reports/gitleaks-report.json \
            --verbose
          SCAN_EXIT=$?

          echo ""
          echo "=== 扫描结果 ==="

          if [ -f scan-reports/gitleaks-report.json ] && [ -s scan-reports/gitleaks-report.json ]; then
            FINDINGS=$(python -c "import json; data=json.load(open('scan-reports/gitleaks-report.json')); print(len(data))" 2>/dev/null || echo "0")
            echo "❌ 发现硬编码密码: ${FINDINGS} 处"
            echo ""
            echo "--- 详情 ---"
            cat scan-reports/gitleaks-report.json | python -m json.tool 2>/dev/null || cat scan-reports/gitleaks-report.json
            echo ""
            echo "--- 修复指南 ---"
            echo "  1. 将硬编码密码改为 os.environ.get('VAR_NAME') 读取"
            echo "  2. Docker Compose 使用 \${VAR:-default} 变量插值"
            echo "  3. 密码值放入 .env 文件（已被 .gitignore 排除）"
            echo "  4. 参考修复: commit 9d51c406 (P1 硬编码密码移除)"
            echo "  5. 详细规则见: .github/gitleaks-config.toml"
          else
            echo "✅ 未发现硬编码密码"
          fi

          if [ $SCAN_EXIT -ne 0 ]; then
            echo ""
            echo "❌ 扫描未通过！请修复硬编码密码后重试。"
            exit 1
          else
            echo "✅ 扫描通过"
          fi

      - name: 上传扫描报告
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: gitleaks-scan-report-${{ github.ref_name }}
          path: scan-reports/gitleaks-report.json
          retention-days: 30

      - name: PR 评论（仅 PR 时）
        if: github.event_name == 'pull_request' && failure()
        uses: actions/github-script@v7
        with:
          script: |
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: `## ❌ 硬编码密码扫描未通过\n\nGitleaks 检测到硬编码密码，请修复后重新提交。\n\n**修复指南**：\n1. 将密码改为 \`os.environ.get('VAR')\` 读取\n2. Docker Compose 使用 \`\${VAR:-default}\` 变量插值\n3. 密码放入 \`.env\`（已被 .gitignore 排除）\n4. 参考: commit 9d51c406 (P1 修复)\n\n**详细报告**：见 Artifact \`gitleaks-scan-report-${{ github.ref_name }}\`\n\n**规则配置**：\`.github/gitleaks-config.toml\``
            })
```

---

## 3. gitleaks 配置全文

**文件**: `.github/gitleaks-config.toml`

```toml
# Gitleaks 配置 - 硬编码密码扫描
#
# 目的：防止 P1 硬编码密码问题复发
# 扫描范围：Python/YAML/Docker Compose/Shell 脚本
# 白名单：环境变量引用、占位符、.env.example、文档
#
# 使用方式：
#   本地: gitleaks detect --config .github/gitleaks-config.toml --source .
#   CI: 通过 p0-security.yml 的 hardcoded-password-scan Job 自动运行

title = "Yunshu Project - 硬编码密码扫描配置（P1 防复发）"

[allowlist]
description = "允许的密码占位符和环境变量引用（白名单）"
paths = [
    # 示例文件允许占位符
    '''\.env\.example$''',
    '''\.env\.backup''',
    # 文档允许示例密码
    '''docs/.*\.md$''',
    '''README.*\.md$''',
    # gitleaks 自身配置
    '''\.github/gitleaks-config\.toml$''',
    # 测试文件中的 mock 密码
    '''tests/.*\.py$''',
    # v1.3.1 新增: 非业务代码路径排除（避免误报）
    # 虚拟环境（第三方依赖, 非项目代码）
    '''venv/.*''',
    '''\.venv/.*''',
    # 历史测试日志（仅记录运行结果, 非源代码）
    '''test_reports/.*''',
    # 通用日志文件
    '''\.log$''',
    # v1.3.1: 测试脱敏器的测试代码与测试报告（包含原始密码字符串用于验证）
    '''scripts/quick_test\.py$''',
    '''scripts/test_report_pdf\.html$''',
    # BFG 清理脚本: 注释中的示例字符串（非真实密码）
    '''scripts/bfg_force_push\.ps1$''',
]
regexes = [
    # Python 环境变量读取（os.environ.get / os.getenv）
    '''os\.environ\.get\s*\(''',
    '''os\.getenv\s*\(''',
    # Docker Compose 变量插值 ${VAR:-default}
    '''\$\{[A-Z_]+_PASSWORD(:-[^}]*)?\}''',
    '''\$\{[A-Z_]+_USER(:-[^}]*)?\}''',
    # 占位符
    '''CHANGE_ME''',
    '''YOUR_PASSWORD_HERE''',
    '''<your-password>''',
    '''<your-secret>''',
    ''' Placeholder ''',
    '''example\.com''',
    '''local\.test''',
    # 注释中的说明
    '''#\s*.*密码.*环境变量''',
    '''#\s*.*password.*env''',
]

# ── 规则 1: 硬编码密码赋值 ──────────────────────────────────────
[[rules]]
id = "hardcoded-password-assignment"
description = "硬编码密码赋值（password = 'xxx'）"
regex = '''(?i)(password|passwd|pwd)\s*[:=]\s*['"][^'"\s]{4,}['"]'''
tags = ["password", "hardcoded", "p1"]
keywords = ["password", "passwd", "pwd"]

# ── 规则 2: Grafana admin 密码硬编码 ───────────────────────────
[[rules]]
id = "grafana-admin-password"
description = "Grafana admin 密码硬编码（GF_SECURITY_ADMIN_PASSWORD）"
regex = '''GF_SECURITY_ADMIN_PASSWORD\s*[:=]\s*['"][^'"\s]{4,}['"]'''
tags = ["grafana", "password", "p1"]
keywords = ["GF_SECURITY_ADMIN_PASSWORD"]

# ── 规则 3: GlitchTip 密码硬编码 ────────────────────────────────
[[rules]]
id = "glitchtip-password"
description = "GlitchTip admin 密码硬编码"
regex = '''GLITCHTIP_ADMIN_PASSWORD\s*[:=]\s*['"][^'"\s]{4,}['"]'''
tags = ["glitchtip", "password", "p1"]
keywords = ["GLITCHTIP_ADMIN_PASSWORD"]

# ── 规则 4: PostgreSQL 密码硬编码 ───────────────────────────────
[[rules]]
id = "postgres-password"
description = "PostgreSQL 密码硬编码"
regex = '''POSTGRES_PASSWORD\s*[:=]\s*['"][^'"\s]{4,}['"]'''
tags = ["postgres", "password", "p1"]
keywords = ["POSTGRES_PASSWORD"]

# ── 规则 5: Django SECRET_KEY 硬编码 ────────────────────────────
# 注: 原 regex 使用 (?!...) Perl lookahead, Go RE2 不支持, 改用 allowlist 排除占位符
[[rules]]
id = "django-secret-key"
description = "Django SECRET_KEY 硬编码（非 dev-secret-key 占位符）"
regex = '''SECRET_KEY\s*[:=]\s*['"][^'"\s]{20,}['"]'''
tags = ["django", "secret", "p1"]
keywords = ["SECRET_KEY"]

  [rules.allowlist]
  regexes = [
    '''dev-secret-key''',
    '''change-me''',
    '''your-secret''',
  ]

# ── 规则 6: HTTP Basic Auth 密码 ────────────────────────────────
[[rules]]
id = "http-basic-auth-password"
description = "HTTP Basic Auth 密码硬编码（auth=(user, 'password')）"
regex = '''auth\s*=\s*\([^)]*['"][^'"\s]{4,}['"]'''
tags = ["http", "auth", "password", "p1"]
keywords = ["auth="]

# ── 规则 7: admin/admin123 常见弱密码 ───────────────────────────
[[rules]]
id = "common-weak-password"
description = "常见弱密码（admin123, password123, 12345678 等）"
regex = '''(?i)(password|admin)\s*[:=]\s*['"](admin123|password123|12345678|qwerty|letmein)['"]'''
tags = ["weak", "password", "p1"]
keywords = ["admin123", "password123", "12345678"]
```

---

## 4. Workflow 字段表

### 4.1 触发事件字段

| 字段 | 值 | 说明 |
|------|-----|------|
| `on.push.branches-ignore` | `['gh-pages']` | 仅排除文档页面分支, 其余分支全部触发 |
| `on.push.paths` | 见 workflow 全文 | 监控/安全/代码文件触发, 用 `!` 嵌入排除项 |
| `on.pull_request.paths` | 见 workflow 全文 | PR 触发过滤（与 push 一致, 移除 LICENSE/.gitignore 排除） |
| `on.schedule.cron` | `'0 4 * * *'` | 每天 04:00 UTC 定时扫描 |
| `on.workflow_dispatch.inputs.scan_all_files` | `'false'`（默认） | 手动触发时可选参数 |

### 4.2 Job 字段

| 字段 | 值 | 说明 |
|------|-----|------|
| `jobs.gitleaks-scan.runs-on` | `ubuntu-22.04` | 运行环境 |
| `jobs.gitleaks-scan.timeout-minutes` | `10` | 超时保护 |
| `env.GITLEAKS_VERSION` | `'8.18.1'` | gitleaks 版本（与缓存 key 绑定） |

### 4.3 Step 字段

| 步骤 | action / 命令 | 关键参数 |
|------|---------------|---------|
| 检出代码 | `actions/checkout@v4` | `fetch-depth: 0`（完整历史） |
| 缓存 gitleaks | `actions/cache@v4` | `key: gitleaks-${GITLEAKS_VERSION}-linux-x64` |
| 安装 gitleaks | `wget + tar` | 仅在缺失时安装 |
| 运行扫描 | `gitleaks detect` | `--no-git --source . --report-format json` |
| 上传报告 | `actions/upload-artifact@v4` | `retention-days: 30`, `if: always()` |
| PR 评论 | `actions/github-script@v7` | `if: pull_request && failure()` |

---

## 5. gitleaks 配置字段表

### 5.1 `[allowlist]` 字段

| 字段 | 数量 | 说明 |
|------|------|------|
| `paths` | 13 条 | 路径白名单（正则匹配） |
| `regexes` | 12 条 | 内容白名单（正则匹配） |

### 5.2 `[[rules]]` 字段

每条规则必填字段:

| 字段 | 说明 | 示例 |
|------|------|------|
| `id` | 规则唯一标识 | `grafana-admin-password` |
| `description` | 规则描述 | `Grafana admin 密码硬编码` |
| `regex` | 正则表达式（Go RE2 语法） | `GF_SECURITY_ADMIN_PASSWORD\s*[:=]...` |
| `tags` | 标签数组 | `["grafana", "password", "p1"]` |
| `keywords` | 关键字数组（加速扫描） | `["GF_SECURITY_ADMIN_PASSWORD"]` |

可选字段:

| 字段 | 说明 |
|------|------|
| `[rules.allowlist]` | 规则级白名单（覆盖全局 allowlist） |
| `[rules.allowlist].regexes` | 规则级内容白名单 |

### 5.3 规则清单（7 条）

| # | id | 关键字 | 用途 |
|---|-----|--------|------|
| 1 | `hardcoded-password-assignment` | password/passwd/pwd | 通用硬编码密码赋值 |
| 2 | `grafana-admin-password` | GF_SECURITY_ADMIN_PASSWORD | Grafana admin 密码 |
| 3 | `glitchtip-password` | GLITCHTIP_ADMIN_PASSWORD | GlitchTip admin 密码 |
| 4 | `postgres-password` | POSTGRES_PASSWORD | PostgreSQL 密码 |
| 5 | `django-secret-key` | SECRET_KEY | Django SECRET_KEY |
| 6 | `http-basic-auth-password` | auth= | HTTP Basic Auth 密码 |
| 7 | `common-weak-password` | admin123/password123/12345678 | 常见弱密码 |

---

## 6. 本地使用方式

### 6.1 获取最新配置

```bash
# 从 master 分支获取最新版本
git show origin/master:.github/workflows/hardcoded-password-scan.yml > workflow.yml
git show origin/master:.github/gitleaks-config.toml > gitleaks-config.toml
```

### 6.2 本地扫描命令（与 CI 一致）

```bash
gitleaks detect \
  --config .github/gitleaks-config.toml \
  --source . \
  --no-git \
  --report-format json \
  --report-path scan-reports/gitleaks-report.json \
  --verbose
```

### 6.3 退出码语义

| 退出码 | 含义 |
|--------|------|
| 0 | 无硬编码密码 |
| 1 | 发现硬编码密码 |
| 2 | 配置错误（panic） |

### 6.4 修改后验证

```bash
# 1. 本地扫描验证语法
gitleaks detect --config .github/gitleaks-config.toml --source . --no-git --verbose

# 2. 推送触发 CI
git push

# 3. 查看 CI 状态
gh run list --workflow=hardcoded-password-scan.yml --limit 5
```

---

## 7. 复用到新仓库

### 7.1 复制文件

```bash
cp .github/workflows/hardcoded-password-scan.yml <new-repo>/.github/workflows/
cp .github/gitleaks-config.toml <new-repo>/.github/
```

### 7.2 调整建议

| 配置项 | 调整建议 |
|--------|---------|
| `on.push.paths` | 移除项目特定路径（如 `scripts/_import_dashboards.py`） |
| `[allowlist].paths` | 检查 `venv/`, `test_reports/` 是否符合新仓库结构 |
| `[allowlist].regexes` | 检查项目特定占位符（如有） |
| `env.GITLEAKS_VERSION` | 关注 gitleaks 官方发布版本, 必要时升级 |

### 7.3 验证清单

- [ ] 配置文件已复制到 `.github/`
- [ ] `paths` 过滤符合新仓库结构
- [ ] `allowlist.paths` 符合新仓库目录约定
- [ ] 本地 `gitleaks detect` 通过
- [ ] 推送后 CI 自动触发并通过

---

## 8. 关联文档

- **CI 配置归档**: [../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md](../archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md)
- **复盘报告**: [../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md](../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md)
- **团队 Wiki**: [../wiki/ci_security_scan_wiki.md](../wiki/ci_security_scan_wiki.md)
- **安全配置 Wiki**: [../wiki/security_config_wiki.md](../wiki/security_config_wiki.md)
- **gitleaks 官方文档**: https://github.com/gitleaks/gitleaks
- **GitHub Actions workflow syntax**: https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions

---

**导出版本**: v1.0
**最后更新**: 2026-07-26
**适用版本**: 云枢智能体 v1.3.1
