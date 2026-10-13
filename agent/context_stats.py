"""会话级读数暂存区（运行时内存，不落盘、不入库）

【为什么需要】plugins/chat.py 的流式主循环每轮都能**实测**模型用时 / 工具调用用时 /
首 token 平均与上下文装配明细，而消费方 /api/context/status（plugins/memory.py）在
另一个模块里。这里放一个极小的线程安全暂存区，让「生产者写、消费者读」，避免两个
插件模块互相 import（插件间顶层 import 是循环导入红线）。

【口径】
  · 累计的是**当前会话**（session_id）自被首次观测以来的读数，换会话各自独立；
  · ttft_ms 是各轮首 token 耗时的**算术平均**（只保留最近 50 个样本，防内存失控）；
  · breakdown 取**最近一轮**的上下文装配（窗口组成随对话变化，累计无意义）；
  · 纯内存：进程重启即清空（面板随即显示 0 / —，不伪造历史）。
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_stats: dict[str, dict] = {}

#: 最多保留多少个会话的读数（简单 LRU：超出后丢最早的键）
_MAX_SESSIONS = 64
#: 首 token 样本上限
_MAX_TTFT_SAMPLES = 50


def record_turn(session_id, *, model_ms=0.0, tool_ms=0.0, ttft_ms=None,
                breakdown=None) -> None:
    """记录一轮对话的读数（best-effort，永不抛）"""
    key = str(session_id or "")
    if not key:
        return
    try:
        with _lock:
            cur = _stats.get(key) or {
                "turns": 0, "model_ms": 0.0, "tool_ms": 0.0,
                "ttft_samples": [], "breakdown": None,
            }
            cur["turns"] = int(cur.get("turns", 0)) + 1
            cur["model_ms"] = float(cur.get("model_ms", 0.0)) + float(model_ms or 0.0)
            cur["tool_ms"] = float(cur.get("tool_ms", 0.0)) + float(tool_ms or 0.0)
            if ttft_ms is not None:
                samples = list(cur.get("ttft_samples") or [])
                samples.append(float(ttft_ms))
                cur["ttft_samples"] = samples[-_MAX_TTFT_SAMPLES:]
            if breakdown:
                cur["breakdown"] = dict(breakdown)
            _stats[key] = cur
            if len(_stats) > _MAX_SESSIONS:
                for k in list(_stats)[: len(_stats) - _MAX_SESSIONS]:
                    _stats.pop(k, None)
    except Exception:  # noqa: BLE001 读数统计失败不得影响对话
        pass


def get_session_stats(session_id) -> dict:
    """取该会话的累计读数（无记录时返回零值，**不伪造**）"""
    key = str(session_id or "")
    with _lock:
        cur = dict(_stats.get(key) or {})
    samples = list(cur.get("ttft_samples") or [])
    return {
        "turns": int(cur.get("turns", 0)),
        "model_ms": round(float(cur.get("model_ms", 0.0)), 1),
        "tool_ms": round(float(cur.get("tool_ms", 0.0)), 1),
        "ttft_ms": (round(sum(samples) / len(samples), 1) if samples else None),
        "ttft_samples": len(samples),
        "breakdown": cur.get("breakdown"),
    }


def reset(session_id=None) -> None:
    """清空某会话（或全部）读数（测试用）"""
    with _lock:
        if session_id is None:
            _stats.clear()
        else:
            _stats.pop(str(session_id), None)


__all__ = ["record_turn", "get_session_stats", "reset"]
