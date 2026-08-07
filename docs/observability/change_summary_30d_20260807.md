# 近 30 天变更摘要（2026-07-08 ~ 2026-08-07）

> 来源：[master_origin_commit_diff_report_20260806.md](master_origin_commit_diff_report_20260806.md)
> 数据：`git log --since="30 days ago" origin/master`，共 796 提交
> 生成时间：2026-08-07

---

## 总览

| 维度 | 内容 |
|---|---|
| 提交总数 | 796（30 天） |
| 当日峰值 | 2026-08-06 单日 25+ 提交 |
| 分支状态 | master 与 origin/master 完全同步 |

---

## 主线一：发布自动化链路（8/06 密集）

- GitHub / Gitee / GitLab 三端 Release 工作流（release-auto.yml、.gitlab-ci.yml）
- 预检工具包 v1.0.0 发布（#317）+ 子包 tag 守卫防误触发
- 发布失败自动重试 + 告警 Issue 机制

## 主线二：CI 稳定性（持续）

- 并发 push 竞争修复：`pull --rebase + 重试`（#312）
- 性能断言 flaky 持续放宽（latency 0.5→2.5ms、parallel 启动差 10→50ms）
- Shard 拆分 / 线程监控（`can't start new thread` 根因分析）

## 主线三：核心功能

- Dynamic Few-shot 注入器替代 SFT 微调（Layer 2.5）
- ChromaDB 导入降级预检 + 技能索引缓存优化
- DST 省略句路由回写守卫 + 规则关键词外置 .env

---

## 关键提交选列

| Commit | 时间 | 说明 |
|---|---|---|
| `9871aa66` | 08-06 22:01 | GitLab 版增强发布日志 + GitHub Release 失败重试与幂等冲突处理 |
| `973ad292` | 08-06 22:00 | #312：看板更新 job 增加 pull --rebase + 重试，修复并发 push 竞争 |
| `34b99370` | 08-06 21:58 | 新增 GitLab CI 版自动发布工作流 |
| `57f5c0c7` | 08-06 20:24 | #317：预检工具包 v1.0.0 最终发布总结 + 操作复盘报告 |
| `d55abd03` | 08-06 13:00 | DST 省略句路由后回写守卫 |
| `51d6aa0d` | 08-06 13:14 | Dynamic Few-shot 注入器替代 SFT 微调 |
| `77534f66` | 08-06 01:28 | 放宽 test_parallel_execution 启动差断言 10ms→50ms |

---

## 观察结论

1. **发布链路集中**：8/06 提交高度集中于 release 自动化（三端 Release + 预检工具包 v1.0.0），对应 #317 系列。
2. **并发写回特征明显**：大量 `[skip ci]` 自动提交（CI 看板/依赖图），印证并发 push 竞争是当日核心问题，`pull --rebase + 重试`（#312）为其根治方案。
3. **性能断言持续放宽**：多个 flaky 断言（latency/parallel 启动差）逐步放宽，符合既有 CI 稳定性策略。
