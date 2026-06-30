"""kwarg_scanner 核心扫描器 — AST 级别静态分析

检测 `func(explicit_kwarg=x, **dict)` 模式中 dict 含同名键的冲突风险。
"""

from __future__ import annotations

import ast
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .types import (
    ConflictFinding,
    FuncSignature,
    ScanConfig,
    RiskLevel,
)


# ════════════════════════════════════════════════════════════
#  结构化日志
# ════════════════════════════════════════════════════════════

def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _log(action: str, config: ScanConfig, **payload: Any) -> None:
    """输出结构化 JSON 日志（trace_id, module_name, action, duration_ms）"""
    if not config.enable_logging:
        return
    record = {
        "trace_id": _trace_id(),
        "module_name": "kwarg_scanner",
        "action": action,
        "duration_ms": 0.0,
        **payload,
    }
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr)


# ════════════════════════════════════════════════════════════
#  函数签名收集器
# ════════════════════════════════════════════════════════════

class _SignatureCollector(ast.NodeVisitor):
    """收集文件中所有函数定义的签名"""

    def __init__(self):
        self.signatures: Dict[str, FuncSignature] = {}

    def _extract(self, node: ast.FunctionDef) -> FuncSignature:
        params: Set[str] = set()
        kwonly: Set[str] = set()
        has_var_kw = False

        for arg in node.args.args:
            params.add(arg.arg)
        for arg in node.args.kwonlyargs:
            kwonly.add(arg.arg)
            params.add(arg.arg)
        if node.args.vararg:
            params.add(node.args.vararg.arg)
        if node.args.kwarg:
            has_var_kw = True
            params.add(node.args.kwarg.arg)

        return FuncSignature(
            name=node.name, params=params,
            kwonly_params=kwonly, has_var_kw=has_var_kw,
            lineno=node.lineno,
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        sig = self._extract(node)
        self.signatures[f"{node.name}@{node.lineno}"] = sig
        self.signatures[node.name] = sig
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        sig = self._extract(node)
        self.signatures[f"{node.name}@{node.lineno}"] = sig
        self.signatures[node.name] = sig
        self.generic_visit(node)


# ════════════════════════════════════════════════════════════
#  扫描器
# ════════════════════════════════════════════════════════════

class KwargScanner:
    """关键字参数冲突风险扫描器

    用法:
        scanner = KwargScanner(ScanConfig(min_risk=RiskLevel.HIGH))
        findings = scanner.scan("src/")
        if findings:
            report = scanner.format_report(findings, "text")
            print(report)
    """

    def __init__(self, config: Optional[ScanConfig] = None):
        self.config = config or ScanConfig()

    # ─── 公开 API ───

    def scan(self, path: str) -> List[ConflictFinding]:
        """扫描文件或目录，返回冲突发现列表

        Args:
            path: 文件或目录路径

        Returns:
            List[ConflictFinding] — 按风险等级过滤后的发现列表
        """
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"路径不存在: {path}")

        t0 = time.time()
        all_findings: List[ConflictFinding] = []
        files_scanned = 0

        if root.is_file():
            if self._should_scan(root):
                files_scanned = 1
                all_findings = self._scan_file(root)
        else:
            for filepath in root.rglob("*.py"):
                if not self._should_scan(filepath):
                    continue
                files_scanned += 1
                all_findings.extend(self._scan_file(filepath))

        elapsed_ms = (time.time() - t0) * 1000
        _log("scan.done", self.config,
             path=path, files_scanned=files_scanned,
             findings_count=len(all_findings),
             duration_ms=round(elapsed_ms, 2))

        # 风险等级过滤
        min_level = self.config.min_risk.value
        return [
            f for f in all_findings
            if RiskLevel.from_str(f.risk_level).value >= min_level
        ]

    def scan_file(self, filepath: str) -> List[ConflictFinding]:
        """扫描单个文件"""
        return self._scan_file(Path(filepath))

    # ─── 内部实现 ───

    def _should_scan(self, path: Path) -> bool:
        """判断文件是否应扫描"""
        if path.suffix != ".py":
            return False
        for excl in self.config.exclude_dirs:
            if excl in path.parts:
                return False
        return True

    def _scan_file(self, filepath: Path) -> List[ConflictFinding]:
        """扫描单个文件"""
        try:
            source = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            _log("scan_file.skip", self.config, file=str(filepath), error=str(e))
            return []

        try:
            tree = ast.parse(source, filename=str(filepath))
        except SyntaxError as e:
            _log("scan_file.syntax_error", self.config, file=str(filepath), error=str(e))
            return []

        # 收集函数签名
        sig_collector = _SignatureCollector()
        sig_collector.visit(tree)

        # 收集已过滤变量
        filtered_vars = self._collect_filtered_vars(tree)

        # 扫描调用
        visitor = _CallScanner(
            str(filepath), sig_collector.signatures,
            filtered_vars, self.config,
        )
        visitor.visit(tree)

        return visitor.findings

    def _collect_filtered_vars(self, tree: ast.AST) -> Set[str]:
        """扫描文件中所有已过滤保留键的变量赋值"""
        filtered: Set[str] = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not node.targets or not isinstance(node.targets[0], ast.Name):
                continue
            var_name = node.targets[0].id

            # 模式1: 变量名匹配过滤命名模式
            if any(var_name.startswith(p) for p in self.config.filtered_name_prefixes):
                filtered.add(var_name)
                continue
            if any(var_name.endswith(s) for s in self.config.filtered_name_suffixes):
                filtered.add(var_name)
                continue

            # 模式2: 字典推导式含 `if k not in _RESERVED` 条件
            if isinstance(node.value, ast.DictComp):
                for gen in node.value.generators:
                    for if_clause in gen.ifs:
                        try:
                            cond_text = ast.unparse(if_clause)
                            if "not in" in cond_text:
                                filtered.add(var_name)
                        except Exception:
                            pass
        return filtered


# ════════════════════════════════════════════════════════════
#  AST 调用扫描器
# ════════════════════════════════════════════════════════════

class _CallScanner(ast.NodeVisitor):
    """扫描函数调用中的关键字参数冲突"""

    def __init__(self, filepath: str,
                 signatures: Dict[str, FuncSignature],
                 filtered_vars: Set[str],
                 config: ScanConfig):
        self.filepath = filepath
        self.signatures = signatures
        self.filtered_vars = filtered_vars
        self.config = config
        self.findings: List[ConflictFinding] = []

    def _get_spread_expr_text(self, node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return f"<{type(node).__name__}>"

    def _resolve_func_name(self, call: ast.Call) -> Optional[str]:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _classify_spread_source(self, node: ast.AST) -> Tuple[str, str]:
        expr_text = self._get_spread_expr_text(node)
        if isinstance(node, ast.Name):
            return ("variable", expr_text)
        if isinstance(node, ast.BoolOp):
            return ("or_expr", expr_text)
        if isinstance(node, ast.DictComp):
            return ("dict_comp", expr_text)
        if isinstance(node, ast.Dict):
            return ("dict_literal", expr_text)
        if isinstance(node, ast.Call):
            return ("call", expr_text)
        return ("other", expr_text)

    def _is_filtered_var(self, node: ast.AST) -> bool:
        """判断 **展开 变量是否已被过滤保留键"""
        if isinstance(node, ast.Name):
            name = node.id
            if name in self.filtered_vars:
                return True
            if any(name.startswith(p) for p in self.config.filtered_name_prefixes):
                return True
            if any(name.endswith(s) for s in self.config.filtered_name_suffixes):
                return True
        return False

    def _add_finding(self, call: ast.Call, func_name: str,
                     explicit_kwargs: List[str], spread_expr: str,
                     risk: str, reason: str,
                     conflicts: Optional[List[str]] = None,
                     fix: str = "") -> None:
        self.findings.append(ConflictFinding(
            file=self.filepath, lineno=call.lineno, col=call.col_offset,
            func_name=func_name, explicit_kwargs=explicit_kwargs,
            spread_expr=spread_expr, risk_level=risk, reason=reason,
            conflicting_params=conflicts or [], suggested_fix=fix,
        ))

    def _check_call(self, call: ast.Call) -> None:
        """检查单个 Call 节点"""
        explicit_kwargs: List[str] = []
        spread_nodes: List[ast.AST] = []

        for kw in call.keywords:
            if kw.arg is None:
                spread_nodes.append(kw.value)
            else:
                explicit_kwargs.append(kw.arg)

        if not spread_nodes or not explicit_kwargs:
            return

        func_name = self._resolve_func_name(call) or "<unknown>"
        sig = self.signatures.get(func_name)

        for spread_node in spread_nodes:
            source_type, expr_text = self._classify_spread_source(spread_node)

            # 已过滤变量 → LOW
            if self._is_filtered_var(spread_node):
                self._add_finding(
                    call, func_name, explicit_kwargs, expr_text,
                    "LOW", f"**{expr_text} 已通过保留键过滤（变量名标记为已过滤）",
                )
                continue

            # 字典字面量: 静态检查键名
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

            # 字典推导式
            if source_type == "dict_comp":
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

            # 变量/or_expr/call: 动态来源
            if source_type in ("variable", "or_expr", "call", "other"):
                if sig:
                    conflicting = [
                        ek for ek in explicit_kwargs
                        if ek in sig.params and sig.has_var_kw
                    ]
                    if conflicting:
                        risk = "HIGH"
                        reason = (f"函数 {func_name} 接受 **kwargs，"
                                  f"显式参数 {conflicting} 可能与 **{expr_text} 中的同名键冲突")
                        fix = (f"在展开前过滤保留键: "
                               f"_RESERVED = {set(conflicting)}; "
                               f"safe = {{k: v for k, v in {expr_text}.items() if k not in _RESERVED}}")
                    elif source_type in ("variable", "or_expr"):
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

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)


# ════════════════════════════════════════════════════════════
#  便捷函数
# ════════════════════════════════════════════════════════════

def scan_file(filepath: str, config: Optional[ScanConfig] = None) -> List[ConflictFinding]:
    """扫描单个文件（便捷函数）

    Args:
        filepath: Python 文件路径
        config: 扫描配置（None 使用默认配置）

    Returns:
        List[ConflictFinding]

    Example:
        >>> from kwarg_scanner import scan_file
        >>> findings = scan_file("src/utils.py")
        >>> high_risks = [f for f in findings if f.risk_level == "HIGH"]
        >>> if high_risks:
        ...     print(f"发现 {len(high_risks)} 处高风险")
    """
    scanner = KwargScanner(config or ScanConfig())
    return scanner.scan_file(filepath)


def scan_directory(path: str, config: Optional[ScanConfig] = None) -> List[ConflictFinding]:
    """扫描目录（便捷函数）

    Args:
        path: 目录路径
        config: 扫描配置（None 使用默认配置，min_risk=LOW）

    Returns:
        List[ConflictFinding]

    Example:
        >>> from kwarg_scanner import scan_directory, RiskLevel, ScanConfig
        >>> config = ScanConfig(min_risk=RiskLevel.HIGH)
        >>> findings = scan_directory("src/", config)
        >>> if findings:
        ...     print(f"阻断: 发现 {len(findings)} 处 HIGH 风险")
    """
    scanner = KwargScanner(config or ScanConfig())
    return scanner.scan(path)
