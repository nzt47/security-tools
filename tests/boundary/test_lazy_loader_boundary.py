"""lazy_loader 边界测试

覆盖场景：empty / invalid / timeout / null / extreme
被测模块：agent.lazy_loader (LazyModuleLoader) + agent.lazy_loader_async (AsyncLazyModuleLoader)

【生成日志摘要】
- 生成时间：2026-07-01
- 版本：v1.0.0
- 内容：BT-011 lazy_loader 边界测试，覆盖 5 类边界场景，10 个测试用例
- 关键场景：空级别加载、未注册模块加载、loader_func 异常、慢加载、None 输入、大量模块注册
"""
import asyncio
import pytest
import time

from agent.lazy_loader import (
    LazyModuleLoader,
    LoadLevel,
    ParallelPreloader,
    get_lazy_loader,
)
from agent.lazy_loader_async import (
    AsyncLazyModuleLoader,
    AsyncParallelPreloader,
    get_async_lazy_loader,
)


# ═══════════════════════════════════════════════════════════════
#  辅助 Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def loader():
    """全新的 LazyModuleLoader 实例（隔离全局单例）"""
    return LazyModuleLoader(max_workers=2)


@pytest.fixture
def async_loader():
    """全新的 AsyncLazyModuleLoader 实例"""
    return AsyncLazyModuleLoader(max_workers=2)


def _make_loader_func(return_value="module_instance", delay=0.0):
    """构造加载函数工厂

    Args:
        return_value: 加载函数返回值
        delay: 模拟加载耗时（秒）
    """
    def _loader():
        if delay > 0:
            time.sleep(delay)
        return return_value
    return _loader


def _make_failing_loader_func(exc=RuntimeError("加载失败")):
    """构造必定失败的加载函数"""
    def _loader():
        raise exc
    return _loader


# ═══════════════════════════════════════════════════════════════
#  TestEmptyBoundary — 空值/空容器边界
# ═══════════════════════════════════════════════════════════════

class TestEmptyBoundary:
    """空值边界测试"""

    def test_empty_level_returns_empty_dict(self, loader):
        """加载无模块的级别应返回空字典"""
        result = loader.load_level(LoadLevel.CRITICAL)
        assert result == {}
        assert loader.is_level_loaded(LoadLevel.CRITICAL) is True

    def test_empty_dependencies_loads_successfully(self, loader):
        """空 dependencies 列表应正常加载"""
        loader.register(
            "mod_a",
            _make_loader_func("instance_a"),
            level=LoadLevel.CRITICAL,
            dependencies=[],
        )
        result = loader.load("mod_a")
        assert result == "instance_a"
        assert loader.is_loaded("mod_a") is True

    def test_empty_name_register_loads(self, loader):
        """空字符串 name 应能注册和加载（源码未校验空 name）"""
        loader.register("", _make_loader_func("empty_name_instance"), level=LoadLevel.CRITICAL)
        result = loader.load("")
        assert result == "empty_name_instance"
        # get_module 也能获取
        assert loader.get_module("") == "empty_name_instance"


# ═══════════════════════════════════════════════════════════════
#  TestInvalidBoundary — 非法输入边界
# ═══════════════════════════════════════════════════════════════

class TestInvalidBoundary:
    """非法输入边界测试"""

    def test_invalid_load_unregistered_returns_none(self, loader):
        """load 未注册的 name 应返回 None"""
        result = loader.load("not_registered")
        assert result is None

    def test_invalid_loader_func_records_error(self, loader):
        """loader_func 抛异常时 load 返回 None，error 被记录"""
        loader.register(
            "broken_mod",
            _make_failing_loader_func(ValueError("模块损坏")),
            level=LoadLevel.CRITICAL,
        )
        result = loader.load("broken_mod")
        assert result is None
        # error 字段应被记录
        info = loader.modules["broken_mod"]
        assert info.error is not None
        assert "模块损坏" in info.error
        # stats 应记录失败
        assert loader.stats.failed_loads == 1
        assert loader.stats.successful_loads == 0

    def test_invalid_get_module_unregistered_returns_none(self, loader):
        """get_module 未注册或未加载的 name 应返回 None"""
        assert loader.get_module("not_registered") is None
        # 注册但未加载
        loader.register("mod_x", _make_loader_func(), level=LoadLevel.CRITICAL)
        assert loader.get_module("mod_x") is None


# ═══════════════════════════════════════════════════════════════
#  TestTimeoutBoundary — 超时边界
# ═══════════════════════════════════════════════════════════════

class TestTimeoutBoundary:
    """超时边界测试"""

    def test_timeout_slow_loader_still_completes(self, loader):
        """慢 loader_func 仍能完成加载（无超时限制）"""
        loader.register(
            "slow_mod",
            _make_loader_func("slow_instance", delay=0.2),
            level=LoadLevel.CRITICAL,
        )
        start = time.perf_counter()
        result = loader.load("slow_mod")
        elapsed = time.perf_counter() - start
        assert result == "slow_instance"
        assert elapsed >= 0.2
        # load_time_ms 应记录实际耗时
        info = loader.modules["slow_mod"]
        assert info.load_time_ms >= 200.0

    def test_timeout_parallel_preloader_future_timeout(self):
        """ParallelPreloader future.result 超时抛 TimeoutError

        注：ParallelPreloader.preload 内部用 future.result(timeout=30)，
        这里直接测试 future 的超时行为，验证超时会被捕获为失败。
        """
        preloader = ParallelPreloader(max_workers=1)
        try:
            # 提交一个慢任务
            future = preloader.executor.submit(_make_loader_func("ok", delay=0.5))
            # 用极短超时触发 TimeoutError
            with pytest.raises(TimeoutError):
                future.result(timeout=0.01)
        finally:
            preloader.shutdown()


# ═══════════════════════════════════════════════════════════════
#  TestNullBoundary — None 输入边界
# ═══════════════════════════════════════════════════════════════

class TestNullBoundary:
    """None 输入边界测试"""

    def test_null_name_load_returns_none(self, loader):
        """load(None) 返回 None（None not in self.modules）"""
        result = loader.load(None)
        assert result is None

    def test_null_get_module_returns_none(self, loader):
        """get_module(None) 返回 None"""
        assert loader.get_module(None) is None

    def test_null_should_load_returns_false(self, loader):
        """should_load(None) 返回 False（None not in self.modules）"""
        assert loader.should_load(None) is False


# ═══════════════════════════════════════════════════════════════
#  TestExtremeBoundary — 极值边界
# ═══════════════════════════════════════════════════════════════

class TestExtremeBoundary:
    """极值边界测试"""

    def test_extreme_many_modules_register(self, loader):
        """注册 100 个模块不报错"""
        for i in range(100):
            loader.register(
                f"mod_{i}",
                _make_loader_func(f"instance_{i}"),
                level=LoadLevel.OPTIONAL,
            )
        assert len(loader.modules) == 100
        # 批量加载 OPTIONAL 级别
        results = loader.load_level(LoadLevel.OPTIONAL)
        assert len(results) == 100
        assert loader.stats.successful_loads == 100

    def test_extreme_max_workers_one(self):
        """max_workers=1 单线程加载仍能工作"""
        single_loader = LazyModuleLoader(max_workers=1)
        single_loader.register(
            "mod_a", _make_loader_func("a"), level=LoadLevel.CRITICAL,
        )
        single_loader.register(
            "mod_b", _make_loader_func("b"), level=LoadLevel.CRITICAL,
        )
        result = single_loader.load_level(LoadLevel.CRITICAL)
        assert result == {"mod_a": "a", "mod_b": "b"}


# ═══════════════════════════════════════════════════════════════
#  TestAsyncBoundary — 异步加载器边界（AsyncLazyModuleLoader）
# ═══════════════════════════════════════════════════════════════

class TestAsyncBoundary:
    """异步加载器边界测试"""

    def test_async_load_unregistered_returns_none(self, async_loader):
        """异步 load 未注册 name 返回 None"""
        result = asyncio.get_event_loop().run_until_complete(
            async_loader.load("not_registered")
        )
        assert result is None

    def test_async_load_level_empty_returns_empty_dict(self, async_loader):
        """异步加载空级别返回空字典"""
        result = asyncio.get_event_loop().run_until_complete(
            async_loader.load_level(LoadLevel.CRITICAL)
        )
        assert result == {}

    def test_async_load_failing_loader_returns_none(self, async_loader):
        """异步加载失败时返回 None，error 被记录"""
        async_loader.register(
            "async_broken",
            _make_failing_loader_func(RuntimeError("异步加载失败")),
            level=LoadLevel.CRITICAL,
        )
        result = asyncio.get_event_loop().run_until_complete(
            async_loader.load("async_broken")
        )
        assert result is None
        info = async_loader.modules["async_broken"]
        assert info.error is not None
        assert "异步加载失败" in info.error
