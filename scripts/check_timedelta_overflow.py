#!/usr/bin/env python3
"""timedelta 溢出风险静态分析工具

使用 AST 分析检测 timedelta(days=参数) 高风险模式，防止 OverflowError。

风险分级：
  - high:   days 来自函数参数（用户可控），无上界校验
  - medium: days 来自变量引用，追溯赋值来源不确定
  - low:    days 为字面量常量（硬编码），无溢出风险

使用方式：
  python scripts/check_timedelta_overflow.py [--target agent/] [--fail-on-high-risk]
  python scripts/check_timedelta_overflow.py --json  # 输出 JSON 报告

CI 集成：
  --fail-on-high-risk 参数使脚本在检测到高风险时以非零退出码退出

可观测性：
  所有输出包含 trace_id/module_name/action/duration_ms 字段
"""

import ast
import json
import os
import sys
import time
import uuid
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)8s] %(name)-30s : %(message)s")
logger = logging.getLogger("timedelta_overflow_scanner")


def _trace_id() -> str:
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


class TimedeltaOverflowVisitor(ast.NodeVisitor):
    """AST 访问器：检测 timedelta(days=...) 调用并分析风险等级"""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.findings: List[Dict[str, Any]] = []
        # 当前函数上下文栈：[(func_name, arg_names_set), ...]
        self._func_stack: List[Tuple[str, Set[str]]] = []
        # 局部变量赋值追踪：{var_name: ast_node}（仅当前函数作用域）
        self._local_vars: Dict[str, ast.AST] = {}

    def _is_timedelta_call(self, node: ast.Call) -> bool:
        """判断 Call 节点是否为 timedelta(...) 调用"""
        # 直接引用 timedelta(...)
        if isinstance(node.func, ast.Name) and node.func.id == "timedelta":
            return True
        # datetime.timedelta(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "timedelta":
            return True
        return False

    def _extract_days_arg(self, call_node: ast.Call) -> Optional[ast.AST]:
        """从 timedelta(...) 调用中提取 days 参数的 AST 节点"""
        # 位置参数：timedelta(days) 的第一个参数
        if call_node.args:
            return call_node.args[0]
        # 关键字参数：timedelta(days=N)
        for kw in call_node.keywords:
            if kw.arg == "days":
                return kw.value
        return None

    def _classify_risk(self, days_node: ast.AST) -> Tuple[str, str]:
        """分析 days 参数来源，返回 (risk_level, description)

        Returns:
            (risk_level, description) — risk_level: high/medium/low
        """
        # 字面量常量：timedelta(days=30)
        if isinstance(days_node, ast.Constant):
            return "low", f"常量值 {days_node.value}"

        # 变量引用：timedelta(days=some_var)
        if isinstance(days_node, ast.Name):
            var_name = days_node.id

            # 检查是否是当前函数的参数
            if self._func_stack:
                func_name, arg_names = self._func_stack[-1]
                if var_name in arg_names:
                    return "high", f"函数参数 '{var_name}'（用户可控）"

            # 检查是否是局部变量
            if var_name in self._local_vars:
                assigned_node = self._local_vars[var_name]
                risk, desc = self._classify_risk(assigned_node)
                return risk, f"局部变量 '{var_name}' ← {desc}"

            # 无法确定来源
            return "medium", f"变量 '{var_name}'（来源不确定）"

        # 运算表达式：timedelta(days=n * 7)
        if isinstance(days_node, ast.BinOp):
            return "medium", "运算表达式（需人工核实）"

        # 函数调用：timedelta(days=get_days())
        if isinstance(days_node, ast.Call):
            return "medium", "函数返回值（需人工核实）"

        # 其他类型
        return "medium", f"表达式类型: {type(days_node).__name__}"

    def visit_FunctionDef(self, node):
        """进入函数定义时，记录参数列表"""
        arg_names = set()
        # 位置参数
        for arg in node.args.args:
            arg_names.add(arg.arg)
        # 关键字参数
        if node.args.kwarg:
            arg_names.add(node.args.kwarg.arg)
        # 仅位置参数
        for arg in node.args.posonlyargs:
            arg_names.add(arg.arg)
        # 仅关键字参数
        for arg in node.args.kwonlyargs:
            arg_names.add(arg.arg)

        self._func_stack.append((node.name, arg_names))
        # 保存旧局部变量，进入新作用域
        old_vars = self._local_vars
        self._local_vars = {}

        self.generic_visit(node)

        # 离开函数，恢复上下文
        self._func_stack.pop()
        self._local_vars = old_vars

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        """追踪局部变量赋值：var = expr"""
        # 先访问子节点（确保右侧表达式中的 timedelta 被检测）
        self.generic_visit(node)
        # 记录赋值：仅追踪简单变量名赋值（var = expr）
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            var_name = node.targets[0].id
            self._local_vars[var_name] = node.value

    def visit_Call(self, node):
        """检测 timedelta(days=...) 调用"""
        if self._is_timedelta_call(node):
            days_node = self._extract_days_arg(node)
            if days_node is not None:
                risk, desc = self._classify_risk(days_node)

                # 获取行号
                lineno = getattr(node, "lineno", 0)
                col_offset = getattr(node, "col_offset", 0)

                # 构建调用模式描述
                try:
                    pattern = ast.unparse(node)
                except Exception:
                    pattern = f"timedelta(days=...) at line {lineno}"

                self.findings.append({
                    "file": self.filepath,
                    "line": lineno,
                    "col": col_offset,
                    "pattern": pattern[:200],  # 截断防止超长
                    "risk": risk,
                    "description": desc,
                    "func_context": self._func_stack[-1][0] if self._func_stack else "<module>",
                })

        self.generic_visit(node)


def analyze_file(filepath: str) -> List[Dict[str, Any]]:
    """分析单个 Python 文件的 timedelta 调用

    Args:
        filepath: 文件路径

    Returns:
        发现列表，每项包含 file/line/pattern/risk/description
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        logger.warning(f"无法读取文件 {filepath}: {e}")
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        logger.warning(f"语法错误 {filepath}:{e.lineno}: {e.msg}")
        return []

    visitor = TimedeltaOverflowVisitor(filepath)
    visitor.visit(tree)
    return visitor.findings


def analyze_directory(dirpath: str, exclude_patterns: List[str] = None) -> Dict[str, Any]:
    """分析整个目录的 timedelta 调用

    Args:
        dirpath: 目标目录
        exclude_patterns: 要排除的路径模式列表

    Returns:
        分析报告 dict，包含 trace_id/summary/details
    """
    trace_id = _trace_id()
    t0 = time.time()

    if exclude_patterns is None:
        exclude_patterns = ["__pycache__", ".git", "node_modules", "venv", ".venv", "tests"]

    all_findings: List[Dict[str, Any]] = []
    files_scanned = 0

    dir_path = Path(dirpath)
    if not dir_path.is_dir():
        logger.error(f"目标路径不是目录: {dirpath}")
        return {"error": f"not a directory: {dirpath}"}

    for py_file in dir_path.rglob("*.py"):
        # 排除模式匹配
        rel_path = str(py_file.relative_to(dir_path))
        if any(pat in str(py_file) for pat in exclude_patterns):
            continue

        findings = analyze_file(str(py_file))
        all_findings.extend(findings)
        files_scanned += 1

    # 统计摘要
    risk_counts = {"high": 0, "medium": 0, "low": 0}
    for f in all_findings:
        risk_counts[f["risk"]] = risk_counts.get(f["risk"], 0) + 1

    duration_ms = round((time.time() - t0) * 1000, 2)

    report = {
        "trace_id": trace_id,
        "module_name": "timedelta_overflow_scanner",
        "action": "scan.complete",
        "duration_ms": duration_ms,
        "target": dirpath,
        "files_scanned": files_scanned,
        "total_calls": len(all_findings),
        "high_risk": risk_counts["high"],
        "medium_risk": risk_counts["medium"],
        "low_risk": risk_counts["low"],
        "details": all_findings,
    }

    # 结构化日志
    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "timedelta_overflow_scanner",
        "action": "scan.complete",
        "duration_ms": duration_ms,
        "files_scanned": files_scanned,
        "total_calls": len(all_findings),
        "high_risk": risk_counts["high"],
        "medium_risk": risk_counts["medium"],
        "low_risk": risk_counts["low"],
    }, ensure_ascii=False))

    return report


def print_console_report(report: Dict[str, Any]):
    """打印控制台友好的报告摘要"""
    print("\n" + "=" * 70)
    print("  timedelta 溢出风险扫描报告")
    print("=" * 70)
    print(f"  扫描目录:     {report['target']}")
    print(f"  扫描文件数:   {report['files_scanned']}")
    print(f"  timedelta 调用总数: {report['total_calls']}")
    print(f"  扫描耗时:     {report['duration_ms']} ms")
    print("-" * 70)
    print(f"  高风险 (high):   {report['high_risk']}  ← 函数参数传入，需校验")
    print(f"  中风险 (medium): {report['medium_risk']}  ← 变量/表达式，需人工核实")
    print(f"  低风险 (low):    {report['low_risk']}  ← 硬编码常量，无溢出风险")
    print("-" * 70)

    # 打印高风险详情
    high_findings = [f for f in report["details"] if f["risk"] == "high"]
    if high_findings:
        print(f"\n  ⚠ 高风险调用 ({len(high_findings)} 个):")
        for f in high_findings:
            print(f"    {f['file']}:{f['line']}  [{f['func_context']}]")
            print(f"      模式: {f['pattern']}")
            print(f"      来源: {f['description']}")

    # 打印中风险详情（前 10 个）
    medium_findings = [f for f in report["details"] if f["risk"] == "medium"]
    if medium_findings:
        print(f"\n  ⚡ 中风险调用 ({len(medium_findings)} 个，显示前 10 个):")
        for f in medium_findings[:10]:
            print(f"    {f['file']}:{f['line']}  [{f['func_context']}]")
            print(f"      模式: {f['pattern']}")
            print(f"      来源: {f['description']}")
        if len(medium_findings) > 10:
            print(f"    ... 还有 {len(medium_findings) - 10} 个中风险调用")

    # 打印低风险摘要
    low_findings = [f for f in report["details"] if f["risk"] == "low"]
    if low_findings:
        print(f"\n  ✓ 低风险调用 ({len(low_findings)} 个): 硬编码常量，无需处理")

    print("\n" + "=" * 70)
    if report["high_risk"] == 0:
        print("  ✅ 结论: 未检测到高风险 timedelta 调用")
    else:
        print(f"  ❌ 结论: 检测到 {report['high_risk']} 个高风险调用，需要添加参数校验")
    print("=" * 70 + "\n")


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="timedelta 溢出风险静态分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/check_timedelta_overflow.py                    # 扫描 agent/
  python scripts/check_timedelta_overflow.py --target agent/    # 指定目录
  python scripts/check_timedelta_overflow.py --json             # JSON 输出
  python scripts/check_timedelta_overflow.py --fail-on-high-risk  # CI 模式
        """,
    )
    parser.add_argument("--target", default="agent/", help="扫描目标目录（默认 agent/）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式报告")
    parser.add_argument("--fail-on-high-risk", action="store_true", help="检测到高风险时退出码 1（CI 模式）")
    parser.add_argument("--output", default=None, help="报告输出文件路径（JSON 格式）")
    args = parser.parse_args()

    # 执行扫描
    report = analyze_directory(args.target)

    if "error" in report:
        print(f"错误: {report['error']}", file=sys.stderr)
        sys.exit(2)

    # 输出报告
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_console_report(report)

    # 可选：写入文件
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"报告已写入: {args.output}")

    # CI 模式：高风险时退出码 1
    if args.fail_on_high_risk and report["high_risk"] > 0:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
