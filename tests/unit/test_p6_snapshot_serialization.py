"""
P6 快照系统序列化测试 - 覆盖序列化/反序列化和增量快照合并逻辑
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
        self._initialized = True
        self.watch_dirs = ["/tmp/test"]
        self.config = {"timeout": 10}


class MockBehavior:
    """可序列化的模拟 Behavior 类"""
    def __init__(self):
        self._current_mode = Mock()
        self._current_mode.value = "NORMAL"
        self._mode_history = ["NORMAL", "ACTIVE"]
        self.THRESHOLDS = {"high": 0.9, "low": 0.1}


class MockPermission:
    """可序列化的模拟 Permission 类"""
    def __init__(self):
        self.DANGEROUS_PATTERNS = [r"rm -rf", r"del /f"]
        self.BLACKLIST = ["bad.com"]
        self.SENSITIVE_EXTENSIONS = {".key", ".pem"}


class MockToolsRegistry:
    """可序列化的模拟 ToolsRegistry 类"""
    def __init__(self):
        self._tools = {"tool1": Mock(), "tool2": Mock()}


class MockDigitalLifeFull:
    """包含完整模块的模拟 DigitalLife 类"""
    def __init__(self, config=None):
        self._config = config or {'test': 'config'}
        self._body = MockBody()
        self._behavior = MockBehavior()
        self._permission = MockPermission()
        self._tools_registry = MockToolsRegistry()


@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotMerge:
    """测试增量快照合并逻辑"""
    
    def test_merge_snapshots_basic(self, snapshot_manager):
        """测试基础快照合并"""
        base_state_data = pickle.dumps({"base_key": "base_value"})
        base_module = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=base_state_data,
            restore_priority=100,
            checksum="base_checksum",
            changed=False
        )
        
        base_snapshot = StateSnapshot(
            snapshot_id="base_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={"test_module": base_module}
        )
        
        incremental_state_data = pickle.dumps({"incremental_key": "incremental_value"})
        incremental_module = ModuleState(
            module_name="test_module",
            initialized=True,
            state_data=incremental_state_data,
            restore_priority=100,
            checksum="incremental_checksum",
            changed=True
        )
        
        incremental_snapshot = StateSnapshot(
            snapshot_id="incremental_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            is_incremental=True,
            base_snapshot_id="base_snap",
            module_states={"test_module": incremental_module}
        )
        
        merged = snapshot_manager._merge_snapshots(base_snapshot, incremental_snapshot)
        
        assert merged.snapshot_id == "incremental_snap"
        assert "test_module" in merged.module_states
        assert merged.module_states["test_module"].changed is True
    
    def test_merge_snapshots_multiple_modules(self, snapshot_manager):
        """测试多模块快照合并"""
        base_module1 = ModuleState(
            module_name="module1",
            initialized=True,
            state_data=pickle.dumps({"key1": "value1"}),
            restore_priority=100,
            changed=False
        )
        base_module2 = ModuleState(
            module_name="module2",
            initialized=True,
            state_data=pickle.dumps({"key2": "value2"}),
            restore_priority=90,
            changed=False
        )
        
        base_snapshot = StateSnapshot(
            snapshot_id="base_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={"module1": base_module1, "module2": base_module2}
        )
        
        incremental_module1 = ModuleState(
            module_name="module1",
            initialized=True,
            state_data=pickle.dumps({"key1": "updated_value1"}),
            restore_priority=100,
            changed=True
        )
        incremental_module3 = ModuleState(
            module_name="module3",
            initialized=True,
            state_data=pickle.dumps({"key3": "value3"}),
            restore_priority=80,
            changed=True
        )
        
        incremental_snapshot = StateSnapshot(
            snapshot_id="incremental_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            is_incremental=True,
            base_snapshot_id="base_snap",
            module_states={"module1": incremental_module1, "module3": incremental_module3}
        )
        
        merged = snapshot_manager._merge_snapshots(base_snapshot, incremental_snapshot)
        
        assert len(merged.module_states) == 3
        assert "module1" in merged.module_states
        assert "module2" in merged.module_states
        assert "module3" in merged.module_states
    
    def test_merge_snapshots_unchanged_module(self, snapshot_manager):
        """测试合并时未变化模块保持不变"""
        base_module = ModuleState(
            module_name="module1",
            initialized=True,
            state_data=pickle.dumps({"key": "base_value"}),
            restore_priority=100,
            checksum="base_checksum",
            changed=False
        )
        
        base_snapshot = StateSnapshot(
            snapshot_id="base_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={"module1": base_module}
        )
        
        unchanged_module = ModuleState(
            module_name="module1",
            initialized=True,
            state_data=pickle.dumps({"key": "base_value"}),
            restore_priority=100,
            checksum="base_checksum",
            changed=False
        )
        
        incremental_snapshot = StateSnapshot(
            snapshot_id="incremental_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            is_incremental=True,
            base_snapshot_id="base_snap",
            module_states={"module1": unchanged_module}
        )
        
        merged = snapshot_manager._merge_snapshots(base_snapshot, incremental_snapshot)
        
        assert merged.module_states["module1"].changed is False


@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotCompatibility:
    """测试版本兼容性检查"""
    
    def test_check_compatibility_p6_version(self, snapshot_manager):
        """测试兼容的p6版本"""
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version="p6.2.0"
        )
        
        result = snapshot_manager._check_compatibility(snapshot)
        
        assert result is True
    
    def test_check_compatibility_p6_1_version(self, snapshot_manager):
        """测试兼容的p6.1版本"""
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version="p6.1.0"
        )
        
        result = snapshot_manager._check_compatibility(snapshot)
        
        assert result is True
    
    def test_check_compatibility_incompatible_version(self, snapshot_manager):
        """测试不兼容的版本"""
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version="p5.0.0"
        )
        
        result = snapshot_manager._check_compatibility(snapshot)
        
        assert result is False


@pytest.mark.p1
@pytest.mark.unit
class TestModuleSerialization:
    """测试模块序列化方法"""
    
    def test_serialize_body_sensor(self, snapshot_manager):
        """测试 BodySensor 序列化"""
        body = MockBody()
        
        state = snapshot_manager._serialize_body_sensor(body)
        
        assert state["initialized"] is True
        assert "watch_dirs" in state
        assert "config" in state
    
    def test_serialize_body_sensor_uninitialized(self, snapshot_manager):
        """测试未初始化的 BodySensor 序列化"""
        body = MockBody()
        body.is_initialized = False
        body._initialized = False
        
        state = snapshot_manager._serialize_body_sensor(body)
        
        assert state["initialized"] is False
    
    def test_serialize_body_sensor_exception(self, snapshot_manager):
        """测试 BodySensor 序列化异常处理"""
        class FailingBody:
            is_initialized = True
            _initialized = True
            
            @property
            def watch_dirs(self):
                raise Exception("test error")
        
        body = FailingBody()
        
        state = snapshot_manager._serialize_body_sensor(body)
        
        assert "error" in state
    
    def test_serialize_behavior(self, snapshot_manager):
        """测试 BehaviorController 序列化"""
        behavior = MockBehavior()
        
        state = snapshot_manager._serialize_behavior(behavior)
        
        assert state["initialized"] is True
        assert state["mode"] == "NORMAL"
        assert "mode_history" in state
        assert "thresholds" in state
    
    def test_serialize_behavior_exception(self, snapshot_manager):
        """测试 BehaviorController 序列化异常处理"""
        behavior = Mock(side_effect=Exception("test error"))
        
        state = snapshot_manager._serialize_behavior(behavior)
        
        assert "error" in state
    
    def test_serialize_permission(self, snapshot_manager):
        """测试 PermissionSystem 序列化"""
        permission = MockPermission()
        
        state = snapshot_manager._serialize_permission(permission)
        
        assert state["initialized"] is True
        assert state["dangerous_patterns_count"] == 2
        assert state["blacklist_count"] == 1
        assert "sensitive_extensions" in state
    
    def test_serialize_permission_exception(self, snapshot_manager):
        """测试 PermissionSystem 序列化异常处理"""
        permission = Mock(side_effect=Exception("test error"))
        
        state = snapshot_manager._serialize_permission(permission)
        
        assert "error" in state
    
    def test_serialize_tools_registry(self, snapshot_manager):
        """测试 ToolsRegistry 序列化"""
        tools_registry = MockToolsRegistry()
        
        state = snapshot_manager._serialize_tools_registry(tools_registry)
        
        assert state["initialized"] is True
        assert state["tools_count"] == 2
        assert len(state["tools"]) == 2
    
    def test_serialize_tools_registry_empty(self, snapshot_manager):
        """测试空的 ToolsRegistry 序列化"""
        tools_registry = Mock()
        tools_registry._tools = {}
        
        state = snapshot_manager._serialize_tools_registry(tools_registry)
        
        assert state["tools_count"] == 0
    
    def test_serialize_tools_registry_exception(self, snapshot_manager):
        """测试 ToolsRegistry 序列化异常处理"""
        tools_registry = Mock(side_effect=Exception("test error"))
        
        state = snapshot_manager._serialize_tools_registry(tools_registry)
        
        assert "error" in state


@pytest.mark.p1
@pytest.mark.unit
class TestModuleRestore:
    """测试模块恢复方法"""
    
    def test_restore_body_sensor(self, snapshot_manager):
        """测试 BodySensor 恢复"""
        body = MockBody()
        
        state = {
            "initialized": True,
            "watch_dirs": ["/tmp/restored"],
            "config": {"timeout": 20}
        }
        
        result = snapshot_manager._restore_body_sensor(body, state)
        
        assert result is True
        assert body._initialized is True
    
    def test_restore_body_sensor_exception(self, snapshot_manager):
        """测试 BodySensor 恢复异常处理"""
        class FailingBody:
            @property
            def _initialized(self):
                raise Exception("test error")
        
        body = FailingBody()
        
        state = {"initialized": True}
        
        result = snapshot_manager._restore_body_sensor(body, state)
        
        assert result is False
    
    def test_restore_behavior(self, snapshot_manager):
        """测试 BehaviorController 恢复"""
        behavior = MockBehavior()
        
        state = {
            "initialized": True,
            "mode": "ACTIVE",
            "mode_history": ["NORMAL", "ACTIVE", "NORMAL"]
        }
        
        result = snapshot_manager._restore_behavior(behavior, state)
        
        assert result is True
    
    def test_restore_behavior_exception(self, snapshot_manager):
        """测试 BehaviorController 恢复异常处理"""
        class FailingBehavior:
            @property
            def _current_mode(self):
                raise Exception("test error")
        
        behavior = FailingBehavior()
        
        state = {"initialized": True, "mode": "NORMAL"}
        
        result = snapshot_manager._restore_behavior(behavior, state)
        
        assert result is False
    
    def test_restore_permission(self, snapshot_manager):
        """测试 PermissionSystem 恢复"""
        permission = MockPermission()
        
        state = {
            "initialized": True,
            "dangerous_patterns_count": 2,
            "blacklist_count": 1,
            "sensitive_extensions": [".key", ".pem"]
        }
        
        result = snapshot_manager._restore_permission(permission, state)
        
        assert result is True
    
    def test_restore_permission_exception(self, snapshot_manager):
        """测试 PermissionSystem 恢复异常处理"""
        class FailingPermission:
            @property
            def SENSITIVE_EXTENSIONS(self):
                raise Exception("test error")
        
        permission = FailingPermission()
        
        state = {"initialized": True, "sensitive_extensions": [".key"]}
        
        result = snapshot_manager._restore_permission(permission, state)
        
        assert result is False
    
    def test_restore_tools_registry(self, snapshot_manager):
        """测试 ToolsRegistry 恢复"""
        tools_registry = MockToolsRegistry()
        
        state = {
            "initialized": True,
            "tools_count": 3,
            "tools": ["tool1", "tool2", "tool3"]
        }
        
        result = snapshot_manager._restore_tools_registry(tools_registry, state)
        
        assert result is True
    
    def test_restore_tools_registry_exception(self, snapshot_manager):
        """测试 ToolsRegistry 恢复异常处理"""
        class FailingToolsRegistry:
            @property
            def _tools(self):
                raise Exception("test error")
        
        tools_registry = FailingToolsRegistry()
        
        state = {"initialized": True, "tools": [1, 2, 3]}  # 非字符串列表会触发异常
        
        result = snapshot_manager._restore_tools_registry(tools_registry, state)
        
        assert result is False


@pytest.mark.p1
@pytest.mark.unit
class TestRestoreModulesByPriority:
    """测试按优先级恢复模块"""
    
    def test_restore_modules_by_priority_basic(self, snapshot_manager):
        """测试基本的优先级恢复"""
        digital_life = MockDigitalLifeFull()
        
        module1_data = pickle.dumps({"key": "value1"})
        module2_data = pickle.dumps({"key": "value2"})
        
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={
                "body_sensor": ModuleState(
                    module_name="body_sensor",
                    initialized=True,
                    state_data=module1_data,
                    restore_priority=100,
                    checksum="checksum1"
                ),
                "behavior": ModuleState(
                    module_name="behavior",
                    initialized=True,
                    state_data=module2_data,
                    restore_priority=90,
                    checksum="checksum2"
                )
            }
        )
        
        result = snapshot_manager._restore_modules_by_priority(digital_life, snapshot)
        
        assert result is True
    
    def test_restore_modules_by_priority_uninitialized(self, snapshot_manager):
        """测试跳过未初始化模块"""
        digital_life = MockDigitalLifeFull()
        
        module_data = pickle.dumps({"key": "value"})
        
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={
                "body_sensor": ModuleState(
                    module_name="body_sensor",
                    initialized=False,
                    state_data=module_data,
                    restore_priority=100,
                    checksum="checksum"
                )
            }
        )
        
        result = snapshot_manager._restore_modules_by_priority(digital_life, snapshot)
        
        assert result is False
    
    def test_restore_modules_by_priority_checksum_mismatch(self, snapshot_manager):
        """测试校验和不匹配的情况"""
        digital_life = MockDigitalLifeFull()
        
        module_data = pickle.dumps({"key": "value"})
        
        snapshot = StateSnapshot(
            snapshot_id="test_snap",
            created_at=datetime.now(),
            version=SNAPSHOT_VERSION,
            module_states={
                "body_sensor": ModuleState(
                    module_name="body_sensor",
                    initialized=True,
                    state_data=module_data,
                    restore_priority=100,
                    checksum="wrong_checksum"
                )
            }
        )
        
        result = snapshot_manager._restore_modules_by_priority(digital_life, snapshot)
        
        assert result is True


@pytest.mark.p1
@pytest.mark.unit
class TestListSnapshots:
    """测试列出快照功能"""
    
    def test_list_snapshots_empty(self, snapshot_manager):
        """测试列出空目录"""
        snapshots = snapshot_manager.list_snapshots()
        
        assert isinstance(snapshots, list)
        assert len(snapshots) == 0
    
    def test_list_snapshots_with_multiple(self, temp_snapshot_dir):
        """测试列出多个快照"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
        mock_digital_life = MockDigitalLifeFull()
        
        result1 = manager.save_snapshot(mock_digital_life, snapshot_id="snap_test_1", force=True)
        result2 = manager.save_snapshot(mock_digital_life, snapshot_id="snap_test_2", force=True)
        
        snapshots = manager.list_snapshots()
        
        assert len(snapshots) == 2
        assert snapshots[0].snapshot_id == "snap_test_2"
        assert snapshots[1].snapshot_id == "snap_test_1"


@pytest.mark.p1
@pytest.mark.unit
class TestCleanupSnapshots:
    """测试清理快照功能"""
    
    def test_cleanup_snapshots_basic(self, temp_snapshot_dir):
        """测试基本清理功能"""
        manager = StateSnapshotManager(
            snapshot_dir=temp_snapshot_dir,
            enable_compression=False
        )
        # 设置允许保存更多快照
        manager.frequency_controller.max_snapshots = 10
        
        mock_digital_life = MockDigitalLifeFull()
        
        for i in range(7):
            manager.save_snapshot(mock_digital_life, snapshot_id=f"snap_test_{i}", force=True)
        
        snapshots_before = manager.list_snapshots()
        assert len(snapshots_before) == 7
        
        deleted_count = manager.cleanup_snapshots(keep_count=5)
        
        snapshots_after = manager.list_snapshots()
        assert len(snapshots_after) == 5
        assert deleted_count >= 2


@pytest.mark.p1
@pytest.mark.unit
class TestSnapshotLoadFull:
    """测试完整快照加载流程"""
    
    def test_load_snapshot_with_digital_life_class(self, snapshot_manager):
        """测试使用 DigitalLife 类加载快照"""
        mock_digital_life = MockDigitalLifeFull()
        
        save_result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        
        loaded = snapshot_manager.load_snapshot(
            digital_life_class=MockDigitalLifeFull,
            snapshot_id=save_result.snapshot_id
        )
        
        assert loaded is not None
        assert isinstance(loaded, MockDigitalLifeFull)
    
    def test_load_snapshot_no_class(self, snapshot_manager):
        """测试不提供类时返回快照数据"""
        mock_digital_life = MockDigitalLifeFull()
        
        save_result = snapshot_manager.save_snapshot(mock_digital_life, force=True)
        
        loaded = snapshot_manager.load_snapshot(
            digital_life_class=None,
            snapshot_id=save_result.snapshot_id
        )
        
        assert loaded is not None
        assert isinstance(loaded, StateSnapshot)
        assert loaded.snapshot_id == save_result.snapshot_id


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
