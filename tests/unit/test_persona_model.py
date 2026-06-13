"""
PersonaModel 测试 - pytest 格式
针对增强版 PersonaModel 的测试用例
"""
import pytest
import tempfile
import json
from pathlib import Path

from persona.persona_model_enhanced import PersonaModel, PersonaSnapshot, PersonaLayer


class TestPersonaModelBasics:
    """测试 PersonaModel 基本功能"""

    @pytest.fixture
    def persona_model(self):
        """创建 PersonaModel 实例"""
        return PersonaModel()

    @pytest.mark.p0
    def test_persona_model_init(self, persona_model):
        """测试 PersonaModel 初始化"""
        assert persona_model is not None
        assert hasattr(persona_model, 'persona')
        assert hasattr(persona_model, 'layers')

    @pytest.mark.p0
    def test_persona_has_five_layers(self, persona_model):
        """测试人格有五层"""
        assert len(persona_model.layers) == 5
        assert 'layer0' in persona_model.layers
        assert 'layer1' in persona_model.layers
        assert 'layer2' in persona_model.layers
        assert 'layer3' in persona_model.layers
        assert 'layer4' in persona_model.layers

    @pytest.mark.p1
    def test_get_layer(self, persona_model):
        """测试获取人格层"""
        layer0 = persona_model.get_layer('layer0')
        assert layer0 is not None
        assert isinstance(layer0, PersonaLayer)
        assert layer0.layer_name == 'layer0'


class TestPersonaLayerAccess:
    """测试人格层访问"""

    @pytest.fixture
    def persona_model(self):
        return PersonaModel()

    @pytest.mark.p0
    def test_get_expression_style(self, persona_model):
        """测试获取表达风格"""
        style = persona_model.get_expression_style()
        assert isinstance(style, dict)
        assert 'tone' in style
        assert 'emotion' in style
        assert 'conciseness' in style
        assert 0.0 <= style['tone'] <= 1.0

    @pytest.mark.p0
    def test_get_hard_rules(self, persona_model):
        """测试获取硬性规则"""
        rules = persona_model.get_hard_rules()
        assert isinstance(rules, list)
        assert len(rules) > 0
        assert "保护用户数据安全" in rules[0] or "保护用户" in rules[0]

    @pytest.mark.p1
    def test_get_identity(self, persona_model):
        """测试获取身份信息"""
        identity = persona_model.get_identity()
        assert isinstance(identity, dict)
        assert 'identity' in identity
        assert 'background' in identity
        assert 'values' in identity


class TestPersonaUpdates:
    """测试人格更新"""

    @pytest.fixture
    def persona_model(self):
        return PersonaModel()

    @pytest.mark.p0
    def test_update_expression_style(self, persona_model):
        """测试更新表达风格"""
        original_tone = persona_model.get_expression_style()['tone']
        persona_model.update_expression_style(tone=0.8)
        new_tone = persona_model.get_expression_style()['tone']
        assert new_tone == 0.8
        assert new_tone != original_tone

    @pytest.mark.p1
    def test_update_respects_bounds(self, persona_model):
        """测试更新是否遵守边界"""
        persona_model.update_expression_style(tone=1.5)
        assert persona_model.get_expression_style()['tone'] == 1.0

        persona_model.update_expression_style(tone=-0.5)
        assert persona_model.get_expression_style()['tone'] == 0.0

    @pytest.mark.p1
    def test_record_interaction(self, persona_model):
        """测试记录交互"""
        initial_count = persona_model.persona["evolution"]["interactions"]
        persona_model.record_interaction()
        assert persona_model.persona["evolution"]["interactions"] == initial_count + 1


class TestPersonaSnapshot:
    """测试人格快照"""

    @pytest.fixture
    def persona_model(self):
        return PersonaModel()

    @pytest.mark.p0
    def test_take_snapshot(self, persona_model):
        """测试拍摄快照"""
        persona_model.update_expression_style(tone=0.9)
        snapshot = persona_model.take_snapshot("test_snapshot")
        assert isinstance(snapshot, PersonaSnapshot)
        assert snapshot.name == "test_snapshot"
        assert snapshot.timestamp is not None

    @pytest.mark.p1
    def test_snapshot_preserves_state(self, persona_model):
        """测试快照保留状态"""
        persona_model.update_expression_style(tone=0.9)
        snapshot = persona_model.take_snapshot("test_preserve")

        persona_model.update_expression_style(tone=0.1)

        snapshots_info = persona_model.get_snapshots()
        assert len(snapshots_info) >= 1
        assert any(s['name'] == 'test_preserve' for s in snapshots_info)

    @pytest.mark.p1
    def test_rollback_to_snapshot(self, persona_model):
        """测试回滚到快照"""
        original_tone = persona_model.get_expression_style()['tone']

        persona_model.update_expression_style(tone=0.9)
        persona_model.take_snapshot("rollback_test")

        persona_model.update_expression_style(tone=0.1)

        success = persona_model.rollback_to_snapshot("rollback_test")
        assert success is True


class TestPersonaSimilarity:
    """测试人格相似度计算"""

    @pytest.fixture
    def persona_model1(self):
        pm = PersonaModel()
        pm.update_expression_style(tone=0.8, emotion=0.6)
        return pm

    @pytest.fixture
    def persona_model2(self):
        pm = PersonaModel()
        pm.update_expression_style(tone=0.9, emotion=0.7)
        return pm

    @pytest.mark.p0
    def test_calculate_similarity(self, persona_model1, persona_model2):
        """测试相似度计算"""
        similarity = persona_model1.calculate_similarity(persona_model2)
        assert 0.0 <= similarity <= 1.0
        assert similarity > 0.8

    @pytest.mark.p1
    def test_identical_personas(self, persona_model1):
        """测试完全相同的人格"""
        pm2 = PersonaModel()
        pm2.update_expression_style(
            tone=persona_model1.get_expression_style()['tone'],
            emotion=persona_model1.get_expression_style()['emotion']
        )
        similarity = persona_model1.calculate_similarity(pm2)
        assert similarity > 0.95

    @pytest.mark.p1
    def test_different_personas(self, persona_model1):
        """测试完全不同的人格"""
        pm2 = PersonaModel()
        pm2.update_expression_style(tone=0.1, emotion=0.1)
        similarity = persona_model1.calculate_similarity(pm2)
        assert similarity >= 0.0
        assert similarity < 1.0


class TestPersonaConflicts:
    """测试人格冲突检测"""

    @pytest.fixture
    def persona_model1(self):
        pm = PersonaModel()
        pm.update_expression_style(tone=0.9, emotion=0.9)
        return pm

    @pytest.fixture
    def persona_model2(self):
        pm = PersonaModel()
        pm.update_expression_style(tone=0.1, emotion=0.1)
        return pm

    @pytest.mark.p0
    def test_detect_conflicts(self, persona_model1, persona_model2):
        """测试冲突检测"""
        conflicts = persona_model1.detect_conflicts(persona_model2)
        assert isinstance(conflicts, list)
        assert len(conflicts) > 0

    @pytest.mark.p1
    def test_no_conflicts_similar(self, persona_model1):
        """测试相似人格无冲突"""
        pm2 = PersonaModel()
        pm2.update_expression_style(tone=0.85, emotion=0.85)
        conflicts = persona_model1.detect_conflicts(pm2)
        assert len(conflicts) == 0 or all(c.get('severity', 1.0) <= 0.5 for c in conflicts)


class TestPersonaDrift:
    """测试人格漂移分析"""

    @pytest.fixture
    def persona_model(self):
        return PersonaModel()

    @pytest.mark.p0
    def test_analyze_drift(self, persona_model):
        """测试漂移分析"""
        baseline = PersonaModel()

        persona_model.update_expression_style(tone=0.9, emotion=0.8)

        drift_report = persona_model.analyze_drift(baseline)

        assert 'timestamp' in drift_report
        assert 'current_style' in drift_report
        assert 'baseline_style' in drift_report
        assert 'style_changes' in drift_report

    @pytest.mark.p1
    def test_drift_detects_changes(self, persona_model):
        """测试漂移检测变化"""
        baseline = PersonaModel()
        baseline_tone = baseline.get_expression_style()['tone']

        persona_model.update_expression_style(tone=baseline_tone + 0.2)

        drift_report = persona_model.analyze_drift(baseline)
        assert 'tone' in drift_report['style_changes']


class TestPersonaMerge:
    """测试人格合并"""

    @pytest.fixture
    def persona_model1(self):
        pm = PersonaModel()
        pm.update_expression_style(tone=0.8)
        return pm

    @pytest.fixture
    def persona_model2(self):
        pm = PersonaModel()
        pm.update_expression_style(tone=0.2)
        return pm

    @pytest.mark.p0
    def test_merge_personas(self, persona_model1, persona_model2):
        """测试人格合并"""
        success = persona_model1.merge_personas(persona_model2)
        assert success is True

    @pytest.mark.p1
    def test_merge_with_weights(self, persona_model1, persona_model2):
        """测试带权重合并"""
        persona_model1.update_expression_style(tone=0.8)
        persona_model2.update_expression_style(tone=0.2)

        persona_model1.merge_personas(persona_model2, weights={"self": 0.7, "other": 0.3})

        merged_tone = persona_model1.get_expression_style()['tone']
        expected_tone = 0.8 * 0.7 + 0.2 * 0.3
        assert abs(merged_tone - expected_tone) < 0.01


class TestPersonaSaveLoad:
    """测试人格保存和加载"""

    @pytest.fixture
    def temp_persona_file(self):
        """临时人格文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            temp_path = Path(f.name)
        yield temp_path
        if temp_path.exists():
            temp_path.unlink()

    @pytest.mark.p1
    def test_save_and_load_persona(self, temp_persona_file):
        """测试保存和加载人格"""
        pm1 = PersonaModel()
        pm1.update_expression_style(tone=0.85)
        pm1.persona["evolution"]["created_at"] = "2026-01-01"

        pm1.save_persona(str(temp_persona_file))

        pm2 = PersonaModel(persona_path=str(temp_persona_file))

        assert pm2.persona["evolution"]["created_at"] == "2026-01-01"


class TestSystemPrompt:
    """测试系统提示词生成"""

    @pytest.fixture
    def persona_model(self):
        return PersonaModel()

    @pytest.mark.p0
    def test_to_system_prompt(self, persona_model):
        """测试生成系统提示词"""
        prompt = persona_model.to_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "你的身份" in prompt
        assert "必须遵守的规则" in prompt
        assert "表达风格" in prompt

    @pytest.mark.p1
    def test_system_prompt_includes_identity(self, persona_model):
        """测试系统提示词包含身份"""
        prompt = persona_model.to_system_prompt()
        identity = persona_model.get_identity()
        assert identity['identity'] in prompt or "云枢" in prompt
