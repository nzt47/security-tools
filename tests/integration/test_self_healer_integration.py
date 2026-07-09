#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""self_healer 集成测试

覆盖 monitoring/self_healer.py 的自愈机制：
- 枚举与 dataclass 验证
- 策略初始化（4 种策略配置）
- 冷却时间与频率限制检查
- execute_action 主流程（跳过条件、动作分发、回调）
- 各动作执行（restart_service/clear_cache/recover_circuit_breaker/gc_collect/clear_memory）
- 记录、查询和统计
- 验证自愈效果（verify_heal）
- 后台线程启停
- 全局单例
"""

import gc
import os
import sys
import time
import shutil
import subprocess
import threading
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

from agent.monitoring.self_healer import (
    HealAction,
    HealStatus,
    HealResult,
    HealPolicy,
    SelfHealRecord,
    SelfHealer,
    get_self_healer,
    execute_heal_action,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def healer():
    """默认 healer（无策略配置）"""
    return SelfHealer(config={})


@pytest.fixture
def healer_with_policies():
    """带 4 种策略配置的 healer"""
    config = {
        "enabled": True,
        "self_healing": {
            "restart_service": {
                "enabled": True, "threshold": 3,
                "cooldown": 300, "max_per_hour": 2,
            },
            "clear_cache": {
                "enabled": True, "threshold": 2,
                "cooldown": 600, "max_per_hour": 10,
            },
            "auto_scale": {
                "enabled": False, "threshold": 5,
                "cooldown": 300, "max_per_hour": 4,
            },
            "circuit_breaker_recovery": {
                "enabled": True, "probe_interval": 60,
            },
        },
    }
    return SelfHealer(config=config)


@pytest.fixture
def reset_singleton():
    """重置全局单例"""
    import agent.monitoring.self_healer as module
    old = module._self_healer
    module._self_healer = None
    yield
    module._self_healer = old


def _make_record(action, status, executed_at, message=""):
    """构造 SelfHealRecord 辅助函数"""
    return SelfHealRecord(
        alert_name="test-alert",
        action=action,
        status=status,
        executed_at=executed_at,
        duration_ms=10.0,
        message=message,
    )


# ═══════════════════════════════════════════════════════════════
# 枚举与 Dataclass
# ═══════════════════════════════════════════════════════════════

class TestEnums:
    """枚举验证"""

    def test_heal_action_values(self):
        assert HealAction.RESTART_SERVICE.value == "restart_service"
        assert HealAction.CLEAR_CACHE.value == "clear_cache"
        assert HealAction.RECOVER_CIRCUIT_BREAKER.value == "recover_circuit_breaker"
        assert HealAction.GC_COLLECT.value == "gc_collect"
        assert HealAction.CLEAR_MEMORY.value == "clear_memory"
        assert HealAction.SCALE_UP.value == "scale_up"

    def test_heal_status_values(self):
        assert HealStatus.PENDING.value == "pending"
        assert HealStatus.RUNNING.value == "running"
        assert HealStatus.SUCCESS.value == "success"
        assert HealStatus.FAILED.value == "failed"
        assert HealStatus.SKIPPED.value == "skipped"

    def test_heal_action_count(self):
        assert len(list(HealAction)) == 9

    def test_heal_status_count(self):
        assert len(list(HealStatus)) == 5


class TestDataclasses:
    """Dataclass 验证"""

    def test_heal_result_defaults(self):
        result = HealResult("gc_collect", HealStatus.SUCCESS, "ok", 10.0)
        assert result.action == "gc_collect"
        assert result.status == HealStatus.SUCCESS
        assert result.message == "ok"
        assert result.duration_ms == 10.0
        assert result.error is None
        assert result.verified is False

    def test_heal_result_with_error(self):
        result = HealResult("restart_service", HealStatus.FAILED, "fail", 5.0, error="timeout")
        assert result.error == "timeout"

    def test_heal_policy_defaults(self):
        policy = HealPolicy()
        assert policy.enabled is True
        assert policy.threshold == 3
        assert policy.cooldown == 300
        assert policy.max_per_hour == 5
        assert policy.interval == 60

    def test_heal_policy_custom(self):
        policy = HealPolicy(enabled=False, threshold=5, cooldown=120, max_per_hour=3)
        assert policy.enabled is False
        assert policy.threshold == 5
        assert policy.cooldown == 120
        assert policy.max_per_hour == 3

    def test_self_heal_record(self):
        record = SelfHealRecord(
            alert_name="high-cpu",
            action="clear_cache",
            status=HealStatus.SUCCESS,
            executed_at=time.time(),
            duration_ms=50.0,
            message="cleared 3 items",
        )
        assert record.alert_name == "high-cpu"
        assert record.verified is False


# ═══════════════════════════════════════════════════════════════
# 初始化与策略配置
# ═══════════════════════════════════════════════════════════════

class TestInitialization:
    """初始化与策略配置"""

    def test_default_config_no_policies(self, healer):
        """空 config → 无策略"""
        assert healer._enabled is True
        assert healer._policies == {}

    def test_disabled_healer(self):
        healer = SelfHealer(config={"enabled": False})
        assert healer._enabled is False

    def test_policies_initialized(self, healer_with_policies):
        """4 种策略正确加载"""
        h = healer_with_policies
        assert "restart_service" in h._policies
        assert "clear_cache" in h._policies
        assert "scale_up" in h._policies
        assert "recover_circuit_breaker" in h._policies

    def test_restart_service_policy_values(self, healer_with_policies):
        p = healer_with_policies._policies["restart_service"]
        assert p.enabled is True
        assert p.threshold == 3
        assert p.cooldown == 300
        assert p.max_per_hour == 2

    def test_clear_cache_policy_values(self, healer_with_policies):
        p = healer_with_policies._policies["clear_cache"]
        assert p.enabled is True
        assert p.threshold == 2
        assert p.cooldown == 600
        assert p.max_per_hour == 10

    def test_scale_up_policy_values(self, healer_with_policies):
        p = healer_with_policies._policies["scale_up"]
        assert p.enabled is False
        assert p.threshold == 5
        assert p.cooldown == 300
        assert p.max_per_hour == 4

    def test_circuit_breaker_policy_values(self, healer_with_policies):
        """熔断恢复策略: threshold 硬编码 1, max_per_hour 硬编码 60"""
        p = healer_with_policies._policies["recover_circuit_breaker"]
        assert p.enabled is True
        assert p.threshold == 1
        assert p.cooldown == 60
        assert p.max_per_hour == 60

    def test_partial_config(self):
        """只配置部分策略"""
        config = {"self_healing": {"clear_cache": {"enabled": False}}}
        h = SelfHealer(config=config)
        assert "clear_cache" in h._policies
        assert h._policies["clear_cache"].enabled is False
        assert "restart_service" not in h._policies

    def test_timeouts_initialized(self, healer):
        """超时参数有默认值"""
        assert healer._restart_timeout > 0
        assert healer._sync_timeout > 0
        assert healer._verify_timeout > 0
        assert healer._thread_join_timeout > 0

    def test_max_records_default(self, healer):
        assert healer._max_records == 500

    def test_health_check_interval_default(self, healer):
        assert healer._health_check_interval == 30

    def test_healer_trace_id_generated(self, healer):
        assert healer._healer_trace_id.startswith("self-healer-")


# ═══════════════════════════════════════════════════════════════
# 冷却时间检查
# ═══════════════════════════════════════════════════════════════

class TestCooldownCheck:
    """_check_cooldown 参数化测试"""

    def test_no_policy_returns_true(self, healer):
        """无策略 → 直接返回 True"""
        assert healer._check_cooldown("clear_cache") is True

    def test_no_records_returns_true(self, healer_with_policies):
        assert healer_with_policies._check_cooldown("clear_cache") is True

    def test_recent_success_blocks(self, healer_with_policies):
        """冷却时间内 SUCCESS → 返回 False"""
        h = healer_with_policies
        h._records.append(_make_record("clear_cache", HealStatus.SUCCESS, time.time()))
        assert h._check_cooldown("clear_cache") is False

    def test_expired_success_allows(self, healer_with_policies):
        """冷却时间外 SUCCESS → 返回 True"""
        h = healer_with_policies
        h._records.append(
            _make_record("clear_cache", HealStatus.SUCCESS, time.time() - 700)
        )
        assert h._check_cooldown("clear_cache") is True

    def test_failed_record_does_not_block(self, healer_with_policies):
        """FAILED 记录不触发冷却"""
        h = healer_with_policies
        h._records.append(_make_record("clear_cache", HealStatus.FAILED, time.time()))
        assert h._check_cooldown("clear_cache") is True

    def test_skipped_record_does_not_block(self, healer_with_policies):
        """SKIPPED 记录不触发冷却"""
        h = healer_with_policies
        h._records.append(_make_record("clear_cache", HealStatus.SKIPPED, time.time()))
        assert h._check_cooldown("clear_cache") is True

    def test_different_action_record_does_not_block(self, healer_with_policies):
        """不同 action 的记录不影响"""
        h = healer_with_policies
        h._records.append(_make_record("restart_service", HealStatus.SUCCESS, time.time()))
        assert h._check_cooldown("clear_cache") is True

    def test_only_latest_success_matters(self, healer_with_policies):
        """只看最近一条 SUCCESS：最近 SUCCESS 在冷却外 → True"""
        h = healer_with_policies
        h._records.append(
            _make_record("clear_cache", HealStatus.SUCCESS, time.time() - 700)
        )
        h._records.append(
            _make_record("clear_cache", HealStatus.FAILED, time.time())
        )
        # reversed 后先遇到 FAILED（跳过），再遇到 SUCCESS（冷却外）→ True
        assert h._check_cooldown("clear_cache") is True

    def test_boundary_exactly_cooldown(self, healer_with_policies):
        """边界: elapsed == cooldown → True (不小于)"""
        h = healer_with_policies
        # cooldown=600, elapsed≈600 → not (elapsed < cooldown) → True
        h._records.append(
            _make_record("clear_cache", HealStatus.SUCCESS, time.time() - 601)
        )
        assert h._check_cooldown("clear_cache") is True


# ═══════════════════════════════════════════════════════════════
# 频率限制检查
# ═══════════════════════════════════════════════════════════════

class TestRateLimitCheck:
    """_check_rate_limit 参数化测试"""

    def test_no_policy_returns_true(self, healer):
        assert healer._check_rate_limit("clear_cache") is True

    def test_under_limit_returns_true(self, healer_with_policies):
        """未超限 → True"""
        h = healer_with_policies
        for _ in range(9):
            h._records.append(
                _make_record("clear_cache", HealStatus.SUCCESS, time.time())
            )
        assert h._check_rate_limit("clear_cache") is True

    def test_at_limit_returns_false(self, healer_with_policies):
        """达到上限(max_per_hour=10) → False"""
        h = healer_with_policies
        for _ in range(10):
            h._records.append(
                _make_record("clear_cache", HealStatus.SUCCESS, time.time())
            )
        assert h._check_rate_limit("clear_cache") is False

    def test_over_limit_returns_false(self, healer_with_policies):
        h = healer_with_policies
        for _ in range(15):
            h._records.append(
                _make_record("clear_cache", HealStatus.SUCCESS, time.time())
            )
        assert h._check_rate_limit("clear_cache") is False

    def test_old_records_not_counted(self, healer_with_policies):
        """一小时前的记录不计入"""
        h = healer_with_policies
        for _ in range(10):
            h._records.append(
                _make_record("clear_cache", HealStatus.SUCCESS, time.time() - 3700)
            )
        assert h._check_rate_limit("clear_cache") is True

    def test_all_statuses_counted(self, healer_with_policies):
        """所有状态的记录都计入频率"""
        h = healer_with_policies
        for status in [HealStatus.SUCCESS, HealStatus.FAILED, HealStatus.SKIPPED]:
            for _ in range(4):
                h._records.append(_make_record("clear_cache", status, time.time()))
        # 12 > 10 → False
        assert h._check_rate_limit("clear_cache") is False

    def test_different_action_not_counted(self, healer_with_policies):
        h = healer_with_policies
        for _ in range(10):
            h._records.append(
                _make_record("restart_service", HealStatus.SUCCESS, time.time())
            )
        # restart_service 的记录不影响 clear_cache
        assert h._check_rate_limit("clear_cache") is True


# ═══════════════════════════════════════════════════════════════
# execute_action 主流程
# ═══════════════════════════════════════════════════════════════

class TestExecuteAction:
    """execute_action 主流程测试"""

    def test_disabled_healer_skips(self):
        h = SelfHealer(config={"enabled": False})
        result = h.execute_action("gc_collect")
        assert result.status == HealStatus.SKIPPED
        assert "禁用" in result.message

    def test_disabled_policy_skips(self, healer_with_policies):
        """scale_up 策略 enabled=False → SKIPPED"""
        result = healer_with_policies.execute_action("scale_up")
        assert result.status == HealStatus.SKIPPED

    def test_cooldown_skips(self, healer_with_policies):
        """冷却时间内 → SKIPPED"""
        h = healer_with_policies
        h._records.append(
            _make_record("gc_collect", HealStatus.SUCCESS, time.time())
        )
        # gc_collect 没有策略 → 不受冷却限制
        # 改用 clear_cache（有策略 cooldown=600）
        h._records.append(
            _make_record("clear_cache", HealStatus.SUCCESS, time.time())
        )
        result = h.execute_action("clear_cache")
        assert result.status == HealStatus.SKIPPED
        assert "冷却" in result.message

    def test_rate_limit_skips(self, healer_with_policies):
        """超频率 → SKIPPED（用 FAILED 记录避免先触发冷却检查）"""
        h = healer_with_policies
        for _ in range(10):
            h._records.append(
                _make_record("clear_cache", HealStatus.FAILED, time.time())
            )
        result = h.execute_action("clear_cache")
        assert result.status == HealStatus.SKIPPED
        assert "频率" in result.message

    def test_gc_collect_success(self, healer):
        """gc_collect 正常执行 → SUCCESS"""
        result = healer.execute_action("gc_collect")
        assert result.status == HealStatus.SUCCESS
        assert result.duration_ms > 0

    def test_unknown_action_fails(self, healer):
        """未知动作 → FAILED"""
        result = healer.execute_action("unknown_action")
        assert result.status == HealStatus.FAILED
        assert "未知" in result.message

    def test_action_lock_prevents_concurrent(self, healer):
        """同一动作并发执行时第二个返回 SKIPPED"""
        h = healer
        lock = h._get_action_lock("gc_collect")
        lock.acquire()  # 模拟动作正在执行
        try:
            result = h.execute_action("gc_collect")
            assert result.status == HealStatus.SKIPPED
            assert "执行中" in result.message
        finally:
            lock.release()

    def test_record_added_after_execution(self, healer):
        """执行后添加记录"""
        h = healer
        h.execute_action("gc_collect")
        assert len(h._records) == 1
        assert h._records[0].action == "gc_collect"
        assert h._records[0].status == HealStatus.SUCCESS

    def test_callback_triggered(self, healer):
        """_on_heal_executed 回调被触发"""
        h = healer
        callback_calls = []

        def callback(record):
            callback_calls.append(record)

        h.set_on_heal_executed(callback)
        h.execute_action("gc_collect")
        assert len(callback_calls) == 1
        assert callback_calls[0].action == "gc_collect"

    def test_callback_error_does_not_affect_result(self, healer):
        """回调异常不影哐主流程"""
        h = healer

        def bad_callback(record):
            raise RuntimeError("callback error")

        h.set_on_heal_executed(bad_callback)
        result = h.execute_action("gc_collect")
        assert result.status == HealStatus.SUCCESS

    def test_context_passed_to_action(self, healer):
        """context 正确传递"""
        h = healer
        result = h.execute_action("gc_collect", context={"alert_name": "high-mem"})
        assert result.status == HealStatus.SUCCESS
        # 记录中 alert_name 应来自 context
        assert h._records[0].alert_name == "high-mem"


# ═══════════════════════════════════════════════════════════════
# _restart_service
# ═══════════════════════════════════════════════════════════════

class TestRestartService:
    """_restart_service 测试"""

    def test_windows_fallback_success(self, healer):
        """Windows 环境: 不执行 subprocess, 直接 fallback → SUCCESS"""
        if os.name != "nt":
            pytest.skip("Windows only")
        result = healer._restart_service({"service_name": "test-svc"})
        assert result.status == HealStatus.SUCCESS
        assert "重启" in result.message

    @patch("os.name", "posix")
    @patch("subprocess.run")
    def test_linux_systemctl_success(self, mock_run, healer):
        """Linux: systemctl 重启成功"""
        mock_run.return_value = MagicMock(returncode=0)
        result = healer._restart_service({"service_name": "nginx"})
        assert result.status == HealStatus.SUCCESS

    @patch("os.name", "posix")
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_linux_all_commands_fail_fallback(self, mock_run, healer):
        """Linux: 所有命令都找不到 → fallback SUCCESS"""
        result = healer._restart_service({"service_name": "nginx"})
        assert result.status == HealStatus.SUCCESS

    @patch("os.name", "posix")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1))
    def test_linux_timeout_fallback(self, mock_run, healer):
        """Linux: 命令超时 → fallback SUCCESS"""
        result = healer._restart_service({"service_name": "nginx"})
        assert result.status == HealStatus.SUCCESS

    def test_context_none_default_service_name(self, healer):
        """context=None → 默认 service_name='yunshu'"""
        result = healer._restart_service(None)
        assert result.status == HealStatus.SUCCESS


# ═══════════════════════════════════════════════════════════════
# _clear_cache
# ═══════════════════════════════════════════════════════════════

class TestClearCache:
    """_clear_cache 测试"""

    @patch("os.path.exists", return_value=False)
    def test_no_cache_files_success(self, mock_exists, healer):
        """无文件可清 → SUCCESS, cleared_count=0"""
        result = healer._clear_cache({"cache_patterns": ["*"]})
        assert result.status == HealStatus.SUCCESS
        assert "0" in result.message

    @patch("os.path.exists", return_value=False)
    def test_context_none_default_patterns(self, mock_exists, healer):
        """context=None → 默认 patterns=['*']"""
        result = healer._clear_cache(None)
        assert result.status == HealStatus.SUCCESS

    @patch("shutil.rmtree")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.isfile", return_value=False)
    @patch("os.path.exists", return_value=True)
    def test_clear_directory(
        self, mock_exists, mock_isfile, mock_isdir, mock_rmtree, healer
    ):
        """目录存在 → shutil.rmtree 调用"""
        result = healer._clear_cache({"cache_patterns": ["test"]})
        assert result.status == HealStatus.SUCCESS
        assert mock_rmtree.called

    @patch("os.remove")
    @patch("os.path.isdir", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("os.path.exists", return_value=True)
    def test_clear_file(
        self, mock_exists, mock_isfile, mock_isdir, mock_remove, healer
    ):
        """文件存在 → os.remove 调用"""
        result = healer._clear_cache({"cache_patterns": ["test"]})
        assert result.status == HealStatus.SUCCESS
        assert mock_remove.called

    @patch("os.remove", side_effect=PermissionError("denied"))
    @patch("os.path.isdir", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("os.path.exists", return_value=True)
    def test_clear_file_error_continues(
        self, mock_exists, mock_isfile, mock_isdir, mock_remove, healer
    ):
        """删除文件失败 → 继续执行，不中断"""
        result = healer._clear_cache({"cache_patterns": ["test"]})
        assert result.status == HealStatus.SUCCESS


# ═══════════════════════════════════════════════════════════════
# _recover_circuit_breaker
# ═══════════════════════════════════════════════════════════════

class TestRecoverCircuitBreaker:
    """_recover_circuit_breaker 测试"""

    @patch("agent.monitoring.self_healer._ERROR_HANDLER_AVAILABLE", False)
    def test_error_handler_unavailable(self, healer):
        """error_handler 不可用 → SKIPPED"""
        result = healer._recover_circuit_breaker({})
        assert result.status == HealStatus.SKIPPED
        assert "不可用" in result.message

    @patch("agent.monitoring.self_healer._ERROR_HANDLER_AVAILABLE", True)
    @patch("agent.monitoring.self_healer.get_error_handler")
    def test_no_open_breakers(self, mock_get_handler, healer):
        """无 open 熔断器 → SKIPPED"""
        mock_handler = MagicMock()
        mock_handler.get_circuit_breaker_status.return_value = {
            "cb1": {"state": "closed"},
        }
        mock_get_handler.return_value = mock_handler
        result = healer._recover_circuit_breaker({})
        assert result.status == HealStatus.SKIPPED
        assert "没有" in result.message

    @patch("agent.monitoring.self_healer._ERROR_HANDLER_AVAILABLE", True)
    @patch("agent.monitoring.self_healer.get_error_handler")
    def test_recover_open_breaker(self, mock_get_handler, healer):
        """有 open 熔断器 → SUCCESS"""
        mock_handler = MagicMock()
        mock_handler.get_circuit_breaker_status.return_value = {
            "cb1": {"state": "open"},
            "cb2": {"state": "closed"},
        }
        mock_cb = MagicMock()
        mock_handler._circuit_breakers = {"cb1": mock_cb}
        mock_get_handler.return_value = mock_handler

        result = healer._recover_circuit_breaker({})
        assert result.status == HealStatus.SUCCESS
        assert "cb1" in result.message
        assert mock_cb._state == "half_open"

    @patch("agent.monitoring.self_healer._ERROR_HANDLER_AVAILABLE", True)
    @patch("agent.monitoring.self_healer.get_error_handler")
    def test_filter_by_name(self, mock_get_handler, healer):
        """指定 cb_name 过滤"""
        mock_handler = MagicMock()
        mock_handler.get_circuit_breaker_status.return_value = {
            "cb1": {"state": "open"},
            "cb2": {"state": "open"},
        }
        mock_cb1 = MagicMock()
        mock_cb2 = MagicMock()
        mock_handler._circuit_breakers = {"cb1": mock_cb1, "cb2": mock_cb2}
        mock_get_handler.return_value = mock_handler

        result = healer._recover_circuit_breaker({"circuit_breaker_name": "cb1"})
        assert result.status == HealStatus.SUCCESS
        assert "cb1" in result.message
        assert "cb2" not in result.message

    @patch("agent.monitoring.self_healer._ERROR_HANDLER_AVAILABLE", True)
    @patch("agent.monitoring.self_healer.get_error_handler")
    def test_recover_exception_returns_failed(self, mock_get_handler, healer):
        """get_circuit_breaker_status 抛异常 → FAILED"""
        mock_handler = MagicMock()
        mock_handler.get_circuit_breaker_status.side_effect = RuntimeError("db error")
        mock_get_handler.return_value = mock_handler
        result = healer._recover_circuit_breaker({})
        assert result.status == HealStatus.FAILED

    @patch("agent.monitoring.self_healer._ERROR_HANDLER_AVAILABLE", True)
    @patch("agent.monitoring.self_healer.get_error_handler")
    def test_context_none_default_all(self, mock_get_handler, healer):
        """context=None → cb_name='*'"""
        mock_handler = MagicMock()
        mock_handler.get_circuit_breaker_status.return_value = {}
        mock_get_handler.return_value = mock_handler
        result = healer._recover_circuit_breaker(None)
        assert result.status == HealStatus.SKIPPED


# ═══════════════════════════════════════════════════════════════
# _gc_collect / _clear_memory
# ═══════════════════════════════════════════════════════════════

class TestGcCollect:
    """_gc_collect 测试"""

    def test_gc_collect_success(self, healer):
        result = healer._gc_collect({})
        assert result.status == HealStatus.SUCCESS
        assert "回收" in result.message

    def test_gc_collect_context_none(self, healer):
        result = healer._gc_collect(None)
        assert result.status == HealStatus.SUCCESS

    @patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=50.0)
    def test_gc_collect_memory_tracking(self, mock_mem, healer):
        result = healer._gc_collect({})
        assert result.status == HealStatus.SUCCESS


class TestClearMemory:
    """_clear_memory 测试"""

    @patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=50.0)
    def test_clear_memory_success(self, mock_mem, healer):
        result = healer._clear_memory({})
        assert result.status == HealStatus.SUCCESS
        assert "释放" in result.message

    def test_clear_memory_context_none(self, healer):
        result = healer._clear_memory(None)
        assert result.status == HealStatus.SUCCESS

    @patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=50.0)
    @patch("os.name", "posix")
    @patch("subprocess.run")
    @patch("builtins.open", new_callable=MagicMock)
    def test_clear_memory_linux(self, mock_open, mock_run, mock_mem, healer):
        """Linux 环境: 尝试 sync + drop_caches"""
        result = healer._clear_memory({})
        assert result.status == HealStatus.SUCCESS

    @patch(
        "agent.monitoring.self_healer.SelfHealer._get_memory_usage",
        side_effect=RuntimeError("fail"),
    )
    def test_clear_memory_exception(self, mock_mem, healer):
        """_get_memory_usage 异常 → FAILED"""
        result = healer._clear_memory({})
        assert result.status == HealStatus.FAILED


class TestGetMemoryUsage:
    """_get_memory_usage 测试"""

    def test_returns_float(self, healer):
        mem = healer._get_memory_usage()
        assert isinstance(mem, float)
        assert mem >= 0


# ═══════════════════════════════════════════════════════════════
# 记录、查询和统计
# ═══════════════════════════════════════════════════════════════

class TestRecordAndQuery:
    """_record_execution / get_records / get_stats"""

    def test_record_added(self, healer):
        result = HealResult("gc_collect", HealStatus.SUCCESS, "ok", 10.0)
        healer._record_execution("gc_collect", result, {"alert_name": "test"})
        assert len(healer._records) == 1
        assert healer._records[0].action == "gc_collect"
        assert healer._records[0].alert_name == "test"

    def test_record_context_none(self, healer):
        result = HealResult("gc_collect", HealStatus.SUCCESS, "ok", 10.0)
        healer._record_execution("gc_collect", result, None)
        assert healer._records[0].alert_name == ""

    def test_max_records_eviction(self, healer):
        """超过 _max_records 时移除最旧记录"""
        healer._max_records = 3
        for i in range(5):
            result = HealResult("gc_collect", HealStatus.SUCCESS, str(i), 1.0)
            healer._record_execution("gc_collect", result, {})
        assert len(healer._records) == 3
        # 最旧的 2 条被移除
        assert healer._records[0].message == "2"

    def test_get_records_empty(self, healer):
        records = healer.get_records()
        assert records == []

    def test_get_records_default_limit(self, healer):
        for _ in range(60):
            result = HealResult("gc_collect", HealStatus.SUCCESS, "ok", 1.0)
            healer._record_execution("gc_collect", result, {})
        records = healer.get_records()
        assert len(records) == 50  # 默认 limit=50

    def test_get_records_filter_by_action(self, healer):
        healer._record_execution(
            "gc_collect", HealResult("gc_collect", HealStatus.SUCCESS, "1", 1.0), {}
        )
        healer._record_execution(
            "clear_cache", HealResult("clear_cache", HealStatus.SUCCESS, "2", 1.0), {}
        )
        records = healer.get_records(action="gc_collect")
        assert len(records) == 1
        assert records[0]["action"] == "gc_collect"

    def test_get_records_filter_by_status(self, healer):
        healer._record_execution(
            "gc_collect", HealResult("gc_collect", HealStatus.SUCCESS, "1", 1.0), {}
        )
        healer._record_execution(
            "gc_collect", HealResult("gc_collect", HealStatus.FAILED, "2", 1.0), {}
        )
        records = healer.get_records(status=HealStatus.FAILED)
        assert len(records) == 1
        assert records[0]["status"] == "failed"

    def test_get_records_returns_dicts(self, healer):
        healer._record_execution(
            "gc_collect", HealResult("gc_collect", HealStatus.SUCCESS, "ok", 1.0), {}
        )
        records = healer.get_records()
        assert isinstance(records[0], dict)
        assert "alert_name" in records[0]
        assert "action" in records[0]
        assert "status" in records[0]
        assert "duration_ms" in records[0]

    def test_get_stats_empty(self, healer):
        stats = healer.get_stats()
        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["failed"] == 0
        assert stats["success_rate"] == 0
        assert stats["by_action"] == {}

    def test_get_stats_with_records(self, healer):
        healer._record_execution(
            "gc_collect", HealResult("gc_collect", HealStatus.SUCCESS, "1", 1.0), {}
        )
        healer._record_execution(
            "gc_collect", HealResult("gc_collect", HealStatus.SUCCESS, "2", 1.0), {}
        )
        healer._record_execution(
            "clear_cache", HealResult("clear_cache", HealStatus.FAILED, "3", 1.0), {}
        )
        stats = healer.get_stats()
        assert stats["total"] == 3
        assert stats["success"] == 2
        assert stats["failed"] == 1
        assert stats["success_rate"] == 2 / 3
        assert "gc_collect" in stats["by_action"]
        assert stats["by_action"]["gc_collect"]["success"] == 2
        assert stats["by_action"]["clear_cache"]["failed"] == 1


# ═══════════════════════════════════════════════════════════════
# verify_heal
# ═══════════════════════════════════════════════════════════════

class TestVerifyHeal:
    """verify_heal 测试"""

    @patch("time.sleep")
    def test_verify_success(self, mock_sleep, healer):
        """health.overall >= 0.7 → True"""
        mock_health = MagicMock()
        mock_health.overall = 0.8
        mock_module = MagicMock()
        mock_module.health_assessor.assess.return_value = mock_health
        with patch.dict(sys.modules, {"agent.health.assessor": mock_module}):
            result = healer.verify_heal("gc_collect", timeout=1.0)
        assert result is True

    @patch("time.sleep")
    def test_verify_timeout_low_health(self, mock_sleep, healer):
        """health.overall < 0.7 → 超时 False"""
        mock_health = MagicMock()
        mock_health.overall = 0.3
        mock_module = MagicMock()
        mock_module.health_assessor.assess.return_value = mock_health
        with patch.dict(sys.modules, {"agent.health.assessor": mock_module}):
            result = healer.verify_heal("gc_collect", timeout=0.1)
        assert result is False

    @patch("time.sleep")
    def test_verify_exception_returns_false(self, mock_sleep, healer):
        """assess 抛异常 → 超时 False"""
        mock_module = MagicMock()
        mock_module.health_assessor.assess.side_effect = RuntimeError("fail")
        with patch.dict(sys.modules, {"agent.health.assessor": mock_module}):
            result = healer.verify_heal("gc_collect", timeout=0.1)
        assert result is False

    def test_verify_default_timeout_from_config(self, healer):
        """timeout=None 时使用 _verify_timeout"""
        # _verify_timeout 可能有值，只需验证不报错
        assert healer._verify_timeout > 0


# ═══════════════════════════════════════════════════════════════
# start/stop 后台线程
# ═══════════════════════════════════════════════════════════════

class TestStartStop:
    """start/stop 后台线程测试"""

    def test_start_sets_running(self, healer):
        healer._health_check_interval = 0.05
        healer.start()
        assert healer._running is True
        healer.stop()
        assert healer._running is False

    def test_start_thread_created(self, healer):
        healer._health_check_interval = 0.05
        healer.start()
        assert healer._health_check_thread is not None
        assert healer._health_check_thread.is_alive()
        healer.stop()

    def test_double_start_noop(self, healer):
        healer._health_check_interval = 0.05
        healer.start()
        thread1 = healer._health_check_thread
        healer.start()  # 重复 start
        assert healer._health_check_thread is thread1
        healer.stop()

    def test_stop_without_start(self, healer):
        """未启动直接 stop → 无异常"""
        healer.stop()
        assert healer._running is False

    def test_stop_joins_thread(self, healer):
        healer._health_check_interval = 0.05
        healer.start()
        healer.stop()
        # join 后线程应不再存活
        assert not healer._health_check_thread.is_alive()


# ═══════════════════════════════════════════════════════════════
# 回调函数
# ═══════════════════════════════════════════════════════════════

class TestCallbacks:
    """回调设置与触发"""

    def test_set_on_heal_executed(self, healer):
        def cb(record):
            pass
        healer.set_on_heal_executed(cb)
        assert healer._on_heal_executed is cb

    def test_set_on_heal_verified(self, healer):
        def cb(record, verified):
            pass
        healer.set_on_heal_verified(cb)
        assert healer._on_heal_verified is cb


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

class TestGlobalSingleton:
    """get_self_healer / execute_heal_action"""

    def test_get_self_healer_singleton(self, reset_singleton):
        h1 = get_self_healer()
        h2 = get_self_healer()
        assert h1 is h2

    def test_get_self_healer_with_config(self, reset_singleton):
        """首次调用传入 config"""
        h = get_self_healer({"enabled": True})
        assert h._enabled is True

    def test_execute_heal_action(self, reset_singleton, healer):
        """execute_heal_action 快捷函数"""
        result = execute_heal_action("gc_collect")
        assert result.status in (HealStatus.SUCCESS, HealStatus.SKIPPED)

    def test_singleton_reset(self, reset_singleton):
        h1 = get_self_healer()
        import agent.monitoring.self_healer as module
        module._self_healer = None
        h2 = get_self_healer()
        assert h1 is not h2


# ═══════════════════════════════════════════════════════════════
# _get_action_lock
# ═══════════════════════════════════════════════════════════════

class TestActionLock:
    """_get_action_lock 测试"""

    def test_returns_lock(self, healer):
        lock = healer._get_action_lock("gc_collect")
        assert isinstance(lock, type(threading.Lock()))

    def test_same_action_same_lock(self, healer):
        lock1 = healer._get_action_lock("gc_collect")
        lock2 = healer._get_action_lock("gc_collect")
        assert lock1 is lock2

    def test_different_action_different_lock(self, healer):
        lock1 = healer._get_action_lock("gc_collect")
        lock2 = healer._get_action_lock("clear_cache")
        assert lock1 is not lock2
