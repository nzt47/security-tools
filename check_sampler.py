#!/usr/bin/env python3
"""检查 StaticSampler 属性"""

import sys
sys.path.insert(0, '.')

from opentelemetry import trace as ot_trace
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ALWAYS_OFF

from agent.monitoring.tracing import _init_opentelemetry
_init_opentelemetry()

provider = ot_trace.get_tracer_provider()
sampler = provider.sampler

print('Sampler:', sampler)
print('Sampler type:', type(sampler).__name__)
print('Sampler is ALWAYS_ON:', sampler is ALWAYS_ON)
print('Sampler is ALWAYS_OFF:', sampler is ALWAYS_OFF)
print()
print('All attributes:')
for attr in dir(sampler):
    if not attr.startswith('__'):
        try:
            value = getattr(sampler, attr)
            print(f'  {attr}: {value}')
        except Exception as e:
            print(f'  {attr}: Error: {e}')