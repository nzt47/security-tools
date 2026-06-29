#!/usr/bin/env python3
"""
CI/CD 流水线脚本 - 智能工具选择模块

用于每次代码提交时自动执行测试，确保代码质量。

使用方式:
    python scripts/cicd_pipeline.py
    python scripts/cicd_pipeline.py --mode=quick    # 快速模式
    python scripts/cicd_pipeline.py --mode=full     # 全量模式
    python scripts/cicd_pipeline.py --mode=stress   # 压力测试模式
"""

import os
import sys
import json
import argparse
import subprocess
import time
from datetime import datetime


def run_command(cmd, cwd=None):
    """运行命令并返回结果"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "Command timed out",
            "returncode": -1,
        }


def run_unit_tests():
    """运行单元测试"""
    print("🔧 运行单元测试...")
    
    cmd = ["python", "-m", "pytest", "agent/tests/test_tool_router.py", "-v"]
    result = run_command(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    if result["success"]:
        print("✅ 单元测试通过")
    else:
        print("❌ 单元测试失败")
        print(f"错误输出:\n{result['stderr']}")
    
    return result


def run_stress_tests():
    """运行压力测试"""
    print("🔧 运行压力测试...")
    
    cmd = ["python", "-m", "agent.tests.test_tool_router"]
    result = run_command(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    if result["success"]:
        print("✅ 压力测试通过")
    else:
        print("❌ 压力测试失败")
        print(f"错误输出:\n{result['stderr']}")
    
    return result


def run_integration_tests():
    """运行集成测试"""
    print("🔧 运行集成测试...")
    
    # 直接调用测试模块，而不是通过subprocess
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from agent.tests.test_tool_router import ToolRouterTester
        
        tester = ToolRouterTester()
        results = tester.run_all_tests()
        
        if results['summary']['success_rate'] == 100.0:
            print("✅ 集成测试通过")
            return {"success": True, "stdout": f"测试完成: {results['summary']['passed']}/{results['summary']['total']} 通过", "stderr": "", "returncode": 0}
        else:
            print("❌ 集成测试失败")
            return {"success": False, "stdout": f"测试完成: {results['summary']['passed']}/{results['summary']['total']} 通过", "stderr": "", "returncode": 1}
    
    except Exception as e:
        print(f"❌ 集成测试异常: {e}")
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


def run_boundary_tests():
    """运行边界条件测试"""
    print("🔧 运行边界条件测试...")
    
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from agent.tests.test_tool_router import ToolRouterTester
        
        tester = ToolRouterTester()
        
        # 只运行边界条件测试
        boundary_tests = [
            ("边界条件1-空工具集", tester.test_empty_tool_set),
            ("边界条件2-动态添加工具", tester.test_dynamic_tool_addition),
            ("边界条件3-动态删除工具", tester.test_dynamic_tool_removal),
            ("边界条件4-配置文件损坏", tester.test_config_file_corruption),
            ("边界条件5-工具名称冲突", tester.test_tool_name_conflicts),
            ("边界条件6-大量工具场景", tester.test_large_tool_set),
        ]
        
        passed = 0
        total = 0
        
        for test_name, test_func in boundary_tests:
            total += 1
            try:
                result = test_func()
                if result:
                    passed += 1
                    print(f"✅ {test_name}")
                else:
                    print(f"❌ {test_name}")
            except Exception as e:
                print(f"❌ {test_name} - 异常: {e}")
        
        if passed == total:
            print(f"✅ 边界条件测试通过 ({passed}/{total})")
            return {"success": True, "stdout": f"边界条件测试完成: {passed}/{total} 通过", "stderr": "", "returncode": 0}
        else:
            print(f"❌ 边界条件测试失败 ({passed}/{total})")
            return {"success": False, "stdout": f"边界条件测试完成: {passed}/{total} 通过", "stderr": "", "returncode": 1}
    
    except Exception as e:
        print(f"❌ 边界条件测试异常: {e}")
        return {"success": False, "stdout": "", "stderr": str(e), "returncode": -1}


def run_lint_check():
    """运行代码风格检查"""
    print("🔧 运行代码风格检查...")
    
    # 检查是否安装了 pylint
    cmd = ["python", "-c", "import pylint"]
    lint_available = run_command(cmd)["success"]
    
    if lint_available:
        cmd = ["pylint", "agent/tool_router.py", "agent/tests/test_tool_router.py"]
        result = run_command(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        if result["success"]:
            print("✅ 代码风格检查通过")
        else:
            print("⚠️ 代码风格警告")
            print(f"输出:\n{result['stdout']}")
            # 代码风格警告不阻止构建
            result["success"] = True
    else:
        print("ℹ️ pylint 未安装，跳过代码风格检查")
        result = {"success": True, "stdout": "", "stderr": "", "returncode": 0}
    
    return result


def run_type_check():
    """运行类型检查"""
    print("🔧 运行类型检查...")
    
    # 检查是否安装了 mypy
    cmd = ["python", "-c", "import mypy"]
    mypy_available = run_command(cmd)["success"]
    
    if mypy_available:
        cmd = ["mypy", "agent/tool_router.py", "agent/tests/test_tool_router.py"]
        result = run_command(cmd, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        if result["success"]:
            print("✅ 类型检查通过")
        else:
            print("⚠️ 类型检查警告")
            print(f"输出:\n{result['stderr']}")
            # 类型警告不阻止构建
            result["success"] = True
    else:
        print("ℹ️ mypy 未安装，跳类型检查")
        result = {"success": True, "stdout": "", "stderr": "", "returncode": 0}
    
    return result


def generate_report(results, start_time):
    """生成测试报告"""
    duration = time.time() - start_time
    
    report = {
        "pipeline_version": "1.0.0",
        "run_time": datetime.now().isoformat(),
        "duration_seconds": round(duration, 2),
        "stages": [],
        "summary": {},
    }
    
    all_success = True
    total_tests = 0
    passed_tests = 0
    
    for stage_name, result in results.items():
        stage_report = {
            "name": stage_name,
            "success": result["success"],
            "returncode": result["returncode"],
            "tests_passed": 0,
            "tests_total": 0,
        }
        
        if "stdout" in result:
            # 提取测试结果 - 支持多种格式
            stdout = result["stdout"]
            
            # 格式1: "测试完成: 9/9 通过"
            import re
            match = re.search(r"测试完成:\s*(\d+)/(\d+)\s*通过", stdout)
            if match:
                passed = int(match.group(1))
                total = int(match.group(2))
                stage_report["tests_passed"] = passed
                stage_report["tests_total"] = total
                total_tests += total
                passed_tests += passed
            
            # 格式2: pytest 格式
            match = re.search(r"(\d+)\s*passed", stdout)
            if match and stage_report["tests_total"] == 0:
                passed = int(match.group(1))
                stage_report["tests_passed"] = passed
                stage_report["tests_total"] = passed
                total_tests += passed
                passed_tests += passed
            
            # 格式3: "成功率: 100.0%"
            match = re.search(r"成功率:\s*([\d.]+)%", stdout)
            if match:
                stage_report["success_rate"] = float(match.group(1))
        
        report["stages"].append(stage_report)
        
        if not result["success"]:
            all_success = False
    
    report["summary"] = {
        "all_passed": all_success,
        "total_tests": total_tests,
        "passed_tests": passed_tests,
        "success_rate": (passed_tests / total_tests) * 100 if total_tests > 0 else 0,
    }
    
    return report


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="CI/CD 流水线脚本")
    parser.add_argument(
        "--mode",
        choices=["quick", "full", "stress"],
        default="full",
        help="测试模式"
    )
    args = parser.parse_args()
    
    start_time = time.time()
    
    print("🚀 启动 CI/CD 流水线")
    print(f"模式: {args.mode}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    results = {}
    
    # 快速模式：只运行关键测试
    if args.mode == "quick":
        print("⚡ 快速模式：运行关键测试")
        results["单元测试"] = run_unit_tests()
    
    # 全量模式：运行所有测试
    elif args.mode == "full":
        print("📦 全量模式：运行所有测试")
        results["代码风格检查"] = run_lint_check()
        results["类型检查"] = run_type_check()
        results["单元测试"] = run_unit_tests()
        results["边界条件测试"] = run_boundary_tests()
        results["集成测试"] = run_integration_tests()
    
    # 压力测试模式
    elif args.mode == "stress":
        print("🔥 压力测试模式")
        results["边界条件测试"] = run_boundary_tests()
        results["压力测试"] = run_stress_tests()
    
    print("=" * 60)
    
    # 生成报告
    report = generate_report(results, start_time)
    
    # 打印摘要
    print("\n📊 测试报告:")
    print(f"运行时间: {report['duration_seconds']} 秒")
    print(f"测试总数: {report['summary']['total_tests']}")
    print(f"通过数: {report['summary']['passed_tests']}")
    print(f"成功率: {report['summary']['success_rate']:.1f}%")
    
    # 保存报告
    report_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "cicd_report.json"
    )
    
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n📄 报告已保存到: {report_path}")
    
    # 输出结果
    if report["summary"]["all_passed"]:
        print("\n🎉 所有测试通过!")
        sys.exit(0)
    else:
        print("\n❌ 部分测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()