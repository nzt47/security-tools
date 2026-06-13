#!/usr/bin/env python3
"""
运行测试并保存结果
"""
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/unit/test_security_utils.py", "-v", "--override-ini=addopts="],
    capture_output=True,
    text=True,
    encoding='utf-8',
    errors='replace'
)

with open('test_results.txt', 'w', encoding='utf-8') as f:
    f.write("STDOUT:\n")
    f.write(result.stdout)
    f.write("\n\nSTDERR:\n")
    f.write(result.stderr)
    f.write(f"\n\nReturn code: {result.returncode}")

print("Results saved to test_results.txt")
print(f"Return code: {result.returncode}")
