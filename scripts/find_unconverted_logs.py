#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""找出未转换 logger 调用最多的文件"""
import re, os
from pathlib import Path

results = []
agent_dir = Path('agent')
for py_file in agent_dir.rglob('*.py'):
    try:
        content = py_file.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        continue
    # 统计 logger 调用总数
    log_calls = re.findall(r'logger\.(info|debug|warning|error|critical)\(', content)
    total = len(log_calls)
    if total == 0:
        continue
    # 统计已结构化的（含 trace_id 或 json.dumps）
    structured = re.findall(r'logger\.\w+\(.*?(?:trace_id|json\.dumps)', content, re.DOTALL)
    unconverted = total - len(structured)
    if unconverted > 0:
        results.append((unconverted, total, str(py_file)))

results.sort(reverse=True)
print(f'{"未转换":>8} {"总数":>8}  文件')
print('-' * 80)
total_unconverted = 0
for unc, tot, path in results[:40]:
    print(f'{unc:>8} {tot:>8}  {path}')
    total_unconverted += unc
print(f'\n总计未转换: {total_unconverted} 处 (前40个文件)')
