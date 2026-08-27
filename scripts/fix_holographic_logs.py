#!/usr/bin/env python3
"""自动将模块的非结构化日志转换为 log_dict 结构化格式（通用工具 v2）

支持：
- 单行与跨行 logger.X("text %s", args) 调用（括号配对解析）
- 已使用 log_dict 的调用保持不动
- f-string（无参数）直接保留为 msg

用法：
    python scripts/fix_holographic_logs.py <文件路径> <module_name> [类名,类名...]
"""

import re
import sys
from pathlib import Path

DEFAULT_CLASS_EXCLUDE = "holographicadapter"

LOGGER_LEVELS = ("info", "warning", "error", "debug", "exception")


def extract_sub(fmt: str, class_excludes: set) -> str:
    """从 fmt 提取 [Tag] 标签（最后一个，排除类名自身）"""
    tags = [t for t in re.findall(r'\[([A-Za-z_]+)\]', fmt) if t.lower() not in class_excludes]
    return tags[-1].lower() if tags else "adapter"


def extract_event(fmt: str) -> str:
    if any(k in fmt for k in ("失败", "异常", "拒绝", "不可用", "错误", "不允许", "拦截", "缺少", "丢弃")):
        return "failed"
    if any(k in fmt for k in ("成功", "已就绪", "已清空", "已关闭", "已合并", "已回滚", "已提交", "通过", "完成")):
        return "success"
    if any(k in fmt for k in ("降级", "跳过", "忽略", "熔断", "暂停", "跳过")):
        return "degrade"
    return "log"


def make_action(fmt: str, class_excludes: set) -> str:
    sub = extract_sub(fmt, class_excludes)
    event = extract_event(fmt)
    return f"{sub}.{event}" if event != "log" else sub


def find_string_end(text: str, start: int, quote: str) -> int:
    """返回从 start（引号位置）开始的字符串字面量结束后的索引"""
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def find_call_end(text: str, open_idx: int) -> int:
    """从 logger.X( 的 ( 开始，括号配对找到匹配 ) 的结束索引"""
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == '"' or ch == "'":
            i = find_string_end(text, i, ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def parse_fmt_and_args(body: str):
    """从调用体（不含外层括号）解析出 fmt 字符串（支持相邻字符串拼接）和 args 文本"""
    body = body.strip()
    if not body:
        return None, ""
    # 提取连续的字符串字面量（Python 隐式拼接，如 "a " "b"）
    fmt_parts = []
    pos = 0
    while pos < len(body):
        rest = body[pos:].lstrip()
        pos += len(body[pos:]) - len(rest)
        if rest.startswith('f"') or rest.startswith("f'"):
            end = find_string_end(rest, 1, rest[1])
        elif rest.startswith('"') or rest.startswith("'"):
            end = find_string_end(rest, 0, rest[0])
        else:
            break
        fmt_parts.append(rest[:end])
        pos += end
    if not fmt_parts:
        return None, body
    fmt = "".join(fmt_parts)
    rest = body[pos:]
    return fmt, rest


def split_logging_kwargs(args: str):
    """从 args 中分离 logging 保留关键字参数（exc_info/stack_info/extra）
    返回 (剩余args, [保留参数子串列表])
    """
    parts = []
    reserved = []
    depth = 0
    start = 0
    i = 0
    n = len(args)
    while i < n:
        ch = args[i]
        if ch in '"\'':
            i = find_string_end(args, i, ch)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            part = args[start:i].strip()
            if part:
                if re.match(r'^(exc_info|stack_info|extra)\s*=', part):
                    reserved.append(part)
                else:
                    parts.append(part)
            start = i + 1
        i += 1
    part = args[start:].strip()
    if part:
        if re.match(r'^(exc_info|stack_info|extra)\s*=', part):
            reserved.append(part)
        else:
            parts.append(part)
    return ", ".join(parts), reserved


def transform_logger_call(text: str, module_name: str, class_excludes: set):
    """将单个 logger.X(...) 调用文本转换为 log_dict 形式，返回新文本"""
    m = re.search(r'logger\.(info|warning|error|debug|exception)\(', text)
    if not m:
        return text
    level = m.group(1)
    open_idx = text.index("(", m.start())
    end_idx = find_call_end(text, open_idx)
    if end_idx == -1:
        return text  # 解析失败，保持不动
    body = text[open_idx + 1:end_idx]
    fmt, rest = parse_fmt_and_args(body)
    if fmt is None or "log_dict" in text[:open_idx]:
        return text
    args = rest.strip()
    # 去掉尾部逗号
    if args.endswith(","):
        args = args[:-1].strip()
    # 去掉前导逗号（跨行参数 ",\n arg" 场景）
    if args.startswith(","):
        args = args[1:].strip()
    # 分离 logging 保留关键字参数（exc_info/stack_info/extra）
    args, reserved_kw = split_logging_kwargs(args)
    action = make_action(fmt, class_excludes)
    if args:
        if "," in args:
            msg = f'{fmt} % ({args})'
        else:
            msg = f'{fmt} % {args}'
    else:
        msg = fmt
    suffix = f", {', '.join(reserved_kw)}" if reserved_kw else ""
    new_call = (
        f"logger.{level}(log_dict("
        f"{{'module_name': '{module_name}', 'action': '{action}', 'msg': {msg}}}"
        f"){suffix})"
    )
    return text[: m.start()] + new_call + text[end_idx + 1:]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    module_name = sys.argv[2]
    class_excludes = {DEFAULT_CLASS_EXCLUDE}
    if len(sys.argv) > 3:
        class_excludes.update(c.lower() for c in sys.argv[3].split(",") if c)

    if not path.exists():
        print(f"[ERROR] 文件不存在: {path}")
        return 1

    content = path.read_text(encoding="utf-8")
    out_lines = []
    converted = 0

    lines = content.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        # 找到 logger.X( 调用起点
        if re.match(r'logger\.(info|warning|error|debug|exception)\(', stripped) and "log_dict" not in line:
            # 收集调用文本（可能跨行）
            start_idx = stripped.index("(")
            call_text = line
            # 检查括号是否闭合（在同行）
            if find_call_end(line, start_idx) == -1:
                # 跨行：继续收集后续行
                j = i + 1
                while j < len(lines):
                    call_text += lines[j]
                    if find_call_end(call_text, start_idx) != -1:
                        break
                    j += 1
                consumed = j - i
            else:
                consumed = 0
            new_text = transform_logger_call(call_text, module_name, class_excludes)
            if new_text != call_text:
                converted += 1
                out_lines.append(new_text)
            else:
                # 未转换（如 fmt 非字符串字面量）：保留所有原始行，避免内容丢失
                for k in range(i, i + consumed + 1):
                    out_lines.append(lines[k])
            i += consumed + 1
            continue
        out_lines.append(line)
        i += 1

    if converted == 0:
        print(f"[OK] 无可转换的日志（{path}）")
        return 0

    path.write_text("".join(out_lines), encoding="utf-8")
    print(f"[OK] 转换完成: {converted} 处日志已结构化（{path}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
