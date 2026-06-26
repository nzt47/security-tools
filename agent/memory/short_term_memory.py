"""临时记忆模块 — ShortTermMemory

临时记忆特征：
- 会话级别，随会话结束自动清理
- 用于暂存当前任务的中间结果
- 支持 TTL 自动过期
- 不写入持久化存储

设计文档：P2 云枢架构升级 — Memory Abstraction Layer (3.1)
"""

import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.memory.base import MemoryCapability

logger = logging.getLogger(__name__)


@dataclass
class ShortTermMemoryEntry:
    """临时记忆条目

    Attributes:
        key: 记忆唯一标识
        content: 记忆内容
        created_at: 创建时间戳
        expires_at: 过期时间戳（0 表示永不过期）
        task_id: 关联的任务 ID
        accessed: 是否被访问过（用于 LRU 清理）
    """
    key: str
    content: Any
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0  # 0 = 永不过期
    task_id: str = ""
    accessed: bool = False


class ShortTermMemory:
    """临时记忆管理器

    特征：
    - 纯内存存储，会话结束自动清空
    - 支持 TTL 自动过期
    - LRU 缓存策略（内存不足时清理最少使用的条目）
    - 用于暂存中间结果、思考过程等

    用法:
        stm = ShortTermMemory(max_size=100, default_ttl=300)
        await stm.save("intermediate_result", {"step": 1, "data": "xxx"})
        value = await stm.get("intermediate_result")
    """

    def __init__(
        self,
        max_size: int = 100,
        default_ttl: int = 300,  # 默认 5 分钟过期
        cleanup_interval: int = 60,  # 清理检查间隔（秒）
    ) -> None:
        """
        Args:
            max_size: 最大条目数（超过后 LRU 清理）
            default_ttl: 默认过期时间（秒），0 表示永不过期
            cleanup_interval: 过期检查间隔（秒）
        """
        self._store: dict[str, ShortTermMemoryEntry] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = threading.RLock()

        logger.info("[ShortTermMemory] 初始化完成: max_size=%d, default_ttl=%ds", max_size, default_ttl)

    # ── 能力声明 ──

    @property
    def capabilities(self) -> set[MemoryCapability]:
        return {MemoryCapability.LOCAL_FIRST}

    # ── 核心操作 ──

    async def save(
        self,
        key: str,
        content: Any,
        ttl: Optional[int] = None,
        task_id: str = "",
    ) -> bool:
        """保存临时记忆

        Args:
            key: 记忆唯一标识
            content: 记忆内容
            ttl: 过期时间（秒），None 使用默认值，0 表示永不过期
            task_id: 关联的任务 ID

        Returns:
            True 表示保存成功
        """
        if not key:
            logger.warning("[ShortTermMemory] save 失败: key 为空")
            return False

        now = time.time()
        ttl = ttl if ttl is not None else self._default_ttl
        expires_at = now + ttl if ttl > 0 else 0

        with self._lock:
            # LRU 清理：如果已满，先清理最少使用的条目
            if len(self._store) >= self._max_size and key not in self._store:
                self._evict_lru()

            entry = ShortTermMemoryEntry(
                key=key,
                content=content,
                created_at=now,
                expires_at=expires_at,
                task_id=task_id,
                accessed=False,
            )
            self._store[key] = entry

        logger.debug("[ShortTermMemory] 保存成功: key=%s, ttl=%s", key, ttl)
        return True

    async def get(self, key: str) -> Optional[Any]:
        """获取临时记忆

        Args:
            key: 记忆标识

        Returns:
            记忆内容或 None（已过期或不存在）
        """
        if not key:
            return None

        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None

            # 检查是否过期
            if entry.expires_at > 0 and time.time() > entry.expires_at:
                del self._store[key]
                logger.debug("[ShortTermMemory] 访问已过期记忆: key=%s", key)
                return None

            # 标记为已访问（用于 LRU）
            entry.accessed = True
            return entry.content

    async def delete(self, key: str) -> bool:
        """删除临时记忆

        Args:
            key: 记忆标识

        Returns:
            True 表示删除成功
        """
        if not key:
            return False

        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    async def clear_task_memory(self, task_id: str) -> int:
        """清除指定任务的所有临时记忆

        Args:
            task_id: 任务 ID

        Returns:
            删除的条目数量
        """
        if not task_id:
            return 0

        with self._lock:
            to_delete = [k for k, v in self._store.items() if v.task_id == task_id]
            for k in to_delete:
                del self._store[k]

            if to_delete:
                logger.info("[ShortTermMemory] 清除任务记忆: task_id=%s, count=%d", task_id, len(to_delete))

            return len(to_delete)

    async def clear_all(self) -> int:
        """清空所有临时记忆

        Returns:
            删除的条目数量
        """
        with self._lock:
            count = len(self._store)
            self._store.clear()
            logger.info("[ShortTermMemory] 已清空所有临时记忆: count=%d", count)
            return count

    # ── 过期清理 ──

    def cleanup_expired(self) -> int:
        """清理所有已过期的记忆

        Returns:
            清理的条目数量
        """
        now = time.time()
        with self._lock:
            to_delete = [
                k for k, v in self._store.items()
                if v.expires_at > 0 and now > v.expires_at
            ]

            for k in to_delete:
                del self._store[k]

            if to_delete:
                logger.debug("[ShortTermMemory] 清理过期记忆: count=%d", len(to_delete))

            return len(to_delete)

    def _evict_lru(self) -> None:
        """LRU 清理：删除最少访问的条目"""
        if not self._store:
            return

        # 找到最少访问且最老的条目
        candidates = [
            (k, v) for k, v in self._store.items()
            if not v.accessed
        ]

        if candidates:
            # 按创建时间排序，删除最老的
            candidates.sort(key=lambda x: x[1].created_at)
            k = candidates[0][0]
            del self._store[k]
            logger.debug("[ShortTermMemory] LRU 清理: key=%s", k)
        else:
            # 所有条目都被访问过，删除最老的
            oldest = min(self._store.items(), key=lambda x: x[1].created_at)
            del self._store[oldest[0]]
            logger.debug("[ShortTermMemory] LRU 清理（无未访问）: key=%s", oldest[0])

    # ── 统计信息 ──

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            now = time.time()
            expired = sum(
                1 for v in self._store.values()
                if v.expires_at > 0 and now > v.expires_at
            )

            return {
                "total_entries": len(self._store),
                "max_size": self._max_size,
                "expired_entries": expired,
                "active_entries": len(self._store) - expired,
                "usage_pct": round(len(self._store) / max(self._max_size, 1) * 100, 1),
            }

    def list_entries(self, include_expired: bool = False) -> list[dict[str, Any]]:
        """列出所有记忆条目

        Args:
            include_expired: 是否包含已过期的条目

        Returns:
            条目信息列表
        """
        now = time.time()
        with self._lock:
            entries = []
            for k, v in self._store.items():
                if not include_expired and v.expires_at > 0 and now > v.expires_at:
                    continue

                entries.append({
                    "key": v.key,
                    "created_at": v.created_at,
                    "expires_at": v.expires_at,
                    "task_id": v.task_id,
                    "ttl_remaining": max(0, v.expires_at - now) if v.expires_at > 0 else None,
                    "content_type": type(v.content).__name__,
                })

            return entries
