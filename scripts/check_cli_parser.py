"""CLI parser 注册一致性检测（AST 符号级校验，防字符串匹配误判）

背景（Why）:
    2026-08-14 排查发现 deploy 脚本用字符串 'convert-cards' 匹配会命中
    docstring/print 导致误 PASS，而实际 parser 注册（set_defaults(func=...)
    + add_parser 子命令）缺失时，运行时才暴露 "invalid choice"。
    本脚本用 AST 做符号级双向校验:
        1. 每个 sub.add_parser("<name>") 子命令都有对应 set_defaults(func=cmd_xxx)
        2. 每个 func= 引用的 cmd_xxx 都真实定义在模块顶层
        3. 子命令名与函数名符合 cmd_<snake> 约定（cmd_convert-cards→cmd_convert_cards）

用法:
    python scripts/check_cli_parser.py [文件...]
    （默认检测 agent/knowledge/__main__.py，可传多个 CLI 入口文件）

退出码: 0=全部一致；1=存在未注册子命令或未定义函数。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _to_subcommand(func_name: str) -> str:
    """cmd_index_rebuild → index-rebuild"""
    assert func_name.startswith("cmd_"), func_name
    return func_name[4:].replace("_", "-")


def _to_func_name(subcommand: str) -> str:
    """index-rebuild → cmd_index_rebuild"""
    return "cmd_" + subcommand.replace("-", "_")


def analyze_cli_file(path: Path) -> Tuple[bool, List[str]]:
    """AST 分析单个 CLI 入口文件，返回 (是否一致, 问题清单)。"""
    problems: List[str] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    defined: set[str] = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    subcommands: set[str] = set()   # add_parser("<name>")
    func_refs: set[str] = set()     # set_defaults(func=cmd_xxx)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # sub.add_parser("name")
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            subcommands.add(node.args[0].value)
        # <var>.set_defaults(func=cmd_xxx)
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_defaults"):
            for kw in node.keywords:
                if kw.arg == "func" and isinstance(kw.value, ast.Name):
                    func_refs.add(kw.value.id)

    # 1. 每个子命令都有 func 注册（约定 cmd_<snake>）
    for sub in sorted(subcommands):
        expected = _to_func_name(sub)
        if expected not in func_refs:
            problems.append(
                f"子命令 '{sub}' 无 set_defaults(func={expected}) 注册"
                f"（函数已定义={expected in defined}）")

    # 2. 每个 func= 引用真实定义在模块顶层
    for ref in sorted(func_refs):
        if ref not in defined:
            problems.append(f"set_defaults(func={ref}) 引用未定义函数")

    # 3. func= 引用的名字与某个子命令对应（cmd_<snake> 约定）
    for ref in sorted(func_refs):
        expected_sub = _to_subcommand(ref)
        if expected_sub not in subcommands:
            problems.append(
                f"func={ref} 对应子命令 '{expected_sub}' 未 add_parser")

    return (not problems), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_cli_parser",
        description="CLI parser 注册一致性检测（AST 符号级）",
    )
    parser.add_argument("files", nargs="*", default=None,
                        help="CLI 入口文件（默认 agent/knowledge/__main__.py）")
    args = parser.parse_args(argv)

    targets = [Path(f) for f in (args.files or ["agent/knowledge/__main__.py"])]
    failed = False
    for t in targets:
        p = t if t.is_absolute() else (_REPO_ROOT / t)
        if not p.exists():
            print(f"[FAIL] 文件不存在: {p}")
            failed = True
            continue
        ok, problems = analyze_cli_file(p)
        print(f"[{'PASS' if ok else 'FAIL'}] {p}")
        for prob in problems:
            print(f"    - {prob}")
        if not ok:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
