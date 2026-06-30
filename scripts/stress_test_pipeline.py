#!/usr/bin/env python3
"""
独立压力测试CI/CD流水线脚本

用于自动化执行压力测试，确保每次代码提交都经过严格的压力测试验证。

支持的模式:
- quick: 快速压力测试（10并发，100请求）
- normal: 标准压力测试（50并发，1000请求）
- extreme: 极端压力测试（100并发，5000请求）

使用方式:
    python scripts/stress_test_pipeline.py --mode=quick
    python scripts/stress_test_pipeline.py --mode=normal
    python scripts/stress_test_pipeline.py --mode=extreme

输出:
    - JSON格式测试报告
    - 控制台日志
    - 性能指标统计
"""

import argparse
import json
import os
import sys
import time
import threading
import traceback
import socket
from datetime import datetime
from typing import Dict, List, Any

# 设置路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tests.test_tool_router import ToolRouterTester


class StressTestPipeline:
    """压力测试流水线"""
    
    def __init__(self, mode: str = "normal"):
        self.mode = mode
        self.results: Dict[str, Any] = {
            "start_time": datetime.now().isoformat(),
            "mode": mode,
            "tests": [],
            "summary": {},
            "performance_metrics": {},
            "error_details": {},  # 新增：详细错误信息
        }
        self._configure_mode()
    
    def _configure_mode(self):
        """根据模式配置测试参数"""
        configs = {
            "quick": {
                "name": "快速压力测试",
                "concurrent_threads": 10,
                "total_requests": 100,
                "description": "适用于快速验证",
                "timeout_seconds": 30,
            },
            "normal": {
                "name": "标准压力测试",
                "concurrent_threads": 50,
                "total_requests": 1000,
                "description": "适用于日常CI/CD",
                "timeout_seconds": 120,
            },
            "extreme": {
                "name": "极端压力测试",
                "concurrent_threads": 100,
                "total_requests": 5000,
                "description": "适用于发布前验证",
                "timeout_seconds": 300,
            },
        }
        
        self.config = configs.get(self.mode, configs["normal"])
        print(f"📋 压力测试配置: {self.config['name']}")
        print(f"   - 并发线程数: {self.config['concurrent_threads']}")
        print(f"   - 总请求数: {self.config['total_requests']}")
        print(f"   - 超时时间: {self.config['timeout_seconds']}秒")
        print(f"   - 描述: {self.config['description']}")
    
    def _capture_exception_details(self, e: Exception, context: str) -> Dict:
        """捕获异常详细信息，包括网络超时"""
        exception_info = {
            "type": type(e).__name__,
            "message": str(e),
            "context": context,
            "traceback": traceback.format_exc(),
            "is_timeout": False,
            "timeout_type": None,
        }
        
        # 检测网络超时相关异常
        timeout_exceptions = (
            socket.timeout,
            TimeoutError,
        )
        
        # 尝试导入 requests 相关异常
        try:
            from requests.exceptions import Timeout as RequestsTimeout, ConnectTimeout, ReadTimeout
            timeout_exceptions += (RequestsTimeout, ConnectTimeout, ReadTimeout)
        except ImportError:
            pass
        
        if isinstance(e, timeout_exceptions):
            exception_info["is_timeout"] = True
            exception_info["timeout_type"] = type(e).__name__
        
        return exception_info
    
    def run_boundary_tests(self) -> bool:
        """运行边界条件测试"""
        print("\n🔧 运行边界条件测试...")
        
        try:
            tester = ToolRouterTester()
            
            boundary_tests = [
                ("空工具集", tester.test_empty_tool_set),
                ("动态添加工具", tester.test_dynamic_tool_addition),
                ("动态删除工具", tester.test_dynamic_tool_removal),
                ("配置文件损坏", tester.test_config_file_corruption),
                ("工具名称冲突", tester.test_tool_name_conflicts),
                ("大量工具场景", tester.test_large_tool_set),
                ("工具数量阈值边界", tester.test_tool_count_threshold),
                ("并发工具变化", tester.test_concurrent_tool_changes),
                ("单工具场景", tester.test_single_tool_scenario),
                ("空输入场景", tester.test_empty_user_input),
                ("高频工具变化", tester.test_frequent_tool_changes),
                ("无描述工具", tester.test_tool_without_description),
            ]
            
            passed = 0
            total = 0
            timeout_count = 0
            test_errors = []
            
            for test_name, test_func in boundary_tests:
                total += 1
                try:
                    result = test_func()
                    if result:
                        passed += 1
                        print(f"   [OK] {test_name}")
                    else:
                        print(f"   [FAIL] {test_name}")
                except Exception as e:
                    # 捕获异常详细信息
                    exc_info = self._capture_exception_details(e, f"边界条件测试: {test_name}")
                    test_errors.append(exc_info)
                    
                    if exc_info["is_timeout"]:
                        timeout_count += 1
                        print(f"   [TIMEOUT] {test_name}: {exc_info['timeout_type']}")
                    else:
                        print(f"   [ERROR] {test_name}: {exc_info['type']} - {exc_info['message']}")
            
            self.results["boundary_tests"] = {
                "passed": passed,
                "total": total,
                "timeout_count": timeout_count,
                "success_rate": (passed / total) * 100 if total > 0 else 0,
                "errors": test_errors[:5],  # 只保留前5个错误
            }
            
            if passed == total:
                print(f"✅ 边界条件测试通过 ({passed}/{total})")
                return True
            else:
                print(f"❌ 边界条件测试失败 ({passed}/{total})")
                if timeout_count > 0:
                    print(f"   ⚠️  超时数量: {timeout_count}")
                return False
        
        except Exception as e:
            exc_info = self._capture_exception_details(e, "边界条件测试主流程")
            self.results["error_details"]["boundary_tests"] = exc_info
            print(f"❌ 边界条件测试异常: {exc_info['type']} - {exc_info['message']}")
            traceback.print_exc()
            return False
    
    def run_concurrent_stress_test(self) -> bool:
        """运行并发压力测试"""
        print(f"\n🔥 运行并发压力测试 ({self.config['concurrent_threads']}线程, {self.config['total_requests']}请求)...")
        
        try:
            from agent.tool_router import get_tools_for_input
            
            start_time = time.time()
            errors = []
            timeout_errors = []
            success_count = 0
            total_count = 0
            lock = threading.Lock()
            timeout_reached = threading.Event()
            
            def stress_worker(worker_id: int, requests_per_worker: int):
                nonlocal success_count, total_count
                
                for i in range(requests_per_worker):
                    # 检查全局超时
                    if timeout_reached.is_set():
                        with lock:
                            errors.append(f"Worker {worker_id}, Request {i}: 全局超时")
                        break
                    
                    try:
                        # 设置单次请求超时
                        request_start = time.time()
                        request_timeout = self.config["timeout_seconds"] / self.config["concurrent_threads"]
                        
                        # 模拟不同类型的用户输入
                        inputs = [
                            "分析日志文件",
                            "搜索记忆中的信息",
                            "读取本地文件内容",
                            "执行系统命令",
                            "发送HTTP请求",
                        ]
                        input_text = inputs[(worker_id + i) % len(inputs)]
                        
                        result = get_tools_for_input(input_text)
                        
                        # 检查单次请求超时
                        request_duration = time.time() - request_start
                        if request_duration > request_timeout:
                            with lock:
                                timeout_errors.append(f"Worker {worker_id}, Request {i}: 请求超时 ({request_duration:.2f}s)")
                        elif isinstance(result, list):
                            with lock:
                                success_count += 1
                        else:
                            with lock:
                                errors.append(f"Worker {worker_id}, Request {i}: Invalid result")
                                
                    except socket.timeout as e:
                        with lock:
                            timeout_errors.append(f"Worker {worker_id}, Request {i}: socket.timeout - {str(e)}")
                    except TimeoutError as e:
                        with lock:
                            timeout_errors.append(f"Worker {worker_id}, Request {i}: TimeoutError - {str(e)}")
                    except Exception as e:
                        exc_info = self._capture_exception_details(e, f"Worker {worker_id}, Request {i}")
                        with lock:
                            if exc_info["is_timeout"]:
                                timeout_errors.append(f"Worker {worker_id}, Request {i}: {exc_info['timeout_type']} - {exc_info['message']}")
                            else:
                                errors.append(f"Worker {worker_id}, Request {i}: {type(e).__name__} - {str(e)}")
                    finally:
                        with lock:
                            total_count += 1
            
            # 计算每个线程的请求数
            requests_per_worker = self.config["total_requests"] // self.config["concurrent_threads"]
            remaining_requests = self.config["total_requests"] % self.config["concurrent_threads"]
            
            # 创建线程
            threads = []
            for i in range(self.config["concurrent_threads"]):
                req_count = requests_per_worker + (1 if i < remaining_requests else 0)
                t = threading.Thread(target=stress_worker, args=(i, req_count))
                threads.append(t)
            
            # 启动线程
            for t in threads:
                t.start()
            
            # 等待完成（带超时）
            for t in threads:
                t.join(timeout=self.config["timeout_seconds"])
            
            # 检查是否超时
            elapsed = time.time() - start_time
            if elapsed >= self.config["timeout_seconds"]:
                timeout_reached.set()
                print(f"   ⚠️  压力测试超时警告: 已运行 {elapsed:.2f}秒")
            
            duration = time.time() - start_time
            
            # 计算性能指标
            rps = total_count / duration if duration > 0 else 0
            avg_latency = (duration / total_count) * 1000 if total_count > 0 else 0
            
            self.results["stress_test"] = {
                "total_requests": total_count,
                "successful_requests": success_count,
                "failed_requests": len(errors),
                "timeout_requests": len(timeout_errors),
                "duration_seconds": duration,
                "requests_per_second": rps,
                "avg_latency_ms": avg_latency,
                "errors": errors[:10],  # 只保留前10个错误
                "timeout_errors": timeout_errors[:10],  # 只保留前10个超时错误
                "timeout_reached": timeout_reached.is_set(),
            }
            
            print(f"   总请求数: {total_count}")
            print(f"   成功请求: {success_count}")
            print(f"   失败请求: {len(errors)}")
            print(f"   超时请求: {len(timeout_errors)}")
            print(f"   耗时: {duration:.2f}秒")
            print(f"   吞吐量: {rps:.2f} 请求/秒")
            print(f"   平均延迟: {avg_latency:.2f}ms")
            
            if len(errors) == 0 and len(timeout_errors) == 0 and success_count == total_count:
                print("✅ 并发压力测试通过")
                return True
            else:
                print("❌ 并发压力测试失败")
                if len(timeout_errors) > 0:
                    print(f"   ⚠️  超时数量: {len(timeout_errors)}")
                return False
        
        except Exception as e:
            exc_info = self._capture_exception_details(e, "并发压力测试主流程")
            self.results["error_details"]["stress_test"] = exc_info
            print(f"❌ 并发压力测试异常: {exc_info['type']} - {exc_info['message']}")
            traceback.print_exc()
            return False
    
    def run_router_functional_tests(self) -> bool:
        """运行路由器功能测试"""
        print("\n🔍 运行路由器功能测试...")
        
        try:
            tester = ToolRouterTester()
            
            functional_tests = [
                ("关键词配置测试", tester.test_keywords_config),
                ("优先级顺序测试", tester.test_priority_order),
                ("工具分类测试", tester.test_tool_classification),
                ("别名合并测试", tester.test_alias_merge),
                ("极端优先级冲突测试", tester.test_extreme_priority_conflict),
                ("决策日志器测试", tester.test_decision_logger),
                ("工具数量一致性测试", tester.test_tool_count_consistency),
                ("性能指标测试", tester.test_performance_metrics),
            ]
            
            passed = 0
            total = 0
            
            for test_name, test_func in functional_tests:
                total += 1
                try:
                    result = test_func()
                    if result:
                        passed += 1
                        print(f"   [OK] {test_name}")
                    else:
                        print(f"   [FAIL] {test_name}")
                except Exception as e:
                    print(f"   [ERROR] {test_name}: {e}")
            
            self.results["functional_tests"] = {
                "passed": passed,
                "total": total,
                "success_rate": (passed / total) * 100 if total > 0 else 0,
            }
            
            if passed == total:
                print(f"✅ 功能测试通过 ({passed}/{total})")
                return True
            else:
                print(f"❌ 功能测试失败 ({passed}/{total})")
                return False
        
        except Exception as e:
            print(f"❌ 功能测试异常: {e}")
            traceback.print_exc()
            return False
    
    def generate_report(self) -> str:
        """生成测试报告"""
        self.results["end_time"] = datetime.now().isoformat()
        
        # 计算总体结果
        all_passed = True
        total_tests = 0
        total_passed = 0
        
        for key in ["boundary_tests", "stress_test", "functional_tests"]:
            if key in self.results:
                if key == "stress_test":
                    total_tests += 1
                    if self.results[key]["failed_requests"] == 0:
                        total_passed += 1
                    else:
                        all_passed = False
                else:
                    total_tests += 1
                    total_passed += self.results[key]["passed"]
                    if self.results[key]["passed"] != self.results[key]["total"]:
                        all_passed = False
        
        self.results["summary"] = {
            "all_passed": all_passed,
            "total_test_categories": 3,
            "passed_test_categories": sum([
                1 for key in ["boundary_tests", "stress_test", "functional_tests"]
                if key in self.results and (
                    (key == "stress_test" and self.results[key]["failed_requests"] == 0) or
                    (key != "stress_test" and self.results[key]["passed"] == self.results[key]["total"])
                )
            ]),
            "timestamp": datetime.now().isoformat(),
        }
        
        return json.dumps(self.results, ensure_ascii=False, indent=2)
    
    def save_report(self, report: str):
        """保存测试报告到文件"""
        report_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        os.makedirs(report_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = os.path.join(report_dir, f"stress_report_{self.mode}_{timestamp}.json")
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        
        print(f"\n📊 测试报告已保存到: {report_path}")
        return report_path
    
    def run(self) -> bool:
        """运行完整的压力测试流水线"""
        print("=" * 70)
        print(f"🚀 压力测试CI/CD流水线 - {self.config['name']}")
        print("=" * 70)
        
        all_passed = True
        
        # 阶段1: 边界条件测试
        print("\n--- 阶段1: 边界条件测试 ---")
        if not self.run_boundary_tests():
            all_passed = False
        
        # 阶段2: 功能测试
        print("\n--- 阶段2: 功能测试 ---")
        if not self.run_router_functional_tests():
            all_passed = False
        
        # 阶段3: 并发压力测试
        print("\n--- 阶段3: 并发压力测试 ---")
        if not self.run_concurrent_stress_test():
            all_passed = False
        
        # 生成报告
        report = self.generate_report()
        self.save_report(report)
        
        print("\n" + "=" * 70)
        if all_passed:
            print("🎉 所有测试通过！")
            print("=" * 70)
            return True
        else:
            print("❌ 部分测试失败！")
            print("=" * 70)
            return False


def main():
    """主入口"""
    parser = argparse.ArgumentParser(description="压力测试CI/CD流水线")
    parser.add_argument(
        "--mode",
        choices=["quick", "normal", "extreme"],
        default="normal",
        help="测试模式: quick(快速)/normal(标准)/extreme(极端)"
    )
    
    args = parser.parse_args()
    
    pipeline = StressTestPipeline(mode=args.mode)
    success = pipeline.run()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()