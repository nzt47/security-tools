
"""
LRU 缓存装饰器和工具函数
用于实现查询缓存优化
"""

import time
import logging
from collections import OrderedDict
from typing import Any, Callable, Optional, TypeVar, Dict, List, Tuple

logger = logging.getLogger(__name__)
logger.info("[LRUCache] 加载缓存模块")

# 泛型类型定义
F = TypeVar('F', bound=Callable[..., Any])


class LRUCache:
    """
    LRU (Least Recently Used) 缓存实现
    
    支持:
    - 指定缓存大小限制
    - TTL (Time-To-Live) 过期时间
    - 缓存命中率统计
    - 自动清理过期条目
    """
    
    def __init__(self, max_size: int = 100, ttl_seconds: Optional[int] = 3600):
        """
        初始化 LRU 缓存
        
        Args:
            max_size: 最大缓存条目数量
            ttl_seconds: 缓存条目的过期时间（秒），None表示不过期
        """
        logger.info(f"[LRUCache] 初始化: max_size={max_size}, ttl_seconds={ttl_seconds}")
        
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        
        # 使用 OrderedDict 实现 LRU 缓存
        self._cache: OrderedDict[Tuple[Any, ...], Tuple[Any, float]] = OrderedDict()
        
        # 统计信息
        self.hits = 0
        self.misses = 0
        self.expired_count = 0
        
        logger.info("[LRUCache] 初始化完成")
    
    def get(self, key: Tuple[Any, ...]) -&gt; Optional[Any]:
        """
        从缓存中获取值
        
        Args:
            key: 缓存键
            
        Returns:
            缓存的值，如果不存在或已过期则返回 None
        """
        now = time.time()
        
        if key in self._cache:
            value, timestamp = self._cache.pop(key)
            
            # 检查是否过期
            if self.ttl_seconds is not None and (now - timestamp) &gt; self.ttl_seconds:
                logger.debug(f"[LRUCache] 缓存过期: {key}")
                self.expired_count += 1
                self.misses += 1
                return None
            
            # 更新访问顺序（移到最前面）
            self._cache[key] = (value, now)
            self.hits += 1
            logger.debug(f"[LRUCache] 缓存命中: {key}")
            return value
        
        self.misses += 1
        logger.debug(f"[LRUCache] 缓存未命中: {key}")
        return None
    
    def set(self, key: Tuple[Any, ...], value: Any) -&gt; None:
        """
        设置缓存值
        
        Args:
            key: 缓存键
            value: 缓存值
        """
        now = time.time()
        
        # 如果键已存在，先移除旧条目
        if key in self._cache:
            self._cache.pop(key)
        
        # 如果缓存已满，移除最久未使用的条目
        if len(self._cache) &gt;= self.max_size:
            self._cache.popitem(last=False)
        
        # 添加新条目到缓存
        self._cache[key] = (value, now)
        logger.debug(f"[LRUCache] 设置缓存: {key}")
    
    def clear(self) -&gt; None:
        """清空所有缓存"""
        logger.info("[LRUCache] 清空缓存")
        self._cache.clear()
        logger.info("[LRUCache] 缓存已清空")
    
    def get_stats(self) -&gt; Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            包含缓存统计信息的字典
        """
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total &gt; 0 else 0
        
        stats = {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 2),
            "expired_count": self.expired_count,
            "total": total
        }
        
        logger.info(f"[LRUCache] 缓存统计: {stats}")
        return stats
    
    def reset_stats(self) -&gt; None:
        """重置统计信息"""
        logger.info("[LRUCache] 重置统计信息")
        self.hits = 0
        self.misses = 0
        self.expired_count = 0


def lru_cache_decorator(max_size: int = 100, ttl_seconds: Optional[int] = 3600):
    """
    LRU 缓存装饰器工厂
    
    使用示例:
        @lru_cache_decorator(max_size=50, ttl_seconds=60)
        def expensive_function(param1, param2):
            # 耗时的操作
            return result
    
    Args:
        max_size: 最大缓存条目数量
        ttl_seconds: 缓存过期时间（秒）
        
    Returns:
        装饰器函数
    """
    def decorator(func: F) -&gt; F:
        cache = LRUCache(max_size=max_size, ttl_seconds=ttl_seconds)
        
        def wrapper(*args: Any, **kwargs: Any) -&gt; Any:
            # 构建缓存键（需要处理不可哈希的参数）
            key_parts = []
            key_parts.append(args)
            
            # 对 kwargs 排序以确保一致性
            sorted_kwargs = tuple(sorted(kwargs.items()))
            key_parts.append(sorted_kwargs)
            
            key = tuple(key_parts)
            
            # 尝试从缓存获取
            cached_result = cache.get(key)
            if cached_result is not None:
                return cached_result
            
            # 执行原函数
            result = func(*args, **kwargs)
            
            # 存入缓存
            cache.set(key, result)
            return result
        
        # 暴露缓存对象以便外部访问
        wrapper.cache = cache  # type: ignore
        return wrapper  # type: ignore
    
    return decorator


class QueryCache:
    """
    查询缓存管理器
    
    为向量存储等模块提供查询缓存
    """
    
    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        """
        初始化查询缓存管理器
        
        Args:
            max_size: 最大缓存条目数量
            ttl_seconds: 缓存过期时间（秒）
        """
        logger.info(f"[QueryCache] 初始化: max_size={max_size}, ttl_seconds={ttl_seconds}")
        
        self.search_cache = LRUCache(max_size=max_size, ttl_seconds=ttl_seconds)
        self.recent_cache = LRUCache(max_size=20, ttl_seconds=60)
        
        logger.info("[QueryCache] 初始化完成")
    
    def clear_all(self) -&gt; None:
        """清空所有缓存"""
        logger.info("[QueryCache] 清空所有缓存")
        self.search_cache.clear()
        self.recent_cache.clear()
    
    def get_stats(self) -&gt; Dict[str, Dict[str, Any]]:
        """获取所有缓存的统计信息"""
        return {
            "search": self.search_cache.get_stats(),
            "recent": self.recent_cache.get_stats()
        }

