"""统一 CI 守卫入口: 检测 → 回滚模拟 → 守卫验证 全流程一键执行

编排(复用各模块核心逻辑, 不复制代码):
    1. detect     —— detect_reranker_changes.detect_changes()
    2. rollback   —— 基于检测结果模拟回滚(safe_git_revert 仅 dry-run, 不执行)
    3. guard      —— simulate_pr_merge_guard.run_guard()(verify + pytest)

输出统一格式: 文本(人类可读) / --json(结构化, CI 接入用)
退出码: 守卫 BLOCKED→1 / ALLOWED→0 / 检测失败→2

用法:
    python scripts/run_ci_guard.py                 # 全流程, 文本输出
    python scripts/run_ci_guard.py --json          # 结构化 JSON
    python scripts/run_ci_guard.py --skip-detect   # 只跑守卫
    python scripts/run_ci_guard.py --force-fail    # 注入失败验证守卫
"""

import argparse
import json
import sys
from datetime import datetime, timezone

# 运行 `python scripts/run_ci_guard.py` 时, scripts 目录自动在 sys.path[0]
from detect_reranker_changes import detect_changes          # noqa: E402
from simulate_pr_merge_guard import run_guard               # noqa: E402
from safe_git_revert import safe_revert                     # noqa: E402


def _step(step: str, status: str, exit_code: int, details: dict) -> dict:
    return {"step": step, "status": status, "exit_code": exit_code,
            "details": details}


def main() -> int:
    p = argparse.ArgumentParser(description="统一 CI 守卫: 检测→回滚模拟→守卫验证")
    p.add_argument("--json", action="store_true", help="结构化 JSON 输出")
    p.add_argument("--force-fail", action="store_true", help="注入守卫失败(验证守卫)")
    p.add_argument("--skip-detect", action="store_true", help="跳过检测与回滚模拟")
    p.add_argument("--pytest-args", default="", help="追加 pytest 参数")
    p.add_argument("--validate", action="store_true",
                   help="输出前用 ci_guard_types 运行时校验 JSON 契约")
    args = p.parse_args()

    steps: list[dict] = []

    # ── Step 1/3: 检测 ──
    if not args.skip_detect:
        det = detect_changes()
        steps.append(_step(
            "detect", "ok" if det["has_changes"] else "no_changes",
            0, {"branch": det["branch"], "base": det["base"],
                "dirty_related": det["dirty_related"],
                "committed": det["committed"],
                "commits": det["commits"]}))

        # ── Step 2/3: 回滚模拟(仅 dry-run) ──
        sim: dict = {"has_changes": det["has_changes"]}
        if not det["has_changes"]:
            sim["message"] = "无需回滚"
            sim["exit_code"] = 0
        elif det["commits"]:
            target = det["commits"][0].split()[0]  # 最近相关 commit
            rev = safe_revert(target, dry_run=True)
            sim["message"] = f"模拟回滚最近相关 commit: {det['commits'][0]}"
            sim["affected_files"] = rev["affected_files"]
            sim["exit_code"] = rev["exit_code"]
        else:
            sim["message"] = f"未提交改动 {len(det['dirty_related'])} 个文件, 回滚建议见 rollback_advice"
            sim["affected_files"] = det["dirty_related"]
            sim["exit_code"] = 0
        steps.append(_step("rollback_sim", "ok", sim["exit_code"], sim))

    # ── Step 3/3: 守卫验证 ──
    guard = run_guard(force_fail=args.force_fail, pytest_args=args.pytest_args,
                      verbose=not args.json)
    steps.append(_step(
        "guard_verify", guard["decision"].lower(), guard["exit_code"],
        {"checks": guard["checks"], "blocked_reasons": guard["blocked_reasons"]}))

    overall_exit = guard["exit_code"]

    report = {
        "tool": "run_ci_guard",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "overall": {"status": "pass" if overall_exit == 0 else "fail",
                    "exit_code": overall_exit},
    }

    # ── 输出前契约自检(OpenAPI 规范落地) ──
    # 提示走 stderr, 保持 stdout 纯净(JSON 模式可被下游直接解析)
    if args.validate:
        from ci_guard_types import validate_report
        errs = validate_report(report)
        if errs:
            for e in errs:
                print(f"::error::契约校验失败: {e}", file=sys.stderr)
            return 2
        print("::notice::契约校验通过(ci_guard_types.validate_report)",
              file=sys.stderr)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 64)
        print("统一 CI 守卫 run_ci_guard — 检测 → 回滚模拟 → 守卫验证")
        print("=" * 64)
        for s in steps:
            icon = "OK" if s["exit_code"] == 0 else "XX"
            print(f"\n[{icon}] {s['step']} ({s['status']}, exit={s['exit_code']})")
            d = s["details"]
            if s["step"] == "detect":
                print(f"  分支: {d['branch']} | base: {d['base']}")
                print(f"  未提交相关改动: {d['dirty_related'] or '无'}")
                print(f"  已提交相关改动: {d['committed'] or '无'}")
            elif s["step"] == "rollback_sim":
                print(f"  {d.get('message', '')}")
                if d.get("affected_files"):
                    print(f"  受影响文件: {len(d['affected_files'])} 个")
            elif s["step"] == "guard_verify":
                for c in d["checks"]:
                    print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']} "
                          f"(exit={c['exit_code']})")
                print(f"  决策: {guard['decision']}")
        print("\n" + "=" * 64)
        print(f"总体: {report['overall']['status'].upper()} "
              f"(exit={overall_exit})")
        if overall_exit != 0:
            print("守卫阻止合并 —— 需修复后重跑")
    return overall_exit


if __name__ == "__main__":
    sys.exit(main())
