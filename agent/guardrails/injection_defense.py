"""注入防御六机制 —— 统一接线与总闸门（TASK-S4-03 步骤 4 / v7.2 §5.7）

【§5.7 六机制 ↔ 本包模块】

    #  机制（§5.7 逐字）       落地模块                        入口
    1  Taint 标记              `foreign_taint.py`              `guard_system_prompt` / `guard_decision_branch`
    2  指令/数据分离            `instruction_data.py`           `guard_tool_call`
    3  能力最小暴露            `capability_exposure.py`        `trim_toolset` / `require_exposed`
    4  出域链路监测            `egress_chain.py`               `EgressChainMonitor.evaluate`
    5  人机边界词              `boundary_words.py`             `guard_execution`
    6  UI 安全渲染             `safe_render.py`                `render_safe` / `render_structured_slot`
    ⑦  审批面安全              **S4-01**（`agent/security/`）  不在本包（勿重造）

【本模块做什么】
    1. `SIX_MECHANISMS` —— 六机制的**声明式清单**（验收报告与面板的数据源）；
    2. `guard_tool_execution()` —— 把机制 **2 + 5** 合成**一道**执行前置闸门
       （工具执行前只调一次，避免调用点散落两处判定）；
    3. `guard_context_assembly()` —— 机制 **1** 在**上下文组装**侧的接线
       （分段判定：外来段出 system prompt，改走沙箱槽位）；
    4. `defense_status()` —— 六机制接线状态快照（`/api`… 面板与验收报告用）。

【为什么机制 2 与 5 合成一道闸门】
    两者都在**同一个位置**生效（工具/动作执行前），且判定顺序固定：
    先看"参数能不能用"（机制 2），再看"这个动作要不要人批"（机制 5）。
    分成两道会让调用点要么漏调一道，要么顺序写反——合成一道是减少接口面。

【本模块不做什么（守边界）】
    - 不实现机制 ⑦（审批面安全）——S4-01 已完成；
    - 不实现出域**执行点**——S4-02 `agent/guardrails/egress_guard.py` 已完成，
      本包 `egress_chain` 只在它之上加"链路视角 + 熔断 + 事故卡"；
    - 不实现 subagent 的**执行器**工具注入层——S4-04 负责，本包只给裁剪契约。

【不易】闸门默认开启但**无命中即零影响**；任何内部异常 fail-open（不阻断主流程），
       但**已判定的拒绝绝不 fail-open**。
【变易】`SIX_MECHANISMS` 是数据；机制实现换代只需改 `module`/`entry` 两列。
【简易】纯标准库 + 本包兄弟模块（全部延迟导入，不加重导入期依赖）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("agent.guardrails.injection_defense")

# ════════════════════════════════════════════════════════════
#  六机制清单（数据）
# ════════════════════════════════════════════════════════════

#: 每个机制项的字段：机制号 / 名称（§5.7 逐字）/ 实现模块 / 入口 / 接线点 / 状态
#: 状态取值：`implemented`（本任务实做）/ `wired`（接线到既有设施，非本包实现）
@dataclass(frozen=True)
class MechanismSpec:
    """单条注入防御机制的声明式规格"""

    number: str                 # "1".."7"
    name: str                   # §5.7 逐字名称
    module: str                 # 实现模块
    entry: Tuple[str, ...]      # 入口符号
    wiring: str                 # 接线点（真实调用位置）
    status: str                 # implemented / wired / external

    def to_dict(self) -> Dict[str, Any]:
        return {"number": self.number, "name": self.name, "module": self.module,
                "entry": list(self.entry), "wiring": self.wiring,
                "status": self.status}


SIX_MECHANISMS: Tuple[MechanismSpec, ...] = (
    MechanismSpec(
        number="1", name="Taint 标记",
        module="agent.guardrails.foreign_taint",
        entry=("mark_foreign", "guard_system_prompt", "guard_decision_branch",
               "wrap_untrusted"),
        wiring=("agent.context.assembler::ContextAssembler.assemble_guarded（分段判定，"
                "外来段出 system prompt）"),
        status="implemented",
    ),
    MechanismSpec(
        number="2", name="指令/数据分离",
        module="agent.guardrails.instruction_data",
        entry=("guard_tool_call", "split_segments", "render_data_block"),
        wiring="guard_tool_execution（工具/动作执行前置闸门）",
        status="implemented",
    ),
    MechanismSpec(
        number="3", name="能力最小暴露",
        module="agent.guardrails.capability_exposure",
        entry=("trim_toolset", "require_exposed", "minimal_exposure_contract"),
        wiring=("agent.subagent 执行器工具注入层（**S4-04 落地**；本包出契约 "
                "minimal_exposure_contract()）"),
        status="wired",
    ),
    MechanismSpec(
        number="4", name="出域链路监测",
        module="agent.guardrails.egress_chain",
        entry=("EgressChainMonitor.evaluate", "record_secret_read"),
        wiring=("组合 S4-02：agent.policy.taint（读端点）+ agent.policy.egress（判定端）+ "
                "agent.guardrails.egress_guard（执行端）；本包补链路视角 + 熔断 + 事故卡"),
        status="wired",
    ),
    MechanismSpec(
        number="5", name="人机边界词",
        module="agent.guardrails.boundary_words",
        entry=("guard_execution", "detect_boundary_words", "BoundaryConfirmation"),
        wiring="guard_tool_execution（工具/动作执行前置闸门；UI 确认走 S4-01 审批面）",
        status="implemented",
    ),
    MechanismSpec(
        number="6", name="UI 安全渲染",
        module="agent.guardrails.safe_render",
        entry=("render_safe", "sanitize_html", "csp_headers",
               "render_structured_slot", "taint_badge"),
        wiring=("后端约束与校验（本任务）；前端组件（TaintBadge / 审批区 DOM 隔离 / "
                "确认 UI）由 **S6-01** 落地"),
        status="implemented",
    ),
    MechanismSpec(
        number="7", name="审批面安全〔P7.1-17〕",
        module="agent.security",
        entry=("approval_session", "actor_matrix", "approval_guard"),
        wiring="**S4-01 已交付**（会话绑定 / CSRF / 链接时效 ≤900s / 越权告警 / "
                "destructive 二次认证 / 按钮区 DOM 隔离）——本包不重造",
        status="external",
    ),
)

#: 本任务实做的机制号（§5.7 表 1-6；⑦ 由 S4-01 承担）
IMPLEMENTED_MECHANISMS: Tuple[str, ...] = ("1", "2", "3", "4", "5", "6")

#: 上下文组装守卫的开关（**默认关**：开启会改变 system prompt 组成，
#: 属"行为变更"，必须由部署侧显式打开）
ENV_GUARD_CONTEXT = "CP_GUARDRAILS_GUARD_CONTEXT"

#: 工具执行闸门开关（**默认开**：无边界词、无参数违规时零影响）
ENV_GUARD_TOOL = "CP_GUARDRAILS_GUARD_TOOL"


class InjectionDefenseError(Exception):
    """注入防御总闸门基类异常"""


def _env_flag(name: str, default: str) -> bool:
    return str(os.environ.get(name, default)).strip().lower() \
        not in ("0", "false", "no", "off")


def guard_context_enabled() -> bool:
    """上下文组装守卫是否启用（默认**关**——会改变 system prompt 组成）"""
    return _env_flag(ENV_GUARD_CONTEXT, "0")


def guard_tool_enabled() -> bool:
    """工具执行闸门是否启用（默认**开**——无命中即零影响）"""
    return _env_flag(ENV_GUARD_TOOL, "1")


# ════════════════════════════════════════════════════════════
#  机制 1：上下文组装侧接线
# ════════════════════════════════════════════════════════════


@dataclass
class AssemblyGuardResult:
    """上下文组装守卫结果"""

    allowed_segments: List[str] = field(default_factory=list)   # 可进 system prompt
    sandbox_blocks: List[Dict[str, Any]] = field(default_factory=list)  # 只能进槽位
    blocked: List[Dict[str, Any]] = field(default_factory=list)  # 被拦的段（诊断）
    enabled: bool = True

    @property
    def changed(self) -> bool:
        return bool(self.blocked)

    def to_dict(self) -> Dict[str, Any]:
        return {"enabled": self.enabled,
                "allowed_count": len(self.allowed_segments),
                "sandbox_block_count": len(self.sandbox_blocks),
                "blocked_count": len(self.blocked),
                "blocked": list(self.blocked)}

    def render(self) -> str:
        """渲染：可信段在前，沙箱段以 `cp-data` 包裹（**不回灌指令区**）"""
        from agent.guardrails.instruction_data import (
            DATA_BLOCK_CLOSE, DATA_BLOCK_OPEN, DATA_BLOCK_PREAMBLE)
        lines = list(self.allowed_segments)
        for block in self.sandbox_blocks:
            lines.append(DATA_BLOCK_PREAMBLE)
            lines.append(DATA_BLOCK_OPEN)
            lines.append(str(block.get("text") or ""))
            lines.append(DATA_BLOCK_CLOSE)
        return "\n".join(lines)


def guard_context_assembly(
    segments: Sequence[Mapping[str, Any]],
    *,
    ledger: Optional[Any] = None,
) -> AssemblyGuardResult:
    """**机制 1 在组装侧的接线**：逐段判定，外来段出 system prompt

    Args:
        segments: 待组装段。每段形如
            `{"text": ..., "source": "retrieval|mcp|subagent|file|trusted", "ref": ...}`
            ——`source` 为 `trusted`（缺省）时**不**打污点标记，直接放行。
        ledger: 外来文本污点账（缺省进程级账）。

    Returns:
        `AssemblyGuardResult`（`allowed_segments` 可进 system prompt；
        `sandbox_blocks` 只能进受沙箱槽位）。
    """
    from agent.guardrails.foreign_taint import (
        DEST_SYSTEM_PROMPT, ForeignSource, check_text, get_foreign_taint,
        wrap_untrusted)
    from agent.guardrails.instruction_data import DATA_BLOCK_PREAMBLE

    active = ledger or get_foreign_taint()
    result = AssemblyGuardResult()
    trusted_sources = {"trusted", "", "working_memory", "user"}
    for index, raw in enumerate(segments or ()):
        text = str((raw or {}).get("text") or "")
        source = str((raw or {}).get("source") or "trusted").strip().lower()
        ref = str((raw or {}).get("ref") or "")
        if not text:
            continue
        if source in trusted_sources:
            result.allowed_segments.append(text)
            continue
        # 外来段：登记污点 → 判定能否进 system prompt
        try:
            wrap_untrusted(text, source, ref=ref, ledger=active)
        except Exception as exc:  # noqa: BLE001 标记失败不阻断组装
            logger.warning("组装期外来段标记失败: %s", exc)
        verdict = check_text(text, destination=DEST_SYSTEM_PROMPT, ledger=active,
                             surface=f"context_segment[{index}]")
        if verdict.allowed:
            result.allowed_segments.append(text)
            continue
        block = wrap_untrusted(text, source, ref=ref, ledger=active)
        block["source_label"] = str(block.get("source_label") or source)
        block["blocked_from"] = DEST_SYSTEM_PROMPT
        result.sandbox_blocks.append(block)
        result.blocked.append({"index": index, "source": source, "ref": ref,
                               "reason": verdict.reason,
                               "chars": len(text),
                               "preamble": DATA_BLOCK_PREAMBLE})
    return result


# ════════════════════════════════════════════════════════════
#  机制 2 + 5：工具/动作执行前置总闸门
# ════════════════════════════════════════════════════════════


@dataclass
class ToolGateResult:
    """工具执行总闸门结果"""

    allowed: bool
    tool_name: str = ""
    stage: str = ""                       # 被拦在哪个机制
    reason: str = ""
    parameter_verdict: Optional[Any] = None
    boundary_verdict: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed, "tool_name": self.tool_name,
            "stage": self.stage, "reason": self.reason,
            "parameter": (self.parameter_verdict.to_dict()
                          if self.parameter_verdict is not None else None),
            "boundary": (self.boundary_verdict.to_dict()
                         if self.boundary_verdict is not None else None),
        }


def guard_tool_execution(
    tool_name: str,
    arguments: Optional[Mapping[str, Any]] = None,
    *,
    action: Any = None,
    action_text: str = "",
    origin: Any = None,
    arg_origins: Optional[Mapping[str, Any]] = None,
    token: Any = None,
    ledger: Optional[Any] = None,
    store: Optional[Any] = None,
    enforce: bool = False,
    actor: str = "auto",
    tenant_id: str = "default",
) -> ToolGateResult:
    """**机制 2 + 5 总闸门**：工具/动作执行前只调一次

    判定顺序（**固定**，不可颠倒）：
        1. 机制 2：`guard_tool_call` —— 参数是否合规（来源 / 污点 / 指令形态）；
        2. 机制 5：`guard_execution` —— 动作是否属"永不自动化五类"、有无单次确认。

    Args:
        tool_name: 工具名。
        arguments: 参数字典。
        action: action 摘要绑定主体（缺省用 `{tool_name, arguments}`）。
        action_text: 边界词识别文本（缺省由 `tool_name + arguments` 拼出）。
        origin: 参数来源（`ArgumentOrigin`；缺省由本函数构造**决策层**来源——
            调用方若无法保证参数来自决策层，必须显式传 `Origin/`FOREIGN`）。
        token: UI 单次确认凭据（机制 5）。
        enforce: True → 被拦即抛异常（机制 2 抛 `ParameterContaminationError`，
            机制 5 抛 `ConfirmationRequiredError`）。

    Returns:
        `ToolGateResult`。
    """
    if not guard_tool_enabled():
        return ToolGateResult(allowed=True, tool_name=str(tool_name or ""),
                              stage="disabled", reason="工具闸门未启用")
    from agent.guardrails.boundary_words import guard_execution
    from agent.guardrails.instruction_data import ArgumentOrigin, guard_tool_call

    args = dict(arguments or {})
    if origin is None:
        origin = ArgumentOrigin(origin="decision_layer",
                                decision_id=f"tool:{tool_name}")
    # ── 机制 2 ──
    param_verdict = guard_tool_call(
        tool_name, args, origin=origin, arg_origins=arg_origins, ledger=ledger,
        enforce=enforce)
    if not param_verdict.allowed:
        return ToolGateResult(allowed=False, tool_name=str(tool_name or ""),
                              stage="instruction_data",
                              reason=param_verdict.reason,
                              parameter_verdict=param_verdict)
    # ── 机制 5 ──
    bound_action = action if action is not None else {
        "tool": str(tool_name or ""), "arguments": args}
    probe = str(action_text or f"{tool_name} {args}")
    boundary_verdict = guard_execution(
        bound_action, action_text=probe, token=token, store=store,
        enforce=enforce, actor=actor)
    if not boundary_verdict.allowed:
        return ToolGateResult(allowed=False, tool_name=str(tool_name or ""),
                              stage="boundary_words",
                              reason=boundary_verdict.reason,
                              parameter_verdict=param_verdict,
                              boundary_verdict=boundary_verdict)
    return ToolGateResult(allowed=True, tool_name=str(tool_name or ""),
                          reason="机制 2 与机制 5 均通过",
                          parameter_verdict=param_verdict,
                          boundary_verdict=boundary_verdict)


# ════════════════════════════════════════════════════════════
#  状态与验收自检
# ════════════════════════════════════════════════════════════


def defense_status() -> Dict[str, Any]:
    """六机制接线状态快照（验收报告 / S6-01 面板数据源）

    逐机制给出：实现模块、入口、接线点、**运行时是否启用**、**当前计数**。
    每项的运行时指标都取自各机制自己的账，不另建聚合（勿自建计数器）。
    """
    status: Dict[str, Any] = {
        "schema": "injection_defense.v1",
        "mechanisms": [m.to_dict() for m in SIX_MECHANISMS],
        "implemented": list(IMPLEMENTED_MECHANISMS),
        "switches": {
            "guard_context": guard_context_enabled(),
            "guard_tool": guard_tool_enabled(),
        },
        "runtime": {},
    }
    runtime: Dict[str, Any] = status["runtime"]
    try:
        from agent.guardrails.foreign_taint import taint_state
        runtime["1_taint"] = taint_state()
    except Exception as exc:  # noqa: BLE001
        runtime["1_taint"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from agent.guardrails.instruction_data import instruction_data_state
        runtime["2_instruction_data"] = instruction_data_state()
    except Exception as exc:  # noqa: BLE001
        runtime["2_instruction_data"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from agent.guardrails.capability_exposure import exposure_state
        runtime["3_capability_exposure"] = exposure_state()
    except Exception as exc:  # noqa: BLE001
        runtime["3_capability_exposure"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from agent.guardrails.egress_chain import egress_chain_state
        runtime["4_egress_chain"] = egress_chain_state()
    except Exception as exc:  # noqa: BLE001
        runtime["4_egress_chain"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from agent.guardrails.boundary_words import boundary_state
        runtime["5_boundary_words"] = boundary_state()
    except Exception as exc:  # noqa: BLE001
        runtime["5_boundary_words"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from agent.guardrails.safe_render import safe_render_state
        runtime["6_safe_render"] = safe_render_state()
    except Exception as exc:  # noqa: BLE001
        runtime["6_safe_render"] = {"error": f"{type(exc).__name__}: {exc}"}
    return status


def mechanism_table() -> List[Dict[str, Any]]:
    """六机制接线表（Markdown 渲染用手的数据）"""
    return [m.to_dict() for m in SIX_MECHANISMS]


def render_mechanism_markdown() -> str:
    """六机制接线表（Markdown；交付文档直接粘贴）"""
    lines = [
        "| # | 机制（§5.7） | 实现模块 | 入口 | 接线点 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    labels = {"implemented": "✅ 本任务实做", "wired": "🔗 接线既有设施",
              "external": "↗ S4-01 承担"}
    for row in mechanism_table():
        lines.append(
            "| {number} | {name} | `{module}` | {entry} | {wiring} | {status} |".format(
                number=row["number"], name=row["name"], module=row["module"],
                entry="<br>".join(f"`{e}`" for e in row["entry"]),
                wiring=row["wiring"], status=labels.get(row["status"], row["status"]),
            )
        )
    return "\n".join(lines)


__all__ = [
    "SIX_MECHANISMS", "IMPLEMENTED_MECHANISMS", "MechanismSpec",
    "ENV_GUARD_CONTEXT", "ENV_GUARD_TOOL",
    "InjectionDefenseError",
    "guard_context_enabled", "guard_tool_enabled",
    "AssemblyGuardResult", "guard_context_assembly",
    "ToolGateResult", "guard_tool_execution",
    "defense_status", "mechanism_table", "render_mechanism_markdown",
]
