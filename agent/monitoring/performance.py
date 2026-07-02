"""
性能监控与日志模块

合并自：
- agent/performance_monitor.py: 初始化性能追踪、运行时采样、告警管理
- agent/performance_logging.py: LLM 响应缓存、性能日志埋点
"""

import hashlib
import json
import time
import threading
import logging
import uuid
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from collections import OrderedDict, deque
from datetime import datetime, timezone

# 结构化日志必需：get_trace_id() 提供上下文追踪 ID
# set_trace_id() 用于跨线程传递 trace_id（ContextVar 不自动继承到子线程）
from agent.monitoring.tracing import get_trace_id, set_trace_id

logger = logging.getLogger(__name__)


# ============================================================================
# 性能监控基础
# ============================================================================

@dataclass
class ModuleInitRecord:
    """模块初始化记录"""
    name: str
    start_time: float
    end_time: float = 0.0
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""

    def finish(self):
        """标记模块初始化完成"""
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000

    def __str__(self):
        status = "✅" if self.success else "❌"
        return f"{status} {self.name}: {self.duration_ms:.2f}ms"


class InitPerformanceTracker:
    """初始化性能追踪器"""

    def __init__(self):
        self.records: Dict[str, ModuleInitRecord] = {}
        self.start_time = time.time()

    def start_module(self, module_name: str):
        """开始追踪某个模块的初始化"""
        self.records[module_name] = ModuleInitRecord(
            name=module_name,
            start_time=time.time()
        )
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "performance",
            "action": "module_init_start",
            "duration_ms": 0,
            "target_module": module_name,
        }, ensure_ascii=False))

    def finish_module(self, module_name: str, success: bool = True, error: str = ""):
        """完成某个模块的初始化追踪"""
        if module_name in self.records:
            record = self.records[module_name]
            record.finish()
            record.success = success
            record.error = error
            status = "成功" if success else f"失败: {error}"
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "performance",
                "action": "module_init_complete",
                "duration_ms": record.duration_ms,
                "target_module": module_name,
                "success": success,
                "status": status,
            }, ensure_ascii=False))

    def get_total_time(self) -> float:
        """获取总初始化时间（毫秒）"""
        return (time.time() - self.start_time) * 1000

    def get_summary(self) -> str:
        """生成性能总结报告"""
        lines = []
        lines.append("=" * 60)
        lines.append("初始化性能总结")
        lines.append("=" * 60)

        sorted_records = sorted(
            self.records.values(),
            key=lambda r: r.duration_ms,
            reverse=True
        )
        total_ms = self.get_total_time()
        lines.append(f"\n总初始化时间: {total_ms:.2f}ms")
        lines.append("\n各模块耗时（按耗时排序）:")

        for record in sorted_records:
            percentage = (record.duration_ms / total_ms * 100) if total_ms > 0 else 0
            lines.append(f"  {record}")
            lines.append(f"    占比: {percentage:.1f}%")

        lines.append("\n关键指标:")
        lines.append(f"  模块总数: {len(self.records)}")
        lines.append(f"  成功数: {sum(1 for r in self.records.values() if r.success)}")
        lines.append(f"  失败数: {sum(1 for r in self.records.values() if not r.success)}")

        if sorted_records:
            bottleneck = sorted_records[0]
            percentage = (bottleneck.duration_ms / total_ms * 100) if total_ms > 0 else 0
            lines.append(f"\n主要瓶颈: {bottleneck.name} ({bottleneck.duration_ms:.2f}ms, {percentage:.1f}%)")

        lines.append("=" * 60)
        return "\n".join(lines)

    def print_summary(self):
        """打印性能总结"""
        print(self.get_summary())

    def get_bottlenecks(self, threshold_ms: float = 50.0) -> List[ModuleInitRecord]:
        """获取耗时超过阈值的瓶颈模块"""
        return [r for r in self.records.values() if r.duration_ms > threshold_ms]

    def get_timeline(self) -> List[Dict]:
        """获取初始化时间线"""
        timeline = []
        for name, record in sorted(self.records.items(), key=lambda x: x[1].start_time):
            timeline.append({
                "module": name,
                "start": record.start_time,
                "end": record.end_time,
                "duration_ms": record.duration_ms,
                "success": record.success
            })
        return timeline


class Timer:
    """简单的计时器类"""

    def __init__(self, name: str = ""):
        self.name = name
        self.start_time = time.time()
        self.end_time = None
        self.elapsed = 0.0

    def stop(self):
        """停止计时器"""
        self.end_time = time.time()
        self.elapsed = self.end_time - self.start_time
        return self.elapsed

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, *args):
        """上下文管理器出口"""
        self.stop()
        if self.name:
            logger.debug(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "performance",
                "action": "timer_elapsed",
                "duration_ms": self.elapsed * 1000,
                "timer_name": self.name,
                "elapsed_seconds": self.elapsed,
            }, ensure_ascii=False))


def log_module_load_time(module_name: str, elapsed_time: float):
    """记录模块加载时间"""
    logger.info(json.dumps({
        "trace_id": get_trace_id(),
        "module_name": "performance",
        "action": "module_loaded",
        "duration_ms": elapsed_time * 1000,
        "target_module": module_name,
        "elapsed_seconds": elapsed_time,
    }, ensure_ascii=False))


def get_performance_recorder():
    """获取性能记录器实例"""
    return InitPerformanceTracker()


# ============================================================================
# 运行时性能采样
# ============================================================================

class RuntimeSampler:
    """运行时性能采样器

    特性：
    - 周期性采样系统性能指标
    - 记录采样历史
    - 支持阈值告警
    - 线程安全的采样记录
    """

    def __init__(self, sample_interval: float = 1.0, max_samples: int = 3600):
        self.sample_interval = sample_interval
        self.max_samples = max_samples
        self.samples: deque = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self._sampling = False
        self._sampler_thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable] = []
        # 后台线程专属 trace_id（ContextVar 不自动继承到子线程，需手动设置）
        self._sampler_trace_id = f"perf-sampler-{uuid.uuid4().hex[:16]}"

    def add_alert_callback(self, callback: Callable[[Dict], None]):
        """添加告警回调函数"""
        self._callbacks.append(callback)

    def start(self):
        """启动采样"""
        if self._sampling:
            return
        self._sampling = True
        self._sampler_thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._sampler_thread.start()
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "performance",
            "action": "sampler_start",
            "duration_ms": 0,
            "sample_interval_s": self.sample_interval,
        }, ensure_ascii=False))

    def stop(self):
        """停止采样"""
        self._sampling = False
        if self._sampler_thread:
            self._sampler_thread.join(timeout=2.0)
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "performance",
            "action": "sampler_stop",
            "duration_ms": 0,
        }, ensure_ascii=False))

    def _sample_loop(self):
        """采样循环"""
        # 后台线程入口：设置专属 trace_id，确保日志可追踪
        set_trace_id(self._sampler_trace_id)
        while self._sampling:
            sample = self._collect_sample()
            with self._lock:
                self.samples.append(sample)
            for callback in self._callbacks:
                try:
                    callback(sample)
                except Exception as e:
                    logger.error(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "performance",
                        "action": "sampler_callback_error",
                        "duration_ms": 0,
                        "error": str(e),
                    }, ensure_ascii=False))
            time.sleep(self.sample_interval)

    def _collect_sample(self) -> Dict:
        """收集采样数据"""
        try:
            import psutil
            return {
                'timestamp': time.time(),
                'cpu_percent': psutil.cpu_percent(interval=0.1),
                'memory_percent': psutil.virtual_memory().percent,
                'memory_used_mb': psutil.virtual_memory().used / (1024 * 1024),
            }
        except ImportError:
            return {
                'timestamp': time.time(),
                'cpu_percent': 0.0,
                'memory_percent': 0.0,
                'memory_used_mb': 0.0,
            }

    def get_samples(self, last_n: Optional[int] = None) -> List[Dict]:
        """获取采样数据"""
        with self._lock:
            if last_n is None:
                return list(self.samples)
            return list(self.samples)[-last_n:]

    def get_average(self, metric: str) -> float:
        """获取指标平均值"""
        with self._lock:
            if not self.samples:
                return 0.0
            values = [s.get(metric, 0.0) for s in self.samples]
            return sum(values) / len(values)

    def get_summary(self) -> Dict:
        """获取采样摘要"""
        with self._lock:
            if not self.samples:
                return {}
            samples_list = list(self.samples)
            cpu_values = [s.get('cpu_percent', 0.0) for s in samples_list]
            mem_values = [s.get('memory_percent', 0.0) for s in samples_list]
            return {
                'sample_count': len(samples_list),
                'duration_seconds': samples_list[-1]['timestamp'] - samples_list[0]['timestamp'],
                'cpu_avg': sum(cpu_values) / len(cpu_values),
                'cpu_max': max(cpu_values),
                'memory_avg': sum(mem_values) / len(mem_values),
                'memory_max': max(mem_values),
            }


# ============================================================================
# 性能告警规则配置
# ============================================================================

@dataclass
class AlertConfig:
    """告警规则配置"""
    cpu_threshold: float = 80.0
    cpu_alert_level: str = "warning"
    memory_threshold: float = 85.0
    memory_alert_level: str = "warning"
    sustained_threshold_count: int = 5
    sustained_check_window: int = 10
    cooldown_seconds: float = 60.0
    enable_logging: bool = True
    enable_callback: bool = True


class PerformanceAlertManager:
    """性能告警管理器"""

    def __init__(self, config: Optional[AlertConfig] = None):
        self.config = config or AlertConfig()
        self._last_alert_time: Dict[str, float] = {}
        self._alert_callbacks: List[Callable[[str, Dict], None]] = []
        self._sustained_counter: Dict[str, int] = {'cpu': 0, 'memory': 0}

    def add_alert_callback(self, callback: Callable[[str, Dict], None]):
        """添加告警回调函数"""
        self._alert_callbacks.append(callback)

    def check_alerts(self, sample: Dict, sampler: Optional[RuntimeSampler] = None) -> List[Dict]:
        """检查采样数据是否触发告警"""
        alerts = []
        current_time = time.time()
        cpu_alert = self._check_cpu_alert(sample, current_time)
        if cpu_alert:
            alerts.append(cpu_alert)
        memory_alert = self._check_memory_alert(sample, current_time)
        if memory_alert:
            alerts.append(memory_alert)
        if sampler:
            sustained_alerts = self._check_sustained_alert(sampler, current_time)
            alerts.extend(sustained_alerts)
        for alert in alerts:
            self._trigger_alert(alert)
        return alerts

    def _check_cpu_alert(self, sample: Dict, current_time: float) -> Optional[Dict]:
        cpu_percent = sample.get('cpu_percent', 0.0)
        if cpu_percent >= self.config.cpu_threshold:
            if self._is_in_cooldown('cpu', current_time):
                return None
            self._last_alert_time['cpu'] = current_time
            return {
                'alert_type': 'cpu_high', 'level': self.config.cpu_alert_level,
                'metric': 'cpu_percent', 'value': cpu_percent,
                'threshold': self.config.cpu_threshold, 'timestamp': current_time,
                'message': f"CPU 使用率过高: {cpu_percent:.1f}% (阈值: {self.config.cpu_threshold:.1f}%)"
            }
        return None

    def _check_memory_alert(self, sample: Dict, current_time: float) -> Optional[Dict]:
        memory_percent = sample.get('memory_percent', 0.0)
        if memory_percent >= self.config.memory_threshold:
            if self._is_in_cooldown('memory', current_time):
                return None
            self._last_alert_time['memory'] = current_time
            return {
                'alert_type': 'memory_high', 'level': self.config.memory_alert_level,
                'metric': 'memory_percent', 'value': memory_percent,
                'threshold': self.config.memory_threshold, 'timestamp': current_time,
                'message': f"内存使用率过高: {memory_percent:.1f}% (阈值: {self.config.memory_threshold:.1f}%)"
            }
        return None

    def _check_sustained_alert(self, sampler: RuntimeSampler, current_time: float) -> List[Dict]:
        alerts = []
        recent_samples = sampler.get_samples(last_n=self.config.sustained_check_window)
        if len(recent_samples) < self.config.sustained_threshold_count:
            return alerts
        cpu_high_count = sum(1 for s in recent_samples if s.get('cpu_percent', 0) >= self.config.cpu_threshold)
        if cpu_high_count >= self.config.sustained_threshold_count:
            if not self._is_in_cooldown('cpu_sustained', current_time):
                self._last_alert_time['cpu_sustained'] = current_time
                avg_cpu = sum(s.get('cpu_percent', 0) for s in recent_samples) / len(recent_samples)
                alerts.append({
                    'alert_type': 'cpu_sustained_high', 'level': 'critical',
                    'metric': 'cpu_percent', 'value': avg_cpu,
                    'threshold': self.config.cpu_threshold,
                    'sustained_count': cpu_high_count, 'timestamp': current_time,
                    'message': f"CPU 持续高负载: 连续 {cpu_high_count} 次采样超过阈值，平均值: {avg_cpu:.1f}%"
                })
        memory_high_count = sum(1 for s in recent_samples if s.get('memory_percent', 0) >= self.config.memory_threshold)
        if memory_high_count >= self.config.sustained_threshold_count:
            if not self._is_in_cooldown('memory_sustained', current_time):
                self._last_alert_time['memory_sustained'] = current_time
                avg_memory = sum(s.get('memory_percent', 0) for s in recent_samples) / len(recent_samples)
                alerts.append({
                    'alert_type': 'memory_sustained_high', 'level': 'critical',
                    'metric': 'memory_percent', 'value': avg_memory,
                    'threshold': self.config.memory_threshold,
                    'sustained_count': memory_high_count, 'timestamp': current_time,
                    'message': f"内存持续高负载: 连续 {memory_high_count} 次采样超过阈值，平均值: {avg_memory:.1f}%"
                })
        return alerts

    def _is_in_cooldown(self, alert_type: str, current_time: float) -> bool:
        last_time = self._last_alert_time.get(alert_type, 0)
        return (current_time - last_time) < self.config.cooldown_seconds

    def _trigger_alert(self, alert: Dict):
        if self.config.enable_logging:
            level = alert.get('level', 'warning')
            message = alert.get('message', '')
            if level == 'critical':
                logger.critical("[PerformanceAlert] %s", message)
            elif level == 'warning':
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "performance",
                    "action": "alert_warning",
                    "duration_ms": 0,
                    "alert_level": level,
                    "message": message,
                }, ensure_ascii=False))
            else:
                logger.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "performance",
                    "action": "alert_info",
                    "duration_ms": 0,
                    "alert_level": level,
                    "message": message,
                }, ensure_ascii=False))
        if self.config.enable_callback:
            alert_type = alert.get('alert_type', '')
            for callback in self._alert_callbacks:
                try:
                    callback(alert_type, alert)
                except Exception as e:
                    logger.error(json.dumps({
                        "trace_id": get_trace_id(),
                        "module_name": "performance",
                        "action": "alert_callback_error",
                        "duration_ms": 0,
                        "alert_type": alert_type,
                        "error": str(e),
                    }, ensure_ascii=False))


def create_default_alert_callback() -> Callable[[str, Dict], None]:
    """创建默认告警回调函数"""
    def default_callback(alert_type: str, alert: Dict):
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "performance",
            "action": "alert_triggered",
            "duration_ms": 0,
            "alert_type": alert_type,
            "message": alert.get('message', ''),
        }, ensure_ascii=False))
    return default_callback


_alert_manager: Optional[PerformanceAlertManager] = None


def get_alert_manager(config: Optional[AlertConfig] = None) -> PerformanceAlertManager:
    """获取全局告警管理器实例"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = PerformanceAlertManager(config)
    return _alert_manager


def setup_performance_monitoring(
    sample_interval: float = 5.0,
    alert_config: Optional[AlertConfig] = None
) -> tuple:
    """设置性能监控和告警系统"""
    sampler = RuntimeSampler(sample_interval=sample_interval)
    alert_manager = PerformanceAlertManager(alert_config)

    def alert_check_callback(sample: Dict):
        alert_manager.check_alerts(sample, sampler)

    sampler.add_alert_callback(alert_check_callback)
    alert_manager.add_alert_callback(create_default_alert_callback())
    logger.info(json.dumps({
        "trace_id": get_trace_id(),
        "module_name": "performance",
        "action": "system_configured",
        "duration_ms": 0,
        "sample_interval_s": sample_interval,
    }, ensure_ascii=False))
    return sampler, alert_manager


# ============================================================================
# LLM 响应缓存
# ============================================================================

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
        self.hits += 1
        self.total_hit_time_ms += elapsed_ms

    def record_miss(self):
        self.misses += 1

    def record_save(self, elapsed_ms: float):
        self.total_save_time_ms += elapsed_ms

    def record_eviction(self):
        self.evictions += 1

    def get_hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def get_avg_hit_time_ms(self) -> float:
        return self.total_hit_time_ms / self.hits if self.hits > 0 else 0.0

    def get_avg_save_time_ms(self) -> float:
        return self.total_save_time_ms / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0.0

    def to_dict(self) -> dict:
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
        # 边界校验：max_size 必须 >= 1，否则 put() 时 len(cache) >= max_size 恒为 True，
        # 首次 put 在空 OrderedDict 上调用 popitem(last=False) 会抛 KeyError。
        # 详见 tests/boundary/test_performance_logging_boundary.py
        if not isinstance(max_size, int) or max_size < 1:
            raise ValueError(
                f"max_size 必须是 >= 1 的整数，收到: {max_size!r}"
            )
        self.max_size = max_size
        self.default_ttl = ttl_seconds
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.stats = LLMCacheStats()
        self.hits_by_pattern: dict[str, int] = {}
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "performance",
            "action": "llm_cache_init",
            "duration_ms": 0,
            "max_size": max_size,
            "ttl_seconds": ttl_seconds,
        }, ensure_ascii=False))

    def _hash_prompt(self, prompt: str) -> str:
        return hashlib.sha256(prompt.encode('utf-8')).hexdigest()

    def get(self, prompt: str) -> Optional[str]:
        start_time = time.perf_counter()
        prompt_hash = self._hash_prompt(prompt)
        if prompt_hash not in self.cache:
            self.stats.record_miss()
            return None
        entry = self.cache[prompt_hash]
        if entry.is_expired():
            del self.cache[prompt_hash]
            self.stats.record_miss()
            return None
        entry.hit_count += 1
        self.cache.move_to_end(prompt_hash)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self.stats.record_hit(elapsed_ms)
        pattern = self._classify_prompt(prompt)
        self.hits_by_pattern[pattern] = self.hits_by_pattern.get(pattern, 0) + 1
        return entry.response

    def put(self, prompt: str, response: str, ttl_seconds: Optional[int] = None):
        start_time = time.perf_counter()
        prompt_hash = self._hash_prompt(prompt)
        ttl = ttl_seconds or self.default_ttl
        if prompt_hash in self.cache:
            self.cache.move_to_end(prompt_hash)
            old_entry = self.cache[prompt_hash]
            entry = CacheEntry(
                prompt_hash=prompt_hash, response=response,
                timestamp=time.time(), ttl_seconds=ttl,
                hit_count=old_entry.hit_count,
                generation_time_ms=old_entry.generation_time_ms
            )
        else:
            if len(self.cache) >= self.max_size:
                evicted_key, evicted_entry = self.cache.popitem(last=False)
                self.stats.record_eviction()
            entry = CacheEntry(
                prompt_hash=prompt_hash, response=response,
                timestamp=time.time(), ttl_seconds=ttl
            )
        self.cache[prompt_hash] = entry
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        self.stats.record_save(elapsed_ms)

    def _classify_prompt(self, prompt: str) -> str:
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
        size = len(self.cache)
        self.cache.clear()
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "performance",
            "action": "llm_cache_clear",
            "duration_ms": 0,
            "cleared_size": size,
        }, ensure_ascii=False))

    def get_stats(self) -> dict:
        return self.stats.to_dict()

    def get_top_patterns(self, top_n: int = 5) -> list:
        sorted_patterns = sorted(self.hits_by_pattern.items(), key=lambda x: x[1], reverse=True)
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
        record_id = f"{task_type}_{task_id}_{time.time()}"
        self.saves.append({
            'id': record_id, 'type': task_type,
            'start_time': time.perf_counter(), 'status': 'running'
        })
        if len(self.saves) > self.max_records:
            self.saves.pop(0)
        return record_id

    def record_save_end(self, record_id: str, success: bool = True, error: Optional[str] = None):
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
        else:
            self.failed += 1

    def get_stats(self) -> dict:
        return {
            'total_saves': self.total_saves,
            'failed': self.failed,
            'success_rate': f"{(self.total_saves - self.failed) / self.total_saves * 100:.1f}%" if self.total_saves > 0 else "N/A",
            'avg_time_ms': f"{self.total_time_ms / self.total_saves:.2f}" if self.total_saves > 0 else "N/A",
            'pending': sum(1 for s in self.saves if s['status'] == 'running')
        }

    def get_recent_saves(self, n: int = 10) -> list:
        return self.saves[-n:]


class PerformanceLogger:
    """性能日志记录器"""

    def __init__(self):
        self.records: list[dict] = []
        self.max_records = 10000

    def log(self, operation: str, elapsed_ms: float, metadata: Optional[dict] = None):
        record = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'operation': operation,
            'elapsed_ms': elapsed_ms,
            'metadata': metadata or {}
        }
        self.records.append(record)
        if len(self.records) > self.max_records:
            self.records.pop(0)
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "performance",
            "action": "perf_operation",
            "duration_ms": elapsed_ms,
            "operation": operation,
            "metadata": metadata or {},
        }, ensure_ascii=False))

    def get_stats(self, operation: Optional[str] = None) -> dict:
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


# 全局实例
llm_cache = LLMCache()
async_save_monitor = AsyncSaveMonitor()
perf_logger = PerformanceLogger()
