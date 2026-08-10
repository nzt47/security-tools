"""Persona 单元测试"""
import pytest

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

    def decide_behavior(self, context):
        decisions = []
        if self.traits.extraversion > 70:
            decisions.append("主动交流")
        if self.traits.conscientiousness > 70:
            decisions.append("认真负责")
        return decisions

    def generate_response_style(self):
        style = "normal"
        if self.emotion.happiness > 70:
            style = "friendly"
        return style

def test_persona_initialization():
    persona = Persona("测试人格")
    assert persona.name == "测试人格"

def test_personality_traits_setting():
    persona = Persona()
    persona.set_trait("extraversion", 80)
    assert persona.get_trait("extraversion") == 80

def test_emotion_update():
    persona = Persona()
    result = persona.update_emotion("happiness", 30)
    assert result == 80

def test_behavior_decision():
    extravert = Persona()
    extravert.set_trait("extraversion", 80)
    decisions = extravert.decide_behavior({"context": "社交"})
    assert "主动交流" in decisions

def test_response_style():
    happy = Persona()
    happy.update_emotion("happiness", 30)
    style = happy.generate_response_style()
    assert style == "friendly"
