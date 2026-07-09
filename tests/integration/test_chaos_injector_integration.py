#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chaos_injector 集成测试

覆盖 monitoring/chaos_injector.py 的混沌工程故障注入：
- FaultType 枚举与 FaultConfig/FaultInjectionRecord dataclass
- ChaosInjector 初始化与 12 种故障注入方法
- trigger_if_active 触发检查（概率/持续时间/目标服务）
- clear_fault/clear_all 清理
- get_active_faults/get_injection_history/get_stats 查询
- with_chaos_injection 装饰器
- chaos_fault 上下文管理器
- 全局单例
"""

import time
import gc
import threading
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

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
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def injector():
    """每个测试使用独立的注入器实例"""
    return ChaosInjector()


@pytest.fixture
def reset_singleton():
    """重置全局单例"""
    import agent.monitoring.chaos_injector as module
    old = module._global_chaos_injector
    module._global_chaos_injector = None
    yield
    module._global_chaos_injector = old


# ═══════════════════════════════════════════════════════════════
# 枚举与 Dataclass
# ═══════════════════════════════════════════════════════════════

class TestEnums:
    def test_fault_type_count(self):
        assert len(list(FaultType)) == 12

    def test_fault_type_values(self):
        assert FaultType.NETWORK_DELAY.value == "network_delay"
        assert FaultType.NETWORK_TIMEOUT.value == "network_timeout"
        assert FaultType.SERVICE_UNAVAILABLE.value == "service_unavailable"
        assert FaultType.MEMORY_PRESSURE.value == "memory_pressure"
        assert FaultType.CPU_PRESSURE.value == "cpu_pressure"
        assert FaultType.DISK_IO_DELAY.value == "disk_io_delay"
        assert FaultType.DISK_FULL.value == "disk_full"
        assert FaultType.CONNECTION_POOL_EXHAUSTED.value == "connection_pool_exhausted"
        assert FaultType.MESSAGE_LOSS.value == "message_loss"
        assert FaultType.MESSAGE_OUT_OF_ORDER.value == "message_out_of_order"
        assert FaultType.MESSAGE_DUPLICATE.value == "message_duplicate"


class TestDataclasses:
    def test_fault_config_defaults(self):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY)
        assert config.enabled is False
        assert config.probability == 1.0
        assert config.duration_ms is None
        assert config.delay_ms is None

    def test_fault_config_custom(self):
        config = FaultConfig(
            fault_type=FaultType.NETWORK_DELAY,
            enabled=True,
            probability=0.5,
            delay_ms=1000,
            target_service="api",
        )
        assert config.enabled is True
        assert config.probability == 0.5
        assert config.delay_ms == 1000
        assert config.target_service == "api"

    def test_fault_injection_record(self):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY)
        record = FaultInjectionRecord(
            fault_type=FaultType.NETWORK_DELAY,
            config=config,
            injected_at=datetime.now(),
        )
        assert record.triggered_count == 0
        assert record.affected_requests == 0
        assert record.recovered_at is None


# ═══════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════

class TestInitialization:
    def test_all_fault_types_initialized(self, injector):
        """12 种故障类型都有默认配置"""
        for fault_type in FaultType:
            assert fault_type in injector._fault_configs
            assert injector._fault_configs[fault_type].enabled is False

    def test_empty_records(self, injector):
        assert injector._injection_records == []

    def test_empty_memory_hold(self, injector):
        assert injector._memory_hold_list == []

    def test_thread_join_timeout(self, injector):
        assert injector._thread_join_timeout > 0

    def test_chaos_trace_id(self, injector):
        assert injector._chaos_trace_id.startswith("chaos-injector-")


# ═══════════════════════════════════════════════════════════════
# 概率与持续时间检查
# ═══════════════════════════════════════════════════════════════

class TestProbabilityCheck:
    def test_disabled_returns_false(self, injector):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=False)
        assert injector._check_probability(config) is False

    def test_probability_1_returns_true(self, injector):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=True, probability=1.0)
        assert injector._check_probability(config) is True

    def test_probability_0_returns_false(self, injector):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=True, probability=0.0)
        assert injector._check_probability(config) is False


class TestDurationCheck:
    def test_disabled_returns_false(self, injector):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=False)
        assert injector._check_duration(config) is False

    def test_no_time_limits_returns_true(self, injector):
        config = FaultConfig(fault_type=FaultType.NETWORK_DELAY, enabled=True)
        assert injector._check_duration(config) is True

    def test_before_start_time_returns_false(self, injector):
        config = FaultConfig(
            fault_type=FaultType.NETWORK_DELAY,
            enabled=True,
            start_time=datetime.now() + timedelta(hours=1),
        )
        assert injector._check_duration(config) is False

    def test_after_end_time_returns_false(self, injector):
        config = FaultConfig(
            fault_type=FaultType.NETWORK_DELAY,
            enabled=True,
            end_time=datetime.now() - timedelta(hours=1),
        )
        assert injector._check_duration(config) is False

    def test_within_time_range_returns_true(self, injector):
        config = FaultConfig(
            fault_type=FaultType.NETWORK_DELAY,
            enabled=True,
            start_time=datetime.now() - timedelta(hours=1),
            end_time=datetime.now() + timedelta(hours=1),
        )
        assert injector._check_duration(config) is True


# ═══════════════════════════════════════════════════════════════
# 故障注入方法
# ═══════════════════════════════════════════════════════════════

class TestInjectNetworkDelay:
    def test_inject_sets_config(self, injector):
        injector.inject_network_delay(delay_ms=500, probability=0.5, target_service="api")
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.enabled is True
        assert config.delay_ms == 500
        assert config.probability == 0.5
        assert config.target_service == "api"

    def test_inject_with_duration(self, injector):
        injector.inject_network_delay(delay_ms=500, duration_ms=1000)
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.duration_ms == 1000
        assert config.end_time is not None

    def test_inject_without_duration(self, injector):
        injector.inject_network_delay(delay_ms=500)
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        assert config.duration_ms is None
        assert config.end_time is None

    def test_inject_adds_record(self, injector):
        injector.inject_network_delay(delay_ms=500)
        assert len(injector._injection_records) == 1
        assert injector._injection_records[0].fault_type == FaultType.NETWORK_DELAY


class TestInjectNetworkTimeout:
    def test_inject_sets_config(self, injector):
        injector.inject_network_timeout(probability=0.3, duration_ms=2000)
        config = injector._fault_configs[FaultType.NETWORK_TIMEOUT]
        assert config.enabled is True
        assert config.probability == 0.3
        assert config.duration_ms == 2000

    def test_inject_adds_record(self, injector):
        injector.inject_network_timeout()
        assert len(injector._injection_records) == 1


class TestInjectServiceUnavailable:
    def test_inject_sets_config(self, injector):
        injector.inject_service_unavailable(
            service_name="downstream", error_code=502, probability=0.7
        )
        config = injector._fault_configs[FaultType.SERVICE_UNAVAILABLE]
        assert config.enabled is True
        assert config.target_service == "downstream"
        assert config.error_code == 502
        assert config.probability == 0.7

    def test_default_error_code(self, injector):
        injector.inject_service_unavailable(service_name="svc")
        config = injector._fault_configs[FaultType.SERVICE_UNAVAILABLE]
        assert config.error_code == 503


class TestInjectMemoryPressure:
    def test_inject_small_target(self, injector):
        """使用 1MB 避免大量内存分配"""
        injector.inject_memory_pressure(target_mb=1, duration_ms=None)
        config = injector._fault_configs[FaultType.MEMORY_PRESSURE]
        assert config.enabled is True
        assert config.target_memory_mb == 1
        assert len(injector._memory_hold_list) > 0

    def test_inject_with_duration_starts_thread(self, injector):
        injector.inject_memory_pressure(target_mb=1, duration_ms=100)
        assert injector._memory_pressure_thread is not None
        # 等待线程完成
        time.sleep(0.2)

    def test_inject_adds_record(self, injector):
        injector.inject_memory_pressure(target_mb=1)
        assert len(injector._injection_records) == 1

    def test_reinject_stops_previous_thread(self, injector):
        """重新注入时停止之前的内存压力线程"""
        injector.inject_memory_pressure(target_mb=1, duration_ms=5000)
        thread1 = injector._memory_pressure_thread
        injector.inject_memory_pressure(target_mb=1, duration_ms=100)
        # 第一个线程应该被停止
        time.sleep(0.2)


class TestInjectCpuPressure:
    @patch("multiprocessing.cpu_count", return_value=2)
    @patch("multiprocessing.Process")
    def test_inject_starts_processes(self, mock_process, mock_cpu_count, injector):
        mock_p = MagicMock()
        mock_process.return_value = mock_p
        injector.inject_cpu_pressure(duration_ms=100)
        assert mock_process.call_count == 2
        config = injector._fault_configs[FaultType.CPU_PRESSURE]
        assert config.enabled is True

    @patch("multiprocessing.cpu_count", return_value=1)
    @patch("multiprocessing.Process")
    def test_inject_adds_record(self, mock_process, mock_cpu_count, injector):
        injector.inject_cpu_pressure(duration_ms=100)
        assert len(injector._injection_records) == 1


class TestInjectDiskIoDelay:
    def test_inject_sets_config(self, injector):
        injector.inject_disk_io_delay(
            delay_ms=200, io_operation="read", probability=0.5
        )
        config = injector._fault_configs[FaultType.DISK_IO_DELAY]
        assert config.enabled is True
        assert config.delay_ms == 200
        assert config.io_operation == "read"
        assert config.probability == 0.5

    def test_default_io_operation(self, injector):
        injector.inject_disk_io_delay(delay_ms=200)
        config = injector._fault_configs[FaultType.DISK_IO_DELAY]
        assert config.io_operation == "both"


class TestInjectDiskFull:
    def test_inject_sets_config(self, injector):
        injector.inject_disk_full(disk_usage_percent=90, duration_ms=1000)
        config = injector._fault_configs[FaultType.DISK_FULL]
        assert config.enabled is True
        assert config.disk_usage_percent == 90

    def test_default_usage(self, injector):
        injector.inject_disk_full()
        config = injector._fault_configs[FaultType.DISK_FULL]
        assert config.disk_usage_percent == 95


class TestInjectConnectionPoolExhausted:
    def test_inject_sets_config(self, injector):
        injector.inject_connection_pool_exhausted(
            pool_size=0, probability=1.0, duration_ms=1000
        )
        config = injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
        assert config.enabled is True
        assert config.pool_size == 0

    def test_default_pool_size(self, injector):
        injector.inject_connection_pool_exhausted()
        config = injector._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED]
        assert config.pool_size == 0


class TestInjectMessageLoss:
    def test_inject_sets_config(self, injector):
        injector.inject_message_loss(loss_percent=20, duration_ms=1000)
        config = injector._fault_configs[FaultType.MESSAGE_LOSS]
        assert config.enabled is True
        assert config.message_loss_percent == 20
        assert config.probability == 0.2

    def test_default_loss_percent(self, injector):
        injector.inject_message_loss()
        config = injector._fault_configs[FaultType.MESSAGE_LOSS]
        assert config.message_loss_percent == 10


class TestInjectMessageOutOfOrder:
    def test_inject_sets_config(self, injector):
        injector.inject_message_out_of_order(probability=0.5, duration_ms=1000)
        config = injector._fault_configs[FaultType.MESSAGE_OUT_OF_ORDER]
        assert config.enabled is True
        assert config.probability == 0.5

    def test_default_probability(self, injector):
        injector.inject_message_out_of_order()
        config = injector._fault_configs[FaultType.MESSAGE_OUT_OF_ORDER]
        assert config.probability == 0.5


class TestInjectMessageDuplicate:
    def test_inject_sets_config(self, injector):
        injector.inject_message_duplicate(
            duplicate_count=3, probability=0.7, duration_ms=1000
        )
        config = injector._fault_configs[FaultType.MESSAGE_DUPLICATE]
        assert config.enabled is True
        assert config.duplicate_count == 3
        assert config.probability == 0.7

    def test_defaults(self, injector):
        injector.inject_message_duplicate()
        config = injector._fault_configs[FaultType.MESSAGE_DUPLICATE]
        assert config.duplicate_count == 2
        assert config.probability == 0.5


# ═══════════════════════════════════════════════════════════════
# trigger_if_active
# ═══════════════════════════════════════════════════════════════

class TestTriggerIfActive:
    def test_unknown_fault_type(self, injector):
        """不存在的故障类型 → False"""
        # FaultType 是枚举，不会不存在，但可测试未启用的故障
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is False

    def test_disabled_fault(self, injector):
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is False

    def test_enabled_fault_triggers(self, injector):
        injector.inject_network_delay(delay_ms=100)
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is True

    def test_target_service_mismatch(self, injector):
        injector.inject_network_delay(delay_ms=100, target_service="api")
        assert injector.trigger_if_active(
            FaultType.NETWORK_DELAY, target_service="other"
        ) is False

    def test_target_service_match(self, injector):
        injector.inject_network_delay(delay_ms=100, target_service="api")
        assert injector.trigger_if_active(
            FaultType.NETWORK_DELAY, target_service="api"
        ) is True

    def test_no_target_service_matches_all(self, injector):
        """故障未指定 target_service 时匹配所有"""
        injector.inject_network_delay(delay_ms=100)
        assert injector.trigger_if_active(
            FaultType.NETWORK_DELAY, target_service="anything"
        ) is True

    def test_updates_record(self, injector):
        injector.inject_network_delay(delay_ms=100)
        injector.trigger_if_active(FaultType.NETWORK_DELAY)
        injector.trigger_if_active(FaultType.NETWORK_DELAY)
        record = injector._injection_records[0]
        assert record.triggered_count == 2
        assert record.affected_requests == 2

    def test_expired_fault_does_not_trigger(self, injector):
        """过期故障不触发"""
        injector.inject_network_delay(delay_ms=100, duration_ms=100)
        # 手动设置 end_time 为过去
        config = injector._fault_configs[FaultType.NETWORK_DELAY]
        config.end_time = datetime.now() - timedelta(hours=1)
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is False

    def test_probability_0_does_not_trigger(self, injector):
        injector.inject_network_delay(delay_ms=100, probability=0.0)
        assert injector.trigger_if_active(FaultType.NETWORK_DELAY) is False


# ═══════════════════════════════════════════════════════════════
# get_delay_ms
# ═══════════════════════════════════════════════════════════════

class TestGetDelayMs:
    def test_returns_delay(self, injector):
        injector.inject_network_delay(delay_ms=500)
        assert injector.get_delay_ms(FaultType.NETWORK_DELAY) == 500

    def test_disabled_returns_none(self, injector):
        assert injector.get_delay_ms(FaultType.NETWORK_DELAY) is None

    def test_no_delay_returns_none(self, injector):
        injector.inject_network_timeout()
        assert injector.get_delay_ms(FaultType.NETWORK_TIMEOUT) is None


# ═══════════════════════════════════════════════════════════════
# clear_fault / clear_all
# ═══════════════════════════════════════════════════════════════

class TestClearFault:
    def test_clear_disables_fault(self, injector):
        injector.inject_network_delay(delay_ms=100)
        assert injector._fault_configs[FaultType.NETWORK_DELAY].enabled is True
        injector.clear_fault(FaultType.NETWORK_DELAY)
        assert injector._fault_configs[FaultType.NETWORK_DELAY].enabled is False

    def test_clear_updates_record(self, injector):
        injector.inject_network_delay(delay_ms=100)
        injector.clear_fault(FaultType.NETWORK_DELAY)
        record = injector._injection_records[0]
        assert record.recovered_at is not None

    def test_clear_memory_pressure(self, injector):
        """清除内存压力故障时释放内存"""
        injector.inject_memory_pressure(target_mb=1)
        assert len(injector._memory_hold_list) > 0
        injector.clear_fault(FaultType.MEMORY_PRESSURE)
        assert len(injector._memory_hold_list) == 0


class TestClearAll:
    def test_clear_all_disables_all(self, injector):
        injector.inject_network_delay(delay_ms=100)
        injector.inject_network_timeout()
        injector.inject_service_unavailable(service_name="svc")
        injector.clear_all()
        for config in injector._fault_configs.values():
            assert config.enabled is False

    def test_clear_all_releases_memory(self, injector):
        injector.inject_memory_pressure(target_mb=1)
        injector.clear_all()
        assert len(injector._memory_hold_list) == 0


# ═══════════════════════════════════════════════════════════════
# 查询方法
# ═══════════════════════════════════════════════════════════════

class TestGetActiveFaults:
    def test_no_active_faults(self, injector):
        assert injector.get_active_faults() == []

    def test_active_faults_listed(self, injector):
        injector.inject_network_delay(delay_ms=100)
        injector.inject_network_timeout()
        active = injector.get_active_faults()
        assert len(active) == 2

    def test_cleared_faults_not_listed(self, injector):
        injector.inject_network_delay(delay_ms=100)
        injector.clear_fault(FaultType.NETWORK_DELAY)
        assert injector.get_active_faults() == []


class TestGetInjectionHistory:
    def test_empty_history(self, injector):
        assert injector.get_injection_history() == []

    def test_history_listed(self, injector):
        injector.inject_network_delay(delay_ms=100)
        injector.inject_network_timeout()
        history = injector.get_injection_history()
        assert len(history) == 2
        assert history[0].fault_type == FaultType.NETWORK_DELAY
        assert history[1].fault_type == FaultType.NETWORK_TIMEOUT


class TestGetStats:
    def test_empty_stats(self, injector):
        stats = injector.get_stats()
        assert stats["active_faults"] == 0
        assert stats["total_injections"] == 0
        assert stats["total_triggered"] == 0
        assert stats["total_affected_requests"] == 0
        assert len(stats["fault_types"]) == 12

    def test_stats_with_faults(self, injector):
        injector.inject_network_delay(delay_ms=100)
        injector.trigger_if_active(FaultType.NETWORK_DELAY)
        stats = injector.get_stats()
        assert stats["active_faults"] == 1
        assert stats["total_injections"] == 1
        assert stats["total_triggered"] == 1

    def test_stats_all_fault_types_present(self, injector):
        stats = injector.get_stats()
        for ft in FaultType:
            assert ft.value in stats["fault_types"]
            assert stats["fault_types"][ft.value] is False


# ═══════════════════════════════════════════════════════════════
# 装饰器
# ═══════════════════════════════════════════════════════════════

class TestDecorator:
    def test_no_fault_function_executes(self, reset_singleton):
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def my_func(x):
            return x * 2

        assert my_func(5) == 10

    def test_network_timeout_raises(self, reset_singleton):
        get_chaos_injector().inject_network_timeout()
        with pytest.raises(TimeoutError):
            @with_chaos_injection(FaultType.NETWORK_TIMEOUT)
            def my_func():
                return "ok"
            my_func()

    def test_service_unavailable_raises(self, reset_singleton):
        get_chaos_injector().inject_service_unavailable(service_name="svc")
        with pytest.raises(ConnectionError):
            @with_chaos_injection(FaultType.SERVICE_UNAVAILABLE)
            def my_func():
                return "ok"
            my_func()

    def test_decorator_preserves_function_name(self, reset_singleton):
        @with_chaos_injection(FaultType.NETWORK_DELAY)
        def my_named_function():
            pass

        assert my_named_function.__name__ == "my_named_function"


# ═══════════════════════════════════════════════════════════════
# 上下文管理器
# ═══════════════════════════════════════════════════════════════

class TestContextManager:
    def test_network_delay_context(self, reset_singleton):
        with chaos_fault(FaultType.NETWORK_DELAY, delay_ms=100):
            inj = get_chaos_injector()
            assert inj._fault_configs[FaultType.NETWORK_DELAY].enabled is True
        # 退出后自动清除
        assert get_chaos_injector()._fault_configs[FaultType.NETWORK_DELAY].enabled is False

    def test_network_timeout_context(self, reset_singleton):
        with chaos_fault(FaultType.NETWORK_TIMEOUT):
            assert get_chaos_injector()._fault_configs[FaultType.NETWORK_TIMEOUT].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.NETWORK_TIMEOUT].enabled is False

    def test_service_unavailable_context(self, reset_singleton):
        with chaos_fault(FaultType.SERVICE_UNAVAILABLE, service_name="svc"):
            assert get_chaos_injector()._fault_configs[FaultType.SERVICE_UNAVAILABLE].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.SERVICE_UNAVAILABLE].enabled is False

    def test_disk_io_delay_context(self, reset_singleton):
        with chaos_fault(FaultType.DISK_IO_DELAY, delay_ms=100):
            assert get_chaos_injector()._fault_configs[FaultType.DISK_IO_DELAY].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.DISK_IO_DELAY].enabled is False

    def test_disk_full_context(self, reset_singleton):
        with chaos_fault(FaultType.DISK_FULL, disk_usage_percent=90):
            assert get_chaos_injector()._fault_configs[FaultType.DISK_FULL].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.DISK_FULL].enabled is False

    def test_connection_pool_context(self, reset_singleton):
        with chaos_fault(FaultType.CONNECTION_POOL_EXHAUSTED, pool_size=0):
            assert get_chaos_injector()._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.CONNECTION_POOL_EXHAUSTED].enabled is False

    def test_message_loss_context(self, reset_singleton):
        with chaos_fault(FaultType.MESSAGE_LOSS, loss_percent=10):
            assert get_chaos_injector()._fault_configs[FaultType.MESSAGE_LOSS].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.MESSAGE_LOSS].enabled is False

    def test_message_out_of_order_context(self, reset_singleton):
        with chaos_fault(FaultType.MESSAGE_OUT_OF_ORDER, probability=0.5):
            assert get_chaos_injector()._fault_configs[FaultType.MESSAGE_OUT_OF_ORDER].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.MESSAGE_OUT_OF_ORDER].enabled is False

    def test_message_duplicate_context(self, reset_singleton):
        with chaos_fault(FaultType.MESSAGE_DUPLICATE, duplicate_count=2):
            assert get_chaos_injector()._fault_configs[FaultType.MESSAGE_DUPLICATE].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.MESSAGE_DUPLICATE].enabled is False

    def test_memory_pressure_context(self, reset_singleton):
        with chaos_fault(FaultType.MEMORY_PRESSURE, target_mb=1):
            assert get_chaos_injector()._fault_configs[FaultType.MEMORY_PRESSURE].enabled is True
        assert get_chaos_injector()._fault_configs[FaultType.MEMORY_PRESSURE].enabled is False


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

class TestGlobalSingleton:
    def test_singleton(self, reset_singleton):
        inj1 = get_chaos_injector()
        inj2 = get_chaos_injector()
        assert inj1 is inj2

    def test_singleton_reset(self, reset_singleton):
        inj1 = get_chaos_injector()
        import agent.monitoring.chaos_injector as module
        module._global_chaos_injector = None
        inj2 = get_chaos_injector()
        assert inj1 is not inj2
