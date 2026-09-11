"""本地模拟完整 CI 流水线（正式巡检工具）

按 CI(bash) 语义逐条执行三个相关 workflow 的核心命令：
  1. ci-guard-runner.yml      —— run_ci_guard.py --json + 退出码解析
  2. reranker-timeout-guard.yml —— verify 6 场景 + pytest 9 用例
  3. core-invariants-guard.yml —— verify_core_invariants.py --json

Why:
- 2026-08-05 run_ci_guard 事件复盘落地: 本地模拟需按 CI(bash) 语义执行,
  避免 PowerShell `>` 重定向(UTF-16)等环境差异产生"假失败/假绿"。
  参见 docs/observability/ci_hidden_failure_fix_report_20260805.md

用法:
    python scripts/simulate_ci_guard_pipeline.py
    python scripts/simulate_ci_guard_pipeline.py --json          # 结构化 JSON(供 CI 报告/看板消费)
    python scripts/simulate_ci_guard_pipeline.py --assert-allowed # 预提交钩子 CI_GUARD 段用

``--assert-allowed`` 语义（与 GH Actions「阻止 PR 合并」等价）::

    exit 0  → 判定链全部通过 ⇒ **允许**本次提交/合并
    非 0    → 任一守卫失败     ⇒ **阻止**（并在 stdout 给出被阻止的 workflow/step）

【不易】该标志**不改变**判定逻辑，只把"是否允许"这一判定显式化为进程退出码 —— 它存在的
原因见下：预提交钩子的 `CI_GUARD` 段自 2026-08 起引用了 `simulate_ci_guard_failure.py`
（该文件名在 git 历史中**从未存在**），且钩子设计为"脚本缺失时静默跳过（跨仓库安全）"，
于是该门禁长期**静默放过**、给人"已受保护"的错觉。2026-09-11（S3-01 收尾）复核发现：
① 引用名漂移；② 即便只改引用，本脚本原先不接受 `--assert-allowed` ⇒ 会因
"unrecognized arguments" 以 exit 2 退出，**反而阻断仓库全部提交**。故在此补齐该标志，
再同步钩子引用（两步缺一不可）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = sys.executable


def run(step: str, cmd: list[str], timeout: int = 600,
        quiet: bool = False) -> dict:
    """按 bash 语义执行(UTF-8 捕获), 返回结构化结果"""
    if not quiet:
        print(f"\n=== [{step}] {' '.join(cmd)} ===")
    try:
        p = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if not quiet:
            for stream, tag in ((p.stdout, "stdout"), (p.stderr, "stderr")):
                lines = [l for l in stream.splitlines() if l.strip()]
                for l in lines[-25:]:
                    print(f"  [{tag}] {l[:200]}")
            print(f"  -> exit={p.returncode}")
        return {"step": step, "exit_code": p.returncode,
                "stdout": p.stdout, "stderr": p.stderr}
    except subprocess.TimeoutExpired:
        if not quiet:
            print("  -> TIMEOUT")
        return {"step": step, "exit_code": -1, "stdout": "", "stderr": "TIMEOUT"}


def simulate() -> dict:
    results: list[dict] = []

    # ── 1. ci-guard-runner.yml ──
    r = run("ci-guard-runner: run_ci_guard --json", [
        PY, "scripts/run_ci_guard.py", "--json"], quiet=True)
    exit_code = r["exit_code"]
    overall = None
    if exit_code == 0:
        try:
            d = json.loads(r["stdout"])
            overall = d["overall"]
            exit_code = overall["exit_code"]
        except Exception as e:
            exit_code = 99
            r["stderr"] += f"\nJSON 解析失败: {e}"
    results.append({"workflow": "ci-guard-runner", "exit_code": exit_code,
                    "overall": overall})

    # ── 2. reranker-timeout-guard.yml ──
    results.append({"workflow": "reranker-timeout-guard", "steps": [
        run("verify 6 场景",
            [PY, "scripts/verify_reranker_timeout_health.py"], quiet=True),
        run("pytest 9 用例",
            [PY, "-m", "pytest", "tests/unit/test_reranker_utils.py", "-q"],
            quiet=True),
    ]})

    # ── 3. core-invariants-guard.yml ──
    results.append({"workflow": "core-invariants-guard", "steps": [
        run("verify_core_invariants --json",
            [PY, "scripts/verify_core_invariants.py", "--json"], quiet=True),
    ]})

    all_ok = all(
        (wf["exit_code"] == 0) if wf["workflow"] == "ci-guard-runner"
        else all(s["exit_code"] == 0 for s in wf["steps"])
        for wf in results)
    return {
        "tool": "simulate_ci_guard_pipeline",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workflows": results,
        "overall": {"status": "pass" if all_ok else "fail",
                    "exit_code": 0 if all_ok else 1},
    }


def _blocked_summary(report: dict) -> list[str]:
    """列出被阻止的 workflow/step（供 --assert-allowed 的阻断说明）"""
    blocked: list[str] = []
    for wf in report["workflows"]:
        if wf["workflow"] == "ci-guard-runner":
            if wf["exit_code"] != 0:
                blocked.append(f"ci-guard-runner (exit={wf['exit_code']})")
        else:
            for s in wf["steps"]:
                if s["exit_code"] != 0:
                    blocked.append(f"{wf['workflow']} / {s['step']} "
                                   f"(exit={s['exit_code']})")
    return blocked


def main() -> int:
    p = argparse.ArgumentParser(description="本地完整 CI 流水线模拟")
    p.add_argument("--json", action="store_true", help="输出结构化 JSON")
    p.add_argument(
        "--assert-allowed", action="store_true",
        help="预提交钩子 CI_GUARD 语义：断言判定链通过；"
             "exit 0=允许本次提交，非 0=阻止（并打印被阻止项）")
    args = p.parse_args()

    report = simulate()
    allowed = report["overall"]["exit_code"] == 0

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.assert_allowed:
        # 单行判定（钩子场景：成功静默、失败可定位）
        if allowed:
            print("[CI_GUARD] 判定链通过 → 允许提交")
        else:
            print("[CI_GUARD] 判定链未通过 → 阻止提交")
    else:
        print("\n" + "=" * 64)
        print("CI 流水线模拟汇总")
        print("=" * 64)
        for wf in report["workflows"]:
            if wf["workflow"] == "ci-guard-runner":
                ok = wf["exit_code"] == 0
                print(f"  ci-guard-runner: exit={wf['exit_code']} "
                      f"{'PASS' if ok else 'FAIL'}")
            else:
                for s in wf["steps"]:
                    ok = s["exit_code"] == 0
                    print(f"  {wf['workflow']} / {s['step']}: "
                          f"exit={s['exit_code']} {'PASS' if ok else 'FAIL'}")
        print(f"\n总体: {report['overall']['status'].upper()} "
              f"(exit={report['overall']['exit_code']})")

    if args.assert_allowed and not allowed:
        for item in _blocked_summary(report):
            print(f"  被阻止: {item}", file=sys.stderr)

    return report["overall"]["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
