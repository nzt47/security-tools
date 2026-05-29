"""
安全守护模块 -- 危险操作检测与防护

我是灵犀的"免疫系统"——在用户或我自己执行危险操作之前发出警报。
"""
import re
import json
import os
import logging

logger = logging.getLogger(__name__)

# 默认危险词库路径
_DEFAULT_KEYWORDS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "dangerous_commands.json")

# 告警回调列表
_alert_callbacks = []


class SafetyGuard:
    """安全守护器 -- 检测危险操作并触发告警"""

    def __init__(self, keywords_path=None):
        self._keywords_path = keywords_path or _DEFAULT_KEYWORDS_PATH
        self._keywords = self._load_keywords()
        self._alert_history = []  # 最近的告警记录
        self._max_alerts = 200
        self._blocked_count = 0
        self._warned_count = 0

    def _load_keywords(self):
        """加载危险关键词库"""
        try:
            with open(self._keywords_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info(f"安全词库已加载: {len(data.get('critical',[]))} 条严重 + {len(data.get('warning',[]))} 条警告")
                return data
        except Exception as e:
            logger.warning(f"加载安全词库失败: {e}，使用内置规则")
            return {"critical": [], "warning": []}

    def reload(self):
        """重新加载关键词库"""
        self._keywords = self._load_keywords()

    def check(self, text):
        """检查文本中是否包含危险操作关键词

        Returns:
            dict: {
                "safe": bool,
                "level": "safe" | "warning" | "critical",
                "matches": [{"pattern": "...", "description": "...", "category": "...", "level": "..."}]
            }
        """
        if not text:
            return {"safe": True, "level": "safe", "matches": []}

        matches = []

        for rule in self._keywords.get("critical", []):
            try:
                if re.search(rule["pattern"], text, re.IGNORECASE):
                    matches.append({
                        "pattern": rule["pattern"],
                        "description": rule["description"],
                        "category": rule.get("category", ""),
                        "level": "critical",
                    })
            except re.error:
                pass

        for rule in self._keywords.get("warning", []):
            try:
                if re.search(rule["pattern"], text, re.IGNORECASE):
                    matches.append({
                        "pattern": rule["pattern"],
                        "description": rule["description"],
                        "category": rule.get("category", ""),
                        "level": "warning",
                    })
            except re.error:
                pass

        level = "safe"
        if any(m["level"] == "critical" for m in matches):
            level = "critical"
            self._blocked_count += 1
        elif matches:
            level = "warning"
            self._warned_count += 1

        result = {
            "safe": level == "safe",
            "level": level,
            "matches": matches,
        }

        # 记录告警
        if not result["safe"]:
            self._record_alert(text, result)

        return result

    def _record_alert(self, text, result):
        """记录告警到历史"""
        import datetime
        alert = {
            "timestamp": datetime.datetime.now().isoformat(),
            "text": text[:200],
            "level": result["level"],
            "match_count": len(result["matches"]),
            "categories": list(set(m["category"] for m in result["matches"])),
        }
        self._alert_history.append(alert)
        if len(self._alert_history) > self._max_alerts:
            self._alert_history = self._alert_history[-self._max_alerts:]

        # 触发回调通知
        for callback in _alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.debug(f"告警回调失败: {e}")

    def get_alerts(self, limit=50):
        """获取最近告警记录"""
        return self._alert_history[-limit:]

    def get_stats(self):
        """获取统计信息"""
        return {
            "blocked_count": self._blocked_count,
            "warned_count": self._warned_count,
            "total_alerts": len(self._alert_history),
            "keywords_loaded": {
                "critical": len(self._keywords.get("critical", [])),
                "warning": len(self._keywords.get("warning", [])),
            },
        }

    def add_keyword(self, pattern, description, level="warning", category=""):
        """动态添加关键词"""
        entry = {"pattern": pattern, "description": description, "category": category}
        if level == "critical":
            self._keywords.setdefault("critical", []).append(entry)
        else:
            self._keywords.setdefault("warning", []).append(entry)


# 注册全局告警回调
def register_alert_callback(callback):
    """注册告警回调函数。回调接收一个 alert dict 参数。"""
    _alert_callbacks.append(callback)


# 全局单例
_safety_guard = None


def get_safety_guard():
    """获取全局安全守护实例"""
    global _safety_guard
    if _safety_guard is None:
        _safety_guard = SafetyGuard()
    return _safety_guard
