# 仓库状态快照报告

> 归档位置：`docs/wiki/REPOSITORY_SNAPSHOT_REPORT.md`
> 生成日期：2026-08-05
> 用途：最终仓库状态快照，供审计与团队同步

---

## 1. 当前分支列表（7 个本地分支）

| 分支 | 指向提交 | 跟踪状态 | 最近提交 |
|------|---------|---------|---------|
| `* master` | `ac93aa6b` | [origin/master] | 4 分钟前（refactor(hooks): BOM 检查合并公共模块） |
| `develop` | `c63cabaa` | [origin/develop: **behind 1**] | 29 小时前 |
| `feature/tlm-step3-vectorstore-sqlite-vec` | `516028f4` | [gitee/...: **ahead 26**] | 11 天前 |
| `fix/arch-circular-deps` | `57c62dc2` | 远程存在（origin+gitee） | 4 周前 |
| `fix/ci-skills-check-403` | `e3c83f16` | worktree 检出（security-tools-fix） | 5 天前 |
| `fix/p0-p2-ci-regression` | `85091b2b` | [origin/...: 同步] | 11 小时前 |
| `gh-pages` | `48f543bc` | GitHub Pages 发布分支 | 9 天前 |

**说明**：
- 无已合并到 master 的旧分支（`git branch --merged` 仅返回 master 自身）
- 无 stale 远程引用（`git remote prune --dry-run` 无输出）
- `fix/ci-skills-check-403` 带 `+` 标记，在另一 worktree 使用中，不可删除

## 2. 分支未合并提交统计（相对 master）

| 分支 | 未合并提交数 | 说明 |
|------|------------|------|
| `develop` | 15 | 本地落后远程 1 个提交（需 pull） |
| `feature/tlm-step3-vectorstore-sqlite-vec` | 6 | 另有 26 个提交领先 gitee 未推送 |
| `fix/p0-p2-ci-regression` | 3 | 与远程同步 |
| `fix/arch-circular-deps` | 2 | 与远程同步 |
| `fix/ci-skills-check-403` | 2 | worktree 分支 |
| `gh-pages` | 1 | 发布分支，独立历史 |

## 3. Tag 信息

### 完整 Tag 列表（按版本倒序，前 8）

| Tag | 说明 |
|-----|------|
| `v2.0.0-feature-tools-router` | 工具路由 v2 功能 |
| `v2.0.0-dependency-fix` | 依赖修复 |
| `v1.5.0-bm25-normalization` | **BM25 短文档归一化优化（最新）** |
| `v1.4.0` | 常规版本 |
| `v1.3.0` | 常规版本 |
| `v1.2.1-fix-secure-manager-return` | SecureConfigManager 修复 |
| `v1.2.0` | 常规版本 |
| `v1.1.10` | 常规版本 |

### 最新 Tag 详情（v1.5.0-bm25-normalization）

| 项 | 值 |
|----|----|
| Tag 名称 | `v1.5.0-bm25-normalization` |
| 类型 | annotated tag |
| Tagger | `nzt47 <13539371839@139.com>` |
| 指向提交 | `9f6289f2`（docs(vector_store): BM25 优化里程碑邮件草稿 + commit 审计 + wiki 渲染验证） |
| 远程状态 | origin ✅ / gitee ✅ 均已推送 |
| Message | BM25 短文档归一化优化 Release：b=0.75->0.5，含核心变更/效果/回滚说明 |

## 4. 工作区状态

- **未提交变更**：19 行（即 19 个文件项）
- **性质**：TLM/observability 等其他任务的未提交成果 + `vector_store.py` 行尾符（LF/CRLF）差异，**均不属于 BM25 优化交付范围**
- **未跟踪文件**：`scripts/ps_bom_contract.py`（BOM 检查公共模块，其他任务产物）

## 5. 远程状态

| 远程 | 地址 | 状态 |
|------|------|------|
| origin | git@github.com:nzt47/security-tools.git | 同步，v1.5.0 tag 已推送 |
| gitee | git@gitee.com:nzt47/security-tools.git | 同步，v1.5.0 tag 已推送 |

## 6. 仓库健康度

| 检查项 | 结果 |
|--------|------|
| 已合并旧分支 | 无（无需清理） |
| stale 远程引用 | 无 |
| fsck 未引用对象 | 无（仓库无孤立对象） |
| 未推送提交 | `feature/tlm-step3` 分支 ahead 26（其他分支已同步） |
| 待 pull 提交 | `develop` behind 1 |
