"""PR 合并守卫模拟 —— verify 脚本 + 单元测试 双检查

供 run_ci_guard.py 的 guard_verify 步骤调用; 与 reranker-timeout-guard.yml
的两个执行步骤等价(verify 6 场景 + pytest 9 用例)。

返回契约(ci-guard-runner.yml 直接消费):
    {
        "decision": "allowed" | "blocked",
        "exit_code": 0 | 1,
        "checks": [{"name": str, "passed": bool, "exit_code": int}],
        "blocked_reasons": [str],
    }

用法:
    from simulate_pr_merge_guard import run_guard
    guard = run_guard()
    guard = run_guard(force_fail=True)     # 注入失败, 验证守卫自身
    guard = run_guard(pytest_args="-q")    # 追加 pytest 参数
"""

import os
import subprocess
import sys

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_VERIFY_SCRIPT = os.path.join("scripts", "verify_reranker_timeout_health.py")
_UNIT_TEST = os.path.join("tests", "unit", "test_reranker_utils.py")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=_PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")


def _check(name: str, cmd: list[str], verbose: bool) -> dict:
    """执行单条检查, 返回 {name, passed, exit_code}"""
    r = _run(cmd)
    passed = r.returncode == 0
    if verbose:
        tail = (r.stdout or r.stderr).strip().splitlines()
        print(f"[{'PASS' if passed else 'FAIL'}] {name} "
              f"(exit={r.returncode})")
        for line in tail[-3:]:
            print(f"    {line}")
    return {"name": name, "passed": passed, "exit_code": r.returncode}


def run_guard(force_fail: bool = False, pytest_args: str = "",
              verbose: bool = True) -> dict:
    """运行守卫双检查, 全过 → allowed, 任一失败 → blocked"""
    py = sys.executable

    checks = [
        _check("verify_reranker_timeout_health", [py, _VERIFY_SCRIPT], verbose),
    ]
    if not force_fail:
        cmd = [py, "-m", "pytest", _UNIT_TEST, "-q"]
        if pytest_args:
            cmd += pytest_args.split()
        checks.append(_check("unit_tests_reranker_utils", cmd, verbose))
    else:
        # force_fail: 注入失败检查, 验证守卫拦截路径本身
        checks.append({"name": "unit_tests_reranker_utils",
                       "passed": False, "exit_code": 1})

    failed = [c for c in checks if not c["passed"]]
    blocked_reasons = [f"{c['name']} 失败(exit={c['exit_code']})"
                       for c in failed] if failed else []

    decision = "allowed" if not blocked_reasons else "blocked"
    if verbose:
        print(f"守卫决策: {decision}"
              + (f" 原因: {blocked_reasons}" if blocked_reasons else ""))

    return {
        "decision": decision,
        "exit_code": 0 if decision == "allowed" else 1,
        "checks": checks,
        "blocked_reasons": blocked_reasons,
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="PR 合并守卫模拟(verify + pytest)")
    p.add_argument("--force-fail", action="store_true", help="注入失败验证守卫")
    p.add_argument("--pytest-args", default="", help="追加 pytest 参数")
    args = p.parse_args()
    result = run_guard(force_fail=args.force_fail, pytest_args=args.pytest_args)
    sys.exit(result["exit_code"])
