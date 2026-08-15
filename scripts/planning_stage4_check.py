#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云枢规划模块阶段 4 落地验证脚本（经验回灌与可观测性）

用途：
    验证阶段 4（D15 / D16 / D17）四类能力是否在代码中落地：

    1. 静态能力检测（U1–U5）：经验回灌 / 规划指标埋点 / 结构化计划摘要 /
       规划质量评测基线 / 配置守卫与降级语义；
    2. 测试基线：阶段 4 专项测试（经验注入、埋点计数、summary 输出）+ D15/D16/D17 复现测试。

    与 scripts/planning_stage3_check.py 的关系：阶段 4 与阶段 3 相互独立（均依赖阶段 2），
    可并行推进；并行执行细则见《阶段3_4并行推进依赖检查清单.md》。

用法：
    python scripts/planning_stage4_check.py                      # 静态检测 + 跑阶段4测试
    python scripts/planning_stage4_check.py --static-only        # 仅静态能力检测（最快）
    python scripts/planning_stage4_check.py --output report.md   # 存档 Markdown 报告
    python scripts/planning_stage4_check.py --no-fail            # 检测失败不置退出码

退出码：
    0 = 静态能力全部 PASS 且（跳过测试或测试无失败）
    1 = 存在能力未落地（FAIL）或测试有失败（供 CI 门禁使用）

设计原则（三义）：
    【不易】只读扫描，不修改任何生产代码与测试；检测项与《阶段4_经验回灌与可观测性.md》
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

# 阶段 4 专项测试（经验注入/埋点/summary + D15/D16/D17 复现测试）
STAGE4_TEST_FILES = [
    "tests/unit/test_planning_stage4.py",
    "tests/unit/test_planning_defect_d15.py",
    "tests/unit/test_planning_defect_d16.py",
    "tests/unit/test_planning_defect_d17.py",
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
    """阶段 4 能力特征检测表（U1–U5，对应阶段 4 文档执行步骤 1–4）

    checks: list[(相对文件路径, [正则, ...])]，文件内所有正则命中 → 该文件 PASS；
            全部文件 PASS → 该能力 PASS。
    """

    U1_TITLE = "U1 经验回灌（D17：decompose/_think 检索注入 + _act 失败教训引导）"
    U1 = [
        ("planning/reflector.py", [
            r"def get_advice_for_task\(self, task_description: str\)",
        ]),
        ("planning/decomposer.py", [
            r"self\.reflector\.get_advice_for_task\(task\)",
        ]),
        ("planning/react.py", [
            r"self\.reflector\.get_advice_for_task\(str\(task\)\)",
            r"context\.get\(\"_next_hint\"\)",
            r"def _write_lesson_hint\(self, context: Dict, thought: ThoughtResult, tool_name: str\)",
        ]),
    ]

    U2_TITLE = "U2 规划指标埋点（D16：planning.* 指标 + get_planning_metrics 汇总）"
    U2 = [
        ("planning/metrics.py", [
            r"class PlanningMetrics",
            r"def record_plan_result\(",
            r"def record_experience_lookup\(",
            r"def get_metrics\(self\) -> Dict",
        ]),
        ("planning/core.py", [
            r"PlanningMetrics\(",
            r"self\.config\.get\(\"metrics\", \{\}\)\.get\(\"enabled\", True\)",
            r"def get_planning_metrics\(self\) -> Dict",
        ]),
    ]

    U3_TITLE = "U3 结构化计划摘要（D15：build_plan_summary / build_react_summary + ChatResult.plan_summary）"
    U3 = [
        ("planning/summary.py", [
            r"def build_plan_summary\(plan: Plan",
            r"def build_react_summary\(message: str, react_result\)",
        ]),
        ("planning/core.py", [
            r"plan_summary: Optional\[Dict\] = None",
            r"build_react_summary\(message, react_result\)",
            r"\"plan_summary\": self\.plan_summary,",
        ]),
    ]

    U4_TITLE = "U4 规划质量评测基线（eval 脚本 + 评测集 + 基线存档）"
    U4 = [
        ("scripts/eval_planning.py", [
            r"def main\(|if __name__ == \"__main__\"",
        ]),
        ("tests/eval/planning_eval_set.py", [
            r"EVAL_CASES|PLANNING_EVAL|eval_cases",
        ]),
        ("docs/zh/规划模块重构计划/规划评测基线.md", []),
    ]

    U5_TITLE = "U5 配置守卫与降级语义（metrics 可关、经验查询失败静默降级、回滚开关）"
    U5 = [
        ("planning/metrics.py", [
            r"self\.enabled = bool\(enabled\)",
            r"if not self\.enabled:",
        ]),
        ("planning/react.py", [
            r"logger\.warning\(f\"\[D17\] 获取历史经验失败",
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


def check_capability(u_id: str) -> Tuple[bool, str]:
    table = {
        "U1": (Capabilities.U1_TITLE, Capabilities.U1),
        "U2": (Capabilities.U2_TITLE, Capabilities.U2),
        "U3": (Capabilities.U3_TITLE, Capabilities.U3),
        "U4": (Capabilities.U4_TITLE, Capabilities.U4),
        "U5": (Capabilities.U5_TITLE, Capabilities.U5),
    }
    title, file_checks = table[u_id]
    ok, failed = _check_file_checks(file_checks)
    if ok:
        return True, f"{title}：能力已落地"
    return False, f"{title}：{'；'.join(failed)}"


# ──────────────────────────────────────────────────────────────────────────
# 测试基线
# ──────────────────────────────────────────────────────────────────────────


def run_stage4_tests() -> Tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", *STAGE4_TEST_FILES, "-q", "--tb=short"]
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
        "stage": "4",
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
        print(colors.red("→ 存在能力未落地（FAIL），按《阶段4_经验回灌与可观测性.md》执行步骤补齐后重跑"))
    else:
        print(colors.green("→ U1–U5 全部通过：阶段 4 四类能力已落地"))

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
            print(colors.green("→ 阶段 4 专项测试全部通过（无回归）"))
    print("=" * 72)


def build_markdown(report: Dict) -> str:
    static = report["static"]
    lines = [
        f"# 规划模块阶段 {report['stage']} 落地验证报告",
        "",
        f"> 由 `scripts/planning_stage{report['stage']}_check.py` 生成于 {report['generated_at']}",
        f"> 工作目录：`{report['root']}`",
        "",
        "## 一、静态能力检测（U1–U5）",
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
    lines += ["", "## 二、阶段 4 专项测试基线", ""]
    if report["static_only"]:
        lines.append("（--static-only 模式，未运行测试）")
    elif tests is None:
        lines.append("（未运行）")
    else:
        stats = tests.get("stats") or {}
        lines.append("- 命令：`python -m pytest " + " ".join(STAGE4_TEST_FILES) + " -q --tb=short`")
        lines.append(f"- 通过：{stats.get('passed', 0)}")
        lines.append(f"- 失败：{stats.get('failed', 0)}")
        lines.append(f"- 跳过：{stats.get('skipped', 0)}")
        lines.append(f"- 收集错误：{stats.get('errors', 0)}")
        lines.append(f"- 退出码：{tests.get('rc')}")
    lines += ["", "## 三、结论", ""]
    if static["failed"]:
        lines.append("存在能力未落地项，需按阶段 4 文档补齐后重跑本脚本。")
    else:
        lines.append("U1–U5 全部通过：阶段 4 四类能力已落地，可进入阶段 5（先过阶段3_4并行推进依赖检查清单）。")
    lines.append("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="云枢规划模块阶段 4 落地验证（经验回灌与可观测性）",
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
    for u_id in [f"U{i}" for i in range(1, 6)]:
        ok, detail = check_capability(u_id)
        static_results.append({"id": u_id, "ok": ok, "detail": detail})

    test_result = None
    if not args.static_only:
        rc, output = run_stage4_tests()
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
