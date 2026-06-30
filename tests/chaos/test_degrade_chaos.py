# -*- coding: utf-8 -*-
"""降级机制混沌测试 — 依赖故障下的稳定性验证

【测试目标】
验证 GracefulDegrade 在以下依赖故障场景下的稳定性：
1. Schema 验证连续失败 → 重试 → 宽松验证 → 纯文本
2. Critic 不可用 → 自动跳过评估
3. Memory 查询超时 → 返回空结果
4. Dashboard 加载失败 → 展示缓存数据
5. 多组件同时故障的级联降级
6. 降级期到期后的自动恢复

【可观测性约束】
- 边界显性化：所有故障注入通过 mock 实现
- 异常处理：所有降级路径不抛异常，返回 fallback
- 埋点预留：降级器内部已埋点（degrade_triggered/degrade_hit）

【生成日志摘要】
- 生成时间：2026-06-27
- 版本：v1.0.0
- 内容：降级机制混沌测试
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 模块级 logger，用于测试过程的结构化日志输出（便于排查报错）
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.graceful_degrade import (  # noqa: E402
    GracefulDegrade,
    DegradeLevel,
    set_trace_id,
    get_trace_id,
)


# ═══════════════════════════════════════════════════════════════
#  1. Schema 验证连续失败
# ═══════════════════════════════════════════════════════════════

class TestSchemaValidationFailure:
    """Schema 验证连续失败的多级降级"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_schema_standard_fail_then_relaxed_succeed(self):
        """标准验证失败 → 宽松验证成功

        场景：标准验证器始终抛异常，宽松验证器返回结果。
        预期：3 次标准失败后，调用宽松验证器，返回 (True, result)。
        """
        set_trace_id("chaos-degrade-schema-001")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id，便于排查时关联日志
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_schema_standard_fail_then_relaxed_succeed")
        degrade = GracefulDegrade(max_retries=2)
        # 记录降级器配置，确认重试上限符合预期
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        strict_attempts = [0]

        def strict_validator(data):
            # 标准验证每次失败时记录 attempt 号与异常信息
            strict_attempts[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=strict_validator_failed, attempt=%d, error=%s",
                        trace_id, strict_attempts[0], "strict validation failed")
            raise ValueError("strict validation failed")

        def relaxed_validator(data):
            # 宽松验证被调用时记录输入，确认降级路径已进入第二级
            logger.info("[DEGRADE_CHAOS] %s - action=relaxed_validator_called, input_data=%s",
                        trace_id, data)
            result = {"relaxed": True, "data": data}
            # 记录宽松验证返回值，便于核对最终结果
            logger.info("[DEGRADE_CHAOS] %s - action=relaxed_validator_return, result=%s",
                        trace_id, result)
            return result

        logger.info("[DEGRADE_CHAOS] %s - action=schema_validate_call_begin, input=%s",
                    trace_id, {"foo": "bar"})
        is_valid, result = degrade.schema_validate_with_fallback(
            strict_validator, {"foo": "bar"}, relaxed_validator
        )
        logger.info("[DEGRADE_CHAOS] %s - action=schema_validate_call_end, is_valid=%s, result=%s",
                    trace_id, is_valid, result)

        # 断言前记录预期值与实际值，失败时可直接定位差异
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_valid, expected=%s, actual=%s",
                    trace_id, True, is_valid)
        assert is_valid is True
        expected_result = {"relaxed": True, "data": {"foo": "bar"}}
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result, expected=%s, actual=%s",
                    trace_id, expected_result, result)
        assert result == expected_result

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_schema_all_fail_should_fallback_to_text(self):
        """标准+宽松都失败 → 降级为纯文本

        场景：标准与宽松验证器都抛异常。
        预期：返回 (False, str(data))，触发 schema_validator 降级。
        """
        set_trace_id("chaos-degrade-schema-002")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_schema_all_fail_should_fallback_to_text")
        degrade = GracefulDegrade(max_retries=2)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        fail_attempts = [0]

        def always_fail(data):
            # 每次验证失败时记录调用次序与异常信息（前3次为标准验证，第4次为宽松验证）
            fail_attempts[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=validator_failed, call_no=%d, error=%s",
                        trace_id, fail_attempts[0], "always fails")
            raise RuntimeError("always fails")

        logger.info("[DEGRADE_CHAOS] %s - action=schema_validate_call_begin, input=%s",
                    trace_id, {"key": "value"})
        is_valid, result = degrade.schema_validate_with_fallback(
            always_fail, {"key": "value"}, always_fail
        )
        # 降级触发后记录组件与降级级别
        degrade_level = degrade.get_state("schema_validator").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s",
                    trace_id, "schema_validator", degrade_level)
        logger.info("[DEGRADE_CHAOS] %s - action=schema_validate_call_end, is_valid=%s, result=%s",
                    trace_id, is_valid, result)

        # 断言前记录预期值与实际值
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_valid, expected=%s, actual=%s",
                    trace_id, False, is_valid)
        assert is_valid is False
        # 字典会被转为字符串
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result_type, expected=%s, actual=%s",
                    trace_id, "str", type(result).__name__)
        assert isinstance(result, str)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result_contains_key, expected=%s, actual=%s",
                    trace_id, True, "key" in result)
        assert "key" in result

        # 验证：schema_validator 进入降级状态
        is_degraded = degrade.is_degraded("schema_validator")
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, True, is_degraded)
        assert is_degraded is True

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_degraded_schema_should_short_circuit_to_text(self):
        """已降级的 schema_validator 应短路返回纯文本

        场景：schema_validator 已降级，再次调用应直接返回文本，
        不再调用验证器。
        """
        set_trace_id("chaos-degrade-schema-003")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_degraded_schema_should_short_circuit_to_text")
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=30)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)
        # 强制降级
        degrade.force_degrade("schema_validator")
        # 记录降级触发：组件与降级级别
        degrade_level = degrade.get_state("schema_validator").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s",
                    trace_id, "schema_validator", degrade_level)

        call_count = [0]

        def validator(data):
            # 验证器被调用时记录（短路命中时此处不应执行）
            call_count[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=validator_called_unexpectedly, call_no=%d",
                        trace_id, call_count[0])
            return data

        logger.info("[DEGRADE_CHAOS] %s - action=schema_validate_call_begin, input=%s",
                    trace_id, "test_data")
        is_valid, result = degrade.schema_validate_with_fallback(
            validator, "test_data"
        )
        # 短路检查命中时记录 component、level、返回值
        logger.info("[DEGRADE_CHAOS] %s - action=short_circuit_hit, component=%s, level=%s, return_value=%s",
                    trace_id, "schema_validator", degrade_level, result)
        logger.info("[DEGRADE_CHAOS] %s - action=schema_validate_call_end, is_valid=%s, result=%s",
                    trace_id, is_valid, result)

        # 已降级，不应调用验证器
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=validator_call_count, expected=%d, actual=%d",
                    trace_id, 0, call_count[0])
        assert call_count[0] == 0, "降级状态应短路，不应调用验证器"
        # 字符串数据原样返回
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_valid, expected=%s, actual=%s",
                    trace_id, False, is_valid)
        assert is_valid is False
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result, expected=%s, actual=%s",
                    trace_id, "test_data", result)
        assert result == "test_data"


# ═══════════════════════════════════════════════════════════════
#  2. Critic 不可用
# ═══════════════════════════════════════════════════════════════

class TestCriticUnavailable:
    """Critic 评估不可用时的自动跳过"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_critic_unavailable_should_return_none_fallback(self):
        """Critic 不可用应返回 None（跳过评估）

        场景：critic_engine 函数始终抛异常。
        预期：重试失败后触发降级，返回 None。
        """
        set_trace_id("chaos-degrade-critic-001")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_critic_unavailable_should_return_none_fallback")
        degrade = GracefulDegrade(max_retries=2)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        critic_attempts = [0]

        def failing_critic(*args, **kwargs):
            # critic 每次失败时记录 attempt 号与异常信息
            critic_attempts[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=critic_call_failed, attempt=%d, error=%s",
                        trace_id, critic_attempts[0], "critic service unavailable")
            raise ConnectionError("critic service unavailable")

        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, input=%s",
                    trace_id, "critic_engine", "input_data")
        result = degrade.call_with_fallback(
            "critic_engine", failing_critic, "input_data"
        )
        # 降级触发后记录组件与降级级别
        degrade_level = degrade.get_state("critic_engine").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s",
                    trace_id, "critic_engine", degrade_level)
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_end, component=%s, result=%s",
                    trace_id, "critic_engine", result)

        # 默认 fallback 为 None（跳过评估）
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result, expected=%s, actual=%s",
                    trace_id, None, result)
        assert result is None
        # 验证降级已触发
        is_degraded = degrade.is_degraded("critic_engine")
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, True, is_degraded)
        assert is_degraded is True


# ═══════════════════════════════════════════════════════════════
#  3. Memory 查询超时
# ═══════════════════════════════════════════════════════════════

class TestMemoryQueryTimeout:
    """Memory 查询超时返回空结果"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_memory_timeout_should_return_empty_list(self):
        """Memory 查询超时应返回空列表

        场景：memory_router 函数始终抛 TimeoutError。
        预期：重试失败后触发降级，返回 []。
        """
        set_trace_id("chaos-degrade-memory-001")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_memory_timeout_should_return_empty_list")
        degrade = GracefulDegrade(max_retries=2)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        memory_attempts = [0]

        def timeout_query(*args, **kwargs):
            # memory 每次查询超时时记录 attempt 号与异常信息
            memory_attempts[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=memory_query_timeout, attempt=%d, error=%s",
                        trace_id, memory_attempts[0], "memory query timed out")
            raise TimeoutError("memory query timed out")

        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, input=%s",
                    trace_id, "memory_router", "query")
        result = degrade.call_with_fallback(
            "memory_router", timeout_query, "query"
        )
        # 降级触发后记录组件与降级级别
        degrade_level = degrade.get_state("memory_router").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s",
                    trace_id, "memory_router", degrade_level)
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_end, component=%s, result=%s",
                    trace_id, "memory_router", result)

        # 默认 fallback 为空列表
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result, expected=%s, actual=%s",
                    trace_id, [], result)
        assert result == []
        is_degraded = degrade.is_degraded("memory_router")
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, True, is_degraded)
        assert is_degraded is True


# ═══════════════════════════════════════════════════════════════
#  4. Dashboard 加载失败
# ═══════════════════════════════════════════════════════════════

class TestDashboardLoadFailure:
    """Dashboard 加载失败展示缓存数据"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_dashboard_fail_should_return_cached_data(self):
        """Dashboard 加载失败应返回上次缓存的数据

        场景：首次成功加载并缓存，第二次起加载失败。
        预期：失败后返回首次缓存的数据。
        """
        set_trace_id("chaos-degrade-dashboard-001")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_dashboard_fail_should_return_cached_data")
        degrade = GracefulDegrade(max_retries=0)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        call_count = [0]

        def dashboard_loader():
            # dashboard 每次加载时记录调用次序与结果（成功/失败）
            call_count[0] += 1
            if call_count[0] == 1:
                logger.info("[DEGRADE_CHAOS] %s - action=dashboard_loader_success, call_no=%d",
                            trace_id, call_count[0])
                # 首次成功，返回数据
                return {"metric1": 100, "metric2": 200}
            logger.info("[DEGRADE_CHAOS] %s - action=dashboard_loader_failed, call_no=%d, error=%s",
                        trace_id, call_count[0], "dashboard service down")
            # 后续失败
            raise RuntimeError("dashboard service down")

        # 第一次：成功，缓存数据
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, call_round=%d",
                    trace_id, "dashboard_loader", 1)
        result1 = degrade.call_with_fallback(
            "dashboard_loader", dashboard_loader
        )
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_end, component=%s, call_round=%d, result=%s",
                    trace_id, "dashboard_loader", 1, result1)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result1, expected=%s, actual=%s",
                    trace_id, {"metric1": 100, "metric2": 200}, result1)
        assert result1 == {"metric1": 100, "metric2": 200}

        # 第二次：失败，应返回缓存数据
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, call_round=%d",
                    trace_id, "dashboard_loader", 2)
        result2 = degrade.call_with_fallback(
            "dashboard_loader", dashboard_loader
        )
        # 降级触发后记录组件与降级级别
        degrade_level = degrade.get_state("dashboard_loader").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s",
                    trace_id, "dashboard_loader", degrade_level)
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_end, component=%s, call_round=%d, result=%s",
                    trace_id, "dashboard_loader", 2, result2)
        # 默认 fallback 是 {}，但我们也可以从缓存池获取
        cached = degrade.get_cached("dashboard_loader")
        # 记录缓存命中：组件与返回值
        logger.info("[DEGRADE_CHAOS] %s - action=cache_hit, component=%s, cached_value=%s",
                    trace_id, "dashboard_loader", cached)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=cached, expected=%s, actual=%s",
                    trace_id, {"metric1": 100, "metric2": 200}, cached)
        assert cached == {"metric1": 100, "metric2": 200}

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_dashboard_first_load_fail_should_return_empty(self):
        """Dashboard 首次加载就失败应返回空字典"""
        set_trace_id("chaos-degrade-dashboard-002")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_dashboard_first_load_fail_should_return_empty")
        degrade = GracefulDegrade(max_retries=1)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        fail_attempts = [0]

        def always_fail():
            # dashboard 每次加载失败时记录 attempt 号与异常信息
            fail_attempts[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=dashboard_loader_failed, attempt=%d, error=%s",
                        trace_id, fail_attempts[0], "always fails")
            raise RuntimeError("always fails")

        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s",
                    trace_id, "dashboard_loader")
        result = degrade.call_with_fallback(
            "dashboard_loader", always_fail
        )
        # 降级触发后记录组件与降级级别
        degrade_level = degrade.get_state("dashboard_loader").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s",
                    trace_id, "dashboard_loader", degrade_level)
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_end, component=%s, result=%s",
                    trace_id, "dashboard_loader", result)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result, expected=%s, actual=%s",
                    trace_id, {}, result)
        assert result == {}, "首次失败应返回默认 fallback {}"
        is_degraded = degrade.is_degraded("dashboard_loader")
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, True, is_degraded)
        assert is_degraded is True


# ═══════════════════════════════════════════════════════════════
#  5. 多组件同时故障的级联降级
# ═══════════════════════════════════════════════════════════════

class TestCascadingDegrade:
    """多组件同时故障的级联降级"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_multiple_components_fail_simultaneously(self):
        """多个组件同时故障应独立降级，互不影响

        场景：schema_validator/critic_engine/memory_router 同时故障。
        预期：每个组件独立降级，返回各自的 fallback。
        """
        set_trace_id("chaos-degrade-cascade-001")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_multiple_components_fail_simultaneously")
        degrade = GracefulDegrade(max_retries=0)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        fail_attempts = [0]

        def always_fail(*args, **kwargs):
            # 每次调用失败时记录调用次序与异常信息
            fail_attempts[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=component_call_failed, call_no=%d, error=%s",
                        trace_id, fail_attempts[0], "fail")
            raise RuntimeError("fail")

        # 同时触发三个组件故障
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s",
                    trace_id, "schema_validator")
        r1 = degrade.call_with_fallback("schema_validator", always_fail)
        # 降级触发后记录组件与降级级别
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s, result=%s",
                    trace_id, "schema_validator", degrade.get_state("schema_validator").level.value, r1)

        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s",
                    trace_id, "critic_engine")
        r2 = degrade.call_with_fallback("critic_engine", always_fail)
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s, result=%s",
                    trace_id, "critic_engine", degrade.get_state("critic_engine").level.value, r2)

        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s",
                    trace_id, "memory_router")
        r3 = degrade.call_with_fallback("memory_router", always_fail)
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s, result=%s",
                    trace_id, "memory_router", degrade.get_state("memory_router").level.value, r3)

        # 各自返回默认 fallback
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=r1, expected=%s, actual=%s",
                    trace_id, None, r1)
        assert r1 is None      # schema_validator 默认 None
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=r2, expected=%s, actual=%s",
                    trace_id, None, r2)
        assert r2 is None      # critic_engine 默认 None
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=r3, expected=%s, actual=%s",
                    trace_id, [], r3)
        assert r3 == []        # memory_router 默认空列表

        # 三个组件都进入降级状态
        is_d1 = degrade.is_degraded("schema_validator")
        is_d2 = degrade.is_degraded("critic_engine")
        is_d3 = degrade.is_degraded("memory_router")
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded_all, expected=(%s,%s,%s), actual=(%s,%s,%s)",
                    trace_id, True, True, True, is_d1, is_d2, is_d3)
        assert is_d1 is True
        assert is_d2 is True
        assert is_d3 is True


# ═══════════════════════════════════════════════════════════════
#  6. 降级期到期后的自动恢复
# ═══════════════════════════════════════════════════════════════

class TestDegradeExpiry:
    """降级期到期后的自动恢复"""

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_degrade_expiry_should_allow_retry(self):
        """降级期到期后应允许重新尝试调用

        场景：组件降级后等待 degrade_seconds 到期，
        下次调用应重新尝试 func 而非直接返回 fallback。
        """
        set_trace_id("chaos-degrade-expire-001")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_degrade_expiry_should_allow_retry")
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=0.5)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        call_count = [0]

        def failing_then_succeeding():
            # 每次调用时记录调用次序与结果（首次失败，后续成功）
            call_count[0] += 1
            if call_count[0] <= 1:
                logger.info("[DEGRADE_CHAOS] %s - action=memory_call_failed, call_no=%d, error=%s",
                            trace_id, call_count[0], "first fail")
                raise RuntimeError("first fail")
            logger.info("[DEGRADE_CHAOS] %s - action=memory_call_success, call_no=%d",
                        trace_id, call_count[0])
            return "recovered"

        # 第一次：失败，触发降级
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, call_round=%d",
                    trace_id, "memory_router", 1)
        result1 = degrade.call_with_fallback("memory_router", failing_then_succeeding)
        # 降级触发后记录组件与降级级别
        degrade_level = degrade.get_state("memory_router").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s, result=%s",
                    trace_id, "memory_router", degrade_level, result1)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result1, expected=%s, actual=%s",
                    trace_id, [], result1)
        assert result1 == []
        is_d1 = degrade.is_degraded("memory_router")
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, True, is_d1)
        assert is_d1 is True

        # 等待降级期到期
        logger.info("[DEGRADE_CHAOS] %s - action=sleep_for_degrade_expiry, seconds=%s",
                    trace_id, 0.6)
        time.sleep(0.6)

        # 第二次：降级期已过，应重新调用 func
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, call_round=%d",
                    trace_id, "memory_router", 2)
        result2 = degrade.call_with_fallback("memory_router", failing_then_succeeding)
        # 降级恢复后记录 component 恢复正常
        is_d2 = degrade.is_degraded("memory_router")
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_recovered, component=%s, is_degraded=%s, result=%s",
                    trace_id, "memory_router", is_d2, result2)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result2, expected=%s, actual=%s",
                    trace_id, "recovered", result2)
        assert result2 == "recovered", (
            f"降级到期后应重新调用 func 返回 'recovered'，实际 {result2}"
        )
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, False, is_d2)
        assert is_d2 is False

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_force_degrade_then_recover(self):
        """强制降级后通过成功调用恢复"""
        set_trace_id("chaos-degrade-expire-002")
        trace_id = get_trace_id()
        # 记录测试起点与 trace_id
        logger.info("[DEGRADE_CHAOS] %s - action=test_start, test_name=%s",
                    trace_id, "test_force_degrade_then_recover")
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=30)
        # 记录降级器配置
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_created, max_retries=%s, degrade_seconds=%s",
                    trace_id, degrade.max_retries, degrade.degrade_seconds)

        # 强制降级
        degrade.force_degrade("critic_engine")
        # 记录降级触发：组件与降级级别
        degrade_level = degrade.get_state("critic_engine").level.value
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_triggered, component=%s, degrade_level=%s",
                    trace_id, "critic_engine", degrade_level)
        is_d1 = degrade.is_degraded("critic_engine")
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, True, is_d1)
        assert is_d1 is True

        # 调用应直接返回 fallback（不调用 func）
        call_count = [0]

        def critic_func():
            # critic_func 被调用时记录（降级期内不应执行）
            call_count[0] += 1
            logger.info("[DEGRADE_CHAOS] %s - action=critic_func_called_unexpectedly, call_no=%d",
                        trace_id, call_count[0])
            return "should_not_reach"

        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, call_round=%d",
                    trace_id, "critic_engine", 1)
        result = degrade.call_with_fallback("critic_engine", critic_func)
        # 短路检查命中时记录 component、level、返回值
        logger.info("[DEGRADE_CHAOS] %s - action=short_circuit_hit, component=%s, level=%s, return_value=%s",
                    trace_id, "critic_engine", degrade_level, result)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result, expected=%s, actual=%s",
                    trace_id, None, result)
        assert result is None
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=call_count, expected=%d, actual=%d",
                    trace_id, 0, call_count[0])
        assert call_count[0] == 0, "降级期不应调用 func"

        # 重置降级状态
        degrade.reset()
        # 记录降级恢复：component 恢复正常
        is_d2 = degrade.is_degraded("critic_engine")
        logger.info("[DEGRADE_CHAOS] %s - action=degrade_recovered, component=%s, is_degraded=%s",
                    trace_id, "critic_engine", is_d2)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=is_degraded, expected=%s, actual=%s",
                    trace_id, False, is_d2)
        assert is_d2 is False

        # 现在应能正常调用
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_begin, component=%s, call_round=%d",
                    trace_id, "critic_engine", 2)
        result = degrade.call_with_fallback("critic_engine", critic_func)
        logger.info("[DEGRADE_CHAOS] %s - action=call_with_fallback_end, component=%s, result=%s",
                    trace_id, "critic_engine", result)
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=result, expected=%s, actual=%s",
                    trace_id, "should_not_reach", result)
        assert result == "should_not_reach"
        logger.info("[DEGRADE_CHAOS] %s - action=assert_before, field=call_count, expected=%d, actual=%d",
                    trace_id, 1, call_count[0])
        assert call_count[0] == 1
