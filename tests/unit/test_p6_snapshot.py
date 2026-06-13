"""
P6 快照管理器单元测试

测试覆盖：
1. SnapshotFrequencyController - 频率控制器
2. SnapshotPerformanceMonitor - 性能监控器
3. StateSnapshotManager - 快照管理器核心功能
4. 数据类测试 - SnapshotResult, SnapshotInfo, ModuleState, StateSnapshot
"""
import pytest
import os
import tempfile
import time
from datetime import datetime
from unittest.mock import patch, MagicMock, mock_open

from agent.p6_snapshot import (
    SnapshotResult,
    SnapshotInfo,
    ModuleState,
    StateSnapshot,
    PerformanceMetrics,
    SnapshotFrequencyController,
    SnapshotPerformanceMonitor,
    StateSnapshotManager,
)


class TestSnapshotDataClasses:
    """测试数据类结构"""
    
    @pytest.mark.p0
    def test_snapshot_result_defaults(self):
        """测试 SnapshotResult 默认值"""
        result = SnapshotResult(success=True)
        assert result.success is True
        assert result.snapshot_id is None
        assert result.version == ""
        assert result.elapsed_ms == 0.0
        assert result.error_message is None
        assert result.is_incremental is False
        assert result.base_snapshot_id is None
        assert result.space_saved_bytes == 0
        assert result.file_size == 0
        assert result.created_at is None
    
    @pytest.mark.p0
    def test_snapshot_result_with_values(self):
        """测试 SnapshotResult 带参数"""
        from datetime import datetime
        now = datetime.now()
        result = SnapshotResult(
            success=True,
            snapshot_id="test_snap_001",
            version="p6.2.0",
            elapsed_ms=123.45,
            is_incremental=True,
            base_snapshot_id="base_snap",
            space_saved_bytes=1024,
            created_at=now,
        )
        assert result.success is True
        assert result.snapshot_id == "test_snap_001"
        assert result.version == "p6.2.0"
        assert result.elapsed_ms == 123.45
        assert result.is_incremental is True
        assert result.base_snapshot_id == "base_snap"
        assert result.space_saved_bytes == 1024
        assert result.created_at == now
    
    @pytest.mark.p1
    def test_snapshot_info(self):
        """测试 SnapshotInfo"""
        from datetime import datetime
        now = datetime.now()
        info = SnapshotInfo(
            snapshot_id="test_snap",
            created_at=now,
            version="p6.2.0",
            file_size=4096,
            is_incremental=False,
        )
        assert info.snapshot_id == "test_snap"
        assert info.created_at == now
        assert info.version == "p6.2.0"
        assert info.file_size == 4096
        assert info.is_incremental is False
        assert info.base_snapshot_id is None
    
    @pytest.mark.p1
    def test_module_state(self):
        """测试 ModuleState"""
        state = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=b"test data",
            restore_priority=100,
            checksum="abc123",
            changed=True,
        )
        assert state.module_name == "test_module"
        assert state.initialized is True
        assert state.state_data == b"test data"
        assert state.restore_priority == 100
        assert state.checksum == "abc123"
        assert state.changed is True
    
    @pytest.mark.p1
    def test_state_snapshot_compute_checksum(self):
        """测试 StateSnapshot 校验和计算"""
        from datetime import datetime
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version="p6.2.0",
        )
        checksum = snapshot.compute_checksum()
        assert checksum is not None
        assert len(checksum) == 64  # SHA256 长度
        assert isinstance(checksum, str)


class TestSnapshotFrequencyController:
    """测试快照频率控制器"""
    
    @pytest.mark.p0
    def test_initial_state(self):
        """测试初始状态"""
        controller = SnapshotFrequencyController(
            min_interval_seconds=300.0,
            max_snapshots=5,
        )
        assert controller.min_interval_seconds == 300.0
        assert controller.max_snapshots == 5
        assert controller.last_save_time == 0.0
        assert controller.save_count == 0
    
    @pytest.mark.p0
    def test_can_save_first_time(self):
        """测试第一次保存应该允许"""
        controller = SnapshotFrequencyController(min_interval_seconds=300.0)
        assert controller.can_save() is True
    
    @pytest.mark.p0
    def test_can_save_force(self):
        """测试强制保存应该始终允许"""
        controller = SnapshotFrequencyController(min_interval_seconds=300.0)
        controller.last_save_time = time.time()  # 刚刚保存过
        assert controller.can_save(force=True) is True
    
    @pytest.mark.p0
    def test_can_save_too_frequent(self):
        """测试过于频繁的保存应该被拒绝"""
        controller = SnapshotFrequencyController(min_interval_seconds=300.0)
        controller.last_save_time = time.time()  # 刚刚保存过
        assert controller.can_save() is False
    
    @pytest.mark.p1
    def test_can_save_after_interval(self):
        """测试间隔足够后可以保存"""
        controller = SnapshotFrequencyController(min_interval_seconds=0.1)  # 0.1秒
        controller.last_save_time = time.time() - 1.0  # 1秒前保存
        assert controller.can_save() is True
    
    @pytest.mark.p1
    def test_on_save_success(self):
        """测试保存成功回调"""
        controller = SnapshotFrequencyController()
        initial_count = controller.save_count
        
        controller.on_save_success()
        
        assert controller.save_count == initial_count + 1
        assert controller.last_save_time > 0


class TestSnapshotPerformanceMonitor:
    """测试性能监控器"""
    
    @pytest.mark.p0
    def test_initialization(self):
        """测试初始化"""
        monitor = SnapshotPerformanceMonitor()
        assert monitor.metrics is not None
        assert monitor.start_time > 0
        assert monitor.metrics.total_saves == 0
        assert monitor.metrics.total_loads == 0
    
    @pytest.mark.p0
    def test_record_save(self):
        """测试记录保存操作"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_save(100.0, 512)
        
        assert monitor.metrics.total_saves == 1
        assert monitor.metrics.total_save_time_ms == 100.0
        assert monitor.metrics.avg_save_time_ms == 100.0
        assert monitor.metrics.last_save_time_ms == 100.0
        assert monitor.metrics.total_space_saved_bytes == 512
        assert monitor.metrics.snapshot_count == 1
    
    @pytest.mark.p0
    def test_record_multiple_saves(self):
        """测试记录多次保存操作"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_save(100.0, 512)
        monitor.record_save(200.0, 1024)
        
        assert monitor.metrics.total_saves == 2
        assert monitor.metrics.total_save_time_ms == 300.0
        assert monitor.metrics.avg_save_time_ms == 150.0
        assert monitor.metrics.last_save_time_ms == 200.0
        assert monitor.metrics.total_space_saved_bytes == 1536
    
    @pytest.mark.p1
    def test_record_load(self):
        """测试记录加载操作"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_load(50.0)
        
        assert monitor.metrics.total_loads == 1
        assert monitor.metrics.total_load_time_ms == 50.0
        assert monitor.metrics.avg_load_time_ms == 50.0
        assert monitor.metrics.last_load_time_ms == 50.0
    
    @pytest.mark.p1
    def test_record_module_serialize(self):
        """测试记录模块序列化"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_module_serialize("test_module", 10.0, 1024)
        
        assert monitor.metrics.module_serialize_times["test_module"] == 10.0
        assert monitor.metrics.module_data_sizes["test_module"] == 1024
    
    @pytest.mark.p1
    def test_record_module_deserialize(self):
        """测试记录模块反序列化"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_module_deserialize("test_module", 5.0)
        
        assert monitor.metrics.module_deserialize_times["test_module"] == 5.0
    
    @pytest.mark.p1
    def test_get_performance_summary(self):
        """测试获取性能摘要"""
        monitor = SnapshotPerformanceMonitor()
        monitor.record_save(100.0, 512)
        monitor.record_load(50.0)
        
        summary = monitor.get_performance_summary()
        
        assert "uptime_seconds" in summary
        assert summary["total_saves"] == 1
        assert summary["total_loads"] == 1
        assert summary["avg_save_ms"] == 100.0
        assert summary["avg_load_ms"] == 50.0


class TestStateSnapshotManager:
    """测试状态快照管理器"""
    
    @pytest.fixture
    def temp_snapshot_dir(self):
        """临时快照目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def snapshot_manager(self, temp_snapshot_dir):
        """创建快照管理器实例"""
        return StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False,
        )
    
    @pytest.mark.p0
    def test_initialization(self, temp_snapshot_dir):
        """测试初始化"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=True,
        )
        assert manager.snapshot_dir is not None
        assert manager.enable_compression is True
        assert manager.frequency_controller is not None
        assert manager.performance_monitor is not None
        assert manager.current_snapshot is None
    
    @pytest.mark.p0
    def test_ensure_snapshot_dir(self, temp_snapshot_dir):
        """测试确保快照目录存在"""
        non_existent_dir = os.path.join(temp_snapshot_dir, "nonexistent")
        manager = StateSnapshotManager(snapshot_dir=non_existent_dir)
        assert os.path.exists(non_existent_dir)
    
    @pytest.mark.p0
    def test_generate_snapshot_id(self, snapshot_manager):
        """测试生成快照ID"""
        snapshot_id = snapshot_manager._generate_snapshot_id()
        assert snapshot_id is not None
        assert snapshot_id.startswith("snap_")
        assert len(snapshot_id) > len("snap_")
    
    @pytest.mark.p0
    def test_compute_checksum(self, snapshot_manager):
        """测试计算校验和"""
        data = b"test data"
        checksum = snapshot_manager._compute_checksum(data)
        assert checksum is not None
        assert len(checksum) == 64  # SHA256
    
    @pytest.mark.p1
    def test_get_snapshot_path(self, snapshot_manager):
        """测试获取快照路径"""
        path = snapshot_manager._get_snapshot_path("test_snap", is_incremental=False)
        assert path is not None
        assert "test_snap.snap" in str(path)
        
        path_inc = snapshot_manager._get_snapshot_path("test_snap", is_incremental=True)
        assert "test_snap.incremental.snap" in str(path_inc)
    
    @pytest.mark.p1
    def test_list_snapshots_empty(self, snapshot_manager):
        """测试列出空目录的快照"""
        snapshots = snapshot_manager.list_snapshots()
        assert isinstance(snapshots, list)
        assert len(snapshots) == 0
    
    @pytest.mark.p1
    def test_check_compatibility_valid(self, snapshot_manager):
        """测试版本兼容性检查 - 有效版本"""
        from datetime import datetime
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p6.2.0",
        )
        assert snapshot_manager._check_compatibility(snapshot) is True
    
    @pytest.mark.p1
    def test_check_compatibility_invalid(self, snapshot_manager):
        """测试版本兼容性检查 - 无效版本"""
        from datetime import datetime
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="v1.0.0",
        )
        assert snapshot_manager._check_compatibility(snapshot) is False
    
    @pytest.mark.p1
    def test_persist_and_load_snapshot(self, snapshot_manager):
        """测试快照持久化和加载"""
        from datetime import datetime
        snapshot = StateSnapshot(
            snapshot_id="test_snap_001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={"test": "config"},
        )
        
        # 持久化
        result = snapshot_manager._persist_snapshot(snapshot)
        assert result is True
        
        # 加载
        loaded = snapshot_manager._load_snapshot_data("test_snap_001")
        assert loaded is not None
        assert loaded.snapshot_id == "test_snap_001"
        assert loaded.version == "p6.2.0"
        assert loaded.config == {"test": "config"}


class MockDigitalLife:
    """模拟的 DigitalLife 类，用于测试快照保存"""
    _config = {"test": "config"}
    _body = None
    _behavior = None
    _permission = None
    _tools_registry = None


class TestStateSnapshotManagerIntegration:
    """快照管理器集成测试"""
    
    @pytest.fixture
    def temp_snapshot_dir(self):
        """临时快照目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.mark.p1
    def test_save_snapshot_with_frequency_control(self, temp_snapshot_dir):
        """测试带频率控制的快照保存"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False,
        )
        
        # 修改频率控制器设置以便测试
        manager.frequency_controller.min_interval_seconds = 0.1
        
        digital_life = MockDigitalLife()
        
        # 第一次保存应该成功
        result1 = manager.save_snapshot(digital_life, force=True)
        assert result1.success is True
        
        # 第二次保存（过于频繁）应该失败
        result2 = manager.save_snapshot(digital_life)
        assert result2.success is False
        assert "过于频繁" in result2.error_message
    
    @pytest.mark.p1
    def test_incremental_snapshot(self, temp_snapshot_dir):
        """测试增量快照"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False,
        )
        
        digital_life = MockDigitalLife()
        
        # 第一次完整快照
        result1 = manager.save_snapshot(digital_life, force=True)
        assert result1.success is True
        assert result1.is_incremental is False
        
        # 第二次增量快照
        result2 = manager.save_snapshot(digital_life, incremental=True, force=True)
        assert result2.success is True
        assert result2.is_incremental is True
        # 验证空间节省（增量快照应该比完整快照小）
        assert result2.space_saved_bytes > 0


@pytest.mark.p0
@pytest.mark.unit
class TestP6SnapshotCriticalPaths:
    """P6 快照关键路径测试"""
    
    def test_module_imports(self):
        """测试模块导入"""
        modules = [
            "agent.p6_snapshot",
        ]
        for module in modules:
            try:
                __import__(module)
            except ImportError as e:
                pytest.skip(f"模块导入失败: {e}")
    
    def test_core_classes_exist(self):
        """测试核心类存在"""
        assert StateSnapshotManager is not None
        assert SnapshotFrequencyController is not None
        assert SnapshotPerformanceMonitor is not None
        assert StateSnapshot is not None
        assert SnapshotResult is not None


@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotPerformanceMonitorOutput:
    """性能监控器输出测试"""
    
    def test_print_performance_panel_with_data(self, capsys):
        """测试性能面板输出（有数据）"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_save(100.5, space_saved=512)
        monitor.record_save(200.75, space_saved=1024)
        monitor.record_load(50.25)
        monitor.record_module_serialize("test_module", 10.5, 256)
        
        monitor.print_performance_panel()
        
        captured = capsys.readouterr()
        assert "P6 快照系统性能监控面板" in captured.out
        assert "总保存次数: 2" in captured.out
        assert "总加载次数: 1" in captured.out
        assert "test_module" in captured.out
    
    def test_print_performance_panel_empty(self, capsys):
        """测试性能面板输出（空数据）"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.print_performance_panel()
        
        captured = capsys.readouterr()
        assert "P6 快照系统性能监控面板" in captured.out
        assert "总保存次数: 0" in captured.out
        assert "总加载次数: 0" in captured.out
    
    def test_get_performance_summary(self):
        """测试获取性能摘要"""
        monitor = SnapshotPerformanceMonitor()
        
        monitor.record_save(100.0, space_saved=512)
        monitor.record_load(50.0)
        
        summary = monitor.get_performance_summary()
        
        assert "uptime_seconds" in summary
        assert summary["total_saves"] == 1
        assert summary["total_loads"] == 1
        assert summary["avg_save_ms"] == 100.0
        assert summary["avg_load_ms"] == 50.0
        assert summary["total_space_saved_bytes"] == 512


@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotLoading:
    """测试快照加载功能"""
    
    @pytest.fixture
    def temp_snapshot_dir(self):
        """临时快照目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir
    
    @pytest.fixture
    def snapshot_manager(self, temp_snapshot_dir):
        """创建快照管理器实例"""
        return StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
    
    def test_load_snapshot_data_only(self, snapshot_manager, temp_snapshot_dir):
        """测试仅加载快照数据（不恢复实例）"""
        # 先创建一个测试快照
        snapshot = StateSnapshot(
            snapshot_id="test_snap_001",
            created_at=datetime.now(),
            version="p6.2.0",
            config={"test": "config"}
        )
        assert snapshot_manager._persist_snapshot(snapshot)
        
        # 仅加载数据，不恢复实例
        loaded = snapshot_manager.load_snapshot(digital_life_class=None)
        assert loaded is not None
        assert loaded.snapshot_id == "test_snap_001"
        assert loaded.config == {"test": "config"}
    
    def test_load_snapshot_nonexistent(self, snapshot_manager):
        """测试加载不存在的快照"""
        loaded = snapshot_manager.load_snapshot(digital_life_class=None, snapshot_id="nonexistent")
        assert loaded is None
    
    def test_list_snapshots_empty(self, snapshot_manager):
        """测试列出快照（空目录）"""
        snapshots = snapshot_manager.list_snapshots()
        assert isinstance(snapshots, list)
        assert len(snapshots) == 0
    
    def test_list_snapshots_with_files(self, snapshot_manager):
        """测试列出快照（有文件）"""
        # 创建一个测试快照
        snapshot = StateSnapshot(
            snapshot_id="test_snap_002",
            created_at=datetime.now(),
            version="p6.2.0",
            config={"test": "config2"}
        )
        assert snapshot_manager._persist_snapshot(snapshot)
        
        # 列出快照
        snapshots = snapshot_manager.list_snapshots()
        assert isinstance(snapshots, list)
        assert len(snapshots) >= 1


# 测试辅助类
class SimpleDigitalLife:
    """简化的 DigitalLife 类用于测试快照恢复"""
    def __init__(self, config=None):
        self._config = config or {}
        self._body = None
        self._behavior = None
        self._permission = None
        self._tools_registry = None
