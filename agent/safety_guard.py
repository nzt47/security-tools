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
import threading
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
        # Why RLock 保护实例级共享状态（单例 get_safety_guard 被多线程调用）：
        # check 的 _blocked_count/_warned_count 为「读-改-写」序列（并发丢计数），
        # _record_alert 的 _alert_history.append+截断 与 get_alerts 遍历并发有结构
        # 风险。锁内仅内存计数/列表变更与 re.search（纯 CPU）；外部回调派发在锁外
        # （持锁纪律：锁内严禁外部回调——回调可能阻塞）。
        self._lock = threading.RLock()
    
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
            with self._lock:
                self._blocked_count += 1
        elif matches:
            level = "warning"
            with self._lock:
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
        with self._lock:
            self._alert_history.append(alert)
            if len(self._alert_history) > self._max_alerts:
                self._alert_history = self._alert_history[-self._max_alerts:]
            # 锁内仅取回调快照（模块级 list 的 append 与迭代并发会 RuntimeError）
            callbacks = list(_alert_callbacks)
        # 回调在锁外派发（持锁纪律：外部回调可能阻塞，不能持锁调用）
        for callback in callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"告警回调执行失败: {e}")
    
    def get_alerts(self, limit: int = 50) -> List[Dict]:
        """获取最近告警记录"""
        with self._lock:
            return self._alert_history[-limit:]
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
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
        with self._lock:
            if level == "critical":
                self._keywords.setdefault("critical", []).append(entry)
            else:
                self._keywords.setdefault("warning", []).append(entry)


_alert_callbacks: List = []
# Why Lock 保护模块级回调列表：register_alert_callback（任意线程可注册）与
# _record_alert（告警线程）的 append/迭代并发会 RuntimeError（list changed size
# during iteration）。锁内仅 list 变更/快照拷贝，回调派发在锁外（持锁纪律）。
_alert_callbacks_lock = threading.Lock()


def register_alert_callback(callback):
    """注册告警回调函数。回调接收一个 alert dict 参数。"""
    with _alert_callbacks_lock:
        _alert_callbacks.append(callback)


_safety_guard = None  # 保留作为 fallback

try:
    from agent.utils.singleton_manager import register_singleton, get_singleton, reset_singleton
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None
    reset_singleton = None


def _create_safety_guard(config=None):
    """SafetyGuard 工厂函数（供 SingletonManager 使用）"""
    return SafetyGuard()


def get_safety_guard():
    """获取全局安全守护实例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("safety_guard")
    global _safety_guard
    if _safety_guard is None:
        _safety_guard = _create_safety_guard()
    return _safety_guard


def reset_safety_guard():
    """重置全局安全守护实例（仅用于测试）"""
    global _safety_guard
    if _SINGLETON_AVAILABLE:
        reset_singleton("safety_guard")
    _safety_guard = None


if _SINGLETON_AVAILABLE:
    register_singleton("safety_guard", _create_safety_guard)
