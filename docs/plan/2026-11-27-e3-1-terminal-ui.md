# E3-1 侧栏终端（PTY）UI 设计与判据（2026-11-27）

> 模型侧已在 `28e76333c` 等提交交付（`agent/pty_session.py` + `terminal` 工具）；本文只覆盖**界面侧**。
> 上一轮明确"没有造无 UI 引用的路由"（口径 §11「别造路由债」）——本轮**先定消费者，再建路由**。

## 1. 谁消费（先定，后建路由）

右坞 `DockPanel` → `TerminalPane` 新增第三个子视图 **「交互」**（现有两个是「命令」「实时活动」）：

| 子视图 | 后端 | 语义 |
|---|---|---|
| 命令 | `/api/terminal/exec` | 白名单命令（不是 shell） |
| 实时活动 | `/api/terminal/activity` | 审计链尾部（只读） |
| **交互（新）** | `/api/terminal/sessions\|input\|output\|signal\|close` | **真 PTY 会话**（`agent/pty_session.py`） |

## 2. 路由（5 个归一化路径 / 6 条规则；全部被 `endpoints.ts` 常量引用）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/terminal/sessions` | 开关/后端/上限 + 会话列表（**开关关时也返回 200 + enabled=false**） |
| POST | `/api/terminal/sessions` | 开会话（command / cwd / rows / cols） |
| POST | `/api/terminal/input` | 写 stdin（默认补 `\r`，PTY 的 Enter） |
| GET | `/api/terminal/output` | 按 offset/limit 取增量输出（前端 800ms 轮询） |
| POST | `/api/terminal/signal` | interrupt（Ctrl-C）/ terminate |
| POST | `/api/terminal/close` | 关闭会话（顺手整树 kill） |

所有路由 `log=False`：**输入/输出原文不进 HTTP 访问日志**（可能是密钥）；审计仍由 `pty_session` 记摘要。

## 3. 失败码口径（响应体不回异常派生串）

`pty_session` 在失败结果里补稳定 `code`；路由层**丢掉 error 原文**（只进服务日志），只回 `code`：

| code | 含义 | 界面文案 |
|---|---|---|
| disabled | 开关 `CP_TERMINAL_ENABLED` 未开 | 终端未启用：设置中心打开 CP_TERMINAL_ENABLED |
| blocked | 权限预设为受限档（fail-closed） | 受限档拒绝：raw PTY 会绕过沙箱 |
| dependency | 缺 pywinpty | 缺少 pywinpty（pip install pywinpty） |
| max_sessions | 会话数到上限 | 会话已达上限，先关一个 |
| bad_cwd | 工作目录不存在 | 工作目录不存在或不可访问 |
| spawn_failed | 启动失败 | 终端启动失败（详见服务日志） |
| no_session | 会话不存在/已回收 | 会话不存在（可能已关闭/回收） |
| write_failed / bad_action | 写入失败 / 非法动作 | 写入失败 / 不支持的动作 |

域内失败一律 **HTTP 200 + ok=false + code**（`hubPost` 只在非 2xx 抛错，界面按 code 出文案）；
只有**缺参数**回 400（`code=missing_id`）。

## 4. 判据（做完逐条核对）

1. `python scripts/audit/route_ui_coverage.py` 重派生后 `--check` 绿；`pending_backlog` **不增**（新路由全部 ui 引用）。
2. 6 条路由都有 docstring（覆盖清单的「说明」列取首段）；`/api/terminal` 域前缀已存在（`route_domains.json` 不新增）。
3. 开关默认关时：GET sessions 200 + `enabled=false`；POST sessions 200 + `code=disabled`（**不 500、不 400**）。
4. 受限档：POST sessions 200 + `code=blocked`。
5. 会话不存在：input/output/signal/close 200 + `code=no_session`；缺 id 400。
6. 单测：`tests/unit/test_terminal_ui_routes.py`（上述 3–5 + 参数收敛 + 输入换行 + 输出 offset 透传）。
7. 前端：`DockPanel.test.tsx` 新增「交互」用例（未启用时的提示、打开会话、发送、轮询增量、Ctrl-C）。
8. `cd yunshu-ui && npm run ui:check` 绿；`python -m pytest tests/unit/test_terminal_tool.py tests/unit/test_terminal_ui_routes.py -q` 绿。

## 5. 明确不做

- **不打开 `CP_TERMINAL_ENABLED`**（B 级开关，Owner 拍板）+ 因此**不动** `engineering.yaml` 的 boost / max_tools；
- 不做 resize / 多标签 / 终端历史落盘（`pty_session` 现状即边界，会话只在进程内存）；
- 不引入 xterm.js（体积/依赖）：输出用等宽 `pre`、输入用单行 Input，够覆盖"看进度 + 发命令 + Ctrl-C"。
