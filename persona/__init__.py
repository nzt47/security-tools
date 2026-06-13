
"""
云枢 Persona 人格系统
参考 immortal-skill 的五层人格架构
"""

__version__ = "2.0.0"

from .persona_model import PersonaModel
from .persona_injector import PersonaInjector
from .distillation import PersonalityPreferenceExtractor

__all__ = [
    "PersonaModel",
    "PersonaInjector",
    "PersonalityPreferenceExtractor",
]

