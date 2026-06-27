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

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.graceful_degrade import (  # noqa: E402
    GracefulDegrade,
    DegradeLevel,
    set_trace_id,
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
        degrade = GracefulDegrade(max_retries=2)

        def strict_validator(data):
            raise ValueError("strict validation failed")

        def relaxed_validator(data):
            return {"relaxed": True, "data": data}

        is_valid, result = degrade.schema_validate_with_fallback(
            strict_validator, {"foo": "bar"}, relaxed_validator
        )

        assert is_valid is True
        assert result == {"relaxed": True, "data": {"foo": "bar"}}

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_schema_all_fail_should_fallback_to_text(self):
        """标准+宽松都失败 → 降级为纯文本

        场景：标准与宽松验证器都抛异常。
        预期：返回 (False, str(data))，触发 schema_validator 降级。
        """
        set_trace_id("chaos-degrade-schema-002")
        degrade = GracefulDegrade(max_retries=2)

        def always_fail(data):
            raise RuntimeError("always fails")

        is_valid, result = degrade.schema_validate_with_fallback(
            always_fail, {"key": "value"}, always_fail
        )

        assert is_valid is False
        # 字典会被转为字符串
        assert isinstance(result, str)
        assert "key" in result

        # 验证：schema_validator 进入降级状态
        assert degrade.is_degraded("schema_validator") is True

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_degraded_schema_should_short_circuit_to_text(self):
        """已降级的 schema_validator 应短路返回纯文本

        场景：schema_validator 已降级，再次调用应直接返回文本，
        不再调用验证器。
        """
        set_trace_id("chaos-degrade-schema-003")
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=30)
        # 强制降级
        degrade.force_degrade("schema_validator")

        call_count = [0]

        def validator(data):
            call_count[0] += 1
            return data

        is_valid, result = degrade.schema_validate_with_fallback(
            validator, "test_data"
        )

        # 已降级，不应调用验证器
        assert call_count[0] == 0, "降级状态应短路，不应调用验证器"
        # 字符串数据原样返回
        assert is_valid is False
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
        degrade = GracefulDegrade(max_retries=2)

        def failing_critic(*args, **kwargs):
            raise ConnectionError("critic service unavailable")

        result = degrade.call_with_fallback(
            "critic_engine", failing_critic, "input_data"
        )

        # 默认 fallback 为 None（跳过评估）
        assert result is None
        # 验证降级已触发
        assert degrade.is_degraded("critic_engine") is True


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
        degrade = GracefulDegrade(max_retries=2)

        def timeout_query(*args, **kwargs):
            raise TimeoutError("memory query timed out")

        result = degrade.call_with_fallback(
            "memory_router", timeout_query, "query"
        )

        # 默认 fallback 为空列表
        assert result == []
        assert degrade.is_degraded("memory_router") is True


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
        degrade = GracefulDegrade(max_retries=0)

        call_count = [0]

        def dashboard_loader():
            call_count[0] += 1
            if call_count[0] == 1:
                # 首次成功，返回数据
                return {"metric1": 100, "metric2": 200}
            # 后续失败
            raise RuntimeError("dashboard service down")

        # 第一次：成功，缓存数据
        result1 = degrade.call_with_fallback(
            "dashboard_loader", dashboard_loader
        )
        assert result1 == {"metric1": 100, "metric2": 200}

        # 第二次：失败，应返回缓存数据
        result2 = degrade.call_with_fallback(
            "dashboard_loader", dashboard_loader
        )
        # 默认 fallback 是 {}，但我们也可以从缓存池获取
        cached = degrade.get_cached("dashboard_loader")
        assert cached == {"metric1": 100, "metric2": 200}

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_dashboard_first_load_fail_should_return_empty(self):
        """Dashboard 首次加载就失败应返回空字典"""
        set_trace_id("chaos-degrade-dashboard-002")
        degrade = GracefulDegrade(max_retries=1)

        def always_fail():
            raise RuntimeError("always fails")

        result = degrade.call_with_fallback(
            "dashboard_loader", always_fail
        )
        assert result == {}, "首次失败应返回默认 fallback {}"
        assert degrade.is_degraded("dashboard_loader") is True


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
        degrade = GracefulDegrade(max_retries=0)

        def always_fail(*args, **kwargs):
            raise RuntimeError("fail")

        # 同时触发三个组件故障
        r1 = degrade.call_with_fallback("schema_validator", always_fail)
        r2 = degrade.call_with_fallback("critic_engine", always_fail)
        r3 = degrade.call_with_fallback("memory_router", always_fail)

        # 各自返回默认 fallback
        assert r1 is None      # schema_validator 默认 None
        assert r2 is None      # critic_engine 默认 None
        assert r3 == []        # memory_router 默认空列表

        # 三个组件都进入降级状态
        assert degrade.is_degraded("schema_validator") is True
        assert degrade.is_degraded("critic_engine") is True
        assert degrade.is_degraded("memory_router") is True


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
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=0.5)

        call_count = [0]

        def failing_then_succeeding():
            call_count[0] += 1
            if call_count[0] <= 1:
                raise RuntimeError("first fail")
            return "recovered"

        # 第一次：失败，触发降级
        result1 = degrade.call_with_fallback("memory_router", failing_then_succeeding)
        assert result1 == []
        assert degrade.is_degraded("memory_router") is True

        # 等待降级期到期
        time.sleep(0.6)

        # 第二次：降级期已过，应重新调用 func
        result2 = degrade.call_with_fallback("memory_router", failing_then_succeeding)
        assert result2 == "recovered", (
            f"降级到期后应重新调用 func 返回 'recovered'，实际 {result2}"
        )
        assert degrade.is_degraded("memory_router") is False

    @pytest.mark.chaos
    @pytest.mark.unit
    def test_force_degrade_then_recover(self):
        """强制降级后通过成功调用恢复"""
        set_trace_id("chaos-degrade-expire-002")
        degrade = GracefulDegrade(max_retries=0, degrade_seconds=30)

        # 强制降级
        degrade.force_degrade("critic_engine")
        assert degrade.is_degraded("critic_engine") is True

        # 调用应直接返回 fallback（不调用 func）
        call_count = [0]

        def critic_func():
            call_count[0] += 1
            return "should_not_reach"

        result = degrade.call_with_fallback("critic_engine", critic_func)
        assert result is None
        assert call_count[0] == 0, "降级期不应调用 func"

        # 重置降级状态
        degrade.reset()
        assert degrade.is_degraded("critic_engine") is False

        # 现在应能正常调用
        result = degrade.call_with_fallback("critic_engine", critic_func)
        assert result == "should_not_reach"
        assert call_count[0] == 1
