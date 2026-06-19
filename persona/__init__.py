"""
云枢 Persona 人格系统
参考 immortal-skill 的五层人格架构
"""

__version__ = "2.0.0"

from .persona_model_enhanced import PersonaModel, PersonaLayer, PersonaSnapshot
from .persona_injector import PersonaInjector
from .distillation_enhanced import (
    PersonalityPreferenceExtractor,
    PersonaDistiller,
    DistillationStrategy,
    DistillationConfig,
    DistillationResult,
)

__all__ = [
    "PersonaModel",
    "PersonaLayer",
    "PersonaSnapshot",
    "PersonaInjector",
    "PersonalityPreferenceExtractor",
    "PersonaDistiller",
    "DistillationStrategy",
    "DistillationConfig",
    "DistillationResult",
]

