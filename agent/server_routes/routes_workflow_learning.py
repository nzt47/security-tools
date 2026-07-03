"""工作流学习系统 API 路由"""

from __future__ import annotations
import logging

from flask import request, jsonify

from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route
from agent.state_manager import get_workflow_learning_service
from agent.workflow_learning import (
    WorkflowLearningError,
    WorkflowNotFoundError,
    LearningRecord,
)

logger = logging.getLogger(__name__)


def _svc():
    return get_workflow_learning_service()


def _err(e: WorkflowLearningError, default_status: int = 400):
    status = default_status
    if isinstance(e, WorkflowNotFoundError):
        status = 404
    return jsonify(e.to_dict()), status


def register_routes(app, state):
    """注册工作流学习系统路由"""

    # ═══════════════════════════════════════════════════
    #  健康检查
    # ═══════════════════════════════════════════════════

    @app.route("/api/workflow-learning/health", methods=["GET"])
    @trace_route("WorkflowLearning")
    @log_request(show_response=False)
    def api_wf_health():
        try:
            return jsonify(_svc().health())
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  学习入口
    # ═══════════════════════════════════════════════════

    @app.route("/api/workflow-learning/learn", methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_learn():
        """从一次成功的 LLM 交互中学习方法

        Body: {
            session_id, user_input, tool_calls: [{name, params, output, success}],
            final_output?, success?, duration_ms?
        }
        """
        try:
            data = request.get_json() or {}
            record = LearningRecord(
                session_id=data.get("session_id", ""),
                user_input=data.get("user_input", ""),
                tool_calls=data.get("tool_calls", []),
                final_output=data.get("final_output", ""),
                success=data.get("success", True),
                duration_ms=float(data.get("duration_ms", 0)),
            )
            wf = _svc().learn_from_interaction(record)
            return jsonify({"ok": True, "workflow": wf.model_dump()}), 201
        except WorkflowLearningError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  匹配 / 执行
    # ═══════════════════════════════════════════════════

    @app.route("/api/workflow-learning/match", methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request(show_response=False)
    def api_wf_match():
        """模拟匹配 — 返回候选工作流列表 (不执行)

        Body: {task_text, top_k?}
        """
        try:
            data = request.get_json() or {}
            task_text = data.get("task_text", "")
            if not task_text:
                return jsonify({"ok": False, "error": "缺少 task_text"}), 400
            candidates = _svc().search(task_text, top_k=int(data.get("top_k", 5)))
            return jsonify({"ok": True, "candidates": candidates})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/try-execute", methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_try_execute():
        """尝试匹配并执行本地工作流 (优先于 LLM)

        Body: {task_text, params?}
        Returns: WorkflowExecutionResult — matched=False 时调用方应转 LLM
        """
        try:
            data = request.get_json() or {}
            task_text = data.get("task_text", "")
            if not task_text:
                return jsonify({"ok": False, "error": "缺少 task_text"}), 400
            result = _svc().try_execute(task_text, params=data.get("params"))
            return jsonify({"ok": True, "result": result.model_dump()})
        except WorkflowLearningError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/execute/<wf_id>", methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_execute_by_id(wf_id: str):
        """按 ID 直接执行工作流 (人工触发)

        Body: {task_text, params?}
        """
        try:
            data = request.get_json() or {}
            task_text = data.get("task_text", "")
            if not task_text:
                return jsonify({"ok": False, "error": "缺少 task_text"}), 400
            result = _svc().execute_by_id(wf_id, task_text, params=data.get("params"))
            return jsonify({"ok": True, "result": result.model_dump()})
        except WorkflowLearningError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  仓库管理
    # ═══════════════════════════════════════════════════

    @app.route("/api/workflow-learning/workflows", methods=["GET"])
    @trace_route("WorkflowLearning")
    @log_request(show_response=False)
    def api_wf_list():
        """列出所有工作流"""
        try:
            enabled_only = request.args.get("enabled_only", "").lower() in (
                "1", "true", "yes")
            items = _svc().list_workflows(enabled_only=enabled_only)
            return jsonify({
                "ok": True,
                "items": [w.model_dump() for w in items],
                "total": len(items),
            })
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/workflows/<wf_id>", methods=["GET"])
    @trace_route("WorkflowLearning")
    @log_request(show_response=False)
    def api_wf_get(wf_id: str):
        try:
            wf = _svc().get(wf_id)
            return jsonify({"ok": True, "workflow": wf.model_dump()})
        except WorkflowLearningError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/workflows/<wf_id>", methods=["DELETE"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_delete(wf_id: str):
        try:
            _svc().delete(wf_id)
            return jsonify({"ok": True})
        except WorkflowLearningError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/workflows/<wf_id>/toggle", methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_toggle(wf_id: str):
        try:
            data = request.get_json() or {}
            enabled = bool(data.get("enabled", True))
            wf = _svc().set_enabled(wf_id, enabled)
            return jsonify({"ok": True, "workflow": wf.model_dump()})
        except WorkflowLearningError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/workflows/<wf_id>/priority", methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_priority(wf_id: str):
        try:
            data = request.get_json() or {}
            priority = int(data.get("priority", 50))
            wf = _svc().update_priority(wf_id, priority)
            return jsonify({"ok": True, "workflow": wf.model_dump()})
        except WorkflowLearningError as e:
            return _err(e)
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  工作流 → 技能 转换
    # ═══════════════════════════════════════════════════

    @app.route("/api/workflow-learning/workflows/<wf_id>/convert-to-skill",
               methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_convert_to_skill(wf_id: str):
        """把指定工作流抽象为 Skill 并注册到 skills_mgmt

        Body: {force?: bool}

        Returns:
            {ok, workflow_id, skill_id, skill_name, version, action}
            错误码:
                - WORKFLOW_NOT_FOUND (404)
                - QUALITY_GATE_FAILED (400)
        """
        try:
            data = request.get_json() or {}
            force = bool(data.get("force", False))
            result = _svc().convert_to_skill(wf_id, force=force)
            return jsonify({"ok": True, **result})
        except WorkflowNotFoundError as e:
            return _err(e, 404)
        except WorkflowLearningError as e:
            return _err(e)
        except ValueError as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "code": "QUALITY_GATE_FAILED",
            }), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/convertible", methods=["GET"])
    @trace_route("WorkflowLearning")
    @log_request(show_response=False)
    def api_wf_convertible():
        """列出当前可转换为 Skill 的工作流（满足质量门控且未转换过）"""
        try:
            candidates = _svc().list_convertible_workflows()
            return jsonify({"ok": True, "candidates": candidates,
                            "total": len(candidates)})
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/workflow-learning/convert-external-skill", methods=["POST"])
    @trace_route("WorkflowLearning")
    @require_token
    @log_request()
    def api_wf_convert_external():
        """把外部 agent 的技能描述翻译为云枢 SKILL 并注册

        Body: {
            external_data: {name, description, steps/prompt, source_format?},
            llm_enabled?: bool,
            target_id?: str
        }

        Returns:
            {ok, skill_id, skill_name, source_format, action}
        """
        try:
            data = request.get_json() or {}
            external_data = data.get("external_data") or {}
            target_id = str(data.get("target_id", "") or "")
            llm_enabled = bool(data.get("llm_enabled", False))

            # 边界显性化
            if not external_data:
                return jsonify({
                    "ok": False,
                    "error": "external_data 不能为空",
                    "code": "VALIDATION_ERROR",
                }), 400

            llm_client = None
            if llm_enabled:
                try:
                    from agent.state_manager import get_llm_client
                    llm_client = get_llm_client()
                except Exception as e:
                    logger.warning("LLM client 不可用，降级规则转换: %s", e)

            result = _svc().convert_external_skill(
                external_data, llm_client=llm_client, target_id=target_id,
            )
            return jsonify({"ok": True, **result})
        except ValueError as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "code": "VALIDATION_ERROR",
            }), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(e)}), 500
