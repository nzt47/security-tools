# 云枢 × DSH 能力对齐路线图（2026-11-24）

> 决策：**DSH 有、云枢没有或弱于 DSH 的能力全部补齐；云枢强于 DSH 的（审计链、知识库、
> 摄入、单位看板、UI 规范守卫、插件签名/AppContainer、中文多引擎搜索、角色/审批、技能双轨、
> 调度广度）保留现状，不为了"对齐"而降级。**
>
> 本文是程序级总规划；每个 Epic 落地时仍按仓库纪律单独提交、单独部署、单独 CI，
> 并在 `data/handoff_next.json` 维护机器可读状态。DSH 参照版本：0.2.2-rc.5
> （`resources/app.asar/dsh/node_modules/@deepseek-ai/`，约 330 个内置插件包）。

## 0. 原则

1. **一个 Epic 一次提交**：可独立回滚；禁止"大爆炸"合并。
2. **先地基后能力**：安全（沙箱/权限）、上下文工程、会话检查点先做，后面所有能力都受益。
3. **每 Epic 必须给"可观测验收"**：真机探针 + 单测 + CI 绿 + 部署三处一致，不接受"代码写完"。
4. **新增能力面按 5 处连锁**：tool_index / 能力清单+盘点表 / BM25 S0+产物 / 路由基线 /
   路由冲突 τ；只加契约字段不触发（已实测）。
5. **UI 一律按 `docs/ui/封装规范.md`**；新页面/叶子按 `docs/ui/导航重组方案.md` 登记深链。
6. **DSH 只作参照，不照搬**：DSH 的插件是 Cordis/TS 体系，云枢是 Flask+React；
   对齐的是**能力语义与验收**，不是实现栈。

## 1. 基线（2026-11-24 18:38 交接卡）

- master `8be4ea434`（result_schema 96/101 收口）；backend/frontend 同 commit；
  bundle `index-D8CIquwu.js`；pid 25148。
- 工具 101（停用 6）；技能文件轨 54；调度 27/27；单位看板 18 ok / 100.0；
  审计 88176 条 / 0 断点；覆盖未覆盖 0；code scanning 0 / dependabot 0。
- CI：ci.yml run 45372034883（8be4ea434）30/30 success。
- open 项 0；frozen 11（含插件 UI iframe、路由硬超时）。

## 2. 差距总表

| # | 能力 | DSH 参照 | 云枢现状 | 等级 | 估时 |
|---|---|---|---|---|---|
| 1 | Office 三件套 | `tool-office`（office_render/convert/recalculate）+ `skill-office` + LibreOfficeKit | 仅 PDF；docx/xlsx/pptx 零支持 | 完全缺 | L |
| 2 | 持久交互终端 | `tool-terminal` 六工具 + `terminal-bash` PTY + 侧栏 | 只有白名单 argv 执行 | 完全缺 | L |
| 3 | 计划模式 | `plan-mode`（/plan、exit_plan_mode、计划卡） | 只有 todo_write | 完全缺 | M |
| 4 | Goal | `goal` + `goal-round-driver` + `tool-goal` + GoalBar | 无 | 完全缺 | L |
| 5 | Agent Team | `agent-team` + `tool-agent-team` + 任务板 UI | delegate/fan_out 一次性 | 形态不同 | L |
| 6 | 子代理控制 | `tool-subagent-control`（send_message/interrupt/list） | 无运行中通信/打断 | 完全缺 | M |
| 7 | Fork 子代理 | `subagent-fork-in-process`（父日志前缀） | 只有全新 spawn | 完全缺 | M |
| 8 | 会话智能 | 标题（LLM）/FTS 搜索/session_query/outline/stats/导出 | 无搜索/标题/大纲/统计/导出 | 完全缺 | M-L |
| 9 | 交付与引用 | `tool-present`、变更文件卡、@file/@session、附件、引用、逐条 feedback | 均无 | 完全缺 | M |
| 10 | 上下文工程 | token-meter、compaction 策略、tool-result pruner、image offload、spill+locator | 摘要压缩 + 硬截断 | 部分 | M-L |
| 11 | 程序化编排 | `tool-workflow`（JS）、`ptc-runtime` | 模板工作流；run_sandbox 仅纯计算 | 完全缺 | L |
| 12 | Ralph 循环 | `tool-ralph` | 无 | 完全缺 | S-M |
| 13 | 结构化提问 | `tool-ask-user` | 无 | 完全缺 | S |
| 14 | OS 级沙箱 + 权限预设 | `sandbox-windows-acl`/`sandbox-local` + `permission-presets` | shell 沙箱默认 shadow；fs 无 OS 约束；插件才有 AppContainer | 部分 | L |
| 15 | 插件市场/索引/推荐 | `plugin-market` + index + recommendations + manager | 市场搜索 + GitHub 安装 | 部分 | M |
| 16 | MCP resources/prompts | `mcp-resources` | 只有 tools | 部分 | S-M |
| 17 | 平台集成 | ACP/SDK/headless、LiveShare、Inspector、worktree/forge、chat import、CoT 翻译、语音输入 | 仅 REST | 多数缺 | XL（分批） |
| 18 | 会话检查点/变更快照 | `session-checkpoint-policy`、`workspace-changes` | 审计 + undo hint | 部分 | M |
| 19 | 工具治理小件 | repeat-tool-reminder、tool-call-timeout-policy、time-context | 部分零散 | 部分 | S |

## 3. 路线图（按依赖排序）

### Phase 0 — 地基（建议第 1–3 周）

#### E0-1 权限预设 + OS 级沙箱（P0）
- **DSH 参照**：`permission-presets`（一个 Permissions 选择同时写 sandbox-mode 与 approval-policy）、
  `sandbox-policy`、`sandbox-windows-acl`（restricted token + capability SID 写允许表）、
  `fs-sandbox`、`bash-sandbox`。
- **云枢现状**：`CP_SANDBOX_SHELL_MODE=off|shadow|enforce`（默认 shadow 只审计；
  enforce 是危险命令模式匹配 + 资源限制）；文件工具无 OS 约束；插件已有 AppContainer。
- **交付**：① 统一"权限预设"（只读 / 工作区写 / 全权 + 审批策略），落到设置中心与
  `CP_PERMISSION_PRESET`；② shell/文件工具接入 OS 级写限制（Windows 优先：受限令牌 +
  可写路径 ACL；非 Windows 走 bwrap/seatbelt 探测，不可用则 fail-closed 提示）；
  ③ 审计记录 enforcement 结果（denied/enforced）。
- **连锁**：新增 env → `settings/registry.py`；改工具实现体 → 干净 worktree 重派生能力清单；
  新增设置项 → 设置中心 UI/契约测试。
- **验收**：预设切换后 shell/fs 的写越界被 OS 拒绝（探针）；只读档下 `write_file` 必失败；
  CI 全绿；不改变插件 AppContainer 现有语义。
- **交付状态（2026-11-24）**：✅ 已交付（896d9f016 + 769a1217a）。
  设计/边界/实测偏差见 `docs/plan/2026-11-24-e0-1-permission-preset-design.md`；
  默认 `full-access`（不改现网/CI 行为），生产切 `workspace-write` 前跑 `scripts/sandbox_grant.py` 预热。
  真机探针 `.local/verify_e0_1_permission_preset.py` 16/16；遗留项在 `data/handoff_next.json`
  的 `dsh-parity-e0-1-followups`（受限档 bash / decompress 围栏 / Low IL / 生产切档）。

#### E0-2 上下文工程（P0）
- **DSH 参照**：`token-meter`、`compaction-basic`、`compaction-tool-result-pruner`、
  `compaction-image-offload`、`spill`/`spill-policy`。
- **云枢现状**：`/api/context/status` 有占用率；`MemoryManager.compress_now()` 摘要压缩；
  工具结果`_truncate_output` 硬截断（丢了就没了）。
- **交付**：① 大输出 spill：超过阈值写 `data/spill/<session>/...`，工具结果回
  `{spilled:true, locator, preview}`，新增 `read_spill` 工具；② 工具结果裁剪策略
  （头/中/尾保留、可回放）；③ 图片/截图类结果 offload 为占位 + 可再读；④ token-meter
  驱动压缩（按真实 token 而非消息条数）。
- **连锁**：新增工具（read_spill）= 5 处连锁；改动工具结果形状 → 注册表/契约测试同步。
- **验收**：超大输出不再丢内容（可按 locator 取回）；压缩后仍可回答细节；token 统计与
  `/api/context/status` 同源。
- **交付状态（2026-11-24，部分）**：① 大输出 spill + `read_spill` ✅（80360b2e3；
  设计/边界见 `docs/plan/2026-11-24-e0-2-context-spill-design.md`；真机探针 8/8，工具 102）。
  ② pruner / ③ 图片 offload / ④ token-meter 压缩**未做**，登记在 `data/handoff_next.json`
  的 `dsh-parity-e0-2-remaining`（顺序 ②→④→③）。

#### E0-3 会话检查点 + 变更快照（P0）
- **DSH 参照**：`session-checkpoint-policy`（模型请求/工具副作用前语义检查点）、
  `workspace-changes`（每轮 git 快照 + 逐文件比较）。
- **云枢现状**：审计链 + 工具 undo_hint；没有会话级检查点，没有"本轮改了哪些文件"。
- **交付**：① 会话检查点（事件级、可回放/对比）；② 每轮 workspace 变更快照
  （git 工作区 diff + 整文件捕获）→ 为 E1-3 的变更文件卡供数据。
- **验收**：一次多文件修改后可列出逐文件 diff；检查点可恢复（至少只读对比）。
- **交付状态（2026-11-25）**：✅ 已交付（3e5f4f887）。
  设计/边界见 `docs/plan/2026-11-25-e0-3-checkpoint-design.md`；会改工作区的工具成功后由
  `tools.call` 后台抓快照（去抖 120s、只收源码类改动、50 文件/1MiB 上界），
  `scripts/checkpoint_cli.py list|show|diff` 可逐文件对照；真机探针 9/9，单测 20。
  **本轮有意不加 HTTP/UI**（变更文件卡的交互属 E1-3，避免现在造无 UI 引用的路由债）。

#### E0-4 工具治理小件（P0，可并行）
- repeat-tool 提醒（同一工具同参重复 N 次告警）、通用工具超时（`exec.signal` 式）、
  time-context（每步时间/耗时）。均为 S 级，独立测试。
- **交付状态（2026-11-24，部分）**：① repeat-tool 提醒 ✅（b0de31b43；
  设计见 `docs/plan/2026-11-24-e0-4-tool-governance-design.md`；探针 7/7，单测 17）。
  ② 通用工具超时**本仓早已存在**（`agent/timeout_budget.py` 分层上界 + run 级 deadline），
  缺的是协作式取消；③ time-context **未做**（塞进工具结果会打破整字典相等断言，实测 7 处红，
  需先定消息通道注入点）。②③ 均登记在 `data/handoff_next.json::dsh-parity-e0-4-remaining`。

### Phase 1 — Agent 工作方式（建议第 4–6 周）

#### E1-1 计划模式（P1）
- **DSH 参照**：`plan-mode`（`/plan`、`exit_plan_mode`、计划卡、用户批准/反馈）。
- **交付**：会话级 plan_mode 状态；`enter_plan_mode`/`exit_plan_mode` 工具或命令；
  计划以 Markdown 落 `data/plans/<session>.md`；UI 计划卡 + 批准/反馈按钮；
  计划模式下仍可用全部工具（与 DSH 一致），靠权限预设约束。
- **验收**：进入计划模式→产出计划→用户批准→执行；拒绝后继续规划；会话恢复后状态保留。
- **交付状态（2026-11-25）**：✅ 已交付（c1d1eb451）。设计/边界/实测偏差见
  `docs/plan/2026-11-25-e1-1-plan-mode-design.md`；实现为**命令 + 计划模式回复即计划版本**
  （不新增模型工具，避免 5 处连锁与"模型漏调用工具"）；计划落
  `data/plan_mode/<session>/state.json + plan.md + vN.md`（**偏差**：路线图写的 `data/plans/`
  是 planning 引擎的领域，混放会污染）；计划卡嵌会话页（不新增叶子，深链不变）；
  `/api/plan-mode/{state,mode,decision}` 三条路由已登记 route_ui_intent + route_domains。
  真机探针 `.local/verify_e1_1_plan_mode.py` 13/13；单测 29+10；前端 202 files / 1546 tests；
  ui:check 全绿；0 新增 raw 控件。

#### E1-2 Goal（P1）
- **DSH 参照**：`goal` + `goal-round-driver` + `tool-goal`（create/edit/pause/resume/
  complete/block/clear，每会话单目标，Round 上限默认 256）+ GoalBar。
- **交付**：goal 服务（事件溯源/持久化）、模型工具、自动续跑驱动器（受预算/轮次上限）、
  UI GoalBar；与现有"交接/长任务"口径打通（不重复两套状态）。
- **验收**：跨重启/恢复目标仍在；自动续跑在 round 上限停止；blocked 有稳定 code+说明。
- **交付状态（2026-11-25）**：✅ 已交付，四片一次收口：
  `b0aead94f`（a：事件溯源 + `/goal` 命令族）、`f43ce3e57`（b：`goal` 工具 8 动作 +
  稳定块 + 5 处连锁，工具 102→103）、`1ee611915`（c：round driver + 前端同链路续跑）、
  `cd3bbd141`（d：GoalBar + `/api/goal/*` 三路由）、`d2719ffa8`（派生基线修正）。
  设计/边界/偏差见 `docs/plan/2026-11-25-e1-2-goal-design.md`；`plan` 与 `goal` 两条
  事件通道都遵守"收尾事件先于 done"。真机探针：a 16/16、b 15/15、d UI PASS；
  单测 goal* 共 50 项；前端 204 files / 1558 tests；ui:check 全绿。
  **边界（如实写）**：round driver 由前端驱动（页面关闭即停）、只执行轮次上限
  （token 预算待 E0-2④ token-meter）、goal 工具与 todo_write 同为 L1 摘要确认。

#### E1-3 ask_user_question + present/交付卡（P1）
- **DSH 参照**：`tool-ask-user`（选项/多选/自定义）、`tool-present`、变更文件卡。
- **交付**：结构化提问工具 + UI 接管；`present` 声明交付文件；变更文件卡（逐文件 diff、
  打开/定位）；与 E0-3 快照对接。
- **验收**：提问带选项与多选；交付文件在回复下方成卡；点击可打开。
- **交付状态（2026-11-25，E1-3a 已交付）**：`0e159677f` —— `ask_user_question`（不阻塞：
  `USER_INPUT_REQUIRED` → SSE `ask` → 本轮结束 → 回答按普通用户消息继续）+ `present`
  （纯字符串校验、不读文件）+ `/api/ask/{pending,answer}` + QuestionCard/PresentCard；
  设计页 `docs/plan/2026-11-25-e1-3-interaction-design.md`。测试：ask 29+7、前端 207 files/1571。
  **关键决定**：两个交互原语不进检索索引（`META_ALWAYS_TOOLS` 排除、截断后追加），
  否则路由冲突决策层 48→46 / v1 27→26（跌破下限）。
  部署后真机探针 **13/13**（`.local/verify_e1_3_ask.py`，含令牌/409 stale/no_pending/present 校验）；
  UI 探针 **19/19**（`.local/verify_e1_3a_ui.py`：刷新恢复卡 → 选择 → 提交 → 答案落盘 + 用户消息
  + next_message 续跑 → 交付卡 → 复制路径 → 新一轮收起），截图 `.ui_shots/ASK_E13A.png` /
  `PRESENT_E13A.png`。
- **交付状态（2026-11-25，E1-3b 已交付）**：`db4b0a208` + `493a2641e` —— `GET /api/changes?session_id=[&checkpoint=]`
  把 E0-3 最近一次检查点摊成**逐文件**变更（每文件 20k / 整份 200k 截断并标记）；
  未跟踪文件标 `new_file`（`git diff` 无块、内容在检查点里整文件捕获）；快照补记 `session_id`
  （`maybe_snapshot()` 在排入线程前取 ContextVar），会话不一致时 `scope="workspace"` 并给说明
  —— **不假装属于本会话**。前端 ChangeCard（逐文件展开 diff）；`changes` 槽新一轮/切会话清空。
  派生面：route_ui_coverage hash `0b181b0b198f900f` → **`1185a7cf39d55b43`**（路由 526→527、
  ui 465→466、unreferenced 仍 34、该进却未进 UI 0）；ui_reachability 113 页 / 0 不可达。
  测试：后端 `test_changes_routes.py` 13、前端 ChangeCard 9 + store 3（全量 vitest 208 files/1583）；
  真机探针 11/11→**14/14**（`.local/verify_e1_3b_changes.py`，探针自己改一行再还原）、UI 探针 9/9
  （`.local/verify_e1_3b_ui.py`，截图 `.ui_shots/CHANGE_E13B.png`）。**顺带修的真缺陷**：
  `checkpoint.changes()` 指定不存在的 id 会回落"最近一次"（把打错的 id 伪装成成功）⇒ 现在 404
  `unknown_checkpoint`。
- **E1-3 余项（记入 `data/handoff_next.json` `dsh-parity-e1-3-followups`）**：检查点仍是
  **workspace 级**（历史快照没有 session_id），变更卡只能标作用域、不能按会话过滤；
  另：快照去抖 120s ⇒ 一轮刚结束拉 `/api/changes` 可能还是上一轮的快照。
- **真机端到端发现并修掉的真缺陷（2026-11-25，`3a76fe862`）**：工作台的工具白名单有三条来源
  （主线装配 / 智能选择 / 全量回退），E1-3a 只把交互原语补进了"智能选择"那条；**默认主线
  `engineering` 走的是主线装配**，于是模型思考里写「可用工具里没有 ask_user_question」，
  提问卡/交付卡在工作台里等于不存在。修法：白名单算完统一并上 `META_ALWAYS_TOOLS`
  （纯函数 `plugins.chat.merge_meta_tools()`，原序不动、已存在不重复）。
  真机 E2E（`.local/probe_e1_3_e2e_llm.py`，真模型要求调用两个工具）**8/8**：
  SSE `ask`/`present` 都出现在 `done` 之前，`GET /api/ask/pending` 能读回。
  教训：**交互原语的"永远在场"要在所有装配路径上验证**，只测纯函数 + 智能选择路径会漏掉主线。
- **真机全链路 UI 又逮到两个真缺陷（`b820a1396`）**：`.local/probe_e1_3_e2e_ui_llm.py`（真模型、
  不拦流）第一跑：卡片出现、提交 200、台账落盘，但**对话里没有那条回答**、续跑没发生。
  ① 服务端 `ask` 后没结束本轮（模型继续自问自答）⇒ 工具循环 `_ask_raised` 即 `break`
  并 gate 掉"补结论"合成轮；② 客户端 `sendMessage` 在 `streaming` 时**静默丢弃**答案 ⇒
  改为排队、本轮 `finally` 补发。修后全链路 **7/7**（卡片→答题→`answers.jsonl`→pending 清空→
  回答成用户消息→续跑 5678 字），截图 `.ui_shots/ASK_E13A_LIVE*.png`。


#### E1-4 会话智能（P1）
- **DSH 参照**：`session-query-sqlite`（FTS5）、`session-title-*`、`session-turn-outline`、
  `session-stats`、`session-log-export`。
- **交付**：会话标题（LLM，异步、可回退首条消息）；FTS5 索引 + 搜索 API/UI；
  turn outline；每轮 stats；会话导出（ZIP/MD）；模型工具 `session_search`/`session_trace`。
- **验收**：能按关键词搜到历史会话并跳转；标题生成失败不影响会话；导出可读。
- **交付状态（2026-11-25 设计定稿 / 2026-11-26 a+b+c 已交付）**：设计页
  `docs/plan/2026-11-26-e1-4-session-intelligence-design.md`；已交付 **E1-4a 标题**
  （`SessionManager.set_title` 索引+meta 双写、`title_source` 人工锁定、旁路生成器
  `agent/context/session_title.py`：守护线程 + 8s 超时 + 关 thinking、≥2 轮才起、
  LLM 失败回退首条消息；列表「AI」角标）、**E1-4b 搜索**（`agent/context/session_index.py`
  FTS5 trigram + `messages` 表；`add_message` 提交后同进程写入；`GET /api/session-search`
  三字以上 MATCH+snippet、短查询回落 LIKE+`LIMIT`；会话栏「历史命中」区块；
  `scripts/rebuild_session_index.py` 全量重建）、**E1-4c 大纲/统计/导出**
  （`agent/context/session_insight.py` 纯函数 + `/api/sessions/<id>/{outline,stats,export?format=md|zip}`，
  导出文件名 `session_<sha256(sid)[:12]>`；历史抽屉里「大纲 / 统计 / 导出 MD / 导出 ZIP」）。
  数字：后端新增用例 **27+15+6+16=64**、前端 +5（vitest **208 files/1589**）；真机探针
  **a 8/8 · b 9/9 · c 9/9**、UI 探针 **PASS**（截图 `.ui_shots/E14_SEARCH.png` /
  `E14_INSIGHT.png`）；派生面 coverage hash `1185a7cf39d55b43` → **`ef228b9ab0f80905`**
  （路由 527→531、ui 466→470、unreferenced 仍 34、该进却未进 UI 0）。
- **交付状态（2026-11-26，E1-4d 已交付）**：`session_search`（检索本机会话索引，只回片段 ≤200 字、
  limit ≤10）与 `session_trace`（某轮工具轨迹，缺省最近 3 轮）两个**任务工具**
  （`effect=read` / `permission_level=public`）；5 处连锁全做：YAML+register 逐字一致 /
  tool_index 99→**101** / 能力清单 150→**152** / `_BM25_HALF_SATURATION` 5.7446→**5.788** /
  7 条主线 boost 各 +2 且 max_tools 各 +2（engineering 28→30、harness 27→29、knowledge 25→27…）。
  真机：工具已启用且在索引里、`session_search` 能搜到刚写的消息、**真模型会主动调用**
  `session_search`（9/9）。**两个关键坑**：① `register_all` 必须接受生命周期管理器传的 `self`
  （零参数 ⇒ 后端启动 TypeError、deploy 卡 180s 未就绪）；② 主线装配按身份减工具 ⇒ 新任务工具
  还要进 boost（与 E1-2b 给 goal 加 boost 同形），否则模型说"工具集里没有它"。
- **E1-4 全部收口**：a 标题 / b FTS5 搜索 / c 大纲·统计·导出 / d 两个模型工具 ⇒
  Phase 1（工作方式）**完成**；下一段 Phase 2 多代理（E2-1 …）。
- **E2-1a 已交付（2026-11-26）**：`agent/subagent/runs.py` 运行期登记表 + 执行器每轮
  drain 追加指令 / 轮间协作式打断 + `subagent_control` 工具（list/send/interrupt/inbox，
  core 常驻）；17 条单测 + 真机探针全过。**E2-1b 同日交付（cd7765cc5）**：3 条路由 +
  分身页「运行中」块（追加指令 / 协作式打断），已部署；路由 11 + 页面 3 条测试，ui:check 全绿。

### Phase 2 — 多代理协作（建议第 7–9 周）

#### E2-1 子代理控制（P2）
- **DSH 参照**：`tool-subagent-control`（send_message / interrupt_agent / list_agents / 收件箱）。
- **交付**：子代理运行中消息、打断、发现；与现有 delegate/fan_out 复用同一生命周期管理器。
- **验收**：运行中发消息被消费；打断后子代理收尾并回报；权限/审计照旧。

#### E2-2 Agent Team（P2）
- **DSH 参照**：`agent-team`（共享任务 DAG）+ `tool-agent-team`（四个任务 schema）+ 任务板 UI。
- **交付**：团队模式（会话级开关）、任务创建/认领/完成、依赖等待、任务板页；
  默认关闭（与 DSH 一致）。
- **验收**：根与后代看到同一任务板；依赖满足才执行；关闭时零行为。

#### E2-3 Fork 子代理（P2）
- **DSH 参照**：`subagent-fork-in-process`（子代理以父日志前缀为种子）。
- **交付**：`subagent_fork`（或 delegate 的 fork 模式），用于"接着父上下文继续"；
  与现有 spawn 区分，权限继承按最小集。
- **验收**：fork 子代理能引用父对话事实；不复制系统密钥；审计标注 fork 来源。

#### E2-4 Ralph 循环（P2）
- **DSH 参照**：`tool-ralph`（不可变目标 + 每轮全新子代理 + 有界报告）。
- **交付**：`ralph` 工具（maxRounds 上限、终态 complete/blocked/budget-limited）。
- **验收**：轮次间只传有界报告；达到上限停止；不复制父会话。

### Phase 3 — 执行与内容（建议第 10–12 周）

#### E3-1 持久终端（P3，优先级实际很高）
- **DSH 参照**：`tool-terminal` 六工具 + `terminal-bash` + 侧栏终端页。
- **交付**：PTY 会话服务（Windows ConPTY/Unix pty）、六工具、侧栏终端页、输出上限与
  job 集成；与 shell_execute 的分工写进工具描述。
- **验收**：交互式程序（python REPL/调试器）可跨调用保持状态；多会话隔离；关闭清理。

#### E3-2 Office 三件套（P3）
- **DSH 参照**：`office-kit`（LibreOfficeKit，Windows 预编译）+ `tool-office` + `skill-office`。
- **交付**：`office_render/convert/recalculate` 三工具、Office 技能（docx/xlsx/pptx 工作流）、
  临时文件语义（不改输入）；Windows 打包 LibreOfficeKit 或调用本机 LibreOffice。
- **验收**：docx/xlsx/pptx 能渲染成图、转 PDF、重算公式；失败回明确错误；不写工作区。

#### E3-3 交付输入面（P3）
- **交付**：聊天附件/图片上传与粘贴、@file/@session 引用、消息引用、逐条 like/dislike。
- **验收**：上传图片进对话并可被工具读取；引用能定位到文件/会话；反馈落库。

#### E3-4 语音/翻译（P3，按需）
- 语音输入（STT，先接本机 SenseVoice 或系统 STT）、CoT 翻译（可选开关）。
- 验收：开关默认关；失败不影响对话。

### Phase 4 — 编排与生态（建议第 13–16 周）

#### E4-1 Workflow JS 编排（P4）
- **DSH 参照**：`tool-workflow`（JS 脚本扇出 subagent，返回 JSON，可后台）。
- **云枢现状**：模板工作流。
- **交付**：在现有模板体系旁加"脚本编排"（沙箱 JS，绑定 subagent/工具），或先做
  "模板 + 脚本钩子"的渐进版；保留模板路径。
- **验收**：脚本可并行扇出、拿结构化返回；超时/取消可回收。

#### E4-2 PTC 运行时（P4）
- **DSH 参照**：`ptc-runtime`（Node VM/REPL，工具绑定，保留状态）。
- **交付**：沙箱内程序化调用工具（先 Python 再 JS），有超时/内存/输出上限；与 run_sandbox 区分。
- **验收**：一次程序调用多工具、状态跨调用保留；越权工具被拒。

#### E4-3 MCP resources/prompts（P4）
- 在现有 MCP 连接上增加 resources 列表/读取、prompts 列表；模型工具 + UI 只读页。

#### E4-4 插件市场索引/推荐（P4）
- **DSH 参照**：`plugin-market` + index + recommendations + manager。
- **交付**：市场索引格式（JSON）、Discover 页、一键装 bundle、官方推荐位；与现有
  ext_discover/ext_install、签名/白名单复用；不绕过签名。
- **验收**：索引可配置；安装仍走签名与白名单；卸载干净。

#### E4-5 平台集成（P4，分批）
- ACP/SDK/headless（对外 JSON-RPC/CLI）、LiveShare（可选）、Inspector/trajectory、
  worktree/forge（Git 变更请求面板）、DeepSeek 会话导入。
- 每项单独 Epic，按真实需求排序；默认关闭。

### Phase 5 — 长期质量（持续）

#### E5-1 会话持久化/格式迁移（P5）
- 事件溯源会话 + 版本化格式迁移；与现有 JSONL/审计并存，先只读投影。

#### E5-2 工作目录/依赖工具（P5）
- `working_directory`（会话级 cwd）、`load_workspace_dependencies`（内置 Python/Node 路径）。

## 4. 排期建议

| 周 | 内容 | 里程碑 |
|---|---|---|
| 1–3 | E0-1 / E0-2 / E0-3 / E0-4 | 权限预设 + 沙箱、spill、变更快照可用 |
| 4–6 | E1-1 / E1-2 / E1-3 / E1-4 | 计划模式 + Goal + 提问/交付卡 + 会话搜索 |
| 7–9 | E2-1 / E2-2 / E2-3 / E2-4 | 子代理控制 + 任务板 + fork + Ralph |
| 10–12 | E3-1 / E3-2 / E3-3 / E3-4 | PTY + Office + 附件/引用 + 语音 |
| 13–16 | E4-1…E4-5 | 脚本编排 + PTC + MCP resources + 市场 + 平台集成 |
| 持续 | E5-1 / E5-2 + 每 Epic 的回归 | 会话格式/目录工具 |

可并行：E0-4、E1-3、E2-4、E3-4、E4-3 体量小，可由第二条工作流并行；E0-1 与 E0-2 有交叉
（工具结果 spill 会改 shell/fs 输出），建议同一人按序做。

## 5. 每个 Epic 的固定动作（Definition of Done）

1. 设计 1 页（路由/工具/表结构/开关/回滚）。
2. 代码 + 契约测试 + 守卫；新增工具走 5 处连锁；新增 env 进 registry。
3. 干净 worktree 重派生 + 五项 `--check`。
4. 本机真机探针（新增 `.local/verify_*_<issue>.py`，可重跑）。
5. 部署（停后端 → deploy --no-fetch --smoke → 三处一致 + pid + bundle）。
6. 推送 + CI 绿（Windows 分片重点看）；code scanning 0。
7. 更新 `data/handoff_next.json` 与交接文档；运行时状态不提交。

## 6. 保留项（不因对齐而改）

- 审计链（不可篡改、0 断点）、知识库/摄入/转化、单位看板、UI 规范守卫、
  插件签名 + AppContainer、中文多引擎搜索、角色/审批/权限、技能双轨、调度广度。

## 7. 已知风险

1. **Windows 沙箱**：受限令牌对 Python/工具链兼容性差，需按"不可用即 fail-closed"而不是静默降级。
2. **LibreOfficeKit**：打包体积与首次启动；优先探测本机 LibreOffice，其次内置。
3. **PTY**：ConPTY 句柄/编码/清理是主要 bug 源，先做单会话再扩多会话。
4. **脚本编排/PTC**：沙箱逃逸面；必须在独立进程 + 资源上限 + 工具白名单下运行。
5. **计划/Goal/Team**：状态机多，先用事件溯源 + 单目标约束，避免与现有交接状态打架。
6. **派生面**：每个新增工具/路由都会触发 5 处连锁，排期必须留出派生时间。

## 8. 新会话第一步

读 §11.219 的独立提示词 → 读本文 → 跑交接卡与版本核对 → 与 Owner 确认从 E0-1 开始
（或按 Owner 指定顺序）→ 按第 5 节流程执行。
