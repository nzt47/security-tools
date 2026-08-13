#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云枢规划模块基线快速验证脚本（阶段 0 执行工具）

用途：
    快速验证 planning/ 规划模块的当前基线状态，产出可存档的基线报告：

    1. 静态缺陷特征扫描：D1–D19（与《docs/zh/规划模块重构计划/规划模块理想设计.md》
       第四节缺陷清单一一对应）。检测的是"缺陷特征是否存在"：
       - 修复前 → 特征存在 → FAIL（复现缺陷）
       - 修复后 → 特征不存在（或替换为修复标记）→ PASS（验证修复落地）
    2. 规划相关测试基线：`python -m pytest tests/unit tests/integration -k planning`，
       统计通过/失败/跳过与耗时，供阶段 1–5 对比回归。

用法：
    python scripts/planning_baseline_check.py                  # 静态扫描 + 跑测试
    python scripts/planning_baseline_check.py --static-only    # 仅静态扫描（最快）
    python scripts/planning_baseline_check.py --no-tests       # 同 --static-only
    python scripts/planning_baseline_check.py --output report.md   # 存档 Markdown 报告
    python scripts/planning_baseline_check.py --json report.json   # 存档 JSON 报告
    python scripts/planning_baseline_check.py --no-fail         # 检测失败不置退出码

退出码：
    0 = 静态扫描全部 PASS 且（跳过测试或测试无失败）
    1 = 存在缺陷特征（FAIL）或测试有失败（供 CI 门禁使用）

设计原则（三义）：
    【不易】只读扫描：不修改任何生产代码与测试；检测项与主文档缺陷清单一一对应。
    【变易】检测模式集中在 DEFECTS 表，缺陷特征变化时只改表、不改检测逻辑。
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

# ──────────────────────────────────────────────────────────────────────────
# 常量与路径
# ──────────────────────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_TEST_DIRS = ["tests/unit", "tests/integration"]
DEFAULT_TEST_KW = "planning"


class Colors:
    """终端着色（非 TTY 自动降级为无色，兼容 CI 管道）"""

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

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)


# ──────────────────────────────────────────────────────────────────────────
# 缺陷特征检测表（D1–D19）
#
# 每个缺陷项 = {id, title, desc, checks}
#   checks: list[(相对文件路径, [正则表达式, ...])]
#           一个文件内的所有正则都命中 → 该项文件级 PASS；
#           全部文件级 PASS → 该缺陷 PASS。
# 特殊项通过 mode 字段定制（如 D8/D19 需计数语义）。
# ──────────────────────────────────────────────────────────────────────────


class DefectChecks:
    """D1–D19 特征检测定义（特征变化只改这里，检测逻辑在下方 check_defect）"""

    D1_TITLE = "P0·D1 成功判定短路（is_success 过早要求 COMPLETED）"
    D1 = [
        ("planning/models/plan.py", [
            r"def is_success\(self,\s*consider_state: bool = True\)",
        ]),
        ("planning/executor.py", [
            r"plan\.is_success\(consider_state=False\)",
        ]),
    ]

    D2_TITLE = "P0·D2 ReAct 反思参数错误（step_reflect 传字符串）"
    D2 = [
        ("planning/react.py", [
            r"isinstance\(task, Task\)",
            r"Task\(\s*\n?\s*id=f\"react_step_",
        ]),
    ]

    D3_TITLE = "P0·D3 ask_user 伪实现（未真正等待用户）"
    D3 = [
        ("planning/react.py", [
            r"observation=[\"']awaiting_user_input[\"']",
            r"if awaiting_user:",
        ]),
    ]

    D4_TITLE = "D4 refine() 已实现但从未被调用"
    D4 = [
        ("planning/decomposer.py", [
            r"async def refine\(self, plan: Plan, feedback: str\)",
        ]),
        ("planning/core.py", [
            r"decomposer\.refine\(plan, feedback\)",
        ]),
    ]

    D5_TITLE = "D5 并行执行未实现（仅执行 next_tasks[0]）"
    D5 = [
        ("planning/executor.py", [
            r"asyncio\.gather",
            r"parallel_execution",
        ]),
        ("planning/decomposer.py", [
            r"plan\.metadata\[\"parallel_groups\"\]",
        ]),
    ]

    D6_TITLE = "D6 complexity_threshold 配置从未使用"
    D6 = [
        ("planning/core.py", [
            r"threshold = self\.config\.get\(\"complexity_threshold\", 1\.0\)",
        ]),
    ]

    D7_TITLE = "D7 规划引擎未接入聊天主链路（orchestrator 占位）"
    D7 = [
        ("agent/orchestrator/orchestrator.py", [
            r"_planner\.chat\(",
            r"_planning_enabled",
        ]),
        ("config.yaml", [
            r"^\s*enabled:\s*true\s*$",
        ]),
    ]

    D8_TITLE = "D8 双套规划器（task_planner 规则硬编码重复）"
    D8 = [
        ("agent/task_planner/planner.py", [
            r"薄壳|重导出|re-export",
        ]),
        ("planning/task_planner.py", [
            r"class TaskPlanner",
        ]),
    ]

    D9_TITLE = "D9 计划无持久化与崩溃恢复"
    D9 = [
        ("planning/storage.py", [
            r"class PlanningStorage\(PlanDB\)",
            r"def load_unfinished_plans",
        ]),
        ("planning/core.py", [
            r"PlanningStorage\(",
            r"save_plan_checkpoint",
        ]),
    ]

    D10_TITLE = "D10 中文工具匹配靠脆弱关键词表/硬编码句式"
    D10 = [
        ("planning/executor.py", [
            r"_TOOL_KEYWORDS_ZH",
        ]),
    ]

    D11_TITLE = "D11 无规划验证机制（坏计划执行期卡死）"
    D11 = [
        ("planning/validator.py", [
            r"class PlanValidationError",
            r"def validate_plan",
            r"def validate_plan_or_raise",
        ]),
        ("planning/core.py", [
            r"validate_plan\(plan",
        ]),
        ("planning/executor.py", [
            r"validate_plan_or_raise\(plan",
        ]),
    ]

    D12_TITLE = "D12 反思闭环断裂（adjustments 只打日志不应用）"
    D12 = [
        ("planning/react.py", [
            r"context\.setdefault\(\"_hints\", \[\]\)",
            r"_hints",
        ]),
    ]

    D13_TITLE = "D13 无预算/成本管理（无限迭代与超时）"
    D13 = [
        ("planning/budget.py", [
            r"class BudgetManager",
            r"class PlanBudget",
        ]),
        ("planning/executor.py", [
            r"BudgetManager\(",
        ]),
        ("planning/react.py", [
            r"BudgetManager\(",
        ]),
    ]

    D14_TITLE = "D14 无 Plan B / 降级链（失败即整体失败）"
    D14 = [
        ("planning/executor.py", [
            r"fallback_actions",
            r"degrade_chain",
            r"_replan_on_failure",
        ]),
    ]

    D15_TITLE = "D15 无结构化计划摘要（用户不可读）"
    D15 = [
        ("planning/models/plan.py", [
            r"def summarize\(self\)",
        ]),
        ("planning/core.py", [
            r"build_react_summary\(",
        ]),
    ]

    D16_TITLE = "D16 规划无可观测指标埋点"
    D16 = [
        ("planning/metrics.py", [
            r"class PlanningMetrics",
        ]),
        ("planning/core.py", [
            r"PlanningMetrics\(",
            r"planning_metrics\.record_plan_result",
        ]),
    ]

    D17_TITLE = "D17 经验只存不用（反思教训未回灌）"
    D17 = [
        ("planning/reflector.py", [
            r"def get_advice_for_task",
        ]),
        ("planning/react.py", [
            r"get_advice_for_task\(str\(task\)\)",
        ]),
        ("planning/decomposer.py", [
            r"get_advice_for_task\(task\)",
        ]),
    ]

    D18_TITLE = "D18 取消未真正中断（异步改状态不传播取消）"
    D18 = [
        ("planning/executor.py", [
            r"_cancelled_plan_ids",
            r"def cancel_plan",
        ]),
        ("planning/core.py", [
            r"def resume_plan",
        ]),
    ]

    D19_TITLE = "D19 重复定义/死代码（learn_from_experience 二义）"
    D19_FILE = "planning/reflector.py"
    D19_PATTERN = r"async def learn_from_experience"


# ──────────────────────────────────────────────────────────────────────────
# 静态扫描核心
# ──────────────────────────────────────────────────────────────────────────


def _read_file(path: str) -> Optional[str]:
    """读取文件（UTF-8，忽略 BOM；缺失/解码失败返回 None）"""
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, "r", encoding="utf-8-sig") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _match_all(text: str, patterns: List[str]) -> List[str]:
    """返回未命中的正则（空列表 = 全部命中）"""
    missed = []
    for pat in patterns:
        if re.search(pat, text, flags=re.MULTILINE) is None:
            missed.append(pat)
    return missed


def _check_file_checks(file_checks: List[Tuple[str, List[str]]]) -> Tuple[bool, List[str]]:
    """逐文件执行正则命中检测。

    Returns:
        (全部文件 PASS?, [失败描述, ...])
    """
    failed = []
    for rel_path, patterns in file_checks:
        text = _read_file(rel_path)
        if text is None:
            failed.append(f"文件缺失: {rel_path}")
            continue
        missed = _match_all(text, patterns)
        if missed:
            failed.append(f"{rel_path} 未命中 {len(missed)} 个特征: {missed}")
    return (not failed), failed


def check_defect(d_id: str) -> Tuple[bool, str]:
    """检测单个缺陷项（D1–D18 走通用表；D19 需计数语义）。

    Returns:
        (PASS?, 描述)
    """
    if d_id == "D19":
        text = _read_file(DefectChecks.D19_FILE)
        if text is None:
            return False, f"文件缺失: {DefectChecks.D19_FILE}"
        count = len(re.findall(DefectChecks.D19_PATTERN, text))
        if count == 1:
            return True, "learn_from_experience 定义 1 次（无重复定义）"
        return False, f"learn_from_experience 定义 {count} 次（期望 1 次，存在重复定义/死代码）"

    table = {
        "D1": (DefectChecks.D1_TITLE, DefectChecks.D1),
        "D2": (DefectChecks.D2_TITLE, DefectChecks.D2),
        "D3": (DefectChecks.D3_TITLE, DefectChecks.D3),
        "D4": (DefectChecks.D4_TITLE, DefectChecks.D4),
        "D5": (DefectChecks.D5_TITLE, DefectChecks.D5),
        "D6": (DefectChecks.D6_TITLE, DefectChecks.D6),
        "D7": (DefectChecks.D7_TITLE, DefectChecks.D7),
        "D8": (DefectChecks.D8_TITLE, DefectChecks.D8),
        "D9": (DefectChecks.D9_TITLE, DefectChecks.D9),
        "D10": (DefectChecks.D10_TITLE, DefectChecks.D10),
        "D11": (DefectChecks.D11_TITLE, DefectChecks.D11),
        "D12": (DefectChecks.D12_TITLE, DefectChecks.D12),
        "D13": (DefectChecks.D13_TITLE, DefectChecks.D13),
        "D14": (DefectChecks.D14_TITLE, DefectChecks.D14),
        "D15": (DefectChecks.D15_TITLE, DefectChecks.D15),
        "D16": (DefectChecks.D16_TITLE, DefectChecks.D16),
        "D17": (DefectChecks.D17_TITLE, DefectChecks.D17),
        "D18": (DefectChecks.D18_TITLE, DefectChecks.D18),
    }
    title, file_checks = table[d_id]
    ok, failed = _check_file_checks(file_checks)
    if ok:
        return True, f"{title}：修复特征已落地"
    return False, f"{title}：{'; '.join(failed)}"


# ──────────────────────────────────────────────────────────────────────────
# 测试基线
# ──────────────────────────────────────────────────────────────────────────


def run_planning_tests(color: bool) -> Tuple[int, str]:
    """运行规划相关测试，返回 (退出码, 原始输出摘要)。

    说明：--tb=short 控制失败堆栈长度；-q 汇总输出便于解析统计。
    不启用 -p no:cacheprovider，保留 .pytest_cache 供增量加速。
    """
    cmd = [
        sys.executable, "-m", "pytest",
        *DEFAULT_TEST_DIRS,
        "-k", DEFAULT_TEST_KW,
        "-q", "--tb=short",
    ]
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env,
        )
    except FileNotFoundError:
        return 2, f"pytest 未安装或不可执行: {sys.executable} -m pytest"
    combined = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, combined


_ERROR_FILE_RE = re.compile(r"^ERROR\s+([\w\\/.\-]+\.py)", re.MULTILINE)


def parse_test_stats(output: str) -> Dict[str, int]:
    """解析 pytest -q 汇总统计。

    兼容 '377 passed, 1 skipped, 2 errors in 87.3s'：
    - failed 与 errors 分开统计（ERROR 为收集期错误，非用例失败）；
    - 同时出现 failed+errors 时互不污染。
    """
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
    """提取收集期 ERROR 的文件名（去重、截断，供报告定位）"""
    seen: List[str] = []
    for f in _ERROR_FILE_RE.findall(output):
        f = f.replace("\\", "/")
        if f not in seen:
            seen.append(f)
    return seen[:10]


# ──────────────────────────────────────────────────────────────────────────
# 报告输出
# ──────────────────────────────────────────────────────────────────────────


def build_static_results() -> List[Dict]:
    """执行全部静态检测，返回 [{id, title, ok, detail}, ...]"""
    results = []
    for d_id in [f"D{i}" for i in range(1, 20)]:
        ok, detail = check_defect(d_id)
        results.append({"id": d_id, "ok": ok, "detail": detail})
    return results


def build_report(static_results: List[Dict], test_result: Optional[Dict],
                 static_only: bool) -> Dict:
    """组装结构化报告（供终端 / Markdown / JSON 三态输出）"""
    report = {
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
    return report


def print_terminal(report: Dict, colors: Colors) -> None:
    """终端文本输出"""
    static = report["static"]
    print("=" * 70)
    print(f"云枢规划模块基线检查 @ {report['generated_at']}")
    print(f"工作目录: {report['root']}")
    print("=" * 70)
    print(f"[静态缺陷特征扫描] {static['passed']}/{static['total']} PASS")
    for item in static["items"]:
        if item["ok"]:
            print(f"  {colors.green('[PASS]')} {item['id']} {item['detail']}")
        else:
            print(f"  {colors.red('[FAIL]')} {item['id']} {item['detail']}")
    if static["failed"]:
        print(colors.red(f"→ {static['failed']} 项缺陷特征仍存在（对应主文档缺陷清单，请按阶段修复）"))
    else:
        print(colors.green("→ D1–D19 全部通过：规划模块缺陷特征已全部落地修复"))

    tests = report.get("tests")
    if report["static_only"]:
        print(f"[测试基线] 已跳过（--static-only），需全量验证请去掉该参数")
    elif tests is None:
        print(f"[测试基线] 未运行")
    else:
        stats = tests.get("stats") or {}
        print(f"[测试基线] passed={stats.get('passed', 0)} "
              f"failed={stats.get('failed', 0)} "
              f"skipped={stats.get('skipped', 0)} "
              f"errors={stats.get('errors', 0)} "
              f"| 耗时={tests.get('duration_s', 0.0):.1f}s | rc={tests.get('rc')}")
        if stats.get("failed"):
            print(colors.red("→ 测试存在失败，附最近输出（供排查）："))
            tail = "\n".join((tests.get("output") or "").splitlines()[-15:])
            print(tail)
        elif stats.get("errors"):
            print(colors.yellow("→ 存在收集期 ERROR（非用例失败），文件列表："))
            for f in tests.get("error_files") or []:
                print(f"    ERROR {f}")
            print(colors.yellow("→ 若这些 ERROR 与规划模块无关（如无关模块陈旧测试），"
                                "可用 --ignore-collection-errors 放行；否则请先修复"))
        else:
            print(colors.green("→ 规划相关测试全部通过（无回归）"))
    print("=" * 70)


def build_markdown(report: Dict) -> str:
    """Markdown 报告（可直接存档到 docs/zh/规划模块重构计划/规划模块缺陷清单.md）"""
    static = report["static"]
    lines = [
        "# 规划模块基线检查报告",
        "",
        f"> 由 `scripts/planning_baseline_check.py` 生成于 {report['generated_at']}",
        f"> 工作目录：`{report['root']}`",
        "",
        "## 一、静态缺陷特征扫描（D1–D19）",
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
    lines += ["", "## 二、规划测试基线", ""]
    if report["static_only"]:
        lines.append("（--static-only 模式，未运行测试）")
    elif tests is None:
        lines.append("（未运行）")
    else:
        stats = tests.get("stats") or {}
        lines.append(
            f"- 命令：`python -m pytest {' '.join(DEFAULT_TEST_DIRS)} -k {DEFAULT_TEST_KW} -q --tb=short`"
        )
        lines.append(f"- 通过：{stats.get('passed', 0)}")
        lines.append(f"- 失败：{stats.get('failed', 0)}")
        lines.append(f"- 跳过：{stats.get('skipped', 0)}")
        lines.append(f"- 收集错误：{stats.get('errors', 0)}")
        if stats.get("errors"):
            lines.append("- 错误文件：")
            for f in tests.get("error_files") or []:
                lines.append(f"  - `{f}`（收集期 ERROR，非用例失败；若与规划模块无关可用 --ignore-collection-errors 放行）")
        lines.append(f"- 耗时：{tests.get('duration_s', 0.0):.1f}s")
        lines.append(f"- 退出码：{tests.get('rc')}")
    lines += [
        "",
        "## 三、结论",
        "",
    ]
    if static["failed"]:
        lines.append(f"仍有 {static['failed']} 项缺陷特征存在，需按对应阶段任务修复后重跑本脚本。")
    else:
        lines.append("D1–D19 缺陷特征全部通过，规划模块基线锁定，后续阶段可按依赖顺序推进。")
    lines.append("")
    return "\n".join(lines)


def build_json(report: Dict) -> str:
    """JSON 报告（供 CI 消费）"""
    return json.dumps(report, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="云枢规划模块基线快速验证（阶段 0）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--static-only", "--no-tests", dest="static_only", action="store_true",
        help="仅执行静态缺陷特征扫描，不运行 pytest（最快）",
    )
    parser.add_argument(
        "--no-color", dest="no_color", action="store_true",
        help="禁用终端着色（自动用于非 TTY）",
    )
    parser.add_argument(
        "--no-fail", dest="no_fail", action="store_true",
        help="静态扫描或测试失败时不置退出码 1（默认失败置 1，供 CI 门禁）",
    )
    parser.add_argument(
        "--ignore-collection-errors", dest="ignore_collection_errors", action="store_true",
        help="收集期 ERROR（非用例失败）不置退出码 1（用于无关模块陈旧测试的已知噪音）",
    )
    parser.add_argument(
        "--output", dest="output", metavar="FILE",
        help="将报告写入 Markdown 文件（扩展名 .md/.markdown）或 JSON（.json）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    color_enabled = (not args.no_color) and sys.stdout.isatty()
    colors = Colors(color_enabled)

    # 1) 静态扫描
    static_results = build_static_results()

    # 2) 测试基线
    test_result = None
    if not args.static_only:
        rc, output = run_planning_tests(color_enabled)
        m = re.search(r"in\s+([\d.]+)s", output)
        duration_s = float(m.group(1)) if m else 0.0
        test_result = {
            "rc": rc,
            "output": output[-4000:],
            "stats": parse_test_stats(output),
            "error_files": extract_error_files(output),
            "duration_s": duration_s,
        }

    report = build_report(static_results, test_result, args.static_only)
    print_terminal(report, colors)

    # 3) 可选存档
    if args.output:
        out_path = args.output
        if not os.path.isabs(out_path):
            out_path = os.path.join(ROOT, out_path)
        if out_path.lower().endswith((".json",)):
            content = build_json(report)
        else:
            content = build_markdown(report)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"报告已存档: {out_path}")

    # 4) 退出码判定
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
