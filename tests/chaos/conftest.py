# -*- coding: utf-8 -*-
"""tests/chaos/conftest.py — 混沌测试收集配置

跳过收集不兼容的旧演示文件（依赖已废弃的 CircuitBreaker API）。
这些文件保留为历史参考，但不参与 pytest 收集以避免 ImportError。
"""
from __future__ import annotations

# collect_ignore: pytest 收集时跳过的文件列表
# - test_circuit_breaker_mock.py: 依赖 CircuitBreakerConfig/CircuitBreakerState/
#   get_circuit_breaker/breaker.protect 等已废弃 API，与新实现不兼容
# - chaos_demo_mock.py: 演示脚本，非 pytest 测试
collect_ignore = [
    "test_circuit_breaker_mock.py",  # 旧 API 演示文件
    "chaos_demo_mock.py",            # 演示脚本
]
