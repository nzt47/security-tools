# Q4 — DeepSeek 调用封装：tools 注入点 与 缓存命中率计量

> 审计对象：云枢（Yunshu）Windows 单机 AI Agent，仓库根 `C:\Users\Administrator\agent`
> 审计日期：2026-09-25　审计编号：Q4
> 方法：只读静态取证 + 允许范围内的轻量只读命令；所有数字来自实际读取/执行，不引用文档声称值。
> 证据约定：`路径:行号`；无证据的推断显式标注 **【推测】**。

---

## 0. 执行摘要（先看这 6 条）

| # | 结论 | 关键证据 |
|---|---|---|
| 1 | **tools 注入点共 4 处**（生产可达 3 处），无统一收口 | `orchestrator.py:3566`、`tool_calling.py:708`、`plugins/chat.py:1283`、`memory/llm_service.py:432` |
| 2 | 当前**不是全量工具**，而是**动态子集**（白名单 → 实测 26 个） | `tool_calling.py:277` → `get_tool_defs(whitelist=)`；实测 `defs=26` |
| 3 | **不存在** 名为「设计文档 3.5 / B 方案」的文档；`§3.5` 与「B 方案」在本仓库检索均 0 命中 | 见 §3.1 检索证据 |
| 4 | 「稳定前缀 / 易变后缀」分节**已实现，但只覆盖 system_prompt 与 messages，不覆盖 `tools`** | `prompt_builder.py:270-302`、`persona_injector.py:42-77`、`system_prompt_config.py:436-439` |
| 5 | **DeepSeek 服务端前缀缓存命中率：完全无计量**。`prompt_cache_hit_tokens` 在 `agent/` 源码 **0 命中** | grep 证据见 §4.1；唯一裸值在 `_tmp_rootcause_probe/evidence/` |
| 6 | **logprobs 完全未接入**：请求不传、响应不读；`logprob` 全仓库 **0 命中** | grep 证据见 §5.1 |

---

## 1. 真正的生产调用链（HTTP → DeepSeek HTTP 请求）

### 1.1 生产配置（实测读 `.env`）

```
LLM_PROVIDER=DeepSeek
LLM_MODEL=deepseek-flash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-2ccd…（已脱敏）
```

⇒ 生产走 `memory/llm_service.py::LLMService`（base_url 取 `LLM_BASE_URL`，见 `memory/llm_service.py:64-67`），
**不是** `agent/model_router/adapters.py` 的 `OpenAIAdapter`（那条是 judge 旁路，见 §1.4）。

### 1.2 主链路 A：`POST /api/chat`（编排器，非流式）

| 跳 | 文件:行号 | 函数 / 关键语句 |
|---|---|---|
| 1 | `plugins/chat.py:176-177` | `@bp.route("/api/chat", methods=["POST"])` → `def api_chat()` |
| 2 | `plugins/chat.py:280-284` | `response = _Yunshu.chat(user_input, session_id=…, session_mgr=…)` |
| 3 | `agent/orchestrator/orchestrator.py:570` | `def chat(self, user_input, *, session_id, session_mgr) -> str` |
| 4 | `orchestrator.py:591-595` | `result = self.process(user_input, session_id=…, session_mgr=…)` |
| 5 | `orchestrator.py:614` | `def process(self, user_input, **kwargs) -> dict` |
| 6 | `orchestrator.py:1321` | **分叉**：`if self._v2_lifetrace and self._trace_recorder:` |
| 6a | `orchestrator.py:1325` | V2 → `self._call_llm_v2(...)` |
| 6b | `orchestrator.py:1334` | V1 → `self._call_llm(...)` |

**V2 分支（Persona + ToolCallingService）**

| 跳 | 文件:行号 | 函数 / 关键语句 |
|---|---|---|
| 7 | `orchestrator.py:4010` | `def _call_llm_v2(...)` |
| 8 | `orchestrator.py:4036-4041` | `self._persona_injector.build_system_prompt(...)` |
| 9 | `orchestrator.py:4129-4134` | `self._tool_calling_service.chat_with_steps(..., tools_whitelist=tools_whitelist, ...)` |
| 10 | `agent/tool_calling.py:265` | `def chat_with_steps(self, messages, system_prompt, max_tokens, temperature, tools_whitelist, on_step)` |
| 11 | `tool_calling.py:277` | `tool_defs = tools.get_tool_defs(whitelist=tools_whitelist)` ← **tools 构造点 ①** |
| 12 | `tool_calling.py:353` | `need_tools = tool_defs if round_idx < self._max_rounds else None` |
| 13 | `tool_calling.py:363-366` | `response = self._call_llm_with_tools(working_messages, system_prompt, max_tokens, temperature, need_tools)` |
| 14 | `tool_calling.py:657` | `def _call_llm_with_tools(self, messages, system_prompt, max_tokens, temperature, tool_defs)` |
| 15 | `tool_calling.py:684` | `client = self._current_llm._get_client()` |
| 16 | `tool_calling.py:687-688` | `return self._call_llm_openai(client, messages, system_prompt, max_tokens, temperature, tool_defs)` |
| 17 | `tool_calling.py:693` | `def _call_llm_openai(...)` |
| 18 | `tool_calling.py:707-708` | `if tool_defs: kwargs["tools"] = tool_defs` ← **tools 注入点 ②** |
| 19 | `tool_calling.py:710` | `response = client.chat.completions.create(**kwargs)` ← **DeepSeek HTTP** |

**V1 分支（标准路径）**

| 跳 | 文件:行号 | 函数 / 关键语句 |
|---|---|---|
| 7' | `orchestrator.py:3258` | `def _call_llm(self, user_input, body_status, *, ...)` |
| 8' | `orchestrator.py:3434-3445` | `_whitelist = self._get_enabled_tools_whitelist()` → `line_whitelist(_whitelist)` |
| 9' | `orchestrator.py:3450-3455` | `hybrid_select_tools(user_input, _whitelist) or get_tools_for_input(...)` |
| 10' | `orchestrator.py:3458` | `_tool_defs = _tools.get_tool_defs(whitelist=_whitelist)` ← **tools 构造点 ③** |
| 11' | `orchestrator.py:3466-3469` | `_tool_defs = prune_tool_defs(_tool_defs, ...)`（Schema 裁剪，**仅此路径有**） |
| 12' | `orchestrator.py:3566` | `_kwargs["tools"] = _tools_this_round` ← **tools 注入点 ④** |
| 13' | `orchestrator.py:3568` | `_resp = _client.chat.completions.create(**_kwargs)` ← **DeepSeek HTTP** |

### 1.3 主链路 B：`POST /api/chat/stream`（Web UI 工作台，流式）— **旁路，独立实现**

| 跳 | 文件:行号 | 函数 / 关键语句 |
|---|---|---|
| 1 | `plugins/chat.py:1414-1415` | `@bp.route("/api/chat/stream", methods=["POST"])` → `def api_chat_stream()` |
| 2 | `plugins/chat.py:1461` | `for _evt in _workbench_real_stream(question, session_id):` |
| 3 | `plugins/chat.py:1035` | `def _workbench_real_stream(question, session_id="")` |
| 4 | `plugins/chat.py:1051-1054` | 直接读 `.env`：`LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` |
| 5 | `plugins/chat.py:1088-1091` | `llm = LLMService(provider=…, api_key=…, model=…, timeout=60, base_url=…)` |
| 6 | `plugins/chat.py:1168-1197` | 自建工具选择链：`line_whitelist` → `hybrid_select_tools` → `_get_defs(whitelist=_whitelist)` |
| 7 | `plugins/chat.py:1199-1200` | `tool_defs = prune_tool_defs(tool_defs) or tool_defs` |
| 8 | `plugins/chat.py:1277-1284` | `stream_kwargs = dict(..., tools=tool_defs)` ← **tools 注入点 ⑤** |
| 9 | `plugins/chat.py:1287` | `for text_piece in llm.chat_stream(**stream_kwargs):` |
| 10 | `memory/llm_service.py:374` | `def chat_stream(...)` |
| 11 | `memory/llm_service.py:431-432` | `if tools: create_kwargs["tools"] = tools` ← **tools 注入点 ⑥** |
| 12 | `memory/llm_service.py:433` | `stream = client.chat.completions.create(**create_kwargs)` ← **DeepSeek HTTP** |

> ⚠️ **这条路径完全绕过 `Orchestrator.process()`**：不经过 `_v2_lifetrace` 分叉、不经过 `ToolCallingService`、
> 不经过 `tools_prompt_guard` 之外的任何编排层，自建 `LLMService` 实例（`plugins/chat.py:1088`）。

### 1.4 旁路清单（不经过 `/api/chat` 或 `/api/chat/stream`）

| 旁路 | 位置 | 是否带 tools | 直连 DeepSeek? |
|---|---|---|---|
| `/api/news` 翻译 | `app_server.py:886-887` `_DS_KEY`/`_DS_URL` | ❌ | ✅ **裸 HTTP**，跳过全部封装 |
| 记忆摘要 | `memory/llm_service.py:209-213` `_do_summarize` | ❌ | ✅ `client.chat.completions.create` |
| 记忆/摘要对话 | `memory/llm_service.py:296-309` `_do_chat` | ❌ | ✅ |
| LLM-judge（digestion） | `agent/digestion/shadow.py:921` `ModelAdapterFactory`；`judge_runtime.py:124` 凭证 env | ❌ | ✅ 经 `OpenAIAdapter` |
| **OpenAIAdapter（deepseek）** | `agent/model_router/adapters.py:36-38`（`"deepseek": "https://api.deepseek.com/v1"`）；HTTP 于 `:127/:161/:422/:451` | ❌ | ✅ |
| 过程蒸馏 | `agent/process_distill/service.py:60-64` | ❌ | ✅ |
| 记忆管理器 | `memory/memory_manager.py:262` | ❌ | ✅ |
| 插件管理 | `plugins/admin.py`（构造 `LLMService`） | ❌ | ✅ |
| 子代理 / 任务派发 | `agent/orchestrator/task_dispatcher.py` | 【推测】见下 | ✅ |

**答「是否只有一条链路」：否。** 至少有 **2 条 chat 链路 + 7 条以上旁路**。
且 `adapters.py:36-38` 的 `OPENAI_COMPATIBLE_BASE_URLS` 只被 judge 侧使用 —— `OpenAIAdapter` 的构造点仅 `adapters.py:585,590`，
调用方只有 `agent/digestion/shadow.py:921` 与 `tests/**`、`scripts/**`（grep 结果），**不在对话主链路上**。

---

## 2. tools 参数在哪一行被构造并传入？

### 2.1 生产**主**路径（V2，`_call_llm_v2` → `chat_with_steps`）

**构造点** — `agent/tool_calling.py:277`：

```python
277:        tool_defs = tools.get_tool_defs(whitelist=tools_whitelist)
```

**注入点** — `agent/tool_calling.py:693-711`（原文）：

```python
693:    def _call_llm_openai(self, client, messages, system_prompt,
694:                          max_tokens, temperature, tool_defs):
695:        """OpenAI 兼容格式的 LLM 调用"""
696:        api_messages = []
697:        if system_prompt:
698:            api_messages.append({"role": "system", "content": system_prompt})
699:        api_messages.extend(messages)
700:
701:        kwargs = {
702:            "model": self._current_llm.model,
703:            "messages": api_messages,
704:            "max_tokens": max_tokens,
705:            "temperature": temperature,
706:        }
707:        if tool_defs:
708:            kwargs["tools"] = tool_defs
709:
710:        response = client.chat.completions.create(**kwargs)
711:        return response.choices[0].message
```

### 2.2 V1 标准路径

`agent/orchestrator/orchestrator.py:3557-3568`（原文）：

```python
3557:                    _kwargs = {
3558:                        "model": _working_model,
3559:                        "messages": _api_msgs,
3560:                        "max_tokens": _max_output,
3561:                        "temperature": 0.3,
3562:                    }
3563:                    # `_tools_this_round` 为空 ⇒ 不写 tools 键（= 原 pop 的效果），
3564:                    # 且此时提示词已被上面的守卫中和，两侧口径一致。
3565:                    if _tools_this_round:
3566:                        _kwargs["tools"] = _tools_this_round
3567:
3568:                    _resp = _client.chat.completions.create(**_kwargs)
```

### 2.3 判据：全量 还是 动态子集？

**结论：动态子集（白名单过滤），且工具数由「主线装配 / 智能路由」动态决定。**

	exttt{get_tool_defs} 的分支判据 —— `agent/tools/__init__.py:679-714`：

```python
679:    hidden = _hidden_tool_names()
680:    # 无白名单时使用缓存
681:    if whitelist is None:
682:        global _get_tool_defs_cache
...
707:    # 有白名单时不使用缓存，实时计算
708:    defs = []
709:    for name, tool in _registry.items():
710:        if whitelist and name not in whitelist:
711:            continue
712:        if name in hidden:
713:            continue
```

**实测数字（本审计实跑既有只读探针 `_tmp_rootcause_probe/probe_fix_before_after.py`）：**

```
[bootstrap] ok=18 bad=1 defs=26
```

| 口径 | 数值 | 来源 |
|---|---|---|
| `line_whitelist(None)` → `get_tool_defs(whitelist=wl)` 实测 defs | **26** | 实跑 `probe_fix_before_after.py:100-105` 输出 |
| 系统提示词自我宣传的工具数 | **86**（「全部已启用（共 86 个）」） | `evidence/probeC_C3_POSITIVE_raw_with_tools_1790331138.json:8` |
| `data/tool_definitions/*.yaml` 文件数 | **91** | `Get-ChildItem` 实测 |

> ⚠️ **口径不一致（实测）**：提示词说「86 个已启用」，实际下发 `tools` 仅 **26 个**。
> 这与 `agent/tools_prompt_guard.py` 存在的理由同源（"提示词宣传 ≠ 实际下发"），
> 但守卫只判「有/无工具」，**不判数量一致性**。

**历史漂移（重要）**：`docs/工具集评估与重分类报告.md:324` 曾记录
Web UI 路径「`get_tool_defs()` **无白名单 → 全量 74 个 ≈ 11.2k token/轮**」。
**当前代码已修复**：`plugins/chat.py:1155-1197` 已改为白名单链路（代码注释 `:1155-1158` 自述该问题）。
该报告的行号（`chat.py:968/1090`、`orchestrator.py:1300/1309/3411`）**已与现行代码不一致**，引用时需以本节实测为准。

---

## 3. 「设计文档 3.5 的 B 方案」落点

### 3.1 前置事实：该文档在本仓库**不存在**

检索证据（全仓库，含 `docs/`、`.claude/`、`reports/`）：

| 检索词 | 命中数 | 结论 |
|---|---|---|
| `方案 ?B` / `B 方案`（限 `docs/`） | 40+，**全部**为 Docker / ADR / 循环依赖等无关主题 | ❌ 无 tools 相关 B 方案 |
| `Top-3` / `Top3` / `详情卡` / `域目录` / `域目录级`（全仓库） | 26，**全部**为看板卡片 / 检索评测 Top-3，**无一条**描述「tools 固定为域目录级 + Top-3 详情卡」 | ❌ |
| `稳定前缀` / `易变后缀` / `易变节`（全仓库） | 12，全部指向 `system_prompt_config.py:437`、`IdentityPromptPanel.tsx:86` 等 **system_prompt** 分节，**无一条**涉及 `tools` 字段 | ⚠️ 概念存在，但作用域不含 tools |

**⇒ 无法给出「B 方案落点具体在哪一行」，因为它在本仓库没有对应实现。**
下述落点均为 **【推测】**，基于现有代码结构推导，非实测。

### 3.2 【推测】若要实现该方案，最自然的落点

| 落点 | 文件:行号 | 现状 | 【推测】需改成 |
|---|---|---|---|
| tools 构造 | `agent/tool_calling.py:277` | `get_tool_defs(whitelist=tools_whitelist)` 全量详情 | 改为「域目录级 def」+ 取 Top-3 完整 schema |
| tools 注入 | `agent/tool_calling.py:707-708` | 直接写 `kwargs["tools"]` | 同上，注入目录级 def |
| Top-3 详情卡落点 | `agent/tool_calling.py:696-699`（`api_messages` 组装） | system + messages，无 few-shot 工具详情 | 在 **易变尾部**（`messages` 末、`user` 前）插入详情卡 |
| V1 对照 | `orchestrator.py:3458`、`:3566` | 同上 | 同步改，否则两路径再次分裂 |
| 流式对照 | `plugins/chat.py:1197`、`:1283` | 同上 | 同步改 |
| 同源风险点 | `orchestrator.py:3503-3505` | few-shot 注入已插在 `len(_working)-1`（user_input 前）——**位置语义与 B 方案一致** | 可复用该注入位 |

**实现该方案需要改的文件（最少 4 个）**：
`agent/tool_calling.py`、`agent/orchestrator/orchestrator.py`、`plugins/chat.py`、`agent/tools/__init__.py`；
若要做「域目录」还需 `agent/tool_router.py:308` 的 `TOOL_CATEGORIES`（11 类）或 `data/tool_definitions/*.yaml` 的域归并。
配套回归：`tests/unit/test_prompt_cache_order.py`、`tests/unit/test_tools_prompt_alignment.py`。

### 3.3 「稳定前缀 / 易变后缀」分节：**已存在**，但作用域是 system_prompt / messages

| 实现点 | 文件:行号 | 判据（原文） |
|---|---|---|
| V1 messages 组装 | `agent/orchestrator/prompt_builder.py:244` `def build_context_messages` | `:270` 注释「固定 system 消息前置（提升 LLM 前缀缓存命中率）」；`:271-279` 固定区 `tool_urge` 在 `idx0`；`:281-301` 动态区 budget_context；`:302` `user_input` 置尾 |
| V2 system_prompt 组装 | `persona/persona_injector.py:22` `def build_system_prompt` | `:42` `# ── 固定区（前缀稳定，最大化 Provider 端缓存命中）──`（persona / 表达要求 / tool_status）；`:57` `# ── 动态区（每次请求可能变化）──`（user_context / body_status / memory_context / additional_rules） |
| 模板级分节 | `agent/system_prompt_config.py:436-439` | `# 【不易】渲染顺序即模板注入顺序 = DeepSeek 前缀缓存命中顺序：稳定节（身份/原则/技能指令/工具状态）必须前置，易变节（身体状态/行为模式/记忆线索/日期）必须后置` |
| 稳定块判据 | `system_prompt_config.py:355-357` | `_render_identity`：`不含日期——日期属易变内容…前置会击穿其后全部 DeepSeek 前缀缓存` |
| 易变尾簇判据 | `system_prompt_config.py:374-379` | `_render_current_status`：`DeepSeek 前缀缓存：本簇紧跟稳定块之后，是模板中第一处逐轮变化的内容` |
| 回归锁定 | `tests/unit/test_prompt_cache_order.py:1-12`（docstring）、`:48-73`（V1）、`:103-130`（V2） | 断言 `messages[0]` 为 tool_urge；`idx_persona < idx_expression < idx_tool_status` |
| 契约再声明 | `agent/tools_prompt_guard.py:288-292` | `fixed=[tool_urge@idx0]`「明文锁定的前缀缓存顺序契约」 |
| 前端同源声明 | `yunshu-ui/src/pages/prompt-lab/IdentityPromptPanel.tsx:86` | 「system message 的先后）＝ DeepSeek 前缀缓存命中顺序（稳定节前置、易变节后置）」 |

**关键判据缺口**：上述全部分节的排序对象是 **`system_prompt` 字符串内容** 与 **`messages` 数组顺序**。
`tools` 是 chat/completions 请求体的**独立顶层字段**，其 JSON 序列化位置在 HTTP body 中位于 `messages` **之前**（OpenAI 协议字段序），
⇒ **任何「把易变内容塞进 messages 尾部」的做法都无法保护 `tools` 前缀**。
【推测】这正是 B 方案（把易变详情卡放进 messages 而非 tools）要解决的问题，但本仓库**尚无对应实现或文档**。

---

## 4. 缓存命中率计量现状

### 4.1 服务端前缀缓存（DeepSeek `prompt_cache_hit_tokens`）—— **无计量**

**grep 实测（`agent/` 全部 `.py`）：**

| 检索词 | `agent/` 命中 | 全仓库命中 |
|---|---|---|
| `prompt_cache_hit_tokens` | **0** | 51（**全部**在 `_tmp_rootcause_probe/evidence/*.json`，即一次性探针产物） |
| `prompt_cache_miss_tokens` | **0** | 51（同上） |
| `cached_tokens` | **0** | 51（同上） |
| `cache_read_input_tokens` | 2（`tool_calling.py:828,833`，**Anthropic 专属**） | — |

**说明：**
- 生产链路（`orchestrator.py:3568`、`tool_calling.py:710`、`memory/llm_service.py:433`）**从不读取 `response.usage`**。
- 唯一读 `usage.prompt_tokens` 的地方是 **legacy** `OpenAIAdapter`（`adapters.py:142-144`），**不在对话主链路**。
- 唯一读 `usage.cache_read_input_tokens` 的地方是 **Anthropic 分支** `tool_calling.py:827-849`，且只 `logger.debug`，
  **不落库、不聚合、不出数**（且 DeepSeek 不会返回该字段）。

**⇒ 结论：DeepSeek 前缀缓存命中率目前完全没有计量。既无字段、无表、无指标，无法按天出数。**

### 4.2 DeepSeek 确实返回该字段（实测证据，非文档声称）

`_tmp_rootcause_probe/evidence/` 下 17 个 JSON 为**真实直连 api.deepseek.com 的原始响应落盘**（`probe_fix_before_after.py:86`
`"usage": resp.usage.model_dump()`）。抽取实测值：

| 证据文件（tag） | prompt_tokens | cache_hit | cache_miss | 命中率 |
|---|---|---|---|---|
| `probeC_C3_POSITIVE_raw_with_tools_1790331138.json`（**本审计 2026-09-25 实跑**） | 6888 | **256** | 6632 | **3.72%** |
| `probeC_C3_POSITIVE_raw_with_tools_1789825518.json` | 6899 | **6656** | 243 | **96.48%** |
| `probeC_C3_AFTER_aligned_tools_1789812752.json` | 6967 | 384 | 6583 | 5.51% |
| `probeB_E_prod_asis_tools_1789812557.json` | 6899 | 128 | 6771 | 1.86% |
| `probeC_C2_AFTER_aligned_notools_1789825516.json` | 458 | 256 | 202 | 55.90% |
| `probeC_C1_BEFORE_raw_notools_1789825514.json` | 385 | 251 | 134 | 65.19% |

**可读出的实测规律：**
1. 字段确实存在且可用：`{"prompt_cache_hit_tokens": N, "prompt_cache_miss_tokens": M, "prompt_tokens_details": {"cached_tokens": N}}`。
2. **带 tools 时（`prompt_tokens ≈ 6.9k`）命中率在 1.9%–96.5% 间剧烈抖动**：
   同一 tag、同一 prompt，冷态 3.72% vs 热态 96.48% ⇒ **命中率强依赖调用间隔（前缀缓存 TTL 约分钟级）**，单次采样无意义。
3. **不带 tools 时（`prompt_tokens ≈ 400–460`）命中率 55%–65%**，但绝对量极小（251–256 token），
   因为小 prompt 的有效缓存前缀本身很短。
4. ⇒ **tools 段（`prompt_tokens` 从 ~400 涨到 ~6900，即 tools 约占 6.5k token）才是缓存收益的主战场**，
   而这正是当前唯一没有计量、也没有对齐的地方。

### 4.3 本地 LLM 响应缓存（`agent/llm_response_cache.py`）—— **与上者无关，勿混淆**

| 维度 | 本地 `LLMResponseCache` | 服务端前缀缓存 |
|---|---|---|
| 实现 | `agent/llm_response_cache.py:58 class LLMResponseCache` | DeepSeek 上游行为 |
| 键 | `sha256(prompt)` **全文哈希**，`llm_response_cache.py:103-105` | 前缀 token 序列 |
| 存储 | `MultiLevelCache` 仅 L1 内存，`llm_response_cache.py:79`（`l2_enabled=False`） | 不可见 |
| 统计 | `total_hits/misses/puts/evictions`，`llm_response_cache.py:86-91` | ❌ 无 |
| 命中语义 | **完全命中即跳过 HTTP 调用** | **部分命中，仍发 HTTP** |
| 分节影响 | 「不区分前缀」——测试 docstring 自述 `tests/unit/test_prompt_cache_order.py:10-11` | 强依赖前缀顺序 |

**⇒ 二者是两套完全不同的东西。** 仓库中到处出现的 `cache_hit`（`llm_monitor.py:78`、`policy/decisions.py:411`、
`observability/cost_calibration.py:314`、`monitoring/cost_brake.py:1048-1049`、`health/health_score.py:458-469`）
**全部指本地缓存或策略决策缓存**，**没有一个指 DeepSeek 前缀缓存**。

### 4.4 现有 token 计量链路为什么不能"凑出"命中率

| 跳 | 文件:行号 | 事实 |
|---|---|---|
| 1 | `agent/llm_monitor.py:64-66` | `request_tokens / response_tokens / total_tokens` 三字段 |
| 2 | `llm_monitor.py:450-460` | **本地 tiktoken 估算**：`req_tokens = LLMMonitor.estimate_messages_tokens(messages)` + `estimate_tokens(system_prompt)` + `estimate_tokens(json.dumps(tools)) // 2` |
| 3 | `llm_monitor.py:475-477` | 估算值直接写入 `LLMInteraction` |
| 4 | `llm_monitor.py:245-252` | `_utc.record_cost(tokens_in=interaction.request_tokens, ...)` |
| 5 | `llm_monitor.py:678-715` | `_wrapped_create` 包了 `client.chat.completions.create`，**拿到了 `response_obj`（`llm_monitor.py:685`）却只透传不解析 usage** |

⇒ 成本埋点（`observability/utc.py::record_cost`、`model_router/cost_tracker.py:112-141`）全部建立在**估算值**上，
**既不是服务端真值，也不含 cache 维度**。若要做命中率，**必须先从 `response.usage` 取真值**——这一步目前完全缺失。

---

## 5. logprobs 可得性核验

### 5.1 当前调用是否请求 logprobs？—— **否，且全仓库零引用**

| 核验项 | 结果 | 证据 |
|---|---|---|
| `logprob` 全仓库（含 tests/docs/scripts）grep | **0 命中** | grep 工具全仓库检索 |
| 生产请求参数（V2） | 仅 `model / messages / max_tokens / temperature / tools` | `tool_calling.py:701-708` |
| 生产请求参数（V1） | 仅 `model / messages / max_tokens / temperature / tools` | `orchestrator.py:3557-3566` |
| 生产请求参数（流式） | 仅 `model / messages / max_tokens / temperature / stream / tools` | `memory/llm_service.py:424-432` |
| 响应侧读取 | 只取 `response.choices[0].message`（content / tool_calls / reasoning_content），**从不读 `choice.logprobs`** | `tool_calling.py:711`、`orchestrator.py:3569-3571` |

⇒ **当前既不发 `logprobs`，也不读 `choice.logprobs`。主通道为 0。**

### 5.2 SDK 版本是否支持？

| 口径 | 值 | 证据 |
|---|---|---|
| `requirements.txt` 声明 | `openai==2.40.0` | `requirements.txt:182` |
| `pyproject.toml` 声明 | `"openai>=2.0.0,<3.0.0"` | `pyproject.toml:33` |
| **实际安装版本** | **`2.24.0`** | `pip show openai` → `Version: 2.24.0`，`Location: …\Python312\Lib\site-packages` |
| `venv/` 目录 | **不含 `openai`，且 `venv/Scripts` 下无 `python.exe`** | `Get-ChildItem venv\Lib\site-packages -Filter openai*` → 空；`venv\Scripts -Filter python*` → 空 |
| 启动方式 | 用系统 `python`（`Python312`） | `start_yunshu.bat:28` `cmd /k "python app_server.py"` |

> ⚠️ **实测差异（必须指出）**：`requirements.txt:182` 锁定 `openai==2.40.0`，
> 但**运行环境实际安装的是 `2.24.0`**；仓库自带的 `venv/` 是**空壳**（无 openai、无 python.exe），
> 真正运行的是系统 Python312。**声明版本 ≠ 运行版本。**

**SDK 能力实测**（`inspect.signature(Completions.create)`，Python312 / openai 2.24.0）：

```
openai 2.24.0
logprobs supported: True   top_logprobs supported: True
params: ['self','messages','model','audio','frequency_penalty','function_call','functions',
 'logit_bias','logprobs','max_completion_tokens','max_tokens','metadata','modalities','n',
 'parallel_tool_calls','prediction','presence_penalty','prompt_cache_key','prompt_cache_retention',
 'reasoning_effort','response_format','safety_identifier','seed','service_tier','stop','store',
 'stream','stream_options','temperature','tool_choice','tools','top_logprobs','top_p','user',
 'verbosity','web_search_options','extra_headers','extra_query','extra_body','timeout']
```

⇒ **SDK（2.24.0）支持 `logprobs` 与 `top_logprobs`**，无需升级。
**额外发现**：该 SDK 还支持 `prompt_cache_key` 与 `prompt_cache_retention` —— 这是**显式前缀缓存控制参数**，
当前生产**完全未使用**（grep `prompt_cache_key` 在 `agent/` 0 命中）。

### 5.3 DeepSeek 该模型是否返回？

| 核验项 | 结果 | 依据 |
|---|---|---|
| 生产模型 | `deepseek-flash` | `.env` `LLM_MODEL=deepseek-flash` |
| 官方文档 | 【推测】`logprobs` 为 OpenAI 兼容参数，DeepSeek 官方文档对其支持情况未在本仓库内有任何记录 | 全仓库 0 命中，**无任何实测或文档证据** |
| 实测证据 | **无**。`_tmp_rootcause_probe/evidence/*.json` 的 `usage.model_dump()` 中**无 `logprobs` 字段**（该字段在 `choices[0]`，探针未落盘） | 17 个 evidence JSON 全量检查 |
| 结论 | **无法确认 DeepSeek 是否返回 `choice.logprobs`**；需一次带 `logprobs=True` 的真实请求验证 | — |

> **核验建议（1 次请求，只读）**：对 `https://api.deepseek.com/v1/chat/completions` 发
> `{"model":"deepseek-flash","messages":[…],"logprobs":true,"top_logprobs":5,"max_tokens":16}`，
> 检查 `choices[0].logprobs` 是 `null` 还是对象。这是**唯一**能确认可得性的方式。

---

## 6. 「接入 logprobs 主通道」最小改动清单

> 前提：`§5.3` 的可得性未验证。**建议先做第 0 步（1 次探针），再动生产代码。**

| # | 文件:行号 | 新增/修改 | 风险 |
|---|---|---|---|
| **0** | 新增 1 次只读探针（可复用 `_tmp_rootcause_probe/probe_fix_before_after.py:59-70` 的 `send()`） | 加 `logprobs=True, top_logprobs=5`；落盘 `choices[0].logprobs` | **无**（只读探针）。**必须先做**：若不支持，后续改动全废 |
| 1 | `agent/tool_calling.py:701-708` | `kwargs["logprobs"]=True; kwargs["top_logprobs"]=N`（可加 env 开关 `LLM_LOGPROBS_ENABLED`，默认 0） | 中：**改请求体可能击穿 DeepSeek 前缀缓存**（新增字段改变 body 序列化），需灰度对比命中率 |
| 2 | `agent/tool_calling.py:710-711` | 改为保留 `response` 全对象（当前 `:711` 直接 `return response.choices[0].message`，**丢弃 usage 与 logprobs**） | 中：返回类型变更影响所有调用方（`:363` 的 `response` 消费点） |
| 3 | `agent/orchestrator/orchestrator.py:3568-3571` | 同上，保留 `_resp`（当前只取 `_resp.choices[0].message`） | 低：`_resp` 已是局部变量，改动局部 |
| 4 | `memory/llm_service.py:433`（流式） | `stream_options={"include_usage": True}`（流式取 usage 的前提） | **高**：流式 usage 只在**最后一个 chunk**，需改 `chat_stream` 生成器契约（`:434-449` 的 chunk 聚合逻辑），回归面大 |
| 5 | `agent/llm_monitor.py:462-486` | `create_from_api_call` 增加 `logprobs` 载荷提取（新增字段到 `LLMInteraction`） | 低：`LLMInteraction` 已有 `to_dict()`（`:84-88`）与磁盘回填（`:125-128`），加字段需同步 `allowed` 集合 |
| 6 | `agent/llm_monitor.py:64-66` + `:450-460` | **同批修复 token 真值**：改为优先读 `response.usage.prompt_tokens/completion_tokens`，估算仅作兜底 | 低-中：会让现有成本埋点数值**跳变**（估算→真值），影响 `observability/utc.py::record_cost`、`health/health_score.py` 的历史对比 |
| 7 | `agent/llm_monitor.py:678-715` `_wrapped_create` | 已在 `:685` 拿到 `response_obj`，**此处是接入 logprobs/usage 的最低成本收口点** | 低：该 wrapper 已覆盖 `LLMService._get_client` 产出的全部 client（`:654-721`） |

### 6.1 同批应补的「前缀缓存计量」（比 logprobs 更紧急）

| # | 文件:行号 | 新增/修改 | 风险 |
|---|---|---|---|
| C1 | `agent/llm_monitor.py:477` | `LLMInteraction` 增 `cache_hit_tokens / cache_miss_tokens`；从 `response_obj.usage.prompt_cache_hit_tokens` 取真值 | 低：additive 字段，`to_dict`/`allowed` 同步即可 |
| C2 | `agent/llm_monitor.py:245-252` | `record_cost` 增 `cache_hit_tokens` 形参，落到 `events.v1` payload | 低：payload 白名单在 `agent/observability/events.py:92-98`，需同步加键 |
| C3 | `agent/observability/utc.py:280` 附近 | 按日聚合 `cache_hit_rate = Σhit / Σ(hit+miss)`（现有 `utc_daily()` 已支持日级，`cost_brake.py:29` 引用） | 低 |
| C4 | `agent/monitoring/cost_brake.py:1048-1049` | 现有 `cache_hit_rate` 字段是**策略缓存**，需改名或并列新键，避免口径混淆 | **中**：命名混淆会导致误读（见 §4.3） |
| C5 | `deploy/`（Prometheus 规则） | 新增指标名；**当前 `deploy/` 下 `cache_hit_rate`/`prompt_cache` grep 0 命中** | 低 |

---

## 7. 附：本次审计的副作用声明（必须记录）

为取得「生产工具数」与「缓存命中」的**实测真值**，本次运行了仓库中**既有的只读探针**
`C:\Users\Administrator\agent\_tmp_rootcause_probe\probe_fix_before_after.py`（该文件自述 `:11`「只读，不联网」，
但 `:123` 实际会向 api.deepseek.com 发请求）。

该次运行**新建了 3 个证据文件**（非本报告要求，属意外副作用）：

| 文件 | 时间 |
|---|---|
| `_tmp_rootcause_probe/evidence/probeC_C1_BEFORE_raw_notools_1790331133.json` | 2026-09-25 18:12:13 |
| `_tmp_rootcause_probe/evidence/probeC_C2_AFTER_aligned_notools_1790331135.json` | 2026-09-25 18:12:15 |
| `_tmp_rootcause_probe/evidence/probeC_C3_POSITIVE_raw_with_tools_1790331138.json` | 2026-09-25 18:12:18 |

除上述 3 个 `_tmp_rootcause_probe/` 下的探针产物外，**未修改/创建/删除仓库中任何其他文件**；
未启动服务、未跑 pytest、未 git commit。

---

## 8. 一句话回答审计问题

> **tools 注入点在 `agent/tool_calling.py:708`（主路径）与 `agent/orchestrator/orchestrator.py:3566`（标准路径），
> 另有 `plugins/chat.py:1283` / `memory/llm_service.py:432` 两条流式旁路，共 4 处、无统一收口；
> 传入的是「主线/智能路由白名单 → `get_tool_defs(whitelist=)`」的**动态子集（实测 26 个）**，不是全量。
> 缓存命中率方面：`agent/` 源码对 DeepSeek `prompt_cache_hit_tokens` 的读取为 **0 命中**，
> token 成本链路（`llm_monitor.py:450-460`）用的全是**本地 tiktoken 估算值**，
> **服务端前缀缓存命中率完全没有计量，无法按天出数**；
> 仓库中随处可见的 `cache_hit` 全部指**本地 `LLMResponseCache`（sha256 全文哈希，`llm_response_cache.py:103-105`）**或策略决策缓存，两套东西必须分开看。**
