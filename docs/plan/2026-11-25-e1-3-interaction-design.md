# E1-3 结构化提问 + 交付卡（设计） — 2026-11-25

> 状态：设计定稿，待实现（本轮按 §8 切片）。对齐 DSH `tool-ask-user` / `tool-present` /
> 变更文件卡；**复用**本仓既有的"审批边界 → SSE 事件 → 用户动作 → 原链路继续"模式。

## 1. 目标与验收

1. 模型能**结构化提问**（单选/多选/自定义），UI 内联成卡；用户回答后模型继续。
2. 模型能**声明交付文件**，回复下方成卡（文件名/路径/复制；工作区内文件可"在文件管理器中显示"）。
3. **变更文件卡**：一轮里改过哪些文件、逐文件 diff（复用 E0-3 检查点快照）。
4. 不阻塞、不假绿：无 UI 的调用方（cron/子代理/脚本）能拿到明确降级语义。

## 2. 关键取舍

| 问题 | 决策 | 理由 |
|---|---|---|
| ask 是否阻塞流式线程 | **不阻塞**：工具返回 `USER_INPUT_REQUIRED` ⇒ 提升为 SSE `ask` 事件 ⇒ **本轮结束**；用户回答后按"下一条用户消息"继续 | 与既有审批边界同一模式（工具结果 → SSE → 用户动作 → 原链路继续）；不占线程、不绕开"停止生成"/权限 contextvar/超时预算；也天然可审计 |
| 回答如何回到模型 | `POST /api/ask/answer` 记录答案 + 追加一条**普通用户消息**（"回答（问题 xxx）：…"），前端随后 `sendMessage(next_message)` | 复用同一对话链路；会话历史/记忆/审计一致；不造"服务端发起模型轮" |
| 待答问题落哪 | `data/ask/<sha256(sid)[:32]>/pending.json` + `answers.jsonl`（append-only） | 刷新/重启后 `GET /api/ask/pending` 能恢复卡片；答案留痕可复盘 |
| 无 UI 时 | 工具仍返回 `USER_INPUT_REQUIRED`，结果里带 guidance："无交互界面时请按默认继续，并在最终回复说明假设" | 不假装有人回答；模型有明确出路 |
| present 的文件校验 | **不做文件读取**：只做字符串校验（拒绝绝对路径、`..`、控制字符；长度/数量上限），路径原样回显 | present 是"声明"不是"打开"⇒ 没有文件系统 sink，天然规避 path-injection；"打开"交给既有 workspace reveal（仅目录、且只对工作区内文件） |
| 变更文件卡数据源 | E0-3 检查点（`agent/data/checkpoints/`）最近一次快照的逐文件 diff | 不新增采集逻辑；检查点已有去抖与上界 |
| 事件顺序 | `ask`/`present` 都在 `done` **之前** | 前端收到 done 即 break（与 plan/goal 同一契约） |

## 3. `ask_user_question` 工具

~~~json
{"question": "选择用哪种存储？",
 "options": [{"id": "sqlite", "label": "SQLite", "description": "单机、简单"},
             {"id": "postgres", "label": "PostgreSQL"}],
 "allow_multiple": false,
 "allow_custom": true,
 "context": "可选的补充说明（≤500 字）"}
~~~

返回（**非错误**，用 `error_code` 走既有提升通道）：

~~~json
{"ok": false, "error_code": "USER_INPUT_REQUIRED",
 "question_id": "q-<12hex>", "question": "...",
 "options": [...], "allow_multiple": false, "allow_custom": true,
 "guidance": "已向用户展示提问；本轮请结束并等待回答。无交互界面时按默认继续并说明假设。"}
~~~

- 参数校验：question 非空 ≤1000；options ≤8（id/label 必填，label ≤100）；`allow_custom` 默认 true；
- 问题落 `pending.json`（同会话只保留最近 1 条未答；新问题覆盖旧问题时旧问题标 `superseded`）。

## 4. `present` 工具

~~~json
{"files": [{"path": "docs/plan/x.md", "title": "设计页"}],
 "note": "本次交付：设计页 + 快照"}
~~~

返回：`{"ok": true, "presented": [...], "count": N}`；chat 循环识别工具结果里的 `presented`
⇒ SSE `{"type":"present","files":[...],"note":"..."}`。
说明：文件**不读取**、不计算大小；UI 只显示路径/标题并提供"复制路径"；
若文件在会话工作区内，再显示"在文件管理器中显示"（调既有 workspace reveal）。

## 5. 前端

- `QuestionCard`（`components/workbench/chat/QuestionCard.tsx`）：题干、选项（radio/checkbox）、
  "其他（自定义）"输入、提交；无待答问题时不渲染；提交后卡片变"已回答"并把自己从 store 清掉。
- `PresentCard`（同目录）：交付文件列表（标题 + 路径 + 复制 + 工作区文件 reveal），
  位于消息区末尾（不遮输入框）；新一轮开始时清空。
- `ChangeCard`（E1-3c 用）：变更文件列表 + 逐文件 diff（折叠），数据来自 `/api/changes`。
- store：`pendingAsk`、`presented` 两个槽；SSE `ask`/`present` 事件写入；切会话清空。
- 硬规则：只用 `components/ui` 原语（Button/Radio/Checkbox/Textarea/Card/Badge/Input）；
  标题字号写标签 `className`；0 新增 raw 控件（`ui:audit` 守）。

## 6. HTTP API（2 条，新增路由链）

- `GET  /api/ask/pending?session_id=` → 未答问题（无则 `{ok:true, pending:null}`）；
- `POST /api/ask/answer` `{session_id, question_id, answers[], custom}`（`@require_token`）
  → 记录答案 + 追加用户消息 + 返回 `next_message`（前端据此续跑）。
- `GET /api/changes?session_id=`（E1-3c）→ 最近一次检查点的 `{files:[{path, added, removed, diff}]}`。

## 7. 安全 / 边界

- 路径校验用**纯字符串**（`startswith(("/", "\\"))`、`".." in parts`、长度、数量），
  不做文件 IO ⇒ 不产生 CodeQL path-injection 面；
- `present` 不改变权限/审批/沙箱；`ask` 的答案作为普通用户消息进审计链；
- `ask` 的 pending 过期 24h（读取时惰性清理）；会话删除时一并清理；
- 明确不做（首版）：真正的阻塞等待、语音/富文本回答、跨会话提问、文件内容预览。

## 8. 切片（每片单独提交/部署/CI）

1. **E1-3a**：两个工具（ask_user_question + present，一次 5 处连锁）+ SSE 提升 +
   2 条 ask 路由 + QuestionCard/PresentCard + 单测/探针。
2. **E1-3b**：变更文件卡（`/api/changes` + ChangeCard + 干净 worktree 重派生 + 探针）。

## 9. 验收（实现后逐条可测）

1. 模型调用 `ask_user_question` → SSE `ask` 先于 done；卡片出现；本轮结束；
2. 提交答案 → `/api/ask/answer` 记录（pending 清空、answers.jsonl 追加、用户消息落盘）；
   `next_message` 续跑后模型能看到答案；
3. 刷新页面：`GET /api/ask/pending` 能恢复未答提问；
4. 多选/自定义答案原样记录；空回答被拒；
5. `present` → SSE `present` + 卡片列出文件；非法路径（绝对/`..`）被拒；数量上限生效；
6. `present` 不读取文件（无 IO），工作区外文件不显示"reveal"；
7. 变更文件卡：一轮修改后能列出逐文件 diff（与 E0-3 快照一致）；
8. 既有 12 条探针全 0、E1-1/E1-2 探针不回归；`ui:check` 全绿；CI 30/30；CodeQL 0。
