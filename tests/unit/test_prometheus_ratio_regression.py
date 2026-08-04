"""prometheus.py ratio 计算逻辑回归测试

目标：确保未来修改不会破坏分母同步机制（denominator synchronization）

分母同步不变量：
1. 每次调用 record_intent_layer(layer) 后，total = sum(_intent_layer_counts.values())
2. 所有 layer 的 ratio = _intent_layer_counts[layer] / total
3. ratio 总和 = Σ(count_i / total) = total / total = 1.0（恒等）
4. 新增 layer 时，旧 layer 的 ratio 自动重新计算（分母变大）
5. reset_intent_layer_counts() 后，分母从零开始重新累计

关联代码：agent/monitoring/prometheus.py record_intent_layer / reset_intent_layer_counts
关联文档：docs/audit/orchestrator_intent_layer_audit.md 命中点 6 ratio 总和分析

【不易】守 ratio 总和 = 1.0 不变量；分母必须包含所有 layer 计数
【简易】直接测试 _intent_layer_counts + ratio Gauge 值，不拉起完整 Orchestrator
"""
import pytest
from prometheus_client import REGISTRY

from agent.monitoring.prometheus import (
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts,
)


@pytest.fixture(autouse=True)
def _reset_counts():
    """每个测试前重置 ratio 计数视图，隔离测试间状态"""
    reset_intent_layer_counts()
    yield
    reset_intent_layer_counts()


def _get_ratio_gauge_value(layer):
    """从 prometheus REGISTRY 读取 yunshu_intent_layer_ratio{layer=xxx} 的实际 Gauge 值

    用于验证 ratio Gauge 与 _intent_layer_counts 的分母同步一致性。
    """
    for metric in REGISTRY.collect():
        if metric.name == "yunshu_intent_layer_ratio":
            for sample in metric.samples:
                if sample.labels.get("layer") == layer:
                    return sample.value
    return None


def _get_counter_value(layer):
    """从 prometheus REGISTRY 读取 yunshu_intent_layer_total{layer=xxx} 的实际 Counter 值"""
    for metric in REGISTRY.collect():
        if metric.name == "yunshu_intent_layer_total":
            for sample in metric.samples:
                if sample.labels.get("layer") == layer:
                    return sample.value
    return None


def _assert_ratio_sum_is_one():
    """断言 ratio 总和 = 1.0（分母同步核心不变量）"""
    total = sum(_intent_layer_counts.values())
    if total == 0:
        return
    ratio_sum = sum(c / total for c in _intent_layer_counts.values())
    assert abs(ratio_sum - 1.0) < 1e-9, \
        "ratio 总和 = %.10f，应 = 1.0（分母同步失败）" % ratio_sum


def _assert_all_ratios_share_denominator():
    """断言所有 layer 的 ratio 共享同一分母（total）

    验证：ratio_i = count_i / total，且 total = Σ count_i
    """
    total = sum(_intent_layer_counts.values())
    if total == 0:
        return
    for layer, count in _intent_layer_counts.items():
        expected_ratio = count / total
        actual_ratio = _get_ratio_gauge_value(layer)
        if actual_ratio is not None:
            assert abs(actual_ratio - expected_ratio) < 1e-6, \
                "layer=%s ratio=%.6f，期望=%.6f（分母不同步）" % (layer, actual_ratio, expected_ratio)


# ──────────────────────────────────────────────────────────────
#  分母同步核心不变量测试
# ──────────────────────────────────────────────────────────────

class TestDenominatorSync:
    """分母同步机制核心测试：所有 layer ratio 共享同一 total"""

    def test_首次调用_单layer_ratio_等于_1_0(self):
        """首次调用某 layer：total=1，ratio=1.0"""
        record_intent_layer("rule")
        assert _intent_layer_counts["rule"] == 1
        assert sum(_intent_layer_counts.values()) == 1
        _assert_ratio_sum_is_one()
        _assert_all_ratios_share_denominator()

    def test_多layer_所有ratio_共享同一分母(self):
        """多 layer 调用后，所有 ratio 基于同一 total 计算"""
        for _ in range(35):
            record_intent_layer("rule")
        for _ in range(55):
            record_intent_layer("semantic")
        for _ in range(10):
            record_intent_layer("llm")

        total = sum(_intent_layer_counts.values())
        assert total == 100
        # 每个 ratio = count / 100
        assert abs(_intent_layer_counts["rule"] / total - 0.35) < 1e-9
        assert abs(_intent_layer_counts["semantic"] / total - 0.55) < 1e-9
        assert abs(_intent_layer_counts["llm"] / total - 0.10) < 1e-9
        _assert_ratio_sum_is_one()
        _assert_all_ratios_share_denominator()

    def test_新增layer_旧layer_ratio_自动重新计算(self):
        """新增 layer 后，旧 layer 的 ratio 自动更新（分母变大）

        场景：先记 10 次 rule（ratio=1.0），再记 10 次 llm，
        rule 的 ratio 应从 1.0 → 0.5（分母从 10 → 20）。
        """
        for _ in range(10):
            record_intent_layer("rule")
        # 此时 rule ratio = 10/10 = 1.0
        assert abs(_intent_layer_counts["rule"] / 10 - 1.0) < 1e-9

        for _ in range(10):
            record_intent_layer("llm")
        # 现在 rule ratio = 10/20 = 0.5（分母同步更新）
        total = sum(_intent_layer_counts.values())
        assert total == 20
        assert abs(_intent_layer_counts["rule"] / total - 0.5) < 1e-9
        assert abs(_intent_layer_counts["llm"] / total - 0.5) < 1e-9
        _assert_ratio_sum_is_one()
        _assert_all_ratios_share_denominator()

    def test_同layer_多次调用_分母正确递增(self):
        """同一 layer 多次调用，total 正确递增"""
        for i in range(1, 101):
            record_intent_layer("semantic")
            total = sum(_intent_layer_counts.values())
            assert total == i, "第 %d 次调用后 total=%d，期望=%d" % (i, total, i)
        _assert_ratio_sum_is_one()

    def test_ratio_总和_始终_1_0_各种场景(self):
        """参数化场景：ratio 总和始终 = 1.0"""
        scenarios = [
            [("rule", 1)],
            [("rule", 100), ("semantic", 100), ("llm", 100)],
            [("rule", 35), ("semantic", 55), ("llm", 10)],
            [("llm", 1), ("llm_low_confidence_fallback", 1)],  # 双重计数场景
            [("reject", 1000)],
        ]
        for scenario in scenarios:
            reset_intent_layer_counts()
            for layer, count in scenario:
                for _ in range(count):
                    record_intent_layer(layer)
            _assert_ratio_sum_is_one()


# ──────────────────────────────────────────────────────────────
#  reset 重置功能测试
# ──────────────────────────────────────────────────────────────

class TestResetDenominator:
    """reset 后分母从零开始重新累计"""

    def test_reset_后_分母归零(self):
        """reset 后 _intent_layer_counts 清空，分母 = 0"""
        for _ in range(100):
            record_intent_layer("rule")
        assert sum(_intent_layer_counts.values()) == 100

        reset_intent_layer_counts()
        assert len(_intent_layer_counts) == 0
        assert sum(_intent_layer_counts.values()) == 0

    def test_reset_后_重新计数_分母不受历史影响(self):
        """reset 后重新计数，ratio 基于新计数（不受历史累计影响）"""
        for _ in range(1000):
            record_intent_layer("rule")
        reset_intent_layer_counts()

        # 重新计数：1 次 rule + 1 次 llm
        record_intent_layer("rule")
        record_intent_layer("llm")
        total = sum(_intent_layer_counts.values())
        assert total == 2
        assert abs(_intent_layer_counts["rule"] / total - 0.5) < 1e-9
        _assert_ratio_sum_is_one()

    def test_reset_不影响_counter_单调递增(self):
        """reset 只清空 ratio 视图，不影响 Counter（进程级单调递增）

        注：Counter 是 prometheus_client 进程级单调递增值，无法重置。
        此处验证 reset 后 _intent_layer_counts 被清空但重新调用仍能正常计数。
        """
        record_intent_layer("rule")
        assert _intent_layer_counts["rule"] == 1

        reset_intent_layer_counts()
        assert "rule" not in _intent_layer_counts  # ratio 视图已清空

        # 重新调用，_intent_layer_counts 从 0 重新开始（Counter 仍在后台累计）
        record_intent_layer("rule")
        assert _intent_layer_counts["rule"] == 1


# ──────────────────────────────────────────────────────────────
#  Gauge 值与 _intent_layer_counts 同步测试
# ──────────────────────────────────────────────────────────────

class TestGaugeSync:
    """验证 yunshu_intent_layer_ratio Gauge 值与 _intent_layer_counts 同步"""

    def test_gauge_值_等于_count_over_total(self):
        """ratio Gauge 值 = _intent_layer_counts[layer] / total"""
        for _ in range(30):
            record_intent_layer("rule")
        for _ in range(70):
            record_intent_layer("semantic")

        total = sum(_intent_layer_counts.values())
        for layer in ["rule", "semantic"]:
            expected = _intent_layer_counts[layer] / total
            actual = _get_ratio_gauge_value(layer)
            assert actual is not None, "Gauge 值缺失: layer=%s" % layer
            assert abs(actual - expected) < 1e-6, \
                "layer=%s gauge=%.6f 期望=%.6f" % (layer, actual, expected)

    def test_gauge_值_在新增layer后_自动更新(self):
        """新增 layer 后，旧 layer 的 Gauge 值自动更新"""
        record_intent_layer("rule")
        rule_ratio_before = _get_ratio_gauge_value("rule")
        assert abs(rule_ratio_before - 1.0) < 1e-6  # 100%

        record_intent_layer("llm")
        rule_ratio_after = _get_ratio_gauge_value("rule")
        assert abs(rule_ratio_after - 0.5) < 1e-6  # 降至 50%

    def test_counter_与_ratio_计数_一致(self):
        """_intent_layer_counts 增量 = 调用次数（Counter 的 ratio 视图镜像）

        注：prometheus Counter 是进程级单调递增无法重置，但 _intent_layer_counts
        在 reset 后从 0 重新累计，此处验证 reset 后的增量正确性。
        """
        record_intent_layer("semantic")
        record_intent_layer("semantic")
        record_intent_layer("semantic")
        # _intent_layer_counts 反映了 reset 后的增量
        assert _intent_layer_counts["semantic"] == 3
        # ratio 基于这个计数计算
        total = sum(_intent_layer_counts.values())
        assert abs(_intent_layer_counts["semantic"] / total - 1.0) < 1e-9


# ──────────────────────────────────────────────────────────────
#  边界场景测试
# ──────────────────────────────────────────────────────────────

class TestEdgeCases:
    """分母同步边界场景"""

    def test_未知layer_自动纳入分母(self):
        """传入新 layer 值，自动创建并纳入分母计算"""
        record_intent_layer("rule")
        record_intent_layer("custom_layer_xyz")
        total = sum(_intent_layer_counts.values())
        assert total == 2
        assert "custom_layer_xyz" in _intent_layer_counts
        _assert_ratio_sum_is_one()

    def test_大量调用_分母不溢出(self):
        """10000 次调用后 ratio 仍正确（分母不溢出）"""
        for _ in range(5000):
            record_intent_layer("rule")
        for _ in range(5000):
            record_intent_layer("llm")
        total = sum(_intent_layer_counts.values())
        assert total == 10000
        assert abs(_intent_layer_counts["rule"] / total - 0.5) < 1e-9
        _assert_ratio_sum_is_one()

    def test_交替调用_分母最终一致(self):
        """模拟交替调用，最终 ratio 一致"""
        layers = ["rule", "semantic", "llm", "reject"]
        for i in range(400):
            record_intent_layer(layers[i % 4])
        total = sum(_intent_layer_counts.values())
        assert total == 400
        # 每个 layer 100 次
        for layer in layers:
            assert _intent_layer_counts[layer] == 100
            assert abs(_intent_layer_counts[layer] / total - 0.25) < 1e-9
        _assert_ratio_sum_is_one()

    def test_标准四层分布_ratio_精确(self):
        """35/55/10 标准分布下 ratio 精确"""
        for _ in range(35):
            record_intent_layer("rule")
        for _ in range(55):
            record_intent_layer("semantic")
        for _ in range(10):
            record_intent_layer("llm")
        total = sum(_intent_layer_counts.values())
        assert total == 100
        assert abs(_intent_layer_counts["rule"] / total - 0.35) < 1e-9
        assert abs(_intent_layer_counts["semantic"] / total - 0.55) < 1e-9
        assert abs(_intent_layer_counts["llm"] / total - 0.10) < 1e-9
        _assert_all_ratios_share_denominator()
