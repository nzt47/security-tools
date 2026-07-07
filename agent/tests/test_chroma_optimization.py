"""ChromaDB 异步预加载优化单元测试

测试覆盖：
1. 基础功能测试
   - 同步初始化
   - 异步初始化
   - 延迟加载

2. 性能测试
   - 初始化时间
   - 异步加载效果
   - 缓存命中率

3. 边界情况测试
   - 多次初始化
   - 错误处理
   - 并发访问

4. 集成测试
   - 与 LazyDigitalLife 集成
   - 向量操作

运行方式：
```bash
python -m pytest agent/tests/test_chroma_optimization.py -v
python -m pytest agent/tests/test_chroma_optimization.py::TestPerformance -v
```
"""

import pytest
import time
import threading
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch
from typing import List

from agent.memory_optimized import (
    OptimizedChromaDB,
    ChromaInitStats,
    ChromaInitCache,
    ChromaInitProgress,
    LazyCollectionProxy,
    MockChromaClient,
    MockCollection,
    create_optimized_chroma
)


class TestChromaInit:
    """初始化功能测试"""

    def test_sync_init(self, tmp_path):
        """测试同步初始化"""
        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma"),
            collection_name="test_sync",
            enable_async=False,
            enable_cache=False
        )

        assert db.is_initialized
        assert not db.is_initializing

    def test_async_init(self, tmp_path):
        """测试异步初始化"""
        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_async"),
            collection_name="test_async",
            enable_async=True,
            enable_cache=False
        )

        assert not db.is_initialized
        assert db.is_initializing

        # 等待初始化完成
        time.sleep(0.2)
        
        assert db.is_initialized
        assert not db.is_initializing

    def test_lazy_collection(self, tmp_path):
        """测试延迟集合加载"""
        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_lazy"),
            collection_name="test_lazy",
            enable_async=False,
            enable_lazy_collection=True
        )

        assert db._collection is not None
        assert isinstance(db._collection, LazyCollectionProxy)

    def test_progress_callback(self, tmp_path):
        """测试进度回调"""
        progress_updates = []

        def on_progress(progress: ChromaInitProgress):
            progress_updates.append(progress)

        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_progress"),
            collection_name="test_progress",
            enable_async=False,
            on_progress=on_progress
        )

        assert len(progress_updates) > 0
        assert any(p.stage == "complete" for p in progress_updates)


class TestChromaCache:
    """缓存功能测试"""

    def test_cache_put_get(self):
        """测试缓存存取"""
        cache = ChromaInitCache(max_size=5)

        cache.put("/path1", "collection1", {"key": "value1"})
        result = cache.get("/path1", "collection1")

        assert result == {"key": "value1"}

    def test_cache_miss(self):
        """测试缓存未命中"""
        cache = ChromaInitCache(max_size=5)

        result = cache.get("/nonexistent", "collection")
        assert result is None

    def test_cache_eviction(self):
        """测试缓存淘汰"""
        cache = ChromaInitCache(max_size=3)

        for i in range(5):
            cache.put(f"/path{i}", f"collection{i}", {'index': i})

        # 最早的两个应该被淘汰
        assert cache.get("/path0", "collection0") is None
        assert cache.get("/path1", "collection1") is None
        assert cache.get("/path2", "collection2") is not None

    def test_cache_clear(self):
        """测试清空缓存"""
        cache = ChromaInitCache(max_size=5)

        cache.put("/path1", "collection1", {"key": "value1"})
        cache.clear()

        assert cache.get("/path1", "collection1") is None


class TestChromaStats:
    """统计功能测试"""

    def test_record_success(self):
        """测试记录成功初始化"""
        stats = ChromaInitStats()

        stats.record_init(
            success=True,
            total_time_ms=100.0,
            stage_times={"create": 50.0, "init": 50.0}
        )

        assert stats.total_inits == 1
        assert stats.successful_inits == 1
        assert stats.avg_time_ms == 100.0

    def test_record_failure(self):
        """测试记录失败初始化"""
        stats = ChromaInitStats()

        stats.record_init(success=False, total_time_ms=50.0)

        assert stats.total_inits == 1
        assert stats.failed_inits == 1

    def test_async_init_tracking(self):
        """测试异步初始化追踪"""
        stats = ChromaInitStats()

        stats.record_init(success=True, total_time_ms=100.0, is_async=True)

        assert stats.async_inits == 1

    def test_stage_times(self):
        """测试分阶段时间统计"""
        stats = ChromaInitStats()

        stats.record_init(
            success=True,
            total_time_ms=100.0,
            stage_times={"stage1": 30.0, "stage2": 70.0}
        )

        stats_data = stats.get_stats()
        assert "stage_times" in stats_data


class TestPerformance:
    """性能测试"""

    def test_init_time(self, tmp_path):
        """测试初始化时间"""
        start = time.perf_counter()

        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_perf"),
            collection_name="test_perf",
            enable_async=False,
            enable_cache=False
        )

        elapsed_ms = (time.perf_counter() - start) * 1000

        assert db.is_initialized
        print(f"\n初始化时间: {elapsed_ms:.2f}ms")

    def test_async_init_no_blocking(self, tmp_path):
        """测试异步初始化不阻塞"""
        start = time.perf_counter()

        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_async_perf"),
            collection_name="test_async_perf",
            enable_async=True
        )

        # 立即返回，不等待初始化
        blocking_time_ms = (time.perf_counter() - start) * 1000

        assert not db.is_initialized
        assert blocking_time_ms < 50  # 应该小于 50ms

        # 等待初始化完成
        time.sleep(0.2)

        assert db.is_initialized

    def test_cached_init_speedup(self, tmp_path):
        """测试缓存加速初始化"""
        cache = ChromaInitCache()

        # 首次初始化（慢）
        start1 = time.perf_counter()
        db1 = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_cached"),
            collection_name="test_cached",
            enable_async=False,
            enable_cache=True
        )
        time1_ms = (time.perf_counter() - start1) * 1000

        # 第二次初始化（快，使用缓存）
        start2 = time.perf_counter()
        db2 = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_cached"),
            collection_name="test_cached",
            enable_async=False,
            enable_cache=True
        )
        time2_ms = (time.perf_counter() - start2) * 1000

        print(f"\n首次初始化: {time1_ms:.2f}ms")
        print(f"缓存命中: {time2_ms:.2f}ms")

        # 缓存命中应该更快
        assert time2_ms <= time1_ms

    def test_concurrent_init(self, tmp_path):
        """测试并发初始化"""
        results = []
        
        def init_db(name):
            start = time.perf_counter()
            db = OptimizedChromaDB(
                persist_directory=str(tmp_path / "chroma_concurrent"),
                collection_name=name,
                enable_async=False
            )
            elapsed = (time.perf_counter() - start) * 1000
            results.append(elapsed)
            return db

        threads = []
        for i in range(5):
            t = threading.Thread(target=init_db, args=(f"collection_{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(results) == 5
        print(f"\n并发初始化平均时间: {sum(results) / len(results):.2f}ms")


class TestMockImplementation:
    """模拟实现测试"""

    def test_mock_client(self):
        """测试模拟客户端"""
        client = MockChromaClient()
        collection = client.get_or_create_collection("test")

        assert collection.name == "test"

    def test_mock_collection_add(self):
        """测试模拟集合添加"""
        collection = MockCollection("test")

        collection.add(
            embeddings=[[1.0, 2.0]],
            documents=["test document"],
            ids=["id1"]
        )

        assert collection.count() == 1

    def test_mock_collection_query(self):
        """测试模拟集合查询"""
        collection = MockCollection("test")

        result = collection.query(
            query_embeddings=[[1.0, 2.0]],
            n_results=5
        )

        assert 'ids' in result
        assert 'documents' in result


class TestEdgeCases:
    """边界情况测试"""

    def test_multiple_collections(self, tmp_path):
        """测试多个集合"""
        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_multi"),
            collection_name="collection1",
            enable_async=False
        )

        # 可以创建不同集合
        db2 = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_multi"),
            collection_name="collection2",
            enable_async=False
        )

        assert db.is_initialized
        assert db2.is_initialized

    def test_error_handling(self, tmp_path):
        """测试错误处理"""
        # 测试不存在的目录（应该自动创建）
        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "nonexistent" / "path"),
            collection_name="test_error",
            enable_async=False
        )

        assert db.is_initialized

    def test_progress_callback_exception(self, tmp_path):
        """测试进度回调异常"""
        def bad_callback(progress):
            raise Exception("Callback error")

        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_cb_error"),
            collection_name="test_cb_error",
            enable_async=False,
            on_progress=bad_callback
        )

        # 应该正常完成，只是回调失败
        assert db.is_initialized


class TestIntegration:
    """集成测试"""

    def test_vector_operations(self, tmp_path):
        """测试向量操作"""
        db = OptimizedChromaDB(
            persist_directory=str(tmp_path / "chroma_vec"),
            collection_name="test_vectors",
            enable_async=False
        )

        # 添加向量
        db.add(
            embeddings=[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
            documents=["document 1", "document 2"],
            ids=["id1", "id2"]
        )

        # 查询向量
        results = db.query(
            query_embeddings=[[1.0, 2.0, 3.0]],
            n_results=1
        )

        assert results is not None

    def test_global_stats(self, tmp_path):
        """测试全局统计"""
        # 清除之前的统计
        OptimizedChromaDB._stats = ChromaInitStats()

        # 执行几次初始化
        for i in range(3):
            db = OptimizedChromaDB(
                persist_directory=str(tmp_path / f"chroma_stats_{i}"),
                collection_name=f"test_stats_{i}",
                enable_async=False
            )

        stats = OptimizedChromaDB.get_global_stats()
        
        assert stats['total_inits'] >= 3
        assert stats['successful_inits'] >= 3


class TestFactoryFunction:
    """工厂函数测试"""

    def test_create_optimized_chroma(self, tmp_path):
        """测试工厂函数"""
        db = create_optimized_chroma(
            persist_directory=str(tmp_path / "chroma_factory"),
            collection_name="test_factory",
            async_init=False
        )

        assert db.is_initialized
        assert db.persist_directory == str(tmp_path / "chroma_factory")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])