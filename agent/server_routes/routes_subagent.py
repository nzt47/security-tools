"""分身 (Subagent) API 路由

提供分身的创建、查询、执行、销毁等 REST API。
"""

import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request

logger = logging.getLogger(__name__)


def register_routes(app, state):
    """注册所有分身管理路由"""

    Yunshu = state.Yunshu

    # ═══════════════════════════════════════════════════
    #  列表 & 状态
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/list")
    @log_request(show_response=False)
    def api_subagent_list():
        """获取所有活跃分身列表"""
        try:
            subagents = Yunshu.list_subagents()
            return jsonify({"ok": True, "subagents": subagents, "count": len(subagents)})
        except Exception as e:
            logger.error("[SubagentAPI] 列表查询失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/subagent/<name>")
    @log_request(show_response=False)
    def api_subagent_get(name):
        """获取指定分身详情"""
        try:
            subagent = Yunshu.get_subagent(name)
            if subagent is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404
            return jsonify({"ok": True, "subagent": subagent})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  创建 & 销毁
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/create", methods=["POST"])
    @require_token
    @log_request()
    def api_subagent_create():
        """创建新分身

        POST JSON:
            name (str): 分身名称（唯一）
            model_id (str): LLM 模型 ID
            memory_provider (str): 记忆提供商
            tool_sources (list[str], optional): 工具源列表
            permissions (list[str], optional): 权限列表（默认 ['read']）
            context_window (int, optional): 上下文窗口大小（默认 4096）
            tags (list[str], optional): 标签
            ttl_seconds (int, optional): 存活时间（0=永久）
        """
        try:
            data = request.get_json() or {}

            required = ["name", "model_id", "memory_provider"]
            missing = [k for k in required if k not in data]
            if missing:
                return jsonify({"ok": False, "error": f"缺少必要字段: {missing}"}), 400

            config = {
                "name": data["name"],
                "model_id": data["model_id"],
                "memory_provider": data["memory_provider"],
                "tool_sources": data.get("tool_sources", []),
                "permissions": data.get("permissions", ["read"]),
                "context_window": data.get("context_window", 4096),
                "tags": data.get("tags", []),
                "ttl_seconds": data.get("ttl_seconds", 0),
            }

            container = Yunshu.create_subagent(config)
            return jsonify({
                "ok": True,
                "subagent": container.get_status(),
                "message": f"分身 '{container.config.name}' 创建成功",
            })
        except Exception as e:
            logger.error("[SubagentAPI] 创建失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/subagent/<name>/destroy", methods=["POST"])
    @require_token
    @log_request()
    def api_subagent_destroy(name):
        """销毁指定分身"""
        try:
            report = Yunshu.destroy_subagent(name)
            return jsonify({
                "ok": True,
                "report": report,
                "message": f"分身 '{name}' 已销毁",
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  执行 & 热更新
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/<name>/execute", methods=["POST"])
    @require_token
    @log_request()
    def api_subagent_execute(name):
        """在指定分身中执行任务

        POST JSON:
            task (str): 任务描述
        """
        try:
            data = request.get_json() or {}
            task = data.get("task", "").strip()
            if not task:
                return jsonify({"ok": False, "error": "task 不能为空"}), 400

            result = Yunshu.execute_subagent(name, task)
            return jsonify({
                "ok": True,
                "name": name,
                "result": result,
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/subagent/<name>/reload", methods=["POST"])
    @require_token
    @log_request()
    def api_subagent_reload(name):
        """热更新分身配置

        POST JSON:
            model_id (str, optional): 新模型 ID
            memory_provider (str, optional): 新记忆提供商
            tool_sources (list[str], optional): 新工具源
            permissions (list[str], optional): 新权限
            context_window (int, optional): 新上下文窗口大小
            ttl_seconds (int, optional): 新存活时间
        """
        try:
            data = request.get_json() or {}

            # 获取当前配置作为基础
            current = Yunshu.get_subagent(name)
            if current is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404

            new_config = {
                "name": name,
                "model_id": data.get("model_id", current["model_id"]),
                "memory_provider": data.get("memory_provider", current["memory_provider"]),
                "tool_sources": data.get("tool_sources", current["tool_sources"]),
                "permissions": data.get("permissions", current["permissions"]),
                "context_window": data.get("context_window", current["context_window"]),
                "tags": data.get("tags", current.get("tags", [])),
                "ttl_seconds": data.get("ttl_seconds", current.get("ttl_seconds", 0)),
            }

            Yunshu.hot_reload_subagent(name, new_config)
            updated = Yunshu.get_subagent(name)
            return jsonify({
                "ok": True,
                "subagent": updated,
                "message": f"分身 '{name}' 热更新完成",
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
