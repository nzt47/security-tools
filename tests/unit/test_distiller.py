"""
PersonaDistiller 测试 - pytest 格式
"""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

from persona.distiller import (
    PersonaDistiller,
    DistillationStrategy,
    DistillationConfig,
    DistillationResult
)


class TestPersonaDistillerBasics:
    """测试 PersonaDistiller 基本功能"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p0
    def test_distiller_init(self, distiller):
        """测试蒸馏器初始化"""
        assert distiller is not None
        assert distiller.persona_model is not None
        assert distiller.config is not None
        assert distiller.config.strategy == DistillationStrategy.BALANCED

    @pytest.mark.p0
    def test_default_config(self):
        """测试默认配置"""
        config = DistillationConfig()
        assert config.strategy == DistillationStrategy.BALANCED
        assert config.learning_rate == 0.1
        assert config.min_confidence == 0.3
        assert config.time_decay_factor == 0.95

    @pytest.mark.p1
    def test_custom_config(self):
        """测试自定义配置"""
        config = DistillationConfig(
            strategy=DistillationStrategy.AGGRESSIVE,
            learning_rate=0.2,
            min_confidence=0.5
        )
        distiller = PersonaDistiller(config=config)
        
        assert distiller.config.strategy == DistillationStrategy.AGGRESSIVE
        assert distiller.config.learning_rate == 0.2


class TestDistillationStrategies:
    """测试蒸馏策略"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p0
    def test_balanced_strategy(self, distiller):
        """测试平衡策略"""
        preferences = {
            "expression_style": {
                "tone": 0.7,
                "emotion": 0.4,
                "conciseness": 0.6
            }
        }
        
        result = distiller.distill_from_preferences(
            preferences, 
            strategy=DistillationStrategy.BALANCED
        )
        
        assert isinstance(result, DistillationResult)
        assert result.success
        assert result.strategy_used == "balanced"

    @pytest.mark.p0
    def test_conservative_strategy(self, distiller):
        """测试保守策略"""
        preferences = {
            "expression_style": {
                "tone": 0.9,
                "emotion": 0.1
            }
        }
        
        result = distiller.distill_from_preferences(
            preferences,
            strategy=DistillationStrategy.CONSERVATIVE
        )
        
        assert result.success
        assert result.strategy_used == "conservative"

    @pytest.mark.p1
    def test_aggressive_strategy(self, distiller):
        """测试激进策略"""
        preferences = {
            "expression_style": {
                "tone": 0.2,
                "emotion": 0.8
            }
        }
        
        result = distiller.distill_from_preferences(
            preferences,
            strategy=DistillationStrategy.AGGRESSIVE
        )
        
        assert result.success
        assert result.strategy_used == "aggressive"


class TestPersonaFusion:
    """测试人格融合"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        from persona.persona_model_enhanced import PersonaModel
        return PersonaDistiller()

    @pytest.mark.p0
    def test_merge_two_personas(self, distiller):
        """测试融合两个人格"""
        persona1 = distiller.persona_model.persona.copy()
        persona2 = distiller.persona_model.persona.copy()
        
        persona1["layers"]["layer2"]["tone"] = 0.9
        persona2["layers"]["layer2"]["tone"] = 0.1
        
        merged = distiller.merge_personas([persona1, persona2])
        
        assert isinstance(merged, dict)
        assert "layers" in merged
        assert "layer2" in merged["layers"]

    @pytest.mark.p0
    def test_merge_with_weights(self, distiller):
        """测试带权重的人格融合"""
        persona1 = distiller.persona_model.persona.copy()
        persona2 = distiller.persona_model.persona.copy()
        
        persona1["layers"]["layer2"]["tone"] = 0.8
        persona2["layers"]["layer2"]["tone"] = 0.2
        
        merged = distiller.merge_personas([persona1, persona2], weights=[0.7, 0.3])
        
        assert isinstance(merged, dict)

    @pytest.mark.p1
    def test_merge_empty_personas(self, distiller):
        """测试空人格列表"""
        original = distiller.persona_model.persona.copy()
        merged = distiller.merge_personas([])
        
        # 应该返回原始人格
        assert "layers" in merged


class TestDistillationEvaluation:
    """测试蒸馏评估"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p0
    def test_evaluation_score(self, distiller):
        """测试评估分数"""
        preferences = {
            "expression_style": {
                "tone": 0.6,
                "emotion": 0.4
            }
        }
        
        result = distiller.distill_from_preferences(preferences)
        
        assert 0 <= result.evaluation_score <= 1
        assert result.evaluation_score > 0

    @pytest.mark.p0
    def test_changes_made(self, distiller):
        """测试变更记录"""
        preferences = {
            "expression_style": {
                "tone": 0.8,
                "emotion": 0.2
            }
        }
        
        result = distiller.distill_from_preferences(preferences)
        
        assert isinstance(result.changes_made, list)

    @pytest.mark.p1
    def test_timestamp_generation(self, distiller):
        """测试时间戳生成"""
        preferences = {"expression_style": {"tone": 0.5}}
        result = distiller.distill_from_preferences(preferences)
        
        assert isinstance(result.timestamp, str)
        assert len(result.timestamp) > 0


class TestSnapshotManagement:
    """测试快照管理"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p0
    def test_snapshot_creation(self, distiller):
        """测试创建快照"""
        preferences = {"expression_style": {"tone": 0.7}}
        result = distiller.distill_from_preferences(preferences)
        
        assert len(distiller.snapshots) >= 1

    @pytest.mark.p0
    def test_snapshot_rollback(self, distiller):
        """测试快照回滚"""
        preferences1 = {"expression_style": {"tone": 0.9}}
        result1 = distiller.distill_from_preferences(preferences1)
        
        snapshot_name = distiller.snapshots[-1]["name"]
        
        preferences2 = {"expression_style": {"tone": 0.1}}
        result2 = distiller.distill_from_preferences(preferences2)
        
        success = distiller.rollback_to_snapshot(snapshot_name)
        
        assert success is True

    @pytest.mark.p1
    def test_nonexistent_snapshot_rollback(self, distiller):
        """测试回滚不存在的快照"""
        success = distiller.rollback_to_snapshot("nonexistent_snapshot")
        
        assert success is False

    @pytest.mark.p1
    def test_snapshot_limit(self, distiller):
        """测试快照数量限制"""
        config = DistillationConfig(max_history_snapshots=5)
        limited_distiller = PersonaDistiller(config=config)
        
        # 创建超过限制的快照
        for i in range(10):
            preferences = {"expression_style": {"tone": i / 10}}
            limited_distiller.distill_from_preferences(preferences)
        
        assert len(limited_distiller.snapshots) <= 5


class TestAutoTuning:
    """测试自动调参"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p0
    def test_auto_tune_positive_feedback(self, distiller):
        """测试正反馈调参"""
        original_rate = distiller.config.learning_rate
        distiller.auto_tune(0.9)
        
        # 正反馈应该提升学习率或保持不变
        assert distiller.config.learning_rate >= original_rate * 0.5

    @pytest.mark.p0
    def test_auto_tune_negative_feedback(self, distiller):
        """测试负反馈调参"""
        original_rate = distiller.config.learning_rate
        distiller.auto_tune(0.1)
        
        # 负反馈应该降低学习率或保持不变
        assert distiller.config.learning_rate <= original_rate * 1.5

    @pytest.mark.p1
    def test_auto_tune_strategy_switch(self, distiller):
        """测试策略切换"""
        distiller.auto_tune(0.1)
        assert distiller.config.strategy in [
            DistillationStrategy.CONSERVATIVE,
            DistillationStrategy.BALANCED,
            DistillationStrategy.AGGRESSIVE
        ]


class TestEvaluationReport:
    """测试评估报告"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p0
    def test_evaluation_report_generation(self, distiller):
        """测试生成评估报告"""
        preferences = {"expression_style": {"tone": 0.6}}
        distiller.distill_from_preferences(preferences)
        
        report = distiller.get_evaluation_report()
        
        assert isinstance(report, dict)
        assert "timestamp" in report
        assert "metrics" in report
        assert "current_strategy" in report

    @pytest.mark.p1
    def test_metrics_update(self, distiller):
        """测试指标更新"""
        distiller.get_evaluation_report()
        
        initial_count = distiller.evaluation_metrics["total_distillations"]
        
        preferences = {"expression_style": {"tone": 0.5}}
        distiller.distill_from_preferences(preferences)
        
        assert distiller.evaluation_metrics["total_distillations"] == initial_count + 1


class TestDistillationIntegration:
    """测试集成功能"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p1
    def test_full_distillation_workflow(self, distiller):
        """测试完整蒸馏工作流"""
        # 1. 第一次蒸馏
        preferences1 = {
            "expression_style": {"tone": 0.7, "emotion": 0.3},
            "topic_interest": {"编程": 0.8}
        }
        result1 = distiller.distill_from_preferences(preferences1)
        
        assert result1.success
        
        # 2. 第二次蒸馏
        preferences2 = {
            "expression_style": {"tone": 0.6, "emotion": 0.4},
            "topic_interest": {"学习": 0.7}
        }
        result2 = distiller.distill_from_preferences(preferences2)
        
        assert result2.success
        
        # 3. 获取报告
        report = distiller.get_evaluation_report()
        
        assert report["metrics"]["total_distillations"] >= 2

    @pytest.mark.p1
    def test_distill_and_rollback_workflow(self, distiller):
        """测试蒸馏和回滚工作流"""
        # 初始状态
        initial_style = distiller.persona_model.get_expression_style()["tone"]
        
        # 蒸馏
        preferences = {"expression_style": {"tone": 0.9}}
        result = distiller.distill_from_preferences(preferences)
        
        # 回滚
        if distiller.snapshots:
            snapshot_name = distiller.snapshots[-1]["name"]
            success = distiller.rollback_to_snapshot(snapshot_name)
            assert success


class TestEdgeCases:
    """测试边界情况"""

    @pytest.fixture
    def distiller(self):
        """创建 PersonaDistiller 实例"""
        return PersonaDistiller()

    @pytest.mark.p1
    def test_empty_preferences(self, distiller):
        """测试空偏好数据"""
        result = distiller.distill_from_preferences({})
        
        # 空偏好应该也能成功
        assert result.success
        assert len(result.changes_made) == 0

    @pytest.mark.p1
    def test_invalid_weights_merge(self, distiller):
        """测试权重不匹配的情况"""
        persona1 = distiller.persona_model.persona.copy()
        persona2 = distiller.persona_model.persona.copy()
        
        with pytest.raises(ValueError):
            distiller.merge_personas([persona1, persona2], weights=[1.0])

    @pytest.mark.p1
    def test_extreme_values(self, distiller):
        """测试极端值"""
        preferences = {
            "expression_style": {
                "tone": 1.0,
                "emotion": 0.0,
                "conciseness": 1.0
            }
        }
        
        result = distiller.distill_from_preferences(preferences)
        
        assert result.success
