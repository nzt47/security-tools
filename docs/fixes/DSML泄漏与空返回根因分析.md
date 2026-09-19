# DSML 标记泄漏根因分析与修复（TASK-01 · 根因专场）

> 面向：运维 / 后续接手者。
> 本文只回答一件事：**为什么上游会把工具调用写成文本标记，以及平台是怎么把这个触发源消掉的。**
> 症状侧的适配器（第二道防线）另见 `agent/dsml_adapter.py`；本文不重复它。

---

## 1. 一句话根因

**系统提示词向模型宣告了「你有工具」，但同一次请求的 `tools=` 参数为空。**
模型无法走结构化 `tool_calls` 通路，只能用**文本协议**（DSML 标记）表达调用意图；
这段标记随 `content` 原样返回，于是泄漏为用户可见文本。

---

## 2. 取证：触发源被收敛到**提示词里的一行**

### 2.1 三条件对照（上一轮取证，`_baseline/dsml-evidence/cond4_P*.json`）

直连 `api.deepseek.com`，同一 user prompt「列出当前目录下的 Python 文件」：

| 条件 | 提示词是否列出工具名 | `tools=` | 上游响应 |
|---|---|---|---|
| P1 | 列出 | **空（0 个）** | 🔴 DSML 复现（content 162 字，含全角 `U+FF5C`） |
| P2 | 列出 | 26 个 | ✅ 干净（走结构化 `tool_calls`） |
| P3 | 不列 | 空 | ✅ 干净（460 字纯文本推辞） |

⇒ 触发条件 = 「提示词描述了工具」+「`tools=` 为空」。

### 2.2 生产提示词成分拆解（本轮新增，`_tmp_rootcause_probe/probe_prod_prompt_conditions.py`）

P1 用的是**自造**提示词，生产提示词是另一套文本，所以必须再拆一次：
把生产提示词（`agent/system_prompt_manager.get_template()` + 真实渲染的 `{tool_status}`）
逐成分做对照，`tools` 固定为空：

| 条件 | 提示词内容 | `tools=` | 上游响应 |
|---|---|---|---|
| **A** | 生产提示词**原样**（含 `【工具】全部已启用（共 86 个）`） | 空 | 🔴 **DSML 复现**（`execute_command`，自创工具名） |
| **B** | 去掉「执行铁律：…首条回复必须是tool_calls…」 | 空 | 🔴 **DSML 复现**（`execute_shell`） |
| **C** | 清空 `{tool_status}` 段（**只留**执行铁律） | 空 | ✅ **干净**（323 字纯文本推辞） |
| **D** | `{tool_status}` 列出真实工具名 | 空 | 🔴 **DSML 复现**（`list_directory`） |
| **E** | 生产提示词原样 | 26 个 | ✅ 干净（`finish_reason=tool_calls`） |

**结论（两条都很关键）**

1. **充要触发源是 `{tool_status}` 渲染出的 `【工具】…` 那一行**
   （`agent/digital_life_persona.py::_build_tool_status_text`）。
   注意 A 里它只写了「共 86 个」、**并没有列出工具名**，仍然复现
   —— 模型只要"知道自己有工具"，且请求里拿不到 `tools`，就会自创工具名走文本协议。
2. 「执行铁律：首条回复必须是tool_calls」**不是**触发源：C 单独保留它仍然干净，
   B 去掉它仍然复现。它只是**语义上自相矛盾**（要求调用一个不存在的工具通路），
   所以本次一并中和，但它是防御性改动，不是根因修复。

---

## 3. 生产链路上「`tools` 被丢掉」的全部已知位置

提示词是**每次请求渲染一次**的，而 `tools` 由各条链路**逐轮**决定。
两侧口径不同源 ⇒ 只要有一条链路丢 `tools`，不一致就成立。

| # | 位置 | 代码 | 语义 | 处置 |
|---|---|---|---|---|
| 1 | `agent/orchestrator/orchestrator.py:3446` | `if not allow_tools: _tool_defs = []` | 工作流层已执行过工具，本轮不再暴露 | 源头按 `allow_tools` 裁剪 `{tool_status}` + 出网前守卫 |
| 2 | `agent/orchestrator/orchestrator.py:3520`（改前） | `_kwargs.pop("tools", None)` | 工具循环**最后一轮**强制收尾 | 改为按轮算 `_tools_this_round`，出网前守卫对齐 |
| 3 | `agent/tool_calling.py:317` | `need_tools = tool_defs if round_idx < self._max_rounds else None` | 同上（`chat_with_steps` 路径） | 在唯一出网口 `_call_llm_with_tools` 接守卫 |
| 4 | `plugins/chat.py:1194`（改前） | `tools=tool_defs if round_idx == 0 else None` | 工作台流式循环**只首轮**传 tools | 改为每轮都传（这同时修掉"第 2~4 轮永远无法调用工具"的语义缺陷） |
| 5 | `plugins/chat.py:1140`（改前） | 工具定义加载失败 ⇒ `tool_defs` 保持 `None` | 加载降级路径 | 出网口守卫中和 + 收口 `chat_stream` 守卫 |
| 6 | `agent/tool_calling.py:345-353` | 首轮 LLM 失败 → `_current_llm.chat(...)` 纯文本降级 | 该 API **按定义不发 tools** | `memory/llm_service.py::_do_chat` 接守卫 |
| 7 | `agent/orchestrator/orchestrator.py:3485-3494` | Dynamic Few-shot 注入：`build_fewshot_message()` 把**真实工具名 + 调用样本**插进 `messages` | 该段是 `system` 消息 | 守卫按 `TOOL_EXAMPLE_MARKERS` 剔除（`tools` 为空时无条件） |
| 8 | `agent/orchestrator/orchestrator.py:3327` | ContextAssembler 旁路注入 `【可用工具】search, read_file, write_file`（硬编码，与注册表不符） | 该路径默认关（`learning.context_assembler.enabled`） | 标记已纳入守卫；源头未改（见 §7.6） |

> **`tools` 的构造点与提示词注入点是否同源？**
> **不同源。** 提示词侧由 `agent/orchestrator/orchestrator.py:3308 / 3975 / 3988`
> 调 `_build_tool_status_text()` 渲染一次；`tools` 侧由
> `orchestrator:3444`、`tool_calling.py:277`、`plugins/chat.py:1131` 三处**各自**
> 经 `agent.tools.get_tool_defs(whitelist=...)` 构造，且**按轮**还会被置空。
> 这就是缺陷的结构性来源 —— 本次修复就是把两侧用**同一个函数**收口。

---

## 4. 修复

### 4.1 新增唯一收口：`agent/tools_prompt_guard.py`

一条不变量，一个纯函数：

```
「提示词宣传了工具」 ⇒ 「tools 必须非空」
反之（tools 为空）  ⇒ 「提示词不得宣传工具」——就地中和 + WARN event=tools_prompt_mismatch
```

- `prompt_advertises_tools(prompt)`：判定侧。标记分三类，
  见 `agent/tools_prompt_guard.py` 的常量注释：
  - `TOOL_ADVERT_MARKERS = ("【工具】",)` —— **实测证实**的根因标记；
  - `TOOL_ADVERT_ALIKE_MARKERS = ("【可用工具】",)` —— 同类构造（工具名清单），
    来自 `agent/context/assembler.py:195/365`，该路径默认关、未单独定标；
  - `TOOL_HINT_MARKERS = ("提供的工具",)` —— 弱标记，不触发 DSML 但事实为假。
- `neutralize_tool_advertisement(prompt)`：按行替换，**幂等**（中和后的文本不再命中标记），
  复杂度 O(len(prompt))。若确认要中和，顺带把「首条回复必须是tool_calls」这句
  **自相矛盾的硬性命令**一起换掉（`TOOL_IMPERATIVE_MARKERS`，同样只中和不判定）。
- `align_system_prompt_with_tools(prompt, tools_exposed, ...)`：**唯一入口**，
  出网口只调这一个。
- `strip_tool_urge_messages(messages)`：删除催促调用工具的 system 消息
  （「⚡ 立即检查：…直接发起函数调用…」）。**不原地修改入参** ——
  编排器的 `_working` 跨轮累积，原地删会造成"第二轮突然少一条历史"。
  **仅在"确实中和了宣传"的那一趟里调用**：提示词干净时原样返回 `messages`，
  因为那条消息的位置由 `tests/unit/test_prompt_cache_order.py` 锁定，
  且实测（对照 C）它单独不触发 DSML。详见 §6.1。

**设计取舍（为什么只做"中和提示词"这一个方向）**

- **绝不擅自补发工具**：能力边界由主线装配 / 工具闸门决定，
  为了"让提示词一致"而多发工具是越权扩大能力。
- **绝不静默放过**：不一致必须留下 `event=tools_prompt_mismatch`，否则重演本次缺陷
  "线上看不见"的老路。

### 4.2 接入点（5 处，覆盖 §3 全部 6 条路径）

| 文件 | 接入点 | 覆盖 |
|---|---|---|
| `agent/orchestrator/orchestrator.py` | `_call_llm` 每轮出网前（守卫 **先于** `chat.completions.create`） | 路径 1、2 |
| `agent/orchestrator/orchestrator.py` | `_call_llm` / `_call_llm_v2` 提示词源头 `_build_tool_status_text(expose_tools=allow_tools)` | 路径 1（源头就不生成宣传） |
| `agent/tool_calling.py` | `_call_llm_with_tools`（工具循环唯一出网口） | 路径 3、6 |
| `plugins/chat.py` | 工具加载完后的提示词定稿 + 循环每轮带 `tools` | 路径 4、5 |
| `memory/llm_service.py` | `chat_stream`（流式最后收口）、`_do_chat`（按定义无 tools） | 路径 6 + 兜底 |
| `agent/tools_prompt_guard.py` | `strip_tool_urge_messages(..., TOOL_EXAMPLE_MARKERS)` | 路径 7 |

### 4.3 行为变更（需知悉）

1. **工作台流式工具循环现在每轮都带 `tools`**（原来只有第 1 轮）。
   既消除不一致，也恢复了原本声明为「上限 4 轮工具循环」的多轮工具能力。
   轮数上限 `max_tool_rounds = 4` 不变，不会死循环。
2. **`allow_tools=False` 时提示词里不再出现工具状态段**，改为
   「本轮未向模型暴露任何工具（本轮不提供工具调用能力）」。
3. `tools` 非空时**提示词一字不改**（守卫只判定不改写），
   所以正常路径的前缀缓存命中率不受影响。

---

## 5. 复现与修复前后对照（真连上游，非 mock）

探针：`_tmp_rootcause_probe/probe_fix_before_after.py`
（同一 user prompt、同一模型、同一 `tools=` 条件，唯一变量 = 是否经守卫对齐）

| 条件 | `tools` | DSML | `tool_calls` | 上游原文片段 |
|---|---|---|---|---|
| C1 **修复前**（原样生产提示词） | 0 | 🔴 **是** | 0 | `<｜｜DSML｜｜ calls> … invoke name="bash" …`（204 字） |
| C2 **修复后**（经 `tools_prompt_guard` 对齐） | 0 | ✅ **否** | 0 | 「我现在没有可调用的工具，没法直接读取你的目录…」（353 字纯文本） |
| C3 阳性对照（原样提示词 + 26 tools） | 26 | ✅ 否 | **2** | `finish_reason=tool_calls`，走结构化通路 |

对齐日志（真实输出）：

```
event=tools_prompt_mismatch site=probeC direction=prompt_advertised_but_no_tools
tools=0 proven_marker=True neutralized_lines=2 stripped_urge=0 prompt_len=925
```

⇒ 修复前复现、修复后同条件不再产生 DSML，且带工具时的正常通路未受影响。
（`neutralized_lines=2`：1 行 `【工具】…` + 1 行「首条回复必须是tool_calls」祈使句。）

---

## 6. 回归测试

`tests/unit/test_tools_prompt_alignment.py`（**42 passed**，与上一轮的
`tests/unit/test_llm_response_parsing.py` 共 58 passed）分 8 组：

1. 判定侧（含**反例**：只留执行铁律不算宣传，避免噪声告警；
   另含 `context/assembler.py` 的 `【可用工具】` 同类宣传）
2. 中和侧（幂等 / 保留其余文本 / 祈使句一并清理 / 只有祈使句时不动作）
3. 对齐总入口（含 WARN `event=tools_prompt_mismatch` 断言；
   含"干净提示词下**必须原样返回 messages**"——锁定前缀缓存顺序契约）
4. 催促消息处理（不原地改入参、非 system 消息不误删）
5. **真实生产提示词**集成（防止提示词改版后标记失效 ⇒ 假绿）
6. 源码级锁定：4 条丢弃 `tools` 的路径都接了守卫；
   工作台不得退回「只首轮带 tools」；**守卫必须写在出网调用之前**；
   工作台硬编码提示词必须被宣传标记覆盖
7. 出网口集成：直接断言 `client.chat.completions.create` 的**真实 kwargs** 两侧口径一致
8. 性能：100KB 提示词对齐 < 50ms；两倍长度不超过 6 倍耗时（防超线性）

### 6.1 一次真实回归（记录在案）

首版守卫在"提示词干净 + `tools` 为空"时也会剔除催调用工具的 system 消息，
打红了 `tests/unit/test_prompt_cache_order.py::TestOrchestratorV1MessagesOrder::test_v1_messages_order`
（该用例明文锁定 `fixed=[tool_urge@idx0]` 的前缀缓存顺序契约）。

**判定：测试是对的，守卫过头了。** 依据有二：
① 实测对照 C 证明「催促语句单独存在」不触发 DSML ⇒ 它不是根因；
② 动了它会让 DeepSeek 前缀缓存整段失效，代价远大于收益。
**处置：只在"确实中和了宣传"的那一趟里才剔除催促消息**；
提示词干净时原样返回 `messages`，并补一条用例把这个决定钉住。

---

### 6.2 `tests/unit` 全量与基线对照

命令与 `failures_baseline.txt` 同口径（**必须带 `-p no:randomly`**：本仓装了
pytest-randomly，随机序会让 `test_llm_monitor_singleton` 之类的全局单例用例忽红忽绿）：

```powershell
python -m pytest tests/unit -q --no-header -p no:cacheprovider -p no:randomly --timeout=300
```

| 口径 | 结果 |
|---|---|
| **修复后实测** | **4 failed / 19028 passed / 318 skipped / 18 xfailed / 4 xpassed**（1:04:35） |
| `failures_baseline.txt` 基线（7 条） | **7 条全部已消失**（本轮 0 条命中基线） |
| 本轮 4 条失败的归属 | 全部是 §7.7 的 Windows GBK 解码环境问题（`PYTHONUTF8=1` 后 28/28 全绿），**与本修复无关** |

⇒ **本次改动新增失败数 = 0。**

## 7. 遗留与不确定性

1. **`agent/tool_calling.py:317` 的"最后一轮不带 tools"仍然存在**（未改该行的语义），
   只是出网前提示词会被中和。若要彻底改成"最后一轮也带 tools"，
   属于产品语义决策，未在本次范围内。
2. **上游行为存在随机性**：DSML 不是每次必现（同一条件偶发不吐标记）。
   本次对照为单次取样，故 §5 的结论以"修复前复现 + 修复后不复现"的同批对照为准，
   不做统计显著性声明。
3. **`data/` 下 393 处存量 DSML 未清理**：那是缺陷运行期的落盘产物，
   本次只消除触发源，不回写生产数据（D6：不碰生产数据）。
4. **`【技能】` 行未纳入宣传标记**：技能不可被 LLM 当工具调用
   （`agent/tools/__init__.py` 无 skill 注册），对照实验 C 也把 `【技能】` 一并清空，
   故未单独定标。若将来技能改走工具通路，需要重新定标。
5. **`agent/system_prompt_manager.py::DEFAULT_TEMPLATE` 是第二条潜在宣传路径。**
   它比 `data/system_prompt.txt`（当前生效）激进得多：第 29/30/31 行**硬编码**了
   `web_search` / `read_file` / `write_file` / `edit` / `shell_execute` / `run_tests` / `git`
   等工具名与一条"工具铁律"。当前该模板**未生效**（文件存在且非空 ⇒ `get_template()`
   优先读文件），但它含 `{tool_status}` 占位符 ⇒ **已被 `【工具】` 标记覆盖**，
   `tools` 为空时会被整段中和。残留的硬编码工具名行未被中和（按对照 C 的结论，
   单独的能力陈述不触发 DSML）。**若某次 `reset_template()` 或全新部署让该模板生效，
   建议重新定标一次。**
6. **`agent/context/assembler.py` 旁路注入的 `【可用工具】` 已纳入标记，但该路径默认关**
   （`learning.context_assembler.enabled`），故未在真实链路上单独定标；
   另注：它注入的清单是**硬编码且与注册表不符**的（`search` 并不存在），
   属于独立缺陷，未在本次范围内。
7. **`tests/unit` 全量环境说明（已定位到编码，非命名管道）**：
   本机全量跑（与基线同口径 `-p no:randomly`）剩 **4 条**失败 ——
   `test_preflight_runner.py`（3 条）+ `test_ci_l3_context_preflight.py`（1 条）。
   根因：它们用 `subprocess.run(..., capture_output=True, text=True)` 捕获子进程输出，
   `text=True` 在 Windows 上按**宿主默认 ANSI 代码页（GBK）**解码，
   而 `agent/preflight.py` 输出 UTF-8 中文 ⇒ 读取线程抛
   `UnicodeDecodeError: 'gbk' codec can't decode byte ...` ⇒ `proc.stdout` 变成 `None`，
   断言报 `argument of type 'NoneType' is not iterable`。

   **实测证明与本修复无关且非代码缺陷**：
   ```powershell
   $env:PYTHONUTF8="1"
   python -m pytest tests/unit/test_preflight_runner.py tests/unit/test_ci_l3_context_preflight.py -q
   # → 28 passed（全绿）
   ```
   ⇒ 开启 UTF-8 模式后 4 条全部通过；Linux CI（UTF-8 locale）上本就不出现。
   **不计入本次回归。**
8. **⚠️ 已定位、未修复（超出本次允许改动的文件范围）：过程蒸馏子代理路径。**
   `agent/process_distill/distiller.py:169-172` 用 `llm.chat(...)`（**按定义不发 tools**）
   发送 `DISTILL_USER_TEMPLATE`，其中 `可用工具提示: {tool_hint}` 由
   `agent/process_distill/prompts.py:55 build_tool_hint()` 渲染成
   `` `tool_a`、`tool_b`…（共 N 个） `` —— **一份真实工具名清单**，
   与对照实验 D 的触发形态同类（`available_tools` 非空时可达）。
   **影响面有限**：该子代理的输出要按 JSON 契约解析，解析失败会走规则降级
   （`distiller.py:193-196`），DSML 文本**不会直接呈现在用户界面**；
   影响是"蒸馏静默降级"。
   **建议修法（归 TASK-04/后续）**：在 `DISTILL_USER_TEMPLATE` 里把 `tool_hint`
   的语义写死为"**仅供文本映射参考的名字表，你没有可调用的工具**"，
   或让 `distiller` 在调用前显式声明无工具。
   **注意不要**直接把这一行纳入守卫中和：蒸馏需要这些名字做**文本映射**，
   中和掉会让 `steps[].tool` 永久为空，属于功能回退。

---

## 8. 复现材料位置

| 内容 | 路径 |
|---|---|
| 本轮的三个探针脚本 | `_tmp_rootcause_probe/*.py` |
| 探针原始输出日志 | `_tmp_rootcause_probe/logs/*.log` |
| 上游原始响应 JSON（逐条件落盘） | `_tmp_rootcause_probe/evidence/*.json` |
| **归档副本**（临时目录可清理） | `桌面\设计思路\云枢能力层重构审计与子任务\_baseline\dsml-evidence\rootcause_round2\` |
| 上一轮的三条件对照 | `...\_baseline\dsml-evidence\cond4_P*.json` |

复现命令（Windows PowerShell，仓库根）：

```powershell
python _tmp_rootcause_probe\probe_prod_prompt_conditions.py   # 提示词成分拆解 → DSML 触发源
python _tmp_rootcause_probe\probe_fix_before_after.py         # 修复前后同条件对照
```
