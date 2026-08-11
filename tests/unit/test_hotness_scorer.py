"""HotnessScorer 记忆热度计算模块单元测试

覆盖 agent/memory/hotness_scorer.py 的全部方法:
  - record_access: 访问计数记录（内存缓存 / LRU 兜底 / 空 key 短路）
  - compute_hotness: 热度公式各分支（零访问 / 默认 importance / 时间戳缺失/非法/未来）
  - get_hot_records: Top-N 查询（无 adapter / 读取失败 / 空表 / 缓存覆盖 / 埋点成败）
  - run_background_scan_once: 后台扫描持久化（分批次 / 无 key 跳过 / 批量失败降级）
  - _batch_update_hotness: 批量 UPDATE 落库
  - start_background_scan / stop_background_scan / _scan_loop: 后台线程生命周期

设计原则: adapter 用 FakeAdapter 隔离 SQLite（仅实现 get_raw_memories_all /
_get_conn / _lock / _CONTENT_TABLE 最小接口），时间用 mock 固定，后台线程
测试结束后统一 stop 并 join，无阻塞等待。
"""
# pylint: disable=redefined-outer-name,missing-function-docstring,protected-access

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

import agent.memory.hotness_scorer as hotness_scorer_mod
from agent.memory.hotness_scorer import HotnessScorer


class _FakeConn:
    """模拟 sqlite3 连接: 记录 executemany 调用, 支持上下文管理器"""

    def __init__(self, adapter):
        self.adapter = adapter

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, params):
        self.adapter.batches.append((sql, list(params)))

    def commit(self):
        self.adapter.commit_count += 1


class FakeAdapter:
    """模拟 HolographicAdapter: 提供 hotness_scorer 依赖的最小接口"""

    _CONTENT_TABLE = "memory_items"

    def __init__(self, records=None):
        self._records = [dict(r) for r in (records or [])]
        self._lock = threading.Lock()
        self.batches = []
        self.commit_count = 0

    def get_raw_memories_all(self):
        return [dict(r) for r in self._records]

    def _get_conn(self):
        return _FakeConn(self)


@pytest.fixture
def scorer():
    """无 adapter 的 HotnessScorer（仅纯函数能力）"""
    return HotnessScorer(adapter=None)


# ═══════════════════════════════════════════════════════════
#  record_access
# ═══════════════════════════════════════════════════════════

class TestRecordAccess:
    """record_access 访问事件记录"""

    def test_empty_key_noop(self, scorer):
        """空 key 应直接返回, 不写缓存"""
        scorer.record_access("")
        scorer.record_access(None)
        assert scorer._access_cache == {}

    def test_new_key_default_timestamp(self, scorer):
        """新 key 应记录 [1, 当前时间戳]（时间戳默认取 time.time）"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=1000.0):
            scorer.record_access("k1")
        assert scorer._access_cache["k1"] == [1, 1000.0]

    def test_new_key_explicit_timestamp(self, scorer):
        """显式传入时间戳应被采用"""
        scorer.record_access("k1", 42.5)
        assert scorer._access_cache["k1"] == [1, 42.5]

    def test_existing_key_increments(self, scorer):
        """已有 key 应累加计数并更新访问时间"""
        scorer.record_access("k1", 100.0)
        scorer.record_access("k1", 200.0)
        scorer.record_access("k1", 300.0)
        assert scorer._access_cache["k1"] == [3, 300.0]

    def test_cache_lru_eviction(self, scorer):
        """缓存达到容量上限时, 新 key 应触发 FIFO 近似 LRU 弹出最早项"""
        for i in range(HotnessScorer._CACHE_MAX_SIZE):
            scorer._access_cache[f"pre_{i}"] = [1, 0.0]
        scorer.record_access("new_key", 123.0)
        assert "pre_0" not in scorer._access_cache
        assert scorer._access_cache["new_key"] == [1, 123.0]
        assert len(scorer._access_cache) == HotnessScorer._CACHE_MAX_SIZE


# ═══════════════════════════════════════════════════════════
#  compute_hotness
# ═══════════════════════════════════════════════════════════

class TestComputeHotness:
    """compute_hotness 热度公式各分支"""

    def test_zero_access_count(self, scorer):
        """access_count <= 0 时热度应为 0（无访问不进热数据层）"""
        assert scorer.compute_hotness({"access_count": 0}) == 0.0
        assert scorer.compute_hotness({}) == 0.0
        assert scorer.compute_hotness({"access_count": -3}) == 0.0

    def test_missing_last_accessed_uses_zero_hours(self, scorer):
        """last_accessed 缺失时 hours_since=0, 热度 = importance*count"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=2000.0):
            hot = scorer.compute_hotness({"importance": 2.0, "access_count": 5})
        assert hot == pytest.approx(10.0)

    def test_formula_with_recent_access(self, scorer):
        """公式验证: hotness = importance*count/(hours+1)^decay, hours_since=1"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=10000.0):
            hot = scorer.compute_hotness(
                {"importance": 2.0, "access_count": 5, "last_accessed": 10000.0 - 3600.0})
        assert hot == pytest.approx(10.0 / (2.0 ** 1.5))

    def test_default_importance(self, scorer):
        """importance 缺失时兜底为 1.0"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=2000.0):
            hot = scorer.compute_hotness({"access_count": 3, "last_accessed": 2000.0})
        assert hot == pytest.approx(3.0)

    def test_zero_importance_uses_default(self, scorer):
        """importance 为 0（falsy）时应走默认值 1.0"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=2000.0):
            hot = scorer.compute_hotness({"importance": 0, "access_count": 4,
                                          "last_accessed": 2000.0})
        assert hot == pytest.approx(4.0)

    def test_invalid_last_accessed(self, scorer):
        """last_accessed 非法（ValueError/TypeError）时应视为刚访问（hours=0）"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=2000.0):
            hot1 = scorer.compute_hotness({"access_count": 4, "last_accessed": "abc"})
            hot2 = scorer.compute_hotness({"access_count": 4, "last_accessed": {"x": 1}})
        assert hot1 == pytest.approx(4.0)
        assert hot2 == pytest.approx(4.0)

    def test_zero_last_accessed(self, scorer):
        """last_accessed 为 0 时 hours_since 应为 0"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=2000.0):
            hot = scorer.compute_hotness({"access_count": 4, "last_accessed": 0})
        assert hot == pytest.approx(4.0)

    def test_future_timestamp_clamps_to_zero(self, scorer):
        """未来时间戳（负 hours）应钳制为 0"""
        with patch.object(hotness_scorer_mod.time, "time", return_value=2000.0):
            hot = scorer.compute_hotness({"access_count": 4, "last_accessed": 999999.0})
        assert hot == pytest.approx(4.0)


# ═══════════════════════════════════════════════════════════
#  get_hot_records
# ═══════════════════════════════════════════════════════════

class TestGetHotRecords:
    """get_hot_records Top-N 热数据查询"""

    def test_no_adapter_returns_empty(self, scorer):
        """未注入 adapter 时应降级返回空列表"""
        assert scorer.get_hot_records() == []

    def test_non_positive_top_n(self):
        """top_n <= 0 时应返回空列表"""
        s = HotnessScorer(adapter=FakeAdapter(records=[{"key": "k1", "access_count": 1}]))
        assert s.get_hot_records(top_n=0) == []
        assert s.get_hot_records(top_n=-1) == []

    def test_read_failure_returns_empty(self):
        """adapter 读取全表抛异常时应降级返回空列表"""
        adapter = MagicMock()
        adapter.get_raw_memories_all.side_effect = RuntimeError("db down")
        s = HotnessScorer(adapter=adapter)
        assert s.get_hot_records() == []

    def test_empty_records(self):
        """空表应返回空列表"""
        s = HotnessScorer(adapter=FakeAdapter(records=[]))
        assert s.get_hot_records() == []

    def test_ranking_and_cache_override(self):
        """应按 hotness 降序排序, 且内存缓存覆盖 access_count/last_accessed"""
        adapter = FakeAdapter(records=[
            {"key": "k1", "access_count": 1},
            {"key": "k2", "access_count": 5},
        ])
        s = HotnessScorer(adapter=adapter)
        s.record_access("k1", 100.0)  # 缓存覆盖 k1
        with patch.object(hotness_scorer_mod.time, "time", return_value=100.0):
            top = s.get_hot_records(top_n=2)
        assert [r["key"] for r in top] == ["k2", "k1"]
        # k1 的 access_count 被缓存覆盖为 1, last_accessed=100 → hours_since=0
        assert top[1]["access_count"] == 1
        assert top[1]["last_accessed"] == 100.0
        assert top[1]["hotness"] == pytest.approx(1.0)
        assert top[0]["hotness"] == pytest.approx(5.0)

    def test_tracking_metric_called(self):
        """埋点函数应被每个返回的 top-N 记录调用"""
        adapter = FakeAdapter(records=[{"key": "k1", "access_count": 2}])
        s = HotnessScorer(adapter=adapter)
        with patch("agent.memory.observability.track_tlm_hotness_score",
                   create=True) as mock_track:
            top = s.get_hot_records(top_n=1)
        assert len(top) == 1
        mock_track.assert_called_once_with("k1", top[0]["hotness"])

    def test_tracking_metric_failure_ignored(self):
        """埋点抛异常应被吞掉, 不影响 Top-N 结果"""
        adapter = FakeAdapter(records=[{"key": "k1", "access_count": 2}])
        s = HotnessScorer(adapter=adapter)
        with patch("agent.memory.observability.track_tlm_hotness_score",
                   create=True, side_effect=RuntimeError("metrics down")):
            top = s.get_hot_records(top_n=1)
        assert len(top) == 1
        assert top[0]["key"] == "k1"


# ═══════════════════════════════════════════════════════════
#  run_background_scan_once / _batch_update_hotness
# ═══════════════════════════════════════════════════════════

class TestRunBackgroundScanOnce:
    """run_background_scan_once 全表热度重算并持久化"""

    def test_no_adapter_returns_zero(self, scorer):
        """未注入 adapter 时应返回 0"""
        assert scorer.run_background_scan_once() == 0

    def test_read_failure_returns_zero(self):
        """读取全表抛异常时应返回 0"""
        adapter = MagicMock()
        adapter.get_raw_memories_all.side_effect = RuntimeError("db down")
        s = HotnessScorer(adapter=adapter)
        assert s.run_background_scan_once() == 0

    def test_empty_records_returns_zero(self):
        """空表应返回 0 且不产生任何 UPDATE"""
        adapter = FakeAdapter(records=[])
        s = HotnessScorer(adapter=adapter)
        assert s.run_background_scan_once() == 0
        assert adapter.batches == []

    def test_batch_update_success(self):
        """多条记录应按 batch_size 分批 UPDATE 并 commit, 返回更新条数"""
        adapter = FakeAdapter(records=[
            {"key": "k1", "access_count": 1},
            {"key": "k2", "access_count": 5},
            {"key": "k3", "access_count": 2},
        ])
        s = HotnessScorer(adapter=adapter, batch_size=2)
        s.record_access("k2", 0.0)  # 缓存覆盖 k2
        updated = s.run_background_scan_once()
        assert updated == 3
        assert len(adapter.batches) == 2  # 3 条按 batch_size=2 分两批
        sql, params = adapter.batches[0]
        assert "UPDATE memory_items SET hotness = ? WHERE key = ?" in sql
        assert len(params) == 2
        assert len(adapter.batches[1][1]) == 1
        assert adapter.commit_count == 2

    def test_skip_records_without_key_and_empty_batch(self):
        """无 key 记录应跳过; 整批无有效记录时应 continue 不产生 UPDATE"""
        adapter = FakeAdapter(records=[{}, {}, {"key": "k1", "access_count": 3}])
        s = HotnessScorer(adapter=adapter, batch_size=2)
        updated = s.run_background_scan_once()
        assert updated == 1
        assert len(adapter.batches) == 1

    def test_batch_update_failure_continues(self):
        """批量 UPDATE 抛异常时应捕获并继续, 更新计数不累加"""

        class _FailingConn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def executemany(self, sql, params):
                raise RuntimeError("write fail")

            def commit(self):
                pass

        class _FailingAdapter(FakeAdapter):
            def _get_conn(self):
                return _FailingConn()

        adapter = _FailingAdapter(records=[
            {"key": "k1", "access_count": 1},
            {"key": "k2", "access_count": 2},
        ])
        s = HotnessScorer(adapter=adapter, batch_size=2)
        assert s.run_background_scan_once() == 0


class TestBatchUpdateHotness:
    """_batch_update_hotness 批量落库"""

    def test_empty_updates_noop(self, scorer):
        """空 updates 列表应直接返回, 不产生 SQL"""
        scorer.adapter = FakeAdapter()
        scorer._batch_update_hotness([])
        assert scorer.adapter.batches == []

    def test_no_adapter_noop(self, scorer):
        """adapter 为 None 时应直接返回"""
        scorer._batch_update_hotness([(1.0, "k1")])
        assert True  # 不抛异常即通过

    def test_executemany_and_commit(self):
        """应通过 adapter 连接 executemany 批量写入并 commit"""
        adapter = FakeAdapter()
        s = HotnessScorer(adapter=adapter)
        s._batch_update_hotness([(1.5, "k1"), (2.5, "k2")])
        assert len(adapter.batches) == 1
        sql, params = adapter.batches[0]
        assert "UPDATE memory_items SET hotness = ? WHERE key = ?" in sql
        assert params == [(1.5, "k1"), (2.5, "k2")]
        assert adapter.commit_count == 1


# ═══════════════════════════════════════════════════════════
#  后台扫描线程
# ═══════════════════════════════════════════════════════════

class TestBackgroundScanThread:
    """start_background_scan / stop_background_scan / _scan_loop"""

    def test_start_already_running(self):
        """扫描线程已在运行时应直接返回 True 且不重建线程"""
        s = HotnessScorer(adapter=FakeAdapter())
        fake_thread = SimpleNamespace(is_alive=lambda: True)
        s._scan_thread = fake_thread
        assert s.start_background_scan() is True
        assert s._scan_thread is fake_thread

    def test_start_without_adapter_returns_false(self, scorer):
        """未注入 adapter 时应返回 False 且不启动线程"""
        assert scorer.start_background_scan() is False
        assert scorer._scan_thread is None

    def test_start_stop_roundtrip(self):
        """真实启动后台线程应执行一次扫描, stop 后线程退出"""
        adapter = FakeAdapter(records=[{"key": "k1", "access_count": 1}])
        s = HotnessScorer(adapter=adapter, scan_interval=0.01)
        assert s.start_background_scan() is True
        assert s._scan_thread is not None
        assert s._scan_thread.is_alive()
        s.stop_background_scan()
        assert s._scan_thread is None

    def test_stop_without_thread(self, scorer):
        """从未启动线程时 stop 应安全无副作用"""
        scorer.stop_background_scan()
        assert scorer._scan_thread is None

    def test_scan_loop_handles_exception(self):
        """扫描循环内 run_background_scan_once 抛异常应被捕获并继续, stop 后退出"""
        s = HotnessScorer(adapter=FakeAdapter(), scan_interval=0.01)
        s.run_background_scan_once = Mock(side_effect=RuntimeError("boom"))
        s._scan_stop.clear()
        thread = threading.Thread(target=s._scan_loop, daemon=True,
                                  name="hotness-scan-test")
        thread.start()
        time.sleep(0.06)  # 至少循环一轮, 触发 except 与 wait 分支
        s._scan_stop.set()
        thread.join(timeout=3)
        assert not thread.is_alive()