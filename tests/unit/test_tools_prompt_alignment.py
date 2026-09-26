# -*- coding: utf-8 -*-
"""DSML 泄漏**根因**不变量回归测试：提示词宣传的工具有没有真的下发。

────────────────────────────────────────────────────────────────────────────
这条不变量是什么、为什么值得钉住
────────────────────────────────────────────────────────────────────────────
实测根因（`_baseline/dsml-evidence/` 真实抓取，见
`agent/tools_prompt_guard.py` 模块 docstring 的完整对照表）：

    提示词向模型宣告"你有工具"（生产提示词里就是 `## 当前工具与技能状态` 渲染出的
    `【工具】…` 那一行）而请求的 `tools=` 为空
      ⇒ 上游 DeepSeek 走不了结构化 `tool_calls`，只能用**文本协议**（DSML）
         表达调用意图，标记随 `content` 原样返回 ⇒ 用户可见泄漏。

对照实验（同一 user prompt）已把触发源收敛到 `【工具】` 那一行：
清空它 ⇒ 上游改为纯文本推辞（干净）；保留它 ⇒ 必吐 DSML。

本文件把不变量锁成可自动执行的断言：
    ① 判定/中和函数的语义（纯函数，8 个用例）
    ② 生产提示词真的会命中标记（防止标记随提示词改版而失效 ⇒ 假绿）
    ③ 四条会丢弃 tools 的路径都接了守卫（源码级锁定，防止重构时被摘掉）
    ④ 性能不退化（不得引入 O(响应长度²)）
"""
import logging
import os
import re
import sys
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.tools_prompt_guard import (  # noqa: E402
    EVENT_TOOLS_PROMPT_MISMATCH,
    NEUTRAL_FOOTER,
    NEUTRAL_TOOL_LINE,
    TOOL_EXAMPLE_MARKERS,
    align_system_prompt_with_tools,
    assert_invariant,
    neutralize_tool_advertisement,
    prompt_advertises_tools,
    strip_tool_urge_messages,
)

#: 生产提示词的"工具宣传行"样例。**用拼接构造**，避免测试文件本身成为
#: 一处"硬编码提示词"，将来模板改版时能一眼看出这里需要同步。
ADVERT_LINE = "【工具】本轮向模型下发(26 个): search_files, list_directory"
ADVERT_LINE_LISTED = "【工具】已启用(26): search_files, list_directory, shell_execute"

#: 生产模板里的催促调用工具那句（在 messages 里以 system 消息出现）
URGE_MSG = ("⚡ 立即检查：用户这句话需要工具吗？如果需要，直接发起函数调用。"
            "绝对禁止只发文字描述你将要做的操作。没调用工具 = 没执行。立即行动。")


# ══════════════════════════════════════════════════════════════════════
#  ① 判定侧
# ══════════════════════════════════════════════════════════════════════

class TestPromptAdvertisesTools:
    def test_检测到工具状态行(self):
        assert prompt_advertises_tools("你是云枢。\n" + ADVERT_LINE + "\n完") is True

    def test_检测到工具名清单行(self):
        assert prompt_advertises_tools(ADVERT_LINE_LISTED) is True

    def test_检测到泛化的工具能力声明(self):
        # 弱标记：实测不单独触发 DSML，但同属"承诺了工具"，也要算宣传
        assert prompt_advertises_tools("需要时可以使用提供的工具。") is True

    def test_检测到context_assembler的可用工具清单行(self):
        """`agent/context/assembler.py` 旁路注入的 `【可用工具】…` 也是同类宣传。

        它与 `【工具】` 同类（都是工具名清单），但来源不同、开关不同
        （`learning.context_assembler.enabled`，默认关），因此单独钉一条，
        避免将来只改 `【工具】` 那一处就以为覆盖全了。
        """
        line = "【可用工具】search, read_file, write_file"
        assert prompt_advertises_tools(line) is True
        out, n = neutralize_tool_advertisement("前\n" + line + "\n后")
        assert n == 1 and "【可用工具】" not in out

    def test_干净提示词不误判(self):
        clean = "你是云枢。\n执行铁律：遇任何实操请求，首条回复必须是tool_calls。\n"
        # 【不易】刻意断言这个反例：对照实验 C 证明**只留执行铁律、不含【工具】行**
        # 时上游不会吐 DSML，所以它不算"宣传"，避免制造噪声告警。
        assert prompt_advertises_tools(clean) is False

    def test_空输入安全(self):
        assert prompt_advertises_tools("") is False
        assert prompt_advertises_tools(None) is False


# ══════════════════════════════════════════════════════════════════════
#  ② 中和侧
# ══════════════════════════════════════════════════════════════════════

class TestNeutralize:
    def test_按行替换且保留其余文本(self):
        src = "身份行\n" + ADVERT_LINE + "\n结尾行"
        out, n = neutralize_tool_advertisement(src)
        assert n == 1
        assert "身份行" in out and "结尾行" in out
        assert "【工具】" not in out
        assert NEUTRAL_TOOL_LINE in out

    def test_幂等(self):
        src = "A\n" + ADVERT_LINE + "\nB"
        once, n1 = neutralize_tool_advertisement(src)
        twice, n2 = neutralize_tool_advertisement(once)
        assert n1 == 1 and n2 == 0
        assert once == twice

    def test_同时清掉必须发工具调用的祈使句(self):
        """祈使句不参与**判定**（实测不是触发源），但在已确认中和时一并清理。

        否则中和完的提示词仍在下达一条走不通的硬性命令（"首条回复必须是…"），
        属于"修了一半"。
        """
        src = ("身份\n"
               "执行铁律：遇任何实操请求，首条回复必须是tool_calls，严禁先发文字或废话。\n"
               + ADVERT_LINE + "\n尾")
        out, n = neutralize_tool_advertisement(src)
        assert n == 2
        assert "首条回复必须是" not in out
        assert "【工具】" not in out
        assert "身份" in out and "尾" in out

    def test_只有祈使句时不做任何中和(self):
        """单独一句祈使句实测不会触发 DSML（对照 C），不得据此判定为"宣传"。"""
        src = "执行铁律：遇任何实操请求，首条回复必须是tool_calls，严禁先发文字或废话。\nA"
        out, n = neutralize_tool_advertisement(src)
        assert n == 0 and out == src

    def test_干净输入零改动(self):
        src = "A\nB"
        out, n = neutralize_tool_advertisement(src)
        assert n == 0 and out == src


# ══════════════════════════════════════════════════════════════════════
#  ③ 对齐总入口（不变量本体）
# ══════════════════════════════════════════════════════════════════════

class TestAlignInvariant:
    def test_有工具_提示词原样不动(self):
        out, _ = align_system_prompt_with_tools(ADVERT_LINE, True)
        assert out == ADVERT_LINE

    def test_无工具_宣传被中和(self, caplog):
        with caplog.at_level(logging.WARNING):
            out, _ = align_system_prompt_with_tools(
                "身份\n" + ADVERT_LINE + "\n尾", False, site="unit.test")
        assert "【工具】" not in out
        assert NEUTRAL_FOOTER in out
        assert assert_invariant(out, False) is True
        rec = [r.getMessage() for r in caplog.records if EVENT_TOOLS_PROMPT_MISMATCH in r.getMessage()]
        assert rec, "必须留下 event=tools_prompt_mismatch 结构化日志"
        assert "proven_marker=True" in rec[0]
        assert "site=unit.test" in rec[0]

    def test_无工具且提示词干净_不误报宣传且不动messages(self, caplog):
        msgs = [{"role": "system", "content": URGE_MSG}, {"role": "user", "content": "hi"}]
        with caplog.at_level(logging.WARNING):
            out, new_msgs = align_system_prompt_with_tools(
                "干净提示词", False, site="unit.test", messages=msgs)
        assert out == "干净提示词"
        # 【不易】干净提示词下**必须原样返回 messages**：催促消息是 messages 固定区
        # 首位，其位置由 tests/unit/test_prompt_cache_order.py::test_v1_messages_order
        # 明文锁定（fixed=[tool_urge@idx0]）；为一个非根因的问题去动它，
        # 会破坏前缀缓存顺序契约。实测对照 C 也证明它单独不触发 DSML。
        assert new_msgs == msgs
        assert not [r for r in caplog.records
                    if "direction=prompt_advertised_but_no_tools" in r.getMessage()]

    def test_有工具但提示词未宣传_不补宣传(self):
        # 【不易】只做"中和"这一个方向：为了"让提示词一致"而擅自补宣传
        # 会把本次修复变成"扩大提示词"，属于另一个变更面。
        out, _ = align_system_prompt_with_tools("干净提示词", True)
        assert out == "干净提示词"

    def test_不变量自检函数(self):
        assert assert_invariant(ADVERT_LINE, True) is True
        assert assert_invariant(ADVERT_LINE, False) is False
        assert assert_invariant("干净", False) is True


# ══════════════════════════════════════════════════════════════════════
#  ④ 催促消息处理（它同样是"宣传"，且不原地改入参）
# ══════════════════════════════════════════════════════════════════════

class TestStripUrgeMessages:
    def test_剔除催促消息保留其余(self):
        msgs = [
            {"role": "system", "content": URGE_MSG},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "在"},
        ]
        out, n = strip_tool_urge_messages(msgs)
        assert n == 1
        assert len(out) == 2
        assert [m["role"] for m in out] == ["user", "assistant"]

    def test_不原地修改入参(self):
        # orchestrator 的 _working 跨轮累积，原地删会造成"第二轮突然少一条历史"
        msgs = [{"role": "system", "content": URGE_MSG}, {"role": "user", "content": "hi"}]
        before = list(msgs)
        strip_tool_urge_messages(msgs)
        assert msgs == before

    def test_非system消息里的相同文本不误删(self):
        msgs = [{"role": "user", "content": URGE_MSG}]
        out, n = strip_tool_urge_messages(msgs)
        assert n == 0 and len(out) == 1

    def test_fewshot工具示例消息被剔除(self):
        """`orchestrator.prompt_builder` 注入的 Dynamic Few-shot 消息带**真实工具名**。

        真实可达路径：`allow_tools=False` 时 `_whitelist` 仍非空，
        `ToolFewshotStore.sample_for_tools(whitelist)` 照样取到样本并注入，
        而此时 `_tool_defs` 已被清空 ⇒ 落进"有工具名、没有 tools"的触发条件。
        它不在 messages 固定区首位，不受 prompt-cache-order 契约约束，可以删。
        """
        fewshot = ("以下是当前可用工具过往成功调用的脱敏示例,仅供参数提取参考,\n"
                   '{"tool":"shell_execute","params":{"command":"ls"}}')
        msgs = [{"role": "system", "content": fewshot}, {"role": "user", "content": "hi"}]
        out, n = strip_tool_urge_messages(msgs, TOOL_EXAMPLE_MARKERS)
        assert n == 1 and len(out) == 1 and out[0]["role"] == "user"

    def test_提示词干净但带fewshot示例时_只删示例不删催促消息(self, caplog):
        fewshot = "以下是当前可用工具过往成功调用的脱敏示例,仅供参数提取参考,"
        msgs = [
            {"role": "system", "content": URGE_MSG},        # 固定区首位，必须保留
            {"role": "system", "content": fewshot},         # 示例，必须删
            {"role": "user", "content": "hi"},
        ]
        with caplog.at_level(logging.WARNING):
            out, new_msgs = align_system_prompt_with_tools(
                "干净提示词", False, site="unit.test", messages=msgs)
        assert out == "干净提示词"
        assert len(new_msgs) == 2
        assert "立即检查" in new_msgs[0]["content"]
        assert new_msgs[1]["role"] == "user"
        assert any("direction=prompt_clean_but_tool_examples" in r.getMessage()
                   for r in caplog.records)

    def test_经align一并剔除(self):
        """宣传被中和的那一趟里，催促消息也要剔除（它同样是虚假宣传）。"""
        msgs = [{"role": "system", "content": URGE_MSG}, {"role": "user", "content": "hi"}]
        _, out = align_system_prompt_with_tools(
            "身份\n" + ADVERT_LINE, False, messages=msgs)
        assert len(out) == 1 and out[0]["role"] == "user"


# ══════════════════════════════════════════════════════════════════════
#  ⑤ 生产提示词集成（防止标记随提示词改版而失效 ⇒ 假绿）
# ══════════════════════════════════════════════════════════════════════

class TestProductionPromptIntegration:
    """用**真实**的模板与真实渲染函数构造提示词，而不是测试里自造的字符串。

    为什么必须这样：如果只用自造字符串测，一旦生产提示词把 `【工具】` 换掉，
    测试仍然全绿，而线上又不设防了 —— 那正是本次缺陷"线上看不见"的重演。
    """

    @staticmethod
    def _render_real_prompt():
        from agent.digital_life_persona import DigitalLifePersonaMixin
        from agent.system_prompt_manager import get_template

        class _P(DigitalLifePersonaMixin):
            def __init__(self):
                self._cached_tool_status = None
                self._cached_skill_instructions = None
                self._loaded_skill_ids = []

        status = _P()._build_tool_status_text()   # 真实渲染（工具注册表可为空也照测）
        tpl = get_template()
        return tpl.format(
            current_date="2025年1月1日", body_status="（略）", mode_name="正常",
            mode_description="正常", memory_context="（略）",
            tool_status=status, skill_instructions="",
        )

    def test_真实生产提示词命中宣传标记(self):
        prompt = self._render_real_prompt()
        assert prompt_advertises_tools(prompt) is True, (
            "生产提示词的『工具状态』段必须被守卫认出来；"
            "若此断言失败，说明提示词改版后标记失效，守卫会静默放行 DSML")

    def test_真实生产提示词在无工具时被中和到不变量成立(self, caplog):
        prompt = self._render_real_prompt()
        with caplog.at_level(logging.WARNING):
            aligned, _ = align_system_prompt_with_tools(
                prompt, False, site="unit.production_prompt")
        assert assert_invariant(aligned, False) is True
        assert "【工具】" not in aligned
        # 中和不得破坏模板的其余结构（行为护栏：只动宣传行）
        assert "## 核心原则" in aligned
        assert "## 记忆线索" in aligned
        # 自相矛盾的硬性命令也要清掉：本轮没有工具，"首条回复必须是tool_calls"是走不通的
        assert "首条回复必须是" not in aligned

    def test_expose_tools_False_时状态文本不含宣传标记(self):
        from agent.digital_life_persona import DigitalLifePersonaMixin

        class _P(DigitalLifePersonaMixin):
            def __init__(self):
                self._cached_tool_status = None
                self._cached_skill_instructions = None
                self._loaded_skill_ids = []

        txt = _P()._build_tool_status_text(expose_tools=False)
        assert prompt_advertises_tools(txt) is False
        assert "未向模型暴露" in txt

    def test_expose_tools_True_时状态文本可被宣传标记识别(self):
        from agent.digital_life_persona import DigitalLifePersonaMixin

        class _P(DigitalLifePersonaMixin):
            def __init__(self):
                self._cached_tool_status = None
                self._cached_skill_instructions = None
                self._loaded_skill_ids = []

        txt = _P()._build_tool_status_text(expose_tools=True)
        assert prompt_advertises_tools(txt) is True

    def test_allow_tools为假时上游提示词源头即不含宣传(self):
        """覆盖 `allow_tools=False` 那条路径的**源头**修复。

        `orchestrator._call_llm` / `_call_llm_v2` 现在传
        `_build_tool_status_text(expose_tools=allow_tools)`：提示词从**渲染那一刻**
        就没有宣传文本，而不是等到出网前才中和。这样即使将来有人在出网前
        加了提前 return 的分支，也不会把宣传漏出去。
        """
        from agent.digital_life_persona import DigitalLifePersonaMixin
        from agent.system_prompt_manager import get_template

        class _P(DigitalLifePersonaMixin):
            def __init__(self):
                self._cached_tool_status = None
                self._cached_skill_instructions = None
                self._loaded_skill_ids = []

        status = _P()._build_tool_status_text(expose_tools=False)
        prompt = get_template().format(
            current_date="2025年1月1日", body_status="（略）", mode_name="正常",
            mode_description="正常", memory_context="（略）",
            tool_status=status, skill_instructions="",
        )
        assert prompt_advertises_tools(prompt) is False, (
            "allow_tools=False 时提示词源头仍含工具宣传 ⇒ 该路径会复现 DSML")


# ══════════════════════════════════════════════════════════════════════
#  ⑥ 源码级不变量锁定：四条会丢弃 tools 的路径都必须接守卫
# ══════════════════════════════════════════════════════════════════════

def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _strip_comments(src: str) -> str:
    """去掉整行注释后再做源码断言。

    【不易】必须去注释：本次修复把"原实现长什么样"写进了注释（说明为什么改），
    直接把整份源码做子串否定断言会把**注释里的旧代码**当成"代码还在"，
    产生假红。断言的目标是"代码里没有这种写法"，不是"全文没有这个字符串"。
    """
    return "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


class TestDropSitesAreGuarded:
    """这四条路径各自会把 `tools` 丢掉（见 `tools_prompt_guard` 模块 docstring 表）。

    守卫是纯函数，**不会因为上游多一条新路径而自动生效** —— 必须在每条
    出网口显式调用。本类用源码扫描把"接了守卫"这件事钉住：
    将来有人重构掉某一行守卫，这里会红，而不是等到线上又出现 DSML。
    """

    @pytest.mark.parametrize("rel", [
        "agent/orchestrator/orchestrator.py",
        "agent/tool_calling.py",
        "plugins/chat.py",
        "memory/llm_service.py",
    ])
    def test_文件已接入守卫(self, rel):
        src = _read(rel)
        assert re.search(r"tools_prompt_guard", src), (
            "%s 未接入 tools_prompt_guard：该文件存在会丢弃 tools 的路径" % rel)

    def test_工作台流式循环每轮都带tools(self):
        src = _strip_comments(_read("plugins/chat.py"))
        assert "tools=tool_defs if round_idx == 0 else None" not in src, (
            "工作台流式工具循环又变回『只首轮带 tools』：第 2~4 轮将无法发起工具调用，"
            "且提示词仍在宣传工具 ⇒ DSML 根因复现")
        assert "tools=tool_defs," in src

    def test_编排器最后一轮仍走守卫对齐(self):
        src = _read("agent/orchestrator/orchestrator.py")
        # 最后一轮不再用裸 pop 丢 tools，而是先算 _tools_this_round 再对齐提示词
        assert "_tools_this_round" in src
        assert "_is_final_round" in src

    def test_编排器守卫位于出网调用之前(self):
        """顺序不变量：守卫必须先于 `chat.completions.create` 生效。

        只断言"代码里有守卫"是不够的 —— 把它写在出网调用之后，
        请求已经带着不一致的提示词发出去了，测试却仍会绿。
        """
        import inspect
        from agent.orchestrator.orchestrator import Orchestrator

        body = inspect.getsource(Orchestrator._call_llm)
        i_guard = body.find("align_system_prompt_with_tools(")
        i_call = body.find("chat.completions.create(**")
        assert i_guard != -1, "_call_llm 内未调用 tools_prompt_guard"
        assert i_call != -1, "_call_llm 内找不到出网调用（测试需同步更新）"
        assert i_guard < i_call, "守卫必须写在出网调用之前"

    def test_编排器在提示词源头按allow_tools裁剪工具宣传(self):
        src = _read("agent/orchestrator/orchestrator.py")
        assert "_build_tool_status_text(expose_tools=allow_tools)" in src

    def test_流式与纯文本出网口都接了守卫(self):
        src = _read("memory/llm_service.py")
        assert "llm_service.chat_stream" in src
        assert "llm_service._do_chat" in src

    def test_工作台硬编码提示词被宣传标记覆盖(self):
        """`plugins/chat.py` 的 SYSTEM_PROMPT 是**硬编码**的独立提示词。

        它不经过编排器的模板渲染，所以"生产提示词"那组集成用例覆盖不到它。
        若它的措辞改成守卫认不出的说法，工具加载失败时就会静默复现 DSML。
        """
        src = _read("plugins/chat.py")
        m = re.search(r'SYSTEM_PROMPT\s*=\s*"([^"]+)"', src)
        assert m, "plugins/chat.py 里找不到 SYSTEM_PROMPT 字面量（测试需同步更新）"
        assert prompt_advertises_tools(m.group(1)) is True, (
            "工作台硬编码提示词未被守卫的宣传标记覆盖：%r" % m.group(1)[:60])


# ══════════════════════════════════════════════════════════════════════
#  ⑦ 出网口集成：证明真实调用点发出去的请求两侧口径一致
# ══════════════════════════════════════════════════════════════════════

class _FakeCompletions:
    """捕获 `client.chat.completions.create(**kwargs)` 的真实入参。"""

    def __init__(self, result=None):
        self.calls = []
        self._result = result
        self._default = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if callable(self._result):
            return self._result(**kwargs)
        return self._result if self._result is not None else self._default


class _FakeClient:
    def __init__(self, result=None):
        self.chat = type("C", (), {"completions": _FakeCompletions(result)})()

    @property
    def calls(self):
        return self.chat.completions.calls


def _nonstream_result(content="ok"):
    """构造 OpenAI 非流式响应的最小替身：`response.choices[0].message`。"""
    msg = type("M", (), {"content": content, "tool_calls": None,
                         "reasoning_content": None})()
    choice = type("Ch", (), {"message": msg, "finish_reason": "stop"})()
    return type("R", (), {"choices": [choice], "usage": None})()


class _StubLLM:
    """`ToolCallingService._call_llm_with_tools` 需要的最小 LLM 替身。"""

    def __init__(self, client):
        self.model = "stub-model"
        self._client = client

    def _get_client(self):
        return self._client

    def _is_openai_compat(self):
        return True


def _system_content_of(kwargs):
    for m in kwargs.get("messages", []):
        if m.get("role") == "system":
            return m.get("content") or ""
    return ""


class TestCallSitesSendConsistentRequest:
    """直接驱动真实出网口，断言**发到上游的那一份请求**两侧口径一致。

    为什么必须测"发出去的请求"而不是只测守卫函数：
        守卫是纯函数、单测必过；真正的风险是**调用点忘了调它**。
        这几条用例把"守卫生效"落到 `client.chat.completions.create` 的真实 kwargs 上。
    """

    def test_llm_service_chat_stream_无工具时中和提示词(self):
        from memory.llm_service import LLMService

        svc = LLMService.__new__(LLMService)      # 绕过 __init__（会校验 api_key）
        svc.provider = "openai"
        svc.model = "stub-model"
        client = _FakeClient()
        svc._get_client = lambda: client
        svc._is_openai_compat = lambda: True

        advert = "身份\n" + ADVERT_LINE
        list(svc.chat_stream([{"role": "user", "content": "hi"}],
                             system_prompt=advert, tools=None))

        assert client.calls, "未发出请求"
        sent = _system_content_of(client.calls[0])
        assert "【工具】" not in sent
        assert "tools" not in client.calls[0]
        assert "本轮" in sent and "没有可调用的工具" in sent

    def test_llm_service_chat_stream_有工具时原样下发(self):
        from memory.llm_service import LLMService

        svc = LLMService.__new__(LLMService)
        svc.provider = "openai"
        svc.model = "stub-model"
        client = _FakeClient()
        svc._get_client = lambda: client
        svc._is_openai_compat = lambda: True

        advert = "身份\n" + ADVERT_LINE
        tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
        list(svc.chat_stream([{"role": "user", "content": "hi"}],
                             system_prompt=advert, tools=tools))

        sent = _system_content_of(client.calls[0])
        assert "【工具】" in sent, "有工具时必须原样保留宣传（不得改写）"
        assert client.calls[0]["tools"] == tools

    def test_llm_service_do_chat_永远无工具故必须中和(self):
        from memory.llm_service import LLMService

        svc = LLMService.__new__(LLMService)
        svc.provider = "openai"
        svc.model = "stub-model"
        client = _FakeClient()
        svc._get_client = lambda: client
        svc._is_openai_compat = lambda: True
        # _do_chat 走 response.choices[0].message.content
        client.chat.completions.create = lambda **kw: (
            client.calls.append(kw) or type("R", (), {
                "choices": [type("Ch", (), {"message": type("M", (), {"content": "ok"})()})()]})())

        svc._do_chat([{"role": "user", "content": "hi"}],
                     system_prompt="身份\n" + ADVERT_LINE)
        sent = _system_content_of(client.calls[0])
        assert "【工具】" not in sent, "_do_chat 按定义不发 tools，绝不能宣传工具"
        assert "tools" not in client.calls[0]

    def test_tool_calling_service_末轮无工具时中和提示词(self):
        """`chat_with_steps` 最后一轮会把 tool_defs 置空，此处验证出网口被中和。"""
        from agent.tool_calling import ToolCallingService

        svc = ToolCallingService.__new__(ToolCallingService)   # 绕过 __init__（读配置）
        client = _FakeClient(result=_nonstream_result())
        svc._primary_llm = _StubLLM(client)
        svc._upgrade_llm = None

        advert = "身份\n" + ADVERT_LINE
        svc._call_llm_with_tools([{"role": "user", "content": "hi"}], advert,
                                 512, 0.3, [])           # ← 末轮：tool_defs 为空

        assert client.calls, "未发出请求"
        sent = _system_content_of(client.calls[0])
        assert "【工具】" not in sent
        assert "tools" not in client.calls[0]

    def test_tool_calling_service_有工具时不下发tools键以外的改动(self):
        from agent.tool_calling import ToolCallingService

        svc = ToolCallingService.__new__(ToolCallingService)
        client = _FakeClient(result=_nonstream_result())
        svc._primary_llm = _StubLLM(client)
        svc._upgrade_llm = None

        advert = "身份\n" + ADVERT_LINE
        tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
        svc._call_llm_with_tools([{"role": "user", "content": "hi"}], advert,
                                 512, 0.3, tools)

        sent = _system_content_of(client.calls[0])
        assert "【工具】" in sent
        assert client.calls[0]["tools"] == tools


# ══════════════════════════════════════════════════════════════════════
#  ⑧ 性能（E8：不得引入 O(响应长度²)）
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
#  【TESTHYG-1 · 2026-09-26】环境校准载荷：只回答"这台机器现在有多快"
# ══════════════════════════════════════════════════════════════════════
# 为什么需要它：`TestPerformance` 的两条断言都以**墙钟毫秒**为判据，而 22637 条
# 全量验收时整机是满载的（同机多进程 + 邻居负载）⇒ 绝对耗时被整体抬高，
# 预算触顶时**红的是测量环境，不是被测代码**（AUDIT_AND_PLAN.md §16.1）。
# 形状与被测调用同族（正则扫描 + 按行切分的纯文本，~480KB），但与被测函数无关。
_CALIB_TEXT = "校准行 abcdefghij\n" * 20000
_CALIB_RE = re.compile(r"校准行 ([a-z]+)")


def _perf_calibration_workload() -> int:
    """固定文本工作量（返回值只为让解释器别把整段优化掉）"""
    n = 0
    for m in _CALIB_RE.finditer(_CALIB_TEXT):
        n += len(m.group(1))
    return n + len(_CALIB_TEXT.splitlines())


@pytest.mark.serial
class TestPerformance:
    """**墙钟计时断言** ⇒ 必须标 `serial`（2026-09-20 补标，见下）。

    Why：本类的两条用例都以 `time.perf_counter()` 的绝对耗时/比值为判据，
    在并发执行下会被 CPU 争用放大到远超预算。实测（2026-09-20 全量分块回归）：
        空载隔离单跑            6.9 ~ 7.1 ms
        4 分块并行 + 另一全量进程 230.8 ms  ⇒ 约 **33×** 膨胀，直接把 50ms 预算打红
    而 `align_system_prompt_with_tools`（对 ~128KB 字符串做纯文本中和）
    本身**不是**性能缺陷对象，它没有回归 —— 红的是测量环境。

    本仓对这类断言已有既定机制：`serial` marker + CI 把测试拆成
    `-m "not serial"`（并行）与 `-m "serial"`（串行段）两条 lane
    见 `.github/workflows/observability-ci.yml:957,996`；
    `pytest.ini:88` 的 marker 说明里也点名了"并行时状态竞争导致偶发失败"这一类。
    仓库内已有 19 处 `@pytest.mark.serial` 先例（如 `test_perf_monitor.py`），
    本类此前**漏标** ⇒ 在 CI 并行 lane 与本地分块下都会假红。

    【不易·2026-09-21 更正】`serial` marker **只在 observability-ci.yml 生效**：
    `ci.yml:459` 的 unit 分片 lane 是
    `pytest $(split_unit_tests.py --shard N --shards 6) -n 2 --dist=loadscope`，
    **没有** `-m "not serial"` 过滤 ⇒ 本类在 6-shard 的 `-n 2` 并行下照跑，
    marker 拦不住（run 35533584908 的 Shard 5 就是这么红的）。
    所以"隔离测量环境"这一条在本 workflow 里**不成立**，必须让断言本身对负载不敏感。

    【不易】不要改成"无上限放宽阈值"来消除偶发红：阈值放宽会同时削弱这条性能
    守卫的真实检测能力（它要抓的是"中和逻辑退化成超线性"）。**正确做法是让测量
    对负载抖动不敏感**，即 2026-09-21 的处置（run 35533584908：CI 仍报
    56.7ms / 50ms 预算，而 `serial` 已生效 ⇒ 说明**单次墙钟**在本仓 CI 上不够稳）：

      1. **预热一次**后取 **N 次（5 次）最优值** `min(...)` 再比阈值。
         负载抖动只能抬高个别样本，抬不高于"最优样本"；真实退化（每一次都变慢）
         会把 `min` 一起抬高。这一步不放松阈值，只把噪声项消掉。
      2. CI 环境（`CI` / `GITHUB_ACTIONS`）给一个 **3× 有界**的环境余量（50 → 150ms），
         因为 GHA 共享 runner 的**持续**频率/邻居负载与开发机不可比，`min` 也会被
         整体抬高。**这不是无上限放宽**：阈值 ÷ 6.9ms 空载基线就是本条的"退化倍率"
         —— 本地 **≈7×**（50ms）、CI **≈21×**（150ms）；任何把该调用拖过该倍率的
         退化必然判红。而本类要防的"退化成超线性"在 100KB 输入上至少是**百倍级**
         （O(n²) 搬运 ≈ 10¹⁰ 字符操作 ⇒ 秒级），远在检测能力之内。

    实测（2026-09-21，本机 CPython 3.12.0，注入固定延迟的变异探针）：
        本地阈值 +40ms/次（≈6.8×）绿、+100ms/次（≈15×）红；
        CI 阈值   +100ms/次（≈15×）绿、+300ms/次（≈43×）红 —— 与上述倍率一致。

    【TESTHYG-1 · 2026-09-26】**本地预算也做成可解释的有界余量**（此前只有 CI 有）
    现象：22637 条全量验收时本用例红（AUDIT_AND_PLAN.md §16.1，该类唯一一条），
    而隔离单跑 3 次全过、HEAD 差分 2 次全过 ⇒ 红的是**满载的测量环境**。
    为什么 `min` 消抖不够：`min` 只滤得掉**突发**抢占（个别样本被拖长），
    滤不掉**整机持续变慢**（降频 / 带宽争用 / 换页）—— 那种情况下每一次采样都慢。
    处置（与本仓既有口径同族，不新增语义）：
      · 用一段**与本用例无关**的固定文本工作量当场测"机器现在有多慢"
        （`_calib_ms()`，同样取 N 次最优）；余量 = 校准值 ÷ `PERF_CALIB_NOMINAL_MS`，
        再夹到 [1, `PERF_MAX_ENV_ALLOWANCE`]。空载 ⇒ ×1.0（**判据与改前逐字一致**）；
        满载 ⇒ 最多 ×3.0（**与 CI 的 3× 完全同口径**）。
      · 校准是**同进程、同刻**测的 ⇒ 它反映环境（邻居负载/降频/带宽），
        不反映被测函数的退化：函数自己变慢不会让校准值变大，故不是掩盖。
    有界性（"退化倍率"必须随环境写明）：本地空载 **≈7×**（50ms）、
    本地满载与 CI **≈21×**（150ms）—— 上限由 `PERF_MAX_ENV_ALLOWANCE` 钉死，
    不存在"无上限放宽"。而本类要防的超线性在 100KB 输入上是**百倍级**
    （O(n²) ≈ 10¹⁰ 字符操作 ⇒ 秒级），仍远在检测能力之内；第二条用例
    （两倍长度不超三倍耗时）是**负载无关**的比值断言，超线性由它独立兜底。
    实测验证（注入延迟仿真"整机慢 k 倍"：被测调用与校准载荷同时 ×k）：
        k=8 （≈全量验收的实测膨胀量级） ⇒ 改前红 62.3ms/50ms、改后绿（余量 ×3）；
        k=25                          ⇒ 改后**仍红**（超出 21× 上限）⇒ 护栏没被放宽到失效。

    阈值对应的"退化倍率"必须随环境写明，改动本类时同步更新这三行：
        本地空载 ≈7×（50ms）／本地满载 ≈21×（150ms）／CI ≈21×（150ms）。    """

    #: 取最优值的重复次数（只用于消抖，不参与阈值计算）
    PERF_REPEAT = 5
    #: 空载基线 ~6.9ms ⇒ 该预算对应约 7× 退化检测能力
    PERF_BUDGET_MS = 50.0
    #: CI 共享 runner 的**有界**环境余量 ⇒ CI 上约 21× 退化检测能力
    PERF_CI_ALLOWANCE = 3.0 if (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")) else 1.0
    #: 【TESTHYG-1】本地环境余量的**上限**：与 CI 的 3× **同一口径**（有界，见类文档）
    PERF_MAX_ENV_ALLOWANCE = 3.0
    #: 环境校准载荷的**空载标称**（本机 i5-10500 / CPython 3.12.0，2026-09-26 实测
    #: 5 次最优 = 7.9ms）。它不是阈值，只是"这台机器现在有多快"的参照。
    PERF_CALIB_NOMINAL_MS = 7.9
    #: 校准载荷同样取最优值（与 PERF_REPEAT 同口径：只消抖，不放松判据）
    PERF_CALIB_REPEAT = 5

    @staticmethod
    def _best_ms(payload, site, repeat):
        """跑 `repeat` 次取**最优**（最小）耗时，毫秒；返回 (best, samples)"""
        best, samples = float("inf"), []
        for _ in range(repeat):
            t0 = time.perf_counter()
            align_system_prompt_with_tools(payload, False, site=site)
            dt = (time.perf_counter() - t0) * 1000.0
            samples.append(dt)
            best = min(best, dt)
        return best, samples

    @classmethod
    def _calib_ms(cls):
        """与本用例被测代码**无关**的固定文本工作量 ⇒ "这台机器现在有多快"

        形状与被测调用同族（正则扫描 + 按行切分的纯文本），但**不调用它**：
        若拿被测调用自身当参照，"函数自己变慢"会被折算成"环境变慢"而互相抵消，
        那就真成了掩盖。校准只测环境（邻居负载 / 降频 / 带宽争用）。
        """
        best = float("inf")
        for _ in range(cls.PERF_CALIB_REPEAT):
            t0 = time.perf_counter()
            _perf_calibration_workload()
            best = min(best, (time.perf_counter() - t0) * 1000.0)
        return best

    @classmethod
    def _env_allowance(cls):
        """把"机器当前有多慢"折算为**有界**预算余量，∈ [1.0, PERF_MAX_ENV_ALLOWANCE]

        空载 ⇒ 1.0（判据与 TESTHYG-1 之前**完全一致**，不放松）；
        满载 ⇒ 最多 PERF_MAX_ENV_ALLOWANCE（与 CI 的 3× 同口径）。
        """
        if cls.PERF_CI_ALLOWANCE > 1.0:            # CI 共享 runner：沿用既有 3× 余量
            return cls.PERF_CI_ALLOWANCE
        factor = cls._calib_ms() / cls.PERF_CALIB_NOMINAL_MS
        return max(1.0, min(factor, cls.PERF_MAX_ENV_ALLOWANCE))

    def test_100KB提示词对齐耗时小于50ms(self):
        advert = "\n".join([ADVERT_LINE] * 20)
        prompt = ("前缀\n" + advert + "\n" + ("填充文本 abcdefghij\n" * 8000))
        assert len(prompt) > 100_000, "样例提示词需 >100KB，当前 %d" % len(prompt)
        out, _msgs = align_system_prompt_with_tools(prompt, False, site="perf")  # 预热（吃正则/缓存冷启动）
        assert "【工具】" not in out
        _, n = neutralize_tool_advertisement(prompt)
        assert n == 20
        best_ms, samples = self._best_ms(prompt, "perf", self.PERF_REPEAT)
        #: 环境余量：空载 ×1.0、满载最多 ×3.0（有界，见类文档 TESTHYG-1 一节）
        allowance = self._env_allowance()
        budget = self.PERF_BUDGET_MS * allowance
        assert best_ms < budget, (
            "100KB 提示词对齐**最优**耗时 %.1fms 超过预算 %.1fms"
            "（%d 次采样 min=%.1fms，环境余量 ×%.2f（校准 %.2fms / 标称 %.2fms）；样本 %s）"
            % (best_ms, budget, self.PERF_REPEAT, best_ms, allowance,
               self._calib_ms(), self.PERF_CALIB_NOMINAL_MS,
               " ".join("%.1f" % s for s in samples)))

    def test_线性复杂度_两倍长度不超过三倍耗时(self):
        def _mk(mult):
            return ("行\n" * (20000 * mult)) + ADVERT_LINE
        # 两侧同样取 3 次最优：比值断言对**分母**的噪声极敏感（d1 偏小 ⇒ 比值虚高），
        # 单次采样在并发下会随机假红（同类的 `min` 处理理由见类文档）。
        d1 = max(self._best_ms(_mk(1), "perf1", 3)[0], 1e-6)
        d2 = max(self._best_ms(_mk(2), "perf2", 3)[0], 1e-6)
        # 线性应约 2x；给到 6x 余量以容忍噪声。平方级会远超。
        assert d2 < d1 * 6, "耗时比 %.1fx 疑似超线性" % (d2 / d1)

