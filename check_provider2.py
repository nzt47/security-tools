#!/usr/bin/env python3
"""检查 OpenTelemetry provider 结构"""

import sys
sys.path.insert(0, '.')

from opentelemetry import trace as ot_trace

from agent.monitoring.tracing import _init_opentelemetry
_init_opentelemetry()

provider = ot_trace.get_tracer_provider()
print('Provider type:', type(provider).__name__)
print()
print('All attributes:')
for attr in dir(provider):
    if not attr.startswith('__'):
        print('  -', attr)