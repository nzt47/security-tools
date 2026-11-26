"""E1-3b 变更文件卡路由（``GET /api/changes``）

一条只读端点：把 E0-3 检查点里**最近一次**（或指定 ``checkpoint`` 的）工作区快照
摊成「逐文件变更 + 每个文件的 diff」，交给会话页的 ChangeCard 渲染。

口径（与设计页 `docs/plan/2026-11-25-e1-3-interaction-design.md` §2/§6 一致）：
- 数据源只有检查点，**不新增采集逻辑**（检查点已有去抖与上界）；
- 检查点是 **workspace 级**的，历史快照没有 `session_id`；因此：
  · 请求必须带 `session_id`（与其它会话态端点同形，前端不用特判）；
  · 快照的 `session_id` 与请求一致 → ``scope="session"``，否则 ``scope="workspace"``
    （**不假装**是"这个会话改的"）；UI 按 scope 显示不同说明；
- diff 逐文件截断（每文件 20k 字符、整份 200k），截断就标 ``truncated``，不静默丢。
"""
from __future__ import annotations

import logging

from flask import jsonify, request

from agent.server_routes.tracing_decorator import trace_route
from agent.server_routes._errors import safe_error as _safe

logger = logging.getLogger(__name__)


def _session_id() -> str:
    return str(request.args.get("session_id") or "").strip()


def _scope_arg() -> str:
    """scope=session（只看本会话）| workspace（默认，工作区级最近一次）"""
    raw = str(request.args.get("scope") or "").strip().lower()
    return "session" if raw == "session" else "workspace"



def register_routes(app, state=None):
    from agent.context import checkpoint as _cp

    @app.route("/api/changes", methods=["GET"])
    @trace_route("Changes")
    def api_changes():
        """最近一次（或指定）检查点的逐文件变更（只读）"""
        sid = _session_id()
        if not sid:
            return jsonify({"ok": False, "error": "session_id 不能为空"}), 400
        wanted = str(request.args.get("checkpoint") or "").strip()
        scope_arg = _scope_arg()
        try:
            # 「只看本会话」= 只在 session_id 严格相等的快照里取；默认仍是工作区级最近一次
            doc = _cp.changes(wanted, session_id=sid if scope_arg == "session" else "")
        except Exception as exc:  # noqa: BLE001 检查点损坏不该 500 出异常原文
            logger.warning("[changes] 读取检查点失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500
        if doc is None:
            if wanted:
                return jsonify({"ok": False, "code": "unknown_checkpoint",
                                "error": "检查点不存在或已清理"}), 404
            if scope_arg == "session":
                return jsonify({
                    "ok": True, "session_id": sid, "scope": "session",
                    "checkpoint": None, "files": [],
                    "counts": {"files": 0, "added": 0, "removed": 0, "truncated": 0},
                    "note": "本会话还没有检查点：会改工作区的工具在本会话里跑过之后才有快照"
                            "（老快照没有会话归属，只出现在「看全部」里）",
                })
            return jsonify({
                "ok": True, "session_id": sid, "scope": "workspace",
                "checkpoint": None, "files": [],
                "counts": {"files": 0, "added": 0, "removed": 0, "truncated": 0},
                "note": "还没有检查点：会改工作区的工具跑过之后才会生成快照",
            })
        meta = doc.get("checkpoint") or {}
        scoped = bool(meta.get("session_id")) and meta.get("session_id") == sid
        if scope_arg == "session" and not scoped:
            # 指定了 checkpoint 但那份快照不属于本会话：明确拒绝（不静默换一份）
            return jsonify({"ok": False, "code": "not_in_session",
                            "error": "该检查点不是本会话触发的"}), 404
        files = doc.get("files") or []
        return jsonify({
            "ok": True,
            "session_id": sid,
            "scope": "session" if scoped else "workspace",
            "checkpoint": meta,
            "files": files,
            "counts": {
                "files": len(files),
                "added": sum(int(f.get("added") or 0) for f in files),
                "removed": sum(int(f.get("removed") or 0) for f in files),
                "truncated": sum(1 for f in files if f.get("truncated")),
            },
            "note": ("" if scoped else
                     "检查点是工作区级快照（可能由一个会话外的动作触发），不保证只含本会话的改动"),
        })
