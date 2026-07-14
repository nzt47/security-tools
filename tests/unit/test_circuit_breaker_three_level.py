"""三级熔断器(SESSION/USER/GLOBAL)单元测试

测试覆盖(对应任务验收标准):
1. 三级独立计数 — 每级互不污染
2. 级联触发 — SESSION → USER → GLOBAL 任一触发即熔断,记录触发级别
3. 冷却恢复 — 每级独立冷却超时后恢复
4. ContextVar 隔离 — _trace_id_ctx 不受三级熔断影响
5. 配置校验 — ValidationRule 架构校验三级配置
6. 性能 — 三级检查 < 0.1ms(纯内存字典查询)
7. tool_trace 集成 — 熔断事件写入 trace
8. 向后兼容 — 现有 CircuitBreaker API 不变

设计要点(三义):
- [不易] 现有 CircuitBreaker/CircuitBreakerConfig/CircuitState 公开 API 不变
- [不易] ContextVar _trace_id_ctx 隔离机制不动
- [变易] ThreeLevelCircuitBreaker 组合 3 个 CircuitBreaker,按 scope 独立配置
- [简易] 三级检查 = 3 次 dict 查询,满足 < 0.1ms 性能要求
"""

from __future__ import annotations

import time
import threading
import logging
from unittest.mock import MagicMock, patch

import pytest

from agent.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitState,
    # 以下为新增导出(测试时尚未实现,ImportError 即 RED 验证)
    CircuitScope,
    ThreeLevelBreakerConfig,
    ThreeLevelCircuitBreaker,
)
from agent.config_validation import (
    CIRCUIT_BREAKER_VALIDATION_RULES,
    validate_dict_against_rules,
)


# ════════════════════════════════════════════════════════════════
#  fixture
# ════════════════════════════════════════════════════════════════

@pytest.fixture
def fast_three_level_breaker():
    """快速恢复的三级熔断器(测试用,冷却 0.3s)"""
    config = ThreeLevelBreakerConfig(
        session=CircuitBreakerConfig(
            failure_threshold=1.0, min_requests=5,
            reset_timeout=0.3, window_seconds=60,
            max_attempts=1, name="session",
        ),
        user=CircuitBreakerConfig(
            failure_threshold=1.0, min_requests=10,
            reset_timeout=0.3, window_seconds=60,
            max_attempts=1, name="user",
        ),
        global_=CircuitBreakerConfig(
            failure_threshold=1.0, min_requests=20,
            reset_timeout=0.3, window_seconds=60,
            max_attempts=1, name="global",
        ),
    )
    breaker = ThreeLevelCircuitBreaker(config)
    yield breaker
    breaker.reset()


@pytest.fixture
def default_three_level_breaker():
    """默认配置三级熔断器(阈值 5/20/100,冷却 60/300/600)"""
    breaker = ThreeLevelCircuitBreaker()
    yield breaker
    breaker.reset()


# ════════════════════════════════════════════════════════════════
#  1. 三级独立计数
# ════════════════════════════════════════════════════════════════

class TestThreeLevelIndependentCounting:
    """验证 SESSION/USER/GLOBAL 三级独立计数,互不污染"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_level_triggers_at_threshold(self, fast_three_level_breaker):
        """SESSION 级在 5 次连续失败后触发,USER/GLOBAL 不受影响"""
        b = fast_three_level_breaker
        # 4 次失败:不触发
        for _ in range(4):
            b.record_result("sess-A", "user-A", "tool-X", False)
        allowed, scope = b.allow_request("sess-A", "user-A", "tool-X")
        assert allowed is True
        assert scope is None
        # 第 5 次失败:触发 SESSION
        b.record_result("sess-A", "user-A", "tool-X", False)
        allowed, scope = b.allow_request("sess-A", "user-A", "tool-X")
        assert allowed is False
        assert scope == CircuitScope.SESSION

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_isolation_between_sessions(self, fast_three_level_breaker):
        """SESSION 级按 session_id 隔离:session-A 熔断不影响 session-B"""
        b = fast_three_level_breaker
        for _ in range(5):
            b.record_result("sess-A", "user-A", "tool-X", False)
        # session-A 被熔断
        allowed_a, _ = b.allow_request("sess-A", "user-A", "tool-X")
        assert allowed_a is False
        # session-B 不受影响(同用户、同工具)
        allowed_b, scope_b = b.allow_request("sess-B", "user-A", "tool-X")
        assert allowed_b is True
        assert scope_b is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_user_level_triggers_independent_of_session(self, fast_three_level_breaker):
        """USER 级在 10 次连续失败后触发,跨会话累积"""
        b = fast_three_level_breaker
        # 不同 session 但同一 user,各失败 5 次(共 10 次,触发 USER)
        for _ in range(5):
            b.record_result("sess-A", "user-A", "tool-X", False)
        for _ in range(5):
            b.record_result("sess-B", "user-A", "tool-X", False)
        # USER 级触发(高危工具)
        allowed, scope = b.allow_request("sess-C", "user-A", "tool-X", is_high_risk=True)
        assert allowed is False
        assert scope == CircuitScope.USER

    @pytest.mark.unit
    @pytest.mark.p0
    def test_user_isolation_between_users(self, fast_three_level_breaker):
        """USER 级按 user_id 隔离:user-A 熔断不影响 user-B"""
        b = fast_three_level_breaker
        for _ in range(10):
            b.record_result("sess-A", "user-A", "tool-X", False)
        # user-A 高危工具被熔断
        allowed_a, _ = b.allow_request("sess-Z", "user-A", "tool-X", is_high_risk=True)
        assert allowed_a is False
        # user-B 不受影响
        allowed_b, _ = b.allow_request("sess-Z", "user-B", "tool-X", is_high_risk=True)
        assert allowed_b is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_level_triggers_across_all_sessions_users(self, fast_three_level_breaker):
        """GLOBAL 级在 20 次连续失败后触发,跨会话跨用户累积"""
        b = fast_three_level_breaker
        # 2 个 session × 2 个 user × 5 次失败 = 20 次
        for sess in ("sess-A", "sess-B"):
            for user in ("user-A", "user-B"):
                for _ in range(5):
                    b.record_result(sess, user, "tool-X", False)
        # GLOBAL 级触发(新会话、新用户)
        allowed, scope = b.allow_request("sess-new", "user-new", "tool-X")
        assert allowed is False
        assert scope == CircuitScope.GLOBAL

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_isolation_between_tools(self, fast_three_level_breaker):
        """GLOBAL 级按 tool_name 隔离:tool-X 熔断不影响 tool-Y"""
        b = fast_three_level_breaker
        for _ in range(20):
            b.record_result("sess-A", "user-A", "tool-X", False)
        # tool-X 全局熔断
        allowed_x, _ = b.allow_request("sess-new", "user-new", "tool-X")
        assert allowed_x is False
        # tool-Y 不受影响
        allowed_y, _ = b.allow_request("sess-new", "user-new", "tool-Y")
        assert allowed_y is True


# ════════════════════════════════════════════════════════════════
#  2. 级联触发(SESSION → USER → GLOBAL)
# ════════════════════════════════════════════════════════════════

class TestCascadeTrigger:
    """验证三级级联触发顺序与短路语义"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_trigger_takes_precedence_over_user_global(self, fast_three_level_breaker):
        """SESSION 触发时优先返回 SESSION,不检查 USER/GLOBAL"""
        b = fast_three_level_breaker
        # 同时触发 SESSION(5次)和 USER(10次)和 GLOBAL(20次)
        for _ in range(20):
            b.record_result("sess-A", "user-A", "tool-X", False)
        # 应优先返回 SESSION
        allowed, scope = b.allow_request("sess-A", "user-A", "tool-X", is_high_risk=True)
        assert allowed is False
        assert scope == CircuitScope.SESSION

    @pytest.mark.unit
    @pytest.mark.p0
    def test_user_trigger_when_session_not_triggered(self, fast_three_level_breaker):
        """SESSION 未触发时,USER 触发(仅高危工具)"""
        b = fast_three_level_breaker
        # 在不同 session 累积 10 次失败(触发 USER,但不触发任何单一 SESSION)
        for i in range(10):
            b.record_result(f"sess-{i}", "user-A", "tool-X", False)
        # 新 session:SESSION 未触发,但 USER 触发(高危)
        allowed, scope = b.allow_request("sess-new", "user-A", "tool-X", is_high_risk=True)
        assert allowed is False
        assert scope == CircuitScope.USER

    @pytest.mark.unit
    @pytest.mark.p0
    def test_user_trigger_does_not_block_low_risk_tool(self, fast_three_level_breaker):
        """USER 熔断仅阻断高危工具,低危工具不受影响"""
        b = fast_three_level_breaker
        for i in range(10):
            b.record_result(f"sess-{i}", "user-A", "tool-X", False)
        # 低危工具:USER 不阻断
        allowed, scope = b.allow_request("sess-new", "user-A", "tool-X", is_high_risk=False)
        assert allowed is True
        assert scope is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_trigger_when_session_user_not_triggered(self, fast_three_level_breaker):
        """SESSION 和 USER 未触发时,GLOBAL 触发"""
        b = fast_three_level_breaker
        # 跨 session 跨 user 累积 20 次失败(触发 GLOBAL,但不触发任何单一 SESSION 或 USER)
        for i in range(20):
            b.record_result(f"sess-{i}", f"user-{i}", "tool-X", False)
        # 新 session、新 user:SESSION 和 USER 未触发,GLOBAL 触发
        allowed, scope = b.allow_request("sess-new", "user-new", "tool-X")
        assert allowed is False
        assert scope == CircuitScope.GLOBAL

    @pytest.mark.unit
    @pytest.mark.p0
    def test_triggered_level_recorded_and_cleared_on_success(self, fast_three_level_breaker):
        """触发级别被记录,成功后清除"""
        b = fast_three_level_breaker
        for _ in range(5):
            b.record_result("sess-A", "user-A", "tool-X", False)
        # SESSION 触发,记录级别
        assert b.get_triggered_level("sess-A", "user-A", "tool-X") == CircuitScope.SESSION
        # 成功后清除(需先等冷却恢复)
        time.sleep(0.4)
        b.record_result("sess-A", "user-A", "tool-X", True)
        assert b.get_triggered_level("sess-A", "user-A", "tool-X") is None


# ════════════════════════════════════════════════════════════════
#  3. 冷却恢复
# ════════════════════════════════════════════════════════════════

class TestCooldownRecovery:
    """验证每级独立冷却恢复"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_session_cooldown_recovers(self, fast_three_level_breaker):
        """SESSION 级冷却后恢复"""
        b = fast_three_level_breaker
        for _ in range(5):
            b.record_result("sess-A", "user-A", "tool-X", False)
        allowed, _ = b.allow_request("sess-A", "user-A", "tool-X")
        assert allowed is False
        # 等待冷却(0.3s)
        time.sleep(0.4)
        # 半开状态:允许 1 次探测
        allowed, _ = b.allow_request("sess-A", "user-A", "tool-X")
        assert allowed is True
        # 探测成功 → 恢复
        b.record_result("sess-A", "user-A", "tool-X", True)
        allowed, _ = b.allow_request("sess-A", "user-A", "tool-X")
        assert allowed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_user_cooldown_recovers(self, fast_three_level_breaker):
        """USER 级冷却后恢复(仅高危工具)"""
        b = fast_three_level_breaker
        for i in range(10):
            b.record_result(f"sess-{i}", "user-A", "tool-X", False)
        allowed, _ = b.allow_request("sess-new", "user-A", "tool-X", is_high_risk=True)
        assert allowed is False
        time.sleep(0.4)
        # 半开探测成功
        b.record_result("sess-new", "user-A", "tool-X", True)
        allowed, _ = b.allow_request("sess-new", "user-A", "tool-X", is_high_risk=True)
        assert allowed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_cooldown_recovers(self, fast_three_level_breaker):
        """GLOBAL 级冷却后恢复"""
        b = fast_three_level_breaker
        for i in range(20):
            b.record_result(f"sess-{i}", f"user-{i}", "tool-X", False)
        allowed, _ = b.allow_request("sess-new", "user-new", "tool-X")
        assert allowed is False
        time.sleep(0.4)
        b.record_result("sess-new", "user-new", "tool-X", True)
        allowed, _ = b.allow_request("sess-new", "user-new", "tool-X")
        assert allowed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_levels_recover_independently(self):
        """三级独立恢复:SESSION 恢复后 USER 仍熔断(需不同冷却时间)"""
        # 使用不同冷却时间:SESSION=0.3s, USER=1.0s
        config = ThreeLevelBreakerConfig(
            session=CircuitBreakerConfig(
                failure_threshold=1.0, min_requests=5,
                reset_timeout=0.3, window_seconds=60,
                max_attempts=1, name="session",
            ),
            user=CircuitBreakerConfig(
                failure_threshold=1.0, min_requests=10,
                reset_timeout=1.0,  # 比 SESSION 长
                window_seconds=60,
                max_attempts=1, name="user",
            ),
            global_=CircuitBreakerConfig(
                failure_threshold=1.0, min_requests=20,
                reset_timeout=0.3, window_seconds=60,
                max_attempts=1, name="global",
            ),
        )
        b = ThreeLevelCircuitBreaker(config)
        try:
            # 同时触发 SESSION 和 USER
            for _ in range(10):
                b.record_result("sess-A", "user-A", "tool-X", False)
            # SESSION 触发(优先)
            allowed, scope = b.allow_request("sess-A", "user-A", "tool-X", is_high_risk=True)
            assert scope == CircuitScope.SESSION
            # 等待 SESSION 冷却(0.3s),但 USER 未冷却(1.0s)
            time.sleep(0.4)
            # 先 allow_request 触发 SESSION → HALF_OPEN,然后 record_result 恢复 SESSION
            allowed, _ = b.allow_request("sess-A", "user-A", "tool-X")
            assert allowed is True  # SESSION HALF_OPEN 允许探测,USER 不检查(非高危)
            b.record_result("sess-A", "user-A", "tool-X", True)  # SESSION HALF_OPEN 成功 → CLOSED
            # SESSION 已恢复,但 USER 仍打开(冷却未到)
            allowed, scope = b.allow_request("sess-A", "user-A", "tool-X", is_high_risk=True)
            assert allowed is False
            assert scope == CircuitScope.USER
        finally:
            b.reset()


# ════════════════════════════════════════════════════════════════
#  4. ContextVar 隔离
# ════════════════════════════════════════════════════════════════

class TestContextVarIsolation:
    """验证 _trace_id_ctx ContextVar 不受三级熔断影响"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trace_id_contextvar_not_polluted(self, fast_three_level_breaker):
        """三级熔断操作不污染 _trace_id_ctx"""
        from agent.circuit_breaker import set_trace_id, get_trace_id, _trace_id_ctx
        # 初始清空
        _trace_id_ctx.set("")
        b = fast_three_level_breaker
        # 执行三级熔断操作
        for _ in range(5):
            b.record_result("sess-A", "user-A", "tool-X", False)
        b.allow_request("sess-A", "user-A", "tool-X")
        # trace_id 应保持为空(未被三级熔断修改)
        assert get_trace_id() == ""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_trace_id_contextvar_preserved_across_breaker_calls(self, fast_three_level_breaker):
        """三级熔断调用前设置的 trace_id 在调用后保持不变"""
        from agent.circuit_breaker import set_trace_id, get_trace_id, _trace_id_ctx
        _trace_id_ctx.set("")
        set_trace_id("test-trace-12345")
        b = fast_three_level_breaker
        b.record_result("sess-A", "user-A", "tool-X", False)
        b.allow_request("sess-A", "user-A", "tool-X")
        assert get_trace_id() == "test-trace-12345"
        _trace_id_ctx.set("")


# ════════════════════════════════════════════════════════════════
#  5. 配置校验(ValidationRule 架构)
# ════════════════════════════════════════════════════════════════

class TestConfigValidation:
    """验证三级熔断配置纳入 ValidationRule 架构"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_validation_rules_exist(self):
        """CIRCUIT_BREAKER_VALIDATION_RULES 已定义"""
        assert len(CIRCUIT_BREAKER_VALIDATION_RULES) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_valid_config_passes_validation(self):
        """合法三级配置通过校验"""
        valid_config = {
            "session_failure_threshold": 1.0,
            "session_min_requests": 5,
            "session_recovery_timeout": 60.0,
            "session_half_open_max_calls": 1,
            "user_failure_threshold": 1.0,
            "user_min_requests": 20,
            "user_recovery_timeout": 300.0,
            "user_half_open_max_calls": 2,
            "global_failure_threshold": 1.0,
            "global_min_requests": 100,
            "global_recovery_timeout": 600.0,
            "global_half_open_max_calls": 3,
        }
        errors = validate_dict_against_rules(valid_config, CIRCUIT_BREAKER_VALIDATION_RULES)
        assert errors == [], f"合法配置校验失败: {errors}"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_invalid_threshold_rejected(self):
        """非法阈值(failure_threshold > 1)被拒绝"""
        invalid_config = {
            "session_failure_threshold": 1.5,  # 非法:>1
            "session_min_requests": 5,
            "session_recovery_timeout": 60.0,
            "session_half_open_max_calls": 1,
            "user_failure_threshold": 1.0,
            "user_min_requests": 20,
            "user_recovery_timeout": 300.0,
            "user_half_open_max_calls": 2,
            "global_failure_threshold": 1.0,
            "global_min_requests": 100,
            "global_recovery_timeout": 600.0,
            "global_half_open_max_calls": 3,
        }
        errors = validate_dict_against_rules(invalid_config, CIRCUIT_BREAKER_VALIDATION_RULES)
        assert len(errors) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_invalid_recovery_timeout_rejected(self):
        """非法冷却时间(<0)被拒绝"""
        invalid_config = {
            "session_failure_threshold": 1.0,
            "session_min_requests": 5,
            "session_recovery_timeout": -1.0,  # 非法:<0
            "session_half_open_max_calls": 1,
            "user_failure_threshold": 1.0,
            "user_min_requests": 20,
            "user_recovery_timeout": 300.0,
            "user_half_open_max_calls": 2,
            "global_failure_threshold": 1.0,
            "global_min_requests": 100,
            "global_recovery_timeout": 600.0,
            "global_half_open_max_calls": 3,
        }
        errors = validate_dict_against_rules(invalid_config, CIRCUIT_BREAKER_VALIDATION_RULES)
        assert len(errors) > 0


# ════════════════════════════════════════════════════════════════
#  6. 性能(三级检查 < 0.1ms)
# ════════════════════════════════════════════════════════════════

class TestPerformance:
    """验证三级检查性能 < 0.1ms"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_three_level_check_under_0_1ms(self, default_three_level_breaker):
        """三级 allow_request 纯内存查询 < 0.1ms"""
        b = default_three_level_breaker
        # 预热(确保字典已填充)
        b.record_result("sess-warm", "user-warm", "tool-warm", True)
        # 测量 1000 次取平均
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            b.allow_request("sess-warm", "user-warm", "tool-warm")
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed / iterations) * 1000
        assert avg_ms < 0.1, f"三级检查平均耗时 {avg_ms:.4f}ms 超过 0.1ms 阈值"


# ════════════════════════════════════════════════════════════════
#  7. tool_trace 集成
# ════════════════════════════════════════════════════════════════

class TestToolTraceIntegration:
    """验证熔断事件写入 tool_trace"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_circuit_event_emitted_on_block(self, fast_three_level_breaker):
        """熔断阻断时触发 trace 事件"""
        # 使用 mock trace_recorder
        mock_recorder = MagicMock()
        b = ThreeLevelCircuitBreaker(
            config=fast_three_level_breaker._config,
            trace_recorder=mock_recorder,
        )
        try:
            for _ in range(5):
                b.record_result("sess-A", "user-A", "tool-X", False)
            # 触发阻断
            b.allow_request("sess-A", "user-A", "tool-X")
            # 验证 trace_recorder.record_circuit_event 被调用
            assert mock_recorder.record_circuit_event.called
            call_args = mock_recorder.record_circuit_event.call_args
            assert call_args.kwargs.get("scope") == CircuitScope.SESSION or \
                   call_args[1].get("scope") == CircuitScope.SESSION
        finally:
            b.reset()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_trace_event_when_allowed(self, default_three_level_breaker):
        """请求被允许时不触发 trace 事件"""
        mock_recorder = MagicMock()
        b = ThreeLevelCircuitBreaker(
            config=default_three_level_breaker._config,
            trace_recorder=mock_recorder,
        )
        try:
            b.allow_request("sess-A", "user-A", "tool-X")
            assert not mock_recorder.record_circuit_event.called
        finally:
            b.reset()


# ════════════════════════════════════════════════════════════════
#  8. 向后兼容 + call_with_breaker 入口
# ════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """验证现有 CircuitBreaker API 不变 + call_with_breaker 新入口"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_existing_circuit_breaker_still_works(self):
        """现有 CircuitBreaker 单点熔断仍正常工作"""
        breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1.0, min_requests=3,
            reset_timeout=30, name="legacy",
        ))
        assert breaker.state == CircuitState.CLOSED
        for _ in range(3):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    @pytest.mark.unit
    @pytest.mark.p0
    def test_call_with_breaker_success(self, default_three_level_breaker):
        """call_with_breaker 成功路径"""
        b = default_three_level_breaker
        result = b.call_with_breaker(
            lambda x, y: x + y, 3, 4,
            session_id="sess-A", user_id="user-A", tool_name="tool-X",
        )
        assert result == 7

    @pytest.mark.unit
    @pytest.mark.p0
    def test_call_with_breaker_raises_on_block(self, fast_three_level_breaker):
        """call_with_breaker 熔断时抛 CircuitBreakerError"""
        b = fast_three_level_breaker
        for _ in range(5):
            b.record_result("sess-A", "user-A", "tool-X", False)
        with pytest.raises(CircuitBreakerError) as exc_info:
            b.call_with_breaker(
                lambda: "should not reach",
                session_id="sess-A", user_id="user-A", tool_name="tool-X",
            )
        # 异常应包含触发级别信息
        assert CircuitScope.SESSION.value in str(exc_info.value) or \
               "session" in str(exc_info.value).lower()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_call_with_breaker_records_failure(self, default_three_level_breaker):
        """call_with_breaker 失败时记录到三级熔断器"""
        b = default_three_level_breaker
        def failing_func():
            raise ValueError("test error")
        with pytest.raises(ValueError):
            b.call_with_breaker(
                failing_func,
                session_id="sess-A", user_id="user-A", tool_name="tool-X",
            )
        # 验证失败被记录(SESSION 级有 1 次失败)
        status = b.get_status("sess-A", "user-A", "tool-X")
        assert status["session"]["metrics"]["failures"] == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status_returns_three_level_snapshot(self, default_three_level_breaker):
        """get_status 返回三级状态快照"""
        b = default_three_level_breaker
        b.record_result("sess-A", "user-A", "tool-X", False)
        status = b.get_status("sess-A", "user-A", "tool-X")
        assert "session" in status
        assert "user" in status
        assert "global" in status
        assert "state" in status["session"]
        assert "metrics" in status["session"]
