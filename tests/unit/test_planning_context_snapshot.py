"""context_snapshot 单元测试 — 任务5 步骤2（验收3/4/5 + 边界 B1-B12）

覆盖：
- 验收3：round-trip save→restore 上下文 key 与 token 一致（B1）
- 验收4：超 keep 份自动轮转（B2）
- 验收5：快照写入失败主循环继续、不抛异常（B4）
- 边界：降级摘要（B3）、非 JSON 兜底（B5）、key 排序稳定（B6）、session 隔离（B7）、
  restore 缺失/损坏（B8）、mark 标记（B9）、step 覆盖幂等（B10）、空上下文（B11）、
  purge 精确性（B12）
"""

import json
from datetime import datetime

import pytest

from planning.context_snapshot import (
    MAX_BYTES,
    Snapshot,
    list_snapshots,
    mark_snapshot,
    purge_snapshots,
    restore_snapshot,
    save_snapshot,
    _serialize,
)


def _ctx(**kw):
    ctx = {"available_tools": ["t1", "t2"], "user_input": "构建报告", "n": 3}
    ctx.update(kw)
    return ctx


class TestRoundTrip:
    """B1 / 验收3：save → restore 一致"""

    def test_round_trip_keys(self, tmp_path):
        sid = save_snapshot("s1", 0, "任务", _ctx(), [{"action": "tool_a"}], 42,
                            snapshot_root=tmp_path)
        assert sid == "s1/step_0"
        restored = restore_snapshot(sid, snapshot_root=tmp_path)
        assert restored is not None
        assert restored["available_tools"] == ["t1", "t2"]
        assert restored["user_input"] == "构建报告"
        assert restored["n"] == 3

    def test_token_used_stored(self, tmp_path):
        save_snapshot("s1", 0, "任务", _ctx(), [], 123, snapshot_root=tmp_path)
        data = json.loads((tmp_path / "s1" / "step_0.json").read_text(encoding="utf-8"))
        assert data["token_used"] == 123
        assert data["session_id"] == "s1"
        assert data["task"] == "任务"

    def test_steps_summary_last_three(self, tmp_path):
        """步骤摘要仅保留最近 3 步（省 token）"""
        steps = [{"action": f"step_{i}"} for i in range(6)]
        save_snapshot("s1", 0, "任务", _ctx(), steps, 0, snapshot_root=tmp_path)
        data = json.loads((tmp_path / "s1" / "step_0.json").read_text(encoding="utf-8"))
        assert "step_3" in data["steps_summary"]
        assert "step_0" not in data["steps_summary"]


class TestRotation:
    """B2 / 验收4：超 keep 份自动轮转"""

    def test_rotate_keeps_recent(self, tmp_path):
        for i in range(25):
            save_snapshot("s1", i, "任务", _ctx(), [], 0, snapshot_root=tmp_path, keep=20)
        infos = list_snapshots("s1", tmp_path)
        assert len(infos) == 20
        assert infos[0]["step_index"] == 5      # 最旧 5 份（0-4）被删
        assert infos[-1]["step_index"] == 24
        assert not (tmp_path / "s1" / "step_0.json").exists()


class TestDegrade:
    """B3：大上下文降级为摘要模式"""

    def test_degrade_large_context(self, tmp_path):
        big = _ctx(big="x" * 10000)
        save_snapshot("s1", 0, "任务", big, [], 0, snapshot_root=tmp_path, max_bytes=1000)
        data = json.loads((tmp_path / "s1" / "step_0.json").read_text(encoding="utf-8"))
        assert data["degraded"] is True
        restored = restore_snapshot("s1/step_0", tmp_path)
        assert isinstance(restored["big"], dict)
        assert restored["big"]["type"] == "str"
        # 降级下小键仍保留原始类型路径（preview）
        assert "type" in restored["available_tools"]


class TestSaveFailure:
    """B4 / 验收5：写入失败不阻断（返回 "" 不抛异常）"""

    def test_save_failure_not_blocking(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")                 # 文件占用 root 路径 → mkdir 失败
        sid = save_snapshot("s1", 0, "任务", _ctx(), [], 0, snapshot_root=blocker)
        assert sid == ""

    def test_save_circular_reference_not_blocking(self, tmp_path):
        """循环引用经 _serialize 深度守卫摊平后可保存（不炸、不阻断）"""
        def _circular():
            d = {}
            d["self"] = d
            return d
        sid = save_snapshot("s1", 0, "任务", _circular(), [], 0, snapshot_root=tmp_path)
        assert sid != ""
        restored = restore_snapshot(sid, tmp_path)
        assert restored is not None          # 摊平为深度受限的嵌套 dict，可 round-trip


class TestSerialize:
    """B5 / B6：非 JSON 兜底 + key 排序稳定"""

    def test_non_json_types(self, tmp_path):
        ctx = {"dt": datetime(2026, 1, 1), "obj": object()}
        sid = save_snapshot("s1", 0, "任务", ctx, [], 0, snapshot_root=tmp_path)
        assert sid != ""
        restored = restore_snapshot(sid, tmp_path)
        assert isinstance(restored["dt"], str)
        assert isinstance(restored["obj"], str)

    def test_key_order_stable(self):
        a = _serialize({"b": 1, "a": 2, "nested": {"y": 1, "x": 2}})
        b = _serialize({"a": 2, "b": 1, "nested": {"x": 2, "y": 1}})
        assert a == b

    def test_deep_nesting_no_crash(self):
        deep = value = {"k": "v"}
        for _ in range(50):
            deep = {"child": deep}
        snapshot = Snapshot(session_id="s", step_index=0, task="t",
                            context=_serialize({"deep": deep}),
                            steps_summary="", token_used=0)
        json.dumps(snapshot.to_dict(), ensure_ascii=False)      # 不炸栈


class TestIsolation:
    """B7：session 隔离"""

    def test_session_isolation(self, tmp_path):
        save_snapshot("A", 0, "t", _ctx(), [], 0, snapshot_root=tmp_path)
        save_snapshot("B", 0, "t", _ctx(), [], 0, snapshot_root=tmp_path)
        infos_a = list_snapshots("A", tmp_path)
        infos_b = list_snapshots("B", tmp_path)
        assert len(infos_a) == 1 and len(infos_b) == 1
        assert infos_a[0]["state_id"].startswith("A/")
        assert infos_b[0]["state_id"].startswith("B/")


class TestRestoreFailure:
    """B8：缺失/损坏返回 None"""

    def test_restore_missing_returns_none(self, tmp_path):
        assert restore_snapshot("nope/step_0", tmp_path) is None

    def test_restore_corrupt_returns_none(self, tmp_path):
        p = tmp_path / "s1"
        p.mkdir()
        (p / "step_0.json").write_text("{invalid json", encoding="utf-8")
        assert restore_snapshot("s1/step_0", tmp_path) is None


class TestMark:
    """B9：恢复点标记"""

    def test_mark_snapshot(self, tmp_path):
        save_snapshot("s1", 0, "任务", _ctx(), [], 0, snapshot_root=tmp_path)
        assert mark_snapshot("s1/step_0", "good", tmp_path) is True
        meta = json.loads((tmp_path / "s1" / "meta.json").read_text(encoding="utf-8"))
        assert meta["mark"] == "good"
        assert meta["step_index"] == 0

    def test_mark_invalid_value(self, tmp_path):
        assert mark_snapshot("s1/step_0", "bogus", tmp_path) is False


class TestOverwrite:
    """B10：同 step 重复 save 幂等覆盖"""

    def test_overwrite_same_step(self, tmp_path):
        save_snapshot("s1", 0, "任务", _ctx(), [], 0, snapshot_root=tmp_path)
        save_snapshot("s1", 0, "任务", _ctx(), [], 0, snapshot_root=tmp_path)
        assert len(list_snapshots("s1", tmp_path)) == 1


class TestEmpty:
    """B11：空 context/steps"""

    def test_empty_context(self, tmp_path):
        sid = save_snapshot("s1", 0, "任务", {}, [], 0, snapshot_root=tmp_path)
        assert sid != ""
        assert restore_snapshot(sid, tmp_path) == {}


class TestPurge:
    """B12：purge 精确性"""

    def test_purge_precision(self, tmp_path):
        for i in range(6):
            save_snapshot("s1", i, "任务", _ctx(), [], 0, snapshot_root=tmp_path)
        removed = purge_snapshots("s1", keep=3, snapshot_root=tmp_path)
        assert removed == 3
        infos = list_snapshots("s1", tmp_path)
        assert [x["step_index"] for x in infos] == [3, 4, 5]
        assert len(infos) == 3

    def test_purge_under_keep_removes_none(self, tmp_path):
        for i in range(2):
            save_snapshot("s1", i, "任务", _ctx(), [], 0, snapshot_root=tmp_path)
        assert purge_snapshots("s1", keep=5, snapshot_root=tmp_path) == 0
