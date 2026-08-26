"""PermissionSystem — 权限边界系统

我是云枢的"道德防线"和"安全护栏"——防止我在异常状态下做出危险操作。
危险操作需要二次确认，黑名单操作直接禁止，操作前自动备份重要文件。

防止 Agent 在"精神错乱"时造成破坏：
- 危险操作（删除系统文件、修改系统配置等）必须二次确认
- 设置操作黑名单
- 操作前备份重要文件
- 危险关键词检测（整合自 SafetyGuard）

═══════════════════════════════════════════════════════════
 三层权限架构（RBAC + ABAC + 正则黑名单）
═══════════════════════════════════════════════════════════
调用链: PermissionGateway.check(tool_name, params, context)
        ↓
        [层1] RBAC  角色拦截 ——  Role 是否允许调用此工具
        ↓ (通过)
        [层2] ABAC  属性校验 ——  时间窗口 / 会话来源 / IP 段
        ↓ (通过)
        [层3] 正则黑名单兜底 ——  PermissionSystem.check_action
        ↓
        PermissionResult

不变量:
- PermissionSystem (含正则规则集) 作为最后兜底,不可弱化
- PermissionResult 数据结构保持兼容
- 策略文件加载失败 → 降级到"仅正则黑名单"模式 (self._degraded=True)
- RBAC/ABAC 拒绝统一返回 reason="权限不足",不向 LLM 暴露具体规则

日志格式:
- 所有 trace 日志输出为单行 JSON,可直接接入 ELK/Splunk
- 一次 check 调用共享同一 trace_id,贯穿入口/各层/出口
- 字段: ts / trace_id / event / tool / role / source / ip / allowed / reason / layer / duration_ms
═══════════════════════════════════════════════════════════
"""

import re
import json
import time
import uuid
import logging
import shutil
import threading
import ipaddress
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

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
        
        # Why RLock 保护权限日志/告警历史/计数器（模块级单例被多路请求并发调用）：
        # _blocked_count/_warned_count 的 += 为读-改-写序列（并发丢计数）；
        # _log_permission 的 len()+1 生成 id 为 TOCTOU（并发 id 重复）；
        # confirm_action 遍历 _permission_log 与并发 append 抛 RuntimeError；
        # _record_alert 的 append+截断重建读-改-写丢记录。锁内仅内存变更，
        # logger 与 backup_file 的文件 I/O 在锁外（持锁纪律）。
        self._lock = threading.RLock()

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

        result = PermissionResult(allowed=True)
        self._log_permission(action, result, context)
        return result

    def confirm_action(self, action_id: str) -> bool:
        """确认一个需要二次确认的操作

        查找最近的待确认操作并标记为已确认。

        Args:
            action_id: 操作 ID（来自日志）

        Returns:
            是否确认成功
        """
        confirmed = None
        with self._lock:  # 遍历与并发 append 互斥（防 list changed size RuntimeError）
            for entry in self._permission_log:
                if entry.get("id") == action_id and entry.get("requires_confirmation"):
                    entry["confirmed"] = True
                    confirmed = entry
                    break
        if confirmed:
            logger.info(f"操作已确认: {action_id} — {confirmed['action'][:100]}")
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
        with self._lock:  # 切片快照原子（与 _log_permission append 互斥）
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
        with self._lock:  # id 生成（len()+1）与 append 原子，防并发 id 重复
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
            with self._lock:  # blocked_count += 1 原子（读-改-写防丢计数）
                self._blocked_count += 1
        elif matches:
            level = "warning"
            with self._lock:  # warned_count += 1 原子
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
        with self._lock:  # append + 截断重建原子（读-改-写防丢告警）
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
        with self._lock:  # 切片快照原子（与 _record_alert 重建互斥）
            return self._alert_history[-limit:]
    
    def get_security_stats(self) -> Dict[str, Any]:
        """获取安全统计信息"""
        with self._lock:  # 计数/长度快照原子（一致视图）
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


# ════════════════════════════════════════════════════════════
#  三层权限架构扩展：RBAC + ABAC + 正则黑名单
# ════════════════════════════════════════════════════════════
# 设计原则:
#   - 不修改上方 PermissionSystem 类(纯扩展,不重写)
#   - PermissionGateway 组合 PermissionSystem 作为兜底层
#   - 策略文件加载失败时降级为"仅正则黑名单"模式
#   - RBAC/ABAC 拒绝统一返回 reason="权限不足",不向 LLM 暴露具体规则
#   - 所有 trace 日志输出为单行 JSON,可直接接入 ELK/Splunk
# ════════════════════════════════════════════════════════════


class Role(Enum):
    """用户角色枚举(RBAC)"""
    ADMIN = "admin"
    DEVELOPER = "developer"
    GUEST = "guest"


@dataclass
class Permission:
    """单个工具的权限规则描述(供策略层使用)"""
    tool_name: str
    allowed: bool = True
    requires_confirmation: bool = False
    description: str = ""


@dataclass
class ABACContext:
    """ABAC 属性上下文

    属性聚合:
    - role:           用户角色(同时供 RBAC 使用,避免双传)
    - session_source: 会话来源,枚举值 cli | web | api | scheduled
    - time_window:    会话允许时间窗口,None 表示不限制
    - ip:             客户端 IP 字符串,None 表示未提供
    """
    role: Role = Role.GUEST
    session_source: str = "cli"
    time_window: Optional[Tuple[str, str]] = None
    ip: Optional[str] = None


@dataclass
class _ABACRule:
    """ABAC 规则内部表示

    deny_if 支持以下条件(各自独立判定,任一命中即拒绝):
    - time_outside:      [start, end]  当前时间不在 [start, end] 内则拒绝
    - session_source_in: [src, ...]    当前会话来源命中列表则拒绝
    - ip_not_in_cidr:    [cidr, ...]   当前 IP 不在任一 CIDR 内则拒绝
    """
    name: str
    tool: str
    deny_if: Dict[str, Any]
    description: str = ""


class PermissionGateway:
    """三层权限网关

    调用顺序:
        check(tool_name, params, context)
          → [层1] RBAC:  角色是否允许调用此工具
          → [层2] ABAC:  属性上下文校验(时间/来源/IP)
          → [层3] 正则:  沿用 PermissionSystem.check_action 兜底

    降级模式:
        策略文件加载失败 → 跳过 RBAC/ABAC,仅走正则黑名单
        (self._degraded=True)

    日志格式:
        所有 trace 日志输出为单行 JSON,字段:
        ts / trace_id / event / tool / role / source / ip /
        allowed / reason / layer / duration_ms / rule_name / params

    不变量:
        - PermissionSystem 作为最后兜底,正则规则不可弱化
        - RBAC/ABAC 拒绝统一返回 reason="权限不足"
        - PermissionResult 数据结构保持兼容
    """

    DEFAULT_POLICY_PATH = "data/permission_policies.json"

    def __init__(
        self,
        policy_path: Optional[str] = None,
        permission_system: Optional[PermissionSystem] = None,
    ):
        self._ps = permission_system or PermissionSystem()
        self._policies: Dict[str, Dict[str, set]] = {}
        self._abac_rules: List[_ABACRule] = []
        self._default_role: Role = Role.GUEST
        self._degraded: bool = False

        path = policy_path or self.DEFAULT_POLICY_PATH
        if not self._load_policies(path):
            self._degraded = True
            self._log_json(
                "degraded_mode", {"path": path},
                level=logging.WARNING,
            )

    # ── 策略加载 ──────────────────────────────────────────────

    def _load_policies(self, path: str) -> bool:
        """加载 RBAC 角色策略 + ABAC 规则

        Returns:
            True 加载成功; False 加载失败(触发降级)
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._log_json(
                "policy_load_failed",
                {"path": path, "error": str(e)},
                level=logging.WARNING,
            )
            return False

        for role_name, role_cfg in data.get("roles", {}).items():
            try:
                role = Role(role_name.lower())
            except ValueError:
                self._log_json(
                    "unknown_role_skipped",
                    {"role_name": role_name},
                    level=logging.WARNING,
                )
                continue
            self._policies[role.value] = {
                "allowed_tools": set(role_cfg.get("allowed_tools", [])),
                "denied_tools": set(role_cfg.get("denied_tools", [])),
            }

        default_role_name = str(data.get("default_role", "guest")).lower()
        try:
            self._default_role = Role(default_role_name)
        except ValueError:
            self._default_role = Role.GUEST

        for rule in data.get("abac_rules", []):
            self._abac_rules.append(
                _ABACRule(
                    name=rule.get("name", ""),
                    tool=rule.get("tool", ""),
                    deny_if=rule.get("deny_if", {}),
                    description=rule.get("description", ""),
                )
            )

        self._log_json(
            "policy_loaded",
            {
                "path": path,
                "roles": len(self._policies),
                "abac_rules": len(self._abac_rules),
                "default_role": self._default_role.value,
            },
        )
        return True

    # ── 主入口 ───────────────────────────────────────────────

    def check(
        self,
        tool_name: str,
        params: Optional[dict] = None,
        context: Optional[ABACContext] = None,
    ) -> PermissionResult:
        """三层检查入口

        Args:
            tool_name: 工具名(如 shell_execute)
            params:    工具参数字典
            context:   ABAC 属性上下文,缺省用默认 GUEST

        Returns:
            PermissionResult: 三层叠加后的最终结果

        Trace 日志:
            一次 check 调用共享同一 trace_id,贯穿入口/各层/出口。
            所有日志为单行 JSON,grep `[trace=<id>]` 或在 ELK 中
            按 trace_id 字段过滤即可拿到完整决策链。
        """
        trace_id = uuid.uuid4().hex[:12]
        start_ts = time.perf_counter()

        params = params or {}
        if context is None:
            context = ABACContext()

        # 入参快照(params 值截断 50 字符避免日志爆炸)
        params_snapshot = self._snapshot_params(params)

        self._log_json(
            "check_entry",
            {
                "trace_id": trace_id,
                "tool": tool_name,
                "role": context.role.value,
                "source": context.session_source,
                "ip": context.ip,
                "degraded": self._degraded,
                "params": params_snapshot,
            },
        )

        # 降级模式: 跳过 RBAC/ABAC,仅走正则兜底
        if self._degraded:
            self._log_json(
                "degraded_skip_rbac_abac",
                {"trace_id": trace_id},
            )
            result = self._regex_fallback(tool_name, params, trace_id)
            self._log_decision(
                trace_id, start_ts, "REGEX_DEGRADED", tool_name, result
            )
            return result

        # [层1] RBAC 角色拦截
        rbac = self._check_rbac(tool_name, context.role, trace_id)
        if rbac is not None:
            self._log_json(
                "rbac_block",
                {
                    "trace_id": trace_id,
                    "tool": tool_name,
                    "role": context.role.value,
                    "allowed": rbac.allowed,
                    "reason": rbac.reason,
                },
            )
            self._log_decision(
                trace_id, start_ts, "RBAC", tool_name, rbac
            )
            return rbac
        self._log_json(
            "rbac_pass",
            {
                "trace_id": trace_id,
                "tool": tool_name,
                "role": context.role.value,
            },
            level=logging.DEBUG,
        )

        # [层2] ABAC 属性校验
        abac = self._check_abac(tool_name, context, trace_id)
        if abac is not None:
            self._log_json(
                "abac_block",
                {
                    "trace_id": trace_id,
                    "tool": tool_name,
                    "role": context.role.value,
                    "source": context.session_source,
                    "ip": context.ip,
                    "allowed": abac.allowed,
                    "reason": abac.reason,
                },
            )
            self._log_decision(
                trace_id, start_ts, "ABAC", tool_name, abac
            )
            return abac
        self._log_json(
            "abac_pass",
            {
                "trace_id": trace_id,
                "tool": tool_name,
                "role": context.role.value,
            },
            level=logging.DEBUG,
        )

        # [层3] 正则黑名单兜底
        self._log_json(
            "regex_entry",
            {"trace_id": trace_id, "tool": tool_name},
            level=logging.DEBUG,
        )
        result = self._regex_fallback(tool_name, params, trace_id)
        self._log_decision(
            trace_id, start_ts, "REGEX", tool_name, result
        )
        return result

    # ── 层1: RBAC ────────────────────────────────────────────

    def _check_rbac(
        self, tool_name: str, role: Role, trace_id: str = ""
    ) -> Optional[PermissionResult]:
        """RBAC 角色拦截

        Returns:
            None 表示通过; PermissionResult 表示已决策
        """
        policy = self._policies.get(role.value)
        if policy is None:
            self._log_json(
                "rbac_no_policy",
                {"trace_id": trace_id, "role": role.value},
                level=logging.DEBUG,
            )
            return PermissionResult(allowed=False, reason="权限不足")

        denied = policy["denied_tools"]
        if "*" in denied or tool_name in denied:
            self._log_json(
                "rbac_hit_denied",
                {
                    "trace_id": trace_id,
                    "role": role.value,
                    "tool": tool_name,
                },
                level=logging.DEBUG,
            )
            return PermissionResult(allowed=False, reason="权限不足")

        allowed = policy["allowed_tools"]
        if "*" not in allowed and tool_name not in allowed:
            self._log_json(
                "rbac_not_in_allowed",
                {
                    "trace_id": trace_id,
                    "role": role.value,
                    "tool": tool_name,
                    "allowed_tools": sorted(allowed),
                },
                level=logging.DEBUG,
            )
            return PermissionResult(allowed=False, reason="权限不足")

        return None

    # ── 层2: ABAC ────────────────────────────────────────────

    def _check_abac(
        self, tool_name: str, context: ABACContext, trace_id: str = ""
    ) -> Optional[PermissionResult]:
        """ABAC 属性校验

        遍历所有规则,任一 deny_if 条件命中则拒绝。
        规则 tool 字段支持 "*" 通配(对所有工具生效)。
        """
        for rule in self._abac_rules:
            if rule.tool != tool_name and rule.tool != "*":
                continue

            deny_if = rule.deny_if

            # time_outside: 时间窗口外拒绝
            if "time_outside" in deny_if:
                window = deny_if["time_outside"]
                in_window = self._time_in_window(window[0], window[1])
                if not in_window:
                    self._log_json(
                        "abac_hit_time_outside",
                        {
                            "trace_id": trace_id,
                            "rule_name": rule.name,
                            "tool": tool_name,
                            "window": [window[0], window[1]],
                        },
                        level=logging.DEBUG,
                    )
                    return PermissionResult(allowed=False, reason="权限不足")

            # session_source_in: 来源命中拒绝
            if "session_source_in" in deny_if:
                if context.session_source in deny_if["session_source_in"]:
                    self._log_json(
                        "abac_hit_session_source",
                        {
                            "trace_id": trace_id,
                            "rule_name": rule.name,
                            "tool": tool_name,
                            "source": context.session_source,
                        },
                        level=logging.DEBUG,
                    )
                    return PermissionResult(allowed=False, reason="权限不足")

            # ip_not_in_cidr: IP 不在白名单段拒绝
            if "ip_not_in_cidr" in deny_if:
                if not self._ip_in_any_cidr(
                    context.ip, deny_if["ip_not_in_cidr"]
                ):
                    self._log_json(
                        "abac_hit_ip_not_in_cidr",
                        {
                            "trace_id": trace_id,
                            "rule_name": rule.name,
                            "tool": tool_name,
                            "ip": context.ip,
                            "cidrs": deny_if["ip_not_in_cidr"],
                        },
                        level=logging.DEBUG,
                    )
                    return PermissionResult(allowed=False, reason="权限不足")

        return None

    # ── 层3: 正则兜底 ────────────────────────────────────────

    def _regex_fallback(
        self, tool_name: str, params: dict, trace_id: str = ""
    ) -> PermissionResult:
        """正则黑名单兜底

        将 tool_name + params 拼成 action 字符串,
        走 PermissionSystem.check_action 原有四步检查。
        保留原有 reason(危险操作需给操作者明确警示)。
        """
        action = self._compose_action(tool_name, params)
        result = self._ps.check_action(action)
        self._log_json(
            "regex_result",
            {
                "trace_id": trace_id,
                "action": action[:80],
                "tool": tool_name,
                "allowed": result.allowed,
                "requires_confirmation": result.requires_confirmation,
                "reason": result.reason,
            },
            level=logging.DEBUG,
        )
        return result

    @staticmethod
    def _compose_action(tool_name: str, params: dict) -> str:
        """将 tool_name 与参数值拼接为 action 字符串

        仅取参数 value(忽略 key)以匹配正则模式。
        """
        parts = [tool_name]
        for v in params.values():
            parts.append(str(v))
        return " ".join(parts)

    # ── 辅助 ────────────────────────────────────────────────

    @staticmethod
    def _time_in_window(start: str, end: str) -> bool:
        """当前本地时间是否在 [start, end] 窗口内(含端点)

        时间格式: HH:MM (字符串字典序与时间序一致)
        """
        now = datetime.now().strftime("%H:%M")
        return start <= now <= end

    @staticmethod
    def _ip_in_any_cidr(ip: Optional[str], cidr_list: List[str]) -> bool:
        """IP 是否落在任一 CIDR 段内"""
        if ip is None:
            return False
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for cidr in cidr_list:
            try:
                if addr in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    # ── JSON 日志 ────────────────────────────────────────────

    @staticmethod
    def _snapshot_params(params: dict) -> dict:
        """参数快照(值截断 50 字符,避免日志爆炸)"""
        snap = {}
        for k, v in params.items():
            s = str(v)
            if len(s) > 50:
                s = s[:50] + "...(truncated)"
            snap[k] = s
        return snap

    @staticmethod
    def _log_json(
        event: str,
        fields: dict,
        level: int = logging.INFO,
    ):
        """输出单行 JSON 日志(ELK/Splunk 友好)

        统一字段:
        - ts:        ISO 时间戳
        - event:     事件类型
        - module:    模块名(固定 "permission_gateway")

        调用方传入的 fields 会合并进去(如 trace_id/tool/role 等)。
        """
        record = {
            "ts": datetime.now().isoformat(),
            "module": "permission_gateway",
            "event": event,
        }
        record.update(fields)
        logger.log(level, json.dumps(record, ensure_ascii=False))

    def _log_decision(
        self,
        trace_id: str,
        start_ts: float,
        layer: str,
        tool_name: str,
        result: PermissionResult,
    ):
        """出口决策汇总日志(每次 check 必发一条,便于审计)"""
        duration_ms = (time.perf_counter() - start_ts) * 1000
        self._log_json(
            "decision",
            {
                "trace_id": trace_id,
                "layer": layer,
                "tool": tool_name,
                "allowed": result.allowed,
                "requires_confirmation": result.requires_confirmation,
                "reason": result.reason,
                "duration_ms": round(duration_ms, 3),
            },
        )

    # ── 暴露给外部的查询接口 ─────────────────────────────────

    @property
    def is_degraded(self) -> bool:
        """是否处于降级模式(仅正则黑名单)"""
        return self._degraded

    @property
    def default_role(self) -> Role:
        return self._default_role

    def get_permission_system(self) -> PermissionSystem:
        """暴露底层 PermissionSystem(供备份/日志查询复用)"""
        return self._ps
