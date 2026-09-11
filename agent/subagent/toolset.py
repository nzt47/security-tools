"""子代理工具裁剪子集（v7.2 §5.7 机制 3 + §7.0 Actor 矩阵）

【任务定位】
    §5.7 机制 3「能力最小暴露」：**子智能体工具集为裁剪子集（不含记忆读写 /
    核心改写 / 审批权）**。§7.0 把这条约束写成矩阵的一行：执行 capability 时
    sub_agent 命中 ``SCOPE_AUTHORIZED_SUBSET``（授权子集），其余治理/记忆行一律
    ❌。本模块是这两条在**执行器工具注入层**的落地：子代理能看到的工具白名单
    由本模块计算，越界调用在**调用点**被拒。

【守不易（单一权威）】
    权限的**唯一权威是 ``agent.security.actor_matrix``**（S4-01 交付，后端单表）。
    本模块不复制矩阵，只在工具名与矩阵操作之间建立**映射表**
    （``_TOOL_OPERATION_RULES``），再经 ``decide()`` 判定。因此：
      - 矩阵新增/收紧一行 → 本模块自动跟随（无需改代码）；
      - 本模块新增工具类别 → 必须显式登记映射，未登记的受保护类别 **fail-closed
        拒绝**（宁可少给能力，不可悄悄多给）。

【间接调用也要拦】
    「裁剪集外调用必须被拒（含间接调用路径）」。工具名可能被包装成
    ``mcp:filesystem::memory.write`` / ``sub_agent/approval.approve`` 一类形式，
    或藏在 ``alias`` / ``target`` / ``redirect`` 字段里。故 ``name_candidates()``
    对原始名做**多形态展开**（分隔符切分后的全部后缀），任一形态命中拒绝规则即
    整体拒绝；``check_spec()`` 则扫描工具规格字典里的全部别名槽位。

【依赖纪律】
    仅依赖 ``agent.security.actor_matrix``（纯表 + 纯函数，零内部依赖），
    不导入执行器/通道，避免循环依赖。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.security.actor_matrix import (
    ACTOR_SUB_AGENT,
    OP_APPROVE,
    OP_DENY,
    OP_EXECUTE_CAPABILITY,
    OP_FORCE_STAGE,
    OP_MODIFY_POLICY,
    OP_REMOVE_SOURCE,
    OP_SUBMIT_APPROVAL,
    OP_SWITCH_FORGE,
    OP_VIEW_MEMORY,
    OP_VIEW_PANEL,
    OP_VIEW_TRACE,
    OP_WRITE_MEMORY,
    PermissionContext,
    PermissionDecision,
    decide,
)

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  工具名 → 矩阵操作（唯一映射表；未登记 = fail-closed）
# ════════════════════════════════════════════════════════════

#: 工具名（规范化后）→ §7.0 矩阵操作
#: 为什么用映射而不是复制一张「禁列表」：矩阵是唯一权威，映射只是**寻址**。
_TOOL_OPERATION_RULES: Dict[str, str] = {
    # ── 记忆读写（§5.7 机制 3 点名：不含记忆读写）──
    "memory.read": OP_VIEW_MEMORY,
    "memory.recall": OP_VIEW_MEMORY,
    "memory.search": OP_VIEW_MEMORY,
    "memory.query": OP_VIEW_MEMORY,
    "memory.list": OP_VIEW_MEMORY,
    "memory.write": OP_WRITE_MEMORY,
    "memory.forget": OP_WRITE_MEMORY,
    "memory.delete": OP_WRITE_MEMORY,
    "memory.update": OP_WRITE_MEMORY,
    "memory.promote": OP_WRITE_MEMORY,
    # ── 轨迹/面板查看（矩阵：sub_agent ❌）──
    "trace.read": OP_VIEW_TRACE,
    "trace.query": OP_VIEW_TRACE,
    "panel.view": OP_VIEW_PANEL,
    # ── 审批权（§5.7 机制 3 点名：不含审批权；矩阵 sub_agent 不可 approve/reject）──
    "approval.approve": OP_APPROVE,
    "approval.deny": OP_DENY,
    "approval.reject": OP_DENY,
    "approval.submit": OP_SUBMIT_APPROVAL,
    # ── 核心改写（§5.7 机制 3 点名：不含核心改写）──
    # 核心改写 = 改动熔炉/策略/审批矩阵本身，按 §7.0 治理写行判定（sub_agent 全 ❌）
    "core.rewrite": OP_SWITCH_FORGE,
    "core.modify": OP_SWITCH_FORGE,
    "core.switch": OP_SWITCH_FORGE,
    "core.patch": OP_MODIFY_POLICY,
    "prompt.rewrite": OP_MODIFY_POLICY,
    "self_rewrite": OP_MODIFY_POLICY,
    # ── 治理写（矩阵 sub_agent ❌）──
    "governance.switch_forge": OP_SWITCH_FORGE,
    "governance.modify_policy": OP_MODIFY_POLICY,
    "governance.force_stage": OP_FORCE_STAGE,
    "governance.remove_source": OP_REMOVE_SOURCE,
}

#: 受保护类别的**名称前缀**（第二道网，fail-closed）
#: 命中前缀但未登记映射的工具（如 ``memory.dream`` 这类未来新增项）同样拒绝——
#: 「未登记」不等于「允许」。
PROTECTED_TOOL_PREFIXES: Tuple[str, ...] = (
    "memory.", "approval.", "governance.", "core.", "policy.",
    "self_rewrite", "prompt.rewrite", "forge.",
)

#: 工具规格字典里可能藏间接调用的槽位（别名/转发/重定向）
ALIAS_SLOTS: Tuple[str, ...] = (
    "name", "id", "tool", "tool_name", "capability", "capability_id",
    "alias", "aliases", "target", "redirect", "delegate", "invoke", "calls",
)

#: 名称展开的分隔符（模块路径 / 包装前缀 / 命名空间）
_NAME_SEPARATORS: Tuple[str, ...] = ("::", "->", "|", "/", ":", ".")

#: 规范化后的工具名长度上限（防超长串构造）
_MAX_NAME_CHARS = 200

#: 拒绝错误码
E_TOOL_NOT_AUTHORIZED = "E_TOOL_NOT_AUTHORIZED"


# ════════════════════════════════════════════════════════════
#  名称规范化 / 间接路径展开
# ════════════════════════════════════════════════════════════


def normalize_tool_name(name: Any) -> str:
    """工具名规范化：去空白、转小写、统一字面（``sub-agent`` → ``sub_agent``）"""
    text = str(name or "").strip().lower()[:_MAX_NAME_CHARS]
    return text.replace("-", "_").replace(" ", "_")


def name_candidates(raw: Any) -> Tuple[str, ...]:
    """把原始工具名展开为**全部可比对形态**（含间接调用路径）

    规则：按 ``::`` / ``->`` / ``|`` / ``/`` / ``:`` / ``.`` 切分，产出原始名与
    各段后缀的各种拼接形式，保序去重。示例::

        mcp:filesystem::memory.write
          → ("mcp:filesystem::memory.write", "filesystem::memory.write",
             "memory.write", "mcp.filesystem.memory.write", "filesystem.memory.write",
             "write")

    「写」这类**通用词后缀**不会被误判：拒绝规则只以「带类别前缀的完整名」
    （``memory.write``）或受保护前缀为准，不按末段单词匹配。
    """
    text = str(raw or "").strip()[:_MAX_NAME_CHARS]
    if not text:
        return ()
    out: List[str] = []

    def _add(value: str) -> None:
        norm = normalize_tool_name(value)
        if norm and norm not in out:
            out.append(norm)

    _add(text)
    # 分隔符切分 → 逐段后缀（含「点号拼接」形态，覆盖 mcp:fs.memory.write）
    segments = [text]
    for sep in _NAME_SEPARATORS:
        nxt: List[str] = []
        for seg in segments:
            parts = [p for p in seg.split(sep)]
            nxt.extend(parts)
        segments = nxt
    segments = [s for s in segments if s.strip()]
    for idx in range(len(segments)):
        _add(".".join(segments[idx:]))
    # 逐个分隔符的「右截断」形态（覆盖 :: 与 . 混用的包装）
    for sep in _NAME_SEPARATORS:
        if sep in text:
            _add(text.rsplit(sep, 1)[-1])
    return tuple(out)


def tool_spec_names(spec: Any) -> Tuple[str, ...]:
    """从工具规格（字符串 / 字典）中提取**全部**可能的被调用名（含别名槽位）

    字典规格会扫描 ``ALIAS_SLOTS``；值可为字符串或字符串列表。
    """
    names: List[str] = []
    if isinstance(spec, str):
        names.append(spec)
    elif isinstance(spec, Mapping):
        for slot in ALIAS_SLOTS:
            if slot not in spec:
                continue
            value = spec.get(slot)
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, (list, tuple, set, frozenset)):
                names.extend(str(v) for v in value if isinstance(v, (str, int)))
    elif isinstance(spec, (list, tuple, set, frozenset)):
        for item in spec:
            names.extend(tool_spec_names(item))
    else:
        names.append(str(spec))
    return tuple(names)


# ════════════════════════════════════════════════════════════
#  判定结果与异常
# ════════════════════════════════════════════════════════════


class ToolNotAuthorized(Exception):
    """工具不在裁剪子集内（越界调用）

    Attributes:
        tool: 被拒工具名（原始写法）。
        matched: 命中的比对形态（间接路径的实际落点）。
        reason: 可读原因。
        code: 固定 ``E_TOOL_NOT_AUTHORIZED``。
    """

    code = E_TOOL_NOT_AUTHORIZED

    def __init__(self, tool: str, reason: str, *, matched: str = "",
                 decision: Optional[PermissionDecision] = None) -> None:
        self.tool = tool
        self.matched = matched
        self.reason = reason
        self.decision = decision
        super().__init__(
            f"{E_TOOL_NOT_AUTHORIZED}: 子代理不可调用工具 {tool!r}"
            + (f"（命中 {matched!r}）" if matched and matched != tool else "")
            + f" —— {reason}")

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "code": self.code,
            "tool": self.tool,
            "matched": self.matched,
            "reason": self.reason,
        }
        if self.decision is not None:
            data["matrix_operation"] = self.decision.operation
            data["matrix_scope"] = self.decision.scope
        return data


@dataclass(frozen=True)
class ToolDecision:
    """一次工具可用性判定（可直接入审计载荷）"""

    tool: str
    allowed: bool
    matched: str = ""
    reason: str = ""
    matrix_operation: str = ""
    matrix_scope: str = ""
    in_authorized_subset: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "allowed": bool(self.allowed),
            "matched": self.matched,
            "reason": self.reason,
            "matrix_operation": self.matrix_operation,
            "matrix_scope": self.matrix_scope,
            "in_authorized_subset": bool(self.in_authorized_subset),
        }


# ════════════════════════════════════════════════════════════
#  裁剪子集
# ════════════════════════════════════════════════════════════


@dataclass
class ToolTrimReport:
    """裁剪报告（构造时一次性产出，供审计与测试断言）"""

    requested: Tuple[str, ...] = ()
    visible: Tuple[str, ...] = ()
    denied: Tuple[Dict[str, Any], ...] = ()
    authorized_subset: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested": list(self.requested),
            "visible": list(self.visible),
            "denied": [dict(d) for d in self.denied],
            "authorized_subset": list(self.authorized_subset),
            "denied_count": len(self.denied),
        }


@dataclass
class SubAgentToolset:
    """子代理可见工具集（裁剪子集）

    Attributes:
        actor: 子代理执行体标识（默认 ``sub_agent:<id>``，经 ``infer_actor_type``
            推断为 sub_agent）。
        authorized_capabilities: **授权子集**（S4-01 ``PermissionContext`` 的同名
            字段口径）——capability 白名单；不在其中的一律拒绝。
        requested: 调用方**申请**暴露的工具（原始写法）。
        report: 构造时产出的裁剪报告。
    """

    actor: str = "sub_agent:anonymous"
    authorized_capabilities: FrozenSet[str] = frozenset()
    requested: Tuple[str, ...] = ()
    scope: str = ""
    tenant_id: str = ""
    report: ToolTrimReport = field(default_factory=ToolTrimReport)

    # ── 构造 ──

    @classmethod
    def build(
        cls,
        requested: Iterable[str],
        authorized_capabilities: Iterable[str],
        *,
        actor: str = "sub_agent:anonymous",
        scope: str = "",
        tenant_id: str = "",
    ) -> "SubAgentToolset":
        """计算裁剪子集：申请 ∩ 授权子集 − 矩阵拒绝项

        三步都是**交集/减法**，不存在「申请即获得」的路径：
          1. 申请的工具名先规范化（与授权清单同一规范化口径，避免大小写/连字符
             写法差异把同一条 capability 判成两条）；
          2. 逐个经矩阵判定（记忆/审批/核心改写/治理 → ❌）；
          3. 再与授权子集取交（不在清单 → ❌）。
        """
        subset = frozenset(
            normalize_tool_name(c) for c in authorized_capabilities
            if str(c).strip())
        toolset = cls(
            actor=str(actor or "sub_agent:anonymous"),
            authorized_capabilities=subset,
            requested=tuple(str(t) for t in requested),
            scope=str(scope or ""),
            tenant_id=str(tenant_id or ""),
        )
        visible: List[str] = []
        denied: List[Dict[str, Any]] = []
        for raw in toolset.requested:
            decision = toolset.evaluate(raw)
            if decision.allowed:
                normalized = normalize_tool_name(raw)
                if normalized not in visible:
                    visible.append(normalized)
            else:
                denied.append(decision.to_dict())
        toolset.report = ToolTrimReport(
            requested=toolset.requested,
            visible=tuple(visible),
            denied=tuple(denied),
            authorized_subset=tuple(sorted(subset)),
        )
        logger.debug(
            "[Toolset] 裁剪完成 actor=%s 申请=%d 可见=%d 拒绝=%d",
            toolset.actor, len(toolset.requested), len(visible), len(denied))
        return toolset

    # ── 判定 ──

    def _permission_context(self) -> PermissionContext:
        return PermissionContext(
            actor=self.actor,
            actor_type=ACTOR_SUB_AGENT,
            scope=self.scope,
            authorized_capabilities=self.authorized_capabilities,
            extra={"tenant_id": self.tenant_id} if self.tenant_id else {},
        )

    def _matrix_deny(self, name: str) -> Optional[ToolDecision]:
        """受保护类别判定（矩阵 + 前缀双网，fail-closed）"""
        operation = _TOOL_OPERATION_RULES.get(name)
        if operation is None:
            for prefix in PROTECTED_TOOL_PREFIXES:
                if name.startswith(prefix):
                    # 未登记映射的受保护类别 → 同样拒绝（fail-closed）
                    return ToolDecision(
                        tool=name, allowed=False, matched=name,
                        reason=f"受保护类别 {prefix!r}（未登记映射，fail-closed 拒绝）")
            return None
        decision = decide(operation, self._permission_context(),
                          object_type="tool", object_id=name)
        if decision.allowed:
            return None
        return ToolDecision(
            tool=name, allowed=False, matched=name,
            reason=f"§7.0 矩阵拒绝（{decision.operation}）：{decision.reason}",
            matrix_operation=decision.operation,
            matrix_scope=decision.scope,
            in_authorized_subset=name in self.authorized_capabilities,
        )

    def evaluate(self, tool: Any) -> ToolDecision:
        """判定单个工具是否可用（含间接调用路径展开）

        判定顺序：矩阵/受保护类别（硬禁） → 授权子集（白名单）。
        任一步拒绝即拒绝，且**先**报硬禁原因（安全语义优先于清单语义）。
        """
        raw = str(tool or "")
        candidates = name_candidates(raw)
        if not candidates:
            return ToolDecision(tool=raw, allowed=False, reason="工具名为空（fail-closed）")

        for name in candidates:
            hard = self._matrix_deny(name)
            if hard is not None:
                return ToolDecision(
                    tool=raw, allowed=False, matched=name, reason=hard.reason,
                    matrix_operation=hard.matrix_operation,
                    matrix_scope=hard.matrix_scope,
                    in_authorized_subset=hard.in_authorized_subset)

        normalized = normalize_tool_name(raw)
        if normalized in self.authorized_capabilities:
            return ToolDecision(tool=raw, allowed=True, matched=normalized,
                                reason="在授权子集内且未命中 §7.0 拒绝行",
                                matrix_operation=OP_EXECUTE_CAPABILITY,
                                matrix_scope="authorized_subset",
                                in_authorized_subset=True)
        for name in candidates:
            if name in self.authorized_capabilities:
                return ToolDecision(tool=raw, allowed=True, matched=name,
                                    reason="在授权子集内且未命中 §7.0 拒绝行",
                                    matrix_operation=OP_EXECUTE_CAPABILITY,
                                    matrix_scope="authorized_subset",
                                    in_authorized_subset=True)

        # 走一次矩阵以取得权威口径（SCOPE_AUTHORIZED_SUBSET 的拒绝原因）
        decision = decide(OP_EXECUTE_CAPABILITY, self._permission_context(),
                          object_type="capability", object_id=normalized)
        return ToolDecision(
            tool=raw, allowed=False, matched=normalized,
            reason=f"不在授权子集（S4-01 矩阵 SCOPE_AUTHORIZED_SUBSET）：{decision.reason}",
            matrix_operation=decision.operation,
            matrix_scope=decision.scope,
            in_authorized_subset=False)

    def allows(self, tool: Any) -> bool:
        """布尔形式的判定（不抛异常）"""
        return self.evaluate(tool).allowed

    def require(self, tool: Any) -> str:
        """断言工具可用，否则抛 ``ToolNotAuthorized``；可用时返回规范化名"""
        decision = self.evaluate(tool)
        if not decision.allowed:
            raise ToolNotAuthorized(tool, decision.reason,
                                    matched=decision.matched)
        return decision.matched

    def check_spec(self, spec: Any) -> ToolDecision:
        """扫描工具规格（含别名/转发槽位）——**间接调用路径**的统一入口

        规格里任一个槽位命中拒绝规则 → 整体拒绝（与 §5.7 机制 2 的
        「工具调用参数只能由云枢决策层生成」配套：外来文本不得借别名槽位绕过）。
        """
        names = tool_spec_names(spec)
        if not names:
            return ToolDecision(tool=str(spec)[:120], allowed=False,
                                reason="工具规格无可识别的名称槽位（fail-closed）")
        for name in names:
            decision = self.evaluate(name)
            if not decision.allowed:
                return ToolDecision(
                    tool=str(spec)[:200], allowed=False, matched=decision.matched or name,
                    reason=f"规格槽位 {name!r} 被拒：{decision.reason}",
                    matrix_operation=decision.matrix_operation,
                    matrix_scope=decision.matrix_scope,
                    in_authorized_subset=decision.in_authorized_subset)
        return ToolDecision(tool=str(spec)[:200], allowed=True,
                            matched=normalize_tool_name(names[0]),
                            reason="规格全部槽位均可用")

    def require_spec(self, spec: Any) -> str:
        """``check_spec`` 的断言形式"""
        decision = self.check_spec(spec)
        if not decision.allowed:
            raise ToolNotAuthorized(decision.tool, decision.reason,
                                    matched=decision.matched)
        return decision.matched

    # ── 视图 ──

    def visible_tools(self) -> Tuple[str, ...]:
        """可见工具白名单（规范化名，保序）"""
        return self.report.visible

    def as_manifest(self) -> Dict[str, Any]:
        """交给执行器/CLI 的工具清单（**只含白名单**，不放任何被拒项）"""
        return {
            "actor": self.actor,
            "tools": list(self.report.visible),
            "authorized_subset": list(self.report.authorized_subset),
            "denied_count": len(self.report.denied),
        }

    # ── 调用闸门 ──

    def invoke(self, tool: Any, func: Any, *args: Any, **kwargs: Any) -> Any:
        """经裁剪闸门调用工具（越界在**调用点**抛 ``ToolNotAuthorized``）

        刻意**不**接受「工具参数字符串」作为入参来源：参数由调用方（云枢决策层）
        显式传入，外来文本无处拼接（§5.7 机制 2）。
        """
        self.require(tool)
        return func(*args, **kwargs)


__all__ = [
    # 映射与网
    "PROTECTED_TOOL_PREFIXES", "ALIAS_SLOTS", "E_TOOL_NOT_AUTHORIZED",
    # 规范化
    "normalize_tool_name", "name_candidates", "tool_spec_names",
    # 结果
    "ToolDecision", "ToolNotAuthorized", "ToolTrimReport", "SubAgentToolset",
]
