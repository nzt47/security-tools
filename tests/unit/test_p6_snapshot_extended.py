"""
P6 快照系统扩展测试 - 覆盖更多未覆盖的分支
"""
import pytest
import os
import sys
import tempfile
import pickle
import gzip
import time
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock

# 修复路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.p6_snapshot import (
    StateSnapshotManager,
    StateSnapshot,
    ModuleState,
    SnapshotFrequencyController,
    SnapshotPerformanceMonitor,
    SnapshotResult,
    SnapshotInfo
)

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

@pytest.fixture
def snapshot_manager_with_compression(temp_snapshot_dir):
    return StateSnapshotManager(
        snapshot_dir=temp_snapshot_dir,
        enable_compression=True
    )

class MockBodySensor:
    """可序列化的模拟 BodySensor 类"""
    def __init__(self):
        self.initialized = True

class MockDigitalLife:
    """可序列化的模拟 DigitalLife 类"""
    def __init__(self, config=None):
        self._config = config or {'test': 'config'}
        self._body = MockBodySensor()
        self._behavior = None
        self._permission = None

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotFrequencyController:
    """测试频率控制器"""
    
    def test_can_save_first_time(self):
        """测试首次保存"""
        controller = SnapshotFrequencyController()
        assert controller.can_save(force=False) is True
    
    def test_can_save_with_force(self):
        """测试强制保存"""
        controller = SnapshotFrequencyController()
        controller.last_save_time = datetime.now()
        assert controller.can_save(force=True) is True
    
    def test_can_save_too_frequent(self):
        """测试保存过于频繁"""
        controller = SnapshotFrequencyController()
        controller.last_save_time = time.time()
        assert controller.can_save(force=False) is False
    
    def test_on_save_success(self):
        """测试保存成功后的更新"""
        controller = SnapshotFrequencyController()
        initial_count = controller.save_count
        
        controller.on_save_success()
        
        assert controller.save_count == initial_count + 1
        assert controller.last_save_time is not None

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotPerformanceMonitor:
    """测试性能监控器"""
    
    def test_record_save(self):
        """测试记录保存性能"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_save(100.5, space_saved=512)
        
        stats = monitor.get_performance_summary()
        assert stats['total_saves'] == 1
        assert stats['avg_save_ms'] == 100.5
    
    def test_record_load(self):
        """测试记录加载性能"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_load(50.25)
        
        stats = monitor.get_performance_summary()
        assert stats['total_loads'] == 1
        assert stats['avg_load_ms'] == 50.25
    
    def test_record_module_serialize(self):
        """测试记录模块序列化"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_module_serialize("test_module", 10.5, 256)
        
        assert "test_module" in monitor.metrics.module_serialize_times
    
    def test_get_performance_summary(self):
        """测试获取性能摘要"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_save(100.0, space_saved=512)
        monitor.record_save(200.0, space_saved=1024)
        monitor.record_load(50.0)
        
        summary = monitor.get_performance_summary()
        
        assert summary['total_saves'] == 2
        assert summary['total_loads'] == 1
        assert summary['avg_save_ms'] == 150.0
        assert summary['avg_load_ms'] == 50.0
        assert summary['total_space_saved_bytes'] == 1536

@pytest.mark.p1
@pytest.mark.unit
class TestStateSnapshotManagerExtended:
    """测试快照管理器扩展功能"""
    
    def test_check_compatibility_p6_version(self, snapshot_manager):
        """测试版本兼容性检查 - P6版本"""
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p6.2.0"
        )
        assert snapshot_manager._check_compatibility(snapshot) is True
        
        snapshot.version = "p6.1.0"
        assert snapshot_manager._check_compatibility(snapshot) is True
    
    def test_check_compatibility_incompatible_version(self, snapshot_manager):
        """测试版本兼容性检查 - 不兼容版本"""
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p5.0.0"
        )
        assert snapshot_manager._check_compatibility(snapshot) is False
    
    def test_merge_snapshots(self, snapshot_manager):
        """测试快照合并"""
        base_state_data = pickle.dumps({"base_key": "base_value"})
        base_snapshot = StateSnapshot(
            snapshot_id="base_snap",
            created_at=datetime.now(),
            version="p6.2.0",
            module_states={
                "module1": ModuleState(
                    module_name="module1",
                    initialized=True,
                    state_data=base_state_data,
                    restore_priority=100,
                    changed=False
                )
            }
        )
        
        incremental_state_data = pickle.dumps({"incremental_key": "incremental_value"})
        incremental_snapshot = StateSnapshot(
            snapshot_id="incremental_snap",
            created_at=datetime.now(),
            version="p6.2.0",
            is_incremental=True,
            base_snapshot_id="base_snap",
            module_states={
                "module1": ModuleState(
                    module_name="module1",
                    initialized=True,
                    state_data=incremental_state_data,
                    restore_priority=100,
                    changed=True
                )
            }
        )
        
        merged = snapshot_manager._merge_snapshots(base_snapshot, incremental_snapshot)
        
        assert merged.snapshot_id == "incremental_snap"
        assert "module1" in merged.module_states
    
    def test_cleanup_old_snapshots(self, snapshot_manager):
        """测试清理旧快照"""
        # 创建多个快照
        for i in range(10):
            snapshot = StateSnapshot(
                snapshot_id=f"test_snap_{i}",
                created_at=datetime.now(),
                version="p6.2.0"
            )
            snapshot_manager._persist_snapshot(snapshot)
        
        # 限制只保留3个
        snapshot_manager.frequency_controller.max_snapshots = 3
        
        snapshot_manager._cleanup_old_snapshots()
        
        remaining = snapshot_manager.list_snapshots()
        assert len(remaining) <= 3
    
    def test_compute_checksum(self, snapshot_manager):
        """测试计算校验和"""
        data = b"test data"
        checksum = snapshot_manager._compute_checksum(data)
        
        assert len(checksum) == 64
        assert isinstance(checksum, str)
    
    def test_update_module_checksums(self, snapshot_manager):
        """测试更新模块校验和"""
        state_data = pickle.dumps({"test": "data"})
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version="p6.2.0",
            module_states={
                "test_module": ModuleState(
                    module_name="test_module",
                    initialized=True,
                    state_data=state_data,
                    restore_priority=100,
                    checksum="test_checksum"
                )
            }
        )
        
        snapshot_manager._update_module_checksums(snapshot)
        
        assert snapshot_manager.last_module_checksums["test_module"] == "test_checksum"

@pytest.mark.p1
@pytest.mark.unit
class TestStateSnapshotManagerCompression:
    """测试压缩功能"""
    
    def test_save_with_compression(self, snapshot_manager_with_compression):
        """测试带压缩保存"""
        mock_digital_life = MockDigitalLife()
        
        result = snapshot_manager_with_compression.save_snapshot(mock_digital_life, force=True)
        
        assert result.success is True
    
    def test_load_with_compression(self, snapshot_manager_with_compression):
        """测试加载压缩快照"""
        mock_digital_life = MockDigitalLife()
        
        # 保存快照
        result = snapshot_manager_with_compression.save_snapshot(mock_digital_life, force=True)
        assert result.success is True
        
        # 使用返回的快照ID直接加载，避免list_snapshots排序问题
        loaded = snapshot_manager_with_compression._load_snapshot_data(result.snapshot_id)
        
        assert loaded is not None
        assert loaded.snapshot_id == result.snapshot_id

@pytest.mark.p1
@pytest.mark.unit
class TestStateSnapshotManagerIncremental:
    """测试增量快照功能"""
    
    def test_incremental_snapshot(self, snapshot_manager):
        """测试增量快照"""
        mock_digital_life = MockDigitalLife()
        
        # 先保存一个完整快照
        result1 = snapshot_manager.save_snapshot(mock_digital_life, force=True, incremental=False)
        assert result1.success is True
        
        # 保存增量快照
        result2 = snapshot_manager.save_snapshot(mock_digital_life, force=True, incremental=True)
        assert result2.success is True
        
        assert result2.is_incremental is True
    
    def test_load_incremental_snapshot(self, snapshot_manager):
        """测试加载增量快照"""
        mock_digital_life = MockDigitalLife()
        
        # 保存基础快照
        result1 = snapshot_manager.save_snapshot(mock_digital_life, force=True, incremental=False)
        
        # 保存增量快照
        result2 = snapshot_manager.save_snapshot(mock_digital_life, force=True, incremental=True)
        
        # 尝试加载
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        assert loaded is not None

@pytest.mark.p1
@pytest.mark.unit
class TestStateSnapshotManagerErrorHandling:
    """测试错误处理"""
    
    def test_save_failure(self, snapshot_manager):
        """测试保存失败"""
        mock_digital_life = Mock()
        mock_digital_life._config = {}
        # 模拟异常
        with patch.object(snapshot_manager, '_persist_snapshot', return_value=False):
            result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
            assert result.success is False
    
    def test_load_nonexistent_snapshot(self, snapshot_manager):
        """测试加载不存在的快照"""
        loaded = snapshot_manager._load_snapshot_data("nonexistent_id")
        assert loaded is None
    
    def test_list_snapshots_empty(self, snapshot_manager):
        """测试空目录列出快照"""
        snapshots = snapshot_manager.list_snapshots()
        assert isinstance(snapshots, list)
        assert len(snapshots) == 0

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotResult:
    """测试快照结果类"""
    
    def test_success_result(self):
        """测试成功结果"""
        result = SnapshotResult(
            success=True,
            snapshot_id="test_snap",
            elapsed_ms=100.5,
            is_incremental=False
        )
        
        assert result.success is True
        assert result.snapshot_id == "test_snap"
        assert result.elapsed_ms == 100.5
    
    def test_failure_result(self):
        """测试失败结果"""
        result = SnapshotResult(
            success=False,
            error_message="Test error"
        )
        
        assert result.success is False
        assert result.error_message == "Test error"

@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotInfo:
    """测试快照信息类"""
    
    def test_snapshot_info_creation(self):
        """测试创建快照信息"""
        info = SnapshotInfo(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version="p6.2.0",
            file_size=1024,
            is_incremental=False
        )
        
        assert info.snapshot_id == "test_snap"
        assert info.is_incremental is False
        assert info.file_size == 1024

if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
