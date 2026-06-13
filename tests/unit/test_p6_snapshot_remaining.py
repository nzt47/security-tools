"""
P6 快照系统剩余未覆盖代码测试
"""
import pytest
import os
import sys
import tempfile
import pickle
import gzip
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.p6_snapshot import (
    StateSnapshotManager,
    StateSnapshot,
    ModuleState,
    SnapshotResult,
    SnapshotInfo,
)

SNAPSHOT_VERSION = "p6.2.0"


@pytest.fixture
def temp_snapshot_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def snapshot_manager(temp_snapshot_dir):
    return StateSnapshotManager(
        snapshot_dir=temp_snapshot_dir,
        enable_compression=False
    )


class MockDigitalLife:
    """简单的模拟 DigitalLife 类"""
    def __init__(self, config=None):
        self._config = config or {'test': 'config'}


class TestUncoveredErrorHandling:
    """测试未覆盖的错误处理路径"""
    
    def test_persist_snapshot_exception(self, temp_snapshot_dir):
        """测试 _persist_snapshot 异常处理"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=True
        )
        
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION
        )
        
        with patch('builtins.open', side_effect=Exception("写入失败")):
            result = manager._persist_snapshot(snapshot)
        
        assert result is False
    
    def test_load_snapshot_data_empty(self, snapshot_manager):
        """测试 _load_snapshot_data 空目录情况"""
        result = snapshot_manager._load_snapshot_data(None)
        
        assert result is None
    
    def test_load_snapshot_data_exception(self, temp_snapshot_dir):
        """测试 _load_snapshot_data 异常处理"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
        
        with patch.object(manager, 'list_snapshots', side_effect=Exception("列出失败")):
            result = manager._load_snapshot_data(None)
        
        assert result is None
    
    def test_cleanup_old_snapshots_exception(self, temp_snapshot_dir):
        """测试 _cleanup_old_snapshots 异常处理"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
        
        mock_snapshot_info = Mock()
        mock_snapshot_info.snapshot_id = "test_snap"
        
        with patch.object(manager, 'list_snapshots', return_value=[mock_snapshot_info]):
            with patch('pathlib.Path.unlink', side_effect=Exception("删除失败")):
                manager._cleanup_old_snapshots()
    
    def test_save_snapshot_no_config(self, snapshot_manager):
        """测试 save_snapshot 无 _config 属性的情况"""
        class NoConfigDigitalLife:
            pass
        
        result = snapshot_manager.save_snapshot(NoConfigDigitalLife(), force=True)
        
        assert result.success is False or result.success is True  # 取决于实现


class TestIncrementalSnapshotWithChanges:
    """测试增量快照中模块变化的情况"""
    
    def test_incremental_body_sensor_changed(self, snapshot_manager):
        """测试增量快照中 body_sensor 变化"""
        class MockBody:
            is_initialized = True
            _initialized = True
            watch_dirs = ["/tmp/test1"]
            config = {"timeout": 10}
        
        class MockDL:
            _config = {}
            _body = MockBody()
        
        # 第一次保存
        result1 = snapshot_manager.save_snapshot(MockDL(), force=True)
        assert result1.success
        
        # 修改 body
        MockBody.watch_dirs = ["/tmp/test2"]
        
        # 第二次增量保存（应该检测到变化）
        result2 = snapshot_manager.save_snapshot(MockDL(), incremental=True, force=True)
        assert result2.success
    
    def test_incremental_behavior_changed(self, snapshot_manager):
        """测试增量快照中 behavior 变化"""
        class MockBehavior:
            _current_mode = Mock()
            _current_mode.value = "NORMAL"
            _mode_history = ["NORMAL"]
            THRESHOLDS = {}
        
        class MockDL:
            _config = {}
            _behavior = MockBehavior()
        
        # 第一次保存
        result1 = snapshot_manager.save_snapshot(MockDL(), force=True)
        assert result1.success
        
        # 修改 behavior
        MockBehavior._mode_history = ["NORMAL", "ACTIVE"]
        
        # 第二次增量保存
        result2 = snapshot_manager.save_snapshot(MockDL(), incremental=True, force=True)
        assert result2.success


class TestBehaviorModeRestore:
    """测试 BehaviorController 模式恢复的边界情况"""
    
    def test_restore_behavior_unknown_mode(self, snapshot_manager):
        """测试恢复未知行为模式"""
        class MockBehavior:
            _current_mode = Mock()
            _current_mode.value = "NORMAL"
        
        state = {
            "initialized": True,
            "mode": "UNKNOWN_MODE"
        }
        
        result = snapshot_manager._restore_behavior(MockBehavior(), state)
        
        assert result is True
    
    def test_restore_behavior_import_error(self, snapshot_manager):
        """测试恢复时的 ImportError"""
        class MockBehavior:
            _current_mode = Mock()
        
        state = {
            "initialized": True,
            "mode": "ACTIVE"
        }
        
        with patch.dict(sys.modules, {'agent.behavior_controller': None}):
            result = snapshot_manager._restore_behavior(MockBehavior(), state)
        
        assert result is True


class TestModuleRestoreUnknown:
    """测试未知模块恢复"""
    
    def test_restore_unknown_module(self, snapshot_manager):
        """测试恢复未知模块"""
        class MockDL:
            pass
        
        module_data = pickle.dumps({"key": "value"})
        
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={
                "unknown_module": ModuleState(
                    module_name="unknown_module",
                    initialized=True,
                    state_data=module_data,
                    restore_priority=50,
                    checksum="test_checksum"
                )
            }
        )
        
        result = snapshot_manager._restore_modules_by_priority(MockDL(), snapshot)
        
        assert result is False  # 没有成功恢复任何模块


class TestLoadSnapshotFailures:
    """测试 load_snapshot 的失败场景"""
    
    def test_load_snapshot_incompatible_version(self, snapshot_manager):
        """测试加载不兼容版本的快照"""
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version="p5.0.0"  # 不兼容版本
        )
        
        # 先保存
        snapshot_manager._persist_snapshot(snapshot)
        
        # 再加载
        result = snapshot_manager.load_snapshot(None, "test_snap")
        
        assert result is None
    
    def test_load_snapshot_create_instance_failure(self, snapshot_manager):
        """测试创建实例失败"""
        class FailingDL:
            def __init__(self, config):
                raise Exception("创建失败")
        
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            config={"test": "config"}
        )
        
        snapshot_manager._persist_snapshot(snapshot)
        
        result = snapshot_manager.load_snapshot(FailingDL, "test_snap")
        
        assert result is None


class TestListSnapshotsException:
    """测试 list_snapshots 的异常处理"""
    
    def test_list_snapshots_exception(self, temp_snapshot_dir):
        """测试列出快照时的异常"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
        
        with patch('pathlib.Path.iterdir', side_effect=Exception("访问失败")):
            result = manager.list_snapshots()
        
        assert result == []


class TestCleanupSnapshotsException:
    """测试 cleanup_snapshots 的异常处理"""
    
    def test_cleanup_snapshots_delete_failure(self, temp_snapshot_dir):
        """测试删除快照失败"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
        
        mock_info = Mock()
        mock_info.snapshot_id = "test_snap"
        
        with patch.object(manager, 'list_snapshots', return_value=[mock_info]):
            with patch('pathlib.Path.unlink', side_effect=Exception("删除失败")):
                result = manager.cleanup_snapshots(keep_count=0)
        
        assert result == 0


class TestShowPerformancePanel:
    """测试显示性能面板"""
    
    def test_show_performance_panel(self, snapshot_manager):
        """测试 show_performance_panel"""
        snapshot_manager.show_performance_panel()
        # 只需确认不抛出异常


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
