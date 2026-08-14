#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描全项目未被 try/finally 保护的 logging.disable 调用

背景（2026-08-10 logging 泄漏治理复盘）:
- logging.disable(level) 修改的是进程级全局状态 logging.root.manager.disable，
  从不自动恢复，会静默掉同进程后续所有 caplog/assertLogs。
- 两种泄漏模式:
  1. 模块顶层调用: pytest collection import 文件时即执行（import 副作用）
  2. 函数内调用但无 try/finally 配对恢复: 断言失败/异常时恢复代码不执行
- 合法模式（本脚本判定为已保护）:
  a. 调用位于 try 块内，且该 try 的 finally 含 logging.disable 恢复调用
  b. 调用位于函数内 try 之外，但函数内存在 try/finally 恢复（前置禁用模式）

用法:
    python scripts/check_logging_disable_leak.py [--root DIR] [--exclude PATH]...

退出码: 0（默认仅报告）；--exit-nonzero-on-risk 时存在未保护调用返回 1，便于接入 CI。
"""

import argparse
import ast
import sys
from pathlib import Path

# Python 3.10 用 TryExcept/TryFinally，3.11+ 统一为 Try
TRY_TYPES = (ast.Try,) if hasattr(ast, "Try") else (ast.TryExcept, ast.TryFinally)

# 默认排除目录（避免扫描 venv/依赖包）
DEFAULT_EXCLUDES = {"venv", ".venv", ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}


def _is_logging_disable_call(node, aliases):
    """node 是否为 logging 模块（含别名）的 disable 调用"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "disable":
        return False
    value = func.value
    # logging.disable(...) 或 import logging as _logging 后的 _logging.disable(...)
    return isinstance(value, ast.Name) and value.id in aliases


def _collect_logging_aliases(tree):
    """收集模块内所有绑定到 logging 模块的名字（含 import logging as X）"""
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "logging":
                    aliases.add(alias.asname or "logging")
    return aliases


def _build_parents(tree):
    """构建子节点 -> 父节点映射，用于获取任意节点的祖先链"""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _contains(nodes, node):
    """node 是否位于 nodes 列表中任一语句的子树内（或本身就是其成员）"""
    for n in nodes:
        if n is node:
            return True
        for sub in ast.walk(n):
            if sub is node:
                return True
    return False


def _disable_calls_in_nodes(nodes, aliases):
    """nodes 列表中所有语句子树内的 logging.disable 调用"""
    calls = []
    for n in nodes:
        for sub in ast.walk(n):
            if _is_logging_disable_call(sub, aliases):
                calls.append(sub)
    return calls


def _nearest_func(ancestors):
    """祖先链中最近的函数定义节点"""
    for anc in reversed(ancestors):
        if isinstance(anc, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return anc
    return None


def _classify(ancestors, call, aliases):
    """判定调用是否受 try/finally 保护，返回 (受保护, 状态描述)

    判定规则:
    - 调用自身位于某 try 的 finally 内 → 属于恢复调用，跳过（返回 None 由调用方忽略）
    - 调用位于某 try 的 body 内，且该 try 有 finally 且 finally 含 disable 恢复 → 受保护
    - 函数内调用，不在任何 try 内，但函数内存在 try/finally 恢复（前置禁用模式）→ 受保护
    - 其余 → 未受保护
    """
    # 1. 跳过恢复调用本身（位于某 try.finally 内）
    for anc in ancestors:
        if isinstance(anc, TRY_TYPES) and _contains(anc.finalbody, call):
            return None

    # 2. try/finally 配对保护（调用在 try.body 内，finally 有恢复）
    for anc in ancestors:
        if isinstance(anc, TRY_TYPES) and _contains(anc.body, call):
            if anc.finalbody and _disable_calls_in_nodes(anc.finalbody, aliases):
                return True, "受保护: try/finally 配对恢复"
            return False, "未受保护: 位于 try 但 finally 无恢复"

    # 3. 函数级 finally 恢复（前置禁用模式，如 autouse fixture）
    func = _nearest_func(ancestors)
    if func is not None:
        func_recoveries = []
        for try_node in ast.walk(func):
            if isinstance(try_node, TRY_TYPES):
                func_recoveries.extend(_disable_calls_in_nodes(try_node.finalbody, aliases))
        if func_recoveries:
            return True, "受保护: 函数级 finally 恢复(前置禁用)"

    # 4. 未受保护
    if func is None:
        return False, "未受保护: 模块顶层(import 副作用)"
    return False, "未受保护: 函数内无 finally 恢复"


def scan_file(path, aliases=None):
    """扫描单个 .py 文件，返回 [(lineno, protected, status)]"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"  [跳过] {path}: 解析失败 ({type(exc).__name__}: {exc})")
        return []
    if aliases is None:
        aliases = _collect_logging_aliases(tree)
    parents = _build_parents(tree)
    findings = []
    for node in ast.walk(tree):
        if not _is_logging_disable_call(node, aliases):
            continue
        ancestors = []
        cur = node
        while cur in parents:
            cur = parents[cur]
            ancestors.append(cur)
        result = _classify(ancestors, node, aliases)
        if result is None:
            continue  # 恢复调用本身
        protected, status = result
        findings.append((node.lineno, protected, status))
    return findings


def collect_py_files(root, extra_excludes):
    """递归收集扫描范围内的 .py 文件"""
    excludes = DEFAULT_EXCLUDES | {e.rstrip("/\\") for e in extra_excludes}
    files = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        parts = rel.parts
        if any(part in excludes for part in parts[:-1]):
            continue
        files.append(path)
    return files


def main():
    parser = argparse.ArgumentParser(description="扫描未受 try/finally 保护的 logging.disable 调用")
    parser.add_argument("--root", type=str, default=".",
                        help="扫描根目录（默认当前目录）")
    parser.add_argument("--exclude", action="append", default=[],
                        help="额外排除的目录名（可重复传入）")
    parser.add_argument("--only-under", action="append", default=[],
                        help="仅扫描相对 root 的该路径前缀下的文件（可重复传入），"
                             "用于对 tests/ 等风险目录强制阻断而不误伤 scripts/ 独立基准脚本")
    parser.add_argument("--exit-nonzero-on-risk", action="store_true",
                        help="存在未受保护调用时返回退出码 1（可接入 CI）")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    files = collect_py_files(root, args.exclude)
    # --only-under 前缀过滤（如 tests → tests/**，不匹配 testsx/**）
    only_under = [p.rstrip("/\\") for p in args.only_under]
    if only_under:
        files = [p for p in files if any(
            rel == prefix or rel.startswith(prefix + "/")
            for rel in [p.relative_to(root).as_posix()]
            for prefix in only_under
        )]

    total_findings = []
    for path in files:
        findings = scan_file(path)
        for lineno, protected, status in findings:
            total_findings.append((path, lineno, protected, status))

    risky = [f for f in total_findings if not f[2]]
    safe = [f for f in total_findings if f[2]]

    print("=== logging.disable 未受保护扫描报告 ===")
    print(f"扫描根: {root}")
    print(f"扫描文件: {len(files)}，命中 logging.disable 调用: {len(total_findings)}")
    print()

    if risky:
        print(f"[需关注] 未受 try/finally 保护的调用（{len(risky)} 处）:")
        for path, lineno, _, status in risky:
            print(f"  {path.relative_to(root)}:{lineno}  {status}")
    else:
        print("[需关注] 未受保护的调用: 无")

    if safe:
        print()
        print(f"[已保护] try/finally 或函数级 finally 恢复的调用（{len(safe)} 处）:")
        for path, lineno, _, status in safe:
            print(f"  {path.relative_to(root)}:{lineno}  {status}")
    print()

    print(f"汇总: 受保护 {len(safe)} / 未受保护 {len(risky)}")
    if args.exit_nonzero_on_risk and risky:
        print("检测到未受保护调用，退出码 1")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
