"""增强版多级懒加载架构模块 - Asyncio异步支持

继承自 lazy_loader.LazyModuleLoader，增加 async/await 支持。

功能：
- 多级懒加载策略（Critical / Important / Optional）
- asyncio异步加载支持
- 并行预加载支持
- 加载性能监控
- 依赖管理

使用示例：
```python
from agent.lazy_loader_async import AsyncLazyModuleLoader, LoadLevel

loader = AsyncLazyModuleLoader()

# 注册模块
loader.register('memory', load_memory_module, LoadLevel.CRITICAL)
loader.register('lifetrace', load_lifetrace_module, LoadLevel.IMPORTANT,
                async_loader_func=load_lifetrace_async)
loader.register('ocr', load_ocr_module, LoadLevel.OPTIONAL)

# 启动时只加载 Critical 级别
await loader.load_level(LoadLevel.CRITICAL)

# 后台异步加载 Important 级别
asyncio.create_task(loader.load_level(LoadLevel.IMPORTANT))

# Optional 级别在用户请求时加载
if loader.should_load('ocr'):
    await loader.load('ocr')
```
"""

import logging
import json
import uuid
import time
import asyncio
from typing import Callable, Any, Optional, Dict, List, Awaitable

from agent.lazy_loader import (
    LazyModuleLoader,
    LoadLevel,
    ModuleInfo,
)
from agent.lazy_loader._core import _BaseParallelPreloader

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class AsyncLazyModuleLoader(LazyModuleLoader):
    """异步多级懒加载器 — 继承自 LazyModuleLoader，增加异步加载能力

    特性：
    - 多级加载策略
    - 依赖管理
    - asyncio异步加载支持
    - 并行加载支持
    - 性能监控
    """

    def __init__(self, max_workers: int = 4):
        """初始化异步懒加载器"""
        super().__init__(max_workers=max_workers)
        self._async_lock = asyncio.Lock()
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "max_workers.max_workers", "msg": f"[AsyncLazyLoader] 初始化完成: max_workers={max_workers}"}, ensure_ascii=False))

    # ── 注册 ──

    def register(
        self,
        name: str,
        loader_func: Callable[..., Any],
        level: LoadLevel = LoadLevel.IMPORTANT,
        dependencies: Optional[List[str]] = None,
        async_loader_func: Optional[Callable[..., Awaitable[Any]]] = None
    ):
        """注册模块

        Args:
            name: 模块名称
            loader_func: 同步加载函数
            level: 加载级别
            dependencies: 依赖的其他模块
            async_loader_func: 异步加载函数（可选）
        """
        super().register(name, loader_func, level, dependencies,
                         async_loader_func=async_loader_func)

    # ── 异步加载 ──

    async def load_level(self, level: LoadLevel) -> Dict[str, Any]:
        """异步加载指定级别的所有模块

        Args:
            level: 加载级别

        Returns:
            加载的模块字典
        """
        if level in self.loaded_levels:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "level.name", "msg": f"[AsyncLazyLoader] 级别 {level.name} 已加载"}, ensure_ascii=False))
            return self._get_loaded_modules(level)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "level.name", "msg": f"[AsyncLazyLoader] 开始异步加载级别: {level.name}"}, ensure_ascii=False))

        modules_to_load = [
            (name, info) for name, info in self.modules.items()
            if info.level == level and not info.loaded
        ]

        if not modules_to_load:
            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "level.name", "msg": f"[AsyncLazyLoader] 级别 {level.name} 没有需要加载的模块"}, ensure_ascii=False))
            with self._lock:
                self.loaded_levels.add(level)
            return {}

        tasks = [self._load_module_async(name, info) for name, info in modules_to_load]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        loaded_results = {}
        for i, (name, info) in enumerate(modules_to_load):
            result = results[i]
            if isinstance(result, Exception):
                with info._lock:
                    info.error = str(result)
                    info.loading = False
                self.stats.record_load(level, False, 0.0, is_async=True)
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "name.error.result", "msg": f"[AsyncLazyLoader] ❌ 异步加载失败: {name}, error={result}"}, ensure_ascii=False))
            else:
                with info._lock:
                    info.instance = result
                    info.loaded = True
                    info.loading = False
                loaded_results[name] = result
                self.stats.record_load(level, True, info.load_time_ms, is_async=True)

        with self._lock:
            self.loaded_levels.add(level)

        success_count = len(loaded_results)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "level.name", "msg": f"[AsyncLazyLoader] 级别 {level.name} 异步加载完成: "
            f"成功={success_count}, 失败={len(modules_to_load) - success_count}"}, ensure_ascii=False))

        return loaded_results

    async def _load_module_async(self, name: str, info: ModuleInfo) -> Any:
        """异步加载单个模块"""
        if info.dependencies:
            await self._ensure_dependencies_loaded_async(info.dependencies)

        start_time = time.perf_counter()

        try:
            if info.async_loader_func:
                instance = await info.async_loader_func()
            else:
                instance = await asyncio.get_event_loop().run_in_executor(
                    self.executor, info.loader_func
                )

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            with info._lock:
                info.load_time_ms = elapsed_ms

            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "name.elapsed.elapsed_ms", "msg": f"[AsyncLazyLoader] ✅ 异步加载成功: {name}, elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

            return instance

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with info._lock:
                info.error = str(e)
                info.load_time_ms = elapsed_ms

            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "name", "msg": f"[AsyncLazyLoader] ❌ 异步加载失败: {name}, "
                f"error={e}, elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))
            raise

    async def load(self, name: str) -> Optional[Any]:
        """异步加载指定模块（按需加载）

        Args:
            name: 模块名称

        Returns:
            模块实例，如果加载失败返回 None
        """
        if name not in self.modules:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "name", "msg": f"[AsyncLazyLoader] 模块 {name} 未注册"}, ensure_ascii=False))
            return None

        info = self.modules[name]

        if info.loaded:
            return info.instance

        if info.dependencies:
            await self._ensure_dependencies_loaded_async(info.dependencies)

        start_time = time.perf_counter()

        try:
            if info.async_loader_func:
                instance = await info.async_loader_func()
            else:
                instance = await asyncio.get_event_loop().run_in_executor(
                    self.executor, info.loader_func
                )

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            with info._lock:
                info.instance = instance
                info.loaded = True
                info.load_time_ms = elapsed_ms

            self.stats.record_load(info.level, True, elapsed_ms, is_async=True)

            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "name", "msg": f"[AsyncLazyLoader] ✅ 异步按需加载成功: {name}, "
                f"elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

            return instance

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with info._lock:
                info.error = str(e)
                info.load_time_ms = elapsed_ms

            self.stats.record_load(info.level, False, elapsed_ms, is_async=True)

            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "name", "msg": f"[AsyncLazyLoader] ❌ 异步按需加载失败: {name}, "
                f"error={e}, elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

            return None

    async def _ensure_dependencies_loaded_async(self, dependencies: List[str]) -> None:
        """确保依赖模块已加载（异步版本）"""
        tasks = []
        for dep in dependencies:
            if dep in self.modules and not self.modules[dep].loaded:
                tasks.append(self.load(dep))
        if tasks:
            await asyncio.gather(*tasks)

    # ── 同步加载（显式调用父类的同步版本）──

    def load_level_sync(self, level: LoadLevel) -> Dict[str, Any]:
        """同步加载指定级别的所有模块"""
        return LazyModuleLoader.load_level(self, level)

    def load_sync(self, name: str) -> Optional[Any]:
        """同步加载指定模块"""
        return LazyModuleLoader.load(self, name)

    # ── 工具方法 ──

    def should_load(self, name: str) -> bool:
        """判断模块是否应该加载"""
        return super().should_load(name)

    def get_module(self, name: str) -> Optional[Any]:
        """获取已加载的模块实例"""
        return super().get_module(name)

    def get_stats(self) -> dict:
        """获取加载统计（含异步信息）"""
        stats = super().get_stats()
        stats['async_loads'] = self.stats.async_loads
        stats['sync_loads'] = self.stats.sync_loads
        stats['modules'] = {
            name: {
                'level': info.level.name,
                'loaded': info.loaded,
                'load_time_ms': f"{info.load_time_ms:.2f}" if info.load_time_ms > 0 else None,
                'error': info.error,
                'has_async_loader': info.async_loader_func is not None
            }
            for name, info in self.modules.items()
        }
        return stats

    def is_loaded(self, name: str) -> bool:
        return super().is_loaded(name)

    def is_level_loaded(self, level: LoadLevel) -> bool:
        return super().is_level_loaded(level)

    def reset(self):
        """重置所有模块状态"""
        with self._lock:
            for info in self.modules.values():
                with info._lock:
                    info.loaded = False
                    info.loading = False
                    info.instance = None
                    info.error = None
                    info.load_time_ms = 0.0

            self.loaded_levels.clear()
            self.loading_levels.clear()

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "log", "msg": "[AsyncLazyLoader] 重置完成"}, ensure_ascii=False))

    async def close(self):
        """关闭加载器"""
        self.executor.shutdown(wait=True)
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "log", "msg": "[AsyncLazyLoader] 已关闭"}, ensure_ascii=False))


class AsyncParallelPreloader(_BaseParallelPreloader):
    """异步并行预加载器"""

    def __init__(self, max_workers: int = 4):
        super().__init__(max_workers=max_workers, name="AsyncParallelPreloader")

    async def preload(self, modules: List[tuple[str, Callable]]) -> Dict[str, Any]:
        """异步并行预加载多个模块"""
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "len.modules", "msg": f"[AsyncParallelPreloader] 开始异步预加载: {len(modules)} 个模块"}, ensure_ascii=False))

        start_time = time.perf_counter()

        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(self.executor, self._load_module, name, loader)
            for name, loader in modules
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, (name, _) in enumerate(modules):
            result = results[i]
            if isinstance(result, Exception):
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "name.result", "msg": f"[AsyncParallelPreloader] 预加载失败: {name} -> {result}"}, ensure_ascii=False))
            else:
                # _load_module 返回 (name, instance) 元组
                loaded_name, instance = result
                self.results[loaded_name] = instance

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "lazy_loader_async", "action": "log", "msg": f"[AsyncParallelPreloader] 异步预加载完成: "
            f"成功={len(self.results)}, "
            f"失败={len(modules) - len(self.results)}, "
            f"elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

        return self.results


_global_async_loader: Optional[AsyncLazyModuleLoader] = None


def get_async_lazy_loader() -> AsyncLazyModuleLoader:
    """获取全局异步懒加载器实例"""
    global _global_async_loader
    if _global_async_loader is None:
        _global_async_loader = AsyncLazyModuleLoader()
    return _global_async_loader
