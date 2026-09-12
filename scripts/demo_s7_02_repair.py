#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TASK-S7-02 端到端演示：注入真实小 bug → 体检 → 补丁 PR → 人工合入后 L0 通过

【这个演示想证明什么】
    任务书 §三 步骤 6 要求「人为注入一个真实小 bug → 跑 ``self_repair.py`` →
    产出补丁 PR → 人工合入后 L0 锚通过」。本脚本把这条链路做成**可重复执行的命令**，
    并留下机器可读的证据（``evidence.json``）。

【为什么演示跑在「沙箱副本」而不是本仓库】
    任务书 §八 已知坑 2：**绝不在主工作区或本会话 worktree 直接改文件**。故演示的
    第一步是把仓库复制成沙箱（``pipeline.build_sandbox_repo``），再往沙箱里注入 bug；
    交付物所在的工作区自始至终**一个字节都没被改**。

【真实与桩的边界（如实标注）】
    - **真实**：体检（真跑 pytest + junitxml）、定位（真读 git 历史 + 真读锚）、
      隔离验证三关（真复制仓库、真打补丁、真跑 pytest、真跑 L0 锚）、产出（真建
      本地分支、真写补丁与 PR 描述）、审计（真写 Trace + 真写链式审计并真校验）。
    - **桩**：**子代理的产出**。本环境没有外部 agent CLI 凭据（任务书 §八 已知坑 1
      明确「不要真调外部 agent CLI」），故用一个**固定 diff** 的通道桩代替子代理；
      它返回的补丁内容是"人类已经知道的那个修复"，其余机制一概不减免。
      这一条会在 ``evidence.json`` 的 ``stub_boundary`` 字段里如实写出。

【用法】::

    # ① 注入 bug + 跑完整流水线（产物：补丁 + PR 描述 + 报告 + 证据）
    python scripts/demo_s7_02_repair.py inject --workspace <沙箱路径> --artifacts <产物目录>

    # ② 人工合入（把补丁打到沙箱）+ 合入后 L0 锚复验（更新证据）
    python scripts/demo_s7_02_repair.py apply --workspace <沙箱路径> --artifacts <产物目录>

【退出码】0 = 演示链路成立；1 = 链路未成立（证据已写出，可据此排查）
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.repair.pipeline import (  # noqa: E402
    STATUS_PROPOSED,
    PipelineRequest,
    audit_completeness,
    report_markdown,
    run_repair,
)
from agent.repair.policy import RepairPolicy  # noqa: E402
from agent.subagent.channel import (  # noqa: E402
    ChannelExecutor,
    ChannelInvocation,
    RawOutput,
)
from repair_demo_support import build_sandbox_repo, git_commit_all  # noqa: E402

#: 演示用被测模块与测试文件（**沙箱内**新建，仓库本体不含这两个文件）
DEMO_MODULE = "agent/demo_s7_02.py"
DEMO_TEST = "tests/unit/test_demo_s7_02.py"

#: 注入的 bug：``increment`` 少加 1（off-by-one；改一行，语义明确、可机械判定）
MODULE_BUGGY = '''"""演示用被测模块（TASK-S7-02 沙箱；仓库本体不含此文件）"""


def increment(n: int) -> int:
    """把 n 加一后返回"""
    return n - 1          # ← 注入的 bug：应为 n + 1
'''

MODULE_FIXED = '''"""演示用被测模块（TASK-S7-02 沙箱；仓库本体不含此文件）"""


def increment(n: int) -> int:
    """把 n 加一后返回"""
    return n + 1
'''

TEST_CONTENT = '''"""演示用测试（TASK-S7-02 沙箱；仓库本体不含此文件）"""

from agent.demo_s7_02 import increment


def test_increment_positive():
    assert increment(1) == 2


def test_increment_zero():
    assert increment(0) == 1


def test_increment_negative():
    assert increment(-3) == -2
'''

EVIDENCE_NAME = "evidence.json"


def _write(path: str, text: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _fixed_diff() -> str:
    """人类已知的修复补丁（用 difflib 生成**真实可用**的统一 diff）"""
    body = list(difflib.unified_diff(
        MODULE_BUGGY.splitlines(keepends=True), MODULE_FIXED.splitlines(keepends=True),
        fromfile=f"a/{DEMO_MODULE}", tofile=f"b/{DEMO_MODULE}", n=3))
    header = (f"diff --git a/{DEMO_MODULE} b/{DEMO_MODULE}\n"
              f"--- a/{DEMO_MODULE}\n+++ b/{DEMO_MODULE}\n")
    return header + "".join(body)


class DemoSubagentChannel(ChannelExecutor):
    """演示用「子代理通道」桩：返回**人类已知的修复 diff**

    职责边界：它只模拟"子代理返回一份补丁文本"这一件事。补丁之后的一切
    （护栏、隔离验证、产出、审计）都走真实代码路径。

    继承 ``ChannelExecutor``（S4-04 的协议基类）而非裸类：这样"通道可注入"
    在类型层面也是成立的，而不是靠鸭子类型碰巧跑通。
    """

    def __init__(self, diff: str) -> None:
        self.diff = diff
        self.calls = 0

    def __call__(self, invocation: ChannelInvocation) -> RawOutput:
        self.calls += 1
        return RawOutput(stdout=json.dumps({
            "status": "done",
            "summary": "把 increment 的 off-by-one 改回 n + 1",
            "diff": self.diff,
            "rationale": (
                "increment(n) 的语义是 n 加一，但实现写成 `return n - 1`："
                "对 n=1 返回 0，与断言 increment(1) == 2 相差 2，属 off-by-one 的"
                "典型表现（同一个常量写错方向）。根因在单行实现，故最小修复就是把"
                "该行改回 `n + 1`；不涉及调用方与测试。"),
            "files": [DEMO_MODULE],
            "self_eval": {"verdict": "pass", "score": 0.9,
                          "summary": "单行修复，未触碰测试与只读区",
                          "issues": []},
            "input_tokens": 900,
            "output_tokens": 300,
            "cost_usd": 0.0,
            "test_assertion_changed": False,
        }, ensure_ascii=False), returncode=0)


def _load_evidence(artifacts: str) -> Dict[str, Any]:
    """读已有证据（不存在/损坏时返回空字典——``apply`` 阶段可独立执行）"""
    path = os.path.join(artifacts, EVIDENCE_NAME)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_evidence(artifacts: str, payload: Dict[str, Any]) -> str:
    return _write(os.path.join(artifacts, EVIDENCE_NAME),
                  json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _git_commit(root: str, message: str) -> bool:
    """提交沙箱改动（薄包装：日志提示 + 复用 ``repair_demo_support`` 的实现）"""
    ok = git_commit_all(root, message)
    if not ok:
        print(f"[warn] 沙箱内 git 提交失败：{message}")
    return ok


def cmd_inject(args: argparse.Namespace) -> int:
    """注入 bug → 跑完整流水线 → 写出证据（阶段 1）"""
    repo_root = os.path.abspath(args.repo_root)
    workspace = os.path.abspath(args.workspace)
    artifacts = os.path.abspath(args.artifacts)
    os.makedirs(artifacts, exist_ok=True)

    print(f"[1/6] 建立沙箱（源仓库只读）：{repo_root} → {workspace}")
    build_sandbox_repo(repo_root, workspace, overwrite=bool(args.force))

    print(f"[2/6] 注入真实小 bug：{DEMO_MODULE} 的 increment 少加 1")
    # 本仓库的 ``agent/`` 是**命名空间包**（无 ``__init__.py``）。测试通过
    # ``import agent.demo_s7_02`` 引用演示模块时，需要一个常规包来保证解析顺序
    # 与真实项目一致；故在**沙箱内**补一个空 ``__init__.py``（仓库本体不动）。
    _write(os.path.join(workspace, "agent", "__init__.py"), "")
    _write(os.path.join(workspace, DEMO_MODULE), MODULE_BUGGY)
    _write(os.path.join(workspace, DEMO_TEST), TEST_CONTENT)
    committed = _git_commit(workspace, "demo: 注入 off-by-one bug + 演示测试")
    print(f"      沙箱提交：{'成功' if committed else '失败（定位器将缺少最近改动证据）'}")

    print(f"[3/6] 跑自修复流水线（体检 → 定位 → 派工 → 验证 → 产出）")
    channel = DemoSubagentChannel(_fixed_diff())
    from agent.subagent.executor import DelegationExecutor

    # 注意：``agent_cli=""`` + 显式 channel ⇒ 走桩通道（本环境无外部 CLI 凭据）
    executor = DelegationExecutor(channel=channel, agent_cli="",
                                  workspace=os.path.join(artifacts, "subagent"))
    policy = RepairPolicy(max_rounds=1, budget_tokens=200000, timeout_seconds=600.0)
    request = PipelineRequest(
        repo_root=workspace, executor=executor, targets=(DEMO_TEST,), policy=policy,
        artifact_dir=artifacts, create_branch=True, with_anchor=True, with_audit=True,
        run_id="rep-demo-s702", events_dir=os.path.join(artifacts, "events"),
        trace_db=os.path.join(artifacts, "trace.db"))
    report = run_repair(request)
    print(report_markdown(report))
    ok, missing = audit_completeness(report)
    print(f"[审计完整性] {'通过' if ok else '缺失: ' + '/'.join(missing)}")

    evidence = _load_evidence(artifacts)
    evidence.update({
        "task": "TASK-S7-02 自动诊断与补丁 PR（自修复 L1）",
        "stage": "inject",
        "repo_root": repo_root,
        "workspace": workspace,
        "artifacts": artifacts,
        "injection": {
            "module": DEMO_MODULE,
            "test": DEMO_TEST,
            "bug": "increment(n) 实现写成 `return n - 1`（应为 `n + 1`）",
            "fix_diff": _fixed_diff(),
            "sandbox_commit_ok": bool(committed),
        },
        "stub_boundary": {
            "stubbed": "子代理的产出（固定 diff 通道桩）——本环境无外部 agent CLI 凭据",
            "real": ["体检(pytest+junitxml)", "定位(git 只读 + 锚 + L0 锚)",
                     "护栏(只读区/文件数/行数)", "隔离验证(临时副本 + 真实 pytest + 真实 L0 锚)",
                     "产出(本地分支 + 补丁 + PR 描述)", "审计(Trace + 链式审计 + verify_chain)"],
        },
        "pipeline": report.to_dict(),
        "produced": report.produced,
        "status": report.status,
        "audit_complete": bool(ok),
        "audit_missing_steps": list(missing),
        "proposal": report.proposal.to_dict() if report.proposal else None,
    })
    path = _save_evidence(artifacts, evidence)
    print(f"[4/6] 证据已写出：{path}")

    proposal = report.proposal
    if proposal is None:
        print("[FAIL] 本次未产出补丁 PR —— 演示链路未成立（详见上方报告）")
        return 1

    print(f"[5/6] 产物（**全部本地：未 push、未合并**）")
    print(f"      分支     ：{proposal.branch}（创建={proposal.created_branch}）")
    print(f"      补丁     ：{proposal.patch_path}")
    print(f"      PR 描述  ：{proposal.description_path}")
    print(f"      体检报告 ：{proposal.diagnose_path}")
    print(f"[6/6] 下一步（人工动作）：")
    print(f"      python scripts/demo_s7_02_repair.py apply "
          f"--workspace {workspace} --artifacts {artifacts}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """人工合入（把补丁打到沙箱）→ 合入后三关复验 → 更新证据（阶段 2）"""
    from agent.repair.diagnose import run_anchor
    from agent.repair.patchapply import apply_unified_diff

    workspace = os.path.abspath(args.workspace)
    artifacts = os.path.abspath(args.artifacts)
    evidence = _load_evidence(artifacts)
    patch_path = args.patch or (evidence.get("proposal") or {}).get("patch_path", "")
    if not patch_path or not os.path.isfile(patch_path):
        print(f"[FAIL] 找不到补丁文件：{patch_path or '(未记录)'}")
        return 1

    with open(patch_path, "r", encoding="utf-8") as fh:
        diff = fh.read()
    print(f"[1/4] 人工合入：把 {patch_path} 打到沙箱 {workspace}")
    result = apply_unified_diff(diff, workspace)
    print(f"      应用结果：ok={result.ok} applied={result.applied} err={result.error}")
    if not result.ok:
        evidence["merge"] = {"applied": False, "error": result.error}
        _save_evidence(artifacts, evidence)
        return 1
    _git_commit(workspace, "demo: 人工合入修复补丁（模拟人工 review 后合入）")

    print("[2/4] 合入后跑目标用例（真实 pytest）")
    target = _run_pytest(workspace, target=DEMO_TEST)
    print(f"      退出码 {target['exit_code']}｜{target['summary']}")

    print("[3/4] 合入后跑 L0 锚（系统不可写标尺，必须全过）")
    anchor_ok, anchor_detail = run_anchor(repo_root=workspace)
    print(f"      L0 锚：{anchor_ok}｜{anchor_detail}")

    print("[4/4] 复跑体检（应报告「无失败」）")
    from agent.repair.diagnose import SubprocessExecutor, diagnose
    diag = diagnose(repo_root=workspace, targets=(DEMO_TEST,),
                    executor=SubprocessExecutor(), with_anchor=True,
                    junit_path=os.path.join(artifacts, "junit_after_merge.xml"))
    print(f"      体检结论：{'未发现失败' if diag.ok else f'{diag.failure_count} 条失败'}")

    evidence["stage"] = "applied"
    evidence["merge"] = {
        "applied": True,
        "patch_path": patch_path,
        "applied_files": list(result.applied),
        "target_pytest": target,
        "anchor_ok": anchor_ok,
        "anchor_detail": anchor_detail,
        "diagnose_after_merge": diag.to_dict(),
        "demo_ok": bool(target["exit_code"] == 0 and anchor_ok is True and diag.ok),
    }
    path = _save_evidence(artifacts, evidence)
    print(f"[证据] {path}")
    if evidence["merge"]["demo_ok"]:
        print("[OK] 端到端演示成立：注入 bug → 产出补丁 PR → 人工合入后 L0 通过")
        return 0
    print("[FAIL] 合入后复验未通过 —— 详见 evidence.json")
    return 1


def _run_pytest(root: str, *, target: str) -> Dict[str, Any]:
    """在给定根内真跑一次 pytest（演示用；只读语义）"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    argv = ["python", "-m", "pytest", "-q", "--tb=short", "--no-header",
            "-p", "no:randomly", target]
    proc = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600, env=env)
    tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()][-3:]
    return {"command": " ".join(argv), "exit_code": int(proc.returncode),
            "summary": " / ".join(tail)[:300]}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TASK-S7-02 端到端演示（注入 bug → 补丁 PR → 人工合入 → L0 通过）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-root", default=os.getcwd(), help="源仓库根（只读）")
    common.add_argument("--workspace", default="", help="沙箱根（演示用工作区）")
    common.add_argument("--artifacts", default="", help="产物与证据目录")

    inj = sub.add_parser("inject", parents=[common], help="注入 bug 并跑完整流水线")
    inj.add_argument("--force", action="store_true", help="沙箱已存在则先删除")
    inj.set_defaults(func=cmd_inject)

    app = sub.add_parser("apply", parents=[common], help="人工合入补丁并复验")
    app.add_argument("--patch", default="", help="补丁路径（缺省取 evidence.json）")
    app.set_defaults(func=cmd_apply)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.workspace:
        args.workspace = os.path.join(os.path.abspath(args.repo_root),
                                      "data", "repair", "demo_workspace")
    if not args.artifacts:
        args.artifacts = os.path.join(os.path.abspath(args.repo_root),
                                      "data", "repair", "demo_artifacts")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
