"""步骤 5 · 产出本地补丁 PR（**不 push、不合并**）（TASK-S7-02）

【任务定位】
    任务书 §三 步骤 5 的产物（**全部本地**）：分支 ``repair/<date>-<slug>`` + 补丁文件
    + PR 描述 + 体检报告路径 + 复现命令；并**明确不执行** ``git push`` / 远端 PR /
    合并 / rebase。

【不易（为什么"不 push"要由代码结构来保证，而不是靠注释）】
    注释拦不住任何人。本模块的做法是：
      1. 全模块**只调用** ``gitio.run_git_readonly()``（只读白名单）与
         ``gitio.create_local_branch()``（唯一写操作，只创建本地引用）；
      2. 交付物里 ``RepairProposal.pushed`` / ``merged`` 是**恒为 False 的字段**，
         它们不是"状态"而是**自证**：任何真实 push 都必须先有人去改这个数据类；
      3. 单测 ``test_repair_no_push.py`` 对 ``agent/repair/`` 全包做源码扫描 +
         monkeypatch 探针双重断言（验收清单「代码级断言无 push/无 merge 调用」）。

【不易（改测试断言必须显式披露）】
    任务书 §三 步骤 3：「若改测试，必须在 PR 描述中显式标注『修改了测试断言』并给出
    理由，**供人工重点审**」。故 ``pr_description()`` 在检测到测试文件被改动时
    **强制**插入一段固定措辞的警示块，且它不依赖子代理的 ``self_eval`` 自述
    （自述不可信，以护栏的机械判定为准）。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.repair import gitio
from agent.repair.guardrails import guard_report_markdown
from agent.repair.models import (
    REPAIR_STEPS,
    DiagnosisReport,
    RepairPatch,
    RepairProposal,
    RepairTicket,
    VerificationReport,
)
from agent.repair.policy import MIN_REVIEW_FOCUS_ITEMS, describe_readonly_zones
from agent.repair.trace import RepairRunLogger, slugify

logger = logging.getLogger("agent.repair.propose")

#: 产物根目录（仓库相对；**必须**在 .gitignore 内，避免污染交付）
DEFAULT_ARTIFACT_DIR = "data/repair"

#: 分支名前缀（任务书 §三 步骤 5）
BRANCH_PREFIX = "repair/"

#: 固定披露措辞（改测试断言时的强制警示块）
TEST_CHANGE_WARNING = (
    "> ⚠ **本次修改了测试文件**（命中：{paths}）。\n"
    "> 按 TASK-S7-02 §三 步骤 3 纪律，此处**显式标注**并说明理由（见下方「补丁说明」）。\n"
    "> **人工必须重点审**：确认这是「测试本身写错了」的正当修正，"
    "而不是「把断言改松以让补丁过关」。"
)


def default_artifact_dir(repo_root: str) -> str:
    """产物目录的绝对路径（``<repo>/data/repair``）"""
    return os.path.join(os.path.abspath(repo_root), *DEFAULT_ARTIFACT_DIR.split("/"))


def branch_name(*, slug: str, when: Optional[datetime] = None) -> str:
    """分支名 ``repair/<YYYYMMDD>-<slug>``"""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"{BRANCH_PREFIX}{stamp}-{slugify(slug)}"


def _render_failure(diagnosis: Optional[DiagnosisReport],
                    ticket: RepairTicket) -> str:
    lines: List[str] = []
    if ticket.failure is not None and ticket.failure.node_id:
        f = ticket.failure
        loc = f"{f.file}:{f.line}" if f.line else f.file
        lines += [
            f"- 失败用例：`{f.node_id}`",
            f"- 位置：`{loc}`",
            f"- 类型：{f.kind}",
            f"- 断言摘要：{f.message or '(无)'}",
            f"- 堆栈指纹：`{f.stack_fingerprint}`",
        ]
    if diagnosis is not None:
        lines += [
            f"- 本次体检计数：pass {diagnosis.tests_passed} / fail "
            f"{diagnosis.tests_failed} / error {diagnosis.tests_error} / "
            f"skipped {diagnosis.tests_skipped}",
            f"- 体检命令：`{diagnosis.test_command}`",
        ]
        if diagnosis.anchor_ok is not None:
            lines.append(
                f"- L0 锚（体检时）：{'全过' if diagnosis.anchor_ok else '存在失败'}")
    return "\n".join(lines) if lines else "（无失败项信息）"


def _render_evidence(ticket: RepairTicket) -> str:
    lines: List[str] = []
    if ticket.trace_chain:
        lines.append("**Trace 链**（`UnifiedTraceStore`，叶子字段）：")
        for item in ticket.trace_chain[:6]:
            lines.append(
                f"- `{item.get('trace_id', '')[:16]}` capability="
                f"`{item.get('capability_id', '')}` actor={item.get('actor', '')} "
                f"status={item.get('status', '')} "
                f"parent=`{str(item.get('parent_trace_id') or '')[:16]}`")
    else:
        lines.append("**Trace 链**：无（证据缺口，已在工单 `evidence_gaps` 披露）")
    if ticket.descriptors:
        lines.append("**涉及 capability（descriptor）**：")
        for item in ticket.descriptors[:6]:
            lines.append(f"- `{item.get('capability_id', '')}`：{item.get('name', '')}")
    if ticket.recent_changes:
        lines.append("**最近改动**（只读 git）：")
        for item in ticket.recent_changes[:6]:
            if "subject" in item:
                lines.append(f"- `{item.get('short', '')}` {item.get('subject', '')[:100]}")
            else:
                lines.append(f"- `{item.get('path', '')}`（{item.get('source', '')}）")
    if ticket.evidence_gaps:
        lines.append("**证据缺口（如实披露）**：")
        for gap in ticket.evidence_gaps:
            lines.append(f"- {gap}")
    return "\n".join(lines)


def _render_verification(verification: Optional[VerificationReport]) -> str:
    if verification is None:
        return "（无验证记录）"
    lines = [
        f"- 补丁 sha256：`{verification.diff_sha256[:32]}`",
        f"- 打补丁前目标用例确实失败："
        f"{'是' if verification.target_failed_before else '否（不放行）'}",
        "",
        "| 闸门 | 结论 | 命令 | 退出码 | pass | fail |",
        "|---|---|---|---|---|---|",
    ]
    for gate in verification.gate_results:
        conclusion = "通过" if gate.effective_pass else (
            "跳过" if gate.skipped else "未通过")
        lines.append(
            f"| {gate.name} | {conclusion} | `{gate.command[:120]}` | "
            f"{gate.exit_code} | {gate.passed_count} | {gate.failed_count} |")
    for gate in verification.gate_results:
        if gate.output_summary:
            lines += ["", f"**{gate.name} 输出摘要**：", "```",
                      gate.output_summary[-1500:], "```"]
    if verification.apply_error:
        lines += ["", f"**补丁应用错误**：{verification.apply_error}"]
    return "\n".join(lines)


def review_focus(*, patch: RepairPatch, verification: Optional[VerificationReport],
                 ticket: RepairTicket) -> List[str]:
    """人工复核重点（**至少 MIN_REVIEW_FOCUS_ITEMS 条**，机械可数）

    这些条目不是客套话：每一条都对应本流程**在结构上无法自证**的东西——
    「根因是否真是这个」「测试是不是被改松了」「证据是否够」。
    """
    items: List[str] = [
        "**根因是否成立**：补丁修的是「失败用例指出的那个根因」，还是只是让断言"
        "恰好通过？（对照下方证据链里的 Trace/最近改动自行判断）",
        "**是否有测试断言被改松**：若本 PR 触及测试文件，请逐行确认改动属于"
        "「修正测试自身错误」，而非「放宽断言以过关」。",
        "**范围是否最小**：是否夹带了与目标失败项无关的重构、格式化或重命名？",
    ]
    if patch.guard is not None and patch.guard.test_assertions_touched:
        items.append(
            f"**测试文件改动清单**：{', '.join(patch.guard.test_assertions_touched)}"
            f"（护栏已机械识别，请优先审这几个文件）")
    if ticket.evidence_gaps:
        items.append("**证据缺口**：工单列出的缺口项是否会让根因判断站不住？"
                     "（缺口已如实披露，不视为缺陷但影响置信度）")
    if verification is not None and verification.adjacent.skip_reason:
        items.append(f"**邻接回归口径**：{verification.adjacent.skip_reason}")
    items.append(
        "**验证隔离性**：三关均在临时副本上跑（`verify.py`），"
        "主工作区与本会话 worktree 未被改动——可复核 `git status` 无漂移。")
    items.append("**回滚口径**：见下方 `undo_hint`；合并前请确认回滚路径可用。")
    return items


def pr_description(*, proposal_branch: str, ticket: RepairTicket, patch: RepairPatch,
                   verification: Optional[VerificationReport],
                   diagnosis: Optional[DiagnosisReport], policy_dict: Dict[str, Any],
                   run_id: str, audit_note: str = "") -> Tuple[str, List[str], str]:
    """生成 PR 描述（返回 ``(描述正文, 复核重点, undo_hint)``）

    结构（任务书 §三 步骤 5 逐条对应）：
      问题摘要 / 根因分析（引 Trace+审计证据）/ 补丁说明 / 验证证据（三关）/
      风险与回滚（``undo_hint``）/ **人工复核重点**
    """
    guard = patch.guard
    test_touched: List[str] = list(guard.test_assertions_touched) if guard else []
    focus = review_focus(patch=patch, verification=verification, ticket=ticket)

    head: List[str] = [
        f"# 修复补丁 PR（本地）— {ticket.ticket_id}",
        "",
        "> **本 PR 由 TASK-S7-02 自修复 L1 流水线生成，产物止于本地："
        "未 push 远端、未合并、未创建远端 PR。**",
        f"> 运行标识：`{run_id}`｜基线 HEAD：`{ticket.repo_head[:12] or '(未知)'}`"
        f"｜产物分支：`{proposal_branch}`",
        "",
    ]
    if test_touched:
        head += [TEST_CHANGE_WARNING.format(
            paths=", ".join(f"`{p}`" for p in test_touched)), ""]
    head += [
        "## 一、问题摘要",
        "",
        _render_failure(diagnosis, ticket),
        "",
        "## 二、根因分析（引用证据）",
        "",
        patch.rationale or "（子代理未给出变更理由 —— **本身即为复核重点**）",
        "",
        "### 证据链",
        "",
        _render_evidence(ticket),
        "",
        "## 三、补丁说明",
        "",
        f"- 涉及文件（护栏机械统计）：{guard.changed_files if guard else 0} 个，"
        f"单文件最大改动 {guard.max_file_changed_lines if guard else 0} 行",
    ]
    if guard:
        head += ["", guard_report_markdown(guard).rstrip()]
        head += [
            "",
            f"- 策略上限：文件数 ≤ {policy_dict.get('max_changed_files')}、"
            f"单文件行数 ≤ {policy_dict.get('max_lines_per_file')}",
        ]
    if patch.files:
        head += ["", f"- 子代理自报文件清单："
                     f"{', '.join(f'`{f}`' for f in patch.files)}"]
    head += ["", "### 补丁（统一 diff）", "", "```diff", patch.diff.rstrip(), "```",
             "",
             "### 子代理自评（**不可信，仅参考**）", "",
             "```json", json.dumps(dict(patch.self_eval), ensure_ascii=False, indent=2),
             "```",
             "",
             "## 四、验证证据（隔离验证三关）", "",
             "> 三关均在**临时副本**上执行（`agent/repair/verify.py` 复制仓库 → "
             "打补丁 → 跑测试 → 删除副本）；主工作区未改动。", "",
             _render_verification(verification), ""]

    head += [
        "## 五、风险与回滚",
        "",
        f"- 只读区护栏：{describe_readonly_zones()['prefixes']} 等路径已机械拒绝；"
        f"本次未命中。",
        f"- 审计留痕：五步（{'/'.join(REPAIR_STEPS)}）各一条 Trace + 链式审计；"
        f"{audit_note or '审计链校验见运行时报告'}",
    ]
    undo_hint = (
        f"`git branch -D {proposal_branch}`（未合并时直接删除产物分支）；"
        f"若已合并：`git revert -m 1 <merge_sha>`。补丁文件保留在产物目录，"
        f"可用 `git apply -R <patch>` 反向撤销工作区改动。"
    )
    head += [f"- undo_hint：{undo_hint}", ""]
    head += ["## 六、人工复核重点", ""]
    head += [f"{idx}. {item}" for idx, item in enumerate(focus, start=1)]
    head += ["", "## 七、复现命令", "",
             "```bash",
             f"python scripts/self_repair.py --test-target {ticket.failure.file if ticket.failure else '<测试文件>'}",
             f"# 体检报告：<artifact-dir>/diagnose_{run_id}.md",
             f"# 补丁文件：<artifact-dir>/patch_{run_id}.patch",
             "```", ""]
    if len(focus) < MIN_REVIEW_FOCUS_ITEMS:  # pragma: no cover 防御性
        head.append(f"> ⚠ 复核重点不足 {MIN_REVIEW_FOCUS_ITEMS} 条，已如实标注。")
    return "\n".join(head), focus, undo_hint


@dataclass
class ProposeRequest:
    """产出请求

    Attributes:
        repo_root: 仓库根（创建本地分支用）。
        ticket: 工单。
        patch: 补丁。
        verification: 验证报告（``ok`` 必须为 True，否则本模块拒绝产出）。
        diagnosis: 体检报告（写文件与 PR 描述用）。
        run_id: 运行标识。
        ticket_slug: 分支 slug（缺省由失败用例名派生）。
        artifact_dir: 产物目录（缺省 ``<repo>/data/repair``）。
        create_branch: 是否创建本地分支（默认 True；非 git 仓库自动降级为仅出补丁）。
        policy_dict: 策略快照（写进描述）。
        audit_note: 审计链结论说明。
        run_logger: 留痕器。
    """

    repo_root: str
    ticket: RepairTicket
    patch: RepairPatch
    verification: Optional[VerificationReport]
    diagnosis: Optional[DiagnosisReport] = None
    run_id: str = ""
    ticket_slug: str = ""
    artifact_dir: str = ""
    create_branch: bool = True
    policy_dict: Dict[str, Any] = field(default_factory=dict)
    audit_note: str = ""
    run_logger: Optional[RepairRunLogger] = None


class ProposalNotVerified(RuntimeError):
    """验证未过却试图产出（**结构性拒绝**：不得把未验证补丁交给人工）"""


def propose(request: ProposeRequest) -> RepairProposal:
    """产出本地补丁 PR 产物（步骤 5）

    Raises:
        ProposalNotVerified: ``verification`` 缺失或 ``ok=False``
            ——**这是本任务边界 ③ 的代码级落点**。
    """
    verification = request.verification
    if verification is None or not verification.ok:
        raise ProposalNotVerified(
            "验证未通过（或缺验证记录）→ 拒绝产出补丁 PR；"
            "请改为如实产出「未修复」报告")
    run_id = request.run_id or "unknown"
    slug = request.ticket_slug or (
        request.ticket.failure.node_id if request.ticket.failure else "issue")
    branch = branch_name(slug=slug)
    artifact_dir = request.artifact_dir or default_artifact_dir(request.repo_root)

    description, focus, undo_hint = pr_description(
        proposal_branch=branch, ticket=request.ticket, patch=request.patch,
        verification=verification, diagnosis=request.diagnosis,
        policy_dict=request.policy_dict, run_id=run_id,
        audit_note=request.audit_note)

    patch_path = os.path.join(artifact_dir, f"patch_{run_id}.patch")
    desc_path = os.path.join(artifact_dir, f"PR_{run_id}.md")
    diag_path = os.path.join(artifact_dir, f"diagnose_{run_id}.md")
    report_path = os.path.join(artifact_dir, f"report_{run_id}.json")
    os.makedirs(artifact_dir, exist_ok=True)
    _write_text(patch_path, request.patch.diff if request.patch.diff.endswith("\n")
                else request.patch.diff + "\n")
    _write_text(desc_path, description)
    if request.diagnosis is not None:
        _write_text(diag_path, request.diagnosis.summary_markdown())
    _write_text(report_path, json.dumps({
        "run_id": run_id,
        "branch": branch,
        "ticket": request.ticket.to_dict(),
        "patch": request.patch.to_dict(),
        "verification": verification.to_dict(),
        "review_focus": focus,
        "undo_hint": undo_hint,
        "pushed": False,
        "merged": False,
    }, ensure_ascii=False, indent=2) + "\n")

    created = False
    branch_error = ""
    if request.create_branch and gitio.is_git_repo(request.repo_root):
        result = gitio.create_local_branch(request.repo_root, branch,
                                           start_point=request.ticket.repo_head or "HEAD")
        created = bool(result.created)
        branch_error = result.error
    elif request.create_branch:
        branch_error = "给定根不是 git 工作区 → 仅产出补丁文件，不创建分支"

    proposal = RepairProposal(
        branch=branch, patch_path=patch_path, description_path=desc_path,
        diagnose_path=diag_path if request.diagnosis is not None else "",
        pr_description=description, review_focus=focus, undo_hint=undo_hint,
        created_branch=created, pushed=False, merged=False)

    if request.run_logger is not None:
        request.run_logger.record_step(
            "propose", subject=branch,
            status="ok" if created or not branch_error else "error",
            detail={**proposal.to_dict(), "branch_error": branch_error,
                    "report_path": report_path,
                    "review_focus_count": len(focus)},
            error=branch_error)
    return proposal


def _write_text(path: str, text: str) -> str:
    """写 UTF-8 文本（换行统一为 \\n；父目录按需创建）"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


__all__ = [
    "DEFAULT_ARTIFACT_DIR", "BRANCH_PREFIX", "TEST_CHANGE_WARNING",
    "default_artifact_dir", "branch_name", "review_focus", "pr_description",
    "ProposeRequest", "ProposalNotVerified", "propose",
]
