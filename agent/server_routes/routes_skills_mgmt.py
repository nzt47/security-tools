"""技能管理系统 v1 API 路由

设计原则:
    - 边界显性化: 业务异常 → 4xx + 业务错误码；系统异常 → 5xx
    - 健康检查: /api/skills-mgmt/health 返回存储 + 模块依赖状态
    - 防连点: 写操作前端需配合 loading/disabled，后端用 SkillCreator 的锁
    - 后端权威原则: 写操作返回最新数据，前端不本地推导
"""

from __future__ import annotations
import logging
from typing import Any, Dict

from flask import request, jsonify

from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route
from agent.state_manager import get_skills_mgmt_service
from agent.skills_mgmt import (
    SkillMgmtError,
    SkillNotFoundError,
    SkillSearchParams,
)
from agent.skills_mgmt.reviewer import ReviewThresholds

logger = logging.getLogger(__name__)


def _svc():
    """懒加载服务 (避免启动时即初始化)"""
    return get_skills_mgmt_service()


def _err(e: SkillMgmtError, default_status: int = 400):
    """统一异常 → HTTP 响应"""
    status = default_status
    if isinstance(e, SkillNotFoundError):
        status = 404
    return jsonify(e.to_dict()), status


def register_routes(app, state):
    """注册技能管理 v1 路由"""

    # ═══════════════════════════════════════════════════
    #  健康检查
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/health", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_health():
        """技能管理系统健康检查"""
        try:
            return jsonify(_svc().health())
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  搜索 / 列表 / 详情
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/search", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_search():
        """高级搜索 — 支持分类/标签/状态/全文/分页/排序

        Query 参数:
            q, categories, tags, statuses, enabled_only,
            min_quality, sort_by, sort_desc, page, page_size
        """
        try:
            params = SkillSearchParams(
                query=request.args.get("q", ""),
                categories=request.args.getlist("categories"),
                tags=request.args.getlist("tags"),
                statuses=request.args.getlist("statuses"),
                enabled_only=request.args.get("enabled_only", "").lower() in ("1", "true", "yes"),
                min_quality_score=float(request.args.get("min_quality", 0)),
                sort_by=request.args.get("sort_by", "updated_at"),
                sort_desc=request.args.get("sort_desc", "1").lower() in ("1", "true", "yes"),
                page=int(request.args.get("page", 1)),
                page_size=int(request.args.get("page_size", 20)),
            )
            result = _svc().search(params)
            return jsonify({
                "ok": True,
                "items": [s.model_dump() for s in result.items],
                "total": result.total,
                "page": result.page,
                "page_size": result.page_size,
                "elapsed_ms": result.elapsed_ms,
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": f"参数错误: {e}"}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_list():
        """列出所有技能 (不分页)"""
        try:
            items = _svc().list_all()
            return jsonify({
                "ok": True,
                "items": [s.model_dump() for s in items],
                "total": len(items),
            })
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_get(skill_id: str):
        """获取单个技能详情"""
        try:
            skill = _svc().get(skill_id)
            return jsonify({"ok": True, "skill": skill.model_dump()})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  创建 (AI 辅助 / 手动 / 安装)
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/create/ai", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_create_ai():
        """AI 辅助生成技能

        Body: {name, intent, category?, tags?}
        """
        try:
            data = request.get_json() or {}
            name = data.get("name", "").strip()
            intent = data.get("intent", "").strip()
            if not name or not intent:
                return jsonify({"ok": False,
                                "error": "缺少 name 或 intent"}), 400
            skill = _svc().create_via_ai(
                name=name, intent=intent,
                category=data.get("category", "custom"),
                tags=data.get("tags"),
            )
            return jsonify({"ok": True, "skill": skill.model_dump()}), 201
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/create/manual", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_create_manual():
        """手动创建技能 — 直接提交完整字段"""
        try:
            data = request.get_json() or {}
            skill = _svc().create_manual(data)
            return jsonify({"ok": True, "skill": skill.model_dump()}), 201
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/install", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_install():
        """从外部来源安装技能

        Body: {source: 'github:user/repo' | 'url:...' | 'local:...' | 'registry:...', force?}
        """
        try:
            data = request.get_json() or {}
            source = data.get("source", "").strip()
            if not source:
                return jsonify({"ok": False, "error": "缺少 source"}), 400
            skill = _svc().install(source, force=bool(data.get("force", False)))
            return jsonify({"ok": True, "skill": skill.model_dump()}), 201
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  审核
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/<skill_id>/review", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_review(skill_id: str):
        """审核指定技能 (重复检测 + 安全扫描 + 质量评估)"""
        try:
            result = _svc().review(skill_id)
            return jsonify({"ok": True, "review": result.model_dump(),
                            "skill": _svc().get(skill_id).model_dump()})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/review/batch", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_review_batch():
        """批量审核所有 pending_review 状态的技能"""
        try:
            results = _svc().review_all_pending()
            return jsonify({"ok": True, "results": results})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/review/thresholds", methods=["GET", "PUT"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_review_thresholds():
        """获取或更新审核阈值"""
        try:
            svc = _svc()
            if request.method == "GET":
                t = svc.reviewer.thresholds
                return jsonify({"ok": True, "thresholds": {
                    "duplicate_max": t.duplicate_max,
                    "security_min": t.security_min,
                    "quality_min": t.quality_min,
                    "overall_min": t.overall_min,
                }})
            data = request.get_json() or {}
            svc.reviewer.thresholds = ReviewThresholds(
                duplicate_max=float(data.get("duplicate_max", 60.0)),
                security_min=float(data.get("security_min", 70.0)),
                quality_min=float(data.get("quality_min", 50.0)),
                overall_min=float(data.get("overall_min", 60.0)),
            )
            svc.reviewer.dup_detector.threshold = (
                svc.reviewer.thresholds.duplicate_max / 100.0)
            return jsonify({"ok": True})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  更新 / 删除 / 启停
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/<skill_id>", methods=["PATCH"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_update(skill_id: str):
        """部分更新技能字段 (白名单: name/description/tags/content/...)"""
        try:
            data = request.get_json() or {}
            skill = _svc().update(skill_id, data)
            return jsonify({"ok": True, "skill": skill.model_dump()})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>", methods=["DELETE"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_delete(skill_id: str):
        """删除技能"""
        try:
            _svc().delete(skill_id)
            return jsonify({"ok": True})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/toggle", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_toggle(skill_id: str):
        """切换启用状态 (防连点 — 由后端权威决定)"""
        try:
            data = request.get_json() or {}
            enabled = bool(data.get("enabled", True))
            skill = _svc().set_enabled(skill_id, enabled)
            return jsonify({"ok": True, "skill": skill.model_dump()})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  版本管理
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/<skill_id>/versions", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_versions(skill_id: str):
        """列出技能的所有历史版本"""
        try:
            versions = _svc().list_versions(skill_id)
            return jsonify({
                "ok": True,
                "versions": [v.model_dump() for v in versions],
            })
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/versions/bump", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_bump(skill_id: str):
        """升级技能版本

        Body: {kind: 'major'|'minor'|'patch', changelog?, content?}
        """
        try:
            data = request.get_json() or {}
            kind = data.get("kind", "patch")
            bump = _svc().bump_version(
                skill_id, kind,
                changelog=data.get("changelog", ""),
                content=data.get("content"),
            )
            return jsonify({
                "ok": True,
                "old_version": bump.old_version,
                "new_version": bump.new_version,
                "skill": _svc().get(skill_id).model_dump(),
            })
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/versions/rollback", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_rollback(skill_id: str):
        """回滚到指定历史版本

        Body: {target_version: 'x.y.z'}
        """
        try:
            data = request.get_json() or {}
            target = data.get("target_version", "")
            if not target:
                return jsonify({"ok": False, "error": "缺少 target_version"}), 400
            skill = _svc().rollback_version(skill_id, target)
            return jsonify({"ok": True, "skill": skill.model_dump()})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  参数优化 + 性能追踪
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/<skill_id>/optimize", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_optimize(skill_id: str):
        """基于使用指标推荐参数调整"""
        try:
            result = _svc().optimize_params(skill_id)
            return jsonify({"ok": True, **result})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/execution", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_record_execution(skill_id: str):
        """上报一次技能执行结果 (用于性能追踪)

        Body: {success: bool, latency_ms: number}
        """
        try:
            data = request.get_json() or {}
            _svc().record_execution(
                skill_id,
                success=bool(data.get("success", True)),
                latency_ms=float(data.get("latency_ms", 0)),
            )
            return jsonify({"ok": True})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  元数据
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/meta/categories", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_categories():
        """列出所有分类与标签 (供前端过滤面板)"""
        try:
            from agent.skills_mgmt.models import SkillCategory, SkillStatus
            skills = _svc().list_all()
            all_tags = set()
            for s in skills:
                all_tags.update(s.tags)
            return jsonify({
                "ok": True,
                "categories": [c.value for c in SkillCategory],
                "statuses": [s.value for s in SkillStatus],
                "tags": sorted(all_tags),
            })
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500
