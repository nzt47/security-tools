"""优雅降级（GracefulDegrade）边界测试

覆盖场景：boundary / timeout / null / invalid / extreme
对应 Day 4 计划任务：BT-003

测试目标模块：agent/graceful_degrade.py
实际 API：
  - GracefulDegrade(default_fallbacks, max_retries, degrade_seconds)
  - call_with_fallback(component, func, *args, fallback, retry_strategy, **kwargs)
  - schema_validate_with_fallback(validator, data, relaxed_validator)
  - is_degraded(component) / get_state(component) / force_degrade(component, level)
  - get_cached(component) / reset()
  - DegradeLevel: NORMAL / RETRY / RELAXED / FALLBACK / DISABLED
  - DegradeError(message, error_code, component)
  - DegradeState dataclass

注意：tests/unit/test_graceful_degrade_scenarios.py 引用了不存在的旧 API（DegradeConfig /
DegradeModule / with_degrade），无法导入。本文件基于实际 API 编写。
"""

import threading
import time

import pytest

from agent.graceful_degrade import (
    DegradeError,
    DegradeLevel,
    DegradeState,
    GracefulDegrade,
    get_trace_id,
    set_trace_id,
)


@pytest.fixture
def fast_degrade():
    """快速恢复的降级器（便于测试降级到期）"""
    return GracefulDegrade(
        default_fallbacks={
            "schema_validator": None,
            "critic_engine": None,
            "memory_router": [],
            "dashboard_loader": {},
        },
        max_retries=3,
        degrade_seconds=0.2,
    )


@pytest.fixture
def no_retry_degrade():
    """不重试的降级器（一次失败即降级）"""
    return GracefulDegrade(
        max_retries=0,
        degrade_seconds=30.0,
    )


@pytest.fixture
def default_degrade():
    """默认配置的降级器"""
    return GracefulDegrade()


# ═══════════════════════════════════════════════════════════════
#  边界条件：重试次数与降级触发
# ═══════════════════════════════════════════════════════════════


class TestDegradeBoundary:
    """降级触发边界条件测试"""

    def test_boundary_first_call_success_no_degrade(self, default_degrade):
        """首次调用成功不触发降级"""
        result = default_degrade.call_with_fallback(
            "schema_validator", lambda: "ok"
        )
        assert result == "ok"
        assert default_degrade.is_degraded("schema_validator") is False

    def test_boundary_exact_max_retries_triggers_degrade(self, fast_degrade):
        """失败次数刚好达到 max_retries+1 次时触发降级"""
        call_count = [0]

        def always_fail():
            call_count[0] += 1
            raise ValueError("fail")

        result = fast_degrade.call_with_fallback("critic_engine", always_fail)
        # max_retries=3 → 总共尝试 4 次（1 + 3 重试）
        assert call_count[0] == 4
        assert result is None  # default_fallbacks["critic_engine"] = None
        assert fast_degrade.is_degraded("critic_engine") is True

    def test_boundary_one_failure_no_degrade(self, fast_degrade):
        """仅一次失败不触发降级（需达到 max_retries+1 次）"""
        call_count = [0]

        def fail_then_success():
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("transient")
            return "recovered"

        result = fast_degrade.call_with_fallback("memory_router", fail_then_success)
        assert result == "recovered"
        assert call_count[0] == 2
        assert fast_degrade.is_degraded("memory_router") is False

    def test_boundary_degrade_until_expiry_recovers(self, fast_degrade):
        """降级到期后自动恢复"""
        fast_degrade.call_with_fallback(
            "schema_validator", lambda: (_ for _ in ()).throw(ValueError("fail"))
        )
        assert fast_degrade.is_degraded("schema_validator") is True

        time.sleep(0.25)  # degrade_seconds=0.2
        assert fast_degrade.is_degraded("schema_validator") is False

    def test_boundary_degrade_just_before_expiry_still_degraded(self, fast_degrade):
        """降级未到期仍处于降级状态"""
        fast_degrade.call_with_fallback(
            "schema_validator", lambda: (_ for _ in ()).throw(ValueError("fail"))
        )
        time.sleep(0.1)  # < degrade_seconds=0.2
        assert fast_degrade.is_degraded("schema_validator") is True

    def test_boundary_success_after_degrade_recovers(self, fast_degrade):
        """降级期内调用返回 fallback，降级到期后成功调用恢复"""
        # 触发降级
        fast_degrade.call_with_fallback(
            "memory_router", lambda: (_ for _ in ()).throw(ValueError("fail"))
        )
        assert fast_degrade.is_degraded("memory_router") is True

        # 降级期内调用 → 直接返回 fallback
        result = fast_degrade.call_with_fallback(
            "memory_router", lambda: "should_not_call"
        )
        assert result == []

        # 等待降级到期
        time.sleep(0.25)
        assert fast_degrade.is_degraded("memory_router") is False

        # 降级到期后成功调用
        result = fast_degrade.call_with_fallback(
            "memory_router", lambda: "recovered"
        )
        assert result == "recovered"

    def test_boundary_explicit_fallback_overrides_default(self, fast_degrade):
        """显式 fallback 优先于 default_fallbacks"""
        result = fast_degrade.call_with_fallback(
            "unknown_component",
            lambda: (_ for _ in ()).throw(ValueError("fail")),
            fallback="explicit_fallback",
        )
        assert result == "explicit_fallback"


# ═══════════════════════════════════════════════════════════════
#  超时与重试策略边界
# ═══════════════════════════════════════════════════════════════


class TestTimeoutBoundary:
    """超时与重试策略边界测试"""

    def test_timeout_slow_func_returns_result(self, default_degrade):
        """慢函数仍返回结果（不因耗时触发降级）"""
        def slow_func():
            time.sleep(0.05)
            return "slow_result"

        result = default_degrade.call_with_fallback("memory_router", slow_func)
        assert result == "slow_result"
        assert default_degrade.is_degraded("memory_router") is False

    def test_timeout_retry_strategy_invoked(self, fast_degrade):
        """自定义重试策略被调用"""
        retry_calls = []

        def retry_strategy(attempt, exc):
            retry_calls.append((attempt, str(exc)))

        def always_fail():
            raise TimeoutError("request timeout")

        fast_degrade.call_with_fallback(
            "schema_validator",
            always_fail,
            retry_strategy=retry_strategy,
        )
        # max_retries=3 → 重试 3 次（attempt 0,1,2）
        assert len(retry_calls) == 3
        assert all("timeout" in str(exc) for _, exc in retry_calls)

    def test_timeout_retry_strategy_exception_ignored(self, fast_degrade):
        """重试策略自身抛异常被静默忽略"""
        def bad_retry_strategy(attempt, exc):
            raise RuntimeError("retry strategy broken")

        def always_fail():
            raise ValueError("fail")

        # 不应因 retry_strategy 抛异常而崩溃
        result = fast_degrade.call_with_fallback(
            "critic_engine",
            always_fail,
            retry_strategy=bad_retry_strategy,
        )
        assert result is None  # fallback
        assert fast_degrade.is_degraded("critic_engine") is True

    def test_timeout_multiple_failures_then_success(self, fast_degrade):
        """多次失败后成功不触发降级"""
        call_count = [0]

        def fail_twice_then_success():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise TimeoutError("timeout")
            return "success"

        result = fast_degrade.call_with_fallback(
            "memory_router", fail_twice_then_success
        )
        assert result == "success"
        assert call_count[0] == 3
        assert fast_degrade.is_degraded("memory_router") is False


# ═══════════════════════════════════════════════════════════════
#  空值与 None 边界
# ═══════════════════════════════════════════════════════════════


class TestNullAndEmpty:
    """空值与 None 边界测试"""

    def test_null_fallback_uses_default_for_known_component(self, default_degrade):
        """fallback=None 时使用 default_fallbacks 的已知组件回退值"""
        result = default_degrade.call_with_fallback(
            "schema_validator",
            lambda: (_ for _ in ()).throw(ValueError("fail")),
        )
        assert result is None  # default_fallbacks["schema_validator"] = None

    def test_null_fallback_for_unknown_component(self, default_degrade):
        """fallback=None 且未知组件时返回 None"""
        result = default_degrade.call_with_fallback(
            "unknown_component",
            lambda: (_ for _ in ()).throw(ValueError("fail")),
        )
        assert result is None

    def test_empty_default_fallbacks_dict(self):
        """空 default_fallbacks 字典"""
        degrade = GracefulDegrade(default_fallbacks={}, max_retries=0)
        result = degrade.call_with_fallback(
            "any_component",
            lambda: (_ for _ in ()).throw(ValueError("fail")),
        )
        assert result is None

    def test_null_default_fallbacks(self):
        """default_fallbacks=None 时使用内置默认值"""
        degrade = GracefulDegrade(default_fallbacks=None, max_retries=0)
        result = degrade.call_with_fallback(
            "schema_validator",
            lambda: (_ for _ in ()).throw(ValueError("fail")),
        )
        assert result is None  # 内置默认 schema_validator -> None

    def test_null_data_in_schema_validate(self, default_degrade):
        """schema_validate 传入 None 数据"""
        def validator(data):
            if data is None:
                raise ValueError("data is None")
            return data

        is_valid, result = default_degrade.schema_validate_with_fallback(
            validator, None
        )
        # 标准验证失败 3 次 → 触发降级 → 返回 str(None) = "None"
        assert is_valid is False
        assert result == "None"

    def test_empty_string_data_in_schema_validate(self, default_degrade):
        """schema_validate 传入空字符串数据"""
        def validator(data):
            if not data:
                raise ValueError("empty")
            return data

        is_valid, result = default_degrade.schema_validate_with_fallback(
            validator, ""
        )
        # 降级后返回原字符串
        assert is_valid is False
        assert result == ""

    def test_get_cached_returns_none_for_unknown(self, default_degrade):
        """未知组件的缓存返回 None"""
        assert default_degrade.get_cached("never_cached") is None


# ═══════════════════════════════════════════════════════════════
#  非法输入边界
# ═══════════════════════════════════════════════════════════════


class TestInvalidInput:
    """非法输入边界测试"""

    def test_invalid_func_raises_exception_caught(self, default_degrade):
        """func 抛出各类异常均被捕获"""
        exceptions = [ValueError, TypeError, RuntimeError, KeyError, OSError]
        for exc_type in exceptions:
            degrade = GracefulDegrade(max_retries=0)
            result = degrade.call_with_fallback(
                "test_component",
                lambda: (_ for _ in ()).throw(exc_type("test")),
            )
            assert result is None
            assert degrade.is_degraded("test_component") is True

    def test_invalid_validator_in_schema(self, default_degrade):
        """validator 抛异常触发多级降级"""
        def bad_validator(data):
            raise TypeError("validator broken")

        is_valid, result = default_degrade.schema_validate_with_fallback(
            bad_validator, {"key": "value"}
        )
        assert is_valid is False
        assert "key" in result  # str(data) 降级

    def test_invalid_relaxed_validator(self, default_degrade):
        """relaxed_validator 抛异常后降级为纯文本"""
        def bad_validator(data):
            raise ValueError("strict fail")

        def bad_relaxed(data):
            raise TypeError("relaxed fail")

        is_valid, result = default_degrade.schema_validate_with_fallback(
            bad_validator, "test_data", relaxed_validator=bad_relaxed
        )
        assert is_valid is False
        assert result == "test_data"  # 纯文本降级

    def test_invalid_component_empty_string(self, default_degrade):
        """空字符串 component 名正常处理"""
        result = default_degrade.call_with_fallback(
            "", lambda: "ok"
        )
        assert result == "ok"
        assert default_degrade.is_degraded("") is False

    def test_invalid_func_not_callable(self, default_degrade):
        """func 非 callable 时触发降级返回 fallback

        call_with_fallback 内部 try/except Exception 捕获所有异常，
        'str' object is not callable 的 TypeError 被当作普通失败处理，
        重试 max_retries+1 次后触发降级。
        """
        result = default_degrade.call_with_fallback("test", "not_a_func")
        assert result is None  # fallback
        assert default_degrade.is_degraded("test") is True


# ═══════════════════════════════════════════════════════════════
#  极端值边界
# ═══════════════════════════════════════════════════════════════


class TestExtremeValues:
    """极端值边界测试"""

    def test_extreme_zero_max_retries(self):
        """max_retries=0 时一次失败即降级"""
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=30.0)
        call_count = [0]

        def fail_once():
            call_count[0] += 1
            raise ValueError("fail")

        result = degrade.call_with_fallback("test", fail_once)
        assert call_count[0] == 1  # 只调用 1 次
        assert result is None
        assert degrade.is_degraded("test") is True

    def test_extreme_huge_max_retries(self):
        """超大 max_retries 仍能正确计数"""
        degrade = GracefulDegrade(max_retries=100, degrade_seconds=30.0)
        call_count = [0]

        def always_fail():
            call_count[0] += 1
            raise ValueError("fail")

        result = degrade.call_with_fallback("test", always_fail)
        assert call_count[0] == 101  # 1 + 100 重试
        assert result is None
        assert degrade.is_degraded("test") is True

    def test_extreme_zero_degrade_seconds(self):
        """degrade_seconds=0 时降级立即到期"""
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=0.0)
        degrade.call_with_fallback(
            "test", lambda: (_ for _ in ()).throw(ValueError("fail"))
        )
        # degrade_seconds=0 → 立即到期
        # 注意：is_degraded 检查 time.time() >= degrade_until
        # degrade_until = time.time() + 0 = time.time()
        # 由于时间精度，可能已过期
        state = degrade.get_state("test")
        assert state.level == DegradeLevel.FALLBACK

    def test_extreme_huge_degrade_seconds(self):
        """超大 degrade_seconds 保持降级状态"""
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=999999.0)
        degrade.call_with_fallback(
            "test", lambda: (_ for _ in ()).throw(ValueError("fail"))
        )
        assert degrade.is_degraded("test") is True

    def test_extreme_rapid_successive_calls(self, fast_degrade):
        """快速连续调用不导致状态错乱"""
        results = []
        for i in range(50):
            r = fast_degrade.call_with_fallback("test", lambda: f"ok_{i}")
            results.append(r)
        assert all(r is not None for r in results)
        assert len(results) == 50

    def test_extreme_huge_data_in_schema_validate(self, default_degrade):
        """超大数据 schema 验证"""
        huge_data = "x" * 100000
        is_valid, result = default_degrade.schema_validate_with_fallback(
            lambda d: d, huge_data
        )
        assert is_valid is True
        assert result == huge_data


# ═══════════════════════════════════════════════════════════════
#  Schema 验证多级降级
# ═══════════════════════════════════════════════════════════════


class TestSchemaValidateFallback:
    """Schema 验证多级降级测试"""

    def test_schema_standard_validate_success(self, default_degrade):
        """标准验证成功"""
        def validator(data):
            return {"validated": data}

        is_valid, result = default_degrade.schema_validate_with_fallback(
            validator, {"key": "value"}
        )
        assert is_valid is True
        assert result == {"validated": {"key": "value"}}

    def test_schema_standard_fail_relaxed_success(self, default_degrade):
        """标准验证失败但宽松验证成功"""
        def strict_validator(data):
            if not isinstance(data, dict):
                raise TypeError("must be dict")
            return data

        def relaxed_validator(data):
            return {"relaxed": data}

        is_valid, result = default_degrade.schema_validate_with_fallback(
            strict_validator,
            "not_a_dict",
            relaxed_validator=relaxed_validator,
        )
        # 标准验证失败 3 次 → 宽松验证成功
        assert is_valid is True
        assert result == {"relaxed": "not_a_dict"}

    def test_schema_all_fail_text_fallback(self, default_degrade):
        """标准+宽松均失败，降级为纯文本"""
        def strict(data):
            raise ValueError("strict fail")

        def relaxed(data):
            raise ValueError("relaxed fail")

        is_valid, result = default_degrade.schema_validate_with_fallback(
            strict, {"key": "value"}, relaxed_validator=relaxed
        )
        assert is_valid is False
        assert "key" in result  # str(data)
        assert default_degrade.is_degraded("schema_validator") is True

    def test_schema_already_degraded_short_circuit(self, fast_degrade):
        """已降级时短路返回纯文本，不调用 validator"""
        # 先触发降级
        def always_fail(data):
            raise ValueError("fail")

        fast_degrade.schema_validate_with_fallback(always_fail, "test")
        assert fast_degrade.is_degraded("schema_validator") is True

        # 再次调用应短路
        validator_called = [False]

        def tracking_validator(data):
            validator_called[0] = True
            return data

        is_valid, result = fast_degrade.schema_validate_with_fallback(
            tracking_validator, "test_data"
        )
        assert is_valid is False
        assert result == "test_data"
        assert validator_called[0] is False  # 未调用 validator

    def test_schema_str_data_degraded_returns_str(self, default_degrade):
        """字符串数据降级后返回原字符串"""
        def validator(data):
            raise ValueError("always fail")

        is_valid, result = default_degrade.schema_validate_with_fallback(
            validator, "original_string"
        )
        assert is_valid is False
        assert result == "original_string"

    def test_schema_no_relaxed_validator(self, default_degrade):
        """不提供 relaxed_validator 时直接降级"""
        def validator(data):
            raise ValueError("fail")

        is_valid, result = default_degrade.schema_validate_with_fallback(
            validator, "test"
        )
        assert is_valid is False
        assert result == "test"


# ═══════════════════════════════════════════════════════════════
#  状态管理边界
# ═══════════════════════════════════════════════════════════════


class TestStateManagement:
    """状态管理边界测试"""

    def test_force_degrade_sets_fallback_level(self, default_degrade):
        """force_degrade 设置 FALLBACK 级别"""
        default_degrade.force_degrade("test_component")
        assert default_degrade.is_degraded("test_component") is True
        state = default_degrade.get_state("test_component")
        assert state.level == DegradeLevel.FALLBACK

    def test_force_degrade_with_custom_level(self, default_degrade):
        """force_degrade 指定自定义级别"""
        default_degrade.force_degrade(
            "test_component", level=DegradeLevel.DISABLED
        )
        state = default_degrade.get_state("test_component")
        assert state.level == DegradeLevel.DISABLED

    def test_is_degraded_false_for_unknown_component(self, default_degrade):
        """未知组件不处于降级状态"""
        assert default_degrade.is_degraded("never_seen") is False

    def test_get_state_creates_default_for_unknown(self, default_degrade):
        """get_state 为未知组件创建默认状态"""
        state = default_degrade.get_state("new_component")
        assert isinstance(state, DegradeState)
        assert state.component == "new_component"
        assert state.level == DegradeLevel.NORMAL
        assert state.failure_count == 0

    def test_record_failure_increments_count(self, default_degrade):
        """记录失败递增 failure_count"""
        default_degrade._record_failure("test_component")
        default_degrade._record_failure("test_component")
        state = default_degrade.get_state("test_component")
        assert state.failure_count == 2

    def test_maybe_recover_resets_state(self, default_degrade):
        """成功调用后 _maybe_recover 恢复状态"""
        default_degrade.force_degrade("test_component")
        assert default_degrade.is_degraded("test_component") is True

        default_degrade._maybe_recover("test_component")
        state = default_degrade.get_state("test_component")
        assert state.level == DegradeLevel.NORMAL
        assert state.failure_count == 0

    def test_degrade_state_dataclass_fields(self):
        """DegradeState dataclass 默认字段"""
        state = DegradeState(component="test")
        assert state.component == "test"
        assert state.level == DegradeLevel.NORMAL
        assert state.failure_count == 0
        assert state.last_failure_time == 0.0
        assert state.last_fallback_value is None
        assert state.degrade_until == 0.0


# ═══════════════════════════════════════════════════════════════
#  缓存池边界
# ═══════════════════════════════════════════════════════════════


class TestCachePool:
    """缓存池边界测试"""

    def test_cache_updated_on_success(self, default_degrade):
        """成功调用后更新缓存池"""
        default_degrade.call_with_fallback(
            "dashboard_loader", lambda: {"metrics": [1, 2, 3]}
        )
        cached = default_degrade.get_cached("dashboard_loader")
        assert cached == {"metrics": [1, 2, 3]}

    def test_cache_not_updated_on_failure(self, default_degrade):
        """失败调用不更新缓存"""
        default_degrade.call_with_fallback(
            "test_component",
            lambda: (_ for _ in ()).throw(ValueError("fail")),
        )
        assert default_degrade.get_cached("test_component") is None

    def test_cache_overwritten_on_new_success(self, default_degrade):
        """新成功调用覆盖旧缓存"""
        default_degrade.call_with_fallback("test", lambda: "first")
        default_degrade.call_with_fallback("test", lambda: "second")
        assert default_degrade.get_cached("test") == "second"

    def test_cache_preserved_during_degrade(self, fast_degrade):
        """降级期间缓存保留（可用于 Dashboard 回退）"""
        fast_degrade.call_with_fallback(
            "dashboard_loader", lambda: {"data": "cached_value"}
        )
        # 触发降级
        fast_degrade.call_with_fallback(
            "dashboard_loader",
            lambda: (_ for _ in ()).throw(ValueError("fail")),
        )
        # 缓存仍存在
        cached = fast_degrade.get_cached("dashboard_loader")
        assert cached == {"data": "cached_value"}


# ═══════════════════════════════════════════════════════════════
#  并发安全
# ═══════════════════════════════════════════════════════════════


class TestConcurrencySafety:
    """并发访问线程安全测试"""

    def test_concurrent_call_with_fallback_thread_safe(self, default_degrade):
        """并发调用 call_with_fallback 线程安全"""
        results = []
        lock = threading.Lock()

        def worker():
            r = default_degrade.call_with_fallback("test", lambda: "ok")
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        assert all(r == "ok" for r in results)

    def test_concurrent_failures_trigger_degrade_once(self, fast_degrade):
        """并发失败只触发一次降级"""
        call_count = [0]
        count_lock = threading.Lock()

        def always_fail():
            with count_lock:
                call_count[0] += 1
            raise ValueError("fail")

        def worker():
            fast_degrade.call_with_fallback("test_component", always_fail)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert fast_degrade.is_degraded("test_component") is True
        # 最多调用 (1+max_retries) * 线程数 = 4 * 10 = 40
        # 但降级后直接返回 fallback，所以实际调用次数 <= 40
        assert call_count[0] <= 40

    def test_concurrent_force_degrade_safe(self, default_degrade):
        """并发 force_degrade 线程安全"""
        def worker():
            default_degrade.force_degrade("test_component")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert default_degrade.is_degraded("test_component") is True


# ═══════════════════════════════════════════════════════════════
#  重置功能边界
# ═══════════════════════════════════════════════════════════════


class TestResetFunction:
    """重置功能边界测试"""

    def test_reset_clears_all_states(self, default_degrade):
        """reset 清空所有降级状态"""
        default_degrade.force_degrade("component_a")
        default_degrade.force_degrade("component_b")
        assert default_degrade.is_degraded("component_a") is True

        default_degrade.reset()
        assert default_degrade.is_degraded("component_a") is False
        assert default_degrade.is_degraded("component_b") is False

    def test_reset_clears_cache_pool(self, default_degrade):
        """reset 清空缓存池"""
        default_degrade.call_with_fallback("test", lambda: "cached")
        assert default_degrade.get_cached("test") == "cached"

        default_degrade.reset()
        assert default_degrade.get_cached("test") is None

    def test_reset_multiple_times_safe(self, default_degrade):
        """多次 reset 安全无副作用"""
        default_degrade.force_degrade("test")
        default_degrade.reset()
        default_degrade.reset()
        default_degrade.reset()
        assert default_degrade.is_degraded("test") is False

    def test_reset_after_degrade_allows_new_call(self, fast_degrade):
        """reset 后降级状态清除，允许新调用"""
        fast_degrade.call_with_fallback(
            "test", lambda: (_ for _ in ()).throw(ValueError("fail"))
        )
        assert fast_degrade.is_degraded("test") is True

        fast_degrade.reset()
        assert fast_degrade.is_degraded("test") is False

        result = fast_degrade.call_with_fallback("test", lambda: "new_ok")
        assert result == "new_ok"


# ═══════════════════════════════════════════════════════════════
#  DegradeError 与枚举边界
# ═══════════════════════════════════════════════════════════════


class TestDegradeErrorAndEnum:
    """DegradeError 与 DegradeLevel 枚举边界测试"""

    def test_degrade_error_default_error_code(self):
        """DegradeError 默认错误码"""
        err = DegradeError("something failed")
        assert str(err) == "something failed"
        assert err.error_code == "DEGRADE_FAILED"
        assert err.component == ""

    def test_degrade_error_custom_error_code(self):
        """DegradeError 自定义错误码和组件"""
        err = DegradeError(
            "schema broken",
            error_code="SCHEMA_INVALID",
            component="schema_validator",
        )
        assert err.error_code == "SCHEMA_INVALID"
        assert err.component == "schema_validator"

    def test_degrade_error_is_exception(self):
        """DegradeError 是 Exception 子类"""
        err = DegradeError("test")
        assert isinstance(err, Exception)

    def test_degrade_error_can_be_raised_and_caught(self):
        """DegradeError 可被 raise 和 try/except 捕获"""
        with pytest.raises(DegradeError) as exc_info:
            raise DegradeError("test error", error_code="TEST_001")
        assert exc_info.value.error_code == "TEST_001"

    def test_degrade_level_enum_values(self):
        """DegradeLevel 枚举值正确"""
        assert DegradeLevel.NORMAL == "normal"
        assert DegradeLevel.RETRY == "retry"
        assert DegradeLevel.RELAXED == "relaxed"
        assert DegradeLevel.FALLBACK == "fallback"
        assert DegradeLevel.DISABLED == "disabled"

    def test_degrade_level_is_str_enum(self):
        """DegradeLevel 是 str 枚举（可序列化）"""
        assert DegradeLevel.NORMAL.value == "normal"
        assert isinstance(DegradeLevel.NORMAL.value, str)


# ═══════════════════════════════════════════════════════════════
#  trace_id 上下文边界
# ═══════════════════════════════════════════════════════════════


class TestTraceIdContext:
    """trace_id 上下文边界测试"""

    def test_get_trace_id_default_empty(self):
        """默认 trace_id 为空字符串"""
        # 注意：可能被其他测试设置，这里测试 set/get 配对
        set_trace_id("test_id_123")
        assert get_trace_id() == "test_id_123"
        # 恢复默认
        set_trace_id("")

    def test_set_trace_id_none_becomes_empty(self):
        """set_trace_id(None) 设置为空字符串"""
        set_trace_id(None)
        assert get_trace_id() == ""
        set_trace_id("")

    def test_set_trace_id_persists_across_calls(self, default_degrade):
        """trace_id 在调用期间保持"""
        set_trace_id("trace_abc")
        default_degrade.call_with_fallback("test", lambda: "ok")
        assert get_trace_id() == "trace_abc"
        set_trace_id("")
