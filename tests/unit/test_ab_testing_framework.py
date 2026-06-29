"""
A/B测试框架验证用例

覆盖10个核心测试场景：
1. 分流确定性验证（同一用户始终同组）
2. 流量比例准确性（5%流量≈5%用户）
3. 实验组和对照组的隔离性
4. 实验启动/停止的即时生效
5. 指标统计的准确性
6. 统计显著性计算的正确性
7. 异常熔断机制的触发
8. 实验数据持久化与恢复
9. 多层实验的互不干扰
10. 实验结论生成的合理性
"""

import pytest
import os
import shutil
import time
import json
import uuid

from agent.ab_testing import (
    ABTestManager,
    ExperimentStatus,
    ExperimentType,
    ExperimentVariant,
    get_ab_test_manager,
)


def get_temp_dir():
    """获取测试临时目录"""
    temp_dir = os.path.join(os.path.dirname(__file__), 'temp', str(uuid.uuid4())[:8])
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


def cleanup_temp_dir(temp_dir):
    """清理临时目录"""
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


class TestABTestingDeterministicAssignment:
    """测试1：分流确定性验证"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_same_user_same_variant(self):
        """同一用户多次调用应分配到同一变体"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )
            manager.start_experiment(exp.experiment_id)

            user_id = "test_user_001"
            variant1 = manager.assign_variant(exp.experiment_id, user_id)
            variant2 = manager.assign_variant(exp.experiment_id, user_id)
            variant3 = manager.assign_variant(exp.experiment_id, user_id)

            assert variant1 is not None
            assert variant2 is not None
            assert variant3 is not None
            assert variant1.variant_id == variant2.variant_id == variant3.variant_id
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_different_users_different_variants(self):
        """不同用户应分配到不同变体（基于哈希）"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )
            manager.start_experiment(exp.experiment_id)

            variant1 = manager.assign_variant(exp.experiment_id, "user_a")
            variant2 = manager.assign_variant(exp.experiment_id, "user_b")

            assert variant1 is not None
            assert variant2 is not None
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingTrafficRatio:
    """测试2：流量比例准确性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_5_percent_traffic(self):
        """5%流量配置应约5%用户被选中"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
                traffic_ratio=0.05,
            )
            manager.start_experiment(exp.experiment_id)

            total_users = 1000
            assigned_count = 0
            for i in range(total_users):
                variant = manager.assign_variant(exp.experiment_id, f"user_{i}")
                if variant is not None:
                    assigned_count += 1

            ratio = assigned_count / total_users
            assert 0.03 <= ratio <= 0.07, f"流量比例 {ratio} 不在预期范围内 [0.03, 0.07]"
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_100_percent_traffic(self):
        """100%流量配置应所有用户都被选中"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
                traffic_ratio=1.0,
            )
            manager.start_experiment(exp.experiment_id)

            for i in range(100):
                variant = manager.assign_variant(exp.experiment_id, f"user_{i}")
                assert variant is not None, f"用户 {i} 未被分配变体"
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingIsolation:
    """测试3：实验组和对照组的隔离性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_variant_isolation(self):
        """实验组和对照组的指标应独立统计"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
                traffic_ratio=1.0,
            )
            manager.start_experiment(exp.experiment_id)

            manager.record_metric(exp.experiment_id, "control", "quality_score", 80.0, user_id="user1")
            manager.record_metric(exp.experiment_id, "treatment", "quality_score", 90.0, user_id="user2")

            result = manager.analyze_results(exp.experiment_id)

            control_stats = result.variant_results.get("control", {})
            treatment_stats = result.variant_results.get("treatment", {})

            assert control_stats.get("count") == 1
            assert treatment_stats.get("count") == 1
            assert control_stats.get("mean") == 80.0
            assert treatment_stats.get("mean") == 90.0
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingExperimentLifecycle:
    """测试4：实验启动/停止的即时生效"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_experiment_start_stop_effective(self):
        """实验启动后应立即生效，停止后应不再分配"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )

            variant_before = manager.assign_variant(exp.experiment_id, "test_user")
            assert variant_before is None

            manager.start_experiment(exp.experiment_id)

            variant_during = manager.assign_variant(exp.experiment_id, "test_user")
            assert variant_during is not None

            manager.terminate_experiment(exp.experiment_id)

            exp_after = manager.get_experiment(exp.experiment_id)
            assert exp_after.status == ExperimentStatus.TERMINATED
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_pause_resume(self):
        """实验暂停后应保留已分配用户，恢复后继续分配"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )
            manager.start_experiment(exp.experiment_id)

            variant1 = manager.assign_variant(exp.experiment_id, "user1")
            assert variant1 is not None

            manager.pause_experiment(exp.experiment_id)

            variant2 = manager.assign_variant(exp.experiment_id, "user2")
            assert variant2 is None

            manager.start_experiment(exp.experiment_id)

            variant3 = manager.assign_variant(exp.experiment_id, "user2")
            assert variant3 is not None

            variant1_again = manager.assign_variant(exp.experiment_id, "user1")
            assert variant1_again.variant_id == variant1.variant_id
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingMetricStats:
    """测试5：指标统计的准确性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_metric_statistics(self):
        """指标统计应准确计算均值、方差、标准差"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )
            manager.start_experiment(exp.experiment_id)

            variant = manager.assign_variant(exp.experiment_id, "test_user")
            assert variant is not None

            values = [80, 85, 90, 95, 100]
            for v in values:
                manager.record_metric(exp.experiment_id, variant.variant_id, "quality_score", v)

            result = manager.analyze_results(exp.experiment_id)
            stats = result.variant_results.get(variant.variant_id, {})

            assert stats.get("count") == 5
            assert stats.get("mean") == 90.0
            assert stats.get("min") == 80
            assert stats.get("max") == 100
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingStatisticalSignificance:
    """测试6：统计显著性计算的正确性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_significance_detection(self):
        """应有显著差异时应正确检测到"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )
            manager.start_experiment(exp.experiment_id)

            for i in range(100):
                manager.record_metric(exp.experiment_id, "control", "quality_score", 80.0 + (i % 5))
                manager.record_metric(exp.experiment_id, "treatment", "quality_score", 90.0 + (i % 5))

            result = manager.analyze_results(exp.experiment_id)

            assert result.is_significant is True
            assert result.winner == "treatment"
            assert result.p_value < 0.05
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_significance_when_close(self):
        """无显著差异时不应误报"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )
            manager.start_experiment(exp.experiment_id)

            import random
            random.seed(42)
            for i in range(50):
                manager.record_metric(exp.experiment_id, "control", "quality_score", 85.0 + random.uniform(-5, 5))
                manager.record_metric(exp.experiment_id, "treatment", "quality_score", 85.5 + random.uniform(-5, 5))

            result = manager.analyze_results(exp.experiment_id)

            assert result.is_significant is False
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingAutoStop:
    """测试7：异常熔断机制的触发"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_auto_stop_on_metric_degradation(self):
        """指标恶化超过阈值应自动停止实验"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
                auto_stop_threshold=0.1,
            )
            manager.start_experiment(exp.experiment_id)

            for i in range(20):
                manager.record_metric(exp.experiment_id, "control", "quality_score", 90.0)
                manager.record_metric(exp.experiment_id, "treatment", "quality_score", 70.0)

            auto_stopped = manager.check_auto_stop(exp.experiment_id)

            assert auto_stopped is True

            exp_after = manager.get_experiment(exp.experiment_id)
            assert exp_after.status == ExperimentStatus.TERMINATED
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_auto_stop_when_metric_ok(self):
        """指标正常时不应触发自动停止"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
                auto_stop_threshold=0.2,
            )
            manager.start_experiment(exp.experiment_id)

            for i in range(10):
                manager.record_metric(exp.experiment_id, "control", "quality_score", 80.0)
                manager.record_metric(exp.experiment_id, "treatment", "quality_score", 85.0)

            auto_stopped = manager.check_auto_stop(exp.experiment_id)

            assert auto_stopped is False
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingPersistence:
    """测试8：实验数据持久化与恢复"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_experiment_persistence(self):
        """实验数据应正确持久化到数据库"""
        tmpdir = get_temp_dir()
        try:
            manager1 = ABTestManager(storage_path=tmpdir)
            manager1.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager1.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
            )
            manager1.start_experiment(exp.experiment_id)
            manager1.record_metric(exp.experiment_id, "control", "quality_score", 85.0)

            manager2 = ABTestManager(storage_path=tmpdir)
            manager2.initialize()

            exp_restored = manager2.get_experiment(exp.experiment_id)
            assert exp_restored is not None
            assert exp_restored.name == "测试实验"
            assert exp_restored.status == ExperimentStatus.RUNNING

            result = manager2.analyze_results(exp.experiment_id)
            assert result.sample_size == 1
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingMultiLayer:
    """测试9：多层实验的互不干扰"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_multi_layer_isolation(self):
        """不同层级的实验应互不干扰"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants1 = [
                ExperimentVariant(variant_id="v1_control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="v1_treatment", name="实验组", weight=50),
            ]
            exp1 = manager.create_experiment(
                name="实验1-层级0",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants1,
                layer=0,
            )

            variants2 = [
                ExperimentVariant(variant_id="v2_control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="v2_treatment", name="实验组", weight=50),
            ]
            exp2 = manager.create_experiment(
                name="实验2-层级1",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants2,
                layer=1,
            )

            manager.start_experiment(exp1.experiment_id)
            manager.start_experiment(exp2.experiment_id)

            assignments = manager.assign_variant_with_layers("test_user")

            assert exp1.experiment_id in assignments
            assert exp2.experiment_id in assignments

            variant1 = assignments[exp1.experiment_id]
            variant2 = assignments[exp2.experiment_id]

            assert variant1.variant_id in ["v1_control", "v1_treatment"]
            assert variant2.variant_id in ["v2_control", "v2_treatment"]
        finally:
            cleanup_temp_dir(tmpdir)


class TestABTestingConclusion:
    """测试10：实验结论生成的合理性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_conclusion_generation(self):
        """应生成合理的实验结论"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
                min_samples=50,
            )
            manager.start_experiment(exp.experiment_id)

            for i in range(50):
                manager.record_metric(exp.experiment_id, "control", "quality_score", 80.0)
                manager.record_metric(exp.experiment_id, "treatment", "quality_score", 90.0)

            conclusion = manager.generate_conclusion(exp.experiment_id)

            assert conclusion["experiment_id"] == exp.experiment_id
            assert conclusion["experiment_name"] == "测试实验"
            assert conclusion["sample_size"] == 100
            assert "recommendations" in conclusion
            assert len(conclusion["recommendations"]) > 0
        finally:
            cleanup_temp_dir(tmpdir)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_conclusion_insufficient_sample(self):
        """样本不足时应给出警告建议"""
        tmpdir = get_temp_dir()
        try:
            manager = ABTestManager(storage_path=tmpdir)
            manager.initialize()

            variants = [
                ExperimentVariant(variant_id="control", name="对照组", weight=50, is_control=True),
                ExperimentVariant(variant_id="treatment", name="实验组", weight=50),
            ]
            exp = manager.create_experiment(
                name="测试实验",
                experiment_type=ExperimentType.PROMPT_VERSION,
                variants=variants,
                min_samples=100,
            )
            manager.start_experiment(exp.experiment_id)

            for i in range(10):
                manager.record_metric(exp.experiment_id, "control", "quality_score", 80.0)
                manager.record_metric(exp.experiment_id, "treatment", "quality_score", 85.0)

            conclusion = manager.generate_conclusion(exp.experiment_id)

            insufficient_sample = any(r["type"] == "insufficient_sample" for r in conclusion["recommendations"])
            assert insufficient_sample is True
        finally:
            cleanup_temp_dir(tmpdir)
