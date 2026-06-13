"""
PersonalityPreferenceExtractor 测试 - pytest 格式
针对增强版 PersonalityPreferenceExtractor 的测试用例
"""
import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timedelta

from persona.distillation_enhanced import PersonalityPreferenceExtractor


class TestPreferenceExtractorBasics:
    """测试 PreferenceExtractor 基本功能"""

    @pytest.fixture
    def extractor(self):
        """创建 PersonalityPreferenceExtractor 实例"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir, learning_rate=0.1)

    @pytest.mark.p0
    def test_extractor_init(self, extractor):
        """测试提取器初始化"""
        assert extractor is not None
        assert hasattr(extractor, 'preferences')
        assert hasattr(extractor, 'learning_rate')
        assert extractor.learning_rate == 0.1

    @pytest.mark.p0
    def test_default_preferences_structure(self, extractor):
        """测试默认偏好结构"""
        assert 'expression_style' in extractor.preferences
        assert 'topic_interest' in extractor.preferences
        assert 'interaction_pattern' in extractor.preferences
        assert 'tool_preference' in extractor.preferences

    @pytest.mark.p1
    def test_enhanced_preferences_structure(self, extractor):
        """测试增强版偏好结构"""
        assert 'emotional_tendency' in extractor.preferences
        assert 'satisfaction_indicators' in extractor.preferences
        assert 'interaction_rhythm' in extractor.preferences
        assert 'confidence' in extractor.preferences


class TestExpressionStyleExtraction:
    """测试表达风格提取"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_extract_casual_style(self, extractor):
        """测试提取随意风格"""
        conversation = [
            {"role": "user", "content": "哈哈，这个太有趣了！", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        style = extractor.preferences['expression_style']
        assert style['casual'] > 0.5
        assert style['humorous'] > 0.3

    @pytest.mark.p0
    def test_extract_formal_style(self, extractor):
        """测试提取正式风格"""
        conversation = [
            {"role": "user", "content": "您好，请问能否请您帮我分析这个问题？非常感谢！", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        style = extractor.preferences['expression_style']
        assert style['formal'] > style['casual']

    @pytest.mark.p1
    def test_extract_concise_style(self, extractor):
        """测试提取简洁风格"""
        conversation = [
            {"role": "user", "content": "好", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "对", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "是", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        style = extractor.preferences['expression_style']
        assert style['concise'] > 0.5


class TestTopicInterestExtraction:
    """测试话题兴趣提取"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_extract_programming_topic(self, extractor):
        """测试提取编程话题"""
        conversation = [
            {"role": "user", "content": "我想学习Python编程，有什么好的建议吗？", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "这个bug怎么调试？", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        topic_interest = extractor.preferences['topic_interest']
        assert '编程' in topic_interest
        assert topic_interest['编程'] > 0.3

    @pytest.mark.p0
    def test_extract_multiple_topics(self, extractor):
        """测试提取多个话题"""
        conversation = [
            {"role": "user", "content": "我想学习Python编程", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "最近有什么好电影推荐吗？", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        topic_interest = extractor.preferences['topic_interest']
        assert len(topic_interest) >= 2


class TestEmotionalTendency:
    """测试情感倾向提取（新增功能）"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_extract_positive_emotion(self, extractor):
        """测试提取积极情感"""
        conversation = [
            {"role": "user", "content": "太棒了！非常感谢你的帮助！", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        emotion = extractor.preferences['emotional_tendency']
        assert 'positive' in emotion
        assert 'negative' in emotion
        assert 'neutral' in emotion

    @pytest.mark.p1
    def test_emotional_tendency_normalized(self, extractor):
        """测试情感倾向归一化"""
        conversation = [
            {"role": "user", "content": "哈哈，开心", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "难过", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        emotion = extractor.preferences['emotional_tendency']
        total = emotion['positive'] + emotion['negative'] + emotion['neutral']
        assert abs(total - 1.0) < 0.01 or total > 0


class TestInteractionRhythm:
    """测试交互节奏提取（新增功能）"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_extract_rhythm_basic(self, extractor):
        """测试基本节奏提取"""
        now = datetime.now()
        conversation = [
            {"role": "user", "content": "你好", "timestamp": now.isoformat()},
            {"role": "user", "content": "继续", "timestamp": (now + timedelta(seconds=30)).isoformat()},
            {"role": "user", "content": "还有吗", "timestamp": (now + timedelta(seconds=60)).isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        rhythm = extractor.preferences['interaction_rhythm']
        assert 'avg_response_time' in rhythm
        assert 'message_frequency' in rhythm
        assert 'burst_indicator' in rhythm

    @pytest.mark.p1
    def test_rhythm_burst_detection(self, extractor):
        """测试突发检测"""
        now = datetime.now()
        conversation = [
            {"role": "user", "content": "第1条", "timestamp": now.isoformat()},
            {"role": "user", "content": "第2条", "timestamp": (now + timedelta(seconds=10)).isoformat()},
            {"role": "user", "content": "第3条", "timestamp": (now + timedelta(seconds=20)).isoformat()},
            {"role": "user", "content": "第4条", "timestamp": (now + timedelta(seconds=300)).isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        rhythm = extractor.preferences['interaction_rhythm']
        assert rhythm['burst_indicator'] > 0


class TestSatisfactionInference:
    """测试满意度推断（新增功能）"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_infer_satisfaction_indicators(self, extractor):
        """测试推断满意度指标"""
        conversation = [
            {"role": "user", "content": "好的，谢谢！", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "明白了，还有吗？", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        indicators = extractor.preferences['satisfaction_indicators']
        assert 'positive_acknowledgments' in indicators
        assert 'follow_up_questions' in indicators

    @pytest.mark.p1
    def test_satisfaction_score_exists(self, extractor):
        """测试满意度分数存在"""
        conversation = [
            {"role": "user", "content": "好的，谢谢！", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        indicators = extractor.preferences['satisfaction_indicators']
        assert 'score' in indicators
        assert 0.0 <= indicators['score'] <= 1.0


class TestConfidenceUpdate:
    """测试置信度更新（新增功能）"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_confidence_structure(self, extractor):
        """测试置信度结构"""
        assert 'confidence' in extractor.preferences
        assert isinstance(extractor.preferences['confidence'], dict)

    @pytest.mark.p1
    def test_confidence_after_extraction(self, extractor):
        """测试提取后置信度更新"""
        conversation = [
            {"role": "user", "content": "测试消息", "timestamp": datetime.now().isoformat()},
        ] * 10

        extractor.extract_from_conversation(conversation)

        confidence = extractor.preferences['confidence']
        assert confidence['overall'] > 0

    @pytest.mark.p1
    def test_is_confidence_sufficient(self, extractor):
        """测试置信度是否足够"""
        conversation = [
            {"role": "user", "content": "测试", "timestamp": datetime.now().isoformat()},
        ] * 5

        extractor.extract_from_conversation(conversation)

        result = extractor.is_confidence_sufficient('overall')
        assert isinstance(result, bool)


class TestIncrementalUpdate:
    """测试增量更新"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_incremental_update(self, extractor):
        """测试增量更新"""
        message = {
            "role": "user",
            "content": "我想学习Python编程",
            "timestamp": datetime.now().isoformat()
        }

        result = extractor.update_incremental(message)

        assert isinstance(result, dict)
        assert 'topic_interest' in result

    @pytest.mark.p1
    def test_batch_update(self, extractor):
        """测试批量更新"""
        messages = [
            {"role": "user", "content": "消息1", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "消息2", "timestamp": datetime.now().isoformat()},
        ] * 5

        for msg in messages:
            extractor.update_incremental(msg)

        assert len(extractor._message_buffer) == 0


class TestDecayMechanism:
    """测试衰减机制（新增功能）"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_apply_decay(self, extractor):
        """测试应用衰减"""
        conversation = [
            {"role": "user", "content": "编程", "timestamp": datetime.now().isoformat()},
        ] * 5
        extractor.extract_from_conversation(conversation)

        original_value = extractor.preferences['topic_interest'].get('编程', 0.0)

        extractor.apply_decay(decay_factor=0.5)

        new_value = extractor.preferences['topic_interest'].get('编程', 0.0)
        assert new_value < original_value

    @pytest.mark.p1
    def test_decay_factor_configurable(self, extractor):
        """测试衰减因子可配置"""
        extractor.apply_decay(decay_factor=0.8)
        assert extractor.preferences['decay_factor'] == 0.8


class TestPersonalityPromptGeneration:
    """测试人格提示词生成"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_generate_prompt_basic(self, extractor):
        """测试基本提示词生成"""
        conversation = [
            {"role": "user", "content": "哈哈，太有趣了！", "timestamp": datetime.now().isoformat()},
            {"role": "user", "content": "我想学习Python编程", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        prompt = extractor.generate_personality_prompt()

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "你应该" in prompt or "自然" in prompt

    @pytest.mark.p1
    def test_generate_prompt_with_topics(self, extractor):
        """测试带话题的提示词生成"""
        conversation = [
            {"role": "user", "content": "我想学习Python编程" * 10, "timestamp": datetime.now().isoformat()},
        ] * 5
        extractor.extract_from_conversation(conversation)

        prompt = extractor.generate_personality_prompt()

        assert "编程" in prompt or "Python" in prompt or "感兴趣" in prompt


class TestExportImport:
    """测试导出和持久化"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_export_preferences(self, extractor):
        """测试导出偏好"""
        exported = extractor.export_preferences()

        assert isinstance(exported, dict)
        assert 'extracted_at' in exported
        assert 'preferences' in exported

    @pytest.mark.p1
    def test_save_and_load(self, extractor):
        """测试保存和加载"""
        conversation = [
            {"role": "user", "content": "测试", "timestamp": datetime.now().isoformat()},
        ]
        extractor.extract_from_conversation(conversation)

        from persona.distillation_enhanced import PersonalityPreferenceExtractor

        new_extractor = PersonalityPreferenceExtractor(data_dir=str(extractor.data_dir))

        assert new_extractor.preferences.get('last_updated') is not None


class TestAdaptiveLearning:
    """测试自适应学习"""

    @pytest.fixture
    def extractor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield PersonalityPreferenceExtractor(data_dir=tmpdir)

    @pytest.mark.p0
    def test_adaptive_learning_enabled(self, extractor):
        """测试自适应学习已启用"""
        assert extractor.adaptive_learning is True

    @pytest.mark.p1
    def test_custom_learning_rate(self):
        """测试自定义学习率"""
        with tempfile.TemporaryDirectory() as tmpdir:
            extractor = PersonalityPreferenceExtractor(
                data_dir=tmpdir,
                learning_rate=0.05
            )

            assert extractor.learning_rate == 0.05
