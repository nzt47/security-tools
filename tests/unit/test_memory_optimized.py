"""
内存优化模块测试 - 覆盖封装层逻辑
"""

import pytest
import time
import tempfile
import threading
from unittest.mock import Mock, patch, MagicMock

from agent.memory_optimized import (
    ChromaInitProgress,
    ChromaInitStats,
    ChromaInitCache,
    OptimizedChromaDB,
    LazyCollectionProxy,
    MockChromaClient,
    MockCollection,
    create_optimized_chroma,
)


class TestChromaInitProgress:
    """测试 ChromaDB 初始化进度"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_create_progress(self):
        """测试创建进度对象"""
        progress = ChromaInitProgress(
            stage="loading",
            progress=0.5,
            message="Loading data...",
            elapsed_ms=100.0,
        )

        assert progress.stage == "loading"
        assert progress.progress == 0.5
        assert progress.message == "Loading data..."

    @pytest.mark.unit
    @pytest.mark.p2
    def test_progress_range(self):
        """测试进度范围"""
        progress = ChromaInitProgress(
            stage="test",
            progress=0.75,
            message="Test",
            elapsed_ms=50.0,
        )

        assert 0.0 <= progress.progress <= 1.0


class TestChromaInitStats:
    """测试 ChromaDB 初始化统计"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_init(self):
        """测试初始化"""
        stats = ChromaInitStats()

        assert stats.total_inits == 0
        assert stats.successful_inits == 0
        assert stats.failed_inits == 0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_init_success(self):
        """测试记录成功初始化"""
        stats = ChromaInitStats()

        stats.record_init(
            success=True,
            total_time_ms=500.0,
            is_async=False,
        )

        assert stats.total_inits == 1
        assert stats.successful_inits == 1
        assert stats.avg_time_ms == 500.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_init_failure(self):
        """测试记录失败初始化"""
        stats = ChromaInitStats()

        stats.record_init(
            success=False,
            total_time_ms=100.0,
            is_async=False,
        )

        assert stats.total_inits == 1
        assert stats.failed_inits == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_async_init(self):
        """测试记录异步初始化"""
        stats = ChromaInitStats()

        stats.record_init(
            success=True,
            total_time_ms=200.0,
            is_async=True,
        )

        assert stats.async_inits == 1

    @pytest.mark.unit
    @pytest.mark.p2
    def test_record_with_stage_times(self):
        """测试记录带阶段时间的初始化"""
        stats = ChromaInitStats()

        stats.record_init(
            success=True,
            total_time_ms=500.0,
            stage_times={
                "connect": 100.0,
                "load": 300.0,
                "index": 100.0,
            },
        )

        assert "connect" in stats.stage_times
        assert stats.stage_times["connect"] == [100.0]

    @pytest.mark.unit
    @pytest.mark.p2
    def test_avg_time_multiple_inits(self):
        """测试多次初始化的平均时间"""
        stats = ChromaInitStats()

        stats.record_init(True, 200.0)
        stats.record_init(True, 400.0)

        assert stats.avg_time_ms == 300.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_fastest_slowest_time(self):
        """测试最快和最慢时间"""
        stats = ChromaInitStats()

        stats.record_init(True, 100.0)
        stats.record_init(True, 500.0)
        stats.record_init(True, 200.0)

        assert stats.fastest_time_ms == 100.0
        assert stats.slowest_time_ms == 500.0

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_stats_empty(self):
        """测试获取空统计"""
        stats = ChromaInitStats()
        result = stats.get_stats()

        assert result['success_rate'] == "N/A"
        assert result['fastest_time_ms'] == "N/A"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_get_stats_with_data(self):
        """测试获取有数据的统计"""
        stats = ChromaInitStats()
        stats.record_init(True, 100.0)
        stats.record_init(True, 300.0)

        result = stats.get_stats()

        assert result['total_inits'] == 2
        assert result['successful_inits'] == 2
        assert result['success_rate'] == "100.0%"
        assert float(result['avg_time_ms']) == 200.0


class TestChromaInitCache:
    """测试初始化缓存"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_put_and_get(self):
        """测试缓存存取"""
        cache = ChromaInitCache(max_size=5)

        cache.put("/path1", "collection1", {"key": "value1"})
        result = cache.get("/path1", "collection1")

        assert result == {"key": "value1"}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_miss(self):
        """测试缓存未命中"""
        cache = ChromaInitCache(max_size=5)

        result = cache.get("/nonexistent", "collection")
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_eviction(self):
        """测试缓存淘汰"""
        cache = ChromaInitCache(max_size=3)

        for i in range(5):
            cache.put(f"/path{i}", f"collection{i}", {"index": i})

        assert cache.get("/path0", "collection0") is None
        assert cache.get("/path1", "collection1") is None
        assert cache.get("/path2", "collection2") is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_lru_order(self):
        """测试 LRU 顺序"""
        cache = ChromaInitCache(max_size=3)

        cache.put("/path1", "col1", {"idx": 1})
        cache.put("/path2", "col2", {"idx": 2})
        cache.put("/path3", "col3", {"idx": 3})

        cache.get("/path1", "col1")

        cache.put("/path4", "col4", {"idx": 4})

        assert cache.get("/path2", "col2") is None
        assert cache.get("/path1", "col1") is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_clear(self):
        """测试清空缓存"""
        cache = ChromaInitCache(max_size=5)

        cache.put("/path1", "collection1", {"key": "value1"})
        cache.clear()

        assert cache.get("/path1", "collection1") is None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cache_concurrent_access(self):
        """测试并发访问缓存"""
        cache = ChromaInitCache(max_size=10)
        results = []

        def writer():
            for i in range(10):
                cache.put(f"/path{i}", "col", {"idx": i})

        def reader():
            for i in range(10):
                results.append(cache.get(f"/path{i}", "col") is not None)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) > 0


class TestOptimizedChromaDB:
    """测试优化版 ChromaDB 封装层"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sync_init(self):
        """测试同步初始化"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_sync",
                enable_async=False,
                enable_cache=False,
            )

            assert db.is_initialized
            assert not db.is_initializing

    @pytest.mark.unit
    @pytest.mark.p0
    def test_async_init(self):
        """测试异步初始化"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_async",
                enable_async=True,
                enable_cache=False,
            )

            assert db.is_initializing
            assert not db.is_initialized

            time.sleep(0.1)

            assert db.is_initialized
            assert not db.is_initializing

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_hit(self):
        """测试缓存命中"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            OptimizedChromaDB._cache.clear()

            db1 = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_cache",
                enable_async=False,
                enable_cache=True,
            )

            db2 = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_cache",
                enable_async=False,
                enable_cache=True,
            )

            assert db1.is_initialized
            assert db2.is_initialized

    @pytest.mark.unit
    @pytest.mark.p0
    def test_vector_operations(self):
        """测试向量操作封装"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_ops",
                enable_async=False,
                enable_cache=False,
            )

            db.add(
                embeddings=[[1.0, 2.0], [3.0, 4.0]],
                documents=["doc1", "doc2"],
                ids=["id1", "id2"],
            )

            result = db.query(query_embeddings=[[1.0, 2.0]], n_results=2)
            assert result is not None
            assert "ids" in result

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_stats(self):
        """测试获取统计信息"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            OptimizedChromaDB._stats = ChromaInitStats()

            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_stats",
                enable_async=False,
                enable_cache=False,
            )

            stats = db.get_stats()
            assert stats['initialized'] is True
            assert 'global_stats' in stats

    @pytest.mark.unit
    @pytest.mark.p1
    def test_global_stats(self):
        """测试全局统计"""
        OptimizedChromaDB._stats = ChromaInitStats()

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_global",
                enable_async=False,
                enable_cache=False,
            )

        stats = OptimizedChromaDB.get_global_stats()
        assert stats['total_inits'] >= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_clear_cache(self):
        """测试清空全局缓存"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            OptimizedChromaDB._cache.put(tmpdir, "test", {"key": "value"})

            OptimizedChromaDB.clear_cache()

            result = OptimizedChromaDB._cache.get(tmpdir, "test")
            assert result is None

    @pytest.mark.unit
    @pytest.mark.p2
    def test_uninitialized_access(self):
        """测试未初始化时访问集合"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            # mock _init_async 不启动线程，确保 _initialized 保持 False
            # CI Linux 上 MockChromaClient 初始化极快，异步线程可能在断言前完成
            with patch.object(OptimizedChromaDB, '_init_async', lambda self: None):
                db = OptimizedChromaDB(
                    persist_directory=tmpdir,
                    collection_name="test_uninit",
                    enable_async=True,
                    enable_cache=False,
                )

                with pytest.raises(RuntimeError):
                    _ = db.collection

    @pytest.mark.unit
    @pytest.mark.p2
    def test_progress_callback(self):
        """测试进度回调"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            updates = []

            def callback(progress):
                updates.append(progress)

            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_progress",
                enable_async=False,
                enable_cache=False,
                on_progress=callback,
            )

            assert len(updates) > 0
            assert any(p.stage == "complete" for p in updates)


class TestLazyCollectionProxy:
    """测试延迟集合代理"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_lazy_loading(self):
        """测试延迟加载"""
        mock_client = MockChromaClient()
        proxy = LazyCollectionProxy(mock_client, "lazy_test")

        assert proxy._collection is None

        proxy.add(
            embeddings=[[1.0, 2.0]],
            documents=["test"],
            ids=["id1"],
        )

        assert proxy._collection is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_proxy_methods(self):
        """测试代理方法"""
        mock_client = MockChromaClient()
        proxy = LazyCollectionProxy(mock_client, "proxy_test")

        proxy.add(embeddings=[[1.0]], documents=["doc"], ids=["id"])
        result = proxy.query(query_embeddings=[[1.0]])

        assert result is not None
        assert proxy.count() == 1


class TestMockImplementation:
    """测试模拟实现"""

    @pytest.mark.unit
    @pytest.mark.p2
    def test_mock_client(self):
        """测试模拟客户端"""
        client = MockChromaClient()
        collection = client.get_or_create_collection("test")

        assert collection.name == "test"

    @pytest.mark.unit
    @pytest.mark.p2
    def test_mock_collection(self):
        """测试模拟集合"""
        collection = MockCollection("test")

        collection.add(
            embeddings=[[1.0, 2.0]],
            documents=["test doc"],
            ids=["id1"],
        )

        assert collection.count() == 1

        result = collection.query(query_embeddings=[[1.0]], n_results=5)
        assert "ids" in result


class TestFactoryFunction:
    """测试工厂函数"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_create_optimized_chroma(self):
        """测试工厂函数"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = create_optimized_chroma(
                persist_directory=tmpdir,
                collection_name="test_factory",
                async_init=False,
            )

            assert db.is_initialized

    @pytest.mark.unit
    @pytest.mark.p1
    def test_create_async(self):
        """测试异步工厂函数"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db = create_optimized_chroma(
                persist_directory=tmpdir,
                collection_name="test_async_factory",
                async_init=True,
            )

            time.sleep(0.1)
            assert db.is_initialized