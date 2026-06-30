#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量将 logger 调用转换为 JSON 结构化格式

转换规则：
- logger.LEVEL("message") → logger.LEVEL(json.dumps({"trace_id": _trace_id(), "module_name": "xxx", "action": "xxx", "msg": "message"}, ensure_ascii=False))
- 自动确保 import json / import uuid / _trace_id() 已就位
- 跳过已包含 trace_id 或 json.dumps 的调用
- 跳过多参数调用（含 extra= 等）以保证安全

用法：
    python scripts/convert_logger_to_json.py <file1> [file2] ...
    python scripts/convert_logger_to_json.py --dir agent/monitoring
"""

import re
import sys
import uuid
from pathlib import Path
from typing import List, Tuple, Optional


def find_logger_calls(content: str) -> List[Tuple[int, int, str, str]]:
    """查找所有 logger.LEVEL(...) 调用

    返回 [(start, end, level, args), ...]
    start/end 是 content 中的字符偏移量
    """
    calls = []
    pattern = re.compile(r'logger\.(info|debug|warning|error|critical)\(')
    for m in pattern.finditer(content):
        level = m.group(1)
        # 从 '(' 开始追踪括号深度
        paren_start = m.end() - 1  # 指向 '('
        depth = 0
        i = paren_start
        in_string = None
        escape_next = False
        while i < len(content):
            ch = content[i]
            if escape_next:
                escape_next = False
                i += 1
                continue
            if ch == '\\':
                escape_next = True
                i += 1
                continue
            # 处理字符串（包括 f-string、三引号等）
            if in_string:
                if ch == in_string:
                    # 检查是否是三引号结束
                    if content[i:i+3] == ch * 3:
                        in_string = None
                        i += 3
                        continue
                    in_string = None
                i += 1
                continue
            if ch in ('"', "'"):
                # 检查是否是三引号开始
                if content[i:i+3] == ch * 3:
                    in_string = ch
                    i += 3
                    continue
                in_string = ch
                i += 1
                continue
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    args = content[paren_start + 1:i]
                    calls.append((m.start(), i + 1, level, args))
                    break
            i += 1
    return calls


def needs_conversion(args: str) -> bool:
    """判断该 logger 调用是否需要转换（不含 trace_id 或 json.dumps）"""
    if 'trace_id' in args or 'json.dumps' in args:
        return False
    return True


def derive_action(args: str, module_name: str) -> str:
    """从日志参数推导 action 名称"""
    # 尝试提取字符串消息
    text = args.strip()
    # 去掉 f 前缀
    if text.startswith('f"') or text.startswith("f'"):
        text = text[1:]

    # 提取引号内的内容
    msg_match = re.match(r'["\'](.+?)["\']', text, re.DOTALL)
    if msg_match:
        msg = msg_match.group(1)
    else:
        return 'log'

    # 去掉 [Module] 前缀
    msg = re.sub(r'^\[[^\]]+\]\s*', '', msg)

    # 尝试提取英文关键词
    words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', msg)
    if words:
        # 取前3个有意义的词
        action_parts = [w.lower() for w in words[:3] if len(w) > 2]
        if action_parts:
            return '.'.join(action_parts)

    # 如果没有英文关键词，使用简单的动作描述
    return 'log'


def count_args(args: str) -> int:
    """粗略统计顶层逗号数量来判断参数个数"""
    depth = 0
    in_string = None
    escape_next = False
    count = 1
    for ch in args:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\':
            escape_next = True
            continue
        if in_string:
            if ch == in_string:
                in_string = None
            continue
        if ch in ('"', "'"):
            in_string = ch
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == ',' and depth == 0:
            count += 1
    return count


def convert_file(filepath: Path, module_name: Optional[str] = None) -> Tuple[int, int]:
    """转换单个文件

    Returns:
        (converted_count, skipped_count)
    """
    content = filepath.read_text(encoding='utf-8')
    orig_len = len(content)

    if module_name is None:
        module_name = filepath.stem

    # 确保有 import json
    if 'import json' not in content:
        # 在 import logging 后面加
        content = re.sub(
            r'(import logging\b)',
            r'\1\nimport json',
            content,
            count=1
        )

    # 确保有 import uuid
    has_trace_source = bool(
        re.search(r'def\s+_trace_id\s*\(', content)
        or 'get_trace_id' in content
        or 'from agent.monitoring.tracing import' in content
    )
    if not has_trace_source:
        if 'import uuid' not in content:
            content = re.sub(
                r'(import json\b)',
                r'\1\nimport uuid',
                content,
                count=1
            )
        # 在 logger = logging.getLogger(__name__) 后添加 _trace_id 函数
        if 'def _trace_id' not in content:
            trace_func = '''

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]
'''
            content = re.sub(
                r'(logger\s*=\s*logging\.getLogger\(__name__\))',
                r'\1' + trace_func,
                content,
                count=1
            )

    # 查找所有 logger 调用
    calls = find_logger_calls(content)

    # 从后往前替换，避免偏移量失效
    converted = 0
    skipped = 0
    for start, end, level, args in reversed(calls):
        if not needs_conversion(args):
            skipped += 1
            continue

        # 跳过多参数调用（安全考虑）
        arg_count = count_args(args)
        if arg_count > 1:
            skipped += 1
            continue

        # 跳过空参数
        if not args.strip():
            skipped += 1
            continue

        action = derive_action(args, module_name)

        # 确定使用 _trace_id() 还是 get_trace_id()
        if 'get_trace_id' in content or 'from agent.monitoring.tracing import' in content:
            trace_call = 'get_trace_id()'
        else:
            trace_call = '_trace_id()'

        # 构造新的 logger 调用
        new_call = (
            f'logger.{level}(json.dumps({{'
            f'"trace_id": {trace_call}, '
            f'"module_name": "{module_name}", '
            f'"action": "{action}", '
            f'"msg": {args.strip()}'
            f'}}, ensure_ascii=False))'
        )

        content = content[:start] + new_call + content[end:]
        converted += 1

    if converted > 0:
        filepath.write_text(content, encoding='utf-8')

    return converted, skipped


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    files: List[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            files.extend(p.rglob('*.py'))
        else:
            files.append(p)

    total_converted = 0
    total_skipped = 0
    for f in files:
        if not f.exists() or f.suffix != '.py':
            continue
        try:
            converted, skipped = convert_file(f)
            total_converted += converted
            total_skipped += skipped
            if converted > 0:
                print(f'  ✅ {f}: 转换 {converted} 处, 跳过 {skipped} 处')
            else:
                print(f'  ⏭️  {f}: 无需转换')
        except Exception as e:
            print(f'  ❌ {f}: 转换失败 - {e}')

    print(f'\n总计: 转换 {total_converted} 处, 跳过 {total_skipped} 处')


if __name__ == '__main__':
    main()
