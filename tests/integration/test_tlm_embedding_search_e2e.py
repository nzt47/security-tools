"""[TLM] embedding 语义检索端到端集成测试

验证 save(embedding=) → search(mode="semantic") 的完整流程。
使用确定性手工向量（3 维正交基），不依赖 sentence_transformers。

向量设计：
- doc1 = [1.0, 0.0, 0.0]  （"机器学习"）
- doc2 = [0.9, 0.4, 0.0]  （"深度学习"，与 doc1 高相似 ~0.914）
- doc3 = [0.0, 0.0, 1.0]  （"完全不同主题"，与 doc1 正交 = 0.0）
- query  = [0.95, 0.3, 0.0] （应匹配 doc1 > doc2 > doc3）
"""

import pytest
import math

from agent.memory.long_term_memory import LongTermMemory, _cosine_similarity


@pytest.fixture
def ltm(tmp_path):
    """真实 LongTermMemory（临时 SQLite）"""
    return LongTermMemory(db_path=str(tmp_path / "emb_e2e.db"))


# ═══════════════════════════════════════════════════════════════
# 确定性向量
# ═══════════════════════════════════════════════════════════════

VEC_DOC1 = [1.0, 0.0, 0.0]      # 机器学习
VEC_DOC2 = [0.9, 0.4, 0.0]      # 深度学习（与 doc1 相似）
VEC_DOC3 = [0.0, 0.0, 1.0]      # 完全不同主题（正交）
VEC_QUERY = [0.95, 0.05, 0.0]   # 查询向量（应匹配 doc1 > doc2 > doc3）


# ═══════════════════════════════════════════════════════════════
# 端到端测试
# ═══════════════════════════════════════════════════════════════

class TestEmbeddingSearchE2E:
    """save(embedding=) → search(mode="semantic") 端到端"""

    @pytest.mark.asyncio
    async def test_recall_at_1(self, ltm):
        """场景1: recall@1=1.0 — 最相似的结果排第一"""
        await ltm.save("doc1", "机器学习基础", embedding=VEC_DOC1)
        await ltm.save("doc2", "深度学习进阶", embedding=VEC_DOC2)
        await ltm.save("doc3", "完全不同主题", embedding=VEC_DOC3)

        results = await ltm.search("x", mode="semantic", query_embedding=VEC_QUERY, top_k=3)

        assert len(results) == 3
        # recall@1: 最相似的 doc1 应排第一
        assert results[0].metadata["key"] == "doc1"
        # similarity 应为最高
        assert results[0].metadata["similarity"] > results[1].metadata["similarity"]

    @pytest.mark.asyncio
    async def test_KNN按相似度降序(self, ltm):
        """场景2: 多条 embedding → KNN 按相似度降序排列"""
        await ltm.save("doc1", "机器学习", embedding=VEC_DOC1)
        await ltm.save("doc2", "深度学习", embedding=VEC_DOC2)
        await ltm.save("doc3", "不同主题", embedding=VEC_DOC3)

        results = await ltm.search("x", mode="semantic", query_embedding=VEC_QUERY, top_k=3)

        similarities = [r.metadata["similarity"] for r in results]
        # 降序排列
        assert similarities == sorted(similarities, reverse=True)
        # doc3（正交）相似度应最低
        assert results[-1].metadata["key"] == "doc3"
        assert results[-1].metadata["similarity"] == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_无embedding的条目被跳过(self, ltm):
        """场景3: semantic 模式跳过无 embedding 的条目"""
        await ltm.save("doc1", "有向量", embedding=VEC_DOC1)
        await ltm.save("doc2", "无向量")  # 无 embedding
        await ltm.save("doc3", "也有向量", embedding=VEC_DOC3)

        results = await ltm.search("x", mode="semantic", query_embedding=VEC_QUERY, top_k=10)

        keys = [r.metadata["key"] for r in results]
        assert "doc1" in keys
        assert "doc3" in keys
        assert "doc2" not in keys  # 无 embedding 被跳过

    @pytest.mark.asyncio
    async def test_hybrid模式合并去重(self, ltm):
        """场景4: hybrid 模式 — keyword + semantic 合并，按 key 去重"""
        await ltm.save("doc1", "机器学习基础", embedding=VEC_DOC1, tags=["ml"])
        await ltm.save("doc2", "深度学习进阶", embedding=VEC_DOC2, tags=["ml"])
        await ltm.save("doc3", "机器学习应用", embedding=VEC_DOC3, tags=["ml"])
        # doc3 的 embedding 正交但 content 含 "机器学习"

        results = await ltm.search("机器学习", mode="hybrid", query_embedding=VEC_QUERY, top_k=10)

        # 不应有重复 key
        keys = [r.metadata["key"] for r in results]
        assert len(keys) == len(set(keys)), "hybrid 模式存在重复 key"
        # keyword 命中的 doc1 和 doc3 应在结果中
        assert "doc1" in keys
        assert "doc3" in keys

    @pytest.mark.asyncio
    async def test_semantic降级为keyword(self, ltm):
        """场景5: semantic 模式无 query_embedding → 降级为 keyword"""
        await ltm.save("doc1", "机器学习基础")
        results = await ltm.search("机器", mode="semantic", query_embedding=None)
        assert len(results) >= 1
        # 降级后仍能命中 keyword 结果
        assert any("机器" in str(r.content) for r in results)

    @pytest.mark.asyncio
    async def test_top_k限制结果数(self, ltm):
        """场景6: top_k 限制返回数量"""
        for i in range(5):
            await ltm.save(f"doc{i}", f"内容{i}", embedding=VEC_DOC1)

        results = await ltm.search("x", mode="semantic", query_embedding=VEC_QUERY, top_k=2)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_confidence反映相似度(self, ltm):
        """场景7: semantic 结果的 confidence 反映相似度（0-1 范围）"""
        await ltm.save("doc1", "内容1", embedding=VEC_DOC1)
        await ltm.save("doc3", "内容3", embedding=VEC_DOC3)

        results = await ltm.search("x", mode="semantic", query_embedding=VEC_QUERY, top_k=2)

        for r in results:
            assert 0.0 <= r.confidence <= 1.0
        # doc1 的 confidence 应高于 doc3（正交）
        doc1 = next(r for r in results if r.metadata["key"] == "doc1")
        doc3 = next(r for r in results if r.metadata["key"] == "doc3")
        assert doc1.confidence > doc3.confidence


# ═══════════════════════════════════════════════════════════════
# 向量相似度数学验证（辅助断言）
# ═══════════════════════════════════════════════════════════════

class Test向量相似度数学验证:
    """验证测试用向量的数学属性，确保测试预期正确"""

    def test_query与doc1最相似(self):
        sim1 = _cosine_similarity(VEC_QUERY, VEC_DOC1)
        sim2 = _cosine_similarity(VEC_QUERY, VEC_DOC2)
        sim3 = _cosine_similarity(VEC_QUERY, VEC_DOC3)
        assert sim1 > sim2 > sim3

    def test_doc1与doc2高相似(self):
        """doc1 和 doc2 的相似度应 > 0.9"""
        sim = _cosine_similarity(VEC_DOC1, VEC_DOC2)
        assert sim > 0.9

    def test_doc1与doc3正交(self):
        """doc1 和 doc3 正交，相似度 = 0"""
        sim = _cosine_similarity(VEC_DOC1, VEC_DOC3)
        assert sim == pytest.approx(0.0, abs=1e-6)

    def test_query与doc1相似度合理(self):
        """query 与 doc1 的相似度应 > 0.95"""
        sim = _cosine_similarity(VEC_QUERY, VEC_DOC1)
        assert sim > 0.95


# ═══════════════════════════════════════════════════════════════
# 极端数据量边界测试（L3 性能 + 正确性）
# ═══════════════════════════════════════════════════════════════

class Test极端数据量边界:
    """验证 L3 在大量数据下的正确性和基本性能

    基准测试结果（1000 条 × 384 维，本地 Windows）：
    - semantic:  p50=220ms  （瓶颈：全表 SELECT * + json.loads + 全排序）
    - keyword:   p50=4.4ms  （LIKE 全表扫描 + LIMIT，性能良好）
    - hybrid:    p50=220ms  （受 semantic 路径影响）
    - list_recent: p50=69ms （200 条 from_dict 序列化）

    优化建议（未实施，留待后续）：
    1. semantic 只 SELECT key/content/embedding/importance（减少 IO）
    2. 用 heapq.nlargest 替代 sorted（O(n log k) vs O(n log n)）
    3. embedding 存为 BLOB 而非 JSON TEXT（避免 json.loads）
    """

    @pytest.fixture
    def ltm_with_100_entries(self, tmp_path):
        """预置 100 条带 embedding 的 LTM"""
        import random
        ltm = LongTermMemory(db_path=str(tmp_path / "extreme.db"))
        import asyncio

        async def _setup():
            random.seed(42)  # 确定性随机
            for i in range(100):
                emb = [random.random() for _ in range(64)]  # 64 维（减少测试时间）
                await ltm.save(
                    f"extreme_{i}",
                    f"文档{i}内容包含关键词",
                    embedding=emb,
                    importance=random.randint(1, 5),
                )

        asyncio.run(_setup())
        return ltm

    @pytest.mark.asyncio
    async def test_100条semantic正确性(self, ltm_with_100_entries):
        """100 条数据 semantic 搜索 → 返回 top_k 结果且按相似度降序"""
        import random
        random.seed(42)
        query_emb = [random.random() for _ in range(64)]

        results = await ltm_with_100_entries.search(
            "x", mode="semantic", query_embedding=query_emb, top_k=10
        )

        assert len(results) == 10
        similarities = [r.metadata["similarity"] for r in results]
        assert similarities == sorted(similarities, reverse=True)

    @pytest.mark.asyncio
    async def test_100条semantic_top_k限制(self, ltm_with_100_entries):
        """top_k=5 时只返回 5 条"""
        import random
        random.seed(42)
        query_emb = [random.random() for _ in range(64)]

        results = await ltm_with_100_entries.search(
            "x", mode="semantic", query_embedding=query_emb, top_k=5
        )
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_100条keyword正确性(self, ltm_with_100_entries):
        """100 条数据 keyword 搜索 → 命中含关键词的条目"""
        results = await ltm_with_100_entries.search("关键词", mode="keyword", top_k=10)
        assert len(results) >= 1
        assert all("关键词" in str(r.content) for r in results)

    @pytest.mark.asyncio
    async def test_100条hybrid去重(self, ltm_with_100_entries):
        """100 条数据 hybrid 搜索 → 无重复 key"""
        import random
        random.seed(42)
        query_emb = [random.random() for _ in range(64)]

        results = await ltm_with_100_entries.search(
            "文档", mode="hybrid", query_embedding=query_emb, top_k=20
        )
        keys = [r.metadata["key"] for r in results]
        assert len(keys) == len(set(keys)), "hybrid 模式存在重复 key"

    @pytest.mark.asyncio
    async def test_100条list_recent(self, ltm_with_100_entries):
        """100 条数据 list_recent → 按时间降序"""
        entries = ltm_with_100_entries.list_recent(limit=50)
        assert len(entries) == 50
        # created_at 降序
        timestamps = [e.created_at for e in entries]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.asyncio
    async def test_semantic空embedding条目跳过_批量(self, ltm_with_100_entries):
        """100 条中混入无 embedding 条目 → semantic 只返回有 embedding 的"""
        await ltm_with_100_entries.save("no_emb", "无向量内容")  # 无 embedding

        import random
        random.seed(42)
        query_emb = [random.random() for _ in range(64)]

        results = await ltm_with_100_entries.search(
            "x", mode="semantic", query_embedding=query_emb, top_k=105
        )
        keys = [r.metadata["key"] for r in results]
        assert "no_emb" not in keys
        assert len(results) == 100  # 只有 100 条有 embedding
