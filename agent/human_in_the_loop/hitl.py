"""人机协同——高风险操作人工确认"""
import logging
import json
import uuid
import threading
from enum import Enum

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


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
    CANCELLED = "cancelled"      # 已取消

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
        # 异步审批扩展字段（不影响同步 request_approval 流程）
        self.request_id = None     # 请求唯一标识
        self.approver = None       # 审批人
        self.callback = None       # 状态变更回调
        self._timer = None         # 超时定时器（内部使用）

class HITLManager:
    HIGH_RISK_ACTIONS = {
        "delete_file": "删除文件", "write_file": "写入文件",
        "execute_shell": "执行系统命令", "start_process": "启动进程",
        "stop_process": "终止进程", "browser_navigate": "浏览器导航",
    }
    CRITICAL_ACTIONS = {"format": "格式化磁盘", "shutdown": "关闭系统", "rm -rf /": "递归删除根目录"}
    # 数据库写操作(中等风险,需审慎但非致命)
    MEDIUM_RISK_ACTIONS = {"insert", "update", "delete", "drop", "execute_command", "run_shell"}

    def assess(self, tool_name: str, params: dict) -> RiskLevel:
        # 致命风险: 工具名直接命中(format/shutdown) 或参数含危险命令(rm -rf /)
        if tool_name in self.CRITICAL_ACTIONS or any(c in str(params) for c in self.CRITICAL_ACTIONS):
            return RiskLevel.CRITICAL
        if tool_name in self.HIGH_RISK_ACTIONS:
            return RiskLevel.HIGH
        if tool_name in self.MEDIUM_RISK_ACTIONS:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def request_approval(self, tool_name: str, params: dict) -> ApprovalRequest:
        risk = self.assess(tool_name, params)
        req = ApprovalRequest(tool_name, self.HIGH_RISK_ACTIONS.get(tool_name, tool_name), risk, params)
        if risk == RiskLevel.CRITICAL:
            req.status = ApprovalStatus.REJECTED
            logger.critical(f"[HITL] ⛔ 拒绝: {tool_name}")
            return req
        if risk == RiskLevel.HIGH:
            logger.warning(f"[HITL] ⚠️ 需确认: {tool_name}")
            return req
        req.approved = True
        req.status = ApprovalStatus.APPROVED
        return req

    def __init__(self, timeout_seconds: int = 300):
        # 默认审批超时（秒）；0 或 None 表示不启用超时
        self.default_timeout = timeout_seconds
        # 全部请求历史（含已处理），便于 get_request_status 查询终态
        self._requests = {}
        self._lock = threading.Lock()

    def request_async_approval(self, action: str, params: dict,
                               callback=None, timeout_seconds: int = None) -> str:
        """发起异步审批请求，返回 request_id。

        无论风险等级均创建 pending 请求并返回 id，由调用方显式 approve/reject/cancel。
        超时（若启用）后自动触发 callback 并标记 TIMEOUT。
        """
        risk = self.assess(action, params)
        req = ApprovalRequest(action, self.HIGH_RISK_ACTIONS.get(action, action), risk, params)
        req.request_id = uuid.uuid4().hex
        req.callback = callback

        # 解析超时：参数优先，回退到实例默认值
        if timeout_seconds is not None:
            timeout = timeout_seconds
        else:
            timeout = self.default_timeout

        with self._lock:
            self._requests[req.request_id] = req

        # 启动超时定时器（daemon 线程，进程退出不阻塞）
        if timeout is not None and timeout > 0:
            timer = threading.Timer(timeout, self._handle_timeout, args=(req.request_id,))
            timer.daemon = True
            timer.start()
            req._timer = timer

        logger.info(f"[HITL] 异步审批请求创建: {action} (id={req.request_id})")
        return req.request_id

    def _handle_timeout(self, request_id: str):
        """超时回调：标记 TIMEOUT 并通知 callback。"""
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.status != ApprovalStatus.PENDING:
                return  # 已被 approve/reject/cancel，忽略
            req.status = ApprovalStatus.TIMEOUT
            req.approved = False
            callback = req.callback

        if callback is not None:
            try:
                callback(req)
            except Exception as e:
                logger.error(f"[HITL] timeout callback 执行失败: {e}")

    def _finalize(self, request_id: str, status: ApprovalStatus,
                  approved: bool, approver: str = None) -> bool:
        """内部：将 PENDING 请求转为终态并触发 callback。"""
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.status != ApprovalStatus.PENDING:
                return False
            req.status = status
            req.approved = approved
            req.approver = approver
            if req._timer is not None:
                req._timer.cancel()
                req._timer = None
            callback = req.callback

        if callback is not None:
            try:
                callback(req)
            except Exception as e:
                logger.error(f"[HITL] callback 执行失败: {e}")
        return True

    def approve_request(self, request_id: str, approver: str = None) -> bool:
        """批准请求，返回是否成功（请求存在且仍 PENDING）。"""
        return self._finalize(request_id, ApprovalStatus.APPROVED, True, approver)

    def reject_request(self, request_id: str, approver: str = None) -> bool:
        """拒绝请求，返回是否成功。"""
        return self._finalize(request_id, ApprovalStatus.REJECTED, False, approver)

    def cancel_request(self, request_id: str) -> bool:
        """取消请求（不触发 callback），返回是否成功。"""
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.status != ApprovalStatus.PENDING:
                return False
            req.status = ApprovalStatus.CANCELLED
            if req._timer is not None:
                req._timer.cancel()
                req._timer = None
        return True

    def get_request_status(self, request_id: str):
        """查询请求状态（含已处理的历史请求），不存在返回 None。"""
        with self._lock:
            return self._requests.get(request_id)

    def get_pending_requests(self):
        """返回所有仍处于 PENDING 状态的请求列表。"""
        with self._lock:
            return [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING]


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({
            "trace_id": _trace_id(),
            "module_name": "hitl",
            "action": action + ".failed",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        raise
