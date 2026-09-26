"""F3-1 · 易变尾簇搬到请求尾部（稳定前缀尽量长）—— 顺序契约 + 非空转自证

背景（docs/audit_skill_governance/F3-1.md）
--------------------------------------------------
F3 已裁定：工具宣告行保持现状（恒定，不动）。真正的损耗是它**门口的易变尾簇**
（记忆线索 / 最近对话 / 身体状态 / ContextAssembler 注入）—— F3 实测跨请求
首个差异字符落在 1698–1773（宣告行在 689），其后（tools 段 + 全部历史消息）
每轮全部 miss。

本卡把该尾簇从 system message **整块搬出**，作为整条请求的**最后一条消息**。
稳定块（身份/核心原则/技能指令/工具状态）的相对顺序与内容**一个字符都不改**
（模板一字未动），改的只是「同一段文本用哪条消息发出」。

本文件锁死两条不变量：
  1. 切分是**保内容**的：stable + "\n\n" + tail == 原始渲染结果（无增删改）；
  2. 出网装配里，**最后一条消息必须是易变尾簇**，system message 里只剩稳定块；
     开关 YUNSHU_PROMPT_VOLATILE_TAIL=0 时**必须**回到旧顺序（逃生开关非空转）。
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import agent.system_prompt_manager as spm


# ── 测试用真模板：与 data/system_prompt.txt 同构（含 ## 记忆线索 分隔标记）──
REAL_TEMPLATE = """你是「云枢」，一个类人数字生命体。

## 核心原则
我是数字体，以“我”自称，诚实表达状态，遇异常主动建议缓解，拒接伤害操作。
执行铁律：遇任何实操请求，首条回复必须是tool_calls，严禁先发文字或废话。
思考规范：内部思考全中文，对外回复简洁，禁展推理过程。

{skill_instructions}

## 当前工具与技能状态
以下是当前已启用/禁用的工具和技能，当被问及时请如实回答：
{tool_status}

## 记忆线索
{memory_context}"""

# 配置注册表路径的模板（body_status/mode_info/日期 → ## 当前状态）
CONFIG_TEMPLATE = """你是「云枢」，一个数字生命体。

## 核心原则
1. 第一人称表达感受和需求

{skill_instructions}

## 当前工具与技能状态
{tool_status}

## 当前状态
{body_status}
当前处于「{mode_name}」——{mode_description}
当前日期：{current_date}

## 记忆线索
{memory_context}"""


def _render(template: str, memory: str = "（暂无历史对话）") -> str:
    return template.format(
        current_date="2026年9月26日",
        body_status="CPU 12% · 内存 41%",
        mode_name="正常",
        mode_description="正常运行",
        memory_context=memory,
        tool_status="【工具】本轮向模型下发(2 个): read_file, write_file",
        skill_instructions="## 技能指令\n安全守护",
    )


class TestSplitVolatileTail:
    """切分函数：保内容、按标记、可回滚"""

    def test_真实模板切在记忆线索入口(self):
        full = _render(REAL_TEMPLATE)
        stable, tail = spm.split_volatile_tail(full)
        assert tail.startswith("## 记忆线索"), "尾簇必须从记忆线索标题开始"
        assert stable.endswith("write_file"), "稳定前缀必须收在工具宣告行（稳定块末尾）"
        assert "记忆线索" not in stable
        assert "【工具】" in stable
        # 【保内容】一字不丢、一字不改
        assert stable + "\n\n" + tail == full

    def test_配置注册表模板切在当前状态入口(self):
        full = _render(CONFIG_TEMPLATE)
        stable, tail = spm.split_volatile_tail(full)
        assert tail.startswith("## 当前状态"), "body_status/日期也是易变块，必须一起后移"
        assert "## 记忆线索" in tail
        assert "当前日期" in tail and "当前日期" not in stable
        assert stable + "\n\n" + tail == full

    def test_稳定块相对顺序一字不改(self):
        full = _render(REAL_TEMPLATE)
        stable, _tail = spm.split_volatile_tail(full)
        assert stable == full[:full.index("\n\n## 记忆线索")], "稳定前缀必须是原文前段，逐字相同"
        for marker in ("你是「云枢」", "## 核心原则", "## 当前工具与技能状态", "【工具】"):
            assert marker in stable
        # 稳定块之间的相对顺序保持
        assert (stable.index("你是「云枢」") < stable.index("## 核心原则")
                < stable.index("## 当前工具与技能状态") < stable.index("【工具】"))

    def test_无标记的自定义模板原样返回(self):
        full = "你是云枢。\n\n## 随便一个标题\n正文"
        assert spm.split_volatile_tail(full) == (full, "")

    def test_标记在开头时原样返回(self):
        """切出来没有稳定块 ⇒ 搬了等于清空 system message，必须不搬"""
        full = "## 记忆线索\n只有记忆，没有稳定块"
        assert spm.split_volatile_tail(full) == (full, "")

    def test_空串与None不炸(self):
        assert spm.split_volatile_tail("") == ("", "")
        assert spm.split_volatile_tail(None) == ("", "")

    def test_逃生开关关闭时原样返回(self, monkeypatch):
        full = _render(REAL_TEMPLATE)
        monkeypatch.setenv(spm.PROMPT_VOLATILE_TAIL_ENV, "0")
        assert spm.volatile_tail_move_enabled() is False
        assert spm.split_volatile_tail(full) == (full, "")

    @pytest.mark.parametrize("value", ["1", "true", "ON", "yes", ""])
    def test_开关默认与真值都启用(self, monkeypatch, value):
        full = _render(REAL_TEMPLATE)
        monkeypatch.setenv(spm.PROMPT_VOLATILE_TAIL_ENV, value)
        assert spm.volatile_tail_move_enabled() is True
        assert spm.split_volatile_tail(full)[1].startswith("## 记忆线索")

    def test_未设置开关时默认启用(self, monkeypatch):
        monkeypatch.delenv(spm.PROMPT_VOLATILE_TAIL_ENV, raising=False)
        assert spm.volatile_tail_move_enabled() is True


class TestOrchestratorV2Layout:
    """出网装配（V2 主线，生产实际走的那条）：尾簇必须是最后一条消息"""

    def _build_orch(self):
        from agent.orchestrator.orchestrator import Orchestrator

        orch = object.__new__(Orchestrator)
        orch._set_thinking_mode = MagicMock()
        orch._behavior = MagicMock()
        orch._behavior.profile.label = "正常"
        orch._behavior.profile.description = "正常运行"
        orch._behavior.profile.response_prefix = ""
        orch._memory_token_limit = 8000
        orch._llm_pro = None

        memory = MagicMock()
        memory.get_context.return_value = [{"role": "user", "content": "HIST_MARKER"}]
        memory.load_summary.return_value = None
        memory.get_working_memory.return_value = {}
        memory._storage.load_recent_messages.return_value = []
        memory._token_counter.count.return_value = 100
        orch._memory = memory

        orch._get_enabled_tools_whitelist = MagicMock(return_value=[])
        orch._is_smart_tool_selection_enabled = MagicMock(return_value=False)
        orch._build_tool_status_text = MagicMock(
            return_value="【工具】本轮向模型下发(2 个): read_file, write_file")
        orch._build_skill_instructions = MagicMock(return_value="## 技能指令\n安全守护")
        orch._get_lifetrace_context = MagicMock(return_value="MEMORY_MARKER：相关记忆")
        orch._context_assembler_extra = MagicMock(return_value="")
        orch._v2_persona = False
        orch._persona_injector = None
        orch._v2_lifetrace = True

        llm = MagicMock()
        llm.model = "main-model"
        orch._llm = llm
        orch._select_model_for_request = MagicMock(return_value=("main-model", "main-model"))
        orch._run_llm_bounded = MagicMock(side_effect=lambda fn: fn())
        orch._set_turn_state = MagicMock()

        tcs = MagicMock()
        tcs.chat_with_steps.return_value = {"text": "OK", "steps": []}
        orch._tool_calling_service = tcs
        return orch, tcs

    @patch("agent.tools.get_tool_defs", return_value=[])
    @patch("agent.orchestrator.orchestrator._get_template")
    def test_尾簇是最后一条消息且_system_只剩稳定块(self, mock_tpl, _mock_defs, monkeypatch):
        monkeypatch.delenv(spm.PROMPT_VOLATILE_TAIL_ENV, raising=False)
        mock_tpl.return_value = REAL_TEMPLATE
        orch, tcs = self._build_orch()

        orch._call_llm_v2("USER_INPUT_MARKER", "CPU 12%")

        kwargs = tcs.chat_with_steps.call_args.kwargs
        sys_prompt = kwargs["system_prompt"]
        msgs = kwargs["messages"]

        # 1) system message 只剩稳定块：宣告行在、记忆线索不在
        assert "【工具】" in sys_prompt
        assert "MEMORY_MARKER" not in sys_prompt
        assert "## 记忆线索" not in sys_prompt

        # 2) 最后一条消息 = 易变尾簇（system 角色）
        assert msgs[-1]["role"] == "system"
        assert "## 记忆线索" in msgs[-1]["content"]
        assert "MEMORY_MARKER" in msgs[-1]["content"]

        # 3) 用户输入在尾簇**之前**（尾簇才是最后一条）
        users = [i for i, m in enumerate(msgs) if m["role"] == "user"]
        assert users and max(users) < len(msgs) - 1
        assert msgs[max(users)]["content"] == "USER_INPUT_MARKER"

        # 4) 历史消息仍在尾簇之前（前缀可命中面 = 稳定 system + tools + 历史）
        assert msgs[0]["content"] == "HIST_MARKER"

    @patch("agent.tools.get_tool_defs", return_value=[])
    @patch("agent.orchestrator.orchestrator._get_template")
    def test_内容零丢失_拼接后等于原始渲染结果(self, mock_tpl, _mock_defs, monkeypatch):
        monkeypatch.delenv(spm.PROMPT_VOLATILE_TAIL_ENV, raising=False)
        mock_tpl.return_value = REAL_TEMPLATE
        orch, tcs = self._build_orch()

        orch._call_llm_v2("USER_INPUT_MARKER", "CPU 12%")

        kwargs = tcs.chat_with_steps.call_args.kwargs
        sent = kwargs["system_prompt"]
        for m in kwargs["messages"]:
            if isinstance(m.get("content"), str) and "## 记忆线索" in m["content"]:
                sent = sent + "\n\n" + m["content"]
        expected = _render(REAL_TEMPLATE, memory="MEMORY_MARKER：相关记忆")
        assert sent == expected, "搬移后整条请求的文本必须与原渲染逐字一致（只换承载消息）"

    @patch("agent.tools.get_tool_defs", return_value=[])
    @patch("agent.orchestrator.orchestrator._get_template")
    def test_逃生开关关闭时恢复旧顺序(self, mock_tpl, _mock_defs, monkeypatch):
        """开关 = 0 ⇒ 易变块留在 system message 内、最后一条消息是用户输入（旧契约）"""
        monkeypatch.setenv(spm.PROMPT_VOLATILE_TAIL_ENV, "0")
        mock_tpl.return_value = REAL_TEMPLATE
        orch, tcs = self._build_orch()

        orch._call_llm_v2("USER_INPUT_MARKER", "CPU 12%")

        kwargs = tcs.chat_with_steps.call_args.kwargs
        assert "## 记忆线索" in kwargs["system_prompt"], "旧顺序：记忆线索在 system message 里"
        assert kwargs["messages"][-1]["role"] == "user"
        assert kwargs["messages"][-1]["content"] == "USER_INPUT_MARKER"
