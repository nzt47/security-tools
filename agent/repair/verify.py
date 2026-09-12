"""步骤 4 · 隔离验证：临时副本 + 三关（不过即丢弃）（TASK-S7-02 核心）

【任务定位】
    任务书 §三 步骤 4 的关键词是「**绝不在主工作区或本会话 worktree 直接改**」。
    本模块的隔离性由结构保证，而不是靠纪律：

      1. ``prepare_isolated_copy()`` 把仓库**复制**到系统临时目录（排除 ``.git`` /
         venv / 缓存 / 产物目录），得到一份可随意破坏的副本；
      2. 补丁（``patchapply.apply_unified_diff``）只作用于该副本；
      3. 三关（目标用例 / L0 锚 / 邻接回归）全部在该副本内执行；
      4. 无论成败，副本在 ``finally`` 中删除。

    因此「污染交付」这件事在本模块里没有物理路径——这也让「验证不过即丢弃」成为
    默认结果而不是额外努力。

【不易（三关缺一不可，且必须 fail→pass）】
    - 目标用例：**before 失败、after 通过**。只测 after 是不够的——那会让"本来就没坏"
      被当成"修好了"。故本模块先跑一次 before（在未打补丁的副本上）。
    - L0 锚：**必须全过**（系统不可写的客观标尺）。锚跑不起来（完整性失败）时按
      **不通过**处理（fail-closed），并如实写明原因。
    - 邻接回归：按改动文件映射的测试子集（无映射时如实标注"空集"，但仍视为通过——
      空集不是失败，是"没有邻接测试可跑"，这一点在报告里可见）。

【变易】
    「跑测试」这件事本身可注入（``executor``），使三关逻辑可在无 pytest 的环境下
    单测；真实路径走 ``diagnose.SubprocessExecutor``。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

from agent.repair.diagnose import (
    ProbeExecutor,
    ProbeOutput,
    SubprocessExecutor,
    clean_text,
    parse_junit_xml,
    pytest_argv,
    run_anchor,
)
from agent.repair.models import (
    CheckResult,
    RepairPatch,
    RepairTicket,
    VerificationReport,
)
from agent.repair.patchapply import apply_unified_diff
from agent.repair.policy import RepairPolicy
from agent.repair.trace import RepairRunLogger

logger = logging.getLogger("agent.repair.verify")

#: 复制副本时**排除**的目录名（`.git` 单列：副本不需要它，且体积最大）
EXCLUDED_DIRS: Tuple[str, ...] = (
    ".git", ".worktrees", "venv", ".venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".import_linter_cache",
    "htmlcov", "coverage_report", "build", "dist", "backup", "backups",
    ".file_backups", ".fix_backups", ".env.backups", ".backups", "tmp_backup_test",
    "any", "Modules",
)

#: 复制时跳过的**文件后缀**（体积大且与验证无关）
EXCLUDED_SUFFIXES: Tuple[str, ...] = (
    ".pyc", ".pyo", ".pyd", ".log", ".zip", ".7z", ".tar", ".gz",
)

#: 单个测试闸门的输出摘要行数
TAIL_LINES = 15


def _ignore(_dir: str, names: Sequence[str]) -> List[str]:
    """``shutil.copytree`` 的忽略函数（目录名 + 后缀 + 超大产物目录）"""
    ignored: List[str] = []
    for name in names:
        if name in EXCLUDED_DIRS:
            ignored.append(name)
            continue
        if name.endswith(EXCLUDED_SUFFIXES):
            ignored.append(name)
    return ignored


def prepare_isolated_copy(repo_root: str, *, prefix: str = "cp-repair-") -> str:
    """把仓库复制到系统临时目录（**排除 .git / venv / 缓存 / 产物**）

    Returns:
        副本根路径（调用方负责在 finally 中 ``cleanup_isolated_copy``）。
    """
    root = os.path.abspath(repo_root)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"仓库根不存在：{root}")
    dest = tempfile.mkdtemp(prefix=prefix)
    target = os.path.join(dest, os.path.basename(root.rstrip("\\/")) or "repo")
    shutil.copytree(root, target, ignore=_ignore, symlinks=True,
                    dirs_exist_ok=False)
    return target


def cleanup_isolated_copy(verify_root: str) -> bool:
    """删除临时副本（返回是否清理成功；失败只记日志不抛）"""
    path = str(verify_root or "")
    if not path or not os.path.isdir(path):
        return True
    try:
        shutil.rmtree(path, ignore_errors=False)
        return True
    except OSError as exc:
        logger.warning("临时副本清理失败（%s）：%s", path, exc)
        try:
            shutil.rmtree(path, ignore_errors=True)
        except OSError:  # pragma: no cover 尽力而为
            pass
        return False


def _tail(output: ProbeOutput, *, limit: int = TAIL_LINES) -> str:
    text = (output.stdout or "").strip() or (output.stderr or "").strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return clean_text("\n".join(lines[-limit:]), limit=3000)


@dataclass
class VerifyRequest:
    """隔离验证输入

    Attributes:
        patch: 已过护栏的补丁。
        ticket: 定位工单（提供目标用例与邻接测试）。
        repo_root: **真实**仓库根（只读；副本由本模块创建）。
        policy: 策略。
        executor: 探针执行器（可注入）。
        timeout: 单个闸门超时（秒）。
        run_logger: 留痕器。
        keep_copy: 保留副本（调试用；**默认 False**）。
    """

    patch: RepairPatch
    ticket: RepairTicket
    repo_root: str
    policy: RepairPolicy = field(default_factory=RepairPolicy)
    executor: Optional[ProbeExecutor] = None
    timeout: float = 900.0
    run_logger: Optional[RepairRunLogger] = None
    keep_copy: bool = False


def _run_gate(name: str, *, argv: Sequence[str], cwd: str, executor: ProbeExecutor,
              timeout: float, junit_path: str = "") -> CheckResult:
    """跑一个测试闸门并汇总结果

    【判定优先级（为什么 junit 优先于退出码）】pytest 的退出码有 5 种含义
    （0 全过 / 1 有失败 / 2 中断 / 3 内部错误 / 4 用法错误 / 5 无用例），
    其中「退出码 5 = 没有收集到任何用例」在**节点选择**场景下会与"失败"混淆；
    而 junitxml 写了 ``failures`` 与 ``errors`` 计数，是更精确的口径。故：
      - junitxml 可解析 → **只看** junit 计数（有失败/错误即不通过）；
      - junitxml 不可用 → 退化为退出码（并如实记 skip_reason）。
    两条路径都遵守同一条纪律：**不能证明通过就不算通过**。
    """
    started = time.perf_counter()
    output = executor.run(argv, cwd=cwd, timeout=float(timeout))
    result = CheckResult(
        name=name, command=output.command, exit_code=int(output.exit_code),
        passed=(output.exit_code == 0), output_summary=_tail(output),
        duration_ms=(time.perf_counter() - started) * 1000.0)
    if junit_path:
        summary = parse_junit_xml(junit_path, repo_root=cwd)
        if not summary.parse_error:
            result.passed_count = int(summary.passed)
            result.failed_count = int(summary.failed) + int(summary.errors)
            result.passed = (result.failed_count == 0 and result.passed_count > 0)
            if result.passed_count == 0 and result.failed_count == 0:
                result.passed = False
                result.skip_reason = "junitxml 中既无通过也无失败用例（疑似未收集到用例）"
        else:
            result.skip_reason = f"junitxml 不可用（{summary.parse_error}）"
    if not output.available:
        result.passed = False
        result.skip_reason = output.stderr.strip()[:200] or "探针不可用"
    return result


def node_target(node_id: str, fallback_file: str = "") -> str:
    """把 junit 的 ``classname::name`` 形态转成 **pytest 可解析的节点 id**

    【为什么必须转】junit 的 ``classname`` 是**模块点分名**（``tests.unit.test_x``），
    直接喂给 pytest 会得到 `no tests ran`（退出码 4/5）——实测踩过：目标用例闸门
    因此"看起来失败"，而实际上是被选不中。正确形态是 ``<文件路径>::<函数名>``。

    转换规则（保守）：
      1. 若已含 ``.py``，认为已是路径形态，原样返回；
      2. 否则取 ``::`` 之前的部分，把点替换为 ``/`` 并补 ``.py``；
      3. ``classname`` 为空/不可用时退回 ``fallback_file``（体检给出的真实文件路径）。
    """
    text = str(node_id or "").strip()
    fallback = str(fallback_file or "").strip().replace("\\", "/")
    if not text:
        return fallback
    if "::" not in text:
        return text
    head, _, tail = text.partition("::")
    if not head or head.endswith(".py"):
        return text
    if "/" in head or "\\" in head:
        return text
    converted = head.replace(".", "/") + ".py"
    if fallback and not os.path.exists(fallback) and not converted:
        return text
    return f"{converted}::{tail}" if tail else converted


def _target_argv(ticket: RepairTicket, *, junit_path: str) -> Tuple[str, ...]:
    """目标用例的 pytest 命令（**带 junitxml**，以便精确判定 fail→pass）"""
    node = ""
    if ticket.failure is not None:
        node = node_target(ticket.failure.node_id, ticket.failure.file)
    if node:
        args: List[str] = [node]
    else:
        args = [ticket.failure.file] if (ticket.failure and ticket.failure.file) else []
    return pytest_argv(args, junit_path=junit_path)


def _adjacent_argv(targets: Sequence[str], *, junit_path: str) -> Tuple[str, ...]:
    return pytest_argv(list(targets), junit_path=junit_path)


def verify(request: VerifyRequest) -> VerificationReport:
    """隔离验证三关（步骤 4）

    流程：
      ① 复制仓库到临时目录；
      ② 在副本上跑**目标用例 before**（必须失败；否则判「基线不成立」，不放行）；
      ③ 应用补丁（失败 → 丢弃）；
      ④ 三关 after：目标用例（必须 pass）+ L0 锚（必须全过）+ 邻接回归（必须 pass）；
      ⑤ 清理副本。

    Returns:
        ``VerificationReport``（``ok=True`` 才允许产出 PR）。
    """
    policy = request.policy
    executor: ProbeExecutor = (request.executor if request.executor is not None
                               else SubprocessExecutor())
    ticket = request.ticket
    started = time.perf_counter()
    report = VerificationReport(diff_sha256=hashlib.sha256(
        request.patch.diff.encode("utf-8")).hexdigest())

    verify_root = ""
    cleanup_ok = True
    try:
        verify_root = prepare_isolated_copy(request.repo_root)
        report.temp_root = verify_root
        junit_dir = os.path.join(verify_root, ".repair_junit")
        os.makedirs(junit_dir, exist_ok=True)
        before_junit = os.path.join(junit_dir, "before.xml")
        after_junit = os.path.join(junit_dir, "target.xml")
        anchor_junit = os.path.join(junit_dir, "anchor.xml")
        adjacent_junit = os.path.join(junit_dir, "adjacent.xml")

        # ── ② 目标用例 before（必须失败）──
        before = _run_gate("target_before", argv=_target_argv(ticket, junit_path=before_junit),
                           cwd=verify_root, executor=executor,
                           timeout=float(request.timeout), junit_path=before_junit)
        report.target_failed_before = not before.effective_pass
        if not report.target_failed_before:
            report.target.passed = False
            report.target.exit_code = before.exit_code
            report.target.command = before.command
            report.target.output_summary = (
                "目标用例在**未打补丁**时即通过 —— 无法证明补丁修好了任何东西，"
                f"按不放行处理。\n{before.output_summary}")
            return report

        # ── ③ 应用补丁（只作用于临时副本）──
        applied = apply_unified_diff(request.patch.diff, verify_root)
        if not applied.ok:
            report.apply_error = applied.error
            report.target.passed = False
            report.target.command = "apply_unified_diff"
            report.target.output_summary = applied.error
            return report

        # ── ④ 三关 after ──
        report.target = _run_gate(
            "target", argv=_target_argv(ticket, junit_path=after_junit), cwd=verify_root,
            executor=executor, timeout=float(request.timeout), junit_path=after_junit)

        anchor_ok, anchor_detail = run_anchor(repo_root=verify_root)
        report.anchor = CheckResult(
            name="anchor", passed=(anchor_ok is True),
            command="agent.eval.runner.run_l0(reference=True)",
            exit_code=0 if anchor_ok is True else 1,
            output_summary=clean_text(str(anchor_detail), limit=2000),
            passed_count=int(anchor_detail.get("total", 0) or 0)
            if anchor_ok is True else 0,
            failed_count=int(anchor_detail.get("failures", 0) or 0),
            skipped=(anchor_ok is None),
            skip_reason="" if anchor_ok is not None else str(anchor_detail.get("error", "")))
        if anchor_ok is None:
            report.anchor.passed = False

        adjacent_targets = list(ticket.adjacent_tests)
        if adjacent_targets:
            report.adjacent = _run_gate(
                "adjacent", argv=_adjacent_argv(adjacent_targets, junit_path=adjacent_junit),
                cwd=verify_root, executor=executor, timeout=float(request.timeout),
                junit_path=adjacent_junit)
        else:
            report.adjacent = CheckResult(
                name="adjacent", passed=True, command="(无邻接测试候选)",
                exit_code=0, output_summary="改动文件未映射到任何测试文件（空集）。",
                skip_reason="无邻接测试候选（空集不算失败，但已如实标注）")

        report.ok = (report.target.effective_pass and report.anchor.effective_pass
                     and report.adjacent.effective_pass)
        return report
    except Exception as exc:  # noqa: BLE001 验证自身异常 → 不放行（fail-closed）
        report.ok = False
        report.apply_error = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        if verify_root and not request.keep_copy:
            cleanup_ok = cleanup_isolated_copy(verify_root)
        if request.run_logger is not None:
            request.run_logger.record_step(
                "verify", subject=ticket.ticket_id,
                status="ok" if report.ok else "error",
                detail={
                    "ok": report.ok,
                    "target_pass": report.target.effective_pass,
                    "target_failed_before": report.target_failed_before,
                    "anchor_pass": report.anchor.effective_pass,
                    "adjacent_pass": report.adjacent.effective_pass,
                    "adjacent_targets": ticket.adjacent_tests,
                    "apply_error": report.apply_error,
                    "diff_sha256": report.diff_sha256,
                    "copy_cleaned": bool(cleanup_ok),
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
                },
                error="" if report.ok else "至少一关未通过 → 补丁丢弃")


__all__ = [
    "EXCLUDED_DIRS", "EXCLUDED_SUFFIXES", "TAIL_LINES", "VerifyRequest",
    "prepare_isolated_copy", "cleanup_isolated_copy", "node_target", "verify",
]
