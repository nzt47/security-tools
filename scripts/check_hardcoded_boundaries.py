#!/usr/bin/env python3
"""硬编码边界值静态分析工具

检测 agent/ 下重试次数、超时值、容量/并发限制 3 类硬编码边界值，
防止新增未配置化的魔法数字。

风险分级：
  - high:   硬编码且未从 Config 读取（需配置化）
  - medium: 有 DEFAULT_* 常量但未配置化（建议配置化）
  - low:    已配置化模块或测试文件中的硬编码（无需处理）

白名单机制：
  已配置化的模块（通过 observability_config.py 管理）标记为 low，
  不触发 CI 阻断。

使用方式：
  python scripts/check_hardcoded_boundaries.py                    # 扫描 agent/
  python scripts/check_hardcoded_boundaries.py --target agent/    # 指定目录
  python scripts/check_hardcoded_boundaries.py --json             # JSON 输出
  python scripts/check_hardcoded_boundaries.py --fail-on-high-risk  # CI 模式

CI 集成：
  --fail-on-high-risk 参数使脚本在检测到高风险时以非零退出码退出

可观测性：
  所有输出包含 trace_id/module_name/action/duration_ms 字段
"""

import ast
import json
import os
import re
import sys
import time
import uuid
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)8s] %(name)-30s : %(message)s")
logger = logging.getLogger("hardcoded_boundary_scanner")


def _trace_id() -> str:
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


# ============================================================================
# 白名单：已配置化的模块（这些模块的硬编码视为 low 风险）
# ============================================================================

CONFIGURED_MODULES: Set[str] = {
    # retry 相关（相对于 agent/ 目录的路径）
    "error_handler.py",
    "cognitive/reflection.py",
    # http 相关
    "web/http_client.py",
    # cache 相关
    "caching/multi_level_cache.py",
    "monitoring/tracing_cache.py",
    # scheduler 相关
    "task_scheduler.py",
    # Phase 4 Task 1: P2 收尾新增
    "llm_monitor.py",                       # llm_monitor.max_records
    "monitoring/loki.py",                   # loki.push_timeout_sec / loki.query_timeout_sec
    "monitoring/alert_notifier.py",         # alert.timeout_sec
    # Phase 4 Task 2: P3 monitoring 批次新增
    "monitoring/prometheus.py",             # prometheus.max_retries
    "monitoring/chaos_injector.py",         # chaos.thread_join_timeout_sec
    "monitoring/resource_monitor.py",       # resource_monitor.thread_join_timeout_sec
    "monitoring/search.py",                 # search.thread_join_timeout_sec / config_apply_timeout_sec / web_search_timeout_sec / status_check_timeout_sec
    "monitoring/self_healer.py",            # self_healer.restart_timeout_sec / sync_timeout_sec / verify_timeout_sec / thread_join_timeout_sec
    # 配置系统自身
    "monitoring/observability_config.py",
}


# ============================================================================
# 检测模式定义
# ============================================================================

# 重试次数模式：变量名或参数名匹配
RETRY_PATTERNS = {
    "max_retries", "MAX_RETRIES", "retry_count", "retry_max",
    "DEFAULT_MAX_RETRIES", "max_retry", "retries",
}

# 超时值模式：变量名或参数名匹配
TIMEOUT_PATTERNS = {
    "timeout", "TIMEOUT", "DEFAULT_TIMEOUT", "DEFAULT_CONNECT_TIMEOUT",
    "request_timeout", "connect_timeout", "read_timeout",
    "COMMAND_TIMEOUT", "timeout_sec", "timeout_seconds",
}

# 容量/并发限制模式：变量名或参数名匹配
CAPACITY_PATTERNS = {
    "max_workers", "MAX_WORKERS", "pool_size", "POOL_SIZE", "DEFAULT_POOL_SIZE",
    "max_size", "MAX_SIZE", "maxsize", "maxsize_bytes",
    "MAX_HISTORY_LINES", "MAX_HEARTBEAT_HISTORY", "MAX_RECORDS",
    "l1_max_size", "context_max_size", "span_max_size", "span_pool_size",
    "history_size", "batch_size", "buffer_size", "queue_size",
    "DEFAULT_CHECK_INTERVAL", "HEARTBEAT_INTERVAL",
}


def _classify_category(name: str) -> Optional[str]:
    """根据变量/参数名分类检测类别

    Args:
        name: 变量名或参数名

    Returns:
        "retry" / "timeout" / "capacity" / None
    """
    if name in RETRY_PATTERNS:
        return "retry"
    if name in TIMEOUT_PATTERNS:
        return "timeout"
    if name in CAPACITY_PATTERNS:
        return "capacity"
    return None


def _is_numeric_constant(node: ast.AST) -> bool:
    """判断 AST 节点是否为数值常量"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return True
    # 负数：UnaryOp(op=USub, operand=Constant)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _is_numeric_constant(node.operand)
    return False


def _get_constant_value(node: ast.AST) -> Optional[float]:
    """提取常量值"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _get_constant_value(node.operand)
        return -v if v is not None else None
    return None


# ============================================================================
# AST 访问器：检测硬编码边界值
# ============================================================================

class HardcodedBoundaryVisitor(ast.NodeVisitor):
    """AST 访问器：检测重试次数、超时值、容量限制的硬编码"""

    def __init__(self, filepath: str, is_configured: bool):
        self.filepath = filepath
        self.is_configured = is_configured  # 是否为已配置化模块
        self.findings: List[Dict[str, Any]] = []

    def _classify_risk(self, category: str, value: Optional[float]) -> str:
        """风险分级

        Args:
            category: 检测类别（retry/timeout/capacity）
            value: 硬编码值

        Returns:
            risk_level: high/medium/low
        """
        # 已配置化模块中的硬编码视为 low
        if self.is_configured:
            return "low"

        # 数值为 0 或负数通常表示禁用，风险较低
        if value is not None and value <= 0:
            return "low"

        # 未配置化模块中的硬编码视为 high
        return "high"

    def _add_finding(self, node: ast.AST, name: str, value: Optional[float],
                     category: str, context: str, pattern: str):
        """添加发现记录"""
        risk = self._classify_risk(category, value)
        self.findings.append({
            "file": self.filepath,
            "line": getattr(node, "lineno", 0),
            "col": getattr(node, "col_offset", 0),
            "category": category,  # retry/timeout/capacity
            "name": name,
            "value": value,
            "risk": risk,
            "context": context,  # assign / call_arg / default_arg
            "pattern": pattern[:200],
            "configured": self.is_configured,
        })

    def visit_Assign(self, node):
        """检测赋值语句：var = N"""
        # 仅检测简单赋值（单目标）
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            var_name = node.targets[0].id
            category = _classify_category(var_name)

            if category and _is_numeric_constant(node.value):
                value = _get_constant_value(node.value)
                try:
                    pattern = ast.unparse(node)
                except Exception:
                    pattern = f"{var_name} = {value}"
                self._add_finding(node, var_name, value, category, "assign", pattern)

        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        """检测带类型注解的赋值：var: int = N"""
        if isinstance(node.target, ast.Name) and node.value is not None:
            var_name = node.target.id
            category = _classify_category(var_name)

            if category and _is_numeric_constant(node.value):
                value = _get_constant_value(node.value)
                try:
                    pattern = ast.unparse(node)
                except Exception:
                    pattern = f"{var_name} = {value}"
                self._add_finding(node, var_name, value, category, "assign", pattern)

        self.generic_visit(node)

    def visit_Call(self, node):
        """检测函数调用中的硬编码参数：func(timeout=30, max_retries=3)"""
        for kw in node.keywords:
            if kw.arg is None:
                continue
            category = _classify_category(kw.arg)
            if category and _is_numeric_constant(kw.value):
                value = _get_constant_value(kw.value)
                try:
                    pattern = f"{ast.unparse(node.func)}({kw.arg}={value})"
                except Exception:
                    pattern = f"...({kw.arg}={value})"
                self._add_finding(node, kw.arg, value, category, "call_arg", pattern)

        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        """检测函数签名中的默认参数：def f(timeout=30, max_retries=3)"""
        # 检测带默认值的位置参数
        for arg in node.args.args:
            if arg.arg and _classify_category(arg.arg):
                # 默认值在 args.defaults 中（与位置参数对齐）
                # defaults 从右往左对齐，计算偏移
                args_list = node.args.args
                defaults_list = node.args.defaults
                if defaults_list:
                    offset = len(args_list) - len(defaults_list)
                    idx = args_list.index(arg)
                    if idx >= offset:
                        default_node = defaults_list[idx - offset]
                        if _is_numeric_constant(default_node):
                            value = _get_constant_value(default_node)
                            category = _classify_category(arg.arg)
                            try:
                                pattern = f"def {node.name}(..., {arg.arg}={value}, ...)"
                            except Exception:
                                pattern = f"def ...(, {arg.arg}={value}, ...)"
                            self._add_finding(node, arg.arg, value, category, "default_arg", pattern)

        # 检测 keyword-only 参数默认值
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            if arg.arg and _classify_category(arg.arg) and default is not None:
                if _is_numeric_constant(default):
                    value = _get_constant_value(default)
                    category = _classify_category(arg.arg)
                    try:
                        pattern = f"def {node.name}(*, {arg.arg}={value})"
                    except Exception:
                        pattern = f"def ...(*, {arg.arg}={value})"
                    self._add_finding(node, arg.arg, value, category, "default_arg", pattern)

        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef


# ============================================================================
# 分析函数
# ============================================================================

def analyze_file(filepath: str, base_dir: str = "") -> List[Dict[str, Any]]:
    """分析单个 Python 文件的硬编码边界值

    Args:
        filepath: 文件路径
        base_dir: 基础目录（用于计算相对路径，判断是否在白名单中）

    Returns:
        发现列表
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

    # 计算相对路径，判断是否在白名单中
    rel_path = filepath.replace("\\", "/")
    if base_dir:
        rel_path = os.path.relpath(filepath, base_dir).replace("\\", "/")

    # 使用 endswith 匹配，兼容不同 base_dir 导致的路径差异
    is_configured = any(rel_path.endswith(mod) for mod in CONFIGURED_MODULES)

    visitor = HardcodedBoundaryVisitor(rel_path, is_configured)
    visitor.visit(tree)
    return visitor.findings


def analyze_directory(dirpath: str, exclude_patterns: List[str] = None) -> Dict[str, Any]:
    """分析整个目录的硬编码边界值

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
        if any(pat in str(py_file) for pat in exclude_patterns):
            continue

        findings = analyze_file(str(py_file), base_dir=str(dir_path))
        all_findings.extend(findings)
        files_scanned += 1

    # 统计摘要
    risk_counts = {"high": 0, "medium": 0, "low": 0}
    category_counts = {"retry": 0, "timeout": 0, "capacity": 0}

    for f in all_findings:
        risk_counts[f["risk"]] = risk_counts.get(f["risk"], 0) + 1
        category_counts[f["category"]] = category_counts.get(f["category"], 0) + 1

    duration_ms = round((time.time() - t0) * 1000, 2)

    report = {
        "trace_id": trace_id,
        "module_name": "hardcoded_boundary_scanner",
        "action": "scan.complete",
        "duration_ms": duration_ms,
        "target": dirpath,
        "files_scanned": files_scanned,
        "total_findings": len(all_findings),
        "high_risk": risk_counts["high"],
        "medium_risk": risk_counts["medium"],
        "low_risk": risk_counts["low"],
        "category_breakdown": category_counts,
        "configured_modules": sorted(list(CONFIGURED_MODULES)),
        "details": all_findings,
    }

    # 结构化日志
    logger.info(json.dumps({
        "trace_id": trace_id,
        "module_name": "hardcoded_boundary_scanner",
        "action": "scan.complete",
        "duration_ms": duration_ms,
        "files_scanned": files_scanned,
        "total_findings": len(all_findings),
        "high_risk": risk_counts["high"],
        "medium_risk": risk_counts["medium"],
        "low_risk": risk_counts["low"],
        "retry_findings": category_counts["retry"],
        "timeout_findings": category_counts["timeout"],
        "capacity_findings": category_counts["capacity"],
    }, ensure_ascii=False))

    return report


def print_console_report(report: Dict[str, Any]):
    """打印控制台友好的报告摘要"""
    print("\n" + "=" * 70)
    print("  硬编码边界值扫描报告")
    print("=" * 70)
    print(f"  扫描目录:       {report['target']}")
    print(f"  扫描文件数:     {report['files_scanned']}")
    print(f"  硬编码总数:     {report['total_findings']}")
    print(f"  扫描耗时:       {report['duration_ms']} ms")
    print("-" * 70)
    print(f"  高风险 (high):   {report['high_risk']}  ← 未配置化，需处理")
    print(f"  中风险 (medium): {report['medium_risk']}  ← 有常量但未配置化")
    print(f"  低风险 (low):    {report['low_risk']}  ← 已配置化或测试文件")
    print("-" * 70)

    cb = report["category_breakdown"]
    print(f"  按类别统计:")
    print(f"    重试次数 (retry):     {cb['retry']}")
    print(f"    超时值   (timeout):   {cb['timeout']}")
    print(f"    容量限制 (capacity):  {cb['capacity']}")
    print("-" * 70)

    # 打印高风险详情
    high_findings = [f for f in report["details"] if f["risk"] == "high"]
    if high_findings:
        print(f"\n  ⚠ 高风险硬编码 ({len(high_findings)} 个，显示前 20 个):")
        for f in high_findings[:20]:
            print(f"    {f['file']}:{f['line']}  [{f['category']}] {f['name']} = {f['value']}")
            print(f"      上下文: {f['context']}  |  模式: {f['pattern']}")
        if len(high_findings) > 20:
            print(f"    ... 还有 {len(high_findings) - 20} 个高风险硬编码")

    # 打印低风险摘要（已配置化）
    low_findings = [f for f in report["details"] if f["risk"] == "low"]
    if low_findings:
        print(f"\n  ✓ 低风险硬编码 ({len(low_findings)} 个): 已配置化模块或合理硬编码")

    print("\n" + "=" * 70)
    if report["high_risk"] == 0:
        print("  ✅ 结论: 未检测到未配置化的硬编码边界值")
    else:
        print(f"  ❌ 结论: 检测到 {report['high_risk']} 个未配置化的硬编码，建议配置化")
    print("=" * 70 + "\n")


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="硬编码边界值静态分析工具（重试次数 + 超时值 + 容量限制）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/check_hardcoded_boundaries.py                    # 扫描 agent/
  python scripts/check_hardcoded_boundaries.py --target agent/    # 指定目录
  python scripts/check_hardcoded_boundaries.py --json             # JSON 输出
  python scripts/check_hardcoded_boundaries.py --fail-on-high-risk  # CI 模式
  python scripts/check_hardcoded_boundaries.py --baseline 10      # 基线模式（high_risk > 10 阻断）
        """,
    )
    parser.add_argument("--target", default="agent/", help="扫描目标目录（默认 agent/）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式报告")
    parser.add_argument("--fail-on-high-risk", action="store_true", help="检测到高风险时退出码 1（CI 模式）")
    parser.add_argument("--baseline", type=int, default=None,
                        help="基线值：high_risk 超过此值时退出码 1（CI 基线策略）")
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

    # 基线模式：high_risk 超过基线时退出码 1
    if args.baseline is not None and report["high_risk"] > args.baseline:
        print(f"错误: high_risk ({report['high_risk']}) 超过基线 ({args.baseline})", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
