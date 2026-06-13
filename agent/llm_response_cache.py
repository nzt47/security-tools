"""LLM 响应缓存与性能埋点模块

功能：
- LLM 响应缓存（LRU + TTL）
- 缓存命中率统计
- 详细的性能日志埋点
- 异步保存耗时追踪

使用方法：
```python
from agent.llm_response_cache import llm_cache, async_save_monitor

# 使用缓存
cached = llm_cache.get(user_input)
if cached:
    return cached

# 调用 LLM 并保存
response = call_llm(user_input)
llm_cache.put(user_input, response)
```
"""

import time
import hashlib
import logging
import threading
from typing import Optional, Dict, Any, Callable
from collections import OrderedDict
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


@dataclass
class AsyncSaveRecord:
    """异步保存记录"""
    task_id: str
    task_type: str
    start_time: float
    end_time: Optional[float] = None
    elapsed_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


class LLMResponseCache:
    """LLM 响应缓存

    特性：
    - LRU 淘汰策略
    - TTL 过期机制
    - 按提示词类型分类（问候语、状态查询、帮助请求）
    - 详细的性能统计
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        """
        初始化缓存

        Args:
            max_size: 最大缓存条目数
            ttl_seconds: 默认过期时间（秒）
        """
        self.max_size = max_size
        self.default_ttl = ttl_seconds
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

        # 统计信息
        self.total_hits = 0
        self.total_misses = 0
        self.total_puts = 0
        self.total_evictions = 0
        self.total_hit_time_ms = 0.0
        self.total_generation_time_ms = 0.0

        # 按类型统计
        self.hits_by_type: Dict[str, int] = {}

        logger.info(f"[LLMCache] 初始化完成，max_size={max_size}, ttl={ttl_seconds}s")

    def _hash_prompt(self, prompt: str) -> str:
        """对提示词进行哈希"""
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

    def _classify_prompt(self, prompt: str) -> str:
        """分类提示词类型"""
        prompt_lower = prompt.lower()

        # 问候语
        greetings = ['hello', 'hi', '你好', '嗨', '早上好', '下午好', '晚上好', 'hello,', 'hi,']
        if any(greeting in prompt_lower for greeting in greetings) or len(prompt) < 10:
            return 'greeting'

        # 状态查询
        if any(kw in prompt_lower for kw in ['你好吗', '怎么样', '状态', 'status', 'health']):
            return 'status_query'

        # 帮助请求
        if any(kw in prompt_lower for kw in ['帮助', 'help', 'help me']):
            return 'help_request'

        # 其他
        return 'other'

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

        with self._lock:
            if prompt_hash not in self.cache:
                self.total_misses += 1
                logger.debug(f"[LLMCache] 未命中: {len(prompt)} chars")
                return None

            entry = self.cache[prompt_hash]

            if entry.is_expired():
                del self.cache[prompt_hash]
                self.total_misses += 1
                self.total_evictions += 1
                logger.debug(f"[LLMCache] 已过期: {len(prompt)} chars")
                return None

            entry.hit_count += 1
            self.cache.move_to_end(prompt_hash)

            # 更新统计
            self.total_hits += 1
            hit_type = self._classify_prompt(prompt)
            self.hits_by_type[hit_type] = self.hits_by_type.get(hit_type, 0) + 1

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self.total_hit_time_ms += elapsed_ms

            logger.info(
                f"[LLMCache] ✅ 命中: type={hit_type}, "
                f"hits={entry.hit_count}, "
                f"time={elapsed_ms:.2f}ms"
            )

            return entry.response

    def put(self, prompt: str, response: str, ttl_seconds: Optional[int] = None):
        """
        保存响应到缓存

        Args:
            prompt: 提示词
            response: 响应内容
            ttl_seconds: 自定义过期时间，如果为 None 则使用默认值
        """
        start_time = time.perf_counter()
        prompt_hash = self._hash_prompt(prompt)
        ttl = ttl_seconds or self.default_ttl

        with self._lock:
            # 如果已存在，更新
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
                # 新条目
                if len(self.cache) >= self.max_size:
                    evicted_key, evicted_entry = self.cache.popitem(last=False)
                    self.total_evictions += 1
                    logger.info(
                        f"[LLMCache] 🔄 淘汰: hits={evicted_entry.hit_count}"
                    )

                entry = CacheEntry(
                    prompt_hash=prompt_hash,
                    response=response,
                    timestamp=time.time(),
                    ttl_seconds=ttl
                )

            self.cache[prompt_hash] = entry
            self.total_puts += 1

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            prompt_type = self._classify_prompt(prompt)

            logger.info(
                f"[LLMCache] 💾 保存: type={prompt_type}, "
                f"size={len(response)} chars, "
                f"ttl={ttl}s, "
                f"time={elapsed_ms:.2f}ms"
            )

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        total_requests = self.total_hits + self.total_misses
        hit_rate = self.total_hits / total_requests * 100 if total_requests > 0 else 0.0
        avg_hit_time = self.total_hit_time_ms / self.total_hits if self.total_hits > 0 else 0.0

        return {
            'total_hits': self.total_hits,
            'total_misses': self.total_misses,
            'total_puts': self.total_puts,
            'total_evictions': self.total_evictions,
            'hit_rate': f"{hit_rate:.1f}%",
            'cache_size': len(self.cache),
            'avg_hit_time_ms': f"{avg_hit_time:.2f}",
            'hits_by_type': self.hits_by_type.copy()
        }

    def clear(self):
        """清空缓存"""
        with self._lock:
            self.cache.clear()
            logger.info("[LLMCache] 🗑️ 缓存已清空")


class AsyncSaveMonitor:
    """异步保存监控器

    特性：
    - 追踪异步保存任务
    - 记录耗时和成功率
    - 详细的性能日志
    """

    def __init__(self, max_records: int = 1000):
        self.max_records = max_records
        self.records: list[AsyncSaveRecord] = []
        self._lock = threading.Lock()
        self._task_counter = 0

        # 统计信息
        self.total_saves = 0
        self.successful_saves = 0
        self.failed_saves = 0
        self.total_save_time_ms = 0.0

    def start_save(self, task_type: str) -> str:
        """
        开始保存任务

        Args:
            task_type: 任务类型（如 memory, blackbox, lifetrace）

        Returns:
            任务 ID
        """
        self._task_counter += 1
        task_id = f"{task_type}_{self._task_counter}"

        record = AsyncSaveRecord(
            task_id=task_id,
            task_type=task_type,
            start_time=time.perf_counter()
        )

        with self._lock:
            self.records.append(record)
            if len(self.records) > self.max_records:
                self.records.pop(0)

        logger.debug(f"[AsyncSave] ▶️ 开始: {task_id}")
        return task_id

    def end_save(self, task_id: str, success: bool = True, error: Optional[str] = None):
        """
        结束保存任务

        Args:
            task_id: 任务 ID
            success: 是否成功
            error: 错误信息（如果失败）
        """
        with self._lock:
            for record in reversed(self.records):
                if record.task_id == task_id:
                    record.end_time = time.perf_counter()
                    record.elapsed_ms = (record.end_time - record.start_time) * 1000
                    record.success = success
                    record.error = error

                    self.total_saves += 1
                    if success:
                        self.successful_saves += 1
                        self.total_save_time_ms += record.elapsed_ms
                    else:
                        self.failed_saves += 1

                    if success:
                        logger.info(
                            f"[AsyncSave] ✅ 完成: {task_id}, "
                            f"time={record.elapsed_ms:.2f}ms"
                        )
                    else:
                        logger.error(
                            f"[AsyncSave] ❌ 失败: {task_id}, "
                            f"error={error}, "
                            f"time={record.elapsed_ms:.2f}ms"
                        )
                    return

        logger.warning(f"[AsyncSave] 任务未找到: {task_id}")

    def get_stats(self) -> Dict[str, Any]:
        """获取保存统计信息"""
        success_rate = (
            self.successful_saves / self.total_saves * 100
            if self.total_saves > 0 else 0.0
        )
        avg_time = (
            self.total_save_time_ms / self.successful_saves
            if self.successful_saves > 0 else 0.0
        )

        return {
            'total_saves': self.total_saves,
            'successful_saves': self.successful_saves,
            'failed_saves': self.failed_saves,
            'success_rate': f"{success_rate:.1f}%",
            'avg_save_time_ms': f"{avg_time:.2f}"
        }

    def get_recent_records(self, n: int = 10) -> list[AsyncSaveRecord]:
        """获取最近的保存记录"""
        return self.records[-n:]


# 全局实例
llm_cache = LLMResponseCache()
async_save_monitor = AsyncSaveMonitor()


class PerformanceLogger:
    """性能日志记录器

    用于记录对话处理流程中的关键性能指标
    """

    def __init__(self):
        self.timings: list[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def log_timing(self, phase: str, start_time: float, metadata: Optional[Dict[str, Any]] = None):
        """
        记录某个阶段的耗时

        Args:
            phase: 阶段名称
            start_time: 开始时间（time.perf_counter()）
            metadata: 附加信息
        """
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        timing_data = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'phase': phase,
            'elapsed_ms': elapsed_ms,
            'metadata': metadata or {}
        }

        with self._lock:
            self.timings.append(timing_data)
            if len(self.timings) > 1000:
                self.timings.pop(0)

        logger.info(
            f"[Performance] {phase}: {elapsed_ms:.2f}ms"
            + (f" | {metadata}" if metadata else "")
        )

    def get_summary(self) -> Dict[str, Any]:
        """获取性能汇总信息"""
        if not self.timings:
            return {}

        phase_times: Dict[str, list[float]] = {}
        for timing in self.timings:
            phase = timing['phase']
            if phase not in phase_times:
                phase_times[phase] = []
            phase_times[phase].append(timing['elapsed_ms'])

        summary = {}
        for phase, times in phase_times.items():
            summary[phase] = {
                'count': len(times),
                'avg_ms': sum(times) / len(times),
                'min_ms': min(times),
                'max_ms': max(times)
            }

        return summary


# 全局性能日志记录器
perf_logger = PerformanceLogger()

