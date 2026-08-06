# 预检工具包 v1.0.0 发布流程 · 完整操作复盘

> 生成时间：2026-08-06 · 复盘范围：工具包开发 → 发布 → 标签管理 → 环境清理 → 报告归档全流程
> 配套文档：`v100_release_final_summary_20260806.md`（发布总结）/ `v100_release_final_status_20260806.md`（状态确认）

## 1. 背景与目标

云枢知识库重构任务 0（契约层）交付 ChromaDB 导入降级**预检工具包**，经历三个阶段：

1. **契约层开发**：`agent/knowledge/`（schema.py 64 行 + lifecycle.py 19 行）+ 87 单测
2. **挂起根因修复**：chromadb 1.5.9 + pydantic_settings 本地导入偶发死锁（daemon 线程持 import 锁）→ `_create_client` 改子进程探测 + 缓存，`DISABLE_NATIVE_EXT` 默认开启
3. **工具包打包**：`agent/preflight` CLI（12 条路径）+ Docker 容器化 + ci.yml 预检集成 + 114 单测双模块 100% 覆盖

## 2. 发布链路（master 提交序列）

| 提交 | 内容 | 性质 |
|------|------|------|
| `6c83fb32` | feat(preflight): ChromaDB 导入降级预检工具包 v1.0.0 | 发布起点 |
| `b0b1a433` | docs(release): 发布日志 + 回滚指南 | 发布文档（= v1.0.0-preflight） |
| `ca1fb58e` | feat: 技能索引缓存（cherry-pick 自 759e8219） | 功能补充 |
| `8aa23073` / `8c046df2` / `fa196470` | 依赖图 / gitee 修复 / gitee 回归测试 | 并行会话推进 |
| `fa196470` | **master 最新 = v1.0.0 最终指向** | 发布终点 |

## 3. 关键操作步骤与命令

### 3.1 本地提交与标签创建

```powershell
# 环境变量（pre-commit/pre-push hook 跨仓库寻址必需）
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"
# 精确提交（避免混入并行会话文件）
git add <paths>
git commit -m "docs(release): 预检工具包 v1.0.0 发布日志与回滚指南"
# 独立标签（发布文档提交点）
git tag v1.0.0-preflight b0b1a433
```

### 3.2 推送 master 与标签

```powershell
git push origin master
git push origin v1.0.0-preflight
```

### 3.3 技能缓存选择性合入

```powershell
# 仅 cherry-pick 技能缓存（759e8219），不携带 04b6bb22 杂项
git cherry-pick 759e8219        # master 上落为 ca1fb58e
```

### 3.4 v1.0.0 第一次强制前移（→ ca1fb58e）

```powershell
git tag -f v1.0.0 ca1fb58e
git push origin v1.0.0 --force  # 远程 forced update 确认
```

### 3.5 工作区清理（无数据丢失）

```powershell
# 残留 worktree 目录 + coverage 临时文件
Remove-Item .wt-master -Recurse -Force
Remove-Item .coveragerc.tmp -Force
# 失效 worktree 元数据（.tmp-master 目录已被并行会话删除但引用仍注册）
git worktree prune
# SQLite WAL 安全消化：将 214KB WAL 合并进主库后残留自动消失
python -c "import sqlite3; c=sqlite3.connect('data/orchestrator_config.db', timeout=5); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
```

### 3.6 v1.0.0 第二次强制前移（→ fa196470，本次）

```powershell
git tag -f v1.0.0 fa196470
git push origin v1.0.0 --force  # hook 12/12 通过，forced update ca1fb58e→fa196470
```

### 3.7 报告生成与验证

```powershell
# 实时验证远程标签与 master 一致
git ls-remote origin refs/heads/master refs/tags/v1.0.0 refs/tags/v1.0.0-preflight
# 祖先验证（关键提交是否已进 master）
git merge-base --is-ancestor 6c83fb32 fa196470   # exit 0 = 是
git merge-base --is-ancestor ca1fb58e fa196470   # exit 0 = 是
```

## 4. 环境清理记录

| 对象 | 处理 | 风险控制 |
|------|------|----------|
| `.wt-master/` | 删除（残留 worktree，内含已完成使命的提交消息草稿） | 草稿内容已对应提交 8c046df2 |
| `.coveragerc.tmp` | 删除 | 临时文件 |
| `.tmp-master/` worktree 元数据 | `git worktree prune` | 目录已不存在，仅清引用 |
| `data/orchestrator_config.db-shm/.db-wal` | `wal_checkpoint(TRUNCATE)` 消化后消失 | 零数据丢失（214KB WAL 合并进主库） |
| 并行会话 5 文件 + 本会话 2 报告 | 保留 | 归属并行会话/本会话，不属残留 |

## 5. 验证结果（最终）

```
远程 master            = fa196470
远程 v1.0.0            = fa196470  ✓ 可见且最新（= master）
远程 v1.0.0-preflight  = b0b1a433  ✓ 独立标签
```

- 发布链完整性：`6c83fb32`（工具包）+ `ca1fb58e`（技能缓存）均为 master 祖先 ✓
- 环境干净度：本会话残留清零，剩余未跟踪文件归属并行会话

## 6. 经验教训与风险

1. **push 竞争**：并行会话频繁推自动提交（依赖图/看板），push 被拒 non-fast-forward → `git pull --rebase origin master` 后重推
2. **共享工作区竞态**：并行会话切换分支会清空暂存区、覆盖工作区文件 → 提交前先确认当前分支，用精确 `git add <paths>` / `-- <paths>` 提交
3. **commit-origin-guard enforce**：master 人工直推无 PR 会被 BLOCK（ORIGIN-04），后续 master 提交建议走 PR
4. **Windows worktree 残留**：`git worktree remove` 偶发文件锁失败 → 残留目录需 `Remove-Item` + `git worktree prune`
5. **行尾差异假修改**：文件显示 M 但 `git diff` 为空 = 纯 LF/CRLF 差异 → `git restore` 还原，无内容可合并
6. **hook 环境变量**：新终端会话必须重设 `TLM_HOOK_SOURCE_REPO`，否则 pre-commit/pre-push 报 `Bad file descriptor`
