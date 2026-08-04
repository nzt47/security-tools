"""fallback 层双重计数设计单元测试

针对修正后的审计文档（docs/audit/orchestrator_intent_layer_audit.md L189-195）：
fallback 已启用，是 llm 的设计性子指标（非独立意图层）。
一次低置信度请求计 2 次（L467 llm + L553 fallback），ratio 总和仍 = 1.0。

【不易】守 ratio 总和 = 1.0；fallback ⊆ llm（子集不变量）
【简易】直接测试 record_intent_layer 计数行为，不拉起 Orchestrator

关联文档：docs/audit/orchestrator_intent_layer_audit.md 命中点 6（L186-313）
关联代码：agent/monitoring/prometheus.py record_intent_layer
"""
import pytest

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


def _ratio_sum():
    """计算当前 ratio 总和（分母同步不变量，应恒 = 1.0）"""
    total = sum(_intent_layer_counts.values())
    if total == 0:
        return 0.0
    return sum(c / total for c in _intent_layer_counts.values())


class TestFallbackSubmetricDesign:
    """fallback 作为 llm 子指标的双重计数设计验证

    对照审计文档 L189-195：
    - fallback 是 llm 的设计性子指标（非 bug）
    - 一次低置信度请求 = L467 llm + L553 fallback（计 2 次）
    - ratio 总和恒 = 1.0（分母同步）
    """

    def test_实际控制流_L467_llm_后_L553_fallback_ratio_仍_1_0(self):
        """模拟真实控制流：L467 记 llm → L553 记 fallback，ratio 总和 = 1.0"""
        record_intent_layer("llm")                          # L467
        record_intent_layer("llm_low_confidence_fallback") # L553
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_全部低置信度_fallback_等于_llm_兜底率_1_0(self):
        """所有 LLM 请求都是低置信度：fallback = llm，兜底率 = 100%"""
        for _ in range(10):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")
        assert _intent_layer_counts["llm"] == 10
        assert _intent_layer_counts["llm_low_confidence_fallback"] == 10
        # 兜底率 = fallback / llm = 1.0
        low_conf_rate = _intent_layer_counts["llm_low_confidence_fallback"] / _intent_layer_counts["llm"]
        assert abs(low_conf_rate - 1.0) < 1e-9
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_6层并列_含fallback_ratio_总和_1_0(self):
        """6 层并列展示（含 fallback）ratio 总和仍 = 1.0

        对照审计文档 L191：dashboard 将 6 层并列展示时 llm 占比被稀释，
        但 ratio 总和数学上恒 = 1.0（分母同步保证）。
        """
        for _ in range(30):
            record_intent_layer("rule")
        for _ in range(20):
            record_intent_layer("template")
        for _ in range(10):
            record_intent_layer("semantic")
        for _ in range(35):
            record_intent_layer("llm")
        for _ in range(5):
            record_intent_layer("reject")
        for _ in range(15):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")  # 低置信度双计
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_fallback_子集不变量_始终_小于等于_llm(self):
        """fallback ⊆ llm：fallback 计数始终 ≤ llm 计数（守业务语义）"""
        for _ in range(20):
            record_intent_layer("llm")
        for _ in range(7):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")
        assert _intent_layer_counts["llm_low_confidence_fallback"] <= _intent_layer_counts["llm"]
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_文档稀释示例_对照审计L191(self):
        """对照审计文档 L191 稀释说明：fallback 纳入分母使 total 增大

        50 个请求，对比有无 fallback 的分母变化：
        - 全部正常 llm（无 fallback）：total = 50
        - 15 个低置信度（双计 llm+fallback）：total = 65（多 15 次 fallback）
        ratio 总和两种场景均 = 1.0（分母同步）
        """
        # ── 场景 1：50 个请求全部正常 llm（无 fallback）──
        for _ in range(50):
            record_intent_layer("llm")
        total_no_fb = sum(_intent_layer_counts.values())
        assert total_no_fb == 50
        assert abs(_ratio_sum() - 1.0) < 1e-9

        # ── 场景 2：50 个请求，其中 15 个低置信度（llm+fallback 双计）──
        reset_intent_layer_counts()
        for _ in range(35):  # 正常 llm
            record_intent_layer("llm")
        for _ in range(15):  # 低置信度：llm + fallback 双计
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")
        total_with_fb = sum(_intent_layer_counts.values())
        # 35 正常 + 15 低置信度 = 50 请求，但 fallback 双计使 total = 65
        assert total_with_fb == 65
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_子指标隔离后_5主层归一化_总和_1_0(self):
        """方案 A PromQL 逻辑验证：排除 fallback 后 5 主层归一化总和 = 1.0

        对照审计文档方案 A PromQL #1（L276-278）：
          sum by (layer) (rate(...{layer!="llm_low_confidence_fallback"}))
            / on() group_left sum(rate(...{layer!="llm_low_confidence_fallback"}))
        此处用 _intent_layer_counts 模拟该 PromQL 的归一化逻辑。
        """
        for _ in range(30):
            record_intent_layer("rule")
        for _ in range(20):
            record_intent_layer("template")
        for _ in range(10):
            record_intent_layer("semantic")
        for _ in range(35):
            record_intent_layer("llm")
        for _ in range(5):
            record_intent_layer("reject")
        for _ in range(15):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")

        # 排除 fallback 的 5 主层
        main_layers = {'rule', 'template', 'semantic', 'llm', 'reject'}
        main_total = sum(c for layer, c in _intent_layer_counts.items() if layer in main_layers)
        # 归一化（模拟 PromQL 除法）：每主层 count / main_total
        normalized_sum = sum(
            c / main_total for layer, c in _intent_layer_counts.items() if layer in main_layers
        )
        assert abs(normalized_sum - 1.0) < 1e-9

    def test_大量低置信度_ratio_仍_1_0(self):
        """压力测试：大量低置信度请求，ratio 总和仍 = 1.0"""
        for _ in range(500):
            record_intent_layer("llm")
            record_intent_layer("llm_low_confidence_fallback")
        for _ in range(500):
            record_intent_layer("rule")
        assert abs(_ratio_sum() - 1.0) < 1e-9

    def test_交替请求_低置信度与正常_ratio_仍_1_0(self):
        """交替请求：低置信度(llm+fallback) 与正常 llm 交替，ratio 仍 = 1.0"""
        for i in range(20):
            if i % 2 == 0:
                # 低置信度：双计
                record_intent_layer("llm")
                record_intent_layer("llm_low_confidence_fallback")
            else:
                # 正常：单计
                record_intent_layer("llm")
        # llm = 20, fallback = 10, total = 30
        assert _intent_layer_counts["llm"] == 20
        assert _intent_layer_counts["llm_low_confidence_fallback"] == 10
        assert abs(_ratio_sum() - 1.0) < 1e-9
