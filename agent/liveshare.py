"""E4-5 第六批：LiveShare —— 只读分享（令牌只存 sha256，读面按游标轮询）

【不易·安全边界（Owner 2026-11-28 已拍板）】
  1. 允许"持分享令牌即可读、免登录"：因此 **令牌 = 一次性凭据**，服务端**只存 sha256**；
  2. 分享范围 = 整段对话 + 之后的新消息（读面按 seq 游标增量返回）；
  3. TTL：默认 24h、最小 5 分钟、上限 7 天；可随时撤销（按会话整体撤销）。
  其余红线：一枚令牌只对应一个会话；只读；不投影工具步骤/附件路径/系统提示词；
  过期/撤销/不存在 **同一个 404**（不给枚举）；日志只记 token 的 sha256 前 8 位。

【变易·存储】data/liveshare/shares.json（运行期、gitignore）：原子写、加锁；
  过期/撤销超过 7 天的条目在写入时顺带清理，不无限增长。

【简易·投影】project() 是纯函数：seq 从 1 开始、正文截 2000 字符、单次最多 50 条；
  只回 role/content/timestamp/chars/truncated —— 其余字段一律不出去。
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 默认/最小/最大有效期（秒）
DEFAULT_TTL_SECONDS = 24 * 3600
MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 7 * 24 * 3600
#: 过期/撤销的记录保留多久（秒）——只是清理，不影响判据
RETENTION_SECONDS = 7 * 24 * 3600
#: 令牌随机字节数（token_urlsafe(32) ≈ 43 字符）
TOKEN_BYTES = 32
#: 单次读面的分页上限与正文截断
MAX_PAGE = 50
DEFAULT_PAGE = 20
MAX_CONTENT_CHARS = 2000
#: 令牌在日志里的可辨识长度（sha256 前 8 位）
LOG_DIGEST_CHARS = 8

_ROOT = Path(__file__).resolve().parents[1]
#: 分享记录目录（测试注入用模块级变量）
SHARES_DIR = _ROOT / "data" / "liveshare"
SHARES_FILE = "shares.json"

_SID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_LOCK = threading.Lock()


def _shares_path(root: Optional[Path] = None) -> Path:
    return (root or SHARES_DIR) / SHARES_FILE


def token_digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8", "replace")).hexdigest()[:32]


def log_digest(token: str) -> str:
    """只用于日志的可辨识摘要（不泄露令牌本体）"""
    return hashlib.sha256(str(token or "").encode("utf-8", "replace")).hexdigest()[:LOG_DIGEST_CHARS]


def _now(now: Optional[float]) -> float:
    return float(now) if now is not None else time.time()


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(epoch)))


def _parse_iso(stamp: str) -> float:
    """ISO(UTC) → epoch。【必须用 calendar.timegm】time.mktime 按**本地时区**解析，
    会让过期判据整体偏移一个时区（本机 +8h ⇒ 提前 8 小时失效，实测踩过）。"""
    try:
        return float(calendar.timegm(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return 0.0


def clamp_ttl(ttl_seconds: Any) -> int:
    try:
        value = int(ttl_seconds)
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, value))


def _read(root: Optional[Path] = None) -> Dict[str, Any]:
    path = _shares_path(root)
    if not path.exists():
        return {"version": 1, "items": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "items": {}}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, dict):
        return {"version": 1, "items": {}}
    clean: Dict[str, Dict[str, Any]] = {}
    for key, row in items.items():
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        sid = str(row.get("session_id") or "")
        if not _SID_RE.match(sid):
            continue
        clean[key[:64]] = {
            "session_id": sid,
            "created_at": str(row.get("created_at") or ""),
            "expires_at": str(row.get("expires_at") or ""),
            "revoked_at": str(row.get("revoked_at") or ""),
        }
    return {"version": 1, "items": clean}


def _write(items: Dict[str, Dict[str, Any]], root: Optional[Path] = None) -> None:
    path = _shares_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "items": items}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def _expired_before(item: Dict[str, Any], now_epoch: float) -> bool:
    """过期/撤销超过保留期 ⇒ 可清理（判据仍是 expires_at，不受保留期影响）"""
    cutoff = _iso(now_epoch - RETENTION_SECONDS)
    stamp = str(item.get("expires_at") or "")
    if item.get("revoked_at"):
        stamp = str(item.get("revoked_at"))
    return bool(stamp) and stamp < cutoff


def create_share(session_id: str, ttl_seconds: Any = None,
                 now: Optional[float] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """建一条分享记录（明文令牌只在此返回一次；盘上只有 sha256）"""
    sid = str(session_id or "")
    if not _SID_RE.match(sid):
        raise ValueError("非法会话 ID")
    epoch = _now(now)
    ttl = clamp_ttl(ttl_seconds)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    record = {"session_id": sid, "created_at": _iso(epoch), "expires_at": _iso(epoch + ttl),
              "revoked_at": ""}
    with _LOCK:
        payload = _read(root)
        items = payload["items"]
        items[token_digest(token)] = record
        for key in [k for k, v in items.items() if _expired_before(v, epoch)]:
            items.pop(key, None)
        _write(items, root)
    return {"token": token, "expires_at": record["expires_at"],
            "created_at": record["created_at"], "ttl_seconds": ttl}


def resolve(token: str, now: Optional[float] = None,
            root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """令牌 → 分享记录；不存在/已撤销/已过期一律 None（调用方回统一 404）"""
    raw = str(token or "").strip()
    if len(raw) < 16:
        return None
    epoch = _now(now)
    payload = _read(root)
    row = payload["items"].get(token_digest(raw))
    if not isinstance(row, dict):
        return None
    if row.get("revoked_at"):
        return None
    expires = _parse_iso(str(row.get("expires_at")))
    if expires <= epoch:
        return None
    return dict(row)


def revoke_session(session_id: str, now: Optional[float] = None,
                   root: Optional[Path] = None) -> int:
    """撤销某会话的全部分享（返回撤销条数；已是撤销态的不重复计）"""
    sid = str(session_id or "")
    if not _SID_RE.match(sid):
        return 0
    stamp = _iso(_now(now))
    revoked = 0
    with _LOCK:
        payload = _read(root)
        items = payload["items"]
        for row in items.values():
            if row.get("session_id") == sid and not row.get("revoked_at"):
                row["revoked_at"] = stamp
                revoked += 1
        if revoked:
            _write(items, root)
    return revoked


def list_shares(session_id: str, now: Optional[float] = None,
                root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """某会话的分享状态（**不含令牌、不含摘要**）：active = 未撤销且未过期"""
    sid = str(session_id or "")
    epoch = _now(now)
    out: List[Dict[str, Any]] = []
    for row in _read(root)["items"].values():
        if row.get("session_id") != sid:
            continue
        expires = _parse_iso(str(row.get("expires_at")))
        out.append({"created_at": row.get("created_at") or "",
                    "expires_at": row.get("expires_at") or "",
                    "revoked": bool(row.get("revoked_at")),
                    "active": (not row.get("revoked_at")) and expires > epoch})
    out.sort(key=lambda item: item["created_at"], reverse=True)
    return out


def prune(now: Optional[float] = None, root: Optional[Path] = None) -> int:
    """清掉过期/撤销超过保留期的记录（返回清理条数）"""
    epoch = _now(now)
    removed = 0
    with _LOCK:
        payload = _read(root)
        items = payload["items"]
        for key in [k for k, v in items.items() if _expired_before(v, epoch)]:
            items.pop(key, None)
            removed += 1
        if removed:
            _write(items, root)
    return removed


def _clamp(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def project(messages: List[Dict[str, Any]], after: Any = 0,
            limit: Any = DEFAULT_PAGE) -> Dict[str, Any]:
    """把消息压成只读分页（纯函数）：seq 从 1 开始，正文截断，字段白名单"""
    total = len(messages or [])
    start = _clamp(after, 0, 0, total)
    window = _clamp(limit, DEFAULT_PAGE, 1, MAX_PAGE)
    rows: List[Dict[str, Any]] = []
    for index in range(start, total):
        message = messages[index]
        if not isinstance(message, dict):
            continue
        body = str(message.get("content") or "")
        rows.append({
            "seq": index + 1,
            "role": str(message.get("role") or ""),
            "content": body[:MAX_CONTENT_CHARS],
            "chars": len(body),
            "truncated": len(body) > MAX_CONTENT_CHARS,
            "timestamp": str(message.get("timestamp") or ""),
        })
        if len(rows) >= window:
            break
    next_after = rows[-1]["seq"] if rows else start
    return {"messages": rows, "total": total, "next_after": next_after,
            "truncated": next_after < total, "limit": window}


__all__ = [
    "DEFAULT_PAGE", "DEFAULT_TTL_SECONDS", "MAX_CONTENT_CHARS", "MAX_PAGE",
    "MAX_TTL_SECONDS", "MIN_TTL_SECONDS", "RETENTION_SECONDS", "SHARES_DIR",
    "SHARES_FILE", "clamp_ttl", "create_share", "list_shares", "log_digest",
    "project", "prune", "resolve", "revoke_session", "token_digest",
]
