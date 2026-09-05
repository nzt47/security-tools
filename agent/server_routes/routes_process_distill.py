"""过程蒸馏 HTTP API 路由（/api/process-distill/*）

把"知识库/素材 → 子代理蒸馏 → workflow/skill 固化"暴露为 REST：

    POST /api/process-distill/distill
        Body: {
            query?: str,            # 知识库 wiki 检索词（与 paths 至少其一）
            paths?: [str],          # 素材文件/目录路径
            artifacts?: ["workflow","skill"]（默认两者）
            top_k?: int, max_workers?: int, session_id?: str
        }
        Returns: {ok, query, paths, llm_used, materials: [摘要],
                  process: {...}, artifacts: {workflow?, skill?}}

    GET /api/process-distill/health  → {ok, llm_configured, artifacts}

注：与对话工具（distill_process_from_knowledge）同一服务；REST 面向
    外部/前端调用，material 正文在响应中做瘦身（仅 id/title/source_ref）。
"""

from __future__ import annotations

import logging

from flask import request, jsonify

from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

_svc = None


def _get_service():
    """惰性构造 ProcessDistillService（复用进程级单例，避免重复构建 LLM）。"""
    global _svc
    if _svc is None:
        from agent.process_distill.service import ProcessDistillService
        _svc = ProcessDistillService()
    return _svc


def _slim_materials(materials):
    """响应瘦身：只回 id/title/source_ref（全文太大，不进 HTTP 响应）。"""
    out = []
    for m in materials or []:
        if not isinstance(m, dict):
            continue
        out.append({k: m[k] for k in ("id", "title", "source_ref")
                    if k in m})
    return out


def register_routes(app, state):
    """注册过程蒸馏路由到 Flask 应用"""

    @app.route("/api/process-distill/health", methods=["GET"])
    @trace_route("ProcessDistill")
    @log_request(show_response=False)
    def api_pd_health():
        try:
            return jsonify(_get_service().health())
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/process-distill/distill", methods=["POST"])
    @trace_route("ProcessDistill")
    @require_token
    @log_request()
    def api_pd_distill():
        """从知识库/素材蒸馏并固化为 workflow/skill。"""
        try:
            data = request.get_json(silent=True) or {}
            query = str(data.get("query") or "").strip()
            raw_paths = data.get("paths") or []
            if isinstance(raw_paths, str):
                raw_paths = [raw_paths]
            paths = [str(p) for p in raw_paths if str(p).strip()]
            artifacts = data.get("artifacts")
            if artifacts is not None:
                if isinstance(artifacts, str):
                    artifacts = [artifacts]
                artifacts = [str(a) for a in artifacts]
            if not query and not paths:
                return jsonify({
                    "ok": False,
                    "error": "query 与 paths 至少提供一个（没有素材无法蒸馏）",
                    "code": "VALIDATION_ERROR",
                }), 400

            result = _get_service().distill(
                query=query,
                paths=paths,
                artifacts=artifacts,
                top_k=int(data.get("top_k") or 5),
                max_workers=int(data.get("max_workers") or 4),
                session_id=str(data.get("session_id")
                               or "process-distill-http"),
            )
            if result.get("ok"):
                result = dict(result)
                result["materials"] = _slim_materials(
                    result.get("materials", []))
            status = 200 if result.get("ok") else 404
            return jsonify(result), status
        except ValueError as e:
            return jsonify({
                "ok": False, "error": str(e), "code": "VALIDATION_ERROR",
            }), 400
        except Exception as e:  # noqa: BLE001
            logger.exception("[ProcessDistill] distill 异常")
            return jsonify({"ok": False, "error": str(e)}), 500
