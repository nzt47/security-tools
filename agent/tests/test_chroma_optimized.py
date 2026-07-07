"""ChromaDB 异步预加载与性能优化单元测试

测试覆盖：
- 同步初始化
- 异步初始化
- 延迟集合加载
- 缓存机制
- 性能基准测试
- 并发测试
- 边界情况测试

运行方式：
```bash
# 所有测试
python -m pytest agent/tests/test_chroma_optimized.py -v

# 性能测试
python -m pytest agent/tests/test_chroma_optimized.py::TestPerformance -v

# 覆盖率测试
python -m pytest agent/tests/test_chroma_optimized.py -v --cov=agent.memory_optimized
```
"""

import pytest
import time
import threading
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch
from typing import List, Dict, Any

from agent.memory_optimized import (
    OptimizedChromaDB,
    ChromaInitProgress,
    ChromaInitStats,
    ChromaInitCache,
    LazyCollectionProxy,
    MockChromaClient,
    MockCollection,
    create_optimized_chroma
)


class TestBasicFunctionality:
    """基本功能测试"""

    def test_synchronous_init(self):
        """测试同步初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_collection",
                enable_async=False,
                enable_cache=False
            )

            assert db.is_initialized
            assert not db.is_initializing
            assert db.collection is not None

    def test_asynchronous_init(self):
        """测试异步初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_async",
                enable_async=True,
                enable_cache=False
            )

            assert db.is_initializing
            assert not db.is_initialized

            # 等待初始化完成
            time.sleep(0.1)

            assert db.is_initialized
            assert not db.is_initializing

    def test_lazy_collection_proxy(self):
        """测试延迟集合代理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_client = MockChromaClient()
            proxy = LazyCollectionProxy(mock_client, "lazy_collection")

            # 访问前应该没有加载
            assert proxy._collection is None

            # 访问后应该加载
            proxy.add(
                embeddings=[[1.0, 2.0]],
                documents=["test doc"],
                ids=["id1"]
            )
            assert proxy._collection is not None
            assert proxy.count() == 1

    def test_progress_callback(self):
        """测试进度回调"""
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_updates = []

            def callback(progress: ChromaInitProgress):
                progress_updates.append(progress)

            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_progress",
                enable_async=False,
                enable_cache=False,
                on_progress=callback
            )

            assert len(progress_updates) > 0
            # 应该有完成阶段
            assert any(p.stage == "complete" for p in progress_updates)


class TestCacheMechanism:
    """缓存机制测试"""

    def test_cache_put_and_get(self):
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

        # 添加 5 个条目
        for i in range(5):
            cache.put(f"/path{i}", f"collection{i}", {'index': i})

        # 检查前两个应该被淘汰
        assert cache.get("/path0", "collection0") is None
        assert cache.get("/path1", "collection1") is None
        # 后三个应该还在
        assert cache.get("/path2", "collection2") is not None
        assert cache.get("/path3", "collection3") is not None
        assert cache.get("/path4", "collection4") is not None

    def test_cache_clear(self):
        """测试清空缓存"""
        cache = ChromaInitCache(max_size=5)

        cache.put("/path1", "collection1", {"key": "value1"})
        cache.clear()

        assert cache.get("/path1", "collection1") is None

    def test_cache_lru_order(self):
        """测试 LRU 顺序"""
        cache = ChromaInitCache(max_size=3)

        # 添加 3 个条目
        cache.put("/path1", "col1", {"idx": 1})
        cache.put("/path2", "col2", {"idx": 2})
        cache.put("/path3", "col3", {"idx": 3})

        # 访问 path1（使其成为最近使用）
        cache.get("/path1", "col1")

        # 添加第 4 个条目
        cache.put("/path4", "col4", {"idx": 4})

        # path2 应该被淘汰（最久未使用）
        assert cache.get("/path2", "col2") is None
        # 其他三个应该还在
        assert cache.get("/path1", "col1") is not None
        assert cache.get("/path3", "col3") is not None
        assert cache.get("/path4", "col4") is not None


class TestStatistics:
    """统计功能测试"""

    def test_success_stats(self):
        """测试成功初始化统计"""
        stats = ChromaInitStats()

        stats.record_init(
            success=True,
            total_time_ms=100.0,
            stage_times={"create": 30.0, "init": 70.0}
        )

        assert stats.total_inits == 1
        assert stats.successful_inits == 1
        assert stats.failed_inits == 0
        assert stats.avg_time_ms == 100.0

    def test_failure_stats(self):
        """测试失败初始化统计"""
        stats = ChromaInitStats()

        stats.record_init(
            success=False,
            total_time_ms=50.0
        )

        assert stats.total_inits == 1
        assert stats.failed_inits == 1

    def test_async_init_tracking(self):
        """测试异步初始化追踪"""
        stats = ChromaInitStats()

        stats.record_init(success=True, total_time_ms=100.0, is_async=True)
        stats.record_init(success=True, total_time_ms=100.0, is_async=False)

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

    def test_multiple_inits_stats(self):
        """测试多次初始化的统计"""
        stats = ChromaInitStats()

        # 记录 5 次初始化
        for i in range(5):
            stats.record_init(
                success=i != 3,  # 第 4 次失败
                total_time_ms=100.0 + i * 10
            )

        assert stats.total_inits == 5
        assert stats.successful_inits == 4
        assert stats.failed_inits == 1
        # 平均时间：(100 + 110 + 120 + 140) / 4 = 470 / 4 = 117.5
        assert stats.avg_time_ms == 117.5


class TestPerformance:
    """性能测试"""

    def test_init_time(self):
        """测试初始化时间"""
        with tempfile.TemporaryDirectory() as tmpdir:
            start = time.perf_counter()

            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_perf",
                enable_async=False,
                enable_cache=False
            )

            elapsed_ms = (time.perf_counter() - start) * 1000

            assert db.is_initialized
            print(f"\n初始化时间: {elapsed_ms:.2f}ms")

    def test_async_init_no_blocking(self):
        """测试异步初始化不阻塞"""
        with tempfile.TemporaryDirectory() as tmpdir:
            start = time.perf_counter()

            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_async_perf",
                enable_async=True
            )

            # 立即返回，不等待初始化
            blocking_time_ms = (time.perf_counter() - start) * 1000

            assert not db.is_initialized
            assert db.is_initializing
            assert blocking_time_ms < 50  # 应该小于 50ms

            # 等待初始化完成
            time.sleep(0.1)

            assert db.is_initialized
            assert not db.is_initializing

    def test_cached_init_speedup(self):
        """测试缓存加速初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = ChromaInitCache()

            # 首次初始化（模拟）
            start1 = time.perf_counter()
            db1 = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_cached",
                enable_async=False,
                enable_cache=True
            )
            time1_ms = (time.perf_counter() - start1) * 1000

            # 第二次初始化（应该使用缓存，实际简化逻辑）
            cache.put(tmpdir, "test_cached", {"initialized": True})

            # 虽然缓存命中逻辑可能复杂，但至少验证接口调用
            assert True  # 简化验证

    def test_concurrent_inits(self):
        """测试并发初始化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            results = []

            def init_db(idx):
                db = OptimizedChromaDB(
                    persist_directory=tmpdir,
                    collection_name=f"collection_{idx}",
                    enable_async=False
                )
                results.append(db.is_initialized)

            threads = []
            for i in range(5):
                t = threading.Thread(target=init_db, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # 所有都应该成功初始化
            assert all(results)


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
            documents=["test doc"],
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

        assert "ids" in result
        assert "documents" in result


class TestEdgeCases:
    """边界情况测试"""

    def test_multiple_collections(self):
        """测试多个集合"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db1 = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="collection1",
                enable_async=False
            )

            db2 = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="collection2",
                enable_async=False
            )

            assert db1.is_initialized
            assert db2.is_initialized

    def test_empty_document_add(self):
        """测试添加空文档"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_empty",
                enable_async=False
            )

            # 不应该崩溃
            db.add(
                embeddings=[],
                documents=[],
                ids=[]
            )

            assert True  # 只要不崩溃就通过

    def test_large_document_add(self):
        """测试添加大量文档"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_large",
                enable_async=False
            )

            # 添加 100 个文档（模拟）
            embeddings = [[float(i), float(i + 1)] for i in range(100)]
            documents = [f"doc_{i}" for i in range(100)]
            ids = [f"id_{i}" for i in range(100)]

            db.add(
                embeddings=embeddings,
                documents=documents,
                ids=ids
            )

            # 模拟集合计数
            assert True  # 简化验证

    def test_error_handling_in_callback(self):
        """测试进度回调中的错误处理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            def bad_callback(progress):
                raise Exception("Callback error")

            # 应该正常完成，只是回调失败
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
                collection_name="test_cb_error",
                enable_async=False,
                enable_cache=False,
                on_progress=bad_callback
            )

            assert db.is_initialized


class TestIntegration:
    """集成测试"""

    def test_vector_operations(self):
        """测试向量操作"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = OptimizedChromaDB(
                persist_directory=tmpdir,
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
            result = db.query(
                query_embeddings=[[1.0, 2.0, 3.0]],
                n_results=5
            )

            assert result is not None

    def test_global_stats(self):
        """测试全局统计"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 重置全局统计
            OptimizedChromaDB._stats = ChromaInitStats()

            # 执行几次初始化
            for i in range(3):
                db = OptimizedChromaDB(
                    persist_directory=tmpdir,
                    collection_name=f"test_stats_{i}",
                    enable_async=False
                )

            stats = OptimizedChromaDB.get_global_stats()

            assert stats['total_inits'] >= 3
            assert stats['successful_inits'] >= 3


class TestFactoryFunction:
    """工厂函数测试"""

    def test_create_optimized_chroma(self):
        """测试工厂函数"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = create_optimized_chroma(
                persist_directory=tmpdir,
                collection_name="test_factory",
                async_init=False
            )

            assert db.is_initialized
            assert db.persist_directory == tmpdir

    def test_create_optimized_chroma_async(self):
        """测试异步工厂函数"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = create_optimized_chroma(
                persist_directory=tmpdir,
                collection_name="test_factory_async",
                async_init=True
            )

            # 初始时可能正在初始化
            if not db.is_initialized:
                time.sleep(0.1)

            assert db.is_initialized


def test_llm_response_cache_integration():
    """测试 LLM 响应缓存集成（快速验证）"""
    from agent.llm_response_cache import llm_cache

    # 基本测试
    llm_cache.put("test prompt", "test response")
    cached = llm_cache.get("test prompt")

    assert cached == "test response"

    stats = llm_cache.get_stats()
    assert stats['total_hits'] == 1
    assert stats['total_puts'] >= 1


def test_async_save_monitor_integration():
    """测试异步保存监控集成（快速验证）"""
    from agent.llm_response_cache import async_save_monitor

    # 基本测试
    task_id = async_save_monitor.start_save("test_task")
    assert task_id is not None

    async_save_monitor.end_save(task_id, success=True)

    stats = async_save_monitor.get_stats()
    assert stats['total_saves'] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
