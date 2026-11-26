"""Token 计量（token-meter）——把「这次请求真实装了多少 token」变成压缩触发的一等输入

【为什么需要这一层】压缩触发（memory.MemoryManager._should_compress_now）与界面读数
（plugins/memory.py 的 /api/context/status）此前量的是**同一个代理指标**：本会话最近
COMPRESS_TRIGGER_WINDOW(200) 条正文。同源没错，但「记忆池里的正文」≠「真正装进请求的上下文」
（系统提示 + 工具 schema + 历史 + 摘要 + 检索片段，实测主线光固定开销就上万 token）。
DSH 的 token-meter 用的是**实测装配量**，本模块补的正是这半个能力。

【口径（不要和别处各写一套）】
  · record() 只记**实测值**：由装配方（plugins/chat.py 的流式主循环、
    orchestrator._check_context_usage）用同一个 TokenCounter(tiktoken cl100k) 量完再记，
    本模块**不做估算**，也绝不按字符数换算；
  · should_compress() 带**水位**：云枢压缩只写摘要、不删消息 ⇒ 量值不会自己掉下来，
    不记水位就会每条消息压一次（docs/handoff/口径.md §7）。水位在压缩**成功后**由
    note_compaction() 记下当时的实测值，下次要「再长 growth 倍」才允许再压；
  · 分母优先用**编排窗口**（token_limit，与 /api/context/status.token_limit 同一个数），
    缺省用样本自带的 window_tokens；
  · 纯内存、进程重启即清空（快照不伪造历史，也不落盘制造第二份状态）；会话数上限 128（LRU）。

【开关】CP_TOKEN_METER（默认开）。关掉 = 只按记忆窗口口径触发（旧行为），
读数照旧给出实测值（那是**读数**，不是行为，没有关的理由）。
"""
from __future__ import annotations

import os
import threading
import time

#: 实测 token 驱动压缩的开关（关闭 ⇒ 触发只看记忆窗口，读数仍在）
ENV_TOKEN_METER = "CP_TOKEN_METER"
_TRUE_VALUES = ("1", "true", "yes", "on")

#: 最多保留多少个会话的读数（简单 LRU：超出后丢最早的键）
MAX_SESSIONS = 128


def enabled() -> bool:
    """实测口径是否参与**压缩触发**（缺省/拼错一律开；要关必须显式写 0/false/no/off）"""
    raw = str(os.environ.get(ENV_TOKEN_METER, "")).strip().lower()
    if not raw:
        return True
    return raw in _TRUE_VALUES


_lock = threading.Lock()
_meter: dict = {}


def _key(session_id) -> str:
    return str(session_id or "")


def record(session_id, *, prompt_tokens, window_tokens=None, source="",
           overhead_tokens=None, history_tokens=None):
    """记一次**实测**装配量（best-effort，永不抛）。

    Args:
        session_id: 会话键；空串 = 未分会话（单桶，旧调用方）。
        prompt_tokens: 这次请求装进上下文的 token（输入侧）。
        window_tokens: 该次装配用的窗口（分母的次选；编排窗口拿得到时以编排为准）。
        source: 产出方标记（chat / orchestrator / 探针），只用于排障。
        overhead_tokens/history_tokens: 可选分解（系统提示+工具 / 历史），面板展示用。

    Returns:
        写入后的快照；参数非法（负数/非数）时返回 None 且不污染既有样本。
    """
    key = _key(session_id)
    if not key:
        return None
    try:
        prompt = int(prompt_tokens)
        window = int(window_tokens) if window_tokens is not None else None
    except (TypeError, ValueError):
        return None
    if prompt < 0 or (window is not None and window < 0):
        return None
    try:
        with _lock:
            cur = _meter.get(key) or {"samples": 0, "watermark_tokens": 0, "compactions": 0}
            cur.update({
                "prompt_tokens": prompt,
                "window_tokens": window,
                "source": str(source or ""),
                "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "overhead_tokens": (int(overhead_tokens)
                                    if overhead_tokens is not None else None),
                "history_tokens": (int(history_tokens)
                                   if history_tokens is not None else None),
                "samples": int(cur.get("samples", 0)) + 1,
            })
            _meter[key] = cur
            if len(_meter) > MAX_SESSIONS:
                for stale in list(_meter)[: len(_meter) - MAX_SESSIONS]:
                    _meter.pop(stale, None)
            return dict(cur)
    except Exception:  # noqa: BLE001 读数失败绝不影响对话
        return None


def usage(session_id, *, limit_tokens=None):
    """取该会话最近一次实测（含按分母算的占用率）；**无样本返回 None，不伪造 0**。

    这是触发与界面的**唯一**读数入口：/api/context/status 与本模块的
    should_compress() 都只调它。
    """
    key = _key(session_id)
    if not key:
        return None
    try:
        with _lock:
            cur = _meter.get(key)
            snap = dict(cur) if cur else None
    except Exception:  # noqa: BLE001
        return None
    if not snap:
        return None
    prompt = int(snap.get("prompt_tokens") or 0)
    window = snap.get("window_tokens")
    limit = int(limit_tokens) if limit_tokens else (int(window) if window else 0)
    snap["limit_tokens"] = limit or None
    snap["percentage"] = (round(prompt / limit * 100, 1) if limit else None)
    return snap


def note_compaction(session_id) -> int:
    """压缩成功后记水位（= 当时的实测 prompt token）并计数；返回写入的水位。

    失败/无样本时水位记 0（= 下次不设闸门），与 MemoryManager._set_floor 的容错一致。
    """
    key = _key(session_id)
    if not key:
        return 0
    with _lock:
        cur = _meter.get(key) or {"samples": 0, "prompt_tokens": 0, "window_tokens": None}
        cur["watermark_tokens"] = int(cur.get("prompt_tokens") or 0)
        cur["compactions"] = int(cur.get("compactions", 0)) + 1
        _meter[key] = cur
        return int(cur["watermark_tokens"])


def should_compress(session_id, *, limit_tokens=None, threshold=0.8,
                    growth_ratio=1.10):
    """实测口径判一次「该不该压缩」；不需要/无样本/开关关 ⇒ None。

    返回 {reason, prompt_tokens, limit_tokens, percentage, watermark_tokens,
    sampled_at, source}（可直接进日志与状态接口，便于复核「为什么压/为什么没压」）。
    """
    if not enabled():
        return None
    snap = usage(session_id, limit_tokens=limit_tokens)
    if not snap:
        return None
    limit = int(snap.get("limit_tokens") or 0)
    if limit <= 0:
        return None
    prompt = int(snap.get("prompt_tokens") or 0)
    try:
        need = float(limit) * float(threshold)
    except (TypeError, ValueError):
        return None
    if prompt < need:
        return None
    watermark = int(snap.get("watermark_tokens") or 0)
    if watermark and prompt < int(watermark * float(growth_ratio)):
        return None                      # 水位没长够 ⇒ 不重复压（口径 §7）
    return {
        "reason": "token_meter",
        "prompt_tokens": prompt,
        "limit_tokens": limit,
        "percentage": snap.get("percentage"),
        "watermark_tokens": watermark,
        "sampled_at": snap.get("sampled_at"),
        #: 判据来源恒为 token_meter；样本是谁产出的另列（chat / orchestrator），便于排障时区分
        "source": "token_meter",
        "sampled_from": snap.get("source"),
    }


def reset(session_id=None) -> None:
    """清空某会话（或全部）读数（测试用；生产不需要）"""
    with _lock:
        if session_id is None:
            _meter.clear()
        else:
            _meter.pop(_key(session_id), None)


__all__ = [
    "ENV_TOKEN_METER",
    "MAX_SESSIONS",
    "enabled",
    "record",
    "usage",
    "note_compaction",
    "should_compress",
    "reset",
]
