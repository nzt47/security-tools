#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云枢规划模块阶段 3 落地验证脚本（约束、预算与容错降级）

用途：
    验证阶段 3（D13 / D14 / D18）五类机制是否在代码中落地：

    1. 静态能力检测（T1–T6）：预算管理 / 任务级降级链 / 重规划 Plan B /
       协作式取消 / ask_user 恢复语义 / 失败归因落库；
    2. 测试基线：阶段 3 专项测试（预算超限、降级链、重规划、取消、ask_user 恢复/超时）。

    与 scripts/planning_stage2_check.py 的关系：阶段 3 依赖阶段 2（统一执行模型与持久化），
    进入阶段 3 前须通过 scripts/planning_stage2_check.py。

用法：
    python scripts/planning_stage3_check.py                      # 静态检测 + 跑阶段3测试
    python scripts/planning_stage3_check.py --static-only        # 仅静态能力检测（最快）
    python scripts/planning_stage3_check.py --output report.md   # 存档 Markdown 报告
    python scripts/planning_stage3_check.py --no-fail            # 检测失败不置退出码

退出码：
    0 = 静态能力全部 PASS 且（跳过测试或测试无失败）
    1 = 存在能力未落地（FAIL）或测试有失败（供 CI 门禁使用）

设计原则（三义）：
    【不易】只读扫描，不修改任何生产代码与测试；检测项与《阶段3_约束预算与容错降级.md》
            执行步骤一一对应，可追溯。
    【变易】检测模式集中在 CAPABILITIES 表，能力特征变化时只改表、不改检测逻辑。
    【简易】纯 Python 标准库，零第三方依赖；单文件即开即用。
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 阶段 3 专项测试（预算/降级/重规划/取消/ask_user + D13/D14/D18 复现测试）
STAGE3_TEST_FILES = [
    "tests/unit/test_planning_stage3.py",
    "tests/integration/test_planning_budget_degrade_concurrency.py",
    "tests/unit/test_planning_defect_d13.py",
    "tests/unit/test_planning_defect_d14.py",
    "tests/unit/test_planning_defect_d18.py",
]


class Colors:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def red(self, str_) -> str:
        return self._wrap("31", str_)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)


class Capabilities:
    """阶段 3 能力特征检测表（T1–T6，对应阶段 3 文档执行步骤 1–5）

    checks: list[(相对文件路径, [正则, ...])]，文件内所有正则命中 → 该文件 PASS；
            全部文件 PASS → 该能力 PASS。
    """

    T1_TITLE = "T1 预算管理（D13：PlanBudget + BudgetManager + 执行/反思双路接线）"
    T1 = [
        ("planning/budget.py", [
            r"class BudgetStatus\(Enum\)",
            r"class PlanBudget",
            r"class BudgetManager",
            r"def from_config\(cls, config",
            r"def check\(self\) -> BudgetStatus",
        ]),
        ("planning/executor.py", [
            r"BudgetManager\(",
        ]),
        ("planning/react.py", [
            r"self\.budget_manager = BudgetManager\(",
        ]),
        ("config.yaml", [
            r"^\s*budget:$",
            r"^\s*enabled:\s*true\s*$",
        ]),
    ]

    T2_TITLE = "T2 任务级降级链（D14：Task.fallback_actions + _try_degrade_chain）"
    T2 = [
        ("planning/models/task.py", [
            r"fallback_actions: List\[str\] = field\(default_factory=list\)",
        ]),
        ("planning/executor.py", [
            r"async def _try_degrade_chain\(self, action: Action\)",
            r"if task\.fallback_actions:",
        ]),
    ]

    T3_TITLE = "T3 重规划 Plan B（D14：高优先级失败 → refine 重规划而非直接中断）"
    T3 = [
        ("planning/executor.py", [
            r"async def _replan_on_failure\(self, plan: Plan, failed_task: Task\)",
            r"await self\._replan_on_failure\(plan, task\)",
        ]),
        ("config.yaml", [
            r"replan_on_failure:\s*true",
        ]),
    ]

    T4_TITLE = "T4 协作式取消（D18：per-plan 取消标志 + 工具调用前检查）"
    T4 = [
        ("planning/executor.py", [
            r"self\._cancelled_plan_ids: set = set\(\)",
            r"if plan\.id in self\._cancelled_plan_ids:",
            r"async def cancel_plan\(self, plan: Plan\)",
        ]),
        ("planning/core.py", [
            r"def cancel_plan\(self, plan_id: str\)",
        ]),
    ]

    T5_TITLE = "T5 ask_user 恢复语义（暂停/恢复/超时，接阶段 1 修复）"
    T5 = [
        ("planning/core.py", [
            r"def ask_user\(self, plan_id: str, question: str",
            r"async def resume_plan\(self, plan_id: str, user_answer: str\)",
        ]),
        ("planning/react.py", [
            r"awaiting_user_input",
        ]),
        ("config.yaml", [
            r"ask_user_timeout_seconds:\s*\d+",
        ]),
    ]

    T6_TITLE = "T6 失败归因落库（阶段 3 → 阶段 4 经验回灌的数据底座）"
    T6 = [
        ("planning/reflector.py", [
            r"async def learn_from_experience\(self, task_description: str, result: ActionResult\)",
        ]),
        ("planning/executor.py", [
            r"learn_from_experience\(",
        ]),
    ]


# ──────────────────────────────────────────────────────────────────────────
# 静态检测核心
# ──────────────────────────────────────────────────────────────────────────


def _read_file(path: str) -> Optional[str]:
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, "r", encoding="utf-8-sig") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _check_file_checks(file_checks: List[Tuple[str, List[str]]]) -> Tuple[bool, List[str]]:
    failed = []
    for rel_path, patterns in file_checks:
        text = _read_file(rel_path)
        if text is None:
            failed.append(f"文件缺失: {rel_path}")
            continue
        missed = [p for p in patterns if re.search(p, text, flags=re.MULTILINE) is None]
        if missed:
            failed.append(f"{rel_path} 未命中 {len(missed)} 个特征: {missed}")
    return (not failed), failed


def check_capability(t_id: str) -> Tuple[bool, str]:
    table = {
        "T1": (Capabilities.T1_TITLE, Capabilities.T1),
        "T2": (Capabilities.T2_TITLE, Capabilities.T2),
        "T3": (Capabilities.T3_TITLE, Capabilities.T3),
        "T4": (Capabilities.T4_TITLE, Capabilities.T4),
        "T5": (Capabilities.T5_TITLE, Capabilities.T5),
        "T6": (Capabilities.T6_TITLE, Capabilities.T6),
    }
    title, file_checks = table[t_id]
    ok, failed = _check_file_checks(file_checks)
    if ok:
        return True, f"{title}：能力已落地"
    return False, f"{title}：{'；'.join(failed)}"


# ──────────────────────────────────────────────────────────────────────────
# 测试基线
# ──────────────────────────────────────────────────────────────────────────


def run_stage3_tests() -> Tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", *STAGE3_TEST_FILES, "-q", "--tb=short"]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env,
        )
    except FileNotFoundError:
        return 2, f"pytest 未安装或不可执行: {sys.executable} -m pytest"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


_ERROR_FILE_RE = re.compile(r"^ERROR\s+([\w\\/.\-]+\.py)", re.MULTILINE)


def parse_test_stats(output: str) -> Dict[str, int]:
    stats = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    m = re.search(r"(\d+)\s+passed", output)
    if m:
        stats["passed"] = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", output)
    if m:
        stats["failed"] = int(m.group(1))
    m = re.search(r"(\d+)\s+skipped", output)
    if m:
        stats["skipped"] = int(m.group(1))
    m = re.search(r"(\d+)\s+errors?\b", output)
    if m:
        stats["errors"] = int(m.group(1))
    return stats


def extract_error_files(output: str) -> List[str]:
    seen: List[str] = []
    for f in _ERROR_FILE_RE.findall(output):
        f = f.replace("\\", "/")
        if f not in seen:
            seen.append(f)
    return seen[:10]


# ──────────────────────────────────────────────────────────────────────────
# 报告输出
# ──────────────────────────────────────────────────────────────────────────


def build_report(static_results: List[Dict], test_result: Optional[Dict],
                 static_only: bool) -> Dict:
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "root": ROOT,
        "stage": "3",
        "static": {
            "total": len(static_results),
            "passed": sum(1 for r in static_results if r["ok"]),
            "failed": sum(1 for r in static_results if not r["ok"]),
            "items": static_results,
        },
        "tests": test_result,
        "static_only": static_only,
    }


def print_terminal(report: Dict, colors: Colors) -> None:
    static = report["static"]
    print("=" * 72)
    print(f"云枢规划模块阶段 {report['stage']} 落地验证 @ {report['generated_at']}")
    print(f"工作目录: {report['root']}")
    print("=" * 72)
    print(f"[静态能力检测] {static['passed']}/{static['total']} PASS")
    for item in static["items"]:
        mark = colors.green("[PASS]") if item["ok"] else colors.red("[FAIL]")
        print(f"  {mark} {item['id']} {item['detail']}")
    if static["failed"]:
        print(colors.red("→ 存在能力未落地（FAIL），按《阶段3_约束预算与容错降级.md》执行步骤补齐后重跑"))
    else:
        print(colors.green("→ T1–T6 全部通过：阶段 3 五类机制已落地"))

    tests = report.get("tests")
    if report["static_only"]:
        print("[测试基线] 已跳过（--static-only）")
    elif tests is None:
        print("[测试基线] 未运行")
    else:
        stats = tests.get("stats") or {}
        print(f"[测试基线] passed={stats.get('passed', 0)} "
              f"failed={stats.get('failed', 0)} "
              f"skipped={stats.get('skipped', 0)} "
              f"errors={stats.get('errors', 0)} "
              f"| rc={tests.get('rc')}")
        if stats.get("failed"):
            print(colors.red("→ 测试存在失败，附最近输出（供排查）："))
            print("\n".join((tests.get("output") or "").splitlines()[-15:]))
        elif stats.get("errors"):
            print(colors.yellow("→ 存在收集期 ERROR（非用例失败），文件列表："))
            for f in tests.get("error_files") or []:
                print(f"    ERROR {f}")
        else:
            print(colors.green("→ 阶段 3 专项测试全部通过（无回归）"))
    print("=" * 72)


def build_markdown(report: Dict) -> str:
    static = report["static"]
    lines = [
        f"# 规划模块阶段 {report['stage']} 落地验证报告",
        "",
        f"> 由 `scripts/planning_stage{report['stage']}_check.py` 生成于 {report['generated_at']}",
        f"> 工作目录：`{report['root']}`",
        "",
        "## 一、静态能力检测（T1–T6）",
        "",
        f"通过 {static['passed']}/{static['total']}，失败 {static['failed']}。",
        "",
        "| 编号 | 结果 | 说明 |",
        "|------|------|------|",
    ]
    for item in static["items"]:
        mark = "✅ PASS" if item["ok"] else "❌ FAIL"
        lines.append(f"| {item['id']} | {mark} | {item['detail']} |")

    tests = report.get("tests")
    lines += ["", "## 二、阶段 3 专项测试基线", ""]
    if report["static_only"]:
        lines.append("（--static-only 模式，未运行测试）")
    elif tests is None:
        lines.append("（未运行）")
    else:
        stats = tests.get("stats") or {}
        lines.append("- 命令：`python -m pytest " + " ".join(STAGE3_TEST_FILES) + " -q --tb=short`")
        lines.append(f"- 通过：{stats.get('passed', 0)}")
        lines.append(f"- 失败：{stats.get('failed', 0)}")
        lines.append(f"- 跳过：{stats.get('skipped', 0)}")
        lines.append(f"- 收集错误：{stats.get('errors', 0)}")
        lines.append(f"- 退出码：{tests.get('rc')}")
    lines += ["", "## 三、结论", ""]
    if static["failed"]:
        lines.append("存在能力未落地项，需按阶段 3 文档补齐后重跑本脚本。")
    else:
        lines.append("T1–T6 全部通过：阶段 3 五类机制已落地，可进入阶段 5（先过阶段3_4并行推进与依赖检查清单）。")
    lines.append("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="云枢规划模块阶段 3 落地验证（约束、预算与容错降级）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--static-only", "--no-tests", dest="static_only", action="store_true",
        help="仅执行静态能力检测，不运行 pytest（最快）",
    )
    parser.add_argument(
        "--no-color", dest="no_color", action="store_true",
        help="禁用终端着色",
    )
    parser.add_argument(
        "--no-fail", dest="no_fail", action="store_true",
        help="检测失败时不置退出码 1（默认置 1，供 CI 门禁）",
    )
    parser.add_argument(
        "--ignore-collection-errors", dest="ignore_collection_errors", action="store_true",
        help="收集期 ERROR（非用例失败）不置退出码 1",
    )
    parser.add_argument(
        "--output", dest="output", metavar="FILE",
        help="将报告写入 Markdown 文件（扩展名 .md/.markdown）或 JSON（.json）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    colors = Colors((not args.no_color) and sys.stdout.isatty())

    static_results = []
    for t_id in [f"T{i}" for i in range(1, 7)]:
        ok, detail = check_capability(t_id)
        static_results.append({"id": t_id, "ok": ok, "detail": detail})

    test_result = None
    if not args.static_only:
        rc, output = run_stage3_tests()
        test_result = {
            "rc": rc,
            "output": output[-4000:],
            "stats": parse_test_stats(output),
            "error_files": extract_error_files(output),
        }

    report = build_report(static_results, test_result, args.static_only)
    print_terminal(report, colors)

    if args.output:
        out_path = args.output
        if not os.path.isabs(out_path):
            out_path = os.path.join(ROOT, out_path)
        content = (build_markdown(report) if not out_path.lower().endswith(".json")
                   else json.dumps(report, ensure_ascii=False, indent=2))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"报告已存档: {out_path}")

    if args.no_fail:
        return 0
    if report["static"]["failed"]:
        return 1
    if test_result is not None:
        stats = test_result.get("stats") or {}
        if stats.get("failed"):
            return 1
        if stats.get("errors") and not args.ignore_collection_errors:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
