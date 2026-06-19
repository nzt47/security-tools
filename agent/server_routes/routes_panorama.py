"""全景 & 健康 & 状态 API 路由"""
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.tools import list_tools

logger = logging.getLogger(__name__)


def _get_sensor_categories(Yunshu):
    """获取传感器五大分类（含数据来源）"""
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
    cat_reverse = {}
    for group_name, cfg in CAT_CONFIG.items():
        for sc in cfg["sensors"]:
            cat_reverse[sc] = group_name

    sensor_info = Yunshu.body.get_sensor_info()
    grouped = {}
    for group_name in CAT_CONFIG:
        grouped[group_name] = {
            "name": f"{CAT_CONFIG[group_name]['icon']} {group_name}",
            "source": CAT_CONFIG[group_name]["source"],
            "count": 0,
            "sensors": [],
        }

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
    """获取八大维度"""
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


def _get_translate_rules(Yunshu):
    """获取翻译规则摘要"""
    try:
        rules = Yunshu._injector.config.get_all_rules()
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


def _get_behavior_modes(Yunshu):
    """获取六种行为模式"""
    current_mode = Yunshu.get_behavior_mode().value
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


def _get_permission_info(Yunshu):
    """获取权限系统统计"""
    try:
        perm = Yunshu._permission
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


def register_routes(app, state):
    """注册所有全景 & 健康 & 状态路由"""

    Yunshu = state.Yunshu
    session_mgr = state.session_mgr

    # ── 快速状态端点 ──

    @app.route("/api/health")
    @log_request(show_response=False)
    def api_health():
        readings = Yunshu.body.collect_quick()
        return jsonify([r.to_dict() for r in readings])

    @app.route("/api/sensors")
    @log_request(show_response=False)
    def api_sensors():
        return jsonify(Yunshu.body.get_sensor_info())

    @app.route("/api/status")
    @log_request(show_response=False)
    def api_status():
        status = Yunshu.get_status()
        return jsonify(status)

    @app.route("/api/mode")
    @log_request(show_response=False)
    def api_mode():
        mode = Yunshu.get_behavior_mode()
        profile = Yunshu._behavior.profile
        thinking = getattr(Yunshu, '_thinking_mode', {"mode": "idle", "label": ""})
        return jsonify({
            "mode": mode.value,
            "label": profile.label,
            "description": profile.description,
            "can_accept_tasks": profile.can_accept_tasks,
            "enable_reflection": profile.enable_reflection,
            "reasons": Yunshu._behavior._reasons,
            "thinking_mode": thinking.get("label", ""),
        })

    @app.route("/api/cognitive/status")
    @log_request(show_response=False)
    def api_cognitive_status():
        readings = Yunshu.body.collect_quick()
        reading_dicts = [r.to_dict() for r in readings]
        text = Yunshu._injector.get_summary(reading_dicts)
        body_status = Yunshu._build_body_status(readings)
        return jsonify({
            "summary": text,
            "full": body_status,
            "mode": Yunshu._behavior.profile.label,
            "mode_description": Yunshu._behavior.profile.description,
        })

    # ═══════════════════════════════════════════════════
    #  全景视图
    # ═══════════════════════════════════════════════════

    @app.route("/api/panorama")
    @log_request(show_response=False)
    def api_panorama():
        """获取全景页面所需的所有数据（单次调用）"""
        readings = Yunshu.body.collect_quick()
        reading_dicts = [r.to_dict() for r in readings]
        mode = Yunshu.get_behavior_mode()
        profile = Yunshu._behavior.profile
        sensor_info = Yunshu.body.get_sensor_info()
        summary = Yunshu._memory.load_summary()
        config = Yunshu.get_config()
        started_at = getattr(Yunshu, '_started_at', None)

        cognitive_summary = Yunshu._injector.get_summary(reading_dicts)

        # 记忆统计
        try:
            logs = Yunshu._memory._black_box.analyze()
            log_count = sum(logs.values()) if isinstance(logs, dict) else 0
        except Exception:
            log_count = 0

        # 最近消息数
        try:
            recent = Yunshu._memory._storage.load_recent_messages(limit=1)
            total_msgs = len(recent) if recent else 0
            try:
                with open(Yunshu._memory._storage.messages_file, 'r', encoding='utf-8') as f:
                    total_msgs = sum(1 for _ in f)
            except Exception:
                pass
        except Exception:
            total_msgs = 0

        # 构建交互追踪
        last_trace = []
        if session_mgr.get_current_id():
            last_msgs = session_mgr.get_messages(session_mgr.get_current_id(), limit=1)
            if last_msgs:
                last = last_msgs[-1]
                last_trace = [
                    {"phase": 1, "phase_label": "感知", "icon": "👁", "text": f"CPU {readings[0].value if readings else '?'}%, 内存 {readings[1].value if len(readings)>1 else '?'}%"},
                    {"phase": 2, "phase_label": "认知", "icon": "🧠", "text": cognitive_summary[:60]},
                    {"phase": 3, "phase_label": "记忆", "icon": "💾", "text": f"加载摘要·{total_msgs} 条历史"},
                    {"phase": 4, "phase_label": "行动", "icon": "🤖", "text": f"模式: normal → 调用 LLM → 生成响应"},
                ]
            else:
                last_trace = []
        else:
            last_trace = []

        return jsonify({
            "health": [r.to_dict() for r in readings],
            "sensor_on": sum(1 for s in sensor_info if s.get("enabled")),
            "sensor_total": len(sensor_info),
            "sensor_categories": _get_sensor_categories(Yunshu),
            "tag_dimensions": _get_tag_dimensions(),
            "sensor_list": sensor_info,
            "cognitive_summary": cognitive_summary,
            "can_accept": not Yunshu._injector.should_reject_task(reading_dicts)[0],
            "translate_rules": _get_translate_rules(Yunshu),
            "prompt_template": _get_prompt_template(),
            "summary_version": summary[1] if summary else None,
            "summary_text": summary[0][:500] if summary and summary[0] else None,
            "message_count": total_msgs,
            "log_count": log_count,
            "log_stats": logs if isinstance(logs, dict) else {},
            "compress_threshold": 0.8,
            "token_limit": 4096,
            "mode": mode.value,
            "mode_label": profile.label,
            "tool_count": len(list_tools()),
            "tool_list": list_tools(),
            "reflection_count": len(Yunshu._reflection_history),
            "llm_configured": config.get("configured", False),
            "behavior_modes": _get_behavior_modes(Yunshu),
            "permission_info": _get_permission_info(Yunshu),
            "session_id": Yunshu._session_id,
            "interaction_count": Yunshu._interaction_count,
            "started_at": started_at,
            "last_trace": last_trace,
        })
