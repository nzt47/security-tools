"""意图识别埋点 ratio 计算逻辑 + 双重计数风险单元测试

覆盖场景：
- ratio 总和始终 = 1.0（不超 100%）的数学证明验证
- 双重计数导致 Counter 总和 > 实际请求数
- 双重计数不影响 ratio 总和（分母同步增大）
- reset_intent_layer_counts 重置功能
- llm_low_confidence_fallback 双重计数场景模拟（预备性测试）

关联文档：docs/audit/orchestrator_intent_layer_audit.md 命中点 6
关联代码：agent/monitoring/prometheus.py record_intent_layer / reset_intent_layer_counts

注意：llm_low_confidence_fallback 功能已启用（orchestrator.py L553 存在调用），
是 llm 的设计性子指标（非独立意图层）。本测试用模拟 layer 值验证 ratio 计算逻辑
的通用正确性。关联子指标设计专项测试：tests/unit/test_fallback_submetric_ratio_invariant.py

【不易】守 INV-2：业务结果确定后才埋点；ratio 总和恒 = 1.0
【简易】直接测试 record_intent_layer 的计数行为，不拉起完整 Orchestrator 依赖链
"""
import pytest

from agent.monitoring.prometheus import (
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts,
)


@pytest.fixture(autouse=True)
def _reset_counts():
    """每个测试前重置模块级 ratio 计数视图，隔离测试间状态

    Why: _intent_layer_counts 是模块级 dict，测试间会互相污染。
    reset_intent_layer_counts() 清空 dict，但 prometheus_client Counter
    是进程级单调递增无法重置，故测试只验证 _intent_layer_counts 和 ratio 逻辑。
    """
    reset_intent_layer_counts()
    yield
    reset_intent_layer_counts()


class TestRatioSumInvariant:
    """ratio 总和不变量测试：始终 = 1.0，不超 100%"""

    def test_单层场景_ratio_总和等于_1_0(self):
        """单层场景：只有 llm 计数，ratio 总和 = 1.0"""
        record_intent_layer("llm")
        total = sum(_intent_layer_counts.values())
        ratio_sum = sum(c / total for c in _intent_layer_counts.values())
        assert abs(ratio_sum - 1.0) < 0.001

    def test_混合流量_ratio_总和等于_1_0(self):
        """混合流量：rule/semantic/llm/reject 四层，ratio 总和 = 1.0"""
        for _ in range(35):
            record_intent_layer("rule")
        for _ in range(55):
            record_intent_layer("semantic")
        for _ in range(10):
            record_intent_layer("llm")
        for _ in range(5):
            record_intent_layer("reject")

        total = sum(_intent_layer_counts.values())
        ratio_sum = sum(c / total for c in _intent_layer_counts.values())
        assert abs(ratio_sum - 1.0) < 0.001

    def test_极端场景_全reject_ratio_仍等于_1_0(self):
        """极端场景：全部 reject，ratio 总和仍 = 1.0"""
        for _ in range(100):
            record_intent_layer("reject")
        total = sum(_intent_layer_counts.values())
        ratio_sum = sum(c / total for c in _intent_layer_counts.values())
        assert abs(ratio_sum - 1.0) < 0.001


class TestDualCountingRisk:
    """双重计数风险测试：模拟同一次请求计入两个 layer 的场景

    场景映射：orchestrator.py 中 LLM 调用前记 llm（L467），LLM 低置信度时再记
    llm_low_confidence_fallback（L553，已启用）。此处用模拟值验证 ratio 逻辑。
    """

    def test_双重计数_两个layer_同时递增(self):
        """双重计数：一次请求同时计入 llm + llm_low_confidence_fallback"""
        record_intent_layer("llm")
        record_intent_layer("llm_low_confidence_fallback")

        assert _intent_layer_counts["llm"] == 1
        assert _intent_layer_counts["llm_low_confidence_fallback"] == 1

    def test_双重计数_counter_总和_大于实际请求数(self):
        """双重计数导致 Counter 总和 > 实际请求数

        场景：3 rule + 5 semantic + 7 正常 llm + 3 低置信度 llm
        - 实际请求数 = 18
        - Counter 总和 = 21（多 3 次 fallback 双计）
        """
        for _ in range(3):
            record_intent_layer("rule")
        for _ in range(5):
            record_intent_layer("semantic")
        for _ in range(7):
            record_intent_layer("llm")
        for _ in range(3):  # 低置信度：llm + fallback 双计
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")

        total_counts = sum(_intent_layer_counts.values())
        actual_requests = 3 + 5 + 7 + 3  # = 18
        assert total_counts == actual_requests + 3  # 多了 3 次 fallback

    def test_双重计数_ratio_总和_仍等于_1_0(self):
        """双重计数不影响 ratio 总和（分母同步增大，总和恒 = 1.0）"""
        for _ in range(3):
            record_intent_layer("rule")
        for _ in range(5):
            record_intent_layer("semantic")
        for _ in range(7):
            record_intent_layer("llm")
        for _ in range(3):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")

        total = sum(_intent_layer_counts.values())
        ratio_sum = sum(c / total for c in _intent_layer_counts.values())
        assert abs(ratio_sum - 1.0) < 0.001

    def test_双重计数_其他层_被稀释(self):
        """双重计数导致其他层 ratio 被稀释（分母变大）

        无 fallback 时：rule ratio = 3/18 = 16.7%
        有 fallback 时：rule ratio = 3/21 = 14.3%（被稀释）
        """
        for _ in range(3):
            record_intent_layer("rule")
        for _ in range(5):
            record_intent_layer("semantic")
        for _ in range(7):
            record_intent_layer("llm")
        for _ in range(3):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")

        total = sum(_intent_layer_counts.values())
        rule_ratio = _intent_layer_counts["rule"] / total
        # rule=3, total=21 → 14.3%（被稀释）
        assert _intent_layer_counts["rule"] == 3
        assert total == 21
        assert abs(rule_ratio - 3 / 21) < 0.001

    def test_子指标层_是父层_的子集(self):
        """llm_low_confidence_fallback 计数 ≤ llm 计数（包含关系）

        业务语义：低置信度是 LLM 调用的子集。
        fallback 计数 = 低置信度请求数，
        llm 计数 = 所有 LLM 路径请求数（含低置信度）。
        """
        for _ in range(7):
            record_intent_layer("llm")
        for _ in range(3):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")

        assert _intent_layer_counts["llm"] == 10
        assert _intent_layer_counts["llm_low_confidence_fallback"] == 3
        assert _intent_layer_counts["llm_low_confidence_fallback"] <= _intent_layer_counts["llm"]

    def test_低置信率_指标计算正确(self):
        """LLM 低置信率 = fallback / llm（独立指标，不受双重计数影响）

        dashboard 推荐 PromQL：
        yunshu_intent_layer_total{layer="llm_low_confidence_fallback"}
          / yunshu_intent_layer_total{layer="llm"}
        """
        for _ in range(7):
            record_intent_layer("llm")
        for _ in range(3):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")

        llm_count = _intent_layer_counts["llm"]
        fallback_count = _intent_layer_counts["llm_low_confidence_fallback"]
        low_confidence_rate = fallback_count / llm_count
        assert abs(low_confidence_rate - 0.3) < 0.001


class TestResetIntentLayerCounts:
    """reset_intent_layer_counts 重置功能测试"""

    def test_重置后_计数视图为空(self):
        """reset 后 _intent_layer_counts 清空"""
        record_intent_layer("rule")
        record_intent_layer("semantic")
        assert len(_intent_layer_counts) > 0

        reset_intent_layer_counts()
        assert len(_intent_layer_counts) == 0

    def test_重置后_重新计数_不受历史影响(self):
        """reset 后重新计数，ratio 基于新计数计算"""
        for _ in range(100):
            record_intent_layer("rule")
        reset_intent_layer_counts()

        # 重新计数：只有 llm
        record_intent_layer("llm")
        assert _intent_layer_counts == {"llm": 1}

    def test_重置不影响_counter_累计值(self):
        """reset 只清空 ratio 计数视图，不影响 prometheus Counter（单调递增）

        注：Counter 无法在进程内重置，此处只验证 _intent_layer_counts 被清空。
        """
        record_intent_layer("rule")
        record_intent_layer("llm")
        reset_intent_layer_counts()
        # _intent_layer_counts 被清空
        assert _intent_layer_counts == {}
        # 但重新调用仍能正常计数
        record_intent_layer("semantic")
        assert _intent_layer_counts == {"semantic": 1}
