"""
云枢 · 系统身份提示词配置路由
提供组件级开关、参数配置、预览、模板生成等完整 API
"""

import logging
import importlib
from flask import jsonify, request
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

# 兼容两种导入路径：从 app_server.py（根目录）或从 server_routes/__init__.py
try:
    from agent.server_auth import require_token
except ImportError:
    try:
        from ..server_auth import require_token
    except ImportError:
        require_token = lambda f: f  # fallback: 不启用鉴权


def register_routes(app, state):
    """注册系统提示词配置相关路由"""

    # 懒加载管理器（避免循环导入）
    def _get_mgr():
        from agent.system_prompt_config import get_manager
        return get_manager()

    # 同步模板到 system_prompt_manager 的文件
    def _sync_to_template_file(template: str):
        """将生成的模板写入 system_prompt.txt，使 DigitalLife 读取生效"""
        from agent.system_prompt_manager import save_template
        from agent.system_prompt_manager import SYSTEM_PROMPT_FILE
        import os
        try:
            os.makedirs(os.path.dirname(SYSTEM_PROMPT_FILE), exist_ok=True)
            with open(SYSTEM_PROMPT_FILE, "w", encoding="utf-8") as f:
                f.write(template)
            return True
        except Exception as e:
            logger.error("同步模板文件失败: %s", e)
            return False

    # ═══════════════════════════════════════════════════
    #  获取完整配置 + Token 统计
    # ═══════════════════════════════════════════════════

    @app.route("/api/system-prompt/config", methods=["GET"])
    @trace_route("SystemPrompt")
    @require_token
    def api_system_prompt_config_get():
        """获取提示词组件配置及 Token 开销统计"""
        try:
            mgr = _get_mgr()
            result = mgr.get_config_with_stats()
            # 附加当前 V2 特性可用性
            Yunshu = getattr(state, 'Yunshu', None)
            if Yunshu:
                v2 = Yunshu.get_v2_features()
                sections = result.get("sections", {})
                # 更新模块可用性状态
                if "lifetrace" in sections:
                    sections["lifetrace"]["extra_params"] = sections["lifetrace"].get("extra_params", {})
                    sections["lifetrace"]["extra_params"]["module_available"] = v2.get("available", {}).get("lifetrace", False)
                    # 如果模块不可用且当前启用，标记为禁用
                    if not v2.get("available", {}).get("lifetrace", False):
                        sections["lifetrace"]["enabled"] = False
                if "persona" in sections:
                    sections["persona"]["extra_params"] = sections["persona"].get("extra_params", {})
                    sections["persona"]["extra_params"]["module_available"] = v2.get("available", {}).get("persona", False)
                    if not v2.get("available", {}).get("persona", False):
                        sections["persona"]["enabled"] = False
                        sections["distillation"]["enabled"] = False
                if "distillation" in sections:
                    sections["distillation"]["extra_params"] = sections["distillation"].get("extra_params", {})
                    sections["distillation"]["extra_params"]["module_available"] = v2.get("available", {}).get("persona", False)
                result["v2_status"] = v2
            return jsonify(result)
        except Exception as e:
            logger.error("获取提示词配置失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  保存完整配置
    # ═══════════════════════════════════════════════════

    @app.route("/api/system-prompt/config", methods=["POST"])
    @trace_route("SystemPrompt")
    @require_token
    def api_system_prompt_config_save():
        """保存提示词组件配置"""
        try:
            data = request.get_json() or {}
            mgr = _get_mgr()
            success = mgr.save(data)
            if not success:
                return jsonify({"ok": False, "error": "保存失败"}), 500
            return jsonify({"ok": True})
        except Exception as e:
            logger.error("保存提示词配置失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  重置配置
    # ═══════════════════════════════════════════════════

    @app.route("/api/system-prompt/config/reset", methods=["POST"])
    @trace_route("SystemPrompt")
    @require_token
    def api_system_prompt_config_reset():
        """恢复默认配置"""
        try:
            mgr = _get_mgr()
            success = mgr.reset()
            if not success:
                return jsonify({"ok": False, "error": "重置失败"}), 500
            return jsonify({"ok": True})
        except Exception as e:
            logger.error("重置提示词配置失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  应用配置：生成模板 + 同步到运行文件
    # ═══════════════════════════════════════════════════

    @app.route("/api/system-prompt/config/apply", methods=["POST"])
    @trace_route("SystemPrompt")
    @require_token
    def api_system_prompt_config_apply():
        """根据当前配置生成模板并应用到运行环境"""
        try:
            data = request.get_json() or {}
            mgr = _get_mgr()

            # 如果有传入配置，先保存
            config_data = data.get("config")
            if config_data:
                # 兼容两种结构
                if "sections" not in config_data:
                    config_data = {"sections": config_data}
                mgr.save(config_data)

            # 构建模板（使用已保存的最新配置）
            template = mgr.build_template()

            # 同步到模板文件
            sync_ok = _sync_to_template_file(template)

            # 设置 flag 标记已应用（可选）
            config = mgr.load()
            config["_last_applied"] = __import__("time").time()
            mgr.save(config)

            return jsonify({
                "ok": True,
                "template": template,
                "template_length": len(template),
                "synced": sync_ok,
            })
        except Exception as e:
            logger.error("应用提示词配置失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  预览：根据配置生成模板但不保存
    # ═══════════════════════════════════════════════════

    @app.route("/api/system-prompt/config/preview", methods=["POST"])
    @trace_route("SystemPrompt")
    @require_token
    def api_system_prompt_config_preview():
        """根据提供的配置生成预览模板（不保存）"""
        try:
            data = request.get_json() or {}
            config = data.get("config", {})
            # 兼容两种结构：完整 config（含 sections 键）或直接是 sections 字典
            if "sections" not in config:
                config = {"sections": config}
            mgr = _get_mgr()
            template = mgr.build_template(config)
            return jsonify({
                "template": template,
                "template_length": len(template),
            })
        except Exception as e:
            logger.error("预览提示词失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  （继承原有的 system-prompt API）
    #  覆盖原有路由：在保存模板时同步到配置
    # ═══════════════════════════════════════════════════

    @app.route("/api/system-prompt/sync-status", methods=["GET"])
    @trace_route("SystemPrompt")
    @require_token
    def api_system_prompt_sync_status():
        """检查配置模板与运行模板是否同步"""
        try:
            from agent.system_prompt_manager import get_template
            mgr = _get_mgr()
            config = mgr.load()
            has_custom = config.get("custom_template") is not None and bool(config.get("custom_template", "").strip())
            configured_template = mgr.build_template(config)
            current_runtime = get_template()
            is_synced = configured_template.strip() == current_runtime.strip()
            return jsonify({
                "is_synced": is_synced,
                "config_template_length": len(configured_template),
                "runtime_template_length": len(current_runtime),
            })
        except Exception as e:
            logger.error("检查同步状态失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500
