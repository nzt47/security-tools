"""SkillStore 持锁 I/O 重构验证测试

验证目标（【不易】持锁操作严禁包含 I/O）:
    1. upsert/remove/clear 的 _persist 在锁外调用
    2. merge_skills 的 legacy 同步 + feedback 改绑在锁外调用
    3. _persist 失败时 _cache 已更新（内存先行）
    4. 并发 merge 内存无交错损坏
    5. 冷加载（文件不存在）不隐式写文件（懒初始化）

运行:
    python -m pytest tests/unit/test_skill_store_lock_scope.py -v
"""
from __future__ import annotations

import json
import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.skills_mgmt.store import SkillStore
from agent.skills_mgmt.models import Skill


def _make_skill(skill_id: str, **kwargs) -> Skill:
    """构造最小 Skill 对象（与 test_skill_merge.py 风格一致）"""
    defaults = dict(
        name=f"{skill_id} 名称",
        description=f"{skill_id} 描述",
        content="print('hello')",
        tags=[],
        enabled=True,
    )
    defaults.update(kwargs)
    return Skill(id=skill_id, **defaults)


def _assert_lock_free(store: SkillStore, what: str):
    """断言当前调用未持有锁（若锁被持有则 acquire(False) 失败）"""
    acquired = store._lock.acquire(blocking=False)
    assert acquired, f"{what} 调用时锁仍被持有（持锁 I/O 未消除）"
    store._lock.release()


class TestPersistOutsideLock:
    """核心：落盘 I/O 在锁外执行"""

    def test_upsert_persist_outside_lock(self, tmp_path):
        """upsert 调用 _persist 时锁未持有"""
        store = SkillStore(path=str(tmp_path / "skills.json"))
        real_persist = store._persist

        def wrapped():
            _assert_lock_free(store, "upsert->_persist")
            real_persist()

        store._persist = wrapped
        store.upsert(_make_skill("s1"))
        assert store.get("s1") is not None

    def test_remove_persist_outside_lock(self, tmp_path):
        """remove 调用 _persist 时锁未持有"""
        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(_make_skill("s1"))
        real_persist = store._persist

        def wrapped():
            _assert_lock_free(store, "remove->_persist")
            real_persist()

        store._persist = wrapped
        assert store.remove("s1") is True

    def test_clear_persist_outside_lock(self, tmp_path):
        """clear 调用 _persist 时锁未持有"""
        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(_make_skill("s1"))
        real_persist = store._persist

        def wrapped():
            _assert_lock_free(store, "clear->_persist")
            real_persist()

        store._persist = wrapped
        store.clear()
        assert store.count() == 0


class TestMergeLockScope:
    """merge_skills 的 I/O 全部在锁外"""

    def _merge_ready_store(self, tmp_path):
        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(_make_skill("src1", name="源技能", tags=["a"]))
        store.upsert(_make_skill("dst1", name="目标技能", tags=["b"]))
        return store

    def test_merge_persist_outside_lock(self, tmp_path):
        """merge_skills->_persist 时锁未持有"""
        store = self._merge_ready_store(tmp_path)
        real_persist = store._persist

        def wrapped():
            _assert_lock_free(store, "merge->_persist")
            real_persist()

        store._persist = wrapped
        result = store.merge_skills("src1", "dst1")
        assert result["merged_id"] == "dst1"
        assert result["removed_id"] == "src1"

    def test_merge_legacy_sync_outside_lock(self, tmp_path):
        """merge_skills->sync_to_legacy_skills_json 时锁未持有"""
        store = self._merge_ready_store(tmp_path)
        with patch.object(store, "sync_to_legacy_skills_json",
                          side_effect=lambda: _assert_lock_free(
                              store, "merge->sync_to_legacy")) as mock_sync:
            store.merge_skills("src1", "dst1")
            mock_sync.assert_called_once()

    def test_merge_feedback_rebind_outside_lock(self, tmp_path):
        """merge_skills->_rebind_feedback 时锁未持有"""
        store = self._merge_ready_store(tmp_path)
        fb = MagicMock()
        with patch.object(store, "_rebind_feedback",
                          side_effect=lambda fm, **kw: _assert_lock_free(
                              store, "merge->_rebind_feedback") or 1) as mock_rebind:
            result = store.merge_skills("src1", "dst1", feedback_manager=fb)
            mock_rebind.assert_called_once()
            assert result["feedback_rebound_count"] == 1


class TestPersistFailure:
    """落盘失败时内存先行（保持现状语义）"""

    def test_upsert_persist_failure_keeps_cache(self, tmp_path):
        """_persist 抛异常 → _cache 已更新，异常向上传播"""
        store = SkillStore(path=str(tmp_path / "skills.json"))
        with patch.object(store, "_persist", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                store.upsert(_make_skill("s1"))
        # 内存已更新（数据不丢），落盘失败
        assert store.get("s1") is not None


class TestConcurrency:
    """并发写内存原子性"""

    def test_concurrent_merge_no_corruption(self, tmp_path):
        """并发 merge 不同技能对 → _cache 无交错损坏"""
        store = SkillStore(path=str(tmp_path / "skills.json"))
        for i in range(6):
            store.upsert(_make_skill(f"src{i}", tags=[f"t{i}"]))
            store.upsert(_make_skill(f"dst{i}", tags=[f"d{i}"]))
        store._persist = MagicMock()  # 关闭真实落盘，专注内存原子性

        errors = []

        def do_merge(i):
            try:
                store.merge_skills(f"src{i}", f"dst{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=do_merge, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 merge 异常: {errors}"
        data = store._load()
        # 每个 dst 保留、src 删除；6 组互不干扰
        assert len(data) == 6
        for i in range(6):
            assert f"dst{i}" in data
            assert f"src{i}" not in data


class TestColdLoadLazyInit:
    """冷加载懒初始化：不隐式写文件"""

    def test_cold_load_no_implicit_write(self, tmp_path):
        """文件不存在时 list_all 返回空且不创建文件"""
        store_path = tmp_path / "skills.json"
        store = SkillStore(path=str(store_path))
        assert store.list_all() == []
        # 懒初始化：只读路径不落盘，文件不应被隐式创建
        assert not store_path.exists()

    def test_first_write_creates_file(self, tmp_path):
        """首次写操作触发落盘建文件"""
        store_path = tmp_path / "skills.json"
        store = SkillStore(path=str(store_path))
        store.upsert(_make_skill("s1"))
        assert store_path.exists()
        with open(store_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "s1" in data


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
