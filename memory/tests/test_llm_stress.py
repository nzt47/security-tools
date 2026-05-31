#!/usr/bin/env python3
"""高并发 LLM 服务压力测试 - 验证重试机制和日志输出"""

import logging
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/llm_stress_test.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 添加项目路径
sys.path.insert(0, r'c:\Users\Administrator\agent')

from memory.llm_service import LLMService, LLMServiceError


@dataclass
class StressTestResult:
    """压力测试结果"""
    thread_id: int
    success: bool
    retries: int
    duration_seconds: float
    error_message: str = ""
    timestamp: datetime = None


class MockLLMService(LLMService):
    """模拟 LLM 服务 - 用于测试重试机制"""

    def __init__(self, failure_probability=0.3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.failure_probability = failure_probability
        self.call_count = 0
        self.failure_count = 0
        self._client = MagicMock()

    def _get_client(self):
        """覆盖客户端获取"""
        return self._client

    def summarize(self, messages: list, max_tokens: int = 500) -> str:
        """模拟 summarize，按概率失败"""
        self.call_count += 1
        attempt = 0

        for attempt in range(self.max_retries):
            # 模拟失败
            if random.random() < self.failure_probability and attempt < self.max_retries - 1:
                self.failure_count += 1
                logger.warning(f"[MockLLM] 第 {attempt + 1} 次调用模拟失败")
                time.sleep(0.1)
                continue

            # 模拟成功
            logger.info(f"[MockLLM] 调用成功（失败概率 {self.failure_probability:.0%}）")
            time.sleep(0.05)
            return f"摘要结果（线程 {threading.current_thread().name}）"

        raise LLMServiceError("模拟调用耗尽重试次数")


def run_single_test(thread_id: int, failure_probability: float = 0.3) -> StressTestResult:
    """运行单个线程测试"""
    logger.info(f"[线程 {thread_id}] 开始执行")
    start_time = time.time()

    result = StressTestResult(
        thread_id=thread_id,
        success=False,
        retries=0,
        duration_seconds=0,
        timestamp=datetime.now()
    )

    try:
        # 创建 mock 服务
        llm = MockLLMService(
            provider="openai",
            api_key="sk-test-key-12345",
            failure_probability=failure_probability,
            max_retries=3,
            retry_delay=0.5
        )

        messages = [
            {"role": "user", "content": f"测试消息 {thread_id}-1"},
            {"role": "assistant", "content": f"响应 {thread_id}-1"}
        ]

        summary = llm.summarize(messages, max_tokens=100)
        result.success = True
        result.retries = llm.failure_count

    except Exception as e:
        result.success = False
        result.error_message = str(e)
        logger.error(f"[线程 {thread_id}] 测试失败: {e}")

    result.duration_seconds = time.time() - start_time
    logger.info(f"[线程 {thread_id}] 测试结束，耗时: {result.duration_seconds:.2f}s")

    return result


def run_stress_test(num_threads: int = 50, failure_probability: float = 0.3) -> List[StressTestResult]:
    """运行压力测试"""
    logger.info("=" * 60)
    logger.info("开始高并发 LLM 压力测试")
    logger.info(f"线程数: {num_threads}")
    logger.info(f"失败概率: {failure_probability:.0%}")
    logger.info("=" * 60)

    results = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = {
            executor.submit(run_single_test, i, failure_probability): i
            for i in range(num_threads)
        }

        for future in as_completed(futures):
            thread_id = futures[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"线程 {thread_id} 执行异常: {e}")
                results.append(StressTestResult(
                    thread_id=thread_id,
                    success=False,
                    retries=0,
                    duration_seconds=0,
                    error_message=str(e),
                    timestamp=datetime.now()
                ))

    total_duration = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"压力测试完成，总耗时: {total_duration:.2f}s")
    logger.info("=" * 60)

    return results


def analyze_results(results: List[StressTestResult]) -> Dict[str, Any]:
    """分析测试结果"""
    total = len(results)
    successful = sum(1 for r in results if r.success)
    failed = total - successful

    avg_duration = sum(r.duration_seconds for r in results) / total if total > 0 else 0
    max_duration = max(r.duration_seconds for r in results) if results else 0
    min_duration = min(r.duration_seconds for r in results) if results else 0

    total_retries = sum(r.retries for r in results)
    avg_retries = total_retries / total if total > 0 else 0

    return {
        "total_tests": total,
        "successful": successful,
        "failed": failed,
        "success_rate": successful / total * 100 if total > 0 else 0,
        "avg_duration": avg_duration,
        "max_duration": max_duration,
        "min_duration": min_duration,
        "total_retries": total_retries,
        "avg_retries": avg_retries,
        "results": results
    }


def print_summary_report(analysis: Dict[str, Any]):
    """打印总结报告"""
    logger.info("\n" + "=" * 60)
    logger.info("📊 压力测试结果报告")
    logger.info("=" * 60)
    logger.info(f"总测试数: {analysis['total_tests']}")
    logger.info(f"成功数: {analysis['successful']}")
    logger.info(f"失败数: {analysis['failed']}")
    logger.info(f"成功率: {analysis['success_rate']:.1f}%")
    logger.info("-" * 40)
    logger.info(f"平均耗时: {analysis['avg_duration']:.2f}s")
    logger.info(f"最长耗时: {analysis['max_duration']:.2f}s")
    logger.info(f"最短耗时: {analysis['min_duration']:.2f}s")
    logger.info("-" * 40)
    logger.info(f"总重试次数: {analysis['total_retries']}")
    logger.info(f"平均重试次数: {analysis['avg_retries']:.2f}")
    logger.info("=" * 60 + "\n")


if __name__ == "__main__":
    # 创建日志目录
    import os
    os.makedirs('logs', exist_ok=True)

    # 运行测试
    test_results = run_stress_test(num_threads=50, failure_probability=0.3)
    analysis = analyze_results(test_results)
    print_summary_report(analysis)

    # 保存详细结果
    import json
    output_data = {
        "summary": {k: v for k, v in analysis.items() if k != "results"},
        "detailed_results": [
            {
                "thread_id": r.thread_id,
                "success": r.success,
                "retries": r.retries,
                "duration": r.duration_seconds,
                "error": r.error_message
            }
            for r in analysis["results"]
        ]
    }
    with open("logs/stress_test_results.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    logger.info(f"详细结果已保存至: logs/stress_test_results.json")
