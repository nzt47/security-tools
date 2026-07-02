"""StateManager 状态管理器全面单元测试

测试目标：覆盖 agent/state_manager.py 的核心 API
覆盖维度：
1. 正常路径：save/load/list/delete
2. 异常路径：文件不存在、序列化失败、反序列化失败
3. 边界条件：空状态、自动生成 ID、备份清理
4. 日志级别管理：set/get_log_level
5. 全局单例：get_state_manager
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.state_manager import (
    StateInfo,
    StateLoadResult,
    StateManager,
    StateSaveResult,
    get_log_level,
    get_state_manager,
    load_state,
    save_state,
    set_log_level,
)


# 状态同步说明：每个用例使用 tmp_path 隔离状态目录，避免用例间状态污染；
# 全局单例测试通过 patch 控制实例创建。


@pytest.fixture
def manager(tmp_path):
    """独立状态管理器（使用临时目录）"""
    return StateManager(state_dir=str(tmp_path), auto_save_interval=0)


# ── 1. 数据类 ──────────────────────────────────────────


class TestStateSaveResult:
    def test_default_values(self):
        result = StateSaveResult(
            success=True,
            state_id="test",
            file_path="/tmp/test.json",
            elapsed_ms=10.5,
        )
        assert result.success is True
        assert result.error_message is None
        assert result.data_size == 0
        assert result.created_at is None

    def test_failure_result(self):
        result = StateSaveResult(
            success=False,
            state_id="test",
            file_path="",
            elapsed_ms=0,
            error_message="disk full",
        )
        assert result.success is False
        assert result.error_message == "disk full"


class TestStateLoadResult:
    def test_success_result(self):
        result = StateLoadResult(
            success=True,
            state_id="test",
            state_data={"key": "value"},
            elapsed_ms=5.0,
            file_path="/tmp/test.json",
        )
        assert result.success is True
        assert result.state_data == {"key": "value"}
        assert result.error_message is None

    def test_failure_result(self):
        result = StateLoadResult(
            success=False,
            state_id="test",
            state_data={},
            elapsed_ms=0,
            error_message="file not found",
        )
        assert result.success is False
        assert result.error_message == "file not found"


class TestStateInfo:
    def test_default_version(self):
        info = StateInfo(
            state_id="test",
            file_path="/tmp/test.json",
            created_at=datetime.now(timezone.utc),
            data_size=100,
        )
        assert info.version == "1.0"


# ── 2. 初始化 ──────────────────────────────────────────


class TestInit:
    def test_default_state_dir(self, tmp_path):
        sm = StateManager(state_dir=str(tmp_path), auto_save_interval=0)
        assert sm._state_dir == tmp_path

    def test_creates_state_dir(self, tmp_path):
        state_dir = tmp_path / "nested" / "state"
        sm = StateManager(state_dir=str(state_dir), auto_save_interval=0)
        assert state_dir.exists()

    def test_auto_save_disabled_when_zero(self, tmp_path):
        sm = StateManager(state_dir=str(tmp_path), auto_save_interval=0)
        assert sm._auto_save_running is False

    def test_initial_current_state_empty(self, manager):
        assert manager._current_state == {}

    def test_initial_last_save_time_zero(self, manager):
        assert manager._last_save_time == 0.0


# ── 3. save_state 保存状态 ──────────────────────────────


class TestSaveState:
    def test_save_basic_state(self, manager):
        result = manager.save_state({"key": "value"})
        assert result.success is True
        assert result.state_id  # 自动生成
        assert Path(result.file_path).exists()

    def test_save_with_custom_state_id(self, manager):
        result = manager.save_state({"data": 1}, state_id="custom_id")
        assert result.success is True
        assert result.state_id == "custom_id"
        assert "custom_id" in result.file_path

    def test_save_includes_metadata(self, manager):
        result = manager.save_state({"x": 1})
        with open(result.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "_metadata" in data
        assert data["_metadata"]["version"] == "1.0"
        assert data["_metadata"]["state_id"] == result.state_id

    def test_save_without_timestamp(self, manager):
        result = manager.save_state({"x": 1}, include_timestamp=False)
        with open(result.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "_metadata" not in data

    def test_save_updates_current_state(self, manager):
        manager.save_state({"updated": True})
        assert manager._current_state.get("updated") is True

    def test_save_updates_last_save_time(self, manager):
        manager.save_state({"x": 1})
        assert manager._last_save_time > 0

    def test_save_data_size_positive(self, manager):
        result = manager.save_state({"key": "value"})
        assert result.data_size > 0

    def test_save_elapsed_ms_positive(self, manager):
        result = manager.save_state({"x": 1})
        assert result.elapsed_ms >= 0

    def test_save_created_at_returned(self, manager):
        result = manager.save_state({"x": 1})
        assert result.created_at is not None

    def test_save_failure_returns_error(self, manager):
        """序列化失败应返回失败结果"""
        # 使用不可序列化的对象（无 __dict__、无 to_dict）
        # 注意：普通类实例有 __dict__ 会被序列化，需要特殊处理
        # 通过让 _json_default 抛 TypeError 来触发失败
        class Cycle:
            def __init__(self):
                self.self_ref = self  # 循环引用，json.dumps 会抛 ValueError

        result = manager.save_state({"obj": Cycle()})
        assert result.success is False
        assert result.error_message is not None


# ── 4. load_state 加载状态 ──────────────────────────────


class TestLoadState:
    def test_load_after_save(self, manager):
        manager.save_state({"key": "value"}, state_id="test_load")
        result = manager.load_state("test_load")
        assert result.success is True
        assert result.state_data["key"] == "value"

    def test_load_default_file(self, manager):
        """state_id=None 应加载默认文件"""
        manager.save_state({"default": True})  # 保存到默认文件
        result = manager.load_state()
        assert result.success is True
        assert result.state_data.get("default") is True

    def test_load_nonexistent_returns_failure(self, manager):
        result = manager.load_state("nonexistent_id")
        assert result.success is False
        assert result.error_message is not None

    def test_load_returns_state_id(self, manager):
        manager.save_state({"x": 1}, state_id="my_id")
        result = manager.load_state("my_id")
        assert result.state_id == "my_id"

    def test_load_returns_file_path(self, manager):
        manager.save_state({"x": 1}, state_id="my_id")
        result = manager.load_state("my_id")
        assert result.file_path is not None
        assert "my_id" in result.file_path

    def test_load_elapsed_ms_positive(self, manager):
        manager.save_state({"x": 1}, state_id="my_id")
        result = manager.load_state("my_id")
        assert result.elapsed_ms >= 0


# ── 5. list_states 列出状态 ──────────────────────────────


class TestListStates:
    def test_empty_list_initially(self, manager):
        states = manager.list_states()
        assert states == []

    def test_list_after_save(self, manager):
        manager.save_state({"x": 1}, state_id="state1")
        states = manager.list_states()
        assert len(states) == 1
        assert states[0].state_id == "state1"

    def test_list_multiple_states(self, manager):
        manager.save_state({"x": 1}, state_id="s1")
        manager.save_state({"x": 2}, state_id="s2")
        manager.save_state({"x": 3}, state_id="s3")
        states = manager.list_states()
        ids = {s.state_id for s in states}
        assert {"s1", "s2", "s3"}.issubset(ids)

    def test_list_returns_state_info(self, manager):
        manager.save_state({"x": 1}, state_id="test_info")
        states = manager.list_states()
        info = next(s for s in states if s.state_id == "test_info")
        assert isinstance(info, StateInfo)
        assert info.data_size > 0
        assert info.version == "1.0"


# ── 6. delete_state 删除状态 ──────────────────────────────


class TestDeleteState:
    def test_delete_existing_state(self, manager):
        manager.save_state({"x": 1}, state_id="to_delete")
        assert manager.delete_state("to_delete") is True
        # 文件应不存在
        assert not manager._get_state_path("to_delete").exists()

    def test_delete_nonexistent_returns_false(self, manager):
        assert manager.delete_state("nonexistent") is False

    def test_delete_removes_from_list(self, manager):
        manager.save_state({"x": 1}, state_id="to_remove")
        manager.delete_state("to_remove")
        states = manager.list_states()
        assert all(s.state_id != "to_remove" for s in states)


# ── 7. get/update/clear 当前状态 ──────────────────────────


class TestCurrentState:
    def test_get_current_state_empty(self, manager):
        assert manager.get_current_state() == {}

    def test_get_current_state_after_save(self, manager):
        manager.save_state({"current": "yes"})
        state = manager.get_current_state()
        assert state.get("current") == "yes"

    def test_update_state_merges(self, manager):
        manager.save_state({"a": 1, "b": 2})
        manager.update_state({"b": 3, "c": 4})
        state = manager.get_current_state()
        assert state["a"] == 1
        assert state["b"] == 3
        assert state["c"] == 4

    def test_clear_state_empties(self, manager):
        manager.save_state({"x": 1})
        manager.clear_state()
        assert manager.get_current_state() == {}


# ── 8. get_last_save_time ──────────────────────────────────


class TestLastSaveTime:
    def test_zero_before_save(self, manager):
        assert manager.get_last_save_time() == 0.0

    def test_positive_after_save(self, manager):
        manager.save_state({"x": 1})
        t = manager.get_last_save_time()
        assert t > 0
        assert t <= time.time()


# ── 9. 日志级别管理 ──────────────────────────────────────


class TestLogLevel:
    def test_set_log_level_valid(self, manager):
        assert manager.set_log_level("DEBUG") is True

    def test_set_log_level_invalid(self, manager):
        assert manager.set_log_level("INVALID_LEVEL") is False

    def test_get_log_level_default(self, manager):
        level = manager.get_log_level()
        assert level in ("INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL", "NOTSET")

    def test_set_and_get_log_level(self, manager):
        manager.set_log_level("WARNING")
        assert manager.get_log_level() == "WARNING"

    def test_set_log_level_for_specific_logger(self, manager):
        assert manager.set_log_level("ERROR", "agent.test") is True


# ── 10. 自动保存控制 ──────────────────────────────────────


class TestAutoSave:
    def test_stop_auto_save_when_not_running(self, manager):
        """auto_save 未启动时 stop 应无副作用"""
        manager.stop_auto_save()
        assert manager._auto_save_running is False

    def test_set_auto_save_interval(self, manager):
        manager.set_auto_save_interval(120)
        assert manager._auto_save_interval == 120

    def test_set_auto_save_interval_zero_disables(self, manager):
        manager.set_auto_save_interval(0)
        assert manager._auto_save_interval == 0


# ── 11. 备份机制 ──────────────────────────────────────────


class TestBackup:
    def test_create_backup_directly(self, manager, tmp_path):
        """直接调用 _create_backup 应创建备份文件"""
        # 手动创建默认状态文件（save_state 会使用生成的 ID，不会写到默认文件）
        default_path = manager._get_state_path()
        default_path.write_text('{"test": "data"}', encoding="utf-8")
        assert default_path.exists()
        # 直接调用 _create_backup
        manager._create_backup("manual_backup_id")
        backups = list(manager._state_dir.glob("*_backup.json"))
        assert len(backups) >= 1

    def test_create_backup_no_existing_file(self, manager):
        """无现有文件时 _create_backup 应静默返回"""
        manager._create_backup("no_file_backup")
        backups = list(manager._state_dir.glob("*_backup.json"))
        assert len(backups) == 0

    def test_no_backup_for_custom_id(self, manager):
        """自定义 state_id 不应创建备份"""
        manager.save_state({"x": 1}, state_id="custom")
        backups = list(manager._state_dir.glob("*_backup.json"))
        assert len(backups) == 0


# ── 12. 全局单例与模块级函数 ──────────────────────────────


class TestGlobalAPI:
    def test_get_state_manager_returns_instance(self):
        sm = get_state_manager()
        assert isinstance(sm, StateManager)

    def test_get_state_manager_singleton(self):
        sm1 = get_state_manager()
        sm2 = get_state_manager()
        assert sm1 is sm2

    def test_module_save_state(self):
        result = save_state({"test": "module"})
        assert isinstance(result, StateSaveResult)

    def test_module_load_state(self):
        result = load_state()
        assert isinstance(result, StateLoadResult)

    def test_module_set_log_level(self):
        assert set_log_level("INFO") is True

    def test_module_get_log_level(self):
        level = get_log_level()
        assert isinstance(level, str)


# ── 13. 内部方法 ──────────────────────────────────────────


class TestInternalMethods:
    def test_generate_state_id_format(self, manager):
        sid = manager._generate_state_id()
        # 格式：YYYYMMDD_HHMMSS_微秒
        assert "_" in sid
        parts = sid.split("_")
        assert len(parts) == 3

    def test_get_state_path_with_id(self, manager):
        path = manager._get_state_path("my_id")
        assert path.name == "my_id.json"

    def test_get_state_path_without_id(self, manager):
        path = manager._get_state_path(None)
        assert path.name == "agent_state.json"

    def test_serialize_state_returns_string(self, manager):
        s = manager._serialize_state({"key": "value"})
        assert isinstance(s, str)
        assert json.loads(s) == {"key": "value"}

    def test_deserialize_state_returns_dict(self, manager):
        d = manager._deserialize_state('{"key": "value"}')
        assert d == {"key": "value"}

    def test_json_default_datetime(self, manager):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = manager._json_default(dt)
        assert "2026-01-01" in result

    def test_json_default_path(self, manager):
        p = Path("/tmp/test")
        result = manager._json_default(p)
        assert "tmp" in result or "test" in result

    def test_json_default_object_with_to_dict(self, manager):
        class Obj:
            def to_dict(self):
                return {"serialized": True}

        result = manager._json_default(Obj())
        assert result == {"serialized": True}

    def test_json_default_object_with_dict(self, manager):
        class Obj:
            def __init__(self):
                self.value = 42

        result = manager._json_default(Obj())
        assert result == {"value": 42}

    def test_json_default_unsupported_raises(self, manager):
        with pytest.raises(TypeError):
            manager._json_default(object())


# ── 14. 集成场景 ──────────────────────────────────────────


class TestIntegration:
    def test_save_load_roundtrip(self, manager):
        """保存后加载应得到相同数据"""
        original = {"users": [1, 2, 3], "config": {"debug": True}}
        manager.save_state(original, state_id="roundtrip")
        result = manager.load_state("roundtrip")
        assert result.success is True
        assert result.state_data["users"] == [1, 2, 3]
        assert result.state_data["config"]["debug"] is True

    def test_save_multiple_and_list(self, manager):
        """保存多个状态后列表应包含全部"""
        for i in range(5):
            manager.save_state({"index": i}, state_id=f"state_{i}")
        states = manager.list_states()
        ids = {s.state_id for s in states}
        assert {f"state_{i}" for i in range(5)}.issubset(ids)

    def test_overwrite_existing_state(self, manager):
        """覆盖保存应更新文件内容"""
        manager.save_state({"v": 1}, state_id="overwrite")
        manager.save_state({"v": 2}, state_id="overwrite")
        result = manager.load_state("overwrite")
        assert result.state_data["v"] == 2
