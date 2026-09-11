"""PolicyInbox — 例外收件箱（§5.6「收件箱只收例外」）

【这条规格的两半，缺一不可】
    §5.6：「OPA/Rego 预编译 WASM+决策缓存 p99 <5ms；**收件箱只收策略未覆盖的例外**。」

    1. **例外要进得来**：``ask`` 决策与 break-glass 例外必须落到人能看见的地方，
       否则「要人」的信号只存在于日志里，等于没人管。
    2. **只有例外进得来**：被策略明确覆盖的 ``allow``/``deny`` **不进收件箱**。
       这一半同样是规格——把策略已经判过的事再推给人，就是审批疲劳（R5）的成因。

    ``PolicyEngine._route_exception`` 保证第 2 半（只在 ask / break-glass 时调用本模块）。

【后端选择（复用既有设施，不另造一套）】
    规格要求「复用 hitl/takeover_queue」。云枢的 ``TakeoverQueue`` 是**纯内存**队列，
    由 ``AlertManager`` 持有（``get_alert_manager()._takeover_queue``），没有单例、
    没有落盘、没有存储路径参数。直接把它拉进决策路径有两个问题：

      - 导入 ``agent.monitoring.alert_manager`` 会拉起监控栈（后台线程/告警渠道），
        对一个「写一条待办」的动作来说代价过大；
      - 队列纯内存 ⇒ 进程重启即丢，而策略例外恰恰是**跨重启仍要有人处理**的东西。

    因此本模块做成**可插拔适配器**，默认后端是本地 JSONL（``log``），并支持：

      - ``backend="takeover"``：写入 ``TakeoverQueue``；
      - ``backend="both"``：既写接管队列又写本地账（双保险）；
      - ``backend="log"``（默认）：只写本地账 ``data/policies/inbox.jsonl``。

    这样「复用 takeover_queue」是**真的接线**（有测试覆盖），而默认路径不会把
    监控栈拖进决策热路径。

【依赖倒置：本模块**不 import** ``agent.monitoring``】
    上面第 2 条理由（不把监控栈拖进决策热路径）落地时进一步发现：**静态导入本身
    就是架构违规**。``agent.monitoring.self_healer`` 已经依赖
    ``agent.permission_system``，而 ``PermissionGateway`` 的第 0 层依赖
    ``agent.policy``；只要 ``agent.policy`` 里任何一处静态引用
    ``agent.monitoring.alert_manager``，``arch_rules`` 的 ``no_circular_dependency``
    就会判出这条环并**阻断 CI**：

        agent.permission_system → agent.policy → agent.policy.egress
          → agent.policy.engine → agent.policy.inbox
          → agent.monitoring.alert_manager → agent.monitoring.self_healer
          → agent.permission_system

    仓库对该规则给出的补救正是「**通过依赖倒置或中间层解耦**」。这里采用前者：
    队列由**调用方注入**（``queue=``）或由**组合根注册解析器**
    （``register_queue_resolver``）；``agent.policy`` 侧只持有 ``Callable``，不认识
    监控栈。组合根注册解析器时，``alert_manager`` 的导入发生在组合根里，
    依赖方向是「组合根 → 两者」，环即消除。

【防洪水（R5 审批疲劳）】
    同一 ``(policy_id, capability_id, tenant_id)`` 在 ``dedupe_window_seconds``
    内的重复例外**合并计数**，不重复入队。例外数量本身是要观测的信号
    （``stats`` 里的 ``deduped``），而不是要被淹没的噪声。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional

from agent.policy.models import PolicyContext, PolicyDecision, now_iso

logger = logging.getLogger("agent.policy.inbox")

#: 环境变量：收件箱后端（log / takeover / both / off）
ENV_INBOX_BACKEND = "CP_POLICY_INBOX_BACKEND"
#: 环境变量：本地账路径
ENV_INBOX_PATH = "CP_POLICY_INBOX_PATH"
#: 环境变量：去重窗口（秒）
ENV_INBOX_DEDUPE = "CP_POLICY_INBOX_DEDUPE_SECONDS"

DEFAULT_INBOX_PATH = "data/policies/inbox.jsonl"
DEFAULT_DEDUPE_SECONDS = 900.0

BACKENDS = ("log", "takeover", "both", "off")


@dataclass(frozen=True)
class InboxItem:
    """一条例外待办（**只含判定叶子，不含匹配值与载荷**）"""

    item_id: str
    kind: str            # policy.ask / policy.break_glass
    policy_id: str
    policy_version: str
    capability_id: str
    tenant_id: str
    actor: str
    message: str
    created_at: str
    effect: str
    break_glass: bool = False
    duplicate_count: int = 1
    takeover_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id, "kind": self.kind,
            "policy_id": self.policy_id, "policy_version": self.policy_version,
            "capability_id": self.capability_id, "tenant_id": self.tenant_id,
            "actor": self.actor, "message": self.message,
            "created_at": self.created_at, "effect": self.effect,
            "break_glass": self.break_glass,
            "duplicate_count": self.duplicate_count,
            "takeover_id": self.takeover_id, "extra": dict(self.extra),
        }

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)


class PolicyInbox:
    """例外收件箱适配器

    Args:
        backend: ``log`` / ``takeover`` / ``both`` / ``off``；``None`` ⇒ 环境变量（默认 log）。
        path: 本地账路径（``log``/``both`` 用）；``None`` ⇒ 环境变量或默认路径。
        queue: 显式注入的 ``TakeoverQueue``（**测试必须显式注入**，避免拉起监控栈）。
        dedupe_window_seconds: 去重窗口；``0`` 关闭去重。
    """

    def __init__(
        self,
        backend: Optional[str] = None,
        *,
        path: Optional[str] = None,
        queue: Any = None,
        dedupe_window_seconds: Optional[float] = None,
    ) -> None:
        raw = str(backend if backend is not None
                  else os.environ.get(ENV_INBOX_BACKEND) or "log").strip().lower()
        self._backend = raw if raw in BACKENDS else "log"
        self._path = str(path if path is not None
                         else os.environ.get(ENV_INBOX_PATH) or DEFAULT_INBOX_PATH)
        self._queue = queue
        if dedupe_window_seconds is None:
            dedupe_window_seconds = _env_float(ENV_INBOX_DEDUPE, DEFAULT_DEDUPE_SECONDS)
        self._dedupe_window = float(dedupe_window_seconds)
        self._lock = threading.RLock()
        self._handler: Optional[Any] = None
        self._recent: Dict[str, Dict[str, Any]] = {}
        self._counts = {"submitted": 0, "deduped": 0, "queue_routed": 0,
                        "failures": 0}

    # ── 属性 ──

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def path(self) -> str:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._backend != "off"

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"backend": self._backend, "path": self._path,
                    "dedupe_window_seconds": self._dedupe_window,
                    "queue_injected": self._queue is not None,
                    **dict(self._counts)}

    # ── 主入口 ──

    def submit(self, decision: PolicyDecision, *, ctx: Optional[PolicyContext] = None,
               severity: str = "", extra: Optional[Dict[str, Any]] = None
               ) -> Optional[InboxItem]:
        """提交一条例外（**只接受 ask / break-glass**）

        Returns:
            :class:`InboxItem`（已入账/已入队）或 ``None``（不适用/被去重/未启用）。
            被去重时返回**已有的那条** item（``duplicate_count`` 已 +1），因此
            调用方拿到的永远是「当前这类例外的代表条目」。
        """
        if not self.enabled:
            return None
        context = ctx if isinstance(ctx, PolicyContext) else PolicyContext.from_input({})
        is_ask = decision.effect == "ask"
        is_bg = bool(decision.break_glass)
        if not (is_ask or is_bg):
            return None  # 「收件箱只收例外」的另一半：其余情形一律不入箱

        key = self._dedupe_key(decision, context)
        with self._lock:
            if self._dedupe_window > 0 and key in self._recent:
                entry = self._recent[key]
                count = int(entry.get("count", 1)) + 1
                entry["count"] = count
                previous = entry.get("item")
                if not isinstance(previous, InboxItem):  # 防御：缓存被外部改写
                    self._recent.pop(key, None)
                else:
                    item = replace(previous, duplicate_count=count)
                    entry["item"] = item
                    self._counts["deduped"] += 1
                    # 复发也要落账：内存里的合并计数如果只活在内存里，运维从
                    # inbox.jsonl 就看不出「这条例外已经反复出现 5 次」——而那正是
                    # R5 审批疲劳的早期信号。追加「复发行」（同 item_id + 新计数），
                    # 保持账本纯追加（不重写历史行）。
                    self._append(item)
                    return item
        kind = "policy.ask" if is_ask else "policy.break_glass"
        takeover_id = self._route_to_takeover(decision, context, kind=kind,
                                              severity=severity)
        item = InboxItem(
            item_id="pi_" + uuid.uuid4().hex[:16], kind=kind,
            policy_id=decision.policy_id, policy_version=decision.policy_version,
            capability_id=decision.capability_id or context.capability_id,
            tenant_id=decision.tenant_id or context.tenant_id,
            actor=decision.actor or context.actor,
            message=decision.message, created_at=now_iso(),
            effect=decision.effect, break_glass=is_bg,
            takeover_id=takeover_id, extra=dict(extra or {}))
        self._append(item)
        with self._lock:
            self._recent[key] = {"count": 1, "item": item, "at": time.time()}
            self._counts["submitted"] += 1
        return item

    def pending(self, *, limit: int = 200) -> List[InboxItem]:
        """读取待办例外（本地账；按时间序）"""
        items: List[InboxItem] = []
        for record in self._read_local(limit=limit):
            items.append(record)
        return items

    def prune_recent(self) -> int:
        """清理去重窗口外的记忆（长驻进程用）"""
        cutoff = time.time() - self._dedupe_window
        with self._lock:
            stale = [k for k, v in self._recent.items() if v.get("at", 0) < cutoff]
            for key in stale:
                self._recent.pop(key, None)
            return len(stale)

    # ── 内部 ──

    @staticmethod
    def _dedupe_key(decision: PolicyDecision, ctx: PolicyContext) -> str:
        return "|".join([
            str(decision.effect), str(decision.policy_id),
            str(decision.capability_id or ctx.capability_id),
            str(decision.tenant_id or ctx.tenant_id),
        ])

    def _route_to_takeover(self, decision: PolicyDecision, ctx: PolicyContext,
                           *, kind: str, severity: str) -> str:
        """路由到 ``TakeoverQueue``（复用既有 hitl 设施）"""
        if self._backend not in ("takeover", "both"):
            return ""
        queue = self._queue if self._queue is not None else _resolve_registered_queue()
        if queue is None:
            return ""
        try:
            record = queue.create_takeover(
                {"name": kind, "severity": severity or (
                    "high" if decision.break_glass else "medium"),
                 "policy_id": decision.policy_id},
                reason=decision.message or f"策略 {decision.policy_id} 要求人工介入",
                evidence={
                    "policy_id": decision.policy_id,
                    "policy_version": decision.policy_version,
                    "effect": decision.effect,
                    "capability_id": decision.capability_id or ctx.capability_id,
                    "tenant_id": decision.tenant_id or ctx.tenant_id,
                    "actor": decision.actor or ctx.actor,
                    "break_glass": bool(decision.break_glass),
                    "values_recorded": False,
                })
            takeover_id = str(getattr(record, "takeover_id", "") or "")
            with self._lock:
                self._counts["queue_routed"] += 1
            return takeover_id
        except Exception as exc:  # noqa: BLE001 收件箱失败不阻断决策
            with self._lock:
                self._counts["failures"] += 1
            logger.warning("例外入接管队列失败: %s: %s", type(exc).__name__, exc)
            return ""


    def _append(self, item: InboxItem) -> bool:
        """追加一行待办（首次入箱与后续复发都走这里）

        同一 ``item_id`` 会因此出现多行：**读端按 item_id 合并取最大
        ``duplicate_count``**（``_read_local``）。账本保持纯追加，不重写历史行。
        """
        if self._backend not in ("log", "both"):
            return True
        try:
            with self._lock:
                handle = self._ensure_handler()
                if handle is None:
                    return False
                handle.write(item.to_json_line() + "\n")
                handle.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._counts["failures"] += 1
            logger.warning("例外待办写入失败（不影响决策）: %s", exc)
            return False

    def _ensure_handler(self) -> Optional[Any]:
        if self._handler is not None:
            return self._handler
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._handler = open(self._path, "a", encoding="utf-8")
        except OSError as exc:
            logger.warning("无法打开例外待办账: %s", exc)
            return None
        return self._handler

    def _read_local(self, *, limit: int = 200) -> List[InboxItem]:
        """读回本地账（按 ``item_id`` 合并复发行，取最大 ``duplicate_count``）"""
        if not os.path.exists(self._path):
            return []
        merged: "Dict[str, InboxItem]" = {}
        order: List[str] = []
        try:
            with open(self._path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    raw_item = InboxItem(
                        item_id=str(data.get("item_id") or ""),
                        kind=str(data.get("kind") or ""),
                        policy_id=str(data.get("policy_id") or ""),
                        policy_version=str(data.get("policy_version") or ""),
                        capability_id=str(data.get("capability_id") or ""),
                        tenant_id=str(data.get("tenant_id") or ""),
                        actor=str(data.get("actor") or ""),
                        message=str(data.get("message") or ""),
                        created_at=str(data.get("created_at") or ""),
                        effect=str(data.get("effect") or ""),
                        break_glass=bool(data.get("break_glass")),
                        duplicate_count=int(data.get("duplicate_count") or 1),
                        takeover_id=str(data.get("takeover_id") or ""),
                        extra=dict(data["extra"]) if isinstance(data.get("extra"), dict) else {})
                    item = raw_item
                    previous = merged.get(item.item_id)
                    if previous is None:
                        merged[item.item_id] = item
                        order.append(item.item_id)
                    elif item.duplicate_count > previous.duplicate_count:
                        merged[item.item_id] = item
        except OSError:
            return list(merged.values())
        out = [merged[key] for key in order]
        if limit:
            out = out[-int(limit):]
        return out

    def close(self) -> None:
        with self._lock:
            if self._handler is not None:
                try:
                    self._handler.flush()
                    self._handler.close()
                except Exception:  # noqa: BLE001
                    pass
                self._handler = None


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default  # 非法值回退默认
    return value if value >= 0 else default


# ════════════════════════════════════════════════════════════
#  接管队列解析器注册（依赖倒置的注入点）
# ════════════════════════════════════════════════════════════

#: 组合根注册的队列解析器（返回 ``TakeoverQueue`` 或 None）
_QUEUE_RESOLVER: Optional[Callable[[], Any]] = None


def register_queue_resolver(resolver: Optional[Callable[[], Any]]) -> None:
    """注册接管队列解析器（**由组合根调用**）

    组合根（如 ``app_server`` / ``lifecycle_manager``）在自己这一侧写：

        from agent.monitoring.alert_manager import get_alert_manager
        from agent.policy.inbox import register_queue_resolver
        register_queue_resolver(
            lambda: getattr(get_alert_manager(), "_takeover_queue", None))

    这样 ``agent.policy`` 侧只有一个 ``Callable``，不认识监控栈；
    ``alert_manager`` 的导入留在组合根里，架构图上不再出现
    ``agent.policy → agent.monitoring`` 这条边。

    ``resolver=None`` 表示注销。
    """
    global _QUEUE_RESOLVER
    _QUEUE_RESOLVER = resolver


def get_queue_resolver() -> Optional[Callable[[], Any]]:
    """当前注册的队列解析器（诊断用）"""
    return _QUEUE_RESOLVER


def _resolve_registered_queue() -> Optional[Any]:
    """调用已注册的解析器；未注册/失败一律返回 None（**不抛异常**）"""
    resolver = _QUEUE_RESOLVER
    if resolver is None:
        return None
    try:
        return resolver()
    except Exception as exc:  # noqa: BLE001 注入方的问题不该影响决策
        logger.warning("接管队列解析器失败: %s: %s", type(exc).__name__, exc)
        return None


# ════════════════════════════════════════════════════════════
#  进程级默认收件箱
# ════════════════════════════════════════════════════════════

_DEFAULT_INBOX: Optional[PolicyInbox] = None
_INBOX_LOCK = threading.Lock()


def get_policy_inbox(reload: bool = False) -> PolicyInbox:
    """进程级默认例外收件箱（懒加载）"""
    global _DEFAULT_INBOX
    if _DEFAULT_INBOX is None or reload:
        with _INBOX_LOCK:
            if _DEFAULT_INBOX is None or reload:
                _DEFAULT_INBOX = PolicyInbox()
    return _DEFAULT_INBOX


def reset_policy_inbox() -> None:
    """丢弃进程级默认收件箱（测试隔离用）"""
    global _DEFAULT_INBOX
    with _INBOX_LOCK:
        if _DEFAULT_INBOX is not None:
            _DEFAULT_INBOX.close()
        _DEFAULT_INBOX = None


__all__ = [
    "ENV_INBOX_BACKEND", "ENV_INBOX_PATH", "ENV_INBOX_DEDUPE",
    "DEFAULT_INBOX_PATH", "DEFAULT_DEDUPE_SECONDS", "BACKENDS",
    "InboxItem", "PolicyInbox", "get_policy_inbox", "reset_policy_inbox",
    "register_queue_resolver", "get_queue_resolver",
]
