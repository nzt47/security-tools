"""
性能测试 - 延迟测试
测试各模块的响应时间和性能指标
"""
import pytest
import time
import statistics
import logging
from unittest.mock import MagicMock, patch
from agent.error_handler import ErrorHandler, CircuitBreaker, RetryPolicy
from agent.lazy_loader import LazyModuleLoader, LoadLevel
from agent.security_utils import DataSanitizer, LogEncryptor

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class TestErrorHandlerPerformance:
    """测试错误处理器性能"""

    @pytest.mark.performance
    @pytest.mark.p1
    def test_error_handler_record_latency(self):
        """测试错误记录延迟"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试错误记录延迟")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化错误处理器")
        handler = ErrorHandler()
        logger.info("  - 错误处理器初始化完成")
        
        from agent.error_handler import YunshuError
        
        logger.info(f"[测试阶段] 执行 {1000} 次错误记录")
        times = []
        for i in range(1000):
            error = YunshuError("test error")
            start = time.perf_counter()
            handler.record_error(error)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            
            if (i + 1) % 200 == 0:
                logger.info(f"  - 已完成 {i + 1}/{1000} 次记录")
        
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)
        p95_time = statistics.quantiles(times, n=20)[18]
        p99_time = statistics.quantiles(times, n=100)[98]
        
        logger.info("\n[结果分析] 错误记录性能统计:")
        logger.info(f"  平均延迟: {avg_time:.4f} ms")
        logger.info(f"  最大延迟: {max_time:.4f} ms")
        logger.info(f"  最小延迟: {min_time:.4f} ms")
        logger.info(f"  P95延迟: {p95_time:.4f} ms")
        logger.info(f"  P99延迟: {p99_time:.4f} ms")
        logger.info(f"  标准差: {statistics.stdev(times):.4f} ms")
        
        assert avg_time < 1.0, f"平均延迟过高: {avg_time:.2f}ms"
        logger.info("  ✓ 性能达标：平均延迟 < 1ms")
        
        logger.info("[性能测试] 错误记录延迟测试通过")
        logger.info("=" * 70)

    @pytest.mark.performance
    @pytest.mark.p1
    def test_circuit_breaker_execute_latency(self):
        """测试熔断器执行延迟"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试熔断器执行延迟")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化熔断器")
        cb = CircuitBreaker(max_failures=10)
        logger.info(f"  - 熔断器初始化完成, max_failures={cb.max_failures}")
        
        def simple_func():
            return "success"
        
        logger.info(f"[测试阶段] 执行 {1000} 次熔断器调用")
        times = []
        for i in range(1000):
            start = time.perf_counter()
            cb.execute(simple_func)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            
            if (i + 1) % 200 == 0:
                logger.info(f"  - 已完成 {i + 1}/{1000} 次调用")
        
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)
        
        logger.info("\n[结果分析] 熔断器执行性能统计:")
        logger.info(f"  平均延迟: {avg_time:.4f} ms")
        logger.info(f"  最大延迟: {max_time:.4f} ms")
        logger.info(f"  最小延迟: {min_time:.4f} ms")
        
        assert avg_time < 0.1, f"熔断器延迟过高: {avg_time:.2f}ms"
        logger.info("  ✓ 性能达标：平均延迟 < 0.1ms")
        
        logger.info("[性能测试] 熔断器执行延迟测试通过")
        logger.info("=" * 70)


class TestLazyLoaderPerformance:
    """测试懒加载器性能"""

    @pytest.mark.performance
    @pytest.mark.p0
    def test_module_register_performance(self):
        """测试模块注册性能"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试模块注册性能")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化懒加载器")
        loader = LazyModuleLoader()
        logger.info("  - 懒加载器初始化完成")
        
        logger.info(f"[测试阶段] 注册 {100} 个模块")
        times = []
        for i in range(100):
            start = time.perf_counter()
            loader.register(f"module_{i}", lambda: f"instance_{i}", LoadLevel.IMPORTANT)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            
            if (i + 1) % 25 == 0:
                logger.info(f"  - 已完成 {i + 1}/{100} 个模块注册")
        
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)
        
        logger.info("\n[结果分析] 模块注册性能统计:")
        logger.info(f"  平均延迟: {avg_time:.4f} ms")
        logger.info(f"  最大延迟: {max_time:.4f} ms")
        logger.info(f"  最小延迟: {min_time:.4f} ms")
        logger.info(f"  总注册模块数: {len(loader.modules)}")
        
        assert avg_time < 0.5, f"模块注册延迟过高: {avg_time:.2f}ms"
        logger.info("  ✓ 性能达标：平均延迟 < 0.5ms")
        
        logger.info("[性能测试] 模块注册性能测试通过")
        logger.info("=" * 70)

    @pytest.mark.performance
    @pytest.mark.p0
    def test_module_load_performance(self):
        """测试模块加载性能"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试模块加载性能")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化懒加载器并注册模块")
        loader = LazyModuleLoader()
        
        for i in range(50):
            loader.register(f"module_{i}", lambda: "instance", LoadLevel.CRITICAL)
        
        logger.info(f"  - 已注册 {50} 个CRITICAL级别模块")
        
        logger.info("[测试阶段] 执行批量加载")
        start = time.perf_counter()
        loader.load_level(LoadLevel.CRITICAL)
        elapsed = (time.perf_counter() - start) * 1000
        
        logger.info("\n[结果分析] 模块加载性能统计:")
        logger.info(f"  总耗时: {elapsed:.4f} ms")
        logger.info(f"  模块数量: {50}")
        logger.info(f"  平均每模块耗时: {elapsed / 50:.4f} ms")
        
        assert elapsed < 150.0, f"模块加载耗时过高: {elapsed:.2f}ms"
        logger.info("  ✓ 性能达标：总耗时 < 150ms")
        
        logger.info("[性能测试] 模块加载性能测试通过")
        logger.info("=" * 70)


class TestSecurityPerformance:
    """测试安全模块性能"""

    @pytest.mark.performance
    @pytest.mark.p0
    def test_data_sanitizer_performance(self):
        """测试数据脱敏性能"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试数据脱敏性能")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化数据脱敏器")
        sanitizer = DataSanitizer()
        logger.info("  - 数据脱敏器初始化完成")
        
        test_text = "API Key=sk-abcdefghijklmnopqrstuvwxyz, password=secret123, email=user@example.com"
        logger.info(f"  - 测试文本长度: {len(test_text)} 字符")
        
        logger.info(f"[测试阶段] 执行 {1000} 次脱敏处理")
        times = []
        for i in range(1000):
            start = time.perf_counter()
            sanitizer.sanitize_string(test_text)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            
            if (i + 1) % 200 == 0:
                logger.info(f"  - 已完成 {i + 1}/{1000} 次处理")
        
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)
        p95_time = statistics.quantiles(times, n=20)[18]
        
        logger.info("\n[结果分析] 数据脱敏性能统计:")
        logger.info(f"  平均延迟: {avg_time:.4f} ms")
        logger.info(f"  最大延迟: {max_time:.4f} ms")
        logger.info(f"  最小延迟: {min_time:.4f} ms")
        logger.info(f"  P95延迟: {p95_time:.4f} ms")
        
        assert avg_time < 0.5, f"数据脱敏延迟过高: {avg_time:.2f}ms"
        logger.info("  ✓ 性能达标：平均延迟 < 0.5ms")
        
        logger.info("[性能测试] 数据脱敏性能测试通过")
        logger.info("=" * 70)

    @pytest.mark.performance
    @pytest.mark.p1
    def test_encryptor_performance(self):
        """测试加密器性能"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试加密器性能")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化加密器")
        import os
        
        with patch.dict(os.environ, {"TEST_PERF_KEY": ""}):
            encryptor = LogEncryptor(key_env_var="TEST_PERF_KEY")
            logger.info("  - 加密器初始化完成")
            
            test_data = "这是一段测试数据，用于性能测试"
            logger.info(f"  - 测试数据长度: {len(test_data)} 字符")
            
            logger.info(f"[测试阶段] 执行 {100} 次加密")
            times = []
            for i in range(100):
                start = time.perf_counter()
                encryptor.encrypt_string(test_data)
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)
                
                if (i + 1) % 25 == 0:
                    logger.info(f"  - 已完成 {i + 1}/{100} 次加密")
            
            avg_time = statistics.mean(times)
            max_time = max(times)
            min_time = min(times)
            
            logger.info("\n[结果分析] 加密性能统计:")
            logger.info(f"  平均延迟: {avg_time:.4f} ms")
            logger.info(f"  最大延迟: {max_time:.4f} ms")
            logger.info(f"  最小延迟: {min_time:.4f} ms")
        
        logger.info("[性能测试] 加密器性能测试完成")
        logger.info("=" * 70)


class TestRetryPolicyPerformance:
    """测试重试策略性能"""

    @pytest.mark.performance
    @pytest.mark.p1
    def test_retry_policy_calculate_delay(self):
        """测试重试延迟计算性能"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试重试延迟计算性能")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化重试策略")
        policy = RetryPolicy(
            max_retries=5,
            initial_delay=1.0,
            max_delay=30.0,
            backoff_factor=2.0,
            jitter_factor=0.1
        )
        logger.info(f"  - 重试策略初始化完成: max_retries={policy.max_retries}")
        
        logger.info(f"[测试阶段] 执行 {1000} 次延迟计算（每次计算5个尝试的延迟）")
        times = []
        for i in range(1000):
            start = time.perf_counter()
            for attempt in range(5):
                policy.calculate_delay(attempt)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
            
            if (i + 1) % 200 == 0:
                logger.info(f"  - 已完成 {i + 1}/{1000} 次计算")
        
        avg_time = statistics.mean(times)
        max_time = max(times)
        min_time = min(times)
        
        logger.info("\n[结果分析] 重试延迟计算性能统计:")
        logger.info(f"  平均延迟: {avg_time:.4f} ms")
        logger.info(f"  最大延迟: {max_time:.4f} ms")
        logger.info(f"  最小延迟: {min_time:.4f} ms")
        
        assert avg_time < 0.1, f"重试延迟计算延迟过高: {avg_time:.2f}ms"
        logger.info("  ✓ 性能达标：平均延迟 < 0.1ms")
        
        logger.info("[性能测试] 重试延迟计算性能测试通过")
        logger.info("=" * 70)


class TestConcurrencyPerformance:
    """测试并发性能"""

    @pytest.mark.performance
    @pytest.mark.p1
    def test_parallel_preloader_performance(self):
        """测试并行预加载器性能"""
        logger.info("=" * 70)
        logger.info("[性能测试] 开始测试并行预加载器性能")
        logger.info("=" * 70)
        
        logger.info("[准备阶段] 初始化并行预加载器")
        from agent.lazy_loader import ParallelPreloader
        
        preloader = ParallelPreloader(max_workers=4)
        logger.info(f"  - 并行预加载器初始化完成: max_workers=4")
        
        def load_func():
            time.sleep(0.01)
            return "done"
        
        modules = [(f"mod_{i}", load_func) for i in range(10)]
        logger.info(f"  - 准备 {10} 个测试模块，每个模块模拟 10ms 加载时间")
        logger.info(f"  - 串行加载预计耗时: ~{10 * 10}ms")
        logger.info(f"  - 并行加载(4线程)预计耗时: ~{10 * 10 / 4}ms")
        
        logger.info("[测试阶段] 执行并行预加载")
        start = time.perf_counter()
        preloader.preload(modules)
        elapsed = (time.perf_counter() - start) * 1000
        
        logger.info("\n[结果分析] 并行预加载性能统计:")
        logger.info(f"  总耗时: {elapsed:.4f} ms")
        logger.info(f"  加速比: {(10 * 10) / elapsed:.2f}x")
        
        assert elapsed < 50.0, f"并行加载耗时过高: {elapsed:.2f}ms"
        logger.info("  ✓ 性能达标：总耗时 < 50ms")
        
        logger.info("[清理阶段] 关闭预加载器")
        preloader.shutdown()
        logger.info("  - 预加载器已关闭")
        
        logger.info("[性能测试] 并行预加载器性能测试通过")
        logger.info("=" * 70)