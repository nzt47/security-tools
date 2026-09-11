"""记忆租户隔离（v7.2 P7.2-08 铁律）——隔离矩阵、写入守卫、召回可见性

设计来源
--------
v7.2 P7.2-08「IDE 租户映射与记忆隔离」::

    workspace(repository) = 逻辑租户（tenant_id = workspace-hash）
    subject_id            = 登录用户
    事实记忆/策略记忆 按租户隔离
    偏好记忆 跟随 subject 跨租户携带
    铁律：绝不允许个人偏好污染企业策略记忆（企业阶段策略记忆为 org 级只读下发）

三条铁律的可执行形态（本模块 `ISOLATION_MATRIX` + `TenancyPolicy`）：

============  ================  ==================  ===================  ==========
记忆层         隔离载体           跨租户可见            个人通道可写          作用域
============  ================  ==================  ===================  ==========
working       tenant_id         ✗                    ✓                    project
fact          tenant_id         ✗                    ✓                    project
preference    subject_id        ✓（随 subject 携带）  ✓                    global
strategy      tenant_id(org)    ✓（org 级只读下发）    ✗（写入被拒）          project/org
============  ================  ==================  ===================  ==========

写入守卫（hard constraint 1）
-----------------------------
缺 tenant/workspace 字段时**拒绝**（默认，``MissingTenancyError`` 口径对齐 S2-01
``MissingWorkspaceError``）或**显式降级并标注**（``MEMORY_TENANCY_ALLOW_DEGRADE=1``
显式开启）——**绝不静默落全局**。降级条目落 ``__unscoped__`` 命名空间并置
``degraded=True``，默认召回**不包含**（避免"降级"变成静默全局泄漏）。

环境变量
--------
- ``MEMORY_TENANCY_ALLOW_DEGRADE``: ``1`` 时缺租户字段改为显式降级（默认关闭，即拒绝）
"""

import enum
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from agent.logging_utils import log_dict
from agent.memory.taxonomy import (
    GLOBAL_SCOPE,
    IsolationKind,
    LAYER_POLICY,
    MemoryEntry,
    MemoryEntryError,
    MemoryType,
    coerce_memory_type,
    default_scope_for,
    scope_workspace_id,
    sort_for_recall,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ORG_TENANT",
    "DEGRADED_TENANT",
    "DEFAULT_TENANT_PLACEHOLDER",
    "TenancyError",
    "MissingTenancyError",
    "WriteChannel",
    "WriteDisposition",
    "IsolationKind",
    "TenancyContext",
    "WriteDecision",
    "MemoryWriteRejected",
    "IsolationRule",
    "ISOLATION_MATRIX",
    "TenancyPolicy",
    "build_tenancy_policy",
    "get_tenancy_policy",
    "reset_tenancy_policy",
    "tenant_id_for_workspace",
    "resolve_tenancy",
    "tenancy_context",
    "isolation_matrix_rows",
]

#: org 级策略记忆所属的租户命名空间（企业侧只读下发）
ORG_TENANT = "__org__"

#: 缺租户字段时显式降级的落点命名空间
DEGRADED_TENANT = "__unscoped__"

#: TraceContext.tenant_id 的默认占位值（非真实租户，S2-01 口径）
DEFAULT_TENANT_PLACEHOLDER = "default"

#: 是否允许"显式降级"（默认关闭 ⇒ 缺租户字段直接拒绝）
ENV_ALLOW_DEGRADE = "MEMORY_TENANCY_ALLOW_DEGRADE"


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════


class TenancyError(MemoryEntryError):
    """租户上下文非法（缺租户 / 缺工作区 / 越界访问）"""

    def __init__(self, message: str, *, code: str = "TENANCY") -> None:
        super().__init__(message)
        self.code = code


class MissingTenancyError(TenancyError):
    """缺 tenant/workspace 字段（口径对齐 S2-01 ``MissingWorkspaceError``）"""

    def __init__(self, field_name: str, memory_type: Any = "") -> None:
        mt = ""
        try:
            mt = coerce_memory_type(memory_type).value if memory_type else ""
        except MemoryEntryError:
            mt = ""
        super().__init__(
            "记忆写入缺 %s（P7.2-08 不变量：%s层必须携带租户上下文）"
            % (field_name, mt or "记忆"),
            code="MISSING_" + str(field_name).upper(),
        )
        self.field_name = str(field_name)


class MemoryWriteRejected(TenancyError):
    """记忆写入被隔离策略拒绝（携带判定结果，便于调用方与报告取证）"""

    def __init__(self, decision: "WriteDecision") -> None:
        super().__init__(
            "记忆写入被拒（type=%s, reason=%s）"
            % (decision.memory_type.value, decision.reason or "unspecified"),
            code="MEMORY_WRITE_REJECTED",
        )
        self.decision = decision


# ════════════════════════════════════════════════════════════
#  枚举与值对象
# ════════════════════════════════════════════════════════════


class WriteChannel(str, enum.Enum):
    """写入通道 —— 决定策略层是否可写（P7.2-08 铁律）"""

    USER = "user"      # 个人通道（登录用户 / 个人偏好）：**不可写策略层**
    ORG = "org"        # 企业通道：org 级策略记忆只读下发
    SYSTEM = "system"  # 系统通道（迁移 / 回填 / 做梦聚合）


class WriteDisposition(str, enum.Enum):
    """写入判定结果"""

    ACCEPT = "accept"    # 允许原样写入
    DEGRADE = "degrade"  # 显式降级并标注（缺租户字段时的可选出口）
    REJECT = "reject"    # 拒绝（默认出口）


# 复用 taxonomy 的隔离维度枚举（单一来源，避免两套语义漂移）


@dataclass(frozen=True)
class TenancyContext:
    """归一化租户上下文（P7.2-08：tenant_id / workspace_id / subject_id）

    Attributes:
        tenant_id: 逻辑租户（默认 = workspace-hash，来源 S2-01 `derive_workspace_id`）
        workspace_id: 工作区哈希（``ws_<16hex>``，P7.1-19 不变量）
        subject_id: 登录用户标识
        trace_id / task_id: 关联字段（可空）
        source: 上下文来源（explicit / trace_context / workspace_root / none）
        tenant_derived: tenant_id 是否由 workspace-hash 派生（P7.2-08 默认口径）
    """

    tenant_id: str = ""
    workspace_id: str = ""
    subject_id: str = ""
    trace_id: str = ""
    task_id: str = ""
    source: str = "explicit"
    tenant_derived: bool = False

    @property
    def has_tenant(self) -> bool:
        return bool(self.tenant_id)

    @property
    def has_workspace(self) -> bool:
        return bool(self.workspace_id)

    @property
    def has_subject(self) -> bool:
        return bool(self.subject_id)

    def as_dict(self) -> Dict[str, str]:
        """叶子字段快照（不搬运 live 对象，§ 动态插件纪律的同类要求）"""
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "source": self.source,
        }


@dataclass(frozen=True)
class WriteDecision:
    """写入判定（ACCEPT / DEGRADE / REJECT + 取证字段）"""

    disposition: WriteDisposition
    memory_type: MemoryType
    tenant_id: str = ""
    subject_id: str = ""
    scope: str = ""
    org_level: bool = False
    reason: str = ""
    warnings: Tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.disposition is not WriteDisposition.REJECT

    @property
    def degraded(self) -> bool:
        return self.disposition is WriteDisposition.DEGRADE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "type": self.memory_type.value,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "scope": self.scope,
            "org_level": self.org_level,
            "reason": self.reason,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class IsolationRule:
    """隔离矩阵的单行声明（报告与单测共用的可执行口径）"""

    memory_type: MemoryType
    carrier: str                    # 隔离载体：tenant_id / subject_id
    write_channels: Tuple[WriteChannel, ...]
    cross_tenant_visible: bool      # 是否跨租户可见（偏好=True；org 级策略=True 只读）
    read_only: bool                 # 是否只读（org 级策略下发）
    scope_kind: str
    note: str = ""

    @property
    def subject_write_forbidden(self) -> bool:
        return WriteChannel.USER not in self.write_channels

    def as_dict(self) -> Dict[str, Any]:
        return {
            "type": self.memory_type.value,
            "carrier": self.carrier,
            "write_channels": [c.value for c in self.write_channels],
            "subject_write_forbidden": self.subject_write_forbidden,
            "cross_tenant_visible": self.cross_tenant_visible,
            "read_only": self.read_only,
            "scope_kind": self.scope_kind,
            "note": self.note,
        }


#: 隔离矩阵（P7.2-08 —— 三条铁律的声明式表达，单测直接断言本表）
ISOLATION_MATRIX: Tuple[IsolationRule, ...] = (
    IsolationRule(
        memory_type=MemoryType.WORKING,
        carrier="tenant_id",
        write_channels=(WriteChannel.USER, WriteChannel.SYSTEM),
        cross_tenant_visible=False,
        read_only=False,
        scope_kind="project",
        note="工作记忆：任务内短时记忆，短 TTL；随租户隔离",
    ),
    IsolationRule(
        memory_type=MemoryType.FACT,
        carrier="tenant_id",
        write_channels=(WriteChannel.USER, WriteChannel.SYSTEM),
        cross_tenant_visible=False,
        read_only=False,
        scope_kind="project",
        note="事实记忆：项目约束；按租户隔离（跨租户不可见反例口径）",
    ),
    IsolationRule(
        memory_type=MemoryType.PREFERENCE,
        carrier="subject_id",
        write_channels=(WriteChannel.USER, WriteChannel.SYSTEM),
        cross_tenant_visible=True,
        read_only=False,
        scope_kind="global",
        note="偏好记忆：跟随 subject_id 跨租户携带；恒 global 作用域",
    ),
    IsolationRule(
        memory_type=MemoryType.STRATEGY,
        carrier="tenant_id(%s)" % ORG_TENANT,
        write_channels=(WriteChannel.ORG, WriteChannel.SYSTEM),
        cross_tenant_visible=True,
        read_only=True,
        scope_kind="org",
        note="企业策略记忆：org 级只读下发；个人通道写入被拒（绝不污染策略层）",
    ),
)


def isolation_matrix_rows() -> List[Dict[str, Any]]:
    """隔离矩阵的行式视图（供验收报告与单测复用）"""
    return [rule.as_dict() for rule in ISOLATION_MATRIX]


# ════════════════════════════════════════════════════════════
#  租户上下文解析
# ════════════════════════════════════════════════════════════


def tenant_id_for_workspace(workspace_root: str) -> str:
    """P7.2-08：workspace(repository) = 逻辑租户 → ``tenant_id = workspace-hash``

    直接复用 S2-01 的 ``derive_workspace_id``（同路径恒同哈希）。
    """
    from agent.observability.trace_v2 import derive_workspace_id

    return derive_workspace_id(workspace_root)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def resolve_tenancy(
    *,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    workspace_root: str = "",
    subject_id: Optional[str] = None,
    trace_id: str = "",
    task_id: str = "",
    context: Any = None,
    use_current_context: bool = True,
) -> TenancyContext:
    """归一化租户上下文

    解析顺序（显式 > 传入 context > 当前 TraceContext > workspace_root 派生）::

        workspace_id : 显式 > context.workspace_id > derive_workspace_id(workspace_root)
        tenant_id    : 显式 > context.tenant_id（非 "default" 占位）> workspace_id（P7.2-08 派生）
        subject_id   : 显式 > context.subject_id

    ``tenant_id`` 走 P7.2-08 派生时 ``tenant_derived=True``；若显式给定与
    context 不同的 tenant_id（多工作区企业租户），原样保留并置 ``tenant_derived=False``。
    """
    tc = context
    if tc is None and use_current_context:
        from agent.observability.trace_v2 import TraceContext

        tc = TraceContext.current()

    explicit = any(
        _clean(v)
        for v in (tenant_id, workspace_id, workspace_root, subject_id, trace_id, task_id)
    )
    if explicit:
        source = "explicit"
    elif tc is not None:
        source = "trace_context"
    elif workspace_root:
        source = "workspace_root"
    else:
        source = "none"

    ws = _clean(workspace_id) or _clean(getattr(tc, "workspace_id", ""))
    if not ws and _clean(workspace_root):
        ws = tenant_id_for_workspace(_clean(workspace_root))

    tid = _clean(tenant_id)
    tenant_derived = False
    if not tid:
        tc_tenant = _clean(getattr(tc, "tenant_id", ""))
        if tc_tenant and tc_tenant != DEFAULT_TENANT_PLACEHOLDER and tc_tenant != ws:
            tid = tc_tenant
        elif ws:
            tid = ws
            tenant_derived = True  # P7.2-08：tenant_id = workspace-hash
    subj = _clean(subject_id)
    if not subj and subject_id is None:
        subj = _clean(getattr(tc, "subject_id", ""))

    # 只给了 workspace-hash 形态的 tenant_id 时，补出 workspace_id（便于 project 作用域判定）
    if not ws and tid.startswith("ws_"):
        ws = tid

    return TenancyContext(
        tenant_id=tid,
        workspace_id=ws,
        subject_id=subj,
        trace_id=_clean(trace_id) or _clean(getattr(tc, "trace_id", "")),
        task_id=_clean(task_id) or _clean(getattr(tc, "task_id", "")),
        source=source,
        tenant_derived=tenant_derived,
    )


@contextmanager
def tenancy_context(**kwargs: Any) -> Iterator[TenancyContext]:
    """以临时 ``TraceContext`` 承载租户上下文（测试与批处理用）

    用法::

        with tenancy_context(workspace_root="/repo/a", subject_id="alice") as tctx:
            ...  # 期间 TraceContext.current() 可见同一租户
    """
    from agent.observability.trace_v2 import TraceContext

    tctx = resolve_tenancy(use_current_context=False, **kwargs)
    carrier = TraceContext(
        tenant_id=tctx.tenant_id or DEFAULT_TENANT_PLACEHOLDER,
        workspace_id=tctx.workspace_id,
        subject_id=tctx.subject_id,
        trace_id=tctx.trace_id,
        task_id=tctx.task_id,
    )
    token = carrier.enter()
    try:
        yield tctx
    finally:
        TraceContext.exit(token)


# ════════════════════════════════════════════════════════════
#  隔离策略（写入守卫 + 召回可见性 + 召回优先级）
# ════════════════════════════════════════════════════════════


@dataclass
class TenancyPolicy:
    """租户隔离策略（P7.2-08 的可执行实现）

    Attributes:
        allow_missing_tenancy: 缺租户字段时是否允许**显式降级**（默认 False ⇒ 拒绝）
        include_degraded_by_default: 默认召回是否包含降级条目（默认 False ⇒ 隔离在
            ``__unscoped__`` 命名空间，避免"降级"变成静默全局泄漏）
    """

    allow_missing_tenancy: bool = False
    include_degraded_by_default: bool = False

    # ── 写入守卫 ──

    def decide_write(
        self,
        memory_type: Any,
        ctx: TenancyContext,
        *,
        channel: Any = WriteChannel.USER,
        org_level: Optional[bool] = None,
        scope: str = "",
    ) -> WriteDecision:
        """写入判定（ACCEPT / DEGRADE / REJECT）

        规则（顺序即优先级）:

        1. ``strategy`` + 个人通道 ⇒ **REJECT**（铁律：个人偏好绝不污染企业策略记忆）
        2. ``strategy`` + org/system 通道 ⇒ ACCEPT，落 org 命名空间且 ``org_level=True``
        3. ``preference`` 显式要求 ``org_level`` ⇒ **REJECT**（偏好不得升格为 org 级）
        4. ``preference`` 缺 ``subject_id`` ⇒ 拒绝 / 显式降级
        5. 租户隔离层（working/fact/strategy）缺 ``tenant_id`` ⇒ 拒绝 / 显式降级；
           project 作用域还需 ``workspace_id``
        """
        mt = coerce_memory_type(memory_type)
        rule = _rule_for(mt)
        ch = _coerce_channel(channel)

        # 1) 策略层个人写入 → 拒绝
        if rule.subject_write_forbidden and ch is WriteChannel.USER:
            return WriteDecision(
                disposition=WriteDisposition.REJECT,
                memory_type=mt,
                tenant_id=ctx.tenant_id,
                subject_id=ctx.subject_id,
                scope=scope or default_scope_for(mt, ctx.workspace_id),
                reason="strategy_layer_readonly",
                warnings=(
                    "企业策略记忆为 org 级只读下发（P7.2-08 铁律），个人通道写入被拒",
                ),
            )

        # 2) 策略层 org/system 通道 → org 级只读下发（org_level=False 时退化为租户内策略）
        if mt is MemoryType.STRATEGY and ch in (WriteChannel.ORG, WriteChannel.SYSTEM):
            want_org = True if org_level is None else bool(org_level)
            if ch is WriteChannel.ORG:
                want_org = True
            if want_org:
                return WriteDecision(
                    disposition=WriteDisposition.ACCEPT,
                    memory_type=mt,
                    tenant_id=ORG_TENANT,
                    subject_id="",
                    scope=GLOBAL_SCOPE,
                    org_level=True,
                    reason="org_level_delivery",
                )

        # 3) 偏好不得升格为 org 级
        if mt is MemoryType.PREFERENCE and org_level:
            return WriteDecision(
                disposition=WriteDisposition.REJECT,
                memory_type=mt,
                tenant_id=ctx.tenant_id,
                subject_id=ctx.subject_id,
                scope=GLOBAL_SCOPE,
                reason="preference_cannot_be_org_level",
                warnings=("偏好记忆不得标记 org 级（org 级仅限企业策略记忆）",),
            )

        # 4) 偏好层：随 subject 跨租户携带 ⇒ 必须有 subject_id
        if mt is MemoryType.PREFERENCE:
            if not ctx.has_subject:
                return self._missing(mt, ctx, "subject_id")
            return WriteDecision(
                disposition=WriteDisposition.ACCEPT,
                memory_type=mt,
                tenant_id=ctx.tenant_id,
                subject_id=ctx.subject_id,
                scope=GLOBAL_SCOPE,  # 偏好恒 global（跨租户携带的物理前提）
                reason="subject_carried",
            )

        # 5) 租户隔离层：必须有 tenant_id；project 作用域还需 workspace_id
        if not ctx.has_tenant:
            return self._missing(mt, ctx, "tenant_id")
        policy = LAYER_POLICY[mt]
        explicit_scope = str(scope or "").strip()
        if not explicit_scope:
            # 未显式指定作用域：按层默认推导。project 类层缺 workspace 时**拒绝**
            # （不静默降为 global —— 那等于悄悄放宽隔离面，见 hard constraint 1）
            if policy.scope_kind == "project" and not ctx.has_workspace:
                return self._missing(mt, ctx, "workspace_id")
            effective_scope = default_scope_for(mt, ctx.workspace_id)
        else:
            effective_scope = explicit_scope
        if effective_scope.startswith("project:"):
            if not ctx.has_workspace:
                return self._missing(mt, ctx, "workspace_id")
            # 越界守卫：显式 scope 必须与上下文 workspace 一致，否则等于把事实写进别人的租户
            explicit_ws = scope_workspace_id(effective_scope)
            if explicit_ws is not None and explicit_ws != _clean(ctx.workspace_id):
                return WriteDecision(
                    disposition=WriteDisposition.REJECT,
                    memory_type=mt,
                    tenant_id=ctx.tenant_id,
                    subject_id=ctx.subject_id,
                    scope=effective_scope,
                    reason="scope_workspace_mismatch",
                    warnings=(
                        "scope %s 与上下文 workspace %s 不一致：拒绝跨工作区写入（P7.2-08）"
                        % (effective_scope, _clean(ctx.workspace_id) or "<empty>"),
                    ),
                )
        return WriteDecision(
            disposition=WriteDisposition.ACCEPT,
            memory_type=mt,
            tenant_id=ctx.tenant_id,
            subject_id=ctx.subject_id,
            scope=effective_scope,
            org_level=False,
            reason="tenant_isolated",
        )

    def _missing(self, mt: MemoryType, ctx: TenancyContext, field_name: str) -> WriteDecision:
        """缺租户字段的统一出口：默认拒绝；显式开启才降级（绝不静默落全局）"""
        degrade = bool(self.allow_missing_tenancy)
        warning = (
            "记忆写入缺 %s（层=%s）：已**显式降级**到 %s 命名空间并标注 degraded，"
            "默认召回不包含（P7.2-08：不得静默落全局）"
            % (field_name, mt.value, DEGRADED_TENANT)
        )
        if degrade:
            logger.warning(log_dict({
                "module_name": "memory.tenancy",
                "action": "write.degrade",
                "msg": "[tenancy] " + warning,
            }))
            return WriteDecision(
                disposition=WriteDisposition.DEGRADE,
                memory_type=mt,
                tenant_id=DEGRADED_TENANT,
                subject_id=ctx.subject_id,
                scope=GLOBAL_SCOPE,
                reason="missing_%s_degraded" % field_name,
                warnings=(warning,),
            )
        return WriteDecision(
            disposition=WriteDisposition.REJECT,
            memory_type=mt,
            tenant_id=ctx.tenant_id,
            subject_id=ctx.subject_id,
            scope="",
            reason="missing_%s" % field_name,
            warnings=(
                "记忆写入缺 %s（层=%s）：已拒绝（P7.2-08 不变量；如需降级请显式开启 %s=1）"
                % (field_name, mt.value, ENV_ALLOW_DEGRADE),
            ),
        )

    # ── 读取可见性（隔离矩阵的读取侧）──

    def is_visible(
        self,
        entry: MemoryEntry,
        ctx: TenancyContext,
        *,
        include_expired: bool = False,
        include_forget_candidates: bool = True,
        include_degraded: Optional[bool] = None,
        now: Optional[float] = None,
    ) -> bool:
        """条目对当前租户上下文是否可见（P7.2-08 三条铁律的判定函数）

        - 偏好（preference）：**按 subject_id** 判定 ⇒ 同 subject 跨租户可见
        - 事实/工作（fact/working）：**按 tenant_id** 判定 ⇒ 跨租户不可见
        - 策略（strategy）：``org_level`` 条目 org 内**只读可见**；其余按 tenant_id
        - 降级条目（``degraded``）：默认隔离不可见（避免静默全局泄漏）
        """
        if entry is None:
            return False
        allow_degraded = (
            self.include_degraded_by_default if include_degraded is None else bool(include_degraded)
        )
        if entry.degraded:
            # 降级条目隔离在 __unscoped__ 命名空间：默认不可见，显式 include_degraded 才可见
            return bool(allow_degraded)
        if not include_expired and entry.is_expired(now):
            return False
        if not include_forget_candidates and entry.forget_candidate:
            return False

        mt = coerce_memory_type(entry.type)

        # 偏好层：跟随 subject 跨租户携带（与 tenant_id 无关）
        if mt is MemoryType.PREFERENCE:
            return bool(ctx.has_subject) and _clean(entry.subject_id) == _clean(ctx.subject_id)

        if not ctx.has_tenant:
            return False

        # org 级策略记忆：org 内任一租户只读可见（P7.2-08 只读下发）
        if mt is MemoryType.STRATEGY and entry.is_org_level:
            return True

        if _clean(entry.tenant_id) != _clean(ctx.tenant_id):
            return False

        ws = scope_workspace_id(entry.scope)
        if ws is not None:
            # 项目作用域：上下文必须携带同一 workspace（缺 workspace ⇒ 从严不可见）
            if not ctx.has_workspace or ws != _clean(ctx.workspace_id):
                return False
        return True

    def visible_entries(
        self,
        entries: Sequence[MemoryEntry],
        ctx: TenancyContext,
        **kwargs: Any,
    ) -> List[MemoryEntry]:
        """过滤出可见条目（保持入参顺序）"""
        return [e for e in (entries or []) if self.is_visible(e, ctx, **kwargs)]

    def recall(
        self,
        entries: Sequence[MemoryEntry],
        ctx: TenancyContext,
        *,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> List[MemoryEntry]:
        """召回 = 可见性过滤 + §4.3 优先级排序（+ 可选截断）"""
        visible = sort_for_recall(self.visible_entries(entries, ctx, **kwargs))
        if limit is not None and limit >= 0:
            return visible[:limit]
        return visible

    # ── 只读判定（org 级策略下发）──

    def is_readonly(
        self,
        entry: MemoryEntry,
        ctx: TenancyContext,
        *,
        channel: Any = WriteChannel.USER,
    ) -> bool:
        """条目对当前通道是否只读

        P7.2-08 的铁律约束的是**个人通道**（"绝不允许个人偏好污染企业策略记忆"），
        故：

        - org 级下发条目：个人通道只读（读得到、改不了）；org/system 治理通道可改
          （否则组织无法更新自己下发的策略记忆，也无法执行遗忘）；
        - 策略层条目：个人通道一律只读。
        """
        ch = _coerce_channel(channel)
        if ch is not WriteChannel.USER:
            return False
        if entry.is_org_level:
            return True
        return coerce_memory_type(entry.type) is MemoryType.STRATEGY

    def require_writable(self, entry: MemoryEntry, ctx: TenancyContext, *,
                         channel: Any = WriteChannel.USER) -> None:
        """条目不可写时抛 ``MemoryWriteRejected``（org 级只读下发的修改守卫）"""
        if self.is_readonly(entry, ctx, channel=channel):
            raise MemoryWriteRejected(WriteDecision(
                disposition=WriteDisposition.REJECT,
                memory_type=coerce_memory_type(entry.type),
                tenant_id=ctx.tenant_id,
                subject_id=ctx.subject_id,
                scope=entry.scope,
                org_level=entry.is_org_level,
                reason="org_level_readonly",
                warnings=("org 级策略记忆只读下发，禁止就地修改（P7.2-08）",),
            ))


def _rule_for(memory_type: MemoryType) -> IsolationRule:
    for rule in ISOLATION_MATRIX:
        if rule.memory_type is memory_type:
            return rule
    raise TenancyError("隔离矩阵缺少层 %s" % memory_type.value, code="MATRIX_INCOMPLETE")


def _coerce_channel(channel: Any) -> WriteChannel:
    if isinstance(channel, WriteChannel):
        return channel
    key = str(channel or "").strip().lower()
    for member in WriteChannel:
        if member.value == key:
            return member
    return WriteChannel.USER


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def build_tenancy_policy(
    *,
    allow_missing_tenancy: Optional[bool] = None,
    include_degraded_by_default: Optional[bool] = None,
) -> TenancyPolicy:
    """构造租户策略；未显式给定的项走环境变量（默认：拒绝 + 不含降级）"""
    return TenancyPolicy(
        allow_missing_tenancy=(
            _env_flag(ENV_ALLOW_DEGRADE, False)
            if allow_missing_tenancy is None else bool(allow_missing_tenancy)
        ),
        include_degraded_by_default=bool(include_degraded_by_default or False),
    )


_TENANCY_POLICY: Optional[TenancyPolicy] = None


def get_tenancy_policy() -> TenancyPolicy:
    """进程级默认租户策略（首次访问按环境变量构造）

    注意：环境变量只在首次构造时读取；测试请用 ``reset_tenancy_policy()``
    或在构造 ``LayeredMemoryStore`` 时显式注入策略。
    """
    global _TENANCY_POLICY
    if _TENANCY_POLICY is None:
        _TENANCY_POLICY = build_tenancy_policy()
    return _TENANCY_POLICY


def reset_tenancy_policy(policy: Optional[TenancyPolicy] = None) -> None:
    """复位默认租户策略（测试专用）"""
    global _TENANCY_POLICY
    _TENANCY_POLICY = policy
