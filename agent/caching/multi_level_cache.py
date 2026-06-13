"""多级智能缓存系统

功能：
- LRU (Least Recently Used) 淘汰策略
- TTL (Time To Live) 过期机制
- 多级缓存策略（内存 + 磁盘）
- 缓存预热支持
- 缓存统计与监控
- 线程安全设计

缓存层级：
- L1: 内存缓存（LRU + TTL）- 最快，容量小
- L2: 磁盘缓存（文件系统）- 较慢，容量大
- L3: 远程缓存（可选）- 分布式场景

使用示例：
```python
from agent.caching.multi_level_cache import MultiLevelCache

# 创建多级缓存
cache = MultiLevelCache(
    l1_max_size=1000,
    l1_ttl=300,
    l2_enabled=True,
    l2_dir="./cache/l2"
)

# 设置缓存
cache.set("user_123", {"name": "张三", "age": 25}, ttl=600)

# 获取缓存
result = cache.get("user_123")

# 获取统计信息
stats = cache.get_stats()
```
"""

import os
import time
import json
import hashlib
import logging
import threading
from typing import Optional, Dict, Any, Callable
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""
    key: str
    value: Any
    timestamp: float
    ttl_seconds: int
    hit_count: int = 0
    generation_time_ms: float = 0.0
    level: int = 1  # 1=L1, 2=L2, 3=L3

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.timestamp > self.ttl_seconds

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        return {
            'key': self.key,
            'value': self.value,
            'timestamp': self.timestamp,
            'ttl_seconds': self.ttl_seconds,
            'hit_count': self.hit_count,
            'generation_time_ms': self.generation_time_ms,
            'level': self.level
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CacheEntry':
        """从字典创建"""
        return cls(
            key=data['key'],
            value=data['value'],
            timestamp=data['timestamp'],
            ttl_seconds=data['ttl_seconds'],
            hit_count=data.get('hit_count', 0),
            generation_time_ms=data.get('generation_time_ms', 0.0),
            level=data.get('level', 1)
        )


@dataclass
class CacheStats:
    """缓存统计"""
    total_hits: int = 0
    total_misses: int = 0
    total_puts: int = 0
    total_evictions: int = 0
    l1_hits: int = 0
    l2_hits: int = 0
    l1_misses: int = 0
    l2_misses: int = 0
    total_hit_time_ms: float = 0.0
    total_generation_time_ms: float = 0.0

    def record_hit(self, level: int, hit_time_ms: float):
        """记录缓存命中"""
        self.total_hits += 1
        self.total_hit_time_ms += hit_time_ms
        if level == 1:
            self.l1_hits += 1
        elif level == 2:
            self.l2_hits += 1

    def record_miss(self, level: int):
        """记录缓存未命中"""
        self.total_misses += 1
        if level == 1:
            self.l1_misses += 1
        elif level == 2:
            self.l2_misses += 1

    def record_put(self):
        """记录缓存写入"""
        self.total_puts += 1

    def record_eviction(self):
        """记录缓存淘汰"""
        self.total_evictions += 1

    def get_hit_rate(self) -> float:
        """获取命中率"""
        total = self.total_hits + self.total_misses
        return (self.total_hits / total * 100) if total > 0 else 0.0

    def get_l1_hit_rate(self) -> float:
        """获取L1命中率"""
        total = self.l1_hits + self.l1_misses
        return (self.l1_hits / total * 100) if total > 0 else 0.0

    def get_l2_hit_rate(self) -> float:
        """获取L2命中率"""
        total = self.l2_hits + self.l2_misses
        return (self.l2_hits / total * 100) if total > 0 else 0.0

    def get_avg_hit_time_ms(self) -> float:
        """获取平均命中时间"""
        return (self.total_hit_time_ms / self.total_hits) if self.total_hits > 0 else 0.0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'total_hits': self.total_hits,
            'total_misses': self.total_misses,
            'total_puts': self.total_puts,
            'total_evictions': self.total_evictions,
            'l1_hits': self.l1_hits,
            'l2_hits': self.l2_hits,
            'hit_rate': f"{self.get_hit_rate():.1f}%",
            'l1_hit_rate': f"{self.get_l1_hit_rate():.1f}%",
            'l2_hit_rate': f"{self.get_l2_hit_rate():.1f}%",
            'avg_hit_time_ms': f"{self.get_avg_hit_time_ms():.2f}",
            'total_generation_time_ms': f"{self.total_generation_time_ms:.2f}"
        }


class LRUCache:
    """LRU缓存实现"""

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        """
        初始化LRU缓存

        Args:
            max_size: 最大缓存条目数
            ttl_seconds: 默认过期时间（秒）
        """
        self.max_size = max_size
        self.default_ttl = ttl_seconds
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    def _hash_key(self, key: str) -> str:
        """对key进行哈希（用于统一处理）"""
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        hash_key = self._hash_key(key)
        
        with self._lock:
            if hash_key not in self.cache:
                return None

            entry = self.cache[hash_key]

            if entry.is_expired():
                del self.cache[hash_key]
                return None

            self.cache.move_to_end(hash_key)
            entry.hit_count += 1

            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None):
        """设置缓存条目"""
        hash_key = self._hash_key(key)
        ttl = ttl_seconds or self.default_ttl

        with self._lock:
            # 如果已存在，更新
            if hash_key in self.cache:
                self.cache.move_to_end(hash_key)
                old_entry = self.cache[hash_key]
                entry = CacheEntry(
                    key=key,
                    value=value,
                    timestamp=time.time(),
                    ttl_seconds=ttl,
                    hit_count=old_entry.hit_count,
                    generation_time_ms=old_entry.generation_time_ms
                )
            else:
                # 新条目
                if len(self.cache) >= self.max_size:
                    evicted_key, evicted_entry = self.cache.popitem(last=False)
                    logger.debug(f"[LRU] 淘汰: hits={evicted_entry.hit_count}")

                entry = CacheEntry(
                    key=key,
                    value=value,
                    timestamp=time.time(),
                    ttl_seconds=ttl
                )

            self.cache[hash_key] = entry

    def delete(self, key: str):
        """删除缓存条目"""
        hash_key = self._hash_key(key)
        with self._lock:
            if hash_key in self.cache:
                del self.cache[hash_key]

    def clear(self):
        """清空缓存"""
        with self._lock:
            self.cache.clear()

    def get_size(self) -> int:
        """获取缓存大小"""
        with self._lock:
            return len(self.cache)

    def get_keys(self) -> list[str]:
        """获取所有key"""
        with self._lock:
            return [entry.key for entry in self.cache.values()]


class DiskCache:
    """磁盘缓存实现"""

    def __init__(self, cache_dir: str = "./cache/l2", max_size_bytes: int = 100 * 1024 * 1024):
        """
        初始化磁盘缓存

        Args:
            cache_dir: 缓存目录
            max_size_bytes: 最大磁盘空间（字节）
        """
        self.cache_dir = Path(cache_dir)
        self.max_size_bytes = max_size_bytes
        self._lock = threading.RLock()

        # 确保目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, key: str) -> Path:
        """获取缓存文件路径"""
        key_hash = hashlib.sha256(key.encode('utf-8')).hexdigest()
        return self.cache_dir / f"{key_hash}.json"

    def _is_expired(self, timestamp: float, ttl_seconds: int) -> bool:
        """检查是否过期"""
        return time.time() - timestamp > ttl_seconds

    def _get_total_size(self) -> int:
        """获取当前缓存目录大小"""
        total_size = 0
        for file_path in self.cache_dir.glob("*.json"):
            if file_path.is_file():
                total_size += file_path.stat().st_size
        return total_size

    def _evict_oldest(self):
        """淘汰最旧的缓存"""
        files = []
        for file_path in self.cache_dir.glob("*.json"):
            if file_path.is_file():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        files.append((file_path, data.get('timestamp', 0)))
                except Exception:
                    pass

        # 按时间戳排序，删除最旧的
        files.sort(key=lambda x: x[1])

        total_size = self._get_total_size()
        for file_path, _ in files:
            if total_size <= self.max_size_bytes:
                break
            try:
                file_size = file_path.stat().st_size
                file_path.unlink()
                total_size -= file_size
                logger.debug(f"[DiskCache] 淘汰文件: {file_path.name}")
            except Exception as e:
                logger.warning(f"[DiskCache] 删除文件失败: {e}")

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        file_path = self._get_file_path(key)

        with self._lock:
            if not file_path.exists():
                return None

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                if self._is_expired(data['timestamp'], data['ttl_seconds']):
                    file_path.unlink()
                    return None

                return data['value']
            except Exception as e:
                logger.error(f"[DiskCache] 读取失败: {e}")
                return None

    def set(self, key: str, value: Any, ttl_seconds: int = 3600):
        """设置缓存值"""
        file_path = self._get_file_path(key)

        with self._lock:
            # 检查大小限制
            if self._get_total_size() > self.max_size_bytes:
                self._evict_oldest()

            data = {
                'key': key,
                'value': value,
                'timestamp': time.time(),
                'ttl_seconds': ttl_seconds
            }

            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f)
            except Exception as e:
                logger.error(f"[DiskCache] 写入失败: {e}")

    def delete(self, key: str):
        """删除缓存"""
        file_path = self._get_file_path(key)
        with self._lock:
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception as e:
                    logger.warning(f"[DiskCache] 删除失败: {e}")

    def clear(self):
        """清空缓存"""
        with self._lock:
            for file_path in self.cache_dir.glob("*.json"):
                if file_path.is_file():
                    try:
                        file_path.unlink()
                    except Exception as e:
                        logger.warning(f"[DiskCache] 删除失败: {e}")

    def get_size(self) -> int:
        """获取缓存大小（字节）"""
        return self._get_total_size()


class MultiLevelCache:
    """多级缓存系统"""

    def __init__(
        self,
        l1_max_size: int = 1000,
        l1_ttl: int = 300,
        l2_enabled: bool = True,
        l2_dir: str = "./cache/l2",
        l2_max_size_mb: int = 100,
        warmup_enabled: bool = False,
        warmup_callback: Optional[Callable] = None
    ):
        """
        初始化多级缓存

        Args:
            l1_max_size: L1内存缓存最大条目数
            l1_ttl: L1默认过期时间（秒）
            l2_enabled: 是否启用L2磁盘缓存
            l2_dir: L2缓存目录
            l2_max_size_mb: L2最大大小（MB）
            warmup_enabled: 是否启用缓存预热
            warmup_callback: 缓存预热回调函数
        """
        # L1 内存缓存
        self._l1_cache = LRUCache(max_size=l1_max_size, ttl_seconds=l1_ttl)
        self._l1_ttl = l1_ttl

        # L2 磁盘缓存
        self._l2_enabled = l2_enabled
        if l2_enabled:
            self._l2_cache = DiskCache(
                cache_dir=l2_dir,
                max_size_bytes=l2_max_size_mb * 1024 * 1024
            )
        else:
            self._l2_cache = None

        # 统计信息
        self._stats = CacheStats()
        self._stats_lock = threading.RLock()

        # 缓存预热
        self._warmup_enabled = warmup_enabled
        self._warmup_callback = warmup_callback
        self._warmup_done = False

        logger.info(f"[MultiLevelCache] 初始化完成: "
                    f"L1(max={l1_max_size}, ttl={l1_ttl}s), "
                    f"L2(enabled={l2_enabled}, dir={l2_dir}, max={l2_max_size_mb}MB)")

    def _record_stat(self, func, *args):
        """记录统计信息"""
        with self._stats_lock:
            func(*args)

    def get(self, key: str) -> Optional[Any]:
        """
        获取缓存值

        查找顺序：L1 -> L2 -> 返回None

        Args:
            key: 缓存键

        Returns:
            缓存值，如果未命中返回None
        """
        start_time = time.perf_counter()

        # 先查L1
        l1_value = self._l1_cache.get(key)
        if l1_value is not None:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._record_stat(self._stats.record_hit, 1, elapsed_ms)
            logger.debug(f"[MultiLevelCache] L1命中: {key[:30]}...")
            return l1_value

        self._record_stat(self._stats.record_miss, 1)

        # 再查L2
        if self._l2_enabled and self._l2_cache:
            l2_value = self._l2_cache.get(key)
            if l2_value is not None:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                self._record_stat(self._stats.record_hit, 2, elapsed_ms)
                logger.debug(f"[MultiLevelCache] L2命中: {key[:30]}...")

                # 将L2数据提升到L1
                self._l1_cache.set(key, l2_value)

                return l2_value

            self._record_stat(self._stats.record_miss, 2)

        logger.debug(f"[MultiLevelCache] 未命中: {key[:30]}...")
        return None

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None):
        """
        设置缓存值

        写入策略：同时写入L1和L2

        Args:
            key: 缓存键
            value: 缓存值
            ttl_seconds: 过期时间（秒），默认使用L1的TTL
        """
        ttl = ttl_seconds or self._l1_ttl

        # 写入L1
        self._l1_cache.set(key, value, ttl)
        self._record_stat(self._stats.record_put)

        # 写入L2（如果启用）
        if self._l2_enabled and self._l2_cache:
            # L2使用更长的TTL（2倍L1）
            l2_ttl = ttl * 2
            self._l2_cache.set(key, value, l2_ttl)

        logger.debug(f"[MultiLevelCache] 设置: {key[:30]}..., ttl={ttl}s")

    def delete(self, key: str):
        """删除缓存"""
        self._l1_cache.delete(key)
        if self._l2_enabled and self._l2_cache:
            self._l2_cache.delete(key)

    def clear(self):
        """清空所有缓存"""
        self._l1_cache.clear()
        if self._l2_enabled and self._l2_cache:
            self._l2_cache.clear()
        logger.info("[MultiLevelCache] 缓存已清空")

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        with self._stats_lock:
            stats = self._stats.to_dict()
            stats['l1_size'] = self._l1_cache.get_size()
            if self._l2_enabled and self._l2_cache:
                stats['l2_size_bytes'] = self._l2_cache.get_size()
                stats['l2_size_mb'] = stats['l2_size_bytes'] / 1024 / 1024
            return stats

    def warmup(self):
        """执行缓存预热"""
        if not self._warmup_enabled or not self._warmup_callback:
            return

        if self._warmup_done:
            logger.info("[MultiLevelCache] 缓存预热已完成")
            return

        logger.info("[MultiLevelCache] 开始缓存预热...")
        start_time = time.time()

        try:
            warmup_data = self._warmup_callback()
            if warmup_data:
                for key, value in warmup_data.items():
                    self.set(key, value)
                logger.info(f"[MultiLevelCache] 缓存预热完成: {len(warmup_data)} 条数据")
        except Exception as e:
            logger.error(f"[MultiLevelCache] 缓存预热失败: {e}")

        elapsed = time.time() - start_time
        logger.info(f"[MultiLevelCache] 缓存预热耗时: {elapsed:.2f}s")
        self._warmup_done = True

    def get_l1_keys(self) -> list[str]:
        """获取L1缓存的所有key"""
        return self._l1_cache.get_keys()


class CacheManager:
    """缓存管理器"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._caches: Dict[str, MultiLevelCache] = {}

    @classmethod
    def get_instance(cls) -> 'CacheManager':
        """获取单例实例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = CacheManager()
            return cls._instance

    def get_cache(self, name: str, **kwargs) -> MultiLevelCache:
        """
        获取或创建缓存

        Args:
            name: 缓存名称
            **kwargs: 缓存参数

        Returns:
            缓存实例
        """
        if name not in self._caches:
            self._caches[name] = MultiLevelCache(**kwargs)
            logger.info(f"[CacheManager] 创建缓存: {name}")
        return self._caches[name]

    def remove_cache(self, name: str):
        """删除缓存"""
        if name in self._caches:
            del self._caches[name]
            logger.info(f"[CacheManager] 删除缓存: {name}")

    def get_all_cache_names(self) -> list[str]:
        """获取所有缓存名称"""
        return list(self._caches.keys())

    def clear_all(self):
        """清空所有缓存"""
        for cache in self._caches.values():
            cache.clear()
        logger.info("[CacheManager] 所有缓存已清空")


# 全局缓存实例
default_cache = MultiLevelCache()
cache_manager = CacheManager.get_instance()
