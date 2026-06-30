#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版 logger 转换脚本 — 处理多参数 % 格式化调用

将 logger.LEVEL("msg %s %s", arg1, arg2) 转换为:
logger.LEVEL(json.dumps({"trace_id": _trace_id(), "module_name": "xxx",
                         "action": "xxx", "msg": "msg %s %s" % (arg1, arg2)},
                        ensure_ascii=False))

用法:
    python scripts/convert_multi_arg_logger.py <file1> [file2] ...
"""

import re
import sys
import os
from pathlib import Path


def _get_module_name(filepath: str) -> str:
    """从文件路径提取 module_name"""
    return Path(filepath).stem


def _ensure_imports(content: str, module_name: str) -> str:
    """确保 import json / import uuid / _trace_id() 已就位"""
    # 添加 import json
    if 'import json' not in content:
        # 在第一个 import 后添加
        match = re.match(r'^((?:from __future__.*\n)?(?:(?:import|from)\s+.*\n)*)', content)
        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + 'import json\n' + content[insert_pos:]

    # 添加 import uuid
    if 'import uuid' not in content:
        match = re.match(r'^((?:from __future__.*\n)?(?:(?:import|from)\s+.*\n)*)', content)
        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + 'import uuid\n' + content[insert_pos:]

    # 添加 _trace_id 函数
    if '_trace_id' not in content:
        # 在 logger 定义之后添加
        logger_pattern = re.compile(r'^(logger\s*=\s*logging\.getLogger\(.*?\))', re.MULTILINE)
        m = logger_pattern.search(content)
        if m:
            insert_pos = m.end()
            func = '\n\n\ndef _trace_id():\n    """生成 trace_id"""\n    return uuid.uuid4().hex[:16]\n'
            content = content[:insert_pos] + func + content[insert_pos:]

    return content


def find_multi_arg_calls(content: str):
    """查找多参数 logger 调用

    返回 [(start, end, level, format_str, args_str), ...]
    """
    calls = []
    pattern = re.compile(r'logger\.(info|debug|warning|error|critical)\(')

    for m in pattern.finditer(content):
        level = m.group(1)
        paren_start = m.end() - 1  # 指向 '('

        # 追踪括号深度找到完整调用
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
            # 处理字符串
            if in_string:
                if ch == in_string:
                    # 检查三引号
                    if i + 2 < len(content) and content[i + 1] == ch and content[i + 2] == ch:
                        in_string = None
                        i += 3
                        continue
                    in_string = None
                i += 1
                continue
            if ch in ('"', "'", '`'):
                # 检查三引号
                if i + 2 < len(content) and content[i + 1] == ch and content[i + 2] == ch:
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
                    break
            i += 1

        if depth != 0:
            continue

        call_end = i + 1  # 包含 ')'
        args_str = content[paren_start + 1:i]

        # 跳过已含 json.dumps 或 trace_id 的调用
        if 'json.dumps' in args_str or 'trace_id' in args_str:
            continue

        # 统计顶层逗号数量
        comma_count = 0
        d = 0
        in_s = None
        esc = False
        j = 0
        while j < len(args_str):
            ch = args_str[j]
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif in_s:
                if ch == in_s:
                    in_s = None
            elif ch in ('"', "'", '`'):
                in_s = ch
            elif ch == '(' or ch == '[' or ch == '{':
                d += 1
            elif ch == ')' or ch == ']' or ch == '}':
                d -= 1
            elif ch == ',' and d == 0:
                comma_count += 1
            j += 1

        if comma_count == 0:
            continue  # 单参数，跳过

        # 提取格式字符串（第一个参数）
        first_comma = -1
        d = 0
        in_s = None
        esc = False
        for j, ch in enumerate(args_str):
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif in_s:
                if ch == in_s:
                    in_s = None
            elif ch in ('"', "'", '`'):
                in_s = ch
            elif ch == '(' or ch == '[' or ch == '{':
                d += 1
            elif ch == ')' or ch == ']' or ch == '}':
                d -= 1
            elif ch == ',' and d == 0:
                first_comma = j
                break

        if first_comma == -1:
            continue

        format_str = args_str[:first_comma].strip()
        remaining_args = args_str[first_comma + 1:].strip()

        calls.append((m.start(), call_end, level, format_str, remaining_args))

    return calls


def infer_action(content: str, line_num: int) -> str:
    """根据上下文推断 action 名称"""
    lines = content.split('\n')
    if line_num < len(lines):
        line = lines[line_num].strip()
        # 查找附近的函数定义
        for i in range(line_num, max(line_num - 20, -1), -1):
            if i < len(lines):
                l = lines[i].strip()
                func_match = re.match(r'def\s+(\w+)', l)
                if func_match:
                    return func_match.group(1)
    return 'log'


def convert_file(filepath: str) -> int:
    """转换单个文件，返回转换数量"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    module_name = _get_module_name(filepath)
    calls = find_multi_arg_calls(content)

    if not calls:
        return 0

    # 从后往前替换，避免偏移量变化
    converted = 0
    for start, end, level, format_str, args_str in reversed(calls):
        # 推断 action
        line_num = content[:start].count('\n')
        action = infer_action(content, line_num)

        # 构建新的 logger 调用
        # 将 "msg %s" % (arg1, arg2) 格式保留在 msg 字段中
        if args_str:
            new_call = (
                f'logger.{level}(json.dumps({{'
                f'"trace_id": _trace_id(), '
                f'"module_name": "{module_name}", '
                f'"action": "{action}", '
                f'"msg": {format_str} % ({args_str})'
                f'}}, ensure_ascii=False))'
            )
        else:
            new_call = (
                f'logger.{level}(json.dumps({{'
                f'"trace_id": _trace_id(), '
                f'"module_name": "{module_name}", '
                f'"action": "{action}", '
                f'"msg": {format_str}'
                f'}}, ensure_ascii=False))'
            )

        content = content[:start] + new_call + content[end:]
        converted += 1

    if converted > 0:
        # 确保 import 就位
        content = _ensure_imports(content, module_name)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

    return converted


def main():
    if len(sys.argv) < 2:
        print("用法: python convert_multi_arg_logger.py <file1> [file2] ...")
        sys.exit(1)

    total_converted = 0
    total_skipped = 0

    for filepath in sys.argv[1:]:
        if not os.path.exists(filepath):
            print(f"  ❌ {filepath}: 文件不存在")
            continue
        try:
            count = convert_file(filepath)
            if count > 0:
                print(f"  ✅ {filepath}: 转换 {count} 处多参数调用")
                total_converted += count
            else:
                print(f"  ⏭️  {filepath}: 无需转换")
                total_skipped += 1
        except Exception as e:
            print(f"  ❌ {filepath}: 错误 - {e}")

    print(f"\n总计: 转换 {total_converted} 处, 跳过 {total_skipped} 个文件")


if __name__ == '__main__':
    main()
