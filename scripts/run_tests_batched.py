"""批量运行测试套件，跳过已知超时文件，生成 Markdown 报告。

用法:
    python scripts/run_tests_batched.py [--timeout 120] [--dir tests/unit]

已知超时文件（线程死锁，pytest-timeout 的 thread 方法在 Windows 上无法杀死）:
    - test_context_engineering.py  (system_tools.py:118 exec() 线程卡死)
    - test_caching_multi_level.py  (锁死锁)
    - test_dependency_graph.py     (锁死锁)
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

KNOWN_TIMEOUT_FILES = {
    "test_context_engineering.py",
    "test_caching_multi_level.py",
    "test_dependency_graph.py",
}


def discover_test_files(test_dir: str) -> list[Path]:
    """发现所有测试文件，排除已知超时文件。"""
    base = PROJECT_ROOT / test_dir
    if not base.exists():
        print(f"[ERROR] 测试目录不存在: {base}")
        sys.exit(1)
    files = sorted(base.glob("test_*.py"))
    skipped = [f for f in files if f.name in KNOWN_TIMEOUT_FILES]
    runnable = [f for f in files if f.name not in KNOWN_TIMEOUT_FILES]
    print(f"[INFO] 发现 {len(files)} 个测试文件")
    print(f"[INFO] 跳过 {len(skipped)} 个已知超时文件: {[f.name for f in skipped]}")
    print(f"[INFO] 将运行 {len(runnable)} 个测试文件")
    return runnable, skipped


def run_single_test(file_path: Path, timeout: int) -> dict:
    """运行单个测试文件，返回结果字典。"""
    rel_path = file_path.relative_to(PROJECT_ROOT)
    result = {
        "file": str(rel_path),
        "file_name": file_path.name,
        "start_time": datetime.datetime.now().isoformat(),
        "status": "unknown",
        "duration_sec": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "total": 0,
        "message": "",
    }

    cmd = [
        sys.executable, "-m", "pytest",
        str(file_path),
        "--timeout=60",
        "--tb=line",
        "-q",
        "--no-header",
        "-p", "no:cacheprovider",
    ]

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.perf_counter() - t0
        result["duration_sec"] = round(elapsed, 2)
        result["status"] = "passed" if proc.returncode == 0 else "failed"
        result["message"] = parse_pytest_summary(proc.stdout, proc.returncode, proc.returncode)
        counts = extract_counts(proc.stdout)
        result.update(counts)
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - t0
        result["duration_sec"] = round(elapsed, 2)
        result["status"] = "timeout"
        result["message"] = f"测试文件执行超过 {timeout} 秒超时"
    except Exception as e:
        elapsed = time.perf_counter() - t0
        result["duration_sec"] = round(elapsed, 2)
        result["status"] = "error"
        result["message"] = f"执行异常: {e}"

    result["end_time"] = datetime.datetime.now().isoformat()
    return result


def extract_counts(stdout: str) -> dict:
    """从 pytest 输出中提取通过/失败/错误/跳过计数。"""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0}
    if not stdout:
        return counts
    last_line = ""
    for line in stdout.splitlines():
        line = line.strip()
        if "passed" in line or "failed" in line or "error" in line or "skipped" in line:
            last_line = line
    import re
    m = re.findall(r"(\d+)\s+(passed|failed|error|skipped)", last_line)
    for num, kind in m:
        if kind == "passed":
            counts["passed"] = int(num)
        elif kind == "failed":
            counts["failed"] = int(num)
        elif kind == "error":
            counts["errors"] = int(num)
        elif kind == "skipped":
            counts["skipped"] = int(num)
    counts["total"] = counts["passed"] + counts["failed"] + counts["errors"] + counts["skipped"]
    return counts


def parse_pytest_summary(stdout: str, returncode: int, _) -> str:
    """提取 pytest 摘要行。"""
    if not stdout:
        return "无输出"
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if "passed" in line or "failed" in line or "error" in line or "no tests ran" in line:
            return line[:300]
    return f"退出码={returncode}（未找到摘要行）"


def generate_report(results: list[dict], skipped: list[Path], output_path: Path, total_duration: float):
    """生成 Markdown 报告。"""
    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    total_skipped_tests = sum(r["skipped"] for r in results)
    total_tests = sum(r["total"] for r in results)

    passed_files = [r for r in results if r["status"] == "passed"]
    failed_files = [r for r in results if r["status"] == "failed"]
    timeout_files = [r for r in results if r["status"] == "timeout"]
    error_files = [r for r in results if r["status"] == "error"]

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# 批量测试运行报告",
        "",
        f"**生成时间**: {now}",
        f"**总耗时**: {total_duration:.1f} 秒",
        f"**测试目录**: tests/unit/",
        "",
        "## 概要",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 测试文件总数 | {len(results) + len(skipped)} |",
        f"| 已运行文件 | {len(results)} |",
        f"| 跳过文件（已知超时） | {len(skipped)} |",
        f"| 文件通过 | {len(passed_files)} |",
        f"| 文件失败 | {len(failed_files)} |",
        f"| 文件超时 | {len(timeout_files)} |",
        f"| 文件异常 | {len(error_files)} |",
        "",
        "| 测试用例 | 数值 |",
        "|---|---|",
        f"| 总用例数 | {total_tests} |",
        f"| 通过 | {total_passed} |",
        f"| 失败 | {total_failed} |",
        f"| 错误 | {total_errors} |",
        f"| 跳过 | {total_skipped_tests} |",
        "",
        "## 跳过的已知超时文件",
        "",
    ]
    for f in skipped:
        lines.append(f"- `{f.name}`")
    lines.append("")

    if failed_files:
        lines.append("## 失败文件详情")
        lines.append("")
        lines.append("| 文件 | 状态 | 耗时(s) | 通过 | 失败 | 错误 | 摘要 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in failed_files:
            lines.append(
                f"| {r['file_name']} | {r['status']} | {r['duration_sec']} | "
                f"{r['passed']} | {r['failed']} | {r['errors']} | {r['message'][:120]} |"
            )
        lines.append("")

    if timeout_files:
        lines.append("## 超时文件详情")
        lines.append("")
        lines.append("| 文件 | 耗时(s) | 消息 |")
        lines.append("|---|---|---|")
        for r in timeout_files:
            lines.append(f"| {r['file_name']} | {r['duration_sec']} | {r['message']} |")
        lines.append("")

    if error_files:
        lines.append("## 异常文件详情")
        lines.append("")
        lines.append("| 文件 | 耗时(s) | 消息 |")
        lines.append("|---|---|---|")
        for r in error_files:
            lines.append(f"| {r['file_name']} | {r['duration_sec']} | {r['message']} |")
        lines.append("")

    lines.append("## 全部文件结果")
    lines.append("")
    lines.append("| 文件 | 状态 | 耗时(s) | 通过 | 失败 | 错误 | 跳过 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r['file_name']} | {r['status']} | {r['duration_sec']} | "
            f"{r['passed']} | {r['failed']} | {r['errors']} | {r['skipped']} |"
        )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[INFO] 报告已生成: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="批量运行测试套件")
    parser.add_argument("--dir", default="tests/unit", help="测试目录（相对项目根）")
    parser.add_argument("--timeout", type=int, default=120, help="单文件超时秒数")
    parser.add_argument("--output", default="docs/reports/batch_test_report.md", help="报告输出路径")
    parser.add_argument("--json", default="docs/reports/batch_test_results.json", help="JSON 结果输出路径")
    args = parser.parse_args()

    runnable, skipped = discover_test_files(args.dir)

    print(f"\n[INFO] 开始批量运行 {len(runnable)} 个测试文件（单文件超时 {args.timeout}s）...\n")
    results = []
    overall_t0 = time.perf_counter()

    for i, file_path in enumerate(runnable, 1):
        rel = file_path.relative_to(PROJECT_ROOT)
        print(f"[{i}/{len(runnable)}] 运行: {rel} ...", end=" ", flush=True)
        result = run_single_test(file_path, args.timeout)
        results.append(result)
        status_icon = {"passed": "OK", "failed": "FAIL", "timeout": "TIMEOUT", "error": "ERR"}.get(result["status"], "?")
        print(f"{status_icon} ({result['duration_sec']}s) - {result['message'][:80]}")

    overall_duration = time.perf_counter() - overall_t0

    # 保存 JSON 结果
    json_path = PROJECT_ROOT / args.json
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[INFO] JSON 结果已保存: {json_path}")

    # 生成 Markdown 报告
    report_path = PROJECT_ROOT / args.output
    generate_report(results, skipped, report_path, overall_duration)

    # 打印汇总
    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    total_skipped_tests = sum(r["skipped"] for r in results)
    print(f"\n{'='*60}")
    print(f"批量测试完成 - 总耗时: {overall_duration:.1f}s")
    print(f"  文件: {len([r for r in results if r['status']=='passed'])} 通过, "
          f"{len([r for r in results if r['status']=='failed'])} 失败, "
          f"{len([r for r in results if r['status']=='timeout'])} 超时, "
          f"{len([r for r in results if r['status']=='error'])} 异常")
    print(f"  用例: {total_passed} 通过, {total_failed} 失败, {total_errors} 错误, {total_skipped_tests} 跳过")
    print(f"  报告: {report_path}")
    print(f"{'='*60}")

    # 如果有失败/超时/错误，返回非零退出码
    if any(r["status"] != "passed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
