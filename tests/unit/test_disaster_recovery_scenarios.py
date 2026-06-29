"""
容灾恢复场景测试

覆盖 agent/disaster_recovery.py 的所有故障恢复场景，确保系统数据安全。
"""

import pytest
import os
import json
import time
import tempfile
import sqlite3

from agent.disaster_recovery import (
    DisasterRecovery,
    BackupConfig,
    BackupType,
    RecoveryStatus,
    ConfigHotReloader,
    get_disaster_recovery,
    get_config_reloader,
    register_backup_provider,
    trigger_backup,
    restore_from_backup,
)


# ============================================================================
# 容灾恢复基础测试
# ============================================================================


class TestDisasterRecoveryBasic:
    """容灾恢复基础测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default(self):
        """测试默认初始化"""
        dr = DisasterRecovery()
        assert dr is not None
        assert dr._config.enabled is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_custom_config(self):
        """测试自定义配置初始化"""
        config = BackupConfig(
            enabled=False,
            backup_interval_minutes=10,
            max_backups=5,
            auto_recover=False
        )
        dr = DisasterRecovery(config)
        assert dr._config.enabled is False
        assert dr._config.max_backups == 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_disaster_recovery_singleton(self):
        """测试全局容灾恢复单例"""
        d1 = get_disaster_recovery()
        d2 = get_disaster_recovery()
        assert d1 is d2


# ============================================================================
# 备份功能测试
# ============================================================================


class TestBackupFunctionality:
    """备份功能测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_backup_provider(self):
        """测试注册备份提供者"""
        dr = DisasterRecovery(BackupConfig(backup_dir="./tmp_backup_test"))
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test_data", backup_func, restore_func)
        assert "test_data" in dr._backup_providers

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trigger_backup_full(self, tmp_path):
        """测试触发全量备份"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"key": "value"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        assert backup_id != ""
        assert backup_id.startswith("backup_")
        
        # 验证备份文件存在
        backup_path = os.path.join(str(tmp_path), f"{backup_id}.json")
        meta_path = os.path.join(str(tmp_path), f"{backup_id}_meta.json")
        assert os.path.exists(backup_path)
        assert os.path.exists(meta_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trigger_backup_incremental(self, tmp_path):
        """测试触发增量备份"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"incremental": True}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("incr", backup_func, restore_func)
        
        backup_id = dr.trigger_backup(BackupType.INCREMENTAL)
        assert backup_id != ""
        
        # 验证备份文件存在
        backup_path = os.path.join(str(tmp_path), f"{backup_id}.json")
        assert os.path.exists(backup_path)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trigger_backup_disabled(self, tmp_path):
        """测试备份未启用时的行为"""
        config = BackupConfig(backup_dir=str(tmp_path), enabled=False)
        dr = DisasterRecovery(config)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        assert backup_id == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_backup_with_multiple_providers(self, tmp_path):
        """测试多提供者备份"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func1():
            return {"provider1": "data1"}
        
        def backup_func2():
            return {"provider2": "data2"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("provider1", backup_func1, restore_func)
        dr.register_backup_provider("provider2", backup_func2, restore_func)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        assert backup_id != ""
        
        # 验证备份包含两个提供者的数据
        backup_path = os.path.join(str(tmp_path), f"{backup_id}.json")
        with open(backup_path, 'r', encoding='utf-8') as f:
            content = json.load(f)
        assert "provider1" in content["data"]
        assert "provider2" in content["data"]


# ============================================================================
# 备份验证与列表测试
# ============================================================================


class TestBackupVerification:
    """备份验证与列表测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_verify_backup(self, tmp_path):
        """测试验证备份完整性"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"test": "data"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        
        # 验证有效备份
        assert dr._verify_backup(backup_id) is True
        
        # 验证无效备份
        assert dr._verify_backup("invalid_backup") is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_backup_list(self, tmp_path):
        """测试获取备份列表"""
        config = BackupConfig(backup_dir=str(tmp_path), max_backups=3)
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        # 创建多个备份
        for _ in range(5):
            dr.trigger_backup(BackupType.FULL)
            time.sleep(0.1)
        
        backups = dr.get_backup_list()
        
        # 应该只保留3个备份
        assert len(backups) <= 3
        assert all(b.checksum != "" for b in backups)


# ============================================================================
# 恢复功能测试
# ============================================================================


class TestRecoveryFunctionality:
    """恢复功能测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_restore_from_backup(self, tmp_path):
        """测试从备份恢复"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        restored_data = []
        
        def backup_func():
            return {"key": "value", "number": 42}
        
        def restore_func(data):
            restored_data.append(data)
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        # 创建备份
        backup_id = dr.trigger_backup(BackupType.FULL)
        
        # 从备份恢复
        result = dr.restore_from_backup(backup_id)
        
        assert result is True
        assert len(restored_data) == 1
        assert restored_data[0]["key"] == "value"
        assert restored_data[0]["number"] == 42

    @pytest.mark.unit
    @pytest.mark.p0
    def test_restore_from_invalid_backup(self, tmp_path):
        """测试从无效备份恢复"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        result = dr.restore_from_backup("invalid_backup_id")
        
        assert result is False
        
        # 检查恢复状态
        recovery_info = dr.get_recovery_status()
        assert recovery_info.status == RecoveryStatus.FAILED

    @pytest.mark.unit
    @pytest.mark.p0
    def test_auto_recover_on_startup(self, tmp_path):
        """测试启动时自动恢复"""
        config = BackupConfig(
            backup_dir=str(tmp_path),
            auto_recover=True
        )
        dr = DisasterRecovery(config)
        
        restored_data = []
        
        def backup_func():
            return {"recovered": True}
        
        def restore_func(data):
            restored_data.append(data)
        
        dr.register_backup_provider("auto_recover_test", backup_func, restore_func)
        
        # 创建备份
        dr.trigger_backup(BackupType.FULL)
        
        # 创建新的实例模拟重启
        dr2 = DisasterRecovery(config)
        dr2.register_backup_provider("auto_recover_test", backup_func, restore_func)
        
        # 自动恢复
        result = dr2.auto_recover_on_startup()
        
        assert result is True
        assert len(restored_data) == 1
        assert restored_data[0]["recovered"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_restore_from_backup_provider_error(self, tmp_path):
        """测试恢复时提供者函数出错"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"key": "value"}
        
        def failing_restore_func(data):
            raise ValueError("restore error")
        
        dr.register_backup_provider("test", backup_func, failing_restore_func)
        
        # 创建备份
        backup_id = dr.trigger_backup(BackupType.FULL)
        
        # 从备份恢复（应该处理异常并继续）
        result = dr.restore_from_backup(backup_id)
        
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_auto_recover_no_backups(self, tmp_path):
        """测试自动恢复时无备份可用"""
        config = BackupConfig(
            backup_dir=str(tmp_path),
            auto_recover=True
        )
        dr = DisasterRecovery(config)
        
        result = dr.auto_recover_on_startup()
        assert result is False


# ============================================================================
# 数据库修复测试
# ============================================================================


class TestDatabaseRepair:
    """数据库修复测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_repair_valid_database(self, tmp_path):
        """测试修复有效数据库"""
        db_path = os.path.join(str(tmp_path), "valid.db")
        
        # 创建有效的数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        cursor.execute("INSERT INTO test VALUES (1, 'test')")
        conn.commit()
        conn.close()
        
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        result = dr.repair_database(db_path)
        assert result is True
        
        # 验证数据仍然存在
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM test")
        rows = cursor.fetchall()
        assert len(rows) == 1
        conn.close()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_repair_corrupted_database(self, tmp_path):
        """测试修复损坏的数据库"""
        db_path = os.path.join(str(tmp_path), "corrupted.db")
        
        # 创建损坏的数据库文件
        with open(db_path, 'wb') as f:
            f.write(b"invalid sqlite data")
        
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        result = dr.repair_database(db_path)
        assert result is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_repair_nonexistent_database(self, tmp_path):
        """测试修复不存在的数据库"""
        db_path = os.path.join(str(tmp_path), "nonexistent.db")
        
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        result = dr.repair_database(db_path)
        assert result is True  # 不存在的数据库视为修复成功


# ============================================================================
# 配置热加载测试
# ============================================================================


class TestConfigHotReloader:
    """配置热加载测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_reloader(self):
        """测试初始化热加载器"""
        reloader = ConfigHotReloader()
        assert reloader is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_config_reloader_singleton(self):
        """测试全局配置热加载器单例"""
        r1 = get_config_reloader()
        r2 = get_config_reloader()
        assert r1 is r2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_watch_config(self, tmp_path):
        """测试监听配置文件"""
        config_path = os.path.join(str(tmp_path), "config.json")
        
        # 创建初始配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value1"}, f)
        
        reloader = ConfigHotReloader()
        
        changes_detected = []
        
        def callback(path):
            with open(path, 'r', encoding='utf-8') as f:
                changes_detected.append(json.load(f))
        
        reloader.watch_config(config_path, callback)
        reloader.start()
        
        # 修改配置文件
        time.sleep(0.5)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value2"}, f)
        
        # 等待检测到变化
        time.sleep(3)
        
        # 验证回调被调用
        assert len(changes_detected) >= 1
        assert changes_detected[-1]["setting"] == "value2"
        
        reloader.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_watch_config_callback_error(self, tmp_path):
        """测试回调函数出错时的处理"""
        config_path = os.path.join(str(tmp_path), "config.json")
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value1"}, f)
        
        reloader = ConfigHotReloader()
        
        def failing_callback(path):
            raise ValueError("callback error")
        
        reloader.watch_config(config_path, failing_callback)
        reloader.start()
        
        # 修改配置文件（应该不会导致崩溃）
        time.sleep(0.5)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value2"}, f)
        
        time.sleep(2)
        
        # 热加载器应该仍在运行
        assert reloader._watch_thread.is_alive()
        
        reloader.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_watch_multiple_configs(self, tmp_path):
        """测试监听多个配置文件"""
        config_path1 = os.path.join(str(tmp_path), "config1.json")
        config_path2 = os.path.join(str(tmp_path), "config2.json")
        
        with open(config_path1, 'w', encoding='utf-8') as f:
            json.dump({"name": "config1"}, f)
        with open(config_path2, 'w', encoding='utf-8') as f:
            json.dump({"name": "config2"}, f)
        
        reloader = ConfigHotReloader()
        
        changes_detected = []
        
        def callback(path):
            with open(path, 'r', encoding='utf-8') as f:
                changes_detected.append(json.load(f))
        
        reloader.watch_config(config_path1, callback)
        reloader.watch_config(config_path2, callback)
        reloader.start()
        
        # 修改第一个配置
        time.sleep(0.5)
        with open(config_path1, 'w', encoding='utf-8') as f:
            json.dump({"name": "config1_changed"}, f)
        
        time.sleep(2)
        
        # 修改第二个配置
        with open(config_path2, 'w', encoding='utf-8') as f:
            json.dump({"name": "config2_changed"}, f)
        
        time.sleep(2)
        
        # 验证两个配置文件的变化都被检测到
        assert len(changes_detected) >= 2
        assert any(c.get("name") == "config1_changed" for c in changes_detected)
        assert any(c.get("name") == "config2_changed" for c in changes_detected)
        
        reloader.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_watch_nonexistent_file(self, tmp_path):
        """测试监听不存在的文件"""
        config_path = os.path.join(str(tmp_path), "nonexistent.json")
        
        reloader = ConfigHotReloader()
        
        changes_detected = []
        
        def callback(path):
            changes_detected.append(path)
        
        reloader.watch_config(config_path, callback)
        reloader.start()
        
        # 创建文件
        time.sleep(0.5)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"created": True}, f)
        
        time.sleep(2)
        
        # 验证文件创建后被检测到
        assert len(changes_detected) >= 1
        
        reloader.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reloader_stop_not_running(self):
        """测试停止未运行的热加载器"""
        reloader = ConfigHotReloader()
        
        # 应该不会抛出异常
        reloader.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reloader_start_already_running(self, tmp_path):
        """测试启动已运行的热加载器"""
        config_path = os.path.join(str(tmp_path), "config.json")
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value"}, f)
        
        reloader = ConfigHotReloader()
        reloader.watch_config(config_path, lambda p: None)
        reloader.start()
        
        thread_id1 = reloader._watch_thread.ident
        
        # 再次启动应该不创建新线程
        reloader.start()
        
        thread_id2 = reloader._watch_thread.ident
        
        # 应该是同一个线程
        assert thread_id1 == thread_id2
        
        reloader.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_watch_loop_exception(self, tmp_path):
        """测试监听循环异常处理"""
        config_path = os.path.join(str(tmp_path), "config.json")
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value"}, f)
        
        reloader = ConfigHotReloader()
        
        # 模拟线程安全问题导致的异常
        def error_callback(path):
            raise RuntimeError("simulated watch loop error")
        
        reloader.watch_config(config_path, error_callback)
        reloader.start()
        
        time.sleep(2)
        
        # 热加载器应该仍在运行
        assert reloader._watch_thread.is_alive()
        
        reloader.stop()


# ============================================================================
# 备份调度器测试
# ============================================================================


class TestBackupScheduler:
    """备份调度器测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_stop_scheduler(self, tmp_path):
        """测试启动和停止备份调度器"""
        config = BackupConfig(
            backup_dir=str(tmp_path),
            backup_interval_minutes=0.01  # 0.6秒
        )
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"time": time.time()}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("scheduler_test", backup_func, restore_func)
        
        # 启动调度器
        dr.start_backup_scheduler()
        
        # 等待备份
        time.sleep(1)
        
        # 停止调度器
        dr.stop_backup_scheduler()
        
        # 应该有备份
        backups = dr.get_backup_list()
        assert len(backups) >= 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_scheduler_disabled(self, tmp_path):
        """测试备份未启用时不启动调度器"""
        config = BackupConfig(
            backup_dir=str(tmp_path),
            enabled=False
        )
        dr = DisasterRecovery(config)
        
        dr.start_backup_scheduler()
        
        # 调度器线程应该不存在或未运行
        assert dr._backup_thread is None or not dr._backup_thread.is_alive()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_start_scheduler_already_running(self, tmp_path):
        """测试调度器已运行时不重复启动"""
        config = BackupConfig(
            backup_dir=str(tmp_path),
            backup_interval_minutes=0.01
        )
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        # 第一次启动
        dr.start_backup_scheduler()
        thread1 = dr._backup_thread
        
        # 第二次启动（应该不创建新线程）
        dr.start_backup_scheduler()
        
        # 应该是同一个线程
        assert dr._backup_thread is thread1
        
        dr.stop_backup_scheduler()


# ============================================================================
# 状态与统计测试
# ============================================================================


class TestStatusAndStats:
    """状态与统计测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_recovery_status(self, tmp_path):
        """测试获取恢复状态"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        status = dr.get_recovery_status()
        assert status.status == RecoveryStatus.NONE
        assert status.backup_id is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status(self, tmp_path):
        """测试获取容灾恢复状态"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        # 创建备份
        dr.trigger_backup(BackupType.FULL)
        
        status = dr.get_status()
        
        assert "config" in status
        assert "backup_providers" in status
        assert "backup_count" in status
        assert "latest_backup" in status
        assert "recovery_status" in status
        assert "scheduler_running" in status


# ============================================================================
# 异常场景测试
# ============================================================================


class TestExceptionScenarios:
    """异常场景测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_backup_provider_failure(self, tmp_path):
        """测试备份提供者失败时的处理"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def failing_backup_func():
            raise ValueError("backup failed")
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("failing", failing_backup_func, restore_func)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        assert backup_id != ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_restore_provider_failure(self, tmp_path):
        """测试恢复提供者失败时的处理"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"data": "test"}
        
        def failing_restore_func(data):
            raise ValueError("restore failed")
        
        dr.register_backup_provider("failing_restore", backup_func, failing_restore_func)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        result = dr.restore_from_backup(backup_id)
        
        assert result is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_backup_checksum_mismatch(self, tmp_path):
        """测试备份校验和不匹配"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        
        # 手动修改备份文件内容导致校验和不匹配
        backup_path = os.path.join(str(tmp_path), f"{backup_id}.json")
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write("modified content")
        
        assert dr._verify_backup(backup_id) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_backup_invalid_json(self, tmp_path):
        """测试备份文件包含无效JSON"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        backup_id = dr.trigger_backup(BackupType.FULL)
        
        # 手动替换备份文件为无效JSON
        backup_path = os.path.join(str(tmp_path), f"{backup_id}.json")
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write("not valid json")
        
        assert dr._verify_backup(backup_id) is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_auto_recover_no_backups(self, tmp_path):
        """测试自动恢复时无可用备份"""
        config = BackupConfig(
            backup_dir=str(tmp_path),
            auto_recover=True
        )
        dr = DisasterRecovery(config)
        
        result = dr.auto_recover_on_startup()
        assert result is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cleanup_old_backups_exception(self, tmp_path):
        """测试清理旧备份时的异常处理"""
        config = BackupConfig(
            backup_dir=str(tmp_path),
            max_backups=1
        )
        dr = DisasterRecovery(config)
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("test", backup_func, restore_func)
        
        # 创建多个备份
        for _ in range(3):
            dr.trigger_backup(BackupType.FULL)
            time.sleep(0.1)
        
        # 应该只保留1个备份
        backups = dr.get_backup_list()
        assert len(backups) <= 1


# ============================================================================
# 数据库修复深度测试
# ============================================================================


class TestDatabaseRepairDeep:
    """数据库修复深度测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_repair_database_with_errors(self, tmp_path):
        """测试修复有错误的数据库"""
        db_path = os.path.join(str(tmp_path), "corrupted_with_errors.db")
        
        # 创建数据库并写入数据
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
        cursor.execute("INSERT INTO test VALUES (1, 'test1')")
        cursor.execute("INSERT INTO test VALUES (2, 'test2')")
        conn.commit()
        conn.close()
        
        # 模拟数据库损坏（写入部分数据）
        with open(db_path, 'r+b') as f:
            content = f.read()
            f.seek(0)
            f.write(content[:-10])
            f.truncate()
        
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        result = dr.repair_database(db_path)
        assert result is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_repair_database_exception(self, tmp_path):
        """测试修复数据库时的异常处理"""
        db_path = os.path.join(str(tmp_path), "exception.db")
        
        # 创建无效的数据库文件
        with open(db_path, 'wb') as f:
            f.write(b"\x00\x00\x00\x00")
        
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = DisasterRecovery(config)
        
        result = dr.repair_database(db_path)
        assert result is False


# ============================================================================
# 配置热加载异常测试
# ============================================================================


class TestConfigHotReloaderExceptions:
    """配置热加载异常测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_watch_loop_exception(self, tmp_path):
        """测试配置监听循环异常处理"""
        config_path = os.path.join(str(tmp_path), "config.json")
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value1"}, f)
        
        reloader = ConfigHotReloader()
        
        reloader.watch_config(config_path, lambda path: None)
        reloader.start()
        
        # 删除文件模拟异常场景
        os.remove(config_path)
        
        time.sleep(0.5)
        
        # 创建新文件
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value2"}, f)
        
        time.sleep(2)
        
        assert reloader._watch_thread.is_alive()
        
        reloader.stop()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reloader_start_already_running(self, tmp_path):
        """测试热加载器已运行时不重复启动"""
        config_path = os.path.join(str(tmp_path), "config.json")
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump({"setting": "value"}, f)
        
        reloader = ConfigHotReloader()
        reloader.watch_config(config_path, lambda path: None)
        
        reloader.start()
        thread1 = reloader._watch_thread
        
        reloader.start()
        
        assert reloader._watch_thread is thread1
        
        reloader.stop()


# ============================================================================
# 便捷函数测试
# ============================================================================


class TestConvenienceFunctions:
    """便捷函数测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_register_backup_provider_convenience(self):
        """测试便捷函数 register_backup_provider"""
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        register_backup_provider("convenience_test", backup_func, restore_func)
        
        dr = get_disaster_recovery()
        assert "convenience_test" in dr._backup_providers

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trigger_backup_convenience(self, tmp_path):
        """测试便捷函数 trigger_backup"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = get_disaster_recovery()
        dr._config = config
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("convenience_backup", backup_func, restore_func)
        
        backup_id = trigger_backup(BackupType.FULL)
        assert backup_id != ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_restore_from_backup_convenience(self, tmp_path):
        """测试便捷函数 restore_from_backup"""
        config = BackupConfig(backup_dir=str(tmp_path))
        dr = get_disaster_recovery()
        dr._config = config
        
        def backup_func():
            return {"data": "test"}
        
        def restore_func(data):
            pass
        
        dr.register_backup_provider("convenience_restore", backup_func, restore_func)
        
        backup_id = trigger_backup(BackupType.FULL)
        result = restore_from_backup(backup_id)
        assert result is True