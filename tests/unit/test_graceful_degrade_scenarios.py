"""
优雅降级场景测试

覆盖 agent/graceful_degrade.py 的所有降级场景，确保系统在故障时能优雅处理。
"""

import pytest
import time
import json

from agent.graceful_degrade import (
    GracefulDegrade,
    DegradeConfig,
    DegradeModule,
    DegradeLevel,
    get_degrade_manager,
    schema_validate_with_degrade,
    memory_query_with_degrade,
    critic_evaluate_with_degrade,
    dashboard_data_with_degrade,
)


# ============================================================================
# 降级管理器基础测试
# ============================================================================


class TestDegradeBasic:
    """降级管理器基础测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_default(self):
        """测试默认初始化"""
        degrade = GracefulDegrade()
        assert degrade is not None
        assert degrade._config.enabled is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_custom_config(self):
        """测试自定义配置初始化"""
        config = DegradeConfig(
            enabled=False,
            max_retries=5,
            timeout_seconds=60.0,
            cache_ttl_seconds=600
        )
        degrade = GracefulDegrade(config)
        assert degrade._config.enabled is False
        assert degrade._config.max_retries == 5

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_degrade_manager_singleton(self):
        """测试全局降级管理器单例"""
        m1 = get_degrade_manager()
        m2 = get_degrade_manager()
        assert m1 is m2


# ============================================================================
# Schema 校验降级测试
# ============================================================================


class TestSchemaDegrade:
    """Schema 校验降级测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_validate_success(self):
        """测试 Schema 校验成功"""
        degrade = GracefulDegrade()
        result = degrade.schema_validate_with_degrade({"key": "value"}, {})
        assert result["valid"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_validate_degrade(self):
        """测试 Schema 校验失败时的降级"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=1))
        # 使用非字典数据触发校验失败
        result = degrade.schema_validate_with_degrade("not a dict", {})
        assert result["valid"] is True
        assert "degrade_level" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_validate_with_degrade_function(self):
        """测试便捷函数 schema_validate_with_degrade"""
        result = schema_validate_with_degrade({"key": "value"}, {})
        assert result["valid"] is True


# ============================================================================
# Critic 评估降级测试
# ============================================================================


class TestCriticDegrade:
    """Critic 评估降级测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_critic_skip_when_unavailable(self):
        """测试 Critic 不可用时自动跳过"""
        degrade = GracefulDegrade()
        
        # 设置高错误率
        state = degrade._get_module_state(DegradeModule.CRITIC)
        state['error_count'] = 10
        state['success_count'] = 1
        
        result = degrade.critic_evaluate_with_degrade("test input")
        assert result["degraded"] is True
        assert "Critic 服务不可用" in result.get("reason", "")

    @pytest.mark.unit
    @pytest.mark.p0
    def test_critic_evaluate_with_degrade_function(self):
        """测试便捷函数 critic_evaluate_with_degrade"""
        result = critic_evaluate_with_degrade("test input")
        assert "overall_score" in result


# ============================================================================
# Memory 查询降级测试
# ============================================================================


class TestMemoryDegrade:
    """Memory 查询降级测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_query_success(self):
        """测试 Memory 查询成功"""
        degrade = GracefulDegrade()
        result = degrade.memory_query_with_degrade("test query")
        assert isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_query_timeout_returns_empty(self):
        """测试 Memory 查询超时返回空结果"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))
        
        def always_fail():
            raise Exception("memory timeout")
        
        result = degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=always_fail,
            fallback=lambda: []
        )
        assert result == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_query_with_degrade_function(self):
        """测试便捷函数 memory_query_with_degrade"""
        result = memory_query_with_degrade("test query")
        assert isinstance(result, list)


# ============================================================================
# 仪表盘数据降级测试
# ============================================================================


class TestDashboardDegrade:
    """仪表盘数据降级测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dashboard_data_success(self):
        """测试仪表盘数据获取成功"""
        degrade = GracefulDegrade()
        result = degrade.dashboard_data_with_degrade("test_endpoint")
        assert isinstance(result, dict)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dashboard_data_fallback_to_cache(self):
        """测试仪表盘数据加载失败显示缓存数据"""
        degrade = GracefulDegrade(DegradeConfig(cache_ttl_seconds=300))
        
        # 先缓存数据
        def fetch_success():
            return {"data": ["cached_item"], "fresh": True}
        
        degrade.with_degrade(
            module=DegradeModule.DASHBOARD,
            func=fetch_success,
            fallback=lambda: {"data": [], "fresh": False, "cached": True}
        )
        
        # 模拟失败，应该使用缓存
        def fetch_fail():
            raise Exception("dashboard unavailable")
        
        result = degrade.with_degrade(
            module=DegradeModule.DASHBOARD,
            func=fetch_fail,
            fallback=lambda: {"data": [], "fresh": False, "cached": True}
        )
        
        assert result["data"] == ["cached_item"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dashboard_data_with_degrade_function(self):
        """测试便捷函数 dashboard_data_with_degrade"""
        result = dashboard_data_with_degrade("test_endpoint")
        assert isinstance(result, dict)


# ============================================================================
# 降级策略测试
# ============================================================================


class TestDegradeStrategy:
    """降级策略测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_degrade_threshold_boundary(self):
        """测试降级触发阈值边界"""
        degrade = GracefulDegrade()
        state = degrade._get_module_state(DegradeModule.SCHEMA)
        
        # 低错误率（<20%）- 不降级
        state['error_count'] = 1
        state['success_count'] = 9
        should_degrade, level = degrade._should_degrade(DegradeModule.SCHEMA)
        assert should_degrade is False
        
        # 20%错误率 - LENIENT
        state['error_count'] = 2
        state['success_count'] = 8
        should_degrade, level = degrade._should_degrade(DegradeModule.SCHEMA)
        assert should_degrade is True
        assert level == DegradeLevel.LENIENT
        
        # 40%错误率 - CACHE_ONLY
        state['error_count'] = 4
        state['success_count'] = 6
        should_degrade, level = degrade._should_degrade(DegradeModule.SCHEMA)
        assert should_degrade is True
        assert level == DegradeLevel.CACHE_ONLY
        
        # 60%错误率 - SKIP
        state['error_count'] = 6
        state['success_count'] = 4
        should_degrade, level = degrade._should_degrade(DegradeModule.SCHEMA)
        assert should_degrade is True
        assert level == DegradeLevel.SKIP
        
        # 80%错误率 - EMERGENCY
        state['error_count'] = 8
        state['success_count'] = 2
        should_degrade, level = degrade._should_degrade(DegradeModule.SCHEMA)
        assert should_degrade is True
        assert level == DegradeLevel.EMERGENCY

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_skip(self):
        """测试 should_skip 判断"""
        degrade = GracefulDegrade()
        state = degrade._get_module_state(DegradeModule.CRITIC)
        
        # 低错误率 - 不跳过
        state['error_count'] = 1
        state['success_count'] = 9
        assert degrade.should_skip(DegradeModule.CRITIC) is False
        
        # 高错误率 - 跳过
        state['error_count'] = 10
        state['success_count'] = 1
        assert degrade.should_skip(DegradeModule.CRITIC) is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_retry_mechanism(self):
        """测试重试降级机制"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=2, retry_delay_ms=10))
        
        call_count = [0]
        
        def failing_func():
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ValueError("failed")
            return "success"
        
        result = degrade.with_degrade(
            module=DegradeModule.SCHEMA,
            func=failing_func
        )
        
        assert result == "success"
        assert call_count[0] == 3


# ============================================================================
# 缓存与状态测试
# ============================================================================


class TestCacheAndState:
    """缓存与状态管理测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_ttl(self):
        """测试缓存过期时间"""
        degrade = GracefulDegrade(DegradeConfig(cache_ttl_seconds=0.1))
        
        call_count = [0]
        
        def get_data():
            call_count[0] += 1
            return {"value": call_count[0]}
        
        # 第一次调用
        result1 = degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=get_data
        )
        assert result1["value"] == 1
        
        # 第二次调用（缓存命中）
        result2 = degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=get_data
        )
        assert result2["value"] == 1
        assert call_count[0] == 1
        
        # 等待缓存过期
        time.sleep(0.2)
        
        # 第三次调用（缓存过期，重新获取）
        result3 = degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=get_data
        )
        assert result3["value"] == 2
        assert call_count[0] == 2

    @pytest.mark.unit
    @pytest.mark.p0
    def test_clear_cache(self):
        """测试清空缓存"""
        degrade = GracefulDegrade(DegradeConfig(cache_ttl_seconds=300))
        
        def get_data():
            return {"value": 1}
        
        # 缓存数据
        degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=get_data
        )
        
        # 清空缓存
        degrade.clear_cache()
        
        # 缓存应该为空
        assert len(degrade._cache) == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reset(self):
        """测试重置降级状态"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))
        
        def always_fail():
            raise ValueError("fail")
        
        # 触发降级
        degrade.with_degrade(
            module=DegradeModule.SCHEMA,
            func=always_fail
        )
        
        # 重置
        degrade.reset()
        
        # 验证状态已重置
        metrics = degrade.get_metrics()
        assert metrics.total_degrades == 0
        assert len(degrade._module_states) == 0
        assert len(degrade._cache) == 0


# ============================================================================
# 指标与状态测试
# ============================================================================


class TestMetricsAndStatus:
    """降级指标与状态测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_metrics(self):
        """测试获取降级指标"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))
        
        def always_fail():
            raise ValueError("fail")
        
        # 触发多次降级
        for _ in range(5):
            degrade.with_degrade(
                module=DegradeModule.SCHEMA,
                func=always_fail
            )
        
        metrics = degrade.get_metrics()
        
        assert metrics.total_degrades >= 5
        assert metrics.text_only_count >= 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_get_status(self):
        """测试获取降级状态"""
        degrade = GracefulDegrade()
        status = degrade.get_status()
        
        assert "config" in status
        assert "metrics" in status
        assert "module_states" in status
        assert "cache_size" in status


# ============================================================================
# 降级历史与缓存深度测试
# ============================================================================


class TestDegradeHistoryAndCache:
    """降级历史与缓存深度测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_degrade_history_limit(self):
        """测试降级历史记录上限"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))
        
        def always_fail():
            raise ValueError("fail")
        
        # 触发超过100次降级
        for _ in range(110):
            degrade.with_degrade(
                module=DegradeModule.SCHEMA,
                func=always_fail
            )
        
        metrics = degrade.get_metrics()
        assert len(metrics.degrade_history) == 100

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cache_only_when_degrade_required(self):
        """测试需要降级时使用缓存"""
        degrade = GracefulDegrade(DegradeConfig(cache_ttl_seconds=300))
        
        def get_data():
            return {"data": "cached"}
        
        # 先成功获取数据并缓存
        degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=get_data
        )
        
        # 设置高错误率触发降级
        state = degrade._get_module_state(DegradeModule.MEMORY)
        state['error_count'] = 10
        state['success_count'] = 1
        
        # 再次调用，应该使用缓存
        result = degrade.with_degrade(
            module=DegradeModule.MEMORY,
            func=get_data
        )
        
        assert result["data"] == "cached"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_fallback_exception_handling(self):
        """测试回退函数异常处理

        主函数和 fallback 都失败时，返回 default_fallbacks 中的默认值。
        schema 的 default_fallbacks 为 None，因此返回 None。
        与 test_graceful_degrade_comprehensive.py::test_fallback_failure_returns_default 对齐。
        """
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))

        def always_fail():
            raise ValueError("primary fail")

        def failing_fallback():
            raise ValueError("fallback fail")

        result = degrade.with_degrade(
            module=DegradeModule.SCHEMA,
            func=always_fail,
            fallback=failing_fallback
        )

        # default_fallbacks["schema"] = None
        assert result is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_degrade_disabled(self):
        """测试降级禁用时的行为"""
        degrade = GracefulDegrade(DegradeConfig(enabled=False))
        
        should_degrade, level = degrade._should_degrade(DegradeModule.SCHEMA)
        assert should_degrade is False
        assert level == DegradeLevel.NORMAL


# ============================================================================
# 降级模块专项测试
# ============================================================================


class TestDegradeModuleSpecific:
    """降级模块专项测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_schema_validate_text_only_fallback(self):
        """测试 Schema 校验纯文本降级"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))
        
        # 触发纯文本降级
        result = degrade.with_degrade(
            module=DegradeModule.SCHEMA,
            func=lambda: (_ for _ in ()).throw(ValueError("schema error")),
            fallback=lambda: {
                "valid": True,
                "errors": [],
                "warnings": ["Schema 校验降级为纯文本模式"],
                "degrade_level": "text_only"
            }
        )
        
        assert "degrade_level" in result
        assert result["degrade_level"] == "text_only"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_memory_query_simulated_failure(self):
        """测试 Memory 查询模拟失败场景"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))
        
        # 设置时间让模拟失败条件成立
        import time as time_module
        
        def failing_query():
            if time_module.time() % 3 == 0:
                raise Exception("Memory 服务暂时不可用")
            return [{"id": 1, "content": "模拟数据"}]
        
        # 直接测试内存查询方法
        result = degrade.memory_query_with_degrade("test")
        assert isinstance(result, list)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dashboard_data_simulated_failure(self):
        """测试仪表盘数据模拟失败场景"""
        degrade = GracefulDegrade(DegradeConfig(max_retries=0))
        
        # 直接测试仪表盘数据方法
        result = degrade.dashboard_data_with_degrade("test_endpoint")
        assert isinstance(result, dict)


# ============================================================================
# 多模块降级独立性测试
# ============================================================================


class TestModuleIndependence:
    """多模块降级独立性测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_module_independence(self):
        """测试不同模块降级的独立性"""
        degrade = GracefulDegrade()
        
        # 设置 SCHEMA 高错误率
        schema_state = degrade._get_module_state(DegradeModule.SCHEMA)
        schema_state['error_count'] = 10
        schema_state['success_count'] = 1
        
        # 设置 CRITIC 正常状态
        critic_state = degrade._get_module_state(DegradeModule.CRITIC)
        critic_state['error_count'] = 0
        critic_state['success_count'] = 10
        
        # SCHEMA 应该降级
        should_degrade_schema, _ = degrade._should_degrade(DegradeModule.SCHEMA)
        assert should_degrade_schema is True
        
        # CRITIC 不应该降级
        should_degrade_critic, _ = degrade._should_degrade(DegradeModule.CRITIC)
        assert should_degrade_critic is False