"""多级懒加载架构模块

功能：
- 多级懒加载策略（Critical / Important / Optional）
- 并行预加载支持
- 异步加载支持
- 加载性能监控

加载级别：
- LEVEL_CRITICAL (0): 启动必须加载，阻塞式
- LEVEL_IMPORTANT (1): 首次交互后加载，后台异步
- LEVEL_OPTIONAL (2): 用户请求时加载，按需加载

使用示例：
```python
from agent.lazy_loader import LazyModuleLoader, lazy_load

loader = LazyModuleLoader()

# 注册模块
loader.register('memory', load_memory_module, LEVEL_CRITICAL)
loader.register('lifetrace', load_lifetrace_module, LEVEL_IMPORTANT)
loader.register('ocr', load_ocr_module, LEVEL_OPTIONAL)

# 启动时只加载 Critical 级别
loader.load_level(LEVEL_CRITICAL)

# 首次交互后后台加载 Important 级别
loader.load_level_async(LEVEL_IMPORTANT)

# Optional 级别在用户请求时加载
if loader.should_load('ocr'):
    loader.load('ocr')
```
"""

import logging
import json
import uuid
import time
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Optional, Dict, List, Set
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum

from ._core import _BaseParallelPreloader

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class LoadLevel(IntEnum):
    """加载级别枚举"""
    CRITICAL = 0   # 启动必须加载
    IMPORTANT = 1  # 首次交互后加载
    OPTIONAL = 2   # 用户请求时加载


@dataclass
class ModuleInfo:
    """模块信息"""
    name: str
    loader_func: Callable
    level: LoadLevel = LoadLevel.IMPORTANT
    dependencies: List[str] = field(default_factory=list)
    loaded: bool = False
    loading: bool = False
    load_time_ms: float = 0.0
    error: Optional[str] = None
    instance: Optional[Any] = None
    async_loader_func: Optional[Callable] = None  # AsyncLazyModuleLoader 使用
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

    def record_load(self, level: LoadLevel, success: bool, elapsed_ms: float,
                    is_async: bool = False):
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


class LazyModuleLoader:
    """多级懒加载器

    特性：
    - 多级加载策略
    - 依赖管理
    - 并行加载支持
    - 异步加载支持
    - 性能监控
    """

    def __init__(self, max_workers: int = 4):
        """
        初始化懒加载器

        Args:
            max_workers: 最大并行加载线程数
        """
        self.modules: Dict[str, ModuleInfo] = {}
        self.stats = LoadStats()
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.loaded_levels: Set[LoadLevel] = set()
        self.loading_levels: Set[LoadLevel] = set()
        self._lock = threading.Lock()
        self._async_lock = asyncio.Lock()

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "max_workers.max_workers", "msg": f"[LazyLoader] 初始化完成: max_workers={max_workers}"}, ensure_ascii=False))

    def register(
        self,
        name: str,
        loader_func: Callable,
        level: LoadLevel = LoadLevel.IMPORTANT,
        dependencies: Optional[List[str]] = None,
        async_loader_func: Optional[Callable] = None
    ):
        """
        注册模块

        Args:
            name: 模块名称
            loader_func: 加载函数
            level: 加载级别
            dependencies: 依赖的其他模块
            async_loader_func: 异步加载函数（可选，AsyncLazyModuleLoader 使用）
        """
        with self._lock:
            if name in self.modules:
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "name", "msg": f"[LazyLoader] 模块 {name} 已存在，将被覆盖"}, ensure_ascii=False))

            self.modules[name] = ModuleInfo(
                name=name,
                loader_func=loader_func,
                level=level,
                dependencies=dependencies or [],
                async_loader_func=async_loader_func,
            )

            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "name", "msg": f"[LazyLoader] 注册模块: {name}, "
                f"level={level.name}, "
                f"deps={dependencies or []}, "
                f"has_async={async_loader_func is not None}"}, ensure_ascii=False))

    def load_level(self, level: LoadLevel) -> Dict[str, Any]:
        """
        同步加载指定级别的所有模块

        Args:
            level: 加载级别

        Returns:
            加载的模块字典
        """
        if level in self.loaded_levels:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "level.name", "msg": f"[LazyLoader] 级别 {level.name} 已加载"}, ensure_ascii=False))
            return self._get_loaded_modules(level)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "level.name", "msg": f"[LazyLoader] 开始加载级别: {level.name}"}, ensure_ascii=False))

        modules_to_load = [
            (name, info) for name, info in self.modules.items()
            if info.level == level and not info.loaded
        ]

        results = {}
        for name, info in modules_to_load:
            if info.dependencies:
                self._ensure_dependencies_loaded(info.dependencies)

            start_time = time.perf_counter()
            try:
                instance = info.loader_func()
                elapsed_ms = (time.perf_counter() - start_time) * 1000

                with self._lock:
                    info.instance = instance
                    info.loaded = True
                    info.load_time_ms = elapsed_ms

                results[name] = instance
                self.stats.record_load(level, True, elapsed_ms)

                logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "name", "msg": f"[LazyLoader] ✅ 加载成功: {name}, "
                    f"elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                with self._lock:
                    info.error = str(e)
                    info.load_time_ms = elapsed_ms

                self.stats.record_load(level, False, elapsed_ms)

                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "name", "msg": f"[LazyLoader] ❌ 加载失败: {name}, "
                    f"error={e}, elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

        with self._lock:
            self.loaded_levels.add(level)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "level.name", "msg": f"[LazyLoader] 级别 {level.name} 加载完成: "
            f"成功={sum(1 for _, i in modules_to_load if i.loaded)}, "
            f"失败={sum(1 for _, i in modules_to_load if not i.loaded and i.error)}"}, ensure_ascii=False))

        return results

    def load_level_async(self, level: LoadLevel) -> None:
        """
        异步加载指定级别的所有模块（后台执行）

        Args:
            level: 加载级别
        """
        if level in self.loading_levels:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "level.name", "msg": f"[LazyLoader] 级别 {level.name} 正在加载中"}, ensure_ascii=False))
            return

        if level in self.loaded_levels:
            logger.debug(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "level.name", "msg": f"[LazyLoader] 级别 {level.name} 已加载"}, ensure_ascii=False))
            return

        with self._lock:
            self.loading_levels.add(level)

        def _async_load():
            try:
                self.load_level(level)
            finally:
                with self._lock:
                    self.loading_levels.discard(level)

        thread = threading.Thread(target=_async_load, daemon=True)
        thread.start()

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "level.name", "msg": f"[LazyLoader] 异步加载级别: {level.name}"}, ensure_ascii=False))

    def load(self, name: str) -> Optional[Any]:
        """
        加载指定模块（按需加载）

        Args:
            name: 模块名称

        Returns:
            模块实例，如果加载失败返回 None
        """
        if name not in self.modules:
            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "name", "msg": f"[LazyLoader] 模块 {name} 未注册"}, ensure_ascii=False))
            return None

        info = self.modules[name]

        if info.loaded:
            return info.instance

        if info.dependencies:
            self._ensure_dependencies_loaded(info.dependencies)

        start_time = time.perf_counter()
        try:
            instance = info.loader_func()
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            with self._lock:
                info.instance = instance
                info.loaded = True
                info.load_time_ms = elapsed_ms

            self.stats.record_load(info.level, True, elapsed_ms)

            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "name", "msg": f"[LazyLoader] ✅ 按需加载成功: {name}, "
                f"elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

            return instance

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            with self._lock:
                info.error = str(e)
                info.load_time_ms = elapsed_ms

            self.stats.record_load(info.level, False, elapsed_ms)

            logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "name", "msg": f"[LazyLoader] ❌ 按需加载失败: {name}, "
                f"error={e}, elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

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

    def _ensure_dependencies_loaded(self, dependencies: List[str]) -> None:
        """确保依赖模块已加载"""
        for dep in dependencies:
            if dep in self.modules and not self.modules[dep].loaded:
                self.load(dep)

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
            'loaded_levels': [level.name for level in self.loaded_levels],
            'modules': {
                name: {
                    'level': info.level.name,
                    'loaded': info.loaded,
                    'load_time_ms': f"{info.load_time_ms:.2f}" if info.load_time_ms > 0 else None,
                    'error': info.error
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
                info.loaded = False
                info.loading = False
                info.instance = None
                info.error = None
                info.load_time_ms = 0.0

            self.loaded_levels.clear()
            self.loading_levels.clear()

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "log", "msg": "[LazyLoader] 重置完成"}, ensure_ascii=False))


def lazy_load(level: LoadLevel = LoadLevel.IMPORTANT):
    """
    懒加载装饰器

    使用示例：
    ```python
    @lazy_load(LoadLevel.IMPORTANT)
    def my_module():
        return MyModuleClass()
    ```

    Args:
        level: 加载级别
    """
    def decorator(func: Callable) -> Callable:
        func._lazy_load_level = level
        return func
    return decorator


class ParallelPreloader(_BaseParallelPreloader):
    """并行预加载器"""

    def __init__(self, max_workers: int = 4):
        super().__init__(max_workers=max_workers, name="ParallelPreloader")
        self.futures: List[Future] = []

    def preload(self, modules: List[tuple[str, Callable]]) -> Dict[str, Any]:
        """
        并行预加载多个模块

        Args:
            modules: 模块列表，每个元素为 (name, loader_func)

        Returns:
            加载结果字典
        """
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "len.modules", "msg": f"[ParallelPreloader] 开始预加载: {len(modules)} 个模块"}, ensure_ascii=False))

        start_time = time.perf_counter()

        for name, loader in modules:
            future = self.executor.submit(self._load_module, name, loader)
            self.futures.append(future)

        for future in self.futures:
            try:
                name, instance = future.result(timeout=30)
                self.results[name] = instance
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "log", "msg": f"[ParallelPreloader] 预加载失败: {e}"}, ensure_ascii=False))

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "log", "msg": f"[ParallelPreloader] 预加载完成: "
            f"成功={len(self.results)}, "
            f"失败={len(modules) - len(self.results)}, "
            f"elapsed={elapsed_ms:.2f}ms"}, ensure_ascii=False))

        return self.results

    def wait(self):
        """等待所有加载完成"""
        for future in self.futures:
            try:
                future.result()
            except Exception as e:
                logger.error(json.dumps({"trace_id": _trace_id(), "module_name": "__init__", "action": "log", "msg": f"[ParallelPreloader] 等待失败: {e}"}, ensure_ascii=False))


_global_loader: Optional[LazyModuleLoader] = None


def get_lazy_loader() -> LazyModuleLoader:
    """获取全局懒加载器实例"""
    global _global_loader
    if _global_loader is None:
        _global_loader = LazyModuleLoader()
    return _global_loader
