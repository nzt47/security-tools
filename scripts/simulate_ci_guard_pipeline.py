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
    python scripts/simulate_ci_guard_pipeline.py --json   # 结构化 JSON(供 CI 报告/看板消费)
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


def main() -> int:
    p = argparse.ArgumentParser(description="本地完整 CI 流水线模拟")
    p.add_argument("--json", action="store_true", help="输出结构化 JSON")
    args = p.parse_args()

    report = simulate()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
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

    return report["overall"]["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
