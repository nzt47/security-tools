"""向量存储性能测试"""

import time
import tempfile
import pytest
from memory.vector_store import VectorStore


@pytest.fixture
def tmp_store():
    """返回一个使用临时目录的 VectorStore 实例"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = VectorStore(
            collection_name="perf_test",
            persist_dir=tmpdir,
            enable_inverted_index=True,
            cache_size=100,
        )
        yield store


class TestVectorStorePerformance:
    """向量存储性能测试类"""

    def test_vector_store_init_time(self):
        """测试向量存储初始化时间"""
        with tempfile.TemporaryDirectory() as tmpdir:
            start = time.perf_counter()
            store = VectorStore(
                collection_name="perf_init",
                persist_dir=tmpdir,
                enable_inverted_index=True,
            )
            elapsed = (time.perf_counter() - start) * 1000
            assert isinstance(store, VectorStore)
            print(f"向量存储初始化时间: {elapsed:.2f}ms")

    def test_add_performance(self):
        """测试添加记忆性能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(
                collection_name="perf_add",
                persist_dir=tmpdir,
                enable_inverted_index=True,
            )

            start = time.perf_counter()
            for i in range(100):
                content = f"test content {i} with some keywords for search"
                store.add(content, metadata={"type": "test", "index": i})
            elapsed = (time.perf_counter() - start) * 1000

            assert store.count == 100
            print(f"添加100条记忆时间: {elapsed:.2f}ms")

    def test_search_performance(self):
        """测试搜索性能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(
                collection_name="perf_search",
                persist_dir=tmpdir,
                enable_inverted_index=True,
            )

            # 先添加测试数据
            for i in range(500):
                content = f"document {i}: testing search performance with BM25 index"
                store.add(content, metadata={"doc_id": i})

            # 测试搜索性能
            start = time.perf_counter()
            for i in range(100):
                results = store.search("testing BM25 search", top_k=5)
                assert len(results) >= 0
            elapsed = (time.perf_counter() - start) * 1000

            print(f"搜索100次时间: {elapsed:.2f}ms")

    def test_search_cache_performance(self):
        """测试搜索缓存功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(
                collection_name="perf_cache",
                persist_dir=tmpdir,
                enable_inverted_index=True,
                cache_size=100,
                cache_ttl=300,
            )

            # 添加测试数据
            for i in range(100):
                content = f"document {i}: cache test content with keywords"
                store.add(content)

            # 第一次搜索（未缓存）
            results1 = store.search("cache test", top_k=5)

            # 第二次搜索（应该命中缓存）
            results2 = store.search("cache test", top_k=5)

            # 验证两次返回相同结果
            assert len(results1) == len(results2), "缓存返回结果数量不一致"

            # 验证缓存统计
            stats = store.get_cache_stats()
            assert stats['hits'] >= 1, f"缓存命中次数不足: {stats['hits']}"
            assert stats['misses'] >= 1, f"缓存未命中次数不足: {stats['misses']}"

            print(f"缓存统计: 命中={stats['hits']}, 未命中={stats['misses']}, 命中率={stats['hit_rate']}%")

    def test_index_stats(self):
        """测试倒排索引统计"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(
                collection_name="perf_index",
                persist_dir=tmpdir,
                enable_inverted_index=True,
            )

            # 添加测试数据
            for i in range(100):
                content = f"document {i}: testing inverted index performance"
                store.add(content)

            stats = store.get_index_stats()
            assert stats is not None
            assert stats['total_docs'] == 100, f"期待 100 条，实际 {stats['total_docs']}"
            print(f"倒排索引统计: {stats}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
