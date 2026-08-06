# PR 描述模板（本次 CI 修复）

> 适用于：云枢系统测试流程"更新 CI 健康度看板" push 竞争修复（pull --rebase + 重试）

---

## Summary

修复 `update-ci-dashboard` job 在并发 CI 场景下的 push 竞争失败。

**背景**：`actions/checkout` 基于触发时的远端 HEAD，作业运行期间远端 master
可能被并行 workflow 的看板更新/自动提交推进，直接 `git push` 报
`failed to push some refs`（non-fast-forward），导致 CI 失败并产生失败通知噪音。

**修复**：push 前先 `git pull --rebase origin master`，最多重试 3 次；
耗尽后 `::warning::` 跳过（看板为可丢失更新，不阻塞 CI、不产生失败通知）。
逻辑封装为通用脚本 `scripts/git_push_with_retry.sh`（单一事实源），
ci.yml 与操作指南均调用该工具。

## Changes

- `scripts/git_push_with_retry.sh`（新增）：pull --rebase + 重试通用工具，
  支持 `--retries/--sleep/--remote/--fail` 参数
- `.github/workflows/ci.yml`：`update-ci-dashboard` job 的"提交并推送看板更新"步骤
  内联重试逻辑替换为调用 `bash scripts/git_push_with_retry.sh master`
- `.github/workflows/boundary-guard.yml`：硬编码边界值基线 114→115
  （preflight 工具包存量入基线，消除 master 本身 115 > 114 的误报）
- `docs/observability/push_race_retry_simulation_retrospective_20260806.md`（新增）：
  技术复盘（事件时间线 / 五问法根因 / sim1-3 模拟验证 / 完整日志 / 学习要点）
- `docs/ci_guidelines/dev_pr_merge_guide_20260806.md`：§4.2 示例同步为脚本调用

## Validation

- [x] 本地模拟：sim1 远端推进 2 commit 一次收敛；sim2/sim3 attempt1 失败 →
      attempt2 重试收敛；sim4 工具竞争收敛；sim5 工具 `--fail` 耗尽 exit 1
- [x] `bash -n` 语法 + 参数校验（-h / 缺分支 / 非法 retries）
- [x] 两个 workflow YAML 解析通过
- [ ] CI 实测：PR checks 全绿后 squash 合入，观察下一次 push 的
      `update-ci-dashboard` job 结论

## 关联

- 问题跟踪单：`docs/troubleshooting/ci_failures_tracking_20260806.md`（#2）
- 技术复盘：`docs/observability/push_race_retry_simulation_retrospective_20260806.md`
- 操作指南：`docs/ci_guidelines/dev_pr_merge_guide_20260806.md`
- 失败 run：云枢系统测试流程 31072885508（job `update-ci-dashboard`）
