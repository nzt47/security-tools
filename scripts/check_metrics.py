#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""快速检查三项关键指标"""
import sys, json, subprocess
result = subprocess.run(
    [sys.executable, 'scripts/visibility_report.py', '--config', 'config.yaml', '--json-only'],
    capture_output=True, text=True, encoding='utf-8'
)
data = json.loads(result.stdout)
for layer in data['layers']:
    for m in layer['metrics']:
        if m['name'] in ('structured_log_coverage', 'exception_coverage', 'track_event_coverage'):
            print(f'{m["name"]}: {m["value"]}% (threshold {m["threshold"]}%, passed={m["passed"]})')
