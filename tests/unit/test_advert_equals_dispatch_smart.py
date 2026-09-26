# -*- coding: utf-8 -*-
"""B1-T2：「宣告 = 下发」不变量补齐到**智能选择**路径。

────────────────────────────────────────────────────────────────────────────
这条不变量在智能选择路径上曾经不成立
────────────────────────────────────────────────────────────────────────────
B1 已把提示词里的工具数改成由**真实下发集**实算（render_tool_advert_line），
主线路径因此自洽（生产 = 26 个，见 docs/audit_skill_governance/B1.md）。
但编排器还有一条路径会在白名单之后**再收窄一次**：

    orchestrator._call_llm    : if _line_used is None and self._is_smart_tool_selection_enabled()
    orchestrator._call_llm_v2 : if not _line_used and self._is_smart_tool_selection_enabled()

即「**无激活主线 + 开启智能选择**」时按 user_input 走 hybrid_select_tools()。
修复前**渲染发生在这次收窄之前**（V1 早约 136 行、V2 早约 60 行）⇒ 提示词宣告的是
收窄前的白名单，而请求体 tools= 里是收窄后的集合：宣告≠下发
（B1 §6.2 · B1-TODO-1 登记，本卡修复）。

本文件把这条路径钉死：
    ① V1（_call_llm）  ：宣告数 == 请求体 tools 长度（且名字同源同序）
    ② V2（_call_llm_v2）：宣告数 == 出网 tools_whitelist 对应的 tool_defs 长度
    ③ 智能选择返回空 ⇒ **退回白名单**时两侧同样一致（"没返回"不得变成口径错位）
    ④ 有激活主线时仍然优先主线、不再走智能选择（口径仍一致）

【不易】用**真实渲染器**（DigitalLifePersonaMixin._build_tool_status_text）与
**真实注册表**（agent.tools.register）驱动整条链路，只在"外部世界"打桩
（line_whitelist / hybrid_select_tools / LLM 客户端）—— 被打桩的那两个正是
本卡要模拟的"无主线"与"智能选择收窄"，其余全是生产代码。
"""
import os
import re
import sys
from unittest.mock import MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: 渲染行的解析式（与 agent/tools_prompt_guard.render_tool_advert_line 同格式）
_ADVERT_RE = re.compile(r"【工具】本轮向模型下发\((\d+) 个\): ([^\n]*)")

#: 本文件注册的测试工具来源标识（fixture 只注销这一来源，不碰真实注册表）
_SRC = "b1t2_advert_equals_dispatch"

#: 白名单里的 6 个工具；智能选择只留下前 2 个 ⇒ 收窄必须可观测（6 ≠ 2）
_WL = ["b1t2_t%d" % i for i in range(6)]
_SMART = _WL[:2]
_LINE = _WL[:3]          # 模拟"有激活主线"时装配器给出的工具集


def _advert(text):
    """从提示词里取 (宣告数, 宣告的工具名列表)；取不到返回 (None, None)。"""
    m = _ADVERT_RE.search(text or "")
    if not m:
        return None, None
    body = m.group(2).strip()
    return int(m.group(1)), ([x.strip() for x in body.split(",") if x.strip()] if body else [])


@pytest.fixture
def tools6():
    """在**真实注册表**里登记 6 个工具，用完按 source 注销。"""
    from agent import tools as _tools

    try:
        for n in _WL:
            _tools.register(
                n, "B1-T2 用例工具 %s" % n,
                schema={"type": "object", "properties": {}, "additionalProperties": True},
                handler=(lambda **kw: None), source=_SRC)
        yield _WL
    finally:
        _tools.unregister_by_source(_SRC)


def _orch_class():
    """生产类 DigitalLife（Orchestrator + 各 Mixin 的真实组合）。

    真实类才有 _build_tool_status_text / _is_smart_tool_selection_enabled 等混入方法；
    拿不到时退化为最小组合，保证本文件在裁剪过的环境里也能给出结论。
    """
    try:
        from agent.digital_life import DigitalLife
        return DigitalLife
    except Exception:  # noqa: BLE001
        from agent.digital_life_persona import DigitalLifePersonaMixin
        from agent.orchestrator.orchestrator import Orchestrator

        class _Shim(Orchestrator, DigitalLifePersonaMixin):
            pass

        return _Shim


def _build_v1_orch():
    """构一个只差"外部世界"的 _call_llm 宿主（LLM 客户端为假）。"""
    orch = object.__new__(_orch_class())
    orch._session_id = "b1t2-v1-session"
    orch._current_mode = "default"
    orch._behavior = MagicMock()
    orch._behavior.profile.label = "默认"
    orch._behavior.profile.description = "默认模式"
    orch._behavior.profile.response_prefix = ""
    orch._memory_token_limit = 8000
    orch._interaction_count = 0
    orch._current_tool_steps = []
    orch._llm_pro = None
    orch._cached_tool_status = None
    orch._cached_skill_instructions = None
    orch._loaded_skill_ids = []
    orch._set_thinking_mode = MagicMock()
    orch._context_assembler_extra = MagicMock(return_value=None)
    orch._build_skill_instructions = MagicMock(return_value="")
    orch._get_lifetrace_context = MagicMock(return_value="")
    orch._select_model_for_request = MagicMock(return_value=("main-model", "main-model"))

    memory = MagicMock()
    memory.load_summary.return_value = None
    memory.get_context.return_value = []
    memory.get_working_memory.return_value = {}
    memory._storage.load_recent_messages.return_value = []
    memory.get_budget_context.return_value = []
    memory._token_counter.count.return_value = 100
    memory._token_counter.count_messages.return_value = 100
    orch._memory = memory

    orch._tool_calling_service = MagicMock()
    orch._get_enabled_tools_whitelist = MagicMock(return_value=list(_WL))
    orch._is_smart_tool_selection_enabled = MagicMock(return_value=True)

    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "OK"
    resp.choices[0].message.tool_calls = None
    resp.choices[0].message.reasoning_content = None
    client.chat.completions.create.return_value = resp
    llm = MagicMock()
    llm._get_client.return_value = client
    llm.model = "main-model"
    orch._llm = llm
    return orch, client


def _patch_selection(monkeypatch, smart):
    """把"无激活主线"与"智能选择"两个外部事实装好。

    Args:
        smart: 智能选择返回的工具名列表；None/空列表 = **检索没给出结果**，
            调用方应退回白名单（hybrid_select_tools(...) or get_tools_for_input(...)）。
    """
    import agent.lines as _lines
    import agent.orchestrator.orchestrator as _om

    monkeypatch.setattr(_lines, "line_whitelist", lambda wl=None: ([], None), raising=True)
    _smart = list(smart) if smart else []
    monkeypatch.setattr(_om, "hybrid_select_tools",
                        lambda ui, wl=None, **k: (list(_smart) or None), raising=True)
    monkeypatch.setattr(_om, "get_tools_for_input",
                        lambda ui, wl=None, **k: list(_smart), raising=True)


def _patch_active_line(monkeypatch):
    """模拟"有激活主线"：装配器给出 _LINE 三个工具。"""
    import agent.lines as _lines
    import agent.orchestrator.orchestrator as _om

    res = MagicMock()
    res.line_id = "b1t2_line"
    res.max_tools = 26
    res.needs_approval = []
    monkeypatch.setattr(_lines, "line_whitelist", lambda wl=None: (list(_LINE), res), raising=True)
    calls = []

    def _spy(ui, wl=None, **k):
        calls.append(ui)
        return None

    monkeypatch.setattr(_om, "hybrid_select_tools", _spy, raising=True)
    return calls


# ══════════════════════════════════════════════════════════════════════
#  ① V1：_call_llm 在「无激活主线 + 开启智能选择」下宣告数 == 下发数
# ══════════════════════════════════════════════════════════════════════

class TestV1AdvertEqualsDispatch:
    def test_智能选择收窄时宣告数等于请求体tools长度(self, tools6, monkeypatch):
        """**本卡的核心断言**：收窄发生在渲染之前 ⇒ 两侧同一个数字。

        修复前：宣告 6（收窄前白名单）、下发 2（收窄后）⇒ 本用例会红。
        """
        _patch_selection(monkeypatch, _SMART)
        orch, client = _build_v1_orch()

        orch._call_llm("帮我写代码并运行测试", "CPU: 正常")

        kwargs = client.chat.completions.create.call_args[1]
        tools = kwargs.get("tools") or []
        assert len(tools) == len(_SMART), (
            "前置条件：智能选择必须真的把下发集收窄到 %d 个（实际 %d）" % (len(_SMART), len(tools)))
        adv_n, adv_names = _advert(kwargs["messages"][0]["content"])
        assert adv_n == len(tools), (
            "宣告 %s 个、下发 %d 个 —— 智能选择路径上的口径又分裂了" % (adv_n, len(tools)))
        assert adv_names == [d["function"]["name"] for d in tools], "名字也必须同源同序"

    def test_智能选择返回空时退回白名单且两侧一致(self, tools6, monkeypatch):
        """收窄没结果 ⇒ 退回白名单（全量 6 个），宣告同样必须跟着变。"""
        _patch_selection(monkeypatch, [])
        orch, client = _build_v1_orch()

        orch._call_llm("你好", "CPU: 正常")

        kwargs = client.chat.completions.create.call_args[1]
        tools = kwargs.get("tools") or []
        assert len(tools) == len(tools6), (
            "前置条件：智能选择无结果必须退回白名单 %d 个（实际 %d）" % (len(tools6), len(tools)))
        adv_n, _ = _advert(kwargs["messages"][0]["content"])
        assert adv_n == len(tools) == len(tools6)

    def test_有激活主线时不走智能选择但口径仍一致(self, tools6, monkeypatch):
        """回归：主线优先（智能选择不该被调用），且宣告=下发仍然成立。"""
        calls = _patch_active_line(monkeypatch)
        orch, client = _build_v1_orch()

        orch._call_llm("帮我写代码并运行测试", "CPU: 正常")

        assert calls == [], "有激活主线时不得再走智能选择（会二次收窄）"
        kwargs = client.chat.completions.create.call_args[1]
        tools = kwargs.get("tools") or []
        assert len(tools) == len(_LINE)
        adv_n, adv_names = _advert(kwargs["messages"][0]["content"])
        assert adv_n == len(tools)
        assert adv_names == [d["function"]["name"] for d in tools]

    def test_allow_tools为假时提示词不宣传且不下发(self, tools6, monkeypatch):
        """反方向：本轮不下发工具时，宣告侧必须被源头中和（既有不变量，B1 契约）。"""
        from agent.tools_prompt_guard import prompt_advertises_tools

        _patch_selection(monkeypatch, _SMART)
        orch, client = _build_v1_orch()

        orch._call_llm("已执行过工具", "CPU: 正常", allow_tools=False)

        kwargs = client.chat.completions.create.call_args[1]
        assert not (kwargs.get("tools") or []), "allow_tools=False 时不得下发 tools"
        assert prompt_advertises_tools(kwargs["messages"][0]["content"]) is False


# ══════════════════════════════════════════════════════════════════════
#  ② V2：_call_llm_v2 同样把定稿提前到渲染之前
# ══════════════════════════════════════════════════════════════════════

class TestV2AdvertEqualsDispatch:
    @staticmethod
    def _build_v2_orch():
        orch, _client = _build_v1_orch()
        orch._session_id = "b1t2-v2-session"
        orch._v2_persona = None
        orch._v2_lifetrace = False
        orch._persona_injector = MagicMock()
        orch._run_llm_bounded = lambda fn, timeout=0: fn()
        orch._guard_llm_output = lambda resp, ui, **k: resp
        orch._tool_calling_service.chat_with_steps.return_value = {"text": "OK", "steps": []}
        orch._tool_calling_service._max_rounds = 3
        orch._tool_calling_service._tool_timeout = 30
        return orch

    def test_V2智能选择收窄时宣告数等于下发白名单的tool_defs长度(self, tools6, monkeypatch):
        from agent import tools as _tools

        _patch_selection(monkeypatch, _SMART)
        orch = self._build_v2_orch()

        orch._call_llm_v2("帮我写代码并运行测试", "CPU: 正常")

        assert orch._tool_calling_service.chat_with_steps.called
        kw = orch._tool_calling_service.chat_with_steps.call_args[1]
        # 出网口口径：agent/tool_calling.py 的 chat_with_steps 内部就是
        # tool_defs = tools.get_tool_defs(whitelist=tools_whitelist)
        dispatch = _tools.get_tool_defs(whitelist=kw.get("tools_whitelist"))
        assert len(dispatch) == len(_SMART), (
            "前置条件：V2 出网白名单必须已被智能选择收窄到 %d 个（实际 %d）"
            % (len(_SMART), len(dispatch)))
        adv_n, adv_names = _advert(kw["system_prompt"])
        assert adv_n == len(dispatch), (
            "V2 宣告 %s 个、下发 %d 个 —— 口径分裂" % (adv_n, len(dispatch)))
        assert adv_names == [d["function"]["name"] for d in dispatch]

    def test_V2智能选择返回空时退回白名单且两侧一致(self, tools6, monkeypatch):
        from agent import tools as _tools

        _patch_selection(monkeypatch, [])
        orch = self._build_v2_orch()

        orch._call_llm_v2("你好", "CPU: 正常")

        kw = orch._tool_calling_service.chat_with_steps.call_args[1]
        dispatch = _tools.get_tool_defs(whitelist=kw.get("tools_whitelist"))
        assert len(dispatch) == len(tools6)
        adv_n, _ = _advert(kw["system_prompt"])
        assert adv_n == len(dispatch)

    def test_V2人格分支的tool_status同样来自定稿集(self, tools6, monkeypatch):
        """persona 分支是 V2 的另一处渲染点，必须与模板分支同口径。"""
        from agent import tools as _tools

        _patch_selection(monkeypatch, _SMART)
        orch = self._build_v2_orch()
        orch._v2_persona = MagicMock()
        orch._persona_injector.build_system_prompt.return_value = "PERSONA_PROMPT"
        orch._get_user_context = MagicMock(return_value=None)

        orch._call_llm_v2("帮我写代码并运行测试", "CPU: 正常")

        status = orch._persona_injector.build_system_prompt.call_args[1]["tool_status"]
        dispatch = _tools.get_tool_defs(
            whitelist=orch._tool_calling_service.chat_with_steps.call_args[1].get("tools_whitelist"))
        adv_n, _ = _advert(status)
        assert adv_n == len(dispatch) == len(_SMART)
