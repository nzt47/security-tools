# -*- coding: utf-8 -*-
"""主题类（topic）词表 + 三库按类装载 —— C1 验收用例（2026-11-14）。

覆盖四条口径，每条都对应一个可能被写坏的边界：
1. 词表闭集与归一化：写侧未知值**报错**（不静默纠正）；读侧未知值**丢弃**
   （白名单只会因 typo 变窄，绝不因 typo 变宽 —— 变宽就是 fail-open）。
2. 未声明 ⇒ general（fail-closed）：取不到上下文时只读 general。
3. 三库读侧都按白名单过滤：向量 / 知识（含**双链一跳**）/ 经验（含闸门口径）。
4. 向量查询缓存必须按白名单分键，否则 A 的检索结果会被 B 命中。
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


# ════════════════════════════════════════════════════════════
#  1. 词表与归一化
# ════════════════════════════════════════════════════════════


def test_vocabulary_is_closed_and_has_general_default():
    assert T.DEFAULT_TOPIC == "general"
    assert T.DEFAULT_TOPIC in T.TOPICS
    assert T.TOPICS == ("general", "engineering", "knowledge", "ops", "product")


def test_write_side_unknown_topic_raises_instead_of_coercing():
    """写侧不能把 typo 静默改成 general —— 那等于替使用者表态"""
    with pytest.raises(T.TopicError):
        T.normalize_topic("enginearing", strict=True)


def test_empty_or_none_topic_is_general():
    assert T.normalize_topic(None) == "general"
    assert T.normalize_topic("") == "general"
    assert T.normalize_topic("   ") == "general"


def test_topic_is_case_and_space_insensitive():
    assert T.normalize_topic(" Engineering ") == "engineering"
    assert T.normalize_topic("OPS") == "ops"


def test_read_side_whitelist_drops_unknown_instead_of_widening():
    """白名单里的未知值必须**丢弃**：回落到 general 等于因 typo 多放行一类"""
    assert T.normalize_topics(["engineering", "bogus"]) == ("engineering",)
    assert T.normalize_topics(["bogus"]) == ("general",)
    assert T.normalize_topics([]) == ("general",)
    assert T.normalize_topics(None) == ("general",)


def test_whitelist_dedupes_and_keeps_order():
    assert T.normalize_topics(["ops", "engineering", "ops"]) == ("ops", "engineering")


# ════════════════════════════════════════════════════════════
#  2. 执行上下文：未声明 ⇒ general
# ════════════════════════════════════════════════════════════


def test_undeclared_context_reads_only_general():
    assert T.declared_topics() is None
    assert T.current_topics() == ("general",)


def test_topics_context_sets_and_restores():
    with T.topics_context(["engineering", "ops"]) as resolved:
        assert resolved == ("engineering", "ops")
        assert T.current_topics() == ("engineering", "ops")
        with T.topics_context(["knowledge"]):
            assert T.current_topics() == ("knowledge",)
        assert T.current_topics() == ("engineering", "ops")
    assert T.current_topics() == ("general",)


def test_filter_by_topic_handles_three_row_shapes():
    class _Item:
        def __init__(self, meta):
            self.metadata = meta

    rows = [_Item({"topic": "engineering"}), _Item({"topic": "ops"}), _Item({})]
    kept, dropped = T.filter_by_topic(rows, ("general",))
    assert dropped == 2 and len(kept) == 1
    # dict 形态（经验侧 meta 本身带 _topic）
    exp = [{"_topic": "engineering"}, {"_topic": "general"}]
    kept, dropped = T.filter_by_topic(exp, ("engineering",), getter=lambda r: r.get("_topic"))
    assert dropped == 1 and kept == [{"_topic": "engineering"}]


# ════════════════════════════════════════════════════════════
#  3a. 向量库
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def json_store(tmp_path, monkeypatch):
    """JSON 回退后端的 VectorStore（不加载模型、不依赖 sqlite-vec）"""
    import memory.vector_store.vector_store as vsmod

    # 【坑】`_check_chroma_available()` 在 __init__ 里会把模块级 HAS_CHROMA 重新置位，
    # 所以只 patch HAS_CHROMA 不够 —— 必须连探测函数一起废掉，否则后端会变 chromadb。
    monkeypatch.setattr(vsmod, "_check_chroma_available", lambda: False)
    monkeypatch.setattr(vsmod, "HAS_SENTENCE_TRANSFORMERS", False)
    monkeypatch.setattr(vsmod, "HAS_CHROMA", False)
    monkeypatch.setattr(vsmod, "_resolve_encoder_availability", lambda name: False)
    store = vsmod.VectorStore(collection_name="t_topics", persist_dir=str(tmp_path))
    assert store._backend == "json"
    return store


def test_vector_write_stamps_general_by_default(json_store):
    json_store.add("云枢知识卡片蒸馏流程")
    item = json_store.get_recent(limit=1)[0]
    assert item.metadata["topic"] == "general"


def test_vector_write_keeps_explicit_topic(json_store):
    json_store.add("部署脚本先停后端再构建", metadata={"topic": "Ops"})
    assert json_store.get_recent(limit=1)[0].metadata["topic"] == "ops"


def test_vector_write_rejects_unknown_topic(json_store):
    with pytest.raises(T.TopicError):
        json_store.add("内容", metadata={"topic": "devops"})


def test_vector_search_only_returns_allowed_topics(json_store):
    # 三条都含"迁移"这个共同词：这样"不过滤"时三路都能召回，才验证得了过滤本身
    json_store.add("迁移：sqlite vec 的 k 约束写法", metadata={"topic": "engineering"})
    json_store.add("迁移：调度器 deferred 与 pending 判读", metadata={"topic": "ops"})
    json_store.add("迁移：卡片 draft 转 current 的人工确认", metadata={"topic": "knowledge"})

    hits = json_store.search("迁移", top_k=5, topics=["ops"])
    assert [h.metadata["topic"] for h in hits] == ["ops"]

    # 白名单内没有该类数据 ⇒ 空（而不是退回全库）
    assert json_store.search("迁移", top_k=5, topics=["product"]) == []
    # 不传 topics = 逐字旧行为（全库三路都回）
    assert {h.metadata["topic"] for h in json_store.search("迁移", top_k=5)} == {
        "engineering", "ops", "knowledge"}


def test_vector_search_cache_does_not_leak_across_topics(json_store):
    """缓存键不含白名单 ⇒ A 的结果会被 B 命中（静默越权）"""
    json_store.add("缓存 必须按类分键", metadata={"topic": "engineering"})
    json_store.add("缓存 另一类的同一句话", metadata={"topic": "general"})
    q = "缓存"
    first = json_store.search(q, top_k=5, topics=["engineering"])
    second = json_store.search(q, top_k=5, topics=["general"])
    assert [h.metadata["topic"] for h in first] == ["engineering"]
    assert [h.metadata["topic"] for h in second] == ["general"]


# ════════════════════════════════════════════════════════════
#  3b. 知识库
# ════════════════════════════════════════════════════════════


def _make_card(store, slug, title, topic, links=(), body="正文", status="current",
               insight="一句话洞见"):
    """建卡（title 必须满足 slugify(title)==slug，否则 schema 会拦）"""
    from agent.knowledge.schema import Card

    card = Card(
        title=title, slug=slug, status=status, type="concepts",
        source="test", date="2026-11-14", insight=insight,
        topic=topic, links=list(links), content=body,
    )
    return store.create(card)


@pytest.fixture()
def card_store(tmp_path):
    from agent.knowledge.card import CardStore

    root = tmp_path / "wiki"
    for d in ("concepts", "insights", "entities"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return CardStore(root)


def test_card_topic_round_trips_through_frontmatter(card_store):
    _make_card(card_store, "工程卡", "工程卡", "engineering")
    again = card_store.get("工程卡")
    assert again is not None and again.topic == "engineering"
    # 走一次 update（会重写 frontmatter）后 field 仍在 —— 这是最容易丢字段的路径
    again.insight = "改过的洞见"
    card_store.update(again)
    assert card_store.get("工程卡").topic == "engineering"


def test_card_without_topic_defaults_to_general(card_store):
    from agent.knowledge.schema import validate_card

    _make_card(card_store, "旧卡", "旧卡", "general")
    assert card_store.get("旧卡").topic == "general"
    assert validate_card({"title": "t", "slug": "t", "status": "current",
                          "type": "concepts", "source": "s", "date": "d",
                          "insight": "i"}) == []


def test_card_rejects_unknown_topic_in_validation():
    from agent.knowledge.schema import validate_card

    errors = validate_card({"title": "t", "slug": "t", "status": "current",
                            "type": "concepts", "source": "s", "date": "d",
                            "insight": "i", "topic": "devops"})
    assert any("topic" in e for e in errors)


def test_knowledge_search_filters_by_topic(card_store):
    from agent.knowledge.search import KnowledgeSearch

    _make_card(card_store, "部署停后端", "部署停后端", "ops")
    _make_card(card_store, "构建产物", "构建产物", "engineering")

    searcher = KnowledgeSearch(card_store, topics=["ops"], min_score=0.0)
    hits = searcher.search("部署", top_k=5)
    assert [h.slug for h in hits] == ["部署停后端"]


def test_knowledge_link_expansion_cannot_cross_topic(card_store):
    """双链一跳是最容易被忽略的泄漏路径：白名单内的卡片链到白名单外的卡片"""
    from agent.knowledge.search import KnowledgeSearch

    # 外部卡的标题/正文都不含查询词 —— 它只能由**双链一跳**进来（才验得到泄漏）
    _make_card(card_store, "种子卡", "种子卡", "ops", links=["外部卡"],
               body="部署前必须先停后端再构建，否则 rolldown OOM")
    _make_card(card_store, "外部卡", "外部卡", "engineering", body="与本查询无关的细节")

    searcher = KnowledgeSearch(card_store, topics=["ops"], min_score=0.0)
    hits = searcher.search("部署", top_k=5)
    assert [h.slug for h in hits] == ["种子卡"], "链到白名单外的卡片不得被并入候选"


# ════════════════════════════════════════════════════════════
#  3c. 经验库
# ════════════════════════════════════════════════════════════


def _write_samples(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _sample(sid, task, topic, verified="pass", lang="python"):
    return {"id": sid, "task": task, "topic": topic, "verified": verified,
            "task_type": "bugfix", "stack": {"lang": lang, "files_changed": 2},
            "diffs": [{"path": "a.py"}], "pitfalls": ["p"], "created_at": "2026-11-01"}


@pytest.fixture()
def exp_index(tmp_path):
    from agent.skills_mgmt.experience_index import ExperienceIndex

    path = tmp_path / "samples.ndjson"
    _write_samples(path, [
        _sample("e1", "修复 sqlite-vec 的 k 约束写法导致 CI 红", "engineering"),
        _sample("e2", "修复部署脚本先停后端再构建的 OOM", "ops"),
        _sample("e3", "修复旧样本没有 topic 字段的迁移", "general"),
    ])
    idx = ExperienceIndex(str(path), use_vector=False)
    idx.load()
    idx.build()
    return idx


def test_experience_search_filters_by_topic(exp_index):
    hits = exp_index.search("修复 k 约束", top_k=5, min_bm25_score=0.0, topics=["engineering"])
    assert [h["id"] for h in hits] == ["e1"]
    assert exp_index.search("修复 k 约束", top_k=5, min_bm25_score=0.0, topics=["product"]) == []


def test_experience_gate_uses_allowed_top1_not_global_top1(exp_index):
    """设了白名单就按**白名单内**的 top1 判相关度。

    否则「全库最高分属于别的类」会给这条查询背书，把本类里分数很低的条目注入进去。
    """
    # 全库 top1 分很高（> 阈值）；engineering 类里也只有它 ⇒ 放行
    high = exp_index.search("修复 k 约束", top_k=5, min_bm25_score=1.0, topics=["engineering"])
    assert [h["id"] for h in high] == ["e1"]
    # 阈值抬到比 engineering 内 top1 还高 ⇒ 判「本类没命中」，返回空（不拿别的类背书）
    assert exp_index.search("修复 k 约束", top_k=5,
                            min_bm25_score=10_000.0, topics=["engineering"]) == []
    # 同一查询、不设白名单时用的仍是全库 top1（逐字旧行为）
    assert exp_index.search("修复 k 约束", top_k=5, min_bm25_score=1.0) != []
