"""人机协同——高风险操作人工确认

【风险判据的唯一真相】
    "哪个工具多危险"由 ``data/tool_definitions/*.yaml`` 声明（``plane`` / ``effect`` /
    ``risk``），经 :func:`agent.lines.load_tool_meta` 读取。本模块**不再自己维护**这份
    名单——**改动前**的实现把判据写成了两张字符串表，而表里的名字与真实注册工具
    （97 个，见 ``data/tool_definitions/*.yaml``）**完全对不上**：

        HIGH_RISK_ACTIONS   : execute_shell（真名 shell_execute）/ start_process
                              （真名 run_program）/ delete_file（不存在）/
                              browser_navigate（存在但只有 medium）
        MEDIUM_RISK_ACTIONS : insert / update / delete / drop / execute_command /
                              run_shell —— **没有一个是真实工具名**

    后果是**最危险的** ``shell_execute``（YAML: act/execute/**critical**）落到函数末尾
    的 ``return RiskLevel.LOW``，被自动批准放行——治理层看起来齐全，实际一条都没生效。

    **改动后**的判据顺序（见 :meth:`HITLManager.assess`）：
        ① 参数级致命检测（``rm -rf /`` 等，与工具名无关，**保持不变**）；
        ② YAML 派生：``risk`` 决定基准等级，``plane=="govern"`` 或
           ``effect=="extend"`` **至少** HIGH（治理平面＝审批边界）；
        ③ 元数据缺失 → **fail-closed 返回 HIGH**（无法证明安全就不放行）；
        ④ ``HIGH_RISK_ACTIONS`` / ``MEDIUM_RISK_ACTIONS`` 仅作**旧场景兜底**
           （名字已改为真实工具名）；97 个已登记工具**全部**走 ②，不经过这一层。

    ``RiskLevel`` / ``ApprovalRequest`` / ``ConfirmationMode`` 的取值集合与构造签名
    一字未改，本模块的对外契约不变。
"""
import logging
import json
import uuid
import threading
from enum import Enum
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── 工具能力元数据（唯一真相：data/tool_definitions/*.yaml）───────────────

#: YAML 的 ``risk`` 取值 → :class:`RiskLevel`（一一对应，无默认值）
_RISK_TO_LEVEL = {
    "critical": RiskLevel.CRITICAL,
    "high": RiskLevel.HIGH,
    "medium": RiskLevel.MEDIUM,
    "low": RiskLevel.LOW,
}

#: 需要"至少 HIGH"的两个维度：治理平面（改自身能力集）与 extend 效果
_ESCALATE_PLANES = frozenset({"govern"})
_ESCALATE_EFFECTS = frozenset({"extend"})

#: 元数据缓存的锁（首次读取 97 个 YAML 只做一次）
_TOOL_META_LOCK = threading.Lock()
#: ``name → ToolMeta`` 缓存；``None`` = 尚未加载
_TOOL_META_CACHE = None
#: 已告警过的"未登记工具"名字（避免每次审批判定都刷屏）
_UNKNOWN_WARNED = set()


def _tool_meta():
    """惰性加载并缓存工具能力元数据（``data/tool_definitions/*.yaml``）

    【为什么缓存】``assess()`` 在每次审批判定时被调用，而加载要读 **97 个 YAML** ——
        属于"在热路径上做重活"。故模块级缓存，加载一次长期复用。
    【简易】加载失败（缺 ``yaml`` 依赖 / 目录不可读 / 任何异常）⇒ 空 dict，不抛异常；
        调用方据此走"兜底名单 + fail-closed"，绝不因为读不到元数据就静默放行。
    """
    global _TOOL_META_CACHE
    if _TOOL_META_CACHE is None:
        with _TOOL_META_LOCK:
            if _TOOL_META_CACHE is None:
                try:
                    from agent.lines import load_tool_meta
                    _TOOL_META_CACHE = dict(load_tool_meta())
                except Exception as e:  # noqa: BLE001  读不到元数据 ≠ 工具安全
                    logger.warning("[HITL] 工具元数据加载失败（回退内置兜底名单，"
                                   "未登记工具按 HIGH 处理）: %s: %s",
                                   type(e).__name__, e)
                    _TOOL_META_CACHE = {}
    return _TOOL_META_CACHE


def clear_tool_meta_cache() -> None:
    """清空工具元数据缓存（**测试用**：改完 YAML 后让下一次判读取到新值）"""
    global _TOOL_META_CACHE
    with _TOOL_META_LOCK:
        _TOOL_META_CACHE = None
        _UNKNOWN_WARNED.clear()


def _warn_unknown_tool(tool_name: str) -> None:
    """未登记工具只告警一次（fail-closed 是真拒绝，但不能把日志刷爆）"""
    with _TOOL_META_LOCK:
        if tool_name in _UNKNOWN_WARNED:
            return
        if len(_UNKNOWN_WARNED) >= 256:
            _UNKNOWN_WARNED.clear()
        _UNKNOWN_WARNED.add(tool_name)
    logger.warning("[HITL] 工具 %r 未在 data/tool_definitions/ 登记 ⇒ "
                   "无法证明安全，按 fail-closed 判 HIGH", tool_name)


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
    # ── 兜底名单（**仅**用于元数据缺失的旧场景；已被 YAML 派生覆盖）──────────────
    # 为什么还留着：动态生成/外部接入的工具可能**没有** data/tool_definitions/*.yaml，
    #   而 fail-closed 会把它们一律判 HIGH —— 兜底名单让"历史上确实中高风险"的名字
    #   即使读不到元数据也能给出与风险相称的等级与原因文案（ApprovalRequest.reason）。
    # 为什么现在是真实工具名：改动前这张表里是 ``execute_shell`` / ``start_process`` /
    #   ``delete_file`` —— 前两个的真名是 ``shell_execute`` / ``run_program``，第三个
    #   根本不存在；名字错了，兜底层就等于不存在。名字按 ``data/tool_definitions/``
    #   的现状核对，**97 个已登记工具全部走 YAML 派生，不经过这两张表**。
    HIGH_RISK_ACTIONS = {
        "write_file": "写入文件", "edit": "精准改写文件", "apply_patch": "应用补丁",
        "decompress": "解压归档", "run_program": "启动白名单程序",
        "git": "执行 git 操作", "fan_out": "并发扇出",
        "ext_send_channel": "向外部通道发送", "schedule_task": "创建定时任务",
        "workspace_delete": "删除工作区内容", "install_tool": "安装工具",
        "ext_install": "安装扩展", "ext_uninstall": "卸载扩展",
        "ext_configure": "配置扩展", "ext_toggle": "启停扩展",
        "connect_mcp": "接入 MCP 服务", "disconnect_mcp": "断开 MCP 服务",
        "scan_mcp": "扫描 MCP 服务", "shell_execute": "执行系统命令",
        "generate_tool": "生成新工具（改变自身能力集）", "run_sandbox": "沙箱执行",
    }
    CRITICAL_ACTIONS = {"format": "格式化磁盘", "shutdown": "关闭系统", "rm -rf /": "递归删除根目录"}
    # 数据库写操作(中等风险,需审慎但非致命) —— 同样是真实工具名
    MEDIUM_RISK_ACTIONS = {
        "browser_navigate", "run_tests", "set_clipboard", "web_post",
        "delegate", "workspace_write", "compress", "ext_discover",
        "look_at_screen", "submit_task", "stop_process", "process_distill_run",
        "kb_discuss", "cancel_scheduled_task", "distill_process_async",
    }

    @staticmethod
    def _level_from_meta(meta) -> RiskLevel:
        """把 YAML 派生的 :class:`ToolMeta` 折算成 :class:`RiskLevel`

        - ``risk`` 直接映射（**未知取值 ⇒ HIGH**，fail-closed）；
        - ``plane == "govern"`` 或 ``effect == "extend"`` ⇒ **至少 HIGH**：
          治理平面的工具会改变云枢自身的能力集，天然就是审批边界；
        - 其余保持不变（medium → MEDIUM，low → LOW）。
        """
        risk = str(getattr(meta, "risk", "") or "").strip().lower()
        level = _RISK_TO_LEVEL.get(risk, RiskLevel.HIGH)
        if level in (RiskLevel.LOW, RiskLevel.MEDIUM):
            plane = str(getattr(meta, "plane", "") or "").strip().lower()
            effect = str(getattr(meta, "effect", "") or "").strip().lower()
            if plane in _ESCALATE_PLANES or effect in _ESCALATE_EFFECTS:
                return RiskLevel.HIGH
        return level

    def assess(self, tool_name: str, params: dict) -> RiskLevel:
        """评估一次工具调用的风险等级

        判据顺序（**权威来源是 data/tool_definitions/*.yaml**，见模块 docstring）：
            1. 参数级致命检测：工具名命中 ``CRITICAL_ACTIONS``，或**参数内容**里出现
               ``rm -rf /`` 这类致命命令 ⇒ CRITICAL（与工具是否登记无关，先于一切）；
            2. 已登记工具 ⇒ 由 YAML 的 ``risk`` / ``plane`` / ``effect`` 派生
               （``govern`` / ``extend`` 至少 HIGH；``shell_execute`` 是 critical）；
            3. 未登记工具 ⇒ 先查内置兜底名单，仍不认识 ⇒ **fail-closed HIGH**
               （读不到元数据就无法证明它安全，此时"放行"的代价是无限大）。
        """
        # 1. 致命风险: 工具名直接命中(format/shutdown) 或参数含危险命令(rm -rf /)
        if tool_name in self.CRITICAL_ACTIONS or any(c in str(params) for c in self.CRITICAL_ACTIONS):
            return RiskLevel.CRITICAL

        # 2. 权威判据：data/tool_definitions/<tool>.yaml
        meta = _tool_meta().get(tool_name)
        if meta is not None:
            return self._level_from_meta(meta)

        # 3. 元数据缺失：兜底名单 → 仍不认识则 fail-closed
        if tool_name in self.HIGH_RISK_ACTIONS:
            return RiskLevel.HIGH
        if tool_name in self.MEDIUM_RISK_ACTIONS:
            return RiskLevel.MEDIUM
        _warn_unknown_tool(tool_name)
        return RiskLevel.HIGH

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
        logger.error(log_dict({'module_name': 'hitl', 'action': action + '.failed', 'error': f'{type(e).__name__}: {e}'}))
        raise
