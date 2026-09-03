# -*- coding: utf-8 -*-
"""云枢技能/扩展/工具域插件（T1.5）：技能 / 扩展 / 工具管理。

从 app_server.py 迁移而来，路由路径与行为 100% 不变。
约定（PLAN-1 §4）：
  - Blueprint 不设 url_prefix，路由保持 /api/... 原样；
  - 插件模块顶层只 import flask / plugin_api / 标准库；
  - 共享依赖（require_token、log_request、_skills_mgr、_extension_mgr、
    _extension_market、_Yunshu、logger 等）保留在 app_server.py，
    视图函数内部延迟 import，规避循环导入。
  - SkillsManager（_skills_mgr）被 /api/assets/* 路由共享，留在 app_server.py；
    工具状态持久化 helper 仅本域使用，随插件迁入。
"""
import functools
import json
import os

from flask import Blueprint, request, jsonify

from .plugin_api import Plugin, register_plugin

bp = Blueprint("skills", __name__)


# ════════════════════════════════════════════════════════════════════════════
#  共享装饰器（延迟包装）
# ----------------------------------------------------------------------------
# require_token / log_request 保留在 app_server.py；插件顶层不得 import app_server
# （循环导入红线，PLAN-1 §4），故在请求时取用真实装饰器再调用。
# 包装顺序与语义和迁移前一致：日志装饰器在内、令牌校验装饰器在外。
# ════════════════════════════════════════════════════════════════════════════

def _lazy_wrap(f, build):
    """占位包装器：每次调用时用 app_server 的真实装饰器包装 f 后执行。"""
    @functools.wraps(f)
    def _wrapped(*args, **kwargs):
        return build(f)(*args, **kwargs)
    return _wrapped


def _require_token(f):
    """延迟版 @require_token（app_server 共享装饰器）"""
    def _build(fn):
        from app_server import require_token as _real
        return _real(fn)
    return _lazy_wrap(f, _build)


def _log_request(*args, **kwargs):
    """延迟版 @log_request(...)（app_server 共享装饰器）"""
    def _decorator(f):
        def _build(fn):
            from app_server import log_request as _real
            return _real(*args, **kwargs)(fn)
        return _lazy_wrap(f, _build)
    return _decorator


# ════════════════════════════════════════════════════════════════════════════
#  工具状态持久化（从 app_server.py 迁移；仅本域使用，故随插件迁入）
# ════════════════════════════════════════════════════════════════════════════
_TOOLS_CONFIG_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'tools_config.json')
)


def _load_tool_states() -> dict:
    """加载工具启用状态"""
    try:
        with open(_TOOLS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tool_states": {}}


def _save_tool_states(data: dict):
    """保存工具启用状态"""
    with open(_TOOLS_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_tool_state(name: str) -> bool:
    """获取单个工具的启用状态，默认启用"""
    data = _load_tool_states()
    return data.get("tool_states", {}).get(name, True)


def _set_tool_state(name: str, enabled: bool):
    """设置单个工具的启用状态"""
    data = _load_tool_states()
    data.setdefault("tool_states", {})[name] = enabled
    _save_tool_states(data)


def _get_enabled_tool_names() -> list[str] | None:
    """获取所有已启用的工具名称列表，没有配置文件时返回 None（全部启用）"""
    data = _load_tool_states()
    states = data.get("tool_states", {})
    if not states:
        return None
    enabled = [name for name, e in states.items() if e]
    return enabled if enabled else []


# ════════════════════════════════════════════════════════════
#  技能配置 API
# ════════════════════════════════════════════════════════════

# 中文说明覆盖层：部分内置/外来技能元数据缺 description，
# 允许在 UI 手工补中文说明并持久化于此（运行时启停/列表都会保留）。
_DESC_OVERLAY_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'data', 'skills_descriptions_overlay.json')
)

# 已知内置技能的中文说明（供“自动补全中文说明”使用）
_CURATED_DESCRIPTIONS = {
    "self_reflection": "自省反思：复盘自身行为与决策，沉淀经验与改进方向",
    "email-helper": "邮件处理助手：起草、整理与管理邮件（处理收件与回复）",
    "memory_summary": "记忆摘要：压缩与归纳对话历史与长期记忆，控制上下文占用",
    "scripted-selftest": "三层架构示例技能：演示 skill.md 元数据 + 脚本执行 + 参数注入契约",
}


def _load_desc_overlay() -> dict:
    try:
        if os.path.exists(_DESC_OVERLAY_FILE):
            with open(_DESC_OVERLAY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_desc_overlay(overlay: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(_DESC_OVERLAY_FILE), exist_ok=True)
        with open(_DESC_OVERLAY_FILE, 'w', encoding='utf-8') as f:
            json.dump(overlay, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _apply_desc_overlay(items: list) -> None:
    """把覆盖层里的中文说明补到缺描述的已安装技能上"""
    try:
        overlay = _load_desc_overlay()
        for s in items:
            o = overlay.get(s.get("id", ""))
            if o and isinstance(o, dict) and o.get("description") \
                    and not str(s.get("description", "") or "").strip():
                s["description"] = o["description"]
    except Exception:
        pass


@bp.route("/api/skills", methods=["GET"])
@_log_request(show_response=False)
def api_skills_get():
    """获取技能列表（分类：已安装 + 可安装的内置技能）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _skills_mgr
    installed = _skills_mgr.get_all()
    installed_ids = {s["id"] for s in installed}

    # 从扩展存储获取额外已安装的扩展（通过 ext_install 安装的技能和 claude_skill）
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

    # 标记已安装状态
    available = []
    for s in builtin_list:
        available.append({
            "id": s["id"],
            "name": s["name"],
            "description": s.get("description", ""),
            "installed": s["id"] in installed_ids,
            "builtin": s.get("builtin", False),
        })

    # 中文说明覆盖层：补缺描述
    _apply_desc_overlay(installed)

    # 自动分类（与技能资产库共用分类注册表；新技能出现自动归类/新建类）
    try:
        from agent.skills_mgmt.categorizer import SkillClassRegistry, UNCLASSIFIED
        reg = SkillClassRegistry()
        auto_names = reg.auto_class_names()
        for s in installed:
            try:
                s["class_name"] = reg.resolve(
                    f"rt:{s.get('id', '')}",
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    content=s.get("content", "") or s.get("script", ""),
                    tags=s.get("tags"))
                s["class_auto"] = s.get("class_name") in auto_names
            except Exception:  # noqa: BLE001 单条失败不影响列表
                s["class_name"] = UNCLASSIFIED
                s["class_auto"] = False
        for s in available:
            try:
                s["class_name"] = reg.resolve(
                    f"rt:{s.get('id', '')}",
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    content=s.get("content", ""),
                    tags=s.get("tags"))
                s["class_auto"] = s.get("class_name") in auto_names
            except Exception:  # noqa: BLE001
                s["class_name"] = UNCLASSIFIED
                s["class_auto"] = False
    except Exception:  # noqa: BLE001 分类不可用时列表照常返回
        pass

    return jsonify({
        "installed": installed,
        "available": available,
    })


@bp.route("/api/skills/toggle", methods=["POST"])
@_require_token
@_log_request()
def api_skills_toggle():
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _skills_mgr, logger as _logger
    data = request.get_json() or {}
    skill_id = str(data.get("id", "") or "")
    if not skill_id:
        return jsonify({"ok": False, "error": "缺少 id"}), 400
    result = _skills_mgr.toggle(skill_id)
    new_enabled = bool(result.get("enabled", True))
    new_name = str(result.get("name") or skill_id)
    overlay = _load_desc_overlay()
    new_desc = str((overlay.get(skill_id) or {}).get("description", "")
                   or result.get("description", "") or "")

    # 1/2) 写 root 与 agent/data/skills.json：只更新目标行，保留其它技能行
    import json as _json
    skills_file = os.path.normpath(
        os.path.join(os.path.dirname(__file__), '..', 'data', 'skills.json'))
    try:
        all_skills = {"skills": []}
        if os.path.exists(skills_file):
            try:
                with open(skills_file, 'r', encoding='utf-8') as f:
                    all_skills = _json.load(f)
            except Exception:
                all_skills = {"skills": []}
        skills = all_skills.setdefault("skills", [])
        entry = next((s for s in skills if s.get("id") == skill_id), None)
        if entry is None:
            entry = {"id": skill_id}
            skills.append(entry)
        # manager 未返回 enabled（未知 id 等情况）时沿用文件里既有状态
        new_enabled = bool(result.get("enabled", entry.get("enabled", True)))
        entry.update({
            "name": new_name,
            "enabled": new_enabled,
            "description": new_desc,
            "params": entry.get("params", {}) if isinstance(entry.get("params"), dict) else {},
        })
        payload = _json.dumps(all_skills, ensure_ascii=False, indent=2)
        targets = [
            skills_file,
            os.path.normpath(os.path.join(os.path.dirname(__file__), '..',
                                          'agent', 'data', 'skills.json')),
        ]
        for target in targets:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'w', encoding='utf-8') as f:
                f.write(payload)
    except Exception as e:
        _logger.error("[SKILL_SYNC] 同步 skills.json 失败: %s", e)

    # 3) 扩展注册表：保留现有条目，仅更新目标行状态
    try:
        ext_file = os.path.normpath(os.path.join(os.path.dirname(__file__), '..',
                                                 'agent', 'data', 'extensions.json'))
        if os.path.exists(ext_file):
            with open(ext_file, 'r', encoding='utf-8') as f:
                ext_data = _json.load(f)
        else:
            ext_data = {"skills": [], "claude_skills": [], "mcps": [],
                        "channels": [], "plugins": []}
        skills = ext_data.setdefault("skills", [])
        e = next((x for x in skills if x.get("ext_id") == skill_id), None)
        if e is None:
            e = {"ext_id": skill_id, "ext_type": "skill", "source": "builtin"}
            skills.append(e)
        e.update({
            "name": new_name,
            "status": "enabled" if new_enabled else "disabled",
            "description": new_desc,
        })
        with open(ext_file, 'w', encoding='utf-8') as f:
            f.write(_json.dumps(ext_data, ensure_ascii=False, indent=2))
    except Exception as e:
        _logger.error("[SKILL_SYNC] 同步 extensions.json 失败: %s", e)

    return jsonify(result)


@bp.route("/api/skills/describe", methods=["POST"])
@_require_token
@_log_request()
def api_skills_describe():
    """手工补写某个运行时技能的中文说明（持久化到覆盖层）"""
    data = request.get_json() or {}
    skill_id = str(data.get("id", "") or "")
    description = str(data.get("description", "") or "").strip()
    if not skill_id:
        return jsonify({"ok": False, "error": "缺少 id"}), 400
    overlay = _load_desc_overlay()
    overlay[skill_id] = {"description": description}
    ok = _save_desc_overlay(overlay)
    return jsonify({"ok": ok, "id": skill_id, "description": description})


@bp.route("/api/skills/describe/auto", methods=["POST"])
@_require_token
@_log_request()
def api_skills_describe_auto():
    """为缺描述的已知内置技能自动补中文说明（自省/邮件/记忆摘要等）"""
    data = request.get_json() or {}
    ids = data.get("ids") or list(_CURATED_DESCRIPTIONS.keys())
    overlay = _load_desc_overlay()
    applied = []
    for sid in ids:
        sid = str(sid)
        cur = _CURATED_DESCRIPTIONS.get(sid)
        if not cur:
            continue
        # 目标描述为空才覆盖（不覆盖用户已填写的）
        existing = str((overlay.get(sid) or {}).get("description", "") or "")
        if existing:
            continue
        overlay[sid] = {"description": cur}
        applied.append({"id": sid, "description": cur})
    ok = _save_desc_overlay(overlay)
    return jsonify({"ok": ok, "applied": applied, "count": len(applied)})


@bp.route("/api/skills/params", methods=["POST"])
@_require_token
@_log_request()
def api_skills_params():
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _skills_mgr
    data = request.get_json() or {}
    return jsonify(_skills_mgr.update_params(data.get("id", ""), data.get("params", {})))


@bp.route("/api/skills/add", methods=["POST"])
@_require_token
@_log_request()
def api_skills_add():
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _skills_mgr
    return jsonify(_skills_mgr.add(request.get_json() or {}))


@bp.route("/api/skills/delete", methods=["POST"])
@_require_token
@_log_request()
def api_skills_delete():
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _skills_mgr
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

    # 从 skills.json 删除
    result = _skills_mgr.delete(skill_id)
    deleted = result.get("ok", False)

    # 尝试从扩展存储删除（覆盖 Claude Code 技能等）
    try:
        from agent.extensions.store import ExtensionStore
        from agent.extensions.base import ExtensionType
        ext_store = ExtensionStore()
        for ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
            if ext_store.remove(ext_type, skill_id):
                deleted = True
                # 如果是 Claude Code 技能，清理磁盘文件
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


# ════════════════════════════════════════════════════════════
#  扩展系统 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/extensions/list", methods=["GET"])
@_require_token
@_log_request(show_response=False)
def api_extensions_list():
    """列出所有已安装扩展"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        ext_type = request.args.get("type")
        result = _extension_mgr.list_all(ext_type)
        return jsonify({"ok": True, "extensions": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/installed", methods=["GET"])
@_require_token
@_log_request(show_response=False)
def api_extensions_installed():
    """按类型分组获取已安装扩展"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        result = _extension_mgr.get_installed_by_type()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/install", methods=["POST"])
@_require_token
@_log_request()
def api_extensions_install():
    """安装扩展"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        source = data.get("source", data.get("id", ""))
        kwargs = data.get("params", {})

        if not ext_type or not source:
            return jsonify({"ok": False, "error": "缺少 type 或 source/id"}), 400

        result = _extension_mgr.install(ext_type, source, **kwargs)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/uninstall", methods=["POST"])
@_require_token
@_log_request()
def api_extensions_uninstall():
    """卸载扩展"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        ext_id = data.get("id", "")

        if not ext_type or not ext_id:
            return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400

        result = _extension_mgr.uninstall(ext_type, ext_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/toggle", methods=["POST"])
@_require_token
@_log_request()
def api_extensions_toggle():
    """启用/禁用扩展"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        ext_id = data.get("id", "")
        enabled = data.get("enabled")  # None 表示切换

        if not ext_type or not ext_id:
            return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400

        result = _extension_mgr.toggle(ext_type, ext_id, enabled)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/configure", methods=["POST"])
@_require_token
@_log_request()
def api_extensions_configure():
    """配置扩展参数"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        ext_id = data.get("id", "")
        config = data.get("config", {})

        if not ext_type or not ext_id:
            return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400

        result = _extension_mgr.configure(ext_type, ext_id, config)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/discover", methods=["GET"])
@_require_token
@_log_request(show_response=False)
def api_extensions_discover():
    """发现所有可用扩展"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        result = _extension_mgr.discover_all()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/market/search", methods=["GET"])
@_require_token
@_log_request(show_response=False)
def api_extensions_market_search():
    """搜索扩展市场"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_market
    try:
        query = request.args.get("q", "")
        ext_type = request.args.get("type")
        include_github = request.args.get("github", "true").lower() == "true"

        result = _extension_market.search_all(query, ext_type, include_github)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/market/recommend", methods=["GET"])
@_require_token
@_log_request(show_response=False)
def api_extensions_market_recommend():
    """获取推荐扩展"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_market
    try:
        ext_type = request.args.get("type")
        limit = request.args.get("limit", 5, type=int)
        result = _extension_market.get_recommendations(ext_type, limit)
        return jsonify({"ok": True, "recommendations": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/market/refresh", methods=["POST"])
@_require_token
@_log_request()
def api_extensions_market_refresh():
    """刷新社区扩展索引"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_market
    try:
        result = _extension_market.fetch_community_index()
        if result:
            return jsonify({"ok": True, "count": len(result)})
        return jsonify({"ok": False, "error": "获取索引失败"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/extensions/channels/send", methods=["POST"])
@_require_token
@_log_request()
def api_extensions_channel_send():
    """通过通道发送消息"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _extension_mgr
    try:
        data = request.get_json() or {}
        channel_id = data.get("channel_id", "")
        message = data.get("message", "")
        kwargs = data.get("params", {})

        if not channel_id or not message:
            return jsonify({"ok": False, "error": "缺少 channel_id 或 message"}), 400

        result = _extension_mgr.send_channel_message(channel_id, message, **kwargs)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  工具配置 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/tools/config", methods=["GET"])
@_log_request(show_response=False)
def api_tools_config():
    """获取工具列表及使用统计"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _Yunshu
    from agent.tools import list_tools
    tools = list_tools()
    try:
        perm_logs = _Yunshu._permission.get_permission_log()
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


@bp.route("/api/tools/toggle", methods=["POST"])
@_require_token
@_log_request()
def api_tools_toggle():
    """切换工具启用状态"""
    data = request.get_json() or {}
    tool_name = data.get("name", "")
    enabled = data.get("enabled", True)
    _set_tool_state(tool_name, enabled)
    return jsonify({"ok": True, "name": tool_name, "enabled": enabled})


# ════════════════════════════════════════════════════════════
#  工具分类 & 路由关键词 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/tools/categories", methods=["GET"])
@_log_request(show_response=False)
def api_tools_categories():
    from agent.tool_router import get_categorized_tools, get_keywords
    return jsonify({
        "categories": get_categorized_tools(),
        "keywords": get_keywords(),
    })


@bp.route("/api/tools/keywords", methods=["POST"])
@_require_token
@_log_request()
def api_tools_keywords_add():
    data = request.get_json() or {}
    category = data.get("category", "")
    keyword = data.get("keyword", "").strip()
    if not category or not keyword:
        return jsonify({"ok": False, "error": "缺少 category 或 keyword"}), 400
    from agent.tool_router import add_keyword
    ok = add_keyword(category, keyword)
    return jsonify({"ok": ok})


@bp.route("/api/tools/keywords", methods=["DELETE"])
@_require_token
@_log_request()
def api_tools_keywords_remove():
    data = request.get_json() or {}
    category = data.get("category", "")
    keyword = data.get("keyword", "").strip()
    if not category or not keyword:
        return jsonify({"ok": False, "error": "缺少 category 或 keyword"}), 400
    from agent.tool_router import remove_keyword
    ok = remove_keyword(category, keyword)
    return jsonify({"ok": ok})


@bp.route("/api/tools/keywords/update", methods=["POST"])
@_require_token
@_log_request()
def api_tools_keywords_update():
    data = request.get_json() or {}
    category = data.get("category", "")
    old_kw = data.get("old_keyword", "").strip()
    new_kw = data.get("new_keyword", "").strip()
    if not category or not old_kw or not new_kw:
        return jsonify({"ok": False, "error": "缺少必要参数"}), 400
    from agent.tool_router import update_keyword
    ok = update_keyword(category, old_kw, new_kw)
    return jsonify({"ok": ok})


@bp.route("/api/tools/keywords/reset", methods=["POST"])
@_require_token
@_log_request()
def api_tools_keywords_reset():
    from agent.tool_router import reset_keywords
    ok = reset_keywords()
    return jsonify({"ok": ok})


@bp.route("/api/tools/health")
@_log_request(show_response=False)
def api_tools_health():
    """获取工具健康状态（追踪、成功率、评分）"""
    from agent.tools import get_health_status
    return jsonify(get_health_status())


@bp.route("/api/tools/status-batch", methods=["GET"])
@_log_request(show_response=False)
def api_tools_status_batch():
    """获取所有工具和技能的启用状态摘要（供快捷开关栏使用）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _skills_mgr
    from agent.tools import list_tools
    tools = list_tools()
    result = []
    for t in tools:
        result.append({
            "type": "tool",
            "name": t["name"],
            "description": t.get("description", ""),
            "enabled": _get_tool_state(t["name"]),
        })
    # 添加技能状态
    skills = _skills_mgr.get_all()
    for s in skills:
        result.append({
            "type": "skill",
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "description": s.get("description", ""),
            "enabled": s.get("enabled", True),
        })
    return jsonify(result)


PLUGIN = register_plugin(Plugin(
    name="skills",
    version="1.0.0",
    description="技能、扩展与工具管理",
    schema={
        "type": "object",
        "title": "技能与工具管理",
        "description": "技能/工具启用状态与路由关键词（与 /api/skills、/api/tools/* 对齐）",
        "properties": {
            "skill_ids": {
                "type": "array",
                "title": "启用的技能 ID",
                "items": {"type": "string"},
            },
            "tool_states": {
                "type": "object",
                "title": "工具启用状态",
                "description": "工具名 → 是否启用（持久化于 data/tools_config.json）",
                "additionalProperties": {"type": "boolean"},
            },
            "routing_keywords": {
                "type": "array",
                "title": "工具路由关键词",
                "description": "工具分类路由关键词（/api/tools/keywords）",
                "items": {"type": "string"},
            },
        },
    },
    blueprint=bp,
    routes=[
        "/api/skills",
        "/api/skills/add",
        "/api/skills/delete",
        "/api/skills/params",
        "/api/skills/toggle",
        "/api/extensions/channels/send",
        "/api/extensions/configure",
        "/api/extensions/discover",
        "/api/extensions/install",
        "/api/extensions/installed",
        "/api/extensions/list",
        "/api/extensions/market/recommend",
        "/api/extensions/market/refresh",
        "/api/extensions/market/search",
        "/api/extensions/toggle",
        "/api/extensions/uninstall",
        "/api/tools/categories",
        "/api/tools/config",
        "/api/tools/health",
        "/api/tools/keywords",
        "/api/tools/keywords/reset",
        "/api/tools/keywords/update",
        "/api/tools/status-batch",
        "/api/tools/toggle",
    ],
))
