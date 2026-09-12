"""步骤 3 · 派工与补丁生成：八要素委派 + 工具裁剪 + 范围护栏（TASK-S7-02）

【任务定位】
    任务书 §三 步骤 3：走 **S4-04 委派通道**（``agent/subagent/delegation.py`` +
    ``DelegationExecutor``），八要素**缺任一即拒**；子代理**只读源码 + 只能产出
    补丁文本**（不给写权限、不给审批权、不给记忆读写）；产出统一 diff 并立刻过
    范围护栏。

【不易（不自建第二套委派）】
    本模块**不实现**任何委派逻辑：task_file 物化、工具裁剪、并发回压、临时凭据、
    回收三件套、子 Trace 全部来自 S4-04。本模块只做三件 S4-04 不做的事：
      ① 把定位工单翻译成八要素；
      ② 把 S4-04 的 ``ExecutionOutcome`` 翻译成 ``RepairPatch``；
      ③ 在**拿到补丁的第一时间**过范围护栏（不吃护栏的产出不许进验证）。
    ②③ 之所以放在这里而不是 executor 里：S4-04 是通用委派，不该知道"补丁/护栏"
    这类本任务的概念（职责分离）。

【不易（工具裁剪的双重保险）】
    S4-04 的 ``SubAgentToolset`` 已按 §7.0 矩阵拒绝记忆/审批/治理/核心改写类工具。
    本模块再叠加一道**显式白名单**（``REPAIR_SUBAGENT_TOOLS``）并在此断言：
    「申请 ∩ 授权 ∩ 矩阵」的结果必须**只含只读工具**；且我们**不**把写类工具写进
    授权子集——即使子代理申请了 ``write_file``，它也不在授权清单里（交集为空）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from agent.repair.budget import RepairBudget
from agent.repair.guardrails import guard_patch
from agent.repair.models import (
    REASON_DELEGATION_FAILED,
    REASON_DELEGATION_REJECTED,
    REASON_NO_PATCH,
    REASON_READONLY_ZONE,
    REASON_SCOPE_EXCEEDED,
    PatchGuardReport,
    RepairPatch,
    RepairTicket,
)
from agent.repair.policy import (
    REPAIR_FORBIDDEN_TOOLS,
    REPAIR_SUBAGENT_TOOLS,
    RepairPolicy,
)
from agent.repair.trace import RepairRunLogger

logger = logging.getLogger("agent.repair.delegate")

#: 回调地址（§3.9 要素⑧；内部通道，不是网络端点——本流程不做任何网络投递）
DEFAULT_CALLBACK_URL = "internal://repair/patch-result"

#: 目标文本字数上限（§3.9 要求目标具体；过长反而含糊）
GOAL_LIMIT = 1200
#: 变更理由的截断长度
RATIONALE_LIMIT = 2000

#: 产物格式（要素⑤）：机器可读契约 + 人读约定
ARTIFACT_FORMAT = (
    "单个 JSON 对象（**不要 markdown 围栏**），键：\n"
    "  - diff: string —— 统一 diff（unified diff，含 `--- a/<path>` / `+++ b/<path>` "
    "与 `@@` hunk 头；路径必须是仓库相对路径，**必须能被 `git apply` 直接应用**）；\n"
    "  - rationale: string —— 变更理由（说明根因判断与为何这样改）；\n"
    "  - files: string[] —— 涉及文件清单（仓库相对路径）；\n"
    "  - self_eval: object —— {verdict: \"pass\"|\"uncertain\"|\"fail\", score: 0~1, "
    "summary: string, issues: string[]}；\n"
    "  - test_assertion_changed: boolean —— 是否修改了测试文件中的断言\n"
    "若你认为无法在不越界的前提下修复，返回 diff 为空字符串并在 self_eval.issues "
    "说明原因——**不得**为了让补丁「看起来能过」而放宽断言或改动只读区。"
)


class DelegationRejectedError(RuntimeError):
    """八要素不合格（缺任一，**拒绝派工**）"""

    def __init__(self, missing: Sequence[str], detail: Mapping[str, Any]):
        self.missing = tuple(missing)
        self.detail = dict(detail)
        super().__init__(
            f"派工被拒绝：八要素不合格 {len(self.missing)}/8（{', '.join(self.missing)}）")


class DelegationFailedError(RuntimeError):
    """委派执行失败（通道/超时/工具越界等）"""

    def __init__(self, code: str, message: str, detail: Optional[Mapping[str, Any]] = None):
        self.code = str(code)
        self.detail = dict(detail or {})
        super().__init__(message)


@dataclass
class DelegationRequest:
    """派工输入（定位工单 + 策略 + 留痕器）

    Attributes:
        ticket: 定位器工单。
        policy: 策略（预算/超时/护栏阈值）。
        run_logger: 留痕器（写 Trace + 审计）。
        authorized_capabilities: 显式授权子集（缺省 = 只读白名单本身）。
    """

    ticket: RepairTicket
    policy: RepairPolicy = field(default_factory=RepairPolicy)
    run_logger: Optional[RepairRunLogger] = None
    authorized_capabilities: Tuple[str, ...] = REPAIR_SUBAGENT_TOOLS


def _slice_block(ticket: RepairTicket, *, limit: int = 5) -> str:
    """把切片渲染成提示词块（带真实行号，便于子代理按行号定位）"""
    blocks: List[str] = []
    for piece in ticket.slices[:limit]:
        head = (f"--- 文件 {piece.path}（第 {piece.start_line}-{piece.end_line} 行"
                f"{'，已截断' if piece.truncated else ''}）---")
        blocks.append(f"{head}\n{piece.content}")
    return "\n\n".join(blocks) if blocks else "（无可用源码切片）"


def _evidence_block(ticket: RepairTicket, *, limit: int = 5) -> str:
    """证据块：Trace 链 + descriptor + 近期改动（**只列叶子字段**）"""
    lines: List[str] = []
    if ticket.trace_chain:
        lines.append("相关 Trace（最近 %d 条）：" % min(limit, len(ticket.trace_chain)))
        for item in ticket.trace_chain[:limit]:
            lines.append(
                f"  - trace_id={item.get('trace_id', '')[:16]} "
                f"capability={item.get('capability_id', '')} "
                f"actor={item.get('actor', '')} status={item.get('status', '')} "
                f"parent={str(item.get('parent_trace_id') or '')[:16]}")
    if ticket.descriptors:
        lines.append("涉及 capability（descriptor）：")
        for item in ticket.descriptors[:limit]:
            lines.append(f"  - {item.get('capability_id', '')}：{item.get('name', '')}")
    if ticket.recent_changes:
        lines.append("最近改动：")
        for item in ticket.recent_changes[:limit]:
            if "subject" in item:
                lines.append(f"  - {item.get('short', '')} {item.get('subject', '')[:80]}"
                             f"（{item.get('file_count', 0)} 个文件）")
            else:
                lines.append(f"  - {item.get('path', '')}（来源 {item.get('source', '')}）")
    if ticket.evidence_gaps:
        lines.append("证据缺口（**如实告知，不得据此编造**）：")
        for gap in ticket.evidence_gaps[:limit]:
            lines.append(f"  - {gap}")
    lines.append(f"仓库 HEAD：{ticket.repo_head[:12] or '(未知)'}")
    return "\n".join(lines)


def build_goal(ticket: RepairTicket) -> str:
    """要素①目标：具体、可判定、指向**单个**失败项"""
    failure = ticket.failure
    if failure is None or not failure.node_id:
        return ""
    loc = f"{failure.file}:{failure.line}" if failure.line else failure.file
    goal = (
        f"修复失败用例 `{failure.node_id}`（位置 {loc}；失败类型 {failure.kind}）。\n"
        f"失败摘要：{failure.message or '(无摘要)'}\n"
        f"堆栈指纹：{failure.stack_fingerprint}\n"
        f"要求：产出**最小**修复补丁，使该用例由 fail 变为 pass，"
        f"且不改变任何既有公开接口行为。"
    )
    return goal[:GOAL_LIMIT]


def build_constraints(policy: RepairPolicy, ticket: RepairTicket) -> List[str]:
    """要素②约束（非空；无约束的委派等同于没写边界）"""
    constraints = [
        f"单次改动文件数 ≤ {policy.max_changed_files}",
        f"单文件改动行数（增+删）≤ {policy.max_lines_per_file}",
        f"最多产出 1 个统一 diff；token 预算 {policy.budget_tokens}；"
        f"超时 {policy.timeout_seconds:.0f}s",
        "路径一律用**仓库相对 POSIX 路径**（如 agent/foo/bar.py），"
        "不得使用绝对路径或反斜杠",
        "diff 必须能被 `git apply` 直接应用（含 `--- a/` `+++ b/` 与 `@@` hunk 头，"
        "上下文行必须与切片一致）",
        "优先修改**非测试**的源码文件；把测试断言改松以过关属违规",
    ]
    if ticket.adjacent_tests:
        constraints.append(
            f"邻接回归测试将包含：{', '.join(ticket.adjacent_tests[:4])}——"
            f"你的补丁不得使其失败")
    return constraints


def build_prior_artifacts(ticket: RepairTicket) -> List[str]:
    """要素③已有成果：失败项 + 切片 + 证据（可为空列表 = 声明「无」）"""
    artifacts: List[str] = []
    if ticket.failure is not None and ticket.failure.node_id:
        artifacts.append(f"失败用例：{ticket.failure.node_id}")
    for piece in ticket.slices:
        artifacts.append(f"源码切片：{piece.path}:{piece.start_line}-{piece.end_line}")
    for item in ticket.trace_chain[:3]:
        artifacts.append(f"Trace：{item.get('trace_id', '')}")
    for rel in ticket.impl_files:
        artifacts.append(f"相关实现文件：{rel}")
    return artifacts


def build_prohibitions(ticket: RepairTicket) -> List[str]:
    """要素④禁止事项（可为空列表 = 声明「无」；这里**非空**）"""
    prohibited = [
        "禁止改动只读区：core/auth/、core/audit/、schema/、agent/audit/chain.py、"
        "agent/security/、eval/l0_anchor/（L0 锚是系统不可写的标尺）",
        "禁止改动审计与签名相关代码（含 daily_roots/signing/merkle 类文件）",
        "禁止 git push / git merge / 创建远端 PR / 改写历史",
        "禁止执行任何网络访问或安装依赖",
        "禁止把测试断言改宽、跳过测试或删除测试用例来「过关」",
        "禁止写入记忆、提交审批、修改策略/审批矩阵（子代理无这些权限）",
        "禁止在补丁中夹带与目标失败项无关的重构、格式化或重命名",
    ]
    if ticket.evidence_gaps:
        prohibited.append(
            "证据有缺口时**不得编造** Trace/日志/覆盖率为依据；"
            "无法确定根因时如实返回空 diff 并说明")
    return prohibited


def build_input_text(ticket: RepairTicket) -> str:
    """要素 payload：给子代理的完整上下文（切片 + 证据；**不含整仓**）"""
    return "\n\n".join([
        "# 失败项",
        (f"用例：{ticket.failure.node_id}\n位置：{ticket.failure.file}:"
         f"{ticket.failure.line}\n摘要：{ticket.failure.message}\n"
         f"正文（截断）：{ticket.failure.text}") if ticket.failure else "(无失败项)",
        "# 源码切片（行号即真实文件行号）",
        _slice_block(ticket),
        "# 证据",
        _evidence_block(ticket),
    ])


def build_delegation_context(ticket: RepairTicket, policy: RepairPolicy,
                             *, tenant_id: str = "default", subject_id: str = "",
                             trace_id: str = "", workspace_id: str = "",
                             ) -> Any:
    """组装 §3.9 八要素 ``DelegationContext``（缺任一 → 由 S4-04 闸门拒绝）

    本函数**不提供任何默认值补齐**：八要素逐项显式取值，取不到就是 ``None``——
    这正是 S4-04「不得用默认值蒙混」的要求在调用侧的对偶。
    """
    from agent.subagent.delegation import DelegationContext

    return DelegationContext(
        goal=build_goal(ticket),
        constraints=build_constraints(policy, ticket),
        prior_artifacts=build_prior_artifacts(ticket),
        prohibitions=build_prohibitions(ticket),
        artifact_format=ARTIFACT_FORMAT,
        budget_tokens=int(policy.budget_tokens),
        timeout_seconds=float(policy.timeout_seconds),
        callback_url=DEFAULT_CALLBACK_URL,
        tenant_id=str(tenant_id or "default"),
        subject_id=str(subject_id or ""),
        task_id=f"repair:{ticket.ticket_id}",
        trace_id=str(trace_id or ""),
        policy_version="repair.l1.v1",
        metadata={
            "authorized_capabilities": list(REPAIR_SUBAGENT_TOOLS),
            "workspace_id": str(workspace_id or ""),
            "ticket_id": ticket.ticket_id,
        },
    )


def assert_readonly_toolset(toolset: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    """断言裁剪后的工具集**只含只读工具**（无写权限/无审批权/无记忆读写）

    判定分两层，两层都要过：
      ① **白名单**：裁剪集里的每个工具都必须在 ``REPAIR_SUBAGENT_TOOLS`` 内；
      ② **明令禁止清单**：``REPAIR_FORBIDDEN_TOOLS`` 内的工具一旦出现即违规。
    两层会重叠（如 ``memory.write`` 既不在白名单也在禁止清单），故对同一工具
    只记一条问题——报告要能一眼数清"越界了几个"。

    Returns:
        ``(ok, problems)``；problems 为具体的越界工具问题清单。
    """
    visible = [str(t) for t in (toolset.get("tools") or toolset.get("visible") or ())]
    problems: List[str] = []
    allowed = {str(t) for t in REPAIR_SUBAGENT_TOOLS}
    for name in visible:
        if name in allowed:
            continue
        if name in REPAIR_FORBIDDEN_TOOLS:
            problems.append(f"裁剪集含明令禁止工具：{name}")
        else:
            problems.append(f"裁剪集含非只读工具：{name}")
    return (not problems), problems


def parse_patch_payload(outcome: Any) -> RepairPatch:
    """把 S4-04 的 ``ExecutionOutcome`` 翻译成 ``RepairPatch``

    优先取 ``payload["diff"]``；退化到 ``payload["patch_text"]``（有些执行体会用
    这个键名）。**不猜**：两者都没有 → diff 为空（调用方判「无产出」）。
    """
    payload: Dict[str, Any] = dict(getattr(outcome, "payload", {}) or {})
    diff = payload.get("diff")
    if not isinstance(diff, str) or not diff.strip():
        alt = payload.get("patch_text")
        diff = alt if isinstance(alt, str) else ""
    rationale = payload.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        summary = payload.get("summary")
        rationale = summary if isinstance(summary, str) else ""
    files_raw = payload.get("files")
    files: List[str] = []
    if isinstance(files_raw, list):
        files = [str(f).replace("\\", "/") for f in files_raw if str(f or "").strip()]
    if not files and isinstance(payload.get("artifacts"), list):
        for item in payload["artifacts"]:
            if isinstance(item, dict) and item.get("path"):
                files.append(str(item["path"]).replace("\\", "/"))
    self_eval = payload.get("self_eval")
    return RepairPatch(
        diff=str(diff or ""),
        rationale=str(rationale or "")[:RATIONALE_LIMIT],
        files=files,
        self_eval=dict(self_eval) if isinstance(self_eval, Mapping) else {},
        delegation_id=str(getattr(outcome, "delegation_id", "") or ""),
        trace_id=str(getattr(outcome, "trace_id", "") or ""),
        toolset=dict(getattr(outcome, "toolset", {}) or {}))


@dataclass
class DelegationOutcome:
    """派工结果（补丁 + 预算 + 证据）

    Attributes:
        patch: 补丁（可能为空 = 无产出）。
        reason: 拒绝/失败原因码（空 = 未失败）。
        detail: 失败明细。
        ok: 是否拿到**通过护栏**的补丁。
        guard: 护栏判定（补丁为空时仍给出，便于报告）。
        delegation_id / trace_id: 证据链锚点。
        tokens_used: 本次委派记账的 token。
    """

    patch: RepairPatch = field(default_factory=RepairPatch)
    reason: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    guard: Optional[PatchGuardReport] = None
    delegation_id: str = ""
    trace_id: str = ""
    tokens_used: int = 0


def _tokens_of(outcome: Any) -> int:
    """从 ``ExecutionOutcome`` 提取 token 消耗（取不到 → 0，不估算）"""
    cost = getattr(outcome, "cost", None)
    for attr in ("counted_tokens", "total_tokens"):
        value = getattr(cost, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, int(value))
    payload = getattr(outcome, "payload", {}) or {}
    if isinstance(payload, Mapping):
        total = 0
        for key in ("input_tokens", "output_tokens"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                total += max(0, int(value))
        if total:
            return total
    return 0


def delegate(request: DelegationRequest, *, executor: Any, budget: RepairBudget,
             tenant_id: str = "default", subject_id: str = "") -> DelegationOutcome:
    """派工：八要素准入 → 委派 → 取补丁 → 过护栏（步骤 3）

    Args:
        request: 派工输入。
        executor: ``DelegationExecutor``（可注入桩；**必须**提供）。
        budget: 预算账本（超额抛 ``BudgetExceeded``）。
        tenant_id / subject_id: 租户与主体（§2.7 隔离口径）。

    Returns:
        ``DelegationOutcome``；``ok=False`` 时 ``reason`` 为原因码。
    """
    ticket = request.ticket
    policy = request.policy
    logger_ = request.run_logger

    def _record(outcome: DelegationOutcome, status: str, detail: Dict[str, Any],
                error: str = "") -> DelegationOutcome:
        if logger_ is not None:
            logger_.record_step("delegate", subject=ticket.ticket_id, status=status,
                                trace_id=outcome.trace_id, detail=detail, error=error)
        return outcome

    # ① 八要素组装 + 自校验（S4-04 的准入闸门；缺任一即拒）
    from agent.subagent.delegation import DelegationRejected

    try:
        ctx = build_delegation_context(
            ticket, policy, tenant_id=tenant_id, subject_id=subject_id,
            trace_id=logger_.run_id if logger_ is not None else "",
            workspace_id=logger_.workspace_id if logger_ is not None else "")
        ctx.require_valid()
    except DelegationRejected as exc:  # 八要素不合格
        detail = exc.to_dict()
        outcome = DelegationOutcome(
            reason=REASON_DELEGATION_REJECTED, detail=detail, ok=False)
        return _record(outcome, "error", detail,
                       error=f"八要素不合格：{detail.get('missing')}")

    # ② 预算预检（余额不足即停，不"派出去再说"）
    from agent.repair.budget import BudgetExceeded

    try:
        budget.check_headroom(needed=1)
    except BudgetExceeded as exc:
        detail = {"code": exc.code, **exc.detail}
        budget.notes.append(f"{exc.code}：{exc}")
        return _record(DelegationOutcome(reason=exc.code, detail=detail), "error", detail,
                       error=str(exc))

    # ③ 委派（工具裁剪由 S4-04 完成；本流程只交出只读白名单作为授权子集）
    try:
        result = executor.execute(
            ctx, tools=REPAIR_SUBAGENT_TOOLS,
            authorized_capabilities=request.authorized_capabilities)
    except Exception as exc:  # noqa: BLE001 执行器异常不得穿透（要留痕）
        detail = {"exception": f"{type(exc).__name__}: {exc}"}
        return _record(DelegationOutcome(reason=REASON_DELEGATION_FAILED, detail=detail),
                       "error", detail, error=detail["exception"])

    toolset = dict(getattr(result, "toolset", {}) or {})
    patch = parse_patch_payload(result)
    patch.toolset = toolset
    tokens = _tokens_of(result)
    try:
        budget.consume(tokens, source="subagent")
    except BudgetExceeded as exc:
        detail = {"code": exc.code, **exc.detail}
        budget.notes.append(f"{exc.code}：{exc}")
        return _record(DelegationOutcome(patch=patch, reason=exc.code, detail=detail,
                                         delegation_id=patch.delegation_id,
                                         trace_id=patch.trace_id, tokens_used=tokens),
                       "error", detail, error=str(exc))

    # ④ 子代理无写权限/无审批权：对裁剪结果**逐名断言**
    tools_ok, tool_problems = assert_readonly_toolset(toolset)
    if not tools_ok:
        detail = {"code": "E_REPAIR_TOOLSET_UNSAFE", "problems": tool_problems,
                  "toolset": toolset}
        return _record(DelegationOutcome(patch=patch, reason=REASON_DELEGATION_FAILED,
                                         detail=detail,
                                         delegation_id=patch.delegation_id,
                                         trace_id=patch.trace_id, tokens_used=tokens),
                       "error", detail, error="；".join(tool_problems))

    # ⑤ S4-04 判定失败 → 如实上报（不拿半个补丁去验证）
    if not bool(getattr(result, "ok", False)):
        detail = {
            "code": str(getattr(result, "error_code", "") or REASON_DELEGATION_FAILED),
            "error": str(getattr(result, "error", "") or ""),
            "sub_reason": str(getattr(result, "sub_reason", "") or ""),
            "tool_violations": [dict(v) for v in
                                (getattr(result, "tool_violations", ()) or ())][:5],
        }
        return _record(DelegationOutcome(patch=patch, reason=REASON_DELEGATION_FAILED,
                                         detail=detail,
                                         delegation_id=patch.delegation_id,
                                         trace_id=patch.trace_id, tokens_used=tokens),
                       "error", detail, error=detail["error"] or detail["code"])

    # ⑥ 范围护栏（硬闸）：命中只读区 / 超文件数 / 超行数 → 丢弃
    guard = guard_patch(patch.diff, policy=policy)
    patch.guard = guard
    base_detail: Dict[str, Any] = {
        "delegation_id": patch.delegation_id,
        "tier": str(getattr(result, "tier", "") or ""),
        "tokens_used": tokens,
        "changed_files": guard.changed_files,
        "max_file_changed_lines": guard.max_file_changed_lines,
        "readonly_hits": list(guard.readonly_hits),
        "test_assertions_touched": list(guard.test_assertions_touched),
        "toolset": toolset,
        "diff_chars": len(patch.diff),
    }
    if not patch.non_empty:
        patch.rejected_reason = REASON_NO_PATCH
        detail = {**base_detail, "violations": list(guard.violations),
                  "parse_error": guard.parse_error}
        return _record(DelegationOutcome(patch=patch, reason=REASON_NO_PATCH, detail=detail,
                                         guard=guard, delegation_id=patch.delegation_id,
                                         trace_id=patch.trace_id, tokens_used=tokens),
                       "error", detail, error="子代理未产出补丁（diff 为空）")
    if not guard.ok:
        if guard.readonly_hits:
            reason = REASON_READONLY_ZONE
        else:
            reason = REASON_SCOPE_EXCEEDED
        patch.rejected_reason = reason
        detail = {**base_detail, "violations": list(guard.violations)}
        return _record(DelegationOutcome(patch=patch, reason=reason, detail=detail,
                                         guard=guard, delegation_id=patch.delegation_id,
                                         trace_id=patch.trace_id, tokens_used=tokens),
                       "error", detail, error="；".join(guard.violations[:5]))

    outcome = DelegationOutcome(
        patch=patch, ok=True, guard=guard, delegation_id=patch.delegation_id,
        trace_id=patch.trace_id, tokens_used=tokens)
    return _record(outcome, "ok", base_detail)


def patch_envelope(patch: RepairPatch) -> str:
    """补丁的 JSON 信封（产物落盘用；含 diff 与元数据）"""
    return json.dumps({
        "schema": "repair.patch.v1",
        "diff": patch.diff,
        "rationale": patch.rationale,
        "files": list(patch.files),
        "self_eval": dict(patch.self_eval),
        "delegation_id": patch.delegation_id,
        "trace_id": patch.trace_id,
        "toolset": dict(patch.toolset),
        "guard": patch.guard.to_dict() if patch.guard else None,
    }, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "DEFAULT_CALLBACK_URL", "ARTIFACT_FORMAT", "GOAL_LIMIT",
    "DelegationRejectedError", "DelegationFailedError", "DelegationRequest",
    "DelegationOutcome", "build_goal", "build_constraints", "build_prior_artifacts",
    "build_prohibitions", "build_input_text", "build_delegation_context",
    "assert_readonly_toolset", "parse_patch_payload", "delegate", "patch_envelope",
]
