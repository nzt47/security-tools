# -*- coding: utf-8 -*-
"""三库「按类装载」的**合并验收**：造混合数据，验 A 看不到 B 的类（后续③）。

前面两条用例文件是按库分开测的（test_topics_three_stores / test_topics_policy）；
这一份把验收①原样跑一遍：**同一次运行里**三个库都造出两类数据，
两个 profile（engineering 线 / knowledge 线）各自检索，互相看不到对方的类。

为什么还要单独一份：分开测只能证明"单个库过滤生效"；验收要的是
「A 检索不到 B 的类（向量 / 知识 / 经验三条都要）」—— 那就得在同一次装配置里
把三条库一起跑，才拦得住"某一条接了、另一条忘了"。
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

#: 两个 profile 的装载声明（对应两条主线的写法）
PROFILES = {
    "engineering": ["general", "engineering"],
    "knowledge": ["general", "knowledge"],
}


@pytest.fixture()
def mixed_vector(tmp_path, monkeypatch):
    import memory.vector_store.vector_store as vsmod

    monkeypatch.setattr(vsmod, "_check_chroma_available", lambda: False)
    monkeypatch.setattr(vsmod, "HAS_SENTENCE_TRANSFORMERS", False)
    monkeypatch.setattr(vsmod, "HAS_CHROMA", False)
    monkeypatch.setattr(vsmod, "_resolve_encoder_availability", lambda name: False)
    store = vsmod.VectorStore(collection_name="t_mixed", persist_dir=str(tmp_path))
    # 三类各两条，内容都含同一个词，保证"不过滤时三路都能召回"
    for i, topic in enumerate(["engineering", "engineering", "knowledge", "knowledge",
                               "ops", "ops"]):
        store.add(f"迁移 记录 {i}", metadata={"topic": topic})
    return store


@pytest.fixture()
def mixed_cards(tmp_path):
    from agent.knowledge.card import CardStore
    from agent.knowledge.schema import Card

    root = tmp_path / "wiki"
    for d in ("concepts", "insights", "entities"):
        (root / d).mkdir(parents=True, exist_ok=True)
    store = CardStore(root)
    for i, topic in enumerate(["engineering", "engineering", "knowledge", "knowledge",
                               "ops", "ops"]):
        store.create(Card(title=f"迁移口径{i}", slug=f"迁移口径{i}", status="current",
                          type="concepts", source="t", date="2026-11-14",
                          insight="迁移相关的一句话", topic=topic, content="迁移 正文"))
    return store


@pytest.fixture()
def mixed_experience(tmp_path, monkeypatch):
    """经验库：**替换 BM25 腿**为确定性桩，只验「按类过滤 + 闸门」这一层。

    为什么不用真 BM25：本用例要证的是"过滤生效"，而 BM25 是否命中取决于
    rank_bm25 是否安装、以及分词口径（那两条由 test_topics_three_stores /
    test_experience_index 各自守住）。在这里塞真 BM25 会让用例随环境红灯，
    却与"按类装载"无关。
    """
    from agent.skills_mgmt.experience_index import ExperienceIndex

    path = tmp_path / "samples.ndjson"
    tasks = {
        "engineering": "修复 sqlite-vec 的 k 约束写法导致 CI 红",
        "knowledge": "修复 sqlite-vec 的 k 约束写法要写进知识卡片",
        "ops": "修复 sqlite-vec 的 k 约束写法上线后触发告警",
    }
    rows = []
    for i, (topic, task) in enumerate(tasks.items()):
        rows.append({"id": f"e{i}", "task": task,
                     "topic": topic, "verified": "pass", "task_type": "bugfix",
                     "stack": {"lang": "python", "files_changed": 1},
                     "diffs": [{"path": "a.py"}], "pitfalls": ["p"],
                     "created_at": "2026-11-01"})
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    idx = ExperienceIndex(str(path), use_vector=False)
    idx.load()

    class _StubBM25:
        """恒返回三条（得分递减）—— 与 query 无关，过滤才是被测对象"""

        def search(self, query, top_k=5):
            from agent.skills_mgmt.bm25_searcher import BM25Match

            return [BM25Match(skill_id="e0", score=30.0),
                    BM25Match(skill_id="e1", score=25.0),
                    BM25Match(skill_id="e2", score=20.0)][:top_k]

    idx._bm25 = _StubBM25()
    return idx


def _vector_topics(store, whitelist):
    items, applied = P.search_with_topics(store, "迁移", top_k=50,
                                          topics=whitelist)
    assert applied == T.normalize_topics(whitelist)
    return {i.metadata["topic"] for i in items}


def _knowledge_topics(store, whitelist):
    from agent.knowledge.search import KnowledgeSearch

    hits = KnowledgeSearch(store, topics=whitelist, min_score=0.0).search("迁移", top_k=50)
    slugs = {h.slug for h in hits}
    return {c.topic for c in store.list() if c.slug in slugs}


def _experience_topics(idx, whitelist):
    hits = idx.search("修复 k 约束", top_k=50, min_bm25_score=0.0, topics=whitelist)
    return {h["meta"].get("_topic") for h in hits}


def test_engineering_profile_sees_only_its_classes(mixed_vector, mixed_cards, mixed_experience):
    wl = PROFILES["engineering"]
    # 三个库都只造了 engineering/knowledge/ops 三类数据（没有 general 行），
    # 所以"engineering 能看到什么"应当**恰好**是 engineering 一类。
    assert _vector_topics(mixed_vector, wl) == {"engineering"}
    assert _knowledge_topics(mixed_cards, wl) == {"engineering"}
    assert _experience_topics(mixed_experience, wl) == {"engineering"}


def test_knowledge_profile_cannot_see_engineering(mixed_vector, mixed_cards, mixed_experience):
    """验收①：A 检索不到 B 的类 —— 三条库**同时**断言"""
    wl = PROFILES["knowledge"]
    assert "engineering" not in _vector_topics(mixed_vector, wl)
    assert "engineering" not in _knowledge_topics(mixed_cards, wl)
    assert "engineering" not in _experience_topics(mixed_experience, wl)


def test_profiles_never_see_ops(mixed_vector, mixed_cards, mixed_experience):
    """两类都没声明 ops ⇒ 三条库都读不到 ops（不是"没数据"，是"没权限"）"""
    for wl in PROFILES.values():
        assert "ops" not in _vector_topics(mixed_vector, wl)
        assert "ops" not in _knowledge_topics(mixed_cards, wl)
        assert "ops" not in _experience_topics(mixed_experience, wl)


def test_undeclared_profile_sees_only_general(tmp_path, mixed_experience):
    """验收②：未声明 ⇒ 只读 general（这里故意让 general 无数据 ⇒ 三条库都空）"""
    from agent.knowledge.card import CardStore
    from agent.knowledge.search import KnowledgeSearch

    root = tmp_path / "w2"
    for d in ("concepts", "insights", "entities"):
        (root / d).mkdir(parents=True, exist_ok=True)
    store = CardStore(root)
    from agent.knowledge.schema import Card

    store.create(Card(title="工程卡", slug="工程卡", status="current", type="concepts",
                      source="t", date="2026-11-14", insight="迁移", topic="engineering"))
    assert KnowledgeSearch(store, min_score=0.0).search("迁移") != []
    assert KnowledgeSearch(store, topics=[], min_score=0.0).search("迁移") == []
    assert _experience_topics(mixed_experience, []) == set()
