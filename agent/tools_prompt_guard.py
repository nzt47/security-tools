# -*- coding: utf-8 -*-
"""工具暴露 ↔ 提示词宣传 的一致性守卫（DSML 泄漏**根因**防线）。

────────────────────────────────────────────────────────────────────────────
为什么需要它（实测根因，不是推测）
────────────────────────────────────────────────────────────────────────────
两条实测证据链（`_baseline/dsml-evidence/`，真实直连 api.deepseek.com 抓取）：

1) 三条件对照（`cond4_P1/P2/P3_*.json`）+ 生产提示词成分拆解（本轮新增探针）：

   | 条件 | 提示词 | tools= | 上游响应 |
   |---|---|---|---|
   | A 生产提示词原样 | 含 `【工具】…` 工具清单宣告（当时渲染的是**注册表全量**计数，
     即「宣称的工具集」而非「本轮下发的工具集」） | 空 | **DSML 复现** |
   | B 去掉「首条回复必须是tool_calls」 | 含 `【工具】…` | 空 | **DSML 复现** |
   | C 清空 `{tool_status}` 段 | 仅剩「执行铁律」 | 空 | **干净**（纯文本推辞） |
   | D `{tool_status}` 列出真实工具名 | 含 `【工具】…(N): 名字…`（当时的渲染文案；
     B1 后工具侧改为 `【工具】本轮向模型下发(N 个): 名字…`，**标记子串不变**） | 空 | **DSML 复现** |
   | E 生产提示词原样 | 同上 | 26 个 | **干净**（走结构化 `tool_calls`） |

   ⇒ **充要触发源是 `{tool_status}` 渲染出的 `【工具】…` 那一行**
     （`agent/digital_life_persona.py::_build_tool_status_text`）。
     提示词宣告"你有工具"，而请求没带 `tools` ⇒ 模型只能退回**文本协议**
     （DSML 标记）表达调用意图，标记被当作正文返回 ⇒ 用户可见泄漏。

   ⇒ 注意 B/C 的组合含义：单独保留「执行铁律：首条回复必须是tool_calls」
     **不会**触发 DSML（C 干净）；真正致命的是工具清单/数量的宣告。

2) 生产链路里 **4 条路径会各自把 `tools` 丢掉**，而提示词是**同一次请求里
   预先渲染好**的，不会跟着变 —— 这就是"提示词宣传了工具、`tools=` 却为空"
   这条不变量在生产上的全部破口：

   | 位置 | 代码 | 语义 |
   |---|---|---|
   | `agent/orchestrator/orchestrator.py:3446` | `if not allow_tools: _tool_defs = []` | 工作流层已执行过工具，本轮不暴露 |
   | `agent/orchestrator/orchestrator.py:3520` | `_kwargs.pop("tools", None)` | 工具循环**最后一轮**强制收尾 |
   | `agent/tool_calling.py:317` | `need_tools = tool_defs if round_idx < self._max_rounds else None` | 同上（`chat_with_steps` 路径） |
   | `plugins/chat.py:1194` | `tools=tool_defs if round_idx == 0 else None` | 工作台流式循环**只首轮**传 tools |
   | `plugins/chat.py:1140` | 工具定义加载失败 ⇒ `tool_defs` 保持 `None` | 加载降级路径 |

   ⇒ 任何单点修复都会漏掉其余路径，所以本模块把不变量收成**一个纯函数**，
     每个调用点只需一行。

────────────────────────────────────────────────────────────────────────────
本模块的不变量
────────────────────────────────────────────────────────────────────────────
    「提示词宣传了工具」 ⇒ 「`tools` 必须非空」
    反之（`tools` 为空）  ⇒ 「提示词不得宣传工具」——就地中和 + `WARN event=tools_prompt_mismatch`

【不易】本模块**不擅自补发工具**：能力边界由主线装配/工具闸门决定，
        为了"让提示词一致"而多发工具是越权。只做"中和提示词"这一个方向。
【不易】本模块**不静默放过**：不一致必须留下 `event=tools_prompt_mismatch` 结构化日志，
        否则就重演了本次缺陷"线上看不见"的老路。
【变易】新增"宣传标记"只需往 `TOOL_ADVERT_MARKERS` 追加一条，并补一条单测。
【不易】而标记的**渲染侧唯一产出点**是同在本模块的 `render_tool_advert_line()`：
        判定侧（标记）与产出侧（渲染）住在一起，"改了渲染忘了改标记"这类漂移
        在结构上不可能发生。单测 `tests/unit/test_tool_count_consistency.py` 钉住它。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: 结构化日志事件名（可观测性契约，勿改字符串）
EVENT_TOOLS_PROMPT_MISMATCH = "tools_prompt_mismatch"

#: **已实测证实**会触发 DSML 的"工具宣传"标记。
#:
#: 【不易】这里只有一条，是实测逼出来的，不是设计偏好：
#:   - `【工具】` 来自 `_build_tool_status_text()`，是生产提示词里唯一
#:     宣告"你有工具/有多少工具"的文本；
#:   - 对照实验 C 证明：把 `【工具】`/`【技能】` 行清掉、**只留**
#:     「执行铁律：…首条回复必须是tool_calls…」时，上游**不会**吐 DSML；
#:   - 对照实验 B 证明：保留 `【工具】`、只去掉执行铁律时，上游**仍会**吐 DSML。
#:   ⇒ 多写标记会把"无害的提示词"误判成不一致，反而制造噪声告警。
TOOL_ADVERT_MARKERS = ("【工具】",)

#: **与 `【工具】` 同类构造**但未单独定标的宣传标记：同样是一份**工具名清单**。
#:
#: 来源：`agent/context/assembler.py:195` / `:365`
#:     `parts.append("【可用工具】" + ", ".join(tools))`
#:     —— `tools` 是**硬编码**的 `["search", "read_file", "write_file"]`（或 `["*"]`）。
#:
#: 【不易】为什么归到"宣传"而不是忽略：
#:   对照实验 D 证明「提示词列出工具名 + tools 为空 ⇒ 必吐 DSML」，
#:   而这里正是"列出工具名"这一形态；且清单里的 `search` **根本不在注册表里**
#:   （注册表里是 `search_files` / `web_search`）⇒ 模型会照着猜，
#:   落到 `dsml_adapter` 的"未知工具 validation_error"分支（TASK-01a 警告 1 的实例）。
#: 【变易】该路径由 `learning.context_assembler.enabled` 控制、**默认关**，
#:   所以未在真实链路上单独定标；一旦开启就必须被守卫覆盖，故先纳入。
TOOL_ADVERT_ALIKE_MARKERS = ("【可用工具】",)

#: **未证实触发 DSML、但同属"工具能力声明"**的弱标记。
#:
#: 【不易】为什么仍然要处理：提示词断言"你可以用工具"而请求不带 `tools` 时，
#:     这句话在**事实上是假的**。实测（对照 P3「需要时使用提供的工具」）证明
#:     单独一句泛泛的"有工具"**不会**触发 DSML，所以它不是根因；
#:     但既然守卫要在"不一致"时就地中和，就必须连它一起中和，否则中和完的
#:     提示词仍然在向模型承诺工具，属于"修了一半"。
#: 【不易】日志会区分 `proven=True/False`，让运维一眼看出命中的是根因标记还是弱标记。
TOOL_HINT_MARKERS = ("提供的工具",)

#: 判定/中和实际使用的合并集合（顺序即优先级，仅影响"命中了哪一类"的可读性）。
_ALL_ADVERT_MARKERS = (TOOL_ADVERT_MARKERS
                       + TOOL_ADVERT_ALIKE_MARKERS
                       + TOOL_HINT_MARKERS)

#: 「**命令**模型必须发工具调用」的祈使句标记。
#:
#: 【不易】它**只参与中和、不参与判定**（不在 `TOOL_ADVERT_MARKERS` 里）。
#:   理由来自实测：对照实验 C（清空 `{tool_status}`、只留这句话）上游**不会**吐
#:   DSML ⇒ 它不是触发源，把它当"宣传"判定会制造噪声告警。
#:   但一旦本轮确实不发 `tools`，这句话就是**自相矛盾的硬性命令**
#:   （要求模型走一条不存在的通路），所以只在"已经确认要中和"的那一趟里一并替换掉。
TOOL_IMPERATIVE_MARKERS = ("首条回复必须是",)

#: 中和后替换成的文本。
#: 【不易】刻意**不含** `【工具】` 子串 ⇒ 中和是幂等的（再跑一次不会重复中和）。
NEUTRAL_TOOL_LINE = (
    "【本轮无可用工具】本轮未向你下发任何工具定义，你无法调用工具；"
    "请直接用自然语言回答用户，不要输出任何形式的工具调用标记。"
)

#: 「必须发工具调用」祈使句被中和后的文本。
#: 【不易】同样是幂等的：不含 `首条回复必须是` 子串。
NEUTRAL_IMPERATIVE_LINE = (
    "执行铁律：本轮没有工具可用，遇任何请求都直接用自然语言回答，"
    "严禁输出工具调用标记或类似格式的文本。"
)

#: "催促调用工具"的 system 消息标记（`orchestrator._call_llm` 会往 messages
#: 里插一条督促消息）。tools 不发时它同样是**虚假宣传**。
#: 【不易】按"整条 system 消息"粒度删除，不做子串替换：这类消息的每一句
#: 都在催促调用工具，改半句反而语义割裂。
#: 【不易】只在"确实中和了宣传"的那一趟里删（见 `align_system_prompt_with_tools`）：
#: 该消息位于 messages 固定区首位，其位置被
#: `tests/unit/test_prompt_cache_order.py::test_v1_messages_order` 明文锁定，
#: 且实测（对照 C）它单独不触发 DSML ⇒ 提示词干净时不动它，避免打红已登记的契约。
TOOL_URGE_MARKERS = (
    "直接发起函数调用",
    "没调用工具 = 没执行",
    "首条回复必须是",
)

#: 「工具调用示例」消息标记 —— `agent/orchestrator/prompt_builder.py:52`
#: 注入的 Dynamic Few-shot 消息：
#:     「以下是当前可用工具过往成功调用的脱敏示例,仅供参数提取参考,」
#: 后面跟的是**真实工具名 + 调用样本**，因此在"本轮不发 tools"时构成同类宣传。
#:
#: 【不易】与 `TOOL_URGE_MARKERS` 分开、且**无条件剔除**（不要求提示词也命中宣传标记）：
#:   ① 它是**机器注入**的示例消息（非用户内容），删掉不会改用户语义；
#:   ② 它不在 messages 固定区首位，不受 `test_prompt_cache_order` 的顺序契约约束；
#:   ③ 真实可达路径：`allow_tools=False` 时 `_whitelist` 仍非空，
#:      `ToolFewshotStore.sample_for_tools(whitelist)` 照样能取到样本并注入，
#:      而此时 `_tool_defs` 已被清空 ⇒ 正好落进"有工具名、没有 tools"的触发条件。
TOOL_EXAMPLE_MARKERS = ("过往成功调用的脱敏示例",)

#: 中和后的收尾声明。显式告诉模型"这轮没有工具"，并要求它不要输出标记协议。
#: 这是**正向**指令，比"删掉宣传"更能压住模型自创文本协议的冲动。
NEUTRAL_FOOTER = (
    "\n\n## 本轮工具可用性（系统声明）\n"
    "本轮请求**没有附带任何工具定义**，你当前没有可调用的工具。\n"
    "请直接用自然语言回答用户；不要输出任何工具调用标记或类似格式的文本。"
)


def prompt_advertises_tools(system_prompt: str) -> bool:
    """提示词是否向模型宣传了"你有工具"。

    这是不变量的判定侧。纯函数，无副作用 ⇒ 可被单测直接钉住。
    强标记（已证实根因）与弱标记（同属能力声明）**任一命中**都算宣传。
    """
    if not system_prompt:
        return False
    return any(m in system_prompt for m in _ALL_ADVERT_MARKERS)


def hit_proven_marker(system_prompt: str) -> bool:
    """是否命中了**已实测证实**的根因标记（用于日志分级，不改变行为）。"""
    if not system_prompt:
        return False
    return any(m in system_prompt for m in TOOL_ADVERT_MARKERS)


def neutralize_tool_advertisement(system_prompt: str) -> tuple[str, int]:
    """把提示词里的工具宣传行替换为"本轮无工具"的显式声明。

    Returns:
        (中和后的提示词, 被中和的行数)。行数为 0 表示本来就没宣传（幂等）。

    【不易】按行处理而不是整段正则替换：提示词是按行拼装的（`_get_template().format`
        产生多行文本），行粒度既能精确定位、又不会误伤相邻行；
        复杂度 O(len(prompt))，满足"不得引入 O(响应长度²)"的性能约束。
    【不易】两趟：先判定"是否确实要中和"，再替换。第二趟顺带把
        「必须发工具调用」的祈使句一起换掉（`TOOL_IMPERATIVE_MARKERS`）——
        它不参与判定（实测不是触发源），只在已确认中和时清理，
        否则中和完的提示词仍在下达一条走不通的硬性命令。
    """
    if not system_prompt:
        return system_prompt, 0
    lines = system_prompt.split("\n")
    if not any(m in ln for ln in lines for m in _ALL_ADVERT_MARKERS):
        return system_prompt, 0
    out, n = [], 0
    for ln in lines:
        if any(m in ln for m in _ALL_ADVERT_MARKERS):
            out.append(NEUTRAL_TOOL_LINE)
            n += 1
        elif any(m in ln for m in TOOL_IMPERATIVE_MARKERS):
            out.append(NEUTRAL_IMPERATIVE_LINE)
            n += 1
        else:
            out.append(ln)
    return "\n".join(out), n


def strip_tool_urge_messages(messages: list | None,
                             markers: tuple = TOOL_URGE_MARKERS) -> tuple[list, int]:
    """删掉催促调用工具 / 工具调用示例的 system 消息（仅在 `tools` 为空时调用）。

    Args:
        markers: 命中即删的标记集。默认催调用工具那批；传
            `TOOL_URGE_MARKERS + TOOL_EXAMPLE_MARKERS` 可连同 few-shot 示例一起删。

    Returns:
        (处理后的 messages 列表, 删除条数)。**不原地修改入参**：
        调用方的 `messages` 常被复用（如 orchestrator 的 `_working` 跨轮累积），
        原地删会造成"第二轮突然少一条历史"的隐蔽 bug。
    """
    if not messages:
        return list(messages or []), 0
    out, n = [], 0
    for msg in messages:
        try:
            is_system = (msg or {}).get("role") == "system"
            content = (msg or {}).get("content") or ""
        except AttributeError:
            out.append(msg)
            continue
        if is_system and any(m in content for m in markers):
            n += 1
            continue
        out.append(msg)
    return out, n


def align_system_prompt_with_tools(system_prompt: str,
                                   tools_exposed: bool,
                                   *,
                                   site: str = "",
                                   messages: list | None = None,
                                   tools_count: int = 0,
                                   ) -> tuple[str, list | None]:
    """**唯一入口**：让"提示词宣传"与"`tools` 是否下发"保持一致。

    Args:
        system_prompt: 即将发给上游的 system 提示词。
        tools_exposed: 本次请求是否真的会带非空 `tools`。
        site: 调用点标识（写进结构化日志，便于定位是哪条路径不一致）。
        messages: 可选。传入时，`tools` 为空会顺带剔除 messages 里的工具宣传：
            命中 `TOOL_EXAMPLE_MARKERS` 的 **few-shot 工具调用示例**总是剔除；
            只有在"提示词确实宣传了工具"的那一趟里，才连同 `TOOL_URGE_MARKERS`
            的催促消息一起剔除（该消息受前缀缓存顺序契约约束，不能无条件动）。
        tools_count: 仅用于日志。

    Returns:
        (可安全发出的 system_prompt, 对齐后的 messages)。
        未传 messages 时第二项为 None。

    【不易】tools_exposed=True 时**只判定不改写**：即使提示词没宣传工具也不补，
        因为"补宣传"会把本次修复变成"扩大提示词"，属于另一个变更面。
        这种"有工具但没宣传"的方向只记 DEBUG，不算缺陷。
    """
    sys_p = system_prompt or ""
    advertised = prompt_advertises_tools(sys_p)

    if tools_exposed:
        if not advertised:
            logger.debug(
                "event=%s site=%s direction=tools_without_advert tools=%d "
                "note=有工具但提示词未宣传（非缺陷，不处理）",
                EVENT_TOOLS_PROMPT_MISMATCH, site, tools_count)
        return sys_p, messages

    # ── tools 为空：提示词不得宣传工具 ──
    if not advertised:
        # 提示词本来就干净 ⇒ 系统提示词无缺陷，**原样返回**。
        #
        # 【不易】这里刻意**不**剔除催促调用工具的 system 消息，原因有二：
        #   ① 实测（对照实验 C：清空 `{tool_status}`、只留催促语句）上游**不会**
        #      吐 DSML ⇒ 干净的提示词 + 催促消息不构成根因，没有必要改请求结构；
        #   ② 那条消息位于 `messages` 的**固定区首位**，是
        #      `tests/unit/test_prompt_cache_order.py::test_v1_messages_order`
        #      明文锁定的前缀缓存顺序契约（fixed=[tool_urge@idx0]）。
        #      为了一个非根因的问题去动它，会破坏已登记的契约、并让 DeepSeek 前缀
        #      缓存整段失效 —— 代价远大于收益。
        #   但 **few-shot 工具调用示例**必须删：它是机器注入的示例消息、不受该契约
        #   约束，而它里面带着**真实工具名**（见 `TOOL_EXAMPLE_MARKERS`），
        #   在 `allow_tools=False` 这条真实可达路径上照样构成"有工具名、没有 tools"。
        new_msgs, ex_n = strip_tool_urge_messages(messages, TOOL_EXAMPLE_MARKERS)
        if ex_n:
            logger.warning(
                "event=%s site=%s direction=prompt_clean_but_tool_examples "
                "tools=0 stripped_examples=%d "
                "note=提示词干净但 messages 里带了工具调用示例，已剔除",
                EVENT_TOOLS_PROMPT_MISMATCH, site, ex_n)
        return sys_p, (new_msgs if messages is not None else None)

    new_sys, line_n = neutralize_tool_advertisement(sys_p)
    new_msgs, urge_n = strip_tool_urge_messages(
        messages, TOOL_URGE_MARKERS + TOOL_EXAMPLE_MARKERS)
    if NEUTRAL_FOOTER not in new_sys:
        new_sys = new_sys + NEUTRAL_FOOTER
    logger.warning(
        "event=%s site=%s direction=prompt_advertised_but_no_tools "
        "tools=0 proven_marker=%s neutralized_lines=%d stripped_urge=%d prompt_len=%d "
        "note=提示词宣传了工具但请求未带 tools，已就地中和（DSML 根因防线）",
        EVENT_TOOLS_PROMPT_MISMATCH, site, hit_proven_marker(sys_p), line_n, urge_n, len(sys_p))
    return new_sys, (new_msgs if messages is not None else None)


def assert_invariant(system_prompt: str, tools_exposed: bool) -> bool:
    """不变量自检：返回 True 表示"提示词宣传了工具"与"`tools` 非空"一致。

    供回归测试与线上断言使用（**不抛异常**：调用方在热路径上，
    抛异常会击穿对话主链路，与 D4 降级原则冲突）。
    """
    return prompt_advertises_tools(system_prompt) <= bool(tools_exposed)


# ────────────────────────────────────────────────────────────────────────────
# 【B1】「宣布多少个工具」的唯一事实源
# ────────────────────────────────────────────────────────────────────────────
# 为什么这一段住在本模块：本模块的不变量是「提示词宣传工具 ⇒ tools 非空」与
# 「tools 为空 ⇒ 提示词不得宣传工具」。判定"宣传了什么"的依据是
# `TOOL_ADVERT_MARKERS`，而**渲染"宣传了什么"的代码就在这里** —— 两边同模块，
# 口径才可能唯一。
#
# 审计实测（`docs/audit_skill_governance/AUDIT_AND_PLAN.md` E6；本卡 B1）：同一件事
# 曾有三个互相矛盾的数字 —— 提示词宣告的"注册表全量"计数、`data/tool_definitions/*.yaml`
# 的文件数、以及**真实下发**的主线白名单工具数。只有最后一个是模型真正看得到的能力集，
# 所以这里只认它，且只从**入参的 tool_defs** 里数，不做任何二次统计。
#
# 【不易】数字只能来自 `len(tool_defs)`：`tool_defs` 是调用方**真的要下发的**
#   那一份（编排器是 `get_tool_defs(whitelist=...)` 的返回值），因此"宣告值"与
#   "下发值"天然同源，不存在"另算一遍"的空间。
# 【不易】`resolve_dispatch_tool_defs()` 也放在这里、而不是各调用方各写一份：
#   选择链一旦分叉成多份，"口径再次分裂"就会重演 —— 本卡要修的就是这件事。

#: token 实测使用的编码：与 `agent/llm_monitor.LLMMonitor.estimate_tokens` 同口径
#: （审计报告的 18,311 也是这个口径），避免"字符÷3"这类换算再次制造低估。
TOOL_DEFS_TOKEN_ENCODING = "cl100k_base"

#: 无工具可下发时的宣告行。
#: 【不易】它**仍然带 `【工具】` 标记**："本轮确实没有工具"同样是一条能力声明，
#:   必须能被守卫看见并就地中和（`align_system_prompt_with_tools` 的
#:   `tools_exposed=False` 分支会把它整行换成 `NEUTRAL_TOOL_LINE`）；
#:   若渲染成不带标记的普通文本，守卫会判定"提示词本来干净"而放行。
TOOL_ADVERT_EMPTY_LINE = "【工具】本轮没有可下发的工具（0 个）"


def tool_names_of(tool_defs) -> list[str]:
    """取 OpenAI 形态 tool_defs 里的工具名（保序）。

    容错：入参为 None / 元素不是 dict / 没有 function.name 时跳过该项，
    **绝不抛异常** —— 本函数在系统提示词渲染路径上，抛异常会击穿对话主链路。
    """
    names: list = []
    for d in tool_defs or []:
        try:
            fn = d.get("function") or {}
            name = fn.get("name") or d.get("name")
        except (AttributeError, TypeError):
            continue
        if name:
            names.append(str(name))
    return names


def render_tool_advert_line(tool_defs) -> str:
    """把**本轮实际下发的 tool_defs** 渲染成提示词里的工具宣告行。

    Args:
        tool_defs: 即将作为请求 `tools=` 下发的那一份定义列表。

    Returns:
        单行文本，**必含** `TOOL_ADVERT_MARKERS` 里的 `【工具】`：
        非空 ⇒ `【工具】本轮向模型下发(N 个): 名字…`；空 ⇒ `TOOL_ADVERT_EMPTY_LINE`。

    【不易】N 与名字来自**同一个入参**，所以"数字对了名字不对"这类半对不变量
        不会出现；反过来，任何"另外数一遍工具"的写法（注册表条数、yaml 文件数、
        路由日志里的计数）都不等于下发值，本卡的验收就是要消掉它们。
    【不易】名字全量列出、不截断：模型要靠名字决定调哪个工具，而旧实现在
        "无白名单"分支只给一个数字（`全部已启用（共 N 个）`）—— 那个 N 数与
        下发集是两处统计，正是三个口径互相矛盾的来源。
    """
    names = tool_names_of(tool_defs)
    if not names:
        return TOOL_ADVERT_EMPTY_LINE
    return "【工具】本轮向模型下发(%d 个): %s" % (len(names), ", ".join(names))


def resolve_dispatch_tool_defs(enabled_whitelist: list | None = None) -> list[dict]:
    """解析"本轮真正会下发给模型"的工具定义集（与出网口同源）。

    步骤与 `orchestrator._call_llm` 的下发链**同源**：
        ① `enabled_whitelist`（调用方给的启用白名单；None = 不限制）
        ② 主线装配（`agent.lines.line_whitelist`；未装线/装配为空 ⇒ 保持 ①）
        ③ `agent.tools.get_tool_defs(whitelist=…)` —— 与真正下发时**同一个函数**

    【不易】③ 必须是 `get_tool_defs()` 本身：它会剔除 `internal` 与声明不可被
        LLM 调用的工具，所以"注册表里有 N 个"不等于"模型能看到 N 个"。
    【不易·已知边界】编排器在"无激活主线且开启智能选择"时还会按 user_input 再收窄一次
        （`hybrid_select_tools`）。那一步依赖输入、无法在本函数里复现；此时调用方应把
        已收窄的结果通过 `_build_tool_status_text(tool_defs=…)` 直接传进来（同源优先）。
        生产默认路径（有激活主线）不含该步，故不构成偏差。
    【变易】主线装配不可用 / 抛异常 ⇒ 退回 ① 的结果，绝不断对话。
    【简易】只读，无副作用。
    """
    from agent import tools as _tools

    whitelist = enabled_whitelist
    try:
        from agent.lines import line_whitelist as _line_wl
        _line_tools, _line_res = _line_wl(whitelist)
        if _line_tools:
            whitelist = _line_tools
    except Exception as e:  # noqa: BLE001 主线装配故障绝不断对话
        logger.debug("event=tool_count_resolve site=line_whitelist note=主线装配不可用，退回白名单: %s", e)
    return _tools.get_tool_defs(whitelist=whitelist)


def count_tool_defs_tokens(tool_defs) -> int | None:
    """实测 tool_defs 的 token 数（tiktoken cl100k_base，与审计口径一致）。

    Returns:
        token 数；**tiktoken 不可用或编码失败时返回 None**（调用方据此不打印数字，
        而不是退化成"字符÷3" —— 那种静默换口径正是审计点名的失真来源）。
    """
    try:
        import tiktoken
        enc = tiktoken.get_encoding(TOOL_DEFS_TOKEN_ENCODING)
        return len(enc.encode(json.dumps(list(tool_defs or []), ensure_ascii=False)))
    except Exception as e:  # noqa: BLE001 计量失败不得影响工具集本身
        logger.debug("event=tool_count_resolve site=count_tokens note=token 实测不可用: %s", e)
        return None


__all__ = [
    "EVENT_TOOLS_PROMPT_MISMATCH",
    "TOOL_ADVERT_MARKERS",
    "TOOL_ADVERT_ALIKE_MARKERS",
    "TOOL_HINT_MARKERS",
    "TOOL_IMPERATIVE_MARKERS",
    "TOOL_URGE_MARKERS",
    "TOOL_EXAMPLE_MARKERS",
    "TOOL_ADVERT_EMPTY_LINE",
    "TOOL_DEFS_TOKEN_ENCODING",
    "NEUTRAL_TOOL_LINE",
    "NEUTRAL_IMPERATIVE_LINE",
    "NEUTRAL_FOOTER",
    "prompt_advertises_tools",
    "hit_proven_marker",
    "neutralize_tool_advertisement",
    "strip_tool_urge_messages",
    "align_system_prompt_with_tools",
    "assert_invariant",
    "tool_names_of",
    "render_tool_advert_line",
    "resolve_dispatch_tool_defs",
    "count_tool_defs_tokens",
]
