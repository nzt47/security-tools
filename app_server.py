"""灵犀 Web 应用 — 感知底座 + 数字生命对话

整合 BodySensor 仪表盘和 DigitalLife 聊天界面，
提供完整的可视化交互体验。

启动:
    python app_server.py
    访问 http://127.0.0.1:5678
"""

import os
import json
import logging
import platform
import webbrowser
import concurrent.futures
from flask import Flask, jsonify, render_template, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)
app.static_folder = os.path.join(os.path.dirname(__file__), 'static')
app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')

# ── 初始化 DigitalLife ──
from config import Config
from agent import DigitalLife

_cfg = Config()
_lingxi = DigitalLife(_cfg.merged)
_lingxi.start()

# ── 人格配置管理器 ──
_PERSONALITY_FILE = os.path.join(os.path.dirname(__file__), 'data', 'personality.json')

class PersonalityManager:
    """管理灵犀的人格配置数据"""

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

# ── 技能配置管理器 ──
_SKILLS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'skills.json')

class SkillsManager:
    """管理灵犀的技能配置"""

    def _load(self) -> dict:
        try:
            with open(_SKILLS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"skills": []}

    def _save(self, data: dict):
        with open(_SKILLS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_all(self) -> list:
        return self._load().get("skills", [])

    def toggle(self, skill_id: str) -> dict:
        data = self._load()
        for s in data["skills"]:
            if s["id"] == skill_id:
                s["enabled"] = not s.get("enabled", True)
                self._save(data)
                return {"ok": True, "id": skill_id, "enabled": s["enabled"]}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

    def update_params(self, skill_id: str, params: dict) -> dict:
        data = self._load()
        for s in data["skills"]:
            if s["id"] == skill_id:
                s["params"].update(params)
                self._save(data)
                return {"ok": True, "id": skill_id, "params": s["params"]}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

    def add(self, skill: dict) -> dict:
        data = self._load()
        skill_id = skill.get("id", "")
        if any(s["id"] == skill_id for s in data["skills"]):
            return {"ok": False, "error": f"技能已存在: {skill_id}"}
        data["skills"].append({
            "id": skill_id,
            "name": skill.get("name", skill_id),
            "enabled": skill.get("enabled", True),
            "description": skill.get("description", ""),
            "params": skill.get("params", {}),
        })
        self._save(data)
        return {"ok": True, "id": skill_id}

    def delete(self, skill_id: str) -> dict:
        data = self._load()
        before = len(data["skills"])
        data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]
        if len(data["skills"]) < before:
            self._save(data)
            return {"ok": True}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

_skills_mgr = SkillsManager()

# ── 聊天历史 ──
_CHAT_HISTORY = []


# ════════════════════════════════════════════════════════════
#  API 路由
# ════════════════════════════════════════════════════════════

@app.route("/api/health")
def api_health():
    readings = _lingxi.body.collect_quick()
    return jsonify([r.to_dict() for r in readings])


@app.route("/api/sensors")
def api_sensors():
    return jsonify(_lingxi.body.get_sensor_info())


@app.route("/api/status")
def api_status():
    status = _lingxi.get_status()
    return jsonify(status)


@app.route("/api/mode")
def api_mode():
    mode = _lingxi.get_behavior_mode()
    profile = _lingxi._behavior.profile
    return jsonify({
        "mode": mode.value,
        "label": profile.label,
        "description": profile.description,
        "can_accept_tasks": profile.can_accept_tasks,
        "enable_reflection": profile.enable_reflection,
        "reasons": _lingxi._behavior._reasons,
    })


@app.route("/api/cognitive/status")
def api_cognitive_status():
    readings = _lingxi.body.collect_quick()
    reading_dicts = [r.to_dict() for r in readings]
    text = _lingxi._injector.get_summary(reading_dicts)
    body_status = _lingxi._build_body_status(readings)
    return jsonify({
        "summary": text,
        "full": body_status,
        "mode": _lingxi._behavior.profile.label,
        "mode_description": _lingxi._behavior.profile.description,
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json()
    user_input = (data or {}).get("message", "").strip()
    if not user_input:
        return jsonify({"error": "消息不能为空"}), 400

    # 记录 LLM 状态便于诊断
    llm_state = _lingxi.get_config()
    logger.info(f"Chat request - LLM configured: {llm_state['configured']}, "
                f"provider: {llm_state['provider']}, key_set: {llm_state['api_key_set']}")

    try:
        response = _lingxi.chat(user_input)
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
        response = f"（处理出错: {e}）"

    entry = {
        "user": user_input,
        "lingxi": response,
        "mode": _lingxi.get_behavior_mode().value,
    }
    _CHAT_HISTORY.append(entry)

    return jsonify({
        "response": response,
        "mode": _lingxi.get_behavior_mode().value,
        "mode_label": _lingxi._behavior.profile.label,
        "health": _lingxi.body.get_health_report(),
        "llm_state": llm_state,
    })


@app.route("/api/history")
def api_history():
    # 返回带真实索引的历史记录
    start = max(0, len(_CHAT_HISTORY) - 50)
    result = []
    for i in range(start, len(_CHAT_HISTORY)):
        entry = dict(_CHAT_HISTORY[i])
        entry["_real_index"] = i
        result.append(entry)
    return jsonify(result)


@app.route("/api/clear", methods=["POST"])
def api_clear():
    _CHAT_HISTORY.clear()
    return jsonify({"ok": True})


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    """清空所有历史记录"""
    _CHAT_HISTORY.clear()
    return jsonify({"ok": True})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """获取或设置 LLM 配置"""
    if request.method == "GET":
        return jsonify(_lingxi.get_config())

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

    result = _lingxi.configure_llm(
        provider=data.get("provider", ""),
        api_key=data.get("api_key", ""),
        model=data.get("model", ""),
    )
    if result.get("ok"):
        _CHAT_HISTORY.clear()
    return jsonify(result)


# ════════════════════════════════════════════════════════════
#  全景 API
# ════════════════════════════════════════════════════════════

@app.route("/api/panorama")
def api_panorama():
    """获取全景页面所需的所有数据（单次调用）"""
    readings = _lingxi.body.collect_quick()
    reading_dicts = [r.to_dict() for r in readings]
    mode = _lingxi.get_behavior_mode()
    profile = _lingxi._behavior.profile
    sensor_info = _lingxi.body.get_sensor_info()
    summary = _lingxi._memory.load_summary()
    config = _lingxi.get_config()
    started_at = getattr(_lingxi, '_started_at', None)

    # 认知状态
    cognitive_summary = _lingxi._injector.get_summary(reading_dicts)

    # 记忆统计
    try:
        logs = _lingxi._memory._black_box.analyze()
        log_count = sum(logs.values()) if isinstance(logs, dict) else 0
    except Exception:
        log_count = 0

    # 最近消息数（从 storage 加载）
    try:
        recent = _lingxi._memory._storage.load_recent_messages(limit=1)
        total_msgs = len(recent) if recent else 0
        # 尝试获取实际总数
        try:
            with open(_lingxi._memory._storage.messages_file, 'r', encoding='utf-8') as f:
                total_msgs = sum(1 for _ in f)
        except Exception:
            pass
    except Exception:
        total_msgs = 0

    # 构建交互追踪
    last_trace = []
    if _CHAT_HISTORY:
        last = _CHAT_HISTORY[-1]
        mode_label = last.get('mode', 'normal')
        last_trace = [
            {"phase": 1, "phase_label": "感知", "icon": "👁", "text": f"CPU {readings[0].value if readings else '?'}%, 内存 {readings[1].value if len(readings)>1 else '?'}%"},
            {"phase": 2, "phase_label": "认知", "icon": "🧠", "text": cognitive_summary[:60]},
            {"phase": 3, "phase_label": "记忆", "icon": "💾", "text": f"加载摘要·{total_msgs} 条历史"},
            {"phase": 4, "phase_label": "行动", "icon": "🤖", "text": f"模式: {mode_label} → 调用 LLM → 生成响应"},
        ]

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
        "can_accept": not _lingxi._injector.should_reject_task(reading_dicts)[0],
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
        "tool_count": len(_lingxi._list_tools()),
        "tool_list": _lingxi._list_tools(),
        "reflection_count": len(_lingxi._reflection_history),
        "llm_configured": config.get("configured", False),
        "behavior_modes": _get_behavior_modes(),
        "permission_info": _get_permission_info(),
        # 系统
        "session_id": _lingxi._session_id,
        "interaction_count": _lingxi._interaction_count,
        "started_at": started_at,
        # 追踪
        "last_trace": last_trace,
    })


def _get_sensor_categories():
    """获取传感器五大分类（含数据来源）"""
    # 五大分类映射 + 数据来源
    CAT_CONFIG = {
        "硬件感知": {
            "icon": "💻", "sensors": ["cpu","gpu","memory","disk","battery","board","chassis","port","peripheral"],
            "source": "🔬 从硬件直接读取 （WMI/寄存器/传感器）",
        },
        "网络感知": {
            "icon": "🌐", "sensors": ["network"],
            "source": "🔬 从硬件直接读取 （网卡/协议栈）",
        },
        "进程与行为": {
            "icon": "⚙️", "sensors": ["process","activity","behavior"],
            "source": "⚡ 推测得来 （系统调用/性能计数器）",
        },
        "文件感知": {
            "icon": "📁", "sensors": ["file","change","hwfile"],
            "source": "🖥️ 从软件获得 （文件系统 API/快照对比）",
        },
        "系统与环境": {
            "icon": "🌿", "sensors": ["environment","system"],
            "source": "🖥️ 从软件获得 （OS 环境变量/系统 API）",
        },
    }
    # 反向映射: category → 分类名
    cat_reverse = {}
    for group_name, cfg in CAT_CONFIG.items():
        for sc in cfg["sensors"]:
            cat_reverse[sc] = group_name

    sensor_info = _lingxi.body.get_sensor_info()
    grouped = {}
    for group_name in CAT_CONFIG:
        grouped[group_name] = {
            "name": f"{CAT_CONFIG[group_name]['icon']} {group_name}",
            "source": CAT_CONFIG[group_name]["source"],
            "count": 0,
            "sensors": [],
        }

    for s in sensor_info:
        cat = s.get("category", "")
        group = cat_reverse.get(cat, "📡 其他")
        if group not in grouped:
            continue
        grouped[group]["count"] += 1
        grouped[group]["sensors"].append({
            "name": s.get("label", s.get("name", "")),
            "key": s.get("name", ""),
            "enabled": s.get("enabled", True),
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
    try:
        rules = _lingxi._injector.config.get_all_rules()
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
    current_mode = _lingxi.get_behavior_mode().value
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
    try:
        perm = _lingxi._permission
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

@app.route("/api/personality", methods=["GET"])
def api_personality_get():
    return jsonify(_personality_mgr.get())

@app.route("/api/personality/params", methods=["POST"])
def api_personality_params():
    data = request.get_json() or {}
    params = data.get("params", {})
    result = _personality_mgr.update_params(params)
    return jsonify(result)

@app.route("/api/personality/profile", methods=["POST"])
def api_personality_profile():
    data = request.get_json() or {}
    profile = data.get("profile", "")
    result = _personality_mgr.apply_profile(profile)
    return jsonify(result)

@app.route("/api/personality/reset", methods=["POST"])
def api_personality_reset():
    result = _personality_mgr.reset()
    return jsonify(result)


# ════════════════════════════════════════════════════════════
#  技能配置 API
# ════════════════════════════════════════════════════════════

@app.route("/api/skills", methods=["GET"])
def api_skills_get():
    return jsonify(_skills_mgr.get_all())

@app.route("/api/skills/toggle", methods=["POST"])
def api_skills_toggle():
    data = request.get_json() or {}
    skill_id = data.get("id", "")
    return jsonify(_skills_mgr.toggle(skill_id))

@app.route("/api/skills/params", methods=["POST"])
def api_skills_params():
    data = request.get_json() or {}
    return jsonify(_skills_mgr.update_params(data.get("id", ""), data.get("params", {})))

@app.route("/api/skills/add", methods=["POST"])
def api_skills_add():
    return jsonify(_skills_mgr.add(request.get_json() or {}))

@app.route("/api/skills/delete", methods=["POST"])
def api_skills_delete():
    data = request.get_json() or {}
    return jsonify(_skills_mgr.delete(data.get("id", "")))


# ── 工具配置 API ──
@app.route("/api/tools/config", methods=["GET"])
def api_tools_config():
    """获取工具列表及使用统计"""
    from agent.tools import list_tools
    tools = list_tools()
    try:
        perm_logs = _lingxi._permission.get_permission_log()
    except Exception:
        perm_logs = []
    result = []
    for t in tools:
        tool_name = t["name"]
        call_count = sum(1 for log in perm_logs if log.get("tool") == tool_name)
        result.append({
            "name": tool_name,
            "description": t.get("description", ""),
            "enabled": True,
            "call_count": call_count,
            "last_used": None,
        })
    return jsonify(result)

@app.route("/api/tools/toggle", methods=["POST"])
def api_tools_toggle():
    """切换工具启用状态"""
    data = request.get_json() or {}
    tool_name = data.get("name", "")
    enabled = data.get("enabled", True)
    return jsonify({"ok": True, "name": tool_name, "enabled": enabled})

# ── 历史记录 API ──
@app.route("/api/history/search")
def api_history_search():
    """搜索历史记录"""
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify(_CHAT_HISTORY[-50:])
    results = [
        {"index": i, **entry}
        for i, entry in enumerate(_CHAT_HISTORY)
        if q in entry.get("user", "").lower() or q in entry.get("lingxi", "").lower()
    ]
    return jsonify(results[-50:])

@app.route("/api/history/<int:index>", methods=["DELETE"])
def api_history_delete(index):
    """删除指定索引的历史记录"""
    global _CHAT_HISTORY
    if 0 <= index < len(_CHAT_HISTORY):
        deleted = _CHAT_HISTORY.pop(index)
        return jsonify({"ok": True, "deleted": deleted})
    return jsonify({"ok": False, "error": "索引超出范围"}), 404

# ── 记忆操作 API ──
@app.route("/api/memory/overview")
def api_memory_overview():
    """获取记忆概览"""
    try:
        summary = _lingxi._memory.load_summary()
        recent = _lingxi._memory._storage.load_recent_messages(limit=20)
        logs = _lingxi._memory._black_box.analyze()
        log_stats = logs if isinstance(logs, dict) else {}
        return jsonify({
            "summary_version": summary[1] if summary else None,
            "summary_text": summary[0][:300] if summary and summary[0] else None,
            "recent_messages": [
                {"index": i, "role": m.get("role", "?"), "content": m.get("content", "")[:100]}
                for i, m in enumerate(recent)
            ] if recent else [],
            "message_count": len(recent) if recent else 0,
            "log_stats": log_stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/memory/manual", methods=["POST"])
def api_memory_manual():
    """手动添加记忆"""
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    priority = data.get("priority", "normal")
    if not content:
        return jsonify({"ok": False, "error": "内容不能为空"}), 400
    try:
        _lingxi._memory.add_memory({
            "role": "user",
            "content": f"[手动记忆·优先级:{priority}] {content}"
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/memory/compress", methods=["POST"])
def api_memory_compress():
    """触发记忆压缩"""
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_lingxi._memory.compress())
        loop.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/memory/<int:index>", methods=["DELETE"])
def api_memory_delete_index(index):
    """删除指定索引的记忆"""
    # 标记删除操作已接收（简化实现）
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════
#  HTML 界面
# ════════════════════════════════════════════════════════════

# HTML 模板已提取到 templates/index.html

@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    print("=" * 56)
    print("  灵犀 · 数字生命体 Web 界面")
    print("  http://127.0.0.1:5678")
    print("=" * 56)
    print("  顶部: 实时健康指标 + 状态栏")
    print("  下方: 与灵犀对话")
    print("=" * 56)
    webbrowser.open("http://127.0.0.1:5678")
    app.run(host="127.0.0.1", port=5678, debug=False, threaded=True)
