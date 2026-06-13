"""LLM 响应缓存与日志埋点模块

功能：
- LLM 响应缓存，减少重复请求
- 详细的性能日志埋点
- 异步保存监控
"""

import hashlib
import time
import logging
from collections import OrderedDict
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""
    prompt_hash: str
    response: str
    timestamp: float
    ttl_seconds: int
    hit_count: int = 0
    generation_time_ms: float = 0.0

    def is_expired(self) -> bool:
        """检查是否过期"""
        return time.time() - self.timestamp > self.ttl_seconds


class LLMCacheStats:
    """LLM 缓存统计"""

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.total_save_time_ms = 0.0
        self.total_hit_time_ms = 0.0

    def record_hit(self, elapsed_ms: float):
        """记录缓存命中"""
        self.hits += 1
        self.total_hit_time_ms += elapsed_ms

    def record_miss(self):
        """记录缓存未命中"""
        self.misses += 1

    def record_save(self, elapsed_ms: float):
        """记录保存时间"""
        self.total_save_time_ms += elapsed_ms

    def record_eviction(self):
        """记录淘汰"""
        self.evictions += 1

    def get_hit_rate(self) -> float:
        """获取命中率"""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def get_avg_hit_time_ms(self) -> float:
        """获取平均命中时间"""
        return self.total_hit_time_ms / self.hits if self.hits > 0 else 0.0

    def get_avg_save_time_ms(self) -> float:
        """获取平均保存时间"""
        return self.total_save_time_ms / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0.0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': f"{self.get_hit_rate():.2%}",
            'avg_hit_time_ms': f"{self.get_avg_hit_time_ms():.2f}",
            'avg_save_time_ms': f"{self.get_avg_save_time_ms():.2f}",
            'evictions': self.evictions
        }


class LLMCache:
    """LLM 响应缓存

    特性：
    - LRU 淘汰策略
    - TTL 过期机制
    - 详细的性能统计
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        """
        初始化缓存

        Args:
            max_size: 最大缓存条目数
            ttl_seconds: 默认 TTL（秒）
        """
        self.max_size = max_size
        self.default_ttl = ttl_seconds
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.stats = LLMCacheStats()
        self.hits_by_pattern: dict[str, int] = {}

        logger.info(
            f"[LLM Cache] 初始化完成: max_size={max_size}, "
            f"ttl={ttl_seconds}s"
        )

    def _hash_prompt(self, prompt: str) -> str:
        """对提示词进行哈希"""
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

    def get(self, prompt: str) -> Optional[str]:
        """
        获取缓存的响应

        Args:
            prompt: 提示词

        Returns:
            缓存的响应，如果未命中返回 None
        """
        start_time = time.perf_counter()
        prompt_hash = self._hash_prompt(prompt)

        if prompt_hash not in self.cache:
            self.stats.record_miss()
            logger.debug(
                f"[LLM Cache] 未命中: hash={prompt_hash[:16]}..."
            )
            return None

        entry = self.cache[prompt_hash]

        if entry.is_expired():
            del self.cache[prompt_hash]
            self.stats.record_miss()
            logger.debug(
                f"[LLM Cache] 已过期: hash={prompt_hash[:16]}..."
            )
            return None

        entry.hit_count += 1
        self.cache.move_to_end(prompt_hash)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self.stats.record_hit(elapsed_ms)

        pattern = self._classify_prompt(prompt)
        self.hits_by_pattern[pattern] = self.hits_by_pattern.get(pattern, 0) + 1

        logger.info(
            f"[LLM Cache] ✅ 命中: pattern={pattern}, "
            f"hit_count={entry.hit_count}, "
            f"elapsed={elapsed_ms:.2f}ms"
        )

        return entry.response

    def put(self, prompt: str, response: str, ttl_seconds: Optional[int] = None):
        """
        保存响应到缓存

        Args:
            prompt: 提示词
            response: 响应内容
            ttl_seconds: 自定义 TTL，如果为 None 则使用默认值
        """
        start_time = time.perf_counter()
        prompt_hash = self._hash_prompt(prompt)
        ttl = ttl_seconds or self.default_ttl

        if prompt_hash in self.cache:
            self.cache.move_to_end(prompt_hash)
            old_entry = self.cache[prompt_hash]
            entry = CacheEntry(
                prompt_hash=prompt_hash,
                response=response,
                timestamp=time.time(),
                ttl_seconds=ttl,
                hit_count=old_entry.hit_count,
                generation_time_ms=old_entry.generation_time_ms
            )
        else:
            if len(self.cache) >= self.max_size:
                evicted_key, evicted_entry = self.cache.popitem(last=False)
                self.stats.record_eviction()
                logger.info(
                    f"[LLM Cache] 🔄 淘汰: hash={evicted_key[:16]}..., "
                    f"hit_count={evicted_entry.hit_count}"
                )

            entry = CacheEntry(
                prompt_hash=prompt_hash,
                response=response,
                timestamp=time.time(),
                ttl_seconds=ttl
            )

        self.cache[prompt_hash] = entry

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self.stats.record_save(elapsed_ms)

        logger.info(
            f"[LLM Cache] 💾 保存: hash={prompt_hash[:16]}..., "
            f"size={len(response)} chars, "
            f"ttl={ttl}s, "
            f"elapsed={elapsed_ms:.2f}ms"
        )

    def _classify_prompt(self, prompt: str) -> str:
        """分类提示词类型"""
        prompt_lower = prompt.lower()

        if any(kw in prompt_lower for kw in ['hello', 'hi', '你好', '嗨']):
            return 'greeting'
        elif any(kw in prompt_lower for kw in ['how are you', '怎么样', '状态']):
            return 'status_query'
        elif any(kw in prompt_lower for kw in ['help', '帮助', 'help me']):
            return 'help_request'
        elif len(prompt) < 20:
            return 'short'
        elif len(prompt) < 100:
            return 'medium'
        else:
            return 'long'

    def clear(self):
        """清空缓存"""
        size = len(self.cache)
        self.cache.clear()
        logger.info(f"[LLM Cache] 🗑️ 清空缓存: {size} 条目")

    def get_stats(self) -> dict:
        """获取缓存统计"""
        return self.stats.to_dict()

    def get_top_patterns(self, top_n: int = 5) -> list:
        """获取最常见的提示词类型"""
        sorted_patterns = sorted(
            self.hits_by_pattern.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return sorted_patterns[:top_n]


class AsyncSaveMonitor:
    """异步保存监控器"""

    def __init__(self):
        self.saves: list[dict] = []
        self.max_records = 1000
        self.total_saves = 0
        self.total_time_ms = 0.0
        self.failed = 0

    def record_save_start(self, task_type: str, task_id: str) -> str:
        """记录保存开始"""
        record_id = f"{task_type}_{task_id}_{time.time()}"
        self.saves.append({
            'id': record_id,
            'type': task_type,
            'start_time': time.perf_counter(),
            'status': 'running'
        })

        if len(self.saves) > self.max_records:
            self.saves.pop(0)

        logger.debug(
            f"[AsyncSave] ▶️ 开始: type={task_type}, id={record_id}"
        )

        return record_id

    def record_save_end(self, record_id: str, success: bool = True,
                       error: Optional[str] = None):
        """记录保存完成"""
        elapsed_ms = 0.0

        for record in reversed(self.saves):
            if record['id'] == record_id:
                elapsed_ms = (time.perf_counter() - record['start_time']) * 1000
                record['elapsed_ms'] = elapsed_ms
                record['status'] = 'success' if success else 'failed'
                record['end_time'] = datetime.now(timezone.utc).isoformat()

                if error:
                    record['error'] = error

                break

        self.total_saves += 1
        if success:
            self.total_time_ms += elapsed_ms
            logger.info(
                f"[AsyncSave] ✅ 完成: id={record_id}, "
                f"elapsed={elapsed_ms:.2f}ms, "
                f"type={self.saves[-1].get('type', 'unknown')}"
            )
        else:
            self.failed += 1
            logger.warning(
                f"[AsyncSave] ❌ 失败: id={record_id}, "
                f"elapsed={elapsed_ms:.2f}ms, "
                f"error={error}"
            )

    def get_stats(self) -> dict:
        """获取保存统计"""
        return {
            'total_saves': self.total_saves,
            'failed': self.failed,
            'success_rate': f"{(self.total_saves - self.failed) / self.total_saves * 100:.1f}%"
                if self.total_saves > 0 else "N/A",
            'avg_time_ms': f"{self.total_time_ms / self.total_saves:.2f}"
                if self.total_saves > 0 else "N/A",
            'pending': sum(1 for s in self.saves if s['status'] == 'running')
        }

    def get_recent_saves(self, n: int = 10) -> list:
        """获取最近的保存记录"""
        return self.saves[-n:]


class PerformanceLogger:
    """性能日志记录器"""

    def __init__(self):
        self.records: list[dict] = []
        self.max_records = 10000

    def log(self, operation: str, elapsed_ms: float,
            metadata: Optional[dict] = None):
        """记录性能数据"""
        record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'operation': operation,
            'elapsed_ms': elapsed_ms,
            'metadata': metadata or {}
        }

        self.records.append(record)

        if len(self.records) > self.max_records:
            self.records.pop(0)

        logger.info(
            f"[Perf] {operation}: {elapsed_ms:.2f}ms"
            + (f" | {metadata}" if metadata else "")
        )

    def get_stats(self, operation: Optional[str] = None) -> dict:
        """获取性能统计"""
        if operation:
            records = [r for r in self.records if r['operation'] == operation]
        else:
            records = self.records

        if not records:
            return {'count': 0, 'avg_ms': 0, 'min_ms': 0, 'max_ms': 0}

        elapsed_times = [r['elapsed_ms'] for r in records]

        return {
            'count': len(records),
            'avg_ms': sum(elapsed_times) / len(elapsed_times),
            'min_ms': min(elapsed_times),
            'max_ms': max(elapsed_times),
            'p95_ms': sorted(elapsed_times)[int(len(elapsed_times) * 0.95)]
                if len(elapsed_times) > 1 else elapsed_times[0]
        }


llm_cache = LLMCache()
async_save_monitor = AsyncSaveMonitor()
perf_logger = PerformanceLogger()
