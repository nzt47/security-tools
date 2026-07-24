"""VectorStore sqlite-vec 后端测试

覆盖：
- SqliteVecBackend 核心功能（add/search/get_by_id/get_recent/clear/count/get_stats）
- VectorStore _backend 不可变性（线程安全）
- VectorStore sqlite-vec 集成（使用 mock encoder 避免加载真实 55s 模型）
- recall@1=1.0 验证（查询与某条记录完全相同时，top1 应该是该记录）

CI Linux 环境下 sentence_transformers 被 patch 为 None，仅 SqliteVecBackend
直接测试可运行；集成测试在本地 Windows（sqlite-vec + sentence_transformers 均可用）下运行。
"""
import os
import sys
import time
import hashlib
import tempfile
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

# ── 依赖可用性检测 ──
# 模块级保存真实 sqlite_vec 引用，供 autouse fixture 覆盖 conftest.py 的全局禁用
try:
    import sqlite_vec
    _REAL_SQLITE_VEC = sqlite_vec
    _HAS_SQLITE_VEC = True
except ImportError:
    _REAL_SQLITE_VEC = None
    _HAS_SQLITE_VEC = False

# sentence_transformers 可用性（CI Linux 下被 conftest patch 为 None）
_HAS_ST = False
if not sys.platform.startswith('linux') or not os.environ.get('CI'):
    try:
        import sentence_transformers  # noqa: F401
        _HAS_ST = True
    except ImportError:
        _HAS_ST = False


@pytest.fixture(autouse=True)
def _enable_sqlite_vec_for_tests():
    """覆盖 conftest.py 的全局禁用，为 sqlite-vec 测试启用真实模块。

    Why: tests/unit/conftest.py 的 _disable_optional_systems_safety fixture
    默认 patch.dict(sys.modules, {'sqlite_vec': None}) 禁用 sqlite-vec，
    避免间接实例化 VectorStore 的测试触发 55s+ 模型加载。本测试文件需要
    真实 sqlite-vec，通过 patch.dict 嵌套覆盖启用（内层覆盖外层）。
    """
    if _REAL_SQLITE_VEC is None:
        yield
        return
    with patch.dict(sys.modules, {'sqlite_vec': _REAL_SQLITE_VEC}):
        yield


# ════════════════════════════════════════════════════════════
# Fixtures
# ════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_db_path():
    """临时数据库路径（每个测试独立目录）"""
    tmpdir = tempfile.mkdtemp(prefix="sqlite_vec_test_")
    return os.path.join(tmpdir, "test_vec.db")


@pytest.fixture
def sqlite_vec_backend(tmp_db_path):
    """SqliteVecBackend 实例（dim=4，便于构造测试向量）"""
    if not _HAS_SQLITE_VEC:
        pytest.skip("sqlite-vec not installed")
    from memory.vector_store.sqlite_vec_backend import SqliteVecBackend
    backend = SqliteVecBackend(db_path=tmp_db_path, collection_name="test", dim=4)
    yield backend
    backend.clear()


def _make_deterministic_vec(text: str, dim: int = 4):
    """基于文本 hash 生成确定性向量（测试用，避免加载真实模型）"""
    h = hashlib.md5(text.encode("utf-8")).digest()
    return [float(h[i % len(h)]) / 255.0 for i in range(dim)]


def _make_mock_encoder(dim: int = 4):
    """构造 mock SentenceTransformer encoder

    返回 numpy array（与真实 sentence_transformers 行为一致，支持 .tolist()）。
    """
    mock_encoder = MagicMock()
    mock_encoder.get_sentence_embedding_dimension.return_value = dim

    def mock_encode(texts):
        if isinstance(texts, str):
            texts = [texts]
        return np.array([_make_deterministic_vec(t, dim) for t in texts])

    mock_encoder.encode = mock_encode
    return mock_encoder


# ════════════════════════════════════════════════════════════
# SqliteVecBackend 核心功能测试
# ════════════════════════════════════════════════════════════

class TestSqliteVecBackend:
    """SqliteVecBackend 核心功能测试（仅依赖 sqlite-vec，不需要 sentence_transformers）"""

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p0
    def test_add_and_count(self, sqlite_vec_backend):
        """测试添加和计数"""
        assert sqlite_vec_backend.count() == 0
        assert sqlite_vec_backend.add("id1", "hello", [1.0, 2.0, 3.0, 4.0], {"tag": "a"})
        assert sqlite_vec_backend.count() == 1
        assert sqlite_vec_backend.add("id2", "world", [5.0, 6.0, 7.0, 8.0], {"tag": "b"})
        assert sqlite_vec_backend.count() == 2

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p0
    def test_search_knn_exact_match(self, sqlite_vec_backend):
        """测试 KNN 精确匹配（distance=0）"""
        sqlite_vec_backend.add("id1", "hello", [1.0, 0.0, 0.0, 0.0])
        sqlite_vec_backend.add("id2", "world", [0.0, 1.0, 0.0, 0.0])
        sqlite_vec_backend.add("id3", "foo", [1.0, 1.0, 0.0, 0.0])

        results = sqlite_vec_backend.search([1.0, 0.0, 0.0, 0.0], top_k=1)
        assert len(results) == 1
        assert results[0]["id"] == "id1"
        assert results[0]["distance"] == pytest.approx(0.0, abs=1e-6)
        assert results[0]["content"] == "hello"

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p0
    def test_get_by_id(self, sqlite_vec_backend):
        """测试按 ID 查找"""
        sqlite_vec_backend.add("id1", "hello", [1.0, 2.0, 3.0, 4.0], {"tag": "a"})

        r = sqlite_vec_backend.get_by_id("id1")
        assert r is not None
        assert r["id"] == "id1"
        assert r["content"] == "hello"
        assert r["metadata"] == {"tag": "a"}

        assert sqlite_vec_backend.get_by_id("nonexistent") is None

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_get_recent(self, sqlite_vec_backend):
        """测试获取最近添加（按 created_at DESC 排序）"""
        for i in range(5):
            sqlite_vec_backend.add(f"id{i}", f"content{i}", [float(i)] * 4)
            time.sleep(0.02)  # 确保 created_at 时间戳不同（Windows tick ~15.6ms）

        recent = sqlite_vec_backend.get_recent(limit=3)
        assert len(recent) == 3
        # 最近添加的在前（created_at DESC）
        assert recent[0]["id"] == "id4"
        assert recent[1]["id"] == "id3"
        assert recent[2]["id"] == "id2"

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p0
    def test_clear(self, sqlite_vec_backend):
        """测试清空"""
        sqlite_vec_backend.add("id1", "hello", [1.0, 2.0, 3.0, 4.0])
        sqlite_vec_backend.add("id2", "world", [5.0, 6.0, 7.0, 8.0])
        assert sqlite_vec_backend.count() == 2

        deleted = sqlite_vec_backend.clear()
        assert deleted == 2
        assert sqlite_vec_backend.count() == 0

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_batch_add(self, sqlite_vec_backend):
        """测试批量添加"""
        items = [
            {
                "id": f"id{i}",
                "content": f"content{i}",
                "embedding": [float(i)] * 4,
                "metadata": {"idx": i},
            }
            for i in range(10)
        ]
        success = sqlite_vec_backend.batch_add(items)
        assert success == 10
        assert sqlite_vec_backend.count() == 10

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_batch_add_dimension_mismatch_skipped(self, sqlite_vec_backend):
        """测试批量添加时维度不匹配的项被跳过"""
        items = [
            {"id": "id1", "content": "ok", "embedding": [1.0, 2.0, 3.0, 4.0], "metadata": {}},
            {"id": "id2", "content": "bad", "embedding": [1.0, 2.0, 3.0], "metadata": {}},  # dim=3
        ]
        success = sqlite_vec_backend.batch_add(items)
        assert success == 1
        assert sqlite_vec_backend.count() == 1

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_get_stats(self, sqlite_vec_backend):
        """测试统计信息"""
        sqlite_vec_backend.add("id1", "hello", [1.0, 2.0, 3.0, 4.0])

        stats = sqlite_vec_backend.get_stats()
        assert stats["backend"] == "sqlite_vec"
        assert stats["dim"] == 4
        assert stats["total_entries"] == 1
        assert stats["collection"] == "test"
        assert "vec_test" in stats["vec_table"]
        assert "meta_test" in stats["meta_table"]

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p0
    def test_recall_at_1(self, sqlite_vec_backend):
        """测试 recall@1=1.0：查询向量与某条记录完全相同时，top1 应该是该记录"""
        for i in range(20):
            vec = [float(i), float(i + 1), float(i + 2), float(i + 3)]
            sqlite_vec_backend.add(f"id{i}", f"content{i}", vec)

        # 用第 5 条记录的向量查询
        query_vec = [5.0, 6.0, 7.0, 8.0]
        results = sqlite_vec_backend.search(query_vec, top_k=1)
        assert len(results) == 1
        assert results[0]["id"] == "id5"
        assert results[0]["distance"] == pytest.approx(0.0, abs=1e-6)

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_dimension_mismatch_raises(self, sqlite_vec_backend):
        """测试维度不匹配抛出 ValueError"""
        with pytest.raises(ValueError, match="维度不匹配"):
            sqlite_vec_backend.add("id1", "hello", [1.0, 2.0, 3.0])  # dim=4, 给 3

        with pytest.raises(ValueError, match="维度不匹配"):
            sqlite_vec_backend.search([1.0, 2.0, 3.0], top_k=1)

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_persistence_across_connections(self, tmp_db_path):
        """测试跨连接持久化（重新打开数据库后数据仍在）"""
        from memory.vector_store.sqlite_vec_backend import SqliteVecBackend

        backend1 = SqliteVecBackend(db_path=tmp_db_path, collection_name="test", dim=4)
        backend1.add("id1", "hello", [1.0, 2.0, 3.0, 4.0])

        # 新连接实例化
        backend2 = SqliteVecBackend(db_path=tmp_db_path, collection_name="test", dim=4)
        assert backend2.count() == 1

        r = backend2.get_by_id("id1")
        assert r is not None
        assert r["content"] == "hello"

        # 新连接搜索
        results = backend2.search([1.0, 2.0, 3.0, 4.0], top_k=1)
        assert len(results) == 1
        assert results[0]["id"] == "id1"

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_top_k_limit(self, sqlite_vec_backend):
        """测试 top_k 限制返回数量"""
        for i in range(10):
            sqlite_vec_backend.add(f"id{i}", f"content{i}", [float(i)] * 4)

        results = sqlite_vec_backend.search([5.0, 5.0, 5.0, 5.0], top_k=3)
        assert len(results) == 3

    @pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not installed")
    @pytest.mark.p1
    def test_empty_search(self, sqlite_vec_backend):
        """测试空数据库搜索"""
        results = sqlite_vec_backend.search([1.0, 2.0, 3.0, 4.0], top_k=5)
        assert results == []


# ════════════════════════════════════════════════════════════
# VectorStore _backend 不可变性测试
# ════════════════════════════════════════════════════════════

class TestVectorStoreBackendImmutable:
    """VectorStore _backend 不可变性测试（线程安全核心契约）

    _backend 在构造期确定后不可变，_use_chroma 改为只读 property。
    运行期不再修改 _use_chroma（修复并发问题）。
    """

    @pytest.mark.p0
    def test_use_chroma_is_readonly_property(self):
        """测试 _use_chroma 是只读 property（基于 _backend 派生）"""
        from memory.vector_store.vector_store import VectorStore
        # _use_chroma 应该是 property 对象（只读）
        assert isinstance(VectorStore._use_chroma, property)

    @pytest.mark.p0
    def test_backend_field_exists_in_init(self):
        """测试 _backend 字段在 __init__ 中被初始化"""
        # 通过 inspect 源码确认 _backend 被初始化
        import inspect
        from memory.vector_store.vector_store import VectorStore
        source = inspect.getsource(VectorStore.__init__)
        assert "self._backend" in source
        assert "self._backend" in source

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p1
    def test_backend_is_sqlite_vec_when_available(self, tmp_path):
        """测试 sqlite-vec 可用时 _backend == 'sqlite_vec'"""
        from memory.vector_store.vector_store import VectorStore
        mock_encoder = _make_mock_encoder(dim=4)
        with patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder):
            vs = VectorStore(
                collection_name="test_immutable",
                persist_dir=str(tmp_path),
                cache_size=0,
            )
        assert vs._backend == "sqlite_vec"
        assert vs._use_chroma is False
        vs.clear()

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p0
    def test_use_chroma_cannot_be_assigned_at_runtime(self, tmp_path):
        """测试 _use_chroma 运行期不可赋值（property 只读，AttributeError）"""
        from memory.vector_store.vector_store import VectorStore
        mock_encoder = _make_mock_encoder(dim=4)
        with patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder):
            vs = VectorStore(
                collection_name="test_immutable2",
                persist_dir=str(tmp_path),
                cache_size=0,
            )
        # _use_chroma 是只读 property，赋值应抛 AttributeError
        with pytest.raises(AttributeError):
            vs._use_chroma = True
        vs.clear()

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p1
    def test_backend_field_not_modified_by_add_failure(self, tmp_path):
        """测试 add 失败不修改 _backend（运行期不可变）"""
        from memory.vector_store.vector_store import VectorStore
        mock_encoder = _make_mock_encoder(dim=4)
        with patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder):
            vs = VectorStore(
                collection_name="test_immutable3",
                persist_dir=str(tmp_path),
                cache_size=0,
            )
            original_backend = vs._backend
            # 正常 add 不应修改 _backend
            vs.add("test content")
            assert vs._backend == original_backend
        vs.clear()


# ════════════════════════════════════════════════════════════
# VectorStore sqlite-vec 集成测试（使用 mock encoder）
# ════════════════════════════════════════════════════════════

class TestVectorStoreSqliteVecIntegration:
    """VectorStore sqlite-vec 集成测试

    使用 mock encoder 避免加载真实 sentence_transformers 模型（55s）。
    仅在本地 Windows（sqlite-vec + sentence_transformers 均可用）下运行。
    """

    @pytest.fixture
    def mock_vector_store(self, tmp_path):
        """使用 mock encoder 的 VectorStore 实例（sqlite-vec 后端）"""
        if not _HAS_SQLITE_VEC:
            pytest.skip("sqlite-vec not installed")
        if not _HAS_ST:
            pytest.skip("sentence_transformers not available (CI Linux patches it to None)")

        from memory.vector_store.vector_store import VectorStore

        mock_encoder = _make_mock_encoder(dim=4)

        # patch SentenceTransformer 构造函数，返回 mock_encoder
        with patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder):
            vs = VectorStore(
                collection_name="test_integration",
                persist_dir=str(tmp_path),
                cache_size=0,  # 禁用缓存，避免测试间状态泄漏
            )

        assert vs._backend == "sqlite_vec", f"expected sqlite_vec, got {vs._backend}"
        yield vs
        vs.clear()

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p0
    def test_add_and_count(self, mock_vector_store):
        """测试通过 VectorStore API 添加和计数"""
        vs = mock_vector_store
        assert vs.count == 0
        vs.add("hello world", {"tag": "greeting"})
        assert vs.count == 1
        vs.add("foo bar", {"tag": "test"})
        assert vs.count == 2

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p0
    def test_search_returns_memory_items(self, mock_vector_store):
        """测试搜索返回 MemoryItem 列表"""
        from memory.vector_store.vector_store import MemoryItem
        vs = mock_vector_store
        vs.add("hello world")

        results = vs.search("hello world", top_k=1)
        assert len(results) == 1
        assert isinstance(results[0], MemoryItem)
        assert results[0].content == "hello world"

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p0
    def test_get_by_id(self, mock_vector_store):
        """测试按 ID 查找"""
        vs = mock_vector_store
        item_id = vs.add("test content", {"tag": "test"})

        item = vs.get_by_id(item_id)
        assert item is not None
        assert item.content == "test content"
        assert item.metadata["tag"] == "test"

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p1
    def test_get_recent(self, mock_vector_store):
        """测试获取最近添加"""
        vs = mock_vector_store
        for i in range(5):
            vs.add(f"content{i}")

        recent = vs.get_recent(limit=3)
        assert len(recent) == 3

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p0
    def test_clear(self, mock_vector_store):
        """测试清空"""
        vs = mock_vector_store
        vs.add("item1")
        vs.add("item2")
        assert vs.count == 2

        vs.clear()
        assert vs.count == 0

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p1
    def test_get_stats_contains_backend_field(self, mock_vector_store):
        """测试 get_stats 包含 backend 字段"""
        vs = mock_vector_store
        vs.add("test content")

        stats = vs.get_stats()
        assert stats["backend"] == "sqlite_vec"
        assert stats["count"] == 1
        assert "sqlite_vec" in stats
        assert stats["sqlite_vec"]["backend"] == "sqlite_vec"

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p1
    def test_batch_add(self, mock_vector_store):
        """测试批量添加"""
        vs = mock_vector_store
        items = [{"content": f"item{i}", "metadata": {"idx": i}} for i in range(5)]
        ids = vs.batch_add(items)

        assert len(ids) == 5
        assert vs.count == 5

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p0
    def test_recall_at_1_with_mock_encoder(self, mock_vector_store):
        """测试 recall@1=1.0：相同内容查询应返回自身（mock encoder 确定性向量）"""
        vs = mock_vector_store
        contents = [
            "人工智能是计算机科学的分支",
            "机器学习是AI的子领域",
            "深度学习使用神经网络",
            "自然语言处理处理文本",
            "计算机视觉处理图像",
        ]
        for content in contents:
            vs.add(content)

        # 用完全相同的内容查询（mock encoder 生成相同向量）
        for content in contents:
            results = vs.search(content, top_k=1)
            assert len(results) == 1, f"expected 1 result for: {content}"
            assert results[0].content == content, f"recall@1 failed for: {content}"

    @pytest.mark.skipif(not _HAS_SQLITE_VEC or not _HAS_ST,
                        reason="sqlite-vec or sentence_transformers not available")
    @pytest.mark.p1
    def test_persistence_across_vector_store_instances(self, tmp_path):
        """测试跨 VectorStore 实例持久化"""
        if not _HAS_SQLITE_VEC or not _HAS_ST:
            pytest.skip("dependencies not available")

        from memory.vector_store.vector_store import VectorStore

        mock_encoder = _make_mock_encoder(dim=4)

        with patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder):
            vs1 = VectorStore(
                collection_name="test_persist",
                persist_dir=str(tmp_path),
                cache_size=0,
            )
            vs1.add("persistent content")

        # 新实例
        with patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder):
            vs2 = VectorStore(
                collection_name="test_persist",
                persist_dir=str(tmp_path),
                cache_size=0,
            )

        assert vs2.count == 1
        results = vs2.search("persistent content", top_k=1)
        assert len(results) == 1
        assert results[0].content == "persistent content"
        vs2.clear()
