# CI gitleaks 安全扫描配置归档

**归档日期**: 2026-07-26
**版本**: v1.3.1
**状态**: ✅ 验收通过（master + staging 双分支 CI 通过）
**归档负责人**: 安全团队
**来源 commit**: `32bd26db` (master) / `32bd26db` (staging)

---

## 1. 归档概述

本次归档记录 gitleaks 硬编码密码扫描 CI 工作流的完整修复链路与验证通过状态，作为 P1 防复发机制的知识库快照。归档内容覆盖：

- **CI 工作流文件**：`.github/workflows/hardcoded-password-scan.yml`
- **gitleaks 配置文件**：`.github/gitleaks-config.toml`
- **真实密码修复**：`scripts/rotate_grafana_password.ps1`（P1 复发修复）
- **修复过程文档**：复盘报告 + workflow 导出

---

## 2. 修复 commits 链路

共 7 个 commit, 跨 2 个工作日, 每个独立可回滚:

| # | Commit | 类型 | 根因摘要 | 修复方式 |
|---|--------|------|---------|---------|
| 1 | `192c4a70` | schema | `paths` + `paths-ignore` 混用被 GitHub Actions 拒绝 | 改用 `!` 排除模式嵌入 `paths` |
| 2 | `fcea2234` | action 版本 | `actions/upload-artifact@v3` 已强制废弃 | 升级 `upload-artifact` 到 v4 |
| 3 | `a4da1e37` | action 版本 | 上一次遗漏 `checkout` + `cache` 升级 | 补全 v3 → v4 |
| 4 | `d52bd3cd` | gitleaks 参数 | `--no-git-text` 不存在 | 改用 `--no-git` |
| 5 | `ab608151` | 正则引擎 | Go RE2 不支持 Perl lookahead `(?!...)` | 改用 gitleaks 原生 `allowlist.regexes` |
| 6 | `dea18ba9` | 误报排除 | venv / test_reports / *.log 触发 162 处误报 | allowlist.paths 添加 3 类路径 |
| 7 | `18795a2a` | P1 真实问题 | `Yunshu@P1Verify2026!` 硬编码密码 | 改用 `$env:GRAFANA_OLD_PASSWORD` 环境变量 |
| 8 | `32bd26db` | 误报排除 | 错误提示中示例语法被识别为赋值 | 简化提示文本 |

---

## 3. 验证证据

### 3.1 master 分支 CI 验证

| 运行 ID | 触发事件 | 时长 | 结论 | 备注 |
|---------|---------|------|------|------|
| `30113381577` | push | 0s | failure | workflow schema 加载失败 |
| `30143436727` | push | 40s | failure | action v3 deprecated |
| `30143549694` | push | 22s | failure | gitleaks `--no-git-text` 不存在 |
| `30143578164` | push | 35s | failure | RE2 不支持 `(?!...)` |
| `30143628903` | push | 2m | failure | 扫描发现 168 处告警 |
| `30143793020` | push | 1m | failure | 扫描发现 5 处告警 |
| `30145116365` | push | 1m | failure | 扫描发现 1 处误报 |
| **`30145240113`** | push | 1m | **success** | ✅ **master 验证通过** |

### 3.2 staging 分支预验证

- **staging 分支创建**：基于 `origin/master` (HEAD: `32bd26db`)
- **触发方式**：`workflow_dispatch`（手动触发, 因 paths 过滤在内容无变化的分支创建中不触发）
- **CI 运行 ID**：`30164944709`
- **结论**：✅ **staging 预验证通过**

| 步骤 | 状态 |
|------|------|
| Set up job | ✓ |
| 检出代码（完整历史） | ✓ |
| 缓存 gitleaks 二进制 | ✓ |
| 安装 gitleaks | ✓ |
| 运行 gitleaks 扫描 | ✓ |
| 上传扫描报告 | ✓ |
| Post 缓存 gitleaks 二进制 | ✓ |
| Post 检出代码（完整历史） | ✓ |
| Complete job | ✓ |

### 3.3 唯一警告（非阻塞）

```
Node.js 20 is deprecated. The following actions target Node.js 20
but are being forced to run on Node.js 24:
actions/cache@v4, actions/checkout@v4, actions/upload-artifact@v4
```

**处理建议**：GitHub 自动强制运行在 Node.js 24, 不阻塞 CI。建议后续升级到 actions v5（待官方发布）。

---

## 4. 归档文件清单

### 4.1 CI 配置（可执行版本, master HEAD）

| 文件 | 路径 | 行数 | 说明 |
|------|------|------|------|
| workflow | `.github/workflows/hardcoded-password-scan.yml` | 168 | GitHub Actions 工作流 |
| gitleaks config | `.github/gitleaks-config.toml` | 122 | gitleaks 规则与白名单 |
| 密码修复 | `scripts/rotate_grafana_password.ps1` | 49 | P1 真实密码修复 |

### 4.2 文档归档

| 文件 | 路径 | 大小 | 内容 |
|------|------|------|------|
| workflow 导出 | `docs/ci/2026-07-26-gitleaks-ci-workflow-export.md` | ~16KB | workflow + config 全文 + 字段表 + 使用方式 |
| 复盘报告 | `docs/postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md` | ~10KB | 7 类根因分析 + 改进建议 |
| 团队 Wiki | `docs/wiki/ci_security_scan_wiki.md` | ~10KB | 团队使用指南 + 故障排查 + FAQ |
| 本归档 | `docs/archive/CI_GITLEAKS_SECURITY_SCAN_ARCHIVE.md` | 当前 | 修复链路 + 验证证据 + 文件清单 |

### 4.3 获取完整配置内容

```bash
# 从 master 分支获取最新版本
git show origin/master:.github/workflows/hardcoded-password-scan.yml > workflow.yml
git show origin/master:.github/gitleaks-config.toml > gitleaks-config.toml

# 或查阅归档导出（包含完整内容与字段说明）
cat docs/ci/2026-07-26-gitleaks-ci-workflow-export.md
```

---

## 5. 关键决策记录

| 决策点 | 选择 | 理由 |
|--------|------|------|
| `paths` vs `paths-ignore` | 用 `!` 排除模式嵌入 `paths` | GitHub Actions 推荐, 保留触发过滤语义 |
| action v3 vs v4 | 升级到 v4 | v3 已强制废弃, 降级不可行 |
| lookahead vs allowlist | 用 gitleaks 原生 `allowlist.regexes` | RE2 兼容, 语义清晰 |
| 真实密码修复 vs allowlist 排除 | 修复密码 | P1 真实问题必须修复, 不能用 allowlist 掩盖 |
| staging 同步策略 | 从 master 创建分支 | 所有修复已在 master, cherry-pick 无意义 |

---

## 6. 后续行动项

| # | 行动项 | 负责人 | 截止日期 | 状态 |
|---|--------|--------|---------|------|
| 1 | 创建 `docs/security/gitleaks-rule-authoring.md` 规则编写手册 | TBD | 2026-08-02 | pending |
| 2 | 引入 `actionlint` pre-commit hook | TBD | 2026-08-02 | pending |
| 3 | 引入 `gitleaks` pre-commit hook | TBD | 2026-08-09 | pending |
| 4 | 清理 `test_reports/logs/` 历史日志 | TBD | 2026-08-09 | pending |
| 5 | 季度 review `actions/*` 版本 | TBD | 季度 | pending |

详见复盘报告：[../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md](../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md)

---

## 7. 关联资源

- **复盘报告**：[../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md](../postmortems/2026-07-26-gitleaks-ci-fix-postmortem.md)
- **workflow 导出**：[../ci/2026-07-26-gitleaks-ci-workflow-export.md](../ci/2026-07-26-gitleaks-ci-workflow-export.md)
- **团队 Wiki**：[../wiki/ci_security_scan_wiki.md](../wiki/ci_security_scan_wiki.md)
- **安全配置 Wiki**：[../wiki/security_config_wiki.md](../wiki/security_config_wiki.md)
- **P1 修复参考 commit**：`9d51c406`
- **GitHub deprecation 公告**：https://github.blog/changelog/2024-04-16-deprecation-notice-v3-of-the-artifact-actions/
- **gitleaks 文档**：https://github.com/gitleaks/gitleaks
- **GitHub Actions workflow syntax**：https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions

---

**文档版本**: v1.0
**最后更新**: 2026-07-26
**适用版本**: 云枢智能体 v1.3.1
