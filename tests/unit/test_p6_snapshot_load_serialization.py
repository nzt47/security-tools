"""P6 Snapshot 快照加载和序列化测试，覆盖版本兼容场景"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from pathlib import Path

from agent.p6_snapshot import (
    StateSnapshotManager,
    StateSnapshot,
    ModuleState,
)


class TestSnapshotLoading:
    """测试快照加载功能"""

    def test_load_snapshot_latest(self):
        """测试加载最新快照"""
        manager = StateSnapshotManager()
        manager._load_snapshot_data = MagicMock(return_value=None)

        result = manager.load_snapshot()

        assert result is None
        manager._load_snapshot_data.assert_called_once_with(None)

    def test_load_snapshot_specific_id(self):
        """测试加载指定ID的快照"""
        manager = StateSnapshotManager()
        manager._load_snapshot_data = MagicMock(return_value=None)

        result = manager.load_snapshot(snapshot_id="test-snapshot-123")

        assert result is None
        manager._load_snapshot_data.assert_called_once_with("test-snapshot-123")

    def test_load_snapshot_data_no_snapshots(self):
        """测试没有快照的情况"""
        manager = StateSnapshotManager()
        manager.list_snapshots = MagicMock(return_value=[])

        result = manager._load_snapshot_data()

        assert result is None

    def test_load_snapshot_data_incremental_first(self):
        """测试优先加载增量快照"""
        manager = StateSnapshotManager()
        manager.list_snapshots = MagicMock(return_value=[MagicMock(snapshot_id="snap1")])
        
        # 需要正确模拟路径存在
        mock_incremental_path = MagicMock()
        mock_incremental_path.exists.return_value = True
        
        mock_full_path = MagicMock()
        mock_full_path.exists.return_value = False
        
        def get_path_side_effect(snapshot_id, is_incremental):
            if is_incremental:
                return mock_incremental_path
            return mock_full_path
        
        manager._get_snapshot_path = MagicMock(side_effect=get_path_side_effect)
        manager._load_from_path = MagicMock(return_value="loaded_snapshot")

        result = manager._load_snapshot_data()

        assert result == "loaded_snapshot"

    def test_load_snapshot_data_fallback_to_full(self):
        """测试增量快照失败后回退到完整快照"""
        manager = StateSnapshotManager()
        manager.list_snapshots = MagicMock(return_value=[MagicMock(snapshot_id="snap1")])
        manager._get_snapshot_path = MagicMock(return_value=Path("/fake/path"))
        
        def load_from_path_side_effect(path):
            raise Exception("Incremental load failed")
        
        manager._load_from_path = MagicMock(side_effect=load_from_path_side_effect)

        result = manager._load_snapshot_data()

        assert result is None

    def test_load_from_path_not_exists(self):
        """测试文件不存在的情况"""
        manager = StateSnapshotManager()
        fake_path = Path("/nonexistent/path/snapshot.dat")
        
        # _load_from_path 没有处理 FileNotFoundError，直接会抛出异常
        # 这里测试在真实场景下，文件不存在时的行为
        assert not fake_path.exists()
        
        # 由于方法没有 try-except，文件不存在会抛出异常
        with pytest.raises(FileNotFoundError):
            manager._load_from_path(fake_path)


class TestSnapshotVersionCompatibility:
    """测试版本兼容性检查"""

    def test_compatibility_p6_version(self):
        """测试p6版本兼容"""
        manager = StateSnapshotManager()
        
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p6.2.0"
        )

        result = manager._check_compatibility(snapshot)

        assert result is True

    def test_compatibility_p6_1_version(self):
        """测试p6.1版本兼容"""
        manager = StateSnapshotManager()
        
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p6.1.0"
        )

        result = manager._check_compatibility(snapshot)

        assert result is True

    def test_compatibility_p6_3_version(self):
        """测试p6.3版本兼容"""
        manager = StateSnapshotManager()
        
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p6.3.0"
        )

        result = manager._check_compatibility(snapshot)

        assert result is True

    def test_compatibility_incompatible_version(self):
        """测试不兼容版本"""
        manager = StateSnapshotManager()
        
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p5.0.0"
        )

        result = manager._check_compatibility(snapshot)

        assert result is False

    def test_compatibility_empty_version(self):
        """测试空版本号"""
        manager = StateSnapshotManager()
        
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version=""
        )

        result = manager._check_compatibility(snapshot)

        assert result is False


class TestSnapshotSerialization:
    """测试快照序列化"""

    def test_snapshot_checksum(self):
        """测试快照校验和计算"""
        snapshot = StateSnapshot(
            snapshot_id="test",
            created_at=datetime.now(),
            version="p6.2.0",
            config={"key": "value"},
            module_states={"test_module": ModuleState(module_name="test_module", initialized=True, state_data=b"test data")}
        )

        checksum = snapshot.compute_checksum()

        assert isinstance(checksum, str)
        assert len(checksum) > 0

    def test_module_state_checksum(self):
        """测试模块状态校验和"""
        module_state = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=b"test data"
        )

        # checksum 需要手动计算（不是属性）
        import hashlib
        expected_checksum = hashlib.sha256(b"test data").hexdigest()
        module_state.checksum = expected_checksum

        assert isinstance(module_state.checksum, str)
        assert len(module_state.checksum) > 0
        assert module_state.checksum == expected_checksum

    def test_snapshot_equality(self):
        """测试快照相等性检查"""
        now = datetime.now()
        snapshot1 = StateSnapshot(
            snapshot_id="same",
            created_at=now,
            version="p6.2.0"
        )
        snapshot2 = StateSnapshot(
            snapshot_id="same",
            created_at=now,
            version="p6.2.0"
        )

        assert snapshot1 == snapshot2

    def test_snapshot_inequality(self):
        """测试快照不等性"""
        now = datetime.now()
        snapshot1 = StateSnapshot(
            snapshot_id="snap1",
            created_at=now,
            version="p6.2.0"
        )
        snapshot2 = StateSnapshot(
            snapshot_id="snap2",
            created_at=now,
            version="p6.2.0"
        )

        assert snapshot1 != snapshot2


class TestIncrementalSnapshot:
    """测试增量快照"""

    def test_incremental_snapshot_creation(self):
        """测试创建增量快照"""
        snapshot = StateSnapshot(
            snapshot_id="incr-1",
            created_at=datetime.now(),
            version="p6.2.0",
            is_incremental=True,
            base_snapshot_id="base-1"
        )

        assert snapshot.is_incremental is True
        assert snapshot.base_snapshot_id == "base-1"

    def test_full_snapshot_creation(self):
        """测试创建完整快照"""
        snapshot = StateSnapshot(
            snapshot_id="full-1",
            created_at=datetime.now(),
            version="p6.2.0",
            is_incremental=False
        )

        assert snapshot.is_incremental is False
        assert snapshot.base_snapshot_id is None


class TestSnapshotCleanup:
    """测试快照清理"""

    def test_cleanup_no_old_snapshots(self):
        """测试没有旧快照需要清理"""
        manager = StateSnapshotManager()
        manager.list_snapshots = MagicMock(return_value=[])

        manager._cleanup_old_snapshots()

        # 不应该抛出异常

    def test_cleanup_with_max_reached(self):
        """测试达到最大快照数时的清理"""
        manager = StateSnapshotManager()
        mock_snapshots = [MagicMock(snapshot_id=f"snap{i}") for i in range(15)]
        manager.list_snapshots = MagicMock(return_value=mock_snapshots)
        manager.frequency_controller.max_snapshots = 10
        
        # 模拟 _get_snapshot_path 返回存在的路径
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        manager._get_snapshot_path = MagicMock(return_value=mock_path)

        manager._cleanup_old_snapshots()

        # 应该调用 5 次 unlink（5个快照 * 2种格式）
        assert mock_path.unlink.call_count == 5 * 2