# plugins/admin.py
"""配置、认证、审计与模型实例管理插件（任务 T1.6）。

从 app_server.py 迁移的管理/配置域路由（路由路径与行为 100% 不变）：
    /api/config、/api/config/logs、/api/auth/token-check、/api/audit/logs、
    /api/network-config*、/api/apply-network-config、/api/system-prompt*、
    /api/llm/instances*、/api/search/instances*、/api/search-performance/*

共享依赖约定（见 docs/yunshu-pluginization/PLAN-1-backend-pluginization.md §4）：
    - 模块顶层不得 import app_server（循环导入红线）；
    - app_server 中的全局单例（_Yunshu/_network_config_mgr/_web_search 等）
      在视图函数内部延迟 import，调用时才执行，无循环导入问题；
    - require_token / log_request 取自共享模块 agent.server_auth（与
      agent/server_routes/* 一致），实现与 app_server 内嵌版本等价。
"""
from __future__ import annotations

import logging
import uuid
import datetime

from flask import Blueprint, request, jsonify

from .plugin_api import Plugin, register_plugin
from agent.server_auth import require_token, log_request
from agent.network_config import _DEFAULT_SEARCH_INSTANCE
from agent.server_routes.routes_config import validate_search_instance as _validate_search_instance

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)


def _trace_id():
    """生成 trace_id（结构化日志用）"""
    import uuid as _uuid
    return _uuid.uuid4().hex[:16]


def _log_struct(action: str, message: str, duration_ms: int = 0, **extra):
    """输出结构化 JSON 日志（与 app_server._log_struct 相同格式与 module_name）"""
    import json as _json
    payload = {
        "trace_id": _trace_id(),
        "module_name": "app_server",
        "action": action,
        "duration_ms": duration_ms,
        "message": message,
    }
    payload.update(extra)
    logger.info(_json.dumps(payload, ensure_ascii=False))


# ════════════════════════════════════════════════════════════
#  认证 & 全局配置 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/auth/token-check")
@log_request(show_response=False)
def api_auth_token_check():
    """检查令牌是否有效（前端用）"""
    from app_server import _API_TOKEN_ENABLED
    return jsonify({"enabled": _API_TOKEN_ENABLED, "valid": True})


@bp.route("/api/config", methods=["GET", "POST"])
@require_token
@log_request()
def api_config():
    """获取或设置 LLM 配置"""
    from app_server import _CHAT_HISTORY, _session_mgr, _get_current_session_id, _Yunshu
    if request.method == "GET":
        return jsonify(_Yunshu.get_config())

    data = request.get_json() or {}
    provider = data.get("provider", "")

    # 检查依赖库
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

    result = _Yunshu.configure_llm(
        provider=data.get("provider", ""),
        api_key=data.get("api_key", ""),
        model=data.get("model", ""),
    )
    if result.get("ok"):
        _session_mgr.clear_messages(_get_current_session_id())
        _CHAT_HISTORY.clear()
    return jsonify(result)


# ════════════════════════════════════════════════════════════
#  系统身份提示词 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/system-prompt", methods=["GET"])
@log_request(show_response=False)
def api_system_prompt_get():
    """获取系统提示词模板及预览信息"""
    from agent.system_prompt_manager import get_template, has_custom_template, get_placeholder_descriptions
    template = get_template()
    placeholders = get_placeholder_descriptions()

    # 生成预览：尝试代入示例值
    try:
        preview = template.format(
            current_date=f"{datetime.datetime.now().year}年{datetime.datetime.now().month}月{datetime.datetime.now().day}日",
            body_status="🟢 CPU: 32°C | 内存: 45% | 磁盘: 128G/512G | 电池: 充电中",
            mode_name="对话",
            mode_description="日常交流模式",
            memory_context="（暂无记忆内容）",
            tool_status="web_search: 启用 | file_read: 启用 | ...",
            skill_instructions="",
        )
    except KeyError:
        preview = "（模板包含未知占位符，请检查语法）"
    except Exception as e:
        preview = f"（渲染错误: {e}）"

    return jsonify({
        "template": template,
        "is_custom": has_custom_template(),
        "is_default": not has_custom_template(),
        "placeholders": placeholders,
        "preview": preview,
    })


@bp.route("/api/system-prompt", methods=["POST"])
@require_token
@log_request()
def api_system_prompt_save():
    """保存自定义系统提示词模板"""
    from agent.system_prompt_manager import get_placeholder_descriptions, save_template
    data = request.get_json() or {}
    content = data.get("content", "")

    if not content or not content.strip():
        return jsonify({"ok": False, "error": "内容不能为空"}), 400

    # 验证占位符正确性
    try:
        content.format(
            current_date="测试",
            body_status="测试",
            mode_name="测试",
            mode_description="测试",
            memory_context="测试",
            tool_status="测试",
            skill_instructions="",
        )
    except KeyError as e:
        return jsonify({
            "ok": False,
            "error": f"模板中包含未知占位符: {e}。可用占位符: {', '.join(get_placeholder_descriptions().keys())}"
        }), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"模板语法错误: {e}"}), 400

    success = save_template(content)
    if success:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "保存失败"}), 500


@bp.route("/api/system-prompt/reset", methods=["POST"])
@require_token
@log_request()
def api_system_prompt_reset():
    """重置系统提示词为默认"""
    from agent.system_prompt_manager import reset_template
    success = reset_template()
    if success:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "重置失败"}), 500


# ════════════════════════════════════════════════════════════
#  网络配置 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/network-config", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_network_config_get():
    """获取网络配置"""
    from app_server import _network_config_mgr
    return jsonify(_network_config_mgr.get_all())


@bp.route("/api/network-config", methods=["POST"])
@require_token
@log_request()
def api_network_config_update():
    """更新网络配置"""
    from app_server import _network_config_mgr, _Yunshu
    import time as _time
    t0 = _time.time()
    data = request.get_json() or {}
    try:
        # 记录保存前的 priority（便于排查排序不生效问题）
        before = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        result = _network_config_mgr.update(data)
        # 即时生效：将配置应用到应用实例
        _network_config_mgr.apply_to_app(_Yunshu)
        after = result.get('search', {}).get('engine_priority', [])
        _log_struct(
            'api_network_config_update.done',
            '网络配置已更新',
            duration_ms=int((_time.time() - t0) * 1000),
            priority_before=before,
            priority_after=after,
            priority_changed=before != after,
            default_engine=result.get('search', {}).get('default_engine', ''),
        )
        return jsonify({"ok": True, "config": result})
    except Exception as e:
        _log_struct(
            'api_network_config_update.failed',
            f'更新失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/network-config/reset", methods=["POST"])
@require_token
@log_request()
def api_network_config_reset():
    """重置网络配置为默认值"""
    from app_server import _network_config_mgr
    result = _network_config_mgr.reset()
    return jsonify({"ok": True, "config": result})


@bp.route("/api/network-config/export", methods=["GET"])
@require_token
@log_request()
def api_network_config_export():
    """导出网络配置（脱敏）"""
    from app_server import _network_config_mgr
    try:
        json_str = _network_config_mgr.export_config()
        return jsonify({"ok": True, "config_json": json_str})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/network-config/import", methods=["POST"])
@require_token
@log_request()
def api_network_config_import():
    """导入网络配置"""
    from app_server import _network_config_mgr, _Yunshu
    data = request.get_json() or {}
    json_str = data.get("config_json", "")
    if not json_str:
        return jsonify({"ok": False, "error": "缺少 config_json"}), 400

    try:
        result = _network_config_mgr.import_config(json_str)
        # 即时生效
        _network_config_mgr.apply_to_app(_Yunshu)
        return jsonify({"ok": True, "config": result})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/apply-network-config", methods=["POST"])
@require_token
@log_request()
def api_apply_network_config():
    """应用网络配置到应用实例（即时生效）"""
    from app_server import _network_config_mgr, _web_search, _Yunshu
    try:
        logger.info("[网络配置] 手动触发配置应用...")
        _network_config_mgr.apply_to_app(_Yunshu)
        
        # 同时应用到全局搜索引擎实例 _web_search
        config = _network_config_mgr.get_raw_config()
        search_config = config.get('search', {})
        search_api_keys = config.get('search_api_keys', {})
        
        update_config = {
            'engine_priority': search_config.get('engine_priority', ['duckduckgo', 'tavily']),
            'engine_enabled': search_config.get('engine_enabled', {}),
            'timeout': search_config.get('timeout', 30),
            'default_engine': search_config.get('default_engine', 'duckduckgo'),
        }
        
        # 添加 API Keys
        for key_name in ['tavily', 'bing', 'google', 'google_cx', 'brave']:
            if search_api_keys.get(key_name):
                update_config[f'{key_name}_api_key' if key_name != 'google_cx' else 'google_cx'] = search_api_keys[key_name]
        
        _web_search.update_config(update_config)

        # 注册搜索实例到全局引擎
        _network_config_mgr.apply_search_instances(_web_search)
        # 同步 DigitalLife 的搜索引擎实例
        _Yunshu._web_search = _web_search
        logger.info("[网络配置] 已同时应用到全局搜索引擎实例")
        
        # 返回搜索引擎配置状态供前端验证
        search_config_status = _network_config_mgr.get_search_engines()
        return jsonify({
            "ok": True,
            "message": "配置已即时生效",
            "search_config": search_config_status,
        })
    except Exception as e:
        logger.error("[网络配置] 应用配置失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  LLM 实例管理 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/llm/instances", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_llm_instances_get():
    """获取所有 LLM 实例"""
    from app_server import _network_config_mgr
    try:
        instances = _network_config_mgr.get_llm_instances()
        return jsonify({"ok": True, "instances": instances})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/llm/instances/<string:instance_id>", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_llm_instance_get(instance_id):
    """获取单个 LLM 实例"""
    from app_server import _network_config_mgr
    try:
        instance = _network_config_mgr.get_llm_instance(instance_id)
        if instance:
            return jsonify({"ok": True, "instance": instance})
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/llm/instances", methods=["POST"])
@require_token
@log_request()
def api_llm_instance_add():
    """添加 LLM 实例"""
    from app_server import _network_config_mgr
    try:
        data = request.get_json() or {}
        instance = data.get("instance", {})
        
        # 验证配置
        errors = _network_config_mgr.validate_llm_instance(instance)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        
        result = _network_config_mgr.add_llm_instance(instance)
        return jsonify({"ok": True, "instance": result})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/llm/instances/<string:instance_id>", methods=["PUT"])
@require_token
@log_request()
def api_llm_instance_update(instance_id):
    """更新 LLM 实例"""
    from app_server import _network_config_mgr
    try:
        data = request.get_json() or {}
        updates = data.get("updates", {})
        
        result = _network_config_mgr.update_llm_instance(instance_id, updates)
        if result:
            return jsonify({"ok": True, "instance": result})
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/llm/instances/<string:instance_id>", methods=["DELETE"])
@require_token
@log_request()
def api_llm_instance_delete(instance_id):
    """删除 LLM 实例"""
    from app_server import _network_config_mgr
    try:
        success = _network_config_mgr.delete_llm_instance(instance_id)
        if success:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/llm/instances/<string:instance_id>/default", methods=["POST"])
@require_token
@log_request()
def api_llm_instance_set_default(instance_id):
    """设置默认 LLM 实例"""
    from app_server import _network_config_mgr
    try:
        success = _network_config_mgr.set_default_llm_instance(instance_id)
        if success:
            return jsonify({"ok": True, "message": "已设置为默认实例"})
        return jsonify({"ok": False, "error": "操作失败"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/llm/instances/<string:instance_id>/test", methods=["POST"])
@require_token
@log_request()
def api_llm_instance_test(instance_id):
    """测试 LLM 实例连接"""
    from app_server import _network_config_mgr
    try:
        config = _network_config_mgr.get_raw_config()
        inst = next((i for i in config.get('llm_instances', []) if i.get('id') == instance_id), None)
        if not inst:
            return jsonify({"ok": False, "error": "实例不存在"}), 404

        provider = inst.get('provider', 'openai')
        api_key = inst.get('api_key', '')
        model = inst.get('model', 'gpt-4')
        base_url = inst.get('api_endpoint', '') or None
        timeout = inst.get('timeout', 30)

        if not api_key:
            return jsonify({"ok": False, "error": "API Key 未配置"})

        try:
            from memory.llm_service import LLMService
            llm = LLMService(
                provider=provider, api_key=api_key,
                model=model, base_url=base_url,
                timeout=timeout,
            )
            import time
            t0 = time.time()
            resp = llm.chat(
                messages=[{"role": "user", "content": "回复'OK'"}],
                max_tokens=10, temperature=0.1,
            )
            elapsed = round(time.time() - t0, 2)
            return jsonify({
                "ok": True,
                "elapsed": elapsed,
                "model": model,
                "provider": provider,
                "response": (resp or '')[:100],
            })
        except Exception as e:
            return jsonify({"ok": False, "error": f"连接失败: {e}"})
    except Exception as e:
        logger.error("[LLM 实例] 测试失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  搜索引擎实例管理 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/search/instances", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_search_instances_get():
    from app_server import _network_config_mgr
    try:
        config = _network_config_mgr.get_all()
        return jsonify({"ok": True, "instances": config.get('search_instances', [])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search/instances", methods=["POST"])
@require_token
@log_request()
def api_search_instance_add():
    from app_server import _network_config_mgr, _web_search
    import time as _time
    t0 = _time.time()
    try:
        data = request.get_json() or {}
        instance = data.get("instance", {})
        errors = _validate_search_instance(instance)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400

        priority_before = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        config = _network_config_mgr.get_raw_config()
        new_inst = dict(_DEFAULT_SEARCH_INSTANCE)
        new_inst.update(instance)
        new_inst['id'] = str(uuid.uuid4())
        new_inst['created_at'] = datetime.datetime.now().isoformat()
        new_inst['updated_at'] = new_inst['created_at']

        api_key = new_inst.get('api_key', '')
        if api_key and not api_key.startswith('***'):
            _network_config_mgr._save_secure(f'search_{new_inst["id"]}_api_key', api_key)

        config.setdefault('search_instances', []).append(new_inst)
        _network_config_mgr._save(config)
        _network_config_mgr._add_change_log('add', 'search_instance', {'id': new_inst['id'], 'name': new_inst['name']})
        if _web_search:
            _network_config_mgr._register_search_instance(new_inst, _web_search)
            _network_config_mgr.apply_search_instances(_web_search)

        priority_after = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        _log_struct(
            'api_search_instance_add.done',
            '搜索实例已新增',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=new_inst['id'],
            instance_name=new_inst.get('name', ''),
            engine_type=new_inst.get('engine_type', ''),
            priority_before=priority_before,
            priority_after=priority_after,
        )

        # 返回前脱敏 api_key（避免明文返回前端）
        resp_inst = dict(new_inst)
        if resp_inst.get('api_key'):
            resp_inst['api_key'] = '***' + resp_inst['api_key'][-4:] if len(resp_inst['api_key']) > 4 else '***'
        return jsonify({"ok": True, "instance": resp_inst})
    except Exception as e:
        _log_struct(
            'api_search_instance_add.failed',
            f'新增搜索实例失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search/instances/<string:instance_id>", methods=["PUT"])
@require_token
@log_request()
def api_search_instance_update(instance_id):
    from app_server import _network_config_mgr, _web_search
    import time as _time
    t0 = _time.time()
    try:
        data = request.get_json() or {}
        updates = data.get("updates", {})
        priority_before = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        config = _network_config_mgr.get_raw_config()
        for inst in config.get('search_instances', []):
            if inst.get('id') == instance_id:
                ak = updates.get('api_key', '')
                if ak and ak != '***' and not ak.startswith('***'):
                    _network_config_mgr._save_secure(f'search_{instance_id}_api_key', ak)
                # 移除 api_key 字段，避免脱敏值/明文写入缓存（_save 会再次剥离，这里防御性处理）
                updates_clean = {k: v for k, v in updates.items() if k != 'api_key'}
                inst.update(updates_clean)
                inst['updated_at'] = datetime.datetime.now().isoformat()
                _network_config_mgr._save(config)
                _network_config_mgr._add_change_log('update', 'search_instance', {'id': instance_id, 'name': inst.get('name')})
                if _web_search:
                    _network_config_mgr.apply_search_instances(_web_search)
                priority_after = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
                _log_struct(
                    'api_search_instance_update.done',
                    '搜索实例已更新',
                    duration_ms=int((_time.time() - t0) * 1000),
                    instance_id=instance_id,
                    instance_name=inst.get('name', ''),
                    updated_fields=list(updates_clean.keys()),
                    priority_before=priority_before,
                    priority_after=priority_after,
                    priority_changed=priority_before != priority_after,
                )
                # 返回前脱敏 api_key
                resp_inst = dict(inst)
                if resp_inst.get('api_key'):
                    resp_inst['api_key'] = '***' + resp_inst['api_key'][-4:] if len(resp_inst['api_key']) > 4 else '***'
                return jsonify({"ok": True, "instance": resp_inst})
        _log_struct(
            'api_search_instance_update.not_found',
            f'搜索实例不存在: {instance_id}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
        )
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        _log_struct(
            'api_search_instance_update.failed',
            f'更新搜索实例失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search/instances/<string:instance_id>", methods=["DELETE"])
@require_token
@log_request()
def api_search_instance_delete(instance_id):
    from app_server import _network_config_mgr, _web_search
    import time as _time
    t0 = _time.time()
    try:
        config = _network_config_mgr.get_raw_config()
        before = len(config.get('search_instances', []))
        priority_before = config.get('search', {}).get('engine_priority', [])
        config['search_instances'] = [i for i in config.get('search_instances', []) if i.get('id') != instance_id]
        if len(config['search_instances']) < before:
            # 修复：从 engine_priority 中移除已删除实例的 id（避免残留 UUID 导致前端空行/报错）
            config.setdefault('search', {})['engine_priority'] = [
                p for p in priority_before if p != instance_id
            ]
            # 修复：如果删除的是默认引擎，清理 default_engine 字段（避免指向不存在的实例）
            default_before = config.get('search', {}).get('default_engine', '')
            default_changed = False
            if default_before == instance_id:
                config['search']['default_engine'] = ''
                default_changed = True
            _network_config_mgr._save(config)
            _network_config_mgr._save_secure(f'search_{instance_id}_api_key', '')
            _network_config_mgr._add_change_log('delete', 'search_instance', {'id': instance_id})
            if _web_search:
                _web_search.remove_engine(instance_id)
                # 同步更新 web_search 工具的 engine enum + 重建 priority
                from agent.tools import sync_web_search_engines
                sync_web_search_engines([], search_engine=_web_search)
                _network_config_mgr.apply_search_instances(_web_search)
            priority_after = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
            _log_struct(
                'api_search_instance_delete.done',
                '搜索实例已删除',
                duration_ms=int((_time.time() - t0) * 1000),
                instance_id=instance_id,
                priority_before=priority_before,
                priority_after=priority_after,
                priority_changed=priority_before != priority_after,
                default_engine_cleared=default_changed,
            )
            return jsonify({"ok": True})
        _log_struct(
            'api_search_instance_delete.not_found',
            f'搜索实例不存在: {instance_id}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
        )
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        _log_struct(
            'api_search_instance_delete.failed',
            f'删除搜索实例失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search/instances/<string:instance_id>/default", methods=["POST"])
@require_token
@log_request()
def api_search_instance_set_default(instance_id):
    from app_server import _network_config_mgr, _web_search
    import time as _time
    t0 = _time.time()
    try:
        config = _network_config_mgr.get_raw_config()
        default_before = config.get('search', {}).get('default_engine', '')
        inst = next((i for i in config.get('search_instances', []) if i.get('id') == instance_id), None)
        if not inst:
            _log_struct(
                'api_search_instance_set_default.not_found',
                f'搜索实例不存在: {instance_id}',
                duration_ms=int((_time.time() - t0) * 1000),
                instance_id=instance_id,
            )
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        if _web_search:
            _web_search.set_default_engine(instance_id if inst.get('engine_type') == 'custom' else inst['engine_type'])
        for i in config.get('search_instances', []):
            i['is_default'] = (i.get('id') == instance_id)
        # 同步 default_engine 字段（确保 search.default_engine 与 is_default 一致）
        config.setdefault('search', {})['default_engine'] = instance_id
        _network_config_mgr._save(config)
        _network_config_mgr._add_change_log('update', 'search_instance', {'id': instance_id, 'action': 'set_default'})
        _log_struct(
            'api_search_instance_set_default.done',
            '已设为默认搜索引擎',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            instance_name=inst.get('name', ''),
            default_before=default_before,
            default_after=instance_id,
        )
        return jsonify({"ok": True, "message": "已设为默认搜索引擎"})
    except Exception as e:
        _log_struct(
            'api_search_instance_set_default.failed',
            f'设置默认搜索引擎失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search/instances/<string:instance_id>/test", methods=["POST"])
@require_token
@log_request()
def api_search_instance_test(instance_id):
    from app_server import _network_config_mgr, _web_search
    try:
        config = _network_config_mgr.get_raw_config()
        inst = next((i for i in config.get('search_instances', []) if i.get('id') == instance_id), None)
        if not inst or not _web_search:
            return jsonify({"ok": False, "error": "实例不存在或搜索引擎未初始化"}), (404 if not inst else 503)
        if inst.get('engine_type') == 'custom':
            result = _web_search._search_custom(inst, "test", num_results=2)
        else:
            # 调用专用 handler（如 _search_duckduckgo）
            handler = getattr(_web_search, f'_search_{inst["engine_type"]}', None)
            if handler:
                result = handler("test", num_results=2)
            else:
                result = _web_search.search(query="test", engine=inst.get('engine_type', ''), num_results=2)
        return jsonify({
            "ok": result.get("ok", False),
            "results": (result.get("results") or [])[:2],
            "total": result.get("total_estimate", 0),
            "engine": result.get("engine", ""),
            "error": result.get("error", ""),
        })
    except Exception as e:
        logger.error("[搜索实例] 测试失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  配置变更日志 & 审计日志 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/config/logs", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_config_logs():
    """获取配置变更日志"""
    from app_server import _network_config_mgr
    try:
        limit = request.args.get("limit", 20, type=int)
        logs = _network_config_mgr.get_change_log(limit)
        return jsonify({"ok": True, "logs": logs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 审计日志查询 API（只读，T8.4 第二批灰度开放；无 require_token） ──

@bp.route("/api/audit/logs", methods=["GET"])
@log_request(show_response=False)
def api_audit_logs():
    """获取结构化审计日志（只读查询 Append-only 审计日志）

    T8.4 数据隔离（修复跨租户泄露，见 T8 故障演练剧本 B.3）：
    - 经网关认证：仅返回当前 Key 绑定租户的记录（写侧 tenant_id 字段精确过滤）；
      未绑定租户的旧 Key 返回空集 + warning（隔离优先，宁可少看不可泄露）
    - 内部直调（无网关标记，如管理通道）：保持全量（管理语义）

    2026-09-01：修复 filter_by_key 不存在导致的 500（该方法已从 AuditLogger 移除，
    数据隔离改为对记录 metadata.tenant_id 的本地过滤）；同时兼容管理后台前端
    （yunshu-ui/src/api/audit.ts）的 {code, data:{list,total}} 分页契约。
    """
    try:
        from agent.audit.logger import audit_logger
        trace_id = request.args.get("trace_id", "")
        action = request.args.get("action", "")
        page = max(int(request.args.get("page", 1)), 1)
        page_size = min(max(int(request.args.get("pageSize", 10)), 1), 200)
        keyword = request.args.get("keyword", "").strip().lower()
        # 兼容旧调用方：limit 参数存在时视作单页大小（非管理后台前端）
        if "limit" in request.args:
            page_size = min(max(int(request.args.get("limit", 20)), 1), 200)

        logs = audit_logger.query(trace_id=trace_id, action=action, limit=page * page_size)

        # T8.4 数据隔离：经网关认证时按租户过滤（metadata.tenant_id 精确匹配）
        key_info = getattr(request, "_gateway_key_info", None) or {}
        tenant_id = (key_info or {}).get("tenant_id") if key_info else None
        warning = None
        if key_info and not tenant_id:
            logs = []
            warning = "当前 Key 未绑定租户，按数据隔离策略返回空集"
        elif key_info and tenant_id:
            logs = [r for r in logs if r.get("metadata", {}).get("tenant_id") == tenant_id]

        if keyword:
            logs = [r for r in logs if keyword in str(r.get("action", "")).lower()
                    or keyword in str(r.get("trace_id", "")).lower()]

        total = len(logs)
        page_logs = logs[(page - 1) * page_size: page * page_size]
        payload = {"ok": True, "logs": page_logs, "count": len(page_logs)}
        if warning:
            payload["warning"] = warning
        # 管理后台前端契约：返回 code/data 结构（list/total 分页）
        return jsonify({
            "code": 200,
            "data": {"list": page_logs, "total": total},
            "message": "success",
            **payload,
        })
    except Exception as e:
        logger.error("审计日志查询失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  搜索引擎性能监控接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/search-performance/status")
@log_request()
def api_search_performance_status():
    """获取搜索引擎性能监控状态"""
    try:
        from agent.search_performance_monitor import get_performance_monitor_status
        status = get_performance_monitor_status()
        return jsonify({"ok": True, "status": status})
    except Exception as e:
        logger.error("[性能监控] 获取状态失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search-performance/start", methods=["POST"])
@require_token
@log_request()
def api_search_performance_start():
    """启动搜索引擎性能监控"""
    try:
        from agent.search_performance_monitor import start_performance_monitor
        data = request.get_json() or {}
        interval_sec = data.get("interval_sec", 300)  # 默认 5 分钟
        status = start_performance_monitor(interval_sec)
        return jsonify({"ok": True, "message": "性能监控已启动", "status": status})
    except Exception as e:
        logger.error("[性能监控] 启动失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search-performance/stop", methods=["POST"])
@require_token
@log_request()
def api_search_performance_stop():
    """停止搜索引擎性能监控"""
    try:
        from agent.search_performance_monitor import stop_performance_monitor
        status = stop_performance_monitor()
        return jsonify({"ok": True, "message": "性能监控已停止", "status": status})
    except Exception as e:
        logger.error("[性能监控] 停止失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search-performance/check", methods=["POST"])
@require_token
@log_request()
def api_search_performance_check():
    """手动执行一次性能检测"""
    try:
        from agent.search_performance_monitor import run_manual_performance_check
        result = run_manual_performance_check()
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        logger.error("[性能监控] 手动检测失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search-performance/history")
@log_request()
def api_search_performance_history():
    """获取性能检测历史记录"""
    try:
        from agent.search_performance_monitor import get_performance_history
        limit = request.args.get("limit", 10, type=int)
        history = get_performance_history(limit)
        return jsonify({"ok": True, "history": history})
    except Exception as e:
        logger.error("[性能监控] 获取历史失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/search-performance/summary")
@log_request()
def api_search_performance_summary():
    """获取性能摘要"""
    try:
        from agent.search_performance_monitor import get_performance_summary
        summary = get_performance_summary()
        return jsonify({"ok": True, "summary": summary})
    except Exception as e:
        logger.error("[性能监控] 获取摘要失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


PLUGIN = register_plugin(Plugin(
    name="admin",
    version="1.0.0",
    description="配置、认证、审计与模型实例管理",
    blueprint=bp,
    routes=[
        "/api/config",
        "/api/config/logs",
        "/api/auth/token-check",
        "/api/audit/logs",
        "/api/network-config",
        "/api/network-config/reset",
        "/api/network-config/export",
        "/api/network-config/import",
        "/api/apply-network-config",
        "/api/system-prompt",
        "/api/system-prompt/reset",
        "/api/llm/instances",
        "/api/llm/instances/<string:instance_id>",
        "/api/llm/instances/<string:instance_id>/default",
        "/api/llm/instances/<string:instance_id>/test",
        "/api/search/instances",
        "/api/search/instances/<string:instance_id>",
        "/api/search/instances/<string:instance_id>/default",
        "/api/search/instances/<string:instance_id>/test",
        "/api/search-performance/status",
        "/api/search-performance/start",
        "/api/search-performance/stop",
        "/api/search-performance/check",
        "/api/search-performance/history",
        "/api/search-performance/summary",
    ],
))
