---
title: Git 维护操作复盘：TASK-06 收尾（gc 优化 + 清理 + SOP 沉淀）
slug: git-维护复盘-task06-gc优化
status: current
type: insights
source: docs/zh/Git维护操作复盘_TASK06_20260815.md
date: '2026-08-15'
tags: [git, gc, 仓库维护, 并行会话, TASK-06, SOP]
links: []
contradictions: []
insight: 并行会话活跃环境下执行 git gc 的安全策略：空闲窗口轮询 + 默认参数（禁 --prune=now，reflog 90 天保护）可零影响并行会话；实测 loose 对象
  3.09 MiB(673 个)→0、packs 3→2、size-pack 57.57→56.37 MiB，策略已沉淀为 Git_Archive_Cleanup_SOP §三-E。
scope: 仓库维护（docs/Git_Archive_Cleanup_SOP.md）
metadata:
  report: docs/zh/Git维护操作复盘_TASK06_20260815.md
  commits: [e56b5575]
  gc_before: {loose_count: 673, loose_size: "3.09 MiB", packs: 3, size_pack: "57.57 MiB"}
  gc_after: {loose_count: 0, loose_size: "0 bytes", packs: 2, size_pack: "56.37 MiB"}
  sop_update: {section: "三-E 对象库优化", redline: "并行会话活跃时禁 gc / 禁 --prune=now"}
---

TASK-06 收尾（2026-08-15）执行 Git 仓库维护：对象库优化（git gc）+ 临时数据清理，全程并行会话活跃（9 worktree）零影响。

关键安全策略（已沉淀 SOP §三-E）：
1. 空闲窗口前置检查：`Get-Process git` 计数期望 0；有活跃 git 进程时轮询等待（15s 间隔）。
2. 默认参数 `git gc`：仅清理 2 周前不可达对象；**禁 `--prune=now`**（reflog 可达对象受 90 天保留期保护，防误伤并行会话中间产物）。
3. gc 后验证：HEAD / worktree 数 / 并行会话均不受影响。

实测数据：loose objects 673 个（3.09 MiB）→ 0；packs 3 → 2（合并）；size-pack 57.57 → 56.37 MiB。主要收益在 loose 对象清零与 pack 合并，size-pack 压缩有限（打包算法所致）。

背景：并行会话 rebase 曾剥离 5 个 TASK-06 提交（已重建入库），留下悬空对象——悬空对象不主动清理（reflog 90 天自动过期），避免全局 gc 误伤。

完整报告见 source 字段。
