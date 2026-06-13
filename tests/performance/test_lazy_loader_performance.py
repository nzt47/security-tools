"""懒加载性能测试"""

import time
import asyncio
import pytest
from agent.lazy_loader_async import AsyncLazyModuleLoader, LoadLevel


class TestLazyLoaderPerformance:
    """懒加载性能测试类"""

    def test_lazy_loader_init_time(self):
        """测试懒加载器初始化时间"""
        start = time.perf_counter()
        loader = AsyncLazyModuleLoader(max_workers=4)
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 10.0, f"懒加载器初始化时间过长: {elapsed:.2f}ms"
        print(f"懒加载器初始化时间: {elapsed:.2f}ms")

    def test_sync_module_load_time(self):
        """测试同步模块加载时间"""
        loader = AsyncLazyModuleLoader(max_workers=4)
        
        def slow_loader():
            time.sleep(0.05)
            return "loaded"
        
        loader.register('slow_module', slow_loader, LoadLevel.CRITICAL)
        
        start = time.perf_counter()
        result = loader.load_sync('slow_module')
        elapsed = (time.perf_counter() - start) * 1000
        
        assert result == "loaded"
        assert elapsed < 100.0, f"同步加载时间过长: {elapsed:.2f}ms"
        print(f"同步模块加载时间: {elapsed:.2f}ms")

    @pytest.mark.asyncio
    async def test_async_module_load_time(self):
        """测试异步模块加载时间"""
        loader = AsyncLazyModuleLoader(max_workers=4)
        
        async def async_slow_loader():
            await asyncio.sleep(0.05)
            return "async_loaded"
        
        def sync_slow_loader():
            time.sleep(0.05)
            return "sync_loaded"
        
        loader.register('async_module', sync_slow_loader, LoadLevel.IMPORTANT, async_loader_func=async_slow_loader)
        
        start = time.perf_counter()
        result = await loader.load('async_module')
        elapsed = (time.perf_counter() - start) * 1000
        
        assert result == "async_loaded"
        assert elapsed < 100.0, f"异步加载时间过长: {elapsed:.2f}ms"
        print(f"异步模块加载时间: {elapsed:.2f}ms")

    @pytest.mark.asyncio
    async def test_parallel_module_load(self):
        """测试并行模块加载性能"""
        loader = AsyncLazyModuleLoader(max_workers=4)
        
        def create_loader(delay_ms):
            def loader_func():
                time.sleep(delay_ms / 1000)
                return f"module_{delay_ms}"
            return loader_func
        
        # 注册多个慢加载模块
        for i in range(4):
            loader.register(f'module_{i}', create_loader(50), LoadLevel.IMPORTANT)
        
        start = time.perf_counter()
        await loader.load_level(LoadLevel.IMPORTANT)
        elapsed = (time.perf_counter() - start) * 1000
        
        # 并行加载应该比串行快很多
        assert elapsed < 150.0, f"并行加载时间过长: {elapsed:.2f}ms"
        print(f"并行加载4个模块时间: {elapsed:.2f}ms")

    def test_module_registration_time(self):
        """测试模块注册时间"""
        loader = AsyncLazyModuleLoader(max_workers=4)
        
        start = time.perf_counter()
        for i in range(100):
            loader.register(f'module_{i}', lambda: None, LoadLevel.OPTIONAL)
        elapsed = (time.perf_counter() - start) * 1000
        
        assert elapsed < 50.0, f"模块注册时间过长: {elapsed:.2f}ms"
        print(f"注册100个模块时间: {elapsed:.2f}ms")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
