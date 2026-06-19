# -*- coding: utf-8 -*-
"""
覆盖率测量脚本 - 修复版
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 先启动 coverage
import coverage
cov = coverage.Coverage(source=["agent.system_tools"], omit=["*/tests/*", "*/site-packages/*"])
cov.start()

# 运行测试
import pytest
test_files = [
    "tests/unit/test_system_tools.py",
    "tests/unit/test_system_tools_supplement.py",
    "tests/unit/test_system_tools_final.py",
    "tests/unit/test_system_tools_ultimate.py",
    "tests/unit/test_system_tools_final_complete.py",
]
exit_code = pytest.main(test_files + ["--no-header", "-q", "--tb=no", "--no-cov"])
cov.stop()
cov.save()

# 打印覆盖率
print("\n\n========== 覆盖率报告 ==========")
try:
    cov.report(show_missing=False)
except Exception as e:
    print(f"覆盖率报告失败: {e}")
    # 列出 .coverage 数据文件
    if os.path.exists(".coverage"):
        print(".coverage 文件存在")

# 输出错误测试的数量
print(f"\n测试退出码: {exit_code}")
