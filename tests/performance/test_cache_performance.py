"""缓存性能测试"""

import time
import pytest
from agent.caching import MultiLevelCache, LRUCache


class TestCachePerformance:
    """缓存性能测试类"""

    def test_cache_init_time(self):
        """测试缓存初始化时间"""
        start = time.perf_counter()
        cache = MultiLevelCache(
            l1_max_size=1000,
            l1_ttl=300,
            l2_enabled=False
        )
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 10.0, f"缓存初始化时间过长: {elapsed:.2f}ms"
        print(f"缓存初始化时间: {elapsed:.2f}ms")

    def test_l1_cache_get_set(self):
        """测试L1缓存读写性能"""
        cache = MultiLevelCache(l2_enabled=False)
        
        # 测试写入性能
        start = time.perf_counter()
        for i in range(1000):
            cache.set(f'key_{i}', f'value_{i}_' * 100)
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 100.0, f"缓存写入时间过长: {elapsed:.2f}ms"
        print(f"写入1000条缓存时间: {elapsed:.2f}ms")
        
        # 测试读取性能（缓存命中）
        start = time.perf_counter()
        for i in range(1000):
            result = cache.get(f'key_{i}')
            assert result == f'value_{i}_' * 100
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 50.0, f"缓存读取时间过长: {elapsed:.2f}ms"
        print(f"读取1000条缓存时间: {elapsed:.2f}ms")

    def test_cache_hit_rate(self):
        """测试缓存命中率"""
        cache = MultiLevelCache(l2_enabled=False)
        
        # 写入一些缓存
        for i in range(100):
            cache.set(f'key_{i}', f'value_{i}')
        
        # 重复读取
        for i in range(1000):
            cache.get(f'key_{i % 100}')
        
        stats = cache.get_stats()
        hit_rate = float(stats['hit_rate'].replace('%', ''))
        
        assert hit_rate >= 90.0, f"缓存命中率过低: {hit_rate}%"
        print(f"缓存命中率: {hit_rate}%")

    def test_lru_eviction(self):
        """测试LRU淘汰策略"""
        cache = LRUCache(max_size=100, ttl_seconds=300)
        
        # 写入超过容量的缓存
        for i in range(200):
            cache.set(f'key_{i}', f'value_{i}')
        
        # 前100个应该被淘汰
        assert cache.get('key_0') is None
        # 后100个应该保留
        assert cache.get('key_150') == f'value_150'
        
        print("LRU淘汰策略测试通过")

    def test_cache_concurrent_access(self):
        """测试缓存并发访问性能"""
        import threading
        
        cache = MultiLevelCache(l2_enabled=False)
        num_threads = 10
        num_operations = 100
        
        def worker():
            for i in range(num_operations):
                cache.set(f'thread_key_{threading.current_thread().ident}_{i}', 'value')
                cache.get(f'thread_key_{threading.current_thread().ident}_{i}')
        
        threads = []
        start = time.perf_counter()
        
        for _ in range(num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 500.0, f"并发访问时间过长: {elapsed:.2f}ms"
        print(f"{num_threads}线程并发{num_operations}次操作时间: {elapsed:.2f}ms")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
