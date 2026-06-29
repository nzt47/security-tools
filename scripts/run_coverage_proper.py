"""使用 coverage run 运行测试，确保 .coverage 文件正确生成"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUTPUT_FILE = ROOT / "coverage_report" / "full_test_output.txt"
OUTPUT_FILE.parent.mkdir(exist_ok=True)

# 先清理旧的 .coverage 文件
old_cov = ROOT / ".coverage"
if old_cov.exists():
    old_cov.unlink()

SKIP_TESTS = [
    "tests/unit/test_memory_storage_boundary.py",
    "tests/unit/test_memory_module.py",
    "tests/unit/test_memory_optimized.py",
    "tests/unit/test_memory_refactor.py",
    "tests/unit/test_memory_vector_store.py",
    "tests/unit/test_memory_filter_sensitive.py",
    "tests/unit/test_baseline_collector.py",
    "tests/unit/test_digital_life_comprehensive.py",
    "tests/unit/test_full_stack_demo.py",
    "tests/unit/test_intelligent_optimization.py",
    "tests/unit/test_cognitive_loop.py",
    "tests/unit/test_lifetrace.py",
    "tests/unit/test_detailed_profiler.py",
    "tests/unit/test_v2_performance_patch.py",
    "tests/unit/test_subagent.py",
    "tests/unit/test_subagent_manager.py",
    "tests/unit/test_system_tools_core.py",
    "tests/unit/test_system_tools_platform.py",
    "tests/unit/test_system_tools_security.py",
    "tests/unit/test_workflow_engine_supplement.py",
    "tests/unit/test_workflow_engine.py",
    "tests/unit/test_web_search.py",
    "tests/unit/test_web_scraper.py",
    "tests/unit/test_web_browser_agent.py",
    "tests/unit/test_web_crawler_control.py",
    "tests/unit/test_extensions_api.py",
    "tests/unit/test_tracing_context_propagation.py",
    "tests/unit/test_diagram_tools.py",
    "tests/unit/test_pdf_tools.py",
]

cmd = [
    sys.executable, "-m", "coverage", "run",
    "--branch",
    "--source=agent,sensor,memory,planning,persona,core,cognitive,lifetrace,utils",
    "--omit=" + ",".join([
        "*/tests/*", "*/test_*.py", "*/__init__.py",
        "*/migrations/*", "*/venv/*", "*/env/*",
    ]),
    "-m", "pytest",
    "tests/unit/",
    "--no-header",
    "--tb=line",
    "-q",
    "--maxfail=0",
]

for skip in SKIP_TESTS:
    cmd.extend(["--ignore", skip])

print(f"开始运行 coverage（已跳过 {len(SKIP_TESTS)} 个慢测试）")
print(f"输出将保存到：{OUTPUT_FILE}")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdout=f,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        timeout=900,  # 15 分钟超时
    )

print(f"\n测试完成，退出码：{proc.returncode}")
print(f"输出已保存到：{OUTPUT_FILE}")

# 生成覆盖率报告
print("\n生成覆盖率报告...")
report_cmd = [
    sys.executable, "-m", "coverage", "report",
    "--show-missing",
    "--include=agent/*,sensor/*,memory/*,planning/*,persona/*,core/*,cognitive/*,lifetrace/*,utils/*",
]

report_proc = subprocess.run(
    report_cmd,
    cwd=str(ROOT),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)

cov_report_file = ROOT / "coverage_report" / "coverage_term.txt"
cov_report_file.write_text(report_proc.stdout, encoding="utf-8")
print(f"覆盖率报告已保存到：{cov_report_file}")

# 生成 JSON 报告
json_cmd = [
    sys.executable, "-m", "coverage", "json",
    "-o", "coverage_report/coverage.json",
    "--include=agent/*,sensor/*,memory/*,planning/*,persona/*,core/*,cognitive/*,lifetrace/*,utils/*",
]
json_proc = subprocess.run(
    json_cmd,
    cwd=str(ROOT),
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)
print(f"JSON 报告生成完成（退出码 {json_proc.returncode}）")
if json_proc.stderr:
    print(f"stderr: {json_proc.stderr[:500]}")
