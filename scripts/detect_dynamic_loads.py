"""检测项目中所有动态加载模块的潜在风险

扫描模式:
    - importlib.util.spec_from_file_location / module_from_spec
    - imp.load_source (旧 API)
    - __import__ 动态调用
    - importlib.import_module 带变量参数

风险等级:
    HIGH    : 在 conftest/fixture/生产代码中加载外部脚本 (无包路径)
    MEDIUM  : 在测试代码中加载外部脚本 (可能受 pytest 收集影响)
    LOW     : importlib.import_module 加载标准包 (相对安全)
    INFO    : 仅作信息记录, 无风险

用法:
    python scripts/detect_dynamic_loads.py
    python scripts/detect_dynamic_loads.py --root agent/
    python scripts/detect_dynamic_loads.py --json

说明:
    本脚本只读不写, 不修改任何源代码。
"""
from __future__ import annotations
import sys
import ast
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Set, Optional

ROOT = Path(__file__).resolve().parent.parent

# 动态加载函数名 → 风险等级
DYNAMIC_LOAD_PATTERNS = {
    "spec_from_file_location": "HIGH",   # 从文件路径加载, 无包路径
    "module_from_spec": "HIGH",          # 配合 spec_from_file_location
    "load_source": "HIGH",               # imp.load_source (旧 API, 从路径加载)
    "load_module": "HIGH",               # loader.load_module (旧 API)
    "__import__": "MEDIUM",              # 动态 import, 参数可控时风险中等
    "import_module": "LOW",              # importlib.import_module 加载标准包
}


@dataclass
class DynamicLoadFinding:
    """单条动态加载发现"""
    file: str               # 相对路径
    line: int               # 行号
    col: int               # 列号
    function: str           # 调用的函数名
    risk_level: str         # HIGH / MEDIUM / LOW / INFO
    code_snippet: str       # 代码片段 (单行)
    in_test: bool           # 是否在测试代码中
    suggestion: str = ""   # 建议 (HIGH/MEDIUM 才有)


@dataclass
class ScanReport:
    """扫描报告"""
    root: str
    scanned_files: int
    findings: List[DynamicLoadFinding] = field(default_factory=list)

    @property
    def high_risk(self) -> List[DynamicLoadFinding]:
        return [f for f in self.findings if f.risk_level == "HIGH"]

    @property
    def medium_risk(self) -> List[DynamicLoadFinding]:
        return [f for f in self.findings if f.risk_level == "MEDIUM"]

    @property
    def low_risk(self) -> List[DynamicLoadFinding]:
        return [f for f in self.findings if f.risk_level == "LOW"]


# 排除目录 (相对路径前缀匹配)
EXCLUDE_DIRS = {
    "venv", ".venv", "env", ".env", "node_modules",
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache",
    "build", "dist", ".tox", ".eggs",
    "site-packages",
}


def is_excluded(path: Path, root: Path) -> bool:
    """检查路径是否应被排除"""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    parts = rel.parts
    for excl in EXCLUDE_DIRS:
        if excl in parts:
            return True
    return False


def is_test_file(path: Path) -> bool:
    """判断是否为测试文件 (tests/ 目录或 test_*.py)"""
    if "tests" in path.parts:
        return True
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def get_suggestion(function: str, in_test: bool) -> str:
    """根据函数名和上下文给出建议"""
    if function in ("spec_from_file_location", "module_from_spec", "load_source", "load_module"):
        if in_test:
            return "改用 subprocess 调用外部脚本, 避免 importlib 在 pytest 上下文中的加载坑"
        return "改用 subprocess 或将外部脚本加入包路径后用 importlib.import_module"
    if function == "__import__":
        return "确认参数不可被外部控制, 否则可能触发任意模块加载"
    if function == "import_module":
        return "确认模块名为常量, 否则可能加载非预期模块"
    return ""


class DynamicLoadVisitor(ast.NodeVisitor):
    """AST visitor 识别动态加载调用"""

    def __init__(self, filepath: Path, root: Path):
        self.filepath = filepath
        self.root = root
        self.findings: List[DynamicLoadFinding] = []
        # 跟踪 import 别名: import importlib.util as iu → iu.spec_from_file_location
        self._import_aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import):
        """记录 import 别名, 如 import importlib.util as iu"""
        for alias in node.names:
            if alias.asname:
                self._import_aliases[alias.asname] = alias.name
            else:
                self._import_aliases[alias.name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """记录 from import 别名, 如 from importlib.util import spec_from_file_location as sfl"""
        if node.module:
            for alias in node.names:
                full_name = f"{node.module}.{alias.name}"
                if alias.asname:
                    self._import_aliases[alias.asname] = full_name
                else:
                    self._import_aliases[alias.name] = full_name
        self.generic_visit(node)

    def _resolve_full_name(self, call_name: str) -> str:
        """解析调用名, 替换别名为全名"""
        if call_name in self._import_aliases:
            return self._import_aliases[call_name]
        return call_name

    def visit_Call(self, node: ast.Call):
        """识别动态加载函数调用"""
        # 提取调用名 (支持 a.b.c 形式)
        call_name = self._extract_call_name(node.func)
        if not call_name:
            self.generic_visit(node)
            return

        # 解析别名
        resolved = self._resolve_full_name(call_name)

        # 检查是否匹配动态加载模式 (匹配末尾函数名)
        for pattern, risk in DYNAMIC_LOAD_PATTERNS.items():
            if resolved.endswith(pattern) or call_name == pattern:
                self._add_finding(node, pattern, risk, resolved)
                break

        self.generic_visit(node)

    def _extract_call_name(self, node: ast.expr) -> str:
        """从 Call.func 提取调用名 (支持 Attribute 和 Name)"""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._extract_call_name(node.value)
            if parent:
                return f"{parent}.{node.attr}"
            return node.attr
        return ""

    def _add_finding(self, node: ast.Call, pattern: str, risk: str, resolved: str):
        """添加一条发现"""
        try:
            rel_path = str(self.filepath.relative_to(self.root))
        except ValueError:
            rel_path = str(self.filepath)

        # 读取该行源码
        try:
            with open(self.filepath, encoding="utf-8") as f:
                lines = f.readlines()
            snippet = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else ""
        except Exception:
            snippet = ""

        in_test = is_test_file(self.filepath)

        # 测试代码中的 HIGH 风险降级为 MEDIUM (受 pytest 收集影响)
        effective_risk = risk
        if risk == "HIGH" and in_test:
            effective_risk = "MEDIUM"

        finding = DynamicLoadFinding(
            file=rel_path,
            line=node.lineno,
            col=node.col_offset,
            function=resolved,
            risk_level=effective_risk,
            code_snippet=snippet[:120],  # 截断长行
            in_test=in_test,
            suggestion=get_suggestion(pattern, in_test),
        )
        self.findings.append(finding)


def scan_file(filepath: Path, root: Path) -> List[DynamicLoadFinding]:
    """扫描单个 Python 文件"""
    if is_excluded(filepath, root):
        return []
    if filepath.suffix != ".py":
        return []

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []
    except Exception:
        return []

    visitor = DynamicLoadVisitor(filepath, root)
    visitor.visit(tree)
    return visitor.findings


def scan_directory(root: Path) -> ScanReport:
    """扫描整个目录"""
    report = ScanReport(root=str(root), scanned_files=0)
    for pyfile in root.rglob("*.py"):
        if is_excluded(pyfile, root):
            continue
        report.scanned_files += 1
        findings = scan_file(pyfile, root)
        report.findings.extend(findings)

    # 按文件名 + 行号排序
    report.findings.sort(key=lambda f: (f.file, f.line))
    return report


def print_report(report: ScanReport):
    """打印扫描报告 (文本格式)"""
    print(f"{'='*70}")
    print(f"动态加载模块风险扫描报告")
    print(f"{'='*70}")
    print(f"扫描根目录: {report.root}")
    print(f"扫描文件数: {report.scanned_files}")
    print(f"总发现数:   {len(report.findings)}")
    print(f"  HIGH:    {len(report.high_risk)}")
    print(f"  MEDIUM:  {len(report.medium_risk)}")
    print(f"  LOW:     {len(report.low_risk)}")
    print()

    if not report.findings:
        print("[OK] 未发现动态加载调用")
        return

    # 按风险等级分组打印
    for risk_name, findings in [("HIGH", report.high_risk),
                                  ("MEDIUM", report.medium_risk),
                                  ("LOW", report.low_risk)]:
        if not findings:
            continue
        print(f"{'─'*70}")
        print(f"[{risk_name}] {len(findings)} 处")
        print(f"{'─'*70}")
        for f in findings:
            test_tag = " [test]" if f.in_test else ""
            print(f"  {f.file}:{f.line}{test_tag}")
            print(f"    函数: {f.function}")
            print(f"    代码: {f.code_snippet}")
            if f.suggestion:
                print(f"    建议: {f.suggestion}")
            print()


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描项目动态加载模块的潜在风险")
    parser.add_argument("--root", default=str(ROOT),
                        help="扫描根目录 (默认: 项目根)")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 格式")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    report = scan_directory(root)

    if args.json:
        data = {
            "root": report.root,
            "scanned_files": report.scanned_files,
            "total_findings": len(report.findings),
            "high_risk_count": len(report.high_risk),
            "medium_risk_count": len(report.medium_risk),
            "low_risk_count": len(report.low_risk),
            "findings": [asdict(f) for f in report.findings],
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    # 退出码: 有 HIGH 风险返回 1
    return 1 if report.high_risk else 0


if __name__ == "__main__":
    sys.exit(main())
