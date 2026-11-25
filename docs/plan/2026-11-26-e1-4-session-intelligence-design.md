# E1-4 会话智能（设计） — 2026-11-26

> 状态：**设计稿（未实现）**。对齐 DSH `session-query-sqlite`（FTS5）、`session-title-*`、
> `session-turn-outline`、`session-stats`、`session-log-export`。
> 本文只定**口径与切片**；实现按 §7 切片逐片提交/部署/CI。

## 1. 目标与验收

1. **会话标题**：2 轮之后自动起一个短标题（LLM，异步、失败可回退首条用户消息）；人工改名后**不再自动覆盖**。
2. **搜索**：按关键词搜到历史会话并**跳到那一轮**（FTS5 索引 + `/api/session-search` + 会话列表搜索框）。
3. **turn outline**：一条会话按轮列出「用户说了什么 / 调了哪些工具 / 结论一句」，用于快速回看。
4. **每轮 stats**：tokens（进/出）、工具数、耗时，落到会话详情抽屉。
5. **导出**：Markdown（可读）/ ZIP（`messages.jsonl` + `manifest.json` 工作区快照）。
6. **模型侧工具**：`session_search`（找回自己以前说过什么）、`session_trace`（某轮的工具调用轨迹）。

**验收（逐条可测）**：
① 会话跑满 2 轮后标题自动出现，且 `<首条消息>` 回退可用；② 搜 "检查点" 能命中 E0-3 那一轮并跳转；
③ outline 与 stats 与 `messages.jsonl` 对得上（同一份数据，不重复采集）；④ 导出文件可读、
文件名不含会话原始字符；⑤ `session_search`/`session_trace` 在模型可下发工具集里且真机能调通；
⑥ 既有 12 条探针 + E1-1/E1-2/E1-3 探针不回归；`ui:check` 全绿；CI 30/30；CodeQL 0。

## 2. 关键取舍

| 问题 | 决策 | 理由 |
|---|---|---|
| 中文全文检索 | SQLite **FTS5 `tokenize='trigram'`**（≥3 字），<3 字回落 `LIKE '%%'`（带 `LIMIT`） | `unicode61` 不切中文；trigram 对 CJK 子串检索可用且**无新依赖**（不引 jieba） |
| 索引写在哪 | `data/session_index.db`（**运行期状态、不入库**），表 `messages(session_id, seq, role, content, ts)` + `messages_fts` | 与会话存储同源；删索引可全量重建，不丢事实 |
| 索引何时写 | `SessionManager.add_message()` 提交后**同进程**写一行（失败只 WARN，不阻塞对话）；另给 `scripts/rebuild_session_index.py` 全量重建 | 不新增采集链路；失败降级为"搜不到该轮"而不是"存不进消息" |
| 标题生成 | 后台线程 + 8s 超时 + `thinking: disabled`，只写 `title_auto`；`title_locked`（人工改名）永不被覆盖 | 与 `llm_monitor`/压缩同一个"旁路不阻塞"纪律；LLM 不可用时回退首条消息 |
| 现有 `title` 字段 | 复用；新增 `title_source`（`auto`/`manual`/`fallback`）与 `title_at` | 不改老数据语义：缺字段按 `manual` 处理（不覆盖） |
| outline / stats 的数 | **全部从 `messages.jsonl` 现算**（懒加载 + 上限 200 轮） | 不新增第二份真相；轮次边界用 `round_id`（E1-2 起驱动已有） |
| 导出 | 服务端**流式**生成（`send_file`/`Response`），文件名 `<sha256(sid)[:12]>.md/.zip` | 会话 ID 来自客户端 ⇒ 文件名必须派生、不能直接用；正则捕获后再进路径 |
| 搜索 UI | 会话列表顶部搜索框（既有组件）+ 命中高亮 + 点击跳轮 | 不新增叶子页（`ui_reachability` 不漂移） |
| 模型工具 | `session_search`/`session_trace` 是**任务工具**（进检索索引），**不是** META 交互原语 | 与 ask/present 不同：它们不是交互面，参与路由不会污染 top1（新工具按 5 处连锁派生） |

## 3. HTTP API（4 条）

- `GET /api/session-search?q=&limit=20` → `{ok, hits:[{session_id, title, seq, role, snippet, ts}], took_ms}`
  （`q` 长度 1..200；`limit` 1..50；SQL 参数化，**不拼串**）
- `GET /api/sessions/<session_id>/outline` → `{ok, rounds:[{round_id, user, tools:[name], conclusion, ts}]}`
- `GET /api/sessions/<session_id>/stats` → `{ok, rounds:[{round_id, tokens_in, tokens_out, tools, ms}], totals:{...}}`
- `GET /api/sessions/<session_id>/export?format=md|zip` → 文件流（`Content-Disposition` 用派生名）

新增路由的收尾动作（每条都要）：`config/route_domains.json`（对话与会话）+ `config/route_ui_intent.json`
（`should_be_in_ui`，写消费方组件）+ **干净 worktree 重派生** `reports/route_ui_coverage.json`
与 `reports/ui_reachability.json`。

## 4. 模型工具（2 个）

~~~json
{"name": "session_search", "arguments": {"query": "检查点", "limit": 5}}
{"name": "session_trace", "arguments": {"session_id": "sess_...", "round": 3}}
~~~

- `session_search`：只搜**本进程可读**的会话索引，返回 `{ok, hits:[{session_id, seq, snippet}]}`；
  `limit` 上限 10；结果按 `seq` 升序截断，不返回全文（防把上下文灌爆）。
- `session_trace`：给 `round` 时返回该轮工具调用名/参数摘要/结果摘要（各 200 字），不给就返回最近 3 轮。
- 两者都是 `effect=read`、`permission_level=public`（只读本地台账，与 ask/present 同一治理口径）。
- 新增工具 = **5 处连锁**：YAML + `@_tools.register`（描述逐字一致）+ `tool_index` + 能力清单/盘点表 +
  BM25 半饱和点/路由基线/路由冲突 τ + `data/agent_lines/*.yaml` 名额；改完在**干净 worktree** 全量重派生。

## 5. 前端

- 会话列表：搜索框（输入 300ms 防抖）→ 命中项带 `snippet` 高亮与"第 N 轮"；点击 = 切会话 + 滚到该轮。
- 会话详情抽屉（已有）新增两个 tab：**大纲**（outline）/ **统计**（stats），都用 `components/ui` 原语
  （`Table`/`Badge`/`Empty`），标题字号写标签 `className`；0 新增 raw 控件（`ui:audit` 守）。
- 导出按钮放会话详情抽屉头部（两个格式二选一的下拉），下载走 `a[download]`。
- 标题：会话列表项显示 `title`；`title_source=auto` 时加一个极小的「AI」角标（用 `Badge`）。

## 6. 安全 / 边界

- **路径**：会话 ID 只经 `sha256(sid)[:32]` 派生（与 `data/ask`/`data/goals` 同口径）+ 正则捕获后再进路径；
  导出文件名用 `sha256(sid)[:12]`，**不拼原始 ID**；`os.listdir` 不拼路径。
- **SQL**：FTS 查询全部参数化；`MATCH` 的查询串先做引号转义与长度上限；`LIKE` 回落必须带 `LIMIT`。
- **异常**：路由回固定文案 + 日志（`jsonify(error=exc.message)` 或 `_safe()`），不把异常原文塞响应体。
- **数据**：索引与导出都是**读**；不新增写外部系统的路径。`data/session_index.db` 属运行期状态，不提交。
- **明确不做（首版）**：跨设备同步、语义（向量）搜索、会话合并、导出中的附件二进制内嵌。

## 7. 切片（每片单独提交/部署/CI）

1. **E1-4a 标题**：`title_source/title_at` 字段 + 后台生成 + 回退 + 人工锁定；`PUT /api/sessions/<id>/title`；
   前端「AI」角标。测试：生成/回退/不覆盖人工标题/LLM 超时。
2. **E1-4b 搜索**：FTS5 索引 + `add_message` 写入 + 重建脚本 + `GET /api/session-search` + 列表搜索框；
   探针：中文子串/短查询回落/空查询/上限。**不动工具**（不触发工具派生）。
3. **E1-4c outline + stats + 导出**：3 条路由 + 抽屉两个 tab + 导出按钮；探针：轮次边界、
   导出可读、文件名派生。
4. **E1-4d 模型工具**：`session_search` + `session_trace`（**5 处连锁 + 干净 worktree 全量重派生**：
   tool_index 99→101、能力清单 150→152、BM25 97→99、S0/路由基线/冲突 τ/名额），真机探针。

## 8. 验收清单（实现后逐条跑）

1. `python -X utf8 .local/verify_e1_4a_title.py`（标题自动/回退/不覆盖/超时）；
2. `python -X utf8 .local/verify_e1_4b_search.py`（命中/排序/短查询/取消）；
3. `python -X utf8 .local/verify_e1_4c_outline_stats_export.py`（与 `messages.jsonl` 对账、导出可读）；
4. `python -X utf8 .local/verify_e1_4d_tools.py`（两工具在模型可下发集里 + 真机调用）；
5. UI 探针各 1 条（搜索框命中跳转、抽屉两 tab、导出下载）+ 截图存 `.ui_shots/`；
6. 既有 17 条探针全 0 + E1-1/E1-2/E1-3 探针不回归；`ui:check` 全绿；CI 30/30；CodeQL 0。

## 9. 回滚

- 搜索：`CP_SESSION_SEARCH_ENABLED=0` ⇒ 路由返回 `{ok:false, code:"disabled"}`、UI 隐藏搜索框；索引表互相独立，可整库删。
- 标题：`CP_SESSION_TITLE_ENABLED=0` ⇒ 只回退首条消息，不调 LLM。
- 工具：YAML `enabled: false` + 干净 worktree 重派生（与其它工具同一回滚路径）。
- 导出：路由层直接 404（无状态、无迁移）。
