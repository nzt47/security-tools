"""解析 pytest 输出与 coverage.json，生成测试报告摘要"""
import json
import re
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

ROOT = Path(__file__).parent.parent
COV_DIR = ROOT / "coverage_report"
OUTPUT_FILE = COV_DIR / "full_test_output.txt"
COVERAGE_JSON = COV_DIR / "coverage.json"
REPORT_FILE = COV_DIR / "test_summary.json"


def parse_pytest_output(text: str) -> dict:
    """解析 pytest 输出，提取通过/失败/跳过等统计

    支持两种顺序：
    - "= N passed, M failed, K skipped in X.Ys ="
    - "= N failed, M passed, K skipped in X.Ys ="
    """
    passed = failed = skipped = xfailed = xpassed = errors = warnings = 0
    duration_s = 0.0

    # 匹配各种状态计数（顺序无关）
    count_pat = re.compile(r"(\d+)\s+(passed|failed|skipped|xfailed|xpassed|warnings|errors?)")
    duration_pat = re.compile(r"in\s+([0-9.]+)s")

    # 严格匹配 pytest 总结行：必须以 === 开头并包含数字+passed
    summary_line_pat = re.compile(r"^=+\s*\d+\s+(passed|failed)")
    for line in text.splitlines():
        if summary_line_pat.match(line):
            for m in count_pat.finditer(line):
                cnt = int(m.group(1))
                kind = m.group(2)
                if kind == "passed":
                    passed = cnt
                elif kind == "failed":
                    failed = cnt
                elif kind == "skipped":
                    skipped = cnt
                elif kind == "xfailed":
                    xfailed = cnt
                elif kind == "xpassed":
                    xpassed = cnt
                elif kind.startswith("warning"):
                    warnings = cnt
                elif kind.startswith("error"):
                    errors = cnt
            dm = duration_pat.search(line)
            if dm:
                duration_s = float(dm.group(1))
            break

    # 收集失败用例
    failures = []
    fail_pat = re.compile(r"^(FAILED|ERROR)\s+(\S+)")
    for line in text.splitlines():
        m = fail_pat.match(line.strip())
        if m:
            failures.append({"status": m.group(1), "test_id": m.group(2)})

    # 按文件聚合通过情况
    per_file = {}
    file_pat = re.compile(r"^(tests/\S+\.py)\s+([.\sFEsxX]+)\s+\[\s*(\d+)%\]")
    for line in text.splitlines():
        m = file_pat.match(line.strip())
        if m:
            fp = m.group(1)
            dots = m.group(2)
            per_file[fp] = {
                "passed": dots.count("."),
                "failed": dots.count("F"),
                "error": dots.count("E"),
                "skipped": dots.count("s"),
                "xfailed": dots.count("x"),
                "xpassed": dots.count("X"),
                "progress": int(m.group(3)),
            }
            per_file[fp]["total"] = sum(per_file[fp][k] for k in
                                       ("passed", "failed", "error", "skipped", "xfailed", "xpassed"))

    total = passed + failed + skipped + xfailed + xpassed + errors
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "xfailed": xfailed,
        "xpassed": xpassed,
        "errors": errors,
        "warnings": warnings,
        "duration_s": round(duration_s, 2),
        "total": total,
        "pass_rate": round(passed / total * 100, 2) if total > 0 else 0,
        "failures": failures,
        "per_file": per_file,
    }


def parse_coverage_json(cov_data: dict) -> dict:
    """解析 coverage.json，生成总览与按模块统计"""
    files = cov_data.get("files", {})
    totals = cov_data.get("totals", {})

    per_file = {}
    for fp, data in files.items():
        norm = fp.replace("\\", "/")
        # coverage.json 的每个文件含 summary 子字段
        s = data.get("summary", {})
        per_file[norm] = {
            "statements": s.get("num_statements", 0),
            "missing": s.get("missing_lines", 0),
            "covered": s.get("covered_lines", 0),
            "num_branches": s.get("num_branches", 0),
            "missing_branches": s.get("num_missing_branches", 0),
            "covered_branches": s.get("covered_branches", 0),
            "percent_covered": round(s.get("percent_covered", 0), 2),
        }

    # 按顶级模块聚合
    per_module = defaultdict(lambda: {
        "statements": 0, "missing": 0, "covered": 0,
        "num_branches": 0, "missing_branches": 0, "covered_branches": 0,
        "files": 0,
    })
    for fp, d in per_file.items():
        parts = fp.split("/")
        if not parts:
            continue
        module = parts[0]
        per_module[module]["statements"] += d["statements"]
        per_module[module]["missing"] += d["missing"]
        per_module[module]["covered"] += d["covered"]
        per_module[module]["num_branches"] += d["num_branches"]
        per_module[module]["missing_branches"] += d["missing_branches"]
        per_module[module]["covered_branches"] += d["covered_branches"]
        per_module[module]["files"] += 1

    for module, d in per_module.items():
        d["percent_covered"] = round(d["covered"] / d["statements"] * 100, 2) if d["statements"] > 0 else 0
        d["percent_branch_covered"] = round(d["covered_branches"] / d["num_branches"] * 100, 2) if d["num_branches"] > 0 else 0

    return {
        "totals": {
            "statements": totals.get("num_statements", 0),
            "covered": totals.get("covered_lines", 0),
            "missing": totals.get("missing_lines", 0),
            "num_branches": totals.get("num_branches", 0),
            "covered_branches": totals.get("covered_branches", 0),
            "missing_branches": totals.get("missing_branches", 0),
            "percent_covered": round(totals.get("percent_covered", 0), 2),
            "percent_branches_covered": round(totals.get("percent_branches_covered", 0), 2),
        },
        "per_module": dict(per_module),
        "per_file": per_file,
    }


def main():
    if not OUTPUT_FILE.exists():
        print(f"错误：{OUTPUT_FILE} 不存在")
        sys.exit(1)

    text = OUTPUT_FILE.read_text(encoding="utf-8", errors="replace")
    test_stats = parse_pytest_output(text)

    cov_stats = None
    if COVERAGE_JSON.exists():
        cov_data = json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
        cov_stats = parse_coverage_json(cov_data)
    else:
        print(f"警告：{COVERAGE_JSON} 不存在")

    summary = {
        "generated_at": datetime.now().isoformat(),
        "test_stats": test_stats,
        "coverage_stats": cov_stats,
    }
    REPORT_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"测试摘要已保存到：{REPORT_FILE}")

    ts = test_stats
    print("\n" + "=" * 60)
    print("测试统计")
    print("=" * 60)
    print(f"总数: {ts['total']}")
    print(f"通过: {ts['passed']}")
    print(f"失败: {ts['failed']}")
    print(f"跳过: {ts['skipped']}")
    print(f"错误: {ts['errors']}")
    print(f"xfailed: {ts['xfailed']}")
    print(f"耗时: {ts['duration_s']} 秒")
    print(f"通过率: {ts['pass_rate']}%")

    if cov_stats:
        cs = cov_stats["totals"]
        print("\n" + "=" * 60)
        print("覆盖率统计")
        print("=" * 60)
        print(f"总语句数: {cs['statements']}")
        print(f"已覆盖: {cs['covered']}")
        print(f"未覆盖: {cs['missing']}")
        print(f"总分支数: {cs['num_branches']}")
        print(f"已覆盖分支: {cs['covered_branches']}")
        print(f"总覆盖率: {cs['percent_covered']}%")

        print("\n按模块统计：")
        print(f"{'模块':<12} {'文件数':<8} {'语句数':<10} {'覆盖率':<10} {'分支率':<10}")
        for module, d in sorted(cov_stats["per_module"].items()):
            print(f"{module:<12} {d['files']:<8} {d['statements']:<10} "
                  f"{d['percent_covered']}%   {d['percent_branch_covered']}%")


if __name__ == "__main__":
    main()
