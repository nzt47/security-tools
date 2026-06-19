# -*- coding: utf-8 -*-
"""
精准覆盖率测量脚本
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def measure_module_coverage(module_name, test_files):
    """测量单个模块的覆盖率"""
    print(f"\n{'='*60}")
    print(f"测量 {module_name} 覆盖率")
    print(f"{'='*60}")

    # 清理之前的 coverage 数据
    if os.path.exists(".coverage"):
        os.remove(".coverage")

    # 启动 coverage
    import coverage
    # 使用模块名作为 source
    cov = coverage.Coverage(
        source=[module_name],
        omit=["*/tests/*", "*/site-packages/*"],
        data_file=".coverage_temp"
    )
    cov.start()

    # 运行测试
    import pytest
    args = test_files + ["--no-header", "-q", "--tb=no", "--no-cov"]
    exit_code = pytest.main(args)
    cov.stop()
    cov.save()

    # 打印覆盖率
    print(f"\n========== {module_name} 覆盖率报告 ==========")
    try:
        cov.report(show_missing=False)
    except Exception as e:
        print(f"覆盖率报告失败: {e}")
        return None, exit_code

    # 清理
    if os.path.exists(".coverage_temp"):
        os.remove(".coverage_temp")

    return cov, exit_code


# 测量 system_tools
test_files_system_tools = [
    "tests/unit/test_system_tools.py",
    "tests/unit/test_system_tools_supplement.py",
    "tests/unit/test_system_tools_final.py",
    "tests/unit/test_system_tools_ultimate.py",
    "tests/unit/test_system_tools_final_complete.py",
]
cov_st, exit_st = measure_module_coverage("agent.system_tools", test_files_system_tools)

# 测量 task_scheduler
test_files_scheduler = [
    "tests/unit/test_task_scheduler.py",
    "tests/unit/test_task_scheduler_supplement.py",
    "tests/unit/test_task_scheduler_complete.py",
    "tests/unit/test_task_scheduler_simple.py",
]
cov_ts, exit_ts = measure_module_coverage("agent.task_scheduler", test_files_scheduler)

print(f"\n\n===== 最终结果 =====")
print(f"system_tools 退出码: {exit_st}")
print(f"task_scheduler 退出码: {exit_ts}")
