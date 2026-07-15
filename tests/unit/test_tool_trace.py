"""ToolTraceRecorder 和 ToolTraceRecord 单元测试

覆盖范围:
- ToolTraceRecord 数据类: to_dict 字段完整性、默认值
- hash_content: 16位 hex、脱敏、稳定性、降级
- _is_dangerous: 危险命令检测、安全命令不误报
- 采样策略: 高频 10%、低频全量、危险强制、force 跳过
- 异步写入与查询: record + flush + get_recent/failed/p99
- 降级模式: ring buffer 读写、SQLite 失败触发降级
- trace 生命周期: start/finish、success 推断、permission_decision 传递与清理
- 集成测试: ToolCallingService._execute_safe、tool_router.get_tools_for_input
- 性能: record < 0.5ms、start_trace < 1ms
- 工具方法: clear、flush 空队列、record_tool_selection 日志、set_permission_decision
- 单例: instance 同一对象、reset 清理、reset 停止 writer 线程
"""

import os
import time
import json
import logging
import sqlite3
from unittest.mock import patch, MagicMock
import pytest
from agent.observability.tool_trace import (
    ToolTraceRecord,
    ToolTraceRecorder,
    HIGH_FREQ_TOOLS,
)


# ════════════════════════════════════════════════════════════
#  公共 fixture
# ════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个测试前后重置单例(避免数据库文件残留)"""
    ToolTraceRecorder.reset()
    yield
    ToolTraceRecorder.reset()


@pytest.fixture
def recorder(tmp_path):
    """创建独立 recorder 实例(不污染单例)"""
    r = ToolTraceRecorder(db_path=str(tmp_path / "test_tool_trace.db"))
    yield r
    r._stopped = True


def _make_record(**kwargs):
    """构造 ToolTraceRecord 测试辅助(减少重复)"""
    defaults = dict(
        trace_id="abcd1234abcd1234",
        tool_name="test_tool",
        input_hash="1111222233334444",
        output_hash="5555666677778888",
        latency_ms=10.0,
        success=True,
    )
    defaults.update(kwargs)
    return ToolTraceRecord(**defaults)


# ════════════════════════════════════════════════════════════
#  TestToolTraceRecord
# ════════════════════════════════════════════════════════════

class TestToolTraceRecord:
    """ToolTraceRecord 数据类"""

    def test_to_dict_contains_all_fields(self):
        # 验证 to_dict 包含所有 11 个字段
        record = _make_record()
        d = record.to_dict()
        expected_keys = {
            "trace_id", "tool_name", "input_hash", "output_hash",
            "latency_ms", "success", "error_type", "session_id",
            "user_role", "timestamp", "permission_decision",
        }
        assert set(d.keys()) == expected_keys

    def test_default_values(self):
        # 验证默认值
        record = _make_record()
        assert record.error_type == ""
        assert record.session_id == ""
        assert record.user_role == "guest"
        assert record.permission_decision == ""
        assert record.timestamp > 0


# ════════════════════════════════════════════════════════════
#  TestHashContent
# ════════════════════════════════════════════════════════════

class TestHashContent:
    """hash_content 脱敏哈希"""

    def test_returns_16_hex_chars(self, recorder):
        # 返回 16 位 hex
        h = recorder.hash_content({"key": "value"})
        assert len(h) == 16
        int(h, 16)  # 验证是合法的 hex

    def test_not_store_original(self, recorder):
        # 原文不出现在 hash 中
        original = "super_secret_password_12345"
        h = recorder.hash_content(original)
        assert original not in h
        assert "super_secret" not in h

    def test_same_input_same_hash(self, recorder):
        # 相同内容(顺序不同)相同 hash(sort_keys=True)
        h1 = recorder.hash_content({"a": 1, "b": 2})
        h2 = recorder.hash_content({"b": 2, "a": 1})
        assert h1 == h2

    def test_different_input_different_hash(self, recorder):
        # 不同内容不同 hash
        h1 = recorder.hash_content({"a": 1})
        h2 = recorder.hash_content({"a": 2})
        assert h1 != h2

    def test_string_input(self, recorder):
        # 字符串输入
        h = recorder.hash_content("hello world")
        assert len(h) == 16
        int(h, 16)

    def test_non_serializable_input(self, recorder):
        # 不可序列化对象降级到 str()
        class Foo:
            def __str__(self):
                return "foo_object"
        h = recorder.hash_content(Foo())
        assert len(h) == 16
        int(h, 16)


# ════════════════════════════════════════════════════════════
#  TestIsDangerous
# ════════════════════════════════════════════════════════════

class TestIsDangerous:
    """_is_dangerous 危险命令检测"""

    def test_dangerous_rm_rf_detected(self, recorder):
        # rm -rf / 检测
        assert recorder._is_dangerous({"command": "rm -rf /"}) is True

    def test_dangerous_format_detected(self, recorder):
        # format c: 检测
        assert recorder._is_dangerous({"cmd": "format c:"}) is True

    def test_dangerous_fork_bomb_detected(self, recorder):
        # fork 炸弹检测(JSON 模式不含 },匹配 ":(){:|:&;" 部分)
        assert recorder._is_dangerous(":(){:|:&;}") is True

    def test_dangerous_drop_table_detected(self, recorder):
        # DROP TABLE 检测
        assert recorder._is_dangerous("DROP TABLE users") is True

    def test_safe_command_not_dangerous(self, recorder):
        # 安全命令不误报
        assert recorder._is_dangerous({"command": "ls -la"}) is False

    def test_safe_query_not_dangerous(self, recorder):
        # 安全查询不误报
        assert recorder._is_dangerous({"query": "hello world"}) is False

    def test_empty_input_not_dangerous(self, recorder):
        # 空输入不误报
        assert recorder._is_dangerous({}) is False
        assert recorder._is_dangerous("") is False


# ════════════════════════════════════════════════════════════
#  TestSamplingStrategy
# ════════════════════════════════════════════════════════════

class TestSamplingStrategy:
    """采样策略: 高频 10%、低频全量、危险强制、force 跳过"""

    def test_high_freq_tool_in_set(self):
        # 高频工具在集合中
        assert "web_search" in HIGH_FREQ_TOOLS
        assert "read_file" in HIGH_FREQ_TOOLS
        assert "write_file" in HIGH_FREQ_TOOLS

    def test_low_freq_tool_not_in_set(self):
        # 低频工具不在集合中
        assert "shell_execute" not in HIGH_FREQ_TOOLS
        assert "code_review" not in HIGH_FREQ_TOOLS

    def test_high_freq_sampled_when_random_below_threshold(self, recorder):
        # random < 0.1 → 采样
        with patch("agent.observability.tool_trace.random.random", return_value=0.05):
            ctx = recorder.start_trace("web_search", {"query": "test"})
            recorder.finish_trace(ctx, {"ok": True}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 1

    def test_high_freq_dropped_when_random_above_threshold(self, recorder):
        # random >= 0.1 → 丢弃
        with patch("agent.observability.tool_trace.random.random", return_value=0.15):
            ctx = recorder.start_trace("web_search", {"query": "test"})
            recorder.finish_trace(ctx, {"ok": True}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 0

    def test_low_freq_always_sampled_regardless_of_random(self, recorder):
        # 低频工具无视 random 全量采样
        with patch("agent.observability.tool_trace.random.random", return_value=0.99):
            ctx = recorder.start_trace("shell_execute", {"command": "ls"})
            recorder.finish_trace(ctx, {"ok": True}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("shell_execute")
        assert len(rows) == 1

    def test_dangerous_input_forces_sampling_for_high_freq(self, recorder):
        # 危险输入强制采样(即使高频且 random 高)
        with patch("agent.observability.tool_trace.random.random", return_value=0.99):
            ctx = recorder.start_trace("web_search", {"query": "rm -rf /"})
            recorder.finish_trace(ctx, {"ok": True}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 1

    def test_force_param_skips_sampling(self, recorder):
        # force=True 跳过采样
        with patch("agent.observability.tool_trace.random.random", return_value=0.99):
            record = _make_record(tool_name="web_search")
            recorder.record(record, force=True)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 1


# ════════════════════════════════════════════════════════════
#  TestAsyncWriteAndQuery
# ════════════════════════════════════════════════════════════

class TestAsyncWriteAndQuery:
    """异步写入与查询"""

    def test_record_and_query_recent(self, recorder):
        # record + flush + 查询验证字段
        record = _make_record(tool_name="my_tool", session_id="sess1")
        recorder.record(record, force=True)
        assert recorder.flush()
        rows = recorder.get_recent_traces("my_tool")
        assert len(rows) == 1
        r = rows[0]
        assert r.trace_id == record.trace_id
        assert r.tool_name == "my_tool"
        assert r.input_hash == record.input_hash
        assert r.output_hash == record.output_hash
        assert r.success is True
        assert r.session_id == "sess1"

    def test_query_recent_respects_limit(self, recorder):
        # limit 参数生效
        for i in range(5):
            recorder.record(_make_record(trace_id=f"id{i:016d}"), force=True)
        assert recorder.flush()
        rows = recorder.get_recent_traces("test_tool", limit=3)
        assert len(rows) == 3

    def test_query_recent_filters_by_tool_name(self, recorder):
        # 按 tool_name 过滤
        recorder.record(_make_record(tool_name="tool_a"), force=True)
        recorder.record(_make_record(tool_name="tool_b"), force=True)
        assert recorder.flush()
        assert len(recorder.get_recent_traces("tool_a")) == 1
        assert len(recorder.get_recent_traces("tool_b")) == 1
        assert len(recorder.get_recent_traces("tool_c")) == 0

    def test_query_failed_traces(self, recorder):
        # 查询失败 trace
        recorder.record(_make_record(success=True), force=True)
        recorder.record(_make_record(success=False, error_type="ValueError"), force=True)
        assert recorder.flush()
        failed = recorder.get_failed_traces(since=time.time() - 60)
        assert len(failed) == 1
        assert failed[0].success is False
        assert failed[0].error_type == "ValueError"

    def test_query_failed_traces_filters_by_time(self, recorder):
        # 按时间过滤
        old_ts = time.time() - 2 * 3600  # 2 小时前
        new_ts = time.time() - 60        # 1 分钟前
        recorder.record(_make_record(success=False, timestamp=old_ts), force=True)
        recorder.record(_make_record(success=False, timestamp=new_ts), force=True)
        assert recorder.flush()
        since = time.time() - 3600  # 1 小时前
        failed = recorder.get_failed_traces(since=since)
        assert len(failed) == 1
        assert failed[0].timestamp >= since

    def test_get_latency_p99(self, recorder):
        # p99 计算: 100 条记录延迟 1-100ms
        for i in range(1, 101):
            recorder.record(_make_record(latency_ms=float(i)), force=True)
        assert recorder.flush()
        p99 = recorder.get_latency_p99("test_tool", window=3600)
        # p99 应在 95-100 之间
        assert 95 <= p99 <= 100

    def test_get_latency_p99_no_data(self, recorder):
        # 无数据返回 0.0
        assert recorder.get_latency_p99("nonexistent_tool") == 0.0

    def test_query_returns_empty_for_nonexistent_tool(self, recorder):
        # 不存在的工具返回空列表
        assert recorder.get_recent_traces("nonexistent_tool") == []


# ════════════════════════════════════════════════════════════
#  TestDegradation
# ════════════════════════════════════════════════════════════

class TestDegradation:
    """降级模式: SQLite 失败 → ring buffer"""

    def test_degraded_mode_writes_to_ring_buffer(self, recorder):
        # 降级模式写入 ring buffer
        recorder._degraded = True
        record = _make_record()
        recorder._write_to_db([record])
        assert len(recorder._fallback_ring_buffer) == 1

    def test_degraded_query_reads_from_ring_buffer(self, recorder):
        # 降级模式查询从 ring buffer 读
        recorder._degraded = True
        record = _make_record(tool_name="degraded_tool")
        recorder._write_to_db([record])
        rows = recorder.get_recent_traces("degraded_tool")
        assert len(rows) == 1
        assert rows[0].tool_name == "degraded_tool"

    def test_degraded_failed_query_reads_from_ring_buffer(self, recorder):
        # 降级模式失败查询从 ring buffer 读
        recorder._degraded = True
        record = _make_record(success=False, error_type="ValueError")
        recorder._write_to_db([record])
        failed = recorder.get_failed_traces(since=time.time() - 60)
        assert len(failed) == 1
        assert failed[0].success is False

    def test_sqlite_write_failure_triggers_degradation(self, recorder):
        # SQLite 写入失败触发降级
        record = _make_record()
        with patch.object(recorder, "_get_conn", side_effect=sqlite3.OperationalError("disk full")):
            recorder._write_to_db([record])
        assert recorder._degraded is True
        assert len(recorder._fallback_ring_buffer) == 1


# ════════════════════════════════════════════════════════════
#  TestTraceLifecycle
# ════════════════════════════════════════════════════════════

class TestTraceLifecycle:
    """trace 生命周期: start/finish、success 推断、permission_decision"""

    @pytest.fixture(autouse=True)
    def _mock_random_sample(self):
        # mock random.random=0.0 避免高频采样 flaky
        with patch("agent.observability.tool_trace.random.random", return_value=0.0):
            yield

    def test_start_trace_returns_context(self, recorder):
        # start_trace 返回上下文
        ctx = recorder.start_trace("web_search", {"query": "test"})
        assert len(ctx.trace_id) == 16
        assert ctx.tool_name == "web_search"
        assert len(ctx.input_hash) == 16
        assert ctx.is_dangerous is False
        assert ctx.start_time > 0

    def test_finish_trace_success(self, recorder):
        # finish_trace 成功路径
        ctx = recorder.start_trace("web_search", {"query": "test"})
        recorder.finish_trace(ctx, {"ok": True, "results": []}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 1
        r = rows[0]
        assert r.success is True
        assert r.error_type == ""
        assert len(r.output_hash) == 16
        assert r.latency_ms >= 0

    def test_finish_trace_with_exception(self, recorder):
        # finish_trace 异常路径
        ctx = recorder.start_trace("web_search", {"query": "test"})
        recorder.finish_trace(ctx, None, ValueError("test error"))
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 1
        r = rows[0]
        assert r.success is False
        assert r.error_type == "ValueError"
        assert r.output_hash == ""

    def test_finish_trace_infers_success_from_result_ok(self, recorder):
        # 无异常但 ok=False → success=False, error_type="ToolError"
        ctx = recorder.start_trace("web_search", {"query": "test"})
        recorder.finish_trace(ctx, {"ok": False, "error": "not found"}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        r = rows[0]
        assert r.success is False
        assert r.error_type == "ToolError"

    def test_finish_trace_infers_success_from_result_ok_true(self, recorder):
        # 无异常且 ok=True → success=True
        ctx = recorder.start_trace("web_search", {"query": "test"})
        recorder.finish_trace(ctx, {"ok": True, "results": []}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        r = rows[0]
        assert r.success is True
        assert r.error_type == ""

    def test_permission_decision_flows_to_trace(self, recorder):
        # permission_decision 流入 trace
        recorder.set_permission_decision(False, "denied by RBAC")
        ctx = recorder.start_trace("web_search", {"query": "test"})
        recorder.finish_trace(ctx, {"ok": True}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        r = rows[0]
        assert "denied" in r.permission_decision
        assert "RBAC" in r.permission_decision

    def test_permission_decision_cleared_after_finish(self, recorder):
        # finish_trace 后清理 permission_decision ContextVar
        from agent.observability.tool_trace import _permission_decision_var
        recorder.set_permission_decision(True, "allowed")
        ctx = recorder.start_trace("web_search", {"query": "test"})
        recorder.finish_trace(ctx, {"ok": True}, None)
        assert _permission_decision_var.get() == ""


# ════════════════════════════════════════════════════════════
#  TestIntegration
# ════════════════════════════════════════════════════════════

class TestIntegration:
    """集成测试: ToolCallingService._execute_safe、tool_router.get_tools_for_input"""

    @pytest.fixture(autouse=True)
    def _mock_random_sample(self):
        # mock random.random=0.0 避免高频采样 flaky
        with patch("agent.observability.tool_trace.random.random", return_value=0.0):
            yield

    @pytest.fixture(autouse=True)
    def _register_test_tool(self):
        # 注册 test_tool 到工具表，避免未 mock 场景下 tools.call 报错
        # Why: 用户明确要求把 test_tool 注册到工具表，作为防御性后备
        from agent import tools
        def _test_tool_handler(**kwargs):
            return {"ok": True, "result": "test_tool default"}
        tools.register("test_tool", "测试工具", handler=_test_tool_handler)
        yield
        try:
            tools.unregister("test_tool")
        except Exception:
            pass

    def test_full_tool_call_produces_one_trace(self, recorder):
        # 完整工具调用产生一条 trace,11 字段完整
        ToolTraceRecorder._instance = recorder
        recorder.set_permission_decision(True, "allowed by RBAC")
        ctx = recorder.start_trace(
            "web_search", {"query": "test query"},
            session_id="session_123", user_role="admin",
        )
        recorder.finish_trace(ctx, {"ok": True, "results": [{"title": "test"}]}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 1
        r = rows[0]
        # 11 字段完整
        assert r.trace_id and len(r.trace_id) == 16
        assert r.tool_name == "web_search"
        assert r.input_hash and len(r.input_hash) == 16
        assert r.output_hash and len(r.output_hash) == 16
        assert r.latency_ms >= 0
        assert r.success is True
        assert r.error_type == ""
        assert r.session_id == "session_123"
        assert r.user_role == "admin"
        assert r.timestamp > 0
        assert "allowed" in r.permission_decision
        assert "RBAC" in r.permission_decision

    def test_failed_tool_call_produces_trace(self, recorder):
        # 失败工具调用产生 trace
        ToolTraceRecorder._instance = recorder
        ctx = recorder.start_trace("web_search", {"query": "test"})
        try:
            raise RuntimeError("network timeout")
        except RuntimeError as e:
            recorder.finish_trace(ctx, None, e)
        assert recorder.flush()
        rows = recorder.get_recent_traces("web_search")
        assert len(rows) == 1
        r = rows[0]
        assert r.success is False
        assert r.error_type == "RuntimeError"
        failed = recorder.get_failed_traces(since=time.time() - 60)
        assert len(failed) == 1

    def test_dangerous_tool_call_recorded_with_force(self, recorder):
        # 危险工具调用强制记录
        ToolTraceRecorder._instance = recorder
        ctx = recorder.start_trace("shell_execute", {"command": "rm -rf /"})
        assert ctx.is_dangerous is True
        recorder.finish_trace(ctx, {"ok": True}, None)
        assert recorder.flush()
        rows = recorder.get_recent_traces("shell_execute")
        assert len(rows) == 1

    def test_tool_calling_execute_safe_wraps_trace(self, recorder):
        # _execute_safe 包裹 trace
        from agent.tool_calling import ToolCallingService
        ToolTraceRecorder._instance = recorder
        service = ToolCallingService.__new__(ToolCallingService)
        # mock _execute_safe_core 返回成功
        service._execute_safe_core = MagicMock(
            return_value={"ok": True, "result": "success"}
        )
        result = service._execute_safe("test_tool", {"param": "value"})
        assert recorder.flush()
        assert result == {"ok": True, "result": "success"}
        rows = recorder.get_recent_traces("test_tool")
        assert len(rows) == 1
        assert rows[0].success is True

    def test_tool_calling_execute_safe_records_failure(self, recorder):
        # _execute_safe 记录失败(ok=False → ToolError)
        from agent.tool_calling import ToolCallingService
        ToolTraceRecorder._instance = recorder
        service = ToolCallingService.__new__(ToolCallingService)
        service._execute_safe_core = MagicMock(
            return_value={"ok": False, "error": "tool failed"}
        )
        result = service._execute_safe("test_tool", {"param": "value"})
        assert recorder.flush()
        assert result == {"ok": False, "error": "tool failed"}
        rows = recorder.get_recent_traces("test_tool")
        assert len(rows) == 1
        assert rows[0].success is False
        assert rows[0].error_type == "ToolError"

    def test_tool_router_records_selection(self, recorder, caplog):
        # tool_router 记录工具选择
        from agent import tool_router
        ToolTraceRecorder._instance = recorder
        with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
            tool_router.get_tools_for_input("搜索文件", None)
        # 解析日志 JSON,查找 tool_selection
        log_data = None
        for r in caplog.records:
            try:
                data = json.loads(r.message)
                if data.get("action") == "tool_selection":
                    log_data = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert log_data is not None, "未找到 tool_selection 日志"
        assert log_data["action"] == "tool_selection"
        assert log_data["tools_count"] > 0
        assert "file" in log_data["categories"]


# ════════════════════════════════════════════════════════════
#  TestPerformance
# ════════════════════════════════════════════════════════════

class TestPerformance:
    """性能测试"""

    def test_record_under_0_5ms(self, recorder):
        # record 单次中位数 < 0.5ms(低频工具)
        # 预热
        for _ in range(10):
            recorder.record(_make_record(tool_name="low_freq_tool"), force=True)
        recorder.flush()
        # 测量
        durations = []
        for _ in range(100):
            r = _make_record(tool_name="low_freq_tool")
            t0 = time.perf_counter()
            recorder.record(r, force=True)
            durations.append((time.perf_counter() - t0) * 1000)
        durations.sort()
        median = durations[len(durations) // 2]
        assert median < 0.5, f"record 中位数 {median:.3f}ms 超过 0.5ms"

    def test_start_trace_under_1ms(self, recorder):
        # start_trace 单次中位数 < 1.0ms
        # 预热
        for _ in range(10):
            ctx = recorder.start_trace("web_search", {"query": "test"})
            recorder.finish_trace(ctx, {"ok": True}, None)
        recorder.flush()
        # 测量
        durations = []
        for _ in range(50):
            t0 = time.perf_counter()
            ctx = recorder.start_trace("web_search", {"query": "test"})
            durations.append((time.perf_counter() - t0) * 1000)
            recorder.finish_trace(ctx, {"ok": True}, None)
        durations.sort()
        median = durations[len(durations) // 2]
        assert median < 1.0, f"start_trace 中位数 {median:.3f}ms 超过 1.0ms"


# ════════════════════════════════════════════════════════════
#  TestUtility
# ════════════════════════════════════════════════════════════

class TestUtility:
    """工具方法: clear、flush、record_tool_selection、set_permission_decision"""

    def test_clear(self, recorder):
        # clear 清空数据
        recorder.record(_make_record(), force=True)
        assert recorder.flush()
        assert len(recorder.get_recent_traces("test_tool")) == 1
        recorder.clear()
        assert len(recorder.get_recent_traces("test_tool")) == 0

    def test_flush_returns_true_when_empty(self, recorder):
        # 空队列 flush 返回 True
        assert recorder.flush(timeout=0.5) is True

    def test_record_tool_selection_logs(self, recorder, caplog):
        # record_tool_selection 输出结构化日志
        with caplog.at_level(logging.INFO, logger="agent.observability.tool_trace"):
            recorder.record_tool_selection(
                "搜索文件", {"web", "file"}, ["web_search", "read_file"],
            )
        log_data = None
        for r in caplog.records:
            try:
                data = json.loads(r.message)
                if data.get("action") == "tool_selection":
                    log_data = data
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        assert log_data is not None, "未找到 tool_selection 日志"
        assert log_data["tools_count"] == 2
        assert "file" in log_data["categories"]

    def test_set_permission_decision_allowed(self, recorder):
        # allowed 决策
        from agent.observability.tool_trace import _permission_decision_var
        recorder.set_permission_decision(True, "allowed by RBAC")
        decision = _permission_decision_var.get()
        assert "allowed" in decision
        assert "RBAC" in decision

    def test_set_permission_decision_denied(self, recorder):
        # denied 决策
        from agent.observability.tool_trace import _permission_decision_var
        recorder.set_permission_decision(False, "no permission")
        decision = _permission_decision_var.get()
        assert "denied" in decision

    def test_set_permission_decision_truncates_reason(self, recorder):
        # 长 reason 截断(reason[:50] + "allowed:" 前缀,总长 <= 60)
        from agent.observability.tool_trace import _permission_decision_var
        long_reason = "x" * 200
        recorder.set_permission_decision(True, long_reason)
        decision = _permission_decision_var.get()
        assert len(decision) <= 60


# ════════════════════════════════════════════════════════════
#  TestSingleton
# ════════════════════════════════════════════════════════════

class TestSingleton:
    """单例: instance、reset"""

    def test_instance_returns_same_singleton(self, tmp_path, monkeypatch):
        # 两次 instance() 返回同一对象(隔离 db_path 避免污染)
        monkeypatch.setattr(
            "agent.observability.tool_trace._DEFAULT_DB_PATH",
            str(tmp_path / "test_singleton.db"),
        )
        r1 = ToolTraceRecorder.instance()
        r2 = ToolTraceRecorder.instance()
        assert r1 is r2

    def test_reset_clears_singleton(self, tmp_path, monkeypatch):
        # reset 后 instance() 返回不同对象
        monkeypatch.setattr(
            "agent.observability.tool_trace._DEFAULT_DB_PATH",
            str(tmp_path / "test_singleton.db"),
        )
        r1 = ToolTraceRecorder.instance()
        ToolTraceRecorder.reset()
        r2 = ToolTraceRecorder.instance()
        assert r1 is not r2

    def test_reset_stops_writer_thread(self, tmp_path, monkeypatch):
        # reset 后旧实例 _stopped is True
        monkeypatch.setattr(
            "agent.observability.tool_trace._DEFAULT_DB_PATH",
            str(tmp_path / "test_singleton.db"),
        )
        r1 = ToolTraceRecorder.instance()
        ToolTraceRecorder.reset()
        assert r1._stopped is True
