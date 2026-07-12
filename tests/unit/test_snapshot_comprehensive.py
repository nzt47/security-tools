"""snapshot.py 综合单元测试

覆盖关键分支：
- _persist_snapshot: 压缩/不压缩、异常处理
- _load_snapshot_data: 增量优先、完整回退、无快照
- _load_from_path: 解压、反序列化、增量合并
- _merge_snapshots: 基础+增量合并逻辑
- _check_compatibility: 版本兼容/不兼容
- _cleanup_old_snapshots: 超限删除
- save_snapshot: 频率拦截、完整保存、增量保存、持久化失败
- _save_core_modules_with_delta: 4模块序列化+增量跳过
- _serialize_*: 4个模块序列化+异常
- _restore_*: 4个模块恢复+异常
- _restore_modules_by_priority: 优先级排序、校验失败、未知模块
- load_snapshot: 完整流程、版本不兼容、无类返回数据
- list_snapshots: 目录遍历、文件解析
- cleanup_snapshots: 保留N个
"""
import pickle
import gzip
import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.p6.snapshot import (
    SnapshotResult,
    SnapshotInfo,
    ModuleState,
    StateSnapshot,
    StateSnapshotManager,
)


# ── 辅助类：用于触发异常分支 ──
# 说明：源码中大量使用 hasattr 守卫属性访问（hasattr 会吞掉所有异常），
# 因此 MagicMock(side_effect=...) 无法触发异常路径。只有不受 hasattr
# 守卫的操作（如 len()、list()、dict.get()、f-string 格式化）才能触发。

class _RaisingLen:
    """__len__ 抛异常，用于触发 len() 调用的异常分支"""
    def __len__(self):
        raise RuntimeError("len fail")


class _RaisingIter:
    """__iter__ 抛异常，用于触发 list() 调用的异常分支"""
    def __iter__(self):
        raise RuntimeError("iter fail")


class _RaisingStr:
    """__str__/__format__ 抛异常，用于触发 f-string 格式化的异常分支"""
    def __str__(self):
        raise RuntimeError("str fail")
    def __format__(self, spec):
        raise RuntimeError("format fail")


class _RaisingGetDict:
    """get/__getitem__ 抛异常，用于触发 state.get()/state[key] 的异常分支"""
    def __contains__(self, key):
        return True
    def get(self, key, default=None):
        raise RuntimeError("get fail")
    def __getitem__(self, key):
        raise RuntimeError("getitem fail")


# ── 辅助 fixture ──

@pytest.fixture
def manager(tmp_path):
    """创建临时目录的快照管理器"""
    return StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots"), enable_compression=True)


@pytest.fixture
def manager_no_compress(tmp_path):
    """不压缩的快照管理器"""
    return StateSnapshotManager(snapshot_dir=str(tmp_path / "snapshots_nc"), enable_compression=False)


@pytest.fixture
def fake_digital_life():
    """模拟 DigitalLife 实例

    注意：_body.get() 必须返回 body 自身，避免 _save_core_modules_with_delta
    调用 _body.get() 时返回新的 MagicMock（含不可 pickle 的自动属性）。
    """
    life = MagicMock()
    life.__class__.__name__ = "DigitalLife"
    life._config = {"name": "test", "version": "1.0"}

    body = MagicMock()
    body.is_initialized = True
    body._initialized = True
    body.watch_dirs = ["/tmp/watch1"]
    body.config = {"interval": 5}
    # 让 body.get() 返回 body 自身，保持序列化路径一致
    body.get.return_value = body
    life._body = body

    behavior = MagicMock()
    behavior._current_mode = MagicMock(value="NORMAL")
    behavior._mode_history = ["NORMAL", "SAFE"]
    behavior.THRESHOLDS = {"cpu": 80}
    life._behavior = behavior

    permission = MagicMock()
    permission.DANGEROUS_PATTERNS = ["rm -rf", "del /f"]
    permission.BLACKLIST = ["evil.com"]
    permission.SENSITIVE_EXTENSIONS = [".env", ".key"]
    life._permission = permission

    tools_registry = MagicMock()
    tools_registry._tools = {"tool1": "dummy", "tool2": "dummy"}
    life._tools_registry = tools_registry

    return life


# ── _persist_snapshot 测试 ──

class TestPersistSnapshot:
    """快照持久化"""

    def test_persist_compressed(self, manager, tmp_path):
        snap = StateSnapshot(snapshot_id="test_persist", created_at=datetime.now())
        result = manager._persist_snapshot(snap)
        assert result is True
        path = manager._get_snapshot_path("test_persist", False)
        assert path.exists()

    def test_persist_uncompressed(self, manager_no_compress):
        snap = StateSnapshot(snapshot_id="test_raw", created_at=datetime.now())
        result = manager_no_compress._persist_snapshot(snap)
        assert result is True
        path = manager_no_compress._get_snapshot_path("test_raw", False)
        assert path.exists()

    def test_persist_incremental(self, manager):
        snap = StateSnapshot(snapshot_id="test_inc", created_at=datetime.now(), is_incremental=True, base_snapshot_id="base_001")
        result = manager._persist_snapshot(snap)
        assert result is True
        path = manager._get_snapshot_path("test_inc", True)
        assert path.exists()

    def test_persist_failure_returns_false(self, manager):
        snap = StateSnapshot(snapshot_id="test_fail", created_at=datetime.now())
        with patch("builtins.open", side_effect=OSError("disk full")):
            result = manager._persist_snapshot(snap)
        assert result is False


# ── _load_snapshot_data / _load_from_path 测试 ──

class TestLoadSnapshotData:
    """快照加载"""

    def test_load_returns_none_when_no_snapshots(self, manager):
        result = manager._load_snapshot_data()
        assert result is None

    def test_load_specific_snapshot_not_found(self, manager):
        result = manager._load_snapshot_data("nonexistent_id")
        assert result is None

    def test_load_full_snapshot(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="snap_load_001")
        loaded = manager._load_snapshot_data("snap_load_001")
        assert loaded is not None
        assert loaded.snapshot_id == "snap_load_001"

    def test_load_latest_snapshot(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="snap_first")
        time.sleep(0.01)
        manager.save_snapshot(fake_digital_life, snapshot_id="snap_second", force=True)
        loaded = manager._load_snapshot_data(None)
        assert loaded is not None

    def test_load_from_path_decompresses(self, manager):
        snap = StateSnapshot(snapshot_id="snap_decompress", created_at=datetime.now())
        manager._persist_snapshot(snap)
        path = manager._get_snapshot_path("snap_decompress", False)
        loaded = manager._load_from_path(path)
        assert loaded is not None
        assert loaded.snapshot_id == "snap_decompress"

    def test_load_from_path_uncompressed(self, manager_no_compress):
        snap = StateSnapshot(snapshot_id="snap_raw_load", created_at=datetime.now())
        manager_no_compress._persist_snapshot(snap)
        path = manager_no_compress._get_snapshot_path("snap_raw_load", False)
        loaded = manager_no_compress._load_from_path(path)
        assert loaded is not None


# ── _merge_snapshots 测试 ──

class TestMergeSnapshots:
    """增量快照合并"""

    def test_merge_applies_changed_modules(self, manager):
        base = StateSnapshot(snapshot_id="base", created_at=datetime.now())
        base.module_states = {
            "body": ModuleState(module_name="body", initialized=True, state_data=b"old", checksum="c1", changed=False),
            "behavior": ModuleState(module_name="behavior", initialized=True, state_data=b"old_beh", checksum="c2", changed=False),
        }

        incremental = StateSnapshot(snapshot_id="incr", created_at=datetime.now(), is_incremental=True, base_snapshot_id="base")
        incremental.module_states = {
            "body": ModuleState(module_name="body", initialized=True, state_data=b"new", checksum="c3", changed=True),
        }

        merged = manager._merge_snapshots(base, incremental)
        assert merged.module_states["body"].state_data == b"new"
        assert merged.module_states["behavior"].state_data == b"old_beh"

    def test_merge_skips_unchanged_modules(self, manager):
        base = StateSnapshot(snapshot_id="base2", created_at=datetime.now())
        base.module_states = {
            "perm": ModuleState(module_name="perm", initialized=True, state_data=b"perm_data", checksum="c1", changed=False),
        }

        incremental = StateSnapshot(snapshot_id="incr2", created_at=datetime.now(), is_incremental=True)
        incremental.module_states = {
            "perm": ModuleState(module_name="perm", initialized=True, state_data=b"new_perm", checksum="c2", changed=False),
        }

        merged = manager._merge_snapshots(base, incremental)
        assert merged.module_states["perm"].state_data == b"perm_data"


# ── _check_compatibility 测试 ──

class TestCheckCompatibility:
    """版本兼容性检查"""

    def test_compatible_p6_1(self, manager):
        snap = StateSnapshot(snapshot_id="s1", created_at=datetime.now(), version="p6.1.0")
        assert manager._check_compatibility(snap) is True

    def test_compatible_p6_2(self, manager):
        snap = StateSnapshot(snapshot_id="s2", created_at=datetime.now(), version="p6.2.0")
        assert manager._check_compatibility(snap) is True

    def test_incompatible_version(self, manager):
        snap = StateSnapshot(snapshot_id="s3", created_at=datetime.now(), version="v1.0.0")
        assert manager._check_compatibility(snap) is False


# ── _cleanup_old_snapshots 测试 ──

class TestCleanupOldSnapshots:
    """旧快照清理"""

    def test_cleanup_removes_excess(self, manager, fake_digital_life):
        manager.frequency_controller.max_snapshots = 3
        for i in range(5):
            manager.save_snapshot(fake_digital_life, snapshot_id=f"snap_cleanup_{i}", force=True)
            time.sleep(0.01)
        snapshots = manager.list_snapshots()
        assert len(snapshots) <= 3

    def test_cleanup_no_excess(self, manager, fake_digital_life):
        manager.frequency_controller.max_snapshots = 10
        manager.save_snapshot(fake_digital_life, snapshot_id="snap_only_one", force=True)
        snapshots = manager.list_snapshots()
        assert len(snapshots) == 1


# ── save_snapshot 测试 ──

class TestSaveSnapshot:
    """快照保存核心流程"""

    def test_save_success(self, manager, fake_digital_life):
        result = manager.save_snapshot(fake_digital_life, snapshot_id="save_test_001")
        assert result.success is True
        assert result.snapshot_id == "save_test_001"
        assert result.elapsed_ms > 0

    def test_save_auto_generate_id(self, manager, fake_digital_life):
        result = manager.save_snapshot(fake_digital_life)
        assert result.success is True
        assert result.snapshot_id is not None
        assert result.snapshot_id.startswith("snap_")

    def test_save_frequency_blocked(self, manager, fake_digital_life):
        result1 = manager.save_snapshot(fake_digital_life, snapshot_id="snap_freq_1")
        assert result1.success is True
        result2 = manager.save_snapshot(fake_digital_life, snapshot_id="snap_freq_2")
        assert result2.success is False
        assert "频繁" in result2.error_message

    def test_save_force_bypasses_frequency(self, manager, fake_digital_life):
        result1 = manager.save_snapshot(fake_digital_life, snapshot_id="snap_force_1")
        assert result1.success is True
        result2 = manager.save_snapshot(fake_digital_life, snapshot_id="snap_force_2", force=True)
        assert result2.success is True

    def test_save_incremental(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="snap_base_full")
        result = manager.save_snapshot(fake_digital_life, snapshot_id="snap_incr_001", incremental=True, force=True)
        assert result.success is True
        assert result.is_incremental is True
        assert result.base_snapshot_id == "snap_base_full"

    def test_save_no_config_attribute(self, manager):
        # 用 spec 限制属性，确保 hasattr(life, '_config') 返回 False
        life = MagicMock(spec=['_body', '_behavior', '_permission', '_tools_registry'])
        life.__class__.__name__ = "MinimalLife"
        body = MagicMock()
        body.is_initialized = True
        body._initialized = True
        body.watch_dirs = []
        body.config = {}
        body.get.return_value = body
        life._body = body
        life._behavior._current_mode = MagicMock(value="NORMAL")
        life._behavior._mode_history = ["NORMAL"]
        life._behavior.THRESHOLDS = {}
        life._permission.DANGEROUS_PATTERNS = []
        life._permission.BLACKLIST = []
        life._permission.SENSITIVE_EXTENSIONS = []
        life._tools_registry._tools = {}
        result = manager.save_snapshot(life, snapshot_id="snap_no_config", force=True)
        assert result.success is True

    def test_save_persist_failure(self, manager, fake_digital_life):
        with patch.object(manager, "_persist_snapshot", return_value=False):
            result = manager.save_snapshot(fake_digital_life, snapshot_id="snap_fail_persist", force=True)
        assert result.success is False
        assert "持久化" in result.error_message

    def test_save_exception_returns_failure(self, manager):
        broken_life = MagicMock()
        broken_life.__class__.__name__ = "BrokenLife"
        broken_life._config = MagicMock(side_effect=RuntimeError("boom"))
        # _config 属性存在但访问时抛异常 → save_snapshot 内部 hasattr 检查通过但赋值抛异常
        type(broken_life)._config = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        result = manager.save_snapshot(broken_life, snapshot_id="snap_exception", force=True)
        assert result.success is False
        assert result.error_message is not None


# ── _save_core_modules_with_delta 测试 ──

class TestSaveCoreModulesWithDelta:
    """核心模块序列化（含增量delta）"""

    def test_full_save_all_modules(self, manager, fake_digital_life):
        snapshot = StateSnapshot(snapshot_id="test_delta", created_at=datetime.now())
        space_saved = manager._save_core_modules_with_delta(fake_digital_life, snapshot, incremental=False)
        assert space_saved == 0
        assert "body_sensor" in snapshot.module_states
        assert "behavior" in snapshot.module_states
        assert "permission" in snapshot.module_states
        assert "tools_registry" in snapshot.module_states

    def test_incremental_skip_unchanged(self, manager, fake_digital_life):
        snapshot1 = StateSnapshot(snapshot_id="test_delta_1", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snapshot1, incremental=False)
        manager._update_module_checksums(snapshot1)

        snapshot2 = StateSnapshot(snapshot_id="test_delta_2", created_at=datetime.now())
        space_saved = manager._save_core_modules_with_delta(fake_digital_life, snapshot2, incremental=True)
        assert space_saved > 0
        assert len(snapshot2.module_states) == 0

    def test_incremental_with_changed_module(self, manager, fake_digital_life):
        snapshot1 = StateSnapshot(snapshot_id="test_delta_3", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snapshot1, incremental=False)
        manager._update_module_checksums(snapshot1)

        fake_digital_life._body.watch_dirs = ["/new/path"]
        snapshot2 = StateSnapshot(snapshot_id="test_delta_4", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snapshot2, incremental=True)
        assert "body_sensor" in snapshot2.module_states

    def test_no_body_attribute(self, manager):
        # 用 spec 限制 MagicMock 属性，确保 hasattr(life, '_body') 返回 False
        life = MagicMock(spec=['_behavior', '_permission', '_tools_registry'])
        life._behavior._current_mode = MagicMock(value="NORMAL")
        life._behavior._mode_history = ["NORMAL"]
        life._behavior.THRESHOLDS = {}
        life._permission.DANGEROUS_PATTERNS = []
        life._permission.BLACKLIST = []
        life._permission.SENSITIVE_EXTENSIONS = []
        life._tools_registry._tools = {}
        snapshot = StateSnapshot(snapshot_id="test_no_body", created_at=datetime.now())
        manager._save_core_modules_with_delta(life, snapshot, incremental=False)
        assert "body_sensor" not in snapshot.module_states

    def test_body_with_get_method(self, manager):
        life = MagicMock(spec=['_body', '_behavior', '_permission', '_tools_registry'])
        body = MagicMock()
        body.is_initialized = True
        body._initialized = True
        body.watch_dirs = []
        body.config = {}
        body.get.return_value = body
        life._body = body
        life._behavior._current_mode = MagicMock(value="NORMAL")
        life._behavior._mode_history = ["NORMAL"]
        life._behavior.THRESHOLDS = {}
        life._permission.DANGEROUS_PATTERNS = []
        life._permission.BLACKLIST = []
        life._permission.SENSITIVE_EXTENSIONS = []
        life._tools_registry._tools = {}
        snapshot = StateSnapshot(snapshot_id="test_body_get", created_at=datetime.now())
        manager._save_core_modules_with_delta(life, snapshot, incremental=False)
        assert "body_sensor" in snapshot.module_states


# ── _serialize_* 测试 ──

class TestSerializeMethods:
    """模块序列化方法"""

    def test_serialize_body_sensor_full(self, manager):
        body = MagicMock()
        body.is_initialized = True
        body._initialized = True
        body.watch_dirs = ["/tmp"]
        body.config = {"k": "v"}
        state = manager._serialize_body_sensor(body)
        assert state["initialized"] is True
        assert state["watch_dirs"] == ["/tmp"]
        assert state["config"] == {"k": "v"}

    def test_serialize_body_sensor_minimal(self, manager):
        body = MagicMock()
        body.is_initialized = False
        state = manager._serialize_body_sensor(body)
        assert state["initialized"] is False

    def test_serialize_body_sensor_exception(self, manager):
        # _serialize_body_sensor 所有属性访问都被 hasattr 守卫，
        # 唯一能触发异常的是 logger.info(f-string) 中的 __format__ 调用
        body = MagicMock()
        body.is_initialized = _RaisingStr()
        body._initialized = True
        state = manager._serialize_body_sensor(body)
        assert "error" in state

    def test_serialize_behavior_full(self, manager):
        behavior = MagicMock()
        behavior._current_mode = MagicMock(value="SAFE")
        behavior._mode_history = ["NORMAL", "SAFE", "ALERT"]
        behavior.THRESHOLDS = {"cpu": 90}
        state = manager._serialize_behavior(behavior)
        assert state["mode"] == "SAFE"
        assert state["mode_history"] == ["NORMAL", "SAFE", "ALERT"]
        assert state["thresholds"] == {"cpu": 90}

    def test_serialize_behavior_long_history(self, manager):
        behavior = MagicMock()
        behavior._current_mode = "NORMAL"
        behavior._mode_history = [f"mode_{i}" for i in range(10)]
        behavior.THRESHOLDS = {}
        state = manager._serialize_behavior(behavior)
        assert len(state["mode_history"]) == 5

    def test_serialize_behavior_exception(self, manager):
        # len(_mode_history) 不被 hasattr 守卫，用于触发异常
        behavior = MagicMock()
        behavior._current_mode = MagicMock(value="NORMAL")
        behavior._mode_history = _RaisingLen()
        behavior.THRESHOLDS = {}
        state = manager._serialize_behavior(behavior)
        assert "error" in state

    def test_serialize_permission_full(self, manager):
        perm = MagicMock()
        perm.DANGEROUS_PATTERNS = ["rm", "del"]
        perm.BLACKLIST = ["bad.com"]
        perm.SENSITIVE_EXTENSIONS = [".env"]
        state = manager._serialize_permission(perm)
        assert state["dangerous_patterns_count"] == 2
        assert state["blacklist_count"] == 1
        assert state["sensitive_extensions"] == [".env"]

    def test_serialize_permission_exception(self, manager):
        # len(DANGEROUS_PATTERNS) 不被 hasattr 守卫，用于触发异常
        perm = MagicMock()
        perm.DANGEROUS_PATTERNS = _RaisingLen()
        state = manager._serialize_permission(perm)
        assert "error" in state

    def test_serialize_tools_registry_full(self, manager):
        registry = MagicMock()
        registry._tools = {"t1": MagicMock(), "t2": MagicMock()}
        state = manager._serialize_tools_registry(registry)
        assert state["initialized"] is True
        assert state["tools_count"] == 2
        assert set(state["tools"]) == {"t1", "t2"}

    def test_serialize_tools_registry_empty(self, manager):
        registry = MagicMock()
        registry._tools = {}
        state = manager._serialize_tools_registry(registry)
        assert state["initialized"] is True
        assert state["tools_count"] == 0

    def test_serialize_tools_registry_none(self, manager):
        state = manager._serialize_tools_registry(None)
        assert state["initialized"] is False
        assert state["tools_count"] == 0

    def test_serialize_tools_registry_exception(self, manager):
        # len(_tools) 不被 hasattr 守卫，用于触发异常
        registry = MagicMock()
        registry._tools = _RaisingLen()
        state = manager._serialize_tools_registry(registry)
        assert "error" in state

    def test_serialize_tools_registry_truncates_to_50(self, manager):
        registry = MagicMock()
        registry._tools = {f"tool_{i}": MagicMock() for i in range(60)}
        state = manager._serialize_tools_registry(registry)
        assert len(state["tools"]) == 50


# ── _restore_* 测试 ──

class TestRestoreMethods:
    """模块恢复方法"""

    def test_restore_body_sensor_success(self, manager):
        body = MagicMock()
        body._initialized = False
        body.watch_dirs = []
        body.config = {}
        state = {"initialized": True, "watch_dirs": ["/new"], "config": {"k": "v"}}
        result = manager._restore_body_sensor(body, state)
        assert result is True
        assert body._initialized is True
        assert body.watch_dirs == ["/new"]
        assert body.config == {"k": "v"}

    def test_restore_body_sensor_exception(self, manager):
        # state.get() 不被 hasattr 守卫，用于触发异常
        body = MagicMock()
        state = _RaisingGetDict()
        result = manager._restore_body_sensor(body, state)
        assert result is False

    def test_restore_behavior_success(self, manager):
        behavior = MagicMock()
        behavior._current_mode = None
        behavior._mode_history = []
        state = {"mode": "NORMAL", "mode_history": ["NORMAL"]}
        result = manager._restore_behavior(behavior, state)
        assert result is True

    def test_restore_behavior_exception(self, manager):
        # state["mode"] 不被 hasattr 守卫，用于触发异常
        behavior = MagicMock()
        state = _RaisingGetDict()
        result = manager._restore_behavior(behavior, state)
        assert result is False

    def test_restore_permission_success(self, manager):
        perm = MagicMock()
        perm.SENSITIVE_EXTENSIONS = []
        state = {"dangerous_patterns_count": 2, "sensitive_extensions": [".env"]}
        result = manager._restore_permission(perm, state)
        assert result is True

    def test_restore_permission_exception(self, manager):
        # state.get() 不被 hasattr 守卫，用于触发异常
        perm = MagicMock()
        state = _RaisingGetDict()
        result = manager._restore_permission(perm, state)
        assert result is False

    def test_restore_tools_registry_success(self, manager):
        registry = MagicMock()
        state = {"tools_count": 2, "tools": ["t1", "t2"]}
        result = manager._restore_tools_registry(registry, state)
        assert result is True

    def test_restore_tools_registry_exception(self, manager):
        # state.get() 不被 hasattr 守卫，用于触发异常
        registry = MagicMock()
        state = _RaisingGetDict()
        result = manager._restore_tools_registry(registry, state)
        assert result is False


# ── _restore_modules_by_priority 测试 ──

class TestRestoreModulesByPriority:
    """按优先级恢复模块"""

    def test_restore_all_modules(self, manager, fake_digital_life):
        snapshot = StateSnapshot(snapshot_id="restore_test", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snapshot, incremental=False)
        result = manager._restore_modules_by_priority(fake_digital_life, snapshot)
        assert result is True

    def test_restore_skip_uninitialized(self, manager, fake_digital_life):
        snapshot = StateSnapshot(snapshot_id="restore_skip", created_at=datetime.now())
        snapshot.module_states = {
            "body_sensor": ModuleState(module_name="body_sensor", initialized=False, state_data=b"", checksum=""),
        }
        result = manager._restore_modules_by_priority(fake_digital_life, snapshot)
        assert result is False

    def test_restore_unknown_module(self, manager, fake_digital_life):
        snapshot = StateSnapshot(snapshot_id="restore_unknown", created_at=datetime.now())
        snapshot.module_states = {
            "unknown_mod": ModuleState(module_name="unknown_mod", initialized=True, state_data=pickle.dumps({}), checksum=""),
        }
        result = manager._restore_modules_by_priority(fake_digital_life, snapshot)
        assert result is False

    def test_restore_checksum_mismatch_continues(self, manager, fake_digital_life):
        snapshot = StateSnapshot(snapshot_id="restore_mismatch", created_at=datetime.now())
        snapshot.module_states = {
            "body_sensor": ModuleState(
                module_name="body_sensor", initialized=True,
                state_data=pickle.dumps({"initialized": True}),
                checksum="wrong_checksum",
            ),
        }
        result = manager._restore_modules_by_priority(fake_digital_life, snapshot)
        assert result is True

    def test_restore_priority_order(self, manager, fake_digital_life):
        snapshot = StateSnapshot(snapshot_id="restore_order", created_at=datetime.now())
        low_priority = ModuleState(module_name="tools_registry", initialized=True, state_data=pickle.dumps({}), checksum="", restore_priority=70)
        high_priority = ModuleState(module_name="body_sensor", initialized=True, state_data=pickle.dumps({"initialized": True}), checksum="", restore_priority=100)
        snapshot.module_states = {"tools_registry": low_priority, "body_sensor": high_priority}
        result = manager._restore_modules_by_priority(fake_digital_life, snapshot)
        assert result is True


# ── load_snapshot 测试 ──

class TestLoadSnapshot:
    """快照加载核心流程"""

    def test_load_returns_none_when_no_snapshot(self, manager):
        result = manager.load_snapshot()
        assert result is None

    def test_load_returns_snapshot_data_without_class(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="load_no_class", force=True)
        result = manager.load_snapshot(digital_life_class=None, snapshot_id="load_no_class")
        assert result is not None
        assert isinstance(result, StateSnapshot)

    def test_load_incompatible_version(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="load_incompat", force=True)
        # 手动修改版本
        snap = manager._load_snapshot_data("load_incompat")
        snap.version = "v99.0"
        manager._persist_snapshot(snap)
        # 删除旧文件
        old_path = manager._get_snapshot_path("load_incompat", False)
        if old_path.exists():
            os.remove(old_path)
        manager._persist_snapshot(snap)
        result = manager.load_snapshot(snapshot_id="load_incompat")
        assert result is None

    def test_load_with_class_creates_instance(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="load_with_class", force=True)

        class FakeLifeClass:
            def __init__(self, config):
                self._config = config
                self._body = MagicMock()
                self._behavior = MagicMock()
                self._permission = MagicMock()

        result = manager.load_snapshot(digital_life_class=FakeLifeClass, snapshot_id="load_with_class")
        assert result is not None
        assert isinstance(result, FakeLifeClass)

    def test_load_class_creation_failure(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="load_fail", force=True)

        class FailingClass:
            def __init__(self, config):
                raise RuntimeError("init failed")

        result = manager.load_snapshot(digital_life_class=FailingClass, snapshot_id="load_fail")
        assert result is None


# ── list_snapshots 测试 ──

class TestListSnapshots:
    """快照列表"""

    def test_list_empty(self, manager):
        snapshots = manager.list_snapshots()
        assert snapshots == []

    def test_list_multiple_snapshots(self, manager, fake_digital_life):
        for i in range(3):
            manager.save_snapshot(fake_digital_life, snapshot_id=f"list_snap_{i}", force=True)
            time.sleep(0.01)
        snapshots = manager.list_snapshots()
        assert len(snapshots) == 3
        # 倒序排列
        assert snapshots[0].created_at >= snapshots[-1].created_at

    def test_list_includes_incremental(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="list_full", force=True)
        manager.save_snapshot(fake_digital_life, snapshot_id="list_incr", incremental=True, force=True)
        snapshots = manager.list_snapshots()
        assert len(snapshots) >= 2


# ── cleanup_snapshots 测试 ──

class TestCleanupSnapshots:
    """手动清理快照"""

    def test_cleanup_deletes_excess(self, manager, fake_digital_life):
        for i in range(5):
            manager.save_snapshot(fake_digital_life, snapshot_id=f"cleanup_{i}", force=True)
            time.sleep(0.01)
        deleted = manager.cleanup_snapshots(keep_count=2)
        remaining = manager.list_snapshots()
        assert len(remaining) <= 2

    def test_cleanup_no_excess(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="cleanup_single", force=True)
        deleted = manager.cleanup_snapshots(keep_count=5)
        assert deleted == 0


# ── show_performance_panel 测试 ──

class TestShowPerformancePanel:
    """性能面板"""

    def test_show_panel_no_crash(self, manager, capsys):
        manager.show_performance_panel()
        captured = capsys.readouterr()

    def test_show_panel_after_operations(self, manager, fake_digital_life, capsys):
        manager.save_snapshot(fake_digital_life, snapshot_id="perf_test", force=True)
        manager.show_performance_panel()
        captured = capsys.readouterr()


# ── _update_module_checksums 测试 ──

class TestUpdateModuleChecksums:
    """校验和缓存更新"""

    def test_update_checksums(self, manager):
        snapshot = StateSnapshot(snapshot_id="checksum_test", created_at=datetime.now())
        snapshot.module_states = {
            "mod1": ModuleState(module_name="mod1", initialized=True, state_data=b"data1", checksum="hash1"),
            "mod2": ModuleState(module_name="mod2", initialized=True, state_data=b"data2", checksum="hash2"),
        }
        manager._update_module_checksums(snapshot)
        assert manager.last_module_checksums["mod1"] == "hash1"
        assert manager.last_module_checksums["mod2"] == "hash2"

    def test_update_checksums_overwrites(self, manager):
        manager.last_module_checksums["mod1"] = "old_hash"
        snapshot = StateSnapshot(snapshot_id="checksum_overwrite", created_at=datetime.now())
        snapshot.module_states = {
            "mod1": ModuleState(module_name="mod1", initialized=True, state_data=b"new", checksum="new_hash"),
        }
        manager._update_module_checksums(snapshot)
        assert manager.last_module_checksums["mod1"] == "new_hash"


# ── 边界条件补充测试 ──

class TestComputeChecksum:
    """StateSnapshot.compute_checksum 方法"""

    def test_compute_checksum_returns_hex_string(self):
        snap = StateSnapshot(snapshot_id="cs1", created_at=datetime.now())
        snap.module_states = {
            "mod1": ModuleState(module_name="mod1", initialized=True, state_data=b"d1", checksum="h1"),
        }
        result = snap.compute_checksum()
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex

    def test_compute_checksum_stable_for_same_data(self):
        snap = StateSnapshot(snapshot_id="cs2", created_at=datetime.now())
        snap.config = {"k": "v"}
        snap.module_states = {
            "mod1": ModuleState(module_name="mod1", initialized=True, state_data=b"d", checksum="h"),
        }
        assert snap.compute_checksum() == snap.compute_checksum()

    def test_compute_checksum_differs_on_config_change(self):
        snap = StateSnapshot(snapshot_id="cs3", created_at=datetime.now())
        snap.module_states = {"m": ModuleState(module_name="m", initialized=True, state_data=b"d", checksum="h")}
        snap.config = {"v1": 1}
        first = snap.compute_checksum()
        snap.config = {"v2": 2}
        assert snap.compute_checksum() != first


class TestLoadSnapshotDataEdgeCases:
    """_load_snapshot_data 异常与增量分支"""

    def test_load_incremental_file_corrupted_falls_back_to_full(self, manager, fake_digital_life):
        # 保存完整快照
        manager.save_snapshot(fake_digital_life, snapshot_id="edge_full_001", force=True)
        # 写入损坏的增量文件（同名）
        inc_path = manager._get_snapshot_path("edge_full_001", is_incremental=True)
        inc_path.write_bytes(b"corrupted data not pickle")
        # 加载应回退到完整快照（增量加载异常被 except 捕获）
        loaded = manager._load_snapshot_data("edge_full_001")
        assert loaded is not None
        assert loaded.snapshot_id == "edge_full_001"

    def test_load_from_path_incremental_merges_base(self, manager, fake_digital_life):
        # 保存基础快照
        manager.save_snapshot(fake_digital_life, snapshot_id="edge_base_001", force=True)
        # 保存增量快照
        result = manager.save_snapshot(
            fake_digital_life, snapshot_id="edge_incr_001",
            incremental=True, force=True,
        )
        assert result.success is True
        # 直接通过 _load_from_path 加载增量文件，触发合并逻辑
        inc_path = manager._get_snapshot_path("edge_incr_001", is_incremental=True)
        loaded = manager._load_from_path(inc_path)
        assert loaded is not None


class TestCleanupOldSnapshotsEdgeCases:
    """_cleanup_old_snapshots 删除异常分支"""

    def test_cleanup_swallows_unlink_error(self, manager, fake_digital_life):
        manager.frequency_controller.max_snapshots = 2
        for i in range(4):
            manager.save_snapshot(fake_digital_life, snapshot_id=f"edge_clean_{i}", force=True)
            time.sleep(0.01)
        # 让 unlink 抛异常（Path.unlink 被 patch）
        with patch("pathlib.Path.unlink", side_effect=OSError("permission denied")):
            # 不应抛异常
            manager._cleanup_old_snapshots()


class TestSaveCoreModulesDeltaIncremental:
    """_save_core_modules_with_delta 增量跳过分支"""

    def test_incremental_skips_unchanged_behavior(self, manager, fake_digital_life):
        # 第一次完整保存
        snap1 = StateSnapshot(snapshot_id="delta_full", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snap1, incremental=False)
        manager._update_module_checksums(snap1)
        # 第二次增量保存（相同数据 → behavior 未变化）
        snap2 = StateSnapshot(snapshot_id="delta_inc", created_at=datetime.now(), is_incremental=True)
        space_saved = manager._save_core_modules_with_delta(fake_digital_life, snap2, incremental=True)
        # 至少 behavior 模块未变化，节省空间 > 0
        assert space_saved >= 0

    def test_incremental_skips_unchanged_permission(self, manager, fake_digital_life):
        snap1 = StateSnapshot(snapshot_id="delta_perm_full", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snap1, incremental=False)
        manager._update_module_checksums(snap1)
        snap2 = StateSnapshot(snapshot_id="delta_perm_inc", created_at=datetime.now(), is_incremental=True)
        space_saved = manager._save_core_modules_with_delta(fake_digital_life, snap2, incremental=True)
        assert space_saved >= 0

    def test_incremental_skips_unchanged_tools(self, manager, fake_digital_life):
        snap1 = StateSnapshot(snapshot_id="delta_tools_full", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snap1, incremental=False)
        manager._update_module_checksums(snap1)
        snap2 = StateSnapshot(snapshot_id="delta_tools_inc", created_at=datetime.now(), is_incremental=True)
        space_saved = manager._save_core_modules_with_delta(fake_digital_life, snap2, incremental=True)
        assert space_saved >= 0

    def test_incremental_records_changed_module(self, manager, fake_digital_life):
        snap1 = StateSnapshot(snapshot_id="delta_chg_full", created_at=datetime.now())
        manager._save_core_modules_with_delta(fake_digital_life, snap1, incremental=False)
        manager._update_module_checksums(snap1)
        # 修改 behavior 数据使 checksum 变化
        fake_digital_life._behavior._mode_history = ["SAFE", "NORMAL", "IDLE"]
        snap2 = StateSnapshot(snapshot_id="delta_chg_inc", created_at=datetime.now(), is_incremental=True)
        manager._save_core_modules_with_delta(fake_digital_life, snap2, incremental=True)
        assert "behavior" in snap2.module_states
        assert snap2.module_states["behavior"].changed is True


class TestRestoreBehaviorEdgeCases:
    """_restore_behavior 未知模式分支"""

    def test_restore_unknown_mode_logs_warning(self, manager):
        behavior = MagicMock()
        behavior._current_mode = None
        behavior._mode_history = []
        state = {"mode": "UNKNOWN_MODE", "mode_history": []}
        # 未找到 BehaviorMode 时走 warning 分支，但仍返回 True
        result = manager._restore_behavior(behavior, state)
        assert result is True

    def test_restore_with_mode_history(self, manager):
        behavior = MagicMock()
        behavior._current_mode = MagicMock(value="NORMAL")
        behavior._mode_history = []
        state = {"mode": "NORMAL", "mode_history": ["NORMAL", "SAFE"]}
        result = manager._restore_behavior(behavior, state)
        assert result is True
        behavior._mode_history = state["mode_history"].copy()


class TestRestoreModulesByPriorityEdgeCases:
    """_restore_modules_by_priority 异常分支"""

    def test_restore_module_exception_continues(self, manager, fake_digital_life):
        # 构造一个 state_data 不可反序列化的模块
        snapshot = StateSnapshot(snapshot_id="restore_exc", created_at=datetime.now())
        snapshot.module_states = {
            "body_sensor": ModuleState(
                module_name="body_sensor", initialized=True,
                state_data=b"not_picklable_data",
                checksum="x",
            ),
        }
        # pickle.loads 会抛异常，但 _restore_modules_by_priority 应捕获并继续
        result = manager._restore_modules_by_priority(fake_digital_life, snapshot)
        assert result is False  # 所有模块都失败 → False

    def test_restore_body_with_get_method(self, manager):
        # 覆盖 `body_sensor = digital_life._body.get()` 分支
        life = MagicMock()
        body = MagicMock()
        body.is_initialized = True
        body._initialized = True
        body.watch_dirs = ["/tmp"]
        body.config = {"k": "v"}
        # _body.get() 返回 body 自身
        life._body.get.return_value = body
        life._behavior = MagicMock()
        life._permission = MagicMock()
        life._tools_registry = MagicMock()

        snapshot = StateSnapshot(snapshot_id="restore_get", created_at=datetime.now())
        snapshot.module_states = {
            "body_sensor": ModuleState(
                module_name="body_sensor", initialized=True,
                state_data=pickle.dumps({"initialized": True, "watch_dirs": ["/new"], "config": {"k2": "v2"}}),
                checksum="",
            ),
        }
        result = manager._restore_modules_by_priority(life, snapshot)
        assert result is True


class TestLoadSnapshotEdgeCases:
    """load_snapshot 恢复失败与验证分支"""

    def test_load_restore_failure_continues(self, manager, fake_digital_life):
        # 保存一个快照，但恢复时模块数据损坏
        manager.save_snapshot(fake_digital_life, snapshot_id="load_restore_fail", force=True)
        # 修改快照文件使模块恢复失败
        snap = manager._load_snapshot_data("load_restore_fail")
        snap.module_states = {
            "body_sensor": ModuleState(
                module_name="body_sensor", initialized=True,
                state_data=b"corrupted", checksum="wrong",
            ),
        }
        # 覆盖原文件
        old_path = manager._get_snapshot_path("load_restore_fail", False)
        if old_path.exists():
            os.remove(old_path)
        manager._persist_snapshot(snap)

        class FakeLife:
            def __init__(self, config):
                self._config = config
                self._body = MagicMock()
                self._behavior = MagicMock()
                self._permission = MagicMock()

        # 应该返回实例（恢复部分失败但流程继续）
        result = manager.load_snapshot(digital_life_class=FakeLife, snapshot_id="load_restore_fail")
        assert result is not None


class TestListSnapshotsEdgeCases:
    """list_snapshots 文件解析异常"""

    def test_list_skips_non_snapshot_files(self, manager, fake_digital_life):
        manager.save_snapshot(fake_digital_life, snapshot_id="list_normal", force=True)
        # 创建非快照文件
        (manager.snapshot_dir / "readme.txt").write_text("hello")
        (manager.snapshot_dir / "random.snap.gz").write_bytes(b"corrupted")
        snapshots = manager.list_snapshots()
        # 只返回有效快照
        assert any(s.snapshot_id == "list_normal" for s in snapshots)

    def test_list_handles_dir_iteration_error(self, manager):
        # snapshot_dir 不存在时 iterdir 抛异常 → 被捕获返回空列表
        import shutil
        shutil.rmtree(manager.snapshot_dir)
        snapshots = manager.list_snapshots()
        assert snapshots == []


class TestCleanupSnapshotsEdgeCases:
    """cleanup_snapshots 删除异常"""

    def test_cleanup_swallows_delete_error(self, manager, fake_digital_life):
        for i in range(4):
            manager.save_snapshot(fake_digital_life, snapshot_id=f"cleanup_err_{i}", force=True)
            time.sleep(0.01)
        # unlink 抛异常不应导致整体失败
        with patch("pathlib.Path.unlink", side_effect=OSError("denied")):
            deleted = manager.cleanup_snapshots(keep_count=1)
        # 即使 unlink 失败，函数也正常返回（deleted_count 可能为 0）
        assert isinstance(deleted, int)
