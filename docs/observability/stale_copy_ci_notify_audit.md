# security-tools 旧副本 ci-failure-notify.yml 隐患排查报告

## 背景

`agent\security-tools\` 是 nzt47/security-tools 的独立旧 clone（gitignore 已忽略，
不参与当前 CI）。排查发现其中 `ci-failure-notify.yml` 仍停留在修复前旧版，
除已知的失效 action 外，还残留以下隐患，均已在当前仓库 `agent\.github\workflows\` 修复。

## 隐患排查清单

| # | 隐患 | 旧副本现状 | 当前仓库修复版 | 影响 |
|---|------|-----------|--------------|------|
| 1 | **失效 action 引用** | `uses: visiblelabs/dingtalk-action@v1`（L47），仓库不存在 | 改用自维护 `observability_dingtalk_notify.py` | job 在 Set up job 阶段直接失败 |
| 2 | **Secret 拼写错误** | `DINGTANG_WEBHOOK`（L29/L46/L49），拼写错误 | 统一为 `DINGTALK_WEBHOOK` | 引用的 secret 从未配置，通知静默失效 |
| 3 | **无 workflow_dispatch 手动触发** | 仅 `workflow_run` 触发 | 增加 `workflow_dispatch` + `simulate_failure` 输入 | 无法手动验证通知链路 |
| 4 | **手动触发无兜底值** | prep step 直接用 `github.event.workflow_run.xxx` | 全部 `\|\| '默认值'` 兜底 | workflow_run 为 null（手动触发）时 step 崩溃 |
| 5 | **job if 不兼容手动触发** | `if: conclusion == 'failure'` | `if: (workflow_run != null && conclusion=='failure') \|\| inputs.simulate_failure` | 手动模拟失败无法进入 notify job |
| 6 | **缺 Docker 扫描恢复通知** | 无 recover job | 新增 `docker-scan-recover-notify` job | 失败→恢复无通知 |
| 7 | **监控 workflow 列表不全** | 仅 5 个 workflow | 补充 `关键字参数冲突扫描 (Docker)` | Docker 扫描失败不触发通知 |
| 8 | **通知文本为 markdown 硬编码** | msgtype: markdown + 手写文本 | 自维护脚本统一参数化（status/workflow/branch/commit/actor/message） | 文本维护分散，易漂移 |

## 根因分析

旧副本停留在 CHG-2026-0801 修复**之前**的版本（该次修复解决了 #1/#2/#5，
后续轮次补齐 #3/#4/#6/#7/#8）。副本长期未同步 `git pull`，且 gitignore 静默忽略
导致无人察觉。

## 处置建议

- 该副本为独立 clone，若仍被使用（如作为 hook 源仓库）应 `git pull` 同步最新修复；
- 若仅为历史残留，可删除释放空间；
- 后续可考虑在扫描脚本（`simulate_ci_failure_notify.py` 预检）中扩展
  `security-tools\` 副本路径检查，避免此类静默漂移。

## 排查方法（可复用）

1. `git status` + `git log` 判断副本是否落后
2. 对比新旧 yml：`diff <旧副本> <当前版>`
3. 逐项核对：失效 action / secret 名 / 触发方式 / null 兜底 / job if / job 完整性
