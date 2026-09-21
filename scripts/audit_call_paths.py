"""调用路径全清单 + 防再犯检测（TASK-05 第 0 步 / E1b）

## 为什么需要它（本任务最有价值的交付物之一）

`TASK-05` §2.3b 的核心结论：

> **v1.4 的"统一 Registry"只管得住"注册"，管不住"绕过它的直调"。**
> 若不把「**调用路径收敛**」与「**Registry 收敛**」并列为两条独立目标，
> 重构完成后**仍会有能力在治理之外被执行**。

`.github/workflows/ci.yml` 的 `knowledge-audit-smoke` job 就是活证据：CI **确实
已经在执行能力**，而且**绕过了全部治理**。所以本脚本要做的不是"一次盘点"，
而是把「**调用路径必须收敛**」变成一条**可持续的不变量**：

    python scripts/audit_call_paths.py           # 打印全清单
    python scripts/audit_call_paths.py --json out.json
    python scripts/audit_call_paths.py --check   # 未登记直调 / 白名单腐化 ⇒ 退出码 1

## 四路取证（**不只查已知的那 4 处**）

| 路 | 方法 | 抓什么 |
|---|---|---|
| P1 | AST：**按 import 解析出的** `agent.tools.call()` 调用点 | 收口路径（过闸门），并检查**是否带身份** |
| P2 | AST：**已注册 handler 的跨模块直调**（按 import 解析） | 通用"绕过"检测 |
| P3 | AST：`X.call_tool(...)` 这类**远程执行原语** | 远端 MCP 直调（如 `mcp_adapter.py:254`） |
| P4 | AST：`agent/tools/*.py` 里**未注册**但被外部调用的函数 | 能力形态函数漏登记（低危） |

**为什么要按 import 解析，而不是比名字**：纯名字匹配会把
`subprocess.call` / `breaker.call` / `env.call` 误判成收口路径，
也会把 `scripts/*.py` 自己定义的 `_git` / `_write_file` 误判成对
`agent/tools/git_tools.py::_git` 的直调（实测：这两类假阳性占了首轮输出的 80%）。
⇒ 判据必须是「**该名字在本模块里确实绑定到 `agent.tools` / handler 的定义模块**」。

## 违规范围（**明确声明，不含糊**）

`--check` 的**硬失败**只针对**本地能力平面的生产代码**：
`agent/`、`plugins/`、`cloudshu/`。理由：`mcp_services/` 下除
`yunshu_mcp_server.py`（本机 stdio 服务端）外，其余是**面向外部 MCP 服务端的
客户端与演示/自测脚本**，它们执行的不是本 Registry 管辖的能力。
这些路径仍然**全部列进清单**（`P3` 段），只是不计入退出码 ——
"看得见但不拦"和"看不见"是两回事。

## 锚点纪律（**不要用行号做锚点**）

`TASK-05` 的 2026-09-20 预检已经证明行号会漂移：任务书写的是 `ci.yml:562`，
实测已迁到 **`ci.yml:726`**。⇒ `EXEMPT_CALL_SITES` 的键一律是
**`相对路径::符号名`**，行号只作输出信息。

## `--check` 的两条不变量

1. **未登记的直调 ⇒ 退出码 1**（E1b 硬要求，负例必须实测）；
2. **已登记但**在代码里**再也找不到对应符号** ⇒ 退出码 1
   （防"什么都放行"的白名单随时间腐化成空转声明 —— 与 `TASK-00` §0.2b
   "否定式结论要换口径复测"同一纪律）。
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.capregistry.call_sites import (  # noqa: E402
    DEAD_MODULES, EXEMPT_CALL_SITES, VIOLATION_SCOPE_PREFIXES, anchor_key)

#: 参与扫描的受控顶层目录（`tests/` 与 `docs/` 不入账：它们不是生产调用路径）
_SCAN_ROOTS: Tuple[str, ...] = (
    "agent", "mcp_services", "plugins", "scripts", "cloudshu",
    "core", "sensor", "memory", "planning", "persona", "cognitive",
    "lifetrace", "utils",
)

_REGISTER_NAMES: frozenset = frozenset(
    {"register", "register_dynamic", "register_tool", "register_handler"})

#: `agent/tools/__init__.py::call` 的常见裸别名（无 import 可解析时的兜底）
_FUNNEL_ALIASES: frozenset = frozenset(
    {"call_tool", "_tool_call", "_tools_call", "tools_call"})

#: 指向本地工具注册表的模块名（属性链的基座）
_TOOLS_MODULES: frozenset = frozenset(
    {"agent.tools", "agent.tools.__init__", "tools"})

#: 远程执行原语（属性名）——远端 MCP 直调的指纹
_REMOTE_PRIMITIVES: frozenset = frozenset(
    {"call_tool", "callTool", "invoke_tool", "invokeTool"})

#: 名字太通用，跨模块同名**不能证明**是同一个东西（否则假阳性淹没真问题）
_GENERIC_NAMES: frozenset = frozenset(
    {"handler", "func", "fn", "callback", "wrapper", "inner", "action",
     "test_func", "my_func", "dummy", "noop", "probe", "target", "step",
     "greet", "echo", "make", "build", "run", "main", "f", "g", "h",
     "check", "setup", "helper", "process", "handle", "outer"})

_MIN_HANDLER_NAME_LEN = 5


@dataclass
class Finding:
    """一条调用路径"""

    file: str
    lineno: int
    symbol: str
    callee: str = ""
    capability: str = ""
    path_kind: str = "funnel"      # funnel | direct | remote_primitive | unregistered
    trigger: str = ""
    via_registry: bool = False
    via_gate: bool = False
    has_identity: bool = False
    reachable: bool = True
    reachability: str = ""
    exempt: bool = False
    exempt_reason: str = ""
    exempt_identity: str = ""
    exempt_audit: str = ""
    #: 该条是否计入 `--check` 的硬失败
    in_scope: bool = True

    @property
    def anchor(self) -> str:
        return anchor_key(self.file, self.symbol)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file, "lineno": self.lineno, "symbol": self.symbol,
            "anchor": self.anchor, "callee": self.callee,
            "capability": self.capability, "path_kind": self.path_kind,
            "trigger": self.trigger, "via_registry": self.via_registry,
            "via_gate": self.via_gate, "has_identity": self.has_identity,
            "reachable": self.reachable, "reachability": self.reachability,
            "in_scope": self.in_scope, "exempt": self.exempt,
            "exempt_reason": self.exempt_reason,
            "exempt_identity": self.exempt_identity,
            "exempt_audit": self.exempt_audit,
        }


# ════════════════════════════════════════════════════════════
#  文件枚举与解析
# ════════════════════════════════════════════════════════════


def tracked_python_files(root: str) -> List[str]:
    """`git ls-files` 的受控 Python 文件（排除 tests/ 与 docs/）

    【为什么用 `git ls-files` 而不是 `os.walk`】磁盘上有 `.worktrees`(13,808 py)、
    `.pytest_tmp`(6,929)、`backup`(5,511)、`security-tools`、`.devtools/pylibs`
    等**非受控副本**。`os.walk` 会把它们全部算成调用点，制造几百条假阳性
    （实测：`get_file_info` 的"定义处"会落到某个测试副本里）。
    """
    try:
        out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=root,
                             capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
        print(f"[audit_call_paths] git ls-files 失败，退回 os.walk: {exc}",
              file=sys.stderr)
        return _walk_python(root)
    raw = out.stdout.decode("utf-8", errors="replace")
    files: List[str] = []
    for rel in raw.split("\0"):
        rel = rel.strip()
        if not rel or not rel.endswith(".py"):
            continue
        top = rel.split("/", 1)[0]
        if top in ("tests", "docs") or top not in _SCAN_ROOTS:
            continue
        files.append(rel)
    return sorted(files)


def _walk_python(root: str) -> List[str]:  # pragma: no cover - 兜底
    skip = {"__pycache__", ".worktrees", ".pytest_tmp", "backup", "venv",
            "build", ".git", "tests", "docs", ".devtools", "node_modules"}
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for f in filenames:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, f), root).replace("\\", "/")
                if rel.split("/", 1)[0] in _SCAN_ROOTS:
                    out.append(rel)
    return sorted(out)


#: AST 解析缓存：`(root, rel, mtime_ns, size) -> Optional[ast.Module]`
#
# 【为什么需要（2026-09-21 实测）】`_parse` 有 4 个调用点（`_collect_findings` 主循环、
#   `module_bindings` 侧、`_symbol_exists`、`_symbol_lineno`），后两者是**按 finding
#   调用**的 ⇒ 同一次冷扫描里同一文件被反复解析。实测：1396 个受控文件触发
#   **2802 次 `_parse`（2.01× 重复，其中 5 个文件被解析 4 次）**，
#   `_collect_findings` 冷跑 16.3s。
#
# 【为什么键里放 mtime_ns + size，而不是只放 rel】只按路径缓存会让"改了文件却沿用旧
#   AST"成为可能 —— 而 `--check` 是**门禁结论**，陈旧结论比超时更危险（与上方
#   `_RAW_SCAN_CACHE` 的失效判据同一条理由，此处复用同一套指纹语义）。
#   文件在两次 stat 之间被改写（mtime_ns 与 size 双双不变）的概率可忽略。
#
# 【线程/进程安全】`ast.parse` 的返回值本模块只读不改（全部是 `ast.walk`/`visit`），
#   故跨调用共享安全。pytest-xdist 是**多进程**，各进程独立缓存，无共享问题；
#   进程内用独立锁与 `_RAW_SCAN_LOCK` 解耦，避免与 scan 的临界区互相等待。
_PARSE_CACHE_MAX = 4096
_PARSE_CACHE: "OrderedDict[Tuple[str, str, int, int], Optional[ast.Module]]" = OrderedDict()
_PARSE_CACHE_LOCK = threading.Lock()
_PARSE_CACHE_STATS: Dict[str, int] = {"hit": 0, "miss": 0, "evict": 0}


def _parse(root: str, rel: str) -> Optional[ast.Module]:
    """解析一个受控文件为 AST（**带进程级缓存**，见 `_PARSE_CACHE` 的说明）

    返回的 AST 由调用方**只读**使用；缓存会跨调用共享同一对象，
    因此任何调用点都**不得就地修改**返回的 AST。
    """
    path = os.path.join(root, rel)
    try:
        st = os.stat(path)
        key: Tuple[str, str, int, int] = (root, rel, st.st_mtime_ns, st.st_size)
    except OSError:
        # stat 失败：不缓存（文件可能刚被删除），直接走一次解析尝试
        try:
            with open(path, "r", encoding="utf-8") as f:
                return ast.parse(f.read(), rel)
        except (OSError, SyntaxError, UnicodeDecodeError):
            return None

    with _PARSE_CACHE_LOCK:
        if key in _PARSE_CACHE:
            _PARSE_CACHE_STATS["hit"] += 1
            _PARSE_CACHE.move_to_end(key)
            return _PARSE_CACHE[key]

    try:
        with open(path, "r", encoding="utf-8") as f:
            tree: Optional[ast.Module] = ast.parse(f.read(), rel)
    except (OSError, SyntaxError, UnicodeDecodeError):
        tree = None

    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE_STATS["miss"] += 1
        _PARSE_CACHE[key] = tree
        _PARSE_CACHE.move_to_end(key)
        while len(_PARSE_CACHE) > _PARSE_CACHE_MAX:
            _PARSE_CACHE.popitem(last=False)
            _PARSE_CACHE_STATS["evict"] += 1
    return tree


def parse_cache_clear() -> None:
    """清空 AST 解析缓存（测试与长驻进程用；改完工作区想强制重扫也可先调它）"""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()


def parse_cache_info() -> Dict[str, int]:
    """AST 解析缓存统计（hit / miss / evict / entries）—— 供测试锁定"重复解析被消除" """
    with _PARSE_CACHE_LOCK:
        info = dict(_PARSE_CACHE_STATS)
        info["entries"] = len(_PARSE_CACHE)
        return info


def module_of(rel: str) -> str:
    """`agent/tools/git_tools.py` → `agent.tools.git_tools`"""
    m = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


class _SymbolIndex(ast.NodeVisitor):
    """记录每个 `ast.Call` 所在的**符号名**（生成稳定锚点）"""

    def __init__(self) -> None:
        self.stack: List[str] = []
        self.parent: Dict[int, str] = {}

    def _visit_def(self, node: Any) -> None:
        self.stack.append(node.name)
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self.stack.pop()

    visit_FunctionDef = _visit_def
    visit_AsyncFunctionDef = _visit_def
    visit_ClassDef = _visit_def

    def visit_Call(self, node: ast.Call) -> None:
        self.parent[id(node)] = "::".join(self.stack) or "<module>"
        self.generic_visit(node)


def _callee_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    return f.id if isinstance(f, ast.Name) else ""


def _callee_dotted(node: ast.Call) -> str:
    f = node.func
    parts: List[str] = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def _base_name(node: ast.Call) -> str:
    """属性链的基座名（`a.b.c(...)` → `a`）"""
    f = node.func
    while isinstance(f, ast.Attribute):
        f = f.value
    return f.id if isinstance(f, ast.Name) else ""


# ════════════════════════════════════════════════════════════
#  import 绑定表（**判据的核心**）
# ════════════════════════════════════════════════════════════


def module_bindings(tree: ast.Module) -> Dict[str, Tuple[str, str]]:
    """`本模块里的名字 → (来源模块, 原始名)`

    覆盖三种形态：
        `from agent.tools import call as call_tool`  → {"call_tool": ("agent.tools","call")}
        `from agent.tools.git_tools import _git`     → {"_git": ("agent.tools.git_tools","_git")}
        `import agent.tools.file_tools_reg as ftr`   → {"ftr": ("agent.tools.file_tools_reg","")}
    """
    binds: Dict[str, Tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if a.name == "*":
                    continue
                binds[a.asname or a.name] = (node.module, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                binds[a.asname or a.name.split(".")[0]] = (a.name, "")
    return binds


def _is_funnel(node: ast.Call, binds: Dict[str, Tuple[str, str]]) -> bool:
    """是否是 `agent/tools/__init__.py::call()` 的调用点"""
    f = node.func
    if isinstance(f, ast.Name):
        src = binds.get(f.id)
        if src and src[0] in _TOOLS_MODULES and src[1] == "call":
            return True
        # 裸别名（例如 `call_tool(...)` 但 import 写在别的模块/别处）
        return f.id in _FUNNEL_ALIASES
    if isinstance(f, ast.Attribute) and f.attr == "call":
        base = _base_name(node)
        src = binds.get(base)
        if src and src[0] in _TOOLS_MODULES and (src[1] == "" or src[1] == "tools"):
            return True
        return base in ("tools", "_tools")
    return False


def _handler_hits(node: ast.Call, binds: Dict[str, Tuple[str, str]],
                  handlers: Dict[str, List["RegisteredHandler"]]
                  ) -> List["RegisteredHandler"]:
    """该调用是否**确实**指向某个已注册 handler（按 import 解析）"""
    f = node.func
    if isinstance(f, ast.Name):
        src = binds.get(f.id)
        if src is None:
            return []
        mod, orig = src
        return [d for d in handlers.get(orig or f.id, []) if module_of(d.module) == mod]
    if isinstance(f, ast.Attribute):
        base = _base_name(node)
        src = binds.get(base)
        if src is None or not src[0]:
            return []
        return [d for d in handlers.get(f.attr, []) if module_of(d.module) == src[0]]
    return []


def _is_remote_primitive(node: ast.Call, binds: Dict[str, Tuple[str, str]]) -> bool:
    """`X.call_tool(...)` 形态（且 `X` **不是** `agent.tools` 的别名）"""
    f = node.func
    if not isinstance(f, ast.Attribute) or f.attr not in _REMOTE_PRIMITIVES:
        return False
    base = _base_name(node)
    src = binds.get(base)
    if src and src[0] in _TOOLS_MODULES:
        return False
    return base not in ("tools", "_tools")


@dataclass
class RegisteredHandler:
    name: str
    module: str
    lineno: int
    capability: str
    via: str


def collect_registered_handlers(root: str, files: Sequence[str]
                                ) -> Dict[str, List[RegisteredHandler]]:
    """建**注册面**：handler 函数名 → 注册点（装饰器 + `handler=` 两种形态）"""
    table: Dict[str, List[RegisteredHandler]] = {}
    for rel in files:
        tree = _parse(root, rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    call = dec if isinstance(dec, ast.Call) else None
                    if call is None or _callee_name(call) not in _REGISTER_NAMES:
                        continue
                    cap = ""
                    if call.args and isinstance(call.args[0], ast.Constant):
                        cap = str(call.args[0].value or "")
                    table.setdefault(node.name, []).append(RegisteredHandler(
                        node.name, rel, node.lineno, cap or node.name, "decorator"))
            if isinstance(node, ast.Call) and _callee_name(node) in _REGISTER_NAMES:
                for kw in node.keywords:
                    if kw.arg == "handler" and isinstance(kw.value, ast.Name):
                        cap = ""
                        if node.args and isinstance(node.args[0], ast.Constant):
                            cap = str(node.args[0].value or "")
                        table.setdefault(kw.value.id, []).append(RegisteredHandler(
                            kw.value.id, rel, node.lineno, cap or kw.value.id, "kwarg"))
    return table


def _scope_has_identity(tree: ast.Module, sym: _SymbolIndex, leaf: str) -> bool:
    """该函数作用域内是否**显式**带身份（三选一即算）"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        key = sym.parent.get(id(node), "")
        if (key.split("::")[-1] if key else "") != leaf:
            continue
        if _callee_name(node) == "set_session_source":
            return True
        if any(kw.arg in ("session_source", "identity", "callable_by")
               for kw in node.keywords):
            return True
    return False


_TRIGGER_RULES: Tuple[Tuple[str, str], ...] = (
    ("mcp_services/", "mcp"),
    ("plugins/", "plugin"),
    ("scripts/", "script"),
    ("cloudshu/", "script"),
    ("agent/server_routes/", "http"),
    ("agent/knowledge/__main__.py", "ci"),
    ("agent/orchestrator/", "llm"),
    ("agent/tool_calling.py", "llm"),
    ("agent/tools/", "internal"),
)


def _trigger_of(rel: str) -> str:
    for prefix, trig in _TRIGGER_RULES:
        if rel.startswith(prefix):
            return trig
    return "internal"


def _in_scope(rel: str) -> bool:
    return any(rel.startswith(p) for p in VIOLATION_SCOPE_PREFIXES)


# ════════════════════════════════════════════════════════════
#  可达性（**可复算**，不靠注释里的断言）
# ════════════════════════════════════════════════════════════


def _sdk_available() -> bool:
    try:
        import mcp  # noqa: F401,PLC0415
        return True
    except ImportError:
        return False


def _module_importers(root: str, module: str) -> List[str]:
    """生产代码里 **import 该模块**的位置（排除 tests/docs/非受控副本）

    【不易·必须匹配"import 语句"而不是"出现该字符串"】本脚本第一版用
    `git grep -F <模块名>`，结果把**本文件自己写下的模块名字符串**（例外表里那句
    "实测 `git grep -l agent.mcp_executor` 只命中…"）算成了一个 importer ⇒
    `agent/mcp_executor.py` 被误判为"有生产调用方"、进而误判为可达。
    实测踩到：`TestCallPathInventory::test_死模块被标为不可达` 变红。
    ⇒ 判据收紧成"**真的有一条 import 语句**"，这样文档/注释/例外表里的提及不会污染结论。
    """
    pattern = (r"(^|[[:space:]])(from|import)[[:space:]]+"
               + re.escape(module) + r"([[:space:].]|$)")
    try:
        out = subprocess.run(["git", "grep", "-l", "-E", pattern, "--", "*.py"],
                             cwd=root, capture_output=True, check=False)
    except OSError:  # pragma: no cover
        return []
    text = out.stdout.decode("utf-8", errors="replace")
    res = []
    for rel in text.splitlines():
        rel = rel.strip()
        if not rel or rel.startswith(("tests/", "docs/")):
            continue
        if rel.split("/", 1)[0] not in _SCAN_ROOTS:
            continue
        res.append(rel)
    return res


def _reachability(root: str, file: str, symbol: str, callee: str,
                  path_kind: str) -> Tuple[bool, str]:
    """静态判定该路径**当前是否可达**（不执行任何代码）

    判据优先级：
      1. **死模块**（`DEAD_MODULES`）⇒ **没有生产调用方** ⇒ 不可达。
         【为什么排在远程原语之前】"没人从生产代码里用它"是比"SDK 没装"**更强**的
         不可达事实（后者装上就生效，前者要有人重新接线才生效）。
         【口径修正（实测）】判据从"零 importer"改成"**零生产 importer**"：
         `agent/mcp_executor.py` 被 `scripts/check_mcp_log_level.py`（一个只调日志级别的
         运维脚本）import ⇒ 严格说**有一个 importer**，但 `agent/`、`plugins/`、
         `cloudshu/` 内**没有任何调用方**。把"零 importer"当判据会得出错误结论
         （实测踩到：脚本被误判为可达），故改用"生产范围内有无 importer"。
      2. **远程原语** ⇒ 取决于官方 `mcp` SDK 是否安装（实测未装 ⇒ 不可达）。
         必须**实测**而不是硬编码 `False`：硬编码会在 SDK 装上后静默过期
         —— 那正是"一旦 SDK 装上即生效"要防的。
      3. 其余 ⇒ 可达。
    """
    if file in DEAD_MODULES:
        mod = module_of(file)
        importers = [p for p in _module_importers(root, mod) if p != file]
        prod = [p for p in importers if _in_scope(p)]
        if not prod:
            others = ("；非生产引用：" + "、".join(importers)) if importers else ""
            return False, (f"死模块：**生产代码零调用方**（实测 `git grep -l {mod}` "
                           f"仅命中自身/运维脚本/测试）⇒ 不可达{others}")
        return True, f"有生产 importer（{len(prod)} 处）：{prod[:3]}"
    if path_kind == "remote_primitive":
        if _sdk_available():
            return True, "官方 mcp SDK 已安装 ⇒ 该远程直调路径**当前可达**"
        return False, ("官方 mcp SDK 未安装（实测 ImportError）⇒ 当前不可达；"
                       "**一旦装上即生效**")
    return True, "静态判定为可达"


# ════════════════════════════════════════════════════════════
#  主扫描：进程级缓存 + 两阶段（昂贵且与配置无关 / 廉价但依赖配置）
#
#  【不易·2026-09-21】遗留 L3 的另一半
# ════════════════════════════════════════════════════════════
# 动机（**实测**，见 `_ci_logs/t10/scan_profile_before.txt`）：
#   `scan()` 单次 **17.7s**，**同进程第二次仍 18.7s**（完全无缓存）；
#   其中 `ast.parse` 6.9s、`module_bindings`(ast.walk) 2.1s、`_SymbolIndex.visit` 2.5s。
#   而 `tests/unit/test_capregistry_callpaths_routes.py` 有 1 个 module 级 `scan()` 夹具
#   + 5 次 `main(["--check"])` ⇒ **单进程要跑 6 次全仓 AST ≈ 108s**（实测 main 单次 17.6s）。
#   `pytest.ini` 的 `--timeout=120`（thread 法，超时即 `os._exit(1)`）在 4 路并行争用下
#   被击穿 ⇒ 两次全量回归都在 chunk_0 / chunk_3 出现 `Timeout` 标记（栈停在
#   `module_bindings` → `ast.walk`），该块其余文件**从未执行**。
#
# 设计：把 `scan()` 拆成**昂贵但与配置无关**的前半段（`_collect_findings`，可缓存）与
#   **廉价但依赖模块级配置**的后半段（`_apply_policy`，每次都跑）。
#
# 为什么不能简单地"整份 scan() 结果按文件指纹缓存"：
#   `EXEMPT_CALL_SITES` / `DEAD_MODULES` / `VIOLATION_SCOPE_PREFIXES` 是在**运行期**
#   读取的模块级全局，而测试用 `monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", …)`
#   造负例（"内存里改了配置、磁盘没变"）。若缓存键只含文件指纹，这些负例会**命中过期
#   缓存**而静默失效 —— 本仓已经发生过一次"负例静默失效"（见
#   `test_capregistry_callpaths_routes.py::test_负例_未登记的直调_非零退出` 的注释）。
#   把配置相关的收尾放在缓存之外，就从结构上避免了这一类过期。
#
# 失效判据：被扫描文件的 **(rel, mtime_ns, size) 集合** + 启发式常量签名 + (root, include_scripts)。
#   为什么不用 TTL/进程启动时间：`--check` 是**门禁结论**，必须与当前工作区内容一致；
#   任何时间型缓存都会给出"过期但看起来正常"的结论，比超时更危险（超时至少报 error）。
#   为什么不哈希文件内容：内容哈希要逐文件读全文，与"重新 parse 一遍"同阶，省不下时间；
#   而 py 文件被编辑必然改变 mtime_ns 或 size 之一。文件**集合**也在判据内 ⇒ 新增/删除同样失效。
_RAW_SCAN_CACHE: Dict[Tuple[str, bool], Tuple[Tuple[Any, ...], List["Finding"]]] = {}
_RAW_SCAN_LOCK = threading.Lock()
_RAW_SCAN_STATS: Dict[str, int] = {"hit": 0, "miss": 0}


def _heuristic_signature() -> Tuple[Any, ...]:
    """`_collect_findings` 读取的**模块级启发式常量**签名（缓存键的一部分）

    这些常量只在模块加载时定义、正常不会被改；放进键是零成本的正确性保险 ——
    若有人 monkeypatch 了 `_GENERIC_NAMES` / `_TOOLS_MODULES` 之类，缓存必须失效，
    否则会沿用按旧常量算出的结论。
    """
    return (
        tuple(sorted(_SCAN_ROOTS)),
        tuple(sorted(_REGISTER_NAMES)),
        tuple(sorted(_FUNNEL_ALIASES)),
        tuple(sorted(_TOOLS_MODULES)),
        tuple(sorted(_REMOTE_PRIMITIVES)),
        tuple(sorted(_GENERIC_NAMES)),
        int(_MIN_HANDLER_NAME_LEN),
    )


def _file_state(root: str, files: Sequence[str]) -> Tuple[Any, ...]:
    """被扫描文件的 (rel, mtime_ns, size) 指纹（stat 失败 → None 占位，仍参与判据）

    实测成本：1395 个文件全部 stat 仅 **88ms**（对比一次 scan 17.7s）⇒ 每次都重算无压力。
    """
    state: List[Any] = []
    for rel in files:
        try:
            st = os.stat(os.path.join(root, rel))
            state.append((rel, st.st_mtime_ns, st.st_size))
        except OSError:
            state.append((rel, None, None))
    return tuple(state)


def scan_cache_clear() -> None:
    """清空扫描缓存（测试与长驻进程用；改完工作区想强制重扫也可先调它）"""
    with _RAW_SCAN_LOCK:
        _RAW_SCAN_CACHE.clear()


def scan_cache_info() -> Dict[str, int]:
    """缓存统计（hit / miss / entries）——供测试锁定"同进程重复 scan 真的命中缓存" """
    with _RAW_SCAN_LOCK:
        info = dict(_RAW_SCAN_STATS)
        info["entries"] = len(_RAW_SCAN_CACHE)
        return info


def _collect_findings(root: str, files: Sequence[str]) -> List[Finding]:
    """**昂贵且与例外表无关**的前半段：逐文件 AST 分析 ⇒ 原始 Findings

    产物只取决于「被扫描文件的 (mtime_ns, size) 集合」与启发式常量，
    **不读** `EXEMPT_CALL_SITES` / `DEAD_MODULES` / `VIOLATION_SCOPE_PREFIXES`
    ⇒ 可安全缓存，并在配置变化时复用（配置相关的判定全在 `_apply_policy`）。
    """
    handlers = collect_registered_handlers(root, files)
    handler_names = {n for n in handlers
                     if n not in _GENERIC_NAMES and len(n) >= _MIN_HANDLER_NAME_LEN}

    findings: List[Finding] = []
    for rel in files:
        tree = _parse(root, rel)
        if tree is None:
            continue
        binds = module_bindings(tree)
        sym = _SymbolIndex()
        sym.visit(tree)
        trigger = _trigger_of(rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            key = sym.parent.get(id(node), "<module>")
            leaf = key.split("::")[-1] if key else "<module>"
            cap_arg = (str(node.args[0].value)
                       if node.args and isinstance(node.args[0], ast.Constant) else "")

            # ── P1：收口路径 ──
            if _is_funnel(node, binds):
                has_id = _scope_has_identity(tree, sym, leaf) or any(
                    kw.arg in ("session_source", "identity", "callable_by")
                    for kw in node.keywords)
                findings.append(Finding(
                    file=rel, lineno=node.lineno, symbol=leaf,
                    callee=_callee_dotted(node), capability=cap_arg,
                    path_kind="funnel", trigger=trigger,
                    via_registry=True, via_gate=True, has_identity=has_id))

            # ── P2：已注册 handler 的跨模块直调 ──
            if _callee_name(node) in handler_names:
                hits = _handler_hits(node, binds, handlers)
                for d in hits:
                    if d.module == rel:
                        continue          # 同模块：普通函数调用，不是绕过
                    findings.append(Finding(
                        file=rel, lineno=node.lineno, symbol=leaf,
                        callee=_callee_dotted(node), capability=d.capability,
                        path_kind="direct", trigger=trigger,
                        via_registry=False, via_gate=False, has_identity=False))
                    break

            # ── P3：远程执行原语直调 ──
            if _is_remote_primitive(node, binds):
                findings.append(Finding(
                    file=rel, lineno=node.lineno, symbol=leaf,
                    callee=_callee_dotted(node), capability=cap_arg,
                    path_kind="remote_primitive", trigger=trigger,
                    via_registry=False, via_gate=False, has_identity=False))
    return findings


def _apply_policy(root: str, findings: List[Finding]) -> List[Finding]:
    """**廉价但依赖模块级配置**的后半段：例外锚点补登 + 可达性 + 豁免标记 + 排序

    【关键：这一段**不进缓存**，每次 `scan()` 都重跑】
      它读的全是**运行期可变**的模块级配置（`EXEMPT_CALL_SITES` / `DEAD_MODULES` /
      `VIOLATION_SCOPE_PREFIXES`）。测试正是用 monkeypatch 改这些来造负例，
      "磁盘没变但配置变了"必须立刻反映到结论里，否则负例会静默失效。
      实测成本：39 条 findings 量级 ⇒ 亚毫秒级（重的 `git grep` 仅在
      findings 命中 `DEAD_MODULES` 时才发生）。
    """
    # ── 例外表里"按符号钉住"的条目：即使 AST 启发式没抓到，也要成立检查 ──
    # （否则 `ci.yml` 那种"经 CLI 间接触发"的缺口会因启发式抓不到而被误报腐化；
    #   反之若符号真的从代码里消失 ⇒ 腐化，必须报出来。）
    scanned_anchors = {f.anchor for f in findings}
    for key, meta in EXEMPT_CALL_SITES.items():
        if key in scanned_anchors:
            continue
        rel, _, symbol = key.partition("::")
        if not _symbol_exists(root, rel, symbol):
            continue                        # 由 stale_exemptions() 报腐化
        kind = str(meta.get("path_kind") or "direct")
        # 例外表里未显式声明 `via_*` 时按 path_kind 给默认值：
        # `funnel` 天然是收口路径（过注册表 + 过闸门），不必每行重复写。
        default_funnel = kind == "funnel"
        findings.append(Finding(
            file=rel, lineno=_symbol_lineno(root, rel, symbol) or 0,
            symbol=symbol, callee=str(meta.get("callee") or ""),
            capability=str(meta.get("capability") or ""),
            path_kind=kind, trigger=_trigger_of(rel),
            via_registry=bool(meta.get("via_registry", default_funnel)),
            via_gate=bool(meta.get("via_gate", default_funnel)),
            has_identity=bool(meta.get("identity")),
            in_scope=_in_scope(rel)))

    # ── 可达性 + 豁免登记 + 硬失败范围 ──
    for f in findings:
        f.in_scope = _in_scope(f.file)
        reach, why = _reachability(root, f.file, f.symbol, f.callee, f.path_kind)
        f.reachable = reach
        f.reachability = why
        ex = EXEMPT_CALL_SITES.get(f.anchor)
        if ex is None and f.capability:
            ex = EXEMPT_CALL_SITES.get(anchor_key(f.file, f.capability))
        if ex is not None:
            f.exempt = True
            f.exempt_reason = str(ex.get("reason") or "")
            f.exempt_identity = str(ex.get("identity") or "")
            f.exempt_audit = str(ex.get("audit") or "")
    findings.sort(key=lambda x: (x.path_kind, x.file, x.lineno, x.symbol))
    return findings


def scan(root: str = _ROOT, *, include_scripts: bool = True) -> List[Finding]:
    """扫描全部能触发能力执行的路径（**进程级缓存**，按被扫描文件指纹失效）

    见本文件「进程级扫描缓存」段的动机与失效判据。
    缓存只覆盖"逐文件 AST 分析"这一昂贵前半段；配置相关的收尾每次都重算。

    返回值是**独立副本**（`_apply_policy` 会就地改写 Finding 字段）⇒ 调用方
    随意修改不会污染缓存，缓存也不会把上一次的判定泄漏给下一次。
    """
    files = tracked_python_files(root)
    if not include_scripts:
        files = [f for f in files if not f.startswith("scripts/")]

    cache_key = (os.path.abspath(root), bool(include_scripts))
    signature = (_heuristic_signature(), _file_state(root, files))
    with _RAW_SCAN_LOCK:
        cached = _RAW_SCAN_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature:
            _RAW_SCAN_STATS["hit"] += 1
            raw = copy.deepcopy(cached[1])
        else:
            _RAW_SCAN_STATS["miss"] += 1
            raw = None
    if raw is None:
        raw = _collect_findings(root, files)
        with _RAW_SCAN_LOCK:
            # 存**副本**：随后 `_apply_policy` 会就地改写 raw 里的字段
            _RAW_SCAN_CACHE[cache_key] = (signature, copy.deepcopy(raw))
    return _apply_policy(root, raw)


def _find_symbol_node(tree: ast.Module, symbol: str) -> Optional[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == symbol:
            return node
    return None


def _symbol_exists(root: str, rel: str, symbol: str) -> bool:
    tree = _parse(root, rel)
    if tree is None:
        return False
    if symbol == "<module>":
        return True
    return _find_symbol_node(tree, symbol) is not None


def _symbol_lineno(root: str, rel: str, symbol: str) -> Optional[int]:
    tree = _parse(root, rel)
    if tree is None:
        return None
    node = _find_symbol_node(tree, symbol)
    return getattr(node, "lineno", None)


def unregistered_violations(findings: Sequence[Finding]) -> List[Finding]:
    """**未登记**的直调（`--check` 的硬失败集）

    只有 `direct` / `remote_primitive` 且**在硬失败范围内**（`agent/`、`plugins/`、
    `cloudshu/`）且未登记的才算违规。
    `funnel` **不算**：它就是收口路径；缺身份由 `identity_gaps()` 单列。
    """
    return [f for f in findings
            if f.path_kind in ("direct", "remote_primitive")
            and f.in_scope and not f.exempt]


def stale_exemptions(root: str = _ROOT) -> List[str]:
    """登记了但**代码里已找不到对应符号**的例外（防白名单腐化）"""
    return sorted(k for k in EXEMPT_CALL_SITES
                  if not _symbol_exists(root, *k.split("::", 1)))


def identity_gaps(findings: Sequence[Finding]) -> List[Finding]:
    """**过闸门但不带身份**的收口路径（`async_executor.py:224` 那一类）

    【为什么单列一类】`TASK-05` §2.3c 把 `agent/async_executor.py:224` 记作
    "绕过 `tool_gate`"的现实缺口。**实测更正（D10：发现相反证据要记录并上报）**：
    该文件第 22 行是 `from agent.tools import call as call_tool` ⇒ 第 224 行
    **确实过 `tools.call()`、也确实过 `tool_gate`**。真正的缺口是
    **`submit()` 没有身份参数**，且 `ThreadPoolExecutor` **不继承 `contextvars`**
    ⇒ 执行线程里 `current_session_source()` 为空、`session_source` 落到环境变量
    缺省值 `"cli"`（= 一次后台调用被当成"人从 CLI 调的"）。
    ⇒ 准确表述是「**过闸门但身份缺失**」，不是「绕过闸门」。
    """
    return [f for f in findings
            if f.path_kind == "funnel" and not f.has_identity
            and f.in_scope and not f.exempt]


# ════════════════════════════════════════════════════════════
#  输出
# ════════════════════════════════════════════════════════════


def _fmt(f: Finding) -> str:
    bits = [
        "收口" if f.via_registry else "**不过注册表**",
        "过闸门" if f.via_gate else "**不过闸门**",
        "带身份" if f.has_identity else "**无身份**",
        "可达" if f.reachable else "不可达",
        "已登记例外" if f.exempt else "未登记",
    ]
    scope = "" if f.in_scope else " [范围外/仅登记]"
    return (f"{f.file}:{f.lineno} [{f.path_kind}/{f.trigger}] {f.symbol} → {f.callee} "
            f"(能力={f.capability or '?'}) | " + " / ".join(bits) + scope)


_TITLES = {
    "funnel": "P1 收口路径（经 agent/tools/__init__.py::call() ⇒ 过闸门）",
    "direct": "P2 直调：已注册 handler 的跨模块调用（**绕过治理**）",
    "remote_primitive": "P3 远程执行原语直调（远端 MCP）",
    "unregistered": "P4 未注册的 tools 函数被外部调用（低危，人工判定）",
}


def render_text(findings: Sequence[Finding], root: str = _ROOT) -> str:
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("调用路径全清单（TASK-05 第 0 步）")
    lines.append("=" * 78)
    total = len(findings)
    lines.append(f"扫描到 {total} 条调用路径")
    by_kind: Dict[str, List[Finding]] = {}
    for f in findings:
        by_kind.setdefault(f.path_kind, []).append(f)
    for kind in ("funnel", "direct", "remote_primitive", "unregistered"):
        group = by_kind.get(kind) or []
        lines.append("")
        lines.append(f"── {_TITLES[kind]}：{len(group)} 条 ──")
        for f in group[:80]:
            lines.append("  " + _fmt(f))
        if len(group) > 80:
            lines.append(f"  …（另有 {len(group) - 80} 条，见 --json）")

    viol = unregistered_violations(findings)
    stale = stale_exemptions(root)
    gaps = identity_gaps(findings)
    lines.append("")
    lines.append("=" * 78)
    lines.append("判定（E1b）")
    lines.append("=" * 78)
    lines.append(f"硬失败范围内未登记的直调：{len(viol)}")
    for f in viol:
        lines.append("  ✗ " + _fmt(f))
    lines.append(f"过闸门但无身份（不计硬失败，属身份层：TASK-06）：{len(gaps)}")
    for f in gaps:
        lines.append("  ⚠ " + _fmt(f))
    lines.append(f"已登记但代码里找不到符号（白名单腐化）：{len(stale)}")
    for k in stale:
        lines.append(f"  ⚠ {k}")
    lines.append("")
    lines.append(f"登记例外总数：{len(EXEMPT_CALL_SITES)}"
                 f"（硬失败范围：{'、'.join(VIOLATION_SCOPE_PREFIXES)}）")
    return "\n".join(lines)


def build_payload(findings: Sequence[Finding], root: str = _ROOT) -> Dict[str, Any]:
    return {
        "total": len(findings),
        "by_kind": {k: sum(1 for f in findings if f.path_kind == k)
                    for k in ("funnel", "direct", "remote_primitive", "unregistered")},
        "violations": [f.to_dict() for f in unregistered_violations(findings)],
        "identity_gaps": [f.to_dict() for f in identity_gaps(findings)],
        "stale_exemptions": stale_exemptions(root),
        "exempt_total": len(EXEMPT_CALL_SITES),
        "violation_scope": list(VIOLATION_SCOPE_PREFIXES),
        "findings": [f.to_dict() for f in findings],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="调用路径全清单 + 防再犯检测（TASK-05 第 0 步）")
    ap.add_argument("--check", action="store_true",
                    help="未登记的直调或腐化的例外 ⇒ 非零退出（CI 用）")
    ap.add_argument("--json", default="", help="把完整清单写到该路径")
    ap.add_argument("--no-scripts", action="store_true",
                    help="不扫描 scripts/（仅诊断用；默认扫描）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    findings = scan(include_scripts=not args.no_scripts)
    payload = build_payload(findings)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[audit_call_paths] 全清单已写入 {args.json}")

    if not args.check:
        print(render_text(findings))
        return 0

    viol = payload["violations"]
    stale = payload["stale_exemptions"]
    if viol or stale:
        print(render_text(findings))
        if viol:
            print(f"\n[audit_call_paths] ✗ {len(viol)} 条未登记的直调（绕过 tool_gate "
                  f"执行能力）—— 请收敛到 tools.call()，或在 "
                  f"agent/capregistry/call_sites.py 的 EXEMPT_CALL_SITES 逐点登记")
        if stale:
            print(f"\n[audit_call_paths] ✗ {len(stale)} 条已登记例外在代码里找不到符号"
                  f"（白名单腐化，可能已成空转声明）")
        return 1
    print(f"[audit_call_paths] ✓ 无未登记直调（扫描 {payload['total']} 条路径，"
          f"例外 {payload['exempt_total']} 条，无腐化）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
