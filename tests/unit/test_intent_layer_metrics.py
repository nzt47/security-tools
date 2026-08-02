"""意图识别三层漏斗占比指标测试（任务5：P2-1）

覆盖 agent.monitoring.prometheus 模块级指标：
- yunshu_intent_layer_total (Counter, labels=["layer"])
- yunshu_intent_layer_ratio (Gauge, labels=["layer"])
- record_intent_layer / reset_intent_layer_counts

验收对应：
- 四层命中点（rule/semantic/llm/reject）均记录指标
- 占比可计算（35%/55%/10% 标准可验证）
"""

import pytest

from agent.monitoring.prometheus import (
    _PROMETHEUS_AVAILABLE,
    yunshu_intent_layer_total,
    yunshu_intent_layer_ratio,
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts,
)


def _gauge_value(layer: str):
    """从 prometheus_client Gauge 提取指定 layer 的当前值（无则 None）"""
    for metric in yunshu_intent_layer_ratio.collect():
        for sample in metric.samples:
            if sample.labels.get("layer") == layer:
                return sample.value
    return None


def _counter_value(layer: str):
    """从 prometheus_client Counter 提取指定 layer 的累计值（排除 _created 样本）"""
    for metric in yunshu_intent_layer_total.collect():
        for sample in metric.samples:
            if sample.labels.get("layer") == layer and not sample.name.endswith("_created"):
                return sample.value
    return None


@pytest.fixture(autouse=True)
def _reset_intent_counts():
    """每个测试前后重置模块级 ratio 计数，隔离测试

    【不易】仅清空 _intent_layer_counts dict；prometheus_client Counter 单调不可逆，
           断言用 before/after 差值而非绝对值
    """
    reset_intent_layer_counts()
    yield
    reset_intent_layer_counts()


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestIntentLayerCounter:
    """Counter 递增测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_counter_increment_rule(self):
        """规则层 Counter 递增（验收：curl /metrics 可见 yunshu_intent_layer_total{layer="rule"}）"""
        before = _counter_value("rule") or 0.0
        record_intent_layer("rule")
        after = _counter_value("rule")
        assert after == pytest.approx(before + 1.0)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_counter_increment_all_layers(self):
        """四层（rule/semantic/llm/reject）Counter 均可记录"""
        for layer in ("rule", "semantic", "llm", "reject"):
            before = _counter_value(layer) or 0.0
            record_intent_layer(layer)
            assert _counter_value(layer) == pytest.approx(before + 1.0)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_counter_monotonic_multiple_increments(self):
        """同一层多次记录，Counter 单调递增"""
        before = _counter_value("semantic") or 0.0
        for _ in range(5):
            record_intent_layer("semantic")
        assert _counter_value("semantic") == pytest.approx(before + 5.0)


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestIntentLayerRatio:
    """ratio Gauge 占比计算测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_ratio_even_distribution(self):
        """均匀分布 1:1 → 各层 ratio=0.5"""
        record_intent_layer("rule")
        record_intent_layer("semantic")
        assert _gauge_value("rule") == pytest.approx(0.5)
        assert _gauge_value("semantic") == pytest.approx(0.5)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_ratio_sums_to_one(self):
        """所有已记录层 ratio 之和 = 1.0"""
        for _ in range(3):
            record_intent_layer("rule")
        for _ in range(5):
            record_intent_layer("semantic")
        record_intent_layer("llm")
        total = sum(
            _gauge_value(l) for l in _intent_layer_counts
            if _gauge_value(l) is not None
        )
        assert total == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_ratio_35_55_10_distribution(self):
        """模拟 35%/55%/10% 标准分布，验证占比可计算"""
        for _ in range(35):
            record_intent_layer("rule")
        for _ in range(55):
            record_intent_layer("semantic")
        for _ in range(10):
            record_intent_layer("llm")
        assert _gauge_value("rule") == pytest.approx(0.35, abs=1e-9)
        assert _gauge_value("semantic") == pytest.approx(0.55, abs=1e-9)
        assert _gauge_value("llm") == pytest.approx(0.10, abs=1e-9)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_ratio_updates_after_new_layer(self):
        """新增层后已有层 ratio 同步重算"""
        record_intent_layer("rule")  # rule=1/1=1.0
        assert _gauge_value("rule") == pytest.approx(1.0)
        record_intent_layer("semantic")  # rule=0.5, semantic=0.5
        assert _gauge_value("rule") == pytest.approx(0.5)
        assert _gauge_value("semantic") == pytest.approx(0.5)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_reset_clears_counts(self):
        """reset_intent_layer_counts 清空模块级计数"""
        record_intent_layer("rule")
        record_intent_layer("llm")
        assert _intent_layer_counts  # 非空
        reset_intent_layer_counts()
        assert _intent_layer_counts == {}


@pytest.mark.skipif(not _PROMETHEUS_AVAILABLE, reason="prometheus_client not installed")
class TestIntentLayerDegraded:
    """降级与健壮性测试"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_record_does_not_raise(self):
        """record_intent_layer 不抛异常（埋点失败隔离，不影响主链路）"""
        record_intent_layer("rule")
        record_intent_layer("")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_unknown_layer_label_accepted(self):
        """未知 layer 标签被接受（Counter labels 动态创建）"""
        before = _counter_value("custom_layer") or 0.0
        record_intent_layer("custom_layer")
        assert _counter_value("custom_layer") == pytest.approx(before + 1.0)
