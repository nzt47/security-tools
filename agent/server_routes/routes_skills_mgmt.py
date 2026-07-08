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

    @app.route("/api/skills-mgmt/upload", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_upload():
        """上传 zip 技能包并安装到三层架构文件仓库

        Multipart form-data:
            file: zip 文件
            force: 是否覆盖安装 (可选, 默认 true)
        """
        try:
            if "file" not in request.files:
                return jsonify({"ok": False, "error": "缺少 file 字段",
                                "code": "SKILL_VALIDATION_ERROR"}), 400
            file = request.files["file"]
            if not file.filename or not file.filename.endswith(".zip"):
                return jsonify({"ok": False, "error": "文件必须是 .zip 格式",
                                "code": "SKILL_VALIDATION_ERROR"}), 400

            # 保存到临时文件
            import tempfile, os
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="skill_upload_")
            try:
                os.close(tmp_fd)
                file.save(tmp_path)
                result = _svc().install_from_zip(tmp_path)
                logger.info("[SkillsMgmt] 上传安装成功: %s", result.get("skill_id"))
                return jsonify({"ok": True, "skill": result}), 201
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
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

        Body: {success: bool, latency_ms: number,
               feedback_rating?: int(0-5), feedback_id?: str, trace_id?: str,
               params_used?: dict}  # Item 4: 参数级追踪
        """
        try:
            data = request.get_json() or {}
            params_used = data.get("params_used")
            if params_used is not None and not isinstance(params_used, dict):
                return jsonify({
                    "ok": False,
                    "error": "params_used 必须是 dict",
                }), 400
            _svc().record_execution(
                skill_id,
                success=bool(data.get("success", True)),
                latency_ms=float(data.get("latency_ms", 0)),
                feedback_rating=int(data.get("feedback_rating", 0) or 0),
                feedback_id=str(data.get("feedback_id", "") or ""),
                trace_id=str(data.get("trace_id", "") or ""),
                params_used=params_used,
            )
            return jsonify({"ok": True})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/param-stats", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_param_stats(skill_id: str):
        """获取技能的参数级执行统计 (Item 4)

        Returns:
            {ok, skill_id, param_stats, avoid_params, current_default_hash}
        """
        try:
            skill = _svc().get(skill_id)
            from agent.skills_mgmt.enhancer import SkillEnhancer
            current_hash = SkillEnhancer._hash_params(skill.default_params) \
                if skill.default_params else None
            return jsonify({
                "ok": True,
                "skill_id": skill_id,
                "current_default_params": skill.default_params,
                "current_default_hash": current_hash,
                "param_stats": skill.metrics.param_stats,
                "avoid_params": skill.metrics.avoid_params,
            })
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  技能反馈绑定 (Feedback-Skill Binding)
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/<skill_id>/feedback", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_submit_feedback(skill_id: str):
        """提交针对某技能的用户反馈

        Body: {
            trace_id: str,
            feedback_type: "like"|"dislike"|"report"|"suggestion",
            rating?: int(0-5),
            comment?: str,
            category?: str,
            user_id?: str,
            session_id?: str,
            workflow_id?: str
        }

        Returns:
            {ok, feedback: {...}, summary: {...}}
            错误码:
                - SKILL_NOT_FOUND (404)
                - VALIDATION_ERROR (400)
        """
        try:
            data = request.get_json() or {}
            trace_id = str(data.get("trace_id", "") or "")
            feedback_type = str(data.get("feedback_type", "") or "")
            rating = int(data.get("rating", 0) or 0)

            # 边界显性化
            if not trace_id:
                return jsonify({
                    "ok": False,
                    "error": "trace_id 不能为空",
                    "code": "VALIDATION_ERROR",
                }), 400
            if feedback_type not in ("like", "dislike", "report", "suggestion"):
                return jsonify({
                    "ok": False,
                    "error": f"feedback_type 非法: {feedback_type}",
                    "code": "VALIDATION_ERROR",
                }), 400
            if rating < 0 or rating > 5:
                return jsonify({
                    "ok": False,
                    "error": f"rating 必须在 0-5 之间: {rating}",
                    "code": "VALIDATION_ERROR",
                }), 400

            result = _svc().submit_skill_feedback(
                skill_id,
                trace_id=trace_id,
                feedback_type=feedback_type,
                rating=rating,
                comment=str(data.get("comment", "") or ""),
                category=str(data.get("category", "other") or "other"),
                user_id=str(data.get("user_id", "") or ""),
                session_id=str(data.get("session_id", "") or ""),
                workflow_id=str(data.get("workflow_id", "") or ""),
            )
            return jsonify({"ok": True, **result})
        except SkillMgmtError as e:
            return _err(e)
        except ValueError as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "code": "VALIDATION_ERROR",
            }), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/feedback", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_get_feedback(skill_id: str):
        """获取技能反馈聚合统计

        Query: days=30 (统计窗口)

        Returns:
            {ok, summary: {skill_id, total_feedback, like_count,
                            dislike_count, satisfaction_rate_percent,
                            avg_rating, by_category, recommended_action, ...}}
        """
        try:
            days = int(request.args.get("days", 30))
            if days <= 0 or days > 365:
                return jsonify({
                    "ok": False,
                    "error": "days 必须在 1-365 之间",
                    "code": "VALIDATION_ERROR",
                }), 400
            summary = _svc().get_skill_feedback_summary(skill_id, days=days)
            return jsonify({"ok": True, "summary": summary})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/optimize-with-feedback", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_optimize_with_feedback(skill_id: str):
        """一键式：拉取反馈统计 + 触发参数优化

        Query: days=30

        Returns:
            {ok, recommendations, actions_taken, metrics_snapshot, feedback_summary}
        """
        try:
            days = int(request.args.get("days", 30))
            result = _svc().optimize_with_feedback(skill_id, days=days)
            return jsonify({"ok": True, **result})
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

    # ═══════════════════════════════════════════════════
    #  重复技能检测与合并 (Jaccard≥0.7 触发)
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/duplicates", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_duplicates():
        """扫描整个技能库，列出 Jaccard≥阈值的重复对

        Query: min_jaccard=0.7

        Returns:
            {ok, total, duplicates: [{skill_a, skill_b, jaccard,
                                       content_hash_match, recommend_action}]}
        """
        try:
            min_jaccard = float(request.args.get("min_jaccard", 0.7))
            if min_jaccard < 0.0 or min_jaccard > 1.0:
                return jsonify({
                    "ok": False,
                    "error": "min_jaccard 必须在 0.0-1.0 之间",
                    "code": "VALIDATION_ERROR",
                }), 400
            duplicates = _svc().list_duplicates(min_jaccard=min_jaccard)
            return jsonify({
                "ok": True,
                "total": len(duplicates),
                "duplicates": duplicates,
            })
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/duplicates", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_duplicates_for(skill_id: str):
        """找出与指定技能重复的其他技能

        Query: min_jaccard=0.7
        """
        try:
            min_jaccard = float(request.args.get("min_jaccard", 0.7))
            duplicates = _svc().find_duplicates_for(
                skill_id, min_jaccard=min_jaccard,
            )
            return jsonify({
                "ok": True,
                "skill_id": skill_id,
                "total": len(duplicates),
                "duplicates": duplicates,
            })
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/merge", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_merge():
        """合并两个重复技能

        Body: {
            src_id: str,            # 被合并方（将被删除）
            dst_id: str,            # 合并保留方
            strategy?: str,         # auto | keep_dst | keep_src (默认 auto)
            rebind_feedback?: bool  # 是否改绑 feedback 表（默认 true）
        }

        Returns:
            {ok, merged_id, removed_id, merged_fields, version_added,
             feedback_rebound_count}
            错误码:
                - VALIDATION_ERROR (400)
                - SKILL_NOT_FOUND (404)
        """
        try:
            data = request.get_json() or {}
            src_id = str(data.get("src_id", "") or "")
            dst_id = str(data.get("dst_id", "") or "")
            strategy = str(data.get("strategy", "auto") or "auto")
            rebind_feedback = bool(data.get("rebind_feedback", True))

            # 边界显性化
            if not src_id or not dst_id:
                return jsonify({
                    "ok": False,
                    "error": "src_id 与 dst_id 不能为空",
                    "code": "VALIDATION_ERROR",
                }), 400
            if strategy not in ("auto", "keep_dst", "keep_src"):
                return jsonify({
                    "ok": False,
                    "error": f"strategy 非法: {strategy}",
                    "code": "VALIDATION_ERROR",
                }), 400

            result = _svc().merge_duplicate_skills(
                src_id, dst_id,
                strategy=strategy,
                rebind_feedback=rebind_feedback,
            )
            return jsonify({"ok": True, **result})
        except SkillMgmtError as e:
            return _err(e)
        except ValueError as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "code": "VALIDATION_ERROR",
            }), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/auto-merge", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request()
    def api_skills_mgmt_auto_merge():
        """自动合并高相似度技能对（Jaccard ≥ 阈值）

        仅合并 recommend_action == "merge" 的对，避免误伤 review 类。

        Body: {min_jaccard?: 0.85, max_merges?: 10}
        """
        try:
            data = request.get_json() or {}
            min_jaccard = float(data.get("min_jaccard", 0.85))
            max_merges = int(data.get("max_merges", 10))
            if min_jaccard < 0.5 or min_jaccard > 1.0:
                return jsonify({
                    "ok": False,
                    "error": "min_jaccard 必须在 0.5-1.0 之间",
                    "code": "VALIDATION_ERROR",
                }), 400
            if max_merges < 1 or max_merges > 100:
                return jsonify({
                    "ok": False,
                    "error": "max_merges 必须在 1-100 之间",
                    "code": "VALIDATION_ERROR",
                }), 400

            result = _svc().auto_merge_duplicates(
                min_jaccard=min_jaccard,
                max_merges=max_merges,
            )
            return jsonify({"ok": True, **result})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  三层架构 (Layer 1/2/3) — 分层检索 + 脚本沙箱执行
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/three-layer/summary", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_layer_summary():
        """三层架构统计摘要 — 供前端可视化

        返回:
            {ok, summary: {layer1, layer2, layer3, total_skills, total_scripts}}
        """
        try:
            return jsonify({"ok": True, "summary": _svc().get_layer_summary()})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/match", methods=["POST"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_match():
        """Layer 1: 意图匹配 — 在元数据索引上做快速检索

        Body: {intent: str, top_k?: int=5, enabled_only?: bool=true, min_score?: float=0.01}

        Returns:
            {ok, matches: [...], total_scanned, elapsed_ms, estimated_total_tokens}
        """
        try:
            data = request.get_json() or {}
            intent = (data.get("intent") or "").strip()
            if not intent:
                return jsonify({"ok": False,
                                "error": "缺少 intent 参数",
                                "code": "SKILL_VALIDATION_ERROR"}), 400
            top_k = int(data.get("top_k", 5))
            enabled_only = bool(data.get("enabled_only", True))
            min_score = float(data.get("min_score", 0.01))
            result = _svc().match_skills(
                intent, top_k=top_k,
                enabled_only=enabled_only, min_score=min_score,
            )
            return jsonify({
                "ok": True,
                "matches": [m.to_dict() for m in result.matches],
                "total_scanned": result.total_scanned,
                "elapsed_ms": result.elapsed_ms,
                "estimated_total_tokens": result.estimated_total_tokens,
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": f"参数错误: {e}"}), 400
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/instruction", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_instruction(skill_id: str):
        """Layer 2: 按需加载技能使用说明 (skill.md 正文)

        仅在 Layer 1 命中后才应调用，避免无谓加载。
        """
        try:
            return jsonify({"ok": True,
                            **_svc().load_skill_instruction(skill_id)})
        except SkillMgmtError as e:
            return _err(e)

    # ═══════════════════════════════════════════════════
    #  记忆 → 技能自动抽象
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/abstract-from-memory", methods=["POST"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_abstract_from_memory():
        """从云枢记忆中自动抽象新技能草稿

        Body:
            days: int = 30        — 回溯最近 N 天的记忆
            max_skills: int = 5   — 最多生成多少个草稿
            auto_register: bool = False — True 时自动注册到技能库
            min_cluster_size: int = 3   — 聚类最小大小 (覆盖默认)
            min_success_rate: float = 0.7 — 聚类最小成功率 (覆盖默认)
            cluster_jaccard: float = 0.5  — 聚类合并 Jaccard 阈值 (覆盖默认)
            max_existing_dup_jaccard: float = 0.7 — 与已有技能最大相似度
            enable_signal_scoring: bool = True — 是否启用信号评分过滤

        Returns:
            {ok, total_input_memories, total_clusters,
             passed_clusters, registered_count,
             drafts: [{cluster_id, cluster_size, success_rate,
                       common_tool_names, common_tags,
                       draft_skill_id, draft_name, draft_description,
                       draft_content_preview, draft_default_params,
                       quality_gate_passed, quality_gate_reasons,
                       registered, skill_id, duplicate_of}, ...]}
        """
        try:
            from agent.skills_mgmt.memory_abstractor import MemorySkillAbstractor

            data = request.get_json() or {}
            days = int(data.get("days", 30))
            max_skills = int(data.get("max_skills", 5))
            auto_register = bool(data.get("auto_register", False))
            min_cluster_size = int(data.get("min_cluster_size",
                                            MemorySkillAbstractor.MIN_CLUSTER_SIZE))
            min_success_rate = float(data.get("min_success_rate",
                                              MemorySkillAbstractor.MIN_SUCCESS_RATE))
            cluster_jaccard = float(data.get("cluster_jaccard",
                                             MemorySkillAbstractor.CLUSTER_JACCARD_THRESHOLD))
            max_existing_dup_jaccard = float(
                data.get("max_existing_dup_jaccard",
                         MemorySkillAbstractor.MAX_EXISTING_DUP_JACCARD),
            )
            enable_signal_scoring = bool(
                data.get("enable_signal_scoring", True),
            )

            # 参数边界校验
            if days < 1:
                return jsonify({"ok": False,
                                "error": "days 必须 >= 1"}), 400
            if max_skills < 1 or max_skills > 50:
                return jsonify({"ok": False,
                                "error": "max_skills 必须在 1..50"}), 400
            if not (0.0 <= min_success_rate <= 1.0):
                return jsonify({"ok": False,
                                "error": "min_success_rate 必须在 0..1"}), 400
            if not (0.0 <= cluster_jaccard <= 1.0):
                return jsonify({"ok": False,
                                "error": "cluster_jaccard 必须在 0..1"}), 400
            if not (0.0 <= max_existing_dup_jaccard <= 1.0):
                return jsonify({"ok": False,
                                "error": "max_existing_dup_jaccard 必须在 0..1"}), 400

            abstractor = MemorySkillAbstractor(
                skills_service=_svc(),
                min_cluster_size=min_cluster_size,
                min_success_rate=min_success_rate,
                max_existing_dup_jaccard=max_existing_dup_jaccard,
                cluster_jaccard=cluster_jaccard,
                enable_signal_scoring=enable_signal_scoring,
            )

            drafts = abstractor.abstract_new_skills(
                days=days,
                max_skills=max_skills,
                auto_register=auto_register,
            )

            passed_count = sum(1 for d in drafts if d["quality_gate_passed"])
            registered_count = sum(1 for d in drafts if d.get("registered"))

            return jsonify({
                "ok": True,
                "total_input_memories": sum(
                    d["cluster_size"] for d in drafts
                ),
                "total_clusters": len(drafts),
                "passed_clusters": passed_count,
                "registered_count": registered_count,
                "drafts": drafts,
            })
        except ValueError as e:
            return jsonify({"ok": False,
                            "error": f"参数错误: {e}"}), 400
        except Exception as e:  # noqa: BLE001
            logger.exception("[SkillsMgmt] abstract-from-memory 失败")
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/scripts", methods=["GET"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_list_scripts(skill_id: str):
        """Layer 3: 列出技能的脚本文件 (元信息，不加载代码)"""
        try:
            scripts = _svc().list_skill_scripts(skill_id)
            return jsonify({"ok": True, "scripts": scripts,
                            "total": len(scripts)})
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/<skill_id>/execute", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request(show_response=False)
    def api_skills_mgmt_execute(skill_id: str):
        """Layer 3: 沙箱执行技能脚本

        Body: {script_name?: str="main.py", params?: dict, timeout?: float}

        Returns:
            {ok, success, result?, error?, exit_code, duration_ms, timed_out}
        """
        try:
            data = request.get_json() or {}
            script_name = data.get("script_name", "main.py")
            params = data.get("params")
            timeout = data.get("timeout")
            if timeout is not None:
                timeout = float(timeout)
            result = _svc().execute_skill_script(
                skill_id, script_name=script_name,
                params=params, timeout=timeout,
            )
            # 仅暴露 result/error/stderr，不暴露 raw stdout
            return jsonify({
                "ok": result.success,
                "success": result.success,
                "result": result.result,
                "error": result.error,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "stderr": result.stderr,
            })
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/skills-mgmt/inject", methods=["POST"])
    @trace_route("SkillsMgmt")
    @log_request(show_response=False)
    def api_skills_mgmt_inject():
        """一站式构建 LLM 上下文 (Layer 1 + Layer 2)

        Body: {intent: str, max_tokens?: int=6000, top_k?: int=5,
               auto_load_instruction?: bool=false, skill_id?: str}

        Returns:
            {ok, prompt, matches, instruction?, estimated_tokens, layers_used}
        """
        try:
            data = request.get_json() or {}
            intent = (data.get("intent") or "").strip()
            if not intent:
                return jsonify({"ok": False,
                                "error": "缺少 intent 参数",
                                "code": "SKILL_VALIDATION_ERROR"}), 400
            ctx = _svc().build_skill_context(
                intent,
                max_tokens=int(data.get("max_tokens", 6000)),
                top_k=int(data.get("top_k", 5)),
                auto_load_instruction=bool(data.get("auto_load_instruction", False)),
                skill_id=data.get("skill_id"),
            )
            return jsonify({"ok": True, **ctx})
        except ValueError as e:
            return jsonify({"ok": False, "error": f"参数错误: {e}"}), 400
        except SkillMgmtError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  P1.5 Slash 命令解析器 — 统一入口
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills-mgmt/skill/<skill_id>", methods=["POST"])
    @trace_route("SkillsMgmt")
    @require_token
    @log_request(show_response=False)
    def api_skills_mgmt_slash(skill_id: str):
        """Slash 命令解析器 — 统一技能操作入口

        Body: {
            command: "info|execute|evolve|params|versions",
            params?: dict,        # execute/params 命令的参数
            script_name?: str,    # execute 命令的脚本名
            timeout?: float,      # execute 命令的超时
            patch?: dict,         # params 命令的更新字段
            strategies?: list,    # evolve 命令的变异策略
        }

        Returns:
            {ok, command, result, ...} — 各命令的统一响应格式
        """
        try:
            data = request.get_json() or {}
            command = (data.get("command") or "").strip().lower()
            if not command:
                return jsonify({
                    "ok": False,
                    "error": "缺少 command 参数",
                    "code": "SKILL_VALIDATION_ERROR",
                    "supported_commands": ["info", "execute", "evolve",
                                           "params", "versions"],
                }), 400

            svc = _svc()

            # ─── info: 获取技能详情 ───
            if command == "info":
                skill = svc.get(skill_id)
                return jsonify({
                    "ok": True,
                    "command": "info",
                    "skill": skill.to_dict() if hasattr(skill, "to_dict") else skill.model_dump(),
                })

            # ─── execute: 执行技能脚本 ───
            if command == "execute":
                script_name = data.get("script_name", "main.py")
                params = data.get("params")
                timeout = data.get("timeout")
                if timeout is not None:
                    timeout = float(timeout)
                result = svc.execute_skill_script(
                    skill_id, script_name=script_name,
                    params=params, timeout=timeout,
                )
                return jsonify({
                    "ok": result.success,
                    "command": "execute",
                    "success": result.success,
                    "result": result.result,
                    "error": result.error,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "validation_status": getattr(result, "validation_status", "skipped"),
                })

            # ─── evolve: 触发离线进化 ───
            if command == "evolve":
                from agent.skills_mgmt.offline_evolver import (
                    OfflineEvolver, EvolutionStrategy,
                )
                strategies = None
                if data.get("strategies"):
                    strategies = [EvolutionStrategy(s) for s in data["strategies"]]
                evolver = OfflineEvolver(svc.store, svc.enhancer)
                result = evolver.evolve_once(skill_id, strategies=strategies)
                return jsonify({
                    "ok": not result.skipped and result.error is None,
                    "command": "evolve",
                    "skill_id": result.skill_id,
                    "strategy": result.strategy.value if result.strategy else None,
                    "old_version": result.old_version,
                    "new_version": result.new_version,
                    "improvement": result.improvement,
                    "committed": result.committed,
                    "skipped": result.skipped,
                    "error": result.error,
                })

            # ─── params: 更新参数 ───
            if command == "params":
                patch = data.get("patch") or {}
                if not patch:
                    return jsonify({
                        "ok": False,
                        "error": "params 命令需要 patch 字段",
                        "code": "SKILL_VALIDATION_ERROR",
                    }), 400
                updated = svc.update(skill_id, patch)
                return jsonify({
                    "ok": True,
                    "command": "params",
                    "skill": updated.to_dict() if hasattr(updated, "to_dict") else updated.model_dump(),
                })

            # ─── versions: 获取版本历史 ───
            if command == "versions":
                versions = svc.list_versions(skill_id)
                return jsonify({
                    "ok": True,
                    "command": "versions",
                    "versions": [v.to_dict() if hasattr(v, "to_dict") else v.model_dump()
                                 for v in versions],
                })

            # ─── 未知命令 ───
            return jsonify({
                "ok": False,
                "error": f"不支持的命令: {command}",
                "code": "SKILL_VALIDATION_ERROR",
                "supported_commands": ["info", "execute", "evolve",
                                       "params", "versions"],
            }), 400

        except SkillNotFoundError as e:
            return _err(e)
        except SkillMgmtError as e:
            return _err(e)
        except ValueError as e:
            return jsonify({"ok": False, "error": f"参数错误: {e}"}), 400
        except Exception as e:  # noqa: BLE001
            logger.exception("slash command failed: skill_id=%s", skill_id)
            return jsonify({"ok": False, "error": str(e)}), 500
