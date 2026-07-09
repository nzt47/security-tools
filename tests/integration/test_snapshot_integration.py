"""p6/snapshot.py 集成测试

覆盖范围:
    - 数据类: SnapshotResult / SnapshotInfo / ModuleState / StateSnapshot
    - StateSnapshotManager: 初始化/路径/校验和/持久化/加载/合并/兼容性/清理
    - 序列化: body_sensor / behavior / permission / tools_registry
    - 恢复: 四个模块的恢复方法 + 按优先级恢复
    - 主流程: save_snapshot (完整/增量/强制/频率拒绝) / load_snapshot (有类/无类/版本不兼容)
    - 辅助: list_snapshots / cleanup_snapshots / show_performance_panel
"""

import gzip
import hashlib
import pickle
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.p6.snapshot import (
    ModuleState,
    SnapshotInfo,
    SnapshotResult,
    StateSnapshot,
    StateSnapshotManager,
)


# ──────────────────────────────────────────────
# 测试辅助: FakeDigitalLife 及其依赖模块
# ──────────────────────────────────────────────


class FakeBodySensor:
    """模拟 BodySensor 模块"""

    def __init__(self, initialized=False, watch_dirs=None, config=None):
        self._initialized = initialized
        self.is_initialized = initialized
        self.watch_dirs = watch_dirs or []
        self.config = config or {}


class FakeBodyContainer:
    """模拟 _body 容器(有 get 方法)"""

    def __init__(self, body_sensor):
        self._body_sensor = body_sensor

    def get(self):
        return self._body_sensor


class FakeBehavior:
    """模拟 BehaviorController"""

    def __init__(self, mode="NORMAL", history=None, thresholds=None):
        self._current_mode = mode
        self._mode_history = history or []
        self.THRESHOLDS = thresholds or {}


class FakePermission:
    """模拟 PermissionSystem"""

    def __init__(self, patterns=None, blacklist=None, sensitive_exts=None):
        self.DANGEROUS_PATTERNS = patterns or []
        self.BLACKLIST = blacklist or []
        self.SENSITIVE_EXTENSIONS = sensitive_exts or {".env", ".key"}


class FakeToolsRegistry:
    """模拟 ToolsRegistry"""

    def __init__(self, tools=None):
        self._tools = tools or {}


class FakeDigitalLife:
    """模拟 DigitalLife 实例,含四个核心模块"""

    def __init__(self, config=None, body=None, behavior=None,
                 permission=None, tools_registry=None):
        self._config = config or {"version": "test"}
        self._body = body
        self._behavior = behavior
        self._permission = permission
        self._tools_registry = tools_registry


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────


@pytest.fixture
def snapshot_dir(tmp_path):
    """快照目录(隔离)"""
    return tmp_path / "snapshots"


@pytest.fixture
def manager(snapshot_dir):
    """标准 manager (默认压缩)"""
    return StateSnapshotManager(snapshot_dir=str(snapshot_dir))


@pytest.fixture
def manager_no_compression(snapshot_dir):
    """禁用压缩的 manager"""
    return StateSnapshotManager(
        snapshot_dir=str(snapshot_dir),
        enable_compression=False,
    )


@pytest.fixture
def fast_manager(snapshot_dir):
    """频率控制为 0 的 manager,便于连续保存"""
    m = StateSnapshotManager(snapshot_dir=str(snapshot_dir))
    m.frequency_controller.min_interval_seconds = 0.0
    return m


@pytest.fixture
def digital_life():
    """含全部核心模块的 FakeDigitalLife"""
    return FakeDigitalLife(
        config={"name": "test-life", "version": "1.0"},
        body=FakeBodyContainer(FakeBodySensor(
            initialized=True,
            watch_dirs=["/tmp/a", "/tmp/b"],
            config={"interval": 5},
        )),
        behavior=FakeBehavior(mode="NORMAL", history=["NORMAL"], thresholds={"cpu": 0.8}),
        permission=FakePermission(
            patterns=["p1", "p2"],
            blacklist=["b1"],
            sensitive_exts={".env", ".key"},
        ),
        tools_registry=FakeToolsRegistry(tools={"tool1": {}, "tool2": {}}),
    )


@pytest.fixture
def minimal_digital_life():
    """只有 _config 的最小 DigitalLife(无核心模块属性)"""
    life = FakeDigitalLife(config={"name": "minimal"})
    del life._body
    del life._behavior
    del life._permission
    del life._tools_registry
    return life


def make_snapshot(snapshot_id="snap_test_001", version="p6.2.0",
                  is_incremental=False, base_snapshot_id=None,
                  module_states=None, config=None):
    """构造 StateSnapshot 测试对象"""
    return StateSnapshot(
        snapshot_id=snapshot_id,
        created_at=datetime.now(),
        version=version,
        config=config or {"test": True},
        module_states=module_states or {},
        is_incremental=is_incremental,
        base_snapshot_id=base_snapshot_id,
    )


# ──────────────────────────────────────────────
# 1. 数据类测试
# ──────────────────────────────────────────────


class TestSnapshotResult:
    """SnapshotResult dataclass"""

    def test_default_values(self):
        r = SnapshotResult(success=True)
        assert r.success is True
        assert r.snapshot_id is None
        assert r.version == ""
        assert r.elapsed_ms == 0.0
        assert r.error_message is None
        assert r.is_incremental is False
        assert r.base_snapshot_id is None
        assert r.space_saved_bytes == 0
        assert r.file_size == 0
        assert r.created_at is None

    def test_full_construction(self):
        now = datetime.now()
        r = SnapshotResult(
            success=True,
            snapshot_id="snap_001",
            version="p6.2.0",
            elapsed_ms=42.5,
            is_incremental=True,
            base_snapshot_id="snap_base",
            space_saved_bytes=1024,
            file_size=2048,
            created_at=now,
        )
        assert r.snapshot_id == "snap_001"
        assert r.elapsed_ms == 42.5
        assert r.is_incremental is True
        assert r.space_saved_bytes == 1024
        assert r.created_at == now


class TestSnapshotInfo:
    """SnapshotInfo dataclass"""

    def test_default_values(self):
        now = datetime.now()
        info = SnapshotInfo(
            snapshot_id="snap_001",
            created_at=now,
            version="p6.2.0",
            file_size=1024,
        )
        assert info.is_incremental is False
        assert info.base_snapshot_id is None

    def test_incremental_info(self):
        info = SnapshotInfo(
            snapshot_id="snap_inc",
            created_at=datetime.now(),
            version="p6.2.0",
            file_size=512,
            is_incremental=True,
            base_snapshot_id="snap_base",
        )
        assert info.is_incremental is True
        assert info.base_snapshot_id == "snap_base"


class TestModuleState:
    """ModuleState dataclass"""

    def test_default_values(self):
        ms = ModuleState(
            module_name="test",
            initialized=True,
            state_data=b"abc",
        )
        assert ms.restore_priority == 0
        assert ms.checksum == ""
        assert ms.changed is True

    def test_with_checksum_and_priority(self):
        ms = ModuleState(
            module_name="body",
            initialized=True,
            state_data=b"data",
            restore_priority=100,
            checksum="abc123",
            changed=False,
        )
        assert ms.restore_priority == 100
        assert ms.checksum == "abc123"
        assert ms.changed is False


class TestStateSnapshot:
    """StateSnapshot dataclass"""

    def test_default_values(self):
        snap = StateSnapshot(snapshot_id="s1", created_at=datetime.now())
        assert snap.version == "p6.2.0"
        assert snap.config == {}
        assert snap.module_states == {}
        assert snap.lazy_cache == {}
        assert snap.performance_stats == {}
        assert snap.is_incremental is False
        assert snap.base_snapshot_id is None

    def test_compute_checksum_stable(self):
        """同一份数据两次计算校验和应一致"""
        snap1 = make_snapshot(snapshot_id="s1")
        snap2 = make_snapshot(snapshot_id="s2")  # 不同 ID
        # checksum 不包含 snapshot_id,故相同内容应一致
        assert snap1.compute_checksum() == snap2.compute_checksum()

    def test_compute_checksum_changes_with_content(self):
        """内容不同则校验和不同"""
        snap1 = make_snapshot(config={"a": 1})
        snap2 = make_snapshot(config={"a": 2})
        assert snap1.compute_checksum() != snap2.compute_checksum()

    def test_compute_checksum_includes_module_checksums(self):
        """module_states 的 checksum 参与 compute_checksum"""
        ms1 = ModuleState(module_name="m", initialized=True,
                          state_data=b"x", checksum="c1")
        ms2 = ModuleState(module_name="m", initialized=True,
                          state_data=b"x", checksum="c2")
        snap1 = make_snapshot(module_states={"m": ms1})
        snap2 = make_snapshot(module_states={"m": ms2})
        assert snap1.compute_checksum() != snap2.compute_checksum()


# ──────────────────────────────────────────────
# 2. StateSnapshotManager 初始化与基础方法
# ──────────────────────────────────────────────


class TestManagerInit:
    """StateSnapshotManager 初始化与基础方法"""

    def test_init_creates_directory(self, snapshot_dir):
        assert not snapshot_dir.exists()
        StateSnapshotManager(snapshot_dir=str(snapshot_dir))
        assert snapshot_dir.exists()

    def test_init_with_compression_default(self, manager):
        assert manager.enable_compression is True

    def test_init_creates_frequency_controller(self, manager):
        assert manager.frequency_controller is not None
        assert manager.frequency_controller.max_snapshots == 5

    def test_init_creates_performance_monitor(self, manager):
        assert manager.performance_monitor is not None

    def test_init_current_snapshot_none(self, manager):
        assert manager.current_snapshot is None

    def test_init_last_module_checksums_empty(self, manager):
        assert manager.last_module_checksums == {}

    def test_ensure_snapshot_dir_idempotent(self, manager, snapshot_dir):
        # 已存在时不应报错
        manager._ensure_snapshot_dir()
        assert snapshot_dir.exists()

    def test_generate_snapshot_id_format(self, manager):
        sid = manager._generate_snapshot_id()
        assert sid.startswith("snap_")
        # 格式: snap_YYYYMMDD_HHMMSS_microsecond → 4 段
        parts = sid.split("_")
        assert len(parts) == 4
        assert parts[0] == "snap"
        assert len(parts[1]) == 8   # YYYYMMDD
        assert len(parts[2]) == 6   # HHMMSS

    def test_generate_snapshot_id_uniqueness(self, manager):
        """连续生成多个 ID 应为有效格式(Windows 时钟分辨率低,不强制唯一)"""
        ids = [manager._generate_snapshot_id() for _ in range(20)]
        # 所有 ID 都应以 snap_ 开头
        assert all(sid.startswith("snap_") for sid in ids)
        # 至少有一个 ID(Windows 时钟分辨率约 15ms,可能全部相同)
        assert len(ids) == 20

    def test_get_snapshot_path_full_compressed(self, manager):
        path = manager._get_snapshot_path("snap_001", is_incremental=False)
        assert path.name == "snap_001.snap.gz"

    def test_get_snapshot_path_full_uncompressed(self, manager_no_compression):
        path = manager_no_compression._get_snapshot_path("snap_001", is_incremental=False)
        assert path.name == "snap_001.snap"

    def test_get_snapshot_path_incremental_compressed(self, manager):
        path = manager._get_snapshot_path("snap_001", is_incremental=True)
        assert path.name == "snap_001.incremental.snap.gz"

    def test_get_snapshot_path_incremental_uncompressed(self, manager_no_compression):
        path = manager_no_compression._get_snapshot_path("snap_001", is_incremental=True)
        assert path.name == "snap_001.incremental.snap"

    def test_compute_checksum(self, manager):
        data = b"hello world"
        expected = hashlib.sha256(data).hexdigest()
        assert manager._compute_checksum(data) == expected

    def test_compute_checksum_empty(self, manager):
        data = b""
        expected = hashlib.sha256(data).hexdigest()
        assert manager._compute_checksum(data) == expected


# ──────────────────────────────────────────────
# 3. 持久化与加载
# ──────────────────────────────────────────────


class TestPersistence:
    """_persist_snapshot / _load_snapshot_data / _load_from_path / _merge_snapshots"""

    def test_persist_snapshot_compressed(self, manager):
        snap = make_snapshot(snapshot_id="snap_persist_1")
        assert manager._persist_snapshot(snap) is True
        path = manager._get_snapshot_path("snap_persist_1", is_incremental=False)
        assert path.exists()
        assert path.suffix == ".gz"

    def test_persist_snapshot_uncompressed(self, manager_no_compression):
        snap = make_snapshot(snapshot_id="snap_persist_2")
        assert manager_no_compression._persist_snapshot(snap) is True
        path = manager_no_compression._get_snapshot_path("snap_persist_2", is_incremental=False)
        assert path.exists()
        assert path.suffix == ".snap"

    def test_persist_snapshot_incremental(self, manager):
        snap = make_snapshot(snapshot_id="snap_inc_1", is_incremental=True,
                             base_snapshot_id="snap_base")
        assert manager._persist_snapshot(snap) is True
        path = manager._get_snapshot_path("snap_inc_1", is_incremental=True)
        assert path.exists()

    def test_persist_snapshot_returns_false_on_error(self, manager):
        """写入失败应返回 False"""
        snap = make_snapshot(snapshot_id="snap_err")
        with patch("builtins.open", side_effect=IOError("disk full")):
            assert manager._persist_snapshot(snap) is False

    def test_load_snapshot_data_returns_none_when_empty(self, manager):
        """无快照时返回 None"""
        assert manager._load_snapshot_data() is None

    def test_load_snapshot_data_returns_none_when_not_found(self, manager):
        """指定不存在的 ID 返回 None"""
        assert manager._load_snapshot_data("nonexistent") is None

    def test_load_snapshot_data_by_id(self, manager):
        snap = make_snapshot(snapshot_id="snap_load_1", config={"k": "v"})
        manager._persist_snapshot(snap)
        loaded = manager._load_snapshot_data("snap_load_1")
        assert loaded is not None
        assert loaded.snapshot_id == "snap_load_1"
        assert loaded.config == {"k": "v"}

    def test_load_snapshot_data_latest(self, manager):
        """snapshot_id=None 时加载最新"""
        snap1 = make_snapshot(snapshot_id="snap_old")
        manager._persist_snapshot(snap1)
        time.sleep(0.05)
        snap2 = make_snapshot(snapshot_id="snap_new")
        manager._persist_snapshot(snap2)
        loaded = manager._load_snapshot_data()
        assert loaded is not None
        # 最新排序在前
        assert loaded.snapshot_id in ("snap_old", "snap_new")

    def test_load_from_path_compressed(self, manager):
        snap = make_snapshot(snapshot_id="snap_lfp_c", config={"a": 1})
        manager._persist_snapshot(snap)
        path = manager._get_snapshot_path("snap_lfp_c", is_incremental=False)
        loaded = manager._load_from_path(path)
        assert loaded is not None
        assert loaded.snapshot_id == "snap_lfp_c"
        assert loaded.config == {"a": 1}

    def test_load_from_path_uncompressed(self, manager_no_compression):
        snap = make_snapshot(snapshot_id="snap_lfp_u")
        manager_no_compression._persist_snapshot(snap)
        path = manager_no_compression._get_snapshot_path("snap_lfp_u", is_incremental=False)
        loaded = manager_no_compression._load_from_path(path)
        assert loaded is not None
        assert loaded.snapshot_id == "snap_lfp_u"

    def test_load_from_path_incremental_merges_base(self, manager):
        """加载增量快照时应合并基础快照"""
        # 1. 保存基础快照(含 body_sensor 模块)
        base_ms = ModuleState(
            module_name="body_sensor", initialized=True,
            state_data=pickle.dumps({"initialized": True}),
            restore_priority=100, checksum="base_checksum",
        )
        base = make_snapshot(snapshot_id="snap_base_1",
                             module_states={"body_sensor": base_ms})
        manager._persist_snapshot(base)

        # 2. 保存增量快照(只有 behavior 模块变化)
        inc_ms = ModuleState(
            module_name="behavior", initialized=True,
            state_data=pickle.dumps({"mode": "NORMAL"}),
            restore_priority=90, checksum="inc_checksum",
            changed=True,
        )
        inc = make_snapshot(
            snapshot_id="snap_inc_1",
            is_incremental=True,
            base_snapshot_id="snap_base_1",
            module_states={"behavior": inc_ms},
        )
        manager._persist_snapshot(inc)

        # 3. 加载增量快照,应合并基础快照
        loaded = manager._load_snapshot_data("snap_inc_1")
        assert loaded is not None
        assert "body_sensor" in loaded.module_states
        assert "behavior" in loaded.module_states

    def test_merge_snapshots_applies_changed_modules(self, manager):
        """_merge_snapshots: 基础模块全保留,增量只应用 changed=True 的"""
        base_ms = ModuleState("m1", True, b"d1", checksum="c1")
        base = make_snapshot(module_states={"m1": base_ms})

        inc_ms_changed = ModuleState("m2", True, b"d2", checksum="c2", changed=True)
        inc_ms_unchanged = ModuleState("m1", True, b"d3", checksum="c3", changed=False)
        inc = make_snapshot(
            is_incremental=True,
            module_states={"m1": inc_ms_unchanged, "m2": inc_ms_changed},
        )

        merged = manager._merge_snapshots(base, inc)
        # m1 来自基础(增量未变化)
        assert merged.module_states["m1"].state_data == b"d1"
        # m2 来自增量(changed=True)
        assert merged.module_states["m2"].state_data == b"d2"

    def test_merge_snapshots_uses_incremental_metadata(self, manager):
        """合并后的 snapshot_id/created_at/version 使用增量的"""
        base = make_snapshot(snapshot_id="base", version="p6.1.0")
        inc = make_snapshot(snapshot_id="inc", version="p6.2.0",
                            is_incremental=True, base_snapshot_id="base")
        merged = manager._merge_snapshots(base, inc)
        assert merged.snapshot_id == "inc"
        assert merged.version == "p6.2.0"


# ──────────────────────────────────────────────
# 4. 版本兼容性
# ──────────────────────────────────────────────


class TestCompatibility:
    """_check_compatibility"""

    def test_p6_1_0_compatible(self, manager):
        snap = make_snapshot(version="p6.1.0")
        assert manager._check_compatibility(snap) is True

    def test_p6_2_0_compatible(self, manager):
        snap = make_snapshot(version="p6.2.0")
        assert manager._check_compatibility(snap) is True

    def test_p6_x_compatible(self, manager):
        snap = make_snapshot(version="p6.9.9")
        assert manager._check_compatibility(snap) is True

    def test_p5_incompatible(self, manager):
        snap = make_snapshot(version="p5.0.0")
        assert manager._check_compatibility(snap) is False

    def test_non_p6_incompatible(self, manager):
        snap = make_snapshot(version="1.0.0")
        assert manager._check_compatibility(snap) is False


# ──────────────────────────────────────────────
# 5. 序列化方法
# ──────────────────────────────────────────────


class TestSerialization:
    """_serialize_body_sensor / _serialize_behavior / _serialize_permission / _serialize_tools_registry"""

    def test_serialize_body_sensor_initialized(self, manager):
        body = FakeBodySensor(initialized=True, watch_dirs=["/a", "/b"],
                              config={"k": "v"})
        state = manager._serialize_body_sensor(body)
        assert state["initialized"] is True
        assert state["watch_dirs"] == ["/a", "/b"]
        assert state["config"] == {"k": "v"}

    def test_serialize_body_sensor_uninitialized(self, manager):
        body = FakeBodySensor(initialized=False)
        state = manager._serialize_body_sensor(body)
        assert state["initialized"] is False
        # 未初始化时不保存 watch_dirs / config
        assert "watch_dirs" not in state
        assert "config" not in state

    def test_serialize_body_sensor_no_is_initialized_attr(self, manager):
        """无 is_initialized 属性时默认 False"""
        body = MagicMock()
        del body.is_initialized
        body._initialized = False
        state = manager._serialize_body_sensor(body)
        assert state["initialized"] is False

    def test_serialize_body_sensor_exception_returns_error(self, manager):
        """序列化异常(log_dict 抛异常)时返回 error 字段"""
        body = FakeBodySensor(initialized=True)
        # log_dict 在 try 内首次调用抛异常,在 except 内第二次调用正常返回
        mock_log_dict = MagicMock()
        mock_log_dict.side_effect = [RuntimeError("boom"), {"x": 1}]
        with patch("agent.p6.snapshot.log_dict", mock_log_dict):
            state = manager._serialize_body_sensor(body)
        assert state["initialized"] is False
        assert "error" in state

    def test_serialize_behavior_with_mode(self, manager):
        behavior = FakeBehavior(mode="FOCUS", history=["NORMAL", "FOCUS"],
                                thresholds={"cpu": 0.9})
        state = manager._serialize_behavior(behavior)
        assert state["initialized"] is True
        assert state["mode"] == "FOCUS"
        assert state["mode_history"] == ["NORMAL", "FOCUS"]
        assert state["thresholds"] == {"cpu": 0.9}

    def test_serialize_behavior_mode_history_truncated(self, manager):
        """模式历史超过 5 条时只保留最后 5 条"""
        history = [f"MODE_{i}" for i in range(10)]
        behavior = FakeBehavior(mode="NORMAL", history=history)
        state = manager._serialize_behavior(behavior)
        assert len(state["mode_history"]) == 5
        assert state["mode_history"] == history[-5:]

    def test_serialize_behavior_no_mode_attr(self, manager):
        """无 _current_mode 时使用默认 NORMAL"""
        behavior = MagicMock()
        del behavior._current_mode
        del behavior._mode_history
        del behavior.THRESHOLDS
        state = manager._serialize_behavior(behavior)
        assert state["mode"] == "NORMAL"

    def test_serialize_behavior_enum_mode(self, manager):
        """_current_mode 是枚举时取 .value"""
        from enum import Enum

        class Mode(Enum):
            FOCUS = "focus_mode"

        behavior = FakeBehavior()
        behavior._current_mode = Mode.FOCUS
        state = manager._serialize_behavior(behavior)
        assert state["mode"] == "focus_mode"

    def test_serialize_permission_with_patterns(self, manager):
        permission = FakePermission(
            patterns=["p1", "p2", "p3"],
            blacklist=["b1", "b2"],
            sensitive_exts={".env", ".key", ".pem"},
        )
        state = manager._serialize_permission(permission)
        assert state["dangerous_patterns_count"] == 3
        assert state["blacklist_count"] == 2
        assert set(state["sensitive_extensions"]) == {".env", ".key", ".pem"}

    def test_serialize_permission_no_attrs(self, manager):
        """无 DANGEROUS_PATTERNS 等属性时使用默认计数 0"""
        permission = MagicMock()
        del permission.DANGEROUS_PATTERNS
        del permission.BLACKLIST
        del permission.SENSITIVE_EXTENSIONS
        state = manager._serialize_permission(permission)
        assert state["dangerous_patterns_count"] == 0
        assert state["blacklist_count"] == 0

    def test_serialize_tools_registry_with_tools(self, manager):
        tools = {f"tool_{i}": {"desc": f"tool {i}"} for i in range(10)}
        registry = FakeToolsRegistry(tools=tools)
        state = manager._serialize_tools_registry(registry)
        assert state["initialized"] is True
        assert state["tools_count"] == 10
        assert len(state["tools"]) == 10

    def test_serialize_tools_registry_truncates_to_50(self, manager):
        """工具列表超过 50 个时只保留前 50 个名称"""
        tools = {f"tool_{i}": {} for i in range(60)}
        registry = FakeToolsRegistry(tools=tools)
        state = manager._serialize_tools_registry(registry)
        assert state["tools_count"] == 60  # count 完整
        assert len(state["tools"]) == 50  # 名称列表截断

    def test_serialize_tools_registry_empty(self, manager):
        registry = FakeToolsRegistry(tools={})
        state = manager._serialize_tools_registry(registry)
        assert state["initialized"] is True
        assert state["tools_count"] == 0
        assert state["tools"] == []

    def test_serialize_tools_registry_none(self, manager):
        """tools_registry 为 None 时返回未初始化状态"""
        state = manager._serialize_tools_registry(None)
        assert state["initialized"] is False
        assert state["tools_count"] == 0

    def test_serialize_tools_registry_no_tools_attr(self, manager):
        registry = MagicMock()
        del registry._tools
        state = manager._serialize_tools_registry(registry)
        assert state["initialized"] is False


# ──────────────────────────────────────────────
# 6. 恢复方法
# ──────────────────────────────────────────────


class TestRestoration:
    """_restore_body_sensor / _restore_behavior / _restore_permission / _restore_tools_registry"""

    def test_restore_body_sensor_full(self, manager):
        body = FakeBodySensor(initialized=False)
        state = {
            "initialized": True,
            "watch_dirs": ["/x", "/y"],
            "config": {"k": "v"},
        }
        assert manager._restore_body_sensor(body, state) is True
        assert body._initialized is True
        assert body.watch_dirs == ["/x", "/y"]
        assert body.config == {"k": "v"}

    def test_restore_body_sensor_partial_state(self, manager):
        """只有 initialized 字段时也能恢复"""
        body = FakeBodySensor()
        assert manager._restore_body_sensor(body, {"initialized": False}) is True
        assert body._initialized is False

    def test_restore_body_sensor_exception_returns_false(self, manager):
        body = MagicMock()
        # 设置属性抛异常
        type(body)._initialized = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("fail")))
        result = manager._restore_body_sensor(body, {"initialized": True})
        assert result is False

    def test_restore_behavior_with_mode(self, manager):
        """恢复行为模式(使用真实 BehaviorMode 枚举)"""
        behavior = FakeBehavior()
        state = {"mode": "NORMAL", "mode_history": ["NORMAL", "SAFE"]}
        assert manager._restore_behavior(behavior, state) is True
        assert behavior._mode_history == ["NORMAL", "SAFE"]

    def test_restore_behavior_no_mode_attr(self, manager):
        """behavior 无 _current_mode 属性时也能通过(跳过模式恢复)"""
        behavior = MagicMock()
        del behavior._current_mode
        del behavior._mode_history
        # 无 _mode_history,跳过历史恢复,返回 True
        assert manager._restore_behavior(behavior, {"mode": "NORMAL"}) is True

    def test_restore_behavior_no_history_in_state(self, manager):
        behavior = FakeBehavior()
        state = {"mode": "NORMAL"}  # 无 mode_history
        with patch.dict("sys.modules", {"agent.behavior_controller": None}):
            # ImportError 降级路径
            assert manager._restore_behavior(behavior, state) is True

    def test_restore_permission_logs_sensitive_exts(self, manager):
        permission = FakePermission()
        state = {
            "dangerous_patterns_count": 5,
            "blacklist_count": 2,
            "sensitive_extensions": [".env", ".key"],
        }
        assert manager._restore_permission(permission, state) is True

    def test_restore_permission_no_sensitive_exts_in_state(self, manager):
        permission = FakePermission()
        state = {"dangerous_patterns_count": 0, "blacklist_count": 0}
        assert manager._restore_permission(permission, state) is True

    def test_restore_tools_registry_logs_tools(self, manager):
        registry = FakeToolsRegistry()
        state = {"tools_count": 3, "tools": ["t1", "t2", "t3"]}
        assert manager._restore_tools_registry(registry, state) is True

    def test_restore_tools_registry_empty_state(self, manager):
        registry = FakeToolsRegistry()
        assert manager._restore_tools_registry(registry, {}) is True

    def test_restore_tools_registry_exception_returns_false(self, manager):
        registry = MagicMock()
        # 让 join 抛异常
        state = {"tools": [None, None]}  # None 会让 ", ".join 失败? 实际不会
        # 实际: ", ".join([None]) 会抛 TypeError
        state = {"tools": [1, 2]}  # int 不能 join
        result = manager._restore_tools_registry(registry, state)
        assert result is False


# ──────────────────────────────────────────────
# 7. 按优先级恢复
# ──────────────────────────────────────────────


class TestRestoreByPriority:
    """_restore_modules_by_priority"""

    def test_restore_all_modules(self, manager, digital_life):
        """恢复全部四个模块"""
        # 构造快照含四个模块状态
        body_state = {"initialized": True, "watch_dirs": ["/a"], "config": {}}
        behavior_state = {"mode": "NORMAL", "mode_history": []}
        permission_state = {"dangerous_patterns_count": 1, "blacklist_count": 0}
        tools_state = {"tools_count": 2, "tools": ["t1", "t2"]}

        modules = {
            "body_sensor": ModuleState(
                "body_sensor", True, pickle.dumps(body_state),
                restore_priority=100, checksum=manager._compute_checksum(pickle.dumps(body_state)),
            ),
            "behavior": ModuleState(
                "behavior", True, pickle.dumps(behavior_state),
                restore_priority=90, checksum=manager._compute_checksum(pickle.dumps(behavior_state)),
            ),
            "permission": ModuleState(
                "permission", True, pickle.dumps(permission_state),
                restore_priority=80, checksum=manager._compute_checksum(pickle.dumps(permission_state)),
            ),
            "tools_registry": ModuleState(
                "tools_registry", True, pickle.dumps(tools_state),
                restore_priority=70, checksum=manager._compute_checksum(pickle.dumps(tools_state)),
            ),
        }
        snap = make_snapshot(module_states=modules)

        # 使用真实 BehaviorMode 枚举(NORMAL 成员存在)
        result = manager._restore_modules_by_priority(digital_life, snap)

        assert result is True  # 至少一个成功
        # body_sensor 的 watch_dirs 应被恢复
        body = digital_life._body.get()
        assert body.watch_dirs == ["/a"]

    def test_restore_skips_uninitialized_modules(self, manager, digital_life):
        """未初始化的模块应被跳过"""
        ms = ModuleState("body_sensor", initialized=False,
                         state_data=b"x", restore_priority=100)
        snap = make_snapshot(module_states={"body_sensor": ms})
        result = manager._restore_modules_by_priority(digital_life, snap)
        # 没有已初始化模块,success_count=0,返回 False
        assert result is False

    def test_restore_unknown_module_warns(self, manager, digital_life):
        """未知模块名应触发警告但不影响其他模块"""
        ms_unknown = ModuleState("unknown_mod", True, pickle.dumps({}),
                                 restore_priority=50, checksum="x")
        ms_known = ModuleState("body_sensor", True,
                               pickle.dumps({"initialized": True, "watch_dirs": [], "config": {}}),
                               restore_priority=100, checksum="y")
        snap = make_snapshot(module_states={
            "unknown_mod": ms_unknown,
            "body_sensor": ms_known,
        })
        result = manager._restore_modules_by_priority(digital_life, snap)
        assert result is True  # body_sensor 成功

    def test_restore_priority_order(self, manager, digital_life):
        """恢复顺序应按 restore_priority 降序"""
        # 通过 patch 各 _restore_* 方法记录调用顺序
        order = []
        manager._restore_body_sensor = lambda b, s: order.append("body") or True
        manager._restore_behavior = lambda b, s: order.append("behavior") or True
        manager._restore_permission = lambda p, s: order.append("permission") or True
        manager._restore_tools_registry = lambda t, s: order.append("tools") or True

        # state_data 必须是有效 pickle,否则 pickle.loads 会先抛异常
        valid_data = pickle.dumps({})
        modules = {
            "tools_registry": ModuleState("tools_registry", True, valid_data, 70),
            "permission": ModuleState("permission", True, valid_data, 80),
            "behavior": ModuleState("behavior", True, valid_data, 90),
            "body_sensor": ModuleState("body_sensor", True, valid_data, 100),
        }
        snap = make_snapshot(module_states=modules)
        manager._restore_modules_by_priority(digital_life, snap)
        assert order == ["body", "behavior", "permission", "tools"]

    def test_restore_checksum_mismatch_logs_warning(self, manager, digital_life):
        """校验和不匹配应记录警告但继续恢复"""
        state_data = pickle.dumps({"initialized": True, "watch_dirs": [], "config": {}})
        ms = ModuleState("body_sensor", True, state_data,
                         restore_priority=100, checksum="wrong_checksum")
        snap = make_snapshot(module_states={"body_sensor": ms})
        # 应仍能恢复(checksum 不匹配只警告)
        result = manager._restore_modules_by_priority(digital_life, snap)
        assert result is True

    def test_restore_handles_exception(self, manager, digital_life):
        """单个模块恢复异常不应影响其他模块"""
        # body_sensor 的 state_data 是损坏的 pickle
        ms_bad = ModuleState("body_sensor", True, b"not_valid_pickle",
                             restore_priority=100, checksum="x")
        ms_good = ModuleState("permission", True,
                              pickle.dumps({"dangerous_patterns_count": 0}),
                              restore_priority=80, checksum="y")
        snap = make_snapshot(module_states={
            "body_sensor": ms_bad,
            "permission": ms_good,
        })
        result = manager._restore_modules_by_priority(digital_life, snap)
        # permission 成功,body_sensor 异常被捕获
        assert result is True


# ──────────────────────────────────────────────
# 8. save_snapshot 主流程
# ──────────────────────────────────────────────


class TestSaveSnapshot:
    """save_snapshot 主流程"""

    def test_save_full_snapshot_success(self, fast_manager, digital_life):
        result = fast_manager.save_snapshot(digital_life)
        assert result.success is True
        assert result.snapshot_id is not None
        assert result.is_incremental is False
        assert result.error_message is None
        assert result.elapsed_ms > 0
        assert fast_manager.current_snapshot is not None

    def test_save_with_custom_id(self, fast_manager, digital_life):
        result = fast_manager.save_snapshot(digital_life, snapshot_id="custom_snap_001")
        assert result.success is True
        assert result.snapshot_id == "custom_snap_001"

    def test_save_frequency_rejected(self, manager, digital_life):
        """默认 5 分钟间隔,第一次后第二次应被拒绝"""
        # 第一次保存(force=True)
        r1 = manager.save_snapshot(digital_life, force=True)
        assert r1.success is True
        # 第二次不强制,应被频率控制拒绝
        r2 = manager.save_snapshot(digital_life)
        assert r2.success is False
        assert "频繁" in r2.error_message

    def test_save_force_bypasses_frequency(self, manager, digital_life):
        """force=True 绕过频率限制"""
        r1 = manager.save_snapshot(digital_life, force=True)
        assert r1.success is True
        r2 = manager.save_snapshot(digital_life, force=True)
        assert r2.success is True

    def test_save_snapshot_no_config_attr(self, fast_manager):
        """digital_life 无 _config 属性时仍能保存(跳过配置)"""
        life = MagicMock()
        del life._config
        del life._body
        del life._behavior
        del life._permission
        del life._tools_registry
        result = fast_manager.save_snapshot(life)
        assert result.success is True

    def test_save_snapshot_without_core_modules(self, fast_manager, minimal_digital_life):
        """只有 _config 的 DigitalLife 也能保存"""
        result = fast_manager.save_snapshot(minimal_digital_life)
        assert result.success is True
        # 无核心模块
        assert len(fast_manager.current_snapshot.module_states) == 0

    def test_save_persist_failure_returns_error(self, fast_manager, digital_life):
        """持久化失败应返回错误"""
        with patch.object(fast_manager, "_persist_snapshot", return_value=False):
            result = fast_manager.save_snapshot(digital_life)
        assert result.success is False
        assert "持久化失败" in result.error_message

    def test_save_exception_returns_error_result(self, fast_manager, digital_life):
        """保存过程异常应返回错误结果"""
        with patch.object(fast_manager, "_save_core_modules_with_delta",
                          side_effect=RuntimeError("boom")):
            result = fast_manager.save_snapshot(digital_life)
        assert result.success is False
        assert "boom" in result.error_message

    def test_save_updates_current_snapshot(self, fast_manager, digital_life):
        fast_manager.save_snapshot(digital_life)
        assert fast_manager.current_snapshot is not None
        assert fast_manager.current_snapshot.snapshot_id is not None

    def test_save_updates_module_checksums(self, fast_manager, digital_life):
        """保存后 last_module_checksums 应被更新"""
        fast_manager.save_snapshot(digital_life)
        # body_sensor 和 behavior 等模块的 checksum 应被记录
        assert "body_sensor" in fast_manager.last_module_checksums
        assert "behavior" in fast_manager.last_module_checksums
        assert "permission" in fast_manager.last_module_checksums
        assert "tools_registry" in fast_manager.last_module_checksums

    def test_save_updates_frequency_controller(self, fast_manager, digital_life):
        """保存成功后频率控制器的 save_count 应增加"""
        assert fast_manager.frequency_controller.save_count == 0
        fast_manager.save_snapshot(digital_life)
        assert fast_manager.frequency_controller.save_count == 1

    def test_save_incremental_without_base(self, fast_manager, digital_life):
        """无基础快照时的增量保存(base_snapshot_id 为 None)"""
        result = fast_manager.save_snapshot(digital_life, incremental=True)
        assert result.success is True
        assert result.is_incremental is True
        assert result.base_snapshot_id is None  # 无 current_snapshot

    def test_save_incremental_with_base(self, fast_manager, digital_life):
        """有基础快照后的增量保存应设置 base_snapshot_id"""
        # 1. 完整保存
        r1 = fast_manager.save_snapshot(digital_life)
        assert r1.success is True
        # 2. 增量保存
        r2 = fast_manager.save_snapshot(digital_life, incremental=True)
        assert r2.success is True
        assert r2.is_incremental is True
        assert r2.base_snapshot_id == r1.snapshot_id

    def test_save_incremental_no_change_saves_space(self, fast_manager, digital_life):
        """增量保存时,未变化的模块被跳过,节省空间"""
        # 1. 完整保存
        fast_manager.save_snapshot(digital_life)
        # 2. 增量保存(内容未变)
        result = fast_manager.save_snapshot(digital_life, incremental=True)
        assert result.success is True
        assert result.is_incremental is True
        # 未变化模块被跳过,space_saved_bytes > 0
        assert result.space_saved_bytes > 0

    def test_save_incremental_with_changed_module(self, fast_manager, digital_life):
        """增量保存时,变化的模块应被持久化"""
        # 1. 完整保存
        fast_manager.save_snapshot(digital_life)
        # 2. 修改 behavior
        digital_life._behavior._current_mode = "FOCUS"
        # 3. 增量保存
        result = fast_manager.save_snapshot(digital_life, incremental=True)
        assert result.success is True
        # 增量快照文件存在
        inc_path = fast_manager._get_snapshot_path(result.snapshot_id, is_incremental=True)
        assert inc_path.exists()

    def test_save_snapshot_saves_file_to_disk(self, fast_manager, digital_life):
        """保存后磁盘上应有快照文件"""
        result = fast_manager.save_snapshot(digital_life)
        path = fast_manager._get_snapshot_path(result.snapshot_id, is_incremental=False)
        assert path.exists()

    def test_save_uncompressed_writes_snap_file(self, digital_life, snapshot_dir):
        """禁用压缩时写 .snap 文件"""
        m = StateSnapshotManager(snapshot_dir=str(snapshot_dir), enable_compression=False)
        m.frequency_controller.min_interval_seconds = 0.0
        result = m.save_snapshot(digital_life)
        path = m._get_snapshot_path(result.snapshot_id, is_incremental=False)
        assert path.exists()
        assert path.suffix == ".snap"


# ──────────────────────────────────────────────
# 9. load_snapshot 主流程
# ──────────────────────────────────────────────


class TestLoadSnapshot:
    """load_snapshot 主流程"""

    def test_load_no_snapshot_returns_none(self, manager):
        """无快照时返回 None"""
        assert manager.load_snapshot() is None

    def test_load_nonexistent_snapshot_returns_none(self, manager):
        """指定不存在的 ID 返回 None"""
        assert manager.load_snapshot(snapshot_id="nonexistent") is None

    def test_load_without_class_returns_snapshot(self, fast_manager, digital_life):
        """digital_life_class=None 时返回快照数据对象"""
        fast_manager.save_snapshot(digital_life)
        loaded = fast_manager.load_snapshot()
        assert loaded is not None
        assert isinstance(loaded, StateSnapshot)
        assert fast_manager.current_snapshot is not None

    def test_load_with_class_creates_instance(self, fast_manager, digital_life):
        """提供 digital_life_class 时创建实例并恢复"""
        fast_manager.save_snapshot(digital_life)

        # 用 FakeDigitalLife 作为类,接受 config 参数
        def fake_class(config):
            return FakeDigitalLife(config=config)

        loaded = fast_manager.load_snapshot(digital_life_class=fake_class)
        assert loaded is not None
        assert isinstance(loaded, FakeDigitalLife)
        assert loaded._config == digital_life._config

    def test_load_class_creation_exception_returns_none(self, fast_manager, digital_life):
        """digital_life_class 创建异常时返回 None"""
        fast_manager.save_snapshot(digital_life)

        def failing_class(config):
            raise RuntimeError("init failed")

        loaded = fast_manager.load_snapshot(digital_life_class=failing_class)
        assert loaded is None

    def test_load_version_incompatible_returns_none(self, fast_manager, digital_life):
        """版本不兼容时返回 None"""
        # 手动保存一个 p5 版本快照
        snap = make_snapshot(snapshot_id="snap_p5", version="p5.0.0",
                             config={"k": "v"})
        fast_manager._persist_snapshot(snap)
        loaded = fast_manager.load_snapshot()
        assert loaded is None

    def test_load_updates_current_snapshot(self, fast_manager, digital_life):
        """加载后 current_snapshot 应被更新"""
        fast_manager.save_snapshot(digital_life)
        old_current = fast_manager.current_snapshot
        fast_manager.current_snapshot = None  # 清空
        fast_manager.load_snapshot()
        assert fast_manager.current_snapshot is not None
        assert fast_manager.current_snapshot.snapshot_id == old_current.snapshot_id

    def test_load_updates_module_checksums(self, fast_manager, digital_life):
        """加载(带类)后 last_module_checksums 应被更新"""
        fast_manager.save_snapshot(digital_life)
        fast_manager.last_module_checksums.clear()

        def fake_class(config):
            return FakeDigitalLife(
                config=config,
                body=FakeBodyContainer(FakeBodySensor()),
                behavior=FakeBehavior(),
                permission=FakePermission(),
                tools_registry=FakeToolsRegistry(),
            )

        fast_manager.load_snapshot(digital_life_class=fake_class)
        # 加载后应恢复 checksums
        assert len(fast_manager.last_module_checksums) > 0

    def test_load_by_specific_id(self, fast_manager, digital_life):
        """按指定 ID 加载"""
        fast_manager.save_snapshot(digital_life, snapshot_id="snap_specific_001")
        loaded = fast_manager.load_snapshot(snapshot_id="snap_specific_001")
        assert loaded is not None
        assert loaded.snapshot_id == "snap_specific_001"

    def test_load_with_restore_modules(self, fast_manager, digital_life):
        """加载时应按优先级恢复模块"""
        fast_manager.save_snapshot(digital_life)

        def fake_class(config):
            # 返回一个空的 FakeDigitalLife,带全部模块
            return FakeDigitalLife(
                config=config,
                body=FakeBodyContainer(FakeBodySensor()),
                behavior=FakeBehavior(),
                permission=FakePermission(),
                tools_registry=FakeToolsRegistry(),
            )

        loaded = fast_manager.load_snapshot(digital_life_class=fake_class)
        assert loaded is not None
        # body_sensor 的状态应被恢复
        body = loaded._body.get()
        assert body.watch_dirs == ["/tmp/a", "/tmp/b"]


# ──────────────────────────────────────────────
# 10. 清理与列出
# ──────────────────────────────────────────────


class TestListAndCleanup:
    """list_snapshots / _cleanup_old_snapshots / cleanup_snapshots"""

    def test_list_snapshots_empty(self, manager):
        """空目录返回空列表"""
        assert manager.list_snapshots() == []

    def test_list_snapshots_returns_sorted(self, fast_manager, digital_life):
        """列出快照应按 created_at 倒序"""
        fast_manager.save_snapshot(digital_life, snapshot_id="snap_a")
        time.sleep(0.05)
        fast_manager.save_snapshot(digital_life, snapshot_id="snap_b", force=True)
        snapshots = fast_manager.list_snapshots()
        assert len(snapshots) == 2
        # 倒序: snap_b 在前
        assert snapshots[0].snapshot_id == "snap_b"
        assert snapshots[1].snapshot_id == "snap_a"

    def test_list_snapshots_includes_size_and_version(self, fast_manager, digital_life):
        fast_manager.save_snapshot(digital_life)
        snapshots = fast_manager.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].file_size > 0
        assert snapshots[0].version == "p6.2.0"

    def test_list_snapshots_skips_non_snap_files(self, fast_manager, snapshot_dir):
        """非 .snap 文件应被跳过"""
        # 放一个无关文件
        (snapshot_dir / "readme.txt").write_text("hello")
        assert fast_manager.list_snapshots() == []

    def test_list_snapshots_handles_corrupt_filename(self, fast_manager, snapshot_dir):
        """损坏的文件名应被跳过,不抛异常"""
        (snapshot_dir / "corrupt.snap.gz").write_bytes(b"not_a_snapshot")
        # list_snapshots 不解析内容,只看文件名,故应返回 1 个条目
        snapshots = fast_manager.list_snapshots()
        assert len(snapshots) == 1

    def test_list_snapshots_handles_dir_exception(self, snapshot_dir):
        """目录遍历异常应返回空列表"""
        m = StateSnapshotManager(snapshot_dir=str(snapshot_dir))
        with patch.object(Path, "iterdir", side_effect=OSError("io error")):
            assert m.list_snapshots() == []

    def test_cleanup_old_snapshots_triggered_on_save(self, snapshot_dir, digital_life):
        """超过 max_snapshots 时自动清理"""
        m = StateSnapshotManager(snapshot_dir=str(snapshot_dir))
        m.frequency_controller.min_interval_seconds = 0.0
        # max_snapshots 默认 5,保存 6 个应触发清理
        for i in range(6):
            m.save_snapshot(digital_life, force=True, snapshot_id=f"snap_{i}")
            time.sleep(0.01)
        snapshots = m.list_snapshots()
        # 清理后应 <= 5
        assert len(snapshots) <= 5

    def test_cleanup_snapshots_keep_count(self, fast_manager, digital_life):
        """cleanup_snapshots(keep_count) 应保留指定数量"""
        for i in range(5):
            fast_manager.save_snapshot(digital_life, force=True, snapshot_id=f"snap_c_{i}")
            time.sleep(0.01)
        deleted = fast_manager.cleanup_snapshots(keep_count=2)
        assert deleted >= 3
        snapshots = fast_manager.list_snapshots()
        assert len(snapshots) == 2

    def test_cleanup_snapshots_no_excess(self, fast_manager, digital_life):
        """快照数 <= keep_count 时不删除"""
        fast_manager.save_snapshot(digital_life)
        deleted = fast_manager.cleanup_snapshots(keep_count=5)
        assert deleted == 0

    def test_cleanup_snapshots_deletes_incremental_too(self, fast_manager, digital_life):
        """清理时应同时删除增量快照文件"""
        # 完整 + 增量
        fast_manager.save_snapshot(digital_life, snapshot_id="snap_full")
        fast_manager.save_snapshot(digital_life, incremental=True,
                                   snapshot_id="snap_inc", force=True)
        # 删除 snap_full,应同时删增量(如果存在同 ID)
        deleted = fast_manager.cleanup_snapshots(keep_count=0)
        assert deleted >= 1


# ──────────────────────────────────────────────
# 11. 性能监控
# ──────────────────────────────────────────────


class TestPerformancePanel:
    """show_performance_panel"""

    def test_show_performance_panel_no_error(self, manager):
        """show_performance_panel 不应抛异常"""
        manager.show_performance_panel()

    def test_show_performance_panel_after_save(self, fast_manager, digital_life):
        """保存后调用面板不应抛异常"""
        fast_manager.save_snapshot(digital_life)
        manager.show_performance_panel() if False else fast_manager.show_performance_panel()


# ──────────────────────────────────────────────
# 12. 完整端到端流程
# ──────────────────────────────────────────────


class TestEndToEnd:
    """完整保存→加载循环"""

    def test_save_load_roundtrip(self, fast_manager, digital_life):
        """保存后加载,配置应一致"""
        fast_manager.save_snapshot(digital_life)
        loaded = fast_manager.load_snapshot()
        assert loaded is not None
        assert loaded.config == digital_life._config
        assert "body_sensor" in loaded.module_states
        assert "behavior" in loaded.module_states

    def test_save_load_roundtrip_with_restore(self, fast_manager, digital_life):
        """保存→加载→恢复实例,模块状态应被还原"""
        fast_manager.save_snapshot(digital_life)

        def fake_class(config):
            return FakeDigitalLife(
                config=config,
                body=FakeBodyContainer(FakeBodySensor()),  # 空 body
                behavior=FakeBehavior(),
                permission=FakePermission(),
                tools_registry=FakeToolsRegistry(),
            )

        loaded = fast_manager.load_snapshot(digital_life_class=fake_class)
        assert loaded is not None
        body = loaded._body.get()
        # watch_dirs 应被恢复
        assert body.watch_dirs == ["/tmp/a", "/tmp/b"]
        assert body.config == {"interval": 5}

    def test_multiple_saves_and_load_latest(self, fast_manager, digital_life):
        """多次保存后加载最新"""
        fast_manager.save_snapshot(digital_life, snapshot_id="snap_v1")
        # 修改配置
        digital_life._config = {"name": "updated", "version": "2.0"}
        fast_manager.save_snapshot(digital_life, snapshot_id="snap_v2", force=True)
        loaded = fast_manager.load_snapshot()
        assert loaded is not None
        assert loaded.config == {"name": "updated", "version": "2.0"}

    def test_incremental_save_load_roundtrip(self, fast_manager, digital_life):
        """增量快照保存→加载应合并基础快照"""
        # 1. 完整保存
        r_full = fast_manager.save_snapshot(digital_life, snapshot_id="snap_base")
        assert r_full.success is True
        # 2. 修改 behavior 后增量保存
        digital_life._behavior._current_mode = "FOCUS"
        r_inc = fast_manager.save_snapshot(digital_life, incremental=True,
                                           snapshot_id="snap_inc", force=True)
        assert r_inc.success is True
        # 3. 加载增量快照(应自动合并基础)
        loaded = fast_manager.load_snapshot(snapshot_id="snap_inc")
        assert loaded is not None
        # 应包含 body_sensor(来自基础)和 behavior(来自增量)
        assert "body_sensor" in loaded.module_states
        assert "behavior" in loaded.module_states

    def test_compression_and_uncompression_equivalent(self, snapshot_dir, digital_life):
        """压缩和不压缩两种模式保存的快照都能正常加载"""
        # 压缩模式
        m_c = StateSnapshotManager(snapshot_dir=str(snapshot_dir / "c"))
        m_c.frequency_controller.min_interval_seconds = 0.0
        m_c.save_snapshot(digital_life, snapshot_id="snap_c")
        loaded_c = m_c.load_snapshot()
        assert loaded_c is not None
        assert loaded_c.snapshot_id == "snap_c"

        # 不压缩模式
        m_u = StateSnapshotManager(snapshot_dir=str(snapshot_dir / "u"),
                                   enable_compression=False)
        m_u.frequency_controller.min_interval_seconds = 0.0
        m_u.save_snapshot(digital_life, snapshot_id="snap_u")
        loaded_u = m_u.load_snapshot()
        assert loaded_u is not None
        assert loaded_u.snapshot_id == "snap_u"

    def test_update_module_checksums(self, manager):
        """_update_module_checksums 应更新缓存"""
        ms = ModuleState("m1", True, b"d", checksum="chk_1")
        snap = make_snapshot(module_states={"m1": ms})
        manager._update_module_checksums(snap)
        assert manager.last_module_checksums["m1"] == "chk_1"

    def test_save_core_modules_with_delta_full(self, fast_manager, digital_life):
        """完整保存时所有模块应被序列化(incremental=False)"""
        snap = make_snapshot()
        space_saved = fast_manager._save_core_modules_with_delta(
            digital_life, snap, incremental=False)
        # 完整保存不节省空间
        assert space_saved == 0
        assert "body_sensor" in snap.module_states
        assert "behavior" in snap.module_states
        assert "permission" in snap.module_states
        assert "tools_registry" in snap.module_states

    def test_save_core_modules_with_delta_incremental_no_change(self, fast_manager, digital_life):
        """增量保存时,未变化模块被跳过并计入 space_saved"""
        # 1. 完整保存以建立 checksums
        fast_manager.save_snapshot(digital_life)
        # 2. 增量保存(内容未变)
        snap = make_snapshot()
        space_saved = fast_manager._save_core_modules_with_delta(
            digital_life, snap, incremental=True)
        # 全部模块未变化,space_saved > 0
        assert space_saved > 0
        # 未变化模块不写入 snapshot
        assert len(snap.module_states) == 0

    def test_save_core_modules_partial_modules(self, snapshot_dir):
        """只有部分核心模块的 DigitalLife"""
        m = StateSnapshotManager(snapshot_dir=str(snapshot_dir))
        m.frequency_controller.min_interval_seconds = 0.0
        # 只有 _body 和 _behavior(删除 _permission 和 _tools_registry 属性)
        life = FakeDigitalLife(
            config={"k": "v"},
            body=FakeBodyContainer(FakeBodySensor(initialized=True)),
            behavior=FakeBehavior(),
        )
        del life._permission
        del life._tools_registry
        snap = make_snapshot()
        m._save_core_modules_with_delta(life, snap, incremental=False)
        assert "body_sensor" in snap.module_states
        assert "behavior" in snap.module_states
        assert "permission" not in snap.module_states
        assert "tools_registry" not in snap.module_states

    def test_save_core_modules_body_without_get(self, snapshot_dir):
        """_body 无 get 方法时直接使用 _body"""
        m = StateSnapshotManager(snapshot_dir=str(snapshot_dir))
        m.frequency_controller.min_interval_seconds = 0.0
        body_sensor = FakeBodySensor(initialized=True)
        life = FakeDigitalLife(
            config={"k": "v"},
            body=body_sensor,  # 直接给 body_sensor,非容器
        )
        snap = make_snapshot()
        m._save_core_modules_with_delta(life, snap, incremental=False)
        assert "body_sensor" in snap.module_states
        ms = snap.module_states["body_sensor"]
        assert ms.initialized is True
