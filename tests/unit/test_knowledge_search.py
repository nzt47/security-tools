"""知识检索整合（任务4）单元测试。

覆盖（任务规格 Step 6）:
  - rrf_fuse RRF 融合正确性（两路排名，融合分与顺序断言）
  - reranker 不可用降级（None / 异常 / raw==0.0 → RRF 原序，不抛异常）
  - reranker 正常打分（sigmoid 概率 + 阈值过滤）
  - 双链扩展提升召回（概念关联型查询命中 links 卡片）
  - 双链扩展边界（一跳不递归 / 互引重复跳过不双计 / 断链与归档目标跳过）
  - 复杂双链关系图（RRF 多路累加居首 / 仅双链平等参与排名 / 种子向量命中不双计 /
    扩展卡 links 不递归 / 反向链接不扩展）
  - 误召回保护（低置信 top1 → 空结果）
  - 无关词查询不受双链扩展影响
  - format_context 输出含 [来源: ...] 标记
  - 空库返回空列表不抛异常
  - 敏感命中处理（[敏感] 标记 / 隐藏 snippet）

设计原则: AAA，真实 CardStore(tmp_path) 存卡片，vector_store / reranker 用
轻量 fake（鸭子类型注入，符合 KnowledgeSearch 组合接线契约）。
"""

import asyncio
import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent.knowledge import (
    BM25Index,
    Card,
    CardStore,
    KnowledgeHit,
    KnowledgeSearch,
    format_context,
    rrf_fuse,
)


# ═══════════════════════════════════════════════════════════
#  fakes（鸭子类型，与 ToolReranker / VectorStore 接口对齐）
# ═══════════════════════════════════════════════════════════


class _FakeItem:
    """向量条目：metadata.slug + 可选 metadata._score（ChromaDB 路径无分数）。"""

    def __init__(self, slug, score=None):
        self.metadata = {"slug": slug}
        if score is not None:
            self.metadata["_score"] = score
        self.id = slug


class _FakeVectorStore:
    def __init__(self, results=None, exc=None):
        self._results = results or []
        self._exc = exc
        self.calls = []

    def search(self, query, top_k=5):
        self.calls.append((query, top_k))
        if self._exc is not None:
            raise self._exc
        return self._results[:top_k]


class _KeywordVectorStore:
    """按 content 关键词命中的伪向量路（与 dev/verify_knowledge_search.py --complex 对齐）。"""

    def __init__(self, cards, keywords):
        self._cards = cards
        self._keywords = keywords

    def search(self, query, top_k=10):
        return [
            _FakeItem(c.slug)
            for c in self._cards
            if any(k in c.content for k in self._keywords)
        ][:top_k]


class _FakeReranker:
    def __init__(self, results=None, exc=None):
        self._results = results  # [(slug, fused, raw)]
        self._exc = exc
        self.calls = []

    def rerank(self, query, candidates, tool_descriptions=None, top_k=None):
        self.calls.append(query)
        if self._exc is not None:
            raise self._exc
        return self._results


# ═══════════════════════════════════════════════════════════
#  fixtures
# ═══════════════════════════════════════════════════════════


def _card(
    title,
    slug=None,
    type="concepts",
    status="current",
    content="",
    insight="",
    links=None,
    metadata=None,
):
    """构造合法卡片（slug 与 slugify(title) 一致，通过 schema 校验）。"""
    return Card(
        title=title,
        slug=slug or title,
        status=status,
        type=type,
        source="test",
        date="2026-08-07",
        content=content,
        insight=insight or f"{title} 的核心洞见",
        links=links or [],
        metadata=metadata or {},
    )


@pytest.fixture
def store(tmp_path):
    """临时目录 CardStore（wiki/archives/index/log 相互隔离）。"""
    return CardStore(
        tmp_path / "wiki",
        archives_dir=tmp_path / "archives",
        index_path=tmp_path / "index.md",
        log_path=tmp_path / "log.md",
    )


@pytest.fixture
def seeded_store(store):
    """两张关联卡片：A 含检索词，B 不含检索词但被 A 双链引用。"""
    store.create(_card("驾驭工程", content="通过设计人机协作边界实现双链管理"))
    store.create(_card("提示词工程", content="结构化提示词模板", links=["驾驭工程"]))
    return store


@pytest.fixture
def complex_store(store):
    """复杂双链关系卡（与 dev/verify_knowledge_search.py --complex 数据集对齐）：

    链式(模型训练→特征工程→模型训练)、环形(强化学习→模型评估)、
    互引(迁移学习→特征工程)、断链(幽灵节点)、反向链接(生成对抗→模型训练)、
    扩展卡 links 指向新卡(模型评估→调参技巧)。
    """
    store.create(_card("模型训练", content="机器学习模型训练与调参的完整流程",
                       links=["特征工程", "模型评估", "幽灵节点"]))
    store.create(_card("迁移学习", content="迁移学习减少标注依赖的工程实践", links=["特征工程"]))
    store.create(_card("强化学习", content="强化学习探索与利用的平衡", links=["模型评估"]))
    store.create(_card("深度学习", content="深度学习反向传播算法原理"))
    store.create(_card("在线学习", content="在线学习的流式样本更新", links=["深度学习"]))
    store.create(_card("特征工程", content="数据清洗与特征构建方法", links=["模型训练"]))
    store.create(_card("模型评估", content="评估指标与交叉验证设计", links=["调参技巧"]))
    store.create(_card("调参技巧", content="超参数网格搜索与早停策略"))
    store.create(_card("生成对抗", content="GAN 生成对抗网络的训练技巧", links=["模型训练"]))
    store.create(_card("无关卡", content="烘焙温度与发酵时间控制"))
    return store


@pytest.fixture
def complex_searcher(complex_store):
    """复杂场景检索器：fake 向量路命中 模型训练(调参) + 特征工程(数据/特征)。

    query=「机器学习」时三路预期：
        BM25=[模型训练, 迁移/强化/深度/在线学习] 向量=[模型训练, 特征工程] 双链=[模型评估]
        → 模型训练 2/61(两路累加) → 0.667；模型评估 1/61(仅双链) → 0.333；
          特征工程 1/62(仅向量，种子故双链跳过) → 0.328。
    """
    vs = _KeywordVectorStore(complex_store.list(), ("调参", "数据", "特征"))
    return KnowledgeSearch(complex_store, vector_store=vs, min_score=0.3)


def _search(store, query, **kwargs):
    """构造默认 KnowledgeSearch 并搜索（显式传阈值，隔离环境变量影响）。"""
    searcher = KnowledgeSearch(
        store,
        min_score=kwargs.pop("min_score", 0.3),
        rerank_min_score=kwargs.pop("rerank_min_score", 0.001),
    )
    return searcher, searcher.search(query, **kwargs)


# ═══════════════════════════════════════════════════════════
#  RRF 融合正确性
# ═══════════════════════════════════════════════════════════


class TestRRFFuse:
    """rrf_fuse 融合正确性：只认排名，多路命中累加，顺序稳定。"""

    def test_two_lists_order_and_scores(self):
        fused = rrf_fuse([["a", "b"], ["b", "c"]], k=60)
        # b 两路命中：1/61 + 1/62；a 单路 1/61；c 单路 1/62
        assert list(fused.keys()) == ["b", "a", "c"]
        assert fused["b"] == pytest.approx(1 / 61 + 1 / 62)
        assert fused["a"] == pytest.approx(1 / 61)
        assert fused["c"] == pytest.approx(1 / 62)
        assert fused["b"] > fused["a"] > fused["c"]

    def test_three_lists_accumulate(self):
        fused = rrf_fuse([["a"], ["a"], ["a"]], k=60)
        assert fused["a"] == pytest.approx(3 / 61)

    def test_custom_k(self):
        fused = rrf_fuse([["x"]], k=10)
        assert fused["x"] == pytest.approx(1 / 11)

    def test_empty_lists(self):
        assert rrf_fuse([[], []]) == {}

    def test_tie_keeps_first_appearance(self):
        # 两路完全相同排名：平分时保持先出现顺序稳定（可复现）
        fused = rrf_fuse([["a", "b"], ["a", "b"]], k=60)
        assert list(fused.keys()) == ["a", "b"]

    def test_rank_irrelevant_to_magnitude(self):
        # 不同路分数量纲不同（BM25 高分 / 向量低分），RRF 只认排名
        fused = rrf_fuse([["a"], ["b", "a"]], k=60)
        # a: 1/61+1/62 > b: 1/61 → a 仍排第一
        assert fused["a"] > fused["b"]


# ═══════════════════════════════════════════════════════════
#  基础召回 & 降级链
# ═══════════════════════════════════════════════════════════


class TestKnowledgeSearch:
    """基础检索：BM25 命中 / 空库 / 空查询 / 归一化。"""

    def test_empty_store_returns_empty(self, store):
        searcher, hits = _search(store, "任意查询")
        assert hits == []

    def test_empty_query_returns_empty(self, seeded_store):
        searcher, hits = _search(seeded_store, "   ")
        assert hits == []

    def test_bm25_recall_and_score_normalized(self, seeded_store):
        searcher, hits = _search(seeded_store, "双链")
        assert hits, "BM25 应命中含查询词的卡片"
        assert hits[0].slug == "驾驭工程"
        assert hits[0].rerank_score == 0.0  # 无 reranker → 降级原序
        assert 0.0 <= hits[0].score <= 1.0  # RRF 融合分已归一化

    def test_hit_fields(self, seeded_store):
        searcher, hits = _search(seeded_store, "双链")
        hit = hits[0]
        assert hit.title == "驾驭工程"
        assert hit.status == "current"
        assert hit.type == "concepts"
        assert hit.source_ref == "wiki/concepts/驾驭工程.md"
        assert isinstance(hit.snippet, str) and hit.snippet

    def test_vector_store_raises_fallback(self, seeded_store):
        searcher = KnowledgeSearch(
            seeded_store,
            vector_store=_FakeVectorStore(exc=RuntimeError("向量库崩溃")),
            min_score=0.3,
        )
        hits = searcher.search("双链")  # 向量路异常 → 降级 BM25，不抛异常
        assert hits and hits[0].slug == "驾驭工程"

    def test_vector_store_none_only_bm25(self, seeded_store):
        searcher = KnowledgeSearch(seeded_store, vector_store=None, min_score=0.3)
        hits = searcher.search("双链")
        assert hits and hits[0].slug == "驾驭工程"

    def test_vector_recall_merges_bm25(self, seeded_store):
        # 向量路召回 BM25 未命中的卡片 → 融合结果补充召回
        vs = _FakeVectorStore([_FakeItem("提示词工程", score=0.9)])
        searcher = KnowledgeSearch(seeded_store, vector_store=vs, min_score=0.3)
        hits = searcher.search("双链")
        slugs = [h.slug for h in hits]
        assert "驾驭工程" in slugs and "提示词工程" in slugs

    def test_search_async_matches_sync(self, seeded_store):
        searcher = KnowledgeSearch(seeded_store, min_score=0.3)
        sync = searcher.search("双链")
        async_hits = asyncio.run(searcher.search_async("双链"))
        assert [h.slug for h in async_hits] == [h.slug for h in sync]

    def test_unrelated_query_only_matches_unrelated(self, store):
        # 无关词查询不受双链扩展影响：仅命中含查询词的卡片，不误召关联卡
        store.create(_card(
            "驾驭工程", content="通过设计人机协作边界实现双链管理", links=["提示词工程"]
        ))
        store.create(_card("提示词工程", content="提示词模板设计与工程实践"))
        store.create(_card("无关概念", content="烘焙温度与发酵时间控制"))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("烘焙")
        assert [h.slug for h in hits] == ["无关概念"]


# ═══════════════════════════════════════════════════════════
#  reranker 降级 / 打分 / 阈值过滤
# ═══════════════════════════════════════════════════════════


class TestRerankerDegradation:
    """reranker 不可用 → RRF 原序（rerank_score=0.0），永不抛异常。"""

    def test_none_reranker_original_order(self, seeded_store):
        _, hits = _search(seeded_store, "双链")
        assert hits[0].slug == "驾驭工程"
        assert all(h.rerank_score == 0.0 for h in hits)

    def test_reranker_raises_fallback(self, seeded_store):
        searcher = KnowledgeSearch(
            seeded_store,
            reranker=_FakeReranker(exc=TimeoutError("子进程超时")),
            min_score=0.3,
        )
        hits = searcher.search("双链")  # 不抛异常
        assert hits[0].slug == "驾驭工程"
        assert all(h.rerank_score == 0.0 for h in hits)

    def test_reranker_returns_none_fallback(self, seeded_store):
        searcher = KnowledgeSearch(
            seeded_store,
            reranker=_FakeReranker(results=None),
            min_score=0.3,
        )
        hits = searcher.search("双链")
        assert hits[0].slug == "驾驭工程"
        assert all(h.rerank_score == 0.0 for h in hits)

    def test_reranker_empty_list_fallback(self, seeded_store):
        searcher = KnowledgeSearch(
            seeded_store, reranker=_FakeReranker(results=[]), min_score=0.3
        )
        hits = searcher.search("双链")
        assert hits[0].slug == "驾驭工程"

    def test_reranker_raw_zero_stays_zero(self, seeded_store):
        # raw==0.0 是 ToolReranker 降级标记 → 保持 0.0（非 sigmoid(0)=0.5）
        reranker = _FakeReranker(results=[("驾驭工程", 0.5, 0.0)])
        searcher = KnowledgeSearch(seeded_store, reranker=reranker, min_score=0.3)
        hits = searcher.search("双链")
        assert all(h.rerank_score == 0.0 for h in hits)

    def test_reranker_sigmoid_probability(self, seeded_store):
        reranker = _FakeReranker(results=[("驾驭工程", 0.5, 2.0)])
        searcher = KnowledgeSearch(seeded_store, reranker=reranker, min_score=0.3)
        hits = searcher.search("双链")
        assert hits[0].rerank_score == pytest.approx(
            round(1 / (1 + math.exp(-2.0)), 3), abs=0.001
        )

    def test_reranker_threshold_filter(self, seeded_store):
        # sigmoid(-10)≈4.5e-5 < 0.001 被过滤；sigmoid(3)≈0.953 保留
        reranker = _FakeReranker(results=[
            ("驾驭工程", 0.5, 3.0),
            ("提示词工程", 0.4, -10.0),
        ])
        searcher = KnowledgeSearch(
            seeded_store, reranker=reranker, min_score=0.3, rerank_min_score=0.001
        )
        hits = searcher.search("双链")
        assert [h.slug for h in hits] == ["驾驭工程"]


# ═══════════════════════════════════════════════════════════
#  双链扩展召回
# ═══════════════════════════════════════════════════════════


class TestLinkExpansion:
    """双链一跳扩展：概念关联型查询命中 links 卡片。"""

    def test_link_expansion_improves_recall(self, store):
        # A 含查询词并引用 B；B 不含查询词 → B 仅经双链扩展路召回
        store.create(_card(
            "驾驭工程", content="通过设计人机协作边界实现双链管理", links=["提示词工程"]
        ))
        store.create(_card("提示词工程", content="结构化提示词模板"))

        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("双链")
        slugs = [h.slug for h in hits]
        assert "提示词工程" in slugs, "双链扩展应把关联卡片纳入召回"

    def test_link_expansion_top1_unchanged(self, store):
        # 扩展路不改变 top1（原 BM25 命中仍居首）
        store.create(_card(
            "驾驭工程", content="通过设计人机协作边界实现双链管理", links=["提示词工程"]
        ))
        store.create(_card("提示词工程", content="结构化提示词模板"))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("双链")
        assert hits[0].slug == "驾驭工程"

    def test_broken_link_skipped(self, store):
        store.create(_card("驾驭工程", content="双链管理", links=["不存在的卡片"]))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("双链")  # 断链跳过，不抛异常
        assert hits[0].slug == "驾驭工程"

    def test_archives_link_skipped(self, store):
        store.create(_card("驾驭工程", content="双链管理", links=["archives/旧卡片"]))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("双链")  # 归档目标不可索引，跳过
        assert hits[0].slug == "驾驭工程"

    def test_link_expansion_one_hop_only(self, store):
        # A→B→C 两跳链路：B 经双链扩展召回，C（两跳目标）不递归扩展
        store.create(_card(
            "驾驭工程", content="通过设计人机协作边界实现双链管理", links=["提示词工程"]
        ))
        store.create(_card("提示词工程", content="提示词模板设计与工程实践", links=["多跳终点"]))
        store.create(_card("多跳终点", content="多跳场景不应被递归扩展"))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("双链")
        slugs = [h.slug for h in hits]
        assert "提示词工程" in slugs, "一跳目标应被双链扩展召回"
        assert "多跳终点" not in slugs, "两跳目标不应被递归扩展"

    def test_link_expansion_reciprocal_no_double_count(self, store):
        # A↔B 互引且都 BM25 命中：目标均在 seeds → 双链扩展无新增，
        # B 不因链接路重复累加 RRF 分（B 仅 BM25 rank2 → 归一化 61/62）
        store.create(_card("驾驭工程", content="双链管理", links=["提示词工程"]))
        store.create(_card("提示词工程", content="工程实践", links=["驾驭工程"]))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("双链工程")
        by_slug = {h.slug: h for h in hits}
        assert by_slug["提示词工程"].score == pytest.approx(61 / 62, abs=0.002), \
            "互引卡在双链扩展路应重复跳过，不双计 RRF 分"


# ═══════════════════════════════════════════════════════════
#  误召回保护
# ═══════════════════════════════════════════════════════════


class TestMisRecallProtection:
    """top1 max(各路原始分数) < min_score → 空结果（不输出噪声）。"""

    def test_is_mis_recall_unit(self):
        # raw_scores 为单层 {"bm25": float, "vector": float|None}
        assert KnowledgeSearch._is_mis_recall("a", {"bm25": 0.1}, 0.3) is True
        assert KnowledgeSearch._is_mis_recall("a", {"bm25": 0.5}, 0.3) is False
        # 无任何已知分数（仅向量路且无分数）→ 放行，不误伤
        assert KnowledgeSearch._is_mis_recall("a", {"vector": None}, 0.3) is False
        assert KnowledgeSearch._is_mis_recall("a", {}, 0.3) is False

    def test_low_bm25_top1_returns_empty(self, store):
        # 20 张卡片全部含高频词 → idf 极小 → BM25 分数 < 0.3 → 触发保护
        for i in range(20):
            store.create(_card(f"卡片{i}", content="测试内容" * 5))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("测试")
        assert hits == []

    def test_high_bm25_top1_passes(self, seeded_store):
        _, hits = _search(seeded_store, "双链")
        assert hits, "高置信 top1 不应被误杀"


# ═══════════════════════════════════════════════════════════
#  format_context 输出
# ═══════════════════════════════════════════════════════════


class TestFormatContext:
    """输出带 [来源: slug|status] 标记的引用块。"""

    def _hit(self, slug="驾驭工程", status="current", score=0.82):
        return KnowledgeHit(
            slug=slug,
            title="驾驭工程",
            status=status,
            type="concepts",
            score=score,
            rerank_score=0.0,
            source_ref=f"wiki/concepts/{slug}.md",
            snippet="通过设计人机协作边界…",
        )

    def test_contains_source_marker(self):
        text = format_context([self._hit()])
        assert "【知识库检索结果】" in text
        assert "[来源: wiki/concepts/驾驭工程.md | current | score=0.82]" in text
        assert "snippet: 通过设计人机协作边界…" in text

    def test_empty_hits(self):
        assert format_context([]) == "（知识库未检索到相关卡片）"

    def test_top_k_limited(self):
        hits = [self._hit(slug=f"卡片{i}", score=0.9 - i * 0.1) for i in range(3)]
        text = format_context(hits, top_k=2)
        assert "1." in text and "2." in text
        assert "3." not in text

    def test_numbered_entries(self):
        hits = [self._hit(slug=f"卡片{i}") for i in range(2)]
        text = format_context(hits)
        assert text.startswith("【知识库检索结果】")
        assert "1." in text and "2." in text


# ═══════════════════════════════════════════════════════════
#  敏感命中处理
# ═══════════════════════════════════════════════════════════


class TestSensitiveHandling:
    """敏感素材（meta sensitive=true）→ snippet 标 [敏感] 或按配置隐藏。"""

    def test_sensitive_marker(self, store):
        store.create(_card(
            "机密策略", content="绝密内容不可外泄", metadata={"sensitive": True}
        ))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("机密")
        assert hits[0].snippet.startswith("[敏感]")

    def test_hide_sensitive_snippet(self, store):
        store.create(_card(
            "机密策略", content="绝密内容不可外泄", metadata={"sensitive": True}
        ))
        searcher = KnowledgeSearch(store, min_score=0.3, hide_sensitive_snippet=True)
        hits = searcher.search("机密")
        assert hits[0].snippet == "[敏感] 内容已隐藏"

    def test_normal_snippet_no_marker(self, seeded_store):
        _, hits = _search(seeded_store, "双链")
        assert "[敏感]" not in hits[0].snippet

    def test_sensitive_not_hidden_by_default(self, store):
        # 默认不隐藏（只标 [敏感]），snippet 仍可读
        store.create(_card(
            "机密策略", content="绝密内容不可外泄", metadata={"sensitive": True}
        ))
        searcher = KnowledgeSearch(store, min_score=0.3)
        hits = searcher.search("机密")
        assert "[敏感]" in hits[0].snippet and "不可外泄" in hits[0].snippet


class TestComplexLinkGraph:
    """复杂双链关系图：RRF 多路累加 + 双链扩展边界（与 dev --complex 数据集对齐）。"""

    def test_rrf_multi_path_accumulation_tops(self, complex_searcher):
        # 模型训练 = BM25 rank1 + 向量 rank1 = 2/61 → 归一化 2/3，两路累加居首
        hits = complex_searcher.search("机器学习")
        assert hits[0].slug == "模型训练"
        assert hits[0].score == pytest.approx(2 / 3, abs=0.01)

    def test_link_only_card_equals_bm25_rank1(self, complex_searcher):
        # 模型评估不含查询词，仅双链路 rank1 = 1/61 → 归一化 1/3，与 BM25 rank1 等分
        by_slug = {h.slug: h for h in complex_searcher.search("机器学习")}
        assert by_slug["模型评估"].score == pytest.approx(1 / 3, abs=0.01)

    def test_seed_vector_hit_skips_link_double_count(self, complex_searcher):
        # 特征工程被向量路命中故为种子 → 双链扩展对其「重复跳过」不双计，
        # 分数 = 仅向量 rank2 = 1/62 → 归一化 61/186
        by_slug = {h.slug: h for h in complex_searcher.search("机器学习")}
        assert by_slug["特征工程"].score == pytest.approx(61 / 186, abs=0.002)

    def test_expanded_card_links_not_recursive(self, complex_searcher):
        # 模型评估是双链扩展结果（非种子），其 links 指向 调参技巧 → 不递归召回
        slugs = {h.slug for h in complex_searcher.search("机器学习")}
        assert "调参技巧" not in slugs

    def test_reverse_link_and_unrelated_not_recalled(self, complex_searcher):
        # 生成对抗 反向链接（→模型训练）不被扩展；无关卡不受影响
        slugs = {h.slug for h in complex_searcher.search("机器学习")}
        assert "生成对抗" not in slugs
        assert "无关卡" not in slugs


class TestConcurrency:
    """并发稳定性（stress_knowledge_search.py 验证场景的精简 CI 版）。

    覆盖 CardStore 读写锁（_RWLock）与 search 纯读并发：
    - 写写并发 create/update/delete 串行化 → 库最终一致、无残留、无异常；
    - 多线程 search 结果确定性（无状态污染）。
    """

    def test_concurrent_write_crud_consistent(self, store):
        store.create(_card("初始卡", content="占位"))
        def writer(tid):
            for i in range(5):
                name = f"并发卡{tid}号{i}"
                store.create(_card(name, content="占位"))
                assert store.get(name) is not None
                store.update(_card(name, content="占位更新"))
                assert store.delete(name) is True
                assert store.get(name) is None
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(writer, range(4)))
        assert [c.slug for c in store.list()] == ["初始卡"]

    def test_concurrent_search_deterministic(self, complex_searcher):
        baseline = [h.slug for h in complex_searcher.search("机器学习")]
        assert baseline
        def reader(_):
            for _ in range(5):
                hits = complex_searcher.search("机器学习")
                assert [h.slug for h in hits[:3]] == baseline[:3]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(reader, range(8)))


class TestLinkCachePerfRegression:
    """双链扩展性能回归（防缓存策略退化，CI 自动收集）。

    - 结构断言：search 热路径不读 CardStore（monkeypatch store.get 抛异常仍
      正常检索）——若缓存退化到逐条 resolve_link→store.get 即触发异常失败；
    - 耗时断言：link 阶段 < 20ms（缓存路径 ~0.03ms，legacy 文件 I/O ~40ms，
      阈值远宽于缓存、远窄于退化，抗 CI 环境波动）。
    """

    def test_search_never_reads_store_on_hot_path(self, complex_store, monkeypatch):
        # 构造后热路径（双链扩展/检索）若回退到 CardStore.get 文件 I/O → 抛异常
        def _blocking_get(self, slug):
            raise AssertionError(f"search 热路径不应读 CardStore.get: {slug}")
        monkeypatch.setattr(CardStore, "get", _blocking_get)
        searcher = KnowledgeSearch(complex_store, min_score=0.3)
        hits = searcher.search("机器学习")
        assert hits and hits[0].slug == "模型训练"

    @pytest.mark.serial  # 【P3】日志采集断言：xdist 并行时全局 logging 状态竞争 → 串行段执行
    def test_link_stage_below_io_bound(self, complex_store):
        links_ms: list[float] = []
        class _TimingHandler(logging.Handler):
            def emit(self, record):
                # 兼容两种日志格式：旧 json.dumps 字符串 / 新 log_dict dict
                # （log_dict 后 record.msg 为 dict 对象；旧格式为 JSON 字符串）
                msg = record.msg
                if isinstance(msg, dict):
                    if msg.get("action") != "search_stage_timing":
                        return
                    data = msg
                else:
                    try:
                        data = json.loads(str(msg))
                    except (ValueError, TypeError):
                        return
                    if data.get("action") != "search_stage_timing":
                        return
                links_ms.append(data["ms"]["link"])
        handler = _TimingHandler(level=logging.INFO)
        sl = logging.getLogger("agent.knowledge.search")
        sl.setLevel(logging.INFO)
        sl.propagate = False
        sl.addHandler(handler)
        try:
            searcher = KnowledgeSearch(
                complex_store, min_score=0.3, timing_sample_rate=1.0,
            )
            for _ in range(10):
                searcher.search("机器学习")
        finally:
            sl.removeHandler(handler)
        assert links_ms, "应采集到 search_stage_timing 日志"
        assert max(links_ms) < 20.0, (
            f"link 阶段退化到文件 I/O: max={max(links_ms):.2f}ms（缓存应 <1ms）"
        )


class TestBM25IndexConcurrency:
    """BM25Index 并发读写（threading.RLock 原子化）。

    修复前：add_document（写）与 search（读）并发时 _total_docs 为
    「读-改-写」序列（非原子），并发写会丢失计数更新、倒排表遍历与修改
    交错可能读到半更新结构。修复后：add_document / search / size 同一
    RLock，锁内仅内存 dict 操作，无 I/O。
    """

    def test_concurrent_add_unique_docs_no_lost_count(self):
        """100 线程 × 50 次并发 add_document：size 精确、检索可命中全量"""
        idx = BM25Index()
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)  # 同步起跑，放大读-改-写竞争
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    idx.add_document(f"doc_{tid}_{i}", f"并发检索词{tid} 共享主题词")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert idx.size == total           # _total_docs 无丢失更新
        hits = idx.search("共享主题词", top_k=total)
        assert len(hits) == total          # 倒排表结构完整，全量可检索

    def test_concurrent_overwrite_same_doc_id(self):
        """并发覆盖同一 doc_id：size 恒为 1，不膨胀"""
        idx = BM25Index()
        idx.add_document("fixed_doc", "初始内容")
        n_threads, per = 20, 20
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(_):
            try:
                barrier.wait()
                for _ in range(per):
                    idx.add_document("fixed_doc", "覆盖内容 共享词")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert idx.size == 1               # 覆盖语义：重复 doc_id 不产生多余条目
        hits = idx.search("覆盖内容", top_k=10)
        assert [doc_id for doc_id, _ in hits] == ["fixed_doc"]

    def test_concurrent_mixed_read_write_deterministic(self):
        """写线程 add + 读线程 search 并发：不抛异常、读结果稳定"""
        idx = BM25Index()
        for i in range(50):
            idx.add_document(f"seed_{i}", f"种子主题词 {i}")
        n_writers = 4
        stop = threading.Event()
        errors = []

        def writer(tid):
            try:
                for i in range(100):
                    idx.add_document(f"w_{tid}_{i}", "写线程主题词")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        def reader(_):
            try:
                while not stop.is_set():
                    hits = idx.search("种子主题词")
                    assert hits, "检索应始终命中种子文档"
                    assert isinstance(idx.size, int)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        writers = [threading.Thread(target=writer, args=(t,)) for t in range(n_writers)]
        readers = [threading.Thread(target=reader, args=(t,)) for t in range(4)]
        for t in writers + readers:
            t.start()
        for t in writers:
            t.join()
        stop.set()  # 写完成后停止读线程
        for t in readers:
            t.join()

        assert not errors
        assert idx.size == 50 + n_writers * 100  # 读写混合下计数仍精确
