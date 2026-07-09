"""ABTestManager 集成测试

覆盖 A/B 实验框架的全生命周期：
1. 实验创建参数校验
2. 状态机流转（DRAFT→RUNNING→PAUSED→TERMINATED）
3. 确定性分流与持久化
4. 白名单/黑名单/流量比例控制
5. 指标记录与显著性检验（双样本 Z 检验）
6. 分层实验分配
7. 趋势数据/结论生成/自动停止
"""

import random

import pytest
from unittest.mock import patch

from agent.ab_testing import (
    ABTestManager,
    ExperimentStatus,
    ExperimentType,
    ExperimentVariant,
)

pytestmark = pytest.mark.integration


def _make_variants():
    """构造对照组 + 实验组两个变体"""
    return [
        ExperimentVariant(
            variant_id="control",
            name="对照组",
            weight=50,
            is_control=True,
        ),
        ExperimentVariant(
            variant_id="treatment",
            name="实验组",
            weight=50,
            is_control=False,
        ),
    ]


class TestABTestingIntegration:
    """ABTestManager 集成测试"""

    def test_create_experiment_validates_inputs(self, ab_test_manager):
        """测试 1：create_experiment 参数校验"""
        mgr = ab_test_manager

        # variants < 2
        with pytest.raises(ValueError, match="至少需要 2 个变体"):
            mgr.create_experiment(
                name="test",
                variants=[ExperimentVariant(variant_id="v1", name="v1")],
            )

        # 权重和 <= 0
        with pytest.raises(ValueError, match="权重之和必须大于 0"):
            mgr.create_experiment(
                name="test",
                variants=[
                    ExperimentVariant(variant_id="v1", name="v1", weight=0),
                    ExperimentVariant(variant_id="v2", name="v2", weight=0),
                ],
            )

        # traffic_ratio <= 0
        with pytest.raises(ValueError, match="流量比例必须在"):
            mgr.create_experiment(
                name="test",
                variants=_make_variants(),
                traffic_ratio=0,
            )

        # traffic_ratio > 1.0
        with pytest.raises(ValueError, match="流量比例必须在"):
            mgr.create_experiment(
                name="test",
                variants=_make_variants(),
                traffic_ratio=1.5,
            )

        # 正常创建
        exp = mgr.create_experiment(
            name="正常实验",
            experiment_type=ExperimentType.PROMPT_VERSION,
            variants=_make_variants(),
        )
        assert exp.experiment_id is not None
        assert exp.status == ExperimentStatus.DRAFT
        assert len(exp.variants) == 2
        assert exp.traffic_ratio == 1.0

    def test_experiment_lifecycle_start_pause_terminate(self, ab_test_manager):
        """测试 2：状态机流转 DRAFT→RUNNING→PAUSED→TERMINATED"""
        mgr = ab_test_manager

        exp = mgr.create_experiment(name="生命周期测试", variants=_make_variants())
        exp_id = exp.experiment_id

        # DRAFT → RUNNING
        assert mgr.start_experiment(exp_id) is True
        assert mgr.get_experiment(exp_id).status == ExperimentStatus.RUNNING

        # RUNNING → PAUSED
        assert mgr.pause_experiment(exp_id) is True
        assert mgr.get_experiment(exp_id).status == ExperimentStatus.PAUSED

        # PAUSED → RUNNING（恢复）
        assert mgr.start_experiment(exp_id) is True
        assert mgr.get_experiment(exp_id).status == ExperimentStatus.RUNNING

        # RUNNING → TERMINATED
        assert mgr.terminate_experiment(exp_id, reason="测试结束") is True
        assert mgr.get_experiment(exp_id).status == ExperimentStatus.TERMINATED
        assert mgr.get_experiment(exp_id).ended_at is not None

        # 幂等：已终止再终止返回 True
        assert mgr.terminate_experiment(exp_id) is True

        # 非法状态转换：PAUSED 实验不能暂停
        exp2 = mgr.create_experiment(name="非法暂停测试", variants=_make_variants())
        with pytest.raises(ValueError, match="只能暂停运行中的实验"):
            mgr.pause_experiment(exp2.experiment_id)

    def test_assign_variant_deterministic_and_persistent(self, ab_test_manager):
        """测试 3：确定性分流与持久化"""
        mgr = ab_test_manager

        exp = mgr.create_experiment(name="确定性分流", variants=_make_variants())
        mgr.start_experiment(exp.experiment_id)

        user_id = "user_deterministic_123"
        first = mgr.assign_variant(exp.experiment_id, user_id)
        assert first is not None

        # 同一用户多次分配得到相同变体
        for _ in range(5):
            again = mgr.assign_variant(exp.experiment_id, user_id)
            assert again.variant_id == first.variant_id

        # 统计非空
        stats = mgr.get_assignment_stats(exp.experiment_id)
        assert stats["total_assignments"] >= 1
        assert first.variant_id in stats["variant_distribution"]

    def test_assign_variant_whitelist_blacklist_traffic(self, ab_test_manager):
        """测试 4：白名单/黑名单/流量比例"""
        mgr = ab_test_manager

        # 白名单用户强制分到 variants[0]
        exp_wl = mgr.create_experiment(
            name="白名单测试",
            variants=_make_variants(),
            whitelist=["vip_user"],
        )
        mgr.start_experiment(exp_wl.experiment_id)

        # 验证 whitelist 正确持久化与读取
        exp_wl_loaded = mgr.get_experiment(exp_wl.experiment_id)
        assert exp_wl_loaded.status == ExperimentStatus.RUNNING
        assert "vip_user" in exp_wl_loaded.whitelist, \
            f"whitelist 读取异常: {exp_wl_loaded.whitelist}"

        variant = mgr.assign_variant(exp_wl.experiment_id, "vip_user")
        assert variant is not None
        assert variant.variant_id == "control", \
            f"白名单用户应分到 control，实际: {variant.variant_id}"

        # 黑名单用户返回 None
        exp_bl = mgr.create_experiment(
            name="黑名单测试",
            variants=_make_variants(),
            blacklist=["blocked_user"],
        )
        mgr.start_experiment(exp_bl.experiment_id)

        # 验证 blacklist 正确持久化与读取
        exp_bl_loaded = mgr.get_experiment(exp_bl.experiment_id)
        assert exp_bl_loaded.status == ExperimentStatus.RUNNING
        assert "blocked_user" in exp_bl_loaded.blacklist

        result = mgr.assign_variant(exp_bl.experiment_id, "blocked_user")
        assert result is None, f"黑名单用户应返回 None，实际: {result}"

        # 低流量比例：多数用户返回 None
        exp_low = mgr.create_experiment(
            name="低流量测试",
            variants=_make_variants(),
            traffic_ratio=0.05,
        )
        mgr.start_experiment(exp_low.experiment_id)
        none_count = sum(
            1 for i in range(100)
            if mgr.assign_variant(exp_low.experiment_id, f"u_{i}") is None
        )
        # 95% 应被流量过滤
        assert none_count > 90

    def test_record_metric_and_analyze_significance(self, ab_test_manager):
        """测试 5：指标记录与显著性检验"""
        mgr = ab_test_manager

        exp = mgr.create_experiment(
            name="显著性检验",
            variants=_make_variants(),
            min_samples=10,
            significance_level=0.05,
        )
        mgr.start_experiment(exp.experiment_id)

        # 构造对照组均值 ~60，实验组均值 ~85，差异显著且方差非零
        rng = random.Random(42)
        for i in range(30):
            mgr.record_metric(
                exp.experiment_id, "control", "quality_score",
                60.0 + rng.uniform(-5, 5),
                trace_id=f"trace_c_{i}",
            )
            mgr.record_metric(
                exp.experiment_id, "treatment", "quality_score",
                85.0 + rng.uniform(-5, 5),
                trace_id=f"trace_t_{i}",
            )

        result = mgr.analyze_results(exp.experiment_id)
        assert result.is_significant is True
        assert result.winner == "treatment"
        assert result.p_value < 0.05
        assert result.sample_size == 60

        # 指标可按 trace 追溯
        metrics = mgr.get_metrics_by_trace("trace_c_0")
        assert len(metrics) == 1
        assert 55.0 <= metrics[0].value <= 65.0

    def test_layered_experiments_assignment(self, ab_test_manager):
        """测试 6：分层实验分配"""
        mgr = ab_test_manager

        # Layer 0 实验
        exp_l0 = mgr.create_experiment(
            name="Layer0 实验",
            variants=_make_variants(),
            layer=0,
        )
        mgr.start_experiment(exp_l0.experiment_id)

        # Layer 1 实验
        exp_l1 = mgr.create_experiment(
            name="Layer1 实验",
            variants=[
                ExperimentVariant(variant_id="l1_a", name="A", weight=50),
                ExperimentVariant(variant_id="l1_b", name="B", weight=50),
            ],
            layer=1,
        )
        mgr.start_experiment(exp_l1.experiment_id)

        # 未运行的实验不参与
        exp_draft = mgr.create_experiment(
            name="未运行实验",
            variants=_make_variants(),
            layer=0,
        )
        # 不 start，保持 DRAFT

        assignments = mgr.assign_variant_with_layers("user_layered")
        assert isinstance(assignments, dict)
        assert exp_l0.experiment_id in assignments
        assert exp_l1.experiment_id in assignments
        assert exp_draft.experiment_id not in assignments

        # get_layer_experiments 只返回运行中的
        layer0_exps = mgr.get_layer_experiments(0)
        assert len(layer0_exps) == 1
        assert layer0_exps[0].experiment_id == exp_l0.experiment_id

    def test_trend_data_conclusion_auto_stop(self, ab_test_manager):
        """测试 7：趋势数据/结论生成/自动停止"""
        mgr = ab_test_manager

        exp = mgr.create_experiment(
            name="趋势与结论",
            variants=_make_variants(),
            min_samples=5,
        )
        mgr.start_experiment(exp.experiment_id)

        # 记录一些指标
        for i in range(10):
            mgr.record_metric(
                exp.experiment_id, "control", "quality_score", 70.0,
                trace_id=f"trend_c_{i}",
            )
            mgr.record_metric(
                exp.experiment_id, "treatment", "quality_score", 75.0,
                trace_id=f"trend_t_{i}",
            )

        # 趋势分桶正确
        trend = mgr.get_trend_data(exp.experiment_id, "quality_score", interval_hours=1)
        assert "control" in trend
        assert "treatment" in trend
        assert len(trend["control"]) > 0
        assert "mean" in trend["control"][0]

        # 结论含 recommendations
        conclusion = mgr.generate_conclusion(exp.experiment_id)
        assert "recommendations" in conclusion
        assert len(conclusion["recommendations"]) > 0
        assert conclusion["experiment_id"] == exp.experiment_id

        # 无指标时 check_auto_stop 返回 False（新实验无恶化）
        exp_clean = mgr.create_experiment(
            name="无指标自动停止",
            variants=_make_variants(),
        )
        mgr.start_experiment(exp_clean.experiment_id)
        assert mgr.check_auto_stop(exp_clean.experiment_id) is False
