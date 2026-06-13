"""向量存储性能测试"""

import time
import pytest
from agent.memory.vector_store_optimized_v2 import VectorStoreOptimized


class TestVectorStorePerformance:
    """向量存储性能测试类"""

    def test_vector_store_init_time(self):
        """测试向量存储初始化时间"""
        start = time.perf_counter()
        store = VectorStoreOptimized(
            collection_name="test_performance",
            enable_inverted_index=True
        )
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 50.0, f"向量存储初始化时间过长: {elapsed:.2f}ms"
        print(f"向量存储初始化时间: {elapsed:.2f}ms")

    def test_add_performance(self):
        """测试添加记忆性能"""
        store = VectorStoreOptimized(enable_inverted_index=True)
        
        start = time.perf_counter()
        for i in range(100):
            content = f"这是测试内容 {i}，包含一些关键词如测试、性能、优化等。"
            store.add(content, metadata={"type": "test", "index": i})
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 500.0, f"添加记忆时间过长: {elapsed:.2f}ms"
        print(f"添加100条记忆时间: {elapsed:.2f}ms")

    def test_search_performance(self):
        """测试搜索性能"""
        store = VectorStoreOptimized(enable_inverted_index=True)
        
        # 先添加一些测试数据
        for i in range(1000):
            content = f"文档 {i}：这是关于性能优化的测试文档，包含关键词测试、搜索、索引等。"
            store.add(content, metadata={"doc_id": i})
        
        # 测试搜索性能
        start = time.perf_counter()
        for i in range(100):
            results = store.search("性能优化", top_k=5)
            assert len(results) >= 0
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 500.0, f"搜索时间过长: {elapsed:.2f}ms"
        print(f"搜索100次时间: {elapsed:.2f}ms")

    def test_search_cache_performance(self):
        """测试搜索缓存功能"""
        store = VectorStoreOptimized(enable_inverted_index=True)
        
        # 添加测试数据
        for i in range(100):
            content = f"文档 {i}：缓存测试内容，包含关键词测试和缓存"
            store.add(content)
        
        # 第一次搜索（未缓存）
        results1 = store.search("缓存测试", top_k=5)
        
        # 第二次搜索（应该命中缓存）
        results2 = store.search("缓存测试", top_k=5)
        
        # 验证两次返回相同结果
        assert len(results1) == len(results2), "缓存返回结果数量不一致"
        
        # 验证缓存统计
        stats = store._query_cache.get_stats()
        assert stats['hits'] >= 1, f"缓存命中次数不足: {stats['hits']}"
        assert stats['misses'] >= 1, f"缓存未命中次数不足: {stats['misses']}"
        
        print(f"缓存统计: 命中={stats['hits']}, 未命中={stats['misses']}, 命中率={stats['hit_rate']}%")

    def test_index_stats(self):
        """测试倒排索引统计"""
        store = VectorStoreOptimized(enable_inverted_index=True)
        
        # 添加测试数据
        for i in range(100):
            content = f"文档 {i}：测试倒排索引性能"
            store.add(content)
        
        stats = store.get_index_stats()
        assert stats is not None
        assert stats['total_docs'] == 100
        print(f"倒排索引统计: {stats}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
