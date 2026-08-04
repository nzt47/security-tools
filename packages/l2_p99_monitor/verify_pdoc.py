"""验证 pdoc3 生成的文档是否包含所有 public API"""
import re
import os
import sys

# 1. 从 __init__.py 提取 __all__ 声明的 public 符号
import l2_p99_monitor
declared_all = getattr(l2_p99_monitor, '__all__', [])
print('=== __all__ 声明的符号 ({}) ==='.format(len(declared_all)))
for s in declared_all:
    print('  -', s)

# 2. 检查 index.html 是否包含所有声明的符号
docs_dir = os.path.join('docs_build', 'l2_p99_monitor')
index_html = open(os.path.join(docs_dir, 'index.html'), encoding='utf-8').read()
print()
print('=== index.html (包入口) 检查 ===')
missing_in_index = []
for s in declared_all:
    if s in index_html:
        print('  [OK] {}'.format(s))
    else:
        print('  [MISSING] {}'.format(s))
        missing_in_index.append(s)

# 3. 检查各模块 HTML 中的 public 符号
print()
for mod_file in ['monitor.html', 'parser.html', 'cli.html']:
    html_path = os.path.join(docs_dir, mod_file)
    if not os.path.exists(html_path):
        print('=== {} (不存在) ==='.format(mod_file))
        continue
    html = open(html_path, encoding='utf-8').read()
    # pdoc3 用 id="module.Class" 或 id="module.function" 标记符号
    symbols = re.findall(r'id="l2_p99_monitor\.\w+\.(\w+)"', html)
    unique_symbols = sorted(set(symbols))
    print('=== {} 中的 public 符号 ({}) ==='.format(mod_file, len(unique_symbols)))
    for s in unique_symbols:
        in_all = '[在 __all__]' if s in declared_all else '[额外]'
        print('  - {} {}'.format(s, in_all))

# 4. 汇总
print()
print('=== 汇总 ===')
print('  __all__ 声明: {} 个'.format(len(declared_all)))
print('  index.html 缺失: {} 个'.format(len(missing_in_index)))
if missing_in_index:
    print('  缺失符号:', missing_in_index)
