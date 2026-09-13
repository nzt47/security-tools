# TASK-S9-01 验收报告 — 对话编排「答非所问 + 跨轮串台」修复

> 归属：**S9 真用修复批次**（真用入口阻塞项）
> 上游依据：[`../真用前置_模型凭证核查_20260913.md`](../真用前置_模型凭证核查_20260913.md) §八 D2
> 基线：`master`（`dbac73b4`）｜worktree：`s901`｜验收：2026-09-13

---

## 〇、结论摘要

| # | 验收判据 | 结论 |
|---|---|---|
| 1 | 不串台（两轮字段不相等；本轮无内容返回空） | ✅ **达成**（真机原始输出见 §五.1） |
| 2 | 答得对（「2 加 3 等于多少」→ 含 `5`；不回吐技能正文 / 工具原始 JSON） | ✅ **达成** |
| 3 | 会话隔离（两个 session_id 交替互不污染） | ✅ **达成** |
| 4 | 工具仍可用（真实执行并出现在 `tool_steps`） | ✅ **达成** |
| 5 | 无回归（相关套件全过；公开接口字段名与语义不变） | ✅ **达成** |
| 6 | 不编造（报告片段可从原始输出原样复现） | ✅ 所有片段取自 `.s901_evidence/probe_snapshots.json` 与探针日志 |

**本任务把 D2 拆成三条独立坏支路并逐一修复**（任务书原文只列了两条，第三条是定位过程新增发现）：

| 坏支路 | 现象 | 根因位置（代码行级） |
|---|---|---|
| ① 工具原始载荷当答案 | 「帮我列出当前工作目录下的文件」→ 36074 字符的 `list_directory` 原始 dict repr | `orchestrator.py:1879`（修复前）`"output": str(result.output or "")` |
| ② 技能正文当答案 | 「2 加 3 等于多少？只回答数字」→ 整篇 `# self_reflection` 技能文档 | `orchestrator.py:2297-2299`（修复前）`return {"output": instruction}` + 阈值门控被 rank 归一化架空 |
| ③ 把已答对的短答丢掉 | LLM 正确回答 `5` → 被替换成「抱歉，我暂时无法给出令人满意的回答」 | `orchestrator.py:321`（修复前）`len(response.strip()) < 5` → 低置信度兜底 |

以及任务书已定位的**跨轮串台**：`_last_tool_steps` / `_last_reasoning` 取自全局单例实例属性 + `or` 回退旧值。

---

## 一、交付物

| 类型 | 文件 |
|---|---|
| 新增模块 | `agent/orchestrator/turn_state.py`（会话级 turn state 存储，68 stmts / **100% 覆盖**） |
| 修复 | `agent/orchestrator/orchestrator.py`（三条坏支路 + 串台 + 单一 setter） |
| 修复 | `agent/orchestrator/lifecycle_manager.py`（下线全局属性，改持有会话级 store） |
| 修复 | `agent/server_routes/routes_chat.py`（响应装配按会话读取） |
| 修复 | `plugins/chat.py`（同上；**真机实际生效的 `/api/chat` 是此插件路由**） |
| 新增测试 | `tests/unit/test_orchestrator_turn_state_isolation.py`（25 例，回归锚） |
| 同步更新 | `tests/unit/test_prompt_cache_order.py`、`test_orchestrator_workflow_learning_layer.py`、`test_digital_life_comprehensive.py`、`test_orchestrator_reject.py`、`test_judge_llm_confidence_edge_cases.py`、`test_llm_error_path_recorded.py`、`tests/integration/test_orchestrator三层路由_e2e.py`、`test_digital_life_integration.py` |
| 复验脚本 | `scripts/dev/s901_turn_state_repro.py`（真机连续多轮取证） |
| 文档 | 本报告 + [`S9-01_交付结案报告_20260913.md`](S9-01_交付结案报告_20260913.md) |

---

## 二、现象固化（修复前，真机实测原样输出）

### 2.1 HTTP 真机复现（修复前 master 服务，2026-09-13 02:10）

```
SESSION=s901-pre-3927a6ee
Q=帮我列出当前工作目录下的文件
  tool_steps=
  reasoning=
  response_len=36074
  response_head=<<<{'ok': True, 'path': '.', 'abs_path': 'C:\\Users\\Administrator\\agent', 'type': 'dir',
                   'items': [{'type': 'dir', 'size': 0, 'modified': '2026-07-23 00:12:22', ...
Q=2 加 3 等于多少？只回答数字
  tool_steps=
  reasoning=
  response_len=397
  response_head=<<<# self_reflection

自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进。

## 适用场景
...
```

- ① `response` 是 **`list_directory` 原始返回值的 Python repr**（单引号 + `True` + `abs_path`，36074 字符）；
- ② `response` 是 **`data/skills_repo/self_reflection/skill.md` 的正文**（去掉 front matter 后 397 字符）；
- 两次的 `tool_steps` / `reasoning` 都为空 ⇒ 该会话的串台未复现（全局槽位被中间请求覆盖），
  串台由单测锚点与代码级证据固化（见 §五.1）。

> 说明：任务书 §六 给出的复验命令用 body 字段 `session`，但真机生效的是 **`plugins/chat.py::api_chat`**，
> 它读的是 body 的 **`session_id`**（`plugins/chat.py:148`）。用 `session` 时会被忽略并回落到全局默认会话
> （实测日志 `[SESSION] 会话 ID: sess_20260907_220445_39c3ebf7`）。本报告所有真机复验均用 `session_id`。
>
> **「`/api/chat` 由谁提供」四条独立证据（已核实）**：
> 1. `app_server.py:972-973` 明写 ——「T7：会话交接（原 routes_sessions.register_handoff_routes 已移除，
>    **会话 API 由 plugins/chat.py 提供，此处不再接线**）」；
> 2. `app_server.py` 中 `routes_chat` 出现次数 = **0**（既无 import 也无 `register_routes` 调用；
>    `agent/server_routes/routes_chat.py` 是**已死的注册路径**，仅 `agent/server_routes/__init__.py::register_all_routes`
>    还列着它，而该函数**从未被 app_server 调用** —— `app_server.py:813` 只是一句注释）；
> 3. `app_server.py:137-149` 走 `plugins.loader.register_blueprints(app)` + `app.register_blueprint(_p.blueprint)`，
>    即 `/api/chat` 由插件蓝图注册；
> 4. **日志指纹**：真机响应 `logs` 字段里的 `[CHAT] 开始调用 DigitalLife.chat()` 与 `[CHAT] 响应长度: N 字符`
>    **只存在于 `plugins/chat.py:219/228`**（`routes_chat.py` 无此文案）。
> ⇒ 本任务的响应装配修复必须覆盖 `plugins/chat.py`（已覆盖）；`routes_chat.py` 同步修正是**同源一致性**处理，
> 不改变真机行为。

### 2.2 回归锚（新增用例，修复前必须失败）

修复前 `tests/unit/test_orchestrator_turn_state_isolation.py`：**9 failed / 3 passed**。

```
FAILED ...::Test跨轮串台会话隔离::test_每轮入口清空_同一会话第二轮无内容也不得沿用第一轮
FAILED ...::Test跨轮串台会话隔离::test_v2路径_reasoning为None时不得回退上一轮的reasoning
FAILED ...::Test跨轮串台会话隔离::test_本轮未写入_返回空而非其它会话或上一轮的值
FAILED ...::Test跨轮串台会话隔离::test_会话数上限_有界不无限增长
FAILED ...::Test跨轮串台会话隔离::test_两个会话交替_各自状态互不污染
FAILED ...::Test无or回退旧状态::test_orchestrator源码不得残留全局_last属性读写
FAILED ...::Test无or回退旧状态::test_orchestrator源码不得出现_or_回退上一轮状态
FAILED ...::Test答非所问_语义层误命中::test_低相关度命中_不得短路返回技能文档_必须降级LLM
FAILED ...::Test答非所问_语义层误命中::test_相关度提取函数_只取有界相似度
9 failed, 3 passed
```

修复后同一文件：**25 passed**。

---

## 三、根因（代码行级，不接受"可能/大概"）

### 3.1 坏支路①：工作流学习层把「工具原始载荷」当作最终答案回吐

**生产链（三条行级证据）**：

1. `agent/workflow_learning/executor.py:415`
   `final_output = ctx["prev_output"]` —— 纯工具链工作流的 output 就是**最后一步工具的原始返回值**（dict）；
   同文件 `:447` `output=final_output` 装进 `WorkflowExecutionResult`。
2. `agent/orchestrator/orchestrator.py:1879`（修复前行号）
   `"output": str(result.output or "")` —— 把该 dict **字符串化**（`str(dict)` ⇒ 单引号 + `True` 的 Python repr）。
3. `agent/orchestrator/orchestrator.py:883-888`（修复前行号）
   `output_text = wf_learning_result["output"]` → `ResponseBuilder.success(output_text, msg="handled_by_workflow_learning")`
   —— 直接作为用户可见答案返回。

**数据证据**：`data/learned_workflows.json` 中 `wf-f19dc52c`
（`source_user_input: "帮我列出当前工作目录下的文件"`、`steps: [{tool_name: list_directory, params_template: {path: "."}}]`、
`last_used_at: 2026-09-13T02:10:35`）—— 正是本次复现的提问，命中后 output = `list_directory` 的 dict。

⇒ 与 TASK §1.3 目标 1「工具结果只作为素材，**不得被当作最终答案直接回吐**」直接冲突。

### 3.2 坏支路②：语义层短路把「技能 instruction」当作答案，且阈值门控被 rank 归一化架空

**生产链**：

1. `orchestrator.py:2206-2214`（修复前行号）`instr_data = svc.loader.load_instruction(top1.skill_id)` → `instruction`
2. `orchestrator.py:2297-2299`（修复前行号）`return {"output": instruction, "skill_id": top1.skill_id, ...}`
3. `orchestrator.py:900`（修复前行号）`output_text = semantic_result["output"]` →
   `orchestrator.py:933-935` `ResponseBuilder.success(output_text, msg="handled_by_semantic_layer")`

**为什么一个算术题能命中 `self_reflection`（真机实测，`SkillLoader.match` 直接调用）**：

```
query="2 加 3 等于多少？只回答数字"
  method=rrf
  self_reflection   score=0.9954(rrf_normalized)   tfidf_score=0.1  vector_score=None  bm25_score=3.5184
  （另一次运行）    score=1.0                      tfidf_score=None vector_score=None  bm25_score=3.5184
```

- `min_score = 0.3`（`config.yaml:553`），而复验用的 `top1.score` 是 **RRF 按 rank-1 归一化的融合分**
  （`agent/skills_mgmt/loader.py:1231` `normalized_score = rrf_score / max_possible`），**top1 恒 ≈1.0**
  ⇒ `orchestrator.py:2187`（修复前）`if top1.score < min_score:` 这道「orchestrator 层独立把控阈值」
  **在 RRF 路径上恒不成立，形同虚设**。
- 上游还有一道 `_RRF_QUALITY_MIN = 0.3` 质量门（`agent/skills_mgmt/loader.py:1512-1524`）本意是拒绝
  "全路原始分都很低"的负样本，但它把 **BM25 无界原始分**（3.5184）也算进 `max_raw_score`
  ⇒ 噪声级 tfidf 相似度 0.1 的候选照样过闸。

⇒ 结论：`response` 是技能文档，不是「某处 return 了拼接中间态」，而是**语义层短路的返回值本身就是技能正文**，
且**短路判据被 rank 归一化分架空**。

### 3.3 坏支路③：低置信度兜底把「已经答对的短答」丢掉

`orchestrator.py:321`（修复前行号）`_judge_llm_confidence`：

```python
if not response or len(response.strip()) < 5:
    confidence = "low"; low_reason = "empty_or_too_short"
```

用户明确要求「只回答数字」时，LLM 的**合法**回答就是 `5`（1 字符）⇒ 被判 low ⇒
`orchestrator.py:1264-1290` 提前 return `_FALLBACK_MSG`（"抱歉，我暂时无法给出令人满意的回答…"）。
即：**把已经答对的答案替换成了道歉**，属同一族「答非所问」。

### 3.4 跨轮串台（任务书已定位，此处补代码级确认）

- 读取点：`agent/server_routes/routes_chat.py:428-429`（修复前）`getattr(Yunshu, '_last_tool_steps'/'_last_reasoning')`
  —— 全局单例实例属性，last-write-wins；本轮未写入 ⇒ 返回上一轮（乃至其它会话）的值。
- **`or` 回退注入点**：`orchestrator.py:3441 / 3451`（修复前）
  `self._last_reasoning = _result.get("reasoning") or self._last_reasoning`
  —— 本轮 `reasoning` 为 `None` 时**主动保留**上一轮值。D2 现象「reasoning 与上一轮逐字相同」由此产生。
- 另有 `_current_tool_steps` 也是实例属性，被并发会话共享追加（`orchestrator.py:2998/3009/3037/3047`，修复前）。

---

## 四、修复清单（含行号）

| # | 文件:行（修复后） | 修复内容 |
|---|---|---|
| 1 | `agent/orchestrator/turn_state.py`（新增，全文件） | 会话级 `TurnStateStore`：`begin/set/snapshot/previous`，唯一写入口**显式赋值**（显式 `None` 真实落 `None`）、快照为副本、`OrderedDict` + 上限淘汰最旧（内存有界）、内部锁 |
| 2 | `orchestrator.py:3004/3024/3038/3053` | `_begin_turn` / `_set_turn_state` / `last_turn_state` / `previous_turn_state` + `_turn_state_sessions`：收敛为**单一 setter + 单一读入口**（任务书 §八.2 建议） |
| 3 | `orchestrator.py:562`（`process` 入口） | 每轮入口 `self._begin_turn(_sid)`：本轮槽位先清空（否则走短路路径的轮次会读到上一轮残留） |
| 4 | `orchestrator.py:85/112/123/152/995` | 坏支路①：`_looks_like_tool_payload` / `_render_workflow_material` / `_workflow_turn_steps`；工作流产出为**工具载荷**时**不短路**，把已执行结果作为**素材**注入 system prompt 交给 LLM 转述，并把真实执行的工具记入本轮 `tool_steps` |
| 5 | `orchestrator.py:1062` | 素材下沉时跳过语义层（素材已在手，不再冒险短路） |
| 6 | `orchestrator.py:2360-2400` | 坏支路②：`_bounded_relevance` + `_has_bounded_breakdown` 相关度门控 —— `min_score` 只与**有界相似度**（TF-IDF 余弦 / 向量相似度 / rerank 概率）比较；BM25 无界原始分与 `rrf_normalized` 排名归一化分**不参与**。有界路存在但全部未命中 ⇒ 视为不可信，降级 LLM |
| 7 | `orchestrator.py:3057/3095` | 上述两个静态方法的实现（含"信号不可用则保持既有行为"的向后兼容分支） |
| 8 | `orchestrator.py:397-420` | 坏支路③：`_judge_llm_confidence` 的「过短」判据改为「strip 后为空」，长度不再是置信度依据 |
| 9 | `orchestrator.py:1298-1306` | `_call_llm` / `_call_llm_v2` 新增 `extra_material`（素材注入 system prompt）与 `allow_tools`（工作流已执行过工具 ⇒ 本轮不再暴露工具，**防重复副作用**） |
| 10 | `orchestrator.py` `_call_llm` / `_call_llm_v2` 内部 | 本轮工具步骤改用**局部列表**（原 `_current_tool_steps` 实例属性并发共享）；写状态一律经 `_set_turn_state`，**去掉 3441/3451 的 `or` 回退** |
| 11 | `orchestrator.py:1971`（原行号） | `_learn_workflow_from_interaction` 按会话读取本轮步骤（原读全局槽位，会把别的会话的调用序列学成本会话工作流） |
| 12 | `lifecycle_manager.py:450` | 下线 `_last_tool_steps` / `_last_reasoning`，改为持有 `TurnStateStore` |
| 13 | `routes_chat.py:365-367 / 394-395 / 436-437` | `/api/chat` 响应装配与落库改为按会话读取（**字段名与语义不变**：`tool_steps` / `reasoning`） |
| 14 | `plugins/chat.py:266-271 / 315-316` | 同上（真机生效路由） |

**未改动**：`agent/skills_mgmt/output_guard.py`（S9-02 地盘，一行未动）。

---

## 五、验收逐条（证据命令 + 原始输出）

### 5.0 HTTP 层真机复验（判据 1/2/3/4 一次取全）

**服务**：由 worktree 修复后代码提供服务（`app_server.py` 自带的"启动前清理 5678 端口"逻辑接管端口）。

> ⚠️ **环境前提（必须照做，否则会得到"假阴性"）**：本机 shell 环境无 `LLM_API_KEY`，且运行期有组件会把
> `.env` 的 `LLM_API_KEY` 改写成占位值（实测 worktree `.env` 由 144741 → 145606 字节，key 从 `sk-2cc…` 变为 `sk-test-key`）。
> 直接用 `python app_server.py` 启动时，服务进程拿到的是**占位 key**，DeepSeek 返回
> `401 Authentication Fails, Your api key: ****-key is invalid`，所有 LLM 调用失败 →
> 响应统一变成低置信度兜底文案，**会把「答得对」误判为不达成**。
> 取证时须先用项目自带入口 `agent.env_config_manager.get_env_config_manager().reload()`
> 引导 `.env`，并用**主工作区 `.env`（权威源）**中的真实 key 覆盖 `os.environ` 后再启动。

**判据 1 + 2（同一会话连续两问）**：

```
SESSION=s901-http-2561d026
Q=帮我列出当前工作目录下的文件
  tool_steps=[{"args":{},"id":"wf-f19dc52c","tool":"list_directory","type":"tool_call"},
              {"id":"wf-f19dc52c","status":"success","summary":"{'ok': True, 'path': '.',
               'abs_path': 'C:\\\\Users\\\\Administrator\\\\agent\\\\.worktrees\\\\s901', 'type': 'dir', ...",
               "tool":"list_directory","type":"tool_result"}]
  reasoning=
  response=当前工作目录：`C:\Users\Administrator\agent\.worktrees\s901`

           列出的是目录（未含普通文件，且列表有截断），可见条目如下：

           ```
           Modules/            __pycache__/        agent/
           any/                backup/             backups/
           cache/              cognitive/          config/
           configs/            core/
           ...
Q=2 加 3 等于多少？只回答数字
  tool_steps=
  reasoning=The user asks 2+3, answer only the number. But the system says: 遇任何实操请求，首条回复必须是 tool_calls.
            This is not an 实操请求 — it's a simple math question. Just answer "5". …
  response=5

            ---
            💡 **当前会话上下文即将耗尽**（已使用 27%）。
            点击下方「创建新会话」按钮，我会携带之前的记忆继续对话。
```

- 判据 1：两轮 `tool_steps` **不相等**（2 条 `list_directory` vs 空）；`reasoning` **不相等**（空 vs 本轮新产生的思考）；第二轮**返回空**而非上一轮值 ✅
- 判据 2：`response` = **`5`**（含 `5`）；**不是**技能文档正文；**不是**原始工具 JSON ✅
- 判据 4：第一轮工具**真实执行**并出现在 `tool_steps`（2 条，含真实目录摘要）✅

**判据 3（两个 session_id 交替）**：

```
sessionA=s901-isoA-833b0b   sessionB=s901-isoB-cfec9c
[isoA] 帮我列出当前工作目录下的文件      steps_n=2
[isoB] 2 加 3 等于多少？只回答数字      steps_n=0  reasoning=用户重复问 2+3，只回答数字。前面已答 5。继续答 5。  response_head=5
[isoA] 1 加 1 等于几？只回答数字        steps_n=0  reasoning=The user asks 1+1, only answer the number.        response_head=2

A1.steps_n=2
B.steps_n=0
A2.steps_n=0
A1.steps == A2.steps(A2 是否复用 A 上一轮)=False
B.steps 非空(是否被 A 污染)=False
B 含5=True
A1 响应含原始JSON=False
```

- 判据 3：回到会话 A 的第二问 `steps_n=0`，**未复用** A 上一轮的 2 条；会话 B **未被** A 污染（`steps_n=0`）✅

### 5.1 判据 1 — 不串台

**证据命令**（真机，服务进程外，`worktree s901`）：

```powershell
cd C:\Users\Administrator\agent\.worktrees\s901
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUNBUFFERED='1'
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'
python -u scripts/dev/s901_turn_state_repro.py
```

**原始输出**（`probe_fixed5.log`，2026-09-13 03:xx）：

```
[init] _v2_lifetrace=True  _trace_recorder=True  _tool_calling_service=True
[Q] session=s901-probe-A  question=帮我列出当前工作目录下的文件
  [source]      last_turn_state(session_id)
  [tool_steps]  n=2  [{"type": "tool_call", "tool": "list_directory", "args": {}, "id": "wf-f19dc52c"},
                      {"type": "tool_result", "tool": "list_directory", "id": "wf-f19dc52c",
                       "status": "success", "summary": "{'ok': True, 'path': '.', 'abs_path': ..."}]
  [reasoning]   None
[Q] session=s901-probe-B  question=2 加 3 等于多少？只回答数字
  [tool_steps]  n=0  []
  [reasoning]   "The user asks 2+3, only answer number. The rule says: 遇任何实操请求，首条回复必须是 tool_calls.
                 But this is not an operational request - it's a simple math question. ... Answer: 5."
  [response]    "5\n\n---\n💡 **当前会话上下文即将耗尽**（已使用 37%）。..."
[Q] session=s901-probe-A  question=1 加 1 等于几？只回答数字
  [tool_steps]  n=0  []
  [reasoning]   None
  [response]    "2\n\n---\n💡 **当前会话上下文即将耗尽**（已使用 37%）。..."
==============================================================================
[判据1] tool_steps 两轮相等? False
[判据1] reasoning  两轮相等? False
[判据1] 第二轮本轮无内容时是否为空? tool_steps=[] reasoning="The user asks 2+3, ..."
[判据4] 第一轮 tool_steps 非空(工具真实执行并落账)? True
```

- 两轮 `tool_steps` **不相等**（`[{list_directory…}]` vs `[]`）✅
- 两轮 `reasoning` **不相等**（`None` vs 本轮真机新产生的思考）✅
- 第二轮本轮无工具 ⇒ `tool_steps` **返回空**，未复用上一轮 ✅

**单测锚点**（修复前逐条失败，见 §二.2；修复后 26 例全过）：

```
tests/unit/test_orchestrator_turn_state_isolation.py ..... 25 passed / 26 passed（含新增store单测）
```

### 5.2 判据 2 — 答得对

真机原始输出：`[判据2] 第二轮响应含 '5'? True`；`是技能文档? False`；`是工具原始 JSON? False`（见 §5.1 输出尾部）。
`response` 原样为 `"5\n\n---\n💡 …（当前会话上下文即将耗尽）"` —— 答案本体是 `5`，
尾部那段是本任务范围外的**上下文预算提示**（D4，见 §七）。

同时提供"反向用例"锁定三条坏支路（防判据退化）：

| 用例 | 断言 |
|---|---|
| `test_低相关度命中_不得短路返回技能文档_必须降级LLM` | 真机分数形态（`rrf_normalized=0.9954` / `tfidf_score=0.1`）⇒ 必须调 LLM，响应含 `5`，不含 `# self_reflection` / `适用场景` |
| `test_仅BM25命中_融合分为排名归一化值_不得短路` | 真机形态②（`tfidf_score=None, bm25_score=3.5184, rrf_normalized=1.0`）⇒ 必须降级 LLM |
| `test_工作流输出为工具载荷_不得原样回吐_必须转LLM并记工具步骤` | 工作流产出 `list_directory` dict ⇒ 响应不含 `abs_path` / `'ok': True`；LLM 被调用且收到 `extra_material`、`allow_tools=False`；`tool_steps` 含 `list_directory` |
| `test_高相关度命中_仍短路返回instruction_契约不变` | 真机命中态（`tfidf_score=0.4444`，来自「自我反思一下你的回答」）⇒ 仍短路返回 instruction（既有契约不变） |
| `test_无score_breakdown时_保持既有行为_向后兼容` | 无相关度信号 ⇒ 保持既有阈值行为 |
| `test_工作流输出为文本_仍短路返回_契约不变` | 工作流产出为用户可读文本 ⇒ 仍 0-Token 短路 |
| `test_算术题短答_不得被低置信度兜底替换` / `test_空响应_仍触发低置信度兜底` | 短答 `5` 必须原样返回；空响应仍必须兜底 |

### 5.3 判据 3 — 会话隔离

真机（同一进程内 A/B 交替）：

```
[判据3] 回到会话 A 的第二问（本轮无工具）: tool_steps=[] reasoning=None
[判据3] 是否复用 A 上一轮 tool_steps? False
[判据3] 是否污染会话 B? B.tool_steps=[]
```

单测：`test_两个会话交替_各自状态互不污染`、`test_本轮未写入_返回空而非其它会话或上一轮的值`、
`test_按会话读取_不取其它会话的工具步骤`（`test_orchestrator_workflow_learning_layer.py`）。

### 5.4 判据 4 — 工具仍可用

真机：第一轮 `tool_steps` = 2 条（`tool_call: list_directory` + `tool_result: success`），
且 `summary` 里是**真实目录内容**（`abs_path = C:\Users\Administrator\agent\.worktrees\s901`，`type: dir`）。
修复前该轮 `tool_steps` 为空（工作流短路不经 `_call_llm`，从不写这两个属性）—— **本任务同时把「工具真实执行但不落账」补齐**。

### 5.5 判据 5 — 无回归

**公开接口字段名与语义不变**：`/api/chat` 仍返回 `tool_steps`（list）与 `reasoning`（str|None），
语义由「全局最近一轮」变为「**该会话**最近一轮；本轮无内容为空」—— 这正是 D2 要求的语义修正。

套件（详见 §六）。

### 5.6 判据 6 — 不编造

- 所有真机片段来自 `scripts/dev/s901_turn_state_repro.py` 的输出与
  `.s901_evidence/probe_snapshots.json`（脚本自动落盘的原始快照）；
- 「修复前」片段来自 2026-09-13 02:10 对 master 服务（PID 15184，端口 5678）的
  `POST /api/chat`，命令与输出逐字见 §二.1；
- 未在真机验证的内容一律显式标注（§七）。

---

## 六、质量证据

| 门禁 | 命令 | 结果 |
|---|---|---|
| 回归锚 + 受影响套件 | `pytest tests/unit/test_orchestrator_turn_state_isolation.py tests/unit/test_orchestrator_workflow_learning_layer.py tests/unit/test_prompt_cache_order.py tests/unit/test_orchestrator_reject.py tests/unit/test_judge_llm_confidence_edge_cases.py tests/unit/test_llm_error_path_recorded.py` | **112 passed, 6 skipped** |
| 三层路由 e2e + digital_life | `pytest tests/integration/test_orchestrator三层路由_e2e.py tests/unit/test_digital_life_comprehensive.py tests/integration/test_digital_life_integration.py` | **138 passed, 3 skipped** |
| 邻接回归（unit） | `pytest tests/unit -k "orchestrator or semantic or skill or workflow or digital_life or prompt or confidence or reject or turn_state"` | **2001 passed, 0 failed, 9 skipped, 2 xfailed** |
| 邻接回归（integration） | `pytest tests/integration -k "orchestrator or digital_life or skill or workflow or semantic or route or chat or memory"` | 选中 594 例，**488 passed / 4 skipped / 0 failed**，随后在 `test_digital_life_integration.py` **采集阶段**发生 Windows 原生崩溃（access violation，`exit=-1073741819`，C 扩展环境问题）；该文件**单独运行 47 passed** |
| 覆盖率（新增模块） | `pytest tests/unit/test_orchestrator_turn_state_isolation.py --cov=agent.orchestrator.turn_state` | **68 stmts / 0 miss / 100%** |
| kwarg 扫描 | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **HIGH 0 处**（总计 0 处） |
| kwarg 扫描 | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **HIGH 0 处**（总计 0 处） |
| mypy | `mypy agent/orchestrator/turn_state.py agent/orchestrator/orchestrator.py agent/orchestrator/lifecycle_manager.py --ignore-missing-imports` | 新增模块 **0 error**；`orchestrator.py` 仅剩既存同类错误（host 注入属性 / `Any \| None`），改动行 **0 新错**（与 master 逐类对比） |
| 架构契约 | `lint-imports --config .importlinter` | **Contracts: 2 kept, 0 broken** |
| 产物漂移 | `git status` | 运行时产物（`data/learned_workflows.json`、`data/lifetrace/topics/*`）已还原，见结案报告 |
| pre-commit | 真实提交场景（未用 `--no-verify`） | 见结案报告 |

> mypy 复核方法：对 master 与 `s901` 各取 `文件: 错误码: 消息`（剔除行号）做集合差，
> 新增项仅剩两类 `attr-defined` 的**同一条既有错误的消息措辞差异**（行号位移导致 mypy 提示
> `maybe "request_permission"`），无新增错误类型。

---

## 七、遗留（如实登记、带归属）

| # | 遗留 | 证据 | 归属 |
|---|---|---|---|
| 1 | ~~D1 输出护栏拦截正常回复~~ —— **已由主线修复**（`output_guard.py` 的「显式技能语境」口径）。本任务**一行未动**该文件；本任务期间的进程内探针曾命中一次拦截（`probe_fixed5.log` Round A），但在**最终 HTTP 真机复验**中同一提问已返回正常自然语言目录列表，**未再复现拦截** | `git log -- agent/skills_mgmt/output_guard.py` → 最近改动为 `5a5c8fda`（基线内既有提交，非本任务）；HTTP 复验 §5.0 Round 1 | **S9-02（已修完）** —— 不重复修 |
| 2 | **D4 上下文预算**：`response` 尾部被追加「当前会话上下文即将耗尽（已使用 27%/37%）」；会话累计 token 仍超限 | §5.0 原始输出尾部 | **S9-03** —— 本任务未改预算逻辑；素材注入另设 `ORCHESTRATOR_WF_MATERIAL_MAX_CHARS=6000` 截断，**不触碰预算机制** |
| 3 | **检索层根因未动**：RRF 融合分按 rank-1 归一化（`agent/skills_mgmt/loader.py:1231`）使 top1 恒 ≈1.0；`_RRF_QUALITY_MIN` 质量门（`loader.py:1512-1524`）把**无界 BM25 原始分**计入 `max_raw_score` ⇒ 噪声级候选过闸 | `query="2 加 3 等于多少？只回答数字"` → `tfidf_score=0.1` 仍进入候选 | **S9-03 邻接 / 检索层专项**（本任务在**编排层**加固：`min_score` 只认有界相似度；检索层归一化语义建议专项收敛） |
| 4 | **工作流学习层匹配质量**：`wf-f19dc52c` 由**单轮输入**自动学习（`trigger_patterns` 为单字 `["列","出","当","前","工"]`），并已 `convert_to_skill` 成 `wf-f19dc52c-skill` | `data/learned_workflows.json` | **工作流学习层调优**（本次未改学习/匹配阈值，避免与检索层同批改动） |
| 5 | **工具链工作流不再 0-Token**：产出为工具载荷时改为「素材 → LLM 转述」，代价是 +1 次 LLM 调用（换答案正确） | 设计取舍，见 §四 #4 | 本任务已实现；若需恢复 0-Token，需工作流层定义"用户可读产出"契约 |
| 6 | **worktree 环境事实**：`.worktrees/s901/.env` 原为 **0 字节**（主工作区 `.env` 为 144741 字节），导致 worktree 进程内 LLM 未配置、只能走离线响应；已把主工作区 `.env` 复制进 worktree（`.env` 被 `.gitignore` 忽略，不入库） | `(Get-Item .worktrees\s901\.env).Length` = 0 → 复制后 144741 | 工具链（`scripts/dev/new_session_worktree.py`）—— 建议后续为 worktree 自动链接 `.env` |
| 7 | ~~`tool_traces` 未落账（D3）~~ —— **已由主线修复**（workflow 路径工具轨迹）；本任务**未触碰**该链路，也不重复修 | 主线说明 + 本任务 `git diff` 不涉及 `agent/tool_calling.py` / `agent/observability/tool_trace.py` | **D3（已修完）** —— 不重复修 |
| 8 | **LLM 凭证在工作区/运行期被降级为占位值**：本机 shell 无 `LLM_API_KEY`；运行期有组件把 `.env` 的 `LLM_API_KEY` 改写为 `sk-test-key`（实测 worktree `.env` 144741 → 145606 字节），服务进程遂对 DeepSeek 得到 `401 … api key: ****-key is invalid`，全部 LLM 调用失败 → 响应统一变成低置信度兜底文案，**极易把「答得对」误判为不达成** | §5.0 环境前提 | **凭证/环境专项**（`agent/network_config.py` 的实例 key 归一化与 `env_config_manager` 的写回行为需一并核查）|

---

## 八、双远端 SHA

见 [`S9-01_交付结案报告_20260913.md`](S9-01_交付结案报告_20260913.md) §双远端终态。
