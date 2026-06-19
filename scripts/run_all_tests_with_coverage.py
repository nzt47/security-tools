"""
运行多个测试文件并统一收集覆盖率
"""
import sys
import os
import subprocess
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent

# 测试文件列表
test_files = [
    "test_core.py",
    "agent/test_permission_system.py",
]

print("="*80)
print("开始运行测试并收集覆盖率")
print("="*80)

# 首先运行 pytest 框架的测试
print("\n[1/3] 运行 pytest 测试...")
subprocess.run([
    sys.executable, "-m", "pytest",
    "tests/",
    "-v",
    "--tb=short",
    "--cov=agent",
    "--cov-append",  # 追加覆盖率数据
], cwd=PROJECT_ROOT)

# 运行独立的测试脚本
print("\n[2/3] 运行独立测试脚本...")
for test_file in test_files:
    if (PROJECT_ROOT / test_file).exists():
        print(f"\n  运行: {test_file}")
        # 使用 coverage 运行这些测试
        subprocess.run([
            sys.executable, "-m", "coverage", "run", "-a",
            test_file
        ], cwd=PROJECT_ROOT)

# 生成覆盖率报告
print("\n[3/3] 生成覆盖率报告...")
subprocess.run([
    sys.executable, "-m", "coverage", "report", "-m"
], cwd=PROJECT_ROOT)

subprocess.run([
    sys.executable, "-m", "coverage", "html", "-d", "htmlcov"
], cwd=PROJECT_ROOT)

subprocess.run([
    sys.executable, "-m", "coverage", "xml", "-o", "coverage.xml"
], cwd=PROJECT_ROOT)

print("\n" + "="*80)
print("测试运行完成！")
print(f"HTML报告: {PROJECT_ROOT / 'htmlcov' / 'index.html'}")
print(f"XML报告: {PROJECT_ROOT / 'coverage.xml'}")
print("="*80)
