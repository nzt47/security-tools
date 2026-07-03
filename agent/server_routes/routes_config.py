# MARKER: THIS IS THE CURRENT FILE VERSION - 2026-06-27
"""配置 & 网络配置 & LLM & MCP API 路由"""
import uuid
import datetime
import logging
import json
from typing import List
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.network_config import _DEFAULT_SEARCH_INSTANCE
from agent.server_routes.tracing_decorator import trace_route
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



# 已知的内置搜索引擎类型
BUILTIN_ENGINES = {'tavily', 'firecrawl', 'bing', 'google', 'brave',
                   'duckduckgo', 'baidu', 'sogou', 'so360'}


def validate_search_instance(instance: dict) -> List[str]:
    """验证搜索实例配置"""
    errors = []
    if not instance.get('name'):
        errors.append('名称不能为空')
    engine_type = instance.get('engine_type', '')
    if not engine_type:
        errors.append('引擎类型不能为空')
    if engine_type != 'custom' and engine_type not in BUILTIN_ENGINES:
        errors.append(f'未知的内置引擎类型: {engine_type}')
    if engine_type == 'custom':
        if not instance.get('api_endpoint'):
            errors.append('自定义引擎必须提供 API 端点 URL')
    if instance.get('timeout', 30) < 1 or instance.get('timeout', 30) > 300:
        errors.append('超时必须在 1-300 秒之间')
    return errors


def _get_current_session_id(session_mgr):
    """获取当前会话 ID，如无则创建新会话"""
    session_id = session_mgr.get_current_id()
    if not session_id:
        session = session_mgr.create_session("新会话")
        session_id = session["id"]
    return session_id


def register_routes(app, state):
    """注册所有配置 & 网络 & LLM & MCP 路由"""

    Yunshu = state.Yunshu
    session_mgr = state.session_mgr
    ncm = state.network_config_mgr
    web_search = state.search_engine
    chat_history = state.chat_history

    # ═══════════════════════════════════════════════════
    #  LLM 配置（原 /api/config）
    # ═══════════════════════════════════════════════════

    @app.route("/api/config", methods=["GET", "POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_config():
        if request.method == "GET":
            return jsonify(Yunshu.get_config())

        data = request.get_json() or {}
        provider = data.get("provider", "")

        if provider == "anthropic":
            try:
                import anthropic  # noqa
            except ImportError:
                return jsonify({"ok": False, "error": "缺少依赖库: anthropic。请执行: pip install anthropic"})
        elif provider in ("openai", "deepseek"):
            try:
                import openai  # noqa
            except ImportError:
                return jsonify({"ok": False, "error": "缺少依赖库: openai。请执行: pip install openai"})

        result = Yunshu.configure_llm(
            provider=data.get("provider", ""),
            api_key=data.get("api_key", ""),
            model=data.get("model", ""),
            base_url=data.get("base_url", data.get("api_endpoint", "")),
        )
        if result.get("ok"):
            session_mgr.clear_messages(_get_current_session_id(session_mgr))
            chat_history.clear()
        return jsonify(result)

    # ═══════════════════════════════════════════════════
    #  网络配置
    # ═══════════════════════════════════════════════════

    @app.route("/api/network-config", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_network_config_get():
        return jsonify(ncm.get_all())

    @app.route("/api/network-config", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_network_config_update():
        data = request.get_json() or {}
        try:
            result = ncm.update(data)
            ncm.apply_to_app(Yunshu)
            return jsonify({"ok": True, "config": result})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/network-config/reset", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_network_config_reset():
        result = ncm.reset()
        return jsonify({"ok": True, "config": result})

    @app.route("/api/network-config/export", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_network_config_export():
        try:
            json_str = ncm.export_config()
            return jsonify({"ok": True, "config_json": json_str})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/network-config/import", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_network_config_import():
        data = request.get_json() or {}
        json_str = data.get("config_json", "")
        if not json_str:
            return jsonify({"ok": False, "error": "缺少 config_json"}), 400
        try:
            result = ncm.import_config(json_str)
            ncm.apply_to_app(Yunshu)
            return jsonify({"ok": True, "config": result})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/apply-network-config", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_apply_network_config():
        try:
            logger.info(log_dict({'module_name': 'routes_config', 'action': 'log', 'msg': '[网络配置] 手动触发配置应用...'}))
            ncm.apply_to_app(Yunshu)

            config = ncm.get_raw_config()
            search_config = config.get('search', {})
            search_api_keys = config.get('search_api_keys', {})

            update_config = {
                'engine_priority': search_config.get('engine_priority', ['duckduckgo', 'tavily']),
                'engine_enabled': search_config.get('engine_enabled', {}),
                'timeout': search_config.get('timeout', 30),
                'default_engine': search_config.get('default_engine', 'duckduckgo'),
            }

            for key_name in ['tavily', 'bing', 'google', 'google_cx', 'brave']:
                if search_api_keys.get(key_name):
                    update_config[f'{key_name}_api_key' if key_name != 'google_cx' else 'google_cx'] = search_api_keys[key_name]

            if web_search:
                web_search.update_config(update_config)
                logger.info(log_dict({'module_name': 'routes_config', 'action': 'log', 'msg': '[网络配置] 已同时应用到全局搜索引擎实例'}))

            search_config_status = ncm.get_search_engines()
            return jsonify({
                "ok": True,
                "message": "配置已即时生效",
                "search_config": search_config_status,
            })
        except Exception as e:
            logger.error("[网络配置] 应用配置失败: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  LLM 实例管理
    # ═══════════════════════════════════════════════════

    @app.route("/api/llm/instances", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_llm_instances_get():
        try:
            instances = ncm.get_llm_instances()
            return jsonify({"ok": True, "instances": instances})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/llm/instances/<string:instance_id>", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_llm_instance_get(instance_id):
        try:
            instance = ncm.get_llm_instance(instance_id)
            if instance:
                return jsonify({"ok": True, "instance": instance})
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/llm/instances", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_llm_instance_add():
        try:
            data = request.get_json() or {}
            instance = data.get("instance", {})
            errors = ncm.validate_llm_instance(instance)
            if errors:
                return jsonify({"ok": False, "errors": errors}), 400
            result = ncm.add_llm_instance(instance)
            # 添加后自动应用到 app
            ncm.apply_to_app(Yunshu)
            return jsonify({"ok": True, "instance": result})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/llm/instances/<string:instance_id>", methods=["PUT"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_llm_instance_update(instance_id):
        try:
            data = request.get_json() or {}
            updates = data.get("updates", {})
            result = ncm.update_llm_instance(instance_id, updates)
            if result:
                # 更新后自动应用到 app
                ncm.apply_to_app(Yunshu)
                return jsonify({"ok": True, "instance": result})
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/llm/instances/<string:instance_id>", methods=["DELETE"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_llm_instance_delete(instance_id):
        try:
            success = ncm.delete_llm_instance(instance_id)
            if success:
                # 删除后自动应用到 app（可能回退到 legacy 或其他实例）
                ncm.apply_to_app(Yunshu)
                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/llm/instances/<string:instance_id>/default", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_llm_instance_set_default(instance_id):
        try:
            success = ncm.set_default_llm_instance(instance_id)
            if success:
                # 切换默认后自动应用到 app
                ncm.apply_to_app(Yunshu)
                return jsonify({"ok": True, "message": "已设置为默认实例"})
            return jsonify({"ok": False, "error": "操作失败"}), 500
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/llm/instances/<string:instance_id>/test", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_llm_instance_test(instance_id):
        """测试 LLM 实例连通性"""
        import sys as _sys
        _sys.stderr.reconfigure(encoding='utf-8')
        print('[DEBUG] api_llm_instance_test CALLED with', instance_id, flush=True)
        import time as _time
        _start = _time.time()
        try:
            config = ncm.get_raw_config()
            instances = config.get('llm_instances', [])
            inst = None
            for i in instances:
                if i.get('id') == instance_id:
                    inst = i
                    break
            if not inst:
                return jsonify({"ok": False, "error": "实例不存在"}), 404

            provider = inst.get('provider', 'openai')
            model = inst.get('model', 'gpt-4')
            api_key = inst.get('api_key', '')
            base_url = inst.get('api_endpoint', '')

            if not api_key:
                return jsonify({"ok": False, "error": "API Key 为空，请先配置"})

            # Debug: check the actual key
            _key_preview = api_key[:10] + '...' + api_key[-6:] if len(api_key) > 16 else api_key
            _key_len = len(api_key)
            _key_first_byte = ord(api_key[0]) if api_key else 0

            # Simple direct API test first
            import urllib.request, json as _json
            _body = _json.dumps({
                'model': model,
                'messages': [{'role': 'user', 'content': 'OK'}],
                'max_tokens': 10,
            }).encode()
            _req = urllib.request.Request(
                f'{base_url}/v1/chat/completions',
                data=_body,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
                method='POST')
            _resp = urllib.request.urlopen(_req, timeout=15)
            _result = _json.loads(_resp.read())
            _text = _result['choices'][0]['message']['content'].strip()
            _elapsed = round(_time.time() - _start, 2)
            return jsonify({
                "ok": True, "provider": provider, "model": model,
                "elapsed": _elapsed, "response": _text[:200],
                "debug": {"key_preview": _key_preview, "key_len": _key_len, "key_first_byte": _key_first_byte},
            })
        except urllib.error.HTTPError as _httpe:
            _elapsed = round(_time.time() - _start, 2)
            _err_body = _httpe.read().decode('utf-8')[:200] if hasattr(_httpe, 'read') else ''
            return jsonify({"ok": False, "error": f"HTTP {_httpe.code}: {_err_body}", "elapsed": _elapsed,
                           "debug": {"key_preview": _key_preview, "key_len": _key_len, "key_first_byte": _key_first_byte}}), 500
        except Exception as e:
            _elapsed = round(_time.time() - _start, 2)
            return jsonify({"ok": False, "error": str(e), "elapsed": _elapsed,
                           "debug": {"key_preview": _key_preview, "key_len": _key_len, "key_first_byte": _key_first_byte}}), 500

    # ═══════════════════════════════════════════════════
    #  MCP 服务管理
    # ═══════════════════════════════════════════════════

    @app.route("/api/mcp/services", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_mcp_services_get():
        try:
            services = ncm.get_mcp_services()
            return jsonify({"ok": True, "services": services})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/mcp/services/<string:service_id>", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_mcp_service_get(service_id):
        try:
            service = ncm.get_mcp_service(service_id)
            if service:
                return jsonify({"ok": True, "service": service})
            return jsonify({"ok": False, "error": "服务不存在"}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/mcp/services", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_mcp_service_add():
        try:
            data = request.get_json() or {}
            service = data.get("service", {})
            errors = ncm.validate_mcp_service(service)
            if errors:
                return jsonify({"ok": False, "errors": errors}), 400
            result = ncm.add_mcp_service(service)
            return jsonify({"ok": True, "service": result})
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/mcp/services/<string:service_id>", methods=["PUT"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_mcp_service_update(service_id):
        try:
            data = request.get_json() or {}
            updates = data.get("updates", {})
            result = ncm.update_mcp_service(service_id, updates)
            if result:
                return jsonify({"ok": True, "service": result})
            return jsonify({"ok": False, "error": "服务不存在"}), 404
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/mcp/services/<string:service_id>", methods=["DELETE"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_mcp_service_delete(service_id):
        try:
            success = ncm.delete_mcp_service(service_id)
            if success:
                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": "服务不存在"}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/mcp/enable", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_mcp_enable():
        try:
            data = request.get_json() or {}
            enabled = data.get("enabled", False)
            config = ncm.get_raw_config()
            config['mcp']['enabled'] = enabled
            ncm.update(config)
            return jsonify({"ok": True, "enabled": enabled})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  搜索引擎实例管理
    # ═══════════════════════════════════════════════════

    @app.route("/api/search/instances", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_search_instances_get():
        try:
            config = ncm.get_all()
            instances = config.get('search_instances', [])
            return jsonify({"ok": True, "instances": instances})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search/instances", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_search_instance_add():
        try:
            data = request.get_json() or {}
            instance = data.get("instance", {})

            # 验证
            errors = validate_search_instance(instance)
            if errors:
                return jsonify({"ok": False, "errors": errors}), 400

            config = ncm.get_raw_config()
            new_inst = dict(_DEFAULT_SEARCH_INSTANCE)
            new_inst.update(instance)
            new_inst['id'] = str(uuid.uuid4())
            new_inst['created_at'] = datetime.datetime.now().isoformat()
            new_inst['updated_at'] = new_inst['created_at']

            # 加密保存 API Key
            api_key = new_inst.get('api_key', '')
            if api_key and not api_key.startswith('***'):
                ncm._save_secure(f'search_{new_inst["id"]}_api_key', api_key)

            config['search_instances'].append(new_inst)
            ncm._save(config)
            ncm._add_change_log('add', 'search_instance', {'id': new_inst['id'], 'name': new_inst['name']})

            # 即时注册到搜索引擎
            if web_search:
                ncm._register_search_instance(new_inst, web_search)
                ncm.apply_search_instances(web_search)

            return jsonify({"ok": True, "instance": new_inst})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search/instances/<string:instance_id>", methods=["PUT"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_search_instance_update(instance_id):
        try:
            data = request.get_json() or {}
            updates = data.get("updates", {})
            config = ncm.get_raw_config()
            instances = config.get('search_instances', [])

            for inst in instances:
                if inst.get('id') == instance_id:
                    api_key = updates.get('api_key', '')
                    if api_key and api_key != '***' and not api_key.startswith('***'):
                        ncm._save_secure(f'search_{instance_id}_api_key', api_key)
                    elif api_key and api_key.startswith('***'):
                        updates.pop('api_key', None)

                    inst.update(updates)
                    inst['updated_at'] = datetime.datetime.now().isoformat()
                    ncm._save(config)
                    ncm._add_change_log('update', 'search_instance', {'id': instance_id, 'name': inst.get('name')})

                    # 重新注册
                    if web_search:
                        ncm.apply_search_instances(web_search)

                    return jsonify({"ok": True, "instance": inst})

            return jsonify({"ok": False, "error": "实例不存在"}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search/instances/<string:instance_id>", methods=["DELETE"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_search_instance_delete(instance_id):
        try:
            config = ncm.get_raw_config()
            before = len(config.get('search_instances', []))
            config['search_instances'] = [i for i in config.get('search_instances', []) if i.get('id') != instance_id]

            if len(config['search_instances']) < before:
                ncm._save(config)
                ncm._save_secure(f'search_{instance_id}_api_key', '')
                ncm._add_change_log('delete', 'search_instance', {'id': instance_id})

                # 从搜索引擎移除
                if web_search:
                    web_search.remove_engine(instance_id)
                    # 同步更新 web_search 工具的 engine enum
                    from agent.tools import sync_web_search_engines
                    sync_web_search_engines([], search_engine=web_search)

                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search/instances/<string:instance_id>/default", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_search_instance_set_default(instance_id):
        try:
            config = ncm.get_raw_config()
            instances = config.get('search_instances', [])

            inst = next((i for i in instances if i.get('id') == instance_id), None)
            if not inst:
                return jsonify({"ok": False, "error": "实例不存在"}), 404

            # 先调用 set_default_engine（可能抛出 ValueError）
            if web_search:
                if inst.get('engine_type') == 'custom':
                    web_search.set_default_engine(instance_id)
                else:
                    # 内置引擎用 engine_type 作为标识
                    web_search.set_default_engine(inst['engine_type'])

            # 保存配置：清除其他实例的 is_default
            for i in instances:
                i['is_default'] = (i.get('id') == instance_id)

            ncm._save(config)
            ncm._add_change_log('update', 'search_instance', {'id': instance_id, 'action': 'set_default'})

            return jsonify({"ok": True, "message": "已设为默认搜索引擎"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/search/instances/<string:instance_id>/test", methods=["POST"])
    @trace_route("Config")
    @require_token
    @log_request()
    def api_search_instance_test(instance_id):
        """测试搜索实例连通性"""
        try:
            config = ncm.get_raw_config()
            instances = config.get('search_instances', [])
            inst = next((i for i in instances if i.get('id') == instance_id), None)
            if not inst:
                return jsonify({"ok": False, "error": "实例不存在"}), 404

            if not web_search:
                return jsonify({"ok": False, "error": "搜索引擎未初始化"}), 503

            if inst.get('engine_type') == 'custom':
                # 直接调用通用 handler
                result = web_search._search_custom(inst, "test", num_results=2)
            else:
                # 内置引擎
                result = web_search.search(query="test", engine=inst.get('engine_type', ''), num_results=2)

            return jsonify({
                "ok": result.get("ok", False),
                "results": result.get("results", [])[:2],
                "total": result.get("total_estimate", 0),
                "engine": result.get("engine", ""),
                "error": result.get("error", ""),
            })
        except Exception as e:
            logger.error("[搜索实例] 测试失败: %s", e, exc_info=True)
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  配置变更日志
    # ═══════════════════════════════════════════════════

    @app.route("/api/config/logs", methods=["GET"])
    @trace_route("Config")
    @require_token
    @log_request(show_response=False)
    def api_config_logs():
        try:
            limit = request.args.get("limit", 20, type=int)
            logs = ncm.get_change_log(limit)
            return jsonify({"ok": True, "logs": logs})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
