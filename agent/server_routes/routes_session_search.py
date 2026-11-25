"""E1-4b 会话全文检索路由（``GET /api/session-search``）

一条只读端点：按关键词在**全部会话消息**里找（FTS5 trigram 索引，
见 `agent/context/session_index.py`），返回命中片段与所属会话，供会话列表
「历史命中」区块消费。

口径：
- `q` 归一后为空 ⇒ `{ok:true, hits:[]}`（不是错误）；
- 中文 ≥3 字走 MATCH，<3 字回落 LIKE（都带 `LIMIT`，不会全表扫炸）；
- 索引关闭/不可用 ⇒ `ok=false` + 固定文案（不把异常原文塞进响应体）。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def register_routes(app, state=None):
    from agent.context import session_index as _idx

    @app.route("/api/session-search", methods=["GET"])
    @trace_route("SessionSearch")
    def api_session_search():
        """按关键词检索历史会话消息（只读）"""
        query = str(request.args.get("q") or "").strip()
        limit = request.args.get("limit")
        try:
            doc = _idx.search(query, limit=limit if limit is not None else _idx.DEFAULT_LIMIT)
        except Exception as exc:  # noqa: BLE001 检索失败不抛栈
            logger.warning("[session-search] 检索异常: %s", exc)
            return jsonify({"ok": False, "mode": "error", "hits": [],
                            "error": "检索失败（详见服务日志）"}), 200
        body = {
            "ok": bool(doc.get("ok")),
            "mode": doc.get("mode", ""),
            "query": _idx.normalize_query(query),
            "hits": doc.get("hits") or [],
            "took_ms": doc.get("took_ms", 0.0),
        }
        if doc.get("error"):
            body["error"] = doc["error"]
        return jsonify(body)
