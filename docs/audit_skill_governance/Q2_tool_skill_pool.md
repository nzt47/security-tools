# Q2 · 工具与技能：同池竞争、描述质量、动态召回

> 审计编号：Q2　范围：`agent/`（622 .py）中的能力路由与描述治理
> 审计方式：**只读**。未启动服务、未跑 pytest、未 git commit。
> 所有数字来自本次实际读取/统计（Python 3.12.0 + PyYAML + tiktoken cl100k_base）。
> 无证据的推断一律显式标注【推测】。

---

## 0. 结论速览

| 问题 | 结论 | 关键证据 |
|---|---|---|
| 91 工具与 23 技能是否同池竞争同一 top-k？ | **否，完全分池**。两条互不相交的检索链，产出分别进入 `tools[]` 与 `system prompt 文本`，永不合并排序 | `agent/tool_router_hybrid.py:1389-1414`、`agent/skills_mgmt/file_store.py:421-434` |
| 检索索引里有没有混入对方？ | **没有**。工具索引 `data/tool_index.json` = 90 条**纯工具**，技能 0 条；技能索引 `data/skills_repo/` = 23 条**纯技能** | 实测：`tool_index.json` 90 名 ∩ 技能名 = ∅ |
| 23 技能能否进入 `tools[]`？ | **结构上不能**。23/23 `llm_callable=false`、`callable_mode=manual`、22/23 无 JSON Schema | `data/capability_manifest.json` entries（实测统计） |
| 注入给 LLM 的 tools 是全量还是裁剪？ | **默认取决于一个开关，当前机器为「动态召回」**；开关一关或召回失败即**退回全量 91 条** | `agent/orchestrator/orchestrator.py:3450-3458`、`data/system_prompt_config.json` `smart_tool_selection.enabled=true` |
| 全量注入实测 token | **43,997 字符 / 18,311 token(cl100k) / 14,665 token(仓内 字符÷3 口径)**；走 `prune_tool_defs` 后 40,250 字符 / 16,261 token（仅降 8.52%） | 实测（见 §2.3） |
| 工具/技能描述语义重叠 | **确认存在**，给出 21 组样本（§3），集中在：计划、子代理、测试/验证、代码审查、git、记忆、主动提示 | 见 §3 表 |
| 114 条描述质量 | 平均 94.8 字 / 中位 73 字；动词开头 98/114；含"典型触发句式" 21/114；含"明确不干什么" 10/114；**三段式齐备仅 1/114** | 见 §4 |
| 「注册表唯一事实源」是否成立 | **工具侧：测试守卫下成立（但运行时仍是两份）；技能侧：不成立 —— 15/23 技能存在两份互相冲突的描述，改一处必然不生效** | `tests/unit/test_tool_definitions_yaml.py:956-960`；实测 15/15 描述不一致 |

---

## 1. 工具与技能是否同池竞争？——**分池**，且从未合并排序

### 1.1 工具链（7 步，全程只碰工具）

| 步 | 动作 | 落点 |
|---|---|---|
| ① | 索引来源 = `data/tool_index.json`（90 条，**由 YAML 生成**） | `agent/tool_router_hybrid.py:53,1389-1408`；生成器 `scripts/sync_tool_index.py:272` 写 `"description": d["description"]`（来自 YAML） |
| ② | BM25 文档 = name + parameter_names + description；Embedding 文档 = description | `agent/tool_router_hybrid.py:1442-1447` |
| ③ | 双路融合：`final = α·bm25 + (1-α)·embed`，`α`=`AGENT_HYBRID_ALPHA`=0.5（实测 `.env:247`） | `agent/tool_router_hybrid.py:1555-1575` |
| ④ | 候选池 `top_k` 默认 **40**，且强制 ≥ `max_tools` | `agent/tool_router_hybrid.py:60,1690-1695` |
| ⑤ | 候选 = **检索命中 ∪ 关键词类别命中**（召回兜底） | `agent/tool_router_hybrid.py:1709-1717` |
| ⑥ | 别名合并 + 相关度序优先 + 截断 `max_tools=25` + PINNED_TOOLS 补回 | `agent/tool_router_hybrid.py:1752-1755` → `agent/tool_router.py:702-...`、`650-699` |
| ⑦ | 白名单 → `get_tool_defs(whitelist=…)` → `tools[]` | `agent/tools/__init__.py:664-727`；调用点 `agent/orchestrator/orchestrator.py:3458`、`agent/orchestrator/orchestrator.py:4103`、`agent/tool_calling.py:277`、`plugins/chat.py:1197` |

关键词兜底路由：`agent/tool_router.py:569-647`，类别表 `TOOL_CATEGORIES`（11 类，并集 **90** 个工具，实测），`max_tools=25` 默认（`agent/tool_router.py:572`），`PINNED_TOOLS=('delegate','fan_out')`（`agent/tool_router.py:691` 消费）。

### 1.2 技能链（另一条完全独立的链）

| 步 | 动作 | 落点 |
|---|---|---|
| ① | 索引来源 = `data/skills_repo/<id>/skill.md` 的 **front matter**（23 条） | `agent/skills_mgmt/file_store.py:421-434`（`SkillMDParser.parse`） |
| ② | 惰性装配 `SkillLoader(file_store)`（**只挂文件轨**，不挂 `skills_mgmt.json`） | `agent/skills_mgmt/service.py:82` |
| ③ | 三路：TF-IDF 倒排 + 向量（chroma/BGE-m3）+ BM25，RRF 融合 | `agent/skills_mgmt/loader.py:409`（`match`）、`:866`（`_rrf_fuse`）、`:1302`（`_try_rrf_match`） |
| ④ | 独立 `top_k`（来自 `config.yaml orchestrator.semantic_layer`）+ `min_score` 门控 | `agent/orchestrator/orchestrator.py:2374-2383`、`:2423-2461` |
| ⑤ | 命中 → `load_instruction` → **短路返回技能正文作为答案**（不是进 tools） | `agent/orchestrator/orchestrator.py:2487-2505` |
| ⑥ | 另一条注入路径：`ContextInjector` 把技能元数据/正文追加进**系统提示词文本** | `agent/skills_mgmt/context_injector.py:292`（`inject_metadata`）、`:460`（`inject_instruction`）、`:757`（`build_context`） |
| ⑦ | 再一条：`digital_life_persona._build_skill_instructions` 把**代码内联**的 7 段技能提示词拼进系统提示词 | `agent/digital_life_persona.py:42-62`、`:415-439` |

### 1.3 「分池」的硬证据（6 条，均为实测）

1. **索引不相交**：`data/tool_index.json` 90 个名字与 23 个技能 id 的交集 = **∅**（0 条）。
2. **结构性不可能**：`data/capability_manifest.json` 中 23 条 skill 条目实测 `llm_callable=true` 的**0 条**、`callable_mode` **23/23 = manual**、`schema_registered=true` 仅 1 条；条目自带的理由字段写着"纯提示词技能：由 ContextInjector 按意图注入，**模型不发起调用**；无 JSON Schema"。
3. **注入通道不同**：工具进 `tools` 参数（`agent/tools/__init__.py:693-700` 构造 `{"type":"function","function":{...}}`）；技能进 `system prompt` 文本（`context_injector.py:292`）。
4. **没有合并排序点**：全仓 `grep -rn "DescriptorRegistry|descriptor_registry"` **无任何 router 模块命中**；`tool_router*.py`、`tool_schema_pruner.py`、`tools/__init__.py` 均不 import 技能栈。
5. **两套查询文本来源相同但结果不共享**：两条链都吃 `user_input`，但 `hybrid_select_tools` 只返回工具名（`agent/tool_router_hybrid.py:1632-1761`），`SkillLoader.match` 只返回 `SkillMatch`（`agent/skills_mgmt/loader.py:409`），二者从不在同一列表里排序。
6. **两个 max_tools / top_k 互不知情**：工具 25（`tool_router.py:572`）/ 40（`tool_router_hybrid.py:60`）；技能 top_k 来自 `config.yaml`（`orchestrator.py:2376`）。不存在"共享预算"。

### 1.4 唯一"同池"的地方 —— 但不在路由链上

`data/descriptors.json`（364,381 字节，`DescriptorRegistry`）**把 91 工具 + 29 技能放进同一个 registry**，按 `capability_id` 索引：

- 实测：`descriptors` 120 条 = `origin.source_type` `{builtin: 91, skill: 29}`；120/120 都带 `capability.description`；其中 91 条与 YAML 描述**逐字相同**（`DESC_SAME_AS_YAML 91 / DIFF 0`）。
- 持久化路径 `data/descriptors.json`，写入方 `agent/descriptors/registry.py:543`（`register`）。
- **消费方全在治理/评估/诊断面**：`agent/digestion/*`、`agent/eval/metrics.py:518`、`agent/observability/trace_v2.py:2013`、`agent/repair/locate.py:243`、`agent/memory/forgetting.py:337`、`agent/ui_panels/data.py:425`、`agent/server_routes/routes_ui_panels.py:637`。**没有任何 router 读它**。
- 且它是**快照**：文件 mtime = 2026-09-17 21:40，`.gitignore:226` 忽略（`git check-ignore` 实测命中）。

> 结论：**「同池」只存在于一个只读、过期、且不参与路由的派生台账里。** 当前路由上 91 工具与 23 技能是彻底分池的，谈"top-k 竞争"在实测口径下**不成立**。

---

## 2. 注入给 LLM 的 tools 数组：全量还是动态裁剪？

### 2.1 两条入口，两种开关语义（**口径分裂**，实测）

| 入口 | 选择链 | 是否受 `smart_tool_selection` 开关约束 |
|---|---|---|
| 编排器 `_call_llm` | 主线装配 → 智能选择 → **退回全量** | **受**：`agent/orchestrator/orchestrator.py:3450` `if _line_used is None and self._is_smart_tool_selection_enabled()` |
| 编排器 `_call_llm_v2` | 同上 | **受**：`agent/orchestrator/orchestrator.py:4101` |
| 任务分发 `dispatch_task` | 智能选择 | **受**：`agent/orchestrator/task_dispatcher.py:46` |
| 工作台 SSE `plugins.chat` | 主线 → 智能选择 → 退回全量 | **不受**：`plugins/chat.py:1182-1195` 直接调 `hybrid_select_tools`，**无开关判断** |

开关实现：`agent/orchestrator/task_dispatcher.py:152-162` → `agent/system_prompt_config.py:917-941` → `data/system_prompt_config.json`。
**实测本机取值：`sections.smart_tool_selection.enabled = true`** ⇒ 编排器路径走动态召回。

基础白名单：`agent/digital_life_persona.py:441-457` 读 `data/tools_config.json`；实测该文件 `tool_states` 5 项**全为 true** ⇒ `disabled` 为空 ⇒ **返回 `None`（= 不限制，即全量候选）**（`digital_life_persona.py:451-452`）。
主线：`data/lines` 目录**不存在** ⇒ `agent/lines/integration.py:93-97` 返回 `(None, None)` ⇒ `_line_used is None`。

⇒ **本机实际路径**：`hybrid_select_tools(user_input, None) or get_tools_for_input(user_input, None)`，两者都失败则 `get_tool_defs(whitelist=None)` = **全量**。

### 2.2 裁剪逻辑在哪个函数

| 层 | 函数 | 文件:行 | 作用 |
|---|---|---|---|
| 名额裁剪（**唯一真正减少工具数的地方**） | `_apply_alias_merge_and_priority_sort` / `_restore_pinned_tools` | `agent/tool_router.py:702`、`:650` | 截断到 `max_tools=25`（+ PINNED 补回，可略超） |
| 字段级裁剪 | `prune_schema` / `prune_tool_defs` | `agent/tool_schema_pruner.py:136`、`:176` | 截断 description（`SCHEMA_DESC_MAX_LEN`）、移除 `deprecated` 字段与冗余 `additionalProperties`；**工具级 deprecated=true ⇒ 整条移除** |
| token 预算裁剪（工具级） | `plan_token_budget` / `prune_tool_defs_for_budget` | `agent/capregistry/pruning.py:387`、`:452` | 仅在 `CP_TOOLSET_SCHEMA_TOKEN_BUDGET>0` 时生效；**本机未设置 ⇒ 预算=0 ⇒ 不裁剪**（`agent/capregistry/pruning.py:108,116-121`；`agent/tool_schema_pruner.py:216-217`） |
| 隐藏（不算裁剪） | `_hidden_tool_names` | `agent/tools/__init__.py:635-637` | internal ∪ 声明不可被 LLM 调用 |

**实测裁剪旋钮（`.env` 而非代码默认值，差异必须点名）**：
`.env:161 SCHEMA_DESC_MAX_LEN=100`（代码默认 **200**，`tool_schema_pruner.py:76`）
`.env:163 SCHEMA_PROP_DESC_MAX_LEN=80`（代码默认 **120**，`:77`）
`.env:165 SCHEMA_PRUNE_DEPRECATED=True`；`.env:167 SCHEMA_PRUNE_ADDITIONAL_PROPS=True`
`CP_TOOLSET_SCHEMA_TOKEN_BUDGET` **未出现** ⇒ 0。

### 2.3 实测规模（复算脚本见 §7）

工具集来源：`data/tool_definitions/*.yaml` **91 个**（实测计数；manifest 与之一致）。索引 `data/tool_index.json` 只有 **90** 个（缺 `process_distill_run`，因其 `internal:true`+`llm_callable:false`；生成器 `scripts/sync_tool_index.py:272`）。

| 场景 | 工具数 | JSON 字符数 | token (tiktoken cl100k) | token (仓内 字符÷3 口径) |
|---|---|---|---|---|
| **全量注入（未裁剪）** | 91 | 43,997 | **18,311** | 14,665 |
| 全量经 `prune_tool_defs`（用 `.env` 实参 100/80） | **91（一个都没删）** | 40,250 | **16,261** | 13,416 |
| 模型可见集（剔除 internal `process_distill_run`） | 90 | 43,317 | 18,051 | 14,439 |

裁剪减幅 **8.52%**，**31 条描述被截断并以 `...` 结尾**（实测名单：apply_patch, arch_diagram, browser_navigate, browser_screenshot, data_convert, data_format_detect, delegate, distill_process_from_knowledge, edit, ext_discover, fan_out, get_clipboard, get_weather, git, grep, humanize_zh, json_query, look_at_screen, notify, read_pdf_tables, run_lint, run_sandbox, run_tests, schedule_task, shell_execute, sqlite_query, todo_write, web_extract, web_search, workspace_delete, workspace_init）。截断点在句子中间，例如 `delegate` 被切成"…调用前必须写全八要素——①目标 ②约束 ③已有成"。

动态召回（关键词路，实测真实调用 `agent.tool_router.get_tools_for_input`）：

| 输入 | 命中类别 | 返回工具数 | 注入 token (cl100k，经裁剪) |
|---|---|---|---|
| "你好" | core | 5 | 752 |
| "记住我喜欢喝咖啡" | core, v2 | 9 | — |
| "把这批任务并行派发给多个子代理" | async, core | 12 | — |
| "搜索一下今天的新闻" | core, web | 14 | 2,416 |
| "帮我写一个 Python 脚本并运行测试" | code, core | 20 | — |
| "读取 data/config.json 并总结内容" | code, core, file | **25**（触顶） | 4,269 |

> **即使输入是"你好"，也固定注入 5 个工具**（core 类别恒命中：get_status / search_memory / remember / get_sensor_summary / todo_write）。动态召回的**下限不是 0**。

**与注释的差异**：`plugins/chat.py:1156` 写"实测 91 个 ≈ **13k token/轮**"。本报告实测：tiktoken cl100k = **18,311**（未裁剪）/ **16,261**（裁剪后）；只有仓内 `字符÷3` 口径的 13,416–14,665 与"13k"同量级。⇒ **注释的 13k 只对 `字符÷3` 口径成立，对真实 BPE token 低估约 29%**。

---

## 3. 工具名/描述 与 技能名/描述 的同名与语义重叠

**名称完全相同的**：工具名 ∩ 技能 id = **∅（0 组）**。
**描述文本的字面重合**：双向 bigram Jaccard 最高仅 **0.047**（`process_distill_run` ↔ `scripted-selftest`）⇒ 词面上**几乎不撞**，撞的是**意图**。

以下 21 组为语义重叠样本（= "同一句用户话可能同时命中两边"+1 组技能↔技能）。**"实测"=名字/描述文本可核；"【推测】"=语义归属判断，未见显式声明。**

| # | 工具（证据） | 技能（证据） | 重叠领域 | 判定 |
|---|---|---|---|---|
| 1 | `todo_write`（`data/tool_definitions/todo_write.yaml:3`："把当前任务的计划清单写下来（todo list / plan / 计划、待办清单）"） | `pd-writing-plans-f846e3a2-skill`（`data/skills_repo/pd-writing-plans-f846e3a2-skill/skill.md:4`："Use when you have a spec or requirements for a multi-step task, before writing code"） | 写计划 | 实测文本 + 【推测】语义 |
| 2 | 同上 `todo_write` | `pd-executing-plans-95cbf64a-skill`（`.../pd-executing-plans-95cbf64a-skill/skill.md:4`："Use when you have a written implementation plan to execute"） | 执行计划 | 实测 + 【推测】 |
| 3 | `delegate`（`data/tool_definitions/delegate.yaml:3`："把任务委派给一个独立的子代理执行（delegate / subagent / dispatch task）"） | `pd-subagent-driven-development-8c375695-skill`（`.../skill.md:4`） | 子代理 | 实测 + 【推测】 |
| 4 | `fan_out`（`data/tool_definitions/fan_out.yaml:3`："并行派发多条主线（fan_out / parallel delegate / multi-agent）"） | `pd-dispatching-parallel-agents-b8065ccd-skill`（`.../skill.md:4`："Use when facing 2+ independent tasks"） | 并行子代理 | 实测 + 【推测】 |
| 5 | `run_tests`（`data/tool_definitions/run_tests.yaml:3`："运行项目测试（pytest）"） | `pd-test-driven-development-8562c8ad-skill`（`.../skill.md:4`） | 测试 | 实测 + 【推测】 |
| 6 | `run_tests` | `pd-verification-before-completion-af010352-skill`（`.../skill.md:4`："before claiming work complete… run the verification command"） | 验证 | 实测 + 【推测】 |
| 7 | `code_review`（`data/tool_definitions/code_review.yaml:3`："执行结构化代码审查…基于 gstack review"） | `pd-requesting-code-review-ca5ae995-skill`（`.../skill.md:4`） | 代码审查 | 实测 + 【推测】 |
| 8 | `code_review` | `pd-receiving-code-review-8934157e-skill`（`.../skill.md:4`） | 代码审查 | 实测 + 【推测】 |
| 9 | `run_lint`（`data/tool_definitions/run_lint.yaml:3`："代码检查、lint、静态检查、ruff、mypy、typecheck"） | `pd-verification-before-completion-af010352-skill` | 静态检查/验证 | 实测 + 【推测】 |
| 10 | `git`（`data/tool_definitions/git.yaml:3`："执行 git 版本控制操作。action 取值：status/diff/log/branch/show"） | `pd-using-git-worktrees-d516703a-skill`（`.../skill.md:4`："isolated workspace… git worktree"） | git 工作区 | 实测 + 【推测】 |
| 11 | `git` | `pd-finishing-a-development-branch-e085de5a-skill`（`.../skill.md:4`："merge / PR / cleanup"） | 分支收尾 | 实测 + 【推测】 |
| 12 | `search_memory`（`data/tool_definitions/search_memory.yaml:3`："搜索我的记忆。scope=all/vector/logs"） | `memory_summary`（`data/skills_repo/memory_summary/skill.md:4`："对长对话或历史记忆做结构化压缩"） | 记忆检索/压缩 | 实测 + 【推测】 |
| 13 | `remember`（`data/tool_definitions/remember.yaml:3`："记住重要信息，存储到长期记忆…可通过 search_memory 搜索到"） | `memory_summary` | 记忆写入 | 实测 + 【推测】 |
| 14 | `search_lifetrace`（`data/tool_definitions/search_lifetrace.yaml:3`："搜索我的记忆（使用 LifeTrace）"） | `memory_summary` | 记忆检索 | 实测 + 【推测】 |
| 15 | `notify`（`data/tool_definitions/notify.yaml:3`："主动向用户发出通知/提醒…提醒我"） | `proactive_suggestion`（`.../skill.md:4`："在用户未明确提问时…主动提出有依据的建议"） | 主动提示 | 实测 + 【推测】 |
| 16 | `generate_tool`（`data/tool_definitions/generate_tool.yaml:3`："生成一个自定义工具…自主编写代码生成工具"） | `pd-writing-skills-5da20e67-skill`（`.../skill.md:4`："创建、编辑或验证 agent 技能（SKILL.md）"） | 创建新能力 | 实测 + 【推测】 |
| 17 | `distill_process_from_knowledge`（`data/tool_definitions/distill_process_from_knowledge.yaml:3`："蒸馏为可复现步骤序列…固化为 workflow 与 skill"） | `pd-writing-skills-5da20e67-skill` | 蒸馏为技能 | 实测文本（**该工具描述里直接出现 "skill"**）+ 【推测】 |
| 18 | `arch_diagram`（`data/tool_definitions/arch_diagram.yaml:3`："生成系统架构图…HTML+SVG"） | `pd-frontend-design-77ea5c4e-skill`（`.../skill.md:4`："Create distinctive, production-grade frontend interfaces"） | 前端/界面产出 | 实测 + 【推测】 |
| 19 | `kb_search`（`data/tool_definitions/kb_search.yaml:3`："知识检索（语义融合检索知识卡片）"） | `memory_summary` | 检索/摘要 | 实测 + 【推测】 |
| 20 | `submit_task`（`data/tool_definitions/submit_task.yaml:3`："提交异步任务在后台执行…适用于耗时工具"） | `pd-dispatching-parallel-agents-b8065ccd-skill` | 并行任务 | 实测 + 【推测】 |
| 21 | `humanize_zh`（`data/tool_definitions/humanize_zh.yaml:3`："检测中文文本中的 AI 写作痕迹并给出优化建议"） | `self_reflection`（`.../skill.md:4`："回顾自身推理与回答过程，识别可能的疏漏"） | 文本自检 | 实测 + 【推测】 |

### 3.1 附加：**工具↔工具同名**（manifest 自己登记的 3 组）

`data/capability_manifest.json` 顶层 `same_name_conflicts` 实测 3 组，均为"全局注册表 vs 规划表同名不同描述"：

| 名字 | 全局定义（有 schema） | 规划表定义（无 schema） |
|---|---|---|
| `get_status` | `data/tool_definitions/get_status.yaml:3` "获取我的完整状态" | `agent/tools/core_tools.py:121` "获取完整状态" |
| `search_memory` | `data/tool_definitions/search_memory.yaml:3`（长描述，含 scope 说明） | `agent/tools/core_tools.py:125` "搜索记忆" |
| `get_sensor_summary` | `data/tool_definitions/get_sensor_summary.yaml:3` "查看所有传感器状态" | `agent/tools/core_tools.py:132` "获取传感器摘要" |

另两个只在规划表里的名字：`check_health`（`core_tools.py:116`）、`llm_chat`（`core_tools.py:136`）。规划表是**另一个 registry 实例**（`agent/orchestrator/lifecycle_manager.py:527` `self._planning_tools = ToolRegistry()`），`agent/lines/callability.py:375-376` 已明确承认"与全局注册表**同名但契约不同**"。

---

## 4. 描述质量量化（114 条）

### 4.1 本次使用的口径（**设计文档 4.1 原文在仓库里找不到，故口径由本报告显式定义**）

> 检索证据：`grep -rn "全量改造|三段式|典型触发句式|描述质量" docs/` ⇒ 命中 8 条，**全部与本主题无关**（都是 stop() 三段式、规则三段式）。`docs/` 下没有任何一份文件描述"114 项全量改造"。⇒ 本节判据为本报告自定，**不得当作设计文档口径引用**。

- **语料 114 条** = 91 工具描述（`data/tool_definitions/*.yaml` 的 `description`）+ 23 技能描述（`data/skills_repo/<id>/skill.md` front matter 的 `description`，即**检索实际读的那份**）。
- **动词开头**：取描述第一个标点前的首段，若以「中文实义动词表」或「英文祈使动词表」开头即计 1（动词表见 §7 脚本；助词/介词"把/将/在/从/对/按"**不计**）。
- **典型触发句式**：正则 `适用于|适用场景|使用场景|用于|用来|当…时|时使用|时调用|Use when|Use this|使用本|调用本|触发条件|调用时机|在…之前|场景` 命中即计 1。
- **明确不干什么**：正则 `不得|禁止|不要|不支持|不可|不能|严禁|避免|仅用于|仅供|只读|无副作用|不修改|不删除|不会|不参与|仅支持|不做` 命中即计 1。
- **三段式（做什么 + 何时用 + 不做什么）**：要求「动词开头 ∧ 触发句式 ∧ 明确否定」**三者同时成立**。

### 4.2 实测结果

| 指标 | 91 工具 | 23 技能 | 合计 114 |
|---|---|---|---|
| 空描述 | **0** | **0** | **0** |
| 长度 平均 | 102.3 字 | 65.4 字 | **94.8 字** |
| 长度 中位 | 78 字 | 70 字 | **73 字** |
| 长度 最短 / 最长 | 8 / 579 | 17 / 89 | 8 / 579 |
| 动词开头 | **81（89.0%）** | **17（73.9%）** | **98（86.0%）** |
| 含典型触发句式 | **5（5.5%）** | **16（69.6%）** | **21（18.4%）** |
| 含"明确不干什么" | **10（11.0%）** | **0（0%）** | **10（8.8%）** |
| **三段式齐备** | **1（1.1%）** | **0** | **1（0.9%）** |

长度分布（114 条）：`<20 字 14 条 / 20–49 字 20 条 / 50–99 字 48 条 / 100–199 字 22 条 / ≥200 字 10 条`。

**最短 12 条**（`get_persona_info` 与 `get_status` 都只有 **8 个字**）：
`get_persona_info`(8) "查看当前人格配置"、`get_status`(8) "获取我的完整状态"、`cancel_scheduled_task`(9)、`get_sensor_summary`(9)、`pause_scheduled_task`(9)、`get_preferences`(10)、`resume_scheduled_task`(10)、`trigger_distillation`(10)、`web_batch`(10) "批量请求多个 URL"、`web_download`(13)、`stop_process`(15)、`kb_search`(16)。（**这 12 条全部会被 `SCHEMA_DESC_MAX_LEN=100` 放过，但语义上无法支撑"何时该用/何时不该用"的判断**。）

**触发句式**：技能侧 16 条里 13 条是 pd-\* 系列的原生英文 `Use when …`（`data/skills_repo/pd-*/skill.md:4`）；工具侧仅 5 条：`data_format_detect`（"同时返回…"）、`generate_tool`（"当你需要的能力没有现成扩展时"）、`get_clipboard`（"仅用于…这类需求"）、`list_mcp_connections`（"用于确认…"）、`submit_task`（"适用于耗时工具"）。

**"明确不干什么"**：10 条**全部在工具侧**，例如 `get_clipboard`（"仅用于…；要读文件请用 read_file，要读 PDF 请用 read_pdf"）、`workspace_delete`（"不可撤销。禁止删除工作区根目录"）、`sqlite_query`（"只读查询…安全约束"）、`run_sandbox`（"禁用 import、open、eval、getattr"）。**23 条技能描述里 0 条写了边界。**

**唯一三段式**：`get_clipboard`（`data/tool_definitions/get_clipboard.yaml:3`）。

---

## 5. 「114 项全量改造」的实际成本 —— 与"注册表唯一事实源"是否成立

> 设计文档 4.1 原文在仓库内不可得（§4.1）⇒ 本节按"改一条描述要让**所有消费方**同步生效"定义成本。

### 5.1 每个描述当前**存在几处、存在哪里**（实测）

**A. 工具描述（91 条）：2 处复制，被两个不同消费方各读一份**

| 存储点 | 形态 | 谁读它 |
|---|---|---|
| ① `data/tool_definitions/<name>.yaml:3`（91 个文件） | YAML | **检索**：`scripts/sync_tool_index.py:272` → `data/tool_index.json` → `HybridRetriever.rebuild`（BM25+Embedding 文档）；以及 `CapabilityRegistry`（`agent/capregistry/view.py:194-201` 调 `load_tool_meta()`、`:226-227` 调 `CapabilityRecord.from_tool_meta`） |
| ② `@_tools.register("name", "<描述>", schema=…)` 代码字面量 | Python | **模型可见**：`agent/tools/__init__.py:697` `"description": tool["description"]` —— 即 `tools[]` 参数里那一份 |
| ③ `agent/knowledge/tools.py:253-297` `_TOOL_DEFS` 表（`kb_*` 6 条） | Python | 同上（`register(...)` 循环，`agent/knowledge/tools.py:322-323`） |

实测分布：**① 91 个 YAML 文件；② + ③ 共 91 个代码站点，落在 19 个 .py 文件里**：

`agent/process_distill/tools.py`、`agent/knowledge/tools.py`、`agent/tools/{code,core,db,ext,extra,fan_out,file_tools_reg,git,lint,notify,pdf,plan,search,subagent,system,test,web}_tools.py`

**实测一致性：91/91 逐字相同（差异 0 条）** —— 因为有守门测试：
`tests/unit/test_tool_definitions_yaml.py:956-960` `test_all_descriptions_match`（抽取器见 `:866-911`，含 `agent/tools/*.py` + `agent/process_distill/tools.py` + `knowledge/tools.py` 的 `_TOOL_DEFS`）。
⚠️ 但该守卫的抽取器用 `if extracted["name"] not in py_defs`（`test_tool_definitions_yaml.py:905`）**首个声明优先**，若某工具被二次注册且描述不同，**守卫看不见**。
⚠️ 该守卫是**测试**，不是运行时单一来源：`get_tool_defs()` 永远读代码、`HybridRetriever` 永远读 YAML。

**B. 技能描述（23 条）：多源，且**已有 15 条互相冲突**

| 存储点 | 条数 | 是否受版本控制 | 谁读它 |
|---|---|---|---|
| ① `data/skills_repo/<id>/skill.md` front matter `description`（`:4`） | **23** | ✅ 已跟踪 | **检索**（`agent/skills_mgmt/file_store.py:429-434`）；`ContextInjector` 元数据注入；UI 的"文件轨独有"分支 |
| ② `data/skills_mgmt.json[<id>].description` | **22** | ❌ `.gitignore:224` | **UI 主轨**（`agent/skills_mgmt/registry.py:161-174` → `app_server.py:723-724`）；`SkillSearcher`（`agent/skills_mgmt/searcher.py:41-62`，**单字分词**，与 loader 的 bigram 不同源） |
| ③ `data/skills.json[].description`（legacy） | **30** | ❌ `.gitignore:148` | `agent/digital_life_persona.py:367-376`（"【技能】已启用"文本）；manifest 自己把这条列为 `unmigrated_reader` |
| ④ `data/skills_descriptions_overlay.json` | **4** | ✅ 已跟踪 | `plugins/skills.py:145-155`，**仅当该技能描述为空时才覆盖** |
| ⑤ `agent/digital_life_persona.py:42-62` `_SKILL_PROMPTS` | **7** | ✅ 代码 | 系统提示词（`digital_life_persona.py:415-439`） |
| ⑥ `data/descriptors.json` | **29 skill** | ❌ `.gitignore:226` | 治理/评估/诊断面（**不参与路由**） |
| ⑦ `data/skills_repo/.index/cache.json`（23 条含 description） | 23 | ❌（生成物） | `SkillIndexCache`；**每次 `get_all_metadata` 走 mtime+md5 校验**（`agent/skills_mgmt/index_cache.py:146-164,237-255`）⇒ 此项**不会**造成陈旧 |
| ⑧ `data/skill_vectors/native_chroma/chroma.sqlite3` 的 `embedding_metadata.description` | **仅 8 条** | ❌（生成物） | 向量路；8 条与当前 skill.md **逐字一致**（实测），创建时间 2026-07-23，**另外 15 条技能没有向量** |

### 5.2 「同一能力多处描述、改一处不生效」——**已发生，不是风险**

**实测：23 个技能里 15 个同时存在于 ①②，且 15/15 描述不一致（100% 分歧）。**

| skill_id | 在 `skills_mgmt.json` | 在 `skill.md` | 两份描述相同？ | UI 显示的是 | 检索匹配的是 |
|---|---|---|---|---|---|
| `pd-brainstorming-697b717a-skill` 等 **15 个 pd-\*** | ✅ | ✅ | **❌ 全部不同** | `skills_mgmt.json`（中文） | `skill.md`（英文 `Use when…`） |
| `context_aware` / `emotion_expression` / `memory_summary` / `proactive_suggestion` / `safety_guard` / `scripted-selftest` / `self_reflection` / `voice_interaction` | ❌ | ✅ | n/a | `skill.md` | `skill.md` |

合并规则决定了"谁赢"：`agent/skills_mgmt/registry.py:153-193` `as_legacy_rows()` —— **主轨先写入并 `seen.add`，文件轨只补主轨没有的 id**（`:159-176` 主轨，`:177-190` 文件轨 `if sid in seen: continue`）。
⇒ 对那 15 个技能：**改 `skill.md` 只改检索，UI 不变；改 `skills_mgmt.json` 只改 UI，检索不变。**

**三个具体反例：**

1. `self_reflection`：`skill.md:4` = "自我反思技能 — 让模型回顾自身推理与回答过程…"；`skills_descriptions_overlay.json` = "自省反思：复盘自身行为与决策…"；`digital_life_persona.py:43-44` = "## 自省反思\n每次交互后，你都会进行自我反思…"。**同一技能三套文案**，且 overlay 那份**永远不会生效**（因 `plugins/skills.py:152` 要求原描述为空）。
2. `memory_summary`：`skill.md:4`（"记忆摘要技能 — 对长对话或历史记忆做结构化压缩…适用于总结对话历史、压缩记忆…"）vs overlay（"记忆摘要：压缩与归纳对话历史与长期记忆，控制上下文占用"）。
3. 迁移工具也承认这件事：`agent/skills_mgmt/meta_editor.py` 的受控编辑白名单**只允许改 `data/skills_repo/`**（`meta_editor.py:10-11`），即**自动化改写只能改到文件轨**——改完 UI（主轨）依旧显示旧文案。这是"两个源"在写路径上的又一次固化。

并且**没有任何测试守住技能描述的一致性**：`grep -rn "skills_descriptions_overlay" tests/` = **0 命中**；`grep -rn "skills_mgmt.json" tests/` 的 21 处全是 `tmp_path` 隔离桩，没有一条比对 `skill.md` 与主轨。

### 5.3 改造 114 条的实际成本（实测口径）

**最小可行集**（目标：让模型看到的 + 检索匹配的 = 一致，且"改一处即生效"）：

| 目标 | 必须改的文件数 | 编辑站点数 |
|---|---|---|
| 91 条**工具**描述（YAML 一份 + 代码字面量一份，**两份都要改**才同时满足"检索命中"与"模型可见"） | **110 个文件**（91 YAML + 19 .py） | **182 个站点**（91 + 91） |
| 23 条**技能**描述（`skill.md` × 23 + `skills_mgmt.json` × 15 个键，**同一文件**） | **24 个文件**（23 skill.md + 1 json） | **38 个站点** |
| **合计** | **134 个文件** | **≈220 个站点** |

**连带成本（容易漏）**：

- `data/tool_index.json` 必须重跑 `scripts/sync_tool_index.py` 重生成（否则检索索引仍是旧描述）；
- `data/skills_repo/.index/cache.json` 由 mtime+hash 自动失效（**这块是安全的**，`index_cache.py:237-255`）；
- 向量索引 `data/skill_vectors/native_chroma` **只覆盖 8/23 技能，且 `ensure_indexed` 只索引"新增 id"**（`agent/skills_mgmt/vector_adapter.py:349-353` `new_ids = current_ids - self._indexed_skill_ids`）——
  【**推测**】磁盘上已存在 `skill_<id>` 向量，而 `_try_init_native_chroma` 用 `get_or_create_collection` 只加载**持久化集合**、**不把已有 id 回填 `_indexed_skill_ids`**（`vector_adapter.py:291-300`），故首次 `ensure_indexed()` 会对全部 23 个 id 调 `collection.add`，其中 8 个重复 id 触发异常 → 被 `:414-415` 的 `except Exception` 吞掉，**新增/变更的描述可能永远进不了向量索引**。**本次未启动服务，未实机验证，标注为推测**；
- `data/descriptors.json` 是 2026-09-17 的快照（`agent/descriptors/backfill.py` 回填），改描述后需重跑回填否则治理面板失真；
- `agent/digital_life_persona.py:42-62` 的 7 段内联提示词**不在任何自动同步链路里**，只能手改。

**结论：设计文档"114 项全量改造"的实际成本不是"改 114 条"，而是"改 134 个文件、约 220 个站点"，其中 15 条技能描述是**双写**、91 条工具描述是**双写**、另有 6 条技能描述散落在 overlay/legacy/persona 内联三处（都是死源或旁路）。**

### 5.4 「注册表唯一事实源」是否已成立

| 断言 | 实测裁定 |
|---|---|
| `data/tool_definitions/*.yaml` 是工具的单一事实源（D1） | **部分成立**。YAML 确实是**检索与治理**的唯一来源（`scripts/sync_tool_index.py:4`"只读 YAML，不读 Python 注册表"），但**模型可见的那份描述来自代码字面量**，二者靠 `tests/unit/test_tool_definitions_yaml.py:956` 一条测试守住。**运行时不存在"唯一源"，只存在"两条被测试绑在一起的复本"。** |
| `data/skill_callability.yaml` 是技能的单一事实源 | **不成立**。该文件只承载**策略声明**（`agent/capregistry/view.py:552-554` 自述"覆盖面不足以还原实体"）；技能**描述**分散在 §5.1-B 的 8 处，无任何一致性守卫。 |
| `CapabilityRegistry` 是模型/HTTP/CLI 的统一能力视图 | **成立，但不含描述**：23 条 skill 条目在 `data/capability_manifest.json` 里**根本没有 `description` 字段**（实测 `manifest skill entry has 'description' key: 0`）⇒ `/capabilities/tools` 返回的技能条目 `description 恒为空串`。 |
| 「改一处不生效」风险 | **已实现**，15/15 技能描述冲突（§5.2），且 overlay 的 4 条**结构性永不生效**。 |

---

## 6. 注释/文档声称 vs 实测不符（逐条点名）

| # | 声称（文件:行） | 实测 | 差异性质 |
|---|---|---|---|
| 1 | `plugins/chat.py:1156` "实测 91 个 ≈ **13k token/轮**" | 91 条 = **18,311** token（cl100k）/ 16,261（裁剪后）；14,665 / 13,416（仓内 字符÷3） | 注释只有 `字符÷3` 口径成立；对真实 BPE 低估 ~29% |
| 2 | `agent/capregistry/view.py:24-27` "技能实体（`skills.json` / `skills_mgmt.json` / **`skills_repo/*/skill.md`**）…**不在版本控制内**" | `git ls-files` 实测：23 个 `data/skills_repo/*/skill.md` **全部已跟踪** | 注释过时；`skills.json`/`skills_mgmt.json` 确实被忽略，`skill.md` 不是 |
| 3 | `data/capability_manifest.json` `skill_entity_constraints` ".gitignore:**140** 忽略 data/skills.json" | `git check-ignore -v` 实测 = `.gitignore:**148**` | 行号漂移 |
| 4 | 同上 ".gitignore:**205** 忽略 data/skills_mgmt.json" | 实测 = `.gitignore:**224**` | 行号漂移 |
| 5 | `agent/tool_schema_pruner.py:76-77` 默认 `SCHEMA_DESC_MAX_LEN=200` / `PROP=120` | `.env:161,163` 实际 **100 / 80** | 代码默认 ≠ 生效值（会被误引用） |
| 6 | `agent/system_prompt_config.py:114` "通过 API 的 tools 参数注入 **27 个工具**的 JSON Schema" | 实测注册 91（模型可见 90） | 文案陈旧（manifest 也已把它列为 `ineffective_config`：`docs/rfc/云枢能力清单盘点表.md:192,205`） |
| 7 | `agent/digital_life_persona.py:348-357` 的主轨读取 | manifest 自述此段"仍直接读 legacy `data/skills.json`"；实测代码在 `:366-376` 读 `agent/data/skills.json` 与 `data/skills.json` | 文档指向的行号偏移（内容判定一致） |
| 8 | `agent/skills_mgmt/bm25_searcher.py:17` "中文按字（与 loader.`_tokenize` 一致）" | 实际实现（`:60-68`）已是 **bigram**，且注释 `:50-56` 自己更正为"已同步 bigram" | 同一 docstring 内自相矛盾（`:17` vs `:50-56`） |

---

## 7. 复现脚本

以下代码全部通过 `python -c "import base64;exec(base64.b64decode('<B64>'))"` 运行，**不写任何文件**。为便于复核，给出可直接执行的源码。

### 7.1 规模与 token（§2.3）

```python
import sys, os, glob, json
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\Administrator\agent"
import yaml
defs = []
for p in sorted(glob.glob(os.path.join(root, "data", "tool_definitions", "*.yaml"))):
    d = yaml.safe_load(open(p, encoding="utf-8")) or {}
    if not d.get("name"): continue
    func = {"name": d["name"], "description": d.get("description", "")}
    func["parameters"] = d.get("schema") or {"type": "object", "properties": {},
                                             "additionalProperties": True}
    defs.append({"type": "function", "function": func})
s = json.dumps(defs, ensure_ascii=False)
print(len(defs), len(s), len(s)//3)
import tiktoken
print(len(tiktoken.get_encoding("cl100k_base").encode(s)))   # 18311
# 裁剪（真实 .env 旋钮）
import importlib.util
os.environ.setdefault("SCHEMA_DESC_MAX_LEN", "100")
os.environ.setdefault("SCHEMA_PROP_DESC_MAX_LEN", "80")
spec = importlib.util.spec_from_file_location("tsp", os.path.join(root, "agent", "tool_schema_pruner.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
out = m.prune_tool_defs(defs, intent_context={"selected_tools": []})
print(len(out), len(json.dumps(out, ensure_ascii=False)))    # 91 40250
```

### 7.2 描述质量（§4）

```python
import re, statistics, glob, os, json, sys
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\Administrator\agent"
import yaml
CN_VERBS = ["读取","写入","列出","搜索","查询","获取","查看","执行","创建","删除","取消","暂停",
 "恢复","发送","生成","提交","合并","拆分","提取","检测","比较","记住","触发","切换","配置","安装",
 "卸载","发现","扫描","启用","关闭","连接","断开","截取","解压","运行","终止","收集","提炼","产卡",
 "初始化","派发","通知","巡检","检索","讨论","把","将","对","用","使用","通过","从","在","按","还原",
 "回放","重命名","复制","移动","压缩","下载","上传","添加","移除","注册","调用","评估","评审","蒸馏",
 "总结","记录","更新","同步","校验","验证","分析","审查","规划","委派","遗忘","发布","回滚"]
EN_VERBS = ["Use","Create","Read","Write","Run","Get","List","Search","Check","Build","Add","Remove",
 "Delete","Fetch","Execute","Find","Make","Review","Verify","Call","Send","Generate","Parse","Extract",
 "Compare","Update","Open","Close","Set","Start","Stop","Track","Apply"]
TRIG = re.compile(r"适用于|适用场景|使用场景|用于|用来|当[^，。；]{0,14}时|时使用|时调用|Use when|"
                  r"Use this|使用本|调用本|触发条件|调用时机|在[^，。；]{0,12}之前|场景")
NEG  = re.compile(r"不得|禁止|不要|不支持|不可|不能|严禁|避免|仅用于|仅供|只读|无副作用|不修改|"
                  r"不删除|不会|不参与|仅支持|不做")
def lead(d):
    return re.split(r"[（(：:,，。;；\-—\s、/|]", d.strip(), maxsplit=1)[0]
def verb(d):
    seg = lead(d)
    return any(seg.startswith(v + " ") or seg == v for v in EN_VERBS) or \
           any(seg.startswith(v) for v in CN_VERBS)
tools = []
for p in sorted(glob.glob(os.path.join(root, "data", "tool_definitions", "*.yaml"))):
    d = yaml.safe_load(open(p, encoding="utf-8")) or {}
    if d.get("name"): tools.append(str(d.get("description") or ""))
skills = []
for d in sorted(glob.glob(os.path.join(root, "data", "skills_repo", "*"))):
    p = os.path.join(d, "skill.md")
    if not os.path.isfile(p): continue
    mm = re.search(r"^description:\s*(.*)$", open(p, encoding="utf-8").read(), re.M)
    if mm: skills.append(mm.group(1).strip().strip('"'))
res = [{"kind": "tool", "d": x} for x in tools] + [{"kind": "skill", "d": x} for x in skills]
print("n", len(res), "mean", statistics.mean(len(r["d"]) for r in res),
      "median", statistics.median(len(r["d"]) for r in res))
print("verb", sum(1 for r in res if verb(r["d"])))
print("trig", sum(1 for r in res if TRIG.search(r["d"])))
print("neg ", sum(1 for r in res if NEG.search(r["d"])))
print("tri ", sum(1 for r in res if verb(r["d"]) and TRIG.search(r["d"]) and NEG.search(r["d"])))
```

### 7.3 技能双源分歧（§5.2）

```python
import re, json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\Administrator\agent"
sm = json.load(open(os.path.join(root, "data", "skills_mgmt.json"), encoding="utf-8"))
man = json.load(open(os.path.join(root, "data", "capability_manifest.json"), encoding="utf-8"))
ids = [e["tool_name"] for e in man["entries"] if e.get("kind") == "skill"]
both = neq = 0
for sid in ids:
    p = os.path.join(root, "data", "skills_repo", sid, "skill.md")
    md = re.search(r"^description:\s*(.*)$", open(p, encoding="utf-8").read(), re.M)
    d_repo = md.group(1).strip().strip('"') if md else None
    d_mgmt = (sm.get(sid) or {}).get("description")
    if d_repo is not None and d_mgmt is not None:
        both += 1
        neq += (d_repo != d_mgmt)
print("both_tracks", both, "divergent", neq)   # 15 15
```

### 7.4 工具 YAML vs 代码字面量（§5.1-A）

```python
import ast, glob, os, sys, yaml
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\Administrator\agent"
yaml_desc = {}
for p in sorted(glob.glob(os.path.join(root, "data", "tool_definitions", "*.yaml"))):
    d = yaml.safe_load(open(p, encoding="utf-8")) or {}
    if d.get("name"): yaml_desc[d["name"]] = str(d.get("description") or "")
code = {}
files = glob.glob(os.path.join(root, "agent", "**", "*.py"), recursive=True)
for p in files:
    try: tree = ast.parse(open(p, encoding="utf-8").read())
    except Exception: continue
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call): continue
        f = n.func
        if not isinstance(f, ast.Attribute) or f.attr not in ("register", "register_dynamic"): continue
        if "_planning_tools" in ast.dump(f): continue
        if not n.args: continue
        a0 = n.args[0]
        if not (isinstance(a0, ast.Constant) and isinstance(a0.value, str)): continue
        desc = None
        if len(n.args) > 1 and isinstance(n.args[1], ast.Constant): desc = n.args[1].value
        for kw in n.keywords:
            if kw.arg == "description" and isinstance(kw.value, ast.Constant): desc = kw.value.value
        if isinstance(desc, str): code.setdefault(a0.value, (desc, p, n.lineno))
print("yaml", len(yaml_desc), "code", len(code),
      "mismatch", sum(1 for k, v in code.items() if k in yaml_desc and v[0] != yaml_desc[k]))
# yaml 91 code 85 => 差 6 条走 agent/knowledge/tools.py:253 的 _TOOL_DEFS 循环表
```

### 7.5 动态召回实际返回（§2.3）

```python
import importlib.util, os, sys
sys.stdout.reconfigure(encoding="utf-8")
root = r"C:\Users\Administrator\agent"
sys.path.insert(0, root)
spec = importlib.util.spec_from_file_location("tr", os.path.join(root, "agent", "tool_router.py"))
m = importlib.util.module_from_spec(spec); sys.modules["tr"] = m; spec.loader.exec_module(m)
for q in ("你好", "读取 data/config.json 并总结内容", "把这批任务并行派发给多个子代理"):
    r = m.get_tools_for_input(q)
    print(len(r), r[:8])
```

---

## 8. 本报告的局限

1. **设计文档 4.1 原文不可得**（§4.1 已给检索证据），第 4 节的"动词/触发句式/三段式"判据是本报告自定口径，换判据数字会变；但**结构性事实**（91 条里 31 条超 100 字被截断、10 条写了边界、技能侧 0 条写边界）与判据无关。
2. **未启动服务**，故 `hybrid_select_tools` 的真实召回质量（BM25/Embedding 融合后命中哪些）未测；§2.3 的动态召回数字走的是**关键词兜底路** `get_tools_for_input`（hybrid 不可用时的回退路），hybrid 路的返回数同样受 `max_tools=25` 约束，量级一致。
3. **§5.3 关于向量索引未随 skill.md 变更失效的推断标注为【推测】**（`vector_adapter.py:349-353` + `:414-415` 的代码路径推断），未实机验证。
4. 全仓 `agent/` 有 622 个 .py；本报告 AST 扫描覆盖 `agent/` + `plugins/` 下的 `register` 调用，未覆盖仓库根 `main.py` / `app_server.py` 等旁路注册（已确认 `app_server.py` 无 `register(` 站点）。
