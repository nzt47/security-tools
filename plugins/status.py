# plugins/status.py
"""status 插件：系统状态、感知与性格域（T1.4，迁移自 app_server.py）。

域路由：/api/health、/api/sensors、/api/status、/api/mode、/api/planning/toggle、
         /api/cognitive/status、/api/heartbeat*、/api/panorama、/api/personality*。

共享依赖约定（PLAN-1 §4）：
- 模块顶层只 import 标准库 / flask / plugin_api；绝不顶层 import app_server（循环导入红线）。
- app_server 的模块级全局（_Yunshu、_session_mgr、_cfg 等）在视图函数内延迟 import。
- require_token / log_request 是装饰器，需在模块级应用；而本模块在 app_server 导入中途
  被加载（plugins/__init__ ← app_server「from plugins import example」），模块级
  from app_server import ... 会触发循环导入失败。因此此处提供行为等价的本地实现
  _require_token / _log_request（_require_token 在调用时延迟读取 app_server 的令牌
  配置，运行时 app_server 已完全加载，无循环问题）。
"""

import functools
import json
import os
import secrets

from flask import Blueprint, jsonify, request

from .plugin_api import Plugin, register_plugin

bp = Blueprint("status", __name__)


def _require_token(f):
    """与 app_server.require_token 行为等价的本地版本（延迟读取令牌配置，避免循环导入）"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from app_server import _API_TOKEN_ENABLED, _API_TOKEN
        if not _API_TOKEN_ENABLED:
            return f(*args, **kwargs)
        # 从请求头中提取令牌
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = request.headers.get("X-API-Token", "")
        if not token or not secrets.compare_digest(token, _API_TOKEN):
            return jsonify({"error": "未授权：缺少或无效的 API 令牌"}), 401
        return f(*args, **kwargs)
    return decorated


def _log_request(show_body=True, show_response=True):
    """与 app_server.log_request 行为等价的本地版本（装饰器需模块级应用，无法延迟 import）"""
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            import time
            start_time = time.time()
            endpoint = f.__name__

            logs = []
            logs.append(f"[REQUEST] 接口: {endpoint}")
            logs.append(f"[REQUEST] 方法: {request.method}")
            logs.append(f"[REQUEST] 路径: {request.path}")
            logs.append(f"[REQUEST] 查询参数: {dict(request.args)}")

            if show_body and request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    body = request.get_json() if request.is_json else request.form.to_dict()
                    body_str = str(body)[:200] + ('...' if len(str(body)) > 200 else '')
                    logs.append(f"[REQUEST] 请求体: {body_str}")
                except Exception:
                    logs.append(f"[REQUEST] 请求体: 无法解析")

            # 执行原始函数
            try:
                response = f(*args, **kwargs)
                response_time = (time.time() - start_time) * 1000

                logs.append(f"[RESPONSE] 状态码: {response[1] if isinstance(response, tuple) else 200}")
                logs.append(f"[RESPONSE] 耗时: {response_time:.2f}ms")

                if show_response:
                    if isinstance(response, tuple) and len(response) > 0:
                        resp_data = response[0].get_json() if hasattr(response[0], 'get_json') else str(response[0])[:200]
                    else:
                        resp_data = response.get_json() if hasattr(response, 'get_json') else str(response)[:200]
                    logs.append(f"[RESPONSE] 内容: {resp_data}")

                success = True

            except Exception as e:
                import traceback as tb
                response_time = (time.time() - start_time) * 1000
                logs.append(f"[ERROR] 异常: {type(e).__name__} - {str(e)[:200]}")
                logs.append(f"[ERROR] 耗时: {response_time:.2f}ms")

                # 捕获堆栈信息到日志
                stack_trace = tb.format_exc()
                logs.append(f"[STACK TRACE] {stack_trace[:500]}")

                success = False

                # 打印异常日志到控制台
                print("\n" + "=" * 60)
                print(f"❌ API 请求异常 [{endpoint}]")
                print("-" * 60)
                for log in logs:
                    print(log)
                print("=" * 60 + "\n")

                raise

            finally:
                # 打印成功日志到控制台
                if success:
                    print("\n" + "=" * 60)
                    print(f"📡 API 请求日志 [{endpoint}]")
                    print("-" * 60)
                    for log in logs:
                        print(log)
                    print("=" * 60 + "\n")

            return response
        return decorated
    return decorator


# ════════════════════════════════════════════════════════════
#  人格配置管理器（迁移自 app_server.py，仅本域使用）
# ════════════════════════════════════════════════════════════

# 数据文件仍位于仓库根 data/ 目录（app_server.py 同目录），与迁移前一致
_PERSONALITY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'personality.json',
)


class PersonalityManager:
    """管理云枢的人格配置数据"""

    def __init__(self):
        self._cache = None

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        try:
            with open(_PERSONALITY_FILE, 'r', encoding='utf-8') as f:
                self._cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._cache = self._default()
        return self._cache

    def _save(self, data: dict):
        self._cache = data
        with open(_PERSONALITY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _default(self) -> dict:
        return {
            "current_profile": "gentle_helper",
            "custom_params": {"tone": 0.6, "emotion": 0.7, "conciseness": 0.4, "initiative": 0.5, "humor": 0.3, "empathy": 0.8},
            "profiles": {
                "gentle_helper": {"name": "温和助人型", "description": "温暖、耐心、富有同理心", "params": {"tone": 0.6, "emotion": 0.7, "conciseness": 0.4, "initiative": 0.5, "humor": 0.3, "empathy": 0.8}},
                "professional": {"name": "专业顾问型", "description": "严谨、客观、信息密度高", "params": {"tone": 0.3, "emotion": 0.2, "conciseness": 0.7, "initiative": 0.6, "humor": 0.1, "empathy": 0.4}},
                "humorous": {"name": "幽默风趣型", "description": "轻松、活泼、喜欢开玩笑", "params": {"tone": 0.8, "emotion": 0.9, "conciseness": 0.3, "initiative": 0.7, "humor": 0.9, "empathy": 0.6}},
            },
            "dimensions": [
                {"key": "tone", "label": "语气", "left": "正式", "right": "随意"},
                {"key": "emotion", "label": "情感", "left": "克制", "right": "丰富"},
                {"key": "conciseness", "label": "简练", "left": "详细", "right": "简洁"},
                {"key": "initiative", "label": "主动", "left": "被动", "right": "主动"},
                {"key": "humor", "label": "幽默", "left": "严肃", "right": "幽默"},
                {"key": "empathy", "label": "同理心", "left": "理性", "right": "感性"},
            ],
        }

    def get(self) -> dict:
        data = self._load()
        return {
            "current_profile": data["current_profile"],
            "custom_params": data["custom_params"],
            "profiles": data["profiles"],
            "dimensions": data["dimensions"],
        }

    def update_params(self, params: dict) -> dict:
        data = self._load()
        data["custom_params"].update(params)
        data["current_profile"] = "custom"
        self._save(data)
        return {"ok": True, "params": data["custom_params"]}

    def apply_profile(self, profile_key: str) -> dict:
        data = self._load()
        if profile_key not in data["profiles"]:
            return {"ok": False, "error": f"未知人格方案: {profile_key}"}
        profile = data["profiles"][profile_key]
        data["current_profile"] = profile_key
        data["custom_params"] = dict(profile["params"])
        self._save(data)
        return {"ok": True, "profile": profile_key, "params": data["custom_params"]}

    def reset(self) -> dict:
        return self.apply_profile("gentle_helper")


_personality_mgr = PersonalityManager()


# ════════════════════════════════════════════════════════════
#  健康检查 / 传感器 / 系统状态 / 模式 / 规划开关 / 认知状态
# ════════════════════════════════════════════════════════════

@bp.route("/api/health")
@_log_request(show_response=False)
def api_health():
    from app_server import _Yunshu
    readings = _Yunshu.body.collect_quick()
    return jsonify([r.to_dict() for r in readings])


@bp.route("/api/sensors")
@_log_request(show_response=False)
def api_sensors():
    from app_server import _Yunshu
    return jsonify(_Yunshu.body.get_sensor_info())


@bp.route("/api/status")
@_log_request(show_response=False)
def api_status():
    from app_server import _Yunshu
    status = _Yunshu.get_status()
    return jsonify(status)


@bp.route("/api/mode")
@_log_request(show_response=False)
def api_mode():
    from app_server import _Yunshu
    mode = _Yunshu.get_behavior_mode()
    profile = _Yunshu._behavior.profile
    thinking = getattr(_Yunshu, '_thinking_mode', {})
    return jsonify({
        "mode": mode.value,
        "label": profile.label,
        "description": profile.description,
        "can_accept_tasks": profile.can_accept_tasks,
        "enable_reflection": profile.enable_reflection,
        "reasons": _Yunshu._behavior._reasons,
        "thinking_mode": thinking.get("label", ""),
    })


@bp.route("/api/planning/toggle", methods=["POST"])
@_require_token
@_log_request()
def api_planning_toggle():
    """切换规划引擎运行开关（热生效，不写 config.yaml）

    对应 modules_registry.toggle_planning 动作（原标注"需新增接口"已落地）。
    运行中直接翻转 _Yunshu._planning_enabled（Orchestrator 运行时读取），
    持久化仍由 config.yaml planning.enabled 负责（重启后以配置为准）。
    """
    from app_server import _Yunshu
    data = request.get_json() or {}
    enabled = data.get("enabled")
    current = bool(getattr(_Yunshu, "_planning_enabled", False))
    if enabled is None:
        enabled = not current
    _Yunshu._planning_enabled = bool(enabled)
    return jsonify({
        "ok": True,
        "planning_enabled": bool(enabled),
        "note": "运行中已切换；持久化请修改 config.yaml planning.enabled（重启生效）",
    })


@bp.route("/api/cognitive/status")
@_log_request(show_response=False)
def api_cognitive_status():
    from app_server import _Yunshu
    readings = _Yunshu.body.collect_quick()
    reading_dicts = [r.to_dict() for r in readings]
    text = _Yunshu._injector.get_summary(reading_dicts)
    body_status = _Yunshu._build_body_status(readings)
    return jsonify({
        "summary": text,
        "full": body_status,
        "mode": _Yunshu._behavior.profile.label,
        "mode_description": _Yunshu._behavior.profile.description,
    })


# ════════════════════════════════════════════════════════════
#  全景 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/panorama")
@_log_request(show_response=False)
def api_panorama():
    """获取全景页面所需的所有数据（单次调用）"""
    from app_server import _Yunshu, _session_mgr, _cfg
    from agent.tools import list_tools
    readings = _Yunshu.body.collect_quick()
    reading_dicts = [r.to_dict() for r in readings]
    mode = _Yunshu.get_behavior_mode()
    profile = _Yunshu._behavior.profile
    sensor_info = _Yunshu.body.get_sensor_info()
    summary = _Yunshu._memory.load_summary()
    config = _Yunshu.get_config()
    started_at = getattr(_Yunshu, '_started_at', None)

    # 认知状态
    cognitive_summary = _Yunshu._injector.get_summary(reading_dicts)

    # 记忆统计
    try:
        logs = _Yunshu._memory._black_box.analyze()
        log_count = sum(logs.values()) if isinstance(logs, dict) else 0
    except Exception:
        log_count = 0

    # 最近消息数（从 storage 加载）
    try:
        recent = _Yunshu._memory._storage.load_recent_messages(limit=1)
        total_msgs = len(recent) if recent else 0
        # 尝试获取实际总数
        try:
            with open(_Yunshu._memory._storage.messages_file, 'r', encoding='utf-8') as f:
                total_msgs = sum(1 for _ in f)
        except Exception:
            pass
    except Exception:
        total_msgs = 0

    # 构建交互追踪
    last_trace = []
    if _session_mgr.get_current_id():
        last_msgs = _session_mgr.get_messages(_session_mgr.get_current_id(), limit=1)
        if last_msgs:
            last = last_msgs[-1]
            mode_label = 'normal'
            last_trace = [
                {"phase": 1, "phase_label": "感知", "icon": "👁", "text": f"CPU {readings[0].value if readings else '?'}%, 内存 {readings[1].value if len(readings)>1 else '?'}%"},
                {"phase": 2, "phase_label": "认知", "icon": "🧠", "text": cognitive_summary[:60]},
                {"phase": 3, "phase_label": "记忆", "icon": "💾", "text": f"加载摘要·{total_msgs} 条历史"},
                {"phase": 4, "phase_label": "行动", "icon": "🤖", "text": f"模式: {mode_label} → 调用 LLM → 生成响应"},
            ]
        else:
            last_trace = []
    else:
        last_trace = []

    return jsonify({
        # 阶段一
        "health": [r.to_dict() for r in readings],
        "sensor_on": sum(1 for s in sensor_info if s.get("enabled")),
        "sensor_total": len(sensor_info),
        "sensor_categories": _get_sensor_categories(),
        "tag_dimensions": _get_tag_dimensions(),
        "sensor_list": sensor_info,
        # 阶段二
        "cognitive_summary": cognitive_summary,
        "can_accept": not _Yunshu._injector.should_reject_task(reading_dicts)[0],
        "translate_rules": _get_translate_rules(),
        "prompt_template": _get_prompt_template(),
        # 阶段三
        "summary_version": summary[1] if summary else None,
        "summary_text": summary[0][:500] if summary and summary[0] else None,
        "message_count": total_msgs,
        "log_count": log_count,
        "log_stats": logs if isinstance(logs, dict) else {},
        "compress_threshold": _cfg.get("memory", "compress_threshold", default=0.8),
        "token_limit": _cfg.get("memory", "token_limit", default=4096),
        # 阶段四
        "mode": mode.value,
        "mode_label": profile.label,
        "tool_count": len(list_tools()),
        "tool_list": list_tools(),
        "reflection_count": len(_Yunshu._reflection_history),
        "llm_configured": config.get("configured", False),
        "behavior_modes": _get_behavior_modes(),
        "permission_info": _get_permission_info(),
        # 系统
        "session_id": _Yunshu._session_id,
        "interaction_count": _Yunshu._interaction_count,
        "started_at": started_at,
        # 追踪
        "last_trace": last_trace,
    })


def _get_sensor_categories():
    """获取传感器五大分类（含数据来源）"""
    from app_server import _Yunshu
    # 五大分类映射 + 数据来源
    CAT_CONFIG = {
        "硬件感知": {
            "icon": "💻", "sensors": ["cpu", "gpu", "memory", "disk", "battery", "board", "chassis", "port", "peripheral"],
            "source": "🔬 从硬件直接读取 （WMI/寄存器/传感器）",
        },
        "网络感知": {
            "icon": "🌐", "sensors": ["network"],
            "source": "🔬 从硬件直接读取 （网卡/协议栈）",
        },
        "进程与行为": {
            "icon": "⚙️", "sensors": ["process", "activity", "behavior"],
            "source": "⚡ 推测得来 （系统调用/性能计数器）",
        },
        "文件感知": {
            "icon": "📁", "sensors": ["file", "change", "hwfile"],
            "source": "🖥️ 从软件获得 （文件系统 API/快照对比）",
        },
        "系统与环境": {
            "icon": "🌿", "sensors": ["environment", "system"],
            "source": "🖥️ 从软件获得 （OS 环境变量/系统 API）",
        },
    }
    # 反向映射: category → 分类名
    cat_reverse = {}
    for group_name, cfg in CAT_CONFIG.items():
        for sc in cfg["sensors"]:
            cat_reverse[sc] = group_name

    sensor_info = _Yunshu.body.get_sensor_info()
    grouped = {}
    for group_name in CAT_CONFIG:
        grouped[group_name] = {
            "name": f"{CAT_CONFIG[group_name]['icon']} {group_name}",
            "source": CAT_CONFIG[group_name]["source"],
            "count": 0,
            "sensors": [],
        }

    # 导入标签计算函数
    try:
        from sensor.tags import get_tags
    except Exception:
        get_tags = None

    for s in sensor_info:
        cat = s.get("category", "")
        group = cat_reverse.get(cat, "📡 其他")
        if group not in grouped:
            continue
        grouped[group]["count"] += 1
        sensor_tags = []
        if get_tags:
            try:
                sensor_tags = get_tags(cat, s.get("name", ""))
            except Exception:
                pass
        grouped[group]["sensors"].append({
            "name": s.get("label", s.get("name", "")),
            "key": s.get("name", ""),
            "enabled": s.get("enabled", True),
            "tags": sensor_tags,
        })

    return list(grouped.values())


def _get_tag_dimensions():
    """获取八大维度（硬编码，与 tags.py 同步）"""
    return [
        {"label": "目标域", "values": ["硬件感知", "软件感知", "行为感知", "环境感知"]},
        {"label": "内外方位", "values": ["内部感知", "外部感知", "边界感知"]},
        {"label": "动静属性", "values": ["静态配置", "动态运行", "增量变化"]},
        {"label": "采集方式", "values": ["主动探测", "被动监听", "系统查询", "对比检测"]},
        {"label": "感知层次", "values": ["物理层", "系统层", "应用层"]},
        {"label": "功能角色", "values": ["基础生存", "性能监控", "安全防护", "社交通信", "环境适应"]},
        {"label": "数据特征", "values": ["数值量", "状态量", "事件量", "配置量"]},
        {"label": "可干预性", "values": ["仅可观测", "可配置"]},
    ]


def _get_translate_rules():
    """获取翻译规则摘要"""
    from app_server import _Yunshu
    try:
        rules = _Yunshu._injector.config.get_all_rules()
        result = []
        for name, rule in rules.items():
            thresholds = rule.get("thresholds", [])
            first = thresholds[0] if thresholds else {}
            result.append({
                "name": name,
                "message": first.get("message", rule.get("description", name)),
                "unit": rule.get("unit", ""),
            })
        return result[:8]
    except Exception:
        return []


def _get_prompt_template():
    """获取提示词模板"""
    try:
        from cognitive.templates import DEFAULT_TEMPLATE
        return DEFAULT_TEMPLATE[:500]
    except Exception:
        return ""


def _get_behavior_modes():
    """获取六种行为模式"""
    from app_server import _Yunshu
    current_mode = _Yunshu.get_behavior_mode().value
    mode_info = {
        "normal": {"label": "正常模式", "desc": "全能力运行", "color": "#3fb950"},
        "safe": {"label": "安全模式", "desc": "CPU过热·拒绝高耗能", "color": "#f85149"},
        "power_save": {"label": "省电模式", "desc": "电量不足·降推理", "color": "#d29922"},
        "memory_compact": {"label": "整理模式", "desc": "内存紧张·触发压缩", "color": "#bc8cff"},
        "offline": {"label": "离线模式", "desc": "网络中断·本地逻辑", "color": "#8b949e"},
        "warning": {"label": "预警模式", "desc": "磁盘不足·提示清理", "color": "#db6d28"},
    }
    result = []
    for key, info in mode_info.items():
        active = key == current_mode
        result.append({
            "key": key,
            "label": info["label"],
            "desc": info["desc"],
            "color": info["color"] if active else "#30363d",
            "active": active,
        })
    return result


def _get_permission_info():
    """获取权限系统统计"""
    from app_server import _Yunshu
    try:
        perm = _Yunshu._permission
        logs = perm.get_permission_log()
        import os
        backup_dir = getattr(perm, '_backup_dir', None)
        backup_count = 0
        if backup_dir and os.path.isdir(backup_dir):
            backup_count = len(os.listdir(backup_dir))
        return {
            "check_count": len(logs),
            "backup_count": backup_count,
            "backup_dir": str(backup_dir) if backup_dir else "-",
        }
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════
#  人格配置 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/personality", methods=["GET"])
@_log_request(show_response=False)
def api_personality_get():
    return jsonify(_personality_mgr.get())


@bp.route("/api/personality/params", methods=["POST"])
@_require_token
@_log_request()
def api_personality_params():
    data = request.get_json() or {}
    params = data.get("params", {})
    result = _personality_mgr.update_params(params)
    return jsonify(result)


@bp.route("/api/personality/profile", methods=["POST"])
@_require_token
@_log_request()
def api_personality_profile():
    data = request.get_json() or {}
    profile = data.get("profile", "")
    result = _personality_mgr.apply_profile(profile)
    return jsonify(result)


@bp.route("/api/personality/reset", methods=["POST"])
@_require_token
@_log_request()
def api_personality_reset():
    result = _personality_mgr.reset()
    return jsonify(result)


# ════════════════════════════════════════════════════════════
#  心跳接口
# ════════════════════════════════════════════════════════════

@bp.route("/api/heartbeat")
@_log_request(show_response=False)
def api_heartbeat():
    """心跳检测接口 — 全维度健康检查"""
    from app_server import _Yunshu
    from agent.task_scheduler import get_scheduler, perform_heartbeat_check
    try:
        # 执行完整心跳检查
        hb_result = perform_heartbeat_check(_Yunshu)
        # 同步保存到调度器
        scheduler = get_scheduler()
        scheduler._save_heartbeat(hb_result)
        return jsonify(hb_result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@bp.route("/api/heartbeat/history")
@_log_request(show_response=False)
def api_heartbeat_history():
    """获取心跳历史"""
    from agent.task_scheduler import get_scheduler
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    scheduler = get_scheduler()
    data = scheduler.get_heartbeat_status()
    history = data.get("history", [])
    total = len(history)
    history.reverse()
    paged = history[offset:offset + limit]
    return jsonify({
        "history": paged,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@bp.route("/api/heartbeat/status")
@_log_request(show_response=False)
def api_heartbeat_status():
    """获取心跳概览"""
    from agent.task_scheduler import get_scheduler
    scheduler = get_scheduler()
    data = scheduler.get_heartbeat_status()
    latest = data.get("latest", {})
    history = data.get("history", [])
    healthy_count = sum(1 for h in history if h.get("status") == "healthy")
    return jsonify({
        "status": latest.get("status", "unknown"),
        "timestamp": latest.get("timestamp"),
        "total_checks": len(history),
        "healthy_checks": healthy_count,
        "latest": latest,
    })


PLUGIN = register_plugin(Plugin(
    name="status",
    version="1.0.0",
    description="系统状态、感知与性格",
    blueprint=bp,
    routes=[
        "/api/health",
        "/api/sensors",
        "/api/status",
        "/api/mode",
        "/api/planning/toggle",
        "/api/cognitive/status",
        "/api/heartbeat",
        "/api/heartbeat/history",
        "/api/heartbeat/status",
        "/api/panorama",
        "/api/personality",
        "/api/personality/params",
        "/api/personality/profile",
        "/api/personality/reset",
    ],
))
