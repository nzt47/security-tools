#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自修复 L1 入口（TASK-S7-02）：手动触发「体检 → 定位 → 派工 → 验证 → 产出补丁 PR」

【本脚本是什么 / 不是什么】
    是：**手动**触发的命令行入口 + 体检报告与补丁 PR 的产出。
    不是：自动触发（定时/告警驱动）、自动 push、自动合并、远端 PR —— 见 TASK-S7-02 §五。

    流水线本身在 ``agent/repair/pipeline.py``；本脚本只做三件事：解析参数、
    构造执行器（真实 CLI 可选 / 桩可注入）、打印报告。

【用法】::

    # 体检（只看有没有问题，不派工）
    python scripts/self_repair.py --repo-root . --diagnose-only

    # 完整流程（默认用桩执行器？**不是**——默认走真实 CLI 通道；
    # 无凭据环境请显式传 --channel stub 或在代码层注入执行器）
    python scripts/self_repair.py --repo-root . --test-target tests/unit/test_foo.py

    # 真实外部 agent CLI（§3.10）
    python scripts/self_repair.py --repo-root . --agent-cli claude --test-target tests/unit/test_foo.py

【已知坑（任务书 §八 #1）】
    **不要在 CI/无凭据环境真调外部 agent CLI**。``--channel stub`` 会用一个
    「明确不作答」的桩（返回空 diff），用来验证除"子代理输出"以外的全部机制；
    真实路径请在有凭据的机器上跑，并在报告里如实标注。

【退出码】
    0 = 未发现失败，或成功产出补丁 PR（产物全部本地）；
    1 = 有失败但未产出（丢弃/拒绝）——**不是错误**，是「如实报告未修复」；
    2 = 参数/环境错误（如仓库根不存在）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.repair.pipeline import (  # noqa: E402
    STATUS_ERROR,
    STATUS_NO_FAILURE,
    STATUS_PROPOSED,
    PipelineRequest,
    audit_completeness,
    report_markdown,
    run_repair,
)
from agent.repair.policy import policy_from_env  # noqa: E402
from agent.subagent.channel import (  # noqa: E402
    ChannelExecutor,
    ChannelInvocation,
    RawOutput,
)

EXIT_OK = 0
EXIT_NOT_REPAIRED = 1
EXIT_USAGE = 2


class _StubChannelExecutor(ChannelExecutor):
    """桩通道：**明确不作答**（返回空 diff），用于无凭据环境验证机制本身

    为什么不返回一个假的"看起来能过"的补丁：那会让验证三关变得可被伪造通过。
    返回空 diff 时流程会如实走到「子代理未产出补丁」并停在那里——这正是我们要
    在无凭据环境里验证的行为。
    """

    def __call__(self, invocation: ChannelInvocation) -> RawOutput:
        return RawOutput(stdout=json.dumps({
            "status": "blocked",
            "summary": "桩执行器：未配置真实 agent CLI/LLM（如实不作答）",
            "diff": "",
            "rationale": "",
            "files": [],
            "self_eval": {"verdict": "fail", "score": 0.0,
                          "summary": "无可用执行体", "issues": ["stub_channel"]},
        }, ensure_ascii=False), returncode=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="自修复 L1：自动诊断 + 补丁 PR（**不 push、不合并**）")
    parser.add_argument("--repo-root", default=".",
                        help="被体检/修复的仓库根（默认当前目录）")
    parser.add_argument("--test-target", action="append", default=[],
                        help="体检的 pytest 目标（可重复；默认一组冒烟用例）")
    parser.add_argument("--diagnose-only", action="store_true",
                        help="只体检（不派工、不产出）")
    parser.add_argument("--artifact-dir", default="",
                        help="产物目录（默认 <repo>/data/repair，已在 .gitignore 内）")
    parser.add_argument("--channel", choices=["auto", "stub"], default="auto",
                        help="auto = 真实 CLI/LLM 通道；stub = 明确不作答的桩")
    parser.add_argument("--agent-cli", default="",
                        help="外部 agent CLI（§3.10；缺省读 .env/常量）")
    parser.add_argument("--max-rounds", type=int, default=0,
                        help="覆盖最大轮次（0 = 用策略；由 CP_REPAIR_MAX_ROUNDS 亦可）")
    parser.add_argument("--budget-tokens", type=int, default=0,
                        help="覆盖 token 预算（0 = 用策略）")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="单个测试闸门超时（秒）")
    parser.add_argument("--no-anchor", action="store_true", help="体检不同跑 L0 锚")
    parser.add_argument("--with-audit", action="store_true", help="体检附带审计链快照")
    parser.add_argument("--no-branch", action="store_true", help="不创建本地产物分支")
    parser.add_argument("--events-dir", default="", help="事件流目录（缺省库）")
    parser.add_argument("--trace-db", default="", help="统一 Trace 库（缺省库）")
    parser.add_argument("--capability", default="", help="Trace/descriptor 查询能力名")
    parser.add_argument("--json-out", default="", help="把完整报告写出为 JSON")
    parser.add_argument("--print-md", action="store_true", help="打印 Markdown 摘要")
    return parser


def build_executor(args: argparse.Namespace):
    """构造派工执行器（真实通道 / 桩）"""
    from agent.subagent.executor import DelegationExecutor

    if args.channel == "stub":
        return DelegationExecutor(
            channel=_StubChannelExecutor(),
            workspace=os.path.join(args.repo_root, "data", "repair", "subagent"),
            max_concurrency=1)
    return DelegationExecutor(
        agent_cli=args.agent_cli,
        workspace=os.path.join(args.repo_root, "data", "repair", "subagent"),
        max_concurrency=1)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = os.path.abspath(args.repo_root)
    if not os.path.isdir(repo_root):
        print(f"[参数错误] 仓库根不存在：{repo_root}", file=sys.stderr)
        return EXIT_USAGE

    policy = policy_from_env()
    if args.max_rounds > 0 or args.budget_tokens > 0:
        from dataclasses import replace
        policy = replace(
            policy,
            max_rounds=args.max_rounds or policy.max_rounds,
            budget_tokens=args.budget_tokens or policy.budget_tokens)

    request = PipelineRequest(
        repo_root=repo_root, targets=tuple(args.test_target), policy=policy,
        executor=None if args.diagnose_only else build_executor(args),
        artifact_dir=args.artifact_dir, create_branch=not args.no_branch,
        with_anchor=not args.no_anchor, with_audit=bool(args.with_audit),
        timeout=float(args.timeout), events_dir=args.events_dir,
        trace_db=args.trace_db, capability=args.capability)

    report = run_repair(request)

    if args.json_out:
        parent = os.path.dirname(os.path.abspath(args.json_out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n")
        print(f"[OK] 报告已写出: {args.json_out}")

    print(report_markdown(report))
    ok, missing = audit_completeness(report)
    print(f"[审计完整性] {'通过' if ok else '缺失步骤: ' + '/'.join(missing)}")
    proposal = report.proposal
    if proposal is not None:
        print(f"[产物] 分支 {proposal.branch}｜补丁 {proposal.patch_path}")
        print(f"[产物] PR 描述 {proposal.description_path}")
        print("[边界] 未 push 远端、未合并 —— 合入请人工执行")

    if report.status == STATUS_NO_FAILURE or report.status == STATUS_PROPOSED:
        return EXIT_OK
    if report.status == STATUS_ERROR:
        return EXIT_NOT_REPAIRED
    return EXIT_NOT_REPAIRED


if __name__ == "__main__":
    raise SystemExit(main())
