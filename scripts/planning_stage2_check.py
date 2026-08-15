#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云枢规划模块阶段 2 落地验证脚本（统一执行模型与规划闭环）

用途：
    验证阶段 2（D4 / D5 / D9 / D11 / D12）六项能力是否在代码中落地：

    1. 静态能力检测（S1–S8）：统一执行记录模型 / 反思闭环应用 / refine 激活 /
       并行执行 / 规划验证器 / 持久化与崩溃恢复 / LLM 输出鲁棒性 / 配置默认行为；
    2. 测试基线：阶段 2 专项测试（并行、验证器、持久化恢复、JSON 解析容错）+ 规划核心集成测试。

    与 scripts/planning_baseline_check.py 的区别：
        baseline_check 扫描"D1–D19 缺陷特征是否仍存在"（阶段 0 基线）；
        本脚本扫描"阶段 2 能力是否落地"（阶段 2 验收门禁）。

用法：
    python scripts/planning_stage2_check.py                      # 静态检测 + 跑阶段2测试
    python scripts/planning_stage2_check.py --static-only        # 仅静态能力检测（最快）
    python scripts/planning_stage2_check.py --output report.md   # 存档 Markdown 报告
    python scripts/planning_stage2_check.py --no-fail            # 检测失败不置退出码

退出码：
    0 = 静态能力全部 PASS 且（跳过测试或测试无失败）
    1 = 存在能力未落地（FAIL）或测试有失败（供 CI 门禁使用）

设计原则（三义）：
    【不易】只读扫描，不修改任何生产代码与测试；检测项与《阶段2_统一执行模型与规划闭环.md》
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

# 阶段 2 专项测试（并行/验证器/分解器/环检测/D5/D11 + 规划核心集成）
STAGE2_TEST_FILES = [
    "tests/unit/test_planning_stage2.py",
    "tests/unit/test_planning_executor.py",
    "tests/unit/test_planning_decomposer.py",
    "tests/unit/test_planning_cycle_detection.py",
    "tests/unit/test_planning_defect_d5.py",
    "tests/unit/test_planning_defect_d11.py",
    "tests/integration/test_planning_core.py",
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

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)


class Capabilities:
    """阶段 2 能力特征检测表（S1–S8，对应阶段 2 文档执行步骤 1–5）

    checks: list[(相对文件路径, [正则, ...])]，文件内所有正则命中 → 该文件 PASS；
            全部文件 PASS → 该能力 PASS。
    """

    S1_TITLE = "S1 统一执行记录模型（D4）"
    S1 = [
        ("planning/models/record.py", [
            r"thought: str = \"\"",
            r"observation: str = \"\"",
            r"reasoning: str = \"\"",
        ]),
        ("planning/core.py", [
            r"execution_history\.append\(ExecutionRecord\(",
        ]),
    ]

    S2_TITLE = "S2 反思闭环应用（D12：步骤级 _hints + 计划级 refine）"
    S2 = [
        ("planning/react.py", [
            r"context\.setdefault\(\"_hints\", \[\]\)",
        ]),
        ("planning/core.py", [
            r"decomposer\.refine\(plan, feedback\)",
        ]),
    ]

    S3_TITLE = "S3 refine() 首次激活（D4）"
    S3 = [
        ("planning/decomposer.py", [
            r"async def refine\(self, plan: Plan, feedback: str\)",
        ]),
        ("planning/core.py", [
            r"refine\(plan, feedback\)",
        ]),
    ]

    S4_TITLE = "S4 并行执行（D5：parallel_execution 开关 + asyncio.gather）"
    S4 = [
        ("planning/executor.py", [
            r"self\.parallel_execution = bool\(self\.config\.get\(\"parallel_execution\", False\)\)",
            r"if self\.parallel_execution and len\(next_tasks\) > 1",
            r"await asyncio\.gather\(",
        ]),
        ("planning/decomposer.py", [
            r"plan\.metadata\[\"parallel_groups\"\] = parallel_groups",
        ]),
    ]

    S5_TITLE = "S5 规划验证器（D11：依赖完整性/环检测/工具可用性/描述非空）"
    S5 = [
        ("planning/validator.py", [
            r"class PlanValidationError",
            r"def validate_plan\(",
            r"def validate_plan_or_raise\(",
        ]),
        ("planning/core.py", [
            r"validate_plan\(plan,",
        ]),
        ("planning/executor.py", [
            r"validate_plan_or_raise\(plan,",
        ]),
    ]

    S6_TITLE = "S6 计划持久化与崩溃恢复（D9：SQLite + 启动恢复未完成计划）"
    S6 = [
        ("planning/storage.py", [
            r"class PlanningStorage\(PlanDB\)",
            r"def load_unfinished_plans",
        ]),
        ("planning/core.py", [
            r"PlanningStorage\(",
            r"save_plan_checkpoint\(plan\)",
            r"load_unfinished_plans\(\)",
        ]),
    ]

    S7_TITLE = "S7 LLM 输出鲁棒性（markdown 围栏/裸 JSON/噪音剥离 + 重试）"
    S7 = [
        ("planning/llm_json.py", [
            r"def extract_json\(",
            r"async def extract_json_with_retry\(",
        ]),
        ("planning/decomposer.py", [
            r"from \.llm_json import extract_json, extract_json_with_retry",
        ]),
    ]

    S8_TITLE = "S8 配置默认行为守卫（parallel_execution 默认 false / 存储可关闭）"
    S8 = [
        ("planning/executor.py", [
            r"config\.get\(\"parallel_execution\", False\)",
        ]),
        ("planning/storage.py", [
            r"def is_enabled\(cls, planning_config",
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


def check_capability(s_id: str) -> Tuple[bool, str]:
    table = {
        "S1": (Capabilities.S1_TITLE, Capabilities.S1),
        "S2": (Capabilities.S2_TITLE, Capabilities.S2),
        "S3": (Capabilities.S3_TITLE, Capabilities.S3),
        "S4": (Capabilities.S4_TITLE, Capabilities.S4),
        "S5": (Capabilities.S5_TITLE, Capabilities.S5),
        "S6": (Capabilities.S6_TITLE, Capabilities.S6),
        "S7": (Capabilities.S7_TITLE, Capabilities.S7),
        "S8": (Capabilities.S8_TITLE, Capabilities.S8),
    }
    title, file_checks = table[s_id]
    ok, failed = _check_file_checks(file_checks)
    if ok:
        return True, f"{title}：能力已落地"
    return False, f"{title}：{'；'.join(failed)}"


# ──────────────────────────────────────────────────────────────────────────
# 测试基线
# ──────────────────────────────────────────────────────────────────────────


def run_stage2_tests() -> Tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", *STAGE2_TEST_FILES, "-q", "--tb=short"]
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
    print(f"云枢规划模块阶段 2 落地验证 @ {report['generated_at']}")
    print(f"工作目录: {report['root']}")
    print("=" * 72)
    print(f"[静态能力检测] {static['passed']}/{static['total']} PASS")
    for item in static["items"]:
        mark = colors.green("[PASS]") if item["ok"] else colors.red("[FAIL]")
        print(f"  {mark} {item['id']} {item['detail']}")
    if static["failed"]:
        print(colors.red("→ 存在能力未落地（FAIL），按《阶段2_统一执行模型与规划闭环.md》执行步骤补齐后重跑"))
    else:
        print(colors.green("→ S1–S8 全部通过：阶段 2 六项能力已落地"))

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
            print(colors.green("→ 阶段 2 专项测试全部通过（无回归）"))
    print("=" * 72)


def build_markdown(report: Dict) -> str:
    static = report["static"]
    lines = [
        "# 规划模块阶段 2 落地验证报告",
        "",
        f"> 由 `scripts/planning_stage2_check.py` 生成于 {report['generated_at']}",
        f"> 工作目录：`{report['root']}`",
        "",
        "## 一、静态能力检测（S1–S8）",
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
    lines += ["", "## 二、阶段 2 专项测试基线", ""]
    if report["static_only"]:
        lines.append("（--static-only 模式，未运行测试）")
    elif tests is None:
        lines.append("（未运行）")
    else:
        stats = tests.get("stats") or {}
        lines.append("- 命令：`python -m pytest " + " ".join(STAGE2_TEST_FILES) + " -q --tb=short`")
        lines.append(f"- 通过：{stats.get('passed', 0)}")
        lines.append(f"- 失败：{stats.get('failed', 0)}")
        lines.append(f"- 跳过：{stats.get('skipped', 0)}")
        lines.append(f"- 收集错误：{stats.get('errors', 0)}")
        lines.append(f"- 退出码：{tests.get('rc')}")
    lines += ["", "## 三、结论", ""]
    if static["failed"]:
        lines.append("存在能力未落地项，需按阶段 2 文档补齐后重跑本脚本。")
    else:
        lines.append("S1–S8 全部通过：阶段 2 六项能力已落地，可进入阶段 3/4（先过阶段2_3依赖检查清单）。")
    lines.append("")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="云枢规划模块阶段 2 落地验证（统一执行模型与规划闭环）",
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
    for s_id in [f"S{i}" for i in range(1, 9)]:
        ok, detail = check_capability(s_id)
        static_results.append({"id": s_id, "ok": ok, "detail": detail})

    test_result = None
    if not args.static_only:
        rc, output = run_stage2_tests()
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
