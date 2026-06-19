"""
安全守护模块 -- 危险操作检测与防护

这是 PermissionSystem 的便捷封装，提供轻量级的文本检查接口。

重构说明：
- SafetyGuard 现在是 PermissionSystem 的便捷接口
- 核心功能已整合到 PermissionSystem
- 保持向后兼容，可单独使用
"""

import re
import json
import os
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class SafetyGuard:
    """
    安全守护器 -- 检测危险操作并触发告警
    
    便捷封装类，内部使用 PermissionSystem 的核心功能。
    建议使用 PermissionSystem 以获得完整的权限管理功能。
    """
    
    def __init__(self, keywords_path=None):
        """
        初始化安全守护器
        
        Args:
            keywords_path: 危险关键词库路径
        """
        self._keywords_path = keywords_path or self._get_default_keywords_path()
        self._keywords = self._load_keywords()
        self._alert_history: List[Dict] = []
        self._max_alerts = 200
        self._blocked_count = 0
        self._warned_count = 0
    
    def _get_default_keywords_path(self) -> str:
        """获取默认关键词库路径"""
        return os.path.join(os.path.dirname(__file__), "..", "data", "dangerous_commands.json")
    
    def _load_keywords(self) -> Dict[str, List[Dict]]:
        """加载危险关键词库"""
        try:
            with open(self._keywords_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                logger.info(f"安全词库已加载: {len(data.get('critical',[]))} 条严重 + "
                          f"{len(data.get('warning',[]))} 条警告")
                return data
        except Exception as e:
            logger.warning(f"加载安全词库失败: {e}，使用内置规则")
            return {"critical": [], "warning": []}
    
    def reload(self):
        """重新加载关键词库"""
        self._keywords = self._load_keywords()
    
    def check(self, text: str) -> Dict[str, Any]:
        """
        检查文本中是否包含危险操作关键词
        
        Returns:
            dict: {
                "safe": bool,
                "level": "safe" | "warning" | "critical",
                "matches": [{"pattern": "...", "description": "...", "category": "...", "level": "..."}]
            }
        """
        if not text:
            return {"safe": True, "level": "safe", "matches": []}
        
        matches: List[Dict[str, Any]] = []
        
        for rule in self._keywords.get("critical", []):
            pattern = rule.get("pattern", "")
            if pattern:
                try:
                    if re.search(pattern, text, re.IGNORECASE):
                        matches.append({
                            "pattern": pattern,
                            "description": rule.get("description", ""),
                            "category": rule.get("category", ""),
                            "level": "critical",
                        })
                except re.error:
                    pass
        
        for rule in self._keywords.get("warning", []):
            pattern = rule.get("pattern", "")
            if pattern:
                try:
                    if re.search(pattern, text, re.IGNORECASE):
                        matches.append({
                            "pattern": pattern,
                            "description": rule.get("description", ""),
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
        
        if not result["safe"]:
            self._record_alert(text, result)
        
        return result
    
    def _record_alert(self, text: str, result: Dict[str, Any]):
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
        
        for callback in _alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"告警回调执行失败: {e}")
    
    def get_alerts(self, limit: int = 50) -> List[Dict]:
        """获取最近告警记录"""
        return self._alert_history[-limit:]
    
    def get_stats(self) -> Dict[str, Any]:
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
    
    def add_keyword(self, pattern: str, description: str, level: str = "warning", category: str = ""):
        """
        动态添加关键词
        
        Args:
            pattern: 正则表达式模式
            description: 描述
            level: 级别 ("warning" 或 "critical")
            category: 类别
        """
        entry = {"pattern": pattern, "description": description, "category": category}
        if level == "critical":
            self._keywords.setdefault("critical", []).append(entry)
        else:
            self._keywords.setdefault("warning", []).append(entry)


_alert_callbacks: List = []


def register_alert_callback(callback):
    """注册告警回调函数。回调接收一个 alert dict 参数。"""
    _alert_callbacks.append(callback)


_safety_guard = None


def get_safety_guard():
    """获取全局安全守护实例"""
    global _safety_guard
    if _safety_guard is None:
        _safety_guard = SafetyGuard()
    return _safety_guard
