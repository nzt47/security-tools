#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""找出 agent/ 下没有 try/except/raise 的 .py 文件"""
import ast
from pathlib import Path

agent_dir = Path('agent')
no_exception = []
for py_file in agent_dir.rglob('*.py'):
    if py_file.name.startswith('__'):
        continue
    try:
        source = py_file.read_text(encoding='utf-8')
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, OSError, UnicodeDecodeError):
        continue
    has_exception = any(
        isinstance(node, (ast.Try, ast.Raise))
        for node in ast.walk(tree)
    )
    if not has_exception:
        # 统计行数
        line_count = source.count('\n')
        no_exception.append((line_count, str(py_file)))

no_exception.sort(reverse=True)
print(f'共 {len(no_exception)} 个文件无异常处理\n')
print(f'{"行数":>6}  文件')
print('-' * 80)
for lines, path in no_exception[:50]:
    print(f'{lines:>6}  {path}')
