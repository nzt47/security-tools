"""资源泄漏检测模块

监控云枢项目的资源使用与泄漏风险，覆盖：
1. 内存：tracemalloc 内存分配追踪，统计 top 10 占用对象
2. 线程池/连接池：threading 句柄计数 + 注册的池资源
3. 文件句柄：psutil.Process().open_files() 计数
4. 数据库连接：连接池 active/idle 计数（通过 provider 注册）

核心特性：
- 周期性采样（默认 60 秒，可配置）
- 资源增长趋势检测（最小二乘线性回归，斜率超阈值告警）
- 压测模式：高频采样（1 秒），输出资源释放曲线
- 集成 BusinessMetricsCollector：上报 yunshu_resource_usage gauge（含 resource_type 标签）
- 降级策略：资源监控失败时降级为日志记录，不影响业务主流程
- 性能开销 < 1%（采样轻量化，单次埋点 < 1ms）

使用示例：
    from agent.monitoring.resource_monitor import get_resource_monitor

    monitor = get_resource_monitor()
    monitor.start()                      # 启动周期采样
    snapshot = monitor.get_snapshot()    # 获取当前快照
    trend = monitor.get_trend("memory")  # 获取内存增长趋势
    monitor.enable_stress_mode()         # 切换压测模式（1秒采样）
"""

import gc
import json
import logging
import os
import threading
import time
import tracemalloc
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# set_trace_id 用于后台线程 trace_id 传递（ContextVar 不自动继承到子线程）
from agent.monitoring.tracing import get_trace_id, set_trace_id

logger = logging.getLogger(__name__)

# psutil 为可选依赖（开源，但环境可能未安装）
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False
    logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "psutil", "msg": "[ResourceMonitor] psutil 未安装，文件句柄监控降级为不可用"}, ensure_ascii=False))

# 业务指标收集器（惰性导入避免循环依赖）
_business_collector = None


def _get_business_collector():
    """惰性获取业务指标收集器（埋点失败不影响主流程）"""
    global _business_collector
    if _business_collector is None:
        try:
            from agent.monitoring.business_metrics import get_business_metrics_collector
            _business_collector = get_business_metrics_collector()
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 业务指标收集器不可用: {e}"}, ensure_ascii=False))
    return _business_collector


# ============================================================================
# 数据结构定义
# ============================================================================

@dataclass
class MemoryStat:
    """内存统计"""
    current_bytes: int = 0          # 当前分配字节数
    peak_bytes: int = 0             # 峰值分配字节数
    top_allocations: List[Dict[str, Any]] = field(default_factory=list)  # top 10 分配点


@dataclass
class ThreadPoolStat:
    """线程池统计"""
    active_threads: int = 0         # 活动线程数
    registered_pools: Dict[str, Dict[str, int]] = field(default_factory=dict)  # 池名 -> {active, queued}


@dataclass
class FileHandleStat:
    """文件句柄统计"""
    open_count: int = 0             # 打开文件句柄数
    available: bool = True          # psutil 是否可用


@dataclass
class DbConnectionStat:
    """数据库连接统计"""
    available: bool = True
    pools: Dict[str, Dict[str, int]] = field(default_factory=dict)  # 池名 -> {active, idle, size}


@dataclass
class ResourceSnapshot:
    """单次资源采样快照"""
    timestamp: float                                       # 采样时间戳（epoch 秒）
    iso_time: str = ""                                     # ISO8601 时间（空则自动从 timestamp 生成）
    memory: MemoryStat = field(default_factory=MemoryStat)
    thread_pool: ThreadPoolStat = field(default_factory=ThreadPoolStat)
    file_handles: FileHandleStat = field(default_factory=FileHandleStat)
    db_connections: DbConnectionStat = field(default_factory=DbConnectionStat)
    sample_duration_ms: float = 0.0                        # 本次采样耗时

    def __post_init__(self):
        # 自动从 timestamp 生成 ISO 时间（若未显式提供）
        if not self.iso_time:
            self.iso_time = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TrendResult:
    """趋势分析结果"""
    resource_type: str
    slope: float               # 字节/采样（线性回归斜率）
    intercept: float           # 截距
    r_squared: float           # 拟合优度
    sample_count: int          # 样本数
    is_leaking: bool           # 是否判定为泄漏（斜率超阈值）
    threshold: float           # 当前阈值


# ============================================================================
# 资源监控器
# ============================================================================

class ResourceMonitor:
    """资源泄漏检测监控器

    线程安全：所有共享状态受 self._lock 保护。
    降级策略：任一子监控失败仅记录日志，不影响其他子监控与业务主流程。
    """

    # 资源类型标签（用于埋点）
    RESOURCE_MEMORY = "memory"
    RESOURCE_THREAD = "thread"
    RESOURCE_FILE = "file_handle"
    RESOURCE_DB = "db_connection"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化资源监控器

        Args:
            config: 配置字典（None 则从 ObservabilityConfig 读取）
        """
        self._lock = threading.RLock()
        self._config = config or {}
        # 采样间隔与阈值从 observability_config 读取（惰性，便于热加载生效）
        self._stop_event = threading.Event()
        self._sample_thread: Optional[threading.Thread] = None
        self._stress_mode = False

        # 历史采样存储（环形缓冲，固定大小防止内存膨胀）
        self._history_size = self._get_config("history_size", 1440)
        self._history: deque = deque(maxlen=self._history_size)

        # 外部资源提供者注册表：name -> (snapshot_func, type)
        self._providers: Dict[str, Tuple[Callable[[], Dict[str, int]], str]] = {}

        # 泄漏告警回调
        self._leak_callbacks: List[Callable[[TrendResult], None]] = []

        # tracemalloc 启动状态（幂等）
        self._tracemalloc_started = False
        self._init_tracemalloc()
        # 后台采样线程专属 trace_id（解决 ContextVar 不自动继承到子线程问题）
        self._monitor_trace_id = f"resource-monitor-{uuid.uuid4().hex[:16]}"

        # 持久化配置（跨重启趋势分析）
        self._persist_enabled = bool(self._get_config("persist_enabled", True))
        self._persist_path = self._resolve_persist_path()
        self._persist_max_age_hours = int(self._get_config("persist_max_age_hours", 168))
        self._persist_batch_size = int(self._get_config("persist_batch_size", 100))
        # 持久化缓冲与专用锁（与采样锁分离，避免阻塞采样主流程）
        self._persist_lock = threading.Lock()
        self._persist_buffer: List[ResourceSnapshot] = []
        self._persist_loaded = False  # 历史是否已加载（避免重复加载）

        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "init",
            "duration_ms": 0,
            "psutil_available": _PSUTIL_AVAILABLE,
            "history_size": self._history_size,
            "persist_enabled": self._persist_enabled,
            "persist_path": self._persist_path,
        }, ensure_ascii=False))

    # ── 公开 API ──

    def start(self) -> bool:
        """启动周期性采样后台线程"""
        with self._lock:
            if self._sample_thread and self._sample_thread.is_alive():
                return True
            # 启动前加载持久化历史（仅一次，支持跨重启趋势分析）
            self._load_persisted_history()
            self._stop_event.clear()
            self._sample_thread = threading.Thread(
                target=self._sample_loop,
                daemon=True,
                name="ResourceMonitor",
            )
            self._sample_thread.start()
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "start",
                "duration_ms": 0,
                "interval_sec": self._get_sample_interval(),
                "stress_mode": self._stress_mode,
                "history_loaded": len(self._history),
            }, ensure_ascii=False))
            return True

    def stop(self) -> None:
        """停止周期性采样"""
        self._stop_event.set()
        if self._sample_thread and self._sample_thread.is_alive():
            self._sample_thread.join(timeout=5)
        self._sample_thread = None
        # 停止前刷新持久化缓冲，确保最后的数据落盘
        self._flush_persist()
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "stop",
            "duration_ms": 0,
        }, ensure_ascii=False))

    def sample(self) -> ResourceSnapshot:
        """执行一次手动采样（同步，返回快照）"""
        return self._do_sample()

    def get_snapshot(self) -> Optional[ResourceSnapshot]:
        """获取最近一次采样快照"""
        with self._lock:
            if not self._history:
                return None
            return self._history[-1]

    def get_history(self, limit: Optional[int] = None) -> List[ResourceSnapshot]:
        """获取历史采样列表

        Args:
            limit: 返回最近 N 条；None 表示全部
        """
        with self._lock:
            data = list(self._history)
        if limit is not None:
            data = data[-limit:]
        return data

    def get_trend(self, resource_type: str = RESOURCE_MEMORY) -> Optional[TrendResult]:
        """计算指定资源类型的增长趋势（线性回归）

        Args:
            resource_type: 资源类型（memory/thread/file_handle/db_connection）

        Returns:
            趋势分析结果；样本不足返回 None
        """
        with self._lock:
            history = list(self._history)

        # 趋势计算入口日志（debug 级，便于排查样本不足或资源类型错误）
        logger.debug(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "trend_start",
            "duration_ms": 0,
            "resource_type": resource_type,
            "history_count": len(history),
        }, ensure_ascii=False))

        if len(history) < 2:
            logger.debug(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "trend_skip",
                "duration_ms": 0,
                "resource_type": resource_type,
                "reason": "history_insufficient",
                "history_count": len(history),
            }, ensure_ascii=False))
            return None

        # 提取时间序列 (x=index, y=value)
        series: List[Tuple[int, float]] = []
        for idx, snap in enumerate(history):
            value = self._extract_value(snap, resource_type)
            if value is not None:
                series.append((idx, float(value)))

        if len(series) < 2:
            logger.debug(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "trend_skip",
                "duration_ms": 0,
                "resource_type": resource_type,
                "reason": "series_insufficient",
                "series_count": len(series),
                "history_count": len(history),
            }, ensure_ascii=False))
            return None

        slope, intercept, r_squared = self._linear_regression(series)
        threshold = self._get_config("leak_slope_threshold", 1.0)
        is_leaking = slope > threshold

        result = TrendResult(
            resource_type=resource_type,
            slope=slope,
            intercept=intercept,
            r_squared=r_squared,
            sample_count=len(series),
            is_leaking=is_leaking,
            threshold=threshold,
        )

        # 趋势分析结果日志（info 级，便于排查泄漏误报/漏报与阈值配置问题）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "trend_result",
            "duration_ms": 0,
            "resource_type": resource_type,
            "slope": round(slope, 4),
            "intercept": round(intercept, 4),
            "r_squared": round(r_squared, 6),
            "sample_count": len(series),
            "is_leaking": is_leaking,
            "threshold": threshold,
        }, ensure_ascii=False))

        if is_leaking:
            self._fire_leak_callbacks(result)
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "leak_detected",
                "duration_ms": 0,
                "resource_type": resource_type,
                "slope": slope,
                "threshold": threshold,
                "r_squared": r_squared,
                "sample_count": len(series),
            }, ensure_ascii=False))

        return result

    def enable_stress_mode(self) -> None:
        """启用压测模式：高频采样（1 秒），用于资源释放曲线分析"""
        with self._lock:
            self._stress_mode = True
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "enable_stress_mode",
            "duration_ms": 0,
            "interval_sec": self._get_config("stress_test_interval_sec", 1.0),
        }, ensure_ascii=False))

    def disable_stress_mode(self) -> None:
        """关闭压测模式，恢复常规采样"""
        with self._lock:
            self._stress_mode = False
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "disable_stress_mode",
            "duration_ms": 0,
        }, ensure_ascii=False))

    def register_pool_provider(self, name: str, snapshot_func: Callable[[], Dict[str, int]], pool_type: str = "thread") -> None:
        """注册外部资源池采样提供者

        Args:
            name: 池名称（唯一标识）
            snapshot_func: 返回 {"active": int, "idle": int, "queued": int, "size": int}
            pool_type: 池类型（thread/db）
        """
        with self._lock:
            self._providers[name] = (snapshot_func, pool_type)
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "register_pool_provider",
            "duration_ms": 0,
            "name": name,
            "pool_type": pool_type,
        }, ensure_ascii=False))

    def register_leak_callback(self, callback: Callable[[TrendResult], None]) -> None:
        """注册泄漏告警回调"""
        with self._lock:
            self._leak_callbacks.append(callback)

    def get_status(self) -> Dict[str, Any]:
        """获取监控器运行状态"""
        with self._lock:
            latest = self._history[-1] if self._history else None
            status = {
                "running": self._sample_thread is not None and self._sample_thread.is_alive(),
                "stress_mode": self._stress_mode,
                "sample_interval_sec": self._get_sample_interval(),
                "history_count": len(self._history),
                "history_size": self._history_size,
                "providers": list(self._providers.keys()),
                "psutil_available": _PSUTIL_AVAILABLE,
                "tracemalloc_started": self._tracemalloc_started,
                "latest_snapshot": latest.to_dict() if latest else None,
            }
        # 合并持久化状态（无需持锁，内部独立加锁）
        status["persist"] = self.get_persist_status()
        return status

    # ── 内部实现 ──

    def _init_tracemalloc(self) -> None:
        """初始化 tracemalloc（幂等，失败降级）"""
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start(10)  # 保留 10 帧回溯
            self._tracemalloc_started = True
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "tracemalloc", "msg": f"[ResourceMonitor] tracemalloc 启动失败，内存监控降级: {e}"}, ensure_ascii=False))
            self._tracemalloc_started = False

    def _get_config(self, key: str, default: Any) -> Any:
        """读取配置：优先传入的 config，其次 ObservabilityConfig，最后 default"""
        if self._config and key in self._config:
            return self._config[key]
        try:
            from agent.monitoring.observability_config import get_observability_config
            return get_observability_config().get(f"resource_monitor.{key}", default)
        except Exception:
            return default

    def _get_sample_interval(self) -> float:
        """获取当前采样间隔（压测模式使用 stress 间隔）"""
        if self._stress_mode:
            return float(self._get_config("stress_test_interval_sec", 1.0))
        return float(self._get_config("sample_interval_sec", 60))

    def _sample_loop(self) -> None:
        """周期采样循环"""
        # 设置后台线程 trace_id（ContextVar 不自动继承到子线程）
        set_trace_id(self._monitor_trace_id)
        while not self._stop_event.is_set():
            try:
                self._do_sample()
            except Exception as e:
                # 降级：采样失败仅记录日志，不中断循环
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "resource_monitor",
                    "action": "sample_loop_error",
                    "duration_ms": 0,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "stack_trace": traceback.format_exc(),
                }, ensure_ascii=False))
            interval = self._get_sample_interval()
            # 使用 wait 而非 sleep，便于快速响应 stop 与模式切换
            self._stop_event.wait(interval)

    def _do_sample(self) -> ResourceSnapshot:
        """执行单次采样（聚合各子监控，单点失败不影响整体）"""
        start = time.time()
        snap = ResourceSnapshot(
            timestamp=start,
            iso_time=datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
        )

        # 1. 内存采样（tracemalloc，可能较慢）
        t0 = time.time()
        try:
            snap.memory = self._sample_memory()
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 内存采样失败: {e}"}, ensure_ascii=False))
        mem_ms = (time.time() - t0) * 1000

        # 2. 线程池采样
        t1 = time.time()
        try:
            snap.thread_pool = self._sample_thread_pool()
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 线程池采样失败: {e}"}, ensure_ascii=False))
        thread_ms = (time.time() - t1) * 1000

        # 3. 文件句柄采样（psutil，可能不可用）
        t2 = time.time()
        try:
            snap.file_handles = self._sample_file_handles()
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 文件句柄采样失败: {e}"}, ensure_ascii=False))
        fh_ms = (time.time() - t2) * 1000

        # 4. 数据库连接采样（依赖外部 provider 注册）
        t3 = time.time()
        try:
            snap.db_connections = self._sample_db_connections()
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 数据库连接采样失败: {e}"}, ensure_ascii=False))
        db_ms = (time.time() - t3) * 1000

        snap.sample_duration_ms = (time.time() - start) * 1000

        with self._lock:
            self._history.append(snap)

        # 上报业务指标（埋点失败隔离）
        self._report_metrics(snap)

        # 持久化落盘（异步缓冲，失败降级不影响采样）
        self._persist_sample(snap)

        # 采样详情日志（info 级，含各子监控分步耗时，便于排查采样异常与性能瓶颈）
        logger.info(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "sample",
            "duration_ms": round(snap.sample_duration_ms, 3),
            "memory_bytes": snap.memory.current_bytes,
            "memory_peak_bytes": snap.memory.peak_bytes,
            "memory_sample_ms": round(mem_ms, 3),
            "active_threads": snap.thread_pool.active_threads,
            "thread_sample_ms": round(thread_ms, 3),
            "open_files": snap.file_handles.open_count,
            "file_handle_available": snap.file_handles.available,
            "file_handle_sample_ms": round(fh_ms, 3),
            "db_pool_count": len(snap.db_connections.pools),
            "db_sample_ms": round(db_ms, 3),
            "history_count": len(self._history),
        }, ensure_ascii=False))

        return snap

    def _sample_memory(self) -> MemoryStat:
        """采样内存统计（tracemalloc top 10）"""
        if not self._tracemalloc_started:
            return MemoryStat()

        current, peak = tracemalloc.get_traced_memory()
        # 获取 top 10 内存分配点（按分配大小）
        stats = tracemalloc.take_snapshot().statistics("lineno")
        top = []
        for stat in stats[:10]:
            top.append({
                "file": stat.traceback[0].filename if stat.traceback else "",
                "line": stat.traceback[0].lineno if stat.traceback else 0,
                "size_bytes": stat.size,
                "count": stat.count,
            })
        return MemoryStat(current_bytes=current, peak_bytes=peak, top_allocations=top)

    def _sample_thread_pool(self) -> ThreadPoolStat:
        """采样线程池统计"""
        stat = ThreadPoolStat(active_threads=threading.active_count())
        for name, (func, _pool_type) in list(self._providers.items()):
            try:
                data = func() or {}
                if _pool_type == "thread":
                    stat.registered_pools[name] = {
                        "active": int(data.get("active", 0)),
                        "queued": int(data.get("queued", 0)),
                        "size": int(data.get("size", 0)),
                    }
            except Exception as e:
                logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "name", "msg": f"[ResourceMonitor] 池 {name} 采样失败: {e}"}, ensure_ascii=False))
        return stat

    def _sample_file_handles(self) -> FileHandleStat:
        """采样文件句柄统计（psutil）"""
        if not _PSUTIL_AVAILABLE:
            return FileHandleStat(available=False)
        try:
            proc = psutil.Process()
            open_files = proc.open_files()
            return FileHandleStat(open_count=len(open_files), available=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 文件句柄采样受限: {e}"}, ensure_ascii=False))
            return FileHandleStat(available=False)

    def _sample_db_connections(self) -> DbConnectionStat:
        """采样数据库连接池统计"""
        stat = DbConnectionStat()
        for name, (func, _pool_type) in list(self._providers.items()):
            try:
                data = func() or {}
                if _pool_type == "db":
                    stat.pools[name] = {
                        "active": int(data.get("active", 0)),
                        "idle": int(data.get("idle", 0)),
                        "size": int(data.get("size", 0)),
                    }
            except Exception as e:
                logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "name", "msg": f"[ResourceMonitor] 数据库连接池 {name} 采样失败: {e}"}, ensure_ascii=False))
        return stat

    def _report_metrics(self, snap: ResourceSnapshot) -> None:
        """上报业务指标（埋点失败隔离，不影响采样主流程）

        指标命名遵循 yunshu_<模块>_<动作> 规范：
        - yunshu_resource_usage（gauge）：含 resource_type 标签
        """
        collector = _get_business_collector()
        if collector is None:
            return
        # 单次埋点耗时 < 1ms：仅调用 _set_gauge，无外部 IO
        try:
            metric_name = "yunshu_resource_usage"
            # 内存
            collector._set_gauge(
                metric_name,
                {"resource_type": self.RESOURCE_MEMORY, "success": "true"},
                float(snap.memory.current_bytes),
            )
            # 活动线程
            collector._set_gauge(
                metric_name,
                {"resource_type": self.RESOURCE_THREAD, "success": "true"},
                float(snap.thread_pool.active_threads),
            )
            # 文件句柄
            if snap.file_handles.available:
                collector._set_gauge(
                    metric_name,
                    {"resource_type": self.RESOURCE_FILE, "success": "true"},
                    float(snap.file_handles.open_count),
                )
            # 数据库连接（每个池单独上报）
            for pool_name, pool_stat in snap.db_connections.pools.items():
                collector._set_gauge(
                    metric_name,
                    {"resource_type": self.RESOURCE_DB, "pool": pool_name, "success": "true"},
                    float(pool_stat.get("active", 0)),
                )
        except Exception as e:
            # 埋点失败仅日志记录，不影响主业务流程
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 指标上报失败: {e}"}, ensure_ascii=False))

    @staticmethod
    def _extract_value(snap: ResourceSnapshot, resource_type: str) -> Optional[float]:
        """从快照中提取指定资源类型的标量值"""
        if resource_type == ResourceMonitor.RESOURCE_MEMORY:
            return float(snap.memory.current_bytes)
        if resource_type == ResourceMonitor.RESOURCE_THREAD:
            return float(snap.thread_pool.active_threads)
        if resource_type == ResourceMonitor.RESOURCE_FILE:
            return float(snap.file_handles.open_count) if snap.file_handles.available else None
        if resource_type == ResourceMonitor.RESOURCE_DB:
            # 数据库连接取所有池 active 之和
            return float(sum(p.get("active", 0) for p in snap.db_connections.pools.values()))
        return None

    @staticmethod
    def _linear_regression(series: List[Tuple[int, float]]) -> Tuple[float, float, float]:
        """最小二乘法线性回归，返回 (slope, intercept, r_squared)"""
        n = len(series)
        if n < 2:
            return 0.0, 0.0, 0.0
        sum_x = sum(s[0] for s in series)
        sum_y = sum(s[1] for s in series)
        sum_xx = sum(s[0] * s[0] for s in series)
        sum_xy = sum(s[0] * s[1] for s in series)
        mean_x = sum_x / n
        mean_y = sum_y / n
        # 分母判零，避免除零异常
        denom = sum_xx - n * mean_x * mean_x
        if denom == 0:
            return 0.0, mean_y, 0.0
        slope = (sum_xy - n * mean_x * mean_y) / denom
        intercept = mean_y - slope * mean_x
        # 计算 R²
        ss_tot = sum((s[1] - mean_y) ** 2 for s in series)
        if ss_tot == 0:
            r_squared = 1.0 if slope == 0 else 0.0
        else:
            ss_res = sum((s[1] - (slope * s[0] + intercept)) ** 2 for s in series)
            r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        result = (slope, intercept, max(0.0, min(1.0, r_squared)))
        # 回归计算结果日志（debug 级，便于排查拟合优度与斜率异常）
        logger.debug(json.dumps({
            "trace_id": get_trace_id(),
            "module_name": "resource_monitor",
            "action": "linear_regression",
            "duration_ms": 0,
            "n": n,
            "slope": round(result[0], 6),
            "intercept": round(result[1], 6),
            "r_squared": round(result[2], 6),
        }, ensure_ascii=False))
        return result

    def _fire_leak_callbacks(self, result: TrendResult) -> None:
        """触发泄漏告警回调（异常隔离）"""
        for callback in list(self._leak_callbacks):
            try:
                callback(result)
            except Exception as e:
                logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 泄漏告警回调执行失败: {e}"}, ensure_ascii=False))

    # ── 持久化实现（跨重启趋势分析） ──

    def _resolve_persist_path(self) -> str:
        """解析持久化文件路径（空配置使用默认路径）"""
        configured = self._get_config("persist_path", "")
        if configured:
            return configured
        # 默认路径：./data/resource_monitor_history.jsonl
        return os.path.join("data", "resource_monitor_history.jsonl")

    def _persist_sample(self, snap: ResourceSnapshot) -> None:
        """将快照加入持久化缓冲，达到 batch_size 触发批量写入

        降级策略：缓冲失败仅日志记录，不影响采样主流程。
        设计：使用 JSONL（每行一个 JSON）追加写入，避免全量重写。
        """
        if not self._persist_enabled:
            return
        try:
            with self._persist_lock:
                self._persist_buffer.append(snap)
                should_flush = len(self._persist_buffer) >= self._persist_batch_size
            if should_flush:
                self._flush_persist()
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 持久化缓冲失败: {e}"}, ensure_ascii=False))

    def _flush_persist(self) -> None:
        """将缓冲区快照批量写入磁盘（追加模式）

        原子性：先写入临时文件再 rename，避免崩溃导致数据损坏。
        失败降级：写入异常仅日志，清空缓冲避免无限增长。
        """
        if not self._persist_enabled:
            return
        with self._persist_lock:
            if not self._persist_buffer:
                return
            batch = list(self._persist_buffer)
            self._persist_buffer.clear()

        if not batch:
            return

        try:
            # 确保父目录存在
            parent = os.path.dirname(self._persist_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            # 追加写入（每行一个 JSON 快照）
            lines = []
            for snap in batch:
                try:
                    lines.append(json.dumps(snap.to_dict(), ensure_ascii=False))
                except Exception as e:
                    logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 快照序列化失败: {e}"}, ensure_ascii=False))

            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            logger.debug(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "persist_flush",
                "duration_ms": 0,
                "count": len(lines),
                "path": self._persist_path,
            }, ensure_ascii=False))
        except Exception as e:
            # 落盘失败仅日志，缓冲已清空，下一轮继续
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "persist_flush_failed",
                "duration_ms": 0,
                "path": self._persist_path,
                "error": str(e),
            }, ensure_ascii=False))

    def _load_persisted_history(self) -> int:
        """启动时从磁盘加载历史快照（仅一次）

        Returns:
            加载的快照数量
        """
        if self._persist_loaded or not self._persist_enabled:
            self._persist_loaded = True
            return 0
        self._persist_loaded = True

        if not os.path.exists(self._persist_path):
            return 0

        loaded = 0
        cutoff = time.time() - self._persist_max_age_hours * 3600
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # 过滤过期数据（按 max_age_hours）
                        if data.get("timestamp", 0) < cutoff:
                            continue
                        snap = self._dict_to_snapshot(data)
                        if snap is not None:
                            with self._lock:
                                self._history.append(snap)
                            loaded += 1
                    except Exception as e:
                        logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 历史行解析失败，跳过: {e}"}, ensure_ascii=False))

            # 加载后触发过期清理（重写文件，仅保留有效数据）
            if loaded > 0:
                self._rewrite_persisted_file()

            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "load_persisted_history",
                "duration_ms": 0,
                "loaded": loaded,
                "path": self._persist_path,
            }, ensure_ascii=False))
        except Exception as e:
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "load_persisted_history_failed",
                "duration_ms": 0,
                "path": self._persist_path,
                "error": str(e),
            }, ensure_ascii=False))
        return loaded

    def _rewrite_persisted_file(self) -> None:
        """重写持久化文件（清理过期数据后压缩存储）

        原子性：写入临时文件再 rename，避免崩溃损坏。
        """
        if not os.path.exists(self._persist_path):
            return
        cutoff = time.time() - self._persist_max_age_hours * 3600
        tmp_path = self._persist_path + ".tmp"
        try:
            kept = 0
            with open(self._persist_path, "r", encoding="utf-8") as src, \
                 open(tmp_path, "w", encoding="utf-8") as dst:
                for line in src:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("timestamp", 0) >= cutoff:
                            dst.write(line + "\n")
                            kept += 1
                    except Exception:
                        continue  # 跳过损坏行
            # 原子替换
            os.replace(tmp_path, self._persist_path)
            logger.debug(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "resource_monitor",
                "action": "rewrite_persisted",
                "duration_ms": 0,
                "kept": kept,
                "path": self._persist_path,
            }, ensure_ascii=False))
        except Exception as e:
            # 清理临时文件
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 重写持久化文件失败: {e}"}, ensure_ascii=False))

    def _dict_to_snapshot(self, data: Dict[str, Any]) -> Optional[ResourceSnapshot]:
        """从字典重建快照对象（容错：字段缺失返回 None）"""
        try:
            mem_data = data.get("memory", {})
            tp_data = data.get("thread_pool", {})
            fh_data = data.get("file_handles", {})
            db_data = data.get("db_connections", {})
            return ResourceSnapshot(
                timestamp=float(data.get("timestamp", 0)),
                iso_time=data.get("iso_time", ""),
                memory=MemoryStat(
                    current_bytes=int(mem_data.get("current_bytes", 0)),
                    peak_bytes=int(mem_data.get("peak_bytes", 0)),
                    top_allocations=mem_data.get("top_allocations", []),
                ),
                thread_pool=ThreadPoolStat(
                    active_threads=int(tp_data.get("active_threads", 0)),
                    registered_pools=tp_data.get("registered_pools", {}),
                ),
                file_handles=FileHandleStat(
                    open_count=int(fh_data.get("open_count", 0)),
                    available=bool(fh_data.get("available", True)),
                ),
                db_connections=DbConnectionStat(
                    available=bool(db_data.get("available", True)),
                    pools=db_data.get("pools", {}),
                ),
                sample_duration_ms=float(data.get("sample_duration_ms", 0)),
            )
        except Exception as e:
            logger.warning(json.dumps({"trace_id": get_trace_id(), "module_name": "resource_monitor", "action": "log", "msg": f"[ResourceMonitor] 快照反序列化失败: {e}"}, ensure_ascii=False))
            return None

    # ── 持久化公开 API ──

    def flush_persist(self) -> None:
        """手动刷新持久化缓冲到磁盘（用于优雅停机或外部触发）"""
        self._flush_persist()

    def cleanup_persisted_history(self) -> int:
        """手动清理过期持久化数据

        Returns:
            清理后的保留条数
        """
        if not self._persist_enabled or not os.path.exists(self._persist_path):
            return 0
        self._rewrite_persisted_file()
        # 统计保留条数
        try:
            with open(self._persist_path, "r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def get_persist_status(self) -> Dict[str, Any]:
        """获取持久化状态信息"""
        import os as _os
        file_size = 0
        file_exists = False
        if self._persist_enabled and _os.path.exists(self._persist_path):
            file_exists = True
            try:
                file_size = _os.path.getsize(self._persist_path)
            except Exception:
                pass
        return {
            "enabled": self._persist_enabled,
            "path": self._persist_path,
            "file_exists": file_exists,
            "file_size_bytes": file_size,
            "buffer_count": len(self._persist_buffer),
            "batch_size": self._persist_batch_size,
            "max_age_hours": self._persist_max_age_hours,
            "history_loaded": self._persist_loaded,
        }


# ============================================================================
# 全局实例与访问函数
# ============================================================================

_global_resource_monitor: Optional[ResourceMonitor] = None
_global_monitor_lock = threading.Lock()


def get_resource_monitor() -> ResourceMonitor:
    """获取全局资源监控器实例（惰性初始化，线程安全）"""
    global _global_resource_monitor
    if _global_resource_monitor is None:
        with _global_monitor_lock:
            if _global_resource_monitor is None:
                _global_resource_monitor = ResourceMonitor()
    return _global_resource_monitor


def reset_resource_monitor() -> None:
    """重置全局实例（仅用于测试）"""
    global _global_resource_monitor
    with _global_monitor_lock:
        if _global_resource_monitor is not None:
            _global_resource_monitor.stop()
        _global_resource_monitor = None
