"""
execute_with_retry 参数传递测试
防止参数重复问题（func_args/func_kwargs 方案）

测试场景：
1. 使用 func_args 和 func_kwargs 正确传递参数
2. 确保不会出现 "got multiple values for argument" 错误
3. 测试 with_retry 装饰器正确传递参数
4. 测试参数中包含与具名参数同名键的情况
"""
import pytest
from unittest.mock import MagicMock, patch
from agent.error_handler import (
    ErrorHandler,
    RetryPolicy,
    CircuitBreaker,
    YunshuError,
    with_retry,
    RecoverableError,
)


class TestExecuteWithRetryArgsKwargs:
    """测试 execute_with_retry 的 func_args/func_kwargs 参数传递"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_func_args_kwargs_basic(self):
        """测试基本的 func_args 和 func_kwargs 传递"""
        handler = ErrorHandler()

        def add_func(a, b, c=0):
            return a + b + c

        # 使用 func_args 和 func_kwargs
        result = handler.execute_with_retry(
            add_func,
            func_args=(1, 2),
            func_kwargs={"c": 3}
        )

        assert result == 6

    @pytest.mark.unit
    @pytest.mark.p0
    def test_func_args_only(self):
        """测试只使用 func_args"""
        handler = ErrorHandler()

        def multiply(a, b):
            return a * b

        result = handler.execute_with_retry(
            multiply,
            func_args=(3, 4)
        )

        assert result == 12

    @pytest.mark.unit
    @pytest.mark.p0
    def test_func_kwargs_only(self):
        """测试只使用 func_kwargs"""
        handler = ErrorHandler()

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = handler.execute_with_retry(
            greet,
            func_kwargs={"name": "World", "greeting": "Hi"}
        )

        assert result == "Hi, World!"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_func_args_kwargs(self):
        """测试不传递 func_args 和 func_kwargs（函数无参数）"""
        handler = ErrorHandler()

        def no_args_func():
            return "success"

        result = handler.execute_with_retry(no_args_func)

        assert result == "success"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_func_kwargs_with_same_name_as_method_param(self):
        """测试 func_kwargs 中包含与方法参数同名的键
        
        这是之前导致 "got multiple values for argument" 错误的场景
        现在使用 func_kwargs 方案应该不会出现这个问题
        """
        handler = ErrorHandler()

        def func_with_retry_policy_param(retry_policy, other_param):
            # 函数参数名与 execute_with_retry 的参数名相同
            return f"retry_policy={retry_policy}, other={other_param}"

        # func_kwargs 中包含 "retry_policy" 键，不应该与方法参数冲突
        result = handler.execute_with_retry(
            func_with_retry_policy_param,
            func_kwargs={"retry_policy": "custom_value", "other_param": "test"}
        )

        assert result == "retry_policy=custom_value, other=test"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_func_kwargs_with_circuit_breaker_name(self):
        """测试 func_kwargs 中包含 circuit_breaker 键"""
        handler = ErrorHandler()

        def func_with_cb_param(circuit_breaker, value):
            return f"cb={circuit_breaker}, value={value}"

        result = handler.execute_with_retry(
            func_with_cb_param,
            func_kwargs={"circuit_breaker": "my_cb", "value": 42}
        )

        assert result == "cb=my_cb, value=42"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_func_kwargs_with_all_reserved_names(self):
        """测试 func_kwargs 中包含所有保留参数名"""
        handler = ErrorHandler()

        def func_with_all_params(retry_policy, circuit_breaker, retryable_exceptions, 
                                  on_retry, error_counter, func_args, func_kwargs, extra):
            return "all_params_received"

        result = handler.execute_with_retry(
            func_with_all_params,
            func_kwargs={
                "retry_policy": "rp",
                "circuit_breaker": "cb",
                "retryable_exceptions": "re",
                "on_retry": "or",
                "error_counter": "ec",
                "func_args": "fa",
                "func_kwargs": "fk",
                "extra": "ex"
            }
        )

        assert result == "all_params_received"


class TestWithRetryDecoratorArgs:
    """测试 with_retry 装饰器的参数传递"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_basic_args(self):
        """测试 with_retry 装饰器的基本参数传递"""
        @with_retry(max_retries=2, initial_delay=0.1)
        def add(a, b):
            return a + b

        result = add(1, 2)
        assert result == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_with_kwargs(self):
        """测试 with_retry 装饰器的关键字参数传递"""
        @with_retry(max_retries=2, initial_delay=0.1)
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = greet("World", greeting="Hi")
        assert result == "Hi, World!"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_with_reserved_name_kwargs(self):
        """测试 with_retry 装饰器传递包含保留名的 kwargs
        
        这是之前导致错误的场景：如果 kwargs 中包含 retry_policy 等键
        """
        @with_retry(max_retries=2, initial_delay=0.1)
        def func_with_reserved_param(retry_policy, value):
            return f"rp={retry_policy}, v={value}"

        # 调用时传递 retry_policy 作为函数参数，不应该与方法参数冲突
        result = func_with_reserved_param("custom_policy", value=42)
        assert result == "rp=custom_policy, v=42"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_with_circuit_breaker_kwargs(self):
        """测试 with_retry 装饰器传递 circuit_breaker 作为函数参数"""
        @with_retry(max_retries=2, initial_delay=0.1)
        def func_with_cb(circuit_breaker, data):
            return f"cb={circuit_breaker}, data={data}"

        result = func_with_cb("my_circuit_breaker", data="test_data")
        assert result == "cb=my_circuit_breaker, data=test_data"


class TestExecuteWithRetryWithRetryPolicy:
    """测试 execute_with_retry 配合 RetryPolicy"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_custom_retry_policy(self):
        """测试使用自定义 RetryPolicy"""
        handler = ErrorHandler()
        policy = RetryPolicy(max_retries=2, initial_delay=0.1)

        call_count = [0]

        def sometimes_fail():
            call_count[0] += 1
            if call_count[0] < 2:
                raise RecoverableError("temporary error")
            return "success"

        result = handler.execute_with_retry(
            sometimes_fail,
            retry_policy=policy
        )

        assert result == "success"
        assert call_count[0] == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_policy_and_func_args(self):
        """测试 RetryPolicy 配合 func_args"""
        handler = ErrorHandler()
        policy = RetryPolicy(max_retries=2, initial_delay=0.1)

        call_count = [0]

        def sometimes_fail_with_args(a, b):
            call_count[0] += 1
            if call_count[0] < 2:
                raise RecoverableError("temporary error")
            return a + b

        result = handler.execute_with_retry(
            sometimes_fail_with_args,
            retry_policy=policy,
            func_args=(1, 2)
        )

        assert result == 3
        assert call_count[0] == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_retry_policy_and_func_kwargs(self):
        """测试 RetryPolicy 配合 func_kwargs"""
        handler = ErrorHandler()
        policy = RetryPolicy(max_retries=2, initial_delay=0.1)

        call_count = [0]

        def sometimes_fail_with_kwargs(a, b=0):
            call_count[0] += 1
            if call_count[0] < 2:
                raise RecoverableError("temporary error")
            return a + b

        result = handler.execute_with_retry(
            sometimes_fail_with_kwargs,
            retry_policy=policy,
            func_kwargs={"a": 1, "b": 2}
        )

        assert result == 3
        assert call_count[0] == 2


class TestExecuteWithRetryWithCircuitBreaker:
    """测试 execute_with_retry 配合 CircuitBreaker"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_circuit_breaker_and_func_args(self):
        """测试 CircuitBreaker 配合 func_args"""
        handler = ErrorHandler()
        cb = CircuitBreaker(name="test_cb", max_failures=3)
        handler.register_circuit_breaker("test", cb)

        def add(a, b):
            return a + b

        result = handler.execute_with_retry(
            add,
            circuit_breaker=cb,
            func_args=(1, 2)
        )

        assert result == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_with_circuit_breaker_and_func_kwargs(self):
        """测试 CircuitBreaker 配合 func_kwargs"""
        handler = ErrorHandler()
        cb = CircuitBreaker(name="test_cb2", max_failures=3)
        handler.register_circuit_breaker("test2", cb)

        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"

        result = handler.execute_with_retry(
            greet,
            circuit_breaker=cb,
            func_kwargs={"name": "World", "greeting": "Hi"}
        )

        assert result == "Hi, World!"


class TestExecuteWithRetryErrorCounter:
    """测试 execute_with_retry 的 error_counter 参数"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_counter_with_func_args(self):
        """测试 error_counter 配合 func_args"""
        handler = ErrorHandler()

        with patch('agent.monitoring.metrics.get_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_collector.return_value = mock_instance

            def success_func(a, b):
                return a + b

            result = handler.execute_with_retry(
                success_func,
                error_counter="test_counter",
                func_args=(1, 2)
            )

            assert result == 3
            mock_instance.increment_counter.assert_called_with("test_counter.success")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_counter_with_func_kwargs(self):
        """测试 error_counter 配合 func_kwargs"""
        handler = ErrorHandler()

        with patch('agent.monitoring.metrics.get_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_collector.return_value = mock_instance

            def success_func(a, b=0):
                return a + b

            result = handler.execute_with_retry(
                success_func,
                error_counter="test_counter2",
                func_kwargs={"a": 1, "b": 2}
            )

            assert result == 3
            mock_instance.increment_counter.assert_called_with("test_counter2.success")


class TestExecuteWithRetryOnRetryCallback:
    """测试 execute_with_retry 的 on_retry 回调"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_on_retry_callback_with_func_args(self):
        """测试 on_retry 回调配合 func_args"""
        handler = ErrorHandler()
        retry_count = [0]

        def on_retry_callback(attempt, exc):
            retry_count[0] = attempt

        call_count = [0]

        def sometimes_fail(a, b):
            call_count[0] += 1
            if call_count[0] < 2:
                raise RecoverableError("temporary error")
            return a + b

        result = handler.execute_with_retry(
            sometimes_fail,
            retry_policy=RetryPolicy(max_retries=2, initial_delay=0.1),
            on_retry=on_retry_callback,
            func_args=(1, 2)
        )

        assert result == 3
        assert retry_count[0] == 1  # 第一次重试时调用回调

    @pytest.mark.unit
    @pytest.mark.p0
    def test_on_retry_callback_with_func_kwargs(self):
        """测试 on_retry 回调配合 func_kwargs"""
        handler = ErrorHandler()
        retry_count = [0]

        def on_retry_callback(attempt, exc):
            retry_count[0] = attempt

        call_count = [0]

        def sometimes_fail(a, b=0):
            call_count[0] += 1
            if call_count[0] < 2:
                raise RecoverableError("temporary error")
            return a + b

        result = handler.execute_with_retry(
            sometimes_fail,
            retry_policy=RetryPolicy(max_retries=2, initial_delay=0.1),
            on_retry=on_retry_callback,
            func_kwargs={"a": 1, "b": 2}
        )

        assert result == 3
        assert retry_count[0] == 1


class TestExecuteWithRetryAllParams:
    """测试 execute_with_retry 所有参数组合"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_all_params_combined(self):
        """测试所有参数组合使用"""
        handler = ErrorHandler()
        cb = CircuitBreaker(name="combined_cb", max_failures=3)
        handler.register_circuit_breaker("combined", cb)

        retry_count = [0]

        def on_retry_callback(attempt, exc):
            retry_count[0] = attempt

        call_count = [0]

        def sometimes_fail(a, b, c=0):
            call_count[0] += 1
            if call_count[0] < 2:
                raise RecoverableError("temporary error")
            return a + b + c

        with patch('agent.monitoring.metrics.get_metrics_collector') as mock_collector:
            mock_instance = MagicMock()
            mock_collector.return_value = mock_instance

            result = handler.execute_with_retry(
                sometimes_fail,
                retry_policy=RetryPolicy(max_retries=2, initial_delay=0.1),
                circuit_breaker=cb,
                retryable_exceptions=(RecoverableError,),
                on_retry=on_retry_callback,
                error_counter="combined_counter",
                func_args=(1, 2),
                func_kwargs={"c": 3}
            )

            assert result == 6
            assert retry_count[0] == 1
            # 注意：metrics 只在第一次尝试成功时记录，这里第一次失败了，所以不调用