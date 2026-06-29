#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描项目中函数调用的关键字参数冲突风险

检测模式: 当函数调用同时包含「显式关键字参数」和「**字典展开」时，
如果展开的字典可能含有与显式参数同名的键，会触发 TypeError。

示例（BUG）:
    def emit(action, *, trace_id=None, **kw):
        ...

    emit("x", trace_id="t", **payload)  # 若 payload 含 "trace_id" → TypeError

检测策略:
    1. 用 ast 解析所有 .py 文件
    2. 找到同时含显式 kwarg + **展开的 Call 节点
    3. 尝试解析被调函数的签名（同文件定义优先）
    4. 比对显式 kwarg 名与函数参数名，评估冲突风险
    5. 输出结构化报告（JSON + 控制台摘要）

风险等级:
    HIGH   — 同文件函数，显式 kwarg 与函数参数同名，**展开来自变量
    MEDIUM — 同文件函数，**展开来自变量但无同名参数
    LOW    — 外部函数或内置函数，无法确定签名

用法:
    python scripts/scan_kwarg_conflicts.py [--path agent/] [--format json|text]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ════════════════════════════════════════════════════════════
#  结构化日志（遵循可观测性硬约束）
# ════════════════════════════════════════════════════════════

def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _log(action: str, **payload: Any) -> None:
    """输出结构化 JSON 日志"""
    record = {
        "trace_id": _trace_id(),
        "module_name": "scan_kwarg_conflicts",
        "action": action,
        "duration_ms": 0.0,
        **payload,
    }
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr)


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class FuncSignature:
    """函数签名摘要"""
    name: str
    params: Set[str]  # 所有参数名（位置 + 关键字 + **kwargs 名）
    kwonly_params: Set[str]  # 关键字-only 参数
    has_var_kw: bool  # 是否有 **kwargs
    lineno: int


@dataclass
class ConflictFinding:
    """冲突发现"""
    file: str
    lineno: int
    col: int
    func_name: str
    explicit_kwargs: List[str]  # 显式传递的关键字参数名
    spread_expr: str  # **展开的表达式文本
    risk_level: str  # HIGH / MEDIUM / LOW
    reason: str
    conflicting_params: List[str] = field(default_factory=list)  # 同名冲突参数
    suggested_fix: str = ""


# ════════════════════════════════════════════════════════════
#  AST 分析器
# ════════════════════════════════════════════════════════════

class FunctionSignatureCollector(ast.NodeVisitor):
    """收集文件中所有函数定义的签名"""

    def __init__(self):
        self.signatures: Dict[str, FuncSignature] = {}

    def _extract_signature(self, node: ast.FunctionDef) -> FuncSignature:
        """从 FunctionDef 提取签名信息"""
        params: Set[str] = set()
        kwonly: Set[str] = set()
        has_var_kw = False

        # 位置参数
        for arg in node.args.args:
            params.add(arg.arg)

        # 仅关键字参数
        for arg in node.args.kwonlyargs:
            kwonly.add(arg.arg)
            params.add(arg.arg)

        # *args
        if node.args.vararg:
            params.add(node.args.vararg.arg)

        # **kwargs
        if node.args.kwarg:
            has_var_kw = True
            params.add(node.args.kwarg.arg)

        return FuncSignature(
            name=node.name,
            params=params,
            kwonly_params=kwonly,
            has_var_kw=has_var_kw,
            lineno=node.lineno,
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        sig = self._extract_signature(node)
        # 用名字+行号作为 key（支持同名方法）
        self.signatures[f"{node.name}@{node.lineno}"] = sig
        # 也用纯名字索引（最后定义覆盖）
        self.signatures[node.name] = sig
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """异步函数同处理"""
        sig = self._extract_signature(node)
        self.signatures[f"{node.name}@{node.lineno}"] = sig
        self.signatures[node.name] = sig
        self.generic_visit(node)


class KwargConflictScanner(ast.NodeVisitor):
    """扫描函数调用中的关键字参数冲突"""

    # 已过滤变量的命名模式（变量名含这些前缀/后缀视为已过滤保留键）
    FILTERED_NAME_PREFIXES = ("safe_", "filtered_", "clean_")
    FILTERED_NAME_SUFFIXES = ("_safe", "_filtered", "_clean")

    def __init__(self, filepath: str, signatures: Dict[str, FuncSignature]):
        self.filepath = filepath
        self.signatures = signatures
        self.findings: List[ConflictFinding] = []
        # 收集已过滤变量名（通过扫描赋值语句）
        self._filtered_vars: Set[str] = self._collect_filtered_vars()

    def _collect_filtered_vars(self) -> Set[str]:
        """扫描文件中所有「已过滤保留键」的变量赋值

        识别模式:
            safe_payload = {k: v for k, v in x.items() if k not in _RESERVED}
            safe_kwargs = {k: v for k, v in kwargs.items() if k not in ...}
        """
        filtered: Set[str] = set()
        try:
            source = Path(self.filepath).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=self.filepath)
        except Exception:
            return filtered

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            # 检查赋值目标是否为简单变量名
            if not node.targets or not isinstance(node.targets[0], ast.Name):
                continue
            var_name = node.targets[0].id

            # 模式1: 变量名含 safe_/filtered_/clean_ 前缀或后缀
            if any(var_name.startswith(p) for p in self.FILTERED_NAME_PREFIXES):
                filtered.add(var_name)
                continue
            if any(var_name.endswith(s) for s in self.FILTERED_NAME_SUFFIXES):
                filtered.add(var_name)
                continue

            # 模式2: 赋值是字典推导式，含 `if k not in` 条件
            if isinstance(node.value, ast.DictComp):
                for gen in node.value.generators:
                    for if_clause in gen.ifs:
                        try:
                            cond_text = ast.unparse(if_clause)
                            if "not in" in cond_text and ("_RESERVED" in cond_text
                                                          or "_reserved" in cond_text):
                                filtered.add(var_name)
                        except Exception:
                            pass
        return filtered

    def _is_filtered_var(self, node: ast.AST) -> bool:
        """判断 **展开 的变量是否已被过滤保留键"""
        if isinstance(node, ast.Name):
            name = node.id
            # 在已过滤变量集合中
            if name in self._filtered_vars:
                return True
            # 变量名匹配过滤命名模式
            if any(name.startswith(p) for p in self.FILTERED_NAME_PREFIXES):
                return True
            if any(name.endswith(s) for s in self.FILTERED_NAME_SUFFIXES):
                return True
        return False

    def _get_spread_expr_text(self, node: ast.AST) -> str:
        """获取 **展开 的表达式文本"""
        try:
            return ast.unparse(node)
        except Exception:
            return f"<{type(node).__name__}>"

    def _resolve_func_name(self, call: ast.Call) -> Optional[str]:
        """尝试解析被调函数名"""
        func = call.func
        # 直接名称: foo(...)
        if isinstance(func, ast.Name):
            return func.id
        # 属性访问: obj.method(...) → method
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _classify_spread_source(self, spread_node: ast.AST) -> Tuple[str, str]:
        """分类 **展开 的来源，返回 (类型, 表达式文本)

        类型:
            variable    — 简单变量 (payload, kwargs)
            or_expr     — (x or {}) 模式
            dict_comp   — 字典推导式
            dict_literal— 字典字面量
            call        — 函数调用结果
            other       — 其他
        """
        expr_text = self._get_spread_expr_text(spread_node)

        if isinstance(spread_node, ast.Name):
            return ("variable", expr_text)
        if isinstance(spread_node, ast.BoolOp):
            return ("or_expr", expr_text)
        if isinstance(spread_node, ast.DictComp):
            return ("dict_comp", expr_text)
        if isinstance(spread_node, ast.Dict):
            return ("dict_literal", expr_text)
        if isinstance(spread_node, ast.Call):
            return ("call", expr_text)
        return ("other", expr_text)

    def _check_call(self, call: ast.Call) -> None:
        """检查单个 Call 节点"""
        # 分离显式 kwargs 和 **展开
        explicit_kwargs: List[str] = []
        spread_nodes: List[ast.AST] = []

        for kw in call.keywords:
            if kw.arg is None:
                # **展开
                spread_nodes.append(kw.value)
            else:
                explicit_kwargs.append(kw.arg)

        # 无 **展开 或无显式 kwarg → 无风险
        if not spread_nodes or not explicit_kwargs:
            return

        func_name = self._resolve_func_name(call) or "<unknown>"

        # 查找函数签名
        sig = self.signatures.get(func_name)

        for spread_node in spread_nodes:
            source_type, expr_text = self._classify_spread_source(spread_node)

            # 已过滤变量: 变量名含 safe_/filtered_/clean_ 前缀，或通过
            # 字典推导式 `if k not in _RESERVED` 过滤保留键 → 降为 LOW
            if self._is_filtered_var(spread_node):
                self._add_finding(
                    call, func_name, explicit_kwargs, expr_text,
                    "LOW", f"**{expr_text} 已通过保留键过滤（变量名标记为已过滤）",
                )
                continue

            # 字典字面量: 可静态检查键名
            if source_type == "dict_literal" and isinstance(spread_node, ast.Dict):
                literal_keys = set()
                for k in spread_node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        literal_keys.add(k.value)
                conflicts = literal_keys & set(explicit_kwargs)
                if conflicts:
                    self._add_finding(
                        call, func_name, explicit_kwargs, expr_text,
                        "HIGH", f"字典字面量含与显式参数同名的键: {conflicts}",
                        list(conflicts),
                    )
                continue

            # 字典推导式: 如果有条件过滤，可能安全；否则有风险
            if source_type == "dict_comp":
                # 检查是否有 if 条件过滤键（ifs 在 generators 里）
                has_if_filter = False
                if isinstance(spread_node, ast.DictComp):
                    for gen in spread_node.generators:
                        if gen.ifs:
                            has_if_filter = True
                            break
                if has_if_filter:
                    self._add_finding(
                        call, func_name, explicit_kwargs, expr_text,
                        "LOW", "字典推导式含条件过滤，风险较低",
                    )
                else:
                    conflicts = set(explicit_kwargs) & (sig.params if sig else set())
                    self._add_finding(
                        call, func_name, explicit_kwargs, expr_text,
                        "MEDIUM" if conflicts else "LOW",
                        f"字典推导式无键过滤{'，与函数参数同名: ' + str(conflicts) if conflicts else ''}",
                        list(conflicts) if conflicts else [],
                    )
                continue

            # 变量/or_expr/call: 动态来源，有潜在风险
            if source_type in ("variable", "or_expr", "call", "other"):
                if sig:
                    # 有函数签名：检查显式 kwarg 是否与函数参数同名
                    # 且函数有 **kwargs（意味着额外键会被接收）
                    conflicting = []
                    for ek in explicit_kwargs:
                        if ek in sig.params and sig.has_var_kw:
                            # 显式 kwarg 匹配函数参数，且函数接受 **kwargs
                            # 如果 spread 也含同名键 → 冲突
                            conflicting.append(ek)

                    if conflicting:
                        risk = "HIGH"
                        reason = (f"函数 {func_name} 接受 **kwargs，"
                                  f"显式参数 {conflicting} 可能与 **{expr_text} 中的同名键冲突")
                        fix = (f"在展开前过滤保留键: "
                               f"_RESERVED = {set(conflicting)}; "
                               f"safe = {{k: v for k, v in {expr_text}.items() if k not in _RESERVED}}; "
                               f"func(..., **safe)")
                    elif source_type in ("variable", "or_expr"):
                        # 变量展开但无同名参数 — 仍有可能冲突
                        risk = "MEDIUM"
                        reason = (f"**{expr_text} 展开到函数 {func_name}，"
                                  f"若含键 {list(sig.params)[:3]}... 会触发 TypeError")
                        fix = ""
                    else:
                        risk = "LOW"
                        reason = f"函数 {func_name} 签名已知，无直接同名冲突"
                        fix = ""

                    self._add_finding(
                        call, func_name, explicit_kwargs, expr_text,
                        risk, reason, conflicting, fix,
                    )
                else:
                    # 无函数签名：无法确定，标记为 LOW
                    # 但如果是 (x or {}) 模式且显式 kwarg 多，提高风险
                    if source_type == "or_expr" and len(explicit_kwargs) >= 2:
                        risk = "MEDIUM"
                        reason = (f"**{expr_text} 模式 + {len(explicit_kwargs)} 个显式 kwarg，"
                                  f"外部函数签名未知，建议人工检查")
                    else:
                        risk = "LOW"
                        reason = f"外部函数 {func_name}，无法确定签名"
                    self._add_finding(
                        call, func_name, explicit_kwargs, expr_text,
                        risk, reason, [],
                    )

    def _add_finding(self, call: ast.Call, func_name: str,
                     explicit_kwargs: List[str], spread_expr: str,
                     risk: str, reason: str,
                     conflicts: Optional[List[str]] = None,
                     fix: str = "") -> None:
        """添加发现"""
        self.findings.append(ConflictFinding(
            file=self.filepath,
            lineno=call.lineno,
            col=call.col_offset,
            func_name=func_name,
            explicit_kwargs=explicit_kwargs,
            spread_expr=spread_expr,
            risk_level=risk,
            reason=reason,
            conflicting_params=conflicts or [],
            suggested_fix=fix,
        ))

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)


# ════════════════════════════════════════════════════════════
#  扫描引擎
# ════════════════════════════════════════════════════════════

# 排除目录
EXCLUDE_DIRS = {
    "__pycache__", ".git", "node_modules", "venv", ".venv",
    "env", ".env", ".pytest_cache", ".trae-cn", "worktrees",
    ".claude", "dist", "build", "egg-info",
}


def should_scan(path: Path) -> bool:
    """判断文件是否应扫描"""
    if path.suffix != ".py":
        return False
    parts = path.parts
    for excl in EXCLUDE_DIRS:
        if excl in parts:
            return False
    return True


def scan_file(filepath: Path) -> List[ConflictFinding]:
    """扫描单个文件"""
    try:
        source = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        _log("scan_file.skip", file=str(filepath), error=str(e))
        return []

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        _log("scan_file.syntax_error", file=str(filepath), error=str(e))
        return []

    # 第一步：收集所有函数签名
    sig_collector = FunctionSignatureCollector()
    sig_collector.visit(tree)

    # 第二步：扫描调用冲突
    scanner = KwargConflictScanner(str(filepath), sig_collector.signatures)
    scanner.visit(tree)

    return scanner.findings


def scan_directory(root: str) -> List[ConflictFinding]:
    """扫描整个目录"""
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"路径不存在: {root}")

    all_findings: List[ConflictFinding] = []
    files_scanned = 0

    t0 = time.time()

    # 支持单文件扫描
    if root_path.is_file():
        if should_scan(root_path):
            files_scanned = 1
            all_findings = scan_file(root_path)
    else:
        for filepath in root_path.rglob("*.py"):
            if not should_scan(filepath):
                continue
            files_scanned += 1
            findings = scan_file(filepath)
            all_findings.extend(findings)

    elapsed_ms = (time.time() - t0) * 1000

    _log("scan_directory.done",
         root=root,
         files_scanned=files_scanned,
         findings_count=len(all_findings),
         duration_ms=round(elapsed_ms, 2))

    return all_findings


# ════════════════════════════════════════════════════════════
#  报告生成
# ════════════════════════════════════════════════════════════

def generate_text_report(findings: List[ConflictFinding]) -> str:
    """生成文本报告"""
    lines = []
    lines.append("=" * 80)
    lines.append("关键字参数冲突风险扫描报告")
    lines.append("=" * 80)
    lines.append(f"扫描时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"总发现数: {len(findings)}")
    lines.append("")

    # 按风险等级分组
    by_risk: Dict[str, List[ConflictFinding]] = {"HIGH": [], "MEDIUM": [], "LOW": []}
    for f in findings:
        by_risk.setdefault(f.risk_level, []).append(f)

    for risk in ["HIGH", "MEDIUM", "LOW"]:
        items = by_risk.get(risk, [])
        if not items:
            continue
        icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}[risk]
        lines.append(f"\n{'─' * 80}")
        lines.append(f"{icon} {risk} ({len(items)} 处)")
        lines.append(f"{'─' * 80}")

        for f in items:
            lines.append(f"\n  📍 {f.file}:{f.lineno}:{f.col}")
            lines.append(f"     函数: {f.func_name}")
            lines.append(f"     显式 kwargs: {f.explicit_kwargs}")
            lines.append(f"     **展开: **{f.spread_expr}")
            if f.conflicting_params:
                lines.append(f"     冲突参数: {f.conflicting_params}")
            lines.append(f"     原因: {f.reason}")
            if f.suggested_fix:
                lines.append(f"     建议: {f.suggested_fix}")

    # 汇总统计
    lines.append(f"\n{'=' * 80}")
    lines.append("汇总统计")
    lines.append(f"{'=' * 80}")
    lines.append(f"  HIGH:   {len(by_risk.get('HIGH', []))} 处")
    lines.append(f"  MEDIUM: {len(by_risk.get('MEDIUM', []))} 处")
    lines.append(f"  LOW:    {len(by_risk.get('LOW', []))} 处")
    lines.append(f"  总计:   {len(findings)} 处")
    lines.append("")

    return "\n".join(lines)


def generate_json_report(findings: List[ConflictFinding]) -> str:
    """生成 JSON 报告"""
    return json.dumps({
        "scan_time": time.strftime('%Y-%m-%d %H:%M:%S'),
        "total": len(findings),
        "summary": {
            "HIGH": sum(1 for f in findings if f.risk_level == "HIGH"),
            "MEDIUM": sum(1 for f in findings if f.risk_level == "MEDIUM"),
            "LOW": sum(1 for f in findings if f.risk_level == "LOW"),
        },
        "findings": [
            {
                "file": f.file,
                "lineno": f.lineno,
                "col": f.col,
                "func_name": f.func_name,
                "explicit_kwargs": f.explicit_kwargs,
                "spread_expr": f.spread_expr,
                "risk_level": f.risk_level,
                "reason": f.reason,
                "conflicting_params": f.conflicting_params,
                "suggested_fix": f.suggested_fix,
            }
            for f in findings
        ],
    }, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="扫描 Python 项目中函数调用的关键字参数冲突风险",
    )
    parser.add_argument(
        "--path", default="agent/",
        help="扫描根目录 (默认: agent/)",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="输出格式 (默认: text)",
    )
    parser.add_argument(
        "--min-risk", choices=["HIGH", "MEDIUM", "LOW"], default="LOW",
        help="最低报告风险等级 (默认: LOW=全部报告)",
    )
    parser.add_argument(
        "--output", default=None,
        help="输出文件路径 (默认: 控制台)",
    )
    args = parser.parse_args()

    _log("scan.start", path=args.path, min_risk=args.min_risk)

    findings = scan_directory(args.path)

    # 风险过滤
    risk_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    min_level = risk_order[args.min_risk]
    filtered = [f for f in findings if risk_order[f.risk_level] >= min_level]

    # 生成报告
    if args.format == "json":
        report = generate_json_report(filtered)
    else:
        report = generate_text_report(filtered)

    # 输出
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        _log("scan.output", file=args.output, size=len(report))
        print(f"报告已写入: {args.output}")
    else:
        print(report)

    # 退出码: 有 HIGH 风险则返回 1
    high_count = sum(1 for f in filtered if f.risk_level == "HIGH")
    if high_count > 0:
        _log("scan.exit", high_count=high_count, exit_code=1)
        sys.exit(1)
    else:
        _log("scan.exit", high_count=0, exit_code=0)
        sys.exit(0)


if __name__ == "__main__":
    main()
