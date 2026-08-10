"""AsyncLazyModuleLoader 单元测试"""
import pytest
import asyncio
from agent.lazy_loader_async import (
    AsyncLazyModuleLoader,
    LoadLevel,
    get_async_lazy_loader,
    AsyncParallelPreloader
)


def test_register_module():
    """注册模块"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {"loaded": True}
    
    loader.register("test_module", load_module, LoadLevel.CRITICAL)
    
    assert "test_module" in loader.modules
    assert loader.modules["test_module"].level == LoadLevel.CRITICAL


def test_register_module_with_dependencies():
    """注册带依赖的模块"""
    loader = AsyncLazyModuleLoader()
    
    def load_dep():
        return {"dep": True}
    
    def load_main():
        return {"main": True}
    
    loader.register("dep_module", load_dep, LoadLevel.CRITICAL)
    loader.register("main_module", load_main, LoadLevel.IMPORTANT, dependencies=["dep_module"])
    
    assert loader.modules["main_module"].dependencies == ["dep_module"]


@pytest.mark.asyncio
async def test_load_level():
    """异步加载指定级别模块"""
    loader = AsyncLazyModuleLoader()
    
    loaded_flag = {"value": False}
    
    def load_critical():
        loaded_flag["value"] = True
        return {"result": "critical"}
    
    loader.register("critical_mod", load_critical, LoadLevel.CRITICAL)
    
    result = await loader.load_level(LoadLevel.CRITICAL)
    
    assert "critical_mod" in result
    assert loaded_flag["value"] is True


@pytest.mark.asyncio
async def test_load_level_async_with_async_loader():
    """使用异步加载函数加载模块"""
    loader = AsyncLazyModuleLoader()
    
    async def async_loader():
        await asyncio.sleep(0.01)
        return {"async": True}
    
    loader.register("async_mod", lambda: None, LoadLevel.IMPORTANT, async_loader_func=async_loader)
    
    result = await loader.load_level(LoadLevel.IMPORTANT)
    
    assert "async_mod" in result
    assert result["async_mod"]["async"] is True


@pytest.mark.asyncio
async def test_load_module_with_dependencies():
    """加载带依赖的模块"""
    loader = AsyncLazyModuleLoader()
    
    dep_loaded = {"value": False}
    
    def load_dep():
        dep_loaded["value"] = True
        return {"dep": True}
    
    def load_main():
        return {"main": True}
    
    loader.register("dep", load_dep, LoadLevel.CRITICAL)
    loader.register("main", load_main, LoadLevel.IMPORTANT, dependencies=["dep"])
    
    await loader.load_level(LoadLevel.CRITICAL)
    result = await loader.load(loader.modules["main"].name)
    
    assert dep_loaded["value"] is True
    assert result is not None


def test_load_level_sync():
    """同步加载指定级别模块"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {"sync": True}
    
    loader.register("sync_mod", load_module, LoadLevel.CRITICAL)
    
    result = loader.load_level_sync(LoadLevel.CRITICAL)
    
    assert "sync_mod" in result


def test_load_sync():
    """同步加载单个模块"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {"single": True}
    
    loader.register("single_mod", load_module, LoadLevel.OPTIONAL)
    
    result = loader.load_sync("single_mod")
    
    assert result is not None
    assert result["single"] is True


@pytest.mark.asyncio
async def test_load_nonexistent_module():
    """加载不存在的模块应返回None"""
    loader = AsyncLazyModuleLoader()
    
    result = await loader.load("nonexistent")
    
    assert result is None


def test_load_sync_nonexistent_module():
    """同步加载不存在的模块应返回None"""
    loader = AsyncLazyModuleLoader()
    
    result = loader.load_sync("nonexistent")
    
    assert result is None


@pytest.mark.asyncio
async def test_load_already_loaded():
    """加载已加载的模块应直接返回实例"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {"instance": 1}
    
    loader.register("cached_mod", load_module, LoadLevel.CRITICAL)
    
    result1 = await loader.load("cached_mod")
    result2 = await loader.load("cached_mod")
    
    assert result1 is result2


def test_should_load():
    """判断模块是否应该加载"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {}
    
    loader.register("should_load_mod", load_module, LoadLevel.OPTIONAL)
    
    assert loader.should_load("should_load_mod") is True
    
    loader.load_sync("should_load_mod")
    
    assert loader.should_load("should_load_mod") is False


def test_is_loaded():
    """检查模块是否已加载"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {}
    
    loader.register("check_loaded", load_module, LoadLevel.CRITICAL)
    
    assert loader.is_loaded("check_loaded") is False
    
    loader.load_sync("check_loaded")
    
    assert loader.is_loaded("check_loaded") is True


def test_is_level_loaded():
    """检查级别是否已加载"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {}
    
    loader.register("level_check", load_module, LoadLevel.CRITICAL)
    
    assert loader.is_level_loaded(LoadLevel.CRITICAL) is False
    
    loader.load_level_sync(LoadLevel.CRITICAL)
    
    assert loader.is_level_loaded(LoadLevel.CRITICAL) is True


def test_get_module():
    """获取已加载的模块实例"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {"data": "test"}
    
    loader.register("get_mod", load_module, LoadLevel.CRITICAL)
    loader.load_sync("get_mod")
    
    result = loader.get_module("get_mod")
    
    assert result is not None
    assert result["data"] == "test"


def test_get_module_not_loaded():
    """获取未加载的模块应返回None"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {}
    
    loader.register("not_loaded_mod", load_module, LoadLevel.CRITICAL)
    
    result = loader.get_module("not_loaded_mod")
    
    assert result is None


@pytest.mark.asyncio
async def test_load_failure():
    """模块加载失败应记录错误"""
    loader = AsyncLazyModuleLoader()
    
    def failing_loader():
        raise ValueError("加载失败")
    
    loader.register("failing_mod", failing_loader, LoadLevel.CRITICAL)
    
    result = await loader.load("failing_mod")
    
    assert result is None
    assert loader.modules["failing_mod"].error is not None


def test_reset():
    """重置加载器状态"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {}
    
    loader.register("reset_mod", load_module, LoadLevel.CRITICAL)
    loader.load_sync("reset_mod")
    
    assert loader.is_loaded("reset_mod") is True
    
    loader.reset()
    
    assert loader.is_loaded("reset_mod") is False


def test_get_stats():
    """获取加载统计信息"""
    loader = AsyncLazyModuleLoader()
    
    def load_module():
        return {}
    
    loader.register("stats_mod", load_module, LoadLevel.CRITICAL)
    loader.load_sync("stats_mod")
    
    stats = loader.get_stats()
    
    assert stats["total_attempts"] >= 1
    assert stats["successful_loads"] >= 1


def test_global_async_loader():
    """全局异步加载器实例"""
    loader1 = get_async_lazy_loader()
    loader2 = get_async_lazy_loader()
    
    assert loader1 is loader2


@pytest.mark.asyncio
async def test_parallel_preloader():
    """异步并行预加载器"""
    preloader = AsyncParallelPreloader(max_workers=2)
    
    def load_a():
        return {"a": 1}
    
    def load_b():
        return {"b": 2}
    
    result = await preloader.preload([("a", load_a), ("b", load_b)])
    
    assert "a" in result
    assert "b" in result
    assert result["a"]["a"] == 1