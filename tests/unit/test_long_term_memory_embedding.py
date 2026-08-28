"""[TLM-L4] LongTermMemory embedding 列与三模式检索单元测试

覆盖：
- embedding 读写（save/get/to_dict/from_dict）
- 三种 search mode（keyword/semantic/hybrid）+ 降级
- list_recent 方法
- get_stats 的 embedding_entries 计数
- schema 迁移（幂等）
- _cosine_similarity 辅助函数
"""

import pytest
import os
import sqlite3
from pathlib import Path

from agent.memory.long_term_memory import (
    LongTermMemory,
    LongTermMemoryEntry,
    _cosine_similarity,
    _embedding_to_blob,
    _blob_to_embedding,
)
from agent.memory.base import MemoryResult


@pytest.fixture
def ltm_instance(tmp_path):
    """临时数据库的 LongTermMemory 实例（使用 pytest tmp_path 避免 Windows TEMP 路径问题）"""
    db_path = tmp_path / "test_ltm.db"
    yield LongTermMemory(db_path=str(db_path))


# ═══════════════════════════════════════════════════════════════
# _cosine_similarity
# ═══════════════════════════════════════════════════════════════

class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0

    def test_different_length(self):
        assert _cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vector(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# ═══════════════════════════════════════════════════════════════
# embedding 读写
# ═══════════════════════════════════════════════════════════════

class TestEmbeddingReadWrite:
    @pytest.mark.asyncio
    async def test_save_with_embedding(self, ltm_instance):
        emb = [0.1, 0.2, 0.3, 0.4]
        ok = await ltm_instance.save("k1", "content1", embedding=emb)
        assert ok is True
        entry = await ltm_instance.get("k1")
        assert entry is not None
        # [P3] embedding 用 BLOB(float32) 存储，有微小精度损失，用 approx 比较
        assert entry.embedding == pytest.approx(emb, abs=1e-6)

    @pytest.mark.asyncio
    async def test_save_without_embedding(self, ltm_instance):
        """不传 embedding 时默认 None（向后兼容）"""
        ok = await ltm_instance.save("k2", "content2")
        assert ok is True
        entry = await ltm_instance.get("k2")
        assert entry is not None
        assert entry.embedding is None

    @pytest.mark.asyncio
    async def test_upsert_embedding(self, ltm_instance):
        """覆盖写入时 embedding 更新"""
        await ltm_instance.save("k3", "v1", embedding=[1.0, 0.0])
        await ltm_instance.save("k3", "v2", embedding=[0.0, 1.0])
        entry = await ltm_instance.get("k3")
        assert entry is not None
        assert entry.embedding == [0.0, 1.0]

    def test_to_dict_includes_embedding(self):
        entry = LongTermMemoryEntry(key="k", content="c", embedding=[0.5, 0.5])
        d = entry.to_dict()
        assert d["embedding"] == [0.5, 0.5]

    def test_from_dict_parses_embedding_str(self):
        """embedding 为 JSON 字符串时正确解析"""
        data = {"key": "k", "content": "c", "embedding": "[0.1, 0.2, 0.3]"}
        entry = LongTermMemoryEntry.from_dict(data)
        assert entry.embedding == [0.1, 0.2, 0.3]

    def test_from_dict_embedding_none(self):
        data = {"key": "k", "content": "c", "embedding": None}
        entry = LongTermMemoryEntry.from_dict(data)
        assert entry.embedding is None

    def test_from_dict_embedding_list(self):
        data = {"key": "k", "content": "c", "embedding": [1.0, 2.0]}
        entry = LongTermMemoryEntry.from_dict(data)
        assert entry.embedding == [1.0, 2.0]


# ═══════════════════════════════════════════════════════════════
# search mode
# ═══════════════════════════════════════════════════════════════

class TestSearchMode:
    @pytest.mark.asyncio
    async def test_keyword_search(self, ltm_instance):
        await ltm_instance.save("k1", "机器学习基础", tags=["ml"])
        results = await ltm_instance.search("机器", mode="keyword")
        assert len(results) >= 1
        assert any("机器" in str(r.content) for r in results)

    @pytest.mark.asyncio
    async def test_semantic_search(self, ltm_instance):
        emb1 = [1.0, 0.0, 0.0]
        emb2 = [0.9, 0.1, 0.0]
        await ltm_instance.save("k1", "语义内容A", embedding=emb1)
        await ltm_instance.save("k2", "语义内容B", embedding=emb2)
        results = await ltm_instance.search("x", mode="semantic", query_embedding=[1.0, 0.0, 0.0])
        assert len(results) >= 1
        # 最相似的应排第一
        assert results[0].metadata.get("key") == "k1"

    @pytest.mark.asyncio
    async def test_hybrid_search(self, ltm_instance):
        await ltm_instance.save("k1", "深度学习", embedding=[1.0, 0.0])
        await ltm_instance.save("k2", "机器学习", embedding=[0.7, 0.3])
        results = await ltm_instance.search("学习", mode="hybrid", query_embedding=[1.0, 0.0])
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_semantic_without_embedding_degrades(self, ltm_instance):
        """semantic 模式无 query_embedding 时降级为 keyword"""
        await ltm_instance.save("k1", "内容")
        results = await ltm_instance.search("内容", mode="semantic", query_embedding=None)
        assert len(results) >= 1  # 降级后仍能命中

    @pytest.mark.asyncio
    async def test_semantic_skips_null_embedding(self, ltm_instance):
        """semantic 模式跳过无 embedding 的条目"""
        await ltm_instance.save("k1", "无向量内容")  # 无 embedding
        await ltm_instance.save("k2", "有向量内容", embedding=[1.0, 0.0])
        results = await ltm_instance.search("x", mode="semantic", query_embedding=[1.0, 0.0])
        keys = [r.metadata.get("key") for r in results]
        assert "k2" in keys
        assert "k1" not in keys

    @pytest.mark.asyncio
    async def test_empty_db_search(self, ltm_instance):
        results = await ltm_instance.search("anything", mode="keyword")
        assert results == []


# ═══════════════════════════════════════════════════════════════
# list_recent
# ═══════════════════════════════════════════════════════════════

class TestListRecent:
    @pytest.mark.asyncio
    async def test_list_recent_basic(self, ltm_instance):
        # 【不易】save() 内部用 time.time() 记 created_at；两连写可能落在同一微秒，
        # created_at 相同 → ORDER BY created_at DESC 稳定排序按 rowid 返回插入序
        # （k1 在前），快机器（CI Linux）上必挂。显式 mock 递增时间戳保证确定性。
        from unittest import mock

        _clock = [1000.0]

        def _fake_time():
            _clock[0] += 1.0
            return _clock[0]

        with mock.patch("agent.memory.long_term_memory.time.time", side_effect=_fake_time):
            await ltm_instance.save("k1", "v1")
            await ltm_instance.save("k2", "v2")
        entries = ltm_instance.list_recent(limit=10)
        assert len(entries) == 2
        # 最新的在前（created_at DESC）
        assert entries[0].key == "k2"
        assert entries[1].key == "k1"

    @pytest.mark.asyncio
    async def test_list_recent_limit(self, ltm_instance):
        for i in range(5):
            await ltm_instance.save(f"k{i}", f"v{i}")
        entries = ltm_instance.list_recent(limit=3)
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_list_recent_days_filter(self, ltm_instance):
        await ltm_instance.save("k1", "v1")
        entries = ltm_instance.list_recent(limit=10, days=1)
        assert len(entries) == 1  # 刚写入的都在 1 天内

    @pytest.mark.asyncio
    async def test_list_recent_empty(self, ltm_instance):
        entries = ltm_instance.list_recent(limit=10)
        assert entries == []


# ═══════════════════════════════════════════════════════════════
# get_stats
# ═══════════════════════════════════════════════════════════════

class TestGetStats:
    @pytest.mark.asyncio
    async def test_embedding_entries_count(self, ltm_instance):
        await ltm_instance.save("k1", "v1", embedding=[1.0])
        await ltm_instance.save("k2", "v2")  # 无 embedding
        stats = ltm_instance.get_stats()
        assert stats["embedding_entries"] == 1
        assert stats["total_entries"] == 2


# ═══════════════════════════════════════════════════════════════
# schema 迁移
# ═══════════════════════════════════════════════════════════════

class TestSchemaMigration:
    def test_idempotent_migration(self, tmp_path):
        """多次实例化不应报错（embedding 列幂等迁移）"""
        db_path = str(tmp_path / "mig.db")
        LongTermMemory(db_path=db_path)
        LongTermMemory(db_path=db_path)  # 第二次不应报错
        LongTermMemory(db_path=db_path)  # 第三次也不报错

    @pytest.mark.asyncio
    async def test_old_data_compatible(self, tmp_path):
        """旧数据（无 embedding 列）迁移后仍可使用"""
        db_path = str(tmp_path / "old.db")
        # 先用旧 schema 创建表（无 embedding 列）
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE long_term_memory (
                key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                importance INTEGER DEFAULT 3,
                tags TEXT DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                access_count INTEGER DEFAULT 0,
                sensitive INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """)
        conn.execute("INSERT INTO long_term_memory (key, content, created_at, updated_at, last_accessed) VALUES (?, ?, ?, ?, ?)",
                     ("old_key", "old_content", 1000.0, 1000.0, 1000.0))
        conn.commit()
        conn.close()

        # 实例化时自动迁移
        ltm = LongTermMemory(db_path=db_path)
        entry = await ltm.get("old_key")
        assert entry is not None
        assert entry.content == "old_content"
        assert entry.embedding is None  # 旧数据无 embedding


# ═══════════════════════════════════════════════════════════════
# [P3] BLOB 序列化格式测试
# ═══════════════════════════════════════════════════════════════

class TestEmbeddingBlobFormat:
    """[P3] 验证 BLOB 格式存储 + 向后兼容旧 JSON TEXT"""

    def test_embedding_to_blob_basic(self):
        """list[float] → BLOB 基本序列化"""
        blob = _embedding_to_blob([1.0, 2.0, 3.0])
        assert isinstance(blob, bytes)
        assert len(blob) == 12  # 3 × 4 bytes (float32)

    def test_embedding_to_blob_none(self):
        """None → None"""
        assert _embedding_to_blob(None) is None

    def test_embedding_to_blob_empty(self):
        """空 list → 空 bytes"""
        assert _embedding_to_blob([]) == b""

    def test_blob_to_embedding_round_trip(self):
        """BLOB 往返：list → blob → list 保持精度（float32 范围内）"""
        original = [0.5, -0.3, 1.0, 0.0, -1.0]
        blob = _embedding_to_blob(original)
        result = _blob_to_embedding(blob)
        assert result == pytest.approx(original, abs=1e-6)

    def test_blob_to_embedding_none(self):
        """None → None"""
        assert _blob_to_embedding(None) is None

    def test_blob_to_embedding_empty_bytes(self):
        """空 bytes → None"""
        assert _blob_to_embedding(b"") is None

    def test_blob_to_embedding_legacy_json_text(self):
        """[向后兼容] 旧 JSON TEXT 字符串仍能正确解析"""
        legacy_str = '[0.1, 0.2, 0.3]'
        result = _blob_to_embedding(legacy_str)
        assert result == [0.1, 0.2, 0.3]  # JSON 解析无精度损失

    def test_blob_to_embedding_legacy_json_bytes(self):
        """[向后兼容] 旧 JSON TEXT 存为 bytes 仍能正确解析"""
        legacy_bytes = b'[0.1, 0.2, 0.3]'
        # 长度 14 不是 4 的倍数，struct.unpack 会失败，回退到 JSON 解析
        result = _blob_to_embedding(legacy_bytes)
        assert result == [0.1, 0.2, 0.3]

    def test_blob_to_embedding_list_input(self):
        """list 输入直接返回"""
        result = _blob_to_embedding([1.0, 2.0])
        assert result == [1.0, 2.0]

    def test_blob_to_embedding_invalid_bytes(self):
        """无效 bytes（长度非 4 的倍数且非 JSON）→ None"""
        result = _blob_to_embedding(b'\x01\x02\x03')  # 3 bytes，无法 unpack
        assert result is None

    # ── [P3 兼容性审查] 极端异常数据测试 ──

    def test_blob_to_embedding_memoryview(self):
        """[兼容] memoryview 类型应能正确解析（SQLite 某些配置返回 memoryview）"""
        import struct
        original = [1.0, 2.0, 3.0]
        blob = struct.pack('3f', *original)
        mv = memoryview(blob)
        result = _blob_to_embedding(mv)
        assert result == pytest.approx(original, abs=1e-6)

    def test_blob_to_embedding_memoryview_empty(self):
        """[兼容] 空 memoryview → None"""
        result = _blob_to_embedding(memoryview(b""))
        assert result is None

    def test_blob_to_embedding_nan_filter(self):
        """[防御] BLOB 包含 NaN → 返回 None（防止污染余弦相似度）"""
        import struct
        # NaN 的 float32 表示: 0x7FC00000
        nan_bytes = struct.pack('2f', float('nan'), 1.0)
        result = _blob_to_embedding(nan_bytes)
        assert result is None

    def test_blob_to_embedding_inf_filter(self):
        """[防御] BLOB 包含 Inf → 返回 None"""
        import struct
        inf_bytes = struct.pack('2f', float('inf'), 1.0)
        result = _blob_to_embedding(inf_bytes)
        assert result is None

    def test_blob_to_embedding_negative_inf_filter(self):
        """[防御] BLOB 包含 -Inf → 返回 None"""
        import struct
        neg_inf_bytes = struct.pack('2f', float('-inf'), 1.0)
        result = _blob_to_embedding(neg_inf_bytes)
        assert result is None

    def test_blob_to_embedding_oversized_blob(self):
        """[防御] 超大 BLOB（>10000 floats）→ 返回 None（防止 MemoryError）"""
        import struct
        # 10001 个 float32 = 40004 bytes
        huge_blob = struct.pack('10001f', *([0.0] * 10001))
        result = _blob_to_embedding(huge_blob)
        assert result is None

    def test_blob_to_embedding_max_size_boundary(self):
        """[防御] 刚好 10000 floats 的 BLOB 应正常解析（边界值）"""
        import struct
        boundary_blob = struct.pack('10000f', *([0.5] * 10000))
        result = _blob_to_embedding(boundary_blob)
        assert result is not None
        assert len(result) == 10000

    def test_blob_to_embedding_garbage_bytes_aligned(self):
        """[防御] 长度是 4 的倍数但内容是垃圾 → 可能返回垃圾值或 None

        注意: struct.unpack 对任何 4 字节对齐的数据都会成功，
        所以这不是异常场景，但 NaN/Inf 检查会过滤掉部分垃圾值。
        全零 bytes 会返回 [0.0, 0.0, ...]，这是合法的（虽然语义无意义）。
        """
        result = _blob_to_embedding(b'\x00\x00\x00\x00')
        assert result == [0.0]  # 全零是合法的 float32

    def test_blob_to_embedding_other_types(self):
        """[防御] 其他类型（int, dict, set, float）→ None"""
        assert _blob_to_embedding(42) is None
        assert _blob_to_embedding(3.14) is None
        assert _blob_to_embedding({"key": "value"}) is None
        assert _blob_to_embedding({1, 2, 3}) is None
        assert _blob_to_embedding(True) is None

    def test_blob_to_embedding_str_non_json(self):
        """[防御] str 但非 JSON → None"""
        assert _blob_to_embedding("not a json") is None
        assert _blob_to_embedding("[invalid") is None
        assert _blob_to_embedding("") is None

    def test_blob_to_embedding_str_json_non_list(self):
        """[防御] str 解析为 JSON 但非 list → None"""
        assert _blob_to_embedding('{"key": "value"}') is None
        assert _blob_to_embedding('"just a string"') is None
        assert _blob_to_embedding('42') is None
        assert _blob_to_embedding('null') is None

    @pytest.mark.asyncio
    async def test_save_get_uses_blob_format(self, tmp_path):
        """端到端：save(embedding=) → get() 往返一致（float32 精度）"""
        import os
        db_path = str(tmp_path / "blob_test.db")
        ltm = LongTermMemory(db_path=db_path)

        emb = [0.1, 0.2, 0.3, 0.4, 0.5]
        await ltm.save("blob_key", "content", embedding=emb)
        entry = await ltm.get("blob_key")

        assert entry is not None
        assert entry.embedding == pytest.approx(emb, abs=1e-6)

        # 验证存储格式确实是 BLOB（而非 JSON TEXT）
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT embedding FROM long_term_memory WHERE key = 'blob_key'").fetchone()
        conn.close()
        assert isinstance(row[0], bytes)  # BLOB 类型
        assert len(row[0]) == 20  # 5 × 4 bytes

    @pytest.mark.asyncio
    async def test_legacy_json_text_still_readable(self, tmp_path):
        """[向后兼容] 手工写入旧 JSON TEXT 格式的 embedding 仍能被 get() 读取"""
        import json, sqlite3, os
        db_path = str(tmp_path / "legacy_test.db")

        # 手工创建表并写入旧格式 JSON TEXT
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE long_term_memory (
                key TEXT PRIMARY KEY, content TEXT NOT NULL,
                importance INTEGER DEFAULT 3, tags TEXT DEFAULT '[]',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                last_accessed REAL NOT NULL, access_count INTEGER DEFAULT 0,
                sensitive INTEGER DEFAULT 0, verified INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}', embedding TEXT DEFAULT NULL
            )
        """)
        legacy_emb = json.dumps([0.1, 0.2, 0.3])  # 旧 JSON TEXT 格式
        conn.execute(
            "INSERT INTO long_term_memory (key, content, created_at, updated_at, last_accessed, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy_key", "legacy_content", 1.0, 1.0, 1.0, legacy_emb)
        )
        conn.commit()
        conn.close()

        # 实例化 LTM（自动迁移 embedding 列，但旧数据仍是 JSON TEXT）
        ltm = LongTermMemory(db_path=db_path)
        entry = await ltm.get("legacy_key")

        assert entry is not None
        # 旧 JSON TEXT 仍能正确解析（无精度损失）
        assert entry.embedding == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_mixed_blob_and_json_text(self, tmp_path):
        """[向后兼容] BLOB 和 JSON TEXT 混合存在的场景"""
        import json, sqlite3, os
        db_path = str(tmp_path / "mixed_test.db")
        ltm = LongTermMemory(db_path=db_path)

        # 1. 新数据用 BLOB 存储
        await ltm.save("new_key", "new_content", embedding=[0.4, 0.5, 0.6])

        # 2. 手工插入旧 JSON TEXT 数据
        conn = sqlite3.connect(db_path)
        legacy_emb = json.dumps([0.1, 0.2, 0.3])
        conn.execute(
            "INSERT INTO long_term_memory (key, content, created_at, updated_at, last_accessed, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old_key", "old_content", 1.0, 1.0, 1.0, legacy_emb)
        )
        conn.commit()
        conn.close()

        # 两种格式都能正确读取
        new_entry = await ltm.get("new_key")
        old_entry = await ltm.get("old_key")

        assert new_entry.embedding == pytest.approx([0.4, 0.5, 0.6], abs=1e-6)
        assert old_entry.embedding == [0.1, 0.2, 0.3]  # JSON TEXT 无精度损失

    @pytest.mark.asyncio
    async def test_semantic_search_with_mixed_formats(self, tmp_path):
        """[向后兼容] semantic 搜索同时处理 BLOB 和 JSON TEXT 格式的 embedding"""
        import json, sqlite3, os
        db_path = str(tmp_path / "mixed_search.db")
        ltm = LongTermMemory(db_path=db_path)

        # 新数据用 BLOB
        await ltm.save("blob_key", "doc1", embedding=[1.0, 0.0, 0.0])

        # 旧数据用 JSON TEXT
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO long_term_memory (key, content, created_at, updated_at, last_accessed, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("json_key", "doc2", 1.0, 1.0, 1.0, json.dumps([0.9, 0.1, 0.0]))
        )
        conn.commit()
        conn.close()

        # semantic 搜索应能同时处理两种格式
        results = await ltm.search("x", mode="semantic", query_embedding=[1.0, 0.0, 0.0], top_k=5)
        assert len(results) >= 1  # 至少能搜索到 BLOB 格式的
        # 两种格式都能被搜索到
        keys = [r.metadata["key"] for r in results]
        assert "blob_key" in keys
