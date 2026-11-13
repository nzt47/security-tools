"""未分会话历史池（legacy pool）—— 只读快照 + 冻结口径。

【是什么】data/messages.jsonl 里早期写入的消息**没有 session_id**（会话分家之前的历史）。
2026-11-09 起压缩触发、摘要、最近消息窗口都已按会话收口（CP_CONTEXT_WINDOW_SCOPED /
CP_SESSION_SUMMARY_SCOPED），这些无主消息**不再参与**任何会话的压缩判定 —— 但它们仍在文件里，
看起来像「一个 300 条的池子没人管」，容易被误读成「压缩漏掉了它们」。

【怎么办】不做迁移（把别人的历史搬进某个会话 = 把无关上下文塞给这个会话，比冻在那儿更坏），
按「冻结」处理：保留原样、不再写、不再读进上下文，并在 /api/context/status 上如实披露
条数与口径，让界面能写「未分会话历史池 N 条 · 已冻结」。

【成本】1MB 级文件逐行计数，60s 缓存一次（面板 30s 轮询也不会反复扫盘）；
解析失败逐行跳过，绝不因为一行坏数据把整块读数变成异常。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

LEGACY_POOL_PATH = Path(__file__).resolve().parent.parent / "data" / "messages.jsonl"

#: 快照缓存（秒）：文件只在真实写入时变化，60s 足够，且不拖慢 30s 轮询的面板。
CACHE_TTL_SEC = 60.0

#: 口径文案：界面与文档共用一句，避免各处自称一套。
FROZEN_NOTE = ("未分会话历史池：会话分家前写入、没有 session_id 的消息。"
               "已冻结——不参与任何会话的压缩触发、摘要与最近消息窗口，也不再写入。")

_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"at": 0.0, "payload": None}


def _count(path: Path) -> Dict[str, int]:
    messages = 0
    legacy = 0
    chars = 0
    # 2026-11-14：外部会话「全话」迁入后，同一份文件里既有冻结池、也有带 session_id
    # 的导入行。这里如实分列三个数（**不动 legacy 口径**）：
    #   session_messages = 带 session_id 的行数（含导入与既有会话）
    #   sessions         = 去重后的 session_id 数（导入按源+原会话分）
    #   imported_messages= 带 import_key 的行数（外部会话迁移写入的行）
    session_messages = 0
    imported = 0
    sessions = set()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                messages += 1
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001 坏行跳过（不为一行坏数据整块报错）
                    continue
                session_id = str(row.get("session_id") or "")
                if not session_id:
                    legacy += 1
                    chars += len(str(row.get("content") or ""))
                else:
                    session_messages += 1
                    # 同名会话可能来自不同源：导入行带 source，按 (source, session_id) 去重
                    source = str(row.get("import_source") or row.get("source") or "")
                    sessions.add((source, session_id))
                if row.get("import_key"):
                    imported += 1
    except OSError:
        return {"messages": 0, "legacy_messages": 0, "legacy_chars": 0,
                "session_messages": 0, "sessions": 0, "imported_messages": 0}
    return {"messages": messages, "legacy_messages": legacy, "legacy_chars": chars,
            "session_messages": session_messages, "sessions": len(sessions),
            "imported_messages": imported}


def snapshot(*, refresh: bool = False) -> Dict[str, Any]:
    """未分会话历史池快照（带 60s 缓存）。

    Returns:
        {"legacy_messages": int, "legacy_chars": int, "messages_total": int,
         "policy": "frozen", "note": str, "available": bool}
    """
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE["payload"]
        if not refresh and cached is not None and (now - _CACHE["at"]) < CACHE_TTL_SEC:
            return dict(cached)
    available = LEGACY_POOL_PATH.exists()
    counts = _count(LEGACY_POOL_PATH) if available else {
        "messages": 0, "legacy_messages": 0, "legacy_chars": 0,
        "session_messages": 0, "sessions": 0, "imported_messages": 0}
    payload = {
        "available": available,
        "policy": "frozen",
        "legacy_messages": counts["legacy_messages"],
        "legacy_chars": counts["legacy_chars"],
        "messages_total": counts["messages"],
        # 会话面读数（2026-11-14）：外部会话迁移的验收就看这三个数的增量，
        # **legacy_messages 必须保持原值**（冻结池不因导入而变化）。
        "session_messages": counts["session_messages"],
        "sessions": counts["sessions"],
        "imported_messages": counts["imported_messages"],
        "note": FROZEN_NOTE,
    }
    with _LOCK:
        _CACHE["at"] = now
        _CACHE["payload"] = payload
    return dict(payload)


def reset_cache() -> None:
    """仅供测试：清缓存。"""
    with _LOCK:
        _CACHE["at"] = 0.0
        _CACHE["payload"] = None
