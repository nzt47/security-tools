"""PermissionSystem — 权限边界系统

我是云枢的"道德防线"和"安全护栏"——防止我在异常状态下做出危险操作。
危险操作需要二次确认，黑名单操作直接禁止，操作前自动备份重要文件。

防止 Agent 在"精神错乱"时造成破坏：
- 危险操作（删除系统文件、修改系统配置等）必须二次确认
- 设置操作黑名单
- 操作前备份重要文件
- 危险关键词检测（整合自 SafetyGuard）
"""

import re
import logging
import shutil
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class PermissionResult:
    """权限检查结果"""
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False
    backup_path: str = ""


class PermissionSystem:
    """权限边界系统——我的安全护栏

    当我处于异常状态时，权限系统会更加严格。
    危险操作必须经过"深思熟虑"才能执行。
    """

    # ── 危险操作模式 ──
    # 这些操作本身就有破坏性，需要二次确认
    DANGEROUS_PATTERNS = [
        # 文件删除
        re.compile(r"rm\s+-[rf].*", re.IGNORECASE),
        re.compile(r"deltree|rd\s+/[sq].*", re.IGNORECASE),
        # 格式化/重置
        re.compile(r"\bformat\b", re.IGNORECASE),
        re.compile(r"\b重置\b|\b恢复出厂\b", re.IGNORECASE),
        re.compile(r"diskpart", re.IGNORECASE),
        # 系统修改
        re.compile(r"\breboot\b|\bshutdown\b", re.IGNORECASE),
        re.compile(r"\b关机\b|\b重启\b|\b注销\b", re.IGNORECASE),
        # 注册表修改（Windows）
        re.compile(r"reg\s+(delete|add|copy)", re.IGNORECASE),
        # 权限修改
        re.compile(r"chmod\s+777", re.IGNORECASE),
        re.compile(r"chown\s", re.IGNORECASE),
        # 文件覆盖
        re.compile(r">\s+/dev/sd", re.IGNORECASE),
        re.compile(r"dd\s+if=.*of=/dev/sd", re.IGNORECASE),
    ]

    # ── 黑名单操作 ──
    # 这些操作直接禁止，永不允许
    BLACKLIST = [
        re.compile(r"format\s+[c-zC-Z]:\s*/[fsq]", re.IGNORECASE),
        re.compile(r"format\s+[c-zC-Z]:\\\\", re.IGNORECASE),
        re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
        re.compile(r"dd\s+if=.*of=/dev/sda", re.IGNORECASE),
        re.compile(r">\s+/dev/sda", re.IGNORECASE),
        re.compile(r":\(\)\s*\{.*:\(\)\s*\{", re.IGNORECASE),  # Fork 炸弹
    ]

    # ── 敏感文件扩展名 ──
    # 操作这些文件时需要额外小心
    SENSITIVE_EXTENSIONS = {
        ".exe", ".dll", ".sys", ".bin", ".bat", ".cmd",
        ".reg", ".msi", ".ps1", ".vbs", ".scr",
        ".conf", ".config", ".ini",
    }

    # ── 敏感目录 ──
    SENSITIVE_DIRS = [
        "C:\\Windows", "C:\\System32", "C:\\Program Files",
        "/etc", "/usr/lib", "/boot", "/bin", "/sbin",
    ]

    # ── 危险关键词库（整合自 SafetyGuard）─
    # 分为 critical（阻止）和 warning（警告）两级
    DANGEROUS_KEYWORDS = {
        "critical": [
            {"pattern": r"rm\s+-rf\s+/", "description": "递归删除根目录", "category": "文件系统"},
            {"pattern": r"format\s+[c-zC-Z]:\s*/[fsq]", "description": "格式化系统盘", "category": "磁盘操作"},
            {"pattern": r"dd\s+if=.*of=/dev/sd", "description": "直接写入磁盘设备", "category": "磁盘操作"},
            {"pattern": r":\(\)\s*\{\s*:.*\|.*:.*&\s*\}\s*;", "description": "Fork炸弹", "category": "恶意代码"},
        ],
        "warning": [
            {"pattern": r"rm\s+-[rf]", "description": "递归删除操作", "category": "文件系统"},
            {"pattern": r"\bformat\b", "description": "格式化操作", "category": "磁盘操作"},
            {"pattern": r"\breboot\b|\bshutdown\b", "description": "系统关机重启", "category": "系统控制"},
            {"pattern": r"reg\s+(delete|add)", "description": "注册表修改", "category": "系统配置"},
            {"pattern": r"chmod\s+777", "description": "过度开放权限", "category": "权限设置"},
        ]
    }

    def __init__(self, backup_dir: str = "./.backups", keywords_path: str = None):
        self._backup_dir = Path(backup_dir)
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._permission_log: list[dict] = []
        
        # 整合 SafetyGuard 功能
        self._keywords_path = keywords_path
        self._loaded_keywords = self._load_keywords()
        self._alert_history: list[dict] = []
        self._blocked_count = 0
        self._warned_count = 0
        
        logger.info(f"权限系统初始化，备份目录: {backup_dir}")
        logger.info(f"危险词库: {len(self._loaded_keywords.get('critical', []))} 条严重 + "
                   f"{len(self._loaded_keywords.get('warning', []))} 条警告")

    def check_action(self, action: str, context: str = "") -> PermissionResult:
        """检查操作是否允许执行

        三步检查法：
        1. 黑名单检查 → 直接禁止
        2. 危险模式检查 → 需要二次确认
        3. 敏感路径检查 → 需要二次确认

        Args:
            action: 要执行的操作描述或命令
            context: 操作的上下文说明（可选）

        Returns:
            PermissionResult: 检查结果
        """
        # 1. 黑名单检查
        for pattern in self.BLACKLIST:
            if pattern.search(action):
                result = PermissionResult(
                    allowed=False,
                    reason=f"操作已被列入黑名单，禁止执行。匹配规则: {pattern.pattern}",
                )
                self._log_permission(action, result, context)
                return result

        # 2. 危险模式检查
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern.search(action):
                result = PermissionResult(
                    allowed=True,
                    reason=f"危险操作，需要二次确认。匹配规则: {pattern.pattern}",
                    requires_confirmation=True,
                )
                self._log_permission(action, result, context)
                return result

        # 3. 敏感路径检查
        for sensitive_dir in self.SENSITIVE_DIRS:
            if sensitive_dir.lower() in action.lower():
                result = PermissionResult(
                    allowed=True,
                    reason=f"操作涉及敏感路径 {sensitive_dir}，需要二次确认",
                    requires_confirmation=True,
                )
                self._log_permission(action, result, context)
                return result

        # 4. 敏感文件检查
        for ext in self.SENSITIVE_EXTENSIONS:
            if ext in action.lower():
                result = PermissionResult(
                    allowed=True,
                    reason=f"操作涉及敏感文件类型 ({ext})，需要二次确认",
                    requires_confirmation=True,
                )
                self._log_permission(action, result, context)
                return result

        return PermissionResult(allowed=True)

    def confirm_action(self, action_id: str) -> bool:
        """确认一个需要二次确认的操作

        查找最近的待确认操作并标记为已确认。

        Args:
            action_id: 操作 ID（来自日志）

        Returns:
            是否确认成功
        """
        for entry in self._permission_log:
            if entry.get("id") == action_id and entry.get("requires_confirmation"):
                entry["confirmed"] = True
                logger.info(f"操作已确认: {action_id} — {entry['action'][:100]}")
                return True
        logger.warning(f"未找到待确认操作: {action_id}")
        return False

    def backup_file(self, file_path: str) -> Optional[str]:
        """操作前备份文件

        在执行可能修改文件的操作前，自动创建备份。

        Args:
            file_path: 要备份的文件路径

        Returns:
            备份文件路径，失败返回 None
        """
        src = Path(file_path)
        if not src.exists():
            logger.warning(f"备份失败: 文件不存在 — {file_path}")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{src.name}.{timestamp}.bak"
        dst = self._backup_dir / backup_name

        try:
            shutil.copy2(src, dst)
            logger.info(f"已备份: {file_path} → {dst}")
            return str(dst)
        except Exception as e:
            logger.error(f"备份失败: {file_path} — {e}")
            return None

    def get_permission_log(self, limit: int = 50) -> list[dict]:
        """获取权限检查历史"""
        return self._permission_log[-limit:]

    def is_sensitive_path(self, path: str) -> bool:
        """检查路径是否属于敏感系统路径"""
        path_lower = path.lower()
        for sensitive_dir in self.SENSITIVE_DIRS:
            if path_lower.startswith(sensitive_dir.lower()):
                return True
        return False

    def _log_permission(self, action: str, result: PermissionResult, context: str):
        """记录权限检查日志"""
        entry = {
            "id": f"perm_{len(self._permission_log) + 1:04d}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action[:200],
            "context": context[:200],
            "allowed": result.allowed,
            "reason": result.reason,
            "requires_confirmation": result.requires_confirmation,
            "confirmed": False if result.requires_confirmation else True,
        }
        self._permission_log.append(entry)
        logger.info(
            f"权限检查: {'✓' if result.allowed else '✗'} {action[:80]}"
            f" — {result.reason}" if result.reason else ""
        )
    
    # ════════════════════════════════════════════════════════════
    #  SafetyGuard 功能整合
    # ════════════════════════════════════════════════════════════
    
    def _load_keywords(self) -> Dict[str, List[Dict]]:
        """加载危险关键词库"""
        import json
        import os
        
        if self._keywords_path and os.path.exists(self._keywords_path):
            try:
                with open(self._keywords_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data
            except Exception as e:
                logger.warning(f"加载危险词库失败: {e}")
        
        return self.DANGEROUS_KEYWORDS.copy()
    
    def check_text(self, text: str) -> Dict[str, Any]:
        """
        检查文本中是否包含危险关键词
        
        整合自 SafetyGuard 的 check 方法。
        
        Args:
            text: 要检查的文本
            
        Returns:
            dict: {
                "safe": bool,
                "level": "safe" | "warning" | "critical",
                "matches": [{"pattern": "...", "description": "...", "category": "..."}]
            }
        """
        if not text:
            return {"safe": True, "level": "safe", "matches": []}
        
        matches = []
        
        # 检查 critical 关键词
        for rule in self._loaded_keywords.get("critical", []):
            pattern = rule.get("pattern", "")
            if pattern and re.search(pattern, text, re.IGNORECASE):
                matches.append({
                    "pattern": pattern,
                    "description": rule.get("description", ""),
                    "category": rule.get("category", ""),
                    "level": "critical",
                })
        
        # 检查 warning 关键词
        for rule in self._loaded_keywords.get("warning", []):
            pattern = rule.get("pattern", "")
            if pattern and re.search(pattern, text, re.IGNORECASE):
                matches.append({
                    "pattern": pattern,
                    "description": rule.get("description", ""),
                    "category": rule.get("category", ""),
                    "level": "warning",
                })
        
        # 确定安全级别
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
    
    def _record_alert(self, text: str, result: Dict):
        """记录告警到历史"""
        alert = {
            "timestamp": datetime.now().isoformat(),
            "text": text[:200],
            "level": result["level"],
            "match_count": len(result["matches"]),
            "categories": list(set(m["category"] for m in result["matches"])),
        }
        self._alert_history.append(alert)
        if len(self._alert_history) > 200:
            self._alert_history = self._alert_history[-200:]
    
    def get_alerts(self, limit: int = 50) -> List[Dict]:
        """获取最近告警记录"""
        return self._alert_history[-limit:]
    
    def get_security_stats(self) -> Dict[str, Any]:
        """获取安全统计信息"""
        return {
            "blocked_count": self._blocked_count,
            "warned_count": self._warned_count,
            "total_alerts": len(self._alert_history),
            "keywords_loaded": {
                "critical": len(self._loaded_keywords.get("critical", [])),
                "warning": len(self._loaded_keywords.get("warning", [])),
            },
            "permission_checks": len(self._permission_log),
        }
