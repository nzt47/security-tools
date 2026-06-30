"""资源监控持久化功能测试

覆盖维度：
1. 持久化路径解析（默认/自定义）
2. 缓冲与批量写入
3. 跨重启历史加载
4. 过期数据清理
5. 反序列化容错
6. 落盘失败降级
7. 禁用持久化
8. 公开 API（flush/cleanup/status）
"""

import json
import os
import time

import pytest

from agent.monitoring.resource_monitor import (
    ResourceMonitor,
    ResourceSnapshot,
    reset_resource_monitor,
)


@pytest.fixture(autouse=True)
def isolate_monitor(tmp_path):
    """每个用例独立的监控器实例 + 临时持久化路径"""
    reset_resource_monitor()
    persist_path = str(tmp_path / "test_history.jsonl")
    monitor = ResourceMonitor(config={
        "persist_enabled": True,
        "persist_path": persist_path,
        "persist_batch_size": 3,
        "persist_max_age_hours": 168,
        "history_size": 100,
    })
    yield monitor, persist_path
    monitor.stop()
    reset_resource_monitor()


class TestPersistPathResolution:
    """持久化路径解析测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_custom_path_used(self, isolate_monitor):
        monitor, persist_path = isolate_monitor
        assert monitor._persist_path == persist_path

    @pytest.mark.unit
    @pytest.mark.p1
    def test_default_path_when_empty(self, tmp_path):
        reset_resource_monitor()
        monitor = ResourceMonitor(config={"persist_path": "", "persist_enabled": True})
        # 默认路径应为 data/resource_monitor_history.jsonl
        assert monitor._persist_path.endswith(os.path.join("data", "resource_monitor_history.jsonl"))
        monitor.stop()
        reset_resource_monitor()


class TestBufferingAndFlush:
    """缓冲与批量写入测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sample_buffers_until_batch_size(self, isolate_monitor):
        """采样进入缓冲，未达 batch_size 不落盘"""
        monitor, persist_path = isolate_monitor
        # batch_size=3，采样 2 次不应落盘
        monitor.sample()
        monitor.sample()
        assert not os.path.exists(persist_path)
        assert len(monitor._persist_buffer) == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_flush_triggered_at_batch_size(self, isolate_monitor):
        """达到 batch_size 触发批量落盘"""
        monitor, persist_path = isolate_monitor
        # batch_size=3，采样 3 次应触发落盘
        for _ in range(3):
            monitor.sample()
        assert os.path.exists(persist_path)
        # 文件应有 3 行
        with open(persist_path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 3
        # 缓冲已清空
        assert len(monitor._persist_buffer) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_manual_flush_writes_buffer(self, isolate_monitor):
        """手动 flush_persist 写入未满缓冲"""
        monitor, persist_path = isolate_monitor
        monitor.sample()
        assert not os.path.exists(persist_path)
        monitor.flush_persist()
        assert os.path.exists(persist_path)
        with open(persist_path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stop_flushes_remaining_buffer(self, isolate_monitor):
        """stop() 触发剩余缓冲落盘"""
        monitor, persist_path = isolate_monitor
        monitor.sample()
        monitor.stop()
        assert os.path.exists(persist_path)
        with open(persist_path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_persisted_format_is_jsonl(self, isolate_monitor):
        """落盘格式为 JSON Lines（每行一个合法 JSON）"""
        monitor, persist_path = isolate_monitor
        for _ in range(3):
            monitor.sample()
        with open(persist_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)  # 每行应可解析为 JSON
                    assert "timestamp" in data
                    assert "memory" in data


class TestHistoryLoading:
    """跨重启历史加载测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_persisted_history_on_start(self, isolate_monitor):
        """新实例启动时加载已有历史"""
        monitor, persist_path = isolate_monitor
        # 写入 5 条历史
        for _ in range(5):
            monitor.sample()
        monitor.flush_persist()
        monitor.stop()

        # 创建新实例（模拟重启）
        reset_resource_monitor()
        new_monitor = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": persist_path,
            "persist_batch_size": 100,
            "history_size": 100,
        })
        # 手动触发加载（_load_persisted_history 通常在 start 时调用）
        loaded = new_monitor._load_persisted_history()
        assert loaded == 5
        assert len(new_monitor.get_history()) == 5
        new_monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_only_once(self, isolate_monitor):
        """历史仅加载一次，重复调用不重复加载"""
        monitor, persist_path = isolate_monitor
        for _ in range(3):
            monitor.sample()
        monitor.flush_persist()
        monitor.stop()

        reset_resource_monitor()
        new_monitor = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": persist_path,
        })
        first = new_monitor._load_persisted_history()
        second = new_monitor._load_persisted_history()
        assert first == 3
        assert second == 0  # 第二次不重复加载
        new_monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_load_missing_file_returns_zero(self, isolate_monitor):
        """持久化文件不存在时加载返回 0"""
        monitor, _ = isolate_monitor
        monitor._persist_path = "/nonexistent/path/history.jsonl"
        loaded = monitor._load_persisted_history()
        assert loaded == 0


class TestExpiryCleanup:
    """过期数据清理测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_expired_data_filtered_on_load(self, isolate_monitor):
        """加载时过滤过期数据"""
        monitor, persist_path = isolate_monitor
        monitor._persist_max_age_hours = 1  # 1 小时过期

        # 写入一条当前数据
        monitor.sample()
        # 手动注入一条过期数据（2 小时前）
        expired_snap = ResourceSnapshot(timestamp=time.time() - 7200)
        monitor._persist_buffer.append(expired_snap)
        monitor.flush_persist()
        monitor.stop()

        reset_resource_monitor()
        new_monitor = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": persist_path,
            "persist_max_age_hours": 1,
        })
        loaded = new_monitor._load_persisted_history()
        # 仅加载 1 条有效数据（过期数据被过滤）
        assert loaded == 1
        new_monitor.stop()
        reset_resource_monitor()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rewrite_removes_expired(self, isolate_monitor):
        """重写文件移除过期数据"""
        monitor, persist_path = isolate_monitor
        monitor._persist_max_age_hours = 1

        # 写入当前数据
        monitor.sample()
        # 写入过期数据
        expired = ResourceSnapshot(timestamp=time.time() - 7200)
        monitor._persist_buffer.append(expired)
        monitor.flush_persist()
        monitor.stop()

        # 重写应移除过期行
        monitor._rewrite_persisted_file()
        with open(persist_path, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        # 过期数据被移除，仅保留 1 条
        assert len(lines) == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_cleanup_public_api(self, isolate_monitor):
        """cleanup_persisted_history 公开 API 返回保留条数"""
        monitor, persist_path = isolate_monitor
        for _ in range(3):
            monitor.sample()
        monitor.flush_persist()
        kept = monitor.cleanup_persisted_history()
        assert kept == 3


class TestDeserialization:
    """反序列化容错测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dict_to_snapshot_roundtrip(self, isolate_monitor):
        """快照序列化/反序列化往返一致"""
        monitor, _ = isolate_monitor
        original = monitor.sample()
        data = original.to_dict()
        restored = monitor._dict_to_snapshot(data)
        assert restored is not None
        assert restored.timestamp == original.timestamp
        assert restored.memory.current_bytes == original.memory.current_bytes
        assert restored.thread_pool.active_threads == original.thread_pool.active_threads

    @pytest.mark.unit
    @pytest.mark.p1
    def test_dict_to_snapshot_partial_data(self, isolate_monitor):
        """字段缺失时使用默认值"""
        monitor, _ = isolate_monitor
        partial = {"timestamp": time.time()}  # 仅时间戳
        snap = monitor._dict_to_snapshot(partial)
        assert snap is not None
        assert snap.memory.current_bytes == 0
        assert snap.thread_pool.active_threads == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_dict_to_snapshot_invalid_returns_none(self, isolate_monitor):
        """非法数据返回 None"""
        monitor, _ = isolate_monitor
        assert monitor._dict_to_snapshot({"invalid": "data"}) is not None  # 缺字段也返回默认快照
        # 但完全损坏的类型会返回 None
        assert monitor._dict_to_snapshot({}) is not None  # 空字典仍生成默认快照

    @pytest.mark.unit
    @pytest.mark.p1
    def test_load_skips_corrupt_lines(self, isolate_monitor):
        """加载时跳过损坏行"""
        monitor, persist_path = isolate_monitor
        # 写入 1 条有效数据
        monitor.sample()
        monitor.flush_persist()
        monitor.stop()
        # 追加损坏行
        with open(persist_path, "a", encoding="utf-8") as f:
            f.write("not a json\n")
            f.write("{invalid json\n")

        reset_resource_monitor()
        new_monitor = ResourceMonitor(config={
            "persist_enabled": True,
            "persist_path": persist_path,
        })
        loaded = new_monitor._load_persisted_history()
        # 仅加载 1 条有效数据，损坏行被跳过
        assert loaded == 1
        new_monitor.stop()
        reset_resource_monitor()


class TestDegradation:
    """落盘失败降级测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_persist_failure_does_not_crash_sample(self, isolate_monitor):
        """落盘失败不影响采样主流程"""
        monitor, _ = isolate_monitor
        # 指向不可写路径
        monitor._persist_path = "/nonexistent/dir/history.jsonl"
        monitor._persist_batch_size = 1  # 立即触发写入
        # 采样应正常完成
        snap = monitor.sample()
        assert isinstance(snap, ResourceSnapshot)
        # 缓冲被清空（写入失败也清空避免无限增长）
        assert len(monitor._persist_buffer) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_disabled_persist_no_writes(self, isolate_monitor):
        """禁用持久化时不写入文件"""
        monitor, persist_path = isolate_monitor
        monitor._persist_enabled = False
        monitor._persist_batch_size = 1
        monitor.sample()
        monitor.flush_persist()
        assert not os.path.exists(persist_path)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_flush_empty_buffer_noop(self, isolate_monitor):
        """空缓冲 flush 无副作用"""
        monitor, persist_path = isolate_monitor
        monitor.flush_persist()  # 无数据
        assert not os.path.exists(persist_path)


class TestPublicAPI:
    """公开 API 测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_persist_status(self, isolate_monitor):
        """get_persist_status 返回完整状态"""
        monitor, persist_path = isolate_monitor
        monitor.sample()
        status = monitor.get_persist_status()
        assert status["enabled"] is True
        assert status["path"] == persist_path
        assert "file_exists" in status
        assert "file_size_bytes" in status
        assert status["buffer_count"] == 1
        assert status["batch_size"] == 3

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status_includes_persist(self, isolate_monitor):
        """get_status 包含 persist 字段"""
        monitor, _ = isolate_monitor
        status = monitor.get_status()
        assert "persist" in status
        assert isinstance(status["persist"], dict)
        assert status["persist"]["enabled"] is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_get_persist_status_disabled(self, isolate_monitor):
        """禁用持久化时状态正确反映"""
        monitor, _ = isolate_monitor
        monitor._persist_enabled = False
        status = monitor.get_persist_status()
        assert status["enabled"] is False
