"""
运行覆盖率分析脚本：执行所有单元测试并生成详细覆盖率报告
用法：python scripts/run_coverage_report.py
"""
import subprocess
import sys
import os
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent

def main():
    # 覆盖率输出目录
    cov_dir = ROOT / "coverage_report"
    cov_dir.mkdir(exist_ok=True)

    # 运行 pytest + 覆盖率收集
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/unit/",
        "--cov=agent",
        "--cov=sensor",
        "--cov=memory",
        "--cov=planning",
        "--cov=persona",
        "--cov=core",
        "--cov=cognitive",
        "--cov=lifetrace",
        "--cov=utils",
        "--cov-report=term-missing",
        f"--cov-report=html:{cov_dir / 'html'}",
        f"--cov-report=json:{cov_dir / 'coverage.json'}",
        "--cov-branch",
        "--no-header",
        "--tb=no",
        "-q",
        "--maxfail=0",
        # 跳过依赖外部资源的测试
        "--ignore=tests/unit/test_diagram_tools.py",
        "--ignore=tests/unit/test_pdf_tools.py",
    ]

    print("=" * 80)
    print("运行覆盖率分析（可能需要 10-15 分钟）...")
    print("=" * 80)

    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # 保存原始输出
    out_file = cov_dir / "pytest_output.txt"
    out_file.write_text(result.stdout + "\n--- STDERR ---\n" + result.stderr, encoding="utf-8")
    print(f"原始输出已保存到：{out_file}")
    print(f"HTML 报告：{cov_dir / 'html' / 'index.html'}")
    print(f"JSON 数据：{cov_dir / 'coverage.json'}")
    print(f"退出码：{result.returncode}")

if __name__ == "__main__":
    main()
