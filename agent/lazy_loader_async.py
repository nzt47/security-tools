"""增强版多级懒加载架构模块 - Asyncio异步支持

功能：
- 多级懒加载策略（Critical / Important / Optional）
- asyncio异步加载支持
- 并行预加载支持
- 加载性能监控
- 依赖管理

加载级别：
- LEVEL_CRITICAL (0): 启动必须加载，阻塞式
- LEVEL_IMPORTANT (1): 首次交互后加载，后台异步
- LEVEL_OPTIONAL (2): 用户请求时加载，按需加载

使用示例：
```python
from agent.lazy_loader_async import AsyncLazyModuleLoader, LoadLevel

loader = AsyncLazyModuleLoader()

# 注册模块
loader.register('memory', load_memory_module, LoadLevel.CRITICAL)
loader.register('lifetrace', load_lifetrace_module, LoadLevel.IMPORTANT)
loader.register('ocr', load_ocr_module, LoadLevel.OPTIONAL)

# 启动时只加载 Critical 级别
await loader.load_level(LoadLevel.CRITICAL)

# 后台异步加载 Important 级别
asyncio.create_task(loader.load_level_async(LoadLevel.IMPORTANT))

# Optional 级别在用户请求时加载
if loader.should_load('ocr'):
    await loader.load('ocr')
```
"""

import logging
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Optional, Dict, List, Set, Awaitable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum

logger = logging.getLogger(__name__)


class LoadLevel(IntEnum):
    """加载级别枚举"""
    CRITICAL = 0   # 启动必须加载
    IMPORTANT = 1  # 首次交互后加载
    OPTIONAL = 2   # 用户请求时加载


@dataclass
class ModuleInfo:
    """模块信息"""
    name: str
    loader_func: Callable[..., Any]
    async_loader_func: Optional[Callable[..., Awaitable[Any]]] = None
    level: LoadLevel = LoadLevel.IMPORTANT
    dependencies: List[str] = field(default_factory=list)
    loaded: bool = False
    loading: bool = False
    load_time_ms: float = 0.0
    error: Optional[str] = None
    instance: Optional[Any] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class LoadStats:
    """加载统计"""
    total_attempts: int = 0
    successful_loads: int = 0
    failed_loads: int = 0
    total_load_time_ms: float = 0.0
    by_level: Dict[int, int] = field(default_factory=dict)
    async_loads: int = 0
    sync_loads: int = 0

    def record_load(self, level: LoadLevel, success: bool, elapsed_ms: float, is_async: bool = False):
        """记录加载结果"""
        self.total_attempts += 1
        if success:
            self.successful_loads += 1
        else:
            self.failed_loads += 1

        self.total_load_time_ms += elapsed_ms

        if level not in self.by_level:
            self.by_level[level] = 0
        self.by_level[level] += 1

        if is_async:
            self.async_loads += 1
        else:
            self.sync_loads += 1

    def get_avg_load_time(self) -> float:
        """获取平均加载时间"""
        return self.total_load_time_ms / self.total_attempts if self.total_attempts > 0 else 0.0


class AsyncLazyModuleLoader:
    """异步多级懒加载器

    特性：
    - 多级加载策略
    - 依赖管理
    - asyncio异步加载支持
    - 并行加载支持
    - 性能监控
    """

    def __init__(self, max_workers: int = 4):
        """
        初始化异步懒加载器

        Args:
            max_workers: 最大并行加载线程数
        """
        self.modules: Dict[str, ModuleInfo] = {}
        self.stats = LoadStats()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.loaded_levels: Set[LoadLevel] = set()
        self.loading_levels: Set[LoadLevel] = set()
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()

        logger.info(f"[AsyncLazyLoader] 初始化完成: max_workers={max_workers}")

    def register(
        self,
        name: str,
        loader_func: Callable[..., Any],
        level: LoadLevel = LoadLevel.IMPORTANT,
        dependencies: Optional[List[str]] = None,
        async_loader_func: Optional[Callable[..., Awaitable[Any]]] = None
    ):
        """
        注册模块

        Args:
            name: 模块名称
            loader_func: 同步加载函数
            level: 加载级别
            dependencies: 依赖的其他模块
            async_loader_func: 异步加载函数（可选）
        """
        with self._lock:
            if name in self.modules:
                logger.warning(f"[AsyncLazyLoader] 模块 {name} 已存在，将被覆盖")

            self.modules[name] = ModuleInfo(
                name=name,
                loader_func=loader_func,
                async_loader_func=async_loader_func,
                level=level,
                dependencies=dependencies or []
            )

            logger.info(
                f"[AsyncLazyLoader] 注册模块: {name}, "
                f"level={level.name}, "
                f"deps={dependencies or []}, "
                f"has_async={async_loader_func is not None}"
            )

    async def load_level(self, level: LoadLevel) -> Dict[str, Any]:
        """
        异步加载指定级别的所有模块

        Args:
            level: 加载级别

        Returns:
            加载的模块字典
        """
        if level in self.loaded_levels:
            logger.debug(f"[AsyncLazyLoader] 级别 {level.name} 已加载")
            return self._get_loaded_modules(level)

        logger.info(f"[AsyncLazyLoader] 开始异步加载级别: {level.name}")

        modules_to_load = [
            (name, info) for name, info in self.modules.items()
            if info.level == level and not info.loaded
        ]

        if not modules_to_load:
            logger.info(f"[AsyncLazyLoader] 级别 {level.name} 没有需要加载的模块")
            with self._lock:
                self.loaded_levels.add(level)
            return {}

        # 并行加载所有模块
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
                logger.error(
                    f"[AsyncLazyLoader] ❌ 异步加载失败: {name}, error={result}"
                )
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
        total_count = len(modules_to_load)

        logger.info(
            f"[AsyncLazyLoader] 级别 {level.name} 异步加载完成: "
            f"成功={success_count}, 失败={total_count - success_count}"
        )

        return loaded_results

    async def _load_module_async(self, name: str, info: ModuleInfo) -> Any:
        """异步加载单个模块"""
        # 确保依赖已加载
        if info.dependencies:
            await self._ensure_dependencies_loaded_async(info.dependencies)

        start_time = time.perf_counter()

        try:
            # 优先使用异步加载函数
            if info.async_loader_func:
                instance = await info.async_loader_func()
            else:
                # 使用线程池执行同步加载
                instance = await asyncio.get_event_loop().run_in_executor(
                    self.executor, info.loader_func
                )

            elapsed_ms = (time.perf_counter() - start_time) * 1000

            with info._lock:
                info.load_time_ms = elapsed_ms

            logger.info(
                f"[AsyncLazyLoader] ✅ 异步加载成功: {name}, elapsed={elapsed_ms:.2f}ms"
            )

            return instance

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with info._lock:
                info.error = str(e)
                info.load_time_ms = elapsed_ms

            logger.error(
                f"[AsyncLazyLoader] ❌ 异步加载失败: {name}, error={e}, elapsed={elapsed_ms:.2f}ms"
            )
            raise

    async def load(self, name: str) -> Optional[Any]:
        """
        异步加载指定模块（按需加载）

        Args:
            name: 模块名称

        Returns:
            模块实例，如果加载失败返回 None
        """
        if name not in self.modules:
            logger.error(f"[AsyncLazyLoader] 模块 {name} 未注册")
            return None

        info = self.modules[name]

        if info.loaded:
            return info.instance

        # 确保依赖已加载
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

            logger.info(
                f"[AsyncLazyLoader] ✅ 异步按需加载成功: {name}, elapsed={elapsed_ms:.2f}ms"
            )

            return instance

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with info._lock:
                info.error = str(e)
                info.load_time_ms = elapsed_ms

            self.stats.record_load(info.level, False, elapsed_ms, is_async=True)

            logger.error(
                f"[AsyncLazyLoader] ❌ 异步按需加载失败: {name}, error={e}, elapsed={elapsed_ms:.2f}ms"
            )

            return None

    async def _ensure_dependencies_loaded_async(self, dependencies: List[str]) -> None:
        """确保依赖模块已加载（异步版本）"""
        tasks = []
        for dep in dependencies:
            if dep in self.modules and not self.modules[dep].loaded:
                tasks.append(self.load(dep))
        if tasks:
            await asyncio.gather(*tasks)

    def load_level_sync(self, level: LoadLevel) -> Dict[str, Any]:
        """
        同步加载指定级别的所有模块

        Args:
            level: 加载级别

        Returns:
            加载的模块字典
        """
        if level in self.loaded_levels:
            logger.debug(f"[AsyncLazyLoader] 级别 {level.name} 已加载")
            return self._get_loaded_modules(level)

        logger.info(f"[AsyncLazyLoader] 开始同步加载级别: {level.name}")

        modules_to_load = [
            (name, info) for name, info in self.modules.items()
            if info.level == level and not info.loaded
        ]

        results = {}
        for name, info in modules_to_load:
            if info.dependencies:
                self._ensure_dependencies_loaded_sync(info.dependencies)

            start_time = time.perf_counter()
            try:
                instance = info.loader_func()
                elapsed_ms = (time.perf_counter() - start_time) * 1000

                with info._lock:
                    info.instance = instance
                    info.loaded = True
                    info.load_time_ms = elapsed_ms

                results[name] = instance
                self.stats.record_load(level, True, elapsed_ms, is_async=False)

                logger.info(
                    f"[AsyncLazyLoader] ✅ 同步加载成功: {name}, elapsed={elapsed_ms:.2f}ms"
                )

            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                with info._lock:
                    info.error = str(e)
                    info.load_time_ms = elapsed_ms

                self.stats.record_load(level, False, elapsed_ms, is_async=False)

                logger.error(
                    f"[AsyncLazyLoader] ❌ 同步加载失败: {name}, error={e}, elapsed={elapsed_ms:.2f}ms"
                )

        with self._lock:
            self.loaded_levels.add(level)

        logger.info(
            f"[AsyncLazyLoader] 级别 {level.name} 同步加载完成: "
            f"成功={sum(1 for _, i in modules_to_load if i.loaded)}, "
            f"失败={sum(1 for _, i in modules_to_load if not i.loaded and i.error)}"
        )

        return results

    def _ensure_dependencies_loaded_sync(self, dependencies: List[str]) -> None:
        """确保依赖模块已加载（同步版本）"""
        for dep in dependencies:
            if dep in self.modules and not self.modules[dep].loaded:
                self.load_sync(dep)

    def load_sync(self, name: str) -> Optional[Any]:
        """
        同步加载指定模块（按需加载）

        Args:
            name: 模块名称

        Returns:
            模块实例，如果加载失败返回 None
        """
        if name not in self.modules:
            logger.error(f"[AsyncLazyLoader] 模块 {name} 未注册")
            return None

        info = self.modules[name]

        if info.loaded:
            return info.instance

        if info.dependencies:
            self._ensure_dependencies_loaded_sync(info.dependencies)

        start_time = time.perf_counter()
        try:
            instance = info.loader_func()
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            with info._lock:
                info.instance = instance
                info.loaded = True
                info.load_time_ms = elapsed_ms

            self.stats.record_load(info.level, True, elapsed_ms, is_async=False)

            logger.info(
                f"[AsyncLazyLoader] ✅ 同步按需加载成功: {name}, elapsed={elapsed_ms:.2f}ms"
            )

            return instance

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with info._lock:
                info.error = str(e)
                info.load_time_ms = elapsed_ms

            self.stats.record_load(info.level, False, elapsed_ms, is_async=False)

            logger.error(
                f"[AsyncLazyLoader] ❌ 同步按需加载失败: {name}, error={e}, elapsed={elapsed_ms:.2f}ms"
            )

            return None

    def should_load(self, name: str) -> bool:
        """
        判断模块是否应该加载

        Args:
            name: 模块名称

        Returns:
            是否应该加载
        """
        if name not in self.modules:
            return False

        info = self.modules[name]

        if info.loaded or info.loading:
            return False

        if info.dependencies:
            for dep in info.dependencies:
                if dep in self.modules and not self.modules[dep].loaded:
                    return False

        return True

    def _get_loaded_modules(self, level: LoadLevel) -> Dict[str, Any]:
        """获取已加载的模块"""
        return {
            name: info.instance
            for name, info in self.modules.items()
            if info.level == level and info.loaded and info.instance is not None
        }

    def get_module(self, name: str) -> Optional[Any]:
        """获取已加载的模块实例"""
        if name in self.modules and self.modules[name].loaded:
            return self.modules[name].instance
        return None

    def get_stats(self) -> dict:
        """获取加载统计"""
        return {
            'total_attempts': self.stats.total_attempts,
            'successful_loads': self.stats.successful_loads,
            'failed_loads': self.stats.failed_loads,
            'avg_load_time_ms': f"{self.stats.get_avg_load_time():.2f}",
            'async_loads': self.stats.async_loads,
            'sync_loads': self.stats.sync_loads,
            'loaded_levels': [level.name for level in self.loaded_levels],
            'modules': {
                name: {
                    'level': info.level.name,
                    'loaded': info.loaded,
                    'load_time_ms': f"{info.load_time_ms:.2f}" if info.load_time_ms > 0 else None,
                    'error': info.error,
                    'has_async_loader': info.async_loader_func is not None
                }
                for name, info in self.modules.items()
            }
        }

    def is_loaded(self, name: str) -> bool:
        """检查模块是否已加载"""
        return name in self.modules and self.modules[name].loaded

    def is_level_loaded(self, level: LoadLevel) -> bool:
        """检查级别是否已加载"""
        return level in self.loaded_levels

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

        logger.info("[AsyncLazyLoader] 重置完成")

    async def close(self):
        """关闭加载器"""
        self.executor.shutdown(wait=True)
        logger.info("[AsyncLazyLoader] 已关闭")


class AsyncParallelPreloader:
    """异步并行预加载器"""

    def __init__(self, max_workers: int = 4):
        """
        初始化异步并行预加载器

        Args:
            max_workers: 最大并行加载线程数
        """
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.futures: List[Future] = []
        self.results: Dict[str, Any] = {}

        logger.info(f"[AsyncParallelPreloader] 初始化完成: max_workers={max_workers}")

    async def preload(self, modules: List[tuple[str, Callable]]) -> Dict[str, Any]:
        """
        异步并行预加载多个模块

        Args:
            modules: 模块列表，每个元素为 (name, loader_func)

        Returns:
            加载结果字典
        """
        logger.info(f"[AsyncParallelPreloader] 开始异步预加载: {len(modules)} 个模块")

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
                logger.error(f"[AsyncParallelPreloader] 预加载失败: {name} -> {result}")
            else:
                self.results[name] = result

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            f"[AsyncParallelPreloader] 异步预加载完成: "
            f"成功={len(self.results)}, "
            f"失败={len(modules) - len(self.results)}, "
            f"elapsed={elapsed_ms:.2f}ms"
        )

        return self.results

    def _load_module(self, name: str, loader: Callable) -> tuple[str, Any]:
        """加载单个模块"""
        start_time = time.perf_counter()
        instance = loader()
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        logger.debug(f"[AsyncParallelPreloader] 模块 {name} 加载完成: {elapsed_ms:.2f}ms")

        return name, instance

    async def wait(self):
        """等待所有加载完成"""
        for future in self.futures:
            try:
                await asyncio.get_event_loop().run_in_executor(None, future.result)
            except Exception as e:
                logger.error(f"[AsyncParallelPreloader] 等待失败: {e}")

    def shutdown(self):
        """关闭预加载器"""
        self.executor.shutdown(wait=True)
        logger.info("[AsyncParallelPreloader] 已关闭")


_global_async_loader: Optional[AsyncLazyModuleLoader] = None


def get_async_lazy_loader() -> AsyncLazyModuleLoader:
    """获取全局异步懒加载器实例"""
    global _global_async_loader
    if _global_async_loader is None:
        _global_async_loader = AsyncLazyModuleLoader()
    return _global_async_loader
