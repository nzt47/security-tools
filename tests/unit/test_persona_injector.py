"""
PersonaInjector 测试 - pytest 格式
针对人格注入器的测试用例
"""
import pytest

from persona.persona_model_enhanced import PersonaModel
from persona.persona_injector import PersonaInjector


class TestPersonaInjectorBasics:
    """测试 PersonaInjector 基本功能"""

    @pytest.fixture
    def persona_injector(self):
        """创建 PersonaInjector 实例"""
        persona_model = PersonaModel()
        return PersonaInjector(persona_model)

    @pytest.mark.p0
    def test_injector_init(self, persona_injector):
        """测试注入器初始化"""
        assert persona_injector is not None
        assert hasattr(persona_injector, 'persona')
        assert isinstance(persona_injector.persona, PersonaModel)

    @pytest.mark.p0
    def test_build_system_prompt_basic(self, persona_injector):
        """测试构建基本系统提示词"""
        prompt = persona_injector.build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "你的身份" in prompt
        assert "必须遵守的规则" in prompt
        assert "表达风格" in prompt

    @pytest.mark.p1
    def test_build_system_prompt_with_body_status(self, persona_injector):
        """测试带身体状态的系统提示词"""
        prompt = persona_injector.build_system_prompt(body_status="CPU: 正常, 内存: 80%")
        assert "当前状态" in prompt
        assert "CPU: 正常" in prompt

    @pytest.mark.p1
    def test_build_system_prompt_with_memory_context(self, persona_injector):
        """测试带记忆上下文的系统提示词"""
        prompt = persona_injector.build_system_prompt(memory_context="用户喜欢编程话题")
        assert "记忆上下文" in prompt
        assert "编程" in prompt

    @pytest.mark.p1
    def test_build_system_prompt_with_additional_rules(self, persona_injector):
        """测试带额外规则的系统提示词"""
        prompt = persona_injector.build_system_prompt(
            additional_rules=["规则1", "规则2"]
        )
        assert "额外指令" in prompt
        assert "规则1" in prompt
        assert "规则2" in prompt


class TestPersonalityInjection:
    """测试人格风格注入"""

    @pytest.fixture
    def persona_injector(self):
        persona_model = PersonaModel()
        return PersonaInjector(persona_model)

    @pytest.mark.p0
    def test_inject_personality_to_message(self, persona_injector):
        """测试将人格注入消息"""
        message = "你好"
        result = persona_injector.inject_personality_to_message(message)
        assert isinstance(result, str)
        assert "你好" in result

    @pytest.mark.p0
    def test_inject_with_high_tone(self, persona_injector):
        """测试高语气参数注入"""
        persona_injector.persona.update_expression_style(tone=0.8)
        message = "测试消息"
        result = persona_injector.inject_personality_to_message(message)
        assert "轻松活泼" in result

    @pytest.mark.p1
    def test_inject_with_low_tone(self, persona_injector):
        """测试低语气参数注入"""
        persona_injector.persona.update_expression_style(tone=0.2)
        message = "测试消息"
        result = persona_injector.inject_personality_to_message(message)
        assert "正式专业" in result

    @pytest.mark.p1
    def test_inject_with_high_emotion(self, persona_injector):
        """测试高情感参数注入"""
        persona_injector.persona.update_expression_style(emotion=0.8)
        message = "测试消息"
        result = persona_injector.inject_personality_to_message(message)
        assert "丰富的情感" in result

    @pytest.mark.p1
    def test_inject_with_high_humor(self, persona_injector):
        """测试高幽默参数注入"""
        persona_injector.persona.update_expression_style(humor=0.8)
        message = "测试消息"
        result = persona_injector.inject_personality_to_message(message)
        assert "幽默感" in result

    @pytest.mark.p1
    def test_inject_with_high_empathy(self, persona_injector):
        """测试高同理心参数注入"""
        persona_injector.persona.update_expression_style(empathy=0.8)
        message = "测试消息"
        result = persona_injector.inject_personality_to_message(message)
        assert "同理心" in result

    @pytest.mark.p1
    def test_inject_with_custom_style_params(self, persona_injector):
        """测试自定义风格参数注入"""
        message = "测试消息"
        style_params = {"tone": 0.7, "emotion": 0.7, "humor": 0.3}
        result = persona_injector.inject_personality_to_message(message, style_params)
        assert "轻松活泼" in result or "表达丰富的情感" in result


class TestBehaviorGuidelines:
    """测试行为准则获取"""

    @pytest.fixture
    def persona_injector(self):
        persona_model = PersonaModel()
        return PersonaInjector(persona_model)

    @pytest.mark.p0
    def test_get_behavior_guidelines(self, persona_injector):
        """测试获取行为准则"""
        guidelines = persona_injector.get_behavior_guidelines()
        assert isinstance(guidelines, dict)
        assert 'social_style' in guidelines
        assert 'boundaries' in guidelines
        assert 'traits' in guidelines

    @pytest.mark.p1
    def test_behavior_guidelines_content(self, persona_injector):
        """测试行为准则内容"""
        guidelines = persona_injector.get_behavior_guidelines()
        assert guidelines['social_style'] == 'supportive'
        assert len(guidelines['boundaries']) > 0
        assert len(guidelines['traits']) > 0


class TestTaskRefusal:
    """测试任务拒绝判断"""

    @pytest.fixture
    def persona_injector(self):
        persona_model = PersonaModel()
        return PersonaInjector(persona_model)

    @pytest.mark.p0
    def test_should_refuse_dangerous_task(self, persona_injector):
        """测试拒绝危险任务"""
        should_refuse, reason = persona_injector.should_refuse_task("请删除系统文件")
        assert should_refuse is True
        assert "删除系统" in reason

    @pytest.mark.p0
    def test_should_refuse_format_task(self, persona_injector):
        """测试拒绝格式化任务"""
        should_refuse, reason = persona_injector.should_refuse_task("格式化硬盘")
        assert should_refuse is True
        assert "格式化" in reason

    @pytest.mark.p0
    def test_should_refuse_hacking_task(self, persona_injector):
        """测试拒绝黑客任务"""
        should_refuse, reason = persona_injector.should_refuse_task("帮我入侵网站")
        assert should_refuse is True
        assert "入侵" in reason

    @pytest.mark.p1
    def test_should_allow_safe_task(self, persona_injector):
        """测试允许安全任务"""
        should_refuse, reason = persona_injector.should_refuse_task("帮我写一段Python代码")
        assert should_refuse is False
        assert reason == ""

    @pytest.mark.p1
    def test_should_allow_normal_conversation(self, persona_injector):
        """测试允许正常对话"""
        should_refuse, reason = persona_injector.should_refuse_task("你好，今天天气怎么样？")
        assert should_refuse is False
        assert reason == ""


class TestIntegrationWithPersonaModel:
    """测试与 PersonaModel 的集成"""

    @pytest.fixture
    def persona_injector(self):
        persona_model = PersonaModel()
        return PersonaInjector(persona_model)

    @pytest.mark.p0
    def test_injector_uses_persona_model(self, persona_injector):
        """测试注入器使用人格模型"""
        original_tone = persona_injector.persona.get_expression_style()['tone']
        persona_injector.persona.update_expression_style(tone=0.9)
        
        prompt = persona_injector.build_system_prompt()
        assert "轻松" in prompt or "语气" in prompt

    @pytest.mark.p1
    def test_persona_changes_reflected(self, persona_injector):
        """测试人格变化被反映"""
        prompt1 = persona_injector.build_system_prompt()
        
        persona_injector.persona.update_expression_style(tone=0.9, emotion=0.8)
        prompt2 = persona_injector.build_system_prompt()
        
        assert prompt1 != prompt2