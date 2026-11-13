"""外部会话「全话」迁移 API（记忆运维页「外部会话导入」卡片的后端入口）。

与 /api/memory-import/*（卡片/向量导入）**分开**：载体不同（会话 vs 提炼记忆），
判据也不同（那边按批次确认；这边按来源整段幂等追加）。

| 端点 | 语义 | 写不写消息流 |
|---|---|---|
| GET  /api/memory/import/sessions/sources | 本机来源探测（只读；不解析正文） | 不写 |
| POST /api/memory/import/sessions         | dry-run 只报计数；dry_run=false + confirm=true 才追加 | 可能写 |
| GET  /api/memory/import/sessions/latest  | 最近一份报告（刷新页面后仍可看到上次结果） | 不写 |

【不易】"是否真写"由后端判据决定（confirm=true 且 dry_run=false），不靠前端自觉；
报告只回计数与来源，正文与凭据一律不出现。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from flask import request

from agent.api_envelope import ok, problem
from agent.memory_import import transcript
from agent.memory_import.models import SESSION_SOURCES, MemoryImportError
from agent.server_auth import log_request, require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

#: 同一时刻只允许一次会话迁移（会扫 GB 级源；并发只会互相拖慢并抢写同一文件）
_RUN_LOCK = threading.Lock()

_TRUE_WORDS = ("1", "true", "yes", "on")


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_WORDS


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return int(default)
    return max(int(low), min(int(high), number))


def _error(exc: MemoryImportError):
    """域错误 -> RFC 9457（title/detail 分开，不含明文）。"""
    return problem(exc.status, title=exc.title, detail=exc.detail,
                   type_slug=exc.code)


def register_routes(app, state) -> None:
    """注册外部会话迁移路由（记忆运维页使用）。"""

    @app.route("/api/memory/import/sessions/sources", methods=["GET"])
    @trace_route("MemorySessionsImport")
    @require_token
    @log_request(show_response=False)
    def api_memory_sessions_sources():
        """探测本机来源（只读；只数文件，不解析内容）。"""
        try:
            items = transcript.source_availability()
        except Exception:  # noqa: BLE001 探测失败不把异常原文回给客户端
            logger.exception("外部会话来源探测失败")
            return problem(500, title="来源探测失败",
                           detail="探测本机会话目录时出错，详见服务日志",
                           type_slug="sessions-sources-failed")
        return ok({"sources": items,
                   "note": "只读源文件；Trae 本地只有用户提问轨迹（无助手回复）"})

    @app.route("/api/memory/import/sessions/latest", methods=["GET"])
    @trace_route("MemorySessionsImport")
    @require_token
    @log_request(show_response=False)
    def api_memory_sessions_latest():
        """最近一份报告（只读；没有就回 report=null，不伪造）。"""
        source = str(request.args.get("source") or "").strip().lower()
        if source not in SESSION_SOURCES:
            return problem(400, title="来源参数无效",
                           detail="source 只能取 dsh / claude / trae",
                           type_slug="invalid-source")
        try:
            report = transcript.latest_report(source)
        except Exception:  # noqa: BLE001
            logger.exception("外部会话报告读取失败")
            return problem(500, title="报告读取失败",
                           detail="读取最近一次报告时出错，详见服务日志",
                           type_slug="sessions-report-failed")
        return ok({"source": source, "report": report})

    @app.route("/api/memory/import/sessions", methods=["POST"])
    @trace_route("MemorySessionsImport")
    @require_token
    @log_request(show_response=False)
    def api_memory_sessions_import():
        """按来源迁移会话：默认 dry-run；真写必须 confirm=true 且 dry_run=false。"""
        body = request.get_json(silent=True) or {}
        source = str(body.get("source") or "").strip().lower()
        if source not in SESSION_SOURCES:
            return problem(400, title="来源参数无效",
                           detail="source 只能取 dsh / claude / trae",
                           type_slug="invalid-source")
        dry_run = _as_bool(body.get("dry_run"), True)
        confirm = body.get("confirm") is True
        if not dry_run and not confirm:
            return problem(400, title="需要显式确认",
                           detail="真写入必须传 confirm=true；建议先 dry_run 看计数",
                           type_slug="confirmation-required")
        limit_sessions = _as_int(body.get("limit_sessions"), 0, 0,
                                 transcript.MAX_SESSIONS_PER_RUN)
        include_subagents = _as_bool(body.get("include_subagents"), True)
        if not _RUN_LOCK.acquire(blocking=False):
            return problem(409, title="已有迁移在跑",
                           detail="同一时刻只允许一次会话迁移，请等它结束再试",
                           type_slug="sessions-import-busy")
        try:
            result = transcript.import_sessions(
                source=source, dry_run=dry_run, confirm=confirm,
                limit_sessions=limit_sessions,
                include_subagents=include_subagents,
            )
        except MemoryImportError as exc:
            return _error(exc)
        except Exception:  # noqa: BLE001 解析/写入失败要如实报，但不回异常原文
            logger.exception("外部会话迁移失败")
            return problem(500, title="会话迁移失败",
                           detail="扫描或写入时出错，详见服务日志；已写入的行不会回滚",
                           type_slug="sessions-import-failed")
        finally:
            _RUN_LOCK.release()
        return ok(result)


__all__ = ["register_routes"]
