"""委派契约八要素与 task_file 物化（v7.2 §3.9 / §3.10）

【任务定位】
    v7.2 §3.9 明确「**80% 的委派失败源于上下文包写得太含糊**」，故委派契约不是
    一份「建议填写的表单」，而是**准入闸门**：八要素缺任一即拒绝委派，且不得
    用默认值蒙混（本模块**不提供**任何字段默认值来补齐缺失项）。

    §3.10 规定 CLI 通道的物理协议为 ``<agent_cli> -p <task_file.json>
    --output-format json --max-turns N``，其中 ``task_file`` 就是本模块
    ``build_task_file()`` 的产物——八要素上下文包的 JSON 物化。

【不易（八要素，逐字对齐 §3.9）】
    ①目标 goal ②约束 constraints ③已有成果 prior_artifacts ④禁止事项 prohibitions
    ⑤产物格式 artifact_format ⑥预算令牌 budget_tokens ⑦超时 timeout_seconds
    ⑧回调地址 callback_url（+ tenant/subject/TraceContext）。

    「缺任一即拒绝」的精确语义（本模块的分界线）：
      - ``None`` / 未提供 / 空串 / 空白串 → **缺失**（拒绝）；
      - 列表型要素（②③④）``[]`` → **已声明为「无」**，不算缺失。
    为什么这样切：③已有成果与④禁止事项在真实委派中**合法地可能为空**（首个
    委派没有已有成果；纯探索委派无所禁止）。把它们一律判为缺失会逼迫调用方
    编造内容——那正是 §3.9 要消灭的「含糊」。而「未声明」与「声明为空」是两件
    不同的事，契约要求的是**显式声明**。②约束为空则拒绝：无约束的委派等同于
    没写目标边界，属 §3.9 点名的含糊。

【变易（表驱动）】
    ``EIGHT_ELEMENTS`` 是唯一要素清单，``_ELEMENT_CHECKS`` 是每要素的判定函数。
    新增/调整要素只改这两处，``validate_eight_elements`` / ``build_task_file``
    与全部调用点零改动。

【依赖纪律】
    本模块**零第三方依赖、零 agent 内部依赖**（纯 dataclass + 纯函数），因此可被
    执行器、通道、收集器同时导入而不构成循环依赖，也便于单测直接调用。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

# ════════════════════════════════════════════════════════════
#  八要素（§3.9）——唯一清单
# ════════════════════════════════════════════════════════════

ELEMENT_GOAL = "goal"                             # ①目标
ELEMENT_CONSTRAINTS = "constraints"               # ②约束
ELEMENT_PRIOR_ARTIFACTS = "prior_artifacts"       # ③已有成果
ELEMENT_PROHIBITIONS = "prohibitions"             # ④禁止事项
ELEMENT_ARTIFACT_FORMAT = "artifact_format"       # ⑤产物格式
ELEMENT_BUDGET_TOKENS = "budget_tokens"           # ⑥预算令牌
ELEMENT_TIMEOUT_SECONDS = "timeout_seconds"       # ⑦超时
ELEMENT_CALLBACK_URL = "callback_url"             # ⑧回调地址

#: 八要素（文档顺序即校验报告顺序）
EIGHT_ELEMENTS: Tuple[str, ...] = (
    ELEMENT_GOAL,
    ELEMENT_CONSTRAINTS,
    ELEMENT_PRIOR_ARTIFACTS,
    ELEMENT_PROHIBITIONS,
    ELEMENT_ARTIFACT_FORMAT,
    ELEMENT_BUDGET_TOKENS,
    ELEMENT_TIMEOUT_SECONDS,
    ELEMENT_CALLBACK_URL,
)

#: 要素 → 人读标签（拒绝原因必须点名缺哪一项，故标签与序号一并固化）
ELEMENT_LABELS: Dict[str, str] = {
    ELEMENT_GOAL: "①目标",
    ELEMENT_CONSTRAINTS: "②约束",
    ELEMENT_PRIOR_ARTIFACTS: "③已有成果",
    ELEMENT_PROHIBITIONS: "④禁止事项",
    ELEMENT_ARTIFACT_FORMAT: "⑤产物格式",
    ELEMENT_BUDGET_TOKENS: "⑥预算令牌",
    ELEMENT_TIMEOUT_SECONDS: "⑦超时",
    ELEMENT_CALLBACK_URL: "⑧回调地址",
}

#: 目标的最小字符数（§3.9「写得太含糊」的机器可判定下界）
MIN_GOAL_CHARS = 8

#: 拒绝错误码（可入审计/事件载荷）
E_DELEGATION_INCOMPLETE = "E_DELEGATION_INCOMPLETE"

#: task_file schema 版本
TASK_FILE_SCHEMA_VERSION = 1


def is_declared(value: Any) -> bool:
    """要素是否**已显式声明**（None / 空串 / 空白串 → 未声明）

    列表型要素走同一判定：``[]`` 已声明（语义为「无」），``None`` 未声明。
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        return True
    return True


def _check_goal(value: Any) -> str:
    if not is_declared(value):
        return "目标是必填项（不得为空）"
    if len(str(value).strip()) < MIN_GOAL_CHARS:
        return (f"目标过于含糊（仅 {len(str(value).strip())} 字符，"
                f"少于 {MIN_GOAL_CHARS}）——§3.9：80% 委派失败源于此")
    return ""


def _check_str_list(value: Any, *, require_non_empty: bool) -> str:
    if not is_declared(value):
        return "必须显式声明（无内容时传空列表 []，不得省略）"
    if not isinstance(value, (list, tuple)):
        return f"必须是字符串列表，实际为 {type(value).__name__}"
    items = list(value)
    if require_non_empty and not items:
        return "不得为空列表（无约束的委派等同于未声明边界）"
    for idx, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            return f"第 {idx + 1} 项不是非空字符串"
    return ""


def _check_artifacts(value: Any) -> str:
    return _check_str_list(value, require_non_empty=False)


def _check_prohibitions(value: Any) -> str:
    return _check_str_list(value, require_non_empty=False)


def _check_artifact_format(value: Any) -> str:
    if not is_declared(value):
        return "产物格式是必填项（子代理必须知道交付什么）"
    return ""


def _check_budget(value: Any) -> str:
    if value is None:
        return "预算令牌是必填项（缺失会导致成本不可控）"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"必须是正整数，实际为 {type(value).__name__}"
    if isinstance(value, float) and not float(value).is_integer():
        return "必须是整数（令牌数不可为小数）"
    if int(value) <= 0:
        return "必须大于 0"
    return ""


def _check_timeout(value: Any) -> str:
    if value is None:
        return "超时是必填项（缺失会让委派永久挂起）"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"必须是正数秒，实际为 {type(value).__name__}"
    if float(value) <= 0:
        return "必须大于 0 秒"
    return ""


def _check_callback(value: Any) -> str:
    if not is_declared(value):
        return "回调地址是必填项（结果无处投递）"
    return ""


#: 要素 → 判定函数（返回 "" 表示通过，否则返回可读原因）
_ELEMENT_CHECKS: Dict[str, Callable[[Any], str]] = {
    ELEMENT_GOAL: _check_goal,
    ELEMENT_CONSTRAINTS: lambda v: _check_str_list(v, require_non_empty=True),
    ELEMENT_PRIOR_ARTIFACTS: _check_artifacts,
    ELEMENT_PROHIBITIONS: _check_prohibitions,
    ELEMENT_ARTIFACT_FORMAT: _check_artifact_format,
    ELEMENT_BUDGET_TOKENS: _check_budget,
    ELEMENT_TIMEOUT_SECONDS: _check_timeout,
    ELEMENT_CALLBACK_URL: _check_callback,
}


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════


class DelegationError(Exception):
    """委派契约相关异常基类"""


class DelegationRejected(DelegationError):
    """委派被拒绝（八要素不合格）——**缺哪一项必须点名**，不得含糊拒绝

    Attributes:
        missing: 缺失要素名（``EIGHT_ELEMENTS`` 顺序）。
        reasons: 要素名 → 具体不合格原因。
        code: 错误码（固定 ``E_DELEGATION_INCOMPLETE``，可入审计载荷）。
    """

    code = E_DELEGATION_INCOMPLETE

    def __init__(self, missing: Sequence[str],
                 reasons: Optional[Mapping[str, str]] = None) -> None:
        self.missing: Tuple[str, ...] = tuple(missing)
        self.reasons: Dict[str, str] = dict(reasons or {})
        detail = "；".join(
            f"{ELEMENT_LABELS.get(k, k)}：{self.reasons.get(k, '缺失')}"
            for k in self.missing)
        super().__init__(
            f"委派被拒绝（{E_DELEGATION_INCOMPLETE}）——不合格要素 "
            f"{len(self.missing)}/8：{detail}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "missing": list(self.missing),
            "missing_labels": [ELEMENT_LABELS.get(k, k) for k in self.missing],
            "reasons": dict(self.reasons),
        }


# ════════════════════════════════════════════════════════════
#  委派上下文
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DelegationContext:
    """委派上下文包（八要素 + tenant/subject/TraceContext，§3.9）

    所有要素字段**无默认值**（除 tenant/subject 一类的标识字段），从类型层面
    阻断「忘记填」被静默补全的可能。

    Attributes:
        goal: ①目标（≥ ``MIN_GOAL_CHARS`` 字符）。
        constraints: ②约束（非空字符串列表）。
        prior_artifacts: ③已有成果引用（可为空列表 = 声明「无」）。
        prohibitions: ④禁止事项（可为空列表 = 声明「无」）。
        artifact_format: ⑤产物格式。
        budget_tokens: ⑥预算令牌（正整数）。
        timeout_seconds: ⑦超时（正数秒）。
        callback_url: ⑧回调地址。
        tenant_id / subject_id: 租户与主体（§2.7 隔离口径）。
        task_id: 编排任务 id（Trace 任务级归属）。
        trace_id / parent_trace_id: 父 Trace 串联字段（子 Trace 由执行器经
            ``TraceContext.child()`` 生成，本类只承载父链标识）。
        policy_version: 生效策略版本（审计口径）。
        delegation_id: 本次委派标识（缺省自动生成）。
        delegate_actor: 子代理执行体标识（默认 ``sub_agent:<id>``，供 actor
            矩阵推断为 sub_agent 类型）。
        metadata: 追加叶子字段（**只放标识不放原文**）。
    """

    goal: str
    constraints: Sequence[str]
    prior_artifacts: Optional[Sequence[str]]
    prohibitions: Optional[Sequence[str]]
    artifact_format: str
    budget_tokens: int
    timeout_seconds: float
    callback_url: str
    tenant_id: str = "default"
    subject_id: str = ""
    task_id: str = ""
    trace_id: str = ""
    parent_trace_id: str = ""
    policy_version: str = ""
    delegation_id: str = ""
    delegate_actor: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.delegation_id:
            object.__setattr__(self, "delegation_id", f"dlg-{uuid.uuid4().hex[:12]}")
        if not self.delegate_actor:
            object.__setattr__(self, "delegate_actor", f"sub_agent:{self.delegation_id}")
        # 冻结列表为元组：跨线程传递（ThreadPoolExecutor）时避免共享可变序列。
        # **None 保持 None**——「未声明」与「声明为空」是两件事，归一化会把
        # 未声明悄悄变成「已声明为无」，从而绕过 §3.9 的准入校验。
        for name in ("constraints", "prior_artifacts", "prohibitions"):
            value = getattr(self, name)
            if value is None:
                continue
            object.__setattr__(self, name, tuple(value))

    # ── 八要素视图 ──

    def elements(self) -> Dict[str, Any]:
        """八要素的原始取值（校验与物化的共同输入）"""
        return {name: getattr(self, name) for name in EIGHT_ELEMENTS}

    def validate(self) -> Tuple[str, ...]:
        """返回不合格要素名（空元组 = 全部合格）"""
        return validate_eight_elements(self.elements())

    def require_valid(self) -> "DelegationContext":
        """自校验（不合格抛 ``DelegationRejected``），支持链式调用"""
        reasons = element_problems(self.elements())
        if reasons:
            raise DelegationRejected(list(reasons.keys()), reasons)
        return self

    def to_dict(self) -> Dict[str, Any]:
        """delegation 元数据字典（八要素 + 标识；不含 task_file 包装）"""
        data: Dict[str, Any] = {
            "delegation_id": self.delegation_id,
            "delegate_actor": self.delegate_actor,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "task_id": self.task_id or self.delegation_id,
            "trace_id": self.trace_id,
            "parent_trace_id": self.parent_trace_id,
            "policy_version": self.policy_version,
        }
        data.update({k: (list(v) if isinstance(v, tuple) else v)
                     for k, v in self.elements().items()})
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data


# ════════════════════════════════════════════════════════════
#  校验
# ════════════════════════════════════════════════════════════


def element_problems(elements: Mapping[str, Any]) -> Dict[str, str]:
    """逐要素判定，返回 {要素名: 原因}（仅含不合格项；保持 ``EIGHT_ELEMENTS`` 顺序）

    「缺失」与「填错」合并报告：调用方拿到的都是「为什么不合格」，无需二次分类。
    """
    problems: Dict[str, str] = {}
    for name in EIGHT_ELEMENTS:
        check = _ELEMENT_CHECKS[name]
        if name not in elements:
            problems[name] = "未提供该要素"
            continue
        reason = check(elements.get(name))
        if reason:
            problems[name] = reason
    return problems


def validate_eight_elements(elements: Mapping[str, Any]) -> Tuple[str, ...]:
    """八要素校验 → 缺失/不合格要素名（``EIGHT_ELEMENTS`` 顺序）

    这是「缺任一即拒绝委派」的唯一判定入口；返回空元组表示可放行。
    """
    return tuple(element_problems(elements).keys())


def require_eight_elements(elements: Mapping[str, Any]) -> None:
    """校验并在不合格时抛 ``DelegationRejected``（点名缺哪一项）"""
    problems = element_problems(elements)
    if problems:
        raise DelegationRejected(list(problems.keys()), problems)


def require_delegation(ctx: DelegationContext) -> DelegationContext:
    """校验 ``DelegationContext``（不合格抛 ``DelegationRejected``）"""
    return ctx.require_valid()


# ════════════════════════════════════════════════════════════
#  task_file 物化（§3.10）
# ════════════════════════════════════════════════════════════


def build_task_file(ctx: DelegationContext, *, validate: bool = True) -> Dict[str, Any]:
    """把委派上下文物化为 task_file（§3.10「八要素上下文包的 JSON 物化」）

    结构（八要素**平铺在顶层**，CLI 侧可直接读取，无需理解云枢内部分组）::

        {
          "schema_version": 1,
          "task_id": ..., "delegation_id": ...,
          "goal": ..., "constraints": [...], "prior_artifacts": [...],
          "prohibitions": [...], "artifact_format": ...,
          "budget_tokens": N, "timeout_seconds": N, "callback_url": ...,
          "tenancy": {"tenant_id": ..., "subject_id": ...},
          "trace": {"trace_id": ..., "parent_trace_id": ..., "task_id": ...},
          "policy_version": ..., "delegate_actor": ...
        }

    Args:
        ctx: 委派上下文。
        validate: True（默认）时先校验八要素，不合格抛 ``DelegationRejected``。
            **不提供**「跳过校验直接物化」的默认路径——物化即准入。

    Raises:
        DelegationRejected: 八要素缺任一。
    """
    if validate:
        require_delegation(ctx)
    payload: Dict[str, Any] = {
        "schema_version": TASK_FILE_SCHEMA_VERSION,
        "task_id": ctx.task_id or ctx.delegation_id,
        "delegation_id": ctx.delegation_id,
        "delegate_actor": ctx.delegate_actor,
    }
    payload.update({k: (list(v) if isinstance(v, tuple) else v)
                    for k, v in ctx.elements().items()})
    payload["tenancy"] = {
        "tenant_id": ctx.tenant_id,
        "subject_id": ctx.subject_id,
    }
    payload["trace"] = {
        "trace_id": ctx.trace_id,
        "parent_trace_id": ctx.parent_trace_id,
        "task_id": ctx.task_id or ctx.delegation_id,
    }
    payload["policy_version"] = ctx.policy_version
    if ctx.metadata:
        payload["metadata"] = dict(ctx.metadata)
    return payload


def write_task_file(ctx: DelegationContext, path: str, *,
                    validate: bool = True) -> str:
    """物化并写出 task_file（UTF-8 JSON），返回实际写入路径

    Raises:
        DelegationRejected: 八要素缺任一（**不落盘**，避免产生半成品任务文件）。
    """
    payload = build_task_file(ctx, validate=validate)
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
    return target


def load_task_file(path: str) -> Dict[str, Any]:
    """读取 task_file（不校验，交由 ``delegation_from_task_file`` 判定）"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise DelegationError(f"task_file 必须是 JSON 对象：{path}")
    return data


def delegation_from_task_file(data: Mapping[str, Any]) -> DelegationContext:
    """task_file → ``DelegationContext``（往返一致；要素缺失时**保留为 None**）

    刻意不填默认值：缺失要素原样透传，使 ``require_valid()`` 能如实点名。
    """
    tenancy = data.get("tenancy") or {}
    trace = data.get("trace") or {}
    return DelegationContext(
        goal=data.get("goal"),                                   # type: ignore[arg-type]
        constraints=data.get("constraints"),                     # type: ignore[arg-type]
        prior_artifacts=data.get("prior_artifacts"),             # type: ignore[arg-type]
        prohibitions=data.get("prohibitions"),                   # type: ignore[arg-type]
        artifact_format=data.get("artifact_format"),             # type: ignore[arg-type]
        budget_tokens=data.get("budget_tokens"),                 # type: ignore[arg-type]
        timeout_seconds=data.get("timeout_seconds"),             # type: ignore[arg-type]
        callback_url=data.get("callback_url"),                   # type: ignore[arg-type]
        tenant_id=str(tenancy.get("tenant_id") or "default"),
        subject_id=str(tenancy.get("subject_id") or ""),
        task_id=str(data.get("task_id") or ""),
        trace_id=str(trace.get("trace_id") or ""),
        parent_trace_id=str(trace.get("parent_trace_id") or ""),
        policy_version=str(data.get("policy_version") or ""),
        delegation_id=str(data.get("delegation_id") or ""),
        delegate_actor=str(data.get("delegate_actor") or ""),
        metadata=data.get("metadata") or {},
    )


def make_task_file(ctx: DelegationContext, path: str) -> str:
    """``write_task_file`` 的别名（对齐 §3.10 用词 ``task_file``）"""
    return write_task_file(ctx, path)


__all__ = [
    # 八要素
    "ELEMENT_GOAL", "ELEMENT_CONSTRAINTS", "ELEMENT_PRIOR_ARTIFACTS",
    "ELEMENT_PROHIBITIONS", "ELEMENT_ARTIFACT_FORMAT", "ELEMENT_BUDGET_TOKENS",
    "ELEMENT_TIMEOUT_SECONDS", "ELEMENT_CALLBACK_URL",
    "EIGHT_ELEMENTS", "ELEMENT_LABELS", "MIN_GOAL_CHARS",
    "E_DELEGATION_INCOMPLETE", "TASK_FILE_SCHEMA_VERSION",
    # 上下文
    "DelegationContext",
    # 异常
    "DelegationError", "DelegationRejected",
    # 校验
    "is_declared", "element_problems", "validate_eight_elements",
    "require_eight_elements", "require_delegation",
    # 物化
    "build_task_file", "write_task_file", "make_task_file",
    "load_task_file", "delegation_from_task_file",
]
