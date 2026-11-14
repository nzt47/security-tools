# -*- coding: utf-8 -*-
"""按类装载的**读侧策略**（C2）用例：主线声明 / manifest 声明 / 三库读路径 / 审计。

四条验收（Owner 2026-11-14）在这份文件里逐条落成断言：
1. 两个智能体配置不同类 ⇒ A 检索不到 B 的类（向量 / 知识 / 经验三条）；
2. 未声明类的旧记录归 general，默认不被越权读；
3. 审计记录"谁装载了哪些类"（声明是**配置事件**，不是每次检索）；
4. 现有 tenancy / scoped 语义不回归（由既有用例守，本文件只确保新维度不越界）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import topics as T  # noqa: E402
from agent.memory import topic_policy as P  # noqa: E402


# ════════════════════════════════════════════════════════════
#  1. 三条来源的解析优先级
# ════════════════════════════════════════════════════════════


class _Line:
    def __init__(self, line_id="engineering", topics=None):
        self.id = line_id
        self.topics = topics


def test_line_without_topics_reads_only_general():
    assert P.topics_for_line(_Line(topics=None)) == ("general",)
    assert P.topics_for_line(None) == ("general",)


def test_line_topics_are_normalized_and_deduped():
    assert P.topics_for_line(_Line(topics=["Engineering", "ops", "ops"])) == (
        "engineering", "ops")


def test_manifest_topics_come_from_memory_scope():
    cfg = {"memory_mode": "brokered", "memory_scope": {"topics": ["knowledge"]}}
    assert P.topics_for_manifest(cfg) == ("knowledge",)


def test_manifest_accepts_comma_string():
    cfg = {"memory_mode": "brokered", "memory_scope": {"topics": "ops,engineering"}}
    assert P.topics_for_manifest(cfg) == ("ops", "engineering")


def test_manifest_without_topics_reads_only_general():
    assert P.topics_for_manifest({"memory_mode": "brokered"}) == ("general",)
    assert P.topics_for_manifest(None) == ("general",)


def test_manifest_with_unknown_key_still_fails_closed():
    """写错键名不能让白名单静默变成"全读"（brokered 会降级、这里同样只读 general）"""
    cfg = {"memory_mode": "brokered", "memory_scope": {"topics_typo": ["ops"]}}
    assert P.topics_for_manifest(cfg) == ("general",)


def test_effective_topics_prefers_declared_context(monkeypatch):
    monkeypatch.setattr(P, "_active_line_topics", lambda: ("general", "engineering"))
    assert P.effective_topics() == ("general", "engineering")  # 无声明 ⇒ 主线
    with T.topics_context(["ops"]):
        assert P.effective_topics() == ("ops",)  # 声明优先


def test_effective_topics_fails_closed_when_line_unreadable(monkeypatch):
    def boom():
        raise RuntimeError("yaml 损坏")

    monkeypatch.setattr(P, "_active_line_topics", boom)
    assert P.effective_topics() == ("general",)


# ════════════════════════════════════════════════════════════
#  2. 主线档案：字段要能存活 UI 往返（否则一保存就把声明抹掉）
# ════════════════════════════════════════════════════════════


def test_line_profile_default_is_general_only():
    from agent.lines.models import LineProfile

    p = LineProfile(id="t")
    assert p.topics == ["general"]


def test_line_profile_round_trip_keeps_topics():
    from agent.lines.models import LineProfile

    p = LineProfile.from_dict({"id": "t", "topics": ["general", "engineering"]})
    assert p.to_dict()["topics"] == ["general", "engineering"]
    # to_yaml 也走 to_dict —— 少了这条，主线编辑器保存一次就抹掉声明
    assert "topics:" in p.to_yaml()


def test_line_profile_accepts_comma_string_and_empty():
    from agent.lines.models import LineProfile

    assert LineProfile.from_dict({"id": "t", "topics": "ops,engineering"}).topics == [
        "ops", "engineering"]
    # 空 ⇒ 兜底 general（不是"全读"）
    assert LineProfile.from_dict({"id": "t", "topics": []}).topics == ["general"]


def test_line_profile_validation_rejects_typos():
    from agent.lines.models import LineProfile

    issues = LineProfile.from_dict({"id": "t", "topics": ["devops"]}).validate()
    assert any("未知主题类" in i for i in issues)
    issues = LineProfile.from_dict(
        {"id": "t", "topics": ["ops", "OPS"]}).validate()
    assert any("重复" in i for i in issues)


def test_line_registry_save_load_keeps_topics(tmp_path, monkeypatch):
    from agent.lines.registry import LineRegistry

    monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", str(tmp_path))
    reg = LineRegistry()
    assert reg.load("test-line") is None
    from agent.lines.models import LineProfile

    reg.save(LineProfile.from_dict(
        {"id": "test-line", "topics": ["general", "knowledge"]}))
    assert reg.load("test-line").topics == ["general", "knowledge"]


# ════════════════════════════════════════════════════════════
#  3. 委派上下文 + 审计
# ════════════════════════════════════════════════════════════


def test_enter_manifest_sets_context_and_restores():
    cfg = {"memory_mode": "brokered", "memory_scope": {"topics": ["ops"]}}
    with P.enter_manifest(cfg, actor="sub") as active:
        assert active == ("ops",)
        assert T.current_topics() == ("ops",)
    assert T.current_topics() == ("general",)


def test_audit_only_when_non_default(monkeypatch):
    seen = []
    monkeypatch.setattr(P, "audit_declared",
                        lambda topics, **kw: seen.append((topics, kw.get("source"))) or True)
    with P.enter_manifest({"memory_mode": "brokered"}):
        pass
    assert seen == [], "默认档每次委派都写审计 = 把审计链变成噪声源"
    with P.enter_manifest({"memory_mode": "brokered",
                           "memory_scope": {"topics": ["ops"]}}):
        pass
    assert seen == [(("ops",), "manifest")]


def test_audit_declared_never_carries_content():
    """审计只带标识：topic 列表 + 来源，绝无记忆正文"""
    captured = {}

    class _Rec:
        def record(self, action, **kw):
            captured["action"] = action
            captured.update(kw)

    import agent.audit.facade as facade

    orig = facade.record
    facade.record = _Rec().record
    try:
        assert P.audit_declared(("ops",), actor="a", subject="s", source="line.save",
                                extra={"line": "recon", "content": "不该出现"}) is True
    finally:
        facade.record = orig
    assert captured["payload"] == {"topics": ["ops"], "source": "line.save",
                                   "line": "recon"}


# ════════════════════════════════════════════════════════════
#  4. 三条库的读路径（验收①②）
# ════════════════════════════════════════════════════════════


def _kb_with_two_cards(tmp_path):
    from agent.knowledge.card import CardStore
    from agent.knowledge.schema import Card

    kb = tmp_path / "kb"
    (kb / "wiki").mkdir(parents=True)
    for d in ("concepts", "insights", "entities"):
        (kb / "wiki" / d).mkdir(parents=True, exist_ok=True)
    store = CardStore(kb / "wiki")
    store.create(Card(title="部署口径", slug="部署口径", status="current",
                      type="concepts", source="t", date="2026-11-14",
                      insight="部署前先停后端", topic="ops", content="部署先停后端"))
    store.create(Card(title="缓存口径", slug="缓存口径", status="current",
                      type="concepts", source="t", date="2026-11-14",
                      insight="前缀缓存要稳定节前置", topic="engineering",
                      content="前缀缓存"))
    return kb


def test_kb_search_tool_respects_manifest_topics(tmp_path, monkeypatch):
    """分身 A（只读 engineering）用 kb_search 检索不到 ops 类卡片"""
    from agent.knowledge import tools as kb_tools
    from agent.knowledge.workflow import WorkflowRunner

    kb = _kb_with_two_cards(tmp_path)
    monkeypatch.setattr(kb_tools, "_runner", WorkflowRunner(knowledge_root=str(kb)))
    # 误召回保护的默认阈值 0.3 对两个汉字的短查询会判"未命中"（既有口径），
    # 本用例要验的是**按类过滤**，把阈值下调到 0 只看过滤。
    monkeypatch.setenv("KNOWLEDGE_MIN_SCORE", "0")
    # 主智能体（主线声明了三类）能同时看到两张卡 —— 作为对照基线
    monkeypatch.setattr(P, "_active_line_topics",
                        lambda: ("general", "ops", "engineering"))

    base = kb_tools.kb_search(query="口径")
    assert {h["slug"] for h in base["hits"]} == {"部署口径", "缓存口径"}

    # 分身 A：只装载 engineering ⇒ 看不到 ops 类那张
    cfg = {"memory_mode": "brokered", "memory_scope": {"topics": ["engineering"]}}
    with P.enter_manifest(cfg):
        assert {h["slug"] for h in kb_tools.kb_search(query="口径")["hits"]} == {"缓存口径"}

    # 分身 B：只装载 ops ⇒ 看不到 engineering 类那张（双向都验）
    cfg = {"memory_mode": "brokered", "memory_scope": {"topics": ["ops"]}}
    with P.enter_manifest(cfg):
        assert {h["slug"] for h in kb_tools.kb_search(query="口径")["hits"]} == {"部署口径"}

    # 未声明类的分身（memory_mode=none）⇒ 只读 general ⇒ 两张都读不到（fail-closed）
    with P.enter_manifest({"memory_mode": "none"}):
        assert kb_tools.kb_search(query="口径")["hits"] == []


def test_vector_search_helper_uses_policy(tmp_path, monkeypatch):
    import memory.vector_store.vector_store as vsmod

    monkeypatch.setattr(vsmod, "_check_chroma_available", lambda: False)
    monkeypatch.setattr(vsmod, "HAS_SENTENCE_TRANSFORMERS", False)
    monkeypatch.setattr(vsmod, "HAS_CHROMA", False)
    monkeypatch.setattr(vsmod, "_resolve_encoder_availability", lambda name: False)
    store = vsmod.VectorStore(collection_name="t_policy", persist_dir=str(tmp_path))
    store.add("迁移：部署先停后端", metadata={"topic": "ops"})
    store.add("迁移：前缀缓存", metadata={"topic": "engineering"})

    monkeypatch.setattr(P, "_active_line_topics", lambda: ("general", "engineering"))
    items, topics = P.search_with_topics(store, "迁移", top_k=5)
    assert topics == ("general", "engineering")
    assert {i.metadata["topic"] for i in items} == {"engineering"}

    with T.topics_context(["ops"]):
        items, topics = P.search_with_topics(store, "迁移", top_k=5)
    assert {i.metadata["topic"] for i in items} == {"ops"}


def test_search_helper_tolerates_legacy_stub_without_topics_param():
    """老式鸭子类型桩没有 topics 形参：如实降级（不抛 TypeError），并留告警"""
    class _Legacy:
        def __init__(self):
            self.calls = []

        def search(self, query, top_k):
            self.calls.append((query, top_k))
            return ["legacy"]

    stub = _Legacy()
    assert P.supports_topics(stub) is False
    monkeypatch_topics = pytest.MonkeyPatch()
    monkeypatch_topics.setattr(P, "effective_topics", lambda: ("general",))
    try:
        items, topics = P.search_with_topics(stub, "q", top_k=1)
    finally:
        monkeypatch_topics.undo()
    assert items == ["legacy"] and topics == ("general",)


# ════════════════════════════════════════════════════════════
#  5. 哨兵：五条读路径都必须走策略（漏一条就是越权）
# ════════════════════════════════════════════════════════════


#: **智能体自主检索**的五条路（人机 HTTP 面不在内：见 plugins/memory.py 与
#: routes_knowledge.py 里"为什么不在这里过滤"的注释）。漏接一条＝那条全库可读。
READ_PATHS = {
    "agent/system_tools.py": ("expand_context_from_memory", "search_with_topics"),
    "agent/digital_life_state.py": ("search_memory", "search_with_topics"),
    "agent/knowledge/tools.py": ("kb_search", "effective_topics"),
    "agent/orchestrator/orchestrator.py": ("_pull_experience", "effective_topics"),
    "agent/subagent/executor.py": ("_topics_scope", "enter_manifest"),
}


@pytest.mark.parametrize("rel,needle", [(k, v) for k, v in READ_PATHS.items()])
def test_every_read_path_goes_through_topic_policy(rel, needle):
    fn_marker, want = needle
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert fn_marker in text, f"{rel} 里找不到 {fn_marker}（改了名字？同步改哨兵）"
    assert want in text, (
        f"{rel} 的 {fn_marker} 不再引用 {want} —— "
        "新增/重构读路径必须接上按类过滤，否则那一条就是全库可读（越权）")
