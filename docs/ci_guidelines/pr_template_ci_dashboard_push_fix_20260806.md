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

## Changes

- `.github/workflows/ci.yml`：`update-ci-dashboard` job 的"提交并推送看板更新"步骤
  增加 pull --rebase + 3 次重试逻辑

## Validation

- [x] 本地模拟：双工作副本模拟并发推送竞争场景，重试机制成功收敛（见
      `docs/troubleshooting/ci_failures_tracking_20260806.md` §2）
- [x] bash 语法校验通过
- [ ] CI 实测：合并后观察下一次 push 的 `update-ci-dashboard` job 结论

## 关联

- 问题跟踪单：`docs/troubleshooting/ci_failures_tracking_20260806.md`（#2）
- 失败 run：云枢系统测试流程 31072885508（job `update-ci-dashboard`）
