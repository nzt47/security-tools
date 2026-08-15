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
    module.reset_self_healer()
    yield
    module.reset_self_healer()


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
    """_restart_service 测试（修复 D9：不再假成功）"""

    @patch("os.name", "nt")
    def test_windows_no_restart_command_skipped(self, healer):
        """Windows 且无 restart_command → SKIPPED（非 SUCCESS）"""
        result = healer._restart_service({"service_name": "yunshu"})
        assert result.status == HealStatus.SKIPPED
        assert "重启方式" in result.message

    @patch("os.name", "nt")
    def test_windows_restart_command_failure(self, healer):
        """Windows restart_command 退出码非 0 → FAILED 携带证据"""
        mock_result = MagicMock(returncode=1, stderr="boom", stdout="")
        with patch("subprocess.run", return_value=mock_result):
            result = healer._restart_service({"restart_command": ["x.cmd"]})
        assert result.status == HealStatus.FAILED
        assert "boom" in result.message

    @patch("os.name", "nt")
    def test_windows_restart_command_unverified(self, healer):
        """Windows restart_command 成功但端口未恢复 → FAILED（假成功修复）"""
        mock_result = MagicMock(returncode=0, stderr="", stdout="")
        with patch("subprocess.run", return_value=mock_result), \
                patch("agent.monitoring.self_healer.SelfHealer._check_port_open", return_value=False):
            result = healer._restart_service({"restart_command": ["x.cmd"], "ports": [8080]})
        assert result.status == HealStatus.FAILED
        assert "验证失败" in result.message

    @patch("os.name", "nt")
    def test_windows_restart_command_verified(self, healer):
        """Windows restart_command 成功且端口恢复 → SUCCESS 且 verified=True"""
        mock_result = MagicMock(returncode=0, stderr="", stdout="")
        with patch("subprocess.run", return_value=mock_result), \
                patch("agent.monitoring.self_healer.SelfHealer._check_port_open", return_value=True):
            result = healer._restart_service({"restart_command": ["x.cmd"], "ports": [8080]})
        assert result.status == HealStatus.SUCCESS
        assert "验证通过" in result.message
        assert result.verified is True

    @patch("os.name", "nt")
    def test_windows_restart_command_partial_ports_failed(self, healer):
        """部分端口未恢复（8080 通 / 8081 不通）→ FAILED，message 精确定位 failed_ports"""
        mock_result = MagicMock(returncode=0, stderr="", stdout="")
        with patch("subprocess.run", return_value=mock_result), \
                patch("agent.monitoring.self_healer.SelfHealer._check_port_open",
                      side_effect=lambda p: p != 8081):
            result = healer._restart_service({"restart_command": ["x.cmd"], "ports": [8080, 8081]})
        assert result.status == HealStatus.FAILED
        # 失败原因须精确到未恢复的端口（S2 场景：部分不可连）
        assert "8081" in result.message
        assert "验证失败" in result.message
        assert result.verified is False

    @patch("os.name", "posix")
    @patch("subprocess.run")
    def test_linux_systemctl_verified(self, mock_run, healer):
        """Linux: systemctl 重启成功 + 端口恢复 → SUCCESS"""
        mock_run.return_value = MagicMock(returncode=0)
        with patch("agent.monitoring.self_healer.SelfHealer._check_port_open", return_value=True):
            result = healer._restart_service({"service_name": "nginx", "ports": [80]})
        assert result.status == HealStatus.SUCCESS

    @patch("os.name", "posix")
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_linux_all_commands_missing_skipped(self, mock_run, healer):
        """Linux: 所有命令都找不到 → SKIPPED（未找到服务管理工具）"""
        result = healer._restart_service({"service_name": "nginx"})
        assert result.status == HealStatus.SKIPPED
        assert "服务管理工具" in result.message

    @patch("os.name", "posix")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1))
    def test_linux_timeout_skipped(self, mock_run, healer):
        """Linux: 命令超时 → SKIPPED"""
        result = healer._restart_service({"service_name": "nginx"})
        assert result.status == HealStatus.SKIPPED

    @patch("os.name", "nt")
    def test_context_none_default_skipped(self, healer):
        """context=None → SKIPPED（无重启方式，非假成功 SUCCESS）"""
        result = healer._restart_service(None)
        assert result.status == HealStatus.SKIPPED


# ═══════════════════════════════════════════════════════════════
# _clear_cache
# ═══════════════════════════════════════════════════════════════

class TestClearCache:
    """_clear_cache 测试（守安全红线：白名单 + 禁通配）"""

    @pytest.fixture(autouse=True)
    def _setup_whitelist(self, healer):
        """默认注入白名单（healer fixture 默认白名单为空）"""
        healer._cache_whitelist = ["/tmp/cache"]

    def test_wildcard_pattern_skipped(self, healer):
        """pattern='*' → SKIPPED（禁止全量清理）"""
        result = healer._clear_cache({"cache_patterns": ["*"]})
        assert result.status == HealStatus.SKIPPED
        assert "禁止全量" in result.message

    def test_empty_whitelist_disables_clearing(self):
        """白名单为空 → SKIPPED（通配清理默认禁用）"""
        h = SelfHealer(config={})
        result = h._clear_cache({"cache_patterns": ["tmp"]})
        assert result.status == HealStatus.SKIPPED
        assert "白名单为空" in result.message

    def test_out_of_whitelist_path_skipped(self, healer):
        """白名单外路径 → SKIPPED"""
        result = healer._clear_cache({"cache_paths": ["/etc/passwd"]})
        assert result.status == HealStatus.SKIPPED
        assert "不在白名单" in result.message

    def test_whitelist_config_loaded(self):
        """白名单从配置加载"""
        h = SelfHealer(config={"self_healing": {"clear_cache": {"cache_whitelist": ["/safe/cache"]}}})
        assert h._cache_whitelist == ["/safe/cache"]

    @patch("os.path.exists", return_value=False)
    def test_no_cache_files_success(self, mock_exists, healer):
        """白名单内无文件可清 → SUCCESS, cleared_count=0"""
        result = healer._clear_cache({"cache_patterns": ["sub"]})
        assert result.status == HealStatus.SUCCESS
        assert "0" in result.message

    @patch("os.path.exists", return_value=False)
    def test_context_none_default_patterns(self, mock_exists, healer):
        """context=None → 空 patterns → SUCCESS"""
        result = healer._clear_cache(None)
        assert result.status == HealStatus.SUCCESS

    @patch("shutil.rmtree")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.isfile", return_value=False)
    @patch("os.path.exists", return_value=True)
    def test_clear_directory_in_whitelist(
        self, mock_exists, mock_isfile, mock_isdir, mock_rmtree, healer
    ):
        """白名单内目录存在 → shutil.rmtree 调用"""
        result = healer._clear_cache({"cache_patterns": ["test"]})
        assert result.status == HealStatus.SUCCESS
        assert mock_rmtree.called

    @patch("os.remove")
    @patch("os.path.isdir", return_value=False)
    @patch("os.path.isfile", return_value=True)
    @patch("os.path.exists", return_value=True)
    def test_clear_file_in_whitelist(
        self, mock_exists, mock_isfile, mock_isdir, mock_remove, healer
    ):
        """白名单内文件存在 → os.remove 调用"""
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
    """_recover_circuit_breaker 测试（修复 D11：走公开 API，禁改私有字段）"""

    @patch("agent.circuit_breaker.get_all_circuit_breaker_status", return_value={})
    def test_no_open_breakers(self, mock_status, healer):
        """无 open 熔断器 → SKIPPED"""
        result = healer._recover_circuit_breaker({})
        assert result.status == HealStatus.SKIPPED
        assert "没有" in result.message

    @patch("agent.circuit_breaker.get_circuit_breaker")
    @patch("agent.circuit_breaker.get_all_circuit_breaker_status")
    def test_recover_open_breaker_public_api(self, mock_status, mock_get_cb, healer):
        """有 open 熔断器 → 调用公开 API force_close 恢复，不直改私有字段"""
        mock_status.return_value = {
            "cb1": {"state": "open"},
            "cb2": {"state": "closed"},
        }
        mock_breaker = MagicMock()
        mock_breaker.get_status.return_value = {"state": "closed"}
        mock_get_cb.return_value = mock_breaker

        result = healer._recover_circuit_breaker({})
        assert result.status == HealStatus.SUCCESS
        assert "cb1" in result.message
        assert "cb2" not in result.message
        mock_get_cb.assert_called_once_with("cb1")
        mock_breaker.force_close.assert_called_once()
        # 走公开 API 恢复（force_close 调用本身即证明未直改 _state；生产代码禁改私有字段由 grep 校验）

    @patch("agent.circuit_breaker.get_circuit_breaker")
    @patch("agent.circuit_breaker.get_all_circuit_breaker_status")
    def test_filter_by_name(self, mock_status, mock_get_cb, healer):
        """指定 cb_name 过滤"""
        mock_status.return_value = {
            "cb1": {"state": "open"},
            "cb2": {"state": "open"},
        }
        mock_breaker = MagicMock()
        mock_breaker.get_status.return_value = {"state": "closed"}
        mock_get_cb.return_value = mock_breaker

        result = healer._recover_circuit_breaker({"circuit_breaker_name": "cb1"})
        assert result.status == HealStatus.SUCCESS
        assert "cb1" in result.message
        assert "cb2" not in result.message
        mock_get_cb.assert_called_once_with("cb1")

    @patch(
        "agent.circuit_breaker.get_all_circuit_breaker_status",
        side_effect=RuntimeError("db error"),
    )
    def test_recover_exception_returns_failed(self, mock_status, healer):
        """get_all_circuit_breaker_status 抛异常 → FAILED"""
        result = healer._recover_circuit_breaker({})
        assert result.status == HealStatus.FAILED

    @patch("agent.circuit_breaker.get_all_circuit_breaker_status", return_value={})
    def test_context_none_default_all(self, mock_status, healer):
        """context=None → cb_name='*'"""
        result = healer._recover_circuit_breaker(None)
        assert result.status == HealStatus.SKIPPED


# ═══════════════════════════════════════════════════════════════
# _restart_component
# ═══════════════════════════════════════════════════════════════

class TestRestartComponent:
    """_restart_component 测试（补全 D9：进程内模块热重启）"""

    def test_no_target_module_skipped(self, healer):
        """未提供 target_module → SKIPPED"""
        result = healer._restart_component({})
        assert result.status == HealStatus.SKIPPED
        assert "目标模块" in result.message

    def test_reload_success(self, healer):
        """模块热重载成功 → SUCCESS"""
        with patch("agent.monitoring.self_healer.importlib") as mock_importlib:
            mock_importlib.import_module.return_value = MagicMock()
            result = healer._restart_component({"target_module": "agent.monitoring.self_healer"})
        assert result.status == HealStatus.SUCCESS
        assert mock_importlib.reload.called

    def test_reload_failure_failed(self, healer):
        """模块热重载失败 → FAILED"""
        with patch("agent.monitoring.self_healer.importlib") as mock_importlib:
            mock_importlib.import_module.return_value = MagicMock()
            mock_importlib.reload.side_effect = ImportError("boom")
            result = healer._restart_component({"target_module": "some.module"})
        assert result.status == HealStatus.FAILED

    def test_execute_action_dispatch(self, healer):
        """execute_action 分发 restart_component"""
        result = healer.execute_action("restart_component")
        assert result.status == HealStatus.SKIPPED
        assert "目标模块" in result.message


# ═══════════════════════════════════════════════════════════════
# 未实现动作 → SKIPPED
# ═══════════════════════════════════════════════════════════════

class TestUnimplementedActions:
    """未实现动作返回 SKIPPED（原因明确）而非 FAILED"""

    def test_policy_unimplemented_skipped(self, healer):
        """restore_map 预留动作 → SKIPPED"""
        for action in ("retry_limited", "degrade_llm_router", "rebuild_index", "terminate_loop"):
            result = healer.execute_action(action)
            assert result.status == HealStatus.SKIPPED, action
            assert "未实现" in result.message

    def test_heal_action_enum_unimplemented_skipped(self, healer):
        """HealAction 枚举中未实现的动作 → SKIPPED（非 FAILED）"""
        for action in ("scale_up", "scale_down", "restart_pod"):
            result = healer.execute_action(action)
            assert result.status == HealStatus.SKIPPED, action

    def test_unknown_action_fails(self, healer):
        """真正未知的动作 → FAILED"""
        result = healer.execute_action("unknown_action")
        assert result.status == HealStatus.FAILED
        assert "未知" in result.message


# ═══════════════════════════════════════════════════════════════
# verify_action 分发
# ═══════════════════════════════════════════════════════════════

class TestVerifyAction:
    """verify_action 动作专属验证器测试"""

    def test_no_verifier_action_ok(self, healer):
        """无专属验证器的动作 → (True, ...) 仅依赖健康分"""
        ok, reason = healer.verify_action("scale_up")
        assert ok is True
        assert "无专属验证器" in reason

    def test_restart_service_no_basis_fails(self, healer):
        """restart_service 无验证依据 → False"""
        ok, reason = healer.verify_action("restart_service")
        assert ok is False
        assert "验证依据" in reason

    @patch("agent.monitoring.self_healer.SelfHealer._check_port_open", return_value=True)
    def test_restart_service_port_open(self, mock_port, healer):
        """restart_service 端口可连接 → True"""
        healer._verify_state["restart_service"] = {"ports": [8080], "service_name": "x"}
        ok, reason = healer.verify_action("restart_service")
        assert ok is True
        assert "端口" in reason

    @patch("agent.monitoring.self_healer.SelfHealer._check_port_open", return_value=False)
    def test_restart_service_port_closed(self, mock_port, healer):
        """restart_service 端口不可连接 → False"""
        healer._verify_state["restart_service"] = {"ports": [8080], "service_name": "x"}
        ok, reason = healer.verify_action("restart_service")
        assert ok is False
        assert "不可连接" in reason

    def test_clear_cache_no_baseline_fails(self, healer):
        """clear_cache 未记录基线 → False"""
        ok, reason = healer.verify_action("clear_cache")
        assert ok is False

    @patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=90.0)
    def test_memory_freed_ok(self, mock_mem, healer):
        """gc_collect 基线存在且 RSS 下降 > 阈值 → True"""
        healer._verify_state["gc_collect"] = {"mem_mb_before": 100.0}
        ok, reason = healer.verify_action("gc_collect")
        assert ok is True
        assert "RSS" in reason

    @patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=120.0)
    def test_memory_not_freed_fails(self, mock_mem, healer):
        """gc_collect RSS 未下降 → False"""
        healer._verify_state["gc_collect"] = {"mem_mb_before": 100.0}
        ok, reason = healer.verify_action("gc_collect")
        assert ok is False

    def test_memory_no_baseline_fails(self, healer):
        """clear_memory 未记录基线 → False"""
        ok, reason = healer.verify_action("clear_memory")
        assert ok is False

    @patch("agent.circuit_breaker.get_circuit_breaker")
    def test_circuit_breaker_verified(self, mock_get_cb, healer):
        """recover_circuit_breaker 验证：目标非 OPEN → True"""
        healer._last_context["recover_circuit_breaker"] = {"circuit_breaker_name": "cb1"}
        mock_breaker = MagicMock()
        mock_breaker.get_status.return_value = {"state": "closed"}
        mock_get_cb.return_value = mock_breaker
        ok, reason = healer.verify_action("recover_circuit_breaker")
        assert ok is True
        mock_breaker.get_status.assert_called_once()  # 读公开 get_status()

    @patch("agent.circuit_breaker.get_circuit_breaker")
    def test_circuit_breaker_still_open_fails(self, mock_get_cb, healer):
        """recover_circuit_breaker 验证：目标仍 OPEN → False"""
        healer._last_context["recover_circuit_breaker"] = {"circuit_breaker_name": "cb1"}
        mock_breaker = MagicMock()
        mock_breaker.get_status.return_value = {"state": "open"}
        mock_get_cb.return_value = mock_breaker
        ok, reason = healer.verify_action("recover_circuit_breaker")
        assert ok is False
        assert "OPEN" in reason


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
    """verify_heal 测试（修复 D7：动作验证器 + 真实健康分）"""

    @staticmethod
    def _mock_health_module(overall=None, history=None):
        """构造 mock 的 agent.health.assessor 模块"""
        mock_module = MagicMock()
        if history is not None:
            mock_module.health_assessor.get_history.return_value = history
        else:
            mock_health = MagicMock()
            mock_health.overall = overall
            mock_module.health_assessor.get_history.return_value = [mock_health]
        return mock_module

    @patch("time.sleep")
    def test_verify_success(self, mock_sleep, healer):
        """动作验证通过 + 健康分 >= 0.7 → True"""
        healer._verify_state["gc_collect"] = {"mem_mb_before": 100.0}
        with patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=90.0), \
                patch.dict(sys.modules, {"agent.health.assessor": self._mock_health_module(0.8)}):
            result = healer.verify_heal("gc_collect", timeout=5.0)
        assert result is True

    @patch("time.sleep")
    def test_verify_health_none_fails(self, mock_sleep, healer):
        """健康分为 None（无数据禁假满分）→ False，日志含验证失败原因"""
        healer._verify_state["gc_collect"] = {"mem_mb_before": 100.0}
        with patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=90.0), \
                patch.dict(sys.modules, {"agent.health.assessor": self._mock_health_module(None)}):
            result = healer.verify_heal("gc_collect", timeout=0.1)
        assert result is False

    @patch("time.sleep")
    def test_verify_low_health_timeout(self, mock_sleep, healer):
        """健康分 < 0.7 → 超时 False"""
        healer._verify_state["gc_collect"] = {"mem_mb_before": 100.0}
        with patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=90.0), \
                patch.dict(sys.modules, {"agent.health.assessor": self._mock_health_module(0.3)}):
            result = healer.verify_heal("gc_collect", timeout=0.1)
        assert result is False

    @patch("time.sleep")
    def test_verify_action_fails_returns_false(self, mock_sleep, healer):
        """动作验证器失败（restart_service 无验证依据）→ False，即使健康分高"""
        with patch.dict(sys.modules, {"agent.health.assessor": self._mock_health_module(0.9)}):
            result = healer.verify_heal("restart_service", timeout=0.1)
        assert result is False

    @patch("time.sleep")
    def test_verify_exception_returns_false(self, mock_sleep, healer):
        """get_history 抛异常 → 超时 False"""
        mock_module = MagicMock()
        mock_module.health_assessor.get_history.side_effect = RuntimeError("fail")
        with patch.dict(sys.modules, {"agent.health.assessor": mock_module}):
            result = healer.verify_heal("gc_collect", timeout=0.1)
        assert result is False

    @patch("time.sleep")
    def test_verify_empty_history_fails(self, mock_sleep, healer):
        """get_history 为空（无真实评分）→ False"""
        healer._verify_state["gc_collect"] = {"mem_mb_before": 100.0}
        with patch("agent.monitoring.self_healer.SelfHealer._get_memory_usage", return_value=90.0), \
                patch.dict(sys.modules, {"agent.health.assessor": self._mock_health_module(history=[])}):
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
        module.reset_self_healer()
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
