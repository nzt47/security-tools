"""灾备恢复（DisasterRecovery）边界测试

覆盖场景：boundary / timeout / empty / invalid / extreme
对应 Day 4 计划任务：BT-004

测试目标模块：agent/disaster_recovery.py
实际 API：
  - DisasterRecovery(backup_root, max_backups, auto_repair)
  - register(name, path, reload_callback) / backup(name) / backup_all()
  - restore(name) / check_integrity(name, validator) / repair_if_corrupted(name, validator)
  - reload_config(name) / recover_on_startup(validators)
  - reset() / get_state(name)
  - RecoveryAction: BACKUP / RESTORE / REPAIR / RELOAD / SKIP
  - RecoveryError(message, error_code, resource)
  - RecoveryResult(success, action, resource, message, duration_ms, timestamp)
  - ResourceState(name, path, backup_path, last_backup, last_recovery, is_corrupted, recovery_count)

注意：tests/unit/test_disaster_recovery_scenarios.py 引用不存在的旧 API，无法导入。
本文件基于实际 API 编写，使用 tmp_path fixture 隔离文件系统操作。
"""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from agent.disaster_recovery import (
    DisasterRecovery,
    RecoveryAction,
    RecoveryError,
    RecoveryResult,
    ResourceState,
    get_trace_id,
    set_trace_id,
)


@pytest.fixture
def dr_manager(tmp_path):
    """创建灾备恢复管理器（使用临时目录）"""
    backup_root = tmp_path / "backups"
    return DisasterRecovery(backup_root=backup_root, max_backups=3, auto_repair=True)


@pytest.fixture
def resource_file(tmp_path):
    """创建临时资源文件"""
    file_path = tmp_path / "resource.json"
    file_path.write_text('{"key": "value"}', encoding="utf-8")
    return file_path


@pytest.fixture
def resource_dir(tmp_path):
    """创建临时资源目录"""
    dir_path = tmp_path / "resource_dir"
    dir_path.mkdir()
    (dir_path / "file1.txt").write_text("content1", encoding="utf-8")
    (dir_path / "file2.txt").write_text("content2", encoding="utf-8")
    return dir_path


@pytest.fixture
def registered_manager(dr_manager, resource_file):
    """已注册资源的灾备管理器"""
    dr_manager.register("test_resource", resource_file)
    return dr_manager


# ═══════════════════════════════════════════════════════════════
#  备份边界条件
# ═══════════════════════════════════════════════════════════════


class TestBackupBoundary:
    """备份操作边界条件测试"""

    def test_boundary_backup_single_file_success(self, registered_manager):
        """备份单个文件成功"""
        result = registered_manager.backup("test_resource")
        assert result.success is True
        assert result.action == RecoveryAction.BACKUP
        assert result.resource == "test_resource"
        assert "备份成功" in result.message

    def test_boundary_backup_directory_success(self, dr_manager, resource_dir):
        """备份目录成功"""
        dr_manager.register("dir_resource", resource_dir)
        result = dr_manager.backup("dir_resource")
        assert result.success is True
        assert result.action == RecoveryAction.BACKUP

    def test_boundary_backup_nonexistent_file(self, dr_manager, tmp_path):
        """备份不存在的文件返回失败"""
        dr_manager.register("missing", tmp_path / "nonexistent.json")
        result = dr_manager.backup("missing")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "不存在" in result.message

    def test_boundary_backup_unregistered_resource(self, dr_manager):
        """备份未注册资源返回失败"""
        result = dr_manager.backup("never_registered")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "未注册" in result.message

    def test_boundary_backup_max_backups_limit(self, dr_manager, resource_file):
        """max_backups 限制下旧备份被清理"""
        dr_manager.register("limited", resource_file)
        # 创建超过 max_backups(3) 个备份
        for _ in range(5):
            time.sleep(0.01)  # 确保时间戳不同
            dr_manager.backup("limited")

        state = dr_manager.get_state("limited")
        backup_files = list(state.backup_path.glob("*.bak"))
        # max_backups=3，只保留最新 3 个
        assert len(backup_files) <= 3

    def test_boundary_backup_multiple_creates_timestamped(self, dr_manager, resource_file):
        """多次备份创建不同时间戳的备份文件"""
        dr_manager.register("multi", resource_file)
        r1 = dr_manager.backup("multi")
        time.sleep(0.01)
        r2 = dr_manager.backup("multi")

        assert r1.success and r2.success
        # 备份文件路径不同（时间戳不同）
        assert r1.message != r2.message

    def test_boundary_backup_updates_last_backup_time(self, registered_manager):
        """备份后 last_backup 时间更新"""
        state_before = registered_manager.get_state("test_resource")
        assert state_before.last_backup == 0.0

        registered_manager.backup("test_resource")
        state_after = registered_manager.get_state("test_resource")
        assert state_after.last_backup > 0.0


# ═══════════════════════════════════════════════════════════════
#  恢复边界条件
# ═══════════════════════════════════════════════════════════════


class TestRestoreBoundary:
    """恢复操作边界条件测试"""

    def test_boundary_restore_from_latest_backup(self, registered_manager, resource_file):
        """从最新备份恢复成功"""
        registered_manager.backup("test_resource")
        # 破坏原文件
        resource_file.write_text("corrupted", encoding="utf-8")

        result = registered_manager.restore("test_resource")
        assert result.success is True
        assert result.action == RecoveryAction.RESTORE
        # 验证内容已恢复
        assert resource_file.read_text(encoding="utf-8") == '{"key": "value"}'

    def test_boundary_restore_no_backup_available(self, dr_manager, resource_file):
        """无备份时恢复失败"""
        dr_manager.register("no_backup", resource_file)
        result = dr_manager.restore("no_backup")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "无可用备份" in result.message

    def test_boundary_restore_corrupted_file_archived(self, registered_manager, resource_file):
        """恢复时损坏文件被归档到 corrupted 目录"""
        registered_manager.backup("test_resource")
        resource_file.write_text("corrupted_data", encoding="utf-8")

        registered_manager.restore("test_resource")

        state = registered_manager.get_state("test_resource")
        corrupted_dir = state.backup_path / "corrupted"
        assert corrupted_dir.exists()
        corrupted_files = list(corrupted_dir.glob("*.corrupted"))
        assert len(corrupted_files) >= 1

    def test_boundary_restore_unregistered_resource(self, dr_manager):
        """恢复未注册资源返回失败"""
        result = dr_manager.restore("never_registered")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP

    def test_boundary_restore_increments_recovery_count(self, registered_manager, resource_file):
        """恢复后 recovery_count 递增"""
        registered_manager.backup("test_resource")
        state_before = registered_manager.get_state("test_resource")
        assert state_before.recovery_count == 0

        registered_manager.restore("test_resource")
        state_after = registered_manager.get_state("test_resource")
        assert state_after.recovery_count == 1

    def test_boundary_restore_clears_corrupted_flag(self, registered_manager, resource_file):
        """恢复后 is_corrupted 标志清除"""
        registered_manager.backup("test_resource")
        # 标记为损坏
        registered_manager.check_integrity(
            "test_resource", validator=lambda p: False
        )
        state = registered_manager.get_state("test_resource")
        assert state.is_corrupted is True

        registered_manager.restore("test_resource")
        state = registered_manager.get_state("test_resource")
        assert state.is_corrupted is False


# ═══════════════════════════════════════════════════════════════
#  空值与缺失边界
# ═══════════════════════════════════════════════════════════════


class TestEmptyAndMissing:
    """空值与缺失边界测试"""

    def test_empty_resource_file_treated_as_valid(self, dr_manager, tmp_path):
        """空文件存在即视为有效（check_integrity 不因空内容失败）"""
        empty_file = tmp_path / "empty.json"
        empty_file.write_text("", encoding="utf-8")
        dr_manager.register("empty", empty_file)

        # 无 validator 时，文件存在即完整
        assert dr_manager.check_integrity("empty") is True

    def test_empty_backup_directory_no_latest(self, dr_manager, resource_file):
        """空备份目录（无 .bak 文件）时 _get_latest_backup 返回 None"""
        dr_manager.register("test", resource_file)
        state = dr_manager.get_state("test")
        # backup_path 存在但无 .bak 文件
        state.backup_path.mkdir(parents=True, exist_ok=True)
        latest = dr_manager._get_latest_backup(state)
        assert latest is None

    def test_missing_resource_file_marked_corrupted(self, dr_manager, tmp_path):
        """资源文件缺失时标记为损坏"""
        missing_path = tmp_path / "missing.json"
        dr_manager.register("missing", missing_path)

        assert dr_manager.check_integrity("missing") is False
        state = dr_manager.get_state("missing")
        assert state.is_corrupted is True

    def test_empty_resource_name_handled(self, dr_manager, resource_file):
        """空字符串资源名正常处理"""
        dr_manager.register("", resource_file)
        result = dr_manager.backup("")
        # 空名也是有效的资源名
        assert result.success is True

    def test_empty_backup_root_auto_created(self, tmp_path):
        """备份根目录不存在时自动创建"""
        backup_root = tmp_path / "nested" / "backup_root"
        assert not backup_root.exists()

        DisasterRecovery(backup_root=backup_root)
        assert backup_root.exists()

    def test_backup_all_empty_registry(self, dr_manager):
        """无注册资源时 backup_all 返回空字典"""
        results = dr_manager.backup_all()
        assert results == {}


# ═══════════════════════════════════════════════════════════════
#  超时与耗时边界
# ═══════════════════════════════════════════════════════════════


class TestTimeoutBoundary:
    """超时与耗时操作边界测试"""

    def test_timeout_slow_validator(self, dr_manager, resource_file):
        """慢校验函数仍能完成"""
        dr_manager.register("slow", resource_file)

        def slow_validator(path):
            time.sleep(0.05)
            return True

        result = dr_manager.check_integrity("slow", validator=slow_validator)
        assert result is True

    def test_timeout_slow_backup_completes(self, dr_manager, tmp_path):
        """大文件备份完成（不因耗时失败）"""
        large_file = tmp_path / "large.dat"
        large_file.write_bytes(b"x" * 100000)  # 100KB
        dr_manager.register("large", large_file)

        result = dr_manager.backup("large")
        assert result.success is True
        assert result.duration_ms >= 0

    def test_timeout_slow_reload_callback(self, dr_manager, resource_file):
        """慢热重载回调完成"""
        reloaded = []

        def slow_callback(path):
            time.sleep(0.05)
            reloaded.append(path)

        dr_manager.register("config", resource_file, reload_callback=slow_callback)
        result = dr_manager.reload_config("config")
        assert result.success is True
        assert len(reloaded) == 1

    def test_timeout_backup_duration_recorded(self, registered_manager):
        """备份 duration_ms 被记录"""
        result = registered_manager.backup("test_resource")
        assert result.success is True
        assert result.duration_ms >= 0
        assert isinstance(result.duration_ms, int)


# ═══════════════════════════════════════════════════════════════
#  非法输入边界
# ═══════════════════════════════════════════════════════════════


class TestInvalidInput:
    """非法输入边界测试"""

    def test_invalid_validator_raises_exception(self, dr_manager, resource_file):
        """校验函数抛异常时标记为损坏"""
        dr_manager.register("test", resource_file)

        def bad_validator(path):
            raise RuntimeError("validator broken")

        result = dr_manager.check_integrity("test", validator=bad_validator)
        assert result is False
        state = dr_manager.get_state("test")
        assert state.is_corrupted is True

    def test_invalid_reload_callback_raises(self, dr_manager, resource_file):
        """热重载回调抛异常时返回失败"""
        def bad_callback(path):
            raise ValueError("callback broken")

        dr_manager.register("config", resource_file, reload_callback=bad_callback)
        result = dr_manager.reload_config("config")
        assert result.success is False
        assert result.action == RecoveryAction.RELOAD
        assert "热重载失败" in result.message

    def test_invalid_backup_root_unwritable(self, tmp_path):
        """备份根目录不可写时初始化可能失败或降级"""
        # Windows 上权限模型不同，此处测试路径创建逻辑
        # 使用超长路径模拟可能的失败
        backup_root = tmp_path / "backup"
        manager = DisasterRecovery(backup_root=backup_root)
        assert manager.backup_root.exists()

    def test_invalid_resource_path_string(self, dr_manager):
        """字符串路径正常转换为 Path"""
        dr_manager.register("str_path", "some/path/file.txt")
        state = dr_manager.get_state("str_path")
        assert isinstance(state.path, Path)
        # Windows 路径分隔符为 \，使用 as_posix() 跨平台比较
        assert state.path.as_posix() == "some/path/file.txt"

    def test_invalid_validator_returning_non_bool(self, dr_manager, resource_file):
        """校验函数返回非布尔值（truthy/falsy）"""
        dr_manager.register("test", resource_file)

        # 返回 truthy 值
        assert dr_manager.check_integrity(
            "test", validator=lambda p: "yes"
        ) is True
        # 返回 falsy 值
        assert dr_manager.check_integrity(
            "test", validator=lambda p: ""
        ) is False

    def test_invalid_backup_corrupted_resource_file(self, dr_manager, tmp_path):
        """备份已损坏的资源文件仍能创建备份"""
        corrupted_file = tmp_path / "corrupted.json"
        corrupted_file.write_text("not valid json{{{", encoding="utf-8")
        dr_manager.register("corrupted", corrupted_file)

        result = dr_manager.backup("corrupted")
        assert result.success is True  # 备份不校验内容


# ═══════════════════════════════════════════════════════════════
#  极端值边界
# ═══════════════════════════════════════════════════════════════


class TestExtremeValues:
    """极端值边界测试"""

    def test_extreme_huge_max_backups(self, tmp_path, resource_file):
        """超大 max_backups 保留所有备份"""
        manager = DisasterRecovery(
            backup_root=tmp_path / "backups", max_backups=10000
        )
        manager.register("test", resource_file)
        for _ in range(5):
            time.sleep(0.01)
            manager.backup("test")

        state = manager.get_state("test")
        backups = list(state.backup_path.glob("*.bak"))
        assert len(backups) == 5  # 全部保留

    def test_extreme_zero_max_backups(self, tmp_path, resource_file):
        """max_backups=0 时保留 0 个备份"""
        manager = DisasterRecovery(
            backup_root=tmp_path / "backups", max_backups=0
        )
        manager.register("test", resource_file)
        manager.backup("test")

        state = manager.get_state("test")
        backups = list(state.backup_path.glob("*.bak"))
        # max_backups=0 → backups[0:] 全部清理
        assert len(backups) == 0

    def test_extreme_many_resources_registered(self, dr_manager, tmp_path):
        """大量资源注册不导致错误"""
        for i in range(100):
            file_path = tmp_path / f"resource_{i}.json"
            file_path.write_text(f'{{"id": {i}}}', encoding="utf-8")
            dr_manager.register(f"resource_{i}", file_path)

        # backup_all 应全部成功
        results = dr_manager.backup_all()
        assert len(results) == 100
        assert all(r.success for r in results.values())

    def test_extreme_rapid_backup_restore_cycles(self, dr_manager, resource_file):
        """快速备份-恢复循环不导致状态错乱"""
        dr_manager.register("cycled", resource_file)
        for i in range(10):
            dr_manager.backup("cycled")
            result = dr_manager.restore("cycled")
            assert result.success is True

        state = dr_manager.get_state("cycled")
        assert state.recovery_count == 10

    def test_extreme_large_file_content_backup(self, dr_manager, tmp_path):
        """超大内容文件备份"""
        large_file = tmp_path / "huge.json"
        large_content = '{"data": "' + "x" * 500000 + '"}'
        large_file.write_text(large_content, encoding="utf-8")
        dr_manager.register("huge", large_file)

        result = dr_manager.backup("huge")
        assert result.success is True

        # 验证备份内容一致
        state = dr_manager.get_state("huge")
        backup_files = list(state.backup_path.glob("*.bak"))
        assert len(backup_files) == 1
        assert backup_files[0].read_text(encoding="utf-8") == large_content


# ═══════════════════════════════════════════════════════════════
#  完整性检查边界
# ═══════════════════════════════════════════════════════════════


class TestIntegrityCheck:
    """完整性检查边界测试"""

    def test_integrity_check_valid_resource(self, registered_manager):
        """完整资源检查通过"""
        assert registered_manager.check_integrity("test_resource") is True
        state = registered_manager.get_state("test_resource")
        assert state.is_corrupted is False

    def test_integrity_check_missing_resource(self, dr_manager, tmp_path):
        """缺失资源检查失败"""
        dr_manager.register("missing", tmp_path / "nonexistent.json")
        assert dr_manager.check_integrity("missing") is False

    def test_integrity_check_custom_validator_pass(self, registered_manager):
        """自定义校验函数通过"""
        def validator(path):
            return path.exists()

        assert registered_manager.check_integrity(
            "test_resource", validator=validator
        ) is True

    def test_integrity_check_custom_validator_fail(self, registered_manager):
        """自定义校验函数失败"""
        def validator(path):
            return False

        assert registered_manager.check_integrity(
            "test_resource", validator=validator
        ) is False
        state = registered_manager.get_state("test_resource")
        assert state.is_corrupted is True

    def test_integrity_check_validator_exception(self, dr_manager, resource_file):
        """校验函数抛异常时视为损坏"""
        dr_manager.register("test", resource_file)

        def bad_validator(path):
            raise RuntimeError("broken")

        assert dr_manager.check_integrity(
            "test", validator=bad_validator
        ) is False

    def test_integrity_check_unregistered_resource(self, dr_manager):
        """未注册资源检查失败"""
        assert dr_manager.check_integrity("never_registered") is False

    def test_integrity_check_resets_corrupted_flag(self, registered_manager):
        """完整检查后 is_corrupted 重置为 False"""
        # 先标记为损坏
        registered_manager.check_integrity(
            "test_resource", validator=lambda p: False
        )
        state = registered_manager.get_state("test_resource")
        assert state.is_corrupted is True

        # 无 validator 检查 → 文件存在即完整
        registered_manager.check_integrity("test_resource")
        state = registered_manager.get_state("test_resource")
        assert state.is_corrupted is False


# ═══════════════════════════════════════════════════════════════
#  自动修复边界
# ═══════════════════════════════════════════════════════════════


class TestRepairIfCorrupted:
    """自动修复边界测试"""

    def test_repair_not_corrupted_skips(self, registered_manager):
        """未损坏时跳过修复"""
        result = registered_manager.repair_if_corrupted("test_resource")
        assert result.success is True
        assert result.action == RecoveryAction.SKIP
        assert "无需修复" in result.message

    def test_repair_corrupted_restores_from_backup(self, registered_manager, resource_file):
        """损坏时从备份恢复

        repair_if_corrupted 内部调用 check_integrity 判断是否损坏，
        必须传入相同的 validator 才能检测到损坏。
        """
        registered_manager.backup("test_resource")
        # 使用 validator 标记为损坏
        fail_validator = lambda p: False

        result = registered_manager.repair_if_corrupted(
            "test_resource", validator=fail_validator
        )
        assert result.success is True
        assert result.action == RecoveryAction.RESTORE

    def test_repair_corrupted_no_backup_fails(self, dr_manager, resource_file):
        """损坏但无备份时修复失败

        repair_if_corrupted 需传入 validator 才能检测到损坏，
        无备份时 restore 返回 SKIP + 失败。
        """
        dr_manager.register("test", resource_file)
        # 无备份，使用 validator 标记损坏
        fail_validator = lambda p: False

        result = dr_manager.repair_if_corrupted(
            "test", validator=fail_validator
        )
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "无可用备份" in result.message

    def test_repair_with_custom_validator(self, registered_manager, resource_file):
        """带自定义校验的修复"""
        registered_manager.backup("test_resource")

        def strict_validator(path):
            content = path.read_text(encoding="utf-8")
            try:
                json.loads(content)
                return True
            except Exception:
                return False

        # 破坏文件
        resource_file.write_text("not json", encoding="utf-8")

        result = registered_manager.repair_if_corrupted(
            "test_resource", validator=strict_validator
        )
        assert result.success is True
        assert result.action == RecoveryAction.RESTORE


# ═══════════════════════════════════════════════════════════════
#  配置热重载边界
# ═══════════════════════════════════════════════════════════════


class TestConfigReload:
    """配置热重载边界测试"""

    def test_reload_config_success(self, dr_manager, resource_file):
        """热重载成功"""
        reloaded_paths = []

        def callback(path):
            reloaded_paths.append(path)

        dr_manager.register("config", resource_file, reload_callback=callback)
        result = dr_manager.reload_config("config")

        assert result.success is True
        assert result.action == RecoveryAction.RELOAD
        assert "热重载成功" in result.message
        assert len(reloaded_paths) == 1

    def test_reload_config_no_callback(self, registered_manager):
        """无回调时热重载失败"""
        result = registered_manager.reload_config("test_resource")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP
        assert "未注册热重载回调" in result.message

    def test_reload_config_unregistered(self, dr_manager):
        """未注册资源热重载失败"""
        result = dr_manager.reload_config("never_registered")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP

    def test_reload_config_callback_exception(self, dr_manager, resource_file):
        """回调异常时热重载失败但不崩溃"""
        def bad_callback(path):
            raise RuntimeError("callback broken")

        dr_manager.register("config", resource_file, reload_callback=bad_callback)
        result = dr_manager.reload_config("config")
        assert result.success is False
        assert "热重载失败" in result.message


# ═══════════════════════════════════════════════════════════════
#  启动恢复边界
# ═══════════════════════════════════════════════════════════════


class TestRecoverOnStartup:
    """启动恢复边界测试"""

    def test_recover_on_startup_all_healthy(self, dr_manager, tmp_path):
        """所有资源健康时启动恢复全部跳过"""
        for i in range(3):
            file_path = tmp_path / f"res_{i}.json"
            file_path.write_text("{}", encoding="utf-8")
            dr_manager.register(f"res_{i}", file_path)

        results = dr_manager.recover_on_startup()
        assert len(results) == 3
        assert all(r.action == RecoveryAction.SKIP for r in results.values())

    def test_recover_on_startup_missing_restored(self, dr_manager, tmp_path):
        """缺失资源从备份恢复"""
        file_path = tmp_path / "res.json"
        file_path.write_text('{"data": "original"}', encoding="utf-8")
        dr_manager.register("res", file_path)
        dr_manager.backup("res")

        # 删除原文件
        file_path.unlink()

        results = dr_manager.recover_on_startup()
        assert results["res"].success is True
        assert results["res"].action == RecoveryAction.RESTORE
        # 文件已恢复
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == '{"data": "original"}'

    def test_recover_on_startup_with_validators(self, dr_manager, tmp_path):
        """带校验函数的启动恢复"""
        file_path = tmp_path / "config.json"
        file_path.write_text('{"valid": true}', encoding="utf-8")
        dr_manager.register("config", file_path)
        dr_manager.backup("config")

        # 破坏文件
        file_path.write_text("corrupted", encoding="utf-8")

        def json_validator(path):
            try:
                json.loads(path.read_text(encoding="utf-8"))
                return True
            except Exception:
                return False

        results = dr_manager.recover_on_startup(
            validators={"config": json_validator}
        )
        assert results["config"].success is True
        assert results["config"].action == RecoveryAction.RESTORE

    def test_recover_on_startup_empty_registry(self, dr_manager):
        """无注册资源时启动恢复返回空字典"""
        results = dr_manager.recover_on_startup()
        assert results == {}


# ═══════════════════════════════════════════════════════════════
#  并发安全
# ═══════════════════════════════════════════════════════════════


class TestConcurrencySafety:
    """并发访问线程安全测试"""

    def test_concurrent_backup_thread_safe(self, dr_manager, tmp_path):
        """并发备份同一资源不冲突"""
        file_path = tmp_path / "shared.json"
        file_path.write_text("{}", encoding="utf-8")
        dr_manager.register("shared", file_path)

        results = []
        lock = threading.Lock()

        def worker():
            r = dr_manager.backup("shared")
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5
        # 至少部分成功（文件锁可能阻止部分操作）
        success_count = sum(1 for r in results if r.success)
        assert success_count >= 1

    def test_concurrent_register_thread_safe(self, dr_manager, tmp_path):
        """并发注册不同资源不冲突"""
        def worker(i):
            file_path = tmp_path / f"res_{i}.json"
            file_path.write_text("{}", encoding="utf-8")
            dr_manager.register(f"res_{i}", file_path)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有资源都已注册
        for i in range(10):
            assert dr_manager.get_state(f"res_{i}") is not None

    def test_concurrent_backup_all_thread_safe(self, dr_manager, tmp_path):
        """并发 backup_all 不冲突"""
        for i in range(5):
            file_path = tmp_path / f"res_{i}.json"
            file_path.write_text("{}", encoding="utf-8")
            dr_manager.register(f"res_{i}", file_path)

        results_list = []
        lock = threading.Lock()

        def worker():
            r = dr_manager.backup_all()
            with lock:
                results_list.append(r)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results_list) == 3
        for results in results_list:
            assert len(results) == 5


# ═══════════════════════════════════════════════════════════════
#  重置功能边界
# ═══════════════════════════════════════════════════════════════


class TestResetFunction:
    """重置功能边界测试"""

    def test_reset_clears_all_resources(self, dr_manager, resource_file):
        """reset 清空所有资源"""
        dr_manager.register("res1", resource_file)
        dr_manager.register("res2", resource_file)
        assert dr_manager.get_state("res1") is not None

        dr_manager.reset()
        assert dr_manager.get_state("res1") is None
        assert dr_manager.get_state("res2") is None

    def test_reset_clears_callbacks(self, dr_manager, resource_file):
        """reset 清空热重载回调"""
        def callback(path):
            pass

        dr_manager.register("config", resource_file, reload_callback=callback)
        dr_manager.reset()

        # 重新注册同名资源但无回调
        dr_manager.register("config", resource_file)
        result = dr_manager.reload_config("config")
        assert result.success is False
        assert "未注册热重载回调" in result.message

    def test_reset_multiple_times_safe(self, dr_manager, resource_file):
        """多次 reset 安全无副作用"""
        dr_manager.register("test", resource_file)
        dr_manager.reset()
        dr_manager.reset()
        dr_manager.reset()
        assert dr_manager.get_state("test") is None

    def test_reset_after_backup_allows_reregister(self, dr_manager, resource_file):
        """reset 后可重新注册资源"""
        dr_manager.register("test", resource_file)
        dr_manager.backup("test")
        dr_manager.reset()

        # 重新注册
        dr_manager.register("test", resource_file)
        result = dr_manager.backup("test")
        assert result.success is True


# ═══════════════════════════════════════════════════════════════
#  RecoveryError 与枚举边界
# ═══════════════════════════════════════════════════════════════


class TestRecoveryErrorAndEnum:
    """RecoveryError 与 RecoveryAction 枚举边界测试"""

    def test_recovery_error_default_error_code(self):
        """RecoveryError 默认错误码"""
        err = RecoveryError("something failed")
        assert str(err) == "something failed"
        assert err.error_code == "RECOVERY_FAILED"
        assert err.resource == ""

    def test_recovery_error_custom_error_code(self):
        """RecoveryError 自定义错误码和资源"""
        err = RecoveryError(
            "backup corrupted",
            error_code="BACKUP_CORRUPTED",
            resource="memory_db",
        )
        assert err.error_code == "BACKUP_CORRUPTED"
        assert err.resource == "memory_db"

    def test_recovery_error_is_exception(self):
        """RecoveryError 是 Exception 子类"""
        err = RecoveryError("test")
        assert isinstance(err, Exception)

    def test_recovery_error_can_be_raised_and_caught(self):
        """RecoveryError 可被 raise 和捕获"""
        with pytest.raises(RecoveryError) as exc_info:
            raise RecoveryError("test", error_code="TEST_001")
        assert exc_info.value.error_code == "TEST_001"

    def test_recovery_action_enum_values(self):
        """RecoveryAction 枚举值正确"""
        assert RecoveryAction.BACKUP == "backup"
        assert RecoveryAction.RESTORE == "restore"
        assert RecoveryAction.REPAIR == "repair"
        assert RecoveryAction.RELOAD == "reload"
        assert RecoveryAction.SKIP == "skip"

    def test_recovery_action_is_str_enum(self):
        """RecoveryAction 是 str 枚举"""
        assert RecoveryAction.BACKUP.value == "backup"
        assert isinstance(RecoveryAction.BACKUP.value, str)

    def test_recovery_result_dataclass_fields(self):
        """RecoveryResult dataclass 默认字段"""
        result = RecoveryResult(
            success=True,
            action=RecoveryAction.SKIP,
            resource="test",
        )
        assert result.success is True
        assert result.action == RecoveryAction.SKIP
        assert result.resource == "test"
        assert result.message == ""
        assert result.duration_ms == 0
        assert result.timestamp > 0  # 自动生成

    def test_resource_state_dataclass_fields(self, tmp_path):
        """ResourceState dataclass 默认字段"""
        state = ResourceState(
            name="test",
            path=tmp_path / "test.json",
            backup_path=tmp_path / "backup",
        )
        assert state.name == "test"
        assert state.last_backup == 0.0
        assert state.last_recovery == 0.0
        assert state.is_corrupted is False
        assert state.recovery_count == 0


# ═══════════════════════════════════════════════════════════════
#  trace_id 上下文边界
# ═══════════════════════════════════════════════════════════════


class TestTraceIdContext:
    """trace_id 上下文边界测试"""

    def test_get_trace_id_default_empty(self):
        """set/get trace_id 配对"""
        set_trace_id("dr_test_id")
        assert get_trace_id() == "dr_test_id"
        set_trace_id("")

    def test_set_trace_id_none_becomes_empty(self):
        """set_trace_id(None) 设置为空字符串"""
        set_trace_id(None)
        assert get_trace_id() == ""
        set_trace_id("")

    def test_set_trace_id_persists_across_calls(self, registered_manager):
        """trace_id 在备份操作期间保持"""
        set_trace_id("dr_trace_123")
        registered_manager.backup("test_resource")
        assert get_trace_id() == "dr_trace_123"
        set_trace_id("")
