#!/usr/bin/env python3

content = '''"""Persona 单元测试 - 人格系统测试"""
import pytest
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_persona")

class PersonalityTraits:
    def __init__(self, openness=50, conscientiousness=50, extraversion=50, agreeableness=50, neuroticism=50):
        self.openness = openness
        self.conscientiousness = conscientiousness
        self.extraversion = extraversion
        self.agreeableness = agreeableness
        self.neuroticism = neuroticism

class EmotionState:
    def __init__(self):
        self.happiness = 50
        self.energy = 50
        self.stress = 50
        self.focus = 50

class Persona:
    def __init__(self, name="云枢", traits=None):
        self.name = name
        self.traits = traits or PersonalityTraits()
        self.emotion = EmotionState()

    def set_trait(self, trait_name, value):
        if hasattr(self.traits, trait_name):
            setattr(self.traits, trait_name, max(0, min(100, value)))
            return True
        return False

    def get_trait(self, trait_name):
        return getattr(self.traits, trait_name, None)

    def update_emotion(self, emotion_type, delta):
        if hasattr(self.emotion, emotion_type):
            current = getattr(self.emotion, emotion_type)
            new_value = max(0, min(100, current + delta))
            setattr(self.emotion, emotion_type, new_value)
            return new_value
        return None

    def get_emotion(self, emotion_type):
        return getattr(self.emotion, emotion_type, None)

    def decide_behavior(self, context):
        decisions = []
        if self.traits.extraversion > 70:
            decisions.append("主动交流")
        if self.traits.conscientiousness > 70:
            decisions.append("认真负责")
        if self.traits.openness > 60:
            decisions.append("乐于创新")
        return decisions

    def generate_response_style(self):
        style = "normal"
        if self.emotion.happiness > 70:
            style = "friendly"
        elif self.emotion.happiness < 30:
            style = "serious"
        if self.traits.neuroticism > 70:
            style = "cautious"
        return style

def test_persona_initialization():
    """测试人格初始化"""
    logger.info("测试: 人格初始化")
    persona = Persona("测试人格")
    assert persona.name == "测试人格"
    assert persona.traits.openness == 50
    assert persona.emotion.happiness == 50

def test_personality_traits_setting():
    """测试人格特征设置"""
    logger.info("测试: 人格特征设置")
    persona = Persona()
    success = persona.set_trait("extraversion", 80)
    assert success is True
    assert persona.get_trait("extraversion") == 80
    persona.set_trait("openness", 150)
    assert persona.get_trait("openness") == 100
    persona.set_trait("conscientiousness", -10)
    assert persona.get_trait("conscientiousness") == 0

def test_invalid_trait_setting():
    """测试无效特征设置"""
    logger.info("测试: 无效特征设置")
    persona = Persona()
    success = persona.set_trait("invalid_trait", 50)
    assert success is False

def test_emotion_update():
    """测试情绪更新"""
    logger.info("测试: 情绪更新")
    persona = Persona()
    result = persona.update_emotion("happiness", 30)
    assert result == 80
    result = persona.update_emotion("happiness", -40)
    assert result == 40
    result = persona.update_emotion("energy", 60)
    assert result == 100
    result = persona.update_emotion("stress", -60)
    assert result == 0

def test_behavior_decision():
    """测试行为决策"""
    logger.info("测试: 行为决策")
    extravert = Persona()
    extravert.set_trait("extraversion", 80)
    extravert.set_trait("conscientiousness", 80)
    decisions = extravert.decide_behavior({"context": "社交"})
    assert "主动交流" in decisions
    assert "认真负责" in decisions

def test_response_style():
    """测试响应风格生成"""
    logger.info("测试: 响应风格生成")
    happy_persona = Persona()
    happy_persona.update_emotion("happiness", 30)
    style = happy_persona.generate_response_style()
    assert style == "friendly"

def test_persona_clone():
    """测试人格克隆"""
    logger.info("测试: 人格克隆")
    original = Persona("Original")
    original.set_trait("extraversion", 70)
    clone = Persona(original.name)
    clone.traits = PersonalityTraits(extraversion=original.traits.extraversion)
    assert clone.get_trait("extraversion") == 70
'''

with open('agent/tests/test_persona.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Created: agent/tests/test_persona.py')
