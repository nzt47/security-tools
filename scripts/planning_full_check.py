#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云枢规划模块重构全量验收脚本（一次性运行四个阶段检查脚本并汇总报告）

用途：
    串联运行规划模块重构的全部检查脚本，输出一份合并验收报告：

      1. planning_baseline_check.py  阶段 0 基线（D1–D19 缺陷特征 + 规划测试）
      2. planning_stage2_check.py    阶段 2 能力（S1–S8）
      3. planning_stage3_check.py    阶段 3 能力（T1–T6）
      4. planning_stage4_check.py    阶段 4 能力（U1–U5）

    每个子脚本以 --output <临时>.json 产出结构化报告，本脚本解析后汇总
    （静态能力 PASS/FAIL、测试基线 passed/failed/skipped/errors、退出码），
    任一子脚本失败即整体退出码 1（供 CI 门禁 / 重构收尾验收使用）。

用法：
    python scripts/planning_full_check.py                       # 全量运行四个脚本
    python scripts/planning_full_check.py --static-only         # 全部仅静态检测（最快）
    python scripts/planning_full_check.py --skip stage3         # 跳过指定脚本（可多次）
    python scripts/planning_full_check.py --output report.md    # 汇总报告存档
    python scripts/planning_full_check.py --no-fail             # 失败不置退出码

退出码：
    0 = 四个脚本全部成功（静态 PASS + 测试无失败）
    1 = 任一脚本失败（静态 FAIL / 测试失败 / 脚本自身异常）

设计原则（三义）：
    【不易】只读串联：不修改任何生产代码/测试/子脚本；子脚本为唯一事实来源。
    【变易】脚本清单集中在 SCRIPTS 表；--skip 支持按需裁剪运行集。
    【简易】纯 Python 标准库；临时 JSON 用 tempfile 自动清理，不留残留。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 子脚本清单：(标识, 文件名, 阶段名, 能力前缀)
SCRIPTS = [
    ("baseline", "planning_baseline_check.py", "阶段 0 基线", "D"),
    ("stage2", "planning_stage2_check.py", "阶段 2 统一执行模型", "S"),
    ("stage3", "planning_stage3_check.py", "阶段 3 约束预算与容错", "T"),
    ("stage4", "planning_stage4_check.py", "阶段 4 经验回灌与观测", "U"),
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


def run_one(script_key: str, script_file: str, json_path: str,
            static_only: bool, no_color: bool, ignore_collection_errors: bool) -> Dict:
    """运行单个子脚本，返回其 JSON 报告（解析失败则返回异常记录）。"""
    cmd = [
        sys.executable, os.path.join(ROOT, "scripts", script_file),
        "--output", json_path,
    ]
    if static_only:
        cmd.append("--static-only")
    if no_color:
        cmd.append("--no-color")
    if ignore_collection_errors:
        cmd.append("--ignore-collection-errors")
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env,
        )
    except FileNotFoundError as e:
        return {"ok": False, "error": f"脚本执行失败: {e}", "rc": 127, "static": None, "tests": None}

    report = {"ok": False, "rc": proc.returncode, "static": None, "tests": None, "error": None}
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            report["static"] = data.get("static")
            report["tests"] = data.get("tests")
            report["static_only"] = data.get("static_only")
            report["generated_at"] = data.get("generated_at")
            report["ok"] = True
        except (OSError, ValueError) as e:
            report["error"] = f"JSON 解析失败: {e}"
    else:
        report["error"] = "子脚本未产出 JSON 报告"
    if proc.returncode != 0 and report["error"] is None:
        report["error"] = f"子脚本退出码 {proc.returncode}（含失败项）"
    return report


def summarize(report: Dict, prefix: str) -> Dict:
    """从子脚本 JSON 提取汇总行（静态通过数 / 测试统计）。"""
    static = report.get("static") or {}
    tests = report.get("tests") or {}
    stats = tests.get("stats") or {}
    return {
        "rc": report.get("rc"),
        "ok": report.get("ok"),
        "error": report.get("error"),
        "static_passed": static.get("passed", 0),
        "static_total": static.get("total", 0),
        "static_failed": static.get("failed", 0),
        "test_passed": stats.get("passed", 0),
        "test_failed": stats.get("failed", 0),
        "test_skipped": stats.get("skipped", 0),
        "test_errors": stats.get("errors", 0),
    }


def run_all(keys: List[str], static_only: bool, no_color: bool,
            ignore_collection_errors: bool) -> List[Dict]:
    """串联运行指定子脚本，返回各脚本汇总行列表。"""
    results = []
    with tempfile.TemporaryDirectory(prefix="planning_full_") as tmpdir:
        for key, script_file, stage_name, prefix in SCRIPTS:
            if key not in keys:
                continue
            print(f"▶ [{stage_name}] 运行 {script_file} ...")
            json_path = os.path.join(tmpdir, f"{key}.json")
            report = run_one(key, script_file, json_path, static_only, no_color,
                             ignore_collection_errors)
            results.append({
                "key": key,
                "script": script_file,
                "stage": stage_name,
                "prefix": prefix,
                **summarize(report, prefix),
            })
    return results


# ──────────────────────────────────────────────────────────────────────────
# 报告输出
# ──────────────────────────────────────────────────────────────────────────


def build_report(results: List[Dict], static_only: bool) -> Dict:
    total_failed = sum(1 for r in results if r.get("rc") != 0 or not r.get("ok"))
    static_failed = sum(1 for r in results if r.get("static_failed"))
    test_failed = sum(1 for r in results if r.get("test_failed"))
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "root": ROOT,
        "static_only": static_only,
        "summary": {
            "scripts_total": len(results),
            "scripts_failed": total_failed,
            "static_failed_items": static_failed,
            "test_failed_items": test_failed,
        },
        "results": results,
    }


def print_terminal(report: Dict, colors: Colors) -> None:
    summary = report["summary"]
    print("=" * 78)
    print(f"云枢规划模块重构全量验收 @ {report['generated_at']}")
    print(f"工作目录: {report['root']}")
    print("=" * 78)
    print(f"[合并统计] 脚本 {summary['scripts_total']} 个"
          f" | 失败 {summary['scripts_failed']} 个"
          f" | 静态失败项 {summary['static_failed_items']} 个"
          f" | 测试失败项 {summary['test_failed_items']} 个")
    print()
    header = f"{'脚本':<8}{'阶段':<22}{'静态':>8}{'测试通过':>10}{'失败':>6}{'跳过':>6}{'收集错误':>8}{'rc':>4}"
    print(header)
    print("-" * len(header))
    for r in report["results"]:
        mark = colors.green("[OK]") if (r.get("rc") == 0 and r.get("ok")) else colors.red("[FAIL]")
        static_str = f"{r['static_passed']}/{r['static_total']}"
        print(f"{r['key']:<8}{r['stage']:<22}"
              f"{static_str:>8}"
              f"{r['test_passed']:>10}{r['test_failed']:>6}{r['test_skipped']:>6}"
              f"{r['test_errors']:>8}{r['rc']:>4} {mark}")
        if r.get("error"):
            print(f"    └─ {colors.red(r['error'])}")
    print("-" * len(header))
    if summary["scripts_failed"] == 0 and summary["static_failed_items"] == 0 and summary["test_failed_items"] == 0:
        print(colors.green("→ 全部检查通过：规划模块重构（阶段 0/2/3/4）验收达成，可进入阶段 5（若未完成）或归档"))
    else:
        print(colors.red("→ 存在失败项，见上方各脚本明细；禁止在失败状态下归档/合并"))
    print("=" * 78)


def build_markdown(report: Dict) -> str:
    summary = report["summary"]
    lines = [
        "# 云枢规划模块重构全量验收报告",
        "",
        f"> 由 `scripts/planning_full_check.py` 生成于 {report['generated_at']}",
        f"> 工作目录：`{report['root']}`",
        "",
        "## 一、汇总",
        "",
        f"- 运行脚本：{summary['scripts_total']} 个（`planning_baseline_check` / `planning_stage2_check` / `planning_stage3_check` / `planning_stage4_check`）",
        f"- 失败脚本：{summary['scripts_failed']} 个",
        f"- 静态能力失败项：{summary['static_failed_items']} 个",
        f"- 测试失败项：{summary['test_failed_items']} 个",
        "",
        "| 脚本 | 阶段 | 静态能力 | 测试通过 | 测试失败 | 跳过 | 收集错误 | 退出码 | 结果 |",
        "|------|------|----------|----------|----------|------|----------|--------|------|",
    ]
    for r in report["results"]:
        ok = "✅ PASS" if (r.get("rc") == 0 and r.get("ok")) else "❌ FAIL"
        lines.append(
            f"| {r['key']} | {r['stage']} | {r['static_passed']}/{r['static_total']} "
            f"| {r['test_passed']} | {r['test_failed']} | {r['test_skipped']} "
            f"| {r['test_errors']} | {r['rc']} | {ok} |"
        )
        if r.get("error"):
            lines.append(f"| | | | | | | | 异常：{r['error']} |")
    lines += ["", "## 二、结论", ""]
    if summary["scripts_failed"] == 0 and summary["static_failed_items"] == 0 and summary["test_failed_items"] == 0:
        lines.append("全部检查通过：D1–D19 缺陷特征修复落地，阶段 2/3/4 能力齐备，规划模块重构验收达成。")
    else:
        lines.append("存在失败项，需按对应阶段文档补齐后重跑本脚本。")
    lines.append("")
    return "\n".join(lines)


def build_json(report: Dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="云枢规划模块重构全量验收（四个阶段检查脚本一键串联）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--static-only", dest="static_only", action="store_true",
        help="全部子脚本仅执行静态检测，不运行 pytest（最快）",
    )
    parser.add_argument(
        "--no-color", dest="no_color", action="store_true",
        help="禁用终端着色",
    )
    parser.add_argument(
        "--no-fail", dest="no_fail", action="store_true",
        help="失败时不置退出码 1（默认置 1，供 CI 门禁）",
    )
    parser.add_argument(
        "--ignore-collection-errors", dest="ignore_collection_errors", action="store_true",
        help="传递给子脚本：收集期 ERROR（非用例失败）不置退出码 1",
    )
    parser.add_argument(
        "--skip", dest="skip", action="append", default=[],
        help="跳过的脚本（baseline/stage2/stage3/stage4，可多次指定）",
    )
    parser.add_argument(
        "--output", dest="output", metavar="FILE",
        help="将汇总报告写入 Markdown 文件（扩展名 .md/.markdown）或 JSON（.json）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    colors = Colors((not args.no_color) and sys.stdout.isatty())

    valid_keys = {k for k, _, _, _ in SCRIPTS}
    skip = {s for s in args.skip}
    invalid = skip - valid_keys
    if invalid:
        print(f"错误: --skip 含未知脚本 {sorted(invalid)}（可选: {sorted(valid_keys)}）")
        return 2
    keys = [k for k, _, _, _ in SCRIPTS if k not in skip]
    if not keys:
        print("错误: 全部脚本均被跳过，无可运行项")
        return 2

    results = run_all(keys, args.static_only, args.no_color, args.ignore_collection_errors)
    report = build_report(results, args.static_only)
    print_terminal(report, colors)

    if args.output:
        out_path = args.output
        if not os.path.isabs(out_path):
            out_path = os.path.join(ROOT, out_path)
        content = (build_json(report) if out_path.lower().endswith(".json")
                   else build_markdown(report))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"报告已存档: {out_path}")

    if args.no_fail:
        return 0
    if any(r.get("rc") != 0 or not r.get("ok") for r in results):
        return 1
    if any(r.get("static_failed") or r.get("test_failed") for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
