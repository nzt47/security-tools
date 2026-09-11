"""Egress — 出域**决策输入**的组装与判定（P7.1-20 决策侧）

【P7.1-20 更正（设计文档原文）】
    「上例中 ``http.send(_)`` 为策略意图伪代码——OPA 是决策引擎，不做网络动作；
    实际数据出域由 egress guard（进程外网络代理）强制执行，OPA 只输出决策。
    LLM 禁止在 Rego 中生成 http.send。」

    云枢落地为「策略决策 + 执行点分离」：
        - **决策**在本模块（``decide_egress`` → ``PolicyEngine.check``）：只出
          ``{allow|deny|ask, policy_id}``，**不碰网络**；
        - **执行**在 ``agent/guardrails/egress_guard.py``（被 ``HttpClient`` 调用）：
          拿决策做放行/拦截，真网络动作由 ``HttpClient`` 自己做。

    本模块**没有**、也**不会**有 ``send``/``request`` 之类的出口。要用它做网络
    请求是不可能的——这正是「引擎不得提供 http.send 类能力」的实现方式。

【决策输入是怎么拼出来的】
    ``capability.trust.data_class`` 与 ``target.external`` 是 §2.5 不变量
    （``data_class=secret`` 且目标外部 ⇒ deny）的两个操作数。它们的来源：

    - ``target.external``：URL 主机是否落在环回/私有/链路本地段之外
      （``classify_target``）。**保守取向**：域名解析不出结论时按「外部」处理。
    - ``data_class``：三条证据按「更严者胜」合并——
        1. 调用方**显式声明**（来自 descriptor ``trust.data_class``）；
        2. **内容证据**：出站载荷里当下就有凭据形态的值（``taint.scan_payload``）；
        3. **链路证据**：同一作用域内先读过敏感文件（``taint`` 台账，§5.7 机制 4）。
      任一条指向 secret ⇒ secret。**只有**在三条都不指向 secret 时才不是 secret
      ——「不知道」不会自动变成 secret（那会让所有外发都被拒），但**已知或已见
      凭据**一定变成 secret。

【脱敏纪律】
    参与判定的载荷**不进决策输入、不进决策日志、不进审计**：``attributes`` 里只放
    派生证据（``secret_kinds``/``tainted``/``payload_bytes``），不放原始内容。
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from agent.policy.engine import PolicyEngine, get_policy_engine
from agent.policy.models import (
    EFFECT_ALLOW,
    EFFECT_ASK,
    EFFECT_DENY,
    PolicyContext,
    PolicyDecision,
)
from agent.policy.taint import (
    describe_kinds,
    get_secret_taint,
    scan_payload,
    taint_state,
)

logger = logging.getLogger("agent.policy.egress")

#: 默认 capability id（调用方未声明能力时的兜底；便于策略按 ``egress.*`` 收口）
DEFAULT_EGRESS_CAPABILITY = "egress.http"

#: 视为「内部」的主机后缀（内网服务常用；**保守**：只列明确的内网 TLD）
INTERNAL_HOST_SUFFIXES: Tuple[str, ...] = (
    ".local", ".internal", ".lan", ".corp", ".intranet", ".localhost",
)

#: 内部主机名（无点单段名，如 ``fileserver``）
_INTERNAL_BARE_HOSTS = ("localhost",)


# ════════════════════════════════════════════════════════════
#  目标分类
# ════════════════════════════════════════════════════════════


def _host_is_private(host: str) -> Optional[bool]:
    """主机是否属于私有/环回/链路本地段；无法判定返回 ``None``"""
    text = str(host or "").strip().strip("[]").lower()
    if not text:
        return None
    if text in _INTERNAL_BARE_HOSTS:
        return True
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        # 非 IP 字面量：按域名后缀判定；其余视为**外部**（保守取向）
        return any(text.endswith(suffix) for suffix in INTERNAL_HOST_SUFFIXES)
    return bool(addr.is_loopback or addr.is_private or addr.is_link_local
                or addr.is_reserved or addr.is_unspecified)


def classify_target(url: Any) -> Dict[str, Any]:
    """解析出站目标（**纯字符串处理，不做 DNS**）

    Returns:
        ``{scheme, host, port, path, external, classified_by}``。
        ``external`` 为 True 表示「目标在受控边界之外」——这是 §2.5 不变量的操作数。
    """
    raw = str(url or "")
    try:
        parts = urlsplit(raw)
    except ValueError:
        return {"scheme": "", "host": "", "port": None, "path": raw,
                "external": True, "classified_by": "unparsable"}
    scheme = str(parts.scheme or "").lower()
    host = str(parts.hostname or "")
    try:
        port = parts.port
    except ValueError:
        port = None
    private = _host_is_private(host)
    if private is None:
        external, by = True, "unresolved"
    else:
        external, by = (not private), ("private_or_loopback" if private else "public")
    return {"scheme": scheme, "host": host, "port": port,
            "path": str(parts.path or ""),
            "external": bool(external), "classified_by": by}


# ════════════════════════════════════════════════════════════
#  请求描述
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EgressRequest:
    """一次出站请求（**执行点交给决策层的全部信息**）

    Attributes:
        url / method: 目标与方法。
        payload: 出站内容（data / json / params）。**只用于本地扫描**，不进入
            决策输入、不落盘、不入审计。
        header_names: 请求头**键名**清单（键名无敏感值；用于「是否携带 Authorization」
            这类判定）。
        capability_id: 发起能力（来自 descriptor），用于策略按能力收口。
        data_class / risk_level: 调用方从 descriptor 读到的声明显式传入
            （``""`` ＝ 未声明）。
        actor / actor_role / tenant_id: 判定与审计维度。
        action: 动作名（缺省 ``http.<method>``）。
        attributes: 额外判定维度（**不得夹带凭据值**）。
    """

    url: str
    method: str = "GET"
    payload: Any = None
    header_names: Tuple[str, ...] = ()
    capability_id: str = ""
    data_class: str = ""
    risk_level: str = ""
    actor: str = ""
    actor_role: str = ""
    tenant_id: str = "default"
    action: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EgressDecision:
    """出域判定的完整结果（决策 + 证据）

    Attributes:
        decision: 引擎给出的 :class:`PolicyDecision`。
        allowed: **执行点是否可放行**（仅当 ``effect == allow``；``ask`` 不算放行）。
        reason: 人类可读原因（可直接作为工具返回的错误文案）。
        evidence: 脱敏证据（``target``/``secret_kinds``/``tainted``/``data_class``）。
        needs_human: 是否需人工介入（ask 或 break-glass 例外）。
    """

    decision: PolicyDecision
    allowed: bool
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    needs_human: bool = False

    @property
    def effect(self) -> str:
        return self.decision.effect

    @property
    def policy_id(self) -> str:
        return self.decision.policy_id

    @property
    def matched(self) -> bool:
        """策略层是否覆盖；``False`` ⇒ 策略层无异议（执行点按既有网络策略放行）"""
        return self.decision.matched

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "effect": self.effect,
            "matched": self.matched,
            "policy_id": self.policy_id,
            "policy_version": self.decision.policy_version,
            "reason": self.reason,
            "needs_human": self.needs_human,
            "break_glass": self.decision.break_glass,
            "evidence": dict(self.evidence),
            "latency_ms": round(float(self.decision.latency_ms), 4),
        }


# ════════════════════════════════════════════════════════════
#  决策输入组装
# ════════════════════════════════════════════════════════════


def _effective_data_class(req: EgressRequest, *, taint_marks: List[str],
                         payload_kinds: List[str]) -> Tuple[str, str]:
    """合并三条证据得 ``data_class``；返回 ``(值, 来源)``

    顺序＝严格度：显式声明 secret > 载荷见凭据 > 链路污点 > 其余。
    """
    declared = str(req.data_class or "").strip().lower()
    if declared == "secret":
        return "secret", "declared"
    if payload_kinds:
        return "secret", "payload_material"
    if taint_marks:
        return "secret", "read_then_egress"
    return declared or "", ("declared" if declared else "undeclared")


def build_egress_context(req: EgressRequest, *, engine: Optional[PolicyEngine] = None
                         ) -> Tuple[PolicyContext, Dict[str, Any]]:
    """组装出域决策输入；返回 ``(ctx, evidence)``

    ``evidence`` 只含派生叶子（**无载荷内容、无凭据值**）。
    """
    target = classify_target(req.url)
    payload_kinds = scan_payload(req.payload) if req.payload is not None else []
    ledger = get_secret_taint()
    try:
        from agent.observability.events import trace_fields
        fields = trace_fields()
    except Exception:  # noqa: BLE001
        fields = {}
    taint_marks = ledger.taint_kinds(trace_id=str(fields.get("trace_id") or ""),
                                     subject_id=str(fields.get("subject_id") or ""))
    data_class, data_class_source = _effective_data_class(
        req, taint_marks=taint_marks, payload_kinds=payload_kinds)

    method = str(req.method or "GET").upper()
    action = str(req.action or f"http.{method.lower()}")
    headers = tuple(sorted(str(h).lower() for h in (req.header_names or ())))
    attributes: Dict[str, Any] = {
        "egress": True,
        "method": method,
        "header_names": list(headers),
        "has_authorization_header": any(h in ("authorization", "proxy-authorization")
                                        for h in headers),
        "payload_bytes": len(str(req.payload)) if req.payload is not None else 0,
        "tainted": bool(taint_marks),
        "target_classified_by": target["classified_by"],
        **describe_kinds(payload_kinds + taint_marks),
        **dict(req.attributes or {}),
    }

    ctx = PolicyContext.build(
        capability_id=str(req.capability_id or DEFAULT_EGRESS_CAPABILITY),
        capability={
            "trust": {"data_class": data_class or None,
                      "risk_level": str(req.risk_level or "") or None},
            "origin": {"external_endpoint": bool(target["external"]),
                       "source_type": "rest"},
            "evolution": {},
        },
        tenant_id=req.tenant_id or "default",
        actor=req.actor, actor_role=req.actor_role,
        action=action, action_kind="egress",
        target={"external": bool(target["external"]), "host": target["host"],
                "scheme": target["scheme"],
                "path": target["path"]},
        attributes=attributes,
    )
    evidence = {
        "target": {"external": bool(target["external"]), "host": target["host"],
                   "scheme": target["scheme"],
                   "classified_by": target["classified_by"]},
        "data_class": data_class,
        "data_class_source": data_class_source,
        "secret_kinds": sorted(set(payload_kinds) | set(taint_marks)),
        "payload_material": bool(payload_kinds),
        "read_then_egress": bool(taint_marks),
        "taint": taint_state(trace_id=str(fields.get("trace_id") or ""),
                             subject_id=str(fields.get("subject_id") or "")),
    }
    return ctx, evidence


# ════════════════════════════════════════════════════════════
#  判定
# ════════════════════════════════════════════════════════════


def decide_egress(req: EgressRequest, *, engine: Optional[PolicyEngine] = None
                  ) -> EgressDecision:
    """**决策侧唯一入口**：给出出域决策，不做任何网络动作

    Returns:
        :class:`EgressDecision`。``allowed=False`` ⇒ 执行点必须拦截；
        ``allowed=True`` ⇒ 策略层无异议，执行点按既有网络策略继续。
    """
    active = engine if engine is not None else get_policy_engine()
    ctx, evidence = build_egress_context(req, engine=active)
    decision = active.check(ctx)
    return _wrap(decision, evidence)


def _wrap(decision: PolicyDecision, evidence: Dict[str, Any]) -> EgressDecision:
    """把引擎决策包装成执行点可直接消费的判定（含原因文案）"""
    if decision.effect == EFFECT_DENY:
        reason = decision.message or (
            f"策略 {decision.policy_id or '<builtin>'} 拒绝本次出域"
            f"（目标 {evidence.get('target', {}).get('host', '')}）")
        return EgressDecision(decision=decision, allowed=False, reason=reason,
                              evidence=evidence, needs_human=False)
    if decision.effect == EFFECT_ASK:
        reason = decision.message or (
            f"策略 {decision.policy_id} 要求人工确认后方可出域")
        return EgressDecision(decision=decision, allowed=False, reason=reason,
                              evidence=evidence, needs_human=True)
    reason = "" if decision.matched else "策略层无覆盖，按既有网络策略放行"
    return EgressDecision(decision=decision,
                          allowed=decision.effect == EFFECT_ALLOW,
                          reason=reason, evidence=evidence,
                          needs_human=decision.break_glass)


__all__ = [
    "DEFAULT_EGRESS_CAPABILITY", "INTERNAL_HOST_SUFFIXES",
    "classify_target", "EgressRequest", "EgressDecision",
    "build_egress_context", "decide_egress",
]
