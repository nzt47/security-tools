"""第二条注入通道的技能白名单闸门（按意图命中注入）

【为什么有这条用例】
    技能有**两条**进提示词的路：
      ① 系统提示词的技能段 —— \`DigitalLifePersonaMixin._build_skill_instructions\`（已按 \`skills:\` 过滤）
      ② **按意图命中注入** —— ContextAssembler 旁路 → \`Orchestrator._context_assembler_procedural\`
         （\`learning.context_assembler.enabled: true\`，**在生产链路上**）
    只堵①会让②成为绕开本线 \`skills:\` 的旁路，那么"本线 skills 是技能面的权威"就是假的。

【钉住的不变量】
    1. 未装线 / 本线未声明技能 ⇒ **不限制**（原样保留）—— 与全仓"未装线 = 没改过"同纪律；
    2. 白名单 ⇒ 只保留声明过的 id（**保序**），其余如实回 \`dropped\`（可记账，不静默丢）；
    3. 任何异常 / 条目形状不认识 ⇒ **原样保留**（技能侧 fail-open，宁可少拦不可静默剥夺）；
    4. 编排器真的接了闸门：装线后，未被本线声明的技能**不进**旁路注入文本。
"""
from __future__ import annotations

import os

import pytest

import agent.lines.integration as _I
from agent.lines import filter_skill_entries
from agent.lines.models import LineProfile
from agent.lines.registry import get_line_registry


def _entry(sid: str) -> dict:
    return {"skill_id": sid, "name": sid, "instruction": f"{sid} 的做法"}


def _write_line(tmp_path, line_id: str, skills: list) -> None:
    prof = LineProfile(id=line_id, name=line_id, skills=list(skills))
    with open(os.path.join(str(tmp_path), f"{line_id}.yaml"), "w", encoding="utf-8") as f:
        f.write(prof.to_yaml())


def _set_active(tmp_path, line_id: str | None) -> None:
    import json

    with open(os.path.join(str(tmp_path), "_active.json"), "w", encoding="utf-8") as f:
        json.dump({"active": line_id}, f)


@pytest.fixture
def tmp_lines(tmp_path, monkeypatch):
    """主线档案目录指到临时目录（真实档案读写，不 mock 注册表）"""
    monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", str(tmp_path))
    return tmp_path


# ════════════════════════════════════════════════════════════
#  1. 纯函数：filter_skill_entries
# ════════════════════════════════════════════════════════════

class TestFilterSkillEntries:

    def test_未装线时原样保留(self, tmp_lines):
        """未装线 = 没改过：一条都不许动（技能侧同样如此）"""
        _set_active(tmp_lines, None)
        items = [_entry("a"), _entry("b")]
        kept, dropped = filter_skill_entries(items)
        assert [e["skill_id"] for e in kept] == ["a", "b"]
        assert dropped == []

    def test_白名单只保留本线声明的技能且保序(self, tmp_lines):
        """用**真实存在**的技能 id：declared ∩ known 才是 allowed（skillpack 的判定）"""
        _write_line(tmp_lines, "eng", ["self_reflection"])
        _set_active(tmp_lines, "eng")
        items = [_entry("memory_summary"), _entry("self_reflection"), _entry("voice_interaction")]
        kept, dropped = filter_skill_entries(items)
        assert [e["skill_id"] for e in kept] == ["self_reflection"], "保序 + 只留本线声明的"
        assert [e["skill_id"] for e in dropped] == ["memory_summary", "voice_interaction"], \
            "被剔除项必须如实回报（可记账）"
        assert len(kept) + len(dropped) == len(items), "保留+剔除恒等于入参（不静默丢条目）"

    def test_声明了不存在的技能_id_等于没声明(self, tmp_lines):
        """写错的 id 进 unknown、不进 allowed ⇒ 不能靠它放行（fail-closed 的逐条版）"""
        _write_line(tmp_lines, "eng", ["并不存在的技能"])
        _set_active(tmp_lines, "eng")
        kept, dropped = filter_skill_entries([_entry("self_reflection")])
        assert kept == [] and [e["skill_id"] for e in dropped] == ["self_reflection"]

    def test_空声明等于不限制(self, tmp_lines):
        """空声明 = 本线没表态 ⇒ 不限制（不是"一个都不给"）"""
        _write_line(tmp_lines, "eng", [])
        _set_active(tmp_lines, "eng")
        items = [_entry("a"), _entry("b")]
        kept, dropped = filter_skill_entries(items)
        assert [e["skill_id"] for e in kept] == ["a", "b"] and dropped == []

    def test_停用线按未装线处理(self, tmp_lines):
        prof = LineProfile(id="eng", name="eng", skills=["a"], enabled=False)
        with open(os.path.join(str(tmp_lines), "eng.yaml"), "w", encoding="utf-8") as f:
            f.write(prof.to_yaml())
        _set_active(tmp_lines, "eng")
        kept, dropped = filter_skill_entries([_entry("a"), _entry("z")])
        assert len(kept) == 2 and dropped == [], "停用 = 本轮不装线 ⇒ 不限制"

    def test_条目形状不认识时原样保留(self, tmp_lines):
        _write_line(tmp_lines, "eng", ["a"])
        _set_active(tmp_lines, "eng")
        items = [_entry("a"), "不是 Mapping"]
        kept, dropped = filter_skill_entries(items)  # type: ignore[list-item]
        assert kept == items and dropped == [], "形状不认识 ⇒ 不猜、不静默丢"

    def test_技能包解析异常时原样保留(self, tmp_lines, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("档案目录炸了")

        monkeypatch.setattr(_I, "line_skill_pack", _boom)
        items = [_entry("a"), _entry("b")]
        kept, dropped = filter_skill_entries(items)
        assert kept == items and dropped == [], "过滤故障 ⇒ 回退不限制（fail-open）"

    def test_空入参返回两个空表(self):
        assert filter_skill_entries([]) == ([], [])
        assert filter_skill_entries(None) == ([], [])  # type: ignore[arg-type]

    def test_自定义_key(self, tmp_lines):
        _write_line(tmp_lines, "eng", ["self_reflection"])
        _set_active(tmp_lines, "eng")
        items = [{"id": "self_reflection"}, {"id": "voice_interaction"}]
        kept, dropped = filter_skill_entries(items, key="id")
        assert [e["id"] for e in kept] == ["self_reflection"]
        assert [e["id"] for e in dropped] == ["voice_interaction"]


# ════════════════════════════════════════════════════════════
#  2. 编排器接线：旁路注入真的过闸门
# ════════════════════════════════════════════════════════════

class _StubMatch:
    def __init__(self, sid: str):
        self.skill_id = sid
        self.name = sid
        self.description = f"{sid} 描述"


class _StubResult:
    def __init__(self, ids):
        self.matches = [_StubMatch(i) for i in ids]


class _StubLoader:
    """只实现编排器用到的那两个方法（match / load_instruction）"""

    def __init__(self, ids):
        self._ids = list(ids)

    def match(self, task, top_k=2):
        return _StubResult(self._ids[:top_k])

    def load_instruction(self, sid):
        return {"instruction": f"{sid} 的做法"}


def _make_orchestrator(ids):
    from unittest import mock

    from agent.orchestrator.orchestrator import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o._ctx_skills_loader = _StubLoader(ids)
    o._load_context_assembler_config = mock.Mock(
        return_value={"enabled": True, "token_budget": 3000})
    o._context_assembler_long_term = lambda task: []
    return o


class TestOrchestratorGate:

    def test_未装线时旁路注入不受影响(self, tmp_lines):
        """接线前行为：未装线 ⇒ 命中即注入（本用例锁住"没装线就等于没改过"）"""
        _set_active(tmp_lines, None)
        o = _make_orchestrator(["self_reflection", "memory_summary"])
        text = o._context_assembler_extra("帮我解析 PDF")
        assert text is not None
        assert "self_reflection 的做法" in text and "memory_summary 的做法" in text

    def test_装线后未声明的技能不进旁路文本(self, tmp_lines):
        """身份减法：本线 skills: 没写的技能，即使被意图命中也不注入"""
        _write_line(tmp_lines, "eng", ["self_reflection"])
        _set_active(tmp_lines, "eng")
        o = _make_orchestrator(["memory_summary", "self_reflection"])
        text = o._context_assembler_extra("帮我解析 PDF")
        assert text is not None
        assert "self_reflection 的做法" in text, "本线声明的技能必须保留"
        assert "memory_summary 的做法" not in text, "未声明的技能不得经旁路注入（第二条通道已堵）"

    def test_装线后命中技能全未声明则技能层为空(self, tmp_lines):
        _write_line(tmp_lines, "eng", ["voice_interaction"])
        _set_active(tmp_lines, "eng")
        o = _make_orchestrator(["memory_summary", "self_reflection"])
        text = o._context_assembler_extra("帮我解析 PDF")
        # 三层全空时既有行为是"跳过注入、返回 None"——那也是"没注入"，一并接受
        assert "的做法" not in (text or ""), "白名单外的技能一律不注入"

    def test_过滤故障时旁路照常注入(self, tmp_lines, monkeypatch):
        """闸门自己坏了，不许把注入链路一起带下去（且回退方向是"不限制"）"""
        _write_line(tmp_lines, "eng", ["self_reflection"])
        _set_active(tmp_lines, "eng")

        def _boom(*a, **kw):
            raise RuntimeError("闸门炸了")

        monkeypatch.setattr(_I, "line_skill_pack", _boom)
        o = _make_orchestrator(["memory_summary", "self_reflection"])
        text = o._context_assembler_extra("帮我解析 PDF")
        assert text is not None
        assert "memory_summary 的做法" in text and "self_reflection 的做法" in text
