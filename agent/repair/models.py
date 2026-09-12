"""自修复 L1 数据模型（TASK-S7-02）

【任务定位】
    把「体检 → 定位 → 派工 → 验证 → 产出」五步之间传递的东西全部显式建模。为什么
    不直接传 dict：任务书 §一 边界 ⑤ 要求**五步全程可审计**，审计载荷必须是**叶子
    字段**（不得把活对象塞进 Trace/审计）；显式数据模型是「哪些字段可以出链」的
    唯一清单——dict 会让「不小心把 Trace 对象序列化进去」变成随时可能发生的事。

【不易】
    - **不编造问题**：``DiagnosisReport.failure_count == 0`` 是**合法结果**，
      模型层面用 ``ok`` 显式承载「无失败」语义，而不是靠空列表让人猜。
    - **不过即丢弃**：``VerificationReport`` 三个闸门（目标用例 / L0 锚 / 邻接回归）
      各自独立成 dataclass，``passed`` 只在三者全过时为真；``RepairReport`` 以
      ``proposal is None`` 表达「丢弃」，不产出半成品。
    - **无 push/无合并**：模型里**不存在** remote/push/merge 字段。产物形态只有
      「本地分支名 + 补丁文本 + PR 描述文本」，从类型层面就不给人误留钩子。

【变易】
    新增一步只需新增一个 dataclass 并挂到 ``RepairReport``；``to_dict()`` 一律
    平铺叶子字段，便于直接进审计载荷与 PR 描述渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: 报告 schema 版本（格式变更必须升版）
REPAIR_SCHEMA = "repair.v1"

# 五步名（审计动作名与 Trace capability 名的唯一来源）
STEP_DIAGNOSE = "diagnose"
STEP_LOCATE = "locate"
STEP_DELEGATE = "delegate"
STEP_VERIFY = "verify"
STEP_PROPOSE = "propose"
#: 五步顺序（审计完整性断言用）
REPAIR_STEPS: Tuple[str, ...] = (
    STEP_DIAGNOSE, STEP_LOCATE, STEP_DELEGATE, STEP_VERIFY, STEP_PROPOSE,
)
#: 每步对应的审计动作名（`agent.audit.facade` 的 action 口径）
AUDIT_ACTIONS: Dict[str, str] = {
    STEP_DIAGNOSE: "repair.diagnose",
    STEP_LOCATE: "repair.locate",
    STEP_DELEGATE: "repair.delegate",
    STEP_VERIFY: "repair.verify",
    STEP_PROPOSE: "repair.propose",
}

#: 补丁被丢弃的原因码（可入审计/报告，便于机械统计）
REASON_VERIFY_FAILED = "E_REPAIR_NOT_VERIFIED"
REASON_READONLY_ZONE = "E_REPAIR_READONLY_ZONE"
REASON_SCOPE_EXCEEDED = "E_REPAIR_SCOPE_EXCEEDED"
REASON_DELEGATION_REJECTED = "E_REPAIR_DELEGATION_REJECTED"
REASON_DELEGATION_FAILED = "E_REPAIR_DELEGATION_FAILED"
REASON_BUDGET_EXCEEDED = "E_REPAIR_BUDGET_EXCEEDED"
REASON_ROUNDS_EXCEEDED = "E_REPAIR_ROUNDS_EXCEEDED"
REASON_NO_PATCH = "E_REPAIR_NO_PATCH"
REASON_NO_FAILURE = "E_REPAIR_NO_FAILURE"


@dataclass
class FailureItem:
    """一条失败项（来自 ``pytest --junitxml``）

    Attributes:
        node_id: 用例节点 id（``path::Class::test``）。
        file: 测试文件（仓库相对路径）。
        line: 失败行号（0 = 未知）。
        message: 断言摘要（**已清洗**：去 ANSI、截断、不含整段堆栈）。
        text: 失败正文（截断；供人读，不进审计载荷）。
        stack_fingerprint: 堆栈指纹（归一化后 sha256 前 16 位）——
            「同一根因的多个失败」靠它归并，比逐条看堆栈机械得多。
        kind: ``failure`` / ``error`` / ``skipped``。
        source_mtime: 测试文件最近修改时间（判断"最近改动"的旁证）。
    """

    node_id: str = ""
    file: str = ""
    line: int = 0
    message: str = ""
    text: str = ""
    stack_fingerprint: str = ""
    kind: str = "failure"
    source_mtime: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """审计友好视图（**不含** text 正文，避免长文本进链）"""
        return {
            "node_id": self.node_id,
            "file": self.file,
            "line": int(self.line),
            "message": self.message,
            "stack_fingerprint": self.stack_fingerprint,
            "kind": self.kind,
            "source_mtime": float(self.source_mtime),
        }


@dataclass
class SourceSlice:
    """失败点 ±N 行的最小源码上下文（避免整仓进提示词）

    Attributes:
        path: 仓库相对路径。
        start_line / end_line: 切片行号区间（1 基，含两端）。
        content: 带行号的源码文本。
        truncated: 是否因超长被截断（如实披露）。
    """

    path: str = ""
    start_line: int = 1
    end_line: int = 1
    content: str = ""
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "start_line": int(self.start_line),
            "end_line": int(self.end_line),
            "chars": len(self.content),
            "truncated": bool(self.truncated),
        }


@dataclass
class AuditSnapshot:
    """审计/告警快照（可选第三件套，复用 S2-02 ``verify_chain``）

    Attributes:
        enabled: 是否真的取了快照（False 表示关闭/不可用，**不是**"没问题"）。
        chain_ok: 链式审计是否校验通过（None = 未校验）。
        entries: 链上条数。
        head_hash: 链头 self_hash 前 16 位。
        issues: 校验问题清单（叶子字符串）。
        error: 取快照失败原因（如库不存在）。
    """

    enabled: bool = False
    chain_ok: Optional[bool] = None
    entries: int = 0
    head_hash: str = ""
    issues: Tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "chain_ok": self.chain_ok,
            "entries": int(self.entries),
            "head_hash": self.head_hash,
            "issues": list(self.issues),
            "error": self.error,
        }


@dataclass
class DiagnosisReport:
    """体检报告（步骤 1 产物）

    Attributes:
        run_id: 本次修复运行标识。
        repo_root: 体检所在仓库根。
        ok: 是否**未发现失败**（True = 全部通过；「无失败」是合法结果）。
        failures: 失败项清单（ok=True 时为空）。
        failures_by_fingerprint: 指纹 → 失败条数（归并视图）。
        tests_passed / tests_failed / tests_error / tests_skipped: 用例计数。
        test_command: 实际执行的 pytest 命令（复现用）。
        junit_path: junitxml 落盘路径。
        anchor_ok: L0 锚是否全过（None = 未跑）。
        anchor_detail: 锚运行摘要（叶子字段）。
        audit: 审计/告警快照。
        recent_changes: 近期改动文件（只读 git 局部历史）。
        disclosures: 如实披露项（未跑锚 / 无凭证 / 降级等）。
        duration_ms: 体检耗时。
    """

    run_id: str = ""
    repo_root: str = ""
    ok: bool = False
    failures: List[FailureItem] = field(default_factory=list)
    failures_by_fingerprint: Dict[str, int] = field(default_factory=dict)
    tests_passed: int = 0
    tests_failed: int = 0
    tests_error: int = 0
    tests_skipped: int = 0
    test_command: str = ""
    junit_path: str = ""
    anchor_ok: Optional[bool] = None
    anchor_detail: Dict[str, Any] = field(default_factory=dict)
    audit: AuditSnapshot = field(default_factory=AuditSnapshot)
    recent_changes: List[Dict[str, Any]] = field(default_factory=list)
    disclosures: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    schema: str = REPAIR_SCHEMA

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "repo_root": self.repo_root,
            "ok": bool(self.ok),
            "failure_count": self.failure_count,
            "failures": [f.to_dict() for f in self.failures],
            "failures_by_fingerprint": dict(self.failures_by_fingerprint),
            "tests_passed": int(self.tests_passed),
            "tests_failed": int(self.tests_failed),
            "tests_error": int(self.tests_error),
            "tests_skipped": int(self.tests_skipped),
            "test_command": self.test_command,
            "junit_path": self.junit_path,
            "anchor_ok": self.anchor_ok,
            "anchor_detail": dict(self.anchor_detail),
            "audit": self.audit.to_dict(),
            "recent_changes": [dict(c) for c in self.recent_changes],
            "disclosures": list(self.disclosures),
            "duration_ms": round(float(self.duration_ms), 2),
        }

    def summary_markdown(self) -> str:
        """人读摘要（写进体检报告文件）"""
        lines = [
            f"# 体检报告 {self.run_id}",
            "",
            f"- 仓库根：`{self.repo_root}`",
            f"- 结论：**{'未发现失败' if self.ok else f'发现 {self.failure_count} 条失败'}**",
            f"- 用例计数：pass {self.tests_passed} / fail {self.tests_failed} / "
            f"error {self.tests_error} / skipped {self.tests_skipped}",
            f"- 命令：`{self.test_command}`",
            f"- L0 锚：{'未运行' if self.anchor_ok is None else ('全过' if self.anchor_ok else '存在失败')}",
        ]
        if self.failures:
            lines += ["", "## 失败项", "",
                      "| 用例 | 位置 | 断言摘要 | 指纹 |", "|---|---|---|---|"]
            for f in self.failures:
                loc = f"{f.file}:{f.line}" if f.line else f.file
                lines.append(f"| `{f.node_id}` | `{loc}` | {f.message} | `{f.stack_fingerprint}` |")
        if self.disclosures:
            lines += ["", "## 披露", ""] + [f"- {d}" for d in self.disclosures]
        return "\n".join(lines) + "\n"


@dataclass
class PatchFileStat:
    """单文件改动统计

    Attributes:
        path: 仓库相对路径（POSIX）。
        added / removed: 新增/删除行数。
        is_new / is_deleted: 新增/删除文件。
        is_test: 是否测试文件（改测试断言须在 PR 描述显式标注）。
        readonly_hit: 是否命中只读区。
    """

    path: str = ""
    added: int = 0
    removed: int = 0
    is_new: bool = False
    is_deleted: bool = False
    is_test: bool = False
    readonly_hit: bool = False

    @property
    def changed_lines(self) -> int:
        return int(self.added) + int(self.removed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "added": int(self.added),
            "removed": int(self.removed),
            "changed_lines": self.changed_lines,
            "is_new": bool(self.is_new),
            "is_deleted": bool(self.is_deleted),
            "is_test": bool(self.is_test),
            "readonly_hit": bool(self.readonly_hit),
        }


@dataclass
class PatchGuardReport:
    """范围护栏判定结果（步骤 3 硬闸）

    Attributes:
        ok: 是否放行（False = 本次产出**丢弃**）。
        files: 逐文件统计。
        changed_files: 改动文件数。
        max_file_changed_lines: 单文件改动行数最大值。
        readonly_hits: 命中只读区的路径 → 原因。
        test_assertions_touched: 被改动的测试文件清单（须显式标注）。
        violations: 违规原因清单（人读）。
        policy: 生效策略快照。
        parse_error: 补丁无法解析时的原因（非空即 ok=False）。
    """

    ok: bool = False
    files: List[PatchFileStat] = field(default_factory=list)
    changed_files: int = 0
    max_file_changed_lines: int = 0
    readonly_hits: Dict[str, str] = field(default_factory=dict)
    test_assertions_touched: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)
    policy: Dict[str, Any] = field(default_factory=dict)
    parse_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "files": [f.to_dict() for f in self.files],
            "changed_files": int(self.changed_files),
            "max_file_changed_lines": int(self.max_file_changed_lines),
            "readonly_hits": dict(self.readonly_hits),
            "test_assertions_touched": list(self.test_assertions_touched),
            "violations": list(self.violations),
            "policy": dict(self.policy),
            "parse_error": self.parse_error,
        }


@dataclass
class RepairPatch:
    """子代理产出的补丁（步骤 3 产物）

    Attributes:
        diff: 统一 diff 文本。
        rationale: 变更理由。
        files: 子代理自报的涉及文件清单。
        self_eval: 子代理自评（叶子字段）。
        delegation_id / trace_id: 委派与子 Trace 标识（证据链锚点）。
        toolset: 工具裁剪清单（无写权限/无审批权的自证）。
        guard: 范围护栏判定。
        rejected_reason: 被丢弃时的原因码（空 = 未被丢弃）。
    """

    diff: str = ""
    rationale: str = ""
    files: List[str] = field(default_factory=list)
    self_eval: Dict[str, Any] = field(default_factory=dict)
    delegation_id: str = ""
    trace_id: str = ""
    toolset: Dict[str, Any] = field(default_factory=dict)
    guard: Optional[PatchGuardReport] = None
    rejected_reason: str = ""

    @property
    def non_empty(self) -> bool:
        return bool(self.diff.strip())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_diff": self.non_empty,
            "diff_chars": len(self.diff),
            "rationale": self.rationale,
            "files": list(self.files),
            "self_eval": dict(self.self_eval),
            "delegation_id": self.delegation_id,
            "trace_id": self.trace_id,
            "toolset": dict(self.toolset),
            "guard": self.guard.to_dict() if self.guard else None,
            "rejected_reason": self.rejected_reason,
        }


@dataclass
class CheckResult:
    """单个验证闸门结果

    Attributes:
        name: 闸门名（``target`` / ``anchor`` / ``adjacent``）。
        passed: 是否通过（None = 未运行 → 视为不过，fail-closed）。
        command: 实际命令（复现用）。
        exit_code: 退出码（-1 = 未运行）。
        output_summary: 输出摘要（尾部若干行，已截断）。
        passed_count / failed_count: 用例计数。
        duration_ms: 耗时。
        skipped: 是否被显式跳过（跳过 = 不通过，如实记原因）。
        skip_reason: 跳过原因。
    """

    name: str = ""
    passed: Optional[bool] = None
    command: str = ""
    exit_code: int = -1
    output_summary: str = ""
    passed_count: int = 0
    failed_count: int = 0
    duration_ms: float = 0.0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def effective_pass(self) -> bool:
        """fail-closed：未运行/跳过一律不算通过"""
        return bool(self.passed) and not self.skipped

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "effective_pass": self.effective_pass,
            "command": self.command,
            "exit_code": int(self.exit_code),
            "output_summary": self.output_summary,
            "passed_count": int(self.passed_count),
            "failed_count": int(self.failed_count),
            "duration_ms": round(float(self.duration_ms), 2),
            "skipped": bool(self.skipped),
            "skip_reason": self.skip_reason,
        }


@dataclass
class VerificationReport:
    """隔离验证三关结果（步骤 4 产物）

    Attributes:
        ok: 三关全过（唯一放行条件）。
        target: 目标用例（必须 fail → pass）。
        anchor: L0 锚（必须全过）。
        adjacent: 邻接回归。
        target_failed_before: 打补丁前目标用例确实失败（防止"本来就没坏"）。
        temp_root: 临时副本根（证据；验证结束即清理）。
        apply_error: 补丁应用失败原因。
        diff_sha256: 被验证补丁的哈希（PR 描述引用，防止事后替换）。
    """

    ok: bool = False
    target: CheckResult = field(default_factory=lambda: CheckResult(name="target"))
    anchor: CheckResult = field(default_factory=lambda: CheckResult(name="anchor"))
    adjacent: CheckResult = field(default_factory=lambda: CheckResult(name="adjacent"))
    target_failed_before: bool = False
    temp_root: str = ""
    apply_error: str = ""
    diff_sha256: str = ""

    @property
    def gate_results(self) -> Tuple[CheckResult, CheckResult, CheckResult]:
        return self.target, self.anchor, self.adjacent

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "target": self.target.to_dict(),
            "anchor": self.anchor.to_dict(),
            "adjacent": self.adjacent.to_dict(),
            "target_failed_before": bool(self.target_failed_before),
            "temp_root": self.temp_root,
            "apply_error": self.apply_error,
            "diff_sha256": self.diff_sha256,
        }


@dataclass
class RepairProposal:
    """本地补丁 PR 产物（步骤 5；**全部本地**）

    Attributes:
        branch: 本地分支名 ``repair/<date>-<slug>``。
        patch_path: 补丁文件落盘路径。
        description_path: PR 描述落盘路径。
        diagnose_path: 体检报告路径（复现入口）。
        pr_description: PR 描述正文。
        review_focus: 人工复核重点条目。
        undo_hint: 撤销提示（回滚口径）。
        created_branch: 是否真的创建了本地分支（False = 仅出补丁文件）。
        pushed: **恒为 False**（本任务不做 push；字段存在即自证）。
        merged: **恒为 False**（本任务不做合并）。
    """

    branch: str = ""
    patch_path: str = ""
    description_path: str = ""
    diagnose_path: str = ""
    pr_description: str = ""
    review_focus: List[str] = field(default_factory=list)
    undo_hint: str = ""
    created_branch: bool = False
    pushed: bool = False
    merged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch,
            "patch_path": self.patch_path,
            "description_path": self.description_path,
            "diagnose_path": self.diagnose_path,
            "pr_description_chars": len(self.pr_description),
            "review_focus": list(self.review_focus),
            "undo_hint": self.undo_hint,
            "created_branch": bool(self.created_branch),
            "pushed": bool(self.pushed),
            "merged": bool(self.merged),
        }


@dataclass
class AuditTrailEntry:
    """一条审计留痕（五步各一条；供报告与 ``verify_chain`` 对照）"""

    step: str = ""
    action: str = ""
    actor: str = ""
    subject: str = ""
    status: str = ""
    trace_id: str = ""
    seq: Optional[int] = None
    duration_ms: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "action": self.action,
            "actor": self.actor,
            "subject": self.subject,
            "status": self.status,
            "trace_id": self.trace_id,
            "seq": self.seq,
            "duration_ms": round(float(self.duration_ms), 2),
            "detail": dict(self.detail),
        }


@dataclass
class RepairReport:
    """一次修复运行的完整报告（入口脚本的最终产物）

    Attributes:
        run_id: 运行标识。
        status: ``no_failure`` / ``proposed`` / ``discarded`` / ``rejected`` / ``error``。
        reason: 未产出时的原因码。
        reasons: 全部原因码（可多条）。
        diagnosis: 步骤 1 产物。
        ticket_id: 步骤 2 产出的工单标识。
        patch: 步骤 3 产物（被拒时仍保留，便于人看子代理到底想改什么）。
        verification: 步骤 4 产物。
        proposal: 步骤 5 产物（**None = 未产出**，即"未过不产出"）。
        audit_trail: 五步审计留痕。
        chain_ok: 审计链 ``verify_chain`` 结论（None = 未校验）。
        rounds_used / tokens_used: 预算与轮次实耗。
        budget_notes: 超限/回退说明。
        started_at / finished_at: 起止时间（ISO-8601）。
        duration_ms: 总耗时。
    """

    run_id: str = ""
    status: str = ""
    reason: str = ""
    reasons: List[str] = field(default_factory=list)
    diagnosis: Optional[DiagnosisReport] = None
    ticket_id: str = ""
    patch: Optional[RepairPatch] = None
    verification: Optional[VerificationReport] = None
    proposal: Optional[RepairProposal] = None
    audit_trail: List[AuditTrailEntry] = field(default_factory=list)
    chain_ok: Optional[bool] = None
    rounds_used: int = 0
    tokens_used: int = 0
    budget_notes: List[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    schema: str = REPAIR_SCHEMA

    @property
    def produced(self) -> bool:
        """是否真的产出了补丁 PR（唯一判据：``proposal`` 非空）"""
        return self.proposal is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "status": self.status,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "produced": self.produced,
            "diagnosis": self.diagnosis.to_dict() if self.diagnosis else None,
            "ticket_id": self.ticket_id,
            "patch": self.patch.to_dict() if self.patch else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "audit_trail": [e.to_dict() for e in self.audit_trail],
            "chain_ok": self.chain_ok,
            "rounds_used": int(self.rounds_used),
            "tokens_used": int(self.tokens_used),
            "budget_notes": list(self.budget_notes),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round(float(self.duration_ms), 2),
        }

    def steps_recorded(self) -> Tuple[str, ...]:
        """已在审计留痕中出现的步骤（审计完整性断言用）"""
        seen: List[str] = []
        for entry in self.audit_trail:
            if entry.step and entry.step not in seen:
                seen.append(entry.step)
        return tuple(seen)


@dataclass
class RepairTicket:
    """派工工单（步骤 2 产物：定位器把上下文打包成可委派的工单）

    Attributes:
        ticket_id: 工单标识。
        failure: 目标失败项。
        slices: 源码切片（失败点 ±N 行 + 相关实现文件）。
        impl_files: 由失败文件反推的实现文件（非测试文件优先）。
        trace_chain: 相关 Trace 链（``UnifiedTraceStore.chain()`` 的叶子视图）。
        descriptors: 涉及 capability 的 descriptor 叶子视图。
        recent_changes: 近期改动（只读 git 局部历史）。
        repo_head: 当前 HEAD SHA（补丁基线锚点）。
        adjacent_tests: 邻接回归测试文件候选。
        evidence_gaps: 证据缺失项（如实披露：无 Trace / 无 descriptor 等）。
    """

    ticket_id: str = ""
    failure: Optional[FailureItem] = None
    slices: List[SourceSlice] = field(default_factory=list)
    impl_files: List[str] = field(default_factory=list)
    trace_chain: List[Dict[str, Any]] = field(default_factory=list)
    descriptors: List[Dict[str, Any]] = field(default_factory=list)
    recent_changes: List[Dict[str, Any]] = field(default_factory=list)
    repo_head: str = ""
    adjacent_tests: List[str] = field(default_factory=list)
    evidence_gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "failure": self.failure.to_dict() if self.failure else None,
            "slices": [s.to_dict() for s in self.slices],
            "impl_files": list(self.impl_files),
            "trace_chain": [dict(t) for t in self.trace_chain],
            "descriptors": [dict(d) for d in self.descriptors],
            "recent_changes": [dict(c) for c in self.recent_changes],
            "repo_head": self.repo_head,
            "adjacent_tests": list(self.adjacent_tests),
            "evidence_gaps": list(self.evidence_gaps),
        }


__all__ = [
    "REPAIR_SCHEMA", "REPAIR_STEPS", "AUDIT_ACTIONS",
    "STEP_DIAGNOSE", "STEP_LOCATE", "STEP_DELEGATE", "STEP_VERIFY", "STEP_PROPOSE",
    "REASON_VERIFY_FAILED", "REASON_READONLY_ZONE", "REASON_SCOPE_EXCEEDED",
    "REASON_DELEGATION_REJECTED", "REASON_DELEGATION_FAILED", "REASON_BUDGET_EXCEEDED",
    "REASON_ROUNDS_EXCEEDED", "REASON_NO_PATCH", "REASON_NO_FAILURE",
    "FailureItem", "SourceSlice", "AuditSnapshot", "DiagnosisReport", "PatchFileStat",
    "PatchGuardReport", "RepairPatch", "CheckResult", "VerificationReport",
    "RepairProposal", "AuditTrailEntry", "RepairReport", "RepairTicket",
]
