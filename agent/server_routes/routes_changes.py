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
import os

from flask import jsonify, request

from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route
from agent.server_routes._errors import safe_error as _safe

logger = logging.getLogger(__name__)


def _session_id() -> str:
    return str(request.args.get("session_id") or "").strip()


def _scope_arg() -> str:
    """scope=session（只看本会话）| workspace（默认，工作区级最近一次）"""
    raw = str(request.args.get("scope") or "").strip().lower()
    return "session" if raw == "session" else "workspace"


#: E4-5：worktree 面板开关（默认关；判据只在这一处与 registry 登记项）
WORKTREE_ENABLED_ENV = "CP_WORKTREE_PANEL_ENABLED"


def _worktree_enabled() -> bool:
    return str(os.environ.get(WORKTREE_ENABLED_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


#: E4-5 第六批续：worktree 写路径开关（与只读面板分开；默认关，B 级）
WORKTREE_WRITE_ENABLED_ENV = "CP_WORKTREE_WRITE_ENABLED"


def _worktree_write_enabled() -> bool:
    return str(os.environ.get(WORKTREE_WRITE_ENABLED_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _wt_msg(code: str) -> str:
    """失败码 → 中文短句（唯一来源在 agent/git_worktree.CODE_MESSAGES）"""
    try:
        from agent import git_worktree as _wt

        return _wt.CODE_MESSAGES.get(code, "操作失败（详见服务日志）")
    except Exception:  # noqa: BLE001
        return "操作失败（详见服务日志）"


def _write_result(result, create: bool = False):
    """写路径结果 → (JSON, HTTP 状态)：失败码映射固定，前端只认 code"""
    if result.get("ok"):
        return jsonify(result), 200
    code = str(result.get("code") or ("create_failed" if create else "commit_failed"))
    status = {
        "write_disabled": 403,
        "bad_name": 400,
        "bad_message": 400,
        "exists": 409,
        "branch_exists": 409,
        "nothing_to_commit": 409,
        "not_found": 404,
        "no_git": 503,
    }.get(code, 500)
    return jsonify({"ok": False, "code": code, "error": _wt_msg(code)}), status



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

    @app.route("/api/git/worktrees", methods=["GET"])
    @trace_route("GitWorktrees")
    def api_git_worktrees():
        """E4-5：git worktree 的**只读**投影（默认关；不建/不删/不提交）"""
        if not _worktree_enabled():
            return jsonify({"ok": False, "code": "disabled",
                            "error": "worktree 面板未启用（CP_WORKTREE_PANEL_ENABLED=0）"})
        try:
            from agent import git_worktree as _wt

            return jsonify(_wt.collect())
        except Exception as exc:  # noqa: BLE001 只读面板：异常只回固定文案
            logger.warning("[worktrees] 读取失败: %s", exc)
            return jsonify({"ok": False, "error": _safe(exc, logger)}), 500

    # ── E4-5 第六批续：worktree 的 B 级写路径（Owner 2026-11-28 已点头）──
    # 范围只有两件事：建 worktree（分支 yunshu/<name>）、在该 worktree 里本地提交；
    # **不推送、不改远程、不删 worktree**。两把钥匙：面板开 + 写路径开。

    @app.route("/api/git/worktrees", methods=["POST"])
    @trace_route("GitWorktreeCreate")
    @require_token
    def api_git_worktree_create():
        """建一个 worktree（B 级；默认关；只建不推）"""
        if not _worktree_enabled():
            return jsonify({"ok": False, "code": "disabled",
                            "error": "worktree 面板未启用（CP_WORKTREE_PANEL_ENABLED=0）"})
        if not _worktree_write_enabled():
            return jsonify({"ok": False, "code": "write_disabled",
                            "error": _wt_msg("write_disabled")}), 403
        body = request.get_json(silent=True) or {}
        try:
            from agent import git_worktree as _wt

            return _write_result(_wt.create(body.get("name")), create=True)
        except Exception as exc:  # noqa: BLE001 写路径失败不把 git 输出搬给前端
            logger.warning("[worktrees] 创建失败: %s", exc)
            return jsonify({"ok": False, "code": "create_failed",
                            "error": _wt_msg("create_failed")}), 500

    @app.route("/api/git/worktrees/commit", methods=["POST"])
    @trace_route("GitWorktreeCommit")
    @require_token
    def api_git_worktree_commit():
        """在指定 worktree 里本地提交（B 级；默认关；**不推送**）"""
        if not _worktree_enabled():
            return jsonify({"ok": False, "code": "disabled",
                            "error": "worktree 面板未启用（CP_WORKTREE_PANEL_ENABLED=0）"})
        if not _worktree_write_enabled():
            return jsonify({"ok": False, "code": "write_disabled",
                            "error": _wt_msg("write_disabled")}), 403
        body = request.get_json(silent=True) or {}
        try:
            from agent import git_worktree as _wt

            return _write_result(_wt.commit(body.get("name"), body.get("message")))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[worktrees] 提交失败: %s", exc)
            return jsonify({"ok": False, "code": "commit_failed",
                            "error": _wt_msg("commit_failed")}), 500
