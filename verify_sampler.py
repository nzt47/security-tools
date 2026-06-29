#!/usr/bin/env python3
"""
验证 OpenTelemetry 采样器配置
"""

import logging
logging.basicConfig(level=logging.DEBUG)

import sys
sys.path.insert(0, '.')

from agent.monitoring.tracing import _init_opentelemetry, diagnose_opentelemetry_config

_init_opentelemetry()
diagnosis = diagnose_opentelemetry_config()

print('\n' + '='*60)
print('📊 采样器配置诊断结果')
print('='*60)
print(f"OpenTelemetry 可用: {diagnosis['opentelemetry_available']}")
print(f"Tracer 已初始化: {diagnosis['tracer_initialized']}")
print(f"采样器信息: {diagnosis['sampler_info']}")
print('\n💡 诊断结论:')
for msg in diagnosis['diagnosis']:
    print(f'   {msg}')