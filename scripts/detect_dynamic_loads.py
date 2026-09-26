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

退出码 (两种输出模式共用同一行代码, 口径一致, 见 main()):
    0 = 无 HIGH;  1 = 存在 HIGH。MEDIUM/LOW 不参与退出码。

审计豁免 (AUDITED_DYNAMIC_LOAD_EXEMPTIONS):
    对"已人工审计、边界已证明"的个别调用点, 把 HIGH 降为 MEDIUM(发现仍照常出现在
    报告/JSON 中, 带 exempted_by 标记), 使其不再阻断 push-master 门禁。
    匹配键为 (文件, 所在函数, 动态加载函数名) 三元组全等 + 每组合命中配额,
    **不是**文件级/目录级放宽。未命中的豁免条目会在 stderr 告警(防腐)。
    依据与守护测试: docs/audit_skill_governance/DYNGATE1.md,
    tests/unit/test_dynamic_loads_high_exemption.py
"""
from __future__ import annotations
import os
import sys
import ast
import json
import logging
import argparse
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Set, Optional, Tuple

# [变易] 诊断日志: 输出到 stderr, 保证 --json 模式 stdout 纯净 (报告即 stdout).
# 级别由环境变量 DETECT_LOG_LEVEL 控制 (DEBUG 时输出受控降级判定全过程):
#   DETECT_LOG_LEVEL=DEBUG python scripts/detect_dynamic_loads.py --json
_logger = logging.getLogger("detect_dynamic_loads")
_console = logging.StreamHandler(sys.stderr)
_console.setFormatter(logging.Formatter("[detect] %(levelname)s %(message)s"))
_logger.addHandler(_console)
_logger.setLevel(os.environ.get("DETECT_LOG_LEVEL", "INFO").upper()
                 if os.environ.get("DETECT_LOG_LEVEL") else logging.INFO)

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


@dataclass(frozen=True)
class AuditedDynamicLoadExemption:
    """人工审计过的动态加载豁免（**窄口径，逐调用点**）

    [不易] 豁免的匹配键是三元组 (file, qualname, pattern) **全等**，
      再叠加人工审计的命中配额 max_matches：
        - file     : 仓库相对路径（POSIX 分隔符），必须是具体文件，**不得**是目录前缀
        - qualname : 该调用所在的函数限定名（点号连接），**不是**整个文件
        - pattern  : DYNAMIC_LOAD_PATTERNS 里的函数名（spec_from_file_location 等）
        - max_matches: 该三元组最多豁免几次。超出配额的那一次**保持 HIGH**
          ⇒ 有人在同一函数里再塞一个动态加载，不会顺带被豁免。
      任一维度不同 ⇒ 不豁免。因此本机制**不可能**表现为"整个文件/整个目录豁免"。

    [为什么保留这种机制而不是删规则] 删规则或把 gate 改成 continue-on-error
      会让**所有**动态加载消失于门禁之外；这里只是把**已经人工看过、且证明了边界**
      的调用点从 HIGH 降为 MEDIUM —— 发现仍然照常出现在报告与 --json 里
      （见 DynamicLoadFinding.exempted_by），只是不再阻断。
    """
    file: str
    qualname: str
    pattern: str
    reason: str
    max_matches: int = 1
    evidence: str = ""


#: 全仓库仅此一条（两个 pattern 同属一个调用点），见 docs/audit_skill_governance/DYNGATE1.md
AUDITED_DYNAMIC_LOAD_EXEMPTIONS: Tuple[AuditedDynamicLoadExemption, ...] = (
    AuditedDynamicLoadExemption(
        file="agent/tools/persistence.py",
        qualname="_import_module_from_path",
        pattern="spec_from_file_location",
        max_matches=1,
        reason=(
            "受控目录遍历加载（设计内的有界加载，非外部输入注入）："
            "persistence.py:410 是该函数的唯一调用点，实参 path 来自 "
            "_iter_custom_modules() 对固定常量 CUSTOM_TOOLS_DIR(<repo>/agent/tools/custom) "
            "的 os.walk 结果；调用方不接收任何用户/网络输入，因此不构成"
            "'路径可控 ⇒ 任意文件加载'。模块内容本身是云枢自生成(LLM)代码，"
            "其治理边界不在 importlib 调用，而在 register_all + 默认 "
            "risk=critical 的 YAML 治理声明（调用前仍需审批）。"
        ),
        evidence="tests/unit/test_dynamic_loads_high_exemption.py",
    ),
    AuditedDynamicLoadExemption(
        file="agent/tools/persistence.py",
        qualname="_import_module_from_path",
        pattern="module_from_spec",
        max_matches=1,
        reason=(
            "同上：module_from_spec 接收的是上一行 spec_from_file_location 返回的 "
            "spec 对象，自身没有独立路径实参可供外部控制，随该受控调用点一并降级。"
        ),
        evidence="tests/unit/test_dynamic_loads_high_exemption.py",
    ),
)


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
    exempted_by: str = ""  # 非空 = 该条曾被人工审计豁免 (记录 file:qualname, 供审计追溯)


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
    "archive",  # 归档代码 (scripts/archive 等) 不参与生产, 不应阻断安全门禁
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
        # [变易] 已判定"受控 spec 加载"的文件 (相对路径): 该文件中 module_from_spec
        # 接收的 spec 必然来自受控的 spec_from_file_location (无独立路径参数可验证),
        # 跟随降级, 避免成对出现时 module_from_spec 单独残留 HIGH 误报.
        self._controlled_files: set[str] = set()
        # [变易] 模块级常量赋值表 (name → 值 AST): 供 _eval_const_path 解析名称引用,
        # 如 ARCHIVED_TOOL_ROUTER = os.path.join(...) 后
        # spec_from_file_location(name, ARCHIVED_TOOL_ROUTER) 的路径参数是变量名.
        self._module_consts: dict[str, ast.AST] = {}
        # [变易] 常量解析递归栈 (防止 A = B; B = A 循环引用死循环)
        self._eval_stack: set[str] = set()
        # [DYNGATE1] 当前所在函数的嵌套栈 (用于审计豁免的 qualname 匹配: 豁免锚定在
        # "具体文件 + 具体函数", 而不是整个文件)
        self._func_stack: List[str] = []
        # [DYNGATE1] 本文件内各 (file, qualname, pattern) 已被豁免的次数 (配额扣减)
        self._exemption_used: Dict[Tuple[str, str, str], int] = {}
        # [DYNGATE1] 本文件实际命中的豁免键集合 (供 scan_directory 做"陈旧豁免"自检)
        self.exemptions_used: Set[Tuple[str, str, str]] = set()

    def _collect_module_consts(self, tree: ast.Module):
        """预扫描模块级常量赋值 (仅顶层 Assign/AnnAssign, 忽略函数/类体内赋值)"""
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    self._module_consts[stmt.targets[0].id] = stmt.value
            elif isinstance(stmt, ast.AnnAssign):
                if isinstance(stmt.target, ast.Name) and stmt.value is not None:
                    self._module_consts[stmt.target.id] = stmt.value

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
                _logger.debug("%s:%d matched pattern=%s risk=%s",
                              self._rel_path(), node.lineno, pattern, risk)
                # [变易] 受控降级: 动态加载的目标是仓库内已有文件 (如加载
                # agent/orchestrator/dialog_state.py 绕过循环导入), 路径为代码
                # 常量, 参数不可被外部控制 → HIGH 降为 MEDIUM, 避免误报阻断
                # daily 全量扫描. 判定为受控后, 该文件后续的 module_from_spec
                # (接收上述 spec 对象, 无独立路径参数) 跟随降级.
                if risk == "HIGH" and self._is_controlled_spec_load(node, pattern):
                    self._controlled_files.add(self._rel_path())
                    _logger.info("degrade HIGH->MEDIUM %s:%d (%s: repo-internal const path)",
                                 self._rel_path(), node.lineno, pattern)
                    risk = "MEDIUM"
                elif pattern == "module_from_spec" and self._rel_path() in self._controlled_files:
                    _logger.info("degrade HIGH->MEDIUM %s:%d (module_from_spec follows controlled spec)",
                                 self._rel_path(), node.lineno)
                    risk = "MEDIUM"
                # [DYNGATE1] 人工审计豁免: 仅对已在 AuditedDynamicLoadExemptions 中
                # 逐调用点登记过的 (文件, 函数, 加载函数名) 降级, 且受配额约束.
                # 未登记的调用点 (含同文件其它函数) 一律保持 HIGH.
                exempted_by = ""
                if risk == "HIGH":
                    _ex, _key = self._lookup_audited_exemption(node, pattern)
                    if _ex is not None:
                        self._exemption_used[_key] = self._exemption_used.get(_key, 0) + 1
                        self.exemptions_used.add(_key)
                        exempted_by = f"{_ex.file}:{_ex.qualname}"
                        _logger.info(
                            "exempt HIGH->MEDIUM %s:%d (%s in %s: audited, see %s)",
                            self._rel_path(), node.lineno, pattern,
                            _ex.qualname, _ex.evidence or "AUDITED_DYNAMIC_LOAD_EXEMPTIONS")
                        risk = "MEDIUM"
                self._add_finding(node, pattern, risk, resolved, exempted_by=exempted_by)
                break

        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        """跟踪函数嵌套栈 (审计豁免的 qualname 锚点)"""
        self._func_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def _qualname(self) -> str:
        """当前调用点所在函数的限定名 (点号连接; 模块级代码为空串)"""
        return ".".join(self._func_stack)

    def _lookup_audited_exemption(self, node: ast.Call, pattern: str):
        """窄口径豁免查表: (file, qualname, pattern) 三元组全等 + 未超配额

        返回 (exemption, key) 或 (None, key)。任何一维不匹配、或该组合的命中次数
        已达 max_matches ⇒ 返回 None (保持 HIGH)。
        """
        file_rel = self._exemption_rel_path()
        key = (file_rel, self._qualname(), pattern)
        for ex in AUDITED_DYNAMIC_LOAD_EXEMPTIONS:
            if (ex.file, ex.qualname, ex.pattern) != key:
                continue
            if self._exemption_used.get(key, 0) >= ex.max_matches:
                _logger.warning(
                    "exempt quota exhausted %s:%d (%s in %s: max_matches=%d) -> keep HIGH",
                    file_rel, node.lineno, pattern, ex.qualname, ex.max_matches)
                return None, key
            return ex, key
        return None, key

    def _rel_path(self) -> str:
        """返回当前扫描文件的仓库相对路径 (用于受控文件集合匹配)"""
        try:
            return str(self.filepath.relative_to(self.root))
        except ValueError:
            return str(self.filepath)

    def _exemption_rel_path(self) -> str:
        """审计豁免的匹配路径 —— **恒以仓库根 ROOT 为基准**, 统一为 POSIX 分隔符。

        [不易] 与 _is_controlled_spec_load 同口径: 豁免条目按仓库相对路径登记,
          若用 self.root (扫描根) 计算, `--root agent` 会得到 "tools/persistence.py"
          而默认全仓扫描得到 "agent/tools/persistence.py" —— 同一份代码在两种
          扫描方式下结论不一致(实测踩过)。只有扫描根之外的临时文件才退回 self.root。
        """
        for base in (ROOT, self.root):
            try:
                return str(self.filepath.relative_to(base)).replace("\\", "/")
            except ValueError:
                continue
        return str(self.filepath).replace("\\", "/")

    def _is_controlled_spec_load(self, node: ast.Call, pattern: str) -> bool:
        """判断动态加载的目标是否指向仓库内已有文件 (受控加载)

        [不易] 仅放宽"路径为代码常量且指向仓库内已存在文件"的场景:
          - 位置参数第 2 个 (args[1]) 或关键字 location/pathname 为加载路径
          - 路径为字符串常量 (非外部输入), 且相对仓库根解析后是真实文件
          - 绝对路径/非常量路径 (可能被外部控制) 一律不降级, 保持 HIGH
        """
        path_arg = None
        if pattern == "spec_from_file_location":
            path_arg = node.args[1] if len(node.args) >= 2 else None
            if path_arg is None:
                for kw in node.keywords:
                    if kw.arg == "location":
                        path_arg = kw.value
                        break
        elif pattern == "load_source":
            # imp.load_source(name, pathname, ...): 第 2 个位置参数为路径
            path_arg = node.args[1] if len(node.args) >= 2 else None

        # [变易] 支持 os.path.join 常量拼接路径: 如 cicd_pipeline/stress_test_pipeline
        # 用 os.path.join(dirname(__file__), "docs", "archive", ...) 拼接仓库内
        # 归档文件的绝对路径。这类路径完全由仓库内常量构成（无外部输入），
        # 与字面量路径同属受控加载，应降级为 MEDIUM 避免误报阻断。
        # [变易] 记录路径是否由常量表达式求值而来: 这类路径 (如
        # os.path.join(dirname(__file__), "docs", ...)) 求值后是绝对路径,
        # 但完全由仓库内常量构成, 指向仓库内文件时仍属受控加载.
        is_resolved_const = False
        if path_arg is not None and not isinstance(path_arg, ast.Constant):
            resolved_const = self._eval_const_path(path_arg)
            if resolved_const is not None:
                path_arg = ast.Constant(value=resolved_const)
                is_resolved_const = True

        if not isinstance(path_arg, ast.Constant) or not isinstance(path_arg.value, str):
            # 路径非常量 (变量/表达式, 可能被外部控制) 保持 HIGH
            _logger.debug("controlled-check %s:%d keep HIGH (path arg not const literal: %s)",
                          self._rel_path(), node.lineno,
                          type(path_arg).__name__ if path_arg is not None else "missing")
            return False
        p = Path(path_arg.value)
        if p.is_absolute():
            if is_resolved_const:
                # 常量表达式求值得到的绝对路径: 仅当指向仓库根内已存在文件时受控
                target = p.resolve()
                if target.is_file() and target.is_relative_to(ROOT.resolve()):
                    _logger.debug("controlled-check %s:%d const-eval absolute path inside repo: %s",
                                  self._rel_path(), node.lineno, path_arg.value)
                    return True
            _logger.debug("controlled-check %s:%d keep HIGH (absolute path: %s)",
                          self._rel_path(), node.lineno, path_arg.value)
            return False
        # [不易] 仓库内路径判定基于 ROOT (仓库根) 而非 self.root (扫描根):
        # 加载路径按仓库相对路径书写 (如 "agent/orchestrator/dialog_state.py"),
        # --root 指向子目录扫描时 (如 --root scripts), 用 self.root 解析会
        # 错误地得到不存在的路径 → 误判未受控 → 误报 HIGH.
        target = (ROOT / p).resolve()
        exists = target.is_file()
        _logger.debug("controlled-check %s:%d path=%r exists=%s",
                      self._rel_path(), node.lineno, path_arg.value, exists)
        return exists

    def _eval_const_path(self, node: ast.AST) -> str | None:
        """尝试对纯常量表达式求值为路径字符串（仅支持 os.path.join + 字面量拼接）。

        支持:
          - os.path.join(const, const, ...) / pathlib Path 拼接的常量参数
          - 常量二元运算 (字符串 + 字符串)
        不支持的（含变量/函数调用/外部输入）返回 None，保持原 HIGH 判定。
        """
        import os as _os
        # os.path.join(...) 调用
        if isinstance(node, ast.Call):
            func = node.func
            func_name = None
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute) and func.attr in ("join", "dirname", "abspath"):
                func_name = func.attr
            if func_name in ("join",):
                parts = []
                for arg in node.args:
                    v = self._eval_const_path(arg)
                    if v is None:
                        return None
                    parts.append(v)
                if not parts:
                    return None
                # 首个元素若是绝对路径或盘符则结果绝对；否则相对
                return _os.path.join(*parts)
            if func_name == "abspath":
                if len(node.args) == 1:
                    v = self._eval_const_path(node.args[0])
                    return _os.path.abspath(v) if v is not None else None
            if func_name == "dirname":
                if len(node.args) == 1:
                    v = self._eval_const_path(node.args[0])
                    return _os.path.dirname(v) if v is not None else None
            return None
        # 常量
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        # 名称引用: __file__ 或模块级常量 (如 ARCHIVED_TOOL_ROUTER = os.path.join(...))
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return str(self.filepath.resolve())
            if node.id in self._module_consts and node.id not in self._eval_stack:
                self._eval_stack.add(node.id)
                try:
                    return self._eval_const_path(self._module_consts[node.id])
                finally:
                    self._eval_stack.discard(node.id)
            return None
        # 二元拼接
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add,)):
            left = self._eval_const_path(node.left)
            right = self._eval_const_path(node.right)
            if left is not None and right is not None:
                return left + right
        return None

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

    def _add_finding(self, node: ast.Call, pattern: str, risk: str, resolved: str,
                     exempted_by: str = ""):
        """添加一条发现 (exempted_by 非空表示该条来自人工审计豁免)"""
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
            exempted_by=exempted_by,
        )
        self.findings.append(finding)


def scan_file(filepath: Path, root: Path,
              exemptions_used: Optional[Set[Tuple[str, str, str]]] = None) -> List[DynamicLoadFinding]:
    """扫描单个 Python 文件

    exemptions_used: 可选的跨文件累计集合, 用于统计本次扫描实际命中了哪些审计豁免
                     (未命中的豁免会被 _warn_stale_exemptions 报警).
    """
    if is_excluded(filepath, root):
        return []
    if filepath.suffix != ".py":
        return []

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        _logger.debug("skip %s (syntax error: %s)", filepath, e)
        return []
    except Exception as e:
        _logger.warning("skip %s (unexpected parse failure: %s: %s)",
                        filepath, type(e).__name__, e)
        return []

    visitor = DynamicLoadVisitor(filepath, root)
    visitor._collect_module_consts(tree)
    visitor.visit(tree)
    if exemptions_used is not None:
        exemptions_used |= visitor.exemptions_used
    return visitor.findings


def _warn_stale_exemptions(root: Path, used: Set[Tuple[str, str, str]]) -> None:
    """陈旧豁免自检：登记了的豁免若在本次扫描范围内一次都没命中，就告警。

    [不易] 这是豁免机制的"防腐"约束：重构把被豁免的调用点挪走/改名后，
      豁免条目会变成一条**看似仍在生效、实际已失效**的死条目；更糟的情况是
      它悄悄掩盖了后来新写入同函数的动态加载。告警走 stderr，不污染 --json stdout。
      仅当被豁免的文件确实落在本次扫描范围内时才告警（--root 指向子目录时不误报）。
    """
    for ex in AUDITED_DYNAMIC_LOAD_EXEMPTIONS:
        if (ex.file, ex.qualname, ex.pattern) in used:
            continue
        try:
            # 豁免条目按仓库相对路径登记 ⇒ 以 ROOT 解析, 再判定是否落在本次扫描范围内
            target = (ROOT / ex.file).resolve()
            in_scope = target.is_file() and target.is_relative_to(root.resolve())
        except (OSError, ValueError):
            in_scope = False
        if in_scope:
            _logger.warning(
                "stale exemption (registered but never matched in this scan): %s %s %s",
                ex.file, ex.qualname, ex.pattern)


def scan_directory(root: Path) -> ScanReport:
    """扫描整个目录"""
    report = ScanReport(root=str(root), scanned_files=0)
    exemptions_used: Set[Tuple[str, str, str]] = set()
    for pyfile in root.rglob("*.py"):
        if is_excluded(pyfile, root):
            continue
        report.scanned_files += 1
        findings = scan_file(pyfile, root, exemptions_used=exemptions_used)
        report.findings.extend(findings)

    # 按文件名 + 行号排序
    report.findings.sort(key=lambda f: (f.file, f.line))
    _warn_stale_exemptions(root, exemptions_used)
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
            exempt_tag = f" [exempt: {f.exempted_by}]" if f.exempted_by else ""
            print(f"  {f.file}:{f.line}{test_tag}{exempt_tag}")
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
    _logger.info("scan start root=%s json=%s", root, args.json)
    report = scan_directory(root)

    if args.json:
        # [变易] Windows 本地 stdout 默认 locale 编码 (GBK/cp936), ensure_ascii=False
        # 的中文 JSON 会报 UnicodeEncodeError 或写出空文件. 显式切 utf-8 保证
        # --json 报告可被任意平台解析. CI (Linux, UTF-8) 不受影响.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass  # stdout 被替换/重定向 (如 pytest capsys) 时不支持 reconfigure
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
    # [不易] 退出码只取决于 report.high_risk (= risk_level=="HIGH" 的条数),
    # 文本模式与 --json 模式**共用这一行**, 不存在两种裁决口径;
    # MEDIUM/LOW/INFO 以及被审计豁免降级后的条目都不影响退出码.
    _logger.info("scan done files=%d findings=%d high=%d exempt=%d -> exit=%d",
                 report.scanned_files, len(report.findings),
                 len(report.high_risk),
                 sum(1 for f in report.findings if f.exempted_by),
                 1 if report.high_risk else 0)
    return 1 if report.high_risk else 0


if __name__ == "__main__":
    sys.exit(main())
