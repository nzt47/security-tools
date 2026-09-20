"""Actor 权限矩阵（v7.2 §7.0）——**后端单表校验的唯一权威**

【任务定位】
    v7.2 §7.0 把三类执行体（human / auto(skill) / sub_agent）的权限边界写成
    一张矩阵。本模块是该矩阵在云枢的**唯一落点**：所有审批入口、capability
    执行闸门、记忆写入闸门都经 `decide()` 判定，**前端不拥有额外权限**
    （前端传来的 actor/actor_type 一律不可信，见 `agent/security/approval_guard.py`）。

【不易（矩阵核心行，逐字对齐 §7.0）】
    | 操作                         | human | auto(skill)   | sub_agent |
    |------------------------------|-------|---------------|-----------|
    | 查看轨迹/记忆/面板           | ✅    | 仅自身 scope  | ❌        |
    | 审批 Approve/Deny            | ✅    | ❌            | ❌        |
    | 切换熔炉/修改策略            | ✅（二次认证/RFC） | ❌      | ❌        |
    | 强制推进 stage / 摘除来源    | ✅（reason 必填+审计） | ❌   | ❌        |
    | 执行 capability              | ✅    | ✅ scope 内   | ✅ 授权子集 |
    | 写入记忆                     | ✅    | 仅工作记忆    | ❌        |

【变易（表驱动：加行不改逻辑）】
    判定逻辑只有一条：查 `_RULES[(operation, actor_type)]`。新增操作/执行体只需
    追加一行（内置行在 `_RULES`，扩展行经 `register_rule()` 注册），`decide()`
    与全部调用点**零改动**。`matrix_rows()` 把当前生效矩阵导出为可断言的数据，
    供文档生成与 §7.0 逐行回归。

【fail-closed】
    未知操作 / 未知 actor_type / 表缺行 → **拒绝**（不放行），并给出可读原因。
    这是治理面的安全默认：新操作忘记登记时是「发不出去」，不是「悄悄放行」。

【依赖纪律】
    本模块**零第三方依赖、零 agent 内部依赖**（纯表 + 纯函数），因此可被审批域、
    审计域、路由域同时导入而不构成循环依赖；与 events 的 actor 常量一致性由
    `tests/unit/test_security_actor_matrix.py` 断言守护（不靠 import 维系）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

# ════════════════════════════════════════════════════════════
#  执行体类型（与 agent.observability.events.ACTOR_* 同值）
# ════════════════════════════════════════════════════════════

#: 人类（登录用户 / 审批人）——矩阵中唯一拥有治理写权限的执行体
ACTOR_HUMAN = "human"
#: 自动化（技能 / 调度器 / 后台任务）
ACTOR_AUTO = "auto"
#: 子智能体（受委派的执行体）
ACTOR_SUB_AGENT = "sub_agent"
#: 服务账号（**第四类主体**，TASK-06 §3 第 1 步 / v1.4 §10.1）
#:
#: 【不易·为什么必须新建而不是复用 auto】`auto` 的语义是"云枢自己的技能/调度器"——
#: 它是**平台内部**的执行体，身份边界由平台自身界定。而 `service_account` 是
#: **外部**主体（cron / CI / Webhook / 别的系统）持有的长期凭据：它有独立的
#: `jti`（可秒级吊销）、独立的 `scope`（可声明能力集合与最高 confirm_level），
#: 且**不继承创建者权限**（v1.4 §10.1 铁律）。
#: 把这两件事压进一个 `auto` 会让"谁在调"在审计里彻底不可分——
#: 那正是 TASK-06 §2.1 记录的第一个身份缺陷。
#:
#: 【为什么放在 `actor_matrix.py` 而不是新建模块】本模块是全仓 actor 类型的**唯一收口**
#: （`:74` 前缀约定、`:447` 规范化/推断、`_RULES` 判定表）；新建平行模块会造成
#: 第二套身份真相源（D1）。SA 的**凭据/scope 机制**另在
#: `agent/security/service_account.py`，本模块只管"这类主体的权限边界"。
ACTOR_SERVICE_ACCOUNT = "service_account"

#: 合法执行体类型（顺序即文档顺序）
ACTOR_TYPES: Tuple[str, ...] = (ACTOR_HUMAN, ACTOR_AUTO, ACTOR_SUB_AGENT,
                               ACTOR_SERVICE_ACCOUNT)

#: 同义写法 → 规范值（配置表/调用方可用 `skill` / `subagent` 等写法）
ACTOR_TYPE_ALIASES: Dict[str, str] = {
    "human": ACTOR_HUMAN,
    "user": ACTOR_HUMAN,
    "operator": ACTOR_HUMAN,
    "reviewer": ACTOR_HUMAN,
    "ui": ACTOR_HUMAN,
    "auto": ACTOR_AUTO,
    "skill": ACTOR_AUTO,
    "auto(skill)": ACTOR_AUTO,
    "auto_skill": ACTOR_AUTO,
    "autoskill": ACTOR_AUTO,
    "scheduler": ACTOR_AUTO,
    "sub_agent": ACTOR_SUB_AGENT,
    "subagent": ACTOR_SUB_AGENT,
    "sub-agent": ACTOR_SUB_AGENT,
    "sub agent": ACTOR_SUB_AGENT,
    # ── 第四类主体（TASK-06）：服务账号 ──
    # 【不易·`ci` / `cron` / `webhook` 也映射到 SA】它们是**场景名**，而 SA 正是
    #   "cron / CI / Webhook"这三个场景的载体（TASK-06 §3 第 1 步的表）。若把它们
    #   留在别名表外，`normalize_actor_type("ci")` 会抛 ValueError（fail-closed 拒绝）
    #   ⇒ 既有脚本用 `actor_type="ci"` 的写法会当场失败。映射进来才算"接住"。
    "service_account": ACTOR_SERVICE_ACCOUNT,
    "service-account": ACTOR_SERVICE_ACCOUNT,
    "serviceaccount": ACTOR_SERVICE_ACCOUNT,
    "sa": ACTOR_SERVICE_ACCOUNT,
    "ci": ACTOR_SERVICE_ACCOUNT,
    "cron": ACTOR_SERVICE_ACCOUNT,
    "webhook": ACTOR_SERVICE_ACCOUNT,
}

#: 非人类执行体的 **actor 名前缀约定**（用于从既有调用方的 actor 名推断类型）
#: 命中即按对应类型判定；未命中按 human（向后兼容既有调用方，见 `infer_actor_type`）
_AUTO_NAME_PREFIXES: Tuple[str, ...] = ("auto:", "auto/", "auto-", "skill:", "skill/")
_SUB_AGENT_NAME_PREFIXES: Tuple[str, ...] = (
    "sub_agent:", "sub_agent/", "subagent:", "subagent/", "sub-agent:")
#: 【TASK-06 新增】服务账号的 **actor 名前缀约定**（v1.4 §10.1 要求 `sub` 带前缀，
#: 用以区分 `sa:` / `user:` / `system:` / `llm:`，避免与用户 id 混淆）。
#: 【顺序敏感】必须在 `_AUTO_NAME_PREFIXES` **之前**判：`sa:` 不以 `auto:`/`skill:`
#: 开头，两者不冲突，但把 SA 放前面能让"将来 SA 前缀扩展成 `auto-sa:`"时仍优先命中。
_SERVICE_ACCOUNT_NAME_PREFIXES: Tuple[str, ...] = (
    "sa:", "sa/", "service_account:", "service_account/", "service-account:",
    "ci:", "cron:", "webhook:")


# ════════════════════════════════════════════════════════════
#  操作（§7.0 矩阵行）
# ════════════════════════════════════════════════════════════

OP_VIEW_TRACE = "view.trace"                       # 查看轨迹
OP_VIEW_MEMORY = "view.memory"                     # 查看记忆
OP_VIEW_PANEL = "view.panel"                       # 查看面板
OP_APPROVE = "approval.approve"                    # 审批 Approve
OP_DENY = "approval.deny"                          # 审批 Deny
OP_SWITCH_FORGE = "governance.switch_forge"        # 切换熔炉
OP_MODIFY_POLICY = "governance.modify_policy"      # 修改策略
OP_FORCE_STAGE = "governance.force_stage"          # 强制推进 stage
OP_REMOVE_SOURCE = "governance.remove_source"      # 摘除来源
OP_EXECUTE_CAPABILITY = "capability.execute"       # 执行 capability
OP_WRITE_MEMORY = "memory.write"                   # 写入记忆
#: 【§7.0 外的扩展行】提交审批提案（不等于审批本身）
#: §7.0 只规定「审批 Approve/Deny 仅 human」，而**提交**提案是进化机制的常态
#: （「自动只产出建议」）：auto 提交提案必须放行，否则技能进化链路整条断掉。
#: 故单列一行：human ✅ / auto ✅（提案不生效）/ sub_agent ❌（不进审批面）。
OP_SUBMIT_APPROVAL = "approval.submit"
#: 【§7.0 外的扩展行】切换开关（TASK-S7-01 开关中心）
#: 与 §7.0「切换熔炉/修改策略」同性质（治理写操作），但**风险分级由开关注册表
#: 决定**：A 级可直接切（不需要二次认证），B 级由服务层强制二次认证 + 双人确认。
#: 故矩阵行只表达「human 专属」这一件事，二次认证的真值由
#: `agent/settings/service.py` 按 `SettingSpec.risk` 逐条裁决——矩阵与开关表
#: 各管一段，不重复表达同一事实（避免两处口径漂移）。
OP_SETTINGS_CHANGE = "settings.change"

#: 全部操作（文档顺序）
OPERATIONS: Tuple[str, ...] = (
    OP_VIEW_TRACE, OP_VIEW_MEMORY, OP_VIEW_PANEL,
    OP_APPROVE, OP_DENY,
    OP_SWITCH_FORGE, OP_MODIFY_POLICY,
    OP_FORCE_STAGE, OP_REMOVE_SOURCE,
    OP_EXECUTE_CAPABILITY, OP_WRITE_MEMORY, OP_SUBMIT_APPROVAL,
    OP_SETTINGS_CHANGE,
)

#: §7.0 之外的**扩展行**（本表新增、但不属于 §7.0 原表的操作）
EXTENSION_OPERATIONS: Tuple[str, ...] = (OP_SUBMIT_APPROVAL, OP_SETTINGS_CHANGE)

#: §7.0 **核心行**对应的操作（验收清单「§7.0 矩阵核心行全部可判定」的判定集）
#: 说明：历史上写作 `OPERATIONS[:-1]`；S7-01 追加 `settings.change` 扩展行后改为
#: **按扩展行集合显式排除**，取值与改造前完全一致（防扩展行悄悄混进核心行）。
CORE_MATRIX_OPERATIONS: Tuple[str, ...] = tuple(
    op for op in OPERATIONS if op not in EXTENSION_OPERATIONS)

#: 操作别名 → 规范值（审批域既有动词 `approve` / `reject` / `deny`；MCP 风格的
#: `approval.*` 写法亦接受）
OPERATION_ALIASES: Dict[str, str] = {
    "approve": OP_APPROVE,
    "approved": OP_APPROVE,
    "approval.approved": OP_APPROVE,
    "deny": OP_DENY,
    "reject": OP_DENY,
    "rejected": OP_DENY,
    "approval.reject": OP_DENY,
    "approval.rejected": OP_DENY,
    "view": OP_VIEW_PANEL,
    "read": OP_VIEW_PANEL,
    "execute": OP_EXECUTE_CAPABILITY,
    "capability.exec": OP_EXECUTE_CAPABILITY,
    "memory.write_working": OP_WRITE_MEMORY,
    "governance.force_promote": OP_FORCE_STAGE,
    "stage.promote": OP_FORCE_STAGE,
    "governance.switch_furnace": OP_SWITCH_FORGE,
    "forge.switch": OP_SWITCH_FORGE,
}

#: 治理类操作（human 专属 + 审计强留痕）——越权尝试必须告警
GOVERNANCE_OPERATIONS: FrozenSet[str] = frozenset({
    OP_APPROVE, OP_DENY, OP_SWITCH_FORGE, OP_MODIFY_POLICY,
    OP_FORCE_STAGE, OP_REMOVE_SOURCE, OP_SETTINGS_CHANGE,
})

#: 审批类操作（Approve/Deny 之外还含审批入口的提交/生效）
APPROVAL_OPERATIONS: FrozenSet[str] = frozenset({OP_APPROVE, OP_DENY})


# ════════════════════════════════════════════════════════════
#  授权范围口径
# ════════════════════════════════════════════════════════════

SCOPE_ALL = "all"                       # 无范围限制（human ✅）
SCOPE_OWN = "own_scope"                 # 仅自身 scope（auto 查看）
SCOPE_IN_SCOPE = "scope"                # scope 内（auto 执行 capability）
SCOPE_AUTHORIZED_SUBSET = "authorized_subset"   # 授权子集（sub_agent 执行 capability）
SCOPE_WORKING_MEMORY = "working_memory"         # 仅工作记忆（auto 写入记忆）
SCOPE_NONE = "none"                     # 无权限

#: 工作记忆层标识（§7.0「仅工作记忆」；兼容字段/中文写法）
MEMORY_LAYER_WORKING = "working"
WORKING_MEMORY_LAYERS: FrozenSet[str] = frozenset({
    "working", "working_memory", "work", "工作记忆", "工作",
})

#: 风险等级序（§3.2 / `agent/descriptors/models.py::RiskLevel` 同序）
#: 与 descriptors 的一致性由单测断言守护（避免重复常量漂移）
RISK_ORDER: Tuple[str, ...] = ("low", "medium", "high", "destructive")
RISK_DESTRUCTIVE = "destructive"


def risk_rank(risk: str) -> int:
    """风险等级 → 序号（未知等级 → -1，调用方据此从严处置）"""
    try:
        return RISK_ORDER.index(str(risk or "").strip().lower())
    except ValueError:
        return -1


def is_destructive(risk: str) -> bool:
    """是否 destructive 级（§5.7⑦：destructive 审批强制二次认证）"""
    return str(risk or "").strip().lower() == RISK_DESTRUCTIVE


# ════════════════════════════════════════════════════════════
#  规则与判定结果
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PermissionRule:
    """一条矩阵规则（§7.0 单元格）

    Attributes:
        allowed: 是否允许。
        scope: 允许时的范围口径（见 SCOPE_* 常量）；不允许时为 SCOPE_NONE。
        requires_second_factor: 是否需要二次认证（§5.7⑦）。
        requires_reason: 是否必须提供 reason（强制推进/摘除来源）。
        alert_on_deny: 越权尝试是否必须告警 + 审计。
        desc: 人读说明（文档与审计载荷引用）。
    """
    allowed: bool
    scope: str = SCOPE_ALL
    requires_second_factor: bool = False
    requires_reason: bool = False
    alert_on_deny: bool = True
    desc: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "scope": self.scope,
            "requires_second_factor": bool(self.requires_second_factor),
            "requires_reason": bool(self.requires_reason),
            "alert_on_deny": bool(self.alert_on_deny),
            "desc": self.desc,
        }


@dataclass(frozen=True)
class PermissionContext:
    """判定上下文（执行体一侧的事实）

    Attributes:
        actor: 执行体标识（登录用户名 / 技能 id / 子智能体 id）。
        actor_type: human / auto / sub_agent（已规范化）。
        scope: 执行体自身 scope 标识（auto 为技能/任务 scope；human 可留空）。
        allowed_scopes: 显式允许访问的 scope 集合（auto 用；缺省 = {scope}）。
        authorized_capabilities: **授权子集**（sub_agent 执行 capability 的白名单；
            由 S4-04「subagent 真实现工具裁剪」的授权清单提供——本任务只定义
            接口形状与判定语义，不实现裁剪本身）。
        session_id: 会话标识（审批面安全用；human 会话绑定）。
        identity_source: 身份来源口径（见 `agent/security/identity.py`）。
        extra: 追加叶子字段（审计载荷透传；调用方守「只放标识不放原文」）。
    """
    actor: str = ""
    actor_type: str = ACTOR_HUMAN
    scope: str = ""
    allowed_scopes: FrozenSet[str] = frozenset()
    authorized_capabilities: FrozenSet[str] = frozenset()
    session_id: str = ""
    identity_source: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionDecision:
    """一次判定的结果（可 JSON 序列化，直接进审计/事件载荷）"""
    allowed: bool
    operation: str
    actor: str
    actor_type: str
    scope: str = SCOPE_NONE
    reason: str = ""
    requires_second_factor: bool = False
    requires_reason: bool = False
    second_factor_ok: bool = False
    alert_on_deny: bool = True
    object_type: str = ""
    object_id: str = ""
    identity_source: str = ""
    matrix_hit: bool = False

    @property
    def denied_by_matrix(self) -> bool:
        """是否被矩阵本身拒绝（而非缺少 reason / 二次认证等前置条件）"""
        return (not self.allowed) and self.matrix_hit

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "operation": self.operation,
            "actor": self.actor,
            "actor_type": self.actor_type,
            "scope": self.scope,
            "reason": self.reason,
            "requires_second_factor": bool(self.requires_second_factor),
            "requires_reason": bool(self.requires_reason),
            "second_factor_ok": bool(self.second_factor_ok),
            "alert_on_deny": bool(self.alert_on_deny),
            "object_type": self.object_type,
            "object_id": self.object_id,
            "identity_source": self.identity_source,
            "matrix_hit": bool(self.matrix_hit),
            "denied_by_matrix": bool(self.denied_by_matrix),
        }


# ════════════════════════════════════════════════════════════
#  内置判定表（§7.0 逐格落地；加行不改逻辑）
# ════════════════════════════════════════════════════════════

def _allow(scope: str = SCOPE_ALL, *, sf: bool = False, reason: bool = False,
           desc: str = "") -> PermissionRule:
    return PermissionRule(allowed=True, scope=scope, requires_second_factor=sf,
                          requires_reason=reason, alert_on_deny=True, desc=desc)


def _deny(desc: str, *, alert: bool = True) -> PermissionRule:
    return PermissionRule(allowed=False, scope=SCOPE_NONE, alert_on_deny=alert,
                          desc=desc)


_VIEW_DESC = "§7.0 查看轨迹/记忆/面板"
_APPROVAL_DESC = "§7.0 审批 Approve/Deny（human 专属）"
_POLICY_DESC = "§7.0 切换熔炉/修改策略（二次认证/RFC）"
_STAGE_DESC = "§7.0 强制推进 stage / 摘除来源（reason 必填 + 审计）"
_EXEC_DESC = "§7.0 执行 capability"
_MEMORY_DESC = "§7.0 写入记忆"

#: 内置矩阵（(operation, actor_type) → 规则）
_RULES: Dict[Tuple[str, str], PermissionRule] = {
    # ── 查看轨迹 / 记忆 / 面板：auto 仅自身 scope，sub_agent ❌ ──
    (OP_VIEW_TRACE, ACTOR_HUMAN): _allow(desc=f"{_VIEW_DESC} / human"),
    (OP_VIEW_TRACE, ACTOR_AUTO): _allow(SCOPE_OWN, desc=f"{_VIEW_DESC} / auto 仅自身 scope"),
    (OP_VIEW_TRACE, ACTOR_SUB_AGENT): _deny(f"{_VIEW_DESC} / sub_agent ❌"),
    (OP_VIEW_MEMORY, ACTOR_HUMAN): _allow(desc=f"{_VIEW_DESC} / human"),
    (OP_VIEW_MEMORY, ACTOR_AUTO): _allow(SCOPE_OWN, desc=f"{_VIEW_DESC} / auto 仅自身 scope"),
    (OP_VIEW_MEMORY, ACTOR_SUB_AGENT): _deny(f"{_VIEW_DESC} / sub_agent ❌"),
    (OP_VIEW_PANEL, ACTOR_HUMAN): _allow(desc=f"{_VIEW_DESC} / human"),
    (OP_VIEW_PANEL, ACTOR_AUTO): _allow(SCOPE_OWN, desc=f"{_VIEW_DESC} / auto 仅自身 scope"),
    (OP_VIEW_PANEL, ACTOR_SUB_AGENT): _deny(f"{_VIEW_DESC} / sub_agent ❌"),

    # ── 审批 Approve/Deny：仅 human（越权必须被拒 + 审计 + 告警） ──
    (OP_APPROVE, ACTOR_HUMAN): _allow(desc=f"{_APPROVAL_DESC} / human ✅"),
    (OP_APPROVE, ACTOR_AUTO): _deny(f"{_APPROVAL_DESC} / auto(skill) ❌"),
    (OP_APPROVE, ACTOR_SUB_AGENT): _deny(f"{_APPROVAL_DESC} / sub_agent ❌"),
    (OP_DENY, ACTOR_HUMAN): _allow(desc=f"{_APPROVAL_DESC} / human ✅"),
    (OP_DENY, ACTOR_AUTO): _deny(f"{_APPROVAL_DESC} / auto(skill) ❌"),
    (OP_DENY, ACTOR_SUB_AGENT): _deny(f"{_APPROVAL_DESC} / sub_agent ❌"),

    # ── 切换熔炉 / 修改策略：仅 human，且需二次认证 ──
    (OP_SWITCH_FORGE, ACTOR_HUMAN): _allow(sf=True, desc=f"{_POLICY_DESC} / human ✅"),
    (OP_SWITCH_FORGE, ACTOR_AUTO): _deny(f"{_POLICY_DESC} / auto ❌"),
    (OP_SWITCH_FORGE, ACTOR_SUB_AGENT): _deny(f"{_POLICY_DESC} / sub_agent ❌"),
    (OP_MODIFY_POLICY, ACTOR_HUMAN): _allow(sf=True, desc=f"{_POLICY_DESC} / human ✅"),
    (OP_MODIFY_POLICY, ACTOR_AUTO): _deny(f"{_POLICY_DESC} / auto ❌"),
    (OP_MODIFY_POLICY, ACTOR_SUB_AGENT): _deny(f"{_POLICY_DESC} / sub_agent ❌"),

    # ── 强制推进 stage / 摘除来源：仅 human，且 reason 必填 ──
    (OP_FORCE_STAGE, ACTOR_HUMAN): _allow(reason=True, desc=f"{_STAGE_DESC} / human ✅"),
    (OP_FORCE_STAGE, ACTOR_AUTO): _deny(f"{_STAGE_DESC} / auto ❌"),
    (OP_FORCE_STAGE, ACTOR_SUB_AGENT): _deny(f"{_STAGE_DESC} / sub_agent ❌"),
    (OP_REMOVE_SOURCE, ACTOR_HUMAN): _allow(reason=True, desc=f"{_STAGE_DESC} / human ✅"),
    (OP_REMOVE_SOURCE, ACTOR_AUTO): _deny(f"{_STAGE_DESC} / auto ❌"),
    (OP_REMOVE_SOURCE, ACTOR_SUB_AGENT): _deny(f"{_STAGE_DESC} / sub_agent ❌"),

    # ── 执行 capability：human ✅ / auto scope 内 / sub_agent 授权子集 ──
    (OP_EXECUTE_CAPABILITY, ACTOR_HUMAN): _allow(desc=f"{_EXEC_DESC} / human ✅"),
    (OP_EXECUTE_CAPABILITY, ACTOR_AUTO): _allow(SCOPE_IN_SCOPE,
                                                desc=f"{_EXEC_DESC} / auto ✅ scope 内"),
    (OP_EXECUTE_CAPABILITY, ACTOR_SUB_AGENT): _allow(SCOPE_AUTHORIZED_SUBSET,
                                                     desc=f"{_EXEC_DESC} / sub_agent ✅ 授权子集"),

    # ── 写入记忆：auto 仅工作记忆，sub_agent ❌ ──
    (OP_WRITE_MEMORY, ACTOR_HUMAN): _allow(desc=f"{_MEMORY_DESC} / human ✅"),
    (OP_WRITE_MEMORY, ACTOR_AUTO): _allow(SCOPE_WORKING_MEMORY,
                                          desc=f"{_MEMORY_DESC} / auto ✅ 仅工作记忆"),
    (OP_WRITE_MEMORY, ACTOR_SUB_AGENT): _deny(f"{_MEMORY_DESC} / sub_agent ❌"),

    # ── 【§7.0 外扩展】提交审批提案：auto 可提交（不生效），sub_agent 不进审批面 ──
    (OP_SUBMIT_APPROVAL, ACTOR_HUMAN): _allow(
        desc="§7.0 外扩展：human 提交审批提案"),
    (OP_SUBMIT_APPROVAL, ACTOR_AUTO): _allow(
        desc="§7.0 外扩展：auto 可提交提案（自动只产出建议，不生效）"),
    (OP_SUBMIT_APPROVAL, ACTOR_SUB_AGENT): _deny(
        "§7.0 外扩展：sub_agent 不进审批面（不可查看/提交审批）"),

    # ── 【§7.0 外扩展】切换开关：human 专属；auto / sub_agent 一律拒 ──
    # 二次认证由 `agent/settings/service.py` 按风险级（A 免 / B 强制）裁量，
    # 故矩阵行不加 sf=True（否则 A 级也会被矩阵拦下，与「A 可直接切」冲突）。
    (OP_SETTINGS_CHANGE, ACTOR_HUMAN): _allow(
        desc="§7.0 外扩展：human 切换开关（风险分级与二次认证见开关注册表）"),
    (OP_SETTINGS_CHANGE, ACTOR_AUTO): _deny(
        "§7.0 外扩展：改开关是治理动作，auto 一律拒绝"),
    (OP_SETTINGS_CHANGE, ACTOR_SUB_AGENT): _deny(
        "§7.0 外扩展：改开关是治理动作，sub_agent 一律拒绝"),

    # ── 【TASK-06 第四类主体】`service_account`（cron / CI / Webhook / 外部系统）──
    #
    # 【为什么必须补满这 13 行（这是一个真实缺陷，不是补文档）】
    #   TASK-06 把 `service_account` 加进 `ACTOR_TYPES` 之后，**本表没有它的任何一行**
    #   ⇒ `rule_for(任意操作, "service_account")` 返回 `None` ⇒
    #   `tests/unit/test_security_actor_matrix.py::TestVersionAlignment::
    #   test_every_matrix_cell_registered` 立刻变红（39 行 vs 13×4=52）。
    #   该不变量（每个 操作 × 主体 都要有一格）存在的理由正是本情形：
    #   **一个类型出现在值域里、却没有权限边界** —— 那等于"边界未定义"，
    #   而"未定义"在实现里会退化成"看调用点怎么写"（最坏的一种不确定性）。
    #
    # 【判定口径：SA 是**外部**主体，权限面必须比 auto 更窄】
    #   与 `auto` 的区别：`auto` 是**平台内部**的执行体（云枢自己的技能/调度器），
    #   `service_account` 是**外部系统**持有的长期凭据（有 jti、可秒级吊销、
    #   有 scope、**不继承创建者权限**，v1.4 §10.1）。
    #   三条硬纪律：
    #     ① **不得拥有审批权**（`approval.approve` / `approval.deny` 一律拒）——
    #        SA 若能批准审批单，就能给自己发授权，那与"预授权是闸门内的一条判定"
    #        完全相反（TASK-06 §5 列为"不通过"）。
    #     ② **不得有治理写权**（熔炉/策略/stage/来源/开关一律拒）——
    #        否则 SA 能修改"它自己被允许做什么"，权限面自举。
    #     ③ **不得读人的资产**（轨迹/记忆/面板一律拒）——SA 的存在意义是执行被授权
    #        的能力，不是浏览会话内容。
    #   唯一的放行是 `capability.execute`（`SCOPE_IN_SCOPE`）：这正是 SA 的用途，
    #   且范围限定在**它自己的 scope 内**（与 auto 同口径，比 human 的是 `SCOPE_ALL` 窄）。
    #   `approval.submit` 也放行（与 auto 一致）：SA 遇到需要人裁决的动作时
    #   **只能提交提案**，不能自己裁决 —— 那是"非交互场景的正确出路"（不是挂空单）。
    (OP_VIEW_TRACE, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：SA 是外部主体，不读会话轨迹（可能含隐私内容）"),
    (OP_VIEW_MEMORY, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：SA 不读记忆（主人格资产，与它的执行职责无关）"),
    (OP_VIEW_PANEL, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：SA 不使用人的界面（面板是交互面的东西，SA 无人在场）"),
    (OP_APPROVE, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：SA **不得审批**（否则可给自己发授权；预授权必须走 scope 而非审批）"),
    (OP_DENY, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：SA **不得驳回**（裁决权 human 专属，与 approve 同一理由）"),
    (OP_SWITCH_FORGE, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：切换熔炉是治理动作，SA 一律拒绝"),
    (OP_MODIFY_POLICY, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：SA 不得改策略（否则能修改「自己被允许做什么」，权限面自举）"),
    (OP_FORCE_STAGE, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：强制推进 stage 是治理动作，SA 一律拒绝"),
    (OP_REMOVE_SOURCE, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：摘除来源是治理动作，SA 一律拒绝"),
    (OP_EXECUTE_CAPABILITY, ACTOR_SERVICE_ACCOUNT): _allow(
        SCOPE_IN_SCOPE,
        desc="TASK-06：SA **唯一放行**的操作 —— 执行其 scope 内被授权的能力"),
    (OP_WRITE_MEMORY, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：SA 不写记忆（认知资产不由外部主体写入）"),
    (OP_SUBMIT_APPROVAL, ACTOR_SERVICE_ACCOUNT): _allow(
        desc="TASK-06：SA 可**提交**审批提案（与 auto 同口径：提案不生效，"
             "裁决权仍在 human —— 这是非交互场景拿到人裁决的正路，不是挂空单）"),
    (OP_SETTINGS_CHANGE, ACTOR_SERVICE_ACCOUNT): _deny(
        "TASK-06：改开关是治理动作，SA 一律拒绝"),
}

# ════════════════════════════════════════════════════════════
#  §7.0 文档矩阵（逐格期望值；单测据以逐行核验，防实现与文档漂移）
# ════════════════════════════════════════════════════════════

#: §7.0 原始矩阵（人读 + 断言用）：{操作组: {执行体: "✅" / "❌" / 说明}}
#:
#: 【TASK-06 新增 `service_account` 列】§7.0 原文只有三类执行体；第四类是本任务按
#: v1.4 §10.1 补的。**文档矩阵必须同步补齐**，否则
#: `tests/unit/test_security_actor_matrix.py::TestVersionAlignment::
#: test_every_matrix_cell_registered` 会在"文档说没有这一列、实现对它有判定"时变红 ——
#: 那条不变量正是用来防"实现与文档各说一套"的，不该为了让测试变绿去放宽它。
#: 取值口径见 `_RULES` 里 TASK-06 段落的三条硬纪律（不审批 / 不治理 / 不读人资产）。
MATRIX_DOC: Dict[str, Dict[str, str]] = {
    "查看轨迹/记忆/面板": {
        ACTOR_HUMAN: "allow",
        ACTOR_AUTO: "own_scope",
        ACTOR_SUB_AGENT: "deny",
        ACTOR_SERVICE_ACCOUNT: "deny",
    },
    "审批 Approve/Deny": {
        ACTOR_HUMAN: "allow",
        ACTOR_AUTO: "deny",
        ACTOR_SUB_AGENT: "deny",
        ACTOR_SERVICE_ACCOUNT: "deny",
    },
    "切换熔炉/修改策略": {
        ACTOR_HUMAN: "allow_second_factor",
        ACTOR_AUTO: "deny",
        ACTOR_SUB_AGENT: "deny",
        ACTOR_SERVICE_ACCOUNT: "deny",
    },
    "强制推进 stage/摘除来源": {
        ACTOR_HUMAN: "allow_reason",
        ACTOR_AUTO: "deny",
        ACTOR_SUB_AGENT: "deny",
        ACTOR_SERVICE_ACCOUNT: "deny",
    },
    "执行 capability": {
        ACTOR_HUMAN: "allow",
        ACTOR_AUTO: "in_scope",
        ACTOR_SUB_AGENT: "authorized_subset",
        ACTOR_SERVICE_ACCOUNT: "in_scope",
    },
    "写入记忆": {
        ACTOR_HUMAN: "allow",
        ACTOR_AUTO: "working_memory",
        ACTOR_SUB_AGENT: "deny",
        ACTOR_SERVICE_ACCOUNT: "deny",
    },
}

#: §7.0 操作组 → 具体操作（供文档矩阵与实现表互相校验）
MATRIX_DOC_ROWS: Dict[str, Tuple[str, ...]] = {
    "查看轨迹/记忆/面板": (OP_VIEW_TRACE, OP_VIEW_MEMORY, OP_VIEW_PANEL),
    "审批 Approve/Deny": (OP_APPROVE, OP_DENY),
    "切换熔炉/修改策略": (OP_SWITCH_FORGE, OP_MODIFY_POLICY),
    "强制推进 stage/摘除来源": (OP_FORCE_STAGE, OP_REMOVE_SOURCE),
    "执行 capability": (OP_EXECUTE_CAPABILITY,),
    "写入记忆": (OP_WRITE_MEMORY,),
}


# ════════════════════════════════════════════════════════════
#  表访问 / 扩展
# ════════════════════════════════════════════════════════════

#: 内置表快照（`reset_rules()` 据此还原被 override 的行）
_BUILTIN_RULES: Dict[Tuple[str, str], PermissionRule] = dict(_RULES)
_RULES_EXTENDED: Dict[Tuple[str, str], PermissionRule] = {}


def normalize_actor_type(value: str, *, default: Optional[str] = None) -> str:
    """执行体类型规范化（别名/大小写无关）

    Args:
        value: 原始写法（`skill` / `auto(skill)` / `subagent` / …）。
        default: 无法识别时的返回值；None → 抛 ValueError（fail-closed）。

    Raises:
        ValueError: 无法识别且未提供 default。
    """
    key = str(value or "").strip().lower()
    if key in ACTOR_TYPE_ALIASES:
        return ACTOR_TYPE_ALIASES[key]
    if default is not None:
        return default
    raise ValueError(f"未知执行体类型: {value!r}（允许: {ACTOR_TYPES}）")


def infer_actor_type(actor: str, *, default: str = ACTOR_HUMAN) -> str:
    """由 actor 名推断执行体类型（**向后兼容**既有调用方）

    约定（保守）：actor 名带 `auto:` / `skill:` / `sub_agent:` 一类前缀时判为
    对应非人类执行体；其余（`reviewer` / 用户名 / `system` / …）判为 human，
    约定（保守，三级）：
        1. actor 名本身是类型别名（`auto` / `skill` / `subagent` / `sub_agent`）→ 对应类型；
        2. actor 名带 `auto:` / `skill:` / `sub_agent:` 一类前缀 → 对应类型；
        3. 其余（`reviewer` / 用户名 / `system` / …）→ human，
    从而**不改既有调用方行为**（既有审批调用点全部以人类身份提交/审批）。
    显式 `actor_type=` 参数优先于本推断。
    """
    name = str(actor or "").strip().lower()
    if not name:
        return default
    if name in ACTOR_TYPE_ALIASES:
        return ACTOR_TYPE_ALIASES[name]
    # 【顺序】SA 前缀先判：`sa:` / `ci:` 等不与 auto/skill 前缀重叠，但显式优先
    # 可保证将来前缀扩展时不出现"被 auto 抢走"的静默误判。
    if name.startswith(_SERVICE_ACCOUNT_NAME_PREFIXES):
        return ACTOR_SERVICE_ACCOUNT
    if name.startswith(_SUB_AGENT_NAME_PREFIXES):
        return ACTOR_SUB_AGENT
    if name.startswith(_AUTO_NAME_PREFIXES):
        return ACTOR_AUTO
    return default


def register_rule(operation: str, actor_type: str, rule: PermissionRule, *,
                  override: bool = False) -> None:
    """注册/覆盖一条矩阵规则（**表驱动的扩展点**：加行不改判定逻辑）

    Args:
        operation: 操作名（可用别名，自动规范化）。
        actor_type: 执行体类型（可用别名）。
        rule: 规则。
        override: 是否允许覆盖既有行（默认 False，重复登记抛 ValueError）。
    """
    op = normalize_operation(operation)
    at = normalize_actor_type(actor_type)
    if not override and (op, at) in _RULES:
        raise ValueError(f"规则已存在: {op}/{at}（如需覆盖请传 override=True）")
    _RULES[(op, at)] = rule
    _RULES_EXTENDED[(op, at)] = rule


def reset_rules() -> None:
    """清除扩展行并还原内置表（测试隔离用）"""
    _RULES.clear()
    _RULES.update(_BUILTIN_RULES)
    _RULES_EXTENDED.clear()


def rule_for(operation: str, actor_type: str) -> Optional[PermissionRule]:
    """查表（未登记 → None，调用方 fail-closed）"""
    try:
        op = normalize_operation(operation)
        at = normalize_actor_type(actor_type)
    except ValueError:
        return None
    return _RULES.get((op, at))


def normalize_operation(operation: str) -> str:
    """操作名规范化（别名 → 规范值；未知操作原样返回小写）"""
    key = str(operation or "").strip().lower()
    if key in OPERATION_ALIASES:
        return OPERATION_ALIASES[key]
    return key


def matrix_rows() -> List[Dict[str, Any]]:
    """导出当前生效矩阵（文档生成 / 逐行回归断言用）"""
    rows: List[Dict[str, Any]] = []
    for op in sorted(_RULES):
        rule = _RULES[op]
        rows.append({
            "operation": op[0], "actor_type": op[1],
            "allowed": rule.allowed, "scope": rule.scope,
            "requires_second_factor": rule.requires_second_factor,
            "requires_reason": rule.requires_reason,
            "alert_on_deny": rule.alert_on_deny, "desc": rule.desc,
        })
    return rows


# ════════════════════════════════════════════════════════════
#  判定
# ════════════════════════════════════════════════════════════

def _scope_allowed(ctx: PermissionContext, target_scope: str) -> bool:
    """auto 的「自身 scope」判定：目标 scope 必须等于自身 scope 或在其白名单内"""
    target = str(target_scope or "").strip()
    if not target:
        return False
    own = str(ctx.scope or "").strip()
    if own and target == own:
        return True
    return target in {str(s).strip() for s in ctx.allowed_scopes if str(s).strip()}


def _capability_authorized(ctx: PermissionContext, capability_id: str) -> bool:
    """sub_agent 的「授权子集」判定：capability 必须在授权清单内"""
    cid = str(capability_id or "").strip()
    if not cid:
        return False
    return cid in {str(c).strip() for c in ctx.authorized_capabilities if str(c).strip()}


def decide(operation: str, ctx: PermissionContext, *,
           object_type: str = "", object_id: str = "",
           target_scope: str = "", memory_layer: str = "",
           reason: str = "", second_factor_ok: bool = False,
           risk: str = "", enforce_preconditions: bool = True) -> PermissionDecision:
    """矩阵判定唯一入口（fail-closed）

    判定顺序（**先矩阵、后前置条件**，两者的拒绝原因可区分）：
        1. 操作/执行体类型可识别且表中有行 → 否则拒绝（matrix_hit=False）；
        2. 矩阵该格 allowed → 否则拒绝（matrix_hit=True，越权）；
        3. requires_reason 且未提供 reason → 拒绝；
        4. requires_second_factor（或 risk=destructive）且未完成二次认证 → 拒绝；
        5. 范围口径（SCOPE_OWN / SCOPE_IN_SCOPE / SCOPE_AUTHORIZED_SUBSET /
           SCOPE_WORKING_MEMORY）不满足 → 拒绝。

    Args:
        operation: 操作名（别名自动规范化）。
        ctx: 执行体上下文。
        object_type / object_id: 受影响对象（object_id 兼作 capability_id）。
        target_scope: 目标资源 scope（auto 查看/执行时必填）。
        memory_layer: 记忆层（`working` 以外一律拒绝 auto）。
        reason: 理由（强制推进/摘除来源必填）。
        second_factor_ok: 是否已通过二次认证（由 `approval_session` 出具）。
        risk: 风险等级（§3.2）；destructive 时**一律**要求二次认证。
        enforce_preconditions: False 时只判「矩阵格 + 范围口径」，
            **跳过** requires_reason / 二次认证两项前置条件。用于「提交提案」
            场景——§7.0 的 reason/二次认证约束作用于**生效动作**（approve /
            apply），不作用于把提案送进审批队列（详见 `approval_guard` 说明）。

    Returns:
        PermissionDecision（`allowed=False` 时 `reason` 可读且可直接入审计）。
    """
    op = normalize_operation(operation)
    actor = str(ctx.actor or "")
    try:
        actor_type = normalize_actor_type(ctx.actor_type)
    except ValueError:
        return PermissionDecision(
            allowed=False, operation=op, actor=actor,
            actor_type=str(ctx.actor_type or ""), reason="未知执行体类型（fail-closed）",
            object_type=object_type, object_id=object_id,
            identity_source=ctx.identity_source, matrix_hit=False)

    rule = _RULES.get((op, actor_type))
    if rule is None:
        return PermissionDecision(
            allowed=False, operation=op, actor=actor, actor_type=actor_type,
            reason=f"未登记操作或矩阵缺行：{op}/{actor_type}（fail-closed）",
            object_type=object_type, object_id=object_id,
            identity_source=ctx.identity_source, matrix_hit=False)

    destructive = is_destructive(risk)
    base: Dict[str, Any] = dict(
        operation=op, actor=actor, actor_type=actor_type, scope=rule.scope,
        requires_second_factor=bool(rule.requires_second_factor or destructive),
        requires_reason=bool(rule.requires_reason),
        second_factor_ok=bool(second_factor_ok),
        alert_on_deny=bool(rule.alert_on_deny), object_type=object_type,
        object_id=object_id, identity_source=ctx.identity_source,
        matrix_hit=True)

    if not rule.allowed:
        return PermissionDecision(
            allowed=False, reason=f"§7.0 矩阵拒绝：{rule.desc or op}", **base)

    if rule.scope == SCOPE_OWN and not _scope_allowed(ctx, target_scope):
        return PermissionDecision(
            allowed=False,
            reason=(f"auto 仅可访问自身 scope（自身={ctx.scope or '未声明'}，"
                    f"目标={target_scope or '未声明'}）"), **base)

    if rule.scope == SCOPE_IN_SCOPE and not _scope_allowed(ctx, target_scope):
        return PermissionDecision(
            allowed=False,
            reason=(f"capability 超出 auto 自身 scope（自身={ctx.scope or '未声明'}，"
                    f"目标={target_scope or '未声明'}）"), **base)

    if rule.scope == SCOPE_AUTHORIZED_SUBSET and not _capability_authorized(ctx, object_id):
        return PermissionDecision(
            allowed=False,
            reason=(f"sub_agent 执行 capability 仅限授权子集："
                    f"{object_id or '未声明 capability'} 不在授权清单"), **base)

    if rule.scope == SCOPE_WORKING_MEMORY:
        layer = str(memory_layer or "").strip().lower()
        if layer not in WORKING_MEMORY_LAYERS:
            return PermissionDecision(
                allowed=False,
                reason=(f"auto 仅可写入工作记忆（目标层={layer or '未声明'}）"), **base)

    if not enforce_preconditions:
        # 提交阶段：只判「矩阵格 + 范围口径」，reason / 二次认证留给生效动作
        return PermissionDecision(allowed=True, reason="矩阵允许（提交阶段，前置条件后置）",
                                  **base)

    if rule.requires_reason and not str(reason or "").strip():
        return PermissionDecision(
            allowed=False, reason=f"{op} 必须提供 reason（审计要求）", **base)

    if (rule.requires_second_factor or destructive) and not second_factor_ok:
        why = "risk=destructive" if destructive else "§7.0 治理写操作"
        return PermissionDecision(
            allowed=False, reason=f"{why} 需二次认证后方可执行", **base)

    return PermissionDecision(allowed=True, reason="矩阵允许", **base)


__all__ = [
    # 执行体
    "ACTOR_HUMAN", "ACTOR_AUTO", "ACTOR_SUB_AGENT", "ACTOR_TYPES",
    "ACTOR_TYPE_ALIASES", "normalize_actor_type", "infer_actor_type",
    # 操作
    "OP_VIEW_TRACE", "OP_VIEW_MEMORY", "OP_VIEW_PANEL", "OP_APPROVE", "OP_DENY",
    "OP_SWITCH_FORGE", "OP_MODIFY_POLICY", "OP_FORCE_STAGE", "OP_REMOVE_SOURCE",
    "OP_EXECUTE_CAPABILITY", "OP_WRITE_MEMORY", "OP_SUBMIT_APPROVAL",
    "OP_SETTINGS_CHANGE",
    "OPERATIONS", "OPERATION_ALIASES", "normalize_operation",
    "GOVERNANCE_OPERATIONS", "APPROVAL_OPERATIONS", "CORE_MATRIX_OPERATIONS",
    "EXTENSION_OPERATIONS",
    # 范围
    "SCOPE_ALL", "SCOPE_OWN", "SCOPE_IN_SCOPE", "SCOPE_AUTHORIZED_SUBSET",
    "SCOPE_WORKING_MEMORY", "SCOPE_NONE", "MEMORY_LAYER_WORKING",
    "WORKING_MEMORY_LAYERS",
    # 风险
    "RISK_ORDER", "RISK_DESTRUCTIVE", "risk_rank", "is_destructive",
    # 规则与判定
    "PermissionRule", "PermissionContext", "PermissionDecision",
    "decide", "rule_for", "register_rule", "reset_rules", "matrix_rows",
    "MATRIX_DOC", "MATRIX_DOC_ROWS",
]
