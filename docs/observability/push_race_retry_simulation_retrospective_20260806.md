# Push 竞争自动重试 — 模拟验证技术复盘（2026-08-06）

- **日期**: 2026-08-06
- **事件**: 云枢 `update-ci-dashboard` job 并发 push 竞争失败（non-fast-forward）
- **修复**: `ci.yml` 看板推送改为 `pull --rebase` + 最多 3 次重试 + 耗尽后 `::warning::` 跳过
- **验证**: 3 次本地真实 git 模拟全部 PASS（含 attempt1 失败 → attempt2 收敛场景）
- **关联**: PR #312 / 问题跟踪单 `docs/troubleshooting/ci_failures_tracking_20260806.md` / 操作指南 `docs/ci_guidelines/dev_pr_merge_guide_20260806.md`

---

## 1. 背景与事件时间线

| 时间 | 事件 |
|---|---|
| 2026-08-06 | 提交 `d55abd03` 直接 push master，触发 guard ORIGIN-04 拦截（enforce 模式 BLOCK） |
| 2026-08-06 | 同次运行中，`update-ci-dashboard` job 基于触发时的远端 HEAD 提交看板，运行期间远端 master 被并行 workflow 推进，`git push` 报 non-fast-forward |
| 2026-08-06 | 根因定位：**CI job 的 push 基于陈旧远端快照**，未先与远端同步 |
| 2026-08-06 | 修复 `ci.yml` L543-L568：commit 后 `pull --rebase` + 3 次重试 + warning 跳过 |
| 2026-08-06 | 3 次本地模拟验证（sim1/sim2/sim3）全部 PASS |
| 2026-08-06 | 创建 PR #312（base=master），沉淀团队操作指南 |

## 2. 根因分析（五问法）

1. **现象**: `git push` 失败，报 non-fast-forward。
2. **直接原因**: CI job 基于触发时刻的远端 HEAD 做 commit + push，但 job 运行期间远端被**并行 workflow** 推进，本地提交的父提交已不是远端 HEAD。
3. **为什么会有并行推进**: 仓库存在多个 workflow（ci.yml / observability-ci.yml / boundary-guard / commit-origin-guard 等），master 上的提交会同时触发多个 workflow；不同 workflow/job 的运行时长不同，先完成的 job 写回 master，后完成的 job 就落后了。
4. **为什么 push 会失败而非自动合并**: git push 是快速前进（fast-forward）语义，远端 HEAD 已前进时拒绝覆盖，强制要求先 `git pull` 同步。
5. **为什么需要重试**: 单次 `pull --rebase` + push 仍可能在"pull 之后、push 之前"的窗口内被第三方再次推进，属于**竞态**，只有重试才能收敛。

## 3. 修复方案

### 3.1 代码（ci.yml `update-ci-dashboard` job）

```bash
for i in 1 2 3; do
  if git pull --rebase origin master; then
    if git push origin master; then
      echo "已推送看板更新 (attempt $i)"
      exit 0
    fi
  fi
  echo "[dashboard] push 竞争失败 (attempt $i/3)，5s 后重试"
  sleep 5
done
echo "::warning::看板更新 3 次重试仍失败，本次跳过（下次推送自动补齐）"
```

要点：

- `fetch-depth: 0`：checkout 需完整历史才能 rebase
- 权限 `contents: write` + 默认 GITHUB_TOKEN（`[skip ci]` 不递归触发 CI）
- 3 次重试耗尽后 `::warning::` 跳过而非 exit 非 0：看板为**可丢失更新**，不阻塞 CI、不产生失败通知噪音
- 看板为行级追加，rebase 冲突概率极低；理论冲突时本次跳过，下次推送自动补齐

### 3.2 纪律（已沉淀到操作指南）

- **R1** 禁止人工直接 push master（guard ORIGIN-04）
- **R2** master 合入统一走 PR
- **R3** CI 自动写回 master 的 job 必须 `pull --rebase` + 重试
- **R4** CI 自动提交用 `[skip ci]`

## 4. 模拟验证（本地真实 git 仓库）

### 4.1 模拟环境

- 裸仓库 `origin.git` 模拟远端，`work_a` 模拟 CI job，`work_b` 模拟并行 workflow
- 重试脚本与 ci.yml **逐行一致**
- 通过 pre-push hook 在首次 push 窗口注入"并行推进远端"，精确复现竞态

### 4.2 sim1 — 远端推进 2 commit，一次收敛

远端在 job 运行期间被并行推进 2 个 commit，重试逻辑第一次 `pull --rebase` 即拉取全部并行提交并 push 成功。

### 4.3 sim2/sim3 — attempt1 失败 → attempt2 重试成功

远端在 **pull 成功之后、push 之前** 被并行推进（最严格竞态窗口），attempt1 push 被拒，重试后收敛。sim3 完整日志：

```
==== 4) 运行 ci.yml 重试逻辑 (L557-567) ====
From /mnt/c/Windows/Temp/push_retry_sim3/origin
 * branch            master     -> FETCH_HEAD
Current branch master is up to date.
[sim] 并行 workflow 正在推进远端 master (attempt1 push 窗口)...   ← 并行 job 抢推
[master c0ca607] concurrent c5 (attempt1 push 窗口)
To /mnt/c/Windows/Temp/push_retry_sim3/origin.git
   57fb9f2..c0ca607  master -> master                            ← 远端被推进
To /mnt/c/Windows/Temp/push_retry_sim3/origin.git
 ! [remote rejected] master -> master (failed to update ref)     ← attempt1 push 被拒
error: failed to push some refs to '.../origin.git'
[dashboard] push 竞争失败 (attempt 1/3)，5s 后重试               ← 重试日志
From /mnt/c/Windows/Temp/push_retry_sim3/origin
   57fb9f2..c0ca607  master     -> origin/master
Successfully rebased and updated refs/heads/master.              ← attempt2 pull --rebase 收敛
To /mnt/c/Windows/Temp/push_retry_sim3/origin.git
   c0ca607..73c3fff  master -> master                            ← attempt2 push 成功
已推送看板更新 (attempt 2)
```

> GitHub 真实远端对同类竞争报 `! [rejected] master -> master (fetch first)`，语义相同（本地 HEAD 落后远端），重试路径一致。

### 4.4 结论

| 场景 | 结果 |
|---|---|
| 远端推进 2 commit（job 运行期） | attempt1 收敛 PASS |
| pull 后 push 前被推进（最严竞态窗口） | attempt1 失败 → attempt2 收敛 PASS |
| 耗尽 3 次 | 输出 `::warning::` 跳过，不阻塞 CI（逻辑层验证） |

## 5. 学习要点

1. **CI 自动写回 master 永远是竞态**：master 是共享可推进目标，任何基于"触发时快照"的 push 都可能失败。
2. **可丢失更新用 warning 而非 error**：看板类数据下次推送自动补齐，失败通知噪音会淹没真实问题。
3. **重试要有界**：3 次 + sleep 5s 足以覆盖秒级竞态窗口，无界重试会拖垮 CI 时长。
4. **pre-push hook 可精确注入竞态**：本地模拟用 hook 在真实 git push 的窗口期注入并行推进，是验证重试逻辑的可靠手段。
5. **模拟必须用真实 git**：仅阅读逻辑无法发现 `git pull` 交互输出、rebase 行为、ref lock 等真实细节。

## 6. 关联文档

- 问题跟踪单：`docs/troubleshooting/ci_failures_tracking_20260806.md`
- 团队操作指南：`docs/ci_guidelines/dev_pr_merge_guide_20260806.md`
- 守卫拦截修复指南：`docs/troubleshooting/commit_origin_guard_fix_guide_20260806.md`
- PR #312：https://github.com/nzt47/security-tools/pull/312
