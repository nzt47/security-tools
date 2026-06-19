# -*- coding: utf-8 -*-
"""
覆盖率测量脚本 - 简化版
直接使用 coverage 命令行来生成报告
"""
import subprocess
import sys
import os

os.chdir(r"C:\Users\Administrator\agent")

# 运行 coverage 测量 system_tools
test_files = " ".join([
    "tests/unit/test_system_tools.py",
    "tests/unit/test_system_tools_supplement.py",
    "tests/unit/test_system_tools_final.py",
    "tests/unit/test_system_tools_ultimate.py",
    "tests/unit/test_system_tools_final_complete.py",
])

# 使用 --include 而不是 --source 以便匹配到模块文件
print("=" * 60)
print("测量 agent.system_tools 覆盖率")
print("=" * 60)
result = subprocess.run(
    [sys.executable, "-m", "pytest", test_files, "-q", "--tb=no",
     "--cov=agent.system_tools",
     "--cov-report=term",
     "--no-cov-on-fail"],
    capture_output=True, text=True
)
print("STDOUT:", result.stdout[-3000:])
print("STDERR:", result.stderr[-1000:])

print("\n" + "=" * 60)
print("测量 agent.task_scheduler 覆盖率")
print("=" * 60)
test_files_ts = " ".join([
    "tests/unit/test_task_scheduler.py",
    "tests/unit/test_task_scheduler_supplement.py",
    "tests/unit/test_task_scheduler_complete.py",
    "tests/unit/test_task_scheduler_simple.py",
])
result = subprocess.run(
    [sys.executable, "-m", "pytest", test_files_ts, "-q", "--tb=no",
     "--cov=agent.task_scheduler",
     "--cov-report=term",
     "--no-cov-on-fail"],
    capture_output=True, text=True
)
print("STDOUT:", result.stdout[-3000:])
print("STDERR:", result.stderr[-1000:])
