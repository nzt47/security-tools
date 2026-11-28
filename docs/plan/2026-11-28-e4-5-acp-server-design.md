# E4-5 第五批：ACP server（stdio-only）设计

> 2026-11-28。上游：`docs/plan/2026-11-28-e4-5-inspector-design.md` §7「ACP/SDK（server）」。
> 本批只做 **ACP v1 最小集 over stdio**：不开监听面、不新增服务端路由/工具、不改运行期行为。

## 1. 两个先定的事（本批的结论）

| 决策 | 结论 | 理由 |
|---|---|---|
| 传输 | **stdio-only** | 客户端（编辑器）自己拉起子进程，信任边界 = OS 进程；不开端口、不引入第二套令牌。HTTP 版要另定鉴权面（谁能连、令牌如何分发），本期不做 |
| 鉴权面 | **沿用既有 REST 令牌** | 脚本只从 `YUNSHU_API_TOKEN` / 仓库 `.env` 的 `FLASK_API_TOKEN` 读，永不打印；服务端审批/审计/限流全部照旧 |
| 「SDK」 | **不新建 SDK 包** | 对外能力 = REST + CLI（第二批）+ ACP（本批）；再发一个 SDK 包等于把同一套 REST 再包一层，维护面只增不减 |

## 2. 协议子集（ACP v1）

| 方法 | 方向 | 本实现 |
|---|---|---|
| `initialize` | C→A | 回 `protocolVersion: 1`、`agentCapabilities{loadSession:false, promptCapabilities{image/audio/embeddedContext:false}, mcpCapabilities{http/sse:false}}`、`agentInfo`、`authMethods: []` |
| `session/new` | C→A | `POST /api/sessions` 建会话（标题 `ACP · <cwd 目录名>`），回 `{sessionId}` |
| `session/prompt` | C→A | 取 `prompt` 里的 `text` 块 → `POST /api/chat`（带 `session_id`）→ 逐块发 `session/update`（`agent_message_chunk`）→ 回 `{stopReason:"end_turn"}` |
| `session/cancel` | C→A（通知） | 标记该会话取消；下一次/本轮结束回 `stopReason:"cancelled"` |

- 错误：坏 JSON `-32700`、非对象 `-32600`、未知方法 `-32601`、参数缺失 `-32602`、
  后端失败 `-32603`（只回脱敏短句）。**单条失败不影响连接**（后续请求照常处理）。
- 单条通知最大 4000 字符（`MAX_CHUNK_CHARS`），同一消息的块共用 `messageId`。
- 客户端能力（`clientCapabilities`）只做记录，不做分支：本实现不需要 fs/terminal 能力。

## 3. 开关与回滚

| 开关 | 默认 | 说明 |
|---|---|---|
| `YUNSHU_ACP_ENABLED` | 关 | 不为真时脚本**直接退出码 2** 并打一行说明；回滚 = 不启动 / 去掉该 env |

- 该 env 只被 `scripts/yunshu_acp.py` 读，不进 `agent/settings/registry.py`（那是**服务进程**的开关台账；
  与本批同族的 CLI 令牌 env 也不在其中）。
- 关掉它不影响服务端：脚本是**客户端**，删掉对线上零影响。

## 4. 边界（如实写死）

- **不**实现 `session/load`（无历史回放）、`session/resume`、`session/list`；
- **不**转发工具调用 / 不实现 `session/request_permission`（工具审批仍在云枢界面里发生）；
- **不**连接 `mcpServers`（参数接受但忽略；MCP 由云枢自己的 E4-3 通道负责）；
- **不**流式：`/api/chat` 是同步整轮，因此答案是**一次生成后分块发出**（不是边跑边发）；
- 取消是**协作式**的：`/api/chat` 已在跑时无法中断后端，只在轮次边界返回 `cancelled`；
- 会话 id 直接复用云枢 `sess_*`（ACP 里它只是不透明串）。

## 5. 判据

1. `initialize` 形状与能力全 false；请求更高版本时回 1；
2. `session/new` 标题带 cwd 目录名、回 `sessionId`；
3. `session/prompt`：先 `session/update` 再 `{stopReason:"end_turn"}`；多 text 块拼接、非 text 忽略；
   长回答分块且 `messageId` 一致；
4. 坏 JSON / 非对象 / 未知方法 / 缺参数各回对应错误码，**连接不断**；
5. 后端失败回 `-32603` 且不含 traceback；`session/cancel` 后 `stopReason:"cancelled"` 且不调后端；
6. 未启用时 `main()` 退出码 2；启用后能用 stdin/stdout 完成一次 `initialize`；
7. `RestTransport` 的请求路径/载荷/令牌与 CLI 同源（`/api/sessions`、`/api/chat`、`token_from_env`）。
