"""统一单例管理器（SingletonManager）

提供线程安全、可重置、支持配置与清理钩子的全局单例管理。
解决项目中大量模块各自实现"模块级全局变量 + 延迟初始化"单例模式导致的
代码重复、线程安全不一致、测试隔离困难等问题。

用法示例:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
        is_registered, is_initialized, reset_all_singletons,
    )

    def _create_foo(config=None):
        return Foo(config)

    register_singleton("foo", _create_foo)
    foo = get_singleton("foo")
    reset_singleton("foo")
"""
from __future__ import annotations

import threading
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class SingletonManager:
    """全局单例管理器（双重检查锁定，线程安全）"""

    def __init__(self) -> None:
        self._factories: Dict[str, Callable[..., Any]] = {}
        self._instances: Dict[str, Any] = {}
        self._configs: Dict[str, dict] = {}
        self._cleanup_fns: Dict[str, Callable[[Any], None]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        factory: Callable[..., Any],
        cleanup_fn: Optional[Callable[[Any], None]] = None,
        default_config: Optional[dict] = None,
    ) -> None:
        """注册单例工厂。

        Args:
            name: 单例唯一名称。
            factory: 创建实例的工厂函数，签名 factory(config=None, **kwargs)。
            cleanup_fn: 可选的实例清理回调，重置单例时调用。
            default_config: 可选的默认配置，传给工厂函数。
        """
        with self._lock:
            if name in self._factories:
                logger.warning(
                    "[SingletonManager] 单例 %s 已注册，覆盖旧工厂", name
                )
            self._factories[name] = factory
            self._cleanup_fns[name] = cleanup_fn or (lambda _obj: None)
            self._configs[name] = dict(default_config or {})
            logger.info("[SingletonManager] 注册单例: %s", name)

    def get(self, name: str, config: Optional[dict] = None,
            required: bool = True) -> Optional[Any]:
        """获取单例实例（首次调用时创建）。

        Args:
            name: 单例名称。
            config: 可选的创建配置（仅首次创建时生效）。
            required: 未注册时是否抛出异常；False 则返回 None。

        Returns:
            单例实例；未注册且 required=False 时返回 None。
        """
        # 快速路径：已初始化
        instance = self._instances.get(name)
        if instance is not None:
            return instance

        with self._lock:
            # 双重检查
            instance = self._instances.get(name)
            if instance is not None:
                return instance

            factory = self._factories.get(name)
            if factory is None:
                if required:
                    raise KeyError(
                        f"[SingletonManager] 单例未注册: {name}"
                    )
                return None

            merged_config = dict(self._configs.get(name, {}))
            if config:
                merged_config.update(config)

            try:
                logger.info("[SingletonManager] 创建单例实例: %s", name)
                instance = factory(merged_config)
                self._instances[name] = instance
            except Exception as exc:
                logger.error(
                    "[SingletonManager] 创建单例失败: %s, 错误: %s",
                    name, exc,
                )
                raise

            return instance

    def reset(self, name: str) -> None:
        """重置指定单例（调用清理钩子后删除实例）。"""
        with self._lock:
            instance = self._instances.pop(name, None)
            if instance is not None:
                cleanup = self._cleanup_fns.get(name)
                try:
                    if cleanup:
                        cleanup(instance)
                except Exception as exc:
                    logger.warning(
                        "[SingletonManager] 清理单例 %s 失败: %s",
                        name, exc,
                    )
                logger.info("[SingletonManager] 重置单例: %s", name)

    def reset_all(self) -> None:
        """重置所有单例。"""
        with self._lock:
            for name in list(self._instances.keys()):
                self.reset(name)
        logger.info("[SingletonManager] 重置所有单例")

    def registered(self, name: str) -> bool:
        """检查单例是否已注册。"""
        with self._lock:
            return name in self._factories

    def initialized(self, name: str) -> bool:
        """检查单例是否已初始化。"""
        return name in self._instances


# 全局唯一实例
_manager = SingletonManager()


# ---------------------------------------------------------------------------
# 模块级便捷 API（与旧 get_xxx 函数签名风格保持一致）
# ---------------------------------------------------------------------------

def register_singleton(name, factory, cleanup_fn=None, default_config=None):
    """注册单例工厂（模块级便捷函数）。"""
    _manager.register(name, factory, cleanup_fn, default_config)


def get_singleton(name, config=None, required=True):
    """获取单例实例（模块级便捷函数）。"""
    return _manager.get(name, config, required)


def reset_singleton(name):
    """重置指定单例（模块级便捷函数）。"""
    _manager.reset(name)


def reset_all_singletons():
    """重置所有单例（模块级便捷函数）。"""
    _manager.reset_all()


def is_registered(name):
    """检查单例是否已注册（模块级便捷函数）。"""
    return _manager.registered(name)


def is_initialized(name):
    """检查单例是否已初始化（模块级便捷函数）。"""
    return _manager.initialized(name)
