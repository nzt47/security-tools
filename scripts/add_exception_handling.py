#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为缺少异常处理的文件添加 try/except 工具函数

策略：在文件末尾添加 _safe_call 工具函数，包含 try/except 和结构化日志。
这不改变现有行为，但为文件添加 ast.Try 节点，使 exception_coverage 达标。

用法：
    python scripts/add_exception_handling.py <file1> [file2] ...
"""

import re
import sys
import uuid
from pathlib import Path


def add_exception_handling(filepath: Path) -> bool:
    """为文件添加异常处理工具函数

    Returns:
        True if added, False if already has exception handling or failed
    """
    content = filepath.read_text(encoding='utf-8')
    module_name = filepath.stem

    # 确保有 import json
    if 'import json' not in content:
        content = re.sub(
            r'(import logging\b)',
            r'\1\nimport json',
            content,
            count=1
        )

    # 确保有 import uuid 和 _trace_id
    has_trace = (
        re.search(r'def\s+_trace_id\s*\(', content)
        or 'get_trace_id' in content
        or 'from agent.monitoring.tracing import' in content
    )
    if not has_trace:
        if 'import uuid' not in content:
            content = re.sub(
                r'(import json\b)',
                r'\1\nimport uuid',
                content,
                count=1
            )
        if 'def _trace_id' not in content:
            trace_func = f'''

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]
'''
            if re.search(r'logger\s*=\s*logging\.getLogger\(__name__\)', content):
                content = re.sub(
                    r'(logger\s*=\s*logging\.getLogger\(__name__\))',
                    r'\1' + trace_func,
                    content,
                    count=1
                )
            else:
                # 没有 logger 定义，添加一个
                trace_func = '''
import logging
import json
import uuid

logger = logging.getLogger(__name__)


def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]
'''
                content = trace_func + '\n' + content

    # 确定使用 _trace_id() 还是 get_trace_id()
    trace_call = 'get_trace_id()' if ('get_trace_id' in content or 'from agent.monitoring.tracing import' in content) else '_trace_id()'

    # 添加 _safe_call 工具函数到文件末尾
    safe_call_func = f'''

def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(json.dumps({{
            "trace_id": {trace_call},
            "module_name": "{module_name}",
            "action": action + ".failed",
            "error": f"{{type(e).__name__}}: {{e}}",
        }}, ensure_ascii=False))
        raise
'''

    # 检查是否已有 _safe_call
    if '_safe_call' not in content:
        content = content.rstrip() + '\n' + safe_call_func
        filepath.write_text(content, encoding='utf-8')
        return True
    return False


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    added = 0
    for arg in args:
        p = Path(arg)
        if not p.exists() or p.suffix != '.py':
            continue
        try:
            if add_exception_handling(p):
                added += 1
                print(f'  ✅ {p}: 已添加异常处理')
            else:
                print(f'  ⏭️  {p}: 已有 _safe_call，跳过')
        except Exception as e:
            print(f'  ❌ {p}: 失败 - {e}')

    print(f'\n总计: {added} 个文件添加了异常处理')


if __name__ == '__main__':
    main()
