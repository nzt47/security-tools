"""E4-5 第六批：LiveShare 路由 —— 一个公开只读读面 + 三条登录态管理面（默认关）

| 端点 | 鉴权 | 语义 |
|---|---|---|
| GET  /api/liveshare/messages?after=&limit= | **分享令牌**（X-LiveShare-Token 头） | 只读分页投影；令牌无效/过期/撤销/功能关一律 **404 同形** |
| POST /api/sessions/<sid>/share | 登录令牌 | 建分享，回**一次性**明文令牌与地址（盘上只存 sha256） |
| GET  /api/sessions/<sid>/share | 登录令牌 | 看该会话分享状态（**不含令牌/摘要**） |
| DELETE /api/sessions/<sid>/share | 登录令牌 | 撤销该会话全部分享 |

【为什么公开面在 /api/liveshare/*】鉴权中间件按前缀白名单放行它（Owner 2026-11-28 拍板"允许免登录持令牌读"）；
该前缀下**只有这一条**路由，且它在路由内自己校验令牌（fail-closed），不许再往里加写路由。
【为什么功能关时是 404 而不是 disabled】公开面不该对外宣告"这里有个分享功能"，也不给枚举者任何信号。
【限流】单 IP 每分钟 ≤ 60 次（进程内滑动窗口）；超限 429。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List

from flask import jsonify, request

from agent import liveshare as _liveshare
from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

#: 功能总开关（默认关：公开读面 404、管理面回 disabled）
ENABLED_ENV = "CP_LIVESHARE_ENABLED"
#: 默认有效期（秒；请求可传更短的 ttl_seconds，最终仍夹在 MIN/MAX 之间）
TTL_ENV = "CP_LIVESHARE_TTL_SECONDS"
#: 分享令牌的请求头（不走 query：URL 会进 Referer / 反代日志）
TOKEN_HEADER = "X-LiveShare-Token"
#: 限流：窗口秒数 / 窗口内最大请求数
RATE_WINDOW_SECONDS = 60
RATE_MAX_REQUESTS = 60

_TRUE_WORDS = ("1", "true", "yes", "on")
_RATE_LOCK = threading.Lock()
_RATE_HITS: Dict[str, List[float]] = {}


def _flag(env_name: str) -> bool:
    return str(__import__("os").environ.get(env_name, "") or "").strip().lower() in _TRUE_WORDS


def _default_ttl() -> int:
    import os

    try:
        value = int(str(os.environ.get(TTL_ENV, "") or "").strip() or _liveshare.DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        value = _liveshare.DEFAULT_TTL_SECONDS
    return _liveshare.clamp_ttl(value)


def _mgr():
    from app_server import _session_mgr

    return _session_mgr


def _client_ip() -> str:
    return str(request.remote_addr or "unknown")


def _rate_limited(ip: str, now: float | None = None) -> bool:
    """进程内滑动窗口限流（返回 True = 超限）"""
    epoch = float(now) if now is not None else time.time()
    with _RATE_LOCK:
        hits = [stamp for stamp in _RATE_HITS.get(ip, []) if epoch - stamp < RATE_WINDOW_SECONDS]
        if len(hits) >= RATE_MAX_REQUESTS:
            _RATE_HITS[ip] = hits
            return True
        hits.append(epoch)
        _RATE_HITS[ip] = hits
        if len(_RATE_HITS) > 5000:  # 防内存无界：过多来源时丢掉最旧的
            for key in list(_RATE_HITS)[:1000]:
                _RATE_HITS.pop(key, None)
        return False


def _not_found():
    """公开面统一 404（功能关/令牌无效/过期/撤销/会话不存在 全同形）"""
    return jsonify({"ok": False, "code": "not_found", "error": "链接不存在或已失效"}), 404


def _error(code: str, detail: str, status: int = 400):
    return jsonify({"ok": False, "code": code, "error": detail}), status


def reset_rate_limit_for_tests() -> None:
    with _RATE_LOCK:
        _RATE_HITS.clear()


def register_routes(app, state=None):
    @app.route("/api/liveshare/messages", methods=["GET"])
    @trace_route("LiveShareMessages")
    def api_liveshare_messages():
        """公开只读读面：按分享令牌取某会话的消息分页（不写、不枚举）"""
        if not _flag(ENABLED_ENV):
            return _not_found()
        if _rate_limited(_client_ip()):
            return _error("rate_limited", "请求过于频繁，请稍后再试", 429)
        token = str(request.headers.get(TOKEN_HEADER) or "").strip()
        record = _liveshare.resolve(token)
        if record is None:
            logger.info("[liveshare] 令牌无效/过期 digest=%s", _liveshare.log_digest(token))
            return _not_found()
        session_id = str(record.get("session_id") or "")
        try:
            mgr = _mgr()
            meta = mgr.get_session_metadata(session_id)
            if not meta:
                return _not_found()
            messages = mgr.get_messages(session_id)
        except Exception:  # noqa: BLE001 读失败不把异常原文回给公开访问者
            logger.exception("[liveshare] 读会话失败")
            return jsonify({"ok": False, "code": "read_failed", "error": "读取失败"}), 500
        body = _liveshare.project(messages, after=request.args.get("after"),
                                  limit=request.args.get("limit"))
        return jsonify({"ok": True,
                        "title": str(meta.get("title") or ""),
                        "expires_at": record.get("expires_at") or "",
                        **body})

    @app.route("/api/sessions/<session_id>/share", methods=["GET", "POST", "DELETE"])
    @trace_route("SessionShare")
    @require_token
    def api_session_share(session_id):
        """分享管理（登录态）：建 / 看状态 / 按会话整体撤销"""
        if not _flag(ENABLED_ENV):
            return jsonify({"ok": False, "code": "disabled",
                            "error": "会话分享未启用（CP_LIVESHARE_ENABLED=0）"})
        try:
            exists = _mgr().get_session(session_id) is not None
        except Exception:  # noqa: BLE001
            logger.exception("[liveshare] 读会话失败")
            return _error("read_failed", "读取会话失败", 500)
        if not exists:
            return _error("not_found", "会话不存在", 404)
        method = request.method.upper()
        if method == "GET":
            return jsonify({"ok": True, "enabled": True,
                            "shares": _liveshare.list_shares(session_id),
                            "default_ttl_seconds": _default_ttl()})
        if method == "DELETE":
            revoked = _liveshare.revoke_session(session_id)
            return jsonify({"ok": True, "revoked": revoked})
        body = request.get_json(silent=True) or {}
        try:
            created = _liveshare.create_share(
                session_id, ttl_seconds=body.get("ttl_seconds") or _default_ttl())
        except ValueError:
            return _error("bad_session", "会话 ID 不可用于分享", 400)
        except OSError:
            logger.exception("[liveshare] 写分享记录失败")
            return _error("share_failed", "创建分享失败（详见服务日志）", 500)
        return jsonify({"ok": True, "token": created["token"],
                        "expires_at": created["expires_at"],
                        "ttl_seconds": created["ttl_seconds"],
                        "url_path": "/#/live/" + created["token"]})


__all__ = ["ENABLED_ENV", "RATE_MAX_REQUESTS", "RATE_WINDOW_SECONDS", "TOKEN_HEADER",
           "TTL_ENV", "register_routes", "reset_rate_limit_for_tests"]
