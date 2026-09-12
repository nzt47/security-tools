"""能力最小暴露 —— 注入防御机制 3（TASK-S4-03 步骤 4 / v7.2 §5.7 + §7.0）

【机制原文（§5.7 表第 3 行）】
    3  能力最小暴露｜子智能体工具集为**裁剪子集**（不含记忆读写/核心改写/审批权）

【本任务与 S4-04 的分工（任务书明示"配合 S4-04"）】
    本模块出**清单与判定**（"哪些能力不得暴露给 sub_agent、裁剪的函数形状是什么"）；
    S4-04 把它接到"执行器工具注入层"（真正让裁剪集外的工具**不可用**）。
    本模块**不**去改 subagent 的执行器——那会与 S4-04 的落点重叠。

【三层裁剪（一张表说了算）】
    1. **绝对禁项**（`FORBIDDEN_CAPABILITY_CLASSES`）：记忆读写 / 核心改写 / 审批权
       —— §5.7 机制 3 逐字三类，**永不可**授予 sub_agent；
    2. **模式禁项**（`FORBIDDEN_PATTERNS`）：按能力 id / 工具名匹配的执行面收口
       （审批路由、熔炉切换、策略修改、核心自改写、记忆库直写…）；
    3. **授权子集**（`trim_toolset(authorized=...)`）：即便不在禁项里，sub_agent 也只能
       拿到**显式授权**的子集——默认**闭集**（未授权即不可用），与 §7.0 的
       `SCOPE_AUTHORIZED_SUBSET` 口径一致。

【与 S4-01 Actor 矩阵的关系（勿另建权限表）】
    `agent/security/actor_matrix.py` 是**唯一权威权限表**（S4-01 交付）。
    本模块**不复制**那张矩阵，只做两件事：
      a) 把它对 `sub_agent` 的结论**投影**成"工具集"形态（矩阵说"不能审批"，
         本模块说"不能看见审批工具"——最小暴露比"看见了但被拒"更强）；
      b) 提供 `enforce_scope_consistency()` 断言本模块的禁项与矩阵**不冲突**
         （若矩阵允许而本模块禁，以更严者为准并告警——安全侧从严是唯一正确的偏向）。

【不易】默认闭集（未授权即不可用）；禁项不可被 `authorized` 覆盖；
       `enforce_scope_consistency()` 把"比矩阵更严"变成显式事实而非隐性假设。
【变易】`FORBIDDEN_PATTERNS` / `DEFAULT_SUBAGENT_TOOLSET` 是数据。
【简易】纯标准库；无全局状态。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger("agent.guardrails.capability_exposure")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

#: sub_agent 的 actor 类型（与 `agent.security.actor_matrix.ACTOR_SUB_AGENT` 同值）
ACTOR_SUB_AGENT = "sub_agent"

#: §5.7 机制 3 逐字的绝对禁项三类
FORBIDDEN_CAPABILITY_CLASSES: Tuple[str, ...] = (
    "memory_read",      # 记忆读
    "memory_write",     # 记忆写
    "core_rewrite",     # 核心改写
    "approval",         # 审批权
)

#: 类别 → 中文名
CLASS_LABELS: Dict[str, str] = {
    "memory_read": "记忆读",
    "memory_write": "记忆写",
    "core_rewrite": "核心改写",
    "approval": "审批权",
}

#: 模式禁项（**数据**）：(类别, 正则)。匹配对象为能力 id / 工具名（小写）。
FORBIDDEN_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    # ── 审批权 ──
    ("approval", re.compile(r"(?:^|\.)(?:approval|approve|deny|review_gate|takeover)(?:\.|$)")),
    ("approval", re.compile(r"approval\.(?:submit|approve|deny|merge|archive)")),
    # ── 核心改写 / 熔炉 ──
    ("core_rewrite", re.compile(r"(?:^|\.)(?:forge|vault|core_rewrite|self_rewrite)(?:\.|$)")),
    ("core_rewrite", re.compile(r"governance\.(?:switch_forge|force_stage|remove_source|modify_policy)")),
    ("core_rewrite", re.compile(r"(?:^|\.)(?:meta_editor|edit_policy|offline_evolver)(?:\.|$)")),
    # ── 记忆读写 ──
    ("memory_write", re.compile(r"(?:^|\.)(?:memory|knowledge)\.(?:write|put|add|delete|forget|set)(?:\.|$)")),
    ("memory_read", re.compile(r"(?:^|\.)(?:memory|knowledge)\.(?:read|get|search|recall|query)(?:\.|$)")),
    ("memory_write", re.compile(r"(?:^|\.)layered_store(?:\.|$)")),
    ("memory_read", re.compile(r"(?:^|\.)memory_abstractor(?:\.|$)")),
)

#: sub_agent 的**默认可见集**（闭集：不在此列且未显式授权的能力一律不可用）
#: 只含"执行面最小集"——读取类工具在**显式授权**后才可见（见 `trim_toolset`）
DEFAULT_SUBAGENT_TOOLSET: Tuple[str, ...] = (
    "read_file", "list_dir", "grep", "search",
)

#: 需**显式授权**才可见的执行类工具（默认不可见——最小暴露的落点）
EXECUTION_TOOLS_REQUIRING_GRANT: Tuple[str, ...] = (
    "write_file", "shell", "run_command", "http_request", "web_fetch",
)

#: §7.0 矩阵中 sub_agent 的范围口径（**引用** S4-01 常量语义，不重复定义规则）
SCOPE_AUTHORIZED_SUBSET = "authorized_subset"


class ExposureError(Exception):
    """能力最小暴露层基类异常"""


class CapabilityNotExposedError(ExposureError):
    """请求的能力未暴露给该执行体——**拒绝**

    Attributes:
        capability: 被拒能力。
        actor_type: 执行体类型。
        classes: 命中的禁项类别（空 = 不在授权子集内）。
    """

    def __init__(self, message: str, *, capability: str = "",
                 actor_type: str = "", classes: Optional[Sequence[str]] = None) -> None:
        self.capability = str(capability or "")
        self.actor_type = str(actor_type or "")
        self.classes = list(classes or [])
        super().__init__(message)


@dataclass
class ExposureDecision:
    """单个能力的暴露判定"""

    capability: str
    exposed: bool
    classes: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"capability": self.capability, "exposed": self.exposed,
                "classes": list(self.classes), "reason": self.reason}


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def classify_capability(capability: Any) -> List[str]:
    """判定能力命中的禁项类别（空列表 = 无禁项）

    Args:
        capability: 能力 id / 工具名（如 `cp.memory.layered_store.write` / `approval.approve`）。
    """
    name = _lower(capability)
    if not name:
        return []
    classes: List[str] = []
    for category, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(name) and category not in classes:
            classes.append(category)
    return classes


def is_forbidden(capability: Any) -> bool:
    """能力是否属于绝对禁项（§5.7 机制 3 三类）"""
    return bool(classify_capability(capability))


def is_exposed(
    capability: Any,
    *,
    actor_type: str = ACTOR_SUB_AGENT,
    authorized: Optional[Iterable[str]] = None,
    toolset: Optional[Iterable[str]] = None,
) -> ExposureDecision:
    """判定能力是否暴露给该执行体（**默认闭集**）

    判定顺序：
        1. 非 sub_agent → 不受本机制约束（返回 exposed=True，交 Actor 矩阵管）；
        2. 命中绝对禁项 → **永不暴露**（`authorized` 也覆盖不了）；
        3. 在可见集（`toolset` 或默认集）内 → 暴露；
        4. 在显式 `authorized` 内 → 暴露；
        5. 其余 → **不暴露**（闭集）。

    Returns:
        `ExposureDecision`。
    """
    name = _lower(capability)
    if _lower(actor_type) != ACTOR_SUB_AGENT:
        return ExposureDecision(capability=name, exposed=True,
                                reason="非 sub_agent，不受机制 3 裁剪约束（见 Actor 矩阵）")
    classes = classify_capability(name)
    if classes:
        labels = "、".join(CLASS_LABELS.get(c, c) for c in classes)
        return ExposureDecision(
            capability=name, exposed=False, classes=classes,
            reason=(f"命中 §5.7 机制 3 绝对禁项（{labels}）——sub_agent 永不暴露，"
                    f"显式授权亦不可覆盖"),
        )
    visible = {_lower(t) for t in (toolset if toolset is not None
                                   else DEFAULT_SUBAGENT_TOOLSET)}
    granted = {_lower(t) for t in (authorized or ())}
    if name in visible:
        return ExposureDecision(capability=name, exposed=True,
                                reason="在 sub_agent 默认可见集内")
    if name in granted:
        return ExposureDecision(capability=name, exposed=True,
                                reason="在显式授权子集内（§7.0 authorized_subset）")
    return ExposureDecision(
        capability=name, exposed=False,
        reason=("不在 sub_agent 可见集内且未显式授权——默认闭集（§5.7 机制 3 "
                "能力最小暴露）"),
    )


def trim_toolset(
    requested: Iterable[str],
    *,
    actor_type: str = ACTOR_SUB_AGENT,
    authorized: Optional[Iterable[str]] = None,
    toolset: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """把请求的能力集**裁剪**为可暴露子集（S4-04 执行器注入层的输入）

    Args:
        requested: 调用方（或模型）请求的能力集。
        actor_type: 执行体类型。
        authorized: 显式授权清单。
        toolset: 覆盖默认可见集。

    Returns:
        {allowed, denied, forbidden, not_authorized, actor_type, scope}
        - `allowed`: 可暴露的能力（**顺序保持请求序**）；
        - `denied`: 被拒能力；
        - `forbidden`: 其中因**绝对禁项**被拒的；
        - `not_authorized`: 其中因"不在可见集/未授权"被拒的；
        - `scope`: §7.0 范围口径（sub_agent 恒 `authorized_subset`）。
    """
    allowed: List[str] = []
    denied: List[str] = []
    forbidden: List[str] = []
    not_authorized: List[str] = []
    for cap in (requested or ()):
        verdict = is_exposed(cap, actor_type=actor_type, authorized=authorized,
                             toolset=toolset)
        if verdict.exposed:
            allowed.append(str(cap))
        else:
            denied.append(str(cap))
            (forbidden if verdict.classes else not_authorized).append(str(cap))
    return {
        "allowed": allowed,
        "denied": denied,
        "forbidden": forbidden,
        "not_authorized": not_authorized,
        "actor_type": _lower(actor_type),
        "scope": (SCOPE_AUTHORIZED_SUBSET if _lower(actor_type) == ACTOR_SUB_AGENT else "all"),
        "requested_count": len(list(requested or ())),
    }


def require_exposed(
    capability: Any,
    *,
    actor_type: str = ACTOR_SUB_AGENT,
    authorized: Optional[Iterable[str]] = None,
    toolset: Optional[Iterable[str]] = None,
) -> None:
    """执行前置闸门：能力未暴露即**拒绝**

    Raises:
        CapabilityNotExposedError: 未暴露。
    """
    verdict = is_exposed(capability, actor_type=actor_type, authorized=authorized,
                         toolset=toolset)
    if verdict.exposed:
        return
    raise CapabilityNotExposedError(
        f"能力 {capability} 未暴露给 {actor_type or 'unknown'}：{verdict.reason}",
        capability=str(capability or ""), actor_type=str(actor_type or ""),
        classes=verdict.classes,
    )


# ════════════════════════════════════════════════════════════
#  与 S4-01 Actor 矩阵的一致性（更严者为准，且显式化）
# ════════════════════════════════════════════════════════════


def enforce_scope_consistency() -> Dict[str, Any]:
    """断言本模块的禁项与 S4-01 Actor 矩阵**不冲突**（安全侧从严）

    规则：矩阵**允许**而本模块**禁止**的能力 → 以更严者为准（拒绝），并**计入报告**
    （这是"最小暴露比矩阵更严"的显式事实，不是隐性假设）；
    矩阵**禁止**而本模块允许的能力 → 视为**缺陷**（本模块过宽），计入 `too_permissive`。

    矩阵不可得时返回 `available=False`，不视为失败（S4-01 未装载的环境仍可用本模块）。

    Returns:
        {available, stricter_than_matrix, too_permissive, matrix_denied_sample, ok}
    """
    report: Dict[str, Any] = {
        "available": False, "stricter_than_matrix": [], "too_permissive": [],
        "matrix_denied_sample": [], "ok": True,
    }
    try:
        from agent.security import actor_matrix
    except Exception as exc:  # noqa: BLE001 矩阵不可得 → 不视为失败
        report["reason"] = f"Actor 矩阵不可得: {type(exc).__name__}"
        return report
    report["available"] = True
    samples: List[str] = []
    for operation in ("approval.approve", "approval.deny", "governance.switch_forge",
                      "governance.modify_policy", "governance.force_stage",
                      "governance.remove_source", "memory.write"):
        rule = None
        try:
            rule = actor_matrix.rule_for(operation, ACTOR_SUB_AGENT)
        except Exception:  # noqa: BLE001
            rule = None
        if rule is None:
            continue
        allowed_by_matrix = bool(getattr(rule, "allowed", False))
        forbidden_by_us = is_forbidden(operation)
        samples.append(operation)
        if allowed_by_matrix and forbidden_by_us:
            report["stricter_than_matrix"].append(operation)
        elif (not allowed_by_matrix) and (not forbidden_by_us):
            # 矩阵禁但本模块的模式表没覆盖 → 本模块过宽（记下来，不阻断）
            report["too_permissive"].append(operation)
    report["matrix_denied_sample"] = samples
    report["ok"] = not report["too_permissive"]
    if report["stricter_than_matrix"]:
        logger.info("机制 3 比 Actor 矩阵更严（安全侧从严，符合预期）: %s",
                    report["stricter_than_matrix"])
    if report["too_permissive"]:
        logger.warning("机制 3 的禁项未覆盖 Actor 矩阵已禁的能力（本模块过宽）: %s",
                       report["too_permissive"])
    return report


# ════════════════════════════════════════════════════════════
#  契约导出（供 S4-04 消费）
# ════════════════════════════════════════════════════════════


def minimal_exposure_contract() -> Dict[str, Any]:
    """**能力最小暴露契约**（S4-04 的机器可读输入；勿自行猜测裁剪集）

    Returns:
        {
          actor_type, forbidden_classes, class_labels, forbidden_patterns,
          default_toolset, requires_grant, scope,
          trim: 可调用（受限于 JSON 化的边界——此处给出等价的声明式描述）
        }
    """
    return {
        "contract_version": "minimal_exposure.v1",
        "actor_type": ACTOR_SUB_AGENT,
        "forbidden_classes": list(FORBIDDEN_CAPABILITY_CLASSES),
        "class_labels": dict(CLASS_LABELS),
        "forbidden_patterns": [p.pattern for _, p in FORBIDDEN_PATTERNS],
        "default_toolset": list(DEFAULT_SUBAGENT_TOOLSET),
        "requires_grant": list(EXECUTION_TOOLS_REQUIRING_GRANT),
        "scope": SCOPE_AUTHORIZED_SUBSET,
        "closed_by_default": True,
        "authorized_subset_required": True,
        "source": "v7.2 §5.7 机制 3 + §7.0 Actor 矩阵（投影）",
    }


def exposure_state() -> Dict[str, Any]:
    """机制 3 状态快照（诊断/验收报告）"""
    return {
        "contract": minimal_exposure_contract(),
        "consistency": enforce_scope_consistency(),
    }


#: 矩阵未覆盖时（如与 `agent.security.actor_matrix` 的断言），矩阵对 sub_agent
#: 的授权子集是否存在——用于 S6-01 面板解释"为什么这个工具看不见"
def explain_denial(capability: Any, *, authorized: Optional[Iterable[str]] = None) -> str:
    """解释某能力为何对 sub_agent 不可见（面向 UI/运维的可读文案）"""
    verdict = is_exposed(capability, authorized=authorized)
    if verdict.exposed:
        return f"能力 {capability} 对 sub_agent 可见：{verdict.reason}"
    if verdict.classes:
        labels = "、".join(CLASS_LABELS.get(c, c) for c in verdict.classes)
        return (f"能力 {capability} 对 sub_agent **永久不可见**：属于 §5.7 机制 3 绝对禁项"
                f"（{labels}），显式授权亦不可覆盖")
    return (f"能力 {capability} 对 sub_agent 默认不可见（闭集）：不在默认可见集内且未出现在"
            f"授权子集清单中")


__all__ = [
    "ACTOR_SUB_AGENT", "FORBIDDEN_CAPABILITY_CLASSES", "CLASS_LABELS",
    "FORBIDDEN_PATTERNS", "DEFAULT_SUBAGENT_TOOLSET",
    "EXECUTION_TOOLS_REQUIRING_GRANT", "SCOPE_AUTHORIZED_SUBSET",
    "ExposureError", "CapabilityNotExposedError", "ExposureDecision",
    "classify_capability", "is_forbidden", "is_exposed", "trim_toolset",
    "require_exposed", "enforce_scope_consistency", "minimal_exposure_contract",
    "exposure_state", "explain_denial",
]
