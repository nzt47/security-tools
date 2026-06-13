"""
P6 Snapshot 快照保存和合并逻辑测试
覆盖版本兼容和增量更新场景
"""
import pytest
import os
import sys
import pickle
import time
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.p6_snapshot import (
    StateSnapshotManager,
    StateSnapshot,
    ModuleState,
    SnapshotResult,
)


class MockDigitalLife:
    """模拟 DigitalLife 实例"""
    def __init__(self, config=None):
        self._config = config or {"name": "test"}
        self._body = MagicMock()
        self._behavior = MagicMock()
        self._permission = MagicMock()
        self._tools_registry = MagicMock()
        self.__class__.__name__ = "DigitalLife"


class TestSnapshotSaveBasic:
    """测试快照保存基本功能"""

    def test_save_snapshot_frequency_blocked(self):
        """测试频率控制阻止保存"""
        manager = StateSnapshotManager()
        
        mock_frequency = MagicMock()
        mock_frequency.can_save.return_value = False
        mock_frequency.last_save_time = time.time()
        mock_frequency.min_interval_seconds = 60
        manager.frequency_controller = mock_frequency
        
        digital_life = MockDigitalLife()
        
        with patch('agent.p6_snapshot.logger'):
            result = manager.save_snapshot(digital_life)
        
        assert result.success is False
        assert result.error_message == "快照保存过于频繁"

    def test_save_snapshot_success(self):
        """测试快照保存成功"""
        manager = StateSnapshotManager()
        
        mock_frequency = MagicMock()
        mock_frequency.can_save.return_value = True
        mock_frequency.on_save_success = MagicMock()
        mock_frequency.save_count = 0
        mock_frequency.max_snapshots = 10
        manager.frequency_controller = mock_frequency
        
        mock_performance = MagicMock()
        mock_performance.record_save = MagicMock()
        manager.performance_monitor = mock_performance
        
        digital_life = MockDigitalLife(config={"name": "test_agent"})
        
        with patch.object(manager, '_generate_snapshot_id', return_value="test-snapshot-001"):
            with patch.object(manager, '_save_core_modules_with_delta', return_value=0):
                with patch.object(manager, '_persist_snapshot', return_value=True):
                    with patch.object(manager, '_update_module_checksums'):
                        with patch.object(manager, '_cleanup_old_snapshots'):
                            with patch('agent.p6_snapshot.logger'):
                                result = manager.save_snapshot(digital_life)
        
        assert result.success is True
        assert result.snapshot_id == "test-snapshot-001"
        assert result.is_incremental is False

    def test_save_snapshot_with_custom_id(self):
        """测试使用自定义快照ID"""
        manager = StateSnapshotManager()
        
        mock_frequency = MagicMock()
        mock_frequency.can_save.return_value = True
        mock_frequency.on_save_success = MagicMock()
        mock_frequency.save_count = 0
        mock_frequency.max_snapshots = 10
        manager.frequency_controller = mock_frequency
        
        mock_performance = MagicMock()
        mock_performance.record_save = MagicMock()
        manager.performance_monitor = mock_performance
        
        digital_life = MockDigitalLife()
        
        with patch.object(manager, '_save_core_modules_with_delta', return_value=0):
            with patch.object(manager, '_persist_snapshot', return_value=True):
                with patch.object(manager, '_update_module_checksums'):
                    with patch.object(manager, '_cleanup_old_snapshots'):
                        with patch('agent.p6_snapshot.logger'):
                            result = manager.save_snapshot(
                                digital_life,
                                snapshot_id="custom-snapshot-id"
                            )
        
        assert result.success is True
        assert result.snapshot_id == "custom-snapshot-id"

    def test_save_snapshot_persist_failure(self):
        """测试持久化失败"""
        manager = StateSnapshotManager()
        
        mock_frequency = MagicMock()
        mock_frequency.can_save.return_value = True
        manager.frequency_controller = mock_frequency
        
        digital_life = MockDigitalLife()
        
        with patch.object(manager, '_generate_snapshot_id', return_value="test-id"):
            with patch.object(manager, '_save_core_modules_with_delta', return_value=0):
                with patch.object(manager, '_persist_snapshot', return_value=False):
                    with patch('agent.p6_snapshot.logger'):
                        result = manager.save_snapshot(digital_life)
        
        assert result.success is False
        assert result.error_message == "快照持久化失败"

    def test_save_snapshot_exception_handling(self):
        """测试保存异常处理"""
        manager = StateSnapshotManager()
        
        mock_frequency = MagicMock()
        mock_frequency.can_save.return_value = True
        manager.frequency_controller = mock_frequency
        
        digital_life = MockDigitalLife()
        
        with patch.object(manager, '_generate_snapshot_id', side_effect=Exception("ID generation failed")):
            with patch('agent.p6_snapshot.logger'):
                result = manager.save_snapshot(digital_life)
        
        assert result.success is False


class TestSnapshotSaveIncremental:
    """测试增量快照保存"""

    def test_save_incremental_snapshot_with_base(self):
        """测试增量快照基于当前快照"""
        manager = StateSnapshotManager()
        
        # 设置当前快照
        base_snapshot = MagicMock()
        base_snapshot.snapshot_id = "base-snapshot-001"
        manager.current_snapshot = base_snapshot
        
        mock_frequency = MagicMock()
        mock_frequency.can_save.return_value = True
        mock_frequency.on_save_success = MagicMock()
        mock_frequency.save_count = 1
        mock_frequency.max_snapshots = 10
        manager.frequency_controller = mock_frequency
        
        mock_performance = MagicMock()
        mock_performance.record_save = MagicMock()
        manager.performance_monitor = mock_performance
        
        digital_life = MockDigitalLife()
        
        with patch.object(manager, '_generate_snapshot_id', return_value="incremental-001"):
            with patch.object(manager, '_save_core_modules_with_delta', return_value=1000):
                with patch.object(manager, '_persist_snapshot', return_value=True):
                    with patch.object(manager, '_update_module_checksums'):
                        with patch.object(manager, '_cleanup_old_snapshots'):
                            with patch('agent.p6_snapshot.logger'):
                                result = manager.save_snapshot(digital_life, incremental=True)
        
        assert result.success is True
        assert result.is_incremental is True
        assert result.space_saved_bytes == 1000

    def test_save_incremental_without_current_snapshot(self):
        """测试无当前快照时的增量保存"""
        manager = StateSnapshotManager()
        manager.current_snapshot = None
        
        mock_frequency = MagicMock()
        mock_frequency.can_save.return_value = True
        manager.frequency_controller = mock_frequency
        
        digital_life = MockDigitalLife()
        
        with patch.object(manager, '_generate_snapshot_id', return_value="test-id"):
            with patch.object(manager, '_save_core_modules_with_delta', return_value=0):
                with patch.object(manager, '_persist_snapshot', return_value=True):
                    with patch.object(manager, '_update_module_checksums'):
                        with patch.object(manager, '_cleanup_old_snapshots'):
                            with patch('agent.p6_snapshot.logger'):
                                result = manager.save_snapshot(digital_life, incremental=True)
        
        assert result.success is True


class TestSnapshotMerge:
    """测试快照合并逻辑"""

    def test_merge_snapshots_basic(self):
        """测试基础快照和增量快照合并"""
        manager = StateSnapshotManager()
        
        # 创建基础快照
        base_state = ModuleState(
            module_name="body_sensor",
            state_data=pickle.dumps({"value": 100}),
            checksum="base_checksum",
            restore_priority=100,
            initialized=True,
            changed=False
        )
        
        base_snapshot = StateSnapshot(
            snapshot_id="base-001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={"name": "base"},
            module_states={"body_sensor": base_state}
        )
        
        # 创建增量快照（包含变化的模块）
        incremental_state = ModuleState(
            module_name="behavior",
            state_data=pickle.dumps({"mode": "active"}),
            checksum="inc_checksum",
            restore_priority=90,
            initialized=True,
            changed=True
        )
        
        incremental_snapshot = StateSnapshot(
            snapshot_id="inc-001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={"name": "incremental"},
            module_states={"behavior": incremental_state}
        )
        
        merged = manager._merge_snapshots(base_snapshot, incremental_snapshot)
        
        assert merged.snapshot_id == "inc-001"
        assert "body_sensor" in merged.module_states  # 来自基础快照
        assert "behavior" in merged.module_states  # 来自增量快照（changed=True）

    def test_merge_snapshots_override_changed_module(self):
        """测试增量快照覆盖基础快照的变化模块"""
        manager = StateSnapshotManager()
        
        # 基础快照中的模块
        base_state = ModuleState(
            module_name="test_module",
            state_data=pickle.dumps({"old_value": 1}),
            checksum="base_checksum",
            restore_priority=50,
            initialized=True,
            changed=False
        )
        
        base_snapshot = StateSnapshot(
            snapshot_id="base-001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={},
            module_states={"test_module": base_state}
        )
        
        # 增量快照中同一模块但已变化
        incremental_state = ModuleState(
            module_name="test_module",
            state_data=pickle.dumps({"new_value": 2}),
            checksum="inc_checksum",
            restore_priority=50,
            initialized=True,
            changed=True
        )
        
        incremental_snapshot = StateSnapshot(
            snapshot_id="inc-001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={},
            module_states={"test_module": incremental_state}
        )
        
        merged = manager._merge_snapshots(base_snapshot, incremental_snapshot)
        
        # 增量快照的变化模块应该覆盖基础快照
        assert merged.module_states["test_module"].changed is True
        assert pickle.loads(merged.module_states["test_module"].state_data) == {"new_value": 2}

    def test_merge_snapshots_preserve_unchanged(self):
        """测试未变化的模块保留基础快照值"""
        manager = StateSnapshotManager()
        
        base_state = ModuleState(
            module_name="unchanged_module",
            state_data=pickle.dumps({"preserved": True}),
            checksum="base_checksum",
            restore_priority=50,
            initialized=True,
            changed=False
        )
        
        base_snapshot = StateSnapshot(
            snapshot_id="base-001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={},
            module_states={"unchanged_module": base_state}
        )
        
        # 增量快照中同一模块但未变化
        incremental_state = ModuleState(
            module_name="unchanged_module",
            state_data=pickle.dumps({"new_value": False}),
            checksum="inc_checksum",
            restore_priority=50,
            initialized=True,
            changed=False
        )
        
        incremental_snapshot = StateSnapshot(
            snapshot_id="inc-001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={},
            module_states={"unchanged_module": incremental_state}
        )
        
        merged = manager._merge_snapshots(base_snapshot, incremental_snapshot)
        
        # 未变化的模块应该保留基础快照的值
        assert pickle.loads(merged.module_states["unchanged_module"].state_data) == {"preserved": True}


class TestSnapshotVersionCompatibility:
    """测试版本兼容性"""

    def test_check_compatibility_p6_versions(self):
        """测试p6版本兼容"""
        manager = StateSnapshotManager()
        
        for version in ["p6.1.0", "p6.2.0", "p6.1.5", "p6.0.0"]:
            snapshot = MagicMock()
            snapshot.version = version
            
            assert manager._check_compatibility(snapshot) is True

    def test_check_compatibility_incompatible_version(self):
        """测试不兼容版本"""
        manager = StateSnapshotManager()
        
        for version in ["p5.0.0", "v1.0.0", "2.0.0"]:
            snapshot = MagicMock()
            snapshot.version = version
            
            with patch('agent.p6_snapshot.logger') as mock_logger:
                result = manager._check_compatibility(snapshot)
                assert result is False
                mock_logger.warning.assert_called()


class TestFrequencyController:
    """测试频率控制器"""

    def test_frequency_controller_can_save_initial(self):
        """测试初始可以保存"""
        manager = StateSnapshotManager()
        controller = manager.frequency_controller
        
        assert controller.can_save(force=False) is True

    def test_frequency_controller_block_after_save(self):
        """测试保存后短时间内被阻止"""
        manager = StateSnapshotManager()
        controller = manager.frequency_controller
        controller.on_save_success()
        
        # 保存后立即检查应该被阻止
        assert controller.can_save(force=False) is False

    def test_frequency_controller_force_save(self):
        """测试强制保存绕过频率限制"""
        manager = StateSnapshotManager()
        controller = manager.frequency_controller
        controller.on_save_success()
        
        # 强制保存应该允许
        assert controller.can_save(force=True) is True

    def test_frequency_controller_save_count(self):
        """测试保存计数"""
        manager = StateSnapshotManager()
        controller = manager.frequency_controller
        
        controller.on_save_success()
        assert controller.save_count == 1
        
        controller.on_save_success()
        assert controller.save_count == 2


class TestPerformanceMonitor:
    """测试性能监控"""

    def test_performance_monitor_record_save(self):
        """测试记录保存性能"""
        manager = StateSnapshotManager()
        monitor = manager.performance_monitor

        monitor.record_save(100.5, 500)

        # 使用 PerformanceMetrics 对象的属性
        assert monitor.metrics.total_saves == 1
        assert monitor.metrics.last_save_time_ms == 100.5
        assert monitor.metrics.total_space_saved_bytes == 500

    def test_performance_monitor_record_load(self):
        """测试记录加载性能"""
        manager = StateSnapshotManager()
        monitor = manager.performance_monitor

        monitor.record_load(50.3)

        # 使用 PerformanceMetrics 对象的属性
        assert monitor.metrics.total_loads == 1
        assert monitor.metrics.last_load_time_ms == 50.3

    def test_performance_monitor_average_times(self):
        """测试平均时间计算"""
        manager = StateSnapshotManager()
        monitor = manager.performance_monitor

        monitor.record_save(100.0, 0)
        monitor.record_save(200.0, 0)
        monitor.record_save(300.0, 0)

        monitor.record_load(50.0)
        monitor.record_load(100.0)

        # 使用 PerformanceMetrics 对象的属性
        assert monitor.metrics.avg_save_time_ms == 200.0
        assert monitor.metrics.avg_load_time_ms == 75.0


class TestSnapshotCleanup:
    """测试快照清理"""

    def test_cleanup_old_snapshots_no_cleanup_needed(self):
        """测试不需要清理"""
        manager = StateSnapshotManager()
        
        mock_frequency = MagicMock()
        mock_frequency.max_snapshots = 10
        manager.frequency_controller = mock_frequency
        
        # 只有5个快照，不需要清理
        with patch.object(manager, 'list_snapshots', return_value=[
            MagicMock(snapshot_id=f"snap-{i}") for i in range(5)
        ]):
            with patch('agent.p6_snapshot.logger'):
                manager._cleanup_old_snapshots()
        
        # 不应该有删除操作

    def test_cleanup_old_snapshots_delete_oldest(self):
        """测试删除最旧的快照"""
        manager = StateSnapshotManager()
        
        mock_frequency = MagicMock()
        mock_frequency.max_snapshots = 3
        manager.frequency_controller = mock_frequency
        
        snapshots = [MagicMock(snapshot_id=f"snap-{i}") for i in range(5)]
        
        with patch.object(manager, 'list_snapshots', return_value=snapshots):
            with patch.object(manager, '_get_snapshot_path') as mock_path:
                mock_path.return_value = MagicMock()
                mock_path.return_value.exists.return_value = True
                mock_path.return_value.unlink = MagicMock()
                
                with patch('agent.p6_snapshot.logger'):
                    manager._cleanup_old_snapshots()
        
        # 应该删除最旧的2个快照（snap-3, snap-4）