"""审批入口 Actor 校验（§7.0 落地 + §5.7⑦ 越权告警）

【单一权威】
    §7.0 的判定**只在后端**（`agent/security/actor_matrix.py` 一张表）。本模块是
    审批域与矩阵之间的**唯一接线层**：把 (object_type, action) 映射为 §7.0 操作、
    组装执行体上下文、调用 `decide()`，并在拒绝时统一 **告警 + 审计 + 事件**。
    前端提交的任何 `actor` / `actor_type` 都**不是**上下文来源——路由必须先用
    身份层（`agent/security/identity.py`）解析出身份，再构造 `ActorContext`。

【越权三件套（验收项：拒绝 + 审计 + 告警）】
    1. **拒绝**：`authorize()` 返回 `allowed=False`，调用方必须据此中止；
    2. **审计**：`emit(policy.denied)` → S2-02 链式镜像
       （`AUDIT_MIRROR_TYPES` 已含 `policy.denied`）写入链式台账。
       **本模块不再单独调 `audit.record()`**：S2-03 已定「一动作一记录」不变量，
       再写一次会产生双份记录（分发壳 §八-2 明确禁止）；
    3. **告警**：`agent/security/alerts.py::report_denial`（结构化日志 + 计数 +
       可注入外部通道），PII 只带掩码/HMAC 哈希。

【不双写事件的边界】
    `policy.denied` 只由**本模块**在拒绝时发出；正常审批生命周期事件仍由
    `agent/skills_mgmt/approval.py` 经 `acr.record_approval()` / `events.emit()`
    发出（S2-03 口径），两者不重叠。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional

import agent.security.alerts as alerts_mod
import agent.security.pii as pii_mod
from agent.security.actor_matrix import (
    OP_APPROVE,
    OP_DENY,
    OP_EXECUTE_CAPABILITY,
    OP_FORCE_STAGE,
    OP_MODIFY_POLICY,
    OP_REMOVE_SOURCE,
    OP_SUBMIT_APPROVAL,
    OP_SWITCH_FORGE,
    OP_WRITE_MEMORY,
    PermissionContext,
    PermissionDecision,
    decide,
    infer_actor_type,
    normalize_actor_type,
    normalize_operation,
)
from agent.security.identity import (
    ResolvedIdentity,
    resolve_identity,
)

logger = logging.getLogger("agent.security.approval_guard")

# ════════════════════════════════════════════════════════════
#  动作 → §7.0 操作 的映射（表驱动）
# ════════════════════════════════════════════════════════════

#: 审批状态机动作 → 操作（**优先级最高**：approve/deny 的执行体限制与对象无关）
_ACTION_OPERATIONS: Dict[str, str] = {
    "approve": OP_APPROVE,
    "approved": OP_APPROVE,
    "merge": OP_APPROVE,
    "merged": OP_APPROVE,
    "mark_manual_executed": OP_APPROVE,
    "archived": OP_APPROVE,
    "reject": OP_DENY,
    "rejected": OP_DENY,
    "deny": OP_DENY,
    "expire": OP_DENY,
    "timeout_deny": OP_DENY,
    "submit": OP_SUBMIT_APPROVAL,
    "submitted": OP_SUBMIT_APPROVAL,
}

#: 对象类型 → 统一治理操作（submit 场景据此细化；上表未命中时生效）
_OBJECT_OPERATIONS: Dict[str, str] = {
    "stage.promote": OP_FORCE_STAGE,       # S3-03 真实链路
    "stage": OP_FORCE_STAGE,
    "source": OP_REMOVE_SOURCE,
    "policy": OP_MODIFY_POLICY,
    "strategy": OP_MODIFY_POLICY,
    "forge": OP_SWITCH_FORGE,
    "furnace": OP_SWITCH_FORGE,
    "memory": OP_WRITE_MEMORY,
    "capability": OP_EXECUTE_CAPABILITY,
}

#: 对象类型 → 扩展治理操作映射（`register_object_operation()` 可加行）
_OBJECT_OPERATIONS_EXTENDED: Dict[str, str] = {}

#: 风险解析器签名：(object_type, object_id, payload) -> 风险等级字符串
RiskResolver = Callable[[str, str, Optional[Mapping[str, Any]]], str]
_risk_resolver: Optional[RiskResolver] = None


# ════════════════════════════════════════════════════════════
#  执行体上下文
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ActorContext:
    """审批入口的执行体上下文（**只由身份层/服务端构造**）

    Attributes:
        actor: 执行体标识（human 为登录用户；auto 为技能/任务；sub_agent 为子智能体）。
        actor_type: human/auto/sub_agent；空串 → 按 actor 名推断
            （向后兼容既有调用方，见 `actor_matrix.infer_actor_type`）。
        identity_source: 身份来源口径（`identity.resolve_identity` 产出）。
        scope: 执行体自身 scope。
        session_id: 会话标识（审批面安全：token 会话绑定）。
        actor_ip: 请求来源 IP（**只用于派生掩码+HMAC，绝不落原文**）。
        authorized_capabilities: sub_agent 授权子集（S4-04 授权清单）。
        degraded: 身份是否降级路径（非权威来源）。
    """
    actor: str = ""
    actor_type: str = ""
    identity_source: str = ""
    scope: str = ""
    session_id: str = ""
    actor_ip: str = ""
    authorized_capabilities: FrozenSet[str] = frozenset()
    degraded: bool = False

    def resolved_type(self) -> str:
        if self.actor_type:
            try:
                return normalize_actor_type(self.actor_type)
            except ValueError:
                return str(self.actor_type)
        return infer_actor_type(self.actor)

    def to_permission_context(self) -> PermissionContext:
        return PermissionContext(
            actor=self.actor, actor_type=self.resolved_type(), scope=self.scope,
            authorized_capabilities=self.authorized_capabilities,
            session_id=self.session_id, identity_source=self.identity_source)

    def ip_fields(self) -> Dict[str, Any]:
        """入链/入日志的 IP PII 字段（裁定 B：掩码 + HMAC；原文不出现）"""
        return pii_mod.ip_pii_fields(self.actor_ip)

    def audit_fields(self) -> Dict[str, Any]:
        """审批审计载荷的叶子字段（身份 + PII；与 S2-02/S2-03 口径一致）"""
        fields: Dict[str, Any] = {
            "actor_type": self.resolved_type(),
            "identity_source": self.identity_source,
        }
        if self.session_id:
            fields["session_id"] = self.session_id
        if self.actor_ip:
            fields.update(self.ip_fields())
        return fields


def context_from_identity(identity: ResolvedIdentity, *,
                          actor_ip: str = "",
                          authorized_capabilities: FrozenSet[str] = frozenset(),
                          scope: str = "") -> ActorContext:
    """由身份层解析结果构造执行体上下文"""
    return ActorContext(
        actor=identity.actor, actor_type=identity.actor_type,
        identity_source=identity.identity_source,
        scope=scope or identity.scope, session_id=identity.session_id,
        actor_ip=actor_ip, authorized_capabilities=authorized_capabilities,
        degraded=bool(identity.degraded))


def context_from_request(*, headers: Optional[Mapping[str, str]] = None,
                         cookies: Optional[Mapping[str, str]] = None,
                         remote_addr: str = "", session_id: str = "",
                         token: str = "",
                         authorized_capabilities: FrozenSet[str] = frozenset(),
                         scope: str = "") -> ActorContext:
    """由 HTTP 请求特征构造执行体上下文（**路由侧唯一正确入口**）

    令牌原文只用于映射表比对（`identity.resolve_identity`），不进入上下文。
    """
    from agent.security.identity import extract_bearer_token
    resolved_token = token or extract_bearer_token(headers)
    identity = resolve_identity(
        token=resolved_token, headers=headers, cookies=cookies,
        remote_addr=remote_addr, session_id=session_id)
    return context_from_identity(identity, actor_ip=remote_addr,
                                 authorized_capabilities=authorized_capabilities,
                                 scope=scope)


# ════════════════════════════════════════════════════════════
#  操作与风险解析
# ════════════════════════════════════════════════════════════

def register_object_operation(object_type: str, operation: str) -> None:
    """登记「对象类型 → 治理操作」（**加行不改逻辑**）"""
    key = str(object_type or "").strip().lower()
    if not key:
        raise ValueError("object_type 不能为空")
    _OBJECT_OPERATIONS_EXTENDED[key] = normalize_operation(operation)


def reset_object_operations() -> None:
    """清除扩展映射（测试隔离）"""
    _OBJECT_OPERATIONS_EXTENDED.clear()


def operation_for(object_type: str, action: str = "", *,
                  default: str = OP_SUBMIT_APPROVAL) -> str:
    """把 (object_type, action) 映射为 §7.0 操作

    优先级：**动作表** > **对象表** > default。
    - `approve` / `reject` / `merge` … 一律映射到 §7.0 的审批行（human 专属），
      **与对象类型无关**（否则「换个 object_type 就能绕过」）；
    - `submit` 一类进入审批队列的动作，按对象类型细化到「强制推进 stage」
      「修改策略」等治理行（据此判定提交方是否被迫为 human）。
    """
    act = str(action or "").strip().lower()
    if act in _ACTION_OPERATIONS:
        op = _ACTION_OPERATIONS[act]
        if op != OP_SUBMIT_APPROVAL:
            return op
        return _object_operation(object_type, default=OP_SUBMIT_APPROVAL)
    return _object_operation(object_type, default=normalize_operation(default))


def _object_operation(object_type: str, *, default: str) -> str:
    key = str(object_type or "").strip().lower()
    if key in _OBJECT_OPERATIONS_EXTENDED:
        return _OBJECT_OPERATIONS_EXTENDED[key]
    if key in _OBJECT_OPERATIONS:
        return _OBJECT_OPERATIONS[key]
    # 前缀匹配（如 `skill:xxx` / `capability.foo`）
    for prefix, op in _OBJECT_OPERATIONS.items():
        if key.startswith(f"{prefix}.") or key.startswith(f"{prefix}:"):
            return op
    return default


def set_risk_resolver(resolver: Optional[RiskResolver]) -> Optional[RiskResolver]:
    """注入风险解析器（如 S1-02 descriptor registry 的 risk_level 查询）"""
    global _risk_resolver
    previous = _risk_resolver
    _risk_resolver = resolver
    return previous


def resolve_risk(object_type: str, object_id: str,
                 payload: Optional[Mapping[str, Any]] = None,
                 *, explicit: str = "") -> str:
    """解析审批对象风险等级（供 §5.7⑦ destructive 二次认证判定）

    顺序：显式 > 注入的解析器 > 载荷叶子字段（`risk` / `risk_level`）> ""（未知）。
    未知**不**当作 destructive（否则一切审批都要二次认证，形式化失效）；
    destructive 的真实来源是 S1-02 descriptor 的 `trust.risk_level`
    （经 `set_risk_resolver` 接线，本任务不硬编码跨模块依赖）。
    """
    if explicit:
        return str(explicit).strip().lower()
    if _risk_resolver is not None:
        try:
            got = _risk_resolver(str(object_type or ""), str(object_id or ""), payload)
            if got:
                return str(got).strip().lower()
        except Exception as e:  # noqa: BLE001 风险解析失败不阻断（按未知处理）
            logger.debug("[Guard] 风险解析器失败（按未知处理）: %s", e)
    for key in ("risk", "risk_level"):
        val = (payload or {}).get(key)
        if val:
            return str(val).strip().lower()
    return ""


# ════════════════════════════════════════════════════════════
#  判定 + 越权三件套
# ════════════════════════════════════════════════════════════

def authorize(*, operation: str, actor_ctx: ActorContext,
              object_type: str = "", object_id: str = "",
              target_scope: str = "", memory_layer: str = "",
              reason: str = "", second_factor_ok: bool = False,
              risk: str = "", enforce_preconditions: bool = True,
              record_id: str = "", source: str = "agent",
              report: bool = True) -> PermissionDecision:
    """判定一次操作（拒绝时默认执行「告警 + 审计事件」）

    Args:
        report: 是否在拒绝时执行越权三件套（**仅测试与非治理路径可置 False**）。
    """
    decision = decide(
        operation, actor_ctx.to_permission_context(),
        object_type=object_type, object_id=object_id,
        target_scope=target_scope, memory_layer=memory_layer,
        reason=reason, second_factor_ok=second_factor_ok, risk=risk,
        enforce_preconditions=enforce_preconditions)
    if not decision.allowed and report and decision.alert_on_deny:
        report_denial(decision, actor_ctx=actor_ctx, record_id=record_id,
                      source=source)
    return decision


def authorize_approval_action(*, action: str, object_type: str = "",
                              object_id: str = "", actor_ctx: Optional[ActorContext] = None,
                              actor: str = "", record_id: str = "",
                              reason: str = "", risk: str = "",
                              second_factor_ok: bool = False,
                              payload: Optional[Mapping[str, Any]] = None,
                              source: str = "agent",
                              enforce_preconditions: Optional[bool] = None,
                              report: bool = True) -> PermissionDecision:
    """审批状态机动作的统一判定入口（`ApprovalFlow` 与路由共用）

    未提供 `actor_ctx` 时按 `actor` 名构造**最小上下文**（兼容既有调用方：
    既有调用点传的都是人类标识，行为不变）。

    Args:
        enforce_preconditions: None → 由动作推导（`submit` 类动作只判矩阵格与
            范围口径；`approve`/`reject`/`merge` 等生效动作判全部前置条件）。
            显式传值优先（`ApprovalFlow.submit` 传 False）。
    """
    ctx = actor_ctx if actor_ctx is not None else ActorContext(
        actor=str(actor or ""), identity_source="legacy_actor_param")
    operation = operation_for(object_type, action)
    resolved_risk = resolve_risk(object_type, object_id, payload, explicit=risk)
    if enforce_preconditions is None:
        enforce_preconditions = str(action or "").strip().lower() not in (
            "submit", "submitted")
    return authorize(
        operation=operation, actor_ctx=ctx, object_type=object_type,
        object_id=object_id, reason=reason, second_factor_ok=second_factor_ok,
        risk=resolved_risk, record_id=record_id, source=source,
        enforce_preconditions=bool(enforce_preconditions),
        report=report)


def report_denial(decision: PermissionDecision, *, actor_ctx: ActorContext,
                  record_id: str = "", source: str = "agent",
                  extra: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """越权三件套之二/三：**审计事件 + 告警**

    - 审计：`emit(policy.denied)` → S2-02 链式镜像（**唯一**审计写入点，
      本模块不调 `audit.record()`，避免与镜像规则双写）；
    - 告警：`alerts.report_denial`（日志 + 计数 + 可注入通道）。

    Returns:
        告警事件 dict（供调用方回填响应体/测试断言）。
    """
    ip_fields = actor_ctx.ip_fields() if actor_ctx.actor_ip else {}
    payload: Dict[str, Any] = {
        "operation": decision.operation,
        "actor": decision.actor or actor_ctx.actor,
        "actor_type": decision.actor_type or actor_ctx.resolved_type(),
        "identity_source": decision.identity_source or actor_ctx.identity_source,
        "reason": decision.reason,
        "object_type": decision.object_type,
        "object_id": decision.object_id,
        "record_id": str(record_id or ""),
        "matrix_hit": bool(decision.matrix_hit),
        "denied_by_matrix": bool(decision.denied_by_matrix),
        "scope": decision.scope,
        "requires_second_factor": bool(decision.requires_second_factor),
        "requires_reason": bool(decision.requires_reason),
        "source": str(source or "agent"),
    }
    if actor_ctx.session_id:
        payload["session_id"] = actor_ctx.session_id
    payload.update({k: v for k, v in (extra or {}).items() if v is not None})

    # 越权序号：**每次尝试都是独立事实**，故并入幂等键 —— 否则同操作/同对象的重复
    # 越权会被事件侧幂等吞掉，链上只剩第一条（本任务实现期实测到的真实缺陷）。
    seq = alerts_mod.next_denial_seq()
    payload["attempt_seq"] = seq
    payload.update(ip_fields)

    # ── 审计（经事件镜像；唯一写入点）──
    try:
        from agent.observability.events import EV_POLICY_DENIED, emit
        emit(EV_POLICY_DENIED, payload,
             actor=str(decision.actor_type or actor_ctx.resolved_type() or "system"),
             idempotency_key=alerts_mod.denial_event_key(
                 seq, decision.operation, decision.object_type,
                 decision.object_id, record_id))
    except Exception as e:  # noqa: BLE001 事件失败不得阻断审批主路径
        logger.debug("[Guard] policy.denied 事件发送失败: %s", e)

    # ── 告警 ──
    return alerts_mod.report_denial(
        operation=decision.operation, actor=decision.actor or actor_ctx.actor,
        actor_type=decision.actor_type or actor_ctx.resolved_type(),
        reason=decision.reason, object_type=decision.object_type,
        object_id=decision.object_id, identity_source=(
            decision.identity_source or actor_ctx.identity_source),
        session_id=actor_ctx.session_id, record_id=record_id,
        ip_fields=ip_fields, seq=seq,
        extra={"denied_by_matrix": bool(decision.denied_by_matrix),
               "matrix_hit": bool(decision.matrix_hit),
               "requires_second_factor": bool(decision.requires_second_factor),
               "requires_reason": bool(decision.requires_reason)})


def with_second_factor(actor_ctx: ActorContext, ok: bool) -> ActorContext:
    """派生一个「已通过/未通过二次认证」的上下文副本（不可变对象）

    二次认证结果**不放进 actor_type**（那是身份事实），只作为判定入参传递；
    本函数仅用于调用方在等待二次认证期间保留原上下文语义。
    """
    return replace(actor_ctx, actor_type=actor_ctx.resolved_type())


__all__ = [
    "ActorContext", "context_from_identity", "context_from_request",
    "operation_for", "register_object_operation", "reset_object_operations",
    "authorize", "authorize_approval_action", "report_denial",
    "resolve_risk", "set_risk_resolver", "RiskResolver",
]
