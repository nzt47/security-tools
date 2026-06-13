"""
LazyLoader 综合测试 - 覆盖所有核心功能
目标：将覆盖率从 0% 提升至 90%+
"""
import pytest
import time
from unittest.mock import MagicMock, patch, call
from agent.lazy_loader import (
    LoadLevel,
    ModuleInfo,
    LoadStats,
    LazyModuleLoader,
    lazy_load,
    ParallelPreloader,
    get_lazy_loader,
)


class TestLoadLevel:
    """测试加载级别枚举"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_level_values(self):
        """测试加载级别值"""
        assert LoadLevel.CRITICAL == 0
        assert LoadLevel.IMPORTANT == 1
        assert LoadLevel.OPTIONAL == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_level_names(self):
        """测试加载级别名称"""
        assert LoadLevel.CRITICAL.name == "CRITICAL"
        assert LoadLevel.IMPORTANT.name == "IMPORTANT"
        assert LoadLevel.OPTIONAL.name == "OPTIONAL"


class TestLoadStats:
    """测试加载统计类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_stats_initialization(self):
        """测试加载统计初始化"""
        stats = LoadStats()
        assert stats.total_attempts == 0
        assert stats.successful_loads == 0
        assert stats.failed_loads == 0
        assert stats.total_load_time_ms == 0.0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_load_success(self):
        """测试记录成功加载"""
        stats = LoadStats()
        stats.record_load(LoadLevel.CRITICAL, True, 100.0)
        
        assert stats.total_attempts == 1
        assert stats.successful_loads == 1
        assert stats.failed_loads == 0
        assert stats.total_load_time_ms == 100.0
        assert stats.by_level[LoadLevel.CRITICAL] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_record_load_failure(self):
        """测试记录失败加载"""
        stats = LoadStats()
        stats.record_load(LoadLevel.IMPORTANT, False, 50.0)
        
        assert stats.total_attempts == 1
        assert stats.successful_loads == 0
        assert stats.failed_loads == 1
        assert stats.by_level[LoadLevel.IMPORTANT] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_avg_load_time(self):
        """测试获取平均加载时间"""
        stats = LoadStats()
        assert stats.get_avg_load_time() == 0.0
        
        stats.record_load(LoadLevel.CRITICAL, True, 100.0)
        stats.record_load(LoadLevel.IMPORTANT, True, 200.0)
        assert stats.get_avg_load_time() == 150.0


class TestLazyModuleLoader:
    """测试懒加载器核心功能"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_initialization(self):
        """测试懒加载器初始化"""
        loader = LazyModuleLoader(max_workers=2)
        assert len(loader.modules) == 0
        assert loader.executor._max_workers == 2
        assert loader.loaded_levels == set()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_module(self):
        """测试注册模块"""
        loader = LazyModuleLoader()
        
        def sample_loader():
            return "loaded_module"
        
        loader.register("test_module", sample_loader, LoadLevel.CRITICAL)
        
        assert "test_module" in loader.modules
        assert loader.modules["test_module"].name == "test_module"
        assert loader.modules["test_module"].level == LoadLevel.CRITICAL
        assert not loader.modules["test_module"].loaded

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_duplicate_module(self):
        """测试注册重复模块"""
        loader = LazyModuleLoader()
        
        def loader1():
            return "version1"
        
        def loader2():
            return "version2"
        
        loader.register("test_module", loader1, LoadLevel.CRITICAL)
        loader.register("test_module", loader2, LoadLevel.IMPORTANT)
        
        assert loader.modules["test_module"].level == LoadLevel.IMPORTANT

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_with_dependencies(self):
        """测试注册带依赖的模块"""
        loader = LazyModuleLoader()
        
        def main_loader():
            return "main"
        
        loader.register("main_module", main_loader, LoadLevel.IMPORTANT, dependencies=["dep1", "dep2"])
        
        assert loader.modules["main_module"].dependencies == ["dep1", "dep2"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_level(self):
        """测试加载指定级别"""
        loader = LazyModuleLoader()
        loaded_flag = [False]
        
        def critical_loader():
            loaded_flag[0] = True
            return "critical_module"
        
        loader.register("critical", critical_loader, LoadLevel.CRITICAL)
        
        results = loader.load_level(LoadLevel.CRITICAL)
        
        assert "critical" in results
        assert results["critical"] == "critical_module"
        assert loader.modules["critical"].loaded is True
        assert LoadLevel.CRITICAL in loader.loaded_levels

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_level_twice(self):
        """测试重复加载同一级别"""
        loader = LazyModuleLoader()
        call_count = [0]
        
        def test_loader():
            call_count[0] += 1
            return "module"
        
        loader.register("test", test_loader, LoadLevel.CRITICAL)
        
        loader.load_level(LoadLevel.CRITICAL)
        loader.load_level(LoadLevel.CRITICAL)
        
        assert call_count[0] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_level_with_dependencies(self):
        """测试加载带依赖的模块"""
        loader = LazyModuleLoader()
        dep_loaded = [False]
        
        def dep_loader():
            dep_loaded[0] = True
            return "dependency"
        
        def main_loader():
            return "main"
        
        loader.register("dependency", dep_loader, LoadLevel.CRITICAL)
        loader.register("main", main_loader, LoadLevel.IMPORTANT, dependencies=["dependency"])
        
        loader.load_level(LoadLevel.CRITICAL)
        results = loader.load_level(LoadLevel.IMPORTANT)
        
        assert dep_loaded[0] is True
        assert "main" in results

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_level_failure(self):
        """测试模块加载失败"""
        loader = LazyModuleLoader()
        
        def failing_loader():
            raise ValueError("加载失败")
        
        loader.register("failing", failing_loader, LoadLevel.CRITICAL)
        
        results = loader.load_level(LoadLevel.CRITICAL)
        
        assert "failing" not in results
        assert loader.modules["failing"].loaded is False
        assert loader.modules["failing"].error == "加载失败"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_single_module(self):
        """测试按需加载单个模块"""
        loader = LazyModuleLoader()
        
        def test_loader():
            return "loaded"
        
        loader.register("test", test_loader, LoadLevel.OPTIONAL)
        
        result = loader.load("test")
        
        assert result == "loaded"
        assert loader.modules["test"].loaded is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_nonexistent_module(self):
        """测试加载不存在的模块"""
        loader = LazyModuleLoader()
        
        result = loader.load("nonexistent")
        
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_with_dependencies(self):
        """测试加载带依赖的模块"""
        loader = LazyModuleLoader()
        dep_loaded = [False]
        
        def dep_loader():
            dep_loaded[0] = True
            return "dep"
        
        def main_loader():
            return "main"
        
        loader.register("dep", dep_loader, LoadLevel.CRITICAL)
        loader.register("main", main_loader, LoadLevel.OPTIONAL, dependencies=["dep"])
        
        result = loader.load("main")
        
        assert dep_loaded[0] is True
        assert result == "main"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_async(self):
        """测试异步加载级别"""
        loader = LazyModuleLoader()
        loaded_flag = [False]
        
        def slow_loader():
            time.sleep(0.1)
            loaded_flag[0] = True
            return "async_module"
        
        loader.register("async", slow_loader, LoadLevel.IMPORTANT)
        
        loader.load_level_async(LoadLevel.IMPORTANT)
        
        time.sleep(0.2)
        
        assert loaded_flag[0] is True
        assert loader.modules["async"].loaded is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_load(self):
        """测试判断是否应该加载"""
        loader = LazyModuleLoader()
        
        def test_loader():
            return "test"
        
        loader.register("test", test_loader, LoadLevel.OPTIONAL)
        
        assert loader.should_load("test") is True
        
        loader.load("test")
        
        assert loader.should_load("test") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_load_with_unloaded_dependency(self):
        """测试有未加载依赖时的should_load"""
        loader = LazyModuleLoader()
        
        def dep_loader():
            return "dep"
        
        def main_loader():
            return "main"
        
        loader.register("dep", dep_loader, LoadLevel.CRITICAL)
        loader.register("main", main_loader, LoadLevel.OPTIONAL, dependencies=["dep"])
        
        assert loader.should_load("main") is False
        
        loader.load("dep")
        
        assert loader.should_load("main") is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_module(self):
        """测试获取已加载模块"""
        loader = LazyModuleLoader()
        
        def test_loader():
            return "test_instance"
        
        loader.register("test", test_loader, LoadLevel.CRITICAL)
        loader.load("test")
        
        result = loader.get_module("test")
        
        assert result == "test_instance"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_loaded(self):
        """测试检查模块是否已加载"""
        loader = LazyModuleLoader()
        
        def test_loader():
            return "test"
        
        loader.register("test", test_loader, LoadLevel.CRITICAL)
        
        assert loader.is_loaded("test") is False
        
        loader.load("test")
        
        assert loader.is_loaded("test") is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_is_level_loaded(self):
        """测试检查级别是否已加载"""
        loader = LazyModuleLoader()
        
        def test_loader():
            return "test"
        
        loader.register("test", test_loader, LoadLevel.CRITICAL)
        
        assert loader.is_level_loaded(LoadLevel.CRITICAL) is False
        
        loader.load_level(LoadLevel.CRITICAL)
        
        assert loader.is_level_loaded(LoadLevel.CRITICAL) is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_stats(self):
        """测试获取加载统计"""
        loader = LazyModuleLoader()
        
        def test_loader():
            return "test"
        
        loader.register("test", test_loader, LoadLevel.CRITICAL)
        loader.load("test")
        
        stats = loader.get_stats()
        
        assert stats["total_attempts"] == 1
        assert stats["successful_loads"] == 1
        assert stats["failed_loads"] == 0
        assert "test" in stats["modules"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reset(self):
        """测试重置所有模块状态"""
        loader = LazyModuleLoader()
        
        def test_loader():
            return "test"
        
        loader.register("test", test_loader, LoadLevel.CRITICAL)
        loader.load("test")
        
        assert loader.modules["test"].loaded is True
        
        loader.reset()
        
        assert loader.modules["test"].loaded is False
        assert loader.modules["test"].instance is None
        assert loader.loaded_levels == set()


class TestLazyLoadDecorator:
    """测试懒加载装饰器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_lazy_load_decorator(self):
        """测试懒加载装饰器"""
        @lazy_load(LoadLevel.CRITICAL)
        def my_module():
            return "my_module_instance"
        
        assert hasattr(my_module, '_lazy_load_level')
        assert my_module._lazy_load_level == LoadLevel.CRITICAL


class TestParallelPreloader:
    """测试并行预加载器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_preloader_initialization(self):
        """测试预加载器初始化"""
        preloader = ParallelPreloader(max_workers=4)
        assert preloader.executor._max_workers == 4

    @pytest.mark.unit
    @pytest.mark.p0
    def test_preload_modules(self):
        """测试并行预加载模块"""
        preloader = ParallelPreloader(max_workers=2)
        
        def loader1():
            return "module1"
        
        def loader2():
            return "module2"
        
        results = preloader.preload([("m1", loader1), ("m2", loader2)])
        
        assert "m1" in results
        assert "m2" in results
        assert results["m1"] == "module1"
        assert results["m2"] == "module2"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_preload_with_timeout(self):
        """测试预加载超时处理"""
        preloader = ParallelPreloader(max_workers=1)
        
        def slow_loader():
            time.sleep(0.5)
            return "slow"
        
        results = preloader.preload([("slow", slow_loader)])
        
        assert "slow" in results

    @pytest.mark.unit
    @pytest.mark.p0
    def test_preload_failure(self):
        """测试预加载失败"""
        preloader = ParallelPreloader(max_workers=1)
        
        def failing_loader():
            raise ValueError("失败")
        
        results = preloader.preload([("failing", failing_loader)])
        
        assert "failing" not in results

    @pytest.mark.unit
    @pytest.mark.p0
    def test_shutdown(self):
        """测试关闭预加载器"""
        preloader = ParallelPreloader(max_workers=2)
        preloader.shutdown()


class TestGlobalLoader:
    """测试全局懒加载器"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_lazy_loader_singleton(self):
        """测试全局懒加载器单例"""
        loader1 = get_lazy_loader()
        loader2 = get_lazy_loader()
        
        assert loader1 is loader2