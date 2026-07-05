"""ChaosInjector 综合单元测试

覆盖模块: agent/monitoring/chaos_injector.py
测试维度: 故障类型枚举 / 配置 / 注入 / 触发 / 清理 / 统计 / 装饰器 / 上下文管理器
设计原则: AAA (Arrange-Act-Assert), 不真正消耗资源 (小内存/短时间), 边界显性化
"""

import time
import threading
from unittest import mock

import pytest

from agent.monitoring.chaos_injector import (
    FaultType,
    FaultConfig,
    FaultInjectionRecord,
    ChaosInjector,
    get_chaos_injector,
    with_chaos_injection,
    chaos_fault,
)


# ═══════════════════════════════════════════════════════════════
# FaultType 枚举测试
# ═══════════════════════════════════════════════════════════════


class TestFaultType:
    """FaultType 枚举完整性测试"""

    def test_all_fault_types_have_value(self):
        for ft in FaultType:
            assert isinstance(ft.value, str)
            assert len(ft.value) > 0

    def test_fault_type_count(self):
        # 应该有 12 种故障类型
        assert len(list(FaultType)) == 12

    def test_specific_fault_types_exist(self):
        assert FaultType.NETWORK_DELAY.value == "network_delay"
        assert FaultType.NETWORK_TIMEOUT.value == "network_timeout"
        assert FaultType.SERVICE_UNAVAILABLE.value == "service_unavailable"
        assert FaultType.MEMORY_PRESSURE.value == "memory_pressure"
        assert FaultType.CPU_PRESSURE.value == "cpu_pressure"
        assert FaultType.DISK_FULL.value == "disk_full"

    def test_fault_type_lookup_by_value(self):
        ft = FaultType("network_delay")
        assert ft is FaultType.NETWORK_DELAY


# ═══════════════════════════════════════════════════════════════
# FaultConfig 数据类
# ═══════════════════════════════════════════════════════════════


class TestFaultConfig:
    """FaultConfig 数据类测试"""

    def test_default_config(self):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY)
        assert config.fault_type == FaultType.NETWORK_DELAY
        assert config.enabled is False
        assert config.probability == 1.0
        assert config.duration_ms is None
        assert config.target_service is None

    def test_config_with_all_fields(self):
        config = FaultConfig(
            fault_type=FaultType.MEMORY_PRESSURE,
            enabled=True,
            probability=0.5,
            duration_ms=1000,
            target_memory_mb=512,
        )
        assert config.enabled is True
        assert config.probability == 0.5
        assert config.target_memory_mb == 512

    def test_config_is_mutable(self):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY)
        config.enabled = True
        config.delay_ms = 500
        assert config.enabled is True
        assert config.delay_ms == 500


# ═══════════════════════════════════════════════════════════════
# FaultInjectionRecord 数据类
# ═══════════════════════════════════════════════════════════════


class TestFaultInjectionRecord:
    """FaultInjectionRecord 数据类测试"""

    def test_default_record(self):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY)
        record = FaultInjectionRecord(
            fault_type=FaultType.NETWORK_DELAY,
            config=config,
            injected_at=__import__("datetime").datetime.now(),
        )
        assert record.triggered_count == 0
        assert record.affected_requests == 0
        assert record.recovered_at is None

    def test_record_with_data(self):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=True)
        record = FaultInjectionRecord(
            fault_type=FaultType.NETWORK_DELAY,
            config=config,
            injected_at=__import__("datetime").datetime.now(),
            triggered_count=5,
            affected_requests=10,
        )
        assert record.triggered_count == 5
        assert record.affected_requests == 10


# ═══════════════════════════════════════════════════════════════
# ChaosInjector 初始化
# ═══════════════════════════════════════════════════════════════


class TestChaosInjectorInit:
    """ChaosInjector 初始化测试"""

    def test_init_creates_all_configs(self):
        injector = ChaosInjector()
        # 应为所有故障类型创建默认配置
        for ft in FaultType:
            assert ft in injector._fault_configs
            assert injector._fault_configs[ft].fault_type == ft
            assert injector._fault_configs[ft].enabled is False

    def test_init_empty_records(self):
        injector = ChaosInjector()
        assert injector._injection_records == []

    def test_init_has_lock(self):
        injector = ChaosInjector()
        assert isinstance(injector._lock, type(threading.RLock()))

    def test_init_chaos_trace_id(self):
        injector = ChaosInjector()
        assert injector._chaos_trace_id.startswith("chaos-injector-")


# ═══════════════════════════════════════════════════════════════
# _check_probability / _check_duration
# ═══════════════════════════════════════════════════════════════


class TestCheckProbability:
    """_check_probability 测试"""

    def test_disabled_returns_false(self):
        injector = ChaosInjector()
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=False, probability=1.0)
        assert injector._check_probability(config) is False

    def test_full_probability_returns_true(self):
        injector = ChaosInjector()
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=True, probability=1.0)
        assert injector._check_probability(config) is True

    def test_zero_probability_returns_false(self):
        injector = ChaosInjector()
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=True, probability=0.0)
        assert injector._check_probability(config) is False


class TestCheckDuration:
    """_check_duration 测试"""

    def test_disabled_returns_false(self):
        injector = ChaosInjector()
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=False)
        assert injector._check_duration(config) is False

    def test_enabled_no_time_bounds_returns_true(self):
        injector = ChaosInjector()
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=True)
        assert injector._check_duration(config) is True

    def test_within_time_range(self):
        from datetime import datetime, timedelta
        injector = ChaosInjector()
        now = datetime.now()
        config = FaultConfig(
            fault_type=FaultType.NETWORK_DELAY,
            enabled=True,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
        )
        assert injector._check_duration(config) is True

    def test_outside_time_range(self):
        from datetime import datetime, timedelta
        injector = ChaosInjector()
        now = datetime.now()
        config = FaultConfig(
            fault_type=FaultType.NETWORK_DELAY,
            enabled=True,
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
        )
        assert injector._check_duration(config) is False


# ═══════════════════════════════════════════════════════════════
# 网络延迟注入
# ═══════════════════════════════════════════════════════════════


class TestInjectNetworkDelay:
    """inject_network_delay 测试"""

    def test_inject_basic(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.enabled is True
        assert config.delay_ms == 100
        assert config.probability == 1.0

    def test_inject_with_probability(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=200, probability=0.5)
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.probability == 0.5

    def test_inject_with_duration(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100, duration_ms=1000)
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.duration_ms == 1000
        assert config.end_time is not None

    def test_inject_with_target_service(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100, target_service="api-service")
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.target_service == "api-service"

    def test_inject_creates_record(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        assert len(injector._injection_records) == 1
        assert injector._injection_records[0].fault_type == FaultType.NETWORK_DELAY


class TestInjectNetworkTimeout:
    """inject_network_timeout 测试"""

    def test_inject_basic(self):
        injector = ChaosInjector()
        injector.inject_network_timeout()
        config = injector._fault_configs[FaultType.NETWORK_TIMEOUT]
        assert config.enabled is True
        assert config.probability == 1.0

    def test_inject_with_probability(self):
        injector = ChaosInjector()
        injector.inject_network_timeout(probability=0.3)
        config = injector._fault_configs[FaultType.NETWORK_TIMEOUT]
        assert config.probability == 0.3

    def test_inject_with_duration(self):
        injector = ChaosInjector()
        injector.inject_network_timeout(duration_ms=500)
        config = injector._fault_configs[FaultType.NETWORK_TIMEOUT]
        assert config.duration_ms == 500
        assert config.end_time is not None


class TestInjectServiceUnavailable:
    """inject_service_unavailable 测试"""

    def test_inject_basic(self):
        injector = ChaosInjector()
        injector.inject_service_unavailable(service_name="user-service")
        config = injector._fault_configs[FaultType.SERVICE_UNAVAILABLE]
        assert config.enabled is True
        assert config.target_service == "user-service"
        assert config.error_code == 503

    def test_inject_custom_error_code(self):
        injector = ChaosInjector()
        injector.inject_service_unavailable(service_name="api", error_code=500)
        config = injector._fault_configs[FaultType.SERVICE_UNAVAILABLE]
        assert config.error_code == 500

    def test_inject_with_probability(self):
        injector = ChaosInjector()
        injector.inject_service_unavailable(service_name="api", probability=0.7)
        config = injector._fault_configs[FaultType.SERVICE_UNAVAILABLE]
        assert config.probability == 0.7


# ═══════════════════════════════════════════════════════════════
# 内存压力注入 (使用小内存避免系统影响)
# ═══════════════════════════════════════════════════════════════


class TestInjectMemoryPressure:
    """inject_memory_pressure 测试"""

    def test_inject_small_memory(self):
        injector = ChaosInjector()
        # 仅分配 1MB 避免影响系统
        injector.inject_memory_pressure(target_mb=1)
        config = injector._fault_configs[FaultType.MEMORY_PRESSURE]
        assert config.enabled is True
        assert config.target_memory_mb == 1
        # 清理
        injector.clear_fault(FaultType.MEMORY_PRESSURE)

    def test_inject_creates_record(self):
        injector = ChaosInjector()
        injector.inject_memory_pressure(target_mb=1)
        assert len(injector._injection_records) == 1
        injector.clear_fault(FaultType.MEMORY_PRESSURE)

    def test_inject_with_duration_starts_thread(self):
        injector = ChaosInjector()
        injector.inject_memory_pressure(target_mb=1, duration_ms=100)
        config = injector._fault_configs[FaultType.MEMORY_PRESSURE]
        assert config.duration_ms == 100
        # 等待线程结束
        time.sleep(0.15)
        injector.clear_fault(FaultType.MEMORY_PRESSURE)

    def test_inject_replaces_previous(self):
        injector = ChaosInjector()
        injector.inject_memory_pressure(target_mb=1)
        injector.inject_memory_pressure(target_mb=2)
        config = injector._fault_configs[FaultType.MEMORY_PRESSURE]
        assert config.target_memory_mb == 2
        injector.clear_fault(FaultType.MEMORY_PRESSURE)


# ═══════════════════════════════════════════════════════════════
# 其他故障注入
# ═══════════════════════════════════════════════════════════════


class TestInjectDiskIODelay:
    """inject_disk_io_delay 测试"""

    def test_inject_basic(self):
        injector = ChaosInjector()
        injector.inject_disk_io_delay(delay_ms=50)
        config = injector._fault_configs[FaultType.DISK_IO_DELAY]
        assert config.enabled is True
        assert config.delay_ms == 50
        assert config.io_operation == "both"

    def test_inject_with_operation(self):
        injector = ChaosInjector()
        injector.inject_disk_io_delay(delay_ms=50, io_operation="read")
        config = injector._fault_configs[FaultType.DISK_IO_DELAY]
        assert config.io_operation == "read"


class TestInjectDiskFull:
    """inject_disk_full 测试"""

    def test_inject_basic(self):
        injector = ChaosInjector()
        injector.inject_disk_full(disk_usage_percent=95)
        config = injector._fault_configs[FaultType.DISK_FULL]
        assert config.enabled is True
        assert config.disk_usage_percent == 95

    def test_inject_custom_percent(self):
        injector = ChaosInjector()
        injector.inject_disk_full(disk_usage_percent=80)
        config = injector._fault_configs[FaultType.DISK_FULL]
        assert config.disk_usage_percent == 80


class TestInjectConnectionPoolExhausted:
    """inject_connection_pool_exhausted 测试"""

    def test_inject_basic(self):
        injector = ChaosInjector()
        injector.inject_connection_pool_exhausted(pool_size=0)
        config = injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
        assert config.enabled is True
        assert config.pool_size == 0

    def test_inject_with_probability(self):
        injector = ChaosInjector()
        injector.inject_connection_pool_exhausted(pool_size=5, probability=0.5)
        config = injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
        assert config.pool_size == 5
        assert config.probability == 0.5


class TestInjectMessageFaults:
    """inject_message_loss / out_of_order / duplicate 测试"""

    def test_inject_message_loss(self):
        injector = ChaosInjector()
        injector.inject_message_loss(loss_percent=20)
        config = injector._fault_configs[FaultType.MESSAGE_LOSS]
        assert config.enabled is True
        assert config.message_loss_percent == 20
        # 概率应转换为 0-1
        assert config.probability == 0.2

    def test_inject_message_out_of_order(self):
        injector = ChaosInjector()
        injector.inject_message_out_of_order(probability=0.6)
        config = injector._fault_configs[FaultType.MESSAGE_OUT_OF_ORDER]
        assert config.enabled is True
        assert config.probability == 0.6

    def test_inject_message_duplicate(self):
        injector = ChaosInjector()
        injector.inject_message_duplicate(duplicate_count=3, probability=0.4)
        config = injector._fault_configs[FaultType.MESSAGE_DUPLICATE]
        assert config.enabled is True
        assert config.duplicate_count == 3
        assert config.probability == 0.4


# ═══════════════════════════════════════════════════════════════
# CPU 压力注入 (使用很短时间避免影响)
# ═══════════════════════════════════════════════════════════════


class TestInjectCPUPressure:
    """inject_cpu_pressure 测试"""

    def test_inject_basic(self):
        injector = ChaosInjector()
        injector.inject_cpu_pressure(duration_ms=100)
        config = injector._fault_configs[FaultType.CPU_PRESSURE]
        assert config.enabled is True
        assert config.duration_ms == 100
        # 等待进程结束
        time.sleep(0.2)


# ═══════════════════════════════════════════════════════════════
# trigger_if_active
# ═══════════════════════════════════════════════════════════════


class TestTriggerIfActive:
    """trigger_if_active 测试"""

    def test_trigger_disabled_fault(self):
        injector = ChaosInjector()
        # 未注入故障
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is False

    def test_trigger_enabled_fault(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is True
        injector.clear_fault(FaultType.NETWORK_DELAY)

    def test_trigger_zero_probability(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100, probability=0.0)
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is False

    def test_trigger_with_target_service_match(self):
        injector = ChaosInjector()
        injector.inject_service_unavailable(service_name="api-svc")
        assert injector.trigger_if_active(FaultType.SERVICE_UNAVAILABLE, "api-svc") is True
        injector.clear_fault(FaultType.SERVICE_UNAVAILABLE)

    def test_trigger_with_target_service_mismatch(self):
        injector = ChaosInjector()
        injector.inject_service_unavailable(service_name="api-svc")
        assert injector.trigger_if_active(
            FaultType.SERVICE_UNAVAILABLE, "other-svc"
        ) is False
        injector.clear_fault(FaultType.SERVICE_UNAVAILABLE)

    def test_trigger_unknown_fault_type(self):
        injector = ChaosInjector()
        # 传入 None 应安全返回 False
        assert injector.trigger_if_active(None) is False

    def test_trigger_updates_record(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.trigger_if_active(FaultType.NETWORK_DELAY)
        injector.trigger_if_active(FaultType.NETWORK_DELAY)
        # triggered_count 应增加
        record = injector._injection_records[-1]
        assert record.triggered_count >= 2
        injector.clear_fault(FaultType.NETWORK_DELAY)


# ═══════════════════════════════════════════════════════════════
# get_delay_ms
# ═══════════════════════════════════════════════════════════════


class TestGetDelayMs:
    """get_delay_ms 测试"""

    def test_get_delay_when_enabled(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=250)
        assert injector.get_delay_ms(FaultType.NETWORK_DELAY) == 250
        injector.clear_fault(FaultType.NETWORK_DELAY)

    def test_get_delay_when_disabled(self):
        injector = ChaosInjector()
        assert injector.get_delay_ms(FaultType.NETWORK_DELAY) is None

    def test_get_delay_for_unknown_type(self):
        injector = ChaosInjector()
        # 没有设置过延迟的故障类型
        assert injector.get_delay_ms(FaultType.DISK_IO_DELAY) is None


# ═══════════════════════════════════════════════════════════════
# clear_fault / clear_all
# ═══════════════════════════════════════════════════════════════


class TestClearFault:
    """clear_fault / clear_all 测试"""

    def test_clear_fault_disables(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.clear_fault(FaultType.NETWORK_DELAY)
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.enabled is False

    def test_clear_fault_updates_record(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.clear_fault(FaultType.NETWORK_DELAY)
        record = injector._injection_records[-1]
        assert record.recovered_at is not None

    def test_clear_fault_unknown_type(self):
        injector = ChaosInjector()
        # 清除未注入的故障类型不应抛异常
        injector.clear_fault(FaultType.NETWORK_DELAY)

    def test_clear_all(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.inject_network_timeout()
        injector.inject_service_unavailable(service_name="api")
        injector.clear_all()
        for ft in FaultType:
            assert injector._fault_configs[ft].enabled is False

    def test_clear_fault_memory_pressure(self):
        injector = ChaosInjector()
        injector.inject_memory_pressure(target_mb=1)
        injector.clear_fault(FaultType.MEMORY_PRESSURE)
        config = injector._fault_configs[FaultType.MEMORY_PRESSURE]
        assert config.enabled is False
        # 内存应被释放
        assert len(injector._memory_hold_list) == 0


# ═══════════════════════════════════════════════════════════════
# get_active_faults / get_injection_history / get_stats
# ═══════════════════════════════════════════════════════════════


class TestGetActiveFaults:
    """get_active_faults 测试"""

    def test_no_active_faults(self):
        injector = ChaosInjector()
        assert injector.get_active_faults() == []

    def test_active_faults(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.inject_network_timeout()
        active = injector.get_active_faults()
        assert len(active) == 2
        injector.clear_all()

    def test_active_faults_after_clear(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.clear_fault(FaultType.NETWORK_DELAY)
        assert injector.get_active_faults() == []


class TestGetInjectionHistory:
    """get_injection_history 测试"""

    def test_empty_history(self):
        injector = ChaosInjector()
        assert injector.get_injection_history() == []

    def test_history_after_injections(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.inject_network_timeout()
        history = injector.get_injection_history()
        assert len(history) == 2
        assert history[0].fault_type == FaultType.NETWORK_DELAY
        assert history[1].fault_type == FaultType.NETWORK_TIMEOUT
        injector.clear_all()

    def test_history_returns_copy(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        h1 = injector.get_injection_history()
        h2 = injector.get_injection_history()
        assert h1 == h2
        assert h1 is not h2  # 应返回副本
        injector.clear_all()


class TestGetStats:
    """get_stats 测试"""

    def test_empty_stats(self):
        injector = ChaosInjector()
        stats = injector.get_stats()
        assert stats["active_faults"] == 0
        assert stats["total_injections"] == 0
        assert stats["total_triggered"] == 0
        assert stats["total_affected_requests"] == 0

    def test_stats_after_injection(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        stats = injector.get_stats()
        assert stats["active_faults"] == 1
        assert stats["total_injections"] == 1
        injector.clear_all()

    def test_stats_after_trigger(self):
        injector = ChaosInjector()
        injector.inject_network_delay(delay_ms=100)
        injector.trigger_if_active(FaultType.NETWORK_DELAY)
        injector.trigger_if_active(FaultType.NETWORK_DELAY)
        stats = injector.get_stats()
        assert stats["total_triggered"] >= 2
        assert stats["total_affected_requests"] >= 2
        injector.clear_all()

    def test_stats_fault_types_dict(self):
        injector = ChaosInjector()
        stats = injector.get_stats()
        assert "fault_types" in stats
        assert isinstance(stats["fault_types"], dict)
        for ft in FaultType:
            assert ft.value in stats["fault_types"]


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════


class TestGlobalSingleton:
    """get_chaos_injector 测试"""

    def test_get_singleton(self):
        injector = get_chaos_injector()
        assert injector is not None
        assert isinstance(injector, ChaosInjector)

    def test_singleton_returns_same_instance(self):
        # 注意：全局单例可能被其他测试修改
        i1 = get_chaos_injector()
        i2 = get_chaos_injector()
        assert i1 is i2


# ═══════════════════════════════════════════════════════════════
# with_chaos_injection 装饰器
# ═══════════════════════════════════════════════════════════════


class TestWithChaosInjectionDecorator:
    """with_chaos_injection 装饰器测试"""

    def test_decorator_no_fault_active(self):
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def my_func(x):
            return x * 2

        injector = get_chaos_injector()
        injector.clear_all()
        assert my_func(5) == 10

    def test_decorator_with_network_timeout(self):
        @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
        def my_func():
            return "success"

        injector = get_chaos_injector()
        injector.inject_network_timeout(probability=1.0)
        try:
            with pytest.raises(TimeoutError):
                my_func()
        finally:
            injector.clear_all()

    def test_decorator_with_service_unavailable(self):
        @with_chaos_injection(FaultType.SERVICE_UNAVAILABLE)
        def my_func():
            return "success"

        injector = get_chaos_injector()
        injector.inject_service_unavailable(service_name="test-svc")
        try:
            with pytest.raises(ConnectionError):
                my_func()
        finally:
            injector.clear_all()

    def test_decorator_preserves_function_name(self):
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def my_named_function():
            return "ok"

        assert my_named_function.__name__ == "my_named_function"


# ═══════════════════════════════════════════════════════════════
# chaos_fault 上下文管理器
# ═══════════════════════════════════════════════════════════════


class TestChaosFaultContextManager:
    """chaos_fault 上下文管理器测试"""

    def test_context_manager_network_delay(self):
        injector = get_chaos_injector()
        injector.clear_all()
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=10):
            config = injector._fault_configs[FaultType.NETWORK_DELAY]
            assert config.enabled is True
            assert config.delay_ms == 10
        # 退出后应清除
        assert injector._fault_configs[FaultType.NETWORK_DELAY].enabled is False

    def test_context_manager_service_unavailable(self):
        injector = get_chaos_injector()
        injector.clear_all()
        with chaos_fault(FaultType.SERVICE_UNAVAILABLE, service_name="api"):
            assert injector._fault_configs[FaultType.SERVICE_UNAVAILABLE].enabled is True
        assert injector._fault_configs[FaultType.SERVICE_UNAVAILABLE].enabled is False

    def test_context_manager_clears_on_exception(self):
        injector = get_chaos_injector()
        injector.clear_all()
        with pytest.raises(ValueError):
            with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=10):
                raise ValueError("test error")
        # 异常后应清除故障
        assert injector._fault_configs[FaultType.NETWORK_DELAY].enabled is False

    def test_context_manager_message_loss(self):
        injector = get_chaos_injector()
        injector.clear_all()
        with chaos_fault(FaultType.MESSAGE_LOSS, loss_percent=50):
            assert injector._fault_configs[FaultType.MESSAGE_LOSS].enabled is True
        assert injector._fault_configs[FaultType.MESSAGE_LOSS].enabled is False


# ═══════════════════════════════════════════════════════════════
# 线程安全
# ═══════════════════════════════════════════════════════════════


class TestThreadSafety:
    """线程安全测试"""

    def test_concurrent_inject_and_clear(self):
        injector = ChaosInjector()
        errors = []

        def injector_worker():
            try:
                for _ in range(10):
                    injector.inject_network_delay(delay_ms=10)
                    injector.clear_fault(FaultType.NETWORK_DELAY)
            except Exception as e:
                errors.append(e)

        def trigger_worker():
            try:
                for _ in range(10):
                    injector.trigger_if_active(FaultType.NETWORK_DELAY)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=injector_worker),
            threading.Thread(target=trigger_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        injector.clear_all()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
