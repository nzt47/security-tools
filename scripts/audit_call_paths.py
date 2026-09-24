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
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ── 控制台编码加固（2026-09-21 · P1「假红」修复）─────────────────────────────
# Why: 中文 Windows 的 GBK 控制台无法编码本脚本**成功路径**上的 ✓ / ✅ 等字符，
#      print() 抛 UnicodeEncodeError ⇒ 进程以退出码 1 结束 ⇒ 门禁产生"假红"
#      （检查本身通过，但按退出码判定会误判为失败，进而可能让真实失败被忽略）。
# How: 只放宽错误处理策略（errors="replace"），**不改编码**，保证中文仍正常显示；
#      在 UTF-8 环境（CI / Linux）下等价于无操作。
# 依据: docs/closeout 之外的实测记录见《06-基线台账.md》§七（四门禁退出码对照表）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

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
# 【不易·2026-09-21 L19：上限必须按**字节**而不是**条数**（这条是 CI 挂起的根因之一）】
#   原上限 4096 条对"1399 个受控文件"等于**不设限** ⇒ 整个仓库的 AST 常驻进程内存。
#   实测（本机，见 _ci_logs/l19/mem_probe.txt）：冷扫描峰值工作集 **707.5 MB**，
#   而把解析结果即弃（`_PARSE_CACHE_MAX=1` 等价物）只要 **44.4 MB** —— **16 倍放大**，
#   放大源就是这里；被缓存的是 **1,399 棵 AST / 2,505,191 个节点**，而受控源码合计仅 22.3 MB。
#   代价：CI 上 `-n 2` 的每个 worker 都要再摊一份，而该 runner 本就有资源耗尽前科
#   （`can't start new thread` INTERNALERROR，见 docs/observability/）。
#   ⇒ 改成"**源码字节预算**"（AST 内存 ≈ 源码字节 × 30，由 707MB/22.3MB 实测标定）。
#   预算取多少：本机交替 A/B（同一进程、每档 3 次、取 min，见 _ci_logs/l19/ab_budget2.py）
#     预算        min 冷扫描   峰值工作集
#     1 B（≈不缓存）  15.43 s     45.5 MB
#     64 KB          15.65 s      ~   （差异在噪声内）
#     256 KB         16.36 s      ~
#     1 MB           18.00 s      ~
#     2 MB           17.87 s    106.8 MB
#     8 MB           19.60 s      ~
#     4096 条（原行为） 18.31 s    707.5 MB
#   ⇒ 越大越慢（AST 常驻把 GC 成本抬上去），且内存线性膨胀。取 256 KB：
#     已覆盖"同一文件紧邻两次解析"（`_symbol_exists` → `_symbol_lineno`）这一唯一
#     仍有收益的场景，又不再把整仓 AST 留在进程里。条数上限保留作第二道闸。
_PARSE_CACHE_MAX = 4096
_PARSE_CACHE_MAX_BYTES = 256 * 1024
_PARSE_CACHE: "OrderedDict[Tuple[str, str, int, int], Optional[ast.Module]]" = OrderedDict()
_PARSE_CACHE_LOCK = threading.Lock()
_PARSE_CACHE_STATS: Dict[str, int] = {"hit": 0, "miss": 0, "evict": 0}
#: 当前缓存里所有条目对应的**源码字节数**（用于字节预算淘汰）
_PARSE_CACHE_BYTES: Dict[str, int] = {"n": 0}


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
        _PARSE_CACHE_BYTES["n"] += key[3]
        _PARSE_CACHE.move_to_end(key)
        # 两道闸：字节预算（内存）与条数上限；`len(...) > 1` 保证"单文件也留得住"，
        # 否则"同一文件第二次解析命中缓存"这条不变量会在大文件上被自己淘汰掉。
        while (_PARSE_CACHE_BYTES["n"] > _PARSE_CACHE_MAX_BYTES
               or len(_PARSE_CACHE) > _PARSE_CACHE_MAX) and len(_PARSE_CACHE) > 1:
            old_key, _ = _PARSE_CACHE.popitem(last=False)
            _PARSE_CACHE_BYTES["n"] -= old_key[3]
            _PARSE_CACHE_STATS["evict"] += 1
    return tree


def parse_cache_clear() -> None:
    """清空 AST 解析缓存（测试与长驻进程用；改完工作区想强制重扫也可先调它）"""
    with _PARSE_CACHE_LOCK:
        _PARSE_CACHE.clear()
        _PARSE_CACHE_BYTES["n"] = 0


def parse_cache_info() -> Dict[str, int]:
    """AST 解析缓存统计（hit / miss / evict / entries）—— 供测试锁定"重复解析被消除" """
    with _PARSE_CACHE_LOCK:
        info = dict(_PARSE_CACHE_STATS)
        info["entries"] = len(_PARSE_CACHE)
        info["source_bytes"] = _PARSE_CACHE_BYTES["n"]
        return info


def module_of(rel: str) -> str:
    """`agent/tools/git_tools.py` → `agent.tools.git_tools`"""
    m = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


# ════════════════════════════════════════════════════════════
#  单次遍历的树索引（2026-09-24 · L6 根治 CI 负载敏感）
# ════════════════════════════════════════════════════════════
# 【为什么要有它（实测，证据见 docs/closeout/遗留问题立项_20260922.md 的 L6）】
#   改造前每解析一个文件要做 **3 次整树遍历**：
#     ① `module_bindings(tree)` 的 `ast.walk`（找 import 绑定）；
#     ② `_SymbolIndex.visit(tree)` 的递归 NodeVisitor（维护符号栈）；
#     ③ `_collect_findings` 主循环里**再一次** `ast.walk`。
#   `ast.walk` 的代价在 `iter_child_nodes` → `iter_fields` → 逐字段 `getattr`：
#   本机 1406 个受控文件，一次全仓遍历就是 **2,214.9 万次 `iter_fields`**；
#   cProfile 下 `ast.walk` 24.3s + `_SymbolIndex` 11.0s ≈ 冷扫描的 60%。
#   本类把这三次合成**一次 BFS 遍历**（与 `ast.walk` **同序**：同样的 deque 弹出顺序，
#   于是"同名重复 import 后者覆盖"的绑定表语义逐字节不变），同时记录父子关系
#   ⇒ 符号锚点由父链上溯算出，不再需要第二次递归遍历。
# 【口径等价（逐条论证）】旧 `_SymbolIndex.parent[id(call)]` 的取值是"该 call 被访问时
#   符号栈的内容"，即**所有**外层 `def`/`async def`/`class` 的名字自外向内 `::` 连接
#   （`lambda` 不入栈）；父链上溯后反转，结果逐字符相同。
#   既有守护用例 `test_身份判定快路径与旧口径等价` 仍然对拍两条路径。
class _TreeIndex:
    """一次 BFS 遍历得到的：节点表（与 `ast.walk` 同序）+ 父表 + 惰性符号锚点"""

    __slots__ = ("nodes", "_parents", "_symbols")

    def __init__(self, tree: ast.AST) -> None:
        nodes: List[ast.AST] = []
        parents: Dict[int, ast.AST] = {}
        queue: "deque[ast.AST]" = deque([tree])
        while queue:
            node = queue.popleft()
            nodes.append(node)
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
                queue.append(child)
        self.nodes = nodes
        self._parents = parents
        self._symbols: Dict[int, str] = {}

    def symbol_of(self, node: ast.AST) -> str:
        """节点所在的**符号锚点**（`Outer::inner`；无外层定义时 `<module>`）"""
        key = self._symbols.get(id(node))
        if key is not None:
            return key
        parts: List[str] = []
        cur = self._parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parts.append(cur.name)
            cur = self._parents.get(id(cur))
        key = "::".join(reversed(parts)) or "<module>"
        self._symbols[id(node)] = key
        return key


class _SymbolIndex(_TreeIndex):
    """（兼容层）`{id(ast.Call) → 符号锚点}` 表

    【2026-09-24 · L6】实现改为复用 `_TreeIndex`：原先这里有一份**独立的**递归遍历，
    与主扫描各算各的，存在口径漂移风险；现在两条路径共用 `symbol_of()`。
    保留 `visit()` / `.parent` 的名字，供既有守护用例
    （`test_身份判定快路径与旧口径等价`）与 `_identity_leaves()` 继续使用。
    """

    def __init__(self, tree: Optional[ast.AST] = None) -> None:
        self.parent: Dict[int, str] = {}
        if tree is not None:
            self.visit(tree)

    def visit(self, tree: ast.AST) -> Dict[int, str]:
        _TreeIndex.__init__(self, tree)
        self.parent = {id(n): self.symbol_of(n)
                       for n in self.nodes if isinstance(n, ast.Call)}
        return self.parent


def _symbol_key_of(sym: Any, node: ast.AST) -> str:
    """取节点的符号锚点：新索引走 `symbol_of()`，兼容对象走 `.parent` 表"""
    symbol_of = getattr(sym, "symbol_of", None)
    if callable(symbol_of):
        return symbol_of(node)
    return sym.parent.get(id(node), "")


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


def module_bindings_from_nodes(nodes: Iterable[ast.AST]
                              ) -> Dict[str, Tuple[str, str]]:
    """从**已遍历好的节点序列**建绑定表（顺序敏感：同名重复 import 后者覆盖）

    【为什么要拆出来（2026-09-24 · L6）】主扫描为了"符号锚点 + 调用点"已经遍历过一次，
    再 `ast.walk` 一遍只为找 import 是纯浪费（一次全仓遍历 ≈ 2,214.9 万次 `iter_fields`）。
    判据不变：`ast.walk` 与 `_TreeIndex.nodes` **都是 BFS 同序**，
    故"后者覆盖前者"的结果逐字节一致（对拍见本仓守护用例
    `test_capregistry_callpaths_routes.py::TestPrefilterAndTraversalEquivalence`
    的 `test_绑定表与_ast_walk_同序等价`）。
    """
    binds: Dict[str, Tuple[str, str]] = {}
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if a.name == "*":
                    continue
                binds[a.asname or a.name] = (node.module, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                binds[a.asname or a.name.split(".")[0]] = (a.name, "")
    return binds


def module_bindings(tree: ast.Module) -> Dict[str, Tuple[str, str]]:
    """`本模块里的名字 → (来源模块, 原始名)`

    覆盖三种形态：
        `from agent.tools import call as call_tool`  → {"call_tool": ("agent.tools","call")}
        `from agent.tools.git_tools import _git`     → {"_git": ("agent.tools.git_tools","_git")}
        `import agent.tools.file_tools_reg as ftr`   → {"ftr": ("agent.tools.file_tools_reg","")}

    兼容入口：内部与主扫描共用 `module_bindings_from_nodes`（判据只有一处实现）。
    """
    return module_bindings_from_nodes(ast.walk(tree))


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


#: 上表的名字在源码里**必然以字面量出现**（`_callee_name` 取的就是 Attribute.attr /
#: Name.id）⇒ 用它做**廉价的字节级预筛**是**保语义**的：源码里没有 `register` 这四个字
#: 的文件，不可能出现 `register(...)` / `register_tool(...)` 这类调用点。
#: 【为什么必须预筛（2026-09-21 L19）】本函数对**全部**受控文件做一遍 `_parse` + `ast.walk`，
#: 实测占冷扫描总成本的 **1/3 以上**（1400 文件里只有一小部分真的注册 handler）。
_REGISTER_NAME_BYTES: Tuple[bytes, ...] = tuple(
    sorted({n.encode() for n in _REGISTER_NAMES}))


def _source_mentions_register(root: str, rel: str) -> bool:
    """源码字节里是否出现任一注册函数名（预筛用；读盘成本 ≈ 30ms/1400 文件）"""
    try:
        with open(os.path.join(root, rel), "rb") as fh:
            raw = fh.read()
    except OSError:  # pragma: no cover - 读不到就当"没有"，与 `_parse` 返回 None 同效
        return False
    return any(hint in raw for hint in _REGISTER_NAME_BYTES)


def collect_registered_handlers(root: str, files: Sequence[str]
                                ) -> Dict[str, List[RegisteredHandler]]:
    """建**注册面**：handler 函数名 → 注册点（装饰器 + `handler=` 两种形态）"""
    table: Dict[str, List[RegisteredHandler]] = {}
    for rel in files:
        if not _source_mentions_register(root, rel):
            continue
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


def _identity_leaves(tree: ast.Module, sym: _SymbolIndex) -> Set[str]:
    """**一次遍历**算出：本文件里哪些"符号名"的调用点带显式身份

    【为什么要有这个函数（2026-09-21 L19）】原来 `_scope_has_identity(tree, sym, leaf)`
    是**按收口调用点逐个调用**的，而它内部是**整棵树**的 `ast.walk` ⇒ 一个文件里有 N 个
    收口调用点就要把整棵树走 N 遍（O(调用点数 × 节点数)）。实测这是冷扫描里**最大的
    单点浪费**。本函数把"该文件哪些符号带身份"一次算完，调用点改成查表 ⇒ 语义完全等价
    （判据仍是"该符号名下有任一调用点满足三选一"），复杂度降到**每文件一次遍历**。
    """
    out: Set[str] = set()
    # 【2026-09-24 · L6】优先复用主扫描已建好的节点表（`_TreeIndex.nodes`），
    #   避免再来一次 `ast.walk`；传入旧式对象/裸树时回退到 ast.walk。
    for node in getattr(sym, "nodes", None) or ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        key = _symbol_key_of(sym, node)
        leaf = key.split("::")[-1] if key else ""
        if not leaf:
            continue
        if _callee_name(node) == "set_session_source" or any(
                kw.arg in ("session_source", "identity", "callable_by")
                for kw in node.keywords):
            out.add(leaf)
    return out


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


#: `_module_importers` 的进程内 memo：`(root, module, 仓库状态 digest) → importer 列表`
#: 【为什么要 memo（2026-09-24 · L6）】它每次都要起一个 `git grep` 子进程，而
#:   `_apply_policy` **每次 `scan()` 都重跑**（它读运行期可变的例外表，故意不进缓存）
#:   ⇒ 一个测试文件里 5 次 `main(["--check"])` 就是 5 轮 `git grep`。本机实测（2 核）：
#:   单次 `scan()` 的热路径仍有 ~2.2s，几乎全在子进程启动上；CI 的 2 核 runner 更贵。
#:   键里带**仓库状态 digest**（与扫描缓存同一套指纹：文件集合 + 逐文件 mtime/size）
#:   ⇒ 仓库内容变了必不命中，与不缓存时的判定语义完全一致。
_IMPORTER_CACHE: Dict[Tuple[str, str, str], List[str]] = {}


def _module_importers(root: str, module: str, state_key: str = "") -> List[str]:
    """生产代码里 **import 该模块**的位置（排除 tests/docs/非受控副本）

    `state_key` 非空时启用进程内 memo（键含仓库状态 ⇒ 不会给出过期结论）。

    【不易·必须匹配"import 语句"而不是"出现该字符串"】本脚本第一版用
    `git grep -F <模块名>`，结果把**本文件自己写下的模块名字符串**（例外表里那句
    "实测 `git grep -l agent.mcp_executor` 只命中…"）算成了一个 importer ⇒
    `agent/mcp_executor.py` 被误判为"有生产调用方"、进而误判为可达。
    实测踩到：`TestCallPathInventory::test_死模块被标为不可达` 变红。
    ⇒ 判据收紧成"**真的有一条 import 语句**"，这样文档/注释/例外表里的提及不会污染结论。
    """
    memo_key = (os.path.abspath(root), module, state_key)
    if state_key:
        memo_hit = _IMPORTER_CACHE.get(memo_key)
        if memo_hit is not None:
            return list(memo_hit)
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
    if state_key:
        if len(_IMPORTER_CACHE) > 256:      # 长驻进程的兜底上限（正常只有个位数条目）
            _IMPORTER_CACHE.clear()
        _IMPORTER_CACHE[memo_key] = list(res)
    return res


def _reachability(root: str, file: str, symbol: str, callee: str,
                  path_kind: str, state_key: str = "") -> Tuple[bool, str]:
    """静态判定该路径**当前是否可达**（不执行任何代码）

    `state_key` 只用作"死模块 importer 查询"的 memo 键（见 `_module_importers`），
    不参与任何判定。

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
        importers = [p for p in _module_importers(root, mod, state_key)
                     if p != file]
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
    """清空**进程内**扫描缓存（测试与长驻进程用；改完工作区想强制重扫也可先调它）

    【边界】只清进程内缓存与 importer memo，**不动**磁盘产物：磁盘产物按仓库状态
    指纹命名，状态变了自然换键；显式删除反而会让"跨进程复用"这一性质测不出来。
    要连磁盘一起清：删掉 `_disk_cache_dir()` 目录即可，或设 `CP_AUDIT_SCAN_DISK_CACHE=0`。
    """
    with _RAW_SCAN_LOCK:
        _RAW_SCAN_CACHE.clear()
        _IMPORTER_CACHE.clear()


def scan_cache_info() -> Dict[str, Any]:
    """缓存统计（进程内 hit/miss/entries + 磁盘产物 disk_*）——供测试锁定缓存真在生效"""
    with _RAW_SCAN_LOCK:
        info: Dict[str, Any] = dict(_RAW_SCAN_STATS)
        info["entries"] = len(_RAW_SCAN_CACHE)
        info.update(_DISK_CACHE_STATS)
        info["disk_dir"] = _disk_cache_dir() or ""
        return info


# ════════════════════════════════════════════════════════════
#  跨进程扫描产物（2026-09-24 · L6 根治 CI 负载敏感）
# ════════════════════════════════════════════════════════════
# 【为什么需要第二层：进程内缓存**管不到跨进程**（实测证据）】
#   CI 的命令是 `-n 2 --dist=loadscope`，而 xdist 的 loadscope 对**带类的测试模块**
#   是**按类分组**的 ⇒ 同一个测试文件的各个类会落到**不同 worker 进程**，
#   每个进程各付一次全仓扫描。2026-09-22 的那轮 CI（job 106915652550）日志可直接读到：
#     · gw1 在 `TestScanCache` 的 `findings` 夹具（= `audit.scan()`）里被
#       `Timeout (>300.0s)` 打断，**gw0 同时**也在 `TestCallPathInventory` 的同一夹具里
#       被同一超时打断（两处栈都停在 `_collect_findings`）；
#     · 被中断的扫描**不会留下任何缓存** ⇒ 随后 gw0 的 `TestCheckMode` 四条用例
#       **各自又从零冷扫一次**（每条 300s）⇒ 单文件 6 次全仓 AST、约 20 分钟。
#   ⇒ 把"逐文件 AST 分析"的产物落成**按仓库状态指纹命名的磁盘文件**：
#     同一次 job 内其它进程（以及本机重跑）直接复用，把 6 次收敛成 1 次。
#
# 【失效判据（与进程内缓存同一套语义，逐字节相同）】
#   键 = `_state_digest(root, signature, include_scripts)`，其中 `signature` 就是进程内
#   缓存用的那个：`_heuristic_signature()` + 被扫描文件的 `(rel, mtime_ns, size)`；
#   再叠加 `root` / `include_scripts` / **扫描器自身源码指纹** / 产物格式版本。
#   ⇒ 改任一受控文件、新增或删除文件、改启发式常量、改扫描器本身，都会换键。
#   **不读过期结论**是硬要求：`--check` 是门禁结论，过期但"看起来正常"比超时更危险。
#
# 【为什么产物里不落"配置相关判定"】
#   只存 `_collect_findings` 的输出（原始 Findings）。`EXEMPT_CALL_SITES` /
#   `DEAD_MODULES` / `VIOLATION_SCOPE_PREFIXES` 一律在 `_apply_policy` 里**每次重算**
#   —— 与进程内缓存同一条理由：守护用例用 monkeypatch 改内存里的例外表造负例，
#   缓存绝不能把这类判定冻住（本仓发生过"负例静默失效"）。
#
# 【边界与开关】
#   · 写盘失败 / 产物损坏 / 目录不可写 ⇒ **一律按未命中处理**，绝不抛给调用方
#     （门禁既不能因缓存而红，更不能因缓存而静默放行）；
#   · 默认目录 = 系统临时目录下的 `cp_audit_call_paths/`（**绝不写仓库目录、绝不碰
#     data/**）；`CP_AUDIT_SCAN_CACHE_DIR=/path` 可改；`CP_AUDIT_SCAN_DISK_CACHE=0` 关闭；
#   · 只保留最近 `_DISK_CACHE_MAX_FILES` 份产物，缓存目录不是无底洞。
_DISK_CACHE_FORMAT = 1
_DISK_CACHE_DIRNAME = "cp_audit_call_paths"
_DISK_CACHE_DIR_ENV = "CP_AUDIT_SCAN_CACHE_DIR"
_DISK_CACHE_OFF_ENV = "CP_AUDIT_SCAN_DISK_CACHE"
_DISK_CACHE_MAX_FILES = 32
_DISK_CACHE_STATS: Dict[str, int] = {
    "disk_hit": 0, "disk_miss": 0, "disk_write": 0, "disk_error": 0}

#: 产物里保存的字段：**只包含 `_collect_findings` 会写的那些**。
#: `reachable` / `reachability` / `exempt*` / `in_scope` 一律由 `_apply_policy` 重算，
#: 落盘就等于把配置相关判定一起冻住（见上）。
_RAW_FIELDS: Tuple[str, ...] = (
    "file", "lineno", "symbol", "callee", "capability", "path_kind",
    "trigger", "via_registry", "via_gate", "has_identity")


def _scanner_source_state() -> Tuple[int, int]:
    """扫描器自身源码的 `(mtime_ns, size)`：产物键的一部分（改了判据就必须重扫）"""
    try:
        st = os.stat(os.path.abspath(__file__))
        return (st.st_mtime_ns, st.st_size)
    except OSError:  # pragma: no cover - 取不到就当"没变"
        return (0, 0)


def _disk_cache_dir() -> Optional[str]:
    """跨进程产物目录；返回 `None` 表示本轮**关闭**（环境变量或取不到临时目录）"""
    raw = str(os.environ.get(_DISK_CACHE_OFF_ENV, "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return None
    override = str(os.environ.get(_DISK_CACHE_DIR_ENV, "")).strip()
    if override:
        return override
    try:
        return os.path.join(tempfile.gettempdir(), _DISK_CACHE_DIRNAME)
    except Exception:  # pragma: no cover - 极端环境（无临时目录）
        return None


def _state_digest(root: str, signature: Tuple[Any, ...], include_scripts: bool) -> str:
    """仓库状态指纹（磁盘产物文件名 + importer memo 键）

    实测成本：`repr(signature)` + sha256 ≈ 10ms（对比一次冷扫描 14.3s），可忽略。
    """
    head = "fmt=%d|root=%s|scripts=%d|scanner=%r|" % (
        _DISK_CACHE_FORMAT, os.path.abspath(root), int(bool(include_scripts)),
        _scanner_source_state())
    digest = hashlib.sha256(head.encode("utf-8"))
    digest.update(repr(signature).encode("utf-8"))
    return digest.hexdigest()


def _disk_cache_path(state_key: str) -> Optional[str]:
    directory = _disk_cache_dir()
    if not directory:
        return None
    return os.path.join(directory, "scan-%s.json" % state_key[:32])


def _load_raw_from_disk(state_key: str) -> Optional[List["Finding"]]:
    """读磁盘产物；未命中/损坏/不可读一律返回 `None`（调用方重新扫描）"""
    path = _disk_cache_path(state_key)
    if path is None:
        return None
    if not os.path.exists(path):
        _DISK_CACHE_STATS["disk_miss"] += 1
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if int(blob.get("format", -1)) != _DISK_CACHE_FORMAT:
            raise ValueError("产物格式版本不匹配")
        rows = blob.get("findings")
        if not isinstance(rows, list):
            raise ValueError("产物缺少 findings 列表")
        out = [Finding(**{k: row[k] for k in _RAW_FIELDS}) for row in rows]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        _DISK_CACHE_STATS["disk_error"] += 1
        return None
    _DISK_CACHE_STATS["disk_hit"] += 1
    return out


def _store_raw_to_disk(state_key: str, raw: Sequence["Finding"], *,
                       root: str, include_scripts: bool) -> None:
    """原子写磁盘产物（先写 `<path>.<pid>.tmp` 再 `os.replace`）

    Why 原子：并发的 xdist worker 会同时写同一个键；`os.replace` 保证读者看到的
    要么是旧产物、要么是完整新产物，**绝不会读到半截 JSON**。
    """
    path = _disk_cache_path(state_key)
    if path is None:
        return
    tmp = "%s.%d.tmp" % (path, os.getpid())
    payload = {
        "format": _DISK_CACHE_FORMAT,
        "root": os.path.abspath(root),
        "include_scripts": bool(include_scripts),
        "state": state_key,
        "findings": [{k: getattr(f, k) for k in _RAW_FIELDS} for f in raw],
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        _DISK_CACHE_STATS["disk_write"] += 1
    except OSError:
        _DISK_CACHE_STATS["disk_error"] += 1
        try:
            os.remove(tmp)
        except OSError:
            pass
    _prune_disk_cache(os.path.dirname(path))


def _prune_disk_cache(directory: str) -> None:
    """只保留最近 `_DISK_CACHE_MAX_FILES` 份产物（失败一律忽略，不影响门禁）"""
    try:
        names = [n for n in os.listdir(directory)
                 if n.startswith("scan-") and n.endswith(".json")]
    except OSError:  # pragma: no cover
        return
    if len(names) <= _DISK_CACHE_MAX_FILES:
        return
    entries: List[Tuple[float, str]] = []
    for name in names:
        full = os.path.join(directory, name)
        try:
            entries.append((os.path.getmtime(full), full))
        except OSError:  # pragma: no cover
            continue
    entries.sort()
    for _, full in entries[:len(entries) - _DISK_CACHE_MAX_FILES]:
        try:
            os.remove(full)
        except OSError:  # pragma: no cover
            pass


#: 参与判据、且**与 handler 表无关**的调用点标识符（`_collect_findings` 的三个分支）
#: 【不易】这里是**派生**而不是手抄：`_is_funnel` 认 `call` 与 `_FUNNEL_ALIASES`，
#:   `_is_remote_primitive` 认 `_REMOTE_PRIMITIVES` —— 这三处将来加名字，预筛集合
#:   自动跟上；守护用例 `test_预筛标识集必须覆盖全部判据名字` 再钉一道。
_CALL_SITE_NAME_EXTRA: frozenset = (
    frozenset({"call"}) | _FUNNEL_ALIASES | _REMOTE_PRIMITIVES
)


def _call_site_pattern(handler_names: Iterable[str]) -> "re.Pattern[bytes]":
    r"""`\b(?:名字1|名字2|…)\b` 的**字节级**预筛正则（见 `_source_may_contain_call_site`）"""
    names = sorted({n for n in handler_names if n} | set(_CALL_SITE_NAME_EXTRA),
                   key=len, reverse=True)
    return re.compile((r"\b(?:%s)\b" % "|".join(re.escape(n) for n in names))
                      .encode("utf-8"))


def _source_may_contain_call_site(root: str, rel: str,
                                  pattern: "re.Pattern[bytes]") -> bool:
    r"""源码字节里是否**可能出现**参与判据的调用点标识符（保语义的保守预筛）

    【判据论证（为什么不丢结论）】`_collect_findings` 只可能为满足下列之一的
    `ast.Call` 生成 Finding：
      · `_is_funnel`：`func` 是 `Name(id="call")` / `Name(id ∈ _FUNNEL_ALIASES)`
        / `Attribute(attr="call")`；
      · `_is_remote_primitive`：`Attribute(attr ∈ _REMOTE_PRIMITIVES)`；
      · `_callee_name(node) ∈ handler_names`：即 `Name.id` 或 `Attribute.attr`。
    这些名字**都是标识符**，在源码里必然**逐字出现**、且两侧（若存在）不是标识符字符
    ⇒ 上面的 `\b名\b` 字节正则**必定命中**。⇒ 未命中的文件**不可能**产生 Finding，
    跳过它只省成本、不改结论（与既有 `_source_mentions_register` 同一手法）。
    【只做上界】命中 ≠ 有调用点（注释/字符串里出现也叫命中）⇒ 多扫不漏扫。
    【实测选择性】本机 1406 个受控文件命中 500 个（35.6%）、命中字节 11.9MB/22.6MB。
    """
    try:
        with open(os.path.join(root, rel), "rb") as fh:
            raw = fh.read()
    except OSError:
        # 读不到就**保守地当作"可能"**：交给 `_parse` 去失败（与直接扫该文件同效）
        return True
    return pattern.search(raw) is not None


def _collect_findings(root: str, files: Sequence[str]) -> List[Finding]:
    """**昂贵且与例外表无关**的前半段：逐文件 AST 分析 ⇒ 原始 Findings

    产物只取决于「被扫描文件的 (mtime_ns, size) 集合」与启发式常量，
    **不读** `EXEMPT_CALL_SITES` / `DEAD_MODULES` / `VIOLATION_SCOPE_PREFIXES`
    ⇒ 可安全缓存，并在配置变化时复用（配置相关的判定全在 `_apply_policy`）。

    【2026-09-24 · L6 的两处成本改造（判据一字不改，逐条对拍见守护用例）】
      ① **字节预筛**：不可能出现调用点标识符的文件连 `ast.parse` 都不做
         （本机 1406 → 500 个文件，判据论证见 `_source_may_contain_call_site`）；
      ② **单次遍历**：import 绑定表 / 符号锚点 / 主循环共用 `_TreeIndex`
         （原本是 3 次整树遍历，见 `_TreeIndex` 的说明）。
    """
    handlers = collect_registered_handlers(root, files)
    handler_names = {n for n in handlers
                     if n not in _GENERIC_NAMES and len(n) >= _MIN_HANDLER_NAME_LEN}
    pattern = _call_site_pattern(handler_names)

    findings: List[Finding] = []
    for rel in files:
        if not _source_may_contain_call_site(root, rel, pattern):
            continue
        tree = _parse(root, rel)
        if tree is None:
            continue
        index = _TreeIndex(tree)
        binds = module_bindings_from_nodes(index.nodes)
        # 身份表**惰性**计算：全仓只有个位数文件含收口调用点（实测 39 条 findings 里
        # 只有 10 条 funnel）⇒ 无条件每文件算一遍反而白走 1400 遍树（实测 +2.3s）。
        identity_leaves: Optional[Set[str]] = None
        trigger = _trigger_of(rel)
        for node in index.nodes:
            if not isinstance(node, ast.Call):
                continue
            key = index.symbol_of(node)
            leaf = key.split("::")[-1] if key else "<module>"
            cap_arg = (str(node.args[0].value)
                       if node.args and isinstance(node.args[0], ast.Constant) else "")

            # ── P1：收口路径 ──
            if _is_funnel(node, binds):
                if identity_leaves is None:
                    identity_leaves = _identity_leaves(tree, index)
                has_id = (leaf in identity_leaves) or any(
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


def _apply_policy(root: str, findings: List[Finding], *,
                  state_key: str = "") -> List[Finding]:
    """**廉价但依赖模块级配置**的后半段：例外锚点补登 + 可达性 + 豁免标记 + 排序

    `state_key` 只透传给"死模块 importer 查询"当 memo 键（见 `_module_importers`），
    **不参与任何判定**：本段照旧每次 `scan()` 重跑。

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
        reach, why = _reachability(root, f.file, f.symbol, f.callee, f.path_kind,
                                   state_key)
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
    """扫描全部能触发能力执行的路径（**进程级缓存 + 跨进程磁盘产物**，按仓库指纹失效）

    见本文件「进程级扫描缓存」与「跨进程扫描产物」两段的动机与失效判据：
      ① 进程内缓存（`_RAW_SCAN_CACHE`）：同一进程重复 `scan()` 只算一次；
      ② 跨进程产物（`_load_raw_from_disk` / `_store_raw_to_disk`）：指纹相同就复用，
         xdist 的另一个 worker、以及本机下一次运行都不必再扫一遍。
    两层都只覆盖"逐文件 AST 分析"这一昂贵前半段；配置相关的收尾每次都重算。

    返回值是**独立副本**（`_apply_policy` 会就地改写 Finding 字段）⇒ 调用方
    随意修改不会污染缓存，缓存也不会把上一次的判定泄漏给下一次。
    """
    files = tracked_python_files(root)
    if not include_scripts:
        files = [f for f in files if not f.startswith("scripts/")]

    cache_key = (os.path.abspath(root), bool(include_scripts))
    signature = (_heuristic_signature(), _file_state(root, files))
    state_key = _state_digest(root, signature, include_scripts)
    with _RAW_SCAN_LOCK:
        cached = _RAW_SCAN_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature:
            _RAW_SCAN_STATS["hit"] += 1
            raw = copy.deepcopy(cached[1])
        else:
            _RAW_SCAN_STATS["miss"] += 1
            raw = None
    if raw is None:
        raw = _load_raw_from_disk(state_key)
        if raw is None:
            raw = _collect_findings(root, files)
            _store_raw_to_disk(state_key, raw, root=root,
                               include_scripts=include_scripts)
        with _RAW_SCAN_LOCK:
            # 存**副本**：随后 `_apply_policy` 会就地改写 raw 里的字段
            _RAW_SCAN_CACHE[cache_key] = (signature, copy.deepcopy(raw))
    return _apply_policy(root, raw, state_key=state_key)


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
