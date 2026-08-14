"""任务 3 契约测试：error_handler 熔断与 circuit_breaker 全局注册表同源

【契约不变量】
1. error_handler._circuit_breakers 与 agent.circuit_breaker._breakers 是同一
   dict 对象（单一事实源）——任何一方的写入/状态变更对另一方即时可见。
2. get_circuit_breaker_status() 与 get_all_circuit_breaker_status() 返回一致。
3. error_handler 触发熔断后，get_all_circuit_breaker_status() 能查到同名
   OPEN 状态（验收清单 #1）。
4. 熔断器状态恢复只允许通过公开 API（force_close/force_half_open/
   register_circuit_breaker 等），不依赖私有字段直改。
5. GracefulDegrade 降级路径不伪造成功：degraded=True 且不计 success。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    get_all_circuit_breaker_status,
    get_breaker_registry,
    register_circuit_breaker,
    reset_breakers,
)
from agent.error_handler import ErrorHandler, get_error_handler
from agent.graceful_degrade import GracefulDegrade, DegradeModule


@pytest.fixture(autouse=True)
def _clean_breakers():
    """每个契约用例前清理全局注册表，保证用例独立"""
    reset_breakers()
    yield
    reset_breakers()


class TestBreakerSingleSourceContract:
    """契约：单一熔断事实源"""

    @pytest.mark.p0
    def test_shared_registry_identity(self):
        """error_handler 与 circuit_breaker 共享同一注册表对象"""
        handler = ErrorHandler()
        assert handler._circuit_breakers is get_breaker_registry()
        assert handler._circuit_breakers is get_error_handler()._circuit_breakers

    @pytest.mark.p0
    def test_handler_open_visible_in_global_status(self):
        """验收 #1：error_handler 侧触发熔断后全局状态查到同名 OPEN"""
        handler = ErrorHandler()
        cb = CircuitBreaker(name="contract_cb")
        handler.register_circuit_breaker("contract_cb", cb)
        for _ in range(100):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

        status = get_all_circuit_breaker_status()
        assert "contract_cb" in status
        assert status["contract_cb"]["state"] == "open"

    @pytest.mark.p0
    def test_global_write_visible_to_handler(self):
        """全局注册表注册对 error_handler 查询可见（双向同步）"""
        handler = ErrorHandler()
        breaker = register_circuit_breaker("contract_global")
        assert handler.get_circuit_breaker("contract_global") is breaker

    @pytest.mark.p0
    def test_status_queries_identical(self):
        """两套查询接口输出一致（同源保证）

        【修复·2026-08-14 CI Pact 契约测试失败】
        两套查询均实时计算 time_since_last_state_change（now - last_state_change），
        两次调用间存在 µs 级差异（2.5e-05 vs 1.5e-05），dict 全等断言必然失败。
        契约不变量是「同源一致」（同一注册表、同一字段集合、非时序字段全等），
        时序字段本就逐次不同，不属契约对比范围，比较前剔除。
        """
        handler = ErrorHandler()
        register_circuit_breaker("contract_a")
        register_circuit_breaker("contract_b")

        status_a = handler.get_circuit_breaker_status()
        status_b = get_all_circuit_breaker_status()

        def _strip_timing(d: dict) -> dict:
            return {
                name: {k: v for k, v in st.items()
                       if k != "time_since_last_state_change"}
                for name, st in d.items()
            }

        assert set(status_a) == set(status_b)
        assert _strip_timing(status_a) == _strip_timing(status_b)

    @pytest.mark.p0
    def test_recovery_uses_public_api_only(self):
        """恢复路径走公开 API：force_half_open 后状态为 half_open"""
        breaker = register_circuit_breaker("contract_recovery")
        for _ in range(100):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        breaker.force_half_open()
        status = get_all_circuit_breaker_status()["contract_recovery"]
        assert status["state"] == "half_open"


class TestDegradeHonestyContract:
    """契约：降级不伪造成功"""

    @pytest.mark.p0
    def test_critic_degrade_honest(self):
        """critic 降级结果 overall_score is None 且 degraded=True"""
        degrade = GracefulDegrade()
        result = degrade.critic_evaluate_with_degrade("input")
        assert result["degraded"] is True
        assert result["overall_score"] is None

    @pytest.mark.p0
    def test_degrade_not_counted_as_success(self):
        """降级路径不计入 success_count（降级不是成功）"""
        degrade = GracefulDegrade()
        before = degrade._get_module_state(DegradeModule.CRITIC)["success_count"]
        degrade.critic_evaluate_with_degrade("input")
        after = degrade._get_module_state(DegradeModule.CRITIC)["success_count"]
        assert after == before

    @pytest.mark.p0
    def test_schema_degrade_no_fake_valid(self):
        """schema 无注入验证器时不伪造 valid=True"""
        degrade = GracefulDegrade()
        result = degrade.schema_validate_with_degrade({"key": "value"}, {})
        assert result["valid"] is False
        assert result["degraded"] is True
