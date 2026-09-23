"""提示词角色（片段"拥有者"）与 prompt_note 接线 —— 不变量回归测试。

为什么单独钉一个文件
────────────────────────────────────────────────────────────────────────────
系统提示词此前是"一个模板 + 多处在外部裸拼字符串"，拼接顺序由**代码位置**决定。
本次改动把它换成"片段集合 + 一次合并"（agent/prompt_manager/roles.py），
并把 LineProfile.prompt_note（此前是**死字段**：UI 承诺注入、data/agent_lines/*.yaml
里有内容，但运行时没有任何一处消费它）真正接到两个 LLM 路径上。

于是这里要钉住两类东西：
    ① 合并器本身：顺序确定、空片段丢弃、预算裁剪可解释、硬片段不被裁、丢弃可审计；
    ② 接线语义：未装线/字段为空/档案损坏 ⇒ 行为与改动前**逐字一致**；
      装了线且 prompt_note 非空 ⇒ 它出现在最终系统提示词里，且带来源标注。
第 ② 类是"未装线等于没改过"这条全仓接线纪律的可执行形式。

关于"确定"的程度（不要过度解读用例名）：priority 或 role 不同的片段，打乱输入
顺序输出逐字相同；**(priority, role) 完全相同的片段之间按传入顺序** —— 这是刻意的
契约（同一角色内保持调用点的因果顺序，例如两条 task 素材）。
"""

import json
import os
import random
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.orchestrator.prompt_builder import (
    PromptBuilder,
    line_prompt_fragment,
    system_tail_text,
    task_fragment,
)
from agent.prompt_manager.registry import PromptRegistry
from agent.prompt_manager.roles import (
    CROPPABLE_ROLES,
    HARD_ROLES,
    PROMPT_ROLES,
    ComposedPrompt,
    PromptFragment,
    compose_fragments,
    estimate_tokens,
)
from agent.prompt_manager.storage import (
    PromptStorage,
    PromptType,
    record_owner,
)

#: 与 agent/orchestrator/orchestrator.py 里 _call_llm 用到的同一套槽位
TEMPLATE = (
    "身份段\n当前日期：{current_date}\n模式：{mode_name}-{mode_description}\n"
    "身体：{body_status}\n记忆：{memory_context}\n工具：{tool_status}\n"
    "技能：{skill_instructions}"
)

#: 用例里传给 _call_llm 的入参（与旧路径逐字对比时的基准量）
BODY_STATUS = "CPU: 正常"
TOOL_MARKER = "TOOL_MARKER"          # 刻意不含【工具】宣传标记：避免工具一致性守卫改写提示词
SKILL_MARKER = "SKILL_MARKER"
MEMORY_FALLBACK = "（暂无历史对话）"


def _legacy_system_prompt() -> str:
    """改动前那条路径会产出的 system prompt（独立渲染，用于逐字对比）"""
    from datetime import datetime

    # 刻意与生产同形（_call_llm 用的就是无 tz 的 datetime.now），故 noqa: DTZ005
    now = datetime.now()  # noqa: DTZ005
    return TEMPLATE.format(
        current_date=f"{now.year}年{now.month}月{now.day}日",
        mode_name="默认",
        mode_description="默认模式",
        body_status=BODY_STATUS,
        memory_context=MEMORY_FALLBACK,
        tool_status=TOOL_MARKER,
        skill_instructions=SKILL_MARKER,
    )


# ══════════════════════════════════════════════════════════════════════
#  ① 合并器：排序确定性
# ══════════════════════════════════════════════════════════════════════


def _frags():
    """一组 (priority, role) 两两可区分的片段（打乱顺序不影响输出）"""
    return [
        PromptFragment("system", "SYS", priority=10, source="system"),
        PromptFragment("line", "LINE", source="line:eng"),
        PromptFragment("tool", "TOOL", source="tool"),
        PromptFragment("memory", "MEM", source="memory"),
        PromptFragment("task", "TASK", priority=90, source="task"),
    ]


class TestComposeOrdering:
    def test_词表顺序即默认输出顺序(self):
        got = [p.role for p in compose_fragments(_frags()).parts]
        assert got == ["system", "line", "tool", "memory", "task"], (
            "全默认 priority 时应按 PROMPT_ROLES 词表顺序输出")

    def test_打乱输入顺序_输出逐字相同(self):
        base = compose_fragments(_frags()).text
        for seed in range(20):
            shuffled = _frags()
            random.Random(seed).shuffle(shuffled)
            assert compose_fragments(shuffled).text == base, (
                "可区分片段（priority/role 不同）打乱输入顺序后输出必须逐字相同")

    def test_绝不依赖dict迭代顺序(self):
        """片段常来自 dict.values()；插入顺序不同不得改变输出。"""
        def build(order):
            d = {}
            for f in order:
                d[f.source] = f
            return compose_fragments(list(d.values())).text

        fwd = _frags()
        assert build(fwd) == build(list(reversed(fwd)))

    def test_priority优先于词表顺序(self):
        frags = [
            PromptFragment("task", "T", priority=1, source="task"),
            PromptFragment("system", "S", priority=99, source="system"),
        ]
        assert compose_fragments(frags).text == "T\n\nS"

    def test_同角色同优先级_按传入顺序(self):
        """契约：同一 (priority, role) 内保持调用点给的因果顺序。"""
        a = PromptFragment("task", "第一条", source="a")
        b = PromptFragment("task", "第二条", source="b")
        assert compose_fragments([a, b]).text == "第一条\n\n第二条"
        assert compose_fragments([b, a]).text == "第二条\n\n第一条"

    def test_自定义分隔符(self):
        frags = [PromptFragment("system", "A"), PromptFragment("line", "B")]
        assert compose_fragments(frags, separator="\n").text == "A\nB"


# ══════════════════════════════════════════════════════════════════════
#  ② 合并器：空片段 / 预算裁剪 / 硬片段 / 丢弃可审计
# ══════════════════════════════════════════════════════════════════════


class TestComposeEmptyAndBudget:
    def test_空片段与空白片段被丢弃且无多余分隔符(self):
        frags = [
            PromptFragment("system", "A", source="system"),
            PromptFragment("task", "", source="空的"),
            PromptFragment("task", "   \n\t ", source="全空白"),
            PromptFragment("line", "B", source="line"),
        ]
        c = compose_fragments(frags)
        assert c.text == "A\n\nB"
        assert [p.source for p in c.parts] == ["system", "line"]
        # 丢弃必须如实出现在 dropped 里（可审计），不许静默消失
        assert [(d.fragment.source, d.reason) for d in c.dropped] == [
            ("空的", "empty"), ("全空白", "empty")]

    def test_预算充足时不做任何裁剪(self):
        c = compose_fragments(_frags(), budget_tokens=10_000, counter=len)
        assert not c.dropped and not c.overflow
        assert [p.role for p in c.parts] == ["system", "line", "tool", "memory", "task"]

    def test_预算超限_按输出逆序丢可裁剪片段(self):
        frags = [
            PromptFragment("line", "L" * 20, source="line"),
            PromptFragment("skill", "K" * 20, source="skill"),
            PromptFragment("memory", "M" * 20, source="memory"),
            PromptFragment("task", "T" * 20, source="task"),
        ]
        full = compose_fragments(frags).text
        c = compose_fragments(frags, budget_tokens=len(full) - 21, counter=len)
        # 恰好丢掉一个片段就能压回预算 ⇒ 丢的是输出顺序里最后一个可裁剪片段 task
        assert [p.source for p in c.parts] == ["line", "skill", "memory"]
        assert [(d.fragment.source, d.reason) for d in c.dropped] == [("task", "budget")]
        assert not c.overflow
        assert c.tokens == len(c.text)

    def test_裁剪后text与保留片段严格一致(self):
        frags = [
            PromptFragment("line", "L" * 30, source="line"),
            PromptFragment("task", "T" * 30, source="t1"),
            PromptFragment("memory", "M" * 30, source="m1"),
        ]
        c = compose_fragments(frags, budget_tokens=60, counter=len)
        assert c.text == "\n\n".join(p.content for p in c.parts)
        assert len(c.dropped) == 3 - len(c.parts), "丢弃数必须与保留数互补（无静默丢失）"

    def test_硬片段绝不被裁_预算仍不够时如实上报overflow(self):
        frags = [
            PromptFragment("line", "L" * 50, source="line"),
            PromptFragment("persona", "P" * 50, source="persona"),
            PromptFragment("task", "T" * 50, source="task"),
        ]
        c = compose_fragments(frags, budget_tokens=1, counter=len)
        assert [p.role for p in c.parts] == ["persona", "line"], "可裁剪片段该丢，硬片段该留"
        assert c.text == "P" * 50 + "\n\n" + "L" * 50
        assert c.overflow is True, "预算仍不够时必须如实上报，而不是偷偷切半个硬片段"

    def test_词表与可裁剪性自洽(self):
        assert HARD_ROLES == frozenset({"system", "persona", "line"})
        assert CROPPABLE_ROLES | HARD_ROLES == set(PROMPT_ROLES)
        assert not (CROPPABLE_ROLES & HARD_ROLES)

    def test_未知角色被拒绝_非法输入不静默(self):
        with pytest.raises(ValueError):
            PromptFragment("nope", "内容")
        with pytest.raises(TypeError):
            PromptFragment("system", None)

    def test_无counter时用确定性粗估(self):
        c = compose_fragments([PromptFragment("system", "a" * 40)])
        assert c.token_source == "estimate"
        assert c.tokens == estimate_tokens("a" * 40) == 10

    def test_传了真counter则如实标记来源(self):
        class _C:
            def count(self, text):
                return 7

        c = compose_fragments([PromptFragment("system", "abc")], counter=_C())
        assert (c.tokens, c.token_source) == (7, "counter")

    def test_role_text与审计清单(self):
        c = compose_fragments([
            PromptFragment("task", "T1", source="a"),
            PromptFragment("task", "T2", source="b"),
            PromptFragment("line", "L", source="line:x"),
        ])
        assert c.role_text("task") == "T1\n\nT2"
        assert c.role_text("tool") == ""
        assert c.sources() == ("line:x", "a", "b")
        assert c.dropped_descriptions() == ()
        assert isinstance(c, ComposedPrompt)


# ══════════════════════════════════════════════════════════════════════
#  ③ 拥有者维度（registry / storage，零 schema 迁移）
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture()
def registry(tmp_path):
    st = PromptStorage(storage_path=str(tmp_path / "prompts"))
    return PromptRegistry(storage=st)


class TestOwnerDimension:
    def test_注册时声明拥有者_可按owner查询(self, registry):
        registry.register_prompt("p1", "主线片段", "内容", PromptType.SYSTEM,
                                 owner="line", tags=["t1"])
        registry.register_prompt("p2", "工具状态", "内容", PromptType.TOOL, owner="tool")
        registry.register_prompt("p3", "无主", "内容", PromptType.SYSTEM)
        assert [p.prompt_id for p in registry.list_prompts_by_owner("line")] == ["p1"]
        assert [p.prompt_id for p in registry.list_prompts_by_owner("tool")] == ["p2"]
        # 空串 = 只要"未声明拥有者"的历史记录（不默认归给任何角色）
        assert [p.prompt_id for p in registry.list_prompts_by_owner("")] == ["p3"]
        assert len(registry.list_prompts()) == 3

    def test_list_prompts形状与既有口径一致(self, registry):
        registry.register_prompt("p1", "主线片段", "内容", PromptType.SYSTEM,
                                 owner="line", tags=["t1"])
        registry.register_prompt("p2", "工具状态", "内容", PromptType.TOOL, owner="tool")
        # 内容类型 + owner 组合过滤
        got = registry.list_prompts(PromptType.TOOL, tags=None, owner="tool")
        assert [p.prompt_id for p in got] == ["p2"]
        # tags 过滤仍然生效（两者可同时用）
        assert registry.list_prompts(prompt_type=None, tags=["t1"], owner="line")[0].prompt_id == "p1"
        assert registry.list_prompts(prompt_type=None, tags=["t1"], owner="tool") == []

    def test_元数据与拥有者同源(self, registry):
        registry.register_prompt("p1", "主线片段", "内容", PromptType.SYSTEM, owner="line")
        meta = registry.get_prompt_metadata("p1")
        assert meta.owner == "line"
        assert record_owner(registry.get_prompt("p1")) == "line"

    def test_更新与撤销拥有者(self, registry):
        registry.register_prompt("p1", "n", "c", owner="skill")
        registry.update_prompt("p1", owner="task")
        assert [p.prompt_id for p in registry.list_prompts_by_owner("task")] == ["p1"]
        registry.update_prompt("p1", owner="")
        assert [p.prompt_id for p in registry.list_prompts_by_owner("")] == ["p1"]

    def test_词表外的owner告警但仍存储(self, registry, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            registry.register_prompt("p1", "n", "c", owner="not_a_role")
        assert record_owner(registry.get_prompt("p1")) == "not_a_role", (
            "数据不得因词表更迭而丢失")
        assert any("unknown_owner" in r.getMessage() for r in caplog.records)

    def test_零schema迁移_表结构未加列(self, registry, tmp_path):
        registry.register_prompt("p1", "n", "c", owner="line")
        db = tmp_path / "prompts" / "prompts.db"
        with sqlite3.connect(str(db)) as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(prompts)")]
        assert "owner" not in cols, "拥有者走 metadata JSON 袋，不得改表结构"
        assert "metadata" in cols


# ══════════════════════════════════════════════════════════════════════
#  ④ 接线：未装线 ⇒ 与旧路径逐字一致；装了线 ⇒ 片段出现在最终提示词里
# ══════════════════════════════════════════════════════════════════════


def _write_line(lines_dir, line_id, *, prompt_note, enabled=True, broken=False):
    os.makedirs(lines_dir, exist_ok=True)
    if broken:
        with open(os.path.join(lines_dir, line_id + ".yaml"), "w", encoding="utf-8") as f:
            f.write("- 这不是字典\n- 而是列表\n")
    else:
        with open(os.path.join(lines_dir, line_id + ".yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({
                "id": line_id, "name": "测试线", "enabled": enabled,
                "max_tools": 10, "prompt_note": prompt_note,
            }, f, allow_unicode=True)
    with open(os.path.join(lines_dir, "_active.json"), "w", encoding="utf-8") as f:
        json.dump({"active": line_id}, f)


def _build_orch():
    """构造一个 mock 好的 Orchestrator（object.__new__，绕过 __init__）"""
    from agent.orchestrator.orchestrator import Orchestrator

    orch = object.__new__(Orchestrator)
    orch._behavior = MagicMock()
    orch._behavior.profile.label = "默认"
    orch._behavior.profile.description = "默认模式"
    orch._memory_token_limit = 8000
    orch._current_mode = "default"
    orch._llm_pro = None
    orch._tool_calling_service = None
    orch._set_thinking_mode = MagicMock()
    orch._build_tool_status_text = MagicMock(return_value=TOOL_MARKER)
    orch._build_skill_instructions = MagicMock(return_value=SKILL_MARKER)
    orch._context_assembler_extra = MagicMock(return_value="")
    orch._get_enabled_tools_whitelist = MagicMock(return_value=[])
    orch._is_smart_tool_selection_enabled = MagicMock(return_value=False)
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

    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "LLM_RESPONSE"
    resp.choices[0].message.tool_calls = None
    resp.choices[0].message.reasoning_content = None
    client.chat.completions.create.return_value = resp
    llm = MagicMock()
    llm._get_client.return_value = client
    llm.model = "main-model"
    llm._is_openai_compat.return_value = True
    orch._llm = llm
    return orch, client


def _final_system_prompt(monkeypatch):
    """跑一次 _call_llm，取最终发到上游的 system 消息内容"""

    monkeypatch.setattr("agent.orchestrator.orchestrator._get_template", lambda: TEMPLATE)
    monkeypatch.setattr("agent.tools.get_tool_defs", lambda **kw: [], raising=False)

    orch, client = _build_orch()
    orch._call_llm("用户输入", BODY_STATUS)
    assert client.chat.completions.create.called, "应发出 LLM 请求"
    kwargs = client.chat.completions.create.call_args[1]
    api_msgs = kwargs["messages"]
    assert api_msgs[0]["role"] == "system"
    return api_msgs[0]["content"]


class TestLinePromptNoteWiring:
    def test_未装线_片段为None且提示词与旧路径逐字一致(self, monkeypatch, tmp_path):
        empty = str(tmp_path / "no_lines")
        os.makedirs(empty, exist_ok=True)
        monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", empty)

        assert line_prompt_fragment() is None, "没有激活主线 ⇒ 不取片段"
        assert _final_system_prompt(monkeypatch) == _legacy_system_prompt(), (
            "未装线时必须与改动前逐字一致（未装线 = 没改过）")

    def test_prompt_note为空_不注入(self, monkeypatch, tmp_path):
        d = str(tmp_path / "lines")
        _write_line(d, "testline", prompt_note="   \n  ")
        monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", d)

        assert line_prompt_fragment() is None, "空/全空白 note 视为未提供"
        assert _final_system_prompt(monkeypatch) == _legacy_system_prompt()

    def test_停用线_按未装线处理(self, monkeypatch, tmp_path):
        d = str(tmp_path / "lines")
        _write_line(d, "testline", prompt_note="本线的交付标准是改完并验证", enabled=False)
        monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", d)

        assert line_prompt_fragment() is None
        assert _final_system_prompt(monkeypatch) == _legacy_system_prompt()

    def test_装了线_片段进入最终系统提示词且带来源标注(self, monkeypatch, tmp_path, caplog):
        import logging
        d = str(tmp_path / "lines")
        note = "本线的交付标准是改完并验证：不得留下未验证的改动。"
        _write_line(d, "testline", prompt_note=note)
        monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", d)

        frag = line_prompt_fragment()
        assert frag is not None
        assert (frag.role, frag.source) == ("line", "line:testline"), "来源标注必须指明是哪条线"
        assert frag.hard is True, "主线片段是不可裁剪的硬片段"

        with caplog.at_level(logging.INFO):
            prompt = _final_system_prompt(monkeypatch)
        assert prompt == _legacy_system_prompt() + "\n\n" + note, (
            "除新增的 line 片段外，其余必须逐字不变，且片段追加在末尾")
        assert any("line_prompt_note" in r.getMessage() and "line:testline" in r.getMessage()
                   for r in caplog.records), "注入必须留下可审计的结构化日志"

    def test_档案损坏_降级不抛且与未装线逐字一致(self, monkeypatch, tmp_path, caplog):
        import logging
        d = str(tmp_path / "lines")
        _write_line(d, "testline", prompt_note="x", broken=True)
        monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", d)

        with caplog.at_level(logging.WARNING):
            assert line_prompt_fragment() is None, "取档案异常必须降级返回 None，绝不抛"
        assert any("degraded" in r.getMessage() for r in caplog.records)
        # 取档案异常不得影响对话：提示词回到未装线口径
        assert _final_system_prompt(monkeypatch) == _legacy_system_prompt()

    def test_显式传入line_id时优先(self, monkeypatch, tmp_path):
        d = str(tmp_path / "lines")
        _write_line(d, "testline", prompt_note="A 线的片段")
        _write_line(d, "other", prompt_note="B 线的片段")
        monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", d)

        assert line_prompt_fragment("other").content == "B 线的片段"
        assert line_prompt_fragment("testline").content == "A 线的片段"
        assert line_prompt_fragment("不存在") is None

    def test_主线不存在_返回None(self, monkeypatch, tmp_path):
        d = str(tmp_path / "lines")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_active.json"), "w", encoding="utf-8") as f:
            json.dump({"active": "ghost"}, f)
        monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", d)
        assert line_prompt_fragment() is None


# ══════════════════════════════════════════════════════════════════════
#  ⑤ 尾部合并与 build_system_prompt 的片段入参
# ══════════════════════════════════════════════════════════════════════


class TestSystemTailAndBuilder:
    def test_无槽位角色进尾部_槽位角色不进尾部(self):
        frags = [
            PromptFragment("line", "LINE", source="line:x"),
            PromptFragment("task", "TASK", source="t"),
            PromptFragment("tool", "TOOL", source="tools"),
            PromptFragment("skill", "SKILL", source="skills"),
            PromptFragment("memory", "MEM", source="mem"),
        ]
        assert system_tail_text(frags) == "LINE\n\nTASK"

    def test_空集合返回空串(self):
        assert system_tail_text([]) == ""
        assert system_tail_text(None) == ""

    def test_hard_only只保留硬片段(self):
        frags = [
            PromptFragment("line", "LINE", source="line:x"),
            PromptFragment("task", "TASK", source="t"),
        ]
        assert system_tail_text(frags, hard_only=True) == "LINE"

    def test_非片段对象降级为按传入顺序拼接(self):
        """调用方传错对象时绝不抛（提示词构建失败不得打断对话）"""
        assert system_tail_text(["不是片段", "也不是"]) == "不是片段\n\n也不是"

    def test_task_fragment对非字符串与空白返回None(self):
        """调用点在 system prompt 构建路径上 ⇒ 类型/空白问题在入口判掉，绝不抛"""
        assert task_fragment(None, "context_assembler") is None
        assert task_fragment("", "workflow_material") is None
        assert task_fragment("   \n ", "workflow_material") is None
        assert task_fragment({"not": "str"}, "workflow_material") is None

    def test_task_fragment构造合法片段(self):
        f = task_fragment("素材", "workflow_material")
        assert f is not None
        assert (f.role, f.content, f.source) == ("task", "素材", "workflow_material")

    def test_build_system_prompt默认走旧路径(self, monkeypatch):
        import agent.digital_life as dl
        monkeypatch.setattr(dl, "_get_template", lambda: TEMPLATE)

        builder = PromptBuilder()
        out = builder.build_system_prompt(
            body_status=BODY_STATUS, tool_status=TOOL_MARKER,
            skill_instructions=SKILL_MARKER, profile=_Profile(),
            memory_context=MEMORY_FALLBACK,
        )
        assert out == _legacy_system_prompt(), "fragments=None 必须逐字等于旧路径"

    def test_build_system_prompt片段填槽位且尾部追加(self, monkeypatch):
        import agent.digital_life as dl
        monkeypatch.setattr(dl, "_get_template", lambda: TEMPLATE)

        builder = PromptBuilder()
        out = builder.build_system_prompt(
            body_status=BODY_STATUS, tool_status="旧工具段",
            skill_instructions="旧技能段", profile=_Profile(),
            memory_context="旧记忆段",
            fragments=[
                PromptFragment("tool", "新工具段", source="tool"),
                PromptFragment("skill", "新技能段", source="skill"),
                PromptFragment("memory", "新记忆段", source="memory"),
                PromptFragment("line", "本线片段", source="line:eng"),
            ],
        )
        assert "新工具段" in out and "新技能段" in out and "新记忆段" in out
        assert "旧工具段" not in out and "旧技能段" not in out and "旧记忆段" not in out
        assert out.endswith("本线片段")
        # 槽位角色不得被注入两次（尾部只放无槽位角色）
        assert out.count("新工具段") == 1

    def test_build_system_prompt片段缺某角色时沿用形参(self, monkeypatch):
        import agent.digital_life as dl
        monkeypatch.setattr(dl, "_get_template", lambda: TEMPLATE)

        builder = PromptBuilder()
        out = builder.build_system_prompt(
            body_status=BODY_STATUS, tool_status="旧工具段",
            skill_instructions="旧技能段", profile=_Profile(),
            memory_context="旧记忆段",
            fragments=[PromptFragment("line", "本线片段", source="line:eng")],
        )
        assert "旧工具段" in out and "旧技能段" in out and "旧记忆段" in out
        assert out.endswith("本线片段")


class _Profile:
    label = "默认"
    description = "默认模式"

# ══════════════════════════════════════════════════════════════════════
#  ⑥ HTTP 面：/preview 与 /validate 带上"会注入的提示词片段"
#     （前端据此渲染，不再自己判一遍 —— 避免第二份口径）
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture()
def lines_client():
    """只注册主线路由的最小 app（与 tests/unit/test_line_skillpack.py 同款）"""
    flask = pytest.importorskip("flask")
    from agent.server_routes.routes_agent_lines import register_routes

    app = flask.Flask("prompt_roles_http_test")
    register_routes(app)
    return app.test_client()


class TestRestSurfacePromptFragments:
    def test_preview_回传role_line片段的内容与来源(self, lines_client):
        resp = lines_client.post("/api/agent-lines/preview", json={
            "id": "engineering", "prompt_note": "本线的交付标准是改完并验证。",
        })
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["prompt_fragments_note"] == ""
        frags = body["prompt_fragments"]
        assert len(frags) == 1
        f = frags[0]
        assert f["role"] == "line"
        assert f["source"] == "line:engineering"
        assert f["content"] == "本线的交付标准是改完并验证。"
        assert f["chars"] == len(f["content"])
        assert f["croppable"] is False, "role=line 是硬片段，前端不该显示成可裁"

    def test_validate_与_preview_同源(self, lines_client):
        payload = {"id": "engineering", "prompt_note": "同一段"}
        prev = lines_client.post("/api/agent-lines/preview", json=payload).get_json()
        val = lines_client.post("/api/agent-lines/validate", json=payload).get_json()
        assert prev["prompt_fragments"] == val["prompt_fragments"]
        assert prev["prompt_fragments_note"] == val["prompt_fragments_note"]

    def test_prompt_note为空_列表为空且给出原因(self, lines_client):
        for payload in ({"id": "t"}, {"id": "t", "prompt_note": ""},
                        {"id": "t", "prompt_note": "   \n "}):
            body = lines_client.post("/api/agent-lines/preview", json=payload).get_json()
            assert body["prompt_fragments"] == []
            assert "不注入" in body["prompt_fragments_note"]
            assert "逐字一致" in body["prompt_fragments_note"]

    def test_停用线_列表为空且说明按未装线处理(self, lines_client):
        body = lines_client.post("/api/agent-lines/validate", json={
            "id": "t", "enabled": False, "prompt_note": "不该注入",
        }).get_json()
        assert body["prompt_fragments"] == []
        assert "停用" in body["prompt_fragments_note"]

    def test_片段信息不可用时降级_预览照常返回(self, lines_client, monkeypatch):
        """判定实现炸了也不许把预览端点带崩：降级成空列表 + 原因"""
        import agent.orchestrator.prompt_builder as pb

        def _boom(profile, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(pb, "line_fragment_for_profile", _boom)
        resp = lines_client.post("/api/agent-lines/preview", json={
            "id": "t", "prompt_note": "x",
        })
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["prompt_fragments"] == []
        assert "降级" in body["prompt_fragments_note"]
        assert body["preview"]["count"] >= 0, "预览本体必须照常返回"

    def test_纯函数口径与运行时同一实现(self):
        """HTTP 投影用的判定函数必须就是运行时装配用的那个（不许各判一遍）"""
        import inspect

        from agent.orchestrator import prompt_builder as pb

        src = inspect.getsource(pb.line_prompt_fragment)
        assert "line_fragment_for_profile(" in src, (
            "运行时装配必须复用 line_fragment_for_profile —— 否则 HTTP 投影与"
            "系统提示词真实注入会是两份口径")

