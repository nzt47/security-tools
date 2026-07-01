#!/usr/bin/env python3
"""DisasterRecovery 综合单元测试

【生成日志摘要】
- 生成时间戳: 2026-07-02
- 内容描述: disaster_recovery 模块全量单元测试
- 模型配置: GLM-5.2
- 关键状态变化: 新增 ~50 个测试，目标覆盖率 90%+
"""

import json
import os
import time
import pytest
from pathlib import Path

from agent.disaster_recovery import (
    RecoveryAction,
    RecoveryError,
    RecoveryResult,
    ResourceState,
    DisasterRecovery,
    set_trace_id,
    get_trace_id,
)


class TestEnums:
    def test_action_values(self):
        assert RecoveryAction.BACKUP.value == "backup"
        assert RecoveryAction.RESTORE.value == "restore"
        assert RecoveryAction.REPAIR.value == "repair"
        assert RecoveryAction.RELOAD.value == "reload"
        assert RecoveryAction.SKIP.value == "skip"

    def test_action_count(self):
        assert len(RecoveryAction) == 5


class TestRecoveryError:
    def test_default_error_code(self):
        err = RecoveryError("msg")
        assert err.error_code == "RECOVERY_FAILED"
        assert err.resource == ""

    def test_custom_params(self):
        err = RecoveryError("msg", "CUSTOM_CODE", "res1")
        assert err.error_code == "CUSTOM_CODE"
        assert err.resource == "res1"
        assert str(err) == "msg"


class TestRecoveryResult:
    def test_defaults(self):
        r = RecoveryResult(success=True, action=RecoveryAction.BACKUP, resource="r")
        assert r.message == ""
        assert r.duration_ms == 0
        assert r.timestamp > 0

    def test_custom(self):
        r = RecoveryResult(
            success=True, action=RecoveryAction.RESTORE, resource="r",
            message="ok", duration_ms=100,
        )
        assert r.message == "ok"
        assert r.duration_ms == 100


class TestResourceState:
    def test_defaults(self):
        state = ResourceState(
            name="r", path=Path("/tmp/r"), backup_path=Path("/tmp/bak"),
        )
        assert state.last_backup == 0.0
        assert state.last_recovery == 0.0
        assert state.is_corrupted is False
        assert state.recovery_count == 0


class TestTraceId:
    def test_set_and_get(self):
        set_trace_id("test123")
        assert get_trace_id() == "test123"

    def test_set_empty(self):
        set_trace_id("")
        assert get_trace_id() == ""

    def test_set_none(self):
        set_trace_id(None)
        assert get_trace_id() == ""


class TestInit:
    def test_creates_backup_root(self, tmp_path):
        backup_root = tmp_path / "backups"
        dr = DisasterRecovery(backup_root)
        assert backup_root.exists()

    def test_default_config(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        assert dr.max_backups == 5
        assert dr.auto_repair is True
        assert dr._resources == {}

    def test_custom_config(self, tmp_path):
        dr = DisasterRecovery(tmp_path, max_backups=10, auto_repair=False)
        assert dr.max_backups == 10
        assert dr.auto_repair is False


class TestRegister:
    def test_register_single(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        dr.register("res1", tmp_path / "res1.txt")
        assert "res1" in dr._resources
        assert dr._resources["res1"].name == "res1"

    def test_register_with_callback(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        cb = lambda path: None
        dr.register("res1", tmp_path / "res1.txt", reload_callback=cb)
        assert "res1" in dr._reload_callbacks

    def test_register_multiple(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        for i in range(5):
            dr.register(f"res{i}", tmp_path / f"res{i}.txt")
        assert len(dr._resources) == 5


class TestBackup:
    def test_backup_success(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        result = dr.backup("res")
        assert result.success is True
        assert result.action == RecoveryAction.BACKUP
        assert result.duration_ms >= 0
        assert dr._resources["res"].last_backup > 0

    def test_backup_unregistered(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        result = dr.backup("not_exist")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP

    def test_backup_missing_file(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        dr.register("res", tmp_path / "missing.txt")
        result = dr.backup("res")
        assert result.success is False
        assert "不存在" in result.message

    def test_backup_directory(self, tmp_path):
        res_dir = tmp_path / "resdir"
        res_dir.mkdir()
        (res_dir / "file.txt").write_text("content")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_dir)
        result = dr.backup("res")
        assert result.success is True

    def test_backup_all(self, tmp_path):
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_text("a")
        f2.write_text("b")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("r1", f1)
        dr.register("r2", f2)
        results = dr.backup_all()
        assert len(results) == 2
        assert all(r.success for r in results.values())

    def test_backup_creates_timestamped_file(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        dr.backup("res")
        backups = list(dr._resources["res"].backup_path.glob("*.bak"))
        assert len(backups) == 1


class TestRestore:
    def test_restore_success(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("original")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        dr.backup("res")
        # 修改原文件
        res_file.write_text("corrupted")
        # 恢复
        result = dr.restore("res")
        assert result.success is True
        assert res_file.read_text() == "original"
        assert dr._resources["res"].recovery_count == 1

    def test_restore_unregistered(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        result = dr.restore("not_exist")
        assert result.success is False

    def test_restore_no_backup(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        result = dr.restore("res")
        assert result.success is False
        assert "无可用备份" in result.message

    def test_restore_increments_recovery_count(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("v1")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        dr.backup("res")
        res_file.write_text("v2")
        dr.restore("res")
        res_file.write_text("v3")
        dr.restore("res")
        assert dr._resources["res"].recovery_count == 2

    def test_restore_clears_corrupted_flag(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("original")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        dr.backup("res")
        dr._resources["res"].is_corrupted = True
        res_file.write_text("corrupted")
        dr.restore("res")
        assert dr._resources["res"].is_corrupted is False


class TestCheckIntegrity:
    def test_intact(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path)
        dr.register("res", res_file)
        assert dr.check_integrity("res") is True
        assert dr._resources["res"].is_corrupted is False

    def test_missing_file(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        dr.register("res", tmp_path / "missing.txt")
        assert dr.check_integrity("res") is False
        assert dr._resources["res"].is_corrupted is True

    def test_unregistered(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        assert dr.check_integrity("not_exist") is False

    def test_validator_passes(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path)
        dr.register("res", res_file)
        assert dr.check_integrity("res", lambda p: True) is True

    def test_validator_fails(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path)
        dr.register("res", res_file)
        assert dr.check_integrity("res", lambda p: False) is False
        assert dr._resources["res"].is_corrupted is True

    def test_validator_exception(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path)
        dr.register("res", res_file)
        def bad_validator(p):
            raise RuntimeError("bad")
        assert dr.check_integrity("res", bad_validator) is False
        assert dr._resources["res"].is_corrupted is True


class TestRepairIfCorrupted:
    def test_no_repair_needed(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path)
        dr.register("res", res_file)
        result = dr.repair_if_corrupted("res")
        assert result.success is True
        assert result.action == RecoveryAction.SKIP

    def test_repair_needed(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("original")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        dr.backup("res")
        # 损坏文件
        res_file.write_text("corrupted")
        result = dr.repair_if_corrupted("res", lambda p: p.read_text() == "original")
        assert result.success is True
        assert result.action == RecoveryAction.RESTORE
        assert res_file.read_text() == "original"

    def test_repair_unregistered(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        result = dr.repair_if_corrupted("not_exist")
        # check_integrity 返回 False → 调用 restore → restore 返回 SKIP（未注册）
        assert result.success is False
        assert result.action == RecoveryAction.SKIP


class TestReloadConfig:
    def test_reload_success(self, tmp_path):
        res_file = tmp_path / "config.json"
        res_file.write_text("{}")
        dr = DisasterRecovery(tmp_path)
        called = []
        dr.register("config", res_file, reload_callback=lambda p: called.append(p))
        result = dr.reload_config("config")
        assert result.success is True
        assert result.action == RecoveryAction.RELOAD
        assert len(called) == 1

    def test_reload_no_callback(self, tmp_path):
        res_file = tmp_path / "config.json"
        res_file.write_text("{}")
        dr = DisasterRecovery(tmp_path)
        dr.register("config", res_file)
        result = dr.reload_config("config")
        assert result.success is False
        assert "未注册热重载回调" in result.message

    def test_reload_unregistered(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        result = dr.reload_config("not_exist")
        assert result.success is False

    def test_reload_callback_failure(self, tmp_path):
        res_file = tmp_path / "config.json"
        res_file.write_text("{}")
        dr = DisasterRecovery(tmp_path)
        def bad_cb(p):
            raise RuntimeError("reload failed")
        dr.register("config", res_file, reload_callback=bad_cb)
        result = dr.reload_config("config")
        assert result.success is False
        assert "热重载失败" in result.message


class TestRecoverOnStartup:
    def test_all_intact(self, tmp_path):
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_text("a")
        f2.write_text("b")
        dr = DisasterRecovery(tmp_path)
        dr.register("r1", f1)
        dr.register("r2", f2)
        results = dr.recover_on_startup()
        assert len(results) == 2
        assert all(r.action == RecoveryAction.SKIP for r in results.values())

    def test_with_corrupted(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("original")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        dr.backup("res")
        res_file.write_text("corrupted")
        results = dr.recover_on_startup(
            validators={"res": lambda p: p.read_text() == "original"}
        )
        assert results["res"].success is True
        assert results["res"].action == RecoveryAction.RESTORE

    def test_empty(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        results = dr.recover_on_startup()
        assert results == {}

    def test_with_default_validators(self, tmp_path):
        f1 = tmp_path / "f1.txt"
        f1.write_text("a")
        dr = DisasterRecovery(tmp_path)
        dr.register("r1", f1)
        results = dr.recover_on_startup()
        assert results["r1"].action == RecoveryAction.SKIP


class TestReset:
    def test_reset_clears_all(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path)
        dr.register("res", res_file)
        dr.reset()
        assert dr._resources == {}
        assert dr._reload_callbacks == {}


class TestGetState:
    def test_get_state_existing(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path)
        dr.register("res", res_file)
        state = dr.get_state("res")
        assert state is not None
        assert state.name == "res"

    def test_get_state_nonexistent(self, tmp_path):
        dr = DisasterRecovery(tmp_path)
        assert dr.get_state("not_exist") is None


class TestCleanupOldBackups:
    def test_max_backups_enforced(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("data")
        dr = DisasterRecovery(tmp_path / "backups", max_backups=3)
        dr.register("res", res_file)
        # 创建 5 个备份
        for i in range(5):
            res_file.write_text(f"v{i}")
            dr.backup("res")
            time.sleep(0.01)  # 确保时间戳不同
        backups = list(dr._resources["res"].backup_path.glob("*.bak"))
        assert len(backups) <= 3


class TestIntegration:
    def test_full_backup_restore_cycle(self, tmp_path):
        res_file = tmp_path / "res.txt"
        res_file.write_text("v1")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("res", res_file)
        # 备份
        assert dr.backup("res").success is True
        # 修改
        res_file.write_text("v2")
        # 恢复
        assert dr.restore("res").success is True
        assert res_file.read_text() == "v1"

    def test_multiple_independent_resources(self, tmp_path):
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_text("a")
        f2.write_text("b")
        dr = DisasterRecovery(tmp_path / "backups")
        dr.register("r1", f1)
        dr.register("r2", f2)
        dr.backup_all()
        f1.write_text("corrupted")
        dr.restore("r1")
        assert f1.read_text() == "a"
        assert f2.read_text() == "b"

    def test_backup_restore_reload_flow(self, tmp_path):
        """完整灾备流程：注册 → 备份 → 损坏 → 检测 → 恢复 → 热重载"""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"key": "value"}')

        dr = DisasterRecovery(tmp_path / "backups")
        reloaded = []
        dr.register("config", config_file, reload_callback=lambda p: reloaded.append(p.read_text()))

        # 备份
        assert dr.backup("config").success is True

        # 热重载
        result = dr.reload_config("config")
        assert result.success is True
        assert reloaded == ['{"key": "value"}']

        # 损坏后恢复
        config_file.write_text("corrupted")
        result = dr.repair_if_corrupted(
            "config",
            validator=lambda p: p.read_text().startswith("{"),
        )
        assert result.success is True
        assert config_file.read_text() == '{"key": "value"}'
