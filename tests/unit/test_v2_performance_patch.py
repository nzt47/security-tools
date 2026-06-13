"""V2 Performance Patch 单元测试"""
import pytest
import time
import threading
from unittest.mock import MagicMock, patch

from agent.v2_performance_patch import LazyInitializer, AsyncInitializer, optimize_v2_initialization


class TestLazyInitializer:
    """测试懒加载初始化器"""

    def test_init_basic(self):
        """测试基本初始化"""
        init_func = MagicMock(return_value="result")
        init_func.__name__ = "mock_init"  # 添加 __name__ 属性
        
        lazy = LazyInitializer(init_func)
        
        assert lazy._init_func == init_func
        assert lazy._args == ()
        assert lazy._kwargs == {}
        assert lazy._instance is None
        assert lazy._initialized is False
        assert hasattr(lazy, '_lock')

    def test_get_first_call(self):
        """测试首次调用 get()"""
        init_func = MagicMock(return_value="result")
        init_func.__name__ = "mock_init"
        
        lazy = LazyInitializer(init_func)
        
        result = lazy.get()
        
        assert result == "result"
        init_func.assert_called_once()
        assert lazy._initialized is True
        assert lazy._instance == "result"

    def test_get_subsequent_calls(self):
        """测试后续调用 get()"""
        init_func = MagicMock(return_value="result")
        init_func.__name__ = "mock_init"
        
        lazy = LazyInitializer(init_func)
        
        result1 = lazy.get()
        result2 = lazy.get()
        result3 = lazy.get()
        
        assert result1 == "result"
        assert result2 == "result"
        assert result3 == "result"
        init_func.assert_called_once()  # 只调用一次

    def test_get_with_args_kwargs(self):
        """测试带参数的懒加载"""
        init_func = MagicMock(return_value="result")
        init_func.__name__ = "mock_init"
        
        lazy = LazyInitializer(init_func, "arg1", "arg2", key1="value1", key2="value2")
        
        result = lazy.get()
        
        init_func.assert_called_once_with("arg1", "arg2", key1="value1", key2="value2")
        assert result == "result"

    def test_is_initialized(self):
        """测试 is_initialized()"""
        init_func = MagicMock(return_value="result")
        init_func.__name__ = "mock_init"
        
        lazy = LazyInitializer(init_func)
        
        assert lazy.is_initialized() is False
        
        lazy.get()
        
        assert lazy.is_initialized() is True

    def test_force_init(self):
        """测试 force_init()"""
        init_func = MagicMock(return_value="result")
        init_func.__name__ = "mock_init"
        
        lazy = LazyInitializer(init_func)
        
        result = lazy.force_init()
        
        assert result == "result"
        assert lazy.is_initialized() is True

    def test_thread_safety(self):
        """测试线程安全"""
        call_count = [0]
        
        def slow_init():
            call_count[0] += 1
            time.sleep(0.05)
            return "result"
        
        lazy = LazyInitializer(slow_init)
        
        # 多个线程同时调用 get()
        threads = []
        results = []
        for _ in range(5):
            def get_result():
                results.append(lazy.get())
            t = threading.Thread(target=get_result)
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        assert call_count[0] == 1  # 初始化只执行一次
        assert all(r == "result" for r in results)

    def test_init_func_raises_exception(self):
        """测试初始化函数抛出异常"""
        init_func = MagicMock(side_effect=Exception("init error"))
        init_func.__name__ = "mock_init"
        
        lazy = LazyInitializer(init_func)
        
        with pytest.raises(Exception):
            lazy.get()
        
        assert lazy.is_initialized() is False
        assert lazy._instance is None


class TestAsyncInitializer:
    """测试异步初始化器"""

    def test_init_basic(self):
        """测试基本初始化"""
        initializer = AsyncInitializer(max_workers=3)
        
        assert initializer._executor is not None
        assert initializer._futures == {}
        assert hasattr(initializer, '_results')
        assert hasattr(initializer, '_lock')

    def test_submit_task(self):
        """测试提交任务"""
        initializer = AsyncInitializer(max_workers=3)
        init_func = MagicMock(return_value="result")
        
        future = initializer.submit("task1", init_func)
        
        assert "task1" in initializer._futures
        assert future == initializer._futures["task1"]

    def test_wait_all_tasks(self):
        """测试等待所有任务完成"""
        initializer = AsyncInitializer(max_workers=3)
        
        def slow_task():
            time.sleep(0.01)
            return "result"
        
        initializer.submit("task1", slow_task)
        initializer.submit("task2", slow_task)
        
        results = initializer.wait()
        
        assert "task1" in results
        assert "task2" in results
        assert results["task1"] == "result"
        assert results["task2"] == "result"

    def test_wait_with_timeout(self):
        """测试带超时的等待"""
        initializer = AsyncInitializer(max_workers=3)
        
        def very_slow_task():
            time.sleep(1)
            return "result"
        
        initializer.submit("slow_task", very_slow_task)
        
        results = initializer.wait(timeout=0.01)
        
        assert "slow_task" in results
        assert results["slow_task"] is None  # 超时返回 None

    def test_wait_with_exception(self):
        """测试任务抛出异常"""
        initializer = AsyncInitializer(max_workers=3)
        
        def failing_task():
            raise Exception("task failed")
        
        initializer.submit("failing_task", failing_task)
        
        results = initializer.wait()
        
        assert "failing_task" in results
        assert results["failing_task"] is None

    def test_get_single_result(self):
        """测试获取单个任务结果"""
        initializer = AsyncInitializer(max_workers=3)
        
        initializer.submit("task1", lambda: "result1")
        
        result = initializer.get_result("task1")
        
        assert result == "result1"

    def test_get_result_nonexistent(self):
        """测试获取不存在的任务结果"""
        initializer = AsyncInitializer(max_workers=3)
        
        result = initializer.get_result("nonexistent")
        
        assert result is None

    def test_shutdown(self):
        """测试关闭线程池"""
        initializer = AsyncInitializer(max_workers=3)
        
        initializer.shutdown(wait=True)
        
        # 验证线程池已关闭（通过尝试提交任务）
        with pytest.raises(Exception):
            initializer.submit("task", lambda: "result")

    def test_parallel_execution(self):
        """测试并行执行"""
        initializer = AsyncInitializer(max_workers=2)
        
        start_times = {}
        end_times = {}
        
        def timed_task(name):
            start_times[name] = time.time()
            time.sleep(0.02)
            end_times[name] = time.time()
            return f"{name}_result"
        
        initializer.submit("task1", timed_task, "task1")
        initializer.submit("task2", timed_task, "task2")
        
        results = initializer.wait()
        
        assert results["task1"] == "task1_result"
        assert results["task2"] == "task2_result"
        
        # 验证两个任务几乎同时开始（并行执行）
        time_diff = abs(start_times["task1"] - start_times["task2"])
        assert time_diff < 0.01  # 启动时间差小于10ms


class TestOptimizeV2Initialization:
    """测试优化装饰器"""

    def test_decorator_basic(self):
        """测试装饰器基本功能"""
        class MockV2Class:
            def __init__(self, config=None):
                self.config = config
                self.initialized = True
            def _init_core_modules(self, config):
                pass
        
        OptimizedClass = optimize_v2_initialization(MockV2Class)
        
        instance = OptimizedClass(config={"test": "value"})
        
        assert hasattr(instance, '_running')
        assert hasattr(instance, '_current_mode')
        assert hasattr(instance, '_session_id')
        assert hasattr(instance, '_interaction_count')
        assert hasattr(instance, '_reflection_history')
        assert hasattr(instance, '_started_at')

    def test_decorator_without_config(self):
        """测试不带配置的初始化"""
        class MockV2Class:
            def __init__(self, config=None):
                self.config = config
            def _init_core_modules(self, config):
                pass
        
        OptimizedClass = optimize_v2_initialization(MockV2Class)
        
        instance = OptimizedClass()
        
        assert instance._running is False
        assert instance._current_mode is None
        assert instance._session_id is None
        assert instance._interaction_count == 0
        assert instance._reflection_history == []
        assert instance._started_at is None

    def test_decorator_logging(self):
        """测试装饰器日志记录"""
        class MockV2Class:
            def __init__(self, config=None):
                pass
            def _init_core_modules(self, config):
                pass
        
        with patch('agent.v2_performance_patch.logger') as mock_logger:
            OptimizedClass = optimize_v2_initialization(MockV2Class)
            instance = OptimizedClass()
            
            mock_logger.info.assert_any_call("V2 优化初始化 - 第一阶段：核心模块")
            mock_logger.info.assert_any_call("V2 优化初始化 - 第二阶段：并行初始化")


class TestEdgeCases:
    """测试边缘情况"""

    def test_lazy_initializer_with_none_result(self):
        """测试懒加载返回 None"""
        init_func = MagicMock(return_value=None)
        init_func.__name__ = "mock_init"
        
        lazy = LazyInitializer(init_func)
        
        result = lazy.get()
        
        assert result is None
        assert lazy.is_initialized() is True

    def test_lazy_initializer_with_no_args(self):
        """测试不带参数的懒加载"""
        def no_arg_func():
            return "no_arg_result"
        
        lazy = LazyInitializer(no_arg_func)
        
        result = lazy.get()
        
        assert result == "no_arg_result"

    def test_async_initializer_with_max_workers_zero(self):
        """测试最大工作线程数为零"""
        with pytest.raises(ValueError):
            AsyncInitializer(max_workers=0)

    def test_async_initializer_with_negative_workers(self):
        """测试负工作线程数"""
        with pytest.raises(ValueError):
            AsyncInitializer(max_workers=-1)

    def test_multiple_lazy_initializers(self):
        """测试多个懒加载器"""
        init_func1 = MagicMock(return_value="result1")
        init_func1.__name__ = "mock_init1"
        init_func2 = MagicMock(return_value="result2")
        init_func2.__name__ = "mock_init2"
        
        lazy1 = LazyInitializer(init_func1)
        lazy2 = LazyInitializer(init_func2)
        
        result1 = lazy1.get()
        result2 = lazy2.get()
        
        assert result1 == "result1"
        assert result2 == "result2"
        init_func1.assert_called_once()
        init_func2.assert_called_once()

    def test_async_initializer_with_empty_tasks(self):
        """测试空任务列表"""
        initializer = AsyncInitializer(max_workers=3)
        
        results = initializer.wait()
        
        assert results == {}
