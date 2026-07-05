"""通用迁移脚本：将 json.dumps({...}, ensure_ascii=False) 日志调用迁移为 log_dict({...})

迁移规则：
    logger.X(json.dumps({"trace_id": _trace_id(), "module_name": "...", "action": "...", "duration_ms": 0, "message": "...", ...}, ensure_ascii=False))
    →
    logger.X(log_dict({"module_name": "...", "action": "...", "message": "...", ...}))

log_dict() 会自动填充 trace_id 和 duration_ms 默认值，所以这两个字段可以省略。

使用方法：
    python scripts/migrate_to_log_dict.py <file1> [file2] ...
    python scripts/migrate_to_log_dict.py --dry-run <file1>  # 预览不写入
    python scripts/migrate_to_log_dict.py --diff <file1>     # 显示 diff

【生成日志摘要】
- 生成时间: 2026-07-02
- 内容描述: log_dict 迁移工具 v1.0
- 关键状态: 支持 json.dumps({...}, ensure_ascii=False) → log_dict({...}) 转换
"""
import ast
import os
import re
import sys
import difflib
from typing import List, Tuple, Optional


# 需要跳过的字段（log_dict 会自动填充）
_SKIP_FIELDS = {'trace_id', 'duration_ms'}


def find_matching_paren(content: str, start: int) -> int:
    """从 start 位置的 '(' 开始，找到匹配的 ')' 位置

    正确处理三引号字符串、单引号字符串、转义字符和注释。
    """
    depth = 0
    i = start
    in_string = False
    string_char = None
    escape = False

    while i < len(content):
        # 检查三引号字符串开始
        if not in_string and i + 2 < len(content):
            triple = content[i:i+3]
            if triple in ('"""', "'''"):
                # 找到三引号字符串结束
                end_idx = content.find(triple, i + 3)
                if end_idx == -1:
                    return -1  # 未闭合的三引号字符串
                i = end_idx + 3
                continue

        ch = content[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == '\\' and in_string:
            escape = True
            i += 1
            continue
        if in_string:
            if ch == string_char:
                in_string = False
                string_char = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            i += 1
            continue
        # 处理注释 (# 开头到行尾)
        if ch == '#':
            # 跳到行尾
            while i < len(content) and content[i] != '\n':
                i += 1
            continue
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def parse_dict_args(args_str: str) -> Optional[ast.Dict]:
    """解析 json.dumps 的第一个参数是否为 dict 字面量"""
    try:
        tree = ast.parse(f'_f({args_str})', mode='eval')
        call = tree.body
        if not isinstance(call, ast.Call):
            return None
        if not call.args:
            return None
        first_arg = call.args[0]
        if not isinstance(first_arg, ast.Dict):
            return None
        return first_arg
    except SyntaxError:
        return None


def dict_node_to_log_dict_str(dict_node: ast.Dict) -> str:
    """将 ast.Dict 节点转换为 log_dict({...}) 字符串"""
    pairs = []
    for key, value in zip(dict_node.keys, dict_node.values):
        if not isinstance(key, ast.Constant):
            # 非 const key，保留原始
            pairs.append(f'{ast.unparse(key)}: {ast.unparse(value)}')
            continue
        key_str = key.value
        if key_str in _SKIP_FIELDS:
            continue  # 跳过 trace_id 和 duration_ms
        pairs.append(f'{ast.unparse(key)}: {ast.unparse(value)}')

    return 'log_dict({' + ', '.join(pairs) + '})'


def migrate_content(content: str) -> Tuple[str, int]:
    """迁移文件内容，返回 (新内容, 替换次数)"""
    pattern = re.compile(r'json\.dumps\(')
    result = []
    last_end = 0
    count = 0

    for match in pattern.finditer(content):
        start = match.start()
        # 找到匹配的右括号
        end = find_matching_paren(content, match.end() - 1)
        if end == -1:
            continue

        # 提取参数字符串
        args_str = content[match.end():end]

        # 解析第一个参数是否为 dict
        dict_node = parse_dict_args(args_str)
        if dict_node is None:
            continue

        # 检查是否包含 trace_id 或 duration_ms（避免误迁移非日志的 json.dumps）
        has_log_field = False
        for key in dict_node.keys:
            if isinstance(key, ast.Constant) and key.value in ('trace_id', 'module_name', 'action', 'duration_ms'):
                has_log_field = True
                break

        if not has_log_field:
            continue  # 不是日志调用，跳过

        # 转换为 log_dict({...})
        replacement = dict_node_to_log_dict_str(dict_node)

        result.append(content[last_end:start])
        result.append(replacement)
        last_end = end + 1
        count += 1

    result.append(content[last_end:])
    return ''.join(result), count


def add_log_dict_import(content: str) -> Tuple[str, bool]:
    """在文件内容中添加 log_dict 导入（如果尚未导入）

    使用 AST 解析定位真实 import 语句，避免误识别 docstring 中的代码示例。

    策略：
    1. 若已存在 log_dict 导入，跳过
    2. 若存在 'from agent.logging_utils import (...)' 多行块，插入 log_dict
    3. 若存在 'from agent.logging_utils import xxx' 单行，追加 , log_dict
    4. 否则在第一个连续 import 块末尾添加新行

    Returns:
        (新内容, 是否添加了导入)
    """
    # 用 AST 定位真实 import 行号，避免误匹配 docstring 中的代码
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # 语法错误时回退到正则
        tree = None

    # 收集模块级 Import / ImportFrom 节点的行号范围
    import_nodes = []
    if tree is not None:
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # node.lineno 是 1-based, end_lineno 是 1-based 闭区间
                start = node.lineno - 1  # 转 0-based
                end = (node.end_lineno or node.lineno) - 1
                import_nodes.append((node, start, end))

    # 检查是否已导入 log_dict
    for node, _, _ in import_nodes:
        if isinstance(node, ast.ImportFrom):
            if node.module == 'agent.logging_utils':
                for alias in node.names:
                    if alias.name == 'log_dict':
                        return content, False
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'log_dict':
                    return content, False

    lines = content.split('\n')

    # 策略 2: 多行 from agent.logging_utils import (...)
    for node, start, end in import_nodes:
        if isinstance(node, ast.ImportFrom) and node.module == 'agent.logging_utils':
            line = lines[start]
            # 检查是否是多行 import（含 '(' 且不在同一行闭合）
            if '(' in line and ')' not in line:
                indent = line[:len(line) - len(line.lstrip())]
                # 在最后一项后添加 log_dict
                last_import_line = lines[end - 1]
                stripped = last_import_line.strip()
                if stripped and not stripped.endswith(','):
                    lines[end - 1] = last_import_line + ','
                lines.insert(end, f'{indent}    log_dict,')
                return '\n'.join(lines), True

    # 策略 3: 单行 from agent.logging_utils import xxx
    for node, start, end in import_nodes:
        if isinstance(node, ast.ImportFrom) and node.module == 'agent.logging_utils':
            line = lines[start]
            if '(' not in line:  # 单行 import
                indent = line[:len(line) - len(line.lstrip())]
                # 提取现有 import 列表
                import_part = line[line.index('import') + 7:].strip()
                lines[start] = f'{indent}from agent.logging_utils import {import_part}, log_dict'
                return '\n'.join(lines), True

    # 策略 4: 在第一个连续 import 块末尾添加
    if import_nodes:
        # 找第一个连续 import 块的末尾
        first_start = import_nodes[0][1]
        first_end = import_nodes[0][2]
        for node, start, end in import_nodes[1:]:
            # 允许中间有空行或注释，但只要 import 节点行号连续就归入同一块
            if start <= first_end + 3:  # 允许最多 3 行间隔（空行/注释）
                first_end = end
            else:
                break
        lines.insert(first_end + 1, 'from agent.logging_utils import log_dict')
        return '\n'.join(lines), True

    # 兜底：在文件开头添加（跳过 docstring）
    # 找到 docstring 结束位置
    insert_at = 0
    if tree is not None and tree.body and isinstance(tree.body[0], ast.Expr):
        if isinstance(tree.body[0].value, (ast.Constant, ast.Str)):
            insert_at = tree.body[0].end_lineno  # 1-based，正好是下一行
    lines.insert(insert_at, 'from agent.logging_utils import log_dict')
    lines.insert(insert_at + 1, '')
    return '\n'.join(lines), True


def migrate_file(file_path: str, dry_run: bool = False, show_diff: bool = False) -> int:
    """迁移单个文件，返回替换次数"""
    with open(file_path, 'r', encoding='utf-8') as f:
        original = f.read()

    new_content, count = migrate_content(original)

    if count == 0:
        print(f"[SKIP] {file_path}: 无可迁移的 json.dumps 日志调用")
        return 0

    # 添加 log_dict 导入
    new_content, imported = add_log_dict_import(new_content)

    if show_diff:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path + '(migrated)',
            n=3,
        )
        print(''.join(diff))

    if dry_run:
        suffix = f"，新增 import" if imported else ""
        print(f"[DRY-RUN] {file_path}: 将替换 {count} 处{suffix}")
        return count
    if show_diff:
        suffix = f"，新增 import" if imported else ""
        print(f"[DIFF-ONLY] {file_path}: 将替换 {count} 处{suffix}（未写入）")
        return count

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    suffix = "，新增 import" if imported else ""
    print(f"[OK] {file_path}: 已替换 {count} 处{suffix}")
    return count


def main():
    args = sys.argv[1:]
    if not args:
        print("用法: python migrate_to_log_dict.py [--dry-run|--diff] <file1> [file2] ...")
        sys.exit(1)

    dry_run = False
    show_diff = False
    files = []

    for arg in args:
        if arg == '--dry-run':
            dry_run = True
        elif arg == '--diff':
            show_diff = True
        else:
            files.append(arg)

    if not files:
        print("错误: 未指定文件")
        sys.exit(1)

    total = 0
    for f in files:
        if not os.path.isfile(f):
            print(f"[ERROR] 文件不存在: {f}")
            continue
        total += migrate_file(f, dry_run=dry_run, show_diff=show_diff)

    print(f"\n=== 总计替换 {total} 处 ===")


if __name__ == '__main__':
    main()
