# 分支状态与待合并 PR 汇总报告（更新版）

> 生成时间：2026-08-07 14:32（更新） · 用于确认 v1.0.0 收尾后续操作
> 更新要点：远程 master 前进至 `5a1bfae9`，v1.0.0 落后 6 提交（**第 11 次前移已触发**）；PR #379 CI 已推进（54 pass）

---

## 1. 分支与标签状态（实时）

| 项 | 指向 | 状态 |
|----|------|------|
| `origin/master`（GitHub，实时） | `5a1bfae9` | 远程权威（CI 健康度看板趋势行 [skip ci]，最新） |
| `gitee/master` | `61075ad3` | ⚠️ 落后 GitHub（第 11 次前移 `-SyncGitee` 一并同步） |
| `v1.0.0`（双端） | `35190a25` | ⚠️ **落后 origin/master 6 提交 → 第 11 次前移已触发** |
| `v1.0.0-preflight` | `b0b1a433` | 未动 ✓ |
| 主工作区本地 `master` | `e42b1ab0` | 落后远程（是 origin/master 祖先，**无分叉**，并行会话已本地推进至 e42b1ab0） |

## 2. v1.0.0 落后明细（6 提交）

```
e42b1ab0 fix(release): 修复 WinForms 引导脚本两点 — UIA 定位 Name + Point 参数拆包
61075ad3 docs(observability): 新增 v1.0.0 架构修复最终闭环报告
3756ba3d ci(core-invariants): checkout 竞态静默跳过 + 汇总步骤文件守卫
926bc437 docs(observability): 新增 v1.0.0 架构修复发布说明
5ed93f78 docs(architecture): 自动更新模块依赖图 [skip ci]
5a1bfae9 docs(ci): 自动更新 CI 健康度看板趋势行 [skip ci]
```

## 3. 待合并 PR：#379（归档）

| 项 | 值 |
|----|----|
| 分支 | `archive/v100-tag-final` → `master`（squash） |
| 内容 | v1.0.0 标签前移最终归档报告（第 10 次前移详情 + 轨迹全景 + 修复验证） |
| mergeable | UNKNOWN（base 前进 5a1bfae9，GitHub 正在重新计算） |
| CI 状态（14:28） | **pass=54 / fail=1 / pending=1 / skip=7** |
| 唯一失败 | 文档链接预检与锚点回归测试（**已自动 rerun**，watch 脚本 job=92789287180） |

> GitHub Actions 曾短暂调度停滞（13:18-13:28 全仓库 run 零启动，特征同 Service Unavailable 故障），14:26 起恢复推进。

## 4. 轮询脚本状态

- 后台运行中：`.tmp-ci-merge-watch-379.ps1`（每 60s，try#66 已过）
- 失败 job 自动 rerun 已生效 2 次（文档链接预检 92786476473 / 92789287180）
- **全绿输出 `NOTIFY: PR #379 CI 全绿` 后通知合并**

## 5. 后续操作清单

```powershell
# ① 等 CI 全绿（轮询中，全绿即通知）
# ② 合并归档 PR（先确认 mergeable 从 UNKNOWN 变为 MERGEABLE）
gh pr merge 379 --squash
# ③ 第 11 次前移（v1.0.0 落后 6 提交，触发条件已成立；含 gitee 同步落后）
pwsh -File scripts/dev/advance_v100_tag.ps1 -Execute -SyncGitee
# ④ 清理归档分支
git worktree remove --force .tmp-v100-tag-archive
git branch -D archive/v100-tag-final; git push origin --delete archive/v100-tag-final
```

## 6. 遗留待并行会话处理

1. **主工作区 30 项未提交**（3 已跟踪修改：ops_log_parallel_session_cleanup、v100_final_execution_report、tlm-hook-failsafe.psd1、run_l3_regression_tests.ps1 + 26 untracked，含知识库重构计划/脚本）——并行会话领地，未动
2. 本地 master（e42b1ab0）落后远程：并行会话 `git fetch && git reset --soft origin/master` 对齐（无分叉，低风险）

## 7. 验证命令

```powershell
git ls-remote origin refs/heads/master refs/tags/v1.0.0 refs/tags/v1.0.0-preflight
git ls-remote gitee refs/heads/master refs/tags/v1.0.0
gh pr view 379 --json mergeable,mergeStateStatus,state
gh pr checks 379                       # 全绿 = 0 fail / 0 pending
pwsh -File scripts/dev/advance_v100_tag.ps1   # dry-run 预览第 11 次前移
```
