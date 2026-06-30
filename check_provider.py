#!/usr/bin/env python3
"""检查 OpenTelemetry provider 结构"""

import sys
sys.path.insert(0, '.')

from opentelemetry import trace as ot_trace
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from agent.monitoring.tracing import _init_opentelemetry
_init_opentelemetry()

provider = ot_trace.get_tracer_provider()
print('Provider type:', type(provider).__name__)

# 检查采样器
if hasattr(provider, '_sampler'):
    sampler = provider._sampler
    print('_sampler:', sampler)
    print('_sampler type:', type(sampler).__name__)
    print('_sampler is ALWAYS_ON:', sampler is ALWAYS_ON)
    
    # 检查采样器属性
    attrs = [x for x in dir(sampler) if not x.startswith('_')]
    print('Sampler attrs:', attrs)
    if hasattr(sampler, '_description'):
        print('_description:', sampler._description)
        
else:
    print('No _sampler attribute')