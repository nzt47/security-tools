"""运行时锁看门狗 — P2·C2 锁纪律运行时护栏（最小框架）

【任务定位】
    锁纪律违规（持锁超时/锁等待饿死）当前只能等崩溃暴露。本模块在
    LOCK_WATCHDOG_ENABLED=1 时包装 threading.Lock/RLock，持锁/等待超阈值
    即计数 + 留栈，并把计数注册进 BUSINESS_METRICS_DEFINITIONS，
    供 Prometheus 告警规则（lock_hold_timeouts_total）消费。

【不易边界】
    - 零开销默认关闭：未启用时包装直通（函数指针替换，热路径无分支）。
    - 只新增不删改：不修改任何现有锁纪律代码；包装层独立可摘除。
    - 锁内仅内存计数（记录器锁 = 原始 threading.Lock，防自采样递归）。

【配置（.env）】
    LOCK_WATCHDOG_ENABLED   看门狗开关，默认 0（关闭）
    LOCK_WATCHDOG_HOLD_MS   持锁超时阈值，默认 2000
    LOCK_WATCHDOG_WAIT_MS   锁等待超时阈值，默认 5000

【指标（注册进 BUSINESS_METRICS_DEFINITIONS）】
    lock_hold_timeouts_total   counter   持锁超时次数（告警主指标）
    lock_wait_timeouts_total   counter   锁等待超时次数
    lock_hold_duration_ms      histogram 持锁时长分布

【告警规则文件】deploy/monitoring/prometheus/lock_watchdog_alerts.yml
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

from .business_metrics import BUSINESS_METRICS_DEFINITIONS, BusinessMetricDefinition

# 原始锁引用（防 patch 后自采样递归——不易）
_ORIG_LOCK = threading.Lock
_ORIG_RLOCK = threading.RLock

_ENV_ENABLED = "LOCK_WATCHDOG_ENABLED"
_ENV_HOLD_MS = "LOCK_WATCHDOG_HOLD_MS"
_ENV_WAIT_MS = "LOCK_WATCHDOG_WAIT_MS"


def _env_enabled() -> bool:
    return os.getenv(_ENV_ENABLED, "0").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _register_metrics() -> None:
    """指标注册（幂等）：watchdog 计数经 Prometheus /metrics 暴露，供告警规则消费"""
    defs = {
        "lock_hold_timeouts_total": BusinessMetricDefinition(
            name="lock_hold_timeouts_total",
            description="持锁超时次数（持锁时长 > 阈值，锁纪律违规）",
            metric_type="counter", labels=["lock_name"], unit="次",
            category="concurrency", business_value="锁纪律违规运行时即暴露", aggregation="sum", retention_days=30,
        ),
        "lock_wait_timeouts_total": BusinessMetricDefinition(
            name="lock_wait_timeouts_total",
            description="锁等待超时次数（潜在死锁/饥饿）",
            metric_type="counter", labels=["lock_name"], unit="次",
            category="concurrency", business_value="锁等待饿死检测", aggregation="sum", retention_days=30,
        ),
        "lock_hold_duration_ms": BusinessMetricDefinition(
            name="lock_hold_duration_ms",
            description="持锁时长分布（毫秒）",
            metric_type="histogram", labels=["lock_name"], unit="毫秒",
            category="concurrency", business_value="锁竞争量化", aggregation="avg", retention_days=7,
        ),
    }
    for name, definition in defs.items():
        if name not in BUSINESS_METRICS_DEFINITIONS:
            BUSINESS_METRICS_DEFINITIONS[name] = definition


class LockWatchdog:
    """锁看门狗单例 — 持锁/等待超时计数（进程内）+ 指标注册"""

    _instance: Optional["LockWatchdog"] = None

    def __init__(self, hold_ms: int, wait_ms: int) -> None:
        self._hold_ms = hold_ms
        self._wait_ms = wait_ms
        self._hold_timeouts: Dict[str, int] = {}   # lock_name -> 次数
        self._wait_timeouts: Dict[str, int] = {}
        self._lock = _ORIG_LOCK()                  # 原始锁：锁内仅内存 dict 变更
        _register_metrics()

    @classmethod
    def get(cls) -> "LockWatchdog":
        if cls._instance is None:
            cls._instance = cls(
                hold_ms=_env_int(_ENV_HOLD_MS, 2000),
                wait_ms=_env_int(_ENV_WAIT_MS, 5000),
            )
        return cls._instance

    def record_hold_timeout(self, lock_name: str, hold_ms: float, stack: str) -> None:
        with self._lock:  # 锁内仅内存计数（持锁纪律）
            self._hold_timeouts[lock_name] = self._hold_timeouts.get(lock_name, 0) + 1
        # 告警留痕放锁外（I/O 移出锁外——不易）
        print(f"[lock_watchdog] 持锁超时 lock={lock_name} hold={hold_ms:.0f}ms\n{stack}")

    def record_wait_timeout(self, lock_name: str, wait_ms: float) -> None:
        with self._lock:
            self._wait_timeouts[lock_name] = self._wait_timeouts.get(lock_name, 0) + 1
        print(f"[lock_watchdog] 锁等待超时 lock={lock_name} wait={wait_ms:.0f}ms（潜在死锁/饥饿）")

    def get_metrics(self) -> Dict[str, Dict[str, int]]:
        """当前计数快照（Prometheus 暴露 + 测试断言用）"""
        with self._lock:
            return {
                "lock_hold_timeouts_total": dict(self._hold_timeouts),
                "lock_wait_timeouts_total": dict(self._wait_timeouts),
            }


class WatchedLock:
    """threading.Lock 的看门狗包装：持锁/等待超阈值即告警（hold 阈值 = 0 表示关闭该检测）"""

    def __init__(self, name: Optional[str] = None, watchdog: Optional[LockWatchdog] = None) -> None:
        self._inner = _ORIG_LOCK()
        self._name = name or f"lock@{id(self):x}"
        self._wd = watchdog or LockWatchdog.get()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        t0 = time.perf_counter()
        acquired = self._inner.acquire(blocking=blocking, timeout=timeout)
        wait_ms = (time.perf_counter() - t0) * 1000.0
        if acquired:
            self._held_at = time.perf_counter()
        elif self._wd._wait_ms and wait_ms > self._wd._wait_ms:
            self._wd.record_wait_timeout(self._name, wait_ms)
        return acquired

    def release(self) -> None:
        hold_ms = (time.perf_counter() - self._held_at) * 1000.0
        if self._wd._hold_ms and hold_ms > self._wd._hold_ms:
            import traceback
            self._wd.record_hold_timeout(self._name, hold_ms, "".join(traceback.format_stack()))
        self._inner.release()

    def locked(self) -> bool:
        return self._inner.locked()

    def __enter__(self) -> "WatchedLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def patch_threading() -> None:
    """LOCK_WATCHDOG_ENABLED=1 时替换 threading.Lock/RLock（进程级生效）"""
    if not _env_enabled():
        return
    LockWatchdog.get()  # 预热：注册指标
    threading.Lock = WatchedLock  # type: ignore[misc]
    threading.RLock = WatchedLock  # type: ignore[misc]


def unpatch_threading() -> None:
    threading.Lock = _ORIG_LOCK  # type: ignore[misc]
    threading.RLock = _ORIG_RLOCK  # type: ignore[misc]
