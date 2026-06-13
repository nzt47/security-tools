"""
Core Abstraction Package
Phase 3 - Architecture Optimization

统一的核心抽象层，消除重复代码
"""
from .storage import (
    BaseStorage,
    JSONFileStorage,
    InMemoryStorage,
    StorableItem,
    create_storage
)

from .registry import (
    BaseRegistry,
    SimpleRegistry,
    CallbackRegistry,
    TypeRegistry,
    register
)

from .config import Config

from .logging import (
    log_section,
    log_operation,
    setup_logger,
    ProgressLogger
)

__all__ = [
    # Storage
    'BaseStorage',
    'JSONFileStorage',
    'InMemoryStorage',
    'StorableItem',
    'create_storage',
    
    # Registry
    'BaseRegistry',
    'SimpleRegistry',
    'CallbackRegistry',
    'TypeRegistry',
    'register',
    
    # Config
    'Config',
    
    # Logging
    'log_section',
    'log_operation',
    'setup_logger',
    'ProgressLogger',
]

__version__ = '0.1.0'
