"""真实的「系统空闲」判据（叶子模块，2026-11-13）。

【为什么需要（§11.103 F / handoff_next: idle-detector-fake-gate）】
    原来的 `agent/log_system/introspection.IdleDetector` 只看进程内的 `mark_activity()`，
    而 `mark_activity()` 在生产里**零调用** ⇒ `_last_activity` 停在启动时刻，开机 5 分钟后
    恒判"空闲"。实测 2026-11-13 的日志内省每 30 分钟准点跑（03:11/03:41/…/09:41，13 轮 0 洞察），
    这个闸门从来没有真的避让过 —— 它是个假闸门。任何"趁空闲做重活"的功能（记忆整理等）
    如果建在它上面，等于没加闸门。

【判据（两条同时满足才算空闲）】
    1. **用户不在用**：Windows 用 `GetLastInputInfo`（系统级、毫秒级，**不依赖谁记得调 mark_activity**）；
       其它平台取不到 ⇒ 只用进程内信号，并在 `state()["degraded"]` 里如实标注。
    2. **进程没在忙**：距上一次 `mark_activity()`（HTTP 请求 / 工具执行等入口）至少 idle_timeout 秒。
    再叠加一条 `cpu_percent <= max_cpu`（psutil 可用时）。
    取 `min(用户输入空闲, 进程内空闲)` 作为有效空闲 —— 任一维度"刚活动过"就不算空闲。

【口径】
    · 本模块不 import 任何 agent.* 的重模块（叶子）：它会被 HTTP 钩子每请求调用一次；
    · 所有取不到的读数返回 **None**（显示「—」），绝不折算成 0 / "空闲"（L5 口径）；
    · `state()` 同时给出 `idle_source` 与 `last_activity_age_s`，用来判断"这个判断新不新鲜"。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Any, Dict, Optional

#: 判定空闲所需的"无活动"秒数（用户输入 + 进程内都按它）
IDLE_TIMEOUT_SEC = 300.0
#: 判定空闲允许的 CPU 百分比上限
MAX_CPU_PERCENT = 20.0
#: CPU 采样窗口（秒）—— psutil.cpu_percent 的阻塞式采样
CPU_SAMPLE_SEC = 0.1
#: Windows GetTickCount64 的低 32 位掩码（LASTINPUTINFO.dwTime 是 32 位 tick）
_TICK32_MASK = 0xFFFFFFFF

_lock = threading.Lock()
_last_activity = time.time()

_cpu_error_logged = False


def mark_activity() -> None:
    """记录一次"有人在用/进程在忙"（HTTP 请求、工具执行等入口调用）。"""
    global _last_activity
    with _lock:
        _last_activity = time.time()


def seconds_since_activity() -> float:
    """距上一次 mark_activity() 的秒数（进程内维度）。"""
    with _lock:
        return max(0.0, time.time() - _last_activity)


def reset_activity_clock(ts: Optional[float] = None) -> None:
    """测试用：把进程内活动时钟拨到指定时刻（默认现在）。"""
    global _last_activity
    with _lock:
        _last_activity = time.time() if ts is None else float(ts)


def user_idle_seconds() -> Optional[float]:
    """系统级"用户输入空闲"秒数（Windows GetLastInputInfo）；其它平台 None。

    为什么不用键鼠钩子/GetAsyncKeyState：那需要轮询或钩子，代价与权限都更重；
    GetLastInputInfo 是系统维护的"最后一次输入"tick，一次调用即可，且**不依赖进程内是否有人调
    mark_activity**。
    """
    if sys.platform != "win32":
        return None
    try:
        class _LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]

        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount64() & _TICK32_MASK
        delta_ms = (tick - int(info.dwTime)) & _TICK32_MASK
        return round(delta_ms / 1000.0, 3)
    except Exception:  # noqa: BLE001 取不到就当"无法判定"，不编造空闲
        return None


def cpu_percent() -> Optional[float]:
    """当前 CPU 使用率（psutil 可用时）；不可用返回 None（不折算成 0）。"""
    global _cpu_error_logged
    try:
        import psutil

        return round(float(psutil.cpu_percent(interval=CPU_SAMPLE_SEC)), 1)
    except Exception as exc:  # noqa: BLE001 没有 psutil / 采样失败都算"无法判定"
        if not _cpu_error_logged:
            _cpu_error_logged = True
            try:
                import logging

                logging.getLogger(__name__).info(
                    "[system_idle] CPU 读数不可用（本次不重试）：%s", type(exc).__name__)
            except Exception:  # noqa: BLE001
                pass
        return None


def state(*, idle_timeout: float = IDLE_TIMEOUT_SEC, max_cpu: float = MAX_CPU_PERCENT,
          sample_cpu: bool = True) -> Dict[str, Any]:
    """完整的空闲判定 + 读数（"这个判断新不新鲜"也在这里）。"""
    user_idle = user_idle_seconds()
    process_idle = seconds_since_activity()

    if user_idle is None:
        effective = process_idle
        source = "process_only"
        degraded = True
        reason = "no_user_input_signal"
    else:
        effective = min(user_idle, process_idle)
        source = "user_input+process"
        degraded = False
        reason = "user_input" if user_idle <= process_idle else "process_activity"

    cpu = cpu_percent() if sample_cpu else None
    idle_enough = effective >= float(idle_timeout)
    cpu_ok = cpu is None or cpu <= float(max_cpu)
    is_idle = bool(idle_enough and cpu_ok)

    if idle_enough and not cpu_ok:
        reason = "cpu_busy"

    return {
        "is_idle": is_idle,
        "reason": reason,
        "idle_source": source,
        "degraded": degraded,
        "idle_seconds": round(effective, 1),
        "user_input_idle_seconds": user_idle,
        "process_idle_seconds": round(process_idle, 1),
        "last_activity_age_s": round(process_idle, 1),
        "cpu_percent": cpu,
        "idle_timeout_sec": float(idle_timeout),
        "max_cpu_percent": float(max_cpu),
    }


def is_idle(*, idle_timeout: float = IDLE_TIMEOUT_SEC,
            max_cpu: float = MAX_CPU_PERCENT, sample_cpu: bool = True) -> bool:
    """空闲与否（只有两条判据都满足才为真；取不到的读数按"无法判定"处理）。"""
    return bool(state(idle_timeout=idle_timeout, max_cpu=max_cpu, sample_cpu=sample_cpu)["is_idle"])
