# master 分支非人工 commit 阻断 — 排查与设计记录

**生成时间**：2026-08-05
**触发事件**：PR #227 合并复盘时发现 master 历史上出现伪装人工身份的自动 commit
**实施 commit**：本 PR（guard-master-commit-origin 机制落地）

---

## 1. 背景

2026-08-05 在合并 PR #227 后清理分支时，发现 master 分支上存在两类非人工发起的 commit：

1. **合法的 github-actions[bot]**（依赖图自动更新、CI 健康度看板）— workflow 显式允许，带 `[skip ci]`
2. **伪装人工的脚本 commit**（author=nzt47，来自 `scripts/publish_fix_to_docs.py`）— 用本地 git 身份直接 push，无法与人工 commit 区分

**问题核心**：`publish_fix_to_docs.py` 第 165-166 行用本地 git config 的 nzt47 身份 `git commit`，未切换 bot 身份。master 分支未开启 PR-only 分支保护，允许直接 push。这导致：
- 自动 commit 混入 master 历史，难以审计
- 脚本可能在无人监督下修改任意文件（不仅限于文档）
- 单看 author email 无法区分人工 vs 脚本

---

## 2. 排查过程

### 2.1 采样数据

通过 `git log origin/master --pretty=format:"%h|%an|%ae|%s"` 采样最近 30 个 commit：

| commit | author | subject | GitHub 关联 PR | 性质 |
|---|---|---|---|---|
| `ab4f3670` | github-actions[bot] | docs(architecture): 自动更新模块依赖图 [skip ci] | `[]` | 合法 bot |
| `2a59976c` | github-actions[bot] | docs(ci): 自动更新 CI 健康度看板趋势行 [skip ci] | `[]` | 合法 bot |
| `ca07ccb5` | nzt47 <13539371839@139.com> | docs(ci): 更新 CI 修复记录索引(1 条) | `[]` | **脚本 push** |
| `7ebdfc33` | nzt47 <13539371839@139.com> | feat(ci): 新增修复记录推送工具 | `[]` | **人工直接 push** |

### 2.2 根因定位

通过 `gh run list --branch=master` 查询关联 workflow run + 读取 `publish_fix_to_docs.py` 源码：

```python
# scripts/publish_fix_to_docs.py 第 165-166 行（修复前）
msg = f"docs(ci): 更新 CI 修复记录索引({len(new_entries)} 条)"
cm = _git(["commit", "-m", msg])  # ← 用本地 git config 的 nzt47 身份
```

**根因**：
1. `publish_fix_to_docs.py` 用本地 git config 的 nzt47 身份提交，未切换 bot 身份
2. master 分支未开启 PR-only 分支保护，允许直接 push
3. GitHub API 的 `/commits/{sha}/pulls` 对直接 push 的 commit 返回空列表（`[]`），这是区分脚本 push vs PR 合并的关键依据

### 2.3 关键技术发现

**GitHub API 查关联 PR**：
- REST 端点：`GET /repos/{owner}/{repo}/commits/{sha}/pulls`
- GraphQL：`associatedPullRequests`（**必须用 40 位完整 SHA**，短 SHA 报 `GitObjectID` 类型错误）
- 实测结果：直接 push 到 master 的 commit（`ca07ccb5`、`7ebdfc33`）均返回 `[]`，无关联 PR
- 合法 bot commit 也返回 `[]`（bot 不走 PR），但 bot 身份本身是白名单

**项目工作流现状**：
- `gh pr list` 仅 5 个 PR，master 历史大量 nzt47 直接 push 的人工 commit
- 项目当前"master 直接 push 为主"，未强制 PR 流程
- 因此"nzt47 commit 必须有 PR 关联"策略会同时阻断脚本 push 和人工直接 push

---

## 3. 解决方案设计

### 3.1 检测策略：白名单 + PR 关联校验 + bot 路径白名单

**5 个校验项**（`verify_commit_origin.py`）：

| ID | 校验内容 | 触发条件 | 行为 |
|---|---|---|---|
| ORIGIN-01 | author/committer email 在白名单 | email 不在白名单 | BLOCK |
| ORIGIN-02 | bot commit 修改路径在白名单 | bot 修改非白名单路径 | BLOCK |
| ORIGIN-03 | bot commit subject 含 [skip ci] | bot commit 缺 [skip ci] | BLOCK |
| ORIGIN-04 | 人工身份 commit 有关联 PR | nzt47 commit 无关联 PR | BLOCK |
| ORIGIN-05 | subject 不命中黑名单（可选） | subject 命中黑名单正则 | BLOCK |

**白名单配置**（`scripts/commit_origin_whitelist.yaml`）：

```yaml
allowed_authors:
  - email: "13539371839@139.com"
    name: "nzt47"
    require_pr: true  # 必须有 GitHub 关联 PR
  - email: "github-actions[bot]@users.noreply.github.com"
    name: "github-actions[bot]"
    require_pr: false
    allowed_paths:      # bot 只能改这些路径
      - "docs/architecture/*"
      - "docs/observability/*"
      - "docs/dashboards/*"
      - "docs/ci-health/*"
      - "VERSION.md"
    require_skip_ci: true
```

### 3.2 GitHub API 三级兜底

| 优先级 | 方法 | 依赖 | 适用场景 |
|---|---|---|---|
| 1 | `gh api repos/{o}/{r}/commits/{sha}/pulls` | gh CLI（自动用 GITHUB_TOKEN） | CI 环境（gh 预装） |
| 2 | GraphQL `associatedPullRequests` | gh CLI | REST 端点偶发空响应 |
| 3 | 纯 `urllib` REST | `GITHUB_TOKEN` 环境变量 | gh 不可用 |

**可靠性保障**（【不易】不锁死 master push）：
- API 调用失败时降级为 `::warning::` 不阻断（即使 enforce 模式）
- 本地无 `GH_TOKEN`/`GITHUB_TOKEN` 时跳过 PR 校验并 `::notice::` 提示
- GraphQL 必须用 40 位完整 SHA（`git rev-parse` 转换）

### 3.3 配套修复 publish_fix_to_docs.py

参照 `.github/workflows/architecture-check.yml:235-236` 的 bot 身份配置模式：

```python
# 修复后（scripts/publish_fix_to_docs.py 第 165-183 行）
orig_name = _git(["config", "user.name"]).stdout.strip()
orig_email = _git(["config", "user.email"]).stdout.strip()
_git(["config", "user.name", "github-actions[bot]"])
_git(["config", "user.email", "github-actions[bot]@users.noreply.github.com"])
try:
    msg = f"docs(ci): 更新 CI 修复记录索引({len(new_entries)} 条) [skip ci]"
    cm = _git(["commit", "-m", msg])
finally:
    # 【不易】提交后恢复本地 git 身份, 避免污染开发者本地配置
    if orig_name: _git(["config", "user.name", orig_name])
    if orig_email: _git(["config", "user.email", orig_email])
```

---

## 4. 三阶段灰度上线路径

⚠️ **核心约束**：项目当前"master 直接 push 为主"，不能一刀切 enforce。

| 阶段 | GUARD_MODE | 行为 | 前置条件 | 回滚方式 |
|---|---|---|---|---|
| **阶段 1**（本 PR） | `dry-run`（默认） | 仅 `::warning::` 告警，exit 0 不阻断 | 无 | 删除 workflow 文件 |
| **阶段 2**（观察 1-2 周） | `enforce`（仓库变量切换） | 检测到问题 exit 1 阻断 | dry-run 报告无误报 | 切回 `dry-run` |
| **阶段 3**（长期） | `enforce` + 分支保护 | master 禁止直接 push，必走 PR | 团队改用 PR 流程 | 关闭分支保护 |

**切换方式**：仓库 Settings → Secrets and variables → Actions → Variables → 新增/修改 `COMMIT_ORIGIN_GUARD_MODE`

---

## 5. 本地验证结果（2026-08-05）

对 4 个已知 commit 跑 `verify_commit_origin.py`，全部符合预期：

| 测试 | commit | 模式 | 期望 | 实际 | 退出码 |
|---|---|---|---|---|---|
| 1 | `ab4f3670`（bot） | dry-run | PASS | PASS（ORIGIN-00） | 0 |
| 2 | `ca07ccb5`（脚本 push） | dry-run | ORIGIN-04 BLOCK | BLOCK（无关联 PR） | 0（告警） |
| 3 | `ca07ccb5`（脚本 push） | enforce | exit 1 | exit 1 | 1（阻断） |
| 4 | `7ebdfc33`（人工直接 push） | dry-run | ORIGIN-04 BLOCK | BLOCK（无关联 PR） | 0（告警） |

**策略局限确认**：测试 4 表明人工直接 push 到 master 的 commit 也会被标记为 BLOCK（无 PR 关联）。这正是阶段 1 采用 dry-run 的原因——dry-run 不阻断，让人工直接 push 也能通过。

**其他验证**：
- ✅ JSON 报告格式正确（`tool`/`status`/`total`/`blocked` 字段齐全）
- ✅ HTML 报告生成成功（1689 bytes）
- ✅ 批量模式正常（`--base` + `--sha` 范围展开）
- ✅ `publish_fix_to_docs.py` dry-run 修改后不崩溃

---

## 6. 误报处理流程

### 6.1 合法脚本被误阻断（enforce 阶段）

1. **确认脚本是否已切换 bot 身份**：参考 `publish_fix_to_docs.py` 的修复模式（`git config user.name/email` 临时切换）
2. **确认 bot 改的路径在 `allowed_paths` 白名单内**：不在则需在 `commit_origin_whitelist.yaml` 追加路径
3. **确认 commit subject 含 `[skip ci]`**：bot commit 契约要求
4. **若仍误报**：临时切 `GUARD_MODE=dry-run` 观察 Job Summary 报告，调整白名单后切回 `enforce`

### 6.2 人工 commit 被误阻断（enforce 阶段）

1. **确认 commit 是否走 PR 流程**：查 `github.com/{owner}/{repo}/pull/{NNN}`
2. **若是直接 push 到 master**：阶段 2 会阻断直接 push，需改走 PR 流程
3. **过渡期例外**：在 `commit_origin_whitelist.yaml` 的 `subject_allowlist_regex` 追加临时例外（enforce 前应清空，改用 PR 流程）

### 6.3 GitHub API 不可用

- API 调用失败时自动降级为 `::warning::` 不阻断（即使 enforce 模式）
- 检查 CI workflow 的 `permissions: pull-requests: read` 是否声明
- 检查 `GITHUB_TOKEN` 是否被覆盖（workflow 中不要手动覆盖默认 token）

---

## 7. API 可靠性说明

### 7.1 必须用 40 位完整 SHA

GraphQL `associatedPullRequests` 查询要求 `GitObjectID` 类型，短 SHA（8 位）会报错：
```
Variable $oid of type GitObjectID! was provided invalid value
```
`verify_commit_origin.py` 通过 `git rev-parse` 强制转换为 40 位完整 SHA。

### 7.2 API 失败降级

| 场景 | 降级行为 |
|---|---|
| gh CLI 不可用 | 尝试 GraphQL → 尝试 urllib + GITHUB_TOKEN |
| 所有 API 路径不可用 | `::warning::` 跳过 ORIGIN-04，标记为 pass（不阻断） |
| 本地无 GH_TOKEN/GITHUB_TOKEN | `::notice::` 跳过 PR 校验（防止本地锁死开发流程） |

### 7.3 跨 fork PR 关联

REST `/commits/{sha}/pulls` 端点对 fork PR 也返回关联（实测有效），不会因 PR 来自 fork 而漏判。

---

## 8. 相关文件清单

### 新增文件

| 文件 | 用途 |
|---|---|
| [scripts/verify_commit_origin.py](file:///C:/Users/Administrator/agent/scripts/verify_commit_origin.py) | 检测脚本（5 个校验项 + GitHub API 三级兜底） |
| [scripts/commit_origin_whitelist.yaml](file:///C:/Users/Administrator/agent/scripts/commit_origin_whitelist.yaml) | 白名单配置（可配置，新增路径只改 YAML） |
| [.github/workflows/guard-master-commit-origin.yml](file:///C:/Users/Administrator/agent/.github/workflows/guard-master-commit-origin.yml) | CI workflow（push 到 master 时触发） |
| [docs/troubleshooting/auto_commit_master_guard.md](file:///C:/Users/Administrator/agent/docs/troubleshooting/auto_commit_master_guard.md) | 本文档 |

### 修改文件

| 文件 | 修改内容 |
|---|---|
| [scripts/publish_fix_to_docs.py](file:///C:/Users/Administrator/agent/scripts/publish_fix_to_docs.py#L165-L183) | 第 165-183 行：commit 前后切换/恢复 bot 身份 + [skip ci] 后缀 |

### 参考实现（不修改，仅模式参照）

| 文件 | 参考点 |
|---|---|
| [scripts/verify_core_invariants.py](file:///C:/Users/Administrator/agent/scripts/verify_core_invariants.py) | CLI 契约（argparse + --json/--quiet/--html + 退出码 0/1） |
| [scripts/report_generator.py](file:///C:/Users/Administrator/agent/scripts/report_generator.py) | 报告生成契约（build_report/to_json/to_text/to_html） |
| [.github/workflows/core-invariants-guard.yml](file:///C:/Users/Administrator/agent/.github/workflows/core-invariants-guard.yml) | workflow 结构 + Slack 通知集成 |
| [.github/workflows/architecture-check.yml](file:///C:/Users/Administrator/agent/.github/workflows/architecture-check.yml) | bot 身份配置模板（第 235-236 行） |

---

## 9. 后续演进建议

1. **阶段 2 切 enforce 前**：清空 `commit_origin_whitelist.yaml` 的 `subject_allowlist_regex`（过渡期例外），改用 PR 关联校验作为主判定
2. **阶段 3 开启分支保护**：master 分支 Settings → Branches → Add rule → 勾选 "Require a pull request before merging" + 把 `guard-master-commit-origin` 和 `ci.yml` 勾选为 required status check
3. **其他脚本排查**：搜索代码库中其他可能用本地身份 git commit 的脚本（关键词 `git commit` + `subprocess`），参照 `publish_fix_to_docs.py` 修复模式切换 bot 身份

---

_由 Claude（GLM-5.2）于 2026-08-05 生成，基于 master 历史采样数据与 `verify_commit_origin.py` 本地验证结果。_
