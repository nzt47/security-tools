#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 from __future__ import 是否在文件开头"""
import ast
from pathlib import Path

issues = []
for py_file in Path('agent').rglob('*.py'):
    try:
        content = py_file.read_text(encoding='utf-8')
        tree = ast.parse(content)
        # Find the first non-docstring statement
        first_stmt_line = None
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue  # skip docstring
            first_stmt_line = node.lineno
            break
        # Find all ImportFrom with __future__
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == '__future__':
                if first_stmt_line and node.lineno > first_stmt_line:
                    issues.append(f'{py_file}:{node.lineno}: from __future__ not at beginning (first stmt at line {first_stmt_line})')
                break
    except SyntaxError as e:
        print(f'  SYNTAX ERROR: {py_file}: {e}')
    except Exception:
        continue

if issues:
    for issue in issues:
        print(f'  ISSUE: {issue}')
else:
    print('  OK: all from __future__ imports are at the beginning')
