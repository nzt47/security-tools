"""任务 3：熔断与降级体系统一 — 单元测试

覆盖 agent/circuit_breaker.py / agent/error_handler.py / agent.graceful_degrade.py
的单一熔断事实源、降级诚实化与恢复路径公开 API。

验收清单对应：
- #2 critic 降级结果 overall_score is None 且 degraded=True
- #3 触发降级后 _module_states[module]["success_count"] 不增加
- #5 get_metrics() 输出含 degraded_fallbacks_used 与 degraded_calls_avoided
"""

import pytest

from agent.graceful_degrade import (
    GracefulDegrade,
    DegradeConfig,
    DegradeModule,
)
from agent.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    get_breaker_registry,
    get_all_circuit_breaker_status,
    force_half_open,
    reset_breakers,
)
from agent.error_handler import ErrorHandler


# ═══════════════════════════════════════════════════════════════
#  [D8] 降级诚实化
# ═══════════════════════════════════════════════════════════════

class TestDegradeHonesty:
    """降级不伪造成功：degraded=True 贯穿降级返回对象，不计 success"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_critic_degrade_returns_none_score_and_degraded(self):
        """验收 #2：critic 降级结果 overall_score is None 且 degraded=True"""
        degrade = GracefulDegrade()
        result = degrade.critic_evaluate_with_degrade("test input")
        assert result["degraded"] is True
        assert result["overall_score"] is None
        assert "Critic 服务不可用" in result.get("reason", "")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_critic_degrade_does_not_count_success(self):
        """验收 #3：critic 降级后 success_count 不增加"""
        degrade = GracefulDegrade()
        before = degrade._get_module_state(DegradeModule.CRITIC)["success_count"]
        degrade.critic_evaluate_with_degrade("test input")
        after = degrade._get_module_state(DegradeModule.CRITIC)["success_count"]
        assert after == before

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_degrade_empty_does_not_count_success(self):
        """降级返回空结果不记为成功（返回类型保持 list）"""
        degrade = GracefulDegrade()
        before = degrade._get_module_state(DegradeModule.MEMORY)["success_count"]
        result = degrade.memory_query_with_degrade("test query")
        assert isinstance(result, list)
        after = degrade._get_module_state(DegradeModule.MEMORY)["success_count"]
        assert after == before

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_degrade_without_validator(self):
        """无注入验证器时 valid=False + degraded=True，不伪造放行"""
        degrade = GracefulDegrade()
        result = degrade.schema_validate_with_degrade({"key": "value"}, {})
        assert result["valid"] is False
        assert result["degraded"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_degrade_with_validator(self):
        """注入验证器后真实校验：通过时 valid=True + degraded=False"""
        degrade = GracefulDegrade()
        degrade.set_schema_validator(
            lambda data: {"valid": True, "errors": [], "warnings": []}
        )
        result = degrade.schema_validate_with_degrade({"key": "value"}, {})
        assert result["valid"] is True
        assert result["degraded"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_validator_rejects(self):
        """验证器判定不合法时 valid=False（诚实反映校验结果，非降级）"""
        degrade = GracefulDegrade()
        degrade.set_schema_validator(
            lambda data: {"valid": False, "errors": ["missing title"], "warnings": []}
        )
        result = degrade.schema_validate_with_degrade({"content": "x"}, {})
        assert result["valid"] is False
        assert result["errors"] == ["missing title"]
        assert result["degraded"] is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_validator_exception_degrades(self):
        """验证器抛异常时降级：valid=False + degraded=True"""
        degrade = GracefulDegrade()

        def boom(data):
            raise RuntimeError("validator crash")

        degrade.set_schema_validator(boom)
        result = degrade.schema_validate_with_degrade({"key": "value"}, {})
        assert result["valid"] is False
        assert result["degraded"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dashboard_degrade_marks_degraded(self):
        """dashboard 降级返回对象带 degraded=True 标记"""
        degrade = GracefulDegrade()
        result = degrade.dashboard_data_with_degrade("test_endpoint")
        assert isinstance(result, dict)
        assert result.get("degraded") is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_critic_degrade_accepts_legacy_three_args(self):
        """既有调用方（critic.py）传 3 个位置参数仍兼容（签名向后兼容）"""
        degrade = GracefulDegrade()
        result = degrade.critic_evaluate_with_degrade(
            "user_query", "response", {"context": "x"}
        )
        assert result["degraded"] is True
        assert result["overall_score"] is None


# ═══════════════════════════════════════════════════════════════
#  [D10] 单一熔断事实源
# ═══════════════════════════════════════════════════════════════

class TestBreakerSingleSource:
    """error_handler 熔断与 circuit_breaker 全局注册表同源"""

    @staticmethod
    def _fresh_handler() -> ErrorHandler:
        reset_breakers()
        return ErrorHandler()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_registry_is_shared_dict(self):
        """error_handler._circuit_breakers 与全局注册表是同一 dict"""
        handler = self._fresh_handler()
        assert handler._circuit_breakers is get_breaker_registry()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_open_breaker_visible_in_global_status(self):
        """验收 #1：error_handler 触发熔断后全局状态能查到同名 OPEN"""
        handler = self._fresh_handler()
        cb = CircuitBreaker(name="test_cb")
        handler.register_circuit_breaker("test_cb", cb)
        for _ in range(100):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        status = get_all_circuit_breaker_status()
        assert "test_cb" in status
        assert status["test_cb"]["state"] == "open"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_status_queries_consistent(self):
        """两套查询接口返回一致"""
        handler = self._fresh_handler()
        cb = CircuitBreaker(name="test_cb")
        handler.register_circuit_breaker("test_cb", cb)
        # 两套查询均实时计算 time_since_last_state_change（now - last_state_change），
        # 两次调用间存在毫秒级时序差，比较前剔除该时序字段（与契约测试同源修复）
        def _strip_timing(d):
            return {name: {k: v for k, v in st.items()
                           if k != "time_since_last_state_change"}
                    for name, st in d.items()}
        assert _strip_timing(handler.get_circuit_breaker_status()) == \
            _strip_timing(get_all_circuit_breaker_status())
        assert handler.get_circuit_breaker("test_cb") is cb

    @pytest.mark.unit
    @pytest.mark.p0
    def test_global_registry_write_visible_to_handler(self):
        """全局注册表写入对 error_handler 读取可见（单一事实源双向同步）"""
        handler = self._fresh_handler()
        from agent.circuit_breaker import register_circuit_breaker as reg_global
        breaker = reg_global("global_cb")
        assert handler.get_circuit_breaker("global_cb") is breaker

    @pytest.mark.unit
    @pytest.mark.p0
    def test_migrate_breakers_shared_returns_zero(self):
        """委托后（共享 dict）migrate_breakers 返回 0"""
        handler = self._fresh_handler()
        assert handler.migrate_breakers() == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_migrate_breakers_moves_open_breakers(self):
        """历史本地注册表（非共享）中的 OPEN 熔断器迁移进统一注册表"""
        handler = self._fresh_handler()
        # 模拟旧版实例：独立本地注册表
        local: dict = {}
        handler._circuit_breakers = local
        cb_open = CircuitBreaker(name="legacy_open")
        cb_closed = CircuitBreaker(name="legacy_closed")
        for _ in range(100):
            cb_open.record_failure()
        assert cb_open.state == CircuitState.OPEN
        local["legacy_open"] = cb_open
        local["legacy_closed"] = cb_closed

        migrated = handler.migrate_breakers()
        assert migrated == 1
        assert get_breaker_registry()["legacy_open"] is cb_open
        assert "legacy_closed" not in get_breaker_registry()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_module_level_migrate_breakers(self):
        """模块级 migrate_breakers 便捷函数可用"""
        from agent.error_handler import migrate_breakers
        assert isinstance(migrate_breakers(), int)


# ═══════════════════════════════════════════════════════════════
#  [D11] 恢复路径统一走公开 API
# ═══════════════════════════════════════════════════════════════

class TestRecoveryPublicAPI:
    """force_half_open 状态转换正确（公开恢复 API，经锁保护）"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_module_level_force_half_open(self):
        """force_half_open(name) 将命名熔断器置为 HALF_OPEN"""
        reset_breakers()
        breaker = force_half_open("test_cb")
        assert breaker.state == CircuitState.HALF_OPEN

    @pytest.mark.unit
    @pytest.mark.p0
    def test_class_force_half_open(self):
        """CircuitBreaker.force_half_open() 状态转换正确"""
        cb = CircuitBreaker(name="test_cb")
        cb.force_open()
        assert cb.state == CircuitState.OPEN
        cb.force_half_open()
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.unit
    @pytest.mark.p0
    def test_error_handler_breaker_force_apis(self):
        """error_handler.CircuitBreaker 补齐 force_* 公开 API（自愈恢复路径依赖）"""
        from agent.error_handler import (
            CircuitBreaker as EHBreaker,
            CircuitState as EHState,
        )
        cb = EHBreaker(name="test_cb")
        cb.force_open()
        assert cb.state == EHState.OPEN
        cb.force_half_open()
        assert cb.state == EHState.HALF_OPEN
        cb.force_close()
        assert cb.state == EHState.CLOSED


# ═══════════════════════════════════════════════════════════════
#  [M6] 降级收益可量化
# ═══════════════════════════════════════════════════════════════

class TestDegradeEconomics:
    """降级埋点含收益字段：degraded_fallbacks_used / degraded_calls_avoided"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_metrics_include_economics_fields(self):
        """验收 #5：get_metrics() 输出含 degraded_fallbacks_used 与 avoided_calls"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))

        def always_fail():
            raise ValueError("fail")

        degrade.with_degrade(module=DegradeModule.SCHEMA, func=always_fail)
        metrics = degrade.get_metrics()
        assert metrics.degraded_fallbacks_used >= 1
        assert hasattr(metrics, "degraded_calls_avoided")
        # degrade_history 条目含收益字段
        assert metrics.degrade_history
        entry = metrics.degrade_history[-1]
        assert "avoided_calls" in entry
        assert "saved_latency_ms" in entry
        assert "fallback_type" in entry

    @pytest.mark.unit
    @pytest.mark.p0
    def test_status_metrics_include_economics(self):
        """get_status() 的 metrics 子项含降级收益统计"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))

        def always_fail():
            raise ValueError("fail")

        degrade.with_degrade(module=DegradeModule.SCHEMA, func=always_fail)
        status = degrade.get_status()
        assert "degraded_fallbacks_used" in status["metrics"]
        assert "degraded_calls_avoided" in status["metrics"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_module_specific_degrade_records_economics(self):
        """模块专用降级方法触发埋点携带 fallback_type 与挽回调用数"""
        degrade = GracefulDegrade()
        degrade.memory_query_with_degrade("q")
        metrics = degrade.get_metrics()
        assert metrics.degraded_fallbacks_used >= 1
        assert metrics.degraded_calls_avoided >= 1
        assert metrics.degrade_history[-1]["fallback_type"] == "default"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_exhausted_counts_zero_avoided(self):
        """重试耗尽场景：调用已实际发生，避免数应为 0（不虚报收益）"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))

        def always_fail():
            raise ValueError("fail")

        degrade.with_degrade(module=DegradeModule.SCHEMA, func=always_fail)
        entry = degrade.get_metrics().degrade_history[-1]
        assert entry["avoided_calls"] == 0
