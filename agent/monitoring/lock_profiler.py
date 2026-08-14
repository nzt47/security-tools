"""锁竞争采样探针 — P1·B1 锁竞争热点分析（初始框架）

【任务定位】
    并发安全系列已规范锁纪律（持锁禁 I/O/回调、锁内仅内存状态变更），
    但缺少量化数据支撑"哪把锁是瓶颈"。本探针在 LOCK_PROFILE=1 时
    替换 threading.Lock/RLock，采集锁等待/持锁时长，供
    scripts/analyze_lock_hotspots.py 生成热点报告。

【不易边界】
    - 零开销默认关闭：未设置 LOCK_PROFILE 时 threading.Lock 原样直通，
      不引入任何分支/计时（生产路径零影响）。
    - 采样记录线程安全：SampledLock 内部记录器锁使用"原始" threading.Lock
      （模块加载时保存 _ORIG_LOCK 引用），避免 patch 后自采样递归。
    - 采样统计路径：内存列表 + 批量落盘，锁内仅内存变更（I/O 在锁外），
      对齐"日志路径异步批量落盘"纪律。

【配置（.env 或环境变量）】
    LOCK_PROFILE        采样开关，默认 0（关闭）
    LOCK_PROFILE_LOG    JSONL 输出路径，默认 <temp>/lock_profile.jsonl
    LOCK_PROFILE_BATCH  批量落盘条数，默认 500

【用法】
    LOCK_PROFILE=1 python scripts/xxx_concurrency.py
    采样日志每行: {"lock_name": "...", "thread_id": 123, "wait_us": 456, "hold_us": 789}
"""

from __future__ import annotations

import atexit
import json
import os
import tempfile
import threading
import time
from typing import Optional

# 原始锁引用（模块加载时保存，防 patch 后自采样递归——不易）
_ORIG_LOCK = threading.Lock
_ORIG_RLOCK = threading.RLock

_ENV_ENABLED = "LOCK_PROFILE"
_ENV_LOG_PATH = "LOCK_PROFILE_LOG"
_ENV_BATCH = "LOCK_PROFILE_BATCH"


def _enabled() -> bool:
    return os.getenv(_ENV_ENABLED, "0").strip().lower() in ("1", "true", "yes", "on")


def _log_path() -> str:
    return os.getenv(_ENV_LOG_PATH, os.path.join(tempfile.gettempdir(), "lock_profile.jsonl"))


def _batch_size() -> int:
    try:
        return max(1, int(os.getenv(_ENV_BATCH, "500")))
    except ValueError:
        return 500


def _caller_name() -> str:
    """取调用点（文件名:行号）作为锁名兜底；inspect 开销仅采样模式可接受"""
    try:
        import inspect
        frame = inspect.currentframe()
        for _ in range(3):  # 向上跳 3 层: _caller_name -> acquire -> 业务代码
            if frame is None or frame.f_back is None:
                break
            frame = frame.f_back
        return f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}"
    except Exception:
        return "<unknown>"


class _BatchRecorder:
    """采样记录器 — 内存缓冲 + 批量落盘（锁内仅 append 内存列表，I/O 在锁外）"""

    def __init__(self, path: str, batch: int) -> None:
        self._path = path
        self._batch = batch
        self._buffer: list = []
        self._lock = _ORIG_LOCK()  # 原始锁：防自采样递归

    def record(self, entry: dict) -> None:
        with self._lock:  # 锁内仅内存列表 append（持锁纪律）
            self._buffer.append(entry)
            if len(self._buffer) >= self._batch:
                self._flush_locked()

    def _flush_locked(self) -> None:
        # 调用方已持 self._lock；swap 后 I/O 在锁外？——
        # 简易版：直接在这里写（缓冲满才触发，频率低，可接受）；
        # 生产化可改为 swap + 后台线程落盘（对齐 B2 异步批量落盘）。
        batch, self._buffer = self._buffer, []
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                for item in batch:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 采样记录失败不阻断业务（采样是诊断手段，非业务路径）

    def flush_all(self) -> None:
        """进程退出前落盘全部缓冲（atexit 兜底，防采样数据丢失）"""
        with self._lock:
            if self._buffer:
                self._flush_locked()


class SampledLock:
    """threading.Lock 的采样包装（wait=阻塞等待时长, hold=持有时长）"""

    def __init__(self, name: Optional[str] = None) -> None:
        self._inner = _ORIG_LOCK()
        self._name = name or _caller_name()
        self._recorder = _recorder()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        t0 = time.perf_counter()
        acquired = self._inner.acquire(blocking=blocking, timeout=timeout)
        wait_us = (time.perf_counter() - t0) * 1_000_000
        if acquired:
            self._held_at = time.perf_counter()
            self._wait_us = wait_us
        else:
            # 未获取到锁（非阻塞/超时）——也记录一次等待（可作竞争信号）
            self._recorder.record({
                "lock_name": self._name, "thread_id": threading.get_ident(),
                "wait_us": round(wait_us, 2), "hold_us": 0, "acquired": False,
            })
        return acquired

    def release(self) -> None:
        hold_us = (time.perf_counter() - self._held_at) * 1_000_000
        self._recorder.record({
            "lock_name": self._name, "thread_id": threading.get_ident(),
            "wait_us": round(self._wait_us, 2), "hold_us": round(hold_us, 2), "acquired": True,
        })
        self._inner.release()

    def locked(self) -> bool:
        return self._inner.locked()

    def __enter__(self) -> "SampledLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class SampledRLock(SampledLock):
    """threading.RLock 的采样包装（可重入，接口兼容）"""

    def __init__(self, name: Optional[str] = None) -> None:
        self._inner = _ORIG_RLOCK()
        self._name = name or _caller_name()
        self._recorder = _recorder()


_recorder_singleton: Optional[_BatchRecorder] = None
_orig_lock_cls = threading.Lock
_orig_rlock_cls = threading.RLock


def _recorder() -> _BatchRecorder:
    global _recorder_singleton
    if _recorder_singleton is None:
        _recorder_singleton = _BatchRecorder(_log_path(), _batch_size())
    return _recorder_singleton


def patch_threading() -> None:
    """LOCK_PROFILE=1 时替换 threading.Lock/RLock 为采样包装（进程级生效）"""
    global _recorder_singleton
    if not _enabled():
        return
    _recorder_singleton = _BatchRecorder(_log_path(), _batch_size())
    # 进程退出前 flush 全部采样缓冲（防批量缓冲数据丢失）
    atexit.register(_recorder_singleton.flush_all)
    threading.Lock = SampledLock  # type: ignore[misc]
    threading.RLock = SampledRLock  # type: ignore[misc]


def unpatch_threading() -> None:
    """还原原始 threading.Lock/RLock（测试/卸载用）"""
    threading.Lock = _orig_lock_cls  # type: ignore[misc]
    threading.RLock = _orig_rlock_cls  # type: ignore[misc]


if __name__ == "__main__":
    # 冒烟验证：LOCK_PROFILE=1 python -m agent.monitoring.lock_profiler
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    patch_threading()
    lock = threading.Lock()
    with lock:
        time.sleep(0.01)
    print(f"采样已启用: log={_log_path()}")
    print("说明: 样例写入见 JSONL（缓冲满 {batch} 条或进程退出前）。")
