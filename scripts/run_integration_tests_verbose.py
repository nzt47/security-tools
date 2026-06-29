#!/usr/bin/env python3
"""
增强版集成测试运行脚本 - 包含详细日志打印和并发问题排查支持

功能特性:
1. 在关键节点输出详细的结构化日志
2. 支持按模块单独运行测试
3. 记录测试执行时间和资源使用情况
4. 自动检测并发问题和超时情况
5. 生成测试报告和问题摘要
"""

import os
import sys
import time
import json
import argparse
import subprocess
import psutil
import threading
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLogger:
    """测试日志记录器 - 输出结构化日志"""

    def __init__(self, log_file: str = None):
        self.log_file = log_file
        self.start_time = datetime.now()
        self.test_results: List[Dict] = []
        self._lock = threading.Lock()

    def log(self, level: str, message: str, **kwargs):
        """输出结构化日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message,
            **kwargs
        }
        
        log_str = json.dumps(log_entry, ensure_ascii=False)
        print(f"[{level.upper()}] {log_entry['message']}")
        
        if self.log_file:
            with self._lock:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(log_str + "\n")

    def info(self, message: str, **kwargs):
        self.log("info", message, **kwargs)

    def warning(self, message: str, **kwargs):
        self.log("warning", message, **kwargs)

    def error(self, message: str, **kwargs):
        self.log("error", message, **kwargs)

    def debug(self, message: str, **kwargs):
        self.log("debug", message, **kwargs)

    def record_result(self, test_name: str, status: str, duration_ms: float, error: str = None):
        """记录测试结果"""
        with self._lock:
            self.test_results.append({
                "test_name": test_name,
                "status": status,
                "duration_ms": duration_ms,
                "error": error,
                "timestamp": datetime.now().isoformat()
            })

    def generate_report(self) -> str:
        """生成测试报告"""
        end_time = datetime.now()
        total_duration = (end_time - self.start_time).total_seconds() * 1000
        
        passed = [r for r in self.test_results if r["status"] == "passed"]
        failed = [r for r in self.test_results if r["status"] == "failed"]
        skipped = [r for r in self.test_results if r["status"] == "skipped"]
        
        avg_duration = sum(r["duration_ms"] for r in self.test_results) / len(self.test_results) if self.test_results else 0
        max_duration = max(r["duration_ms"] for r in self.test_results) if self.test_results else 0
        
        report = f"""
{'='*70}
            集成测试运行报告
{'='*70}

基本信息:
  开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}
  结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}
  总耗时:   {total_duration:.2f} ms
  CPU核心:  {psutil.cpu_count(logical=True)}
  内存总量: {psutil.virtual_memory().total / (1024**3):.2f} GB

测试结果:
  总测试数: {len(self.test_results)}
  通过:     {len(passed)} ✅
  失败:     {len(failed)} ❌
  跳过:     {len(skipped)} ⏭️
  通过率:   {len(passed)/len(self.test_results)*100:.1f}%

性能统计:
  平均耗时: {avg_duration:.2f} ms
  最大耗时: {max_duration:.2f} ms

失败详情:
"""
        
        for result in failed:
            report += f"""
  - {result['test_name']}
    状态: {result['status']}
    耗时: {result['duration_ms']:.2f} ms
    错误: {result['error'][:200] if result['error'] else '无'}
"""
        
        report += f"""
{'='*70}
"""
        return report


def get_test_files() -> Dict[str, str]:
    """获取所有集成测试文件"""
    test_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "integration")
    test_files = {}
    
    if os.path.exists(test_dir):
        for filename in sorted(os.listdir(test_dir)):
            if filename.startswith("test_") and filename.endswith(".py"):
                module_name = filename.replace("test_", "").replace(".py", "")
                test_files[module_name] = os.path.join(test_dir, filename)
    
    return test_files


def run_test_with_logging(logger: TestLogger, test_file: str, test_name: str = None, timeout: int = 120):
    """运行单个测试并记录日志"""
    start_time = time.time()
    full_test_path = test_file
    
    if test_name:
        full_test_path = f"{test_file}::{test_name}"
    
    logger.info(f"开始运行测试: {full_test_path}", test_file=test_file, test_name=test_name)
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", full_test_path, "-v", "--tb=short", "--timeout=60"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        
        duration_ms = (time.time() - start_time) * 1000
        
        if result.returncode == 0:
            logger.info(f"测试通过: {full_test_path}", duration_ms=duration_ms)
            logger.record_result(full_test_path, "passed", duration_ms)
        else:
            logger.error(f"测试失败: {full_test_path}", 
                        returncode=result.returncode,
                        duration_ms=duration_ms,
                        stderr=result.stderr[:500])
            logger.record_result(full_test_path, "failed", duration_ms, error=result.stderr[:500])
            
        return result.returncode == 0
        
    except subprocess.TimeoutExpired:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"测试超时: {full_test_path}", duration_ms=duration_ms)
        logger.record_result(full_test_path, "failed", duration_ms, error="测试超时")
        return False
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"测试异常: {full_test_path}", 
                    error=str(e),
                    duration_ms=duration_ms)
        logger.record_result(full_test_path, "failed", duration_ms, error=str(e))
        return False


def main():
    parser = argparse.ArgumentParser(description="增强版集成测试运行脚本")
    parser.add_argument("--module", "-m", help="指定要运行的测试模块（如: circuit_breaker_degrade_flow）")
    parser.add_argument("--test", "-t", help="指定要运行的单个测试用例")
    parser.add_argument("--all", "-a", action="store_true", help="运行所有集成测试")
    parser.add_argument("--timeout", type=int, default=120, help="单个测试超时时间（秒）")
    parser.add_argument("--log-file", help="日志输出文件")
    parser.add_argument("--generate-report", action="store_true", help="生成测试报告")
    
    args = parser.parse_args()
    
    logger = TestLogger(args.log_file)
    
    logger.info("="*70)
    logger.info("集成测试运行器初始化", version="1.0.0")
    logger.info("当前工作目录: {}".format(os.getcwd()))
    logger.info("Python版本: {}".format(sys.version))
    logger.info("CPU核心数: {}".format(psutil.cpu_count(logical=True)))
    logger.info("可用内存: {:.2f} GB".format(psutil.virtual_memory().available / (1024**3)))
    logger.info("="*70)
    
    test_files = get_test_files()
    
    if not test_files:
        logger.error("未找到集成测试文件")
        sys.exit(1)
    
    logger.info(f"找到 {len(test_files)} 个集成测试文件:", files=list(test_files.keys()))
    
    if args.module:
        if args.module in test_files:
            test_list = [(args.module, test_files[args.module])]
        else:
            logger.error(f"未找到测试模块: {args.module}", available_modules=list(test_files.keys()))
            sys.exit(1)
    elif args.all:
        test_list = list(test_files.items())
    else:
        print("\n可用测试模块:")
        for i, (name, path) in enumerate(test_files.items(), 1):
            print(f"  {i}. {name}")
        
        choice = input("\n请输入要运行的测试模块序号（或输入 'all' 运行全部）: ")
        
        if choice.lower() == "all":
            test_list = list(test_files.items())
        elif choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(test_files):
                name = list(test_files.keys())[index]
                test_list = [(name, test_files[name])]
            else:
                print("无效的选择")
                sys.exit(1)
        else:
            print("无效的选择")
            sys.exit(1)
    
    logger.info(f"将要运行 {len(test_list)} 个测试模块")
    
    passed_count = 0
    failed_count = 0
    
    for module_name, test_file in test_list:
        logger.info(f"\n{'='*70}")
        logger.info(f"开始测试模块: {module_name}", test_file=test_file)
        logger.info(f"{'='*70}")
        
        if args.test:
            success = run_test_with_logging(logger, test_file, args.test, args.timeout)
        else:
            success = run_test_with_logging(logger, test_file, timeout=args.timeout)
        
        if success:
            passed_count += 1
        else:
            failed_count += 1
    
    logger.info(f"\n{'='*70}")
    logger.info(f"测试完成", passed=passed_count, failed=failed_count, total=len(test_list))
    logger.info(f"{'='*70}")
    
    if args.generate_report or failed_count > 0:
        report = logger.generate_report()
        print(report)
        
        if args.log_file:
            report_file = args.log_file.replace(".log", "_report.txt")
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info(f"测试报告已保存: {report_file}")
    
    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
