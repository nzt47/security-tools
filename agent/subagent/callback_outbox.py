"""子代理回调 outbox（离线回退：投递失败先落盘，稍后重放）

【解决什么】HttpCallbackDispatcher 的网络失败（超时 / 5xx / 离线）此前只把
delivered=False 记进委派结果，**没有落盘重试** ⇒ 对端恢复后这条回调永久丢失。
本模块提供 append-only 的本地 outbox：投递失败（且 mode=http）时把 callback_url +
payload 落一条 pending；replay() 逐条重投，成功即写一条 delivered 标记。

【默认逐字旧行为】CP_SUBAGENT_CALLBACK_OUTBOX_DIR 未配置（空）⇒ 不写、不读、不重放。
【依赖纪律】仅标准库。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

#: outbox 目录（空 = 关闭；登记于 agent/settings/registry.py）
ENV_OUTBOX_DIR = "CP_SUBAGENT_CALLBACK_OUTBOX_DIR"
OUTBOX_FILENAME = "callback_outbox.jsonl"
#: 单条 payload 落盘上限（防单条撑爆）
MAX_PAYLOAD_CHARS = 4000
MAX_REPLAY_DEFAULT = 50

__all__ = ["ENV_OUTBOX_DIR", "OUTBOX_FILENAME", "outbox_dir", "outbox_enabled",
           "outbox_path", "enqueue", "pending", "mark_delivered", "replay"]

_lock = threading.Lock()


def outbox_dir(environ: Optional[Mapping[str, str]] = None) -> str:
    raw = (os.environ.get(ENV_OUTBOX_DIR, "") if environ is None
           else environ.get(ENV_OUTBOX_DIR, ""))
    return str(raw or "").strip()


def outbox_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    return bool(outbox_dir(environ))


def outbox_path(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    d = outbox_dir(environ)
    return (Path(d) / OUTBOX_FILENAME) if d else None


def _append(path: Path, rec: Mapping[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(rec), ensure_ascii=False) + "\n")
        return True
    except OSError as e:
        logger.debug("[CallbackOutbox] 写入失败 %s: %s", path, e)
        return False


def enqueue(callback_url: str, payload: Mapping[str, Any], *, error: str = "",
            environ: Optional[Mapping[str, str]] = None,
            now: Optional[float] = None) -> Optional[str]:
    """落一条 pending 回调；未开启 / 写失败 ⇒ None（fail-soft，绝不抛）"""
    if not outbox_enabled(environ):
        return None
    path = outbox_path(environ)
    if path is None:
        return None
    body = json.dumps(dict(payload), ensure_ascii=False)
    if len(body) > MAX_PAYLOAD_CHARS:
        body = body[:MAX_PAYLOAD_CHARS]
    rec = {"kind": "pending", "id": uuid.uuid4().hex[:12],
           "ts": float(now if now is not None else time.time()),
           "callback_url": str(callback_url or ""), "payload": body,
           "error": str(error or "")[:200]}
    with _lock:
        return rec["id"] if _append(path, rec) else None


def _read_all(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        rows: List[dict] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
        return rows
    except OSError:
        return []


def pending(*, environ: Optional[Mapping[str, str]] = None) -> List[dict]:
    """未投递记录（按落盘顺序；已 delivered 的排除）"""
    path = outbox_path(environ)
    if path is None:
        return []
    with _lock:
        rows = _read_all(path)
    done = {r.get("id") for r in rows if r.get("kind") == "delivered"}
    return [r for r in rows if r.get("kind") == "pending" and r.get("id") not in done]


def mark_delivered(record_id: str, *, environ: Optional[Mapping[str, str]] = None,
                   now: Optional[float] = None) -> bool:
    path = outbox_path(environ)
    rid = str(record_id or "").strip()
    if path is None or not rid:
        return False
    with _lock:
        return _append(path, {"kind": "delivered", "id": rid,
                              "ts": float(now if now is not None else time.time())})


def replay(dispatcher: Callable[[Any, Mapping[str, Any]], Mapping[str, Any]], *,
           limit: int = MAX_REPLAY_DEFAULT,
           environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """重放未投递记录（单条失败不影响其余；返回计数，不抛）"""
    result: Dict[str, Any] = {"attempted": 0, "delivered": 0, "failed": 0,
                              "remaining": 0, "degraded": ""}
    if outbox_path(environ) is None:
        result["degraded"] = "outbox_disabled"
        return result
    if dispatcher is None:
        result["degraded"] = "no_dispatcher"
        return result
    todo = pending(environ=environ)[: max(0, int(limit))]
    for rec in todo:
        result["attempted"] += 1
        try:
            payload = json.loads(rec.get("payload") or "{}")
        except ValueError:
            payload = {}
        try:
            out = dispatcher(rec.get("callback_url"), payload)
        except Exception as e:  # noqa: BLE001 单条失败不影响其余
            result["failed"] += 1
            result["degraded"] = "replay_failed:%s" % type(e).__name__
            continue
        if bool((out or {}).get("delivered")) and mark_delivered(rec.get("id"), environ=environ):
            result["delivered"] += 1
        else:
            result["failed"] += 1
    result["remaining"] = len(pending(environ=environ))
    return result
