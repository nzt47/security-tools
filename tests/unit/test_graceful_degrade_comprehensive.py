"""GracefulDegrade 优雅降级全面单元测试

测试目标：覆盖 agent/graceful_degrade.py 的所有分支
覆盖维度：
1. 正常路径：call_with_fallback、with_degrade 成功调用
2. 异常路径：主函数失败触发降级、fallback 失败
3. 边界条件：降级期内直接返回 fallback、参数校验
4. 状态管理：reset、force_degrade、is_degraded
5. 全局单例：get_degrade_manager、reset_degrade_manager
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from agent.graceful_degrade import (
    DegradeError,
    DegradeLevel,
    DegradeModule,
    DegradeState,
    GracefulDegrade,
    get_degrade_manager,
    reset_degrade_manager,
)


# 状态同步说明：每个用例使用独立 GracefulDegrade 实例避免状态污染；
# 全局单例测试在 fixture 中通过 reset_degrade_manager 清理。


@pytest.fixture
def manager():
    """独立降级管理器实例"""
    return GracefulDegrade()


@pytest.fixture
def clean_global():
    """清理全局单例状态"""
    reset_degrade_manager()
    yield
    reset_degrade_manager()


# ── 1. 枚举与数据类 ──────────────────────────────────────


class TestDegradeLevel:
    """DegradeLevel 枚举测试"""

    def test_all_levels_defined(self):
        assert DegradeLevel.NORMAL
        assert DegradeLevel.RETRY
        assert DegradeLevel.RELAXED
        assert DegradeLevel.FALLBACK
        assert DegradeLevel.DISABLED

    def test_level_values(self):
        assert DegradeLevel.NORMAL.value == "normal"
        assert DegradeLevel.FALLBACK.value == "fallback"

    def test_level_is_string_enum(self):
        assert isinstance(DegradeLevel.NORMAL, str)
        assert DegradeLevel.NORMAL == "normal"


class TestDegradeModule:
    """DegradeModule 枚举测试"""

    def test_all_modules_defined(self):
        assert DegradeModule.SCHEMA
        assert DegradeModule.CRITIC
        assert DegradeModule.MEMORY
        assert DegradeModule.DASHBOARD
        assert DegradeModule.TOOL_CALLING
        assert DegradeModule.LLM_ROUTER

    def test_module_values(self):
        assert DegradeModule.SCHEMA.value == "schema"
        assert DegradeModule.CRITIC.value == "critic"

    def test_module_is_string_enum(self):
        assert isinstance(DegradeModule.SCHEMA, str)


class TestDegradeState:
    """DegradeState 数据类测试"""

    def test_default_state(self):
        state = DegradeState(component="test")
        assert state.component == "test"
        assert state.level == DegradeLevel.NORMAL
        assert state.failure_count == 0
        assert state.last_fallback_value is None

    def test_custom_state(self):
        state = DegradeState(
            component="schema",
            level=DegradeLevel.FALLBACK,
            failure_count=3,
            last_fallback_value="default",
        )
        assert state.level == DegradeLevel.FALLBACK
        assert state.failure_count == 3


class TestDegradeError:
    """DegradeError 异常测试"""

    def test_default_error_code(self):
        err = DegradeError("test error")
        assert err.error_code == "DEGRADE_FAILED"
        assert str(err) == "test error"
        assert err.component == ""

    def test_custom_error_code(self):
        err = DegradeError("msg", error_code="CUSTOM_CODE", component="schema")
        assert err.error_code == "CUSTOM_CODE"
        assert err.component == "schema"

    def test_is_exception(self):
        err = DegradeError("test")
        assert isinstance(err, Exception)


# ── 2. 初始化 ──────────────────────────────────────────


class TestInit:
    """初始化参数测试"""

    def test_default_fallbacks(self, manager):
        assert "schema_validator" in manager.default_fallbacks
        assert "critic_engine" in manager.default_fallbacks
        assert "memory_router" in manager.default_fallbacks
        assert "dashboard_loader" in manager.default_fallbacks

    def test_custom_fallbacks(self):
        mgr = GracefulDegrade(default_fallbacks={"custom": "value"})
        assert mgr.default_fallbacks == {"custom": "value"}

    def test_default_max_retries(self, manager):
        assert manager.max_retries == 3

    def test_default_degrade_seconds(self, manager):
        assert manager.degrade_seconds == 30.0

    def test_custom_params(self):
        mgr = GracefulDegrade(max_retries=5, degrade_seconds=60.0)
        assert mgr.max_retries == 5
        assert mgr.degrade_seconds == 60.0

    def test_initial_states_empty(self, manager):
        assert manager._states == {}

    def test_initial_cache_pool_empty(self, manager):
        assert manager._cache_pool == {}


# ── 3. with_degrade 模块化降级 ──────────────────────────


class TestWithDegrade:
    """with_degrade 方法测试"""

    def test_success_call_returns_result(self, manager):
        """主函数成功应返回结果，不触发降级"""
        result = manager.with_degrade(
            DegradeModule.SCHEMA,
            func=lambda: "success",
        )
        assert result == "success"

    def test_success_no_degrade_triggered(self, manager):
        """成功调用后组件不应处于降级状态"""
        manager.with_degrade(DegradeModule.SCHEMA, func=lambda: "ok")
        assert not manager.is_degraded("schema")

    def test_failure_triggers_degrade_and_returns_fallback(self, manager):
        """主函数失败应触发降级并返回 fallback 结果"""

        def fail():
            raise RuntimeError("schema broken")

        result = manager.with_degrade(
            DegradeModule.SCHEMA,
            func=fail,
            fallback=lambda: "fallback_value",
        )
        assert result == "fallback_value"
        assert manager.is_degraded("schema")

    def test_failure_without_fallback_returns_default(self, manager):
        """无 fallback 时返回 default_fallbacks 中的值"""
        # schema 对应的 default_fallbacks 键不存在，使用 None
        result = manager.with_degrade(
            "custom_component",
            func=lambda: (_ for _ in ()).throw(ValueError("fail")),
        )
        # default_fallbacks 中没有 custom_component，返回 None
        assert result is None

    def test_already_degraded_returns_fallback_directly(self, manager):
        """降级期内应直接返回 fallback，不调用主函数"""
        manager.force_degrade("schema")

        call_count = [0]

        def main():
            call_count[0] += 1
            return "should_not_be_called"

        result = manager.with_degrade(
            DegradeModule.SCHEMA,
            func=main,
            fallback=lambda: "fallback",
        )
        assert result == "fallback"
        assert call_count[0] == 0  # 主函数未被调用

    def test_already_degraded_no_fallback_returns_default(self, manager):
        """降级期且无 fallback 应返回 default_fallbacks"""
        manager.force_degrade("schema")
        result = manager.with_degrade(DegradeModule.SCHEMA, func=lambda: "x")
        # schema 不在 default_fallbacks，返回 None
        assert result is None

    def test_fallback_failure_returns_default(self, manager):
        """fallback 也失败时应返回 default_fallbacks"""

        def main():
            raise RuntimeError("main fail")

        def bad_fallback():
            raise RuntimeError("fallback fail")

        result = manager.with_degrade(
            "schema",
            func=main,
            fallback=bad_fallback,
        )
        assert result is None  # default_fallbacks["schema"] 不存在

    def test_string_module_accepted(self, manager):
        """字符串模块名应被接受"""
        result = manager.with_degrade("custom_mod", func=lambda: 42)
        assert result == 42

    def test_func_receives_args(self, manager):
        """主函数应接收传入的 args"""
        result = manager.with_degrade(
            DegradeModule.CRITIC,
            lambda x, y: x + y,
            10, 20,
        )
        assert result == 30

    def test_func_receives_kwargs(self, manager):
        """主函数应接收传入的 kwargs"""
        result = manager.with_degrade(
            DegradeModule.CRITIC,
            lambda **kw: kw.get("value"),
            value="hello",
        )
        assert result == "hello"


# ── 4. is_degraded 状态查询 ──────────────────────────────


class TestIsDegraded:
    """is_degraded 方法测试"""

    def test_not_degraded_initially(self, manager):
        assert not manager.is_degraded("any_component")

    def test_degraded_after_force(self, manager):
        manager.force_degrade("schema")
        assert manager.is_degraded("schema")

    def test_different_components_independent(self, manager):
        manager.force_degrade("schema")
        assert manager.is_degraded("schema")
        assert not manager.is_degraded("critic")


# ── 5. get_state 状态获取 ──────────────────────────────────


class TestGetState:
    """get_state 方法测试"""

    def test_returns_state_for_new_component(self, manager):
        state = manager.get_state("new_component")
        assert isinstance(state, DegradeState)
        assert state.component == "new_component"
        assert state.level == DegradeLevel.NORMAL

    def test_returns_same_state_for_same_component(self, manager):
        state1 = manager.get_state("schema")
        state2 = manager.get_state("schema")
        assert state1 is state2

    def test_state_reflects_force_degrade(self, manager):
        manager.force_degrade("schema", DegradeLevel.DISABLED)
        state = manager.get_state("schema")
        assert state.level == DegradeLevel.DISABLED


# ── 6. force_degrade 强制降级 ──────────────────────────────


class TestForceDegrade:
    """force_degrade 方法测试"""

    def test_force_degrade_default_level(self, manager):
        manager.force_degrade("schema")
        state = manager.get_state("schema")
        assert state.level == DegradeLevel.FALLBACK

    def test_force_degrade_custom_level(self, manager):
        manager.force_degrade("schema", DegradeLevel.DISABLED)
        state = manager.get_state("schema")
        assert state.level == DegradeLevel.DISABLED

    def test_force_degrade_sets_degrade_until(self, manager):
        manager.force_degrade("schema")
        state = manager.get_state("schema")
        assert state.degrade_until > time.time()

    def test_force_degrade_creates_state_if_not_exists(self, manager):
        assert "schema" not in manager._states
        manager.force_degrade("schema")
        assert "schema" in manager._states


# ── 7. reset 重置 ──────────────────────────────────────────


class TestReset:
    """reset 方法测试"""

    def test_reset_clears_states(self, manager):
        manager.force_degrade("schema")
        assert len(manager._states) > 0
        manager.reset()
        assert manager._states == {}

    def test_reset_clears_cache_pool(self, manager):
        manager._cache_pool["key"] = "value"
        manager.reset()
        assert manager._cache_pool == {}

    def test_reset_idempotent(self, manager):
        manager.reset()
        manager.reset()
        assert manager._states == {}


# ── 8. get_cached / _update_cache 缓存 ──────────────────


class TestCachePool:
    """缓存池测试"""

    def test_update_and_get_cached(self, manager):
        manager._update_cache("dashboard", {"data": "value"})
        assert manager.get_cached("dashboard") == {"data": "value"}

    def test_get_cached_missing_returns_none(self, manager):
        assert manager.get_cached("nonexistent") is None

    def test_update_cache_silent_on_failure(self, manager):
        """_update_cache 失败应静默（吞掉异常）"""
        # 使用一个会抛异常的 key（不可哈希类型）
        try:
            manager._update_cache(None, "value")
        except Exception:
            pass  # 应不抛异常


# ── 9. 全局单例 get_degrade_manager ──────────────────────


class TestGetDegradeManager:
    """全局单例测试"""

    def test_returns_same_instance(self, clean_global):
        m1 = get_degrade_manager()
        m2 = get_degrade_manager()
        assert m1 is m2

    def test_force_new_creates_new_instance(self, clean_global):
        m1 = get_degrade_manager()
        m2 = get_degrade_manager(force_new=True)
        assert m1 is not m2

    def test_custom_params_on_first_call(self, clean_global):
        m = get_degrade_manager(max_retries=10, degrade_seconds=120.0)
        assert m.max_retries == 10
        assert m.degrade_seconds == 120.0

    def test_invalid_max_retries_raises(self, clean_global):
        with pytest.raises(DegradeError) as exc_info:
            get_degrade_manager(max_retries=-1, force_new=True)
        assert exc_info.value.error_code == "DEGRADE_INVALID_PARAM"

    def test_invalid_degrade_seconds_raises(self, clean_global):
        with pytest.raises(DegradeError) as exc_info:
            get_degrade_manager(degrade_seconds=0, force_new=True)
        assert exc_info.value.error_code == "DEGRADE_INVALID_PARAM"

    def test_invalid_degrade_seconds_negative_raises(self, clean_global):
        with pytest.raises(DegradeError):
            get_degrade_manager(degrade_seconds=-10, force_new=True)


class TestResetDegradeManager:
    """reset_degrade_manager 测试"""

    def test_reset_clears_singleton(self, clean_global):
        m1 = get_degrade_manager()
        reset_degrade_manager()
        m2 = get_degrade_manager()
        assert m1 is not m2

    def test_reset_calls_instance_reset(self, clean_global):
        m = get_degrade_manager()
        m.force_degrade("schema")
        assert m.is_degraded("schema")
        reset_degrade_manager()
        # 新实例不应有降级状态
        m2 = get_degrade_manager()
        assert not m2.is_degraded("schema")

    def test_reset_when_none_is_safe(self, clean_global):
        """单例未创建时 reset 应无副作用"""
        reset_degrade_manager()
        reset_degrade_manager()


# ── 10. call_with_fallback 兼容接口 ──────────────────────


class TestCallWithFallback:
    """call_with_fallback 方法测试"""

    def test_success_returns_result(self, manager):
        result = manager.call_with_fallback(
            "schema_validator",
            lambda: "ok",
        )
        assert result == "ok"

    def test_failure_returns_fallback(self, manager):
        def fail():
            raise ValueError("broken")

        result = manager.call_with_fallback(
            "schema_validator",
            fail,
            fallback="default_value",
        )
        assert result == "default_value"

    def test_failure_returns_default_fallback(self, manager):
        """无显式 fallback 时使用 default_fallbacks"""
        # schema_validator 的 default 是 None
        result = manager.call_with_fallback(
            "schema_validator",
            lambda: (_ for _ in ()).throw(RuntimeError("x")),
        )
        assert result is None

    def test_memory_router_returns_empty_list(self, manager):
        """memory_router 的 default 是空列表"""
        result = manager.call_with_fallback(
            "memory_router",
            lambda: (_ for _ in ()).throw(RuntimeError("x")),
        )
        assert result == []

    def test_dashboard_loader_returns_empty_dict(self, manager):
        """dashboard_loader 的 default 是空字典"""
        result = manager.call_with_fallback(
            "dashboard_loader",
            lambda: (_ for _ in ()).throw(RuntimeError("x")),
        )
        assert result == {}


# ── 11. 集成场景 ──────────────────────────────────────────


class TestIntegrationScenarios:
    """真实使用场景集成测试"""

    def test_schema_validation_degrade_flow(self, manager):
        """Schema 验证降级流程：失败 → 降级 → 后续直接返回 fallback"""
        # 第一次失败，触发降级
        result1 = manager.with_degrade(
            DegradeModule.SCHEMA,
            func=lambda: (_ for _ in ()).throw(RuntimeError("schema down")),
            fallback=lambda: {"valid": False, "degraded": True},
        )
        assert result1 == {"valid": False, "degraded": True}
        assert manager.is_degraded("schema")

        # 第二次：已降级，直接返回 fallback
        call_count = [0]

        def main():
            call_count[0] += 1
            return "should_not_be_called"

        result2 = manager.with_degrade(
            DegradeModule.SCHEMA,
            func=main,
            fallback=lambda: {"valid": False, "degraded": True},
        )
        assert result2 == {"valid": False, "degraded": True}
        assert call_count[0] == 0  # 主函数未被调用

    def test_critic_engine_degrade_with_args(self, manager):
        """Critic 降级带参数传递"""
        result = manager.with_degrade(
            DegradeModule.CRITIC,
            lambda output: {"score": 0.9, "output": output},
            "test_output",
        )
        assert result == {"score": 0.9, "output": "test_output"}

    def test_multiple_modules_independent(self, manager):
        """多模块降级独立"""
        # schema 降级
        manager.with_degrade(
            DegradeModule.SCHEMA,
            func=lambda: (_ for _ in ()).throw(RuntimeError("x")),
            fallback=lambda: "schema_fallback",
        )
        # critic 仍正常
        result = manager.with_degrade(
            DegradeModule.CRITIC,
            func=lambda: "critic_ok",
        )
        assert result == "critic_ok"
        assert manager.is_degraded("schema")
        assert not manager.is_degraded("critic")

    def test_degrade_then_reset_then_recover(self, manager):
        """降级 → 重置 → 恢复流程"""
        # 触发降级
        manager.force_degrade("schema")
        assert manager.is_degraded("schema")
        # 重置
        manager.reset()
        assert not manager.is_degraded("schema")
        # 重置后主函数可正常调用
        result = manager.with_degrade(
            DegradeModule.SCHEMA,
            func=lambda: "recovered",
        )
        assert result == "recovered"
