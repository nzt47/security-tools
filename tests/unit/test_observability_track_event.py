"""observability.py 模块单元测试 — 覆盖 trackEvent 全调用场景

【生成日志摘要】
- 生成时间: 2026-06-30
- 内容: 覆盖两种 observability.py 变体（模板生成 + 手写 skills_mgmt）
- 版本: v1.0.0
- 关键状态: 26 个 observability.py 文件，2 种模式

测试覆盖维度:
    1. trackEvent 基础调用 — 验证结构化日志输出
    2. trackEvent payload 处理 — None/空/简单/复杂嵌套
    3. trackEvent 指标集成 — BusinessMetricsCollector 可用/不可用
    4. trackEvent 错误隔离 — 埋点失败不传播异常（硬约束）
    5. _emit_structured_log 格式 — trace_id/module_name/action/duration_ms 必填
    6. skills_mgmt 变体 — track_event(snake_case) + emit_metric + traced_action
    7. emit_metric 指标类型 — counter/histogram/gauge + 降级
    8. traced_action 上下文管理器 — 正常/异常/耗时

状态同步机制:
    - 每个测试用例独立 mock，避免模块级单例污染
    - 使用 assertLogs 捕获日志，解析 JSON 验证结构化字段
    - 使用 mock.patch 替换 _metrics 和 _METRICS_AVAILABLE
"""

import json
import logging
import time
import uuid
from unittest.mock import MagicMock, patch, PropertyMock
from contextlib import contextmanager

import pytest


# ============================================================================
# 测试目标：模板生成的 observability.py（以 orchestrator 为代表）
# ============================================================================

from agent.orchestrator import observability as obs_orch
from agent.skills_mgmt import observability as obs_skills


# ============================================================================
# 1. trackEvent 基础调用
# ============================================================================

class TestTrackEventBasic:
    """trackEvent 基础调用场景"""

    @pytest.mark.unit
    def test_normal_call_emits_structured_log(self, caplog):
        """正常调用 trackEvent，应输出包含必填字段的结构化 JSON 日志"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("task_dispatch", {"task_type": "planning"})

        # 查找 track 日志记录
        track_logs = [r for r in caplog.records
                      if "track." in r.getMessage()]
        assert len(track_logs) >= 1

        log_data = json.loads(track_logs[-1].getMessage())
        # 验证硬约束必填字段
        assert "trace_id" in log_data, "缺少 trace_id 字段"
        assert log_data["module_name"] == "orchestrator"
        assert log_data["action"] == "track.task_dispatch"
        assert "duration_ms" in log_data
        assert log_data["event_name"] == "task_dispatch"

    @pytest.mark.unit
    def test_returns_none(self):
        """trackEvent 应返回 None（埋点不阻塞主流程）"""
        assert obs_orch.trackEvent("test_event") is None

    @pytest.mark.unit
    def test_unique_trace_id_per_call(self, caplog):
        """每次调用应生成不同的 trace_id"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("event_a")
            obs_orch.trackEvent("event_b")

        trace_ids = []
        for r in caplog.records:
            msg = r.getMessage()
            if "track." in msg and "trace_id" in msg:
                try:
                    data = json.loads(msg)
                    if "trace_id" in data:
                        trace_ids.append(data["trace_id"])
                except json.JSONDecodeError:
                    pass

        assert len(set(trace_ids)) >= 2, f"trace_id 应唯一，got {trace_ids}"


# ============================================================================
# 2. trackEvent payload 处理
# ============================================================================

class TestTrackEventPayload:
    """trackEvent 的 payload 参数处理"""

    @pytest.mark.unit
    def test_none_payload(self, caplog):
        """payload=None 时应使用空字典，不抛异常"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("event_none", None)

        track_logs = [r for r in caplog.records if "track.event_none" in r.getMessage()]
        assert len(track_logs) >= 1

    @pytest.mark.unit
    def test_empty_payload(self, caplog):
        """payload={} 时应正常处理"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("event_empty", {})

        track_logs = [r for r in caplog.records if "track.event_empty" in r.getMessage()]
        assert len(track_logs) >= 1

    @pytest.mark.unit
    def test_simple_payload_merged_into_log(self, caplog):
        """简单 payload 的键值应对合并到日志记录中（使用非保留键）"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("event_simple", {"user_id": "u123", "action_type": "click"})

        track_logs = [r for r in caplog.records if "track.event_simple" in r.getMessage()]
        assert len(track_logs) >= 1
        log_data = json.loads(track_logs[-1].getMessage())
        assert log_data["user_id"] == "u123"
        assert log_data["action_type"] == "click"

    @pytest.mark.unit
    def test_nested_payload(self, caplog):
        """复杂嵌套 payload（dict/list 嵌套）应正确序列化"""
        payload = {
            "metadata": {"version": "1.0", "features": ["a", "b"]},
            "metrics": {"latency_ms": 42.5, "count": 3},
            "nested_list": [{"id": 1}, {"id": 2}],
        }
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("event_nested", payload)

        track_logs = [r for r in caplog.records if "track.event_nested" in r.getMessage()]
        assert len(track_logs) >= 1
        log_data = json.loads(track_logs[-1].getMessage())
        assert log_data["metadata"]["version"] == "1.0"
        assert log_data["metrics"]["latency_ms"] == 42.5
        assert log_data["nested_list"][1]["id"] == 2

    @pytest.mark.unit
    def test_payload_with_special_chars(self, caplog):
        """payload 含特殊字符（中文/emoji/引号）应正确序列化"""
        payload = {"chinese": "你好世界", "emoji": "🚀", "quote": 'he said "hi"'}
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("event_special", payload)

        track_logs = [r for r in caplog.records if "track.event_special" in r.getMessage()]
        assert len(track_logs) >= 1
        log_data = json.loads(track_logs[-1].getMessage())
        assert log_data["chinese"] == "你好世界"
        assert log_data["emoji"] == "🚀"

    @pytest.mark.unit
    def test_reserved_keys_filtered_from_payload(self, caplog):
        """payload 中的保留键（trace_id/module_name/action 等）应被过滤，不覆盖必填字段"""
        payload = {"trace_id": "fake_id", "module_name": "fake_module",
                   "action": "fake", "user_data": "kept"}
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch.trackEvent("event_override", payload)

        track_logs = [r for r in caplog.records if "track.event_override" in r.getMessage()]
        assert len(track_logs) >= 1
        log_data = json.loads(track_logs[-1].getMessage())
        # 保留字段不被覆盖
        assert log_data["trace_id"] != "fake_id"
        assert log_data["module_name"] == "orchestrator"
        assert log_data["action"] == "track.event_override"
        # 非保留键保留
        assert log_data["user_data"] == "kept"


# ============================================================================
# 3. trackEvent 指标集成
# ============================================================================

class TestTrackEventMetricsIntegration:
    """trackEvent 与 BusinessMetricsCollector 的集成"""

    @pytest.mark.unit
    def test_metrics_available_calls_record_interaction(self):
        """_METRICS_AVAILABLE=True 时应调用 _metrics.record_interaction"""
        mock_metrics = MagicMock()
        with patch.object(obs_orch, "_metrics", mock_metrics), \
             patch.object(obs_orch, "_METRICS_AVAILABLE", True):
            obs_orch.trackEvent("task_complete", {"task_type": "planning"})

        mock_metrics.record_interaction.assert_called_once()
        call_args = mock_metrics.record_interaction.call_args
        # record_interaction(event_name, module_name, success, duration)
        assert call_args[0][0] == "task_complete"
        assert call_args[0][1] == "orchestrator"
        assert call_args[0][2] is True

    @pytest.mark.unit
    def test_metrics_unavailable_skips_record(self, caplog):
        """_METRICS_AVAILABLE=False 时不应调用 _metrics"""
        with patch.object(obs_orch, "_metrics", None), \
             patch.object(obs_orch, "_METRICS_AVAILABLE", False):
            with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
                obs_orch.trackEvent("event_no_metrics")

        # 仍应输出日志（降级不崩溃）
        track_logs = [r for r in caplog.records if "track.event_no_metrics" in r.getMessage()]
        assert len(track_logs) >= 1

    @pytest.mark.unit
    def test_metrics_records_duration(self):
        """trackEvent 应将执行耗时传给 record_interaction"""
        mock_metrics = MagicMock()
        with patch.object(obs_orch, "_metrics", mock_metrics), \
             patch.object(obs_orch, "_METRICS_AVAILABLE", True):
            obs_orch.trackEvent("timed_event")

        call_args = mock_metrics.record_interaction.call_args
        # 第 4 个参数是 duration（毫秒）
        duration = call_args[0][3]
        assert isinstance(duration, float)
        assert duration >= 0.0
        assert duration < 100.0  # 应该非常快


# ============================================================================
# 4. trackEvent 错误隔离（硬约束：埋点失败不影响主流程）
# ============================================================================

class TestTrackEventErrorIsolation:
    """trackEvent 错误隔离 — 埋点失败不传播异常"""

    @pytest.mark.unit
    def test_emit_structured_log_raises_no_propagation(self, caplog):
        """_emit_structured_log 抛异常时，trackEvent 应捕获且不传播"""
        with patch.object(obs_orch, "_emit_structured_log",
                          side_effect=RuntimeError("log system down")):
            with caplog.at_level(logging.ERROR, logger="agent.orchestrator"):
                # 不应抛异常
                result = obs_orch.trackEvent("event_with_log_error")

        assert result is None
        # 应记录 trackEvent.failed 错误日志
        error_logs = [r for r in caplog.records if "trackEvent.failed" in r.getMessage()]
        assert len(error_logs) >= 1
        error_data = json.loads(error_logs[-1].getMessage())
        assert "RuntimeError" in error_data["error"]
        assert error_data["event_name"] == "event_with_log_error"

    @pytest.mark.unit
    def test_metrics_raises_no_propagation(self, caplog):
        """_metrics.record_interaction 抛异常时，trackEvent 应捕获且不传播"""
        mock_metrics = MagicMock()
        mock_metrics.record_interaction.side_effect = ConnectionError("metrics DB down")
        with patch.object(obs_orch, "_metrics", mock_metrics), \
             patch.object(obs_orch, "_METRICS_AVAILABLE", True):
            with caplog.at_level(logging.ERROR, logger="agent.orchestrator"):
                # 不应抛异常
                result = obs_orch.trackEvent("event_with_metrics_error")

        assert result is None
        # 应记录 trackEvent.failed 错误日志
        error_logs = [r for r in caplog.records if "trackEvent.failed" in r.getMessage()]
        assert len(error_logs) >= 1

    @pytest.mark.unit
    def test_payload_with_non_serializable_value(self, caplog):
        """payload 含不可序列化对象时不应崩溃"""
        # 自定义对象不可 JSON 序列化
        class NonSerializable:
            def __str__(self):
                return "<non-serializable>"

        payload = {"obj": NonSerializable()}
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            # 不应抛异常（default=str 降级处理）
            obs_orch.trackEvent("event_non_serializable", payload)

        # 应至少输出日志（可能通过 default=str 降级）
        track_logs = [r for r in caplog.records
                      if "track.event_non_serializable" in r.getMessage()]
        # default=str 会将不可序列化对象转为字符串
        assert len(track_logs) >= 1

    @pytest.mark.unit
    def test_repeated_failures_dont_crash(self):
        """连续多次失败也不应累积崩溃"""
        with patch.object(obs_orch, "_emit_structured_log",
                          side_effect=RuntimeError("persistent failure")):
            for i in range(10):
                # 每次都不应抛异常
                obs_orch.trackEvent(f"event_fail_{i}")


# ============================================================================
# 5. _emit_structured_log 格式验证
# ============================================================================

class TestEmitStructuredLog:
    """_emit_structured_log 结构化日志格式"""

    @pytest.mark.unit
    def test_required_fields_present(self, caplog):
        """日志必须包含 trace_id, module_name, action, duration_ms（硬约束）"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch._emit_structured_log("test_action", duration_ms=42.5)

        log_data = json.loads(caplog.records[-1].getMessage())
        assert "trace_id" in log_data
        assert log_data["module_name"] == "orchestrator"
        assert log_data["action"] == "test_action"
        assert log_data["duration_ms"] == 42.5

    @pytest.mark.unit
    def test_custom_trace_id(self, caplog):
        """显式传入 trace_id 时应使用该值"""
        custom_tid = "custom-trace-12345"
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch._emit_structured_log("test_action", trace_id=custom_tid)

        log_data = json.loads(caplog.records[-1].getMessage())
        assert log_data["trace_id"] == custom_tid

    @pytest.mark.unit
    def test_auto_trace_id_when_none(self, caplog):
        """trace_id=None 时应自动生成"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch._emit_structured_log("test_action", trace_id=None)

        log_data = json.loads(caplog.records[-1].getMessage())
        assert log_data["trace_id"]  # 非空
        assert len(log_data["trace_id"]) > 0

    @pytest.mark.unit
    def test_duration_rounded_to_2_decimals(self, caplog):
        """duration_ms 应四舍五入到 2 位小数"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch._emit_structured_log("test_action", duration_ms=42.56789)

        log_data = json.loads(caplog.records[-1].getMessage())
        assert log_data["duration_ms"] == 42.57

    @pytest.mark.unit
    def test_level_parameter(self, caplog):
        """level 参数应控制日志级别"""
        with caplog.at_level(logging.DEBUG, logger="agent.orchestrator"):
            obs_orch._emit_structured_log("debug_action", level="debug")
            obs_orch._emit_structured_log("warn_action", level="warning")
            obs_orch._emit_structured_log("error_action", level="error")

        actions = [json.loads(r.getMessage())["action"]
                   for r in caplog.records if "{" in r.getMessage()]
        assert "debug_action" in actions
        assert "warn_action" in actions
        assert "error_action" in actions

    @pytest.mark.unit
    def test_extra_payload_merged(self, caplog):
        """额外 payload 应合并到日志记录"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch._emit_structured_log("test_action",
                                          user_id="u123", skill_id="pdf-extractor")

        log_data = json.loads(caplog.records[-1].getMessage())
        assert log_data["user_id"] == "u123"
        assert log_data["skill_id"] == "pdf-extractor"

    @pytest.mark.unit
    def test_json_is_valid(self, caplog):
        """输出必须是合法 JSON"""
        with caplog.at_level(logging.INFO, logger="agent.orchestrator"):
            obs_orch._emit_structured_log("test_action", data={"nested": [1, 2, 3]})

        # 不应抛 JSONDecodeError
        parsed = json.loads(caplog.records[-1].getMessage())
        assert isinstance(parsed, dict)


# ============================================================================
# 6. skills_mgmt 变体测试（track_event + emit_metric + traced_action）
# ============================================================================

class TestSkillsMgmtTrackEvent:
    """skills_mgmt/observability.py 的 track_event（snake_case）变体"""

    @pytest.mark.unit
    def test_track_event_normal_call(self, caplog):
        """track_event 正常调用应输出结构化日志"""
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            obs_skills.track_event("skill_create", {"skill_id": "test-skill"})

        track_logs = [r for r in caplog.records if "track_event" in r.getMessage()]
        assert len(track_logs) >= 1
        log_data = json.loads(track_logs[-1].getMessage())
        assert log_data["module_name"] == "skills_mgmt"
        assert log_data["action"] == "track_event"
        assert log_data["event_name"] == "skill_create"
        assert log_data["payload"]["skill_id"] == "test-skill"

    @pytest.mark.unit
    def test_track_event_none_payload(self, caplog):
        """track_event payload=None 时应使用空字典"""
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            obs_skills.track_event("event_none", None)

        track_logs = [r for r in caplog.records if "track_event" in r.getMessage()]
        assert len(track_logs) >= 1
        log_data = json.loads(track_logs[-1].getMessage())
        assert log_data["payload"] == {}

    @pytest.mark.unit
    def test_track_event_error_isolation(self, caplog):
        """track_event 内部异常不应传播"""
        with patch.object(obs_skills, "_emit_structured_log",
                          side_effect=RuntimeError("log failure")):
            with caplog.at_level(logging.DEBUG, logger="agent.skills_mgmt"):
                result = obs_skills.track_event("event_error")

        assert result is None  # 不抛异常


# ============================================================================
# 7. emit_metric 测试
# ============================================================================

class TestEmitMetric:
    """emit_metric 指标发射函数"""

    @pytest.mark.unit
    def test_emit_metric_metrics_unavailable_noop(self):
        """_METRICS_AVAILABLE=False 时应为 no-op"""
        with patch.object(obs_skills, "_METRICS_AVAILABLE", False):
            # 不应抛异常
            obs_skills.emit_metric("yunshu_skill_test", value=1, kind="counter")

    @pytest.mark.unit
    def test_emit_metric_counter(self):
        """counter 类型应调用 inc_counter（若可用）"""
        mock_metrics = MagicMock()
        mock_metrics.inc_counter = MagicMock()
        with patch.object(obs_skills, "_metrics", mock_metrics), \
             patch.object(obs_skills, "_METRICS_AVAILABLE", True):
            obs_skills.emit_metric("yunshu_skill_test", value=1, kind="counter",
                                   labels={"success": "true"})

        mock_metrics.inc_counter.assert_called_once()

    @pytest.mark.unit
    def test_emit_metric_histogram(self):
        """histogram 类型应调用 observe_histogram"""
        mock_metrics = MagicMock()
        mock_metrics.observe_histogram = MagicMock()
        with patch.object(obs_skills, "_metrics", mock_metrics), \
             patch.object(obs_skills, "_METRICS_AVAILABLE", True):
            obs_skills.emit_metric("yunshu_skill_latency", value=42.5, kind="histogram")

        mock_metrics.observe_histogram.assert_called_once()

    @pytest.mark.unit
    def test_emit_metric_gauge(self):
        """gauge 类型应调用 set_gauge"""
        mock_metrics = MagicMock()
        mock_metrics.set_gauge = MagicMock()
        with patch.object(obs_skills, "_metrics", mock_metrics), \
             patch.object(obs_skills, "_METRICS_AVAILABLE", True):
            obs_skills.emit_metric("yunshu_skill_count", value=10, kind="gauge")

        mock_metrics.set_gauge.assert_called_once()

    @pytest.mark.unit
    def test_emit_metric_auto_adds_success_label(self):
        """labels 缺少 success/failure 时应自动补 success=true（硬约束）"""
        mock_metrics = MagicMock()
        mock_metrics.inc_counter = MagicMock()
        with patch.object(obs_skills, "_metrics", mock_metrics), \
             patch.object(obs_skills, "_METRICS_AVAILABLE", True):
            obs_skills.emit_metric("yunshu_skill_test", value=1, kind="counter",
                                   labels={"skill_id": "test"})

        call_kwargs = mock_metrics.inc_counter.call_args
        labels = call_kwargs.kwargs.get("labels", {})
        assert labels.get("success") == "true"

    @pytest.mark.unit
    def test_emit_metric_preserves_existing_success_label(self):
        """已有 success 标签时不应覆盖"""
        mock_metrics = MagicMock()
        mock_metrics.inc_counter = MagicMock()
        with patch.object(obs_skills, "_metrics", mock_metrics), \
             patch.object(obs_skills, "_METRICS_AVAILABLE", True):
            obs_skills.emit_metric("yunshu_skill_test", value=1, kind="counter",
                                   labels={"success": "false", "skill_id": "test"})

        call_kwargs = mock_metrics.inc_counter.call_args
        labels = call_kwargs.kwargs.get("labels", {})
        assert labels["success"] == "false"  # 不被覆盖

    @pytest.mark.unit
    def test_emit_metric_error_isolation(self):
        """emit_metric 内部异常不应传播（硬约束）"""
        mock_metrics = MagicMock()
        mock_metrics.inc_counter.side_effect = RuntimeError("metrics DB down")
        with patch.object(obs_skills, "_metrics", mock_metrics), \
             patch.object(obs_skills, "_METRICS_AVAILABLE", True):
            # 不应抛异常
            obs_skills.emit_metric("yunshu_skill_test", value=1, kind="counter")

    @pytest.mark.unit
    def test_emit_metric_unknown_kind_noop(self):
        """未知 kind 类型应为 no-op（不抛异常）"""
        mock_metrics = MagicMock()
        with patch.object(obs_skills, "_metrics", mock_metrics), \
             patch.object(obs_skills, "_METRICS_AVAILABLE", True):
            obs_skills.emit_metric("yunshu_skill_test", value=1, kind="unknown_type")

        # 不应调用任何指标方法
        mock_metrics.inc_counter.assert_not_called()
        mock_metrics.observe_histogram.assert_not_called()
        mock_metrics.set_gauge.assert_not_called()


# ============================================================================
# 8. traced_action 上下文管理器测试
# ============================================================================

class TestTracedAction:
    """traced_action 上下文管理器"""

    @pytest.mark.unit
    def test_normal_execution_logs_start_and_end(self, caplog):
        """正常执行应输出 .start 和 .end 日志"""
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            with obs_skills.traced_action("skill_create", skill_id="test"):
                pass  # 模拟操作

        actions = []
        for r in caplog.records:
            try:
                data = json.loads(r.getMessage())
                actions.append(data.get("action", ""))
            except json.JSONDecodeError:
                pass

        assert "skill_create.start" in actions
        assert "skill_create.end" in actions

    @pytest.mark.unit
    def test_end_log_has_status_ok(self, caplog):
        """正常结束时 .end 日志应有 status=ok"""
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            with obs_skills.traced_action("test_action"):
                pass

        end_logs = [r for r in caplog.records
                    if "test_action.end" in r.getMessage()]
        assert len(end_logs) >= 1
        log_data = json.loads(end_logs[-1].getMessage())
        assert log_data["status"] == "ok"

    @pytest.mark.unit
    def test_end_log_has_positive_duration(self, caplog):
        """正常结束时 .end 日志应有正数 duration_ms"""
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            with obs_skills.traced_action("test_action"):
                time.sleep(0.01)  # 10ms

        end_logs = [r for r in caplog.records if "test_action.end" in r.getMessage()]
        log_data = json.loads(end_logs[-1].getMessage())
        assert log_data["duration_ms"] > 0

    @pytest.mark.unit
    def test_exception_logs_error_and_reraises(self, caplog):
        """异常时应输出 .error 日志并重新抛出原异常"""
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            with pytest.raises(ValueError, match="test error"):
                with obs_skills.traced_action("failing_action"):
                    raise ValueError("test error")

        error_logs = [r for r in caplog.records
                      if "failing_action.error" in r.getMessage()]
        assert len(error_logs) >= 1
        log_data = json.loads(error_logs[-1].getMessage())
        assert log_data["status"] == "error"
        assert "test error" in log_data["error"]
        assert log_data["error_type"] == "ValueError"

    @pytest.mark.unit
    def test_context_yields_dict(self):
        """traced_action 应 yield 一个字典上下文"""
        with obs_skills.traced_action("test_action") as ctx:
            assert isinstance(ctx, dict)
            assert "trace_id" in ctx
            assert "payload" in ctx

    @pytest.mark.unit
    def test_custom_trace_id(self, caplog):
        """显式传入 trace_id 时 start/end 日志应使用同一值"""
        custom_tid = "custom-trace-abc"
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            with obs_skills.traced_action("test_action", trace_id=custom_tid):
                pass

        all_logs = [json.loads(r.getMessage())
                    for r in caplog.records
                    if "test_action" in r.getMessage() and "{" in r.getMessage()]
        for log_data in all_logs:
            assert log_data["trace_id"] == custom_tid

    @pytest.mark.unit
    def test_payload_passed_to_start_log(self, caplog):
        """payload 应传递到 .start 日志"""
        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            with obs_skills.traced_action("test_action", skill_id="foo", user="bar"):
                pass

        start_logs = [r for r in caplog.records
                      if "test_action.start" in r.getMessage()]
        assert len(start_logs) >= 1
        log_data = json.loads(start_logs[-1].getMessage())
        assert log_data["skill_id"] == "foo"
        assert log_data["user"] == "bar"


# ============================================================================
# 9. 多模块一致性测试（验证模板生成的 observability.py 一致性）
# ============================================================================

class TestMultiModuleConsistency:
    """验证多个模块的 observability.py 遵循一致接口"""

    @pytest.mark.unit
    @pytest.mark.parametrize("module_path,module_name", [
        ("agent.orchestrator.observability", "orchestrator"),
        ("agent.tools.observability", "tools"),
        ("agent.memory.observability", "memory"),
        ("agent.model_router.observability", "model_router"),
        ("agent.caching.observability", "caching"),
        ("agent.web.observability", "web"),
        ("agent.guardrails.observability", "guardrails"),
        ("agent.audit.observability", "audit"),
    ])
    def test_trackEvent_interface_consistency(self, module_path, module_name, caplog):
        """所有模块的 trackEvent 应有一致接口签名和行为"""
        import importlib
        mod = importlib.import_module(module_path)

        # 接口一致性: trackEvent(event_name, payload) 函数存在
        assert hasattr(mod, "trackEvent"), f"{module_path} 缺少 trackEvent"
        assert callable(mod.trackEvent)

        # 行为一致性: 调用不抛异常
        logger_name = f"agent.{module_name}"
        with caplog.at_level(logging.INFO, logger=logger_name):
            mod.trackEvent("consistency_test", {"check": True})

        # 输出一致性: 日志含必填字段
        track_logs = [r for r in caplog.records
                      if "track.consistency_test" in r.getMessage()]
        assert len(track_logs) >= 1, f"{module_name} 未输出 track 日志"

        log_data = json.loads(track_logs[-1].getMessage())
        assert log_data["module_name"] == module_name
        assert "trace_id" in log_data
        assert "duration_ms" in log_data
        assert log_data["event_name"] == "consistency_test"

    @pytest.mark.unit
    @pytest.mark.parametrize("module_path", [
        "agent.orchestrator.observability",
        "agent.tools.observability",
        "agent.memory.observability",
        "agent.caching.observability",
    ])
    def test_trackEvent_error_isolation_across_modules(self, module_path):
        """所有模块的 trackEvent 在内部异常时都不应传播"""
        import importlib
        mod = importlib.import_module(module_path)

        with patch.object(mod, "_emit_structured_log",
                          side_effect=RuntimeError("forced failure")):
            # 不应抛异常
            result = mod.trackEvent("error_test")
            assert result is None


# ============================================================================
# 10. 性能与并发测试
# ============================================================================

class TestTrackEventPerformance:
    """trackEvent 性能测试（约束：单次埋点耗时 < 5ms）"""

    @pytest.mark.unit
    def test_single_call_under_1ms(self):
        """单次 trackEvent 调用耗时应 < 5ms（CI runner 性能波动宽容阈值）"""
        # 预热（首次调用可能有 import 开销）
        obs_orch.trackEvent("warmup")

        # 实际测量
        iterations = 100
        t0 = time.perf_counter()
        for _ in range(iterations):
            obs_orch.trackEvent("perf_test", {"i": 1})
        elapsed_ms = (time.perf_counter() - t0) * 1000

        avg_ms = elapsed_ms / iterations
        # Why: 原 1ms 阈值在 CI 共享 runner 上不可靠（实测可达 1.9ms），
        # 5ms 仍能捕获真实性能回归（>5ms 明显异常）
        assert avg_ms < 5.0, f"单次埋点耗时 {avg_ms:.3f}ms 超过 5ms 阈值"

    @pytest.mark.unit
    def test_concurrent_calls_thread_safe(self):
        """多线程并发调用 trackEvent 不应崩溃或数据错乱"""
        import threading

        errors = []
        barrier = threading.Barrier(10)

        def worker(thread_id):
            barrier.wait()
            for i in range(50):
                try:
                    obs_orch.trackEvent(f"concurrent_event_{thread_id}", {"i": i})
                except Exception as e:
                    errors.append((thread_id, i, str(e)))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发调用出错: {errors[:3]}"


# ============================================================================
# 测试入口
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
