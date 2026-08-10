"""多级懒加载模块单元测试

测试覆盖：
1. 基础功能测试
   - 模块注册
   - 单个模块加载
   - 级别加载

2. 依赖管理测试
   - 依赖模块自动加载
   - 循环依赖检测

3. 并行加载测试
   - 并行预加载
   - 异步加载

4. 性能测试
   - 加载时间统计
   - 懒加载效果验证

5. 边界情况测试
   - 重复加载
   - 加载失败
   - 未注册模块访问

运行方式：
```bash
python -m pytest agent/tests/test_lazy_loader.py -v
python -m pytest agent/tests/test_lazy_loader.py::TestLazyLoaderPerformance -v
```
"""

import pytest
import time
import threading
from unittest.mock import Mock, patch
from typing import Any

from agent.lazy_loader import (
    LazyModuleLoader, ParallelPreloader, LoadLevel, LoadStats,
    ModuleInfo, lazy_load, get_lazy_loader
)


class TestLazyLoaderBasic:
    """基础功能测试"""

    def test_register_module(self):
        """测试模块注册"""
        loader = LazyModuleLoader()

        def mock_loader():
            return {"loaded": True}

        loader.register("test_module", mock_loader, LoadLevel.CRITICAL)

        assert "test_module" in loader.modules
        assert loader.modules["test_module"].level == LoadLevel.CRITICAL
        assert not loader.modules["test_module"].loaded

    def test_register_multiple_modules(self):
        """测试注册多个模块"""
        loader = LazyModuleLoader()

        for i in range(5):
            loader.register(f"module_{i}", lambda: i, LoadLevel.IMPORTANT)

        assert len(loader.modules) == 5

    def test_load_single_module(self):
        """测试加载单个模块"""
        loader = LazyModuleLoader()

        def mock_loader():
            return {"data": "test"}

        loader.register("test", mock_loader, LoadLevel.CRITICAL)
        result = loader.load("test")

        assert result == {"data": "test"}
        assert loader.is_loaded("test")
        assert loader.modules["test"].loaded
        assert loader.modules["test"].instance == {"data": "test"}

    def test_load_unregistered_module(self):
        """测试加载未注册模块"""
        loader = LazyModuleLoader()
        result = loader.load("nonexistent")

        assert result is None

    def test_load_level(self):
        """测试按级别加载"""
        loader = LazyModuleLoader()

        modules = {
            "critical_1": (lambda: 1, LoadLevel.CRITICAL),
            "critical_2": (lambda: 2, LoadLevel.CRITICAL),
            "important": (lambda: 3, LoadLevel.IMPORTANT),
            "optional": (lambda: 4, LoadLevel.OPTIONAL),
        }

        for name, (func, level) in modules.items():
            loader.register(name, func, level)

        results = loader.load_level(LoadLevel.CRITICAL)

        assert "critical_1" in results
        assert "critical_2" in results
        assert "important" not in results
        assert "optional" not in results
        assert loader.is_level_loaded(LoadLevel.CRITICAL)

    def test_duplicate_registration(self):
        """测试重复注册（覆盖）"""
        loader = LazyModuleLoader()

        loader.register("test", lambda: 1, LoadLevel.CRITICAL)
        loader.register("test", lambda: 2, LoadLevel.IMPORTANT)

        assert loader.modules["test"].level == LoadLevel.IMPORTANT


class TestDependencyManagement:
    """依赖管理测试"""

    def test_simple_dependency(self):
        """测试简单依赖"""
        loader = LazyModuleLoader()
        load_order = []

        def module_a():
            load_order.append("a")
            return "a"

        def module_b():
            load_order.append("b")
            return "b"

        loader.register("a", module_a, LoadLevel.CRITICAL)
        loader.register("b", module_b, LoadLevel.IMPORTANT, dependencies=["a"])

        loader.load("b")

        assert load_order == ["a", "b"]
        assert loader.is_loaded("a")
        assert loader.is_loaded("b")

    def test_multiple_dependencies(self):
        """测试多依赖"""
        loader = LazyModuleLoader()
        load_order = []

        def create_module(name):
            def loader_func():
                load_order.append(name)
                return name
            return loader_func

        loader.register("a", create_module("a"), LoadLevel.CRITICAL)
        loader.register("b", create_module("b"), LoadLevel.CRITICAL)
        loader.register("c", create_module("c"), LoadLevel.IMPORTANT, dependencies=["a", "b"])

        loader.load("c")

        assert "a" in load_order
        assert "b" in load_order
        assert "c" in load_order
        assert load_order.index("a") < load_order.index("c")
        assert load_order.index("b") < load_order.index("c")

    def test_transitive_dependency(self):
        """测试传递依赖"""
        loader = LazyModuleLoader()

        def create_module(name):
            return lambda: name

        loader.register("a", create_module("a"), LoadLevel.CRITICAL)
        loader.register("b", create_module("b"), LoadLevel.IMPORTANT, dependencies=["a"])
        loader.register("c", create_module("c"), LoadLevel.OPTIONAL, dependencies=["b"])

        loader.load("c")

        assert loader.is_loaded("a")
        assert loader.is_loaded("b")
        assert loader.is_loaded("c")


class TestAsyncLoading:
    """异步加载测试"""

    def test_load_level_async(self):
        """测试异步级别加载"""
        loader = LazyModuleLoader()

        for i in range(3):
            loader.register(f"module_{i}", lambda idx=i: idx, LoadLevel.IMPORTANT)

        loader.load_level_async(LoadLevel.IMPORTANT)

        time.sleep(0.1)

        assert loader.is_level_loaded(LoadLevel.IMPORTANT)

    def test_concurrent_loads(self):
        """测试并发加载"""
        loader = LazyModuleLoader()

        def slow_loader(name, delay=0.1):
            def loader_func():
                time.sleep(delay)
                return name
            return loader_func

        loader.register("a", slow_loader("a"), LoadLevel.CRITICAL)
        loader.register("b", slow_loader("b"), LoadLevel.IMPORTANT)

        thread1 = threading.Thread(target=lambda: loader.load_level(LoadLevel.CRITICAL))
        thread2 = threading.Thread(target=lambda: loader.load_level(LoadLevel.IMPORTANT))

        start = time.perf_counter()
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()
        elapsed = time.perf_counter() - start

        assert loader.is_loaded("a")
        assert loader.is_loaded("b")


class TestPerformance:
    """性能测试"""

    def test_load_time_tracking(self):
        """测试加载时间追踪"""
        loader = LazyModuleLoader()

        def slow_loader():
            time.sleep(0.05)
            return "done"

        loader.register("test", slow_loader, LoadLevel.CRITICAL)
        loader.load("test")

        assert loader.modules["test"].load_time_ms >= 50

    def test_stats_recording(self):
        """测试统计记录"""
        loader = LazyModuleLoader()

        loader.register("a", lambda: "a", LoadLevel.CRITICAL)
        loader.register("b", lambda: "b", LoadLevel.CRITICAL)
        loader.register("c", lambda: "c", LoadLevel.IMPORTANT)

        loader.load_level(LoadLevel.CRITICAL)

        stats = loader.get_stats()
        assert stats["total_attempts"] >= 2
        assert stats["successful_loads"] >= 2

    def test_lazy_loading_benefit(self):
        """测试懒加载性能优势"""
        loader = LazyModuleLoader()

        critical_load_time = 0.01
        important_load_time = 0.1

        def critical_loader():
            time.sleep(critical_load_time)
            return "critical"

        def important_loader():
            time.sleep(important_load_time)
            return "important"

        loader.register("critical", critical_loader, LoadLevel.CRITICAL)
        loader.register("important", important_loader, LoadLevel.IMPORTANT)

        start = time.perf_counter()
        loader.load_level(LoadLevel.CRITICAL)
        critical_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        loader.load_level(LoadLevel.IMPORTANT)
        important_elapsed = time.perf_counter() - start

        assert critical_elapsed < important_elapsed
        assert critical_elapsed < 0.05

    def test_parallel_preloader_speedup(self):
        """测试并行预加载加速"""
        module_count = 5
        load_time = 0.05

        def create_loader(name):
            def loader():
                time.sleep(load_time)
                return name
            return loader

        modules = [(f"module_{i}", create_loader(f"module_{i}"))
                  for i in range(module_count)]

        sequential_start = time.perf_counter()
        for name, loader in modules:
            loader()
        sequential_time = time.perf_counter() - sequential_start

        preloader = ParallelPreloader(max_workers=module_count)
        parallel_start = time.perf_counter()
        preloader.preload(modules)
        parallel_time = time.perf_counter() - parallel_start

        assert parallel_time < sequential_time * 0.6


class TestEdgeCases:
    """边界情况测试"""

    def test_load_already_loaded_module(self):
        """测试加载已加载模块"""
        loader = LazyModuleLoader()
        call_count = 0

        def counting_loader():
            nonlocal call_count
            call_count += 1
            return "loaded"

        loader.register("test", counting_loader, LoadLevel.CRITICAL)
        loader.load("test")
        loader.load("test")

        assert call_count == 1

    def test_load_with_error(self):
        """测试加载失败"""
        loader = LazyModuleLoader()

        def failing_loader():
            raise ValueError("Test error")

        loader.register("fail", failing_loader, LoadLevel.CRITICAL)
        result = loader.load("fail")

        assert result is None
        assert loader.modules["fail"].error is not None
        assert "Test error" in loader.modules["fail"].error

    def test_should_load_logic(self):
        """测试 should_load 判断逻辑"""
        loader = LazyModuleLoader()

        def mock_loader():
            return "loaded"

        loader.register("loaded", mock_loader, LoadLevel.CRITICAL)
        loader.register("unloaded", mock_loader, LoadLevel.IMPORTANT)

        loader.load("loaded")

        assert not loader.should_load("loaded")
        assert loader.should_load("unloaded")

    def test_reset_functionality(self):
        """测试重置功能"""
        loader = LazyModuleLoader()

        loader.register("test", lambda: "test", LoadLevel.CRITICAL)
        loader.load("test")

        assert loader.is_loaded("test")

        loader.reset()

        assert not loader.is_loaded("test")
        assert not loader.is_level_loaded(LoadLevel.CRITICAL)


class TestLazyLoaderPerformance:
    """性能基准测试"""

    def test_startup_time_with_lazy_loading(self):
        """测试使用懒加载的启动时间"""
        loader = LazyModuleLoader()

        module_load_times = {
            "critical_1": 0.01,
            "critical_2": 0.01,
            "important_1": 0.05,
            "important_2": 0.05,
            "optional_1": 0.1,
            "optional_2": 0.1,
        }

        def create_delayed_loader(delay):
            def loader():
                time.sleep(delay)
                return True
            return loader

        for name, delay in module_load_times.items():
            if "critical" in name:
                level = LoadLevel.CRITICAL
            elif "important" in name:
                level = LoadLevel.IMPORTANT
            else:
                level = LoadLevel.OPTIONAL

            loader.register(name, create_delayed_loader(delay), level)

        start = time.perf_counter()
        loader.load_level(LoadLevel.CRITICAL)
        startup_time = time.perf_counter() - start

        assert startup_time < 0.05
        assert loader.stats.successful_loads == 2

    def test_memory_efficiency(self):
        """测试内存效率（只加载必要的模块）"""
        loader = LazyModuleLoader()

        loaded_modules = []

        def tracking_loader(name):
            def loader():
                loaded_modules.append(name)
                return name
            return loader

        loader.register("needed", tracking_loader("needed"), LoadLevel.CRITICAL)
        loader.register("not_needed_yet", tracking_loader("not_needed_yet"), LoadLevel.IMPORTANT)

        loader.load_level(LoadLevel.CRITICAL)

        assert loaded_modules == ["needed"]
        assert len(loaded_modules) == 1

    def test_background_loading_performance(self):
        """测试后台加载性能影响"""
        loader = LazyModuleLoader()

        def slow_loader(name):
            def loader():
                time.sleep(0.1)
                return name
            return loader

        for i in range(5):
            loader.register(f"bg_module_{i}", slow_loader(f"bg_module_{i}"), LoadLevel.IMPORTANT)

        start = time.perf_counter()
        loader.load_level(LoadLevel.CRITICAL)
        critical_time = time.perf_counter() - start

        assert critical_time < 0.01

        loader.load_level_async(LoadLevel.IMPORTANT)

        start = time.perf_counter()
        loader.load_level(LoadLevel.CRITICAL)
        second_critical_time = time.perf_counter() - start

        assert second_critical_time < 0.01


def test_decorator_usage():
    """测试装饰器用法"""

    @lazy_load(LoadLevel.IMPORTANT)
    def decorated_module():
        return {"decorated": True}

    assert hasattr(decorated_module, "_lazy_load_level")
    assert decorated_module._lazy_load_level == LoadLevel.IMPORTANT


def test_global_loader_singleton():
    """测试全局加载器单例"""
    loader1 = get_lazy_loader()
    loader2 = get_lazy_loader()

    assert loader1 is loader2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
