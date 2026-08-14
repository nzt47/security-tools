"""人工接管队列 — 升级告警的人工处置状态机（补 M5 后段）

职责边界（【不易】约束）：
- 接管 ≠ 审批：接管队列只负责"有人看、有人管"，高危动作仍需
  `agent.human_in_the_loop.hitl` 单独审批，本模块不自动批准任何动作；
- 状态机：open → assigned → resolved；open 超时未处置自动转 timed_out（二次通知）；
- 仅记录、通知、等待处置——不执行任何高危动作本身。

状态流：
    create_takeover(alert) ──► OPEN ──► assign(owner) ──► ASSIGNED ──► resolve() ──► RESOLVED
                                  │
                                  └──(超时 takeover_timeout)──► TIMED_OUT（二次通知）

线程安全：锁内仅内存变更，通知回调在锁外触发（持锁纪律）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TakeoverStatus(str, Enum):
    """人工接管状态"""
    OPEN = "open"              # 待接管
    ASSIGNED = "assigned"      # 已指派处置人
    RESOLVED = "resolved"      # 已解决
    TIMED_OUT = "timed_out"    # 超时未处置（二次升级）


@dataclass
class TakeoverRecord:
    """人工接管记录"""
    takeover_id: str
    alert_name: str
    reason: str
    evidence: Dict[str, Any]
    status: TakeoverStatus
    created_at: float
    owner: Optional[str] = None
    resolution: Optional[str] = None
    resolved_at: Optional[float] = None
    timed_out_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（供运维与 web 界面查询）"""
        return {
            "takeover_id": self.takeover_id,
            "alert_name": self.alert_name,
            "reason": self.reason,
            "evidence": self.evidence,
            "status": self.status.value,
            "created_at": self.created_at,
            "owner": self.owner,
            "resolution": self.resolution,
            "resolved_at": self.resolved_at,
            "timed_out_at": self.timed_out_at,
        }


# 状态机合法流转表：key=(当前状态, 目标状态) → 是否允许
_ALLOWED_TRANSITIONS = {
    (TakeoverStatus.OPEN, TakeoverStatus.ASSIGNED): True,
    (TakeoverStatus.OPEN, TakeoverStatus.RESOLVED): True,
    (TakeoverStatus.ASSIGNED, TakeoverStatus.RESOLVED): True,
    (TakeoverStatus.OPEN, TakeoverStatus.TIMED_OUT): True,
    (TakeoverStatus.ASSIGNED, TakeoverStatus.TIMED_OUT): True,
}


class TakeoverQueue:
    """人工接管队列

    Args:
        takeover_timeout: OPEN 状态超时时间（秒），超时自动转 TIMED_OUT
        notifier: 通知回调 notifier(record, event)，event ∈ {"created", "timed_out"}；
            默认仅记日志（由 AlertManager 注入真实通知渠道）
        clock: 时间源（可注入 mock 时钟用于测试）
        auto_sweep: 是否启动后台清扫线程（测试可关闭，手动调 sweep()）
        sweep_interval: 后台清扫间隔（秒）
    """

    def __init__(
        self,
        takeover_timeout: float = 1800.0,
        notifier: Optional[Callable[[TakeoverRecord, str], None]] = None,
        clock: Callable[[], float] = time.time,
        auto_sweep: bool = True,
        sweep_interval: float = 60.0,
    ):
        self.takeover_timeout = float(takeover_timeout)
        self._notifier = notifier
        self._clock = clock
        self._sweep_interval = sweep_interval

        self._takeovers: Dict[str, TakeoverRecord] = {}
        self._lock = threading.RLock()

        self._running = False
        self._sweep_thread: Optional[threading.Thread] = None
        # 可打断等待事件：stop() 置位后清扫线程立即退出（防 daemon 线程滞留引用）
        self._wake_event = threading.Event()
        if auto_sweep:
            self.start()

    # ── 入队与流转 ──

    def create_takeover(
        self,
        alert: Any,
        reason: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> TakeoverRecord:
        """创建接管条目并通知（入队）

        Args:
            alert: Alert 对象（含 .name）或含 "name" 键的字典
            reason: 接管原因（如升级告警原因）
            evidence: 证据（如失败动作、失败原因、上下文）

        Returns:
            新建的 TakeoverRecord
        """
        alert_name = alert.name if hasattr(alert, "name") else (alert or {}).get("name", "unknown")
        # evidence 规范化：字典原样保留，字符串/其他转 {"detail": ...}（防 dict(str) 抛 ValueError）
        evidence_dict = dict(evidence) if isinstance(evidence, dict) else ({"detail": evidence} if evidence else {})
        record = TakeoverRecord(
            takeover_id=uuid.uuid4().hex[:16],
            alert_name=str(alert_name),
            reason=reason or "未提供原因",
            evidence=evidence_dict,
            status=TakeoverStatus.OPEN,
            created_at=self._clock(),
        )
        with self._lock:
            self._takeovers[record.takeover_id] = record

        logger.info("[Takeover] 创建接管条目: %s (alert=%s, reason=%s)",
                    record.takeover_id, record.alert_name, record.reason)
        self._notify(record, "created")
        return record

    def assign(self, takeover_id: str, owner: str) -> bool:
        """指派处置人（open → assigned）

        Returns:
            是否流转成功
        """
        if not owner:
            logger.warning("[Takeover] assign 缺少 owner (id=%s)", takeover_id)
            return False
        with self._lock:
            record = self._takeovers.get(takeover_id)
            if record is None:
                return False
            if not _ALLOWED_TRANSITIONS.get((record.status, TakeoverStatus.ASSIGNED)):
                return False
            record.status = TakeoverStatus.ASSIGNED
            record.owner = owner
        logger.info("[Takeover] 已指派: %s → %s (id=%s)", owner, record.alert_name, takeover_id)
        return True

    def resolve(self, takeover_id: str, resolution: str) -> bool:
        """解决接管条目（open/assigned → resolved）

        Returns:
            是否流转成功
        """
        if not resolution:
            logger.warning("[Takeover] resolve 缺少 resolution (id=%s)", takeover_id)
            return False
        with self._lock:
            record = self._takeovers.get(takeover_id)
            if record is None:
                return False
            if not _ALLOWED_TRANSITIONS.get((record.status, TakeoverStatus.RESOLVED)):
                return False
            record.status = TakeoverStatus.RESOLVED
            record.resolution = resolution
            record.resolved_at = self._clock()
        logger.info("[Takeover] 已解决: %s (id=%s, resolution=%.80s)",
                    record.alert_name, takeover_id, resolution)
        return True

    def _timed_out(self, record: TakeoverRecord) -> None:
        """内部：open/assigned → timed_out（超时未处置，二次通知）"""
        with self._lock:
            if not _ALLOWED_TRANSITIONS.get((record.status, TakeoverStatus.TIMED_OUT)):
                return
            record.status = TakeoverStatus.TIMED_OUT
            record.timed_out_at = self._clock()
        logger.warning("[Takeover] 超时未处置，转 TIMED_OUT: %s (id=%s, 超时=%ss)",
                       record.alert_name, record.takeover_id, self.takeover_timeout)
        # 二次通知（锁外触发）
        self._notify(record, "timed_out")

    # ── 超时清扫 ──

    def sweep(self) -> List[str]:
        """扫描超时条目：OPEN/ASSIGNED 超过 takeover_timeout → TIMED_OUT

        Returns:
            本次转为 TIMED_OUT 的 takeover_id 列表
        """
        now = self._clock()
        overdue = []
        with self._lock:
            for record in self._takeovers.values():
                if record.status in (TakeoverStatus.OPEN, TakeoverStatus.ASSIGNED) \
                        and (now - record.created_at) > self.takeover_timeout:
                    overdue.append(record)
        for record in overdue:
            self._timed_out(record)
        return [r.takeover_id for r in overdue]

    # ── 查询 ──

    def get(self, takeover_id: str) -> Optional[TakeoverRecord]:
        """按 ID 查询接管记录"""
        with self._lock:
            return self._takeovers.get(takeover_id)

    def list_takeovers(
        self,
        status: Optional[TakeoverStatus] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询接管条目（供运维与 web 界面）

        Args:
            status: 按状态过滤（None 返回全部）
            limit: 返回条数上限

        Returns:
            接管记录字典列表（最新的在前）
        """
        with self._lock:
            records = sorted(self._takeovers.values(), key=lambda r: r.created_at, reverse=True)
        if status is not None:
            records = [r for r in records if r.status == status]
        return [r.to_dict() for r in records[:limit]]

    # ── 后台清扫线程 ──

    def start(self) -> None:
        """启动后台清扫线程（daemon，进程退出不阻塞）"""
        with self._lock:
            if self._running or self._sweep_thread is not None:
                return
            self._running = True
            thread = threading.Thread(target=self._sweep_loop, name="takeover-sweeper", daemon=True)
            self._sweep_thread = thread
        thread.start()

    def _sweep_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            try:
                self.sweep()
            except Exception as e:
                logger.error("[Takeover] 清扫异常: %s", e)
            # 事件等待替代固定 sleep：stop() 置位后立即退出，当前轮睡眠不再阻塞退出
            self._wake_event.wait(self._sweep_interval)
            self._wake_event.clear()

    def stop(self) -> None:
        """停止后台清扫线程"""
        with self._lock:
            if not self._running:
                return
            self._running = False
            thread = self._sweep_thread
        # 唤醒清扫线程（可能在 wait 中睡眠，置位后立即退出）
        self._wake_event.set()
        if thread is not None:
            thread.join(timeout=2.0)
            with self._lock:
                self._sweep_thread = None

    def close(self) -> None:
        """关闭队列（停止后台线程，并释放通知回调引用打破引用环）"""
        self.stop()
        with self._lock:
            self._notifier = None

    # ── 内部工具 ──

    def _notify(self, record: TakeoverRecord, event: str) -> None:
        """通知回调（锁外触发：回调可能含网络 I/O，禁止持锁调用）"""
        if self._notifier is None:
            return
        try:
            self._notifier(record, event)
        except Exception as e:
            logger.error("[Takeover] 通知回调失败 (event=%s): %s", event, e)
