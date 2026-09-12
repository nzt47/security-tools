"""五步流水线编排（TASK-S7-02 §三 步骤 1–5）

【任务定位】
    把体检 → 定位 → 派工 → 验证 → 产出串成一条**受控**闭环，并在每一处失败上
    明确"停在哪、为什么停、留下了什么证据"。本模块是唯一知道"整条流水线长什么样"
    的地方；入口脚本 ``scripts/self_repair.py`` 只做参数解析与报告打印。

【不易（不产出未验证补丁）】
    ``propose.propose()`` 在 ``verification.ok=False`` 时**结构性拒绝**。因此本模块
    不需要（也无法）绕过它——"验证不过就不产出"这条边界由类型与异常共同保证，
    而不是由本模块的 if 判断保证。

【不易（超限即停并留证）】
    预算与轮次超限由 ``budget`` 抛 ``BudgetExceeded``；本模块捕获后**立即停**，
    并把原因码写入 ``RepairReport.reasons``。停的时候必须留下"为什么停"——
    否则"超出预算"会看起来像"没有发现问题"。

【不易（无失败不编造）】
    体检无失败 → ``status="no_failure"`` 且**不进入**后续四步（也**不**记四条空留痕
    冒充"跑完了"）。五步留痕的完整性只在真的走到那里时才成立；未走的步骤在报告里
    以 ``steps_recorded`` 的缺失如实体现。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.repair import gitio
from agent.repair.budget import BudgetExceeded, RepairBudget
from agent.repair.models import (
    REASON_NO_FAILURE,
    REASON_VERIFY_FAILED,
    REPAIR_STEPS,
    DiagnosisReport,
    RepairPatch,
    RepairReport,
    RepairTicket,
    VerificationReport,
)
from agent.repair.policy import RepairPolicy, policy_from_env
from agent.repair.trace import RepairRunLogger, RunLoggerConfig, new_run_id, slugify
from agent.repair.trace import utc_now_iso

logger = logging.getLogger("agent.repair.pipeline")

#: 状态取值（入口脚本与报告共用）
STATUS_NO_FAILURE = "no_failure"
STATUS_PROPOSED = "proposed"
STATUS_DISCARDED = "discarded"
STATUS_REJECTED = "rejected"
STATUS_ERROR = "error"
STATUSES: Tuple[str, ...] = (
    STATUS_NO_FAILURE, STATUS_PROPOSED, STATUS_DISCARDED, STATUS_REJECTED, STATUS_ERROR,
)


@dataclass
class PipelineRequest:
    """流水线输入

    Attributes:
        repo_root: 仓库根（体检与只读 git 的基准；验证在其**副本**上进行）。
        targets: 体检 pytest 目标。
        policy: 策略（缺省由 ``policy_from_env()`` 生成）。
        executor: 派工执行器（``DelegationExecutor`` 或桩；**必须**提供）。
        probe: 探针执行器（缺省 ``SubprocessExecutor``）。
        artifact_dir: 产物目录（缺省 ``<repo>/data/repair``）。
        create_branch: 是否创建本地产物分支。
        with_anchor: 体检是否同跑 L0 锚。
        with_audit: 体检是否取审计快照。
        timeout: 单个测试闸门超时（秒）。
        run_id: 运行标识（缺省自动生成）。
        emit_events: 是否发事件。
        trace_db: 统一 Trace 库路径（单测隔离用）。
        audit: 审计 facade（单测隔离用）。
        trace_facade: Trace facade（单测隔离用）。
        events_dir: 事件目录（单测隔离用）。
        capability: Trace/descriptor 查询用的 capability 名。
    """

    repo_root: str
    executor: Any = None
    targets: Tuple[str, ...] = ()
    policy: Optional[RepairPolicy] = None
    probe: Any = None
    artifact_dir: str = ""
    create_branch: bool = True
    with_anchor: bool = True
    with_audit: bool = False
    timeout: float = 900.0
    run_id: str = ""
    emit_events: bool = True
    trace_db: str = ""
    audit: Any = None
    trace_facade: Any = None
    events_dir: str = ""
    capability: str = ""
    keep_copy: bool = False


def _result_reasons(report: RepairReport) -> List[str]:
    """收集报告里的全部原因码（去重保序）"""
    out: List[str] = []
    for code in [report.reason, *report.reasons]:
        if code and code not in out:
            out.append(code)
    return out


def _verification_gap(verification: VerificationReport) -> Dict[str, Any]:
    """把三关失败点压成叶子字段（进审计与报告）"""
    return {
        "target_pass": verification.target.effective_pass,
        "anchor_pass": verification.anchor.effective_pass,
        "adjacent_pass": verification.adjacent.effective_pass,
        "target_failed_before": verification.target_failed_before,
        "apply_error": verification.apply_error,
        "diff_sha256": verification.diff_sha256[:16],
    }


def run_repair(request: PipelineRequest) -> RepairReport:
    """执行一次修复流水线（**手动触发**；本函数不做任何自动触发/定时/告警驱动）

    Returns:
        ``RepairReport``；``proposal is None`` 即未产出（丢弃/拒绝/无失败）。
    """
    started_perf = time.perf_counter()
    started_iso = utc_now_iso()
    run_id = request.run_id or new_run_id()
    policy = request.policy or policy_from_env()
    artifact_dir = request.artifact_dir or os.path.join(
        os.path.abspath(request.repo_root), "data", "repair")
    run_logger = RepairRunLogger(
        RunLoggerConfig(repo_root=request.repo_root, trace_db=request.trace_db,
                        trace_facade=request.trace_facade, audit=request.audit,
                        events_dir=request.events_dir, emit_events=request.emit_events),
        run_id=run_id)
    report = RepairReport(run_id=run_id, started_at=started_iso,
                          budget_notes=list(policy.notes))
    budget = RepairBudget.from_policy(policy)

    from agent.repair import diagnose as diag_mod
    from agent.repair import locate as locate_mod
    from agent.repair import propose as propose_mod
    from agent.repair import verify as verify_mod
    from agent.repair.delegate import DelegationRequest, delegate

    try:
        # ── 步骤 1：体检 ──
        junit_path = os.path.join(artifact_dir, f"junit_{run_id}.xml")
        os.makedirs(artifact_dir, exist_ok=True)
        diagnosis = diag_mod.diagnose(
            repo_root=request.repo_root, run_id=run_id, targets=request.targets,
            policy=policy, executor=request.probe, junit_path=junit_path,
            timeout=float(request.timeout), with_anchor=bool(request.with_anchor),
            with_audit=bool(request.with_audit), run_logger=run_logger)
        report.diagnosis = diagnosis
        if diagnosis.failure_count == 0:
            # 「无失败」是合法结果：不编造问题，不进后续步骤
            report.status = STATUS_NO_FAILURE
            report.reason = REASON_NO_FAILURE
            report.reasons = [REASON_NO_FAILURE]
            return _finish(report, run_logger, budget, started_perf, artifact_dir)
        # 体检本身发现失败 → 暴露一条事件（S2-01 已登记事件类型之一）
        run_logger.emit_event("healing.triggered", {
            "level": "L1",
            "signal": "repair.diagnose.failure",
            "scope": "module",
            "severity": "warning",
            "automated": False,
            "requires_approval": True,
            "mttd_ms": None,
            "mttr_ms": None,
            "tenant_id": "default",
            "incident_id": "",
            "repair_run_id": run_id,
            "failure_fingerprints": list(diagnosis.failures_by_fingerprint),
        })

        verification: Optional[VerificationReport] = None
        patch: Optional[RepairPatch] = None
        ticket: Optional[RepairTicket] = None

        while True:
            # ── 预算/轮次闸门（超限即停并留证）──
            try:
                budget.start_round()
            except BudgetExceeded as exc:
                report.reasons.append(exc.code)
                report.budget_notes.append(str(exc))
                break

            # ── 步骤 2：定位 ──
            ticket = locate_mod.locate(diagnosis, repo_root=request.repo_root,
                                       policy=policy, run_logger=run_logger,
                                       capability=request.capability)
            report.ticket_id = ticket.ticket_id
            if ticket.failure is None or not ticket.failure.node_id:
                report.reasons.append("E_REPAIR_NO_TARGET")
                break

            # ── 步骤 3：派工 ──
            outcome = delegate(
                DelegationRequest(ticket=ticket, policy=policy, run_logger=run_logger),
                executor=request.executor, budget=budget)
            report.tokens_used = int(budget.tokens_used)
            patch = outcome.patch
            report.patch = patch
            if not outcome.ok:
                if outcome.reason:
                    report.reasons.append(outcome.reason)
                if outcome.reason in ("E_REPAIR_BUDGET_EXCEEDED",
                                      "E_REPAIR_ROUNDS_EXCEEDED"):
                    break
                if budget.rounds_left <= 0:
                    break
                continue

            # ── 步骤 4：隔离验证 ──
            verification = verify_mod.verify(verify_mod.VerifyRequest(
                patch=patch, ticket=ticket, repo_root=request.repo_root, policy=policy,
                executor=request.probe, timeout=float(request.timeout),
                run_logger=run_logger, keep_copy=bool(request.keep_copy)))
            report.verification = verification
            report.tokens_used = int(budget.tokens_used)
            if verification.ok:
                break
            report.reasons.append(REASON_VERIFY_FAILED)
            report.budget_notes.append(
                "第 %d 轮验证未过 → 丢弃补丁（%s）" % (
                    budget.rounds_used,
                    "; ".join(f"{k}={v}" for k, v in _verification_gap(verification).items())))
            if budget.rounds_left <= 0:
                break
            continue

        # ── 步骤 5：产出（仅当验证真通过）──
        if verification is not None and verification.ok and patch is not None \
                and ticket is not None:
            proposal = propose_mod.propose(propose_mod.ProposeRequest(
                repo_root=request.repo_root, ticket=ticket, patch=patch,
                verification=verification, diagnosis=diagnosis, run_id=run_id,
                ticket_slug=slugify(ticket.failure.node_id if ticket.failure else "issue"),
                artifact_dir=artifact_dir, create_branch=bool(request.create_branch),
                policy_dict=policy.to_dict(),
                audit_note="审计链校验结论见报告 chain_ok 字段",
                run_logger=run_logger))
            report.proposal = proposal
            report.status = STATUS_PROPOSED
            report.reason = ""
        else:
            report.status = _discarded_status(report)
            report.reason = (report.reasons[-1] if report.reasons else REASON_VERIFY_FAILED)
        return _finish(report, run_logger, budget, started_perf, artifact_dir)
    except Exception as exc:  # noqa: BLE001 流水线自身异常也要留痕后如实上报
        logger.exception("修复流水线异常")
        report.status = STATUS_ERROR
        report.reason = f"E_REPAIR_PIPELINE:{type(exc).__name__}"
        report.reasons.append(report.reason)
        report.budget_notes.append(f"流水线异常：{type(exc).__name__}: {exc}")
        return _finish(report, run_logger, budget, started_perf, artifact_dir)


def _discarded_status(report: RepairReport) -> str:
    """区分「护栏拒绝」与「验证未过」两类未产出（报告口径不同）"""
    reasons = set(_result_reasons(report))
    if reasons & {"E_REPAIR_READONLY_ZONE", "E_REPAIR_SCOPE_EXCEEDED",
                  "E_REPAIR_DELEGATION_REJECTED", "E_REPAIR_TOOLSET_UNSAFE"}:
        return STATUS_REJECTED
    return STATUS_DISCARDED


def _finish(report: RepairReport, run_logger: RepairRunLogger, budget: RepairBudget,
            started_perf: float, artifact_dir: str) -> RepairReport:
    """收尾：刷新留痕、校验审计链、落盘报告（**任何情况下都执行**）"""
    report.audit_trail = run_logger.trail()
    report.rounds_used = int(budget.rounds_used)
    report.tokens_used = int(budget.tokens_used)
    report.budget_notes.extend([n for n in budget.notes if n not in report.budget_notes])
    report.budget_notes.extend([n for n in run_logger.notes
                                if n not in report.budget_notes])
    report.finished_at = utc_now_iso()
    report.duration_ms = (time.perf_counter() - started_perf) * 1000.0
    run_logger.flush()
    report.chain_ok = run_logger.verify_chain()
    _write_report(report, artifact_dir)
    return report


def _write_report(report: RepairReport, artifact_dir: str) -> str:
    """把完整报告写成 JSON（**产物目录在 .gitignore 内**，不污染交付）"""
    import json
    try:
        os.makedirs(artifact_dir, exist_ok=True)
        path = os.path.join(artifact_dir, f"run_{report.run_id}.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return path
    except OSError as exc:  # pragma: no cover 落盘失败不阻断（报告仍可打印）
        logger.warning("报告落盘失败：%s", exc)
        return ""


def audit_completeness(report: RepairReport) -> Tuple[bool, Tuple[str, ...]]:
    """审计完整性断言：产出成功时**五步必须全部留痕**

    Returns:
        ``(ok, missing_steps)``；未产出（无失败/丢弃）时要求前若干步留痕，
        具体阈值见 ``expected_steps()``。
    """
    expected = expected_steps(report.status)
    recorded = set(report.steps_recorded())
    missing = tuple(s for s in expected if s not in recorded)
    return (not missing), missing


def expected_steps(status: str) -> Tuple[str, ...]:
    """按最终状态给出"应当留痕"的步骤集合

    - ``no_failure``：只需 ``diagnose``（没发现问题，不该有后四步的留痕）；
    - ``proposed``：五步齐全；
    - 其余（丢弃/拒绝/错误）：至少 ``diagnose`` + ``locate`` + ``delegate``。
    """
    if status == STATUS_NO_FAILURE:
        return (REPAIR_STEPS[0],)
    if status == STATUS_PROPOSED:
        return tuple(REPAIR_STEPS)
    return tuple(REPAIR_STEPS[:3])


def report_markdown(report: RepairReport) -> str:
    """终态报告的人读摘要（入口脚本打印用）"""
    lines = [
        f"# 自修复 L1 运行报告 {report.run_id}",
        "",
        f"- 状态：**{report.status}**" + (f"（原因 `{report.reason}`）"
                                          if report.reason else ""),
        f"- 是否产出补丁 PR：{'是' if report.produced else '否'}"
        f"（产物**全部本地**：未 push 远端、未合并、未创建远端 PR）",
        f"- 轮次：{report.rounds_used}｜token：{report.tokens_used}",
        f"- 审计链校验：{'未校验' if report.chain_ok is None else ('通过' if report.chain_ok else '未通过')}",
        f"- 留痕步骤：{'/'.join(report.steps_recorded()) or '（无）'}",
        f"- 起止：{report.started_at} → {report.finished_at}"
        f"（{report.duration_ms:.0f} ms）",
    ]
    if report.reasons:
        lines += ["", f"- 原因码：{', '.join(f'`{c}`' for c in report.reasons)}"]
    if report.budget_notes:
        lines += ["", "**预算/轮次与降级说明**："] + [f"- {n}" for n in report.budget_notes]
    if report.diagnosis is not None:
        d = report.diagnosis
        lines += ["", "## 体检",
                  f"- 结论：{'未发现失败' if d.ok else f'{d.failure_count} 条失败'}",
                  f"- 命令：`{d.test_command}`",
                  f"- L0 锚：{'未运行' if d.anchor_ok is None else ('全过' if d.anchor_ok else '存在失败')}"]
        for item in d.failures[:5]:
            lines.append(f"  - `{item.node_id}` @ `{item.file}:{item.line}` "
                         f"指纹 `{item.stack_fingerprint}`")
    if report.patch is not None:
        p = report.patch
        lines += ["", "## 补丁",
                  f"- 有 diff：{'是' if p.non_empty else '否'}｜字符数 {len(p.diff)}",
                  f"- 护栏：{'通过' if (p.guard and p.guard.ok) else '拒绝/未解析'}",
                  f"- 丢弃原因：`{p.rejected_reason}`" if p.rejected_reason else "- 未丢弃"]
    if report.verification is not None:
        v = report.verification
        lines += ["", "## 隔离验证三关",
                  f"- 结论：**{'三关全过' if v.ok else '未全过（补丁已丢弃）'}**",
                  f"- 目标用例：{'通过' if v.target.effective_pass else '未通过'}"
                  f"（打补丁前失败：{'是' if v.target_failed_before else '否'}）",
                  f"- L0 锚：{'通过' if v.anchor.effective_pass else '未通过'}",
                  f"- 邻接回归：{'通过' if v.adjacent.effective_pass else '未通过'}"]
        if v.apply_error:
            lines.append(f"- 应用错误：{v.apply_error}")
    if report.proposal is not None:
        pr = report.proposal
        lines += ["", "## 产物（全部本地：**未 push 远端、未合并**）",
                  f"- 分支：`{pr.branch}`（创建：{'是' if pr.created_branch else '否'}）",
                  f"- 补丁：`{pr.patch_path}`",
                  f"- PR 描述：`{pr.description_path}`",
                  f"- 体检报告：`{pr.diagnose_path or '(无)'}`",
                  f"- 复核重点：{len(pr.review_focus)} 条",
                  f"- push/merge：{'是' if pr.pushed else '**否**'} / "
                  f"{'是' if pr.merged else '**否**'}（本任务恒为否）"]
    elif report.status in (STATUS_DISCARDED, STATUS_REJECTED):
        lines += ["", "## 未产出（如实报告）",
                  "- 本次**没有**产出补丁 PR：补丁未通过三关或命中护栏 → 已丢弃",
                  "- 被丢弃的补丁与验证证据仍保留在报告中，供人查看与复现"]
    if report.diagnosis is not None and report.diagnosis.disclosures:
        lines += ["", "## 披露"] + [f"- {x}" for x in report.diagnosis.disclosures]
    return "\n".join(lines) + "\n"


__all__ = [
    "STATUS_NO_FAILURE", "STATUS_PROPOSED", "STATUS_DISCARDED", "STATUS_REJECTED",
    "STATUS_ERROR", "STATUSES", "PipelineRequest", "run_repair", "expected_steps",
    "audit_completeness", "report_markdown", "build_sandbox_repo",
]
