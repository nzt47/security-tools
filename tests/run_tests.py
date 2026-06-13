"""
测试运行器 - Phase 3 自动化测试体系
统一管理所有测试的运行
"""
import sys
import os
import argparse
import logging
from pathlib import Path

# 设置路径
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def run_unit_tests():
    """运行单元测试"""
    logger.info("="*70)
    logger.info("Running Unit Tests")
    logger.info("="*70)
    
    import unittest
    
    # 发现并运行单元测试
    test_loader = unittest.TestLoader()
    test_dir = Path(__file__).parent / "unit"
    if test_dir.exists():
        test_suite = test_loader.discover(str(test_dir), pattern="test_*.py")
        test_runner = unittest.TextTestRunner(verbosity=2)
        result = test_runner.run(test_suite)
        return result.wasSuccessful()
    else:
        logger.warning(f"Unit test directory not found: {test_dir}")
        return True


def run_integration_tests():
    """运行集成测试"""
    logger.info("="*70)
    logger.info("Running Integration Tests")
    logger.info("="*70)
    
    import unittest
    
    test_loader = unittest.TestLoader()
    test_dir = Path(__file__).parent / "integration"
    if test_dir.exists():
        test_suite = test_loader.discover(str(test_dir), pattern="test_*.py")
        test_runner = unittest.TextTestRunner(verbosity=2)
        result = test_runner.run(test_suite)
        return result.wasSuccessful()
    else:
        logger.warning(f"Integration test directory not found: {test_dir}")
        return True


def run_benchmarks():
    """运行基准测试"""
    logger.info("="*70)
    logger.info("Running Performance Benchmarks")
    logger.info("="*70)
    
    benchmark_dir = Path(__file__).parent / "benchmark"
    if benchmark_dir.exists():
        # 查找并运行基准测试
        for benchmark_file in benchmark_dir.glob("benchmark_*.py"):
            logger.info(f"Running benchmark: {benchmark_file}")
            try:
                # 简单执行基准测试文件
                import subprocess
                subprocess.run(
                    [sys.executable, str(benchmark_file)],
                    cwd=str(Path(__file__).parent.parent)
                )
            except Exception as e:
                logger.error(f"Benchmark failed: {e}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Yunshu Test Runner")
    parser.add_argument(
        "--all", action="store_true",
        help="Run all tests (unit, integration, benchmarks)"
    )
    parser.add_argument(
        "--unit", action="store_true",
        help="Run only unit tests"
    )
    parser.add_argument(
        "--integration", action="store_true",
        help="Run only integration tests"
    )
    parser.add_argument(
        "--benchmark", action="store_true",
        help="Run only benchmarks"
    )
    
    args = parser.parse_args()
    
    success = True
    
    if args.all or args.unit:
        success = run_unit_tests() and success
    
    if args.all or args.integration:
        success = run_integration_tests() and success
    
    if args.all or args.benchmark:
        success = run_benchmarks() and success
    
    if not (args.all or args.unit or args.integration or args.benchmark):
        # 默认运行单元测试
        success = run_unit_tests()
    
    logger.info("="*70)
    if success:
        logger.info("✓ All tests passed!")
        return 0
    else:
        logger.error("✗ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
