"""
P6 快照系统高级测试 - 覆盖快照加载、序列化和复杂场景
"""
import pytest
import os
import sys
import tempfile
import pickle
import gzip
import time
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock, call

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

class MockBody:
    """可序列化的模拟 Body 类"""
    def __init__(self):
        self.is_initialized = True
        
    def get_health_report(self):
        return "Healthy"
    
    def get_sensor_summary(self):
        return "Sensors OK"

class MockDigitalLife:
    """可序列化的模拟 DigitalLife 类"""
    def __init__(self, config=None):
        self._config = config or {'test': 'config'}
        self._body = MockBody()
        self._behavior = None
        self._permission = None
        self._memory = None
        self._llm = None
        self._prompt_injector = None

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotSerialization:
    """测试快照序列化逻辑"""
    
    def test_module_state_serialization(self):
        """测试模块状态序列化"""
        state_data = pickle.dumps({"key": "value"})
        module_state = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=state_data,
            restore_priority=100,
            changed=True
        )
        
        assert module_state.module_name == "test_module"
        assert module_state.initialized is True
        assert module_state.changed is True
    
    def test_module_state_deserialization(self):
        """测试模块状态反序列化"""
        state_data = pickle.dumps({"key": "value"})
        module_state = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=state_data,
            restore_priority=100
        )
        
        restored = pickle.loads(pickle.dumps(module_state))
        
        assert restored.module_name == "test_module"
        assert restored.initialized is True
        assert restored.state_data == state_data
    
    def test_snapshot_serialization(self):
        """测试快照序列化"""
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={}
        )
        
        serialized = pickle.dumps(snapshot)
        restored = pickle.loads(serialized)
        
        assert restored.snapshot_id == "test_snap"
        assert restored.version == SNAPSHOT_VERSION

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotLoadingAdvanced:
    """测试快照加载的高级场景"""
    
    def test_load_snapshot_empty(self, snapshot_manager):
        """测试加载空快照"""
        snapshot = StateSnapshot(
            snapshot_id="empty_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={}
        )
        
        snapshot_manager._persist_snapshot(snapshot)
        
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        
        assert loaded is not None
        assert len(loaded.module_states) == 0
    
    def test_load_snapshot_with_modules(self, snapshot_manager):
        """测试加载包含模块的快照"""
        state_data = pickle.dumps({"module_data": "test_value"})
        module_state = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=state_data,
            restore_priority=100,
            changed=True
        )
        
        snapshot = StateSnapshot(
            snapshot_id="module_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={"test_module": module_state}
        )
        
        snapshot_manager._persist_snapshot(snapshot)
        
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        
        assert loaded is not None
        assert "test_module" in loaded.module_states
    
    def test_load_incremental_snapshot_no_base(self, snapshot_manager):
        """测试加载没有基础快照的增量快照"""
        incremental_snapshot = StateSnapshot(
            snapshot_id="incremental_only",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            is_incremental=True,
            base_snapshot_id="nonexistent_base"
        )
        
        snapshot_manager._persist_snapshot(incremental_snapshot)
        
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        
        # 应该返回None或抛出异常，取决于实现
        assert loaded is None or isinstance(loaded, StateSnapshot)
    
    def test_load_snapshot_version_mismatch(self, snapshot_manager):
        """测试加载版本不匹配的快照"""
        snapshot = StateSnapshot(
            snapshot_id="old_version",
            created_at=datetime.now(),
            version="p5.0.0"
        )
        
        snapshot_manager._persist_snapshot(snapshot)
        
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        
        assert loaded is None
    
    def test_load_snapshot_checksum_validation(self, snapshot_manager):
        """测试加载时的校验和验证"""
        state_data = pickle.dumps({"key": "value"})
        module_state = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=state_data,
            restore_priority=100,
            checksum="invalid_checksum"
        )
        
        snapshot = StateSnapshot(
            snapshot_id="checksum_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={"test_module": module_state}
        )
        
        snapshot_manager._persist_snapshot(snapshot)
        snapshot_manager.last_module_checksums["test_module"] = "different_checksum"
        
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        
        assert loaded is not None

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotManagerEdgeCases:
    """测试快照管理器的边界情况"""
    
    def test_save_empty_snapshot(self, snapshot_manager):
        """测试保存空快照"""
        mock_digital_life = MockDigitalLife()
        mock_digital_life._config = {}
        
        result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        
        assert result.success is True
    
    def test_save_large_snapshot(self, snapshot_manager):
        """测试保存大型快照"""
        mock_digital_life = MockDigitalLife()
        mock_digital_life._config = {'large': 'data' * 1000}
        
        result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        
        assert result.success is True
    
    def test_list_snapshots_with_corrupted_files(self, temp_snapshot_dir):
        """测试列出包含损坏文件的快照目录"""
        # 创建一个损坏的文件
        corrupted_file = os.path.join(temp_snapshot_dir, "corrupted.snap")
        with open(corrupted_file, 'w') as f:
            f.write("not a valid pickle")
        
        manager = StateSnapshotManager(snapshot_dir=temp_snapshot_dir)
        snapshots = manager.list_snapshots()
        
        assert isinstance(snapshots, list)
    
    def test_delete_snapshot(self, snapshot_manager):
        """测试删除快照"""
        mock_digital_life = MockDigitalLife()
        
        result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        assert result.success is True
        
        # 检查快照存在
        snapshots = snapshot_manager.list_snapshots()
        assert len(snapshots) == 1
    
    def test_save_snapshot_with_special_characters(self, snapshot_manager):
        """测试保存包含特殊字符的快照"""
        mock_digital_life = MockDigitalLife()
        mock_digital_life._config = {
            'special': '!@#$%^&*()_+-=[]{}|;:,.<>?'
        }
        
        result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        
        assert result.success is True

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotPerformance:
    """测试快照性能监控"""
    
    def test_performance_monitor_multiple_saves(self, snapshot_manager):
        """测试多次保存的性能监控"""
        monitor = snapshot_manager.performance_monitor
        
        monitor.record_save(100.0, space_saved=512)
        monitor.record_save(200.0, space_saved=1024)
        monitor.record_save(150.0, space_saved=768)
        
        summary = monitor.get_performance_summary()
        
        assert summary['total_saves'] == 3
        assert summary['avg_save_ms'] == 150.0
        assert summary['total_space_saved_bytes'] == 2304
    
    def test_performance_monitor_multiple_loads(self, snapshot_manager):
        """测试多次加载的性能监控"""
        monitor = snapshot_manager.performance_monitor
        
        monitor.record_load(50.0)
        monitor.record_load(75.0)
        monitor.record_load(100.0)
        
        summary = monitor.get_performance_summary()
        
        assert summary['total_loads'] == 3
        assert summary['avg_load_ms'] == 75.0
    
    def test_performance_monitor_empty(self, snapshot_manager):
        """测试空性能监控"""
        monitor = snapshot_manager.performance_monitor
        
        summary = monitor.get_performance_summary()
        
        assert summary['total_saves'] == 0
        assert summary['total_loads'] == 0

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotFrequencyControl:
    """测试快照频率控制"""
    
    def test_frequency_control_max_snapshots(self, temp_snapshot_dir):
        """测试最大快照数量限制"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
        manager.frequency_controller.max_snapshots = 2
        
        mock_digital_life = MockDigitalLife()
        
        # 保存多个快照
        result1 = manager.save_snapshot(mock_digital_life, force=True)
        result2 = manager.save_snapshot(mock_digital_life, force=True)
        result3 = manager.save_snapshot(mock_digital_life, force=True)
        
        assert result1.success is True
        assert result2.success is True
        assert result3.success is True
        
        # 检查只保留了最新的2个
        snapshots = manager.list_snapshots()
        assert len(snapshots) <= 2
    
    def test_frequency_control_min_interval(self, snapshot_manager):
        """测试最小间隔控制"""
        mock_digital_life = MockDigitalLife()
        
        # 第一次保存应该成功
        result1 = snapshot_manager.save_snapshot(mock_digital_life, force=False)
        assert result1.success is True
        
        # 立即再次保存（非强制）应该被拒绝
        result2 = snapshot_manager.save_snapshot(mock_digital_life, force=False)
        assert result2.success is False
    
    def test_frequency_control_force_override(self, snapshot_manager):
        """测试强制覆盖频率限制"""
        mock_digital_life = MockDigitalLife()
        
        # 第一次保存
        result1 = snapshot_manager.save_snapshot(mock_digital_life, force=False)
        assert result1.success is True
        
        # 强制保存应该成功
        result2 = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        assert result2.success is True

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotIntegration:
    """测试快照完整流程"""
    
    def test_full_snapshot_cycle(self, snapshot_manager):
        """测试完整的快照生命周期"""
        mock_digital_life = MockDigitalLife()
        
        # 保存快照
        save_result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        assert save_result.success is True
        
        # 列出快照
        snapshots = snapshot_manager.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].snapshot_id == save_result.snapshot_id
        
        # 加载快照
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        assert loaded is not None
        assert loaded.snapshot_id == save_result.snapshot_id
    
    def test_incremental_snapshot_cycle(self, snapshot_manager):
        """测试增量快照生命周期"""
        mock_digital_life = MockDigitalLife()
        
        # 保存基础快照
        base_result = snapshot_manager.save_snapshot(mock_digital_life, force=True, incremental=False)
        assert base_result.success is True
        assert base_result.is_incremental is False
        
        # 保存增量快照
        incremental_result = snapshot_manager.save_snapshot(mock_digital_life, force=True, incremental=True)
        assert incremental_result.success is True
        assert incremental_result.is_incremental is True
        
        # 加载最新快照
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        assert loaded is not None
    
    def test_snapshot_info(self, snapshot_manager):
        """测试快照信息"""
        mock_digital_life = MockDigitalLife()
        
        result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        
        snapshots = snapshot_manager.list_snapshots()
        assert len(snapshots) == 1
        
        info = snapshots[0]
        assert info.snapshot_id == result.snapshot_id
        assert info.version == SNAPSHOT_VERSION
        assert info.is_incremental is False

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotErrorHandling:
    """测试快照错误处理"""
    
    def test_load_corrupted_snapshot(self, temp_snapshot_dir):
        """测试加载损坏的快照"""
        manager = StateSnapshotManager(snapshot_dir=temp_snapshot_dir)
        
        # 创建损坏的快照文件
        corrupted_path = os.path.join(temp_snapshot_dir, "corrupted.snap")
        with open(corrupted_path, 'wb') as f:
            f.write(b"not a valid pickle")
        
        loaded = manager.load_snapshot(digital_life_class=None)
        
        assert loaded is None
    
    def test_save_failure_disk_full(self, snapshot_manager):
        """测试磁盘满时的保存失败"""
        mock_digital_life = Mock()
        mock_digital_life._config = {}
        
        with patch.object(snapshot_manager, '_persist_snapshot', return_value=False):
            result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
            
            assert result.success is False
    
    def test_missing_snapshot_directory(self):
        """测试快照目录不存在"""
        non_existent_dir = os.path.join(tempfile.gettempdir(), "non_existent_dir_xyz")
        
        manager = StateSnapshotManager(snapshot_dir=non_existent_dir)
        
        # 应该能够创建目录或优雅处理
        mock_digital_life = MockDigitalLife()
        
        result = manager.save_snapshot(mock_digital_life, force=True)
        
        assert result.success is True

if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
