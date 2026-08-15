# Changelog — 任务 7 第 4 轮加固（2026-08-15）

> 范围：沙箱拦截日志埋点 + caller 定位 bug 修复、监控循环终止事件接线、人工接管状态流转测试
> 分支：develop（快照 + detached worktree 隔离提交，cherry-pick 入库）
> 规模：3 个代码文件
>
> 提交状态（2026-08-15 09:58 已入库）：`11a3f0a4`（恢复任务 7 底座，9 文件）+ `bcecdd96`（第 4 轮改动，4 文件）已 cherry-pick 到 develop。

## 背景

承接任务 7（安全护栏与人工接管）第 3 轮验收后的三项加固需求：

1. **状态流转完整性**：构造模拟场景，验证告警升级 → 人工接管队列状态机完整流转（open → assigned → resolved）
2. **误拦截可排查性**：沙箱执行器增加详细日志埋点，记录每次命令被拦截的具体原因和调用栈
3. **任务 5/6 事件覆盖核查**：任务文档（`docs/zh/自我修复机制重构计划/tasks/任务7_安全护栏与人工接管.md` L42）要求监控循环终止事件接入升级链路

## 变更明细

### 修改（3 文件）

| 文件 | 改动 |
|---|---|
| `agent/subagent/sandbox.py` | ① 新增 `_log_intercept` 拦截日志 helper：输出 stage / subject / reason / matched_pattern / caller 五要素；② `validate_command` 3 处埋点（cmd_permission / cmd_empty / cmd_dangerous）+ `validate_network` 4 处埋点（network_scheme / network_no_host / network_parse_error / network_write）+ network_method 共 8 处；③ `run_sandboxed` 超时/失败日志；④ 修复 caller 定位 bug（见下） |
| `agent/monitoring/alert_manager.py` | 新增 `notify_loop_terminated`：任务 5 循环终止事件接线——监控循环异常终止 → 构造 FIRING 告警 → `escalate()` 升级（WARNING→CRITICAL）+ 人工接管入队。任务 6 的 `record_deprecated`（P2 通知不升级）已存在，本轮确认覆盖，未改动 |
| `tests/integration/test_task7_alert_escalation.py` | 新增 2 用例：`test_escalation_takeover_full_state_flow`（escalate 创建 open → assign 转 assigned → resolve 转 resolved 全流程断言）、`test_loop_terminated_escalates_to_takeover`（循环终止 → 升级 → 接管入队） |

## 关键 Bug 修复：沙箱拦截日志 caller 恒为 unknown

**【现象】** 4 类拦截日志只输出部分，且 `caller=unknown`——调用栈信息完全丢失。

**【根因】** [sandbox.py](file:///c:/Users/Administrator/agent/agent/subagent/sandbox.py) 的 import 列表中**没有 `import traceback`**，`_log_intercept` 内调用 `traceback.extract_stack` 抛 `NameError`，被 helper 内的 `try/except` 兜底吞掉 → 每次拦截都走 `caller = "unknown"` 分支。排查时 monkey-patch `traceback.extract_stack` 无效也印证了模块作用域根本不存在 `traceback` 名字。

**【修复】** 弃用 `traceback.extract_stack`，改用 `sys._getframe` 从当前帧逐帧上溯（新增 `import sys` 一行）：从 `_getframe(1).f_back` 起跳过 helper 自身帧，取最近 2 个调用帧，输出 `文件:行号(函数)` 链。顺带修正了原 `extract_stack(limit=3)[:-1]` 会把 helper 自身帧算入调用链的缺陷。

**【验证】** 5 类拦截场景实测均输出完整日志，例如：

```
stage=cmd_dangerous subject='DROP TABLE users;' reason=危险命令被拦截: SQL DROP TABLE 破坏数据库
matched_pattern=\bdrop\s+table\b caller=sandbox.py:436(run_sandboxed) <- <stdin>:20(<module>)
```

## 验证记录

| 项 | 结果 |
|---|---|
| 回归：sandbox + task7 集成 + singleton + takeover + permission + hitl（10 个测试文件） | ✅ **139 passed / 3 skipped（Unix-only）/ 0 failed**（92.4s，`-p no:randomly`） |
| 拦截日志冒烟（6 类场景：cmd_dangerous / cmd_empty / network_write / network_scheme / cmd_permission / run_sandboxed 全链路） | ✅ 均输出 stage/subject/reason/matched_pattern/caller |
| caller 调用栈 | ✅ 直接调用显示 `<module>` 帧；经 `run_sandboxed` 显示 `run_sandboxed <- 调用者` 两层链 |
| 状态流转 open→assigned→resolved | ✅ 新测试断言通过 |
| 任务 5 `loop_terminated` → 升级 | ✅ `notify_loop_terminated` 落地 + 测试覆盖 |
| 任务 6 `deprecated` → P2 通知 | ✅ 既有 `record_deprecated`，确认覆盖 |

## 注意事项

1. `caller` 展示最近 2 个调用帧：直接调用校验方法时仅 1 帧（`<module>`），经 `run_sandboxed` 时 2 帧。日志定位到调用入口即可溯源。
2. `notify_loop_terminated` 是入口方法，由监控循环侧（任务 5 实现）在循环异常终止时触发；本模块只保证"终止 → 升级 → 接管"链路完整。
3. 埋点日志走原生 `logger.warning` 字符串格式；与项目 `agent/utils/logging.py` 的 `log_dict` 结构化格式不同，后续如需统一可另行调整（当前以可读性优先）。
4. 本 Changelog 仅覆盖第 4 轮改动；第 2-3 轮改动（escalate 日志锚点、危险命令拦截测试等）已入库 commit `0d3a87ef`。
