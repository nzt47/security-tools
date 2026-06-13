#!/usr/bin/env python3
"""
完整集成测试套件

运行所有测试：
1. Memory 模块单元测试
2. PermissionSystem 危险关键词测试
3. V2 功能开关测试
4. LifeTrace & Persona 模块测试
"""

import sys
import os
import subprocess
import logging
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_test(test_file, description):
    """运行单个测试文件"""
    print("\n" + "=" * 100)
    print(f"[TEST] {description}")
    print("=" * 100)
    
    try:
        result = subprocess.run(
            [sys.executable, test_file],
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )
        
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        success = result.returncode == 0
        status = "[PASS]" if success else "[FAIL]"
        print(f"\n{status} - {description} (exit code: {result.returncode})")
        
        return success
        
    except subprocess.TimeoutExpired:
        print(f"[FAIL] - {description} (timeout)")
        return False
    except Exception as e:
        print(f"[FAIL] - {description} (error: {e})")
        return False

def main():
    """运行所有测试"""
    print("\n")
    print("=" * 100)
    print(">>> 开始运行完整集成测试套件 <<<")
    print("=" * 100)
    
    # 切换到项目根目录
    project_root = Path(__file__).parent
    os.chdir(project_root)
    
    # 定义测试
    tests = [
        ("agent/test_memory_module.py", "Memory 模块单元测试"),
        ("agent/test_permission_system.py", "PermissionSystem 危险关键词测试"),
        ("test_v2_features.py", "V2 功能开关测试"),
        ("test_v2_modules.py", "LifeTrace & Persona 集成测试"),
    ]
    
    # 运行所有测试
    results = []
    for test_file, description in tests:
        test_path = project_root / test_file
        if test_path.exists():
            result = run_test(test_path, description)
            results.append((description, result))
        else:
            print(f"\n[WARN] 跳过 {description} - 文件不存在: {test_file}")
            results.append((description, False))
    
    # 打印汇总
    print("\n" + "=" * 100)
    print("[SUMMARY] 完整测试套件结果汇总")
    print("=" * 100)
    
    passed = 0
    failed = 0
    
    for description, success in results:
        status = "[PASS]" if success else "[FAIL]"
        print(f"  {status} - {description}")
        if success:
            passed += 1
        else:
            failed += 1
    
    print("\n" + "=" * 100)
    print(f"[STATS] 总计: {passed} passed, {failed} failed (共 {len(results)} 项)")
    print("=" * 100)
    
    if failed == 0:
        print("\n[SUCCESS] All tests passed! Integration verification successful!")
        return 0
    else:
        print(f"\n[ERROR] {failed} tests failed, please check.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
