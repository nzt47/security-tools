"""熔断器故障注入测试 - Mock 数据版

用于本地演示熔断器的自动恢复流程，包含：
1. 高错误率触发熔断
2. 熔断后快速失败
3. 自动进入半开状态试探
4. 成功恢复到关闭状态

运行方式:
    python -m pytest tests/chaos/test_circuit_breaker_mock.py -v
    或直接运行: python tests/chaos/test_circuit_breaker_mock.py
"""

import json
import logging
import random
import time
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from agent.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    CircuitBreakerError,
    get_circuit_breaker
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


class MockService:
    """模拟服务，可配置错误率"""
    
    def __init__(self, error_rate: float = 0.0):
        self._error_rate = error_rate
        self._call_count = 0
        self._error_count = 0
    
    def set_error_rate(self, error_rate: float):
        """设置错误率"""
        self._error_rate = error_rate
    
    def get_stats(self):
        """获取统计信息"""
        return {
            "call_count": self._call_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(self._call_count, 1)
        }
    
    def simulate_request(self, request_id: int) -> dict:
        """模拟请求处理"""
        self._call_count += 1
        
        if random.random() < self._error_rate:
            self._error_count += 1
            raise Exception(f"Service error for request {request_id}")
        
        return {
            "request_id": request_id,
            "status": "success",
            "data": f"Response for request {request_id}"
        }


def test_circuit_breaker_auto_recovery_demo():
    """演示熔断器自动恢复流程"""
    print("\n" + "="*60)
    print("熔断器自动恢复流程演示")
    print("="*60)
    
    # 创建模拟服务
    mock_service = MockService(error_rate=0.0)
    
    # 创建熔断器配置（宽松配置便于演示）
    config = CircuitBreakerConfig(
        failure_threshold=0.3,    # 错误率超过30%触发熔断
        reset_timeout=5,          # 熔断5秒后进入半开状态
        max_attempts=2,           # 半开状态最多允许2个试探请求
        min_requests=3            # 最小请求数3个
    )
    
    breaker = CircuitBreaker(config)
    
    @breaker.protect
    def protected_request(request_id: int):
        """受保护的请求"""
        return mock_service.simulate_request(request_id)
    
    # Phase 1: 正常状态 - 低错误率
    print("\n[阶段1] 正常状态（低错误率 10%）")
    mock_service.set_error_rate(0.1)
    success_count = 0
    failure_count = 0
    
    for i in range(1, 11):
        try:
            result = protected_request(i)
            print(f"  请求 {i}: {result['status']}")
            success_count += 1
        except Exception as e:
            print(f"  请求 {i}: 失败 - {str(e)[:50]}")
            failure_count += 1
        
        time.sleep(0.1)
    
    print(f"  统计: 成功={success_count}, 失败={failure_count}")
    print(f"  熔断器状态: {breaker.get_status()['state']}")
    
    # Phase 2: 高错误率触发熔断
    print("\n[阶段2] 高错误率触发熔断（错误率 80%）")
    mock_service.set_error_rate(0.8)
    success_count = 0
    failure_count = 0
    
    for i in range(11, 21):
        try:
            result = protected_request(i)
            print(f"  请求 {i}: {result['status']}")
            success_count += 1
        except CircuitBreakerError as e:
            print(f"  请求 {i}: 熔断触发 - {e.message}")
            failure_count += 1
        except Exception as e:
            print(f"  请求 {i}: 服务错误 - {str(e)[:50]}")
            failure_count += 1
        
        time.sleep(0.1)
    
    print(f"  统计: 成功={success_count}, 失败={failure_count}")
    print(f"  熔断器状态: {breaker.get_status()['state']}")
    
    # Phase 3: 熔断状态 - 快速失败
    print("\n[阶段3] 熔断状态（快速失败）")
    failure_count = 0
    
    for i in range(21, 26):
        try:
            result = protected_request(i)
            print(f"  请求 {i}: 意外成功")
        except CircuitBreakerError as e:
            print(f"  请求 {i}: 快速失败 - {e.message}")
            failure_count += 1
        
        time.sleep(0.1)
    
    print(f"  统计: 快速失败={failure_count}")
    print(f"  熔断器状态: {breaker.get_status()['state']}")
    
    # Phase 4: 等待恢复
    print(f"\n[阶段4] 等待恢复（{config.reset_timeout}秒）")
    time.sleep(config.reset_timeout)
    print(f"  熔断器状态: {breaker.get_status()['state']}")
    
    # Phase 5: 半开状态试探
    print("\n[阶段5] 半开状态试探（服务已修复，错误率 0%）")
    mock_service.set_error_rate(0.0)
    success_count = 0
    failure_count = 0
    
    for i in range(26, 31):
        try:
            result = protected_request(i)
            print(f"  请求 {i}: {result['status']}")
            success_count += 1
        except CircuitBreakerError as e:
            print(f"  请求 {i}: 熔断 - {e.message}")
            failure_count += 1
        except Exception as e:
            print(f"  请求 {i}: 服务错误 - {str(e)[:50]}")
            failure_count += 1
        
        time.sleep(0.2)
    
    print(f"  统计: 成功={success_count}, 失败={failure_count}")
    print(f"  熔断器状态: {breaker.get_status()['state']}")
    
    # Phase 6: 恢复正常
    print("\n[阶段6] 恢复正常状态")
    success_count = 0
    failure_count = 0
    
    for i in range(31, 41):
        try:
            result = protected_request(i)
            print(f"  请求 {i}: {result['status']}")
            success_count += 1
        except Exception as e:
            print(f"  请求 {i}: 失败 - {str(e)[:50]}")
            failure_count += 1
        
        time.sleep(0.1)
    
    print(f"  统计: 成功={success_count}, 失败={failure_count}")
    print(f"  熔断器状态: {breaker.get_status()['state']}")
    
    # 输出最终统计
    print("\n" + "="*60)
    print("最终统计")
    print("="*60)
    service_stats = mock_service.get_stats()
    breaker_stats = breaker.get_status()
    
    print(f"服务调用统计:")
    print(f"  总调用: {service_stats['call_count']}")
    print(f"  错误数: {service_stats['error_count']}")
    print(f"  错误率: {service_stats['error_rate']:.2%}")
    
    print(f"熔断器统计:")
    print(f"  总请求: {breaker_stats['metrics']['total_requests']}")
    print(f"  成功请求: {breaker_stats['metrics']['successes']}")
    print(f"  失败请求: {breaker_stats['metrics']['failures']}")
    print(f"  状态转换次数: {breaker_stats['metrics']['state_transitions']}")
    print(f"  当前状态: {breaker_stats['state']}")
    print(f"  当前错误率: {breaker_stats['current_failure_rate']:.2%}")
    
    return True


def test_circuit_breaker_with_global_instance():
    """测试使用全局熔断器实例"""
    print("\n" + "="*60)
    print("使用全局熔断器实例测试")
    print("="*60)
    
    breaker = get_circuit_breaker("api_service")
    mock_service = MockService(error_rate=0.0)
    
    @breaker.protect
    def api_request(request_id: int):
        return mock_service.simulate_request(request_id)
    
    # 正常请求
    mock_service.set_error_rate(0.0)
    for i in range(1, 6):
        try:
            result = api_request(i)
            print(f" 请求 {i}: {result['status']}")
        except Exception as e:
            print(f" 请求 {i}: 失败")
    
    # 触发熔断
    mock_service.set_error_rate(1.0)
    for i in range(6, 10):
        try:
            result = api_request(i)
            print(f" 请求 {i}: 意外成功")
        except CircuitBreakerError as e:
            print(f" 请求 {i}: 熔断触发")
        except Exception as e:
            print(f" 请求 {i}: 服务错误")
    
    print(f"熔断器状态: {breaker.get_status()['state']}")


if __name__ == "__main__":
    # 设置日志级别
    logging.getLogger("agent.circuit_breaker").setLevel(logging.INFO)
    
    # 运行演示
    test_circuit_breaker_auto_recovery_demo()
    test_circuit_breaker_with_global_instance()