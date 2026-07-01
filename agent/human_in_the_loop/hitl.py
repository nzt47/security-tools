"""人机协同——高风险操作人工确认"""
import logging
from enum import Enum

logger = logging.getLogger(__name__)

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ApprovalStatus(Enum):
    """审批状态枚举"""
    PENDING = "pending"          # 待审批
    APPROVED = "approved"        # 已批准
    REJECTED = "rejected"        # 已拒绝
    TIMEOUT = "timeout"          # 超时未响应
    AUTO_APPROVED = "auto"       # 自动批准（低风险）

class ConfirmationMode(Enum):
    """确认模式枚举"""
    NONE = "none"                # 无需确认
    INLINE = "inline"            # 内联确认（当前会话）
    EXTERNAL = "external"        # 外部确认（独立审批流）
    BATCH = "batch"              # 批量确认

class ApprovalRequest:
    def __init__(self, action: str, reason: str, risk_level: RiskLevel, details: dict = None):
        self.action = action
        self.reason = reason
        self.risk_level = risk_level
        self.details = details or {}
        self.approved = False
        self.status = ApprovalStatus.PENDING
        self.mode = ConfirmationMode.INLINE if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) else ConfirmationMode.NONE

class HITLManager:
    HIGH_RISK_ACTIONS = {
        "delete_file": "删除文件", "write_file": "写入文件",
        "execute_shell": "执行系统命令", "start_process": "启动进程",
        "stop_process": "终止进程", "browser_navigate": "浏览器导航",
    }
    CRITICAL_ACTIONS = {"format": "格式化磁盘", "shutdown": "关闭系统", "rm -rf /": "递归删除根目录"}

    def assess(self, tool_name: str, params: dict) -> RiskLevel:
        if any(c in str(params) for c in self.CRITICAL_ACTIONS):
            return RiskLevel.CRITICAL
        if tool_name in self.HIGH_RISK_ACTIONS:
            return RiskLevel.HIGH
        if tool_name in ("execute_command", "run_shell"):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def request_approval(self, tool_name: str, params: dict) -> ApprovalRequest:
        risk = self.assess(tool_name, params)
        req = ApprovalRequest(tool_name, self.HIGH_RISK_ACTIONS.get(tool_name, tool_name), risk, params)
        if risk == RiskLevel.CRITICAL:
            logger.critical(f"[HITL] ⛔ 拒绝: {tool_name}")
            return req
        if risk == RiskLevel.HIGH:
            logger.warning(f"[HITL] ⚠️ 需确认: {tool_name}")
            return req
        req.approved = True
        return req
