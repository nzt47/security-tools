"""
云枢服务器 UI 管理模块

从 app_server.py 提取，包含服务器端 UI 管理类：
- PersonalityManager: 人格配置管理
- SkillsManager: 技能配置管理
- ActionTracker: 实时操作追踪器
"""

import os
import json
import logging
import threading
import datetime

logger = logging.getLogger(__name__)

# ── 数据文件路径 ──
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_PERSONALITY_FILE = os.path.join(_DATA_DIR, 'personality.json')
_SKILLS_FILE = os.path.join(_DATA_DIR, 'skills.json')
_TOOLS_CONFIG_FILE = os.path.join(_DATA_DIR, 'tools_config.json')


# ════════════════════════════════════════════════════════════
# 人格配置管理
# ════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════
# 工具状态管理
# ════════════════════════════════════════════════════════════

def load_tool_states() -> dict:
    """加载工具启用状态"""
    try:
        with open(_TOOLS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tool_states": {}}


def save_tool_states(data: dict):
    """保存工具启用状态"""
    with open(_TOOLS_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_tool_state(name: str) -> bool:
    """获取单个工具的启用状态，默认启用"""
    data = load_tool_states()
    return data.get("tool_states", {}).get(name, True)


def set_tool_state(name: str, enabled: bool):
    """设置单个工具的启用状态"""
    data = load_tool_states()
    data.setdefault("tool_states", {})[name] = enabled
    save_tool_states(data)


def get_enabled_tool_names() -> list | None:
    """获取所有已启用的工具名称列表，没有配置文件时返回 None（全部启用）"""
    data = load_tool_states()
    states = data.get("tool_states", {})
    if not states:
        return None
    enabled = [name for name, e in states.items() if e]
    return enabled if enabled else []


# ════════════════════════════════════════════════════════════
# 技能配置管理
# ════════════════════════════════════════════════════════════

class SkillsManager:
    """管理云枢的技能配置"""

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


# ════════════════════════════════════════════════════════════
# 实时操作追踪器
# ════════════════════════════════════════════════════════════

class ActionTracker:
    """实时操作追踪器 — 记录智能体正在做什么、做过什么"""

    def __init__(self, max_history=100):
        self._current_action = None
        self._action_history = []
        self._access_log = []
        self._emergency_state = {
            "paused": False,
            "stopped": False,
            "network_blocked": False,
        }
        self._max_history = max_history
        self._lock = threading.Lock()

    def start_action(self, tool: str, params: dict = None, target: str = ""):
        """开始追踪一个操作（自动完成前一个未完成的操作）"""
        with self._lock:
            if self._current_action and self._current_action["status"] == "running":
                start = datetime.datetime.fromisoformat(self._current_action["start_time"])
                elapsed = (datetime.datetime.now() - start).total_seconds()
                self._current_action["status"] = "interrupted"
                self._current_action["elapsed"] = round(elapsed, 2)
                self._current_action["result"] = "被新操作中断"
                self._action_history.append(dict(self._current_action))
                if len(self._action_history) > self._max_history:
                    self._action_history = self._action_history[-self._max_history:]

            self._current_action = {
                "tool": tool,
                "params": params or {},
                "target": target,
                "start_time": datetime.datetime.now().isoformat(),
                "status": "running",
                "elapsed": 0,
            }
        return self._current_action

    def finish_action(self, status="completed", result: str = ""):
        """完成当前操作"""
        with self._lock:
            if self._current_action:
                start = datetime.datetime.fromisoformat(self._current_action["start_time"])
                elapsed = (datetime.datetime.now() - start).total_seconds()
                self._current_action["status"] = status
                self._current_action["elapsed"] = round(elapsed, 2)
                self._current_action["result"] = result[:200]
                self._action_history.append(dict(self._current_action))
                if len(self._action_history) > self._max_history:
                    self._action_history = self._action_history[-self._max_history:]
                old = self._current_action
                self._current_action = None
                return old
        return None

    def log_access(self, access_type: str, target: str, detail: str = "",
                   permission: str = "allowed", duration: float = 0):
        """记录一次数据访问"""
        entry = {
            "time": datetime.datetime.now().isoformat(),
            "type": access_type,
            "target": target,
            "detail": detail,
            "permission": permission,
            "duration": round(duration, 2),
        }
        with self._lock:
            self._access_log.append(entry)
            if len(self._access_log) > self._max_history * 2:
                self._access_log = self._access_log[-self._max_history * 2:]
        return entry

    def get_status(self) -> dict:
        """获取当前状态（供前端轮询）"""
        with self._lock:
            current = None
            if self._current_action:
                start = datetime.datetime.fromisoformat(self._current_action["start_time"])
                elapsed = (datetime.datetime.now() - start).total_seconds()
                current = dict(self._current_action)
                current["elapsed"] = round(elapsed, 2)

            return {
                "current_action": current,
                "emergency": dict(self._emergency_state),
                "action_count": len(self._action_history),
                "access_count": len(self._access_log),
            }

    def get_access_log(self, limit=20, type_filter=None) -> list:
        """获取数据访问记录"""
        with self._lock:
            logs = list(self._access_log)
        if type_filter:
            logs = [l for l in logs if l["type"] == type_filter]
        return logs[-limit:]

    def get_action_history(self, limit=20) -> list:
        """获取操作历史"""
        with self._lock:
            return list(self._action_history[-limit:])

    def emergency_stop(self):
        """紧急停止"""
        with self._lock:
            self._emergency_state["stopped"] = True
            self._current_action = None
        logger.warning("🚨 紧急停止已触发")
        return True

    def emergency_pause(self):
        """暂停智能体"""
        with self._lock:
            self._emergency_state["paused"] = not self._emergency_state["paused"]
        state = "已暂停" if self._emergency_state["paused"] else "已恢复"
        logger.info(f"⏸ 智能体{state}")
        return self._emergency_state["paused"]

    def toggle_network_block(self):
        """切换网络封锁"""
        with self._lock:
            self._emergency_state["network_blocked"] = not self._emergency_state["network_blocked"]
        state = "已封锁" if self._emergency_state["network_blocked"] else "已解除"
        logger.info(f"🔌 网络{state}")
        return self._emergency_state["network_blocked"]

    def reset(self):
        """重置所有状态"""
        with self._lock:
            self._current_action = None
            self._emergency_state = {"paused": False, "stopped": False, "network_blocked": False}
        logger.info("🔄 操作追踪器已重置")
        return True


__all__ = [
    "PersonalityManager",
    "SkillsManager",
    "ActionTracker",
    "load_tool_states",
    "save_tool_states",
    "get_tool_state",
    "set_tool_state",
    "get_enabled_tool_names",
]
