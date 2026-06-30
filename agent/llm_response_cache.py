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
import json
import uuid
import threading
from typing import Optional, Dict, Any
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone

from agent.caching.multi_level_cache import MultiLevelCache

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



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
    - LRU 淘汰策略（基于 MultiLevelCache）
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
        # 使用 MultiLevelCache 作为底层存储（仅 L1 内存层）
        self._cache = MultiLevelCache(l1_max_size=max_size, l1_ttl=ttl_seconds, l2_enabled=False)
        self._lock = threading.Lock()

        # 用于区分"键不存在"和"键已过期/被淘汰"的追踪集合
        self._known_hashes: set = set()

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

    @property
    def cache_size(self) -> int:
        """获取当前缓存大小"""
        return self._cache.get_stats().get('l1_size', 0)

    def _hash_prompt(self, prompt: str) -> str:
        """对提示词进行哈希"""
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

    def _classify_prompt(self, prompt: str) -> str:
        """分类提示词类型"""
        prompt_lower = prompt.lower()

        # 帮助请求（优先级最高）
        if any(kw in prompt_lower for kw in ['帮助', 'help me']):
            return 'help_request'

        # 状态查询
        if any(kw in prompt_lower for kw in ['你好吗', '怎么样', '状态', 'status', 'health']):
            return 'status_query'

        # 问候语
        greetings = ['hello', 'hi', '你好', '嗨', '早上好', '下午好', '晚上好', 'hello,', 'hi,']
        if any(greeting in prompt_lower for greeting in greetings) or len(prompt) < 10:
            return 'greeting'

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
            # 先检查是否在已知集合中（区分"不存在"和"已过期"）
            if prompt_hash not in self._known_hashes:
                self.total_misses += 1
                logger.debug(f"[LLMCache] 未命中: {len(prompt)} chars")
                return None

            # 从 MultiLevelCache 获取（内部处理过期检查）
            result = self._cache.get(prompt_hash)
            if result is None:
                # 键在 _known_hashes 中但缓存返回 None → 已过期
                self._known_hashes.discard(prompt_hash)
                self.total_misses += 1
                self.total_evictions += 1
                logger.debug(f"[LLMCache] 已过期: {len(prompt)} chars")
                return None

            # 命中：更新按类型统计
            self.total_hits += 1
            hit_type = self._classify_prompt(prompt)
            self.hits_by_type[hit_type] = self.hits_by_type.get(hit_type, 0) + 1

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self.total_hit_time_ms += elapsed_ms

            logger.info(
                f"[LLMCache] ✅ 命中: type={hit_type}, "
                f"time={elapsed_ms:.2f}ms"
            )

            return result

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
            was_known = prompt_hash in self._known_hashes
            had_eviction = False

            if not was_known:
                # 检查缓存是否已满，若是则 set 会触发 LRU 淘汰
                if self._cache.get_stats()['l1_size'] >= self.max_size:
                    self.total_evictions += 1
                    had_eviction = True

            # 先写入缓存（LRU 淘汰在此发生）
            self._cache.set(prompt_hash, response, ttl_seconds=ttl)

            # 淘汰发生后同步 _known_hashes，移除已被 LRU 淘汰的键
            if had_eviction:
                self._known_hashes &= set(self._cache.get_l1_keys())

            self._known_hashes.add(prompt_hash)
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

        cache_size = self._cache.get_stats().get('l1_size', 0)

        return {
            'total_hits': self.total_hits,
            'total_misses': self.total_misses,
            'total_puts': self.total_puts,
            'total_evictions': self.total_evictions,
            'hit_rate': f"{hit_rate:.1f}%",
            'cache_size': cache_size,
            'avg_hit_time_ms': f"{avg_hit_time:.2f}",
            'hits_by_type': self.hits_by_type.copy()
        }

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()
            self._known_hashes.clear()
            logger.info("[LLMCache] 🗑️ 缓存已清空")


class AsyncSaveMonitor:
    """异步保存监控器

    特性：
    - 使用 OrderedDict 提高查询效率
    - 追踪异步保存任务
    - 记录耗时和成功率
    - 详细的性能日志
    """

    def __init__(self, max_records: int = 1000):
        self.max_records = max_records
        self.records: OrderedDict[str, AsyncSaveRecord] = OrderedDict()  # 使用 OrderedDict 提高查询效率
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
            # 使用 OrderedDict 按插入顺序存储，便于快速查找和顺序遍历
            self.records[task_id] = record
            # 保持记录数量限制
            while len(self.records) > self.max_records:
                self.records.popitem(last=False)  # 移除最早的记录

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
            # 使用 OrderedDict 的直接键查找，O(1) 复杂度
            if task_id not in self.records:
                logger.warning(f"[AsyncSave] 任务未找到: {task_id}")
                return
            
            record = self.records[task_id]
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
        """获取最近的保存记录
        
        使用 OrderedDict 的有序性，返回最后 n 条记录
        """
        records_list = list(self.records.values())
        return records_list[-n:] if len(records_list) > n else records_list


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


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "llm_response_cache",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
