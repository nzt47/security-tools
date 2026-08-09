"""disaster_recovery 单例迁移单元测试

覆盖：
- 单例行为：两个单例（disaster_recovery / config_hot_reloader）唯一性、注册、reset/GC/幂等
- 备份：注册提供者 + trigger_backup 生成备份、restore_from_backup 恢复、便捷函数走单例
- cleanup 钩子：重置时停止备份调度器线程与热重载监听线程（重点）
- 并发首次初始化、fallback 行为
"""
import gc
import threading
import weakref

import pytest

import agent.disaster_recovery as module
from agent.disaster_recovery import (
    BackupConfig,
    BackupType,
    ConfigHotReloader,
    DisasterRecovery,
    RecoveryAction,
    get_config_reloader,
    get_disaster_recovery,
)
from agent.utils.singleton_manager import is_initialized


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    """每个用例前后重置两个单例，保证测试隔离"""
    module.reset_config_reloader()
    module.reset_disaster_recovery()
    yield
    module.reset_config_reloader()
    module.reset_disaster_recovery()


class TestDisasterRecoverySingleton:
    """单例行为测试"""

    def test_get_disaster_recovery_returns_same_instance(self):
        a = get_disaster_recovery()
        b = get_disaster_recovery()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_disaster_recovery()
        assert is_initialized("disaster_recovery")

    def test_reset_returns_new_instance(self):
        first = get_disaster_recovery()
        module.reset_disaster_recovery()
        second = get_disaster_recovery()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_disaster_recovery())
        module.reset_disaster_recovery()
        gc.collect()
        assert ref() is None

    def test_reset_idempotent_when_not_initialized(self):
        module.reset_disaster_recovery()
        module.reset_disaster_recovery()


class TestConfigHotReloaderSingleton:
    """ConfigHotReloader 单例行为测试"""

    def test_get_config_reloader_returns_same_instance(self):
        a = get_config_reloader()
        b = get_config_reloader()
        assert a is b

    def test_registers_in_singleton_manager(self):
        get_config_reloader()
        assert is_initialized("config_hot_reloader")

    def test_two_singletons_distinct(self):
        """两个单例独立注册、互不共享"""
        dr = get_disaster_recovery()
        reloader = get_config_reloader()
        assert dr is not reloader
        assert isinstance(reloader, ConfigHotReloader)

    def test_reset_returns_new_instance(self):
        first = get_config_reloader()
        module.reset_config_reloader()
        second = get_config_reloader()
        assert first is not second

    def test_reset_releases_instance_for_gc(self):
        ref = weakref.ref(get_config_reloader())
        module.reset_config_reloader()
        gc.collect()
        assert ref() is None


class TestDisasterRecoveryBackup:
    """备份与恢复逻辑测试（重点）"""

    def _make_manager(self, tmp_path, **config_overrides):
        config = BackupConfig(backup_dir=str(tmp_path), **config_overrides)
        return DisasterRecovery(config)

    def test_register_provider_then_trigger_backup(self, tmp_path):
        """注册提供者后 trigger_backup 返回备份 ID（backup_ 前缀）"""
        dr = self._make_manager(tmp_path)
        dr.register_backup_provider(
            "memory_db",
            lambda: {"data": "snapshot-1"},
            lambda data: None,
        )
        backup_id = dr.trigger_backup()
        assert backup_id.startswith("backup_")

    def test_trigger_backup_writes_backup_file(self, tmp_path):
        """trigger_backup 后备份文件落盘"""
        dr = self._make_manager(tmp_path)
        dr.register_backup_provider(
            "memory_db",
            lambda: {"data": "snapshot-1"},
            lambda data: None,
        )
        backup_id = dr.trigger_backup()
        assert (tmp_path / f"{backup_id}.json").exists()

    def test_restore_from_backup_invokes_restore_func(self, tmp_path):
        """restore_from_backup 调用提供者的 restore_func 并返回 True"""
        restored = []

        def restore_func(data):
            restored.append(data)

        dr = self._make_manager(tmp_path)
        dr.register_backup_provider("memory_db", lambda: {"data": "snapshot-1"}, restore_func)
        backup_id = dr.trigger_backup()
        assert dr.restore_from_backup(backup_id) is True
        assert restored == [{"data": "snapshot-1"}]

    def test_restore_missing_backup_returns_false(self, tmp_path):
        """恢复不存在的备份返回 False"""
        dr = self._make_manager(tmp_path)
        assert dr.restore_from_backup("backup_not_exists") is False

    def test_trigger_backup_when_disabled_returns_empty(self, tmp_path):
        """备份未启用时 trigger_backup 返回空字符串"""
        dr = self._make_manager(tmp_path, enabled=False)
        dr.register_backup_provider("memory_db", lambda: {"data": 1}, lambda data: None)
        assert dr.trigger_backup() == ""

    def test_backup_unregistered_resource_skips(self, tmp_path):
        """backup 未注册的资源返回 SKIP"""
        dr = self._make_manager(tmp_path)
        result = dr.backup("unknown_resource")
        assert result.success is False
        assert result.action == RecoveryAction.SKIP

    def test_module_level_convenience_functions(self, tmp_path, monkeypatch):
        """模块级便捷函数走全局单例"""
        dr = get_disaster_recovery()
        # 将单例备份目录指向临时目录，避免污染工作区
        monkeypatch.setattr(dr._config, "backup_dir", str(tmp_path))
        restored = []

        module.register_backup_provider(
            "config",
            lambda: {"version": 1},
            lambda data: restored.append(data),
        )
        backup_id = module.trigger_backup(BackupType.FULL)
        assert backup_id.startswith("backup_")
        assert module.restore_from_backup(backup_id) is True
        assert restored == [{"version": 1}]


class TestDisasterRecoveryCleanupHook:
    """cleanup 钩子测试（重点）"""

    def test_reset_stops_backup_scheduler(self, tmp_path):
        """cleanup：重置时停止备份调度器线程"""
        dr = get_disaster_recovery()
        dr._config.backup_dir = str(tmp_path)
        dr.start_backup_scheduler()
        assert dr._backup_thread is not None and dr._backup_thread.is_alive()
        module.reset_disaster_recovery()
        assert not dr._backup_thread.is_alive()

    def test_reset_without_start_is_safe(self):
        """未启动备份调度器时 reset 安全（cleanup 幂等）"""
        get_disaster_recovery()
        module.reset_disaster_recovery()  # 不应抛异常
        module.reset_disaster_recovery()

    def test_reset_stops_config_reloader(self):
        """cleanup：重置时停止 ConfigHotReloader 监听线程"""
        reloader = get_config_reloader()
        reloader.start()
        assert reloader._watch_thread is not None and reloader._watch_thread.is_alive()
        module.reset_config_reloader()
        assert not reloader._watch_thread.is_alive()

    def test_reset_config_reloader_without_start_is_safe(self):
        """未启动监听时 reset 安全（cleanup 幂等）"""
        get_config_reloader()
        module.reset_config_reloader()  # 不应抛异常
        module.reset_config_reloader()

    def test_reset_then_get_is_fresh(self):
        """重置后新实例可正常启动/停止（无残留状态）"""
        dr = get_disaster_recovery()
        dr.start_backup_scheduler()
        module.reset_disaster_recovery()
        fresh = get_disaster_recovery()
        assert fresh is not dr
        fresh.start_backup_scheduler()
        assert fresh._backup_thread.is_alive()
        fresh.stop_backup_scheduler()


class TestDisasterRecoveryConcurrency:
    """并发场景测试"""

    def test_concurrent_first_get_initializes_once(self):
        """多线程并发首次 get 只构造一个实例（双检锁）"""
        orig_cls = module.DisasterRecovery
        created = []

        class CountingDR(orig_cls):
            def __init__(self, *args, **kwargs):
                created.append(1)
                super().__init__(*args, **kwargs)

        module.DisasterRecovery = CountingDR
        try:
            results = []
            errors = []
            barrier = threading.Barrier(8)

            def worker():
                barrier.wait()
                try:
                    results.append(get_disaster_recovery())
                except Exception as e:  # pragma: no cover
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert not errors
            assert len(created) == 1, f"应只构造一次，实际 {len(created)} 次"
            assert all(r is results[0] for r in results)
        finally:
            module.DisasterRecovery = orig_cls

    def test_concurrent_get_after_init_returns_same_instance(self):
        get_disaster_recovery()
        instances = []

        def worker():
            instances.append(get_disaster_recovery())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(i is instances[0] for i in instances)


class TestDisasterRecoveryFallback:
    """SingletonManager 不可用时的 fallback 行为"""

    def test_fallback_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_disaster_recovery()
        b = get_disaster_recovery()
        assert a is b

    def test_fallback_reloader_still_singleton(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        a = get_config_reloader()
        b = get_config_reloader()
        assert a is b

    def test_fallback_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_disaster_recovery()
        module.reset_disaster_recovery()
        second = get_disaster_recovery()
        assert first is not second

    def test_fallback_reloader_reset_works(self, monkeypatch):
        monkeypatch.setattr(module, "_SINGLETON_AVAILABLE", False)
        first = get_config_reloader()
        module.reset_config_reloader()
        second = get_config_reloader()
        assert first is not second
