"""技能 & 工具配置 API 路由"""
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.tools import list_tools
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def _get_tool_state(tool_name):
    """获取单个工具启用状态"""
    try:
        from agent.server_ui import get_tool_state as _gts
        return _gts(tool_name)
    except Exception:
        return True


def _set_tool_state(tool_name, enabled):
    """设置单个工具启用状态"""
    try:
        from agent.server_ui import set_tool_state as _sts
        _sts(tool_name, enabled)
    except Exception:
        pass


def register_routes(app, state):
    """注册所有技能 & 工具配置路由"""

    skills_mgr = state.skills_mgr

    # ═══════════════════════════════════════════════════
    #  技能管理
    # ═══════════════════════════════════════════════════

    @app.route("/api/skills", methods=["GET"])
    @trace_route("Skills")
    @log_request(show_response=False)
    def api_skills_get():
        """获取技能列表（分类：已安装 + 可安装的内置技能）"""
        installed = skills_mgr.get_all()
        installed_ids = {s["id"] for s in installed}

        # 从扩展存储获取额外已安装的扩展
        try:
            from agent.extensions.store import ExtensionStore
            from agent.extensions.base import ExtensionType
            ext_store = ExtensionStore()
            for ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
                for ext in ext_store.list_all(ext_type):
                    ext_id = ext.get("ext_id", "")
                    if ext_id and ext_id not in installed_ids:
                        installed.append({
                            "id": ext_id,
                            "name": ext.get("name", ext_id),
                            "enabled": ext.get("status") in ("enabled", "installed"),
                            "description": ext.get("description", ""),
                            "params": ext.get("config", {}),
                            "source": "extension_store",
                        })
                        installed_ids.add(ext_id)
        except Exception:
            pass

        # 从内置注册表获取所有可用的技能
        try:
            from agent.extensions.base import BUILTIN_EXTENSIONS
            builtin_list = BUILTIN_EXTENSIONS.get("skill", [])
        except ImportError:
            builtin_list = []

        available = []
        for s in builtin_list:
            available.append({
                "id": s["id"],
                "name": s["name"],
                "description": s.get("description", ""),
                "installed": s["id"] in installed_ids,
                "builtin": s.get("builtin", False),
            })

        return jsonify({
            "installed": installed,
            "available": available,
        })

    @app.route("/api/skills/toggle", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_skills_toggle():
        """切换技能启用状态（唯一数据源: data/skills.json，通过 SkillsManager 操作）"""
        data = request.get_json() or {}
        skill_id = data.get("id", "")
        result = skills_mgr.toggle(skill_id)
        return jsonify(result)

    @app.route("/api/skills/params", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_skills_params():
        data = request.get_json() or {}
        return jsonify(skills_mgr.update_params(data.get("id", ""), data.get("params", {})))

    @app.route("/api/skills/add", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_skills_add():
        return jsonify(skills_mgr.add(request.get_json() or {}))

    @app.route("/api/skills/delete", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_skills_delete():
        data = request.get_json() or {}
        skill_id = data.get("id", "")

        # 内置技能不可删除
        try:
            from agent.extensions.base import BUILTIN_EXTENSIONS
            for s in BUILTIN_EXTENSIONS.get("skill", []):
                s_id = s.get("id", "")
                if s_id == skill_id and s.get("builtin", False):
                    return jsonify({"ok": False, "error": "内置技能不可删除"})
        except Exception:
            pass

        result = skills_mgr.delete(skill_id)
        deleted = result.get("ok", False)

        try:
            from agent.extensions.store import ExtensionStore
            from agent.extensions.base import ExtensionType
            ext_store = ExtensionStore()
            for ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
                if ext_store.remove(ext_type, skill_id):
                    deleted = True
                    if ext_type == ExtensionType.CLAUDE_SKILL:
                        import shutil
                        claude_dir = os.path.join(os.path.expanduser("~"), ".claude", "skills", skill_id)
                        if os.path.exists(claude_dir):
                            shutil.rmtree(claude_dir, ignore_errors=True)
        except Exception:
            pass

        if deleted:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": f"未找到技能: {skill_id}"})

    # ═══════════════════════════════════════════════════
    #  工具配置
    # ═══════════════════════════════════════════════════

    @app.route("/api/tools/config", methods=["GET"])
    @trace_route("Skills")
    @log_request(show_response=False)
    def api_tools_config():
        """获取工具列表及使用统计"""
        tools = list_tools()
        try:
            perm_logs = state.Yunshu._permission.get_permission_log()
        except Exception:
            perm_logs = []
        result = []
        for t in tools:
            tool_name = t["name"]
            call_count = sum(1 for log in perm_logs if log.get("tool") == tool_name)
            result.append({
                "name": tool_name,
                "description": t.get("description", ""),
                "enabled": _get_tool_state(tool_name),
                "call_count": call_count,
                "last_used": None,
            })
        return jsonify(result)

    @app.route("/api/tools/toggle", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_tools_toggle():
        data = request.get_json() or {}
        tool_name = data.get("name", "")
        enabled = data.get("enabled", True)
        _set_tool_state(tool_name, enabled)
        return jsonify({"ok": True, "name": tool_name, "enabled": enabled})


    # ═══════════════════════════════════════════════════
    #  工具分类 & 路由关键词 API
    # ═══════════════════════════════════════════════════

    @app.route("/api/tools/categories", methods=["GET"])
    @trace_route("Skills")
    @log_request(show_response=False)
    def api_tools_categories():
        from ..tool_router import get_categorized_tools, get_keywords
        return jsonify({
            "categories": get_categorized_tools(),
            "keywords": get_keywords(),
        })

    @app.route("/api/tools/keywords", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_tools_keywords_add():
        data = request.get_json() or {}
        category = data.get("category", "")
        keyword = data.get("keyword", "").strip()
        if not category or not keyword:
            return jsonify({"ok": False, "error": "缺少 category 或 keyword"}), 400
        from ..tool_router import add_keyword
        ok = add_keyword(category, keyword)
        return jsonify({"ok": ok})

    @app.route("/api/tools/keywords", methods=["DELETE"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_tools_keywords_remove():
        data = request.get_json() or {}
        category = data.get("category", "")
        keyword = data.get("keyword", "").strip()
        if not category or not keyword:
            return jsonify({"ok": False, "error": "缺少 category 或 keyword"}), 400
        from ..tool_router import remove_keyword
        ok = remove_keyword(category, keyword)
        return jsonify({"ok": ok})

    @app.route("/api/tools/keywords/update", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_tools_keywords_update():
        data = request.get_json() or {}
        category = data.get("category", "")
        old_kw = data.get("old_keyword", "").strip()
        new_kw = data.get("new_keyword", "").strip()
        if not category or not old_kw or not new_kw:
            return jsonify({"ok": False, "error": "缺少必要参数"}), 400
        from ..tool_router import update_keyword
        ok = update_keyword(category, old_kw, new_kw)
        return jsonify({"ok": ok})

    @app.route("/api/tools/keywords/reset", methods=["POST"])
    @trace_route("Skills")
    @require_token
    @log_request()
    def api_tools_keywords_reset():
        from ..tool_router import reset_keywords
        ok = reset_keywords()
        return jsonify({"ok": ok})
    @app.route("/api/tools/status-batch", methods=["GET"])
    @trace_route("Skills")
    @log_request(show_response=False)
    def api_tools_status_batch():
        """获取所有工具和技能的启用状态摘要"""
        tools = list_tools()
        result = []
        for t in tools:
            result.append({
                "type": "tool",
                "name": t["name"],
                "description": t.get("description", ""),
                "enabled": _get_tool_state(t["name"]),
            })
        skills = skills_mgr.get_all()
        for s in skills:
            result.append({
                "type": "skill",
                "id": s["id"],
                "name": s.get("name", s["id"]),
                "description": s.get("description", ""),
                "enabled": s.get("enabled", True),
            })
        return jsonify(result)
