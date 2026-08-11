"""VersionDeploymentManager 版本部署管理模块单元测试

覆盖：
- DeploymentStatus / RollbackTrigger 枚举值
- DeploymentConfig / DeploymentRecord dataclass 默认值
- VersionDeploymentManager 部署、灰度发布、健康检查、自动回滚、统计全流程
- 单例工厂函数

外部依赖（version_control、线程、时间）一律 mock，不触碰真实磁盘与真实线程。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agent.prompt_manager.deployment import (
    DeploymentConfig,
    DeploymentRecord,
    DeploymentStatus,
    RollbackTrigger,
    VersionDeploymentManager,
    _create_deployment_manager,
    get_deployment_manager,
)


# ═══════════════════════════════════════════════════════════
#  枚举与 dataclass
# ═══════════════════════════════════════════════════════════

class TestDeploymentEnums:
    """DeploymentStatus 与 RollbackTrigger 枚举"""

    def test_deployment_status_all_values(self):
        """DeploymentStatus 应包含 8 个状态且 value 正确"""
        assert list(DeploymentStatus) == [
            DeploymentStatus.PENDING,
            DeploymentStatus.PRE_CHECK,
            DeploymentStatus.DEPLOYING,
            DeploymentStatus.VERIFYING,
            DeploymentStatus.SUCCESS,
            DeploymentStatus.FAILED,
            DeploymentStatus.ROLLING_BACK,
            DeploymentStatus.ROLLED_BACK,
        ]
        assert DeploymentStatus.PENDING.value == "pending"
        assert DeploymentStatus.PRE_CHECK.value == "pre_check"
        assert DeploymentStatus.DEPLOYING.value == "deploying"
        assert DeploymentStatus.VERIFYING.value == "verifying"
        assert DeploymentStatus.SUCCESS.value == "success"
        assert DeploymentStatus.FAILED.value == "failed"
        assert DeploymentStatus.ROLLING_BACK.value == "rolling_back"
        assert DeploymentStatus.ROLLED_BACK.value == "rolled_back"

    def test_rollback_trigger_all_values(self):
        """RollbackTrigger 应包含 5 个触发条件且 value 正确"""
        assert list(RollbackTrigger) == [
            RollbackTrigger.ERROR_RATE,
            RollbackTrigger.FAILURE_COUNT,
            RollbackTrigger.HEALTH_CHECK,
            RollbackTrigger.MANUAL,
            RollbackTrigger.TIMEOUT,
        ]
        assert RollbackTrigger.ERROR_RATE.value == "error_rate"
        assert RollbackTrigger.FAILURE_COUNT.value == "failure_count"
        assert RollbackTrigger.HEALTH_CHECK.value == "health_check"
        assert RollbackTrigger.MANUAL.value == "manual"
        assert RollbackTrigger.TIMEOUT.value == "timeout"


class TestDeploymentConfig:
    """DeploymentConfig dataclass 默认值"""

    def test_defaults(self):
        """DeploymentConfig 应具备正确的默认值"""
        cfg = DeploymentConfig(prompt_id="p1", target_version="v2")
        assert cfg.prompt_id == "p1"
        assert cfg.target_version == "v2"
        assert cfg.canary_enabled is True
        assert cfg.canary_percentage == 10
        assert cfg.canary_duration_seconds == 300
        assert cfg.auto_rollback_enabled is True
        assert cfg.max_error_rate == 0.05
        assert cfg.max_failure_count == 10
        assert cfg.health_check_interval == 30
        assert cfg.health_check_timeout == 10
        assert cfg.deployment_timeout == 1800
        assert cfg.rollback_version is None

    def test_custom_values(self):
        """显式传入的字段应覆盖默认值"""
        cfg = DeploymentConfig(
            prompt_id="p1", target_version="v2",
            canary_enabled=False, canary_percentage=50, rollback_version="v1",
        )
        assert cfg.canary_enabled is False
        assert cfg.canary_percentage == 50
        assert cfg.rollback_version == "v1"


class TestDeploymentRecord:
    """DeploymentRecord dataclass 默认值"""

    def test_defaults(self):
        """DeploymentRecord 可选字段应具备正确默认值"""
        rec = DeploymentRecord(
            deployment_id="d1", prompt_id="p1", target_version="v2",
            previous_version="v1", status=DeploymentStatus.PENDING, started_at=100.0,
        )
        assert rec.completed_at is None
        assert rec.canary_percentage == 100
        assert rec.rollback_trigger is None
        assert rec.rollback_reason == ""
        assert rec.error_count == 0
        assert rec.total_requests == 0
        assert rec.error_rate == 0.0
        assert rec.details == {}

    def test_details_default_factory(self):
        """details 默认应为独立空字典（互不共享）"""
        r1 = DeploymentRecord("a", "p", "v", "v0", DeploymentStatus.PENDING, 1.0)
        r2 = DeploymentRecord("b", "p", "v", "v0", DeploymentStatus.PENDING, 1.0)
        r1.details["x"] = 1
        assert r2.details == {}


# ═══════════════════════════════════════════════════════════
#  公共 fixtures 与工具函数
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def manager():
    """每个测试独立的管理器实例"""
    return VersionDeploymentManager()


@pytest.fixture
def base_config():
    """关闭灰度以简化部署主流程的配置"""
    return DeploymentConfig(
        prompt_id="p1", target_version="v2.0", canary_enabled=False,
    )


def _make_record(**overrides):
    """构造带默认字段的 DeploymentRecord"""
    kwargs = dict(
        deployment_id="d1", prompt_id="p1", target_version="v2.0",
        previous_version="v1.0", status=DeploymentStatus.PENDING, started_at=100.0,
    )
    kwargs.update(overrides)
    return DeploymentRecord(**kwargs)


def _execute_captured_thread(mock_thread_cls):
    """取出 start_deployment 捕获的线程 target 并同步执行（mock 掉真实线程）"""
    call = mock_thread_cls.call_args
    target = call.kwargs["target"]
    args = call.kwargs["args"]
    target(*args)


# ═══════════════════════════════════════════════════════════
#  健康检查注册 / 执行
# ═══════════════════════════════════════════════════════════

class TestRegisterHealthChecker:
    """register_health_checker 方法"""

    def test_register_health_checker(self, manager):
        """注册后应存入 _health_checkers 且可被取回"""
        checker = Mock(return_value=(True, ""))
        manager.register_health_checker("p1", checker)
        assert manager._health_checkers["p1"] is checker


class TestRunHealthCheck:
    """_run_health_check / _run_full_health_check 方法"""

    def test_no_checker_defaults_ok(self, manager):
        """未注册检查器时单检应默认通过"""
        assert manager._run_health_check("p1") is True

    def test_checker_healthy(self, manager):
        """检查器返回健康时应通过"""
        manager.register_health_checker("p1", Mock(return_value=(True, "ok")))
        assert manager._run_health_check("p1") is True

    def test_checker_unhealthy(self, manager):
        """检查器返回不健康时应失败"""
        manager.register_health_checker("p1", Mock(return_value=(False, "down")))
        assert manager._run_health_check("p1") is False

    def test_checker_raises(self, manager):
        """检查器抛异常时应视为不健康"""
        def boom():
            raise RuntimeError("boom")
        manager.register_health_checker("p1", boom)
        assert manager._run_health_check("p1") is False

    def test_full_check_no_checker(self, manager):
        """未配置检查器时完整检查应默认通过并给出提示信息"""
        healthy, message = manager._run_full_health_check("p1")
        assert healthy is True
        assert message == "未配置健康检查器"

    def test_full_check_returns_tuple(self, manager):
        """完整检查应透传检查器返回的 (is_healthy, message)"""
        manager.register_health_checker("p1", Mock(return_value=(False, "broken")))
        assert manager._run_full_health_check("p1") == (False, "broken")

    def test_full_check_raises(self, manager):
        """完整检查器抛异常时应返回不健康与异常信息"""
        def boom():
            raise RuntimeError("boom")
        manager.register_health_checker("p1", boom)
        healthy, message = manager._run_full_health_check("p1")
        assert healthy is False
        assert message == "健康检查异常: boom"


# ═══════════════════════════════════════════════════════════
#  部署主流程
# ═══════════════════════════════════════════════════════════

class TestStartDeployment:
    """start_deployment 方法"""

    def test_records_metadata_and_canary_percentage(self, manager):
        """应生成部署记录并注册到活动部署，灰度开启时 canary_percentage 取配置值"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0", canary_percentage=30)
        fake_time = Mock()
        fake_time.time.return_value = 1000.0
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_get_current_version", return_value="v1.0"), \
             patch("agent.prompt_manager.deployment.threading.Thread"):
            record = manager.start_deployment(config, Mock(return_value=True))
        assert record.deployment_id == "deploy_1000_p1"
        assert record.previous_version == "v1.0"
        assert record.status == DeploymentStatus.PENDING
        assert record.canary_percentage == 30
        assert manager._deployments[record.deployment_id] is record
        assert manager._active_deployments["p1"] is record

    def test_canary_disabled_uses_full_percentage(self, manager):
        """关闭灰度时 canary_percentage 应固定为 100"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0", canary_enabled=False)
        fake_time = Mock()
        fake_time.time.return_value = 1000.0
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_get_current_version", return_value=None), \
             patch("agent.prompt_manager.deployment.threading.Thread"):
            record = manager.start_deployment(config, Mock(return_value=True))
        assert record.canary_percentage == 100
        assert record.previous_version == "unknown"  # 无历史版本时的兜底

    def test_success_path_synchronously(self, manager, base_config):
        """start_deployment 捕获的线程任务应完整走通成功路径并清理活动部署"""
        deploy_callback = Mock(return_value=True)
        fake_time = Mock()
        fake_time.time.return_value = 1000.0
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_get_current_version", return_value="v1.0"), \
             patch("agent.prompt_manager.deployment.threading.Thread") as mock_thread:
            record = manager.start_deployment(base_config, deploy_callback)
            assert mock_thread.return_value.start.called
            _execute_captured_thread(mock_thread)
        assert record.status == DeploymentStatus.SUCCESS
        assert record.completed_at == 1000.0
        assert record.canary_percentage == 100
        deploy_callback.assert_called_once_with("p1", "v2.0")
        assert "p1" not in manager._active_deployments


class TestExecuteDeployment:
    """_execute_deployment 方法各分支"""

    def test_pre_check_failure(self, manager, base_config):
        """部署前检查失败应标记 FAILED 并写入失败原因"""
        record = _make_record()
        manager._active_deployments["p1"] = record
        with patch.object(manager, "_pre_deployment_check", return_value=False):
            manager._execute_deployment(record, base_config, Mock())
        assert record.status == DeploymentStatus.FAILED
        assert record.details["failure_reason"] == "部署前检查失败"
        assert record.completed_at is not None
        assert "p1" not in manager._active_deployments

    def test_deploy_callback_false(self, manager, base_config):
        """部署回调返回 False 应标记 FAILED"""
        record = _make_record()
        manager._active_deployments["p1"] = record
        manager._execute_deployment(record, base_config, Mock(return_value=False))
        assert record.status == DeploymentStatus.FAILED
        assert record.details["failure_reason"] == "部署执行失败"

    def test_success_path(self, manager, base_config):
        """正常路径应依次经历 PRE_CHECK/DEPLOYING/VERIFYING/SUCCESS"""
        record = _make_record()
        manager._active_deployments["p1"] = record
        manager._execute_deployment(record, base_config, Mock(return_value=True))
        assert record.status == DeploymentStatus.SUCCESS
        assert record.completed_at is not None

    def test_canary_rolled_back_early_return(self, manager):
        """灰度阶段已回滚时应直接返回，不再进入全量验证"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0", canary_enabled=True)
        record = _make_record()
        manager._active_deployments["p1"] = record

        def fake_canary(rec, cfg):
            rec.status = DeploymentStatus.ROLLED_BACK

        with patch.object(manager, "_canary_release", side_effect=fake_canary), \
             patch.object(manager, "_post_deployment_verify") as verify:
            manager._execute_deployment(record, config, Mock(return_value=True))
            verify.assert_not_called()
        assert record.status == DeploymentStatus.ROLLED_BACK

    def test_post_verify_failure_triggers_rollback(self, manager, base_config):
        """部署后验证失败应触发 HEALTH_CHECK 回滚"""
        record = _make_record()
        manager._active_deployments["p1"] = record
        with patch.object(manager, "_post_deployment_verify", return_value=False), \
             patch.object(manager, "_execute_rollback"):
            manager._execute_deployment(record, base_config, Mock(return_value=True))
        assert record.status == DeploymentStatus.ROLLED_BACK
        assert record.rollback_trigger == RollbackTrigger.HEALTH_CHECK
        assert record.rollback_reason == "部署后验证失败"

    def test_execution_exception(self, manager, base_config):
        """部署流程抛异常应捕获并标记 FAILED"""
        record = _make_record()
        manager._active_deployments["p1"] = record

        def boom(prompt_id, version):
            raise RuntimeError("boom")

        manager._execute_deployment(record, base_config, boom)
        assert record.status == DeploymentStatus.FAILED
        assert record.details["failure_reason"] == "部署异常: boom"


class TestPreDeploymentCheck:
    """_pre_deployment_check 方法"""

    def test_returns_true(self, manager, base_config, caplog):
        """未注册健康检查器时应放行并给出警告日志"""
        import logging
        record = _make_record()
        with caplog.at_level(logging.WARNING, logger="agent.prompt_manager.deployment"):
            assert manager._pre_deployment_check(base_config, record) is True
            assert "未注册健康检查器" in caplog.text

    def test_no_warning_when_checker_registered(self, manager, caplog):
        """已注册健康检查器时不应出现未注册警告"""
        import logging
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0")
        record = _make_record()
        manager.register_health_checker("p1", Mock(return_value=(True, "")))
        with caplog.at_level(logging.WARNING, logger="agent.prompt_manager.deployment"):
            assert manager._pre_deployment_check(config, record) is True
            assert "未注册健康检查器" not in caplog.text


class TestPostDeploymentVerify:
    """_post_deployment_verify 方法"""

    def test_healthy_returns_true(self, manager, base_config):
        """完整健康检查通过时验证应返回 True"""
        record = _make_record()
        with patch.object(manager, "_run_full_health_check", return_value=(True, "ok")):
            assert manager._post_deployment_verify(base_config, record) is True

    def test_unhealthy_returns_false(self, manager, base_config):
        """完整健康检查失败时验证应返回 False"""
        record = _make_record()
        with patch.object(manager, "_run_full_health_check", return_value=(False, "bad")):
            assert manager._post_deployment_verify(base_config, record) is False


# ═══════════════════════════════════════════════════════════
#  灰度发布与回滚
# ═══════════════════════════════════════════════════════════

class TestCanaryRelease:
    """_canary_release 方法各回滚分支"""

    def test_completes_when_duration_zero(self, manager):
        """灰度持续时间为 0 时应立即完成且不触发回滚"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0",
                                  canary_duration_seconds=0)
        record = _make_record()
        fake_time = Mock()
        fake_time.time.return_value = 1000.0
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_run_health_check", return_value=True), \
             patch.object(manager, "_trigger_rollback") as rollback:
            manager._canary_release(record, config)
            rollback.assert_not_called()
        assert record.status == DeploymentStatus.PENDING

    def test_error_rate_rollback(self, manager):
        """错误率超过阈值应触发 ERROR_RATE 回滚"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0",
                                  max_error_rate=0.05, max_failure_count=100,
                                  canary_duration_seconds=300)
        record = _make_record(total_requests=100, error_count=10)
        times = [2000.0, 2001.0, 2002.0, 2003.0, 2004.0]
        fake_time = Mock()
        fake_time.time.side_effect = times
        fake_time.sleep = Mock()
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_run_health_check", return_value=True), \
             patch.object(manager, "_execute_rollback"):
            manager._canary_release(record, config)
        assert record.status == DeploymentStatus.ROLLED_BACK
        assert record.rollback_trigger == RollbackTrigger.ERROR_RATE
        assert record.error_rate == 0.1
        assert "错误率超标" in record.rollback_reason

    def test_failure_count_rollback(self, manager):
        """失败次数达到阈值应触发 FAILURE_COUNT 回滚"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0",
                                  max_failure_count=10, canary_duration_seconds=300)
        record = _make_record(total_requests=0, error_count=10)
        times = [2000.0, 2001.0, 2002.0, 2003.0, 2004.0]
        fake_time = Mock()
        fake_time.time.side_effect = times
        fake_time.sleep = Mock()
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_run_health_check", return_value=True), \
             patch.object(manager, "_execute_rollback"):
            manager._canary_release(record, config)
        assert record.status == DeploymentStatus.ROLLED_BACK
        assert record.rollback_trigger == RollbackTrigger.FAILURE_COUNT

    def test_timeout_rollback(self, manager):
        """部署超时应触发 TIMEOUT 回滚"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0",
                                  deployment_timeout=30, canary_duration_seconds=300)
        record = _make_record(started_at=1000.0, total_requests=0, error_count=0)
        times = [2000.0, 2001.0, 2001.0, 2002.0, 2003.0]
        fake_time = Mock()
        fake_time.time.side_effect = times
        fake_time.sleep = Mock()
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_run_health_check", return_value=True), \
             patch.object(manager, "_execute_rollback"):
            manager._canary_release(record, config)
        assert record.status == DeploymentStatus.ROLLED_BACK
        assert record.rollback_trigger == RollbackTrigger.TIMEOUT

    def test_health_check_rollback(self, manager):
        """灰度阶段健康检查失败应触发 HEALTH_CHECK 回滚"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0",
                                  canary_duration_seconds=300)
        record = _make_record()
        times = [2000.0, 2001.0, 2002.0, 2003.0]
        fake_time = Mock()
        fake_time.time.side_effect = times
        fake_time.sleep = Mock()
        with patch("agent.prompt_manager.deployment.time", fake_time), \
             patch.object(manager, "_run_health_check", return_value=False), \
             patch.object(manager, "_execute_rollback"):
            manager._canary_release(record, config)
        assert record.status == DeploymentStatus.ROLLED_BACK
        assert record.rollback_trigger == RollbackTrigger.HEALTH_CHECK
        assert record.rollback_reason == "灰度阶段健康检查失败"


class TestTriggerRollback:
    """_trigger_rollback 方法"""

    def test_success_marks_rolled_back(self, manager):
        """回滚成功应标记 ROLLED_BACK 并记录触发条件与原因"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0")
        record = _make_record()
        with patch.object(manager, "_execute_rollback"):
            manager._trigger_rollback(record, config, RollbackTrigger.MANUAL, "人为操作")
        assert record.status == DeploymentStatus.ROLLED_BACK
        assert record.rollback_trigger == RollbackTrigger.MANUAL
        assert record.rollback_reason == "人为操作"
        assert record.completed_at is not None

    def test_rollback_failure_marks_failed(self, manager):
        """回滚执行抛异常应标记 FAILED 而非 ROLLED_BACK"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0")
        record = _make_record()
        with patch.object(manager, "_execute_rollback", side_effect=RuntimeError("rollback boom")):
            manager._trigger_rollback(record, config, RollbackTrigger.ERROR_RATE, "x")
        assert record.status == DeploymentStatus.FAILED
        assert record.completed_at is not None
        assert record.rollback_trigger == RollbackTrigger.ERROR_RATE


class TestExecuteRollback:
    """_execute_rollback 方法"""

    def test_calls_version_manager(self, manager):
        """应调用版本管理器回滚到 previous_version"""
        config = DeploymentConfig(prompt_id="p1", target_version="v2.0")
        record = _make_record(previous_version="v1.0")
        with patch("agent.prompt_manager.version_control.get_version_manager") as get_vm:
            manager._execute_rollback(config, record)
            get_vm.return_value.rollback_to_version.assert_called_once_with("p1", "v1.0")


class TestFailDeployment:
    """_fail_deployment 方法"""

    def test_marks_failed(self, manager):
        """应标记 FAILED、写入失败原因与完成时间"""
        record = _make_record()
        manager._fail_deployment(record, "原因")
        assert record.status == DeploymentStatus.FAILED
        assert record.details["failure_reason"] == "原因"
        assert record.completed_at is not None


# ═══════════════════════════════════════════════════════════
#  版本获取 / 错误上报 / 手动回滚 / 查询
# ═══════════════════════════════════════════════════════════

class TestGetCurrentVersion:
    """_get_current_version 方法"""

    def test_returns_first_history_version(self, manager):
        """有版本历史时应返回第一条记录版本号"""
        vm = Mock()
        vm.get_version_history.return_value = [
            SimpleNamespace(version_number="v2.0"),
            SimpleNamespace(version_number="v1.0"),
        ]
        with patch("agent.prompt_manager.version_control.get_version_manager", return_value=vm):
            assert manager._get_current_version("p1") == "v2.0"

    def test_returns_none_on_empty_or_error(self, manager):
        """历史为空或获取异常时应返回 None"""
        vm = Mock()
        vm.get_version_history.return_value = []
        with patch("agent.prompt_manager.version_control.get_version_manager", return_value=vm):
            assert manager._get_current_version("p1") is None
        with patch("agent.prompt_manager.version_control.get_version_manager",
                   side_effect=RuntimeError("boom")):
            assert manager._get_current_version("p1") is None


class TestReportErrorSuccess:
    """report_error / report_success 方法"""

    def test_report_error_increments(self, manager):
        """报告错误应同时累加 error_count 与 total_requests"""
        record = _make_record()
        manager._active_deployments["p1"] = record
        manager.report_error("p1")
        assert record.error_count == 1
        assert record.total_requests == 1

    def test_report_success_increments(self, manager):
        """报告成功应仅累加 total_requests"""
        record = _make_record()
        manager._active_deployments["p1"] = record
        manager.report_success("p1")
        assert record.total_requests == 1
        assert record.error_count == 0

    def test_report_unknown_prompt_noop(self, manager):
        """无活动部署的 prompt 上报不应抛异常"""
        manager.report_error("missing")
        manager.report_success("missing")


class TestManualRollback:
    """manual_rollback 方法"""

    def test_unknown_id_returns_false(self, manager):
        """不存在的部署 ID 应返回 False"""
        assert manager.manual_rollback("nope") is False

    def test_known_id_triggers_manual_rollback(self, manager):
        """存在的部署应触发 MANUAL 回滚并返回 True"""
        record = _make_record(previous_version="v1.0")
        manager._deployments["d1"] = record
        with patch.object(manager, "_trigger_rollback") as trigger:
            assert manager.manual_rollback("d1", "线上问题") is True
            args = trigger.call_args[0]
            assert args[0] is record
            assert args[1].rollback_version == "v1.0"
            assert args[2] == RollbackTrigger.MANUAL
            assert args[3] == "线上问题"


class TestGetDeployment:
    """get_deployment 方法"""

    def test_get_existing(self, manager):
        """已存在的部署应返回记录"""
        record = _make_record()
        manager._deployments["d1"] = record
        assert manager.get_deployment("d1") is record

    def test_get_missing_returns_none(self, manager):
        """不存在的部署应返回 None"""
        assert manager.get_deployment("missing") is None


class TestGetDeploymentHistory:
    """get_deployment_history 方法"""

    def _seed(self, manager):
        manager._deployments["d1"] = _make_record(deployment_id="d1", started_at=100.0)
        manager._deployments["d2"] = _make_record(deployment_id="d2", started_at=300.0)
        manager._deployments["d3"] = _make_record(
            deployment_id="d3", prompt_id="p2", started_at=200.0,
            status=DeploymentStatus.ROLLED_BACK, rollback_trigger=RollbackTrigger.MANUAL,
            rollback_reason="r", completed_at=250.0, error_count=2, total_requests=100,
            error_rate=0.02,
        )

    def test_sorted_by_started_at_desc(self, manager):
        """历史记录应按开始时间倒序返回"""
        self._seed(manager)
        ids = [h["deployment_id"] for h in manager.get_deployment_history()]
        assert ids == ["d2", "d3", "d1"]

    def test_filter_by_prompt_id(self, manager):
        """指定 prompt_id 时应过滤出对应记录"""
        self._seed(manager)
        ids = [h["deployment_id"] for h in manager.get_deployment_history(prompt_id="p1")]
        assert ids == ["d2", "d1"]

    def test_limit(self, manager):
        """limit 应截断返回条数"""
        self._seed(manager)
        assert len(manager.get_deployment_history(limit=1)) == 1

    def test_fields(self, manager):
        """返回字段应包含完整序列化信息"""
        self._seed(manager)
        item = next(h for h in manager.get_deployment_history(prompt_id="p2") if h["deployment_id"] == "d3")
        assert item["prompt_id"] == "p2"
        assert item["target_version"] == "v2.0"
        assert item["previous_version"] == "v1.0"
        assert item["status"] == "rolled_back"
        assert item["started_at"] == 200.0
        assert item["completed_at"] == 250.0
        assert item["canary_percentage"] == 100
        assert item["rollback_trigger"] == "manual"
        assert item["rollback_reason"] == "r"
        assert item["error_count"] == 2
        assert item["total_requests"] == 100
        assert item["error_rate"] == 0.02


# ═══════════════════════════════════════════════════════════
#  单例工厂
# ═══════════════════════════════════════════════════════════

class TestDeploymentManagerFactory:
    """工厂函数与全局单例"""

    def test_create_factory_returns_instance(self):
        """_create_deployment_manager 应返回 VersionDeploymentManager 实例"""
        inst = _create_deployment_manager()
        assert isinstance(inst, VersionDeploymentManager)

    def test_get_returns_same_instance_fallback(self):
        """singleton 不可用时 fallback 单例应保持同一实例并懒创建"""
        import agent.prompt_manager.deployment as deployment_mod
        with patch.object(deployment_mod, "_SINGLETON_AVAILABLE", False), \
             patch.object(deployment_mod, "_global_deployment_manager", None):
            inst1 = get_deployment_manager()
            inst2 = get_deployment_manager()
            assert inst1 is inst2
            assert isinstance(inst1, VersionDeploymentManager)

    def test_get_via_singleton_manager(self):
        """singleton 可用时应走 get_singleton 路径"""
        import agent.prompt_manager.deployment as deployment_mod
        fake = VersionDeploymentManager()
        with patch.object(deployment_mod, "_SINGLETON_AVAILABLE", True), \
             patch.object(deployment_mod, "get_singleton", return_value=fake):
            assert get_deployment_manager() is fake
            deployment_mod.get_singleton.assert_called_once_with("prompt_deployment_manager")
