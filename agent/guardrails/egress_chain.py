"""出域链路监测 —— 注入防御机制 4（TASK-S4-03 步骤 4 / v7.2 §5.7）

【机制原文（§5.7 表第 4 行）】
    4  出域链路监测｜检测"**读本地密钥 → 外发 HTTP**"链路，命中即**熔断 + 事故卡**

【与 S4-02 的边界（本任务明确要求的"勿与 S4-02 策略引擎重复实现"）】
    S4-02 已经把这条链路的**两个端点**都做出来了，**本模块不重造任何一端**：

      读端点   `agent/policy/taint.py`   —— `mark_secret_read()` / `observe_file_read()`
                                            （记录"本作用域读过哪些密钥"）
      判定端   `agent/policy/egress.py`  —— `decide_egress()`（策略层决策，不做网络动作）
      执行端   `agent/guardrails/egress_guard.py` —— `EgressGuard.precheck()`（真拦截）

    S4-02 的判定是**逐请求**的：这次出站的内容/声明/目标是否合规。
    本模块补的是**跨请求的链路视角**与**后果动作**，即 §5.7 机制 4 里
    S4-02 明确留给 S4-03 的那半句「**命中即熔断 + 事故卡**」：

      1. 链路判定：同一作用域内「已读过密钥」**且**「正在向受控边界之外发起 HTTP」
         —— 即使本次请求的 payload 扫不出密钥（拆包/编码/延时外发），链路仍然成立；
      2. 后果动作：**熔断**出域熔断器（`agent.circuit_breaker`，既有设施）+
         **事故卡**（`agent.self_healing.levels.raise_incident`）+ 审计 + 事件。

【级别映射（判断依据，非默认套用）】
    命中链路 → **L4**（journal 补偿→快照）。理由：链路命中意味着**可能已经泄露**，
    处置不是"重试/降级"（L1/L2）也不是"重启"（L3），而是"止损 + 取证 + 轮换"——
    与 L4「补偿 → 快照」同构。这是本模块的显式判断，写在代码里以便复核。

【默认开启的理由（与 S4-02 `egress_guard` 同款）】
    默认开：**开启不等于收紧**——只有在作用域内**确实登记过密钥污点**、**且**目标是
    外部时才命中；无污点时的行为与加装前一致。内部异常 fail-open（不阻断主流程），
    但**已判定的链路命中绝不 fail-open**。

【不易】不重造 S4-02 的读/判/执行端点；熔断走既有 `circuit_breaker`；
       事故卡走既有 `levels.raise_incident`；失败 best-effort 绝不影响拦截本身。
【变易】`CHAIN_BREAKER_NAME` 与级别映射是可调常量；`evaluate()` 的判定顺序为表驱动。
【简易】纯标准库 + 既有设施（全部延迟导入，不加重导入期依赖）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.guardrails.egress_chain")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

ENV_ENABLED = "CP_GUARDRAILS_EGRESS_CHAIN"

#: 出域链路熔断器名（走既有 `agent.circuit_breaker.get_circuit_breaker`）
CHAIN_BREAKER_NAME = "guardrails.egress_chain"

#: 链路命中的自愈级别（判断依据见模块 docstring）
CHAIN_LEVEL = "L4"

#: 链路命中的事故卡信号名（与 `levels.resolve_level` 的信号表对齐由调用方保证）
CHAIN_SIGNAL = "secret_egress_chain"

#: 链路账容量上限（作用域数）
DEFAULT_MAX_SCOPES = 500

#: 判定结论
VERDICT_ALLOW = "allow"
VERDICT_CHAIN_HIT = "chain_hit"


class EgressChainError(Exception):
    """出域链路层基类异常"""


class EgressChainBlockedError(EgressChainError):
    """命中「读密钥 → 外发」链路——**拒绝本次出域**

    Attributes:
        scope: 触发链路的作用域。
        secret_kinds: 该作用域已登记的密钥类别。
        target_host: 本次出域目标主机。
        incident_id: 同时开出的事故卡 id。
    """

    def __init__(self, message: str, *, scope: str = "",
                 secret_kinds: Optional[Sequence[str]] = None,
                 target_host: str = "", incident_id: str = "") -> None:
        self.scope = str(scope or "")
        self.secret_kinds = list(secret_kinds or [])
        self.target_host = str(target_host or "")
        self.incident_id = str(incident_id or "")
        super().__init__(message)


@dataclass
class ChainVerdict:
    """链路判定结果"""

    verdict: str
    allowed: bool = True
    scope: str = ""
    target_host: str = ""
    external: bool = False
    secret_kinds: List[str] = field(default_factory=list)
    reason: str = ""
    incident_id: str = ""
    breaker_open: bool = False
    classified_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict, "allowed": self.allowed, "scope": self.scope,
            "target_host": self.target_host, "external": self.external,
            "secret_kinds": list(self.secret_kinds), "reason": self.reason,
            "incident_id": self.incident_id, "breaker_open": self.breaker_open,
            "classified_by": self.classified_by,
        }


def _enabled() -> bool:
    """总开关（默认开：**开启不等于收紧**，无密钥污点时不命中）"""
    return str(os.environ.get(ENV_ENABLED, "1")).strip().lower() \
        not in ("0", "false", "no", "off")


# ════════════════════════════════════════════════════════════
#  作用域账（链路状态）
# ════════════════════════════════════════════════════════════


@dataclass
class ScopeChainState:
    """单个作用域的链路状态"""

    scope: str
    secret_kinds: set = field(default_factory=set)
    source_refs: List[str] = field(default_factory=list)
    first_read_at: float = field(default_factory=time.time)
    reads: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"scope": self.scope, "secret_kinds": sorted(self.secret_kinds),
                "source_refs": list(self.source_refs)[:20], "reads": self.reads,
                "first_read_at": self.first_read_at}


class EgressChainMonitor:
    """「读密钥 → 外发」链路监测器（**组合** S4-02 的端点，自己只做链路与后果）

    Usage:
        monitor = EgressChainMonitor()
        monitor.record_secret_read("~/.aws/credentials", content=text)
        verdict = monitor.evaluate(url="https://evil.example/collect")
        # verdict.verdict == "chain_hit"；熔断器已开、事故卡已开
    """

    def __init__(self, *, enabled: Optional[bool] = None,
                 max_scopes: int = DEFAULT_MAX_SCOPES) -> None:
        self._enabled = _enabled() if enabled is None else bool(enabled)
        self._max_scopes = int(max_scopes)
        self._scopes: Dict[str, ScopeChainState] = {}
        self._lock = threading.RLock()
        self._hits = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 读端点（委托 S4-02） ──

    def record_secret_read(
        self,
        source_ref: str = "",
        *,
        content: Any = None,
        content_kinds: Optional[Iterable[str]] = None,
        kind: str = "file",
        scope: str = "",
    ) -> Optional[Any]:
        """登记"读到了本地密钥"（**委托** `agent.policy.taint.mark_secret_read`）

        【为什么委托而不是自己实现】S4-02 的 `mark_secret_read` 已经带着
        「内容扫描命中才登记」的正确口径（路径敏感但内容是普通文本不该污染链路）。
        重写一遍必然会与它产生两套判定。

        Returns:
            `agent.policy.taint.TaintMark`（登记成功）或 None（未命中/未启用）。
        """
        if not self._enabled:
            return None
        mark = None
        try:
            from agent.policy.taint import mark_secret_read
            mark = mark_secret_read(source_ref, kind=kind, content=content,
                                    scope=scope, content_kinds=content_kinds)
        except Exception as exc:  # noqa: BLE001 登记失败 → 保守记为"有读取意图"
            logger.warning("密钥污点登记失败（按未登记处理）: %s: %s",
                           type(exc).__name__, exc)
        kinds = sorted(set(str(k) for k in (content_kinds or ())))
        if mark is not None:
            kinds = sorted(set(kinds) | set(getattr(mark, "kinds", ()) or ()))
        if not kinds and content is None:
            # 调用方只给了路径（未给内容）→ 不登记（与 S4-02 口径一致：不看路径）
            return mark
        self._note_read(source_ref=source_ref, kinds=kinds, scope=scope)
        return mark

    def _note_read(self, *, source_ref: str, kinds: Sequence[str], scope: str) -> None:
        """记入链路账（**独立于 S4-02 的污点账**：S4-02 记"读到什么"，
        本账记"哪个作用域需要看住"）"""
        if not kinds and not str(source_ref or ""):
            return
        key = str(scope or self._current_scope())
        with self._lock:
            state = self._scopes.get(key)
            if state is None:
                state = ScopeChainState(scope=key)
                self._scopes[key] = state
                if len(self._scopes) > self._max_scopes:
                    oldest = min(self._scopes.values(), key=lambda s: s.first_read_at)
                    self._scopes.pop(oldest.scope, None)
            state.secret_kinds |= set(kinds)
            state.reads += 1
            if source_ref and source_ref not in state.source_refs:
                state.source_refs.append(str(source_ref))

    @staticmethod
    def _current_scope() -> str:
        """当前作用域（优先 TraceContext；不可得时退回进程级）"""
        try:
            from agent.policy.taint import PROCESS_SCOPE, get_secret_taint
            from agent.observability.events import trace_fields
            fields = trace_fields()
            return get_secret_taint().scope_for(
                trace_id=str(fields.get("trace_id") or ""),
                subject_id=str(fields.get("subject_id") or "")) or PROCESS_SCOPE
        except Exception:  # noqa: BLE001
            return "__process__"

    # ── 链路状态 ──

    def scope_state(self, scope: str = "") -> Dict[str, Any]:
        """某作用域的链路状态（含 S4-02 污点账的合并视图）"""
        key = str(scope or self._current_scope())
        with self._lock:
            state = self._scopes.get(key)
            local: Dict[str, Any] = (state.to_dict() if state else
                                     {"scope": key, "secret_kinds": [],
                                      "source_refs": [], "reads": 0})
        s402 = self._s402_tainted(scope=key)
        local_kinds: List[str] = [str(x) for x in (local.get("secret_kinds") or [])]
        policy_kinds: List[str] = [str(x) for x in (s402.get("kinds") or [])]
        merged = sorted(set(local_kinds) | set(policy_kinds))
        return {**local, "policy_taint_kinds": policy_kinds,
                "policy_tainted": bool(s402.get("tainted", False)),
                "secret_kinds": merged, "tainted": bool(merged)}

    def is_chain_armed(self, scope: str = "") -> bool:
        """该作用域是否"已读过密钥"（链路的前半段是否成立）"""
        return bool(self.scope_state(scope).get("tainted"))

    def _s402_tainted(self, *, scope: str) -> Dict[str, Any]:
        """查 S4-02 的秘密污点账（best-effort；不可得时返回空）

        【实现说明】显式传 `trace_id=` / `subject_id=` 两个关键字，
        不用 `**kwargs` 展开——后者会让类型检查无法解析被调方签名
        （mypy: incompatible type for **dict）。
        """
        try:
            from agent.policy.taint import get_secret_taint
            ledger = get_secret_taint()
            trace_id = ""
            subject_id = ""
            try:
                from agent.observability.events import trace_fields
                fields = trace_fields()
                trace_id = str(fields.get("trace_id") or "")
                subject_id = str(fields.get("subject_id") or "")
            except Exception:  # noqa: BLE001 无 trace 上下文 ⇒ 仅按进程级查
                trace_id = ""
                subject_id = ""
            tainted = bool(ledger.is_tainted(trace_id=trace_id, subject_id=subject_id))
            kinds = ledger.taint_kinds(trace_id=trace_id, subject_id=subject_id)
            return {"tainted": tainted, "kinds": sorted({str(k) for k in (kinds or ())})}
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取 S4-02 污点账失败（按无污点处理）: %s", exc)
            return {"tainted": False, "kinds": []}

    # ── 出域端判定 ──

    def evaluate(
        self,
        *,
        url: str,
        method: str = "GET",
        scope: str = "",
        payload: Any = None,
        header_names: Sequence[str] = (),
        capability_id: str = "",
        data_class: str = "",
        tenant_id: str = "default",
        trace_id: str = "",
        incident_dir: Optional[str] = None,
        enforce: bool = False,
        trip_breaker: bool = True,
        raise_card: bool = True,
    ) -> ChainVerdict:
        """判定一次出站是否构成「读密钥 → 外发」链路（**命中即熔断 + 事故卡**）

        Args:
            url / method: 出站目标与方法。
            scope: 作用域（缺省由当前 TraceContext 推导）。
            payload / header_names / capability_id / data_class / tenant_id:
                透传给 S4-02 的 `EgressRequest`（用于决策侧留痕；**本模块不落 payload**）。
            incident_dir: 事故卡目录（用例必须显式传）。
            enforce: True → 命中即抛 `EgressChainBlockedError`。
            trip_breaker / raise_card: 命中时的后果动作开关（默认都做）。

        Returns:
            `ChainVerdict`（`allowed=False` 表示必须拦截）。
        """
        if not self._enabled:
            return ChainVerdict(verdict=VERDICT_ALLOW, allowed=True,
                                reason="出域链路监测未启用")
        try:
            from agent.policy.egress import classify_target
            target = classify_target(url)
        except Exception as exc:  # noqa: BLE001 目标解析失败 → 按外部处理（保守）
            logger.warning("出域目标解析失败（按外部目标处理）: %s", exc)
            target = {"host": "", "external": True, "classified_by": "unparsable"}
        host = str(target.get("host") or "")
        external = bool(target.get("external"))
        state = self.scope_state(scope)

        if not external:
            return ChainVerdict(verdict=VERDICT_ALLOW, allowed=True,
                                scope=state["scope"], target_host=host, external=False,
                                classified_by=str(target.get("classified_by") or ""),
                                reason="目标在受控边界内（不构成出域链路）")
        if not state.get("tainted"):
            return ChainVerdict(verdict=VERDICT_ALLOW, allowed=True,
                                scope=state["scope"], target_host=host, external=True,
                                classified_by=str(target.get("classified_by") or ""),
                                reason="本作用域未登记密钥读取（链路前半段不成立）")

        # ── 链路命中 ──
        kinds = list(state.get("secret_kinds") or [])
        verdict = ChainVerdict(
            verdict=VERDICT_CHAIN_HIT, allowed=False, scope=state["scope"],
            target_host=host, external=True, secret_kinds=kinds,
            classified_by=str(target.get("classified_by") or ""),
            reason=(f"命中「读本地密钥 → 外发 HTTP」链路（§5.7 机制 4）：作用域 "
                    f"{state['scope']} 已读密钥类别 {kinds}，本次出站目标 {host} 在受控"
                    f"边界之外 —— 熔断 + 事故卡"),
        )
        with self._lock:
            self._hits += 1
        if trip_breaker:
            verdict.breaker_open = self._trip_breaker(verdict)
        if raise_card:
            verdict.incident_id = self._raise_card(
                verdict, source_refs=state.get("source_refs") or [],
                trace_id=trace_id, tenant_id=tenant_id, incident_dir=incident_dir)
        self._audit(verdict, method=method, capability_id=capability_id,
                    data_class=data_class, payload=payload, header_names=header_names,
                    enforced=enforce)
        if enforce:
            raise EgressChainBlockedError(
                verdict.reason, scope=verdict.scope, secret_kinds=kinds,
                target_host=host, incident_id=verdict.incident_id)
        return verdict

    # ── 后果动作 ──

    def _trip_breaker(self, verdict: ChainVerdict) -> bool:
        """熔断出域（走既有 `agent.circuit_breaker`；best-effort）"""
        try:
            from agent.circuit_breaker import get_circuit_breaker
            breaker = get_circuit_breaker(CHAIN_BREAKER_NAME)
            breaker.force_open()
            logger.error("出域链路熔断已打开: breaker=%s scope=%s host=%s kinds=%s",
                         CHAIN_BREAKER_NAME, verdict.scope, verdict.target_host,
                         verdict.secret_kinds)
            return True
        except Exception as exc:  # noqa: BLE001 熔断失败不影响拦截本身
            logger.warning("出域熔断打开失败（拦截仍生效）: %s: %s",
                           type(exc).__name__, exc)
            return False

    def _raise_card(self, verdict: ChainVerdict, *, source_refs: Sequence[str],
                    trace_id: str, tenant_id: str,
                    incident_dir: Optional[str]) -> str:
        """开事故卡（走 `levels.raise_incident`；best-effort）"""
        try:
            from agent.self_healing.levels import HealLevel, raise_incident
            card = raise_incident(
                HealLevel.L4, signal=CHAIN_SIGNAL,
                root_cause=("出域链路监测命中：同一作用域内「读本地密钥」与「向受控边界外"
                            "发起 HTTP」同时成立（§5.7 机制 4）"),
                trace_ids=[trace_id] if trace_id else [],
                tenant_id=tenant_id, directory=incident_dir,
                detail={"scope": verdict.scope, "target_host": verdict.target_host,
                        "secret_kinds": list(verdict.secret_kinds),
                        "classified_by": verdict.classified_by,
                        # 只记来源**引用**（文件名），不记内容
                        "secret_source_refs": list(source_refs)[:20],
                        "breaker": CHAIN_BREAKER_NAME},
            )
            return card.incident_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("出域链路事故卡开出失败: %s: %s", type(exc).__name__, exc)
            return ""

    def _audit(self, verdict: ChainVerdict, *, method: str, capability_id: str,
               data_class: str, payload: Any, header_names: Sequence[str],
               enforced: bool = False) -> None:
        """拦截入审计（best-effort；**不含 payload 原文**）

        【`enforced` 的真实含义（实现期复核修正）】
        第一版硬编码 `"enforced": True`——但 `evaluate(trip_breaker=False,
        raise_card=False)` 时本模块**什么后果动作都没做**，此时写 True 是审计失真。
        现拆成：
            `consequence_actions_taken` = 本模块是否真的熔断/开卡
            `caller_action_required`    = 调用方是否**必须**拦截本次出域
            `enforced`                  = 调用方声明已按判定阻断（`enforce=True` 抛异常路径）
        三者互不冒充，事后复查才不会把"没人拦"读成"拦住了"。
        """
        try:
            from agent.audit.facade import audit
            audit.record("guardrails.egress_chain_blocked",
                         actor="guardrails.egress_chain",
                         subject=f"egress:{verdict.target_host or '-'}",
                         payload={"scope": verdict.scope,
                                  "target_host": verdict.target_host,
                                  "method": str(method or "GET"),
                                  "secret_kinds": list(verdict.secret_kinds),
                                  "capability_id": str(capability_id or ""),
                                  "data_class": str(data_class or ""),
                                  "breaker_open": verdict.breaker_open,
                                  "incident_id": verdict.incident_id,
                                  "network_action_taken": False,
                                  "verdict": verdict.verdict,
                                  "consequence_actions_taken": bool(
                                      verdict.breaker_open or verdict.incident_id),
                                  "caller_action_required": True,
                                  "enforced": bool(enforced)})
        except Exception as exc:  # noqa: BLE001
            logger.debug("出域链路审计写入失败: %s", exc)

    # ── 维护 ──

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"enabled": self._enabled, "scope_count": len(self._scopes),
                    "hits": self._hits, "breaker_name": CHAIN_BREAKER_NAME,
                    "level": CHAIN_LEVEL, "signal": CHAIN_SIGNAL,
                    "scopes": [s.to_dict() for s in list(self._scopes.values())[:50]]}

    def reset(self) -> None:
        """清空链路账与熔断器（用例隔离）"""
        with self._lock:
            self._scopes.clear()
            self._hits = 0
        try:
            from agent.circuit_breaker import get_circuit_breaker
            get_circuit_breaker(CHAIN_BREAKER_NAME).reset()
        except Exception:  # noqa: BLE001
            pass


_MONITOR_LOCK = threading.RLock()
_MONITOR: Optional[EgressChainMonitor] = None


def get_egress_chain_monitor() -> EgressChainMonitor:
    """进程级链路监测器（惰性创建）"""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is None:
            _MONITOR = EgressChainMonitor()
        return _MONITOR


def set_egress_chain_monitor(monitor: Optional[EgressChainMonitor]) -> Optional[EgressChainMonitor]:
    """替换进程级监测器（用例注入；返回旧实例）"""
    global _MONITOR
    with _MONITOR_LOCK:
        old, _MONITOR = _MONITOR, monitor
        return old


def reset_egress_chain_monitor() -> None:
    """清空并丢弃进程级监测器（用例隔离）"""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR is not None:
            _MONITOR.reset()
        _MONITOR = None


def egress_chain_state() -> Dict[str, Any]:
    """机制 4 状态快照（诊断/验收报告）"""
    return get_egress_chain_monitor().stats()


__all__ = [
    "ENV_ENABLED", "CHAIN_BREAKER_NAME", "CHAIN_LEVEL", "CHAIN_SIGNAL",
    "DEFAULT_MAX_SCOPES", "VERDICT_ALLOW", "VERDICT_CHAIN_HIT",
    "EgressChainError", "EgressChainBlockedError",
    "ChainVerdict", "ScopeChainState", "EgressChainMonitor",
    "get_egress_chain_monitor", "set_egress_chain_monitor",
    "reset_egress_chain_monitor", "egress_chain_state",
]
