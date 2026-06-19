"""缓存模块

提供多级智能缓存功能，支持LRU淘汰策略和TTL过期机制。

导出：
- MultiLevelCache: 多级缓存系统
- CacheManager: 缓存管理器
- lru_cache_decorator: 函数级 LRU 缓存装饰器
- QueryCache: 查询缓存管理器
- default_cache: 默认缓存实例
- cache_manager: 缓存管理器实例
"""

from .multi_level_cache import (
    MultiLevelCache,
    CacheManager,
    default_cache,
    cache_manager,
    LRUCache,
    DiskCache,
    lru_cache_decorator,
    QueryCache,
)

__all__ = [
    'MultiLevelCache',
    'CacheManager',
    'default_cache',
    'cache_manager',
    'LRUCache',
    'DiskCache',
    'lru_cache_decorator',
    'QueryCache',
]
