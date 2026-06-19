"""
Core Abstraction Package
Phase 3 - Architecture Optimization

统一的核心抽象层，消除重复代码

当前保留模块：
- registry: 被 planning/executor.py 使用
"""

from .registry import (
    BaseRegistry,
    SimpleRegistry,
    CallbackRegistry,
    TypeRegistry,
    register
)

__all__ = [
    # Registry
    'BaseRegistry',
    'SimpleRegistry',
    'CallbackRegistry',
    'TypeRegistry',
    'register',
]

__version__ = '0.1.0'
