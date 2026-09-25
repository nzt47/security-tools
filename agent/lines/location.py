"""能力执行位置（`location`: local / remote）—— **事实判定器**

【为什么有这个模块】
    《云枢能力层重构方案 v1.4》的核心新增分类轴是 `location`：一项能力的**执行**是发生在
    云枢自己这个进程里（`local`），还是跨出了进程/协议边界（`remote`）。仓库此前
    **完全没有**这个维度（实测 `data/capability_manifest.json` 114 条 0 条含该键、
    91 个 `data/tool_definitions/*.yaml` 里 `location` 出现 0 次）。

    它不能用"人工逐条填"的方式补：那样**无法防未来新增能力**（新增一条就漏一条）。
    故本模块把判定做成**从可证事实推导**，人工声明只作为**钉住（pin）/覆盖（override）**，
    且必须写明理由；`scripts/sync_capability_manifest.py --check` 会拦住"声明与事实不符"。

【判定铁律（v1.4 §2.5，逐条已对仓库现实核对）】
    只要这次执行**跨出本进程**（含本机 stdio 子进程、本机 localhost HTTP、Unix domain
    socket、浏览器/IDE 等外部宿主）⇒ `remote`；只在同进程内加载（含 FFI/动态库/WASM）⇒ `local`。

    边缘情形与仓库实例（详见 docs/rfc/CapabilitySpec规范.md 的对照表）：
    | 情形 | 归类 | 仓库实例 |
    |---|---|---|
    | 本地 subprocess 调 CLI（stdin/stdout） | remote | `shell_execute`→`agent/system_tools.py`、`run_program` |
    | 本机 stdio MCP | **remote** | `connect_mcp`→`McpConnector.connect_stdio`→`MCPClient`（`asyncio.create_subprocess_exec`） |
    | HTTP（含 localhost） | remote | `web_*`、`connect_mcp` 的 http 传输（`urllib.request.urlopen`） |
    | 跨进程浏览器驱动 | remote | `browser_navigate`（selenium webdriver 起独立驱动进程） |
    | 同进程 FFI / 动态库 | local | `pywin32`/`comtypes`/`wmi`（`pyproject.toml` 依赖） |
    | 出进程的 COM/DCOM、WMI | **remote** | `wmi.WMI()` / `win32com.client.Dispatch`（跨进程 RPC，见 `_REMOTE_FFI`） |
    | 进程内 SQLite 直连 | local | `sqlite_query`→`orchestrator_config.db` |
    | prompt-only skill 提示词注入 | local | 22 条纯提示词技能（`ContextInjector` 同进程注入） |
    | 带脚本 skill（SkillExecutor 起子进程） | remote | `scripted-selftest`（`agent/skills_mgmt/executor.py:202 subprocess.run`） |

【事实来源（三层，优先级从高到低）】
    ① **声明钉住**：`data/tool_definitions/<name>.yaml` 或 `data/skill_callability.yaml` 里的
       `location:` 键。它是**钉住值**，`--check` 会与 ②③ 对拍：不一致即非零退出。
       【不易】钉住不等于可以乱写。`location_reason` 是必填的说明字段；缺失时 `--check`
       仍能跑（向后兼容 D2），但盘点表会把它标为"无理由覆盖"。
    ② **执行器边界事实**（本模块主体）：从 `host_executor`（`模块:函数`）出发，
       做**有界调用链静态解析**，看链路是否触达跨边界原语（`subprocess.*` /
       `asyncio.create_subprocess_*` / `socket` / `urllib.request` / `selenium` / …）。
       解析不到的名字**不猜**（记为 unresolved），避免把 `local` 误判成 `remote`。
    ③ **注册来源事实**：`agent/tools/__init__.py::registry_facts()["source"]`
       （由调用方注入，本模块**不导入** `agent.tools` —— 那会构成
       `agent.lines ↔ agent.tools` 循环依赖，被架构规则 `no_circular_dependency` 判违规，
       与 `callability.runtime_executors()` 同一取舍）。`source` 是**注册来源**，
       按 v1.4 §5.1 它**不是**执行边界 ⇒ 只能作为**佐证**，不能单独定案（见 `_SOURCE_HINT`）。

【不易】
    - 本模块**只读**源码，不执行任何被扫描的代码（纯 AST），也不写任何文件；
    - 任何异常都降级为**保守值 `remote`**：未识别的能力按"会跨边界"对待（需要超时/熔断），
      与 `agent/lines/models.py` 的"缺字段给保守默认（act/execute/medium）"同一取舍；
    - 判定结果带 `location_evidence`（逐条可核），不接受"黑箱结论"。
【简易】
    from agent.lines.location import judge_executor_location
    judge_executor_location("agent.tools.ext_tools:_connect_mcp")
"""
from __future__ import annotations

import ast
import os
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 取值域（唯一来源；清单、YAML 校验、守门测试都从这里取）
LOCATIONS: Tuple[str, ...] = ("local", "remote")

#: 缺事实时的保守默认。为什么是 `remote` 而不是 `local`：
#:   未登记/未识别的能力按"会跨出进程边界"对待 —— 那是**更受约束**的一侧
#:  （需要超时、熔断、SSRF 检查、网络审计），漏判的代价是安全缺口，
#:   误判的代价只是一条多余的约束。与 `models.py:193` 的保守默认同一取舍。
DEFAULT_LOCATION = "remote"

#: 判定来源（写进清单的 `location_source`，供"这条为什么是这个值"可追溯）
LOCATION_SOURCES: Tuple[str, ...] = (
    "declaration",     # 声明钉住（YAML 的 location 键）
    "executor_boundary",  # 执行器调用链命中跨边界原语（硬事实）
    "registry_source",    # 注册来源佐证（软事实，须与目标语义一致才采用）
    "skill_chain",        # 技能侧执行链路（注入器 = 同进程；执行器 = 子进程）
    "default",            # 保守兜底（清单里出现即需要逐条说明，见盘点表）
)

# ── 跨边界原语（按"被调用的点分路径"匹配）────────────────────────────────
#: 跨**进程**边界：起子进程 / 替换镜像 / 进程池。含本机 stdio（v1.4 铁律：本机 stdio 也是 remote）
_PROCESS_BOUNDARY: Tuple[str, ...] = (
    "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.getoutput", "subprocess.getstatusoutput",
    "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
    "os.system", "os.popen", "os.fork", "os.execv", "os.execvp", "os.execl",
    "os.spawnl", "os.spawnv", "os.spawnlp", "os.spawnvp",
    "multiprocessing.Process", "multiprocessing.Pool", "multiprocessing.get_context",
    "concurrent.futures.ProcessPoolExecutor", "pty.spawn", "pty.fork",
    "psutil.Popen", "win32process.CreateProcess",
)

#: 跨**协议/网络**边界：HTTP / socket / 各家客户端 SDK（含 localhost —— 端口就是边界）
_NETWORK_BOUNDARY: Tuple[str, ...] = (
    "urllib.request.urlopen", "urllib.request.Request",
    "socket.socket", "socket.create_connection", "socket.socketpair",
    "http.client.HTTPConnection", "http.client.HTTPSConnection",
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.request", "requests.Session", "requests.head", "requests.patch",
    "httpx.get", "httpx.post", "httpx.Client", "httpx.AsyncClient", "httpx.request",
    "aiohttp.ClientSession", "websockets.connect", "urllib3.PoolManager",
    "smtplib.SMTP", "smtplib.SMTP_SSL", "ftplib.FTP", "paramiko.SSHClient",
    "xmlrpc.client.ServerProxy", "redis.Redis", "pymysql.connect",
    "psycopg2.connect", "pymongo.MongoClient", "sqlalchemy.create_engine",
)

#: 跨**外部宿主**边界：浏览器/IDE 等由第三方进程承载的能力
_HOST_BOUNDARY: Tuple[str, ...] = (
    "selenium.webdriver.Chrome", "selenium.webdriver.Firefox",
    "selenium.webdriver.Edge", "selenium.webdriver.Remote",
    "webdriver.Chrome", "webdriver.Firefox", "webdriver.Remote",
    "playwright.sync_api.sync_playwright", "playwright.async_api.async_playwright",
)

#: 【不易·安全归类】**出进程**的 FFI：WMI 与 DCOM 走的是跨进程 RPC，
#: 不属 v1.4 §2.5「FFI/动态库/WASM 同进程加载 ⇒ local」那一行。
#: 它们的副作用也不受本进程限制（可查/改操作系统状态）⇒ 归 `remote` 并在盘点表写明理由。
_REMOTE_FFI: Tuple[str, ...] = (
    "wmi.WMI", "win32com.client.Dispatch", "win32com.client.DispatchEx",
    "comtypes.client.CreateObject", "win32com.client.gencache.EnsureDispatch",
)

#: 【同进程 FFI】v1.4 铁律归 `local`。保留常量是为了**能识别并如实标注**
#: "这是同进程但副作用极强"的能力（盘点表的备注列），而不是把它们误判成 remote。
_LOCAL_FFI: Tuple[str, ...] = (
    "ctypes.CDLL", "ctypes.WinDLL", "ctypes.cdll.LoadLibrary",
    "ctypes.windll.LoadLibrary", "win32clipboard.OpenClipboard",
    "win32gui.FindWindow", "win32api.GetSystemMetrics",
)

#: 统一的"跨边界"集合（判定只看这一个；
#: `_LOCAL_FFI` 不在其中 —— 它是 local，单独用于生成备注）
_BOUNDARY_CALLS: Tuple[str, ...] = _PROCESS_BOUNDARY + _NETWORK_BOUNDARY + _HOST_BOUNDARY + _REMOTE_FFI

#: 注册来源佐证表：来源 ⇒ (location, 说明)。**只作佐证**，命中后仍需目标语义支持
#: （见 `judge_executor_location` 的 `registry_source_hint` 说明）。
_SOURCE_HINT: Dict[str, Tuple[str, str]] = {
    "mcp": ("remote", "注册来源为 MCP 服务（工具经由 stdio/HTTP 传输层暴露）"),
    "mcp_admin": ("remote", "注册来源标记为 MCP 管理面（作用对象是跨 stdio/HTTP 边界的 MCP 连接）"),
    "plugin": ("local", "注册来源为插件（进程内 Flask 蓝图/模块，不跨边界）"),
    "generated": ("local", "注册来源为 LLM 自生成工具（进程内 exec 装载）"),
    "market": ("local", "注册来源为扩展市场安装（进程内模块）"),
}

#: 扫描根（相对仓库根）。只扫这三个目录：仓库里还有 2200+ 个 py，
#: 全仓扫描会把判定时间推到秒级，而能力的执行链路不会跑出这三个根。
_SCAN_ROOTS: Tuple[str, ...] = ("agent", "mcp_services", "plugins")

#: 有界解析上限（防"跟随调用链走进整个仓库"）
_MAX_DEPTH = 6
_MAX_NODES = 400


# ════════════════════════════════════════════════════════════
#  模块加载与缓存（惰性：只加载解析得到的调用链上的模块）
# ════════════════════════════════════════════════════════════

_TREE_CACHE: Dict[str, Tuple[float, ast.Module]] = {}
_ALIAS_CACHE: Dict[str, Dict[str, str]] = {}
_SYMBOL_CACHE: Dict[str, Dict[str, ast.AST]] = {}
_ENCLOSING_CACHE: Dict[str, str] = {}
_MISS_CACHE: Set[str] = set()


def _module_path(module: str) -> Optional[str]:
    """模块点名 → 仓库内文件路径（找不到返回 None）

    【不易】只认仓库内的三个扫描根。第三方模块（`subprocess`、`selenium`）返回 None，
    它们由原语表匹配，不参与"追进源码"。
    """
    head = module.split(".")[0]
    if head not in _SCAN_ROOTS:
        return None
    rel = module.replace(".", os.sep)
    for cand in (rel + ".py", os.path.join(rel, "__init__.py")):
        path = os.path.join(_REPO_ROOT, cand)
        if os.path.isfile(path):
            return path
    return None


def _module_of_path(path: str) -> str:
    rel = os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel.replace("/", ".")


def _load_tree(module: str) -> Optional[ast.Module]:
    """按 mtime 缓存地解析一个模块（解析失败 ⇒ None，绝不抛给调用方）"""
    path = _module_path(module)
    if path is None or module in _MISS_CACHE:
        return None
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        _MISS_CACHE.add(module)
        return None
    hit = _TREE_CACHE.get(module)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError, UnicodeDecodeError):
        _MISS_CACHE.add(module)
        return None
    _TREE_CACHE[module] = (mtime, tree)
    return tree


# ════════════════════════════════════════════════════════════
#  每模块节点索引（键 = 模块；与 _TREE_CACHE 同步失效）
# ════════════════════════════════════════════════════════════
#
# 【不易·为什么必须有它】`_TREE_CACHE` 只缓存了"**解析**结果"：热调用不再读盘、不再
# `ast.parse`，但 `_class_boundary` / `_enclosing_class` / `_module_boundary_primitives`
# 这几处**每次调用仍对同一棵树 `ast.walk` 整树**。cProfile 实测（热调用、含插桩开销）
# 总 25.2s：`_class_boundary` 251 次 → 14.9s（每次内部都整树遍历）、
# `_module_boundary_primitives` 3024 次 → 6.2s、`_is_boundary` 55.5k 次 → 3.5s——
# 也就是说"缓存是热的"依然慢，成本全在**反复遍历已缓存的树**上。
# 索引把"类名 → 类节点""函数名 → 所在类名"一次算好（子树原语惰性记忆化），
# 之后每次查询是 dict 查找。
#
# 【不易·只加索引，不改判定】索引只回答"**节点在哪**"，判定仍逐字走原来的函数体：
#   · 同名类在模块里可能有**多个定义**，原实现在"第一个同名类无边界原语"时会**继续
#     往后找下一个同名类** ⇒ 索引按 `ast.walk` 顺序保留**全部**同名类节点，调用点逐字照旧；
#   · `_body_boundary_primitives(node.body)` 与原来"合成一个
#     `ast.Module(body=list(node.body))` 再遍历"**等价**：`ast.walk` 对合成 Module 的可达
#     节点集合 = 各语句子树之并（Module 自身不是 `ast.Call`，`type_ignores=[]` 无子节点），
#     且返回值是 `sorted(set)` ⇒ 与遍历顺序无关，逐字相同。
_NODE_INDEX_CACHE: Dict[str, Tuple[Optional[ast.Module], "_NodeIndex"]] = {}


class _NodeIndex:
    """一个模块语法树的节点索引（**一次** `ast.walk` 建成，之后 O(1) 查询）

    失效口径与 `_TREE_CACHE` 一致：缓存记录"由哪一棵树建成"，
    只有传到**同一个树对象**时才复用（文件 mtime 变了 → `_load_tree` 换新树 → 自动重建）；
    `invalidate_cache()` 同时清掉它。
    """

    __slots__ = ("_classes", "_enclosing", "_body_prims")

    def __init__(self, tree: ast.Module) -> None:
        self._classes: Dict[str, List[ast.ClassDef]] = {}
        self._enclosing: Dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            self._classes.setdefault(node.name, []).append(node)
            # 函数 → 所在类（只认**直接子节点**，与原 `_enclosing_class` 逐字一致）
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._enclosing.setdefault(sub.name, node.name)
        self._body_prims: Dict[ast.AST, List[str]] = {}

    def class_nodes(self, cls: str) -> Sequence[ast.ClassDef]:
        """同名类定义节点（**按 `ast.walk` 顺序全部保留**；无则空）"""
        return self._classes.get(cls, ())

    def enclosing_class(self, func: str) -> str:
        """函数所在的类名（不在类里 ⇒ 空串）"""
        return self._enclosing.get(func, "")

    def body_prims_of(self, node: ast.AST) -> List[str]:
        """`node.body` 这组语句（含各语句子树）里的跨边界原语（结果按节点记忆化）"""
        hit = self._body_prims.get(node)
        if hit is None:
            hit = _body_boundary_primitives(node.body)
            self._body_prims[node] = hit
        return hit


def _node_index(module: str, tree: ast.Module) -> _NodeIndex:
    """取模块的节点索引（键 = 模块；与传进来的树对象绑定 ⇒ 随 `_TREE_CACHE` 同步失效）"""
    hit = _NODE_INDEX_CACHE.get(module)
    if hit is not None and hit[0] is tree:
        return hit[1]
    index = _NodeIndex(tree)
    _NODE_INDEX_CACHE[module] = (tree, index)
    return index


def invalidate_cache() -> None:
    """清空模块树与仓库索引缓存（测试与治理脚本用）"""
    global _REPO_INDEX
    _TREE_CACHE.clear()
    _NODE_INDEX_CACHE.clear()
    _MISS_CACHE.clear()
    _ALIAS_CACHE.clear()
    _SYMBOL_CACHE.clear()
    _TRANSPORT_MODULE_CACHE.clear()
    _REPO_INDEX = None


def _iter_repo_modules() -> Iterable[str]:
    for root in _SCAN_ROOTS:
        base = os.path.join(_REPO_ROOT, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".pytest_tmp", "node_modules")]
            for fname in filenames:
                if fname.endswith(".py"):
                    yield _module_of_path(os.path.join(dirpath, fname))


#: 函数定义 / 属性赋值 的正则（**为什么用正则而不是 ast**：建索引要过一遍全仓 600+ 个 py，
#: 实测 `ast.parse` 全量 52s，而"只提取 def 名与 `X.attr = Cls(`"用正则在 2s 量级 ——
#: 索引只回答"这个名字/属性在哪个模块定义"，精确性由后续对该模块的 ast 解析保证，
#: 不需要两遍 ast。两个索引共用**同一趟**文件读取。
_DEF_RE = __import__("re").compile(rb"^[ \t]*(?:async[ \t]+)?def[ \t]+([A-Za-z_][A-Za-z0-9_]*)",
                                  __import__("re").MULTILINE)
#: `self._web_http = HttpClient(` / `dl._discovery_service = ToolDiscoveryService(` 形态
_ATTR_CLASS_RE = __import__("re").compile(
    rb"\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*([A-Z][A-Za-z0-9_]*)[ \t]*\(")

_REPO_INDEX: Optional[Tuple[Dict[str, Tuple[str, ...]], Dict[str, Tuple[Tuple[str, str], ...]]]] = None


def _repo_index():
    """仓库索引：`(函数名 → 定义模块, 属性名 → ((类名, 所在模块), …))`（单趟扫描 + 缓存）

    【为什么需要属性索引】本仓库的执行链路大量写成 `dl._web_http.post(...)` /
    `self._session.request(...)`：变量是实例属性、静态上拿不到类型。但仓库里
    `dl._web_http = HttpClient({...})` 这样的**赋值点**能给出类名，再结合该模块的 import
    即可定位到类的定义模块 —— 这是把"猜"变成"可核事实"的关键一步。
    【不易·不猜】属性名在仓库内对应**多个**类时（如 `_session`）不解析 —— 猜错会把
    local 误报成 remote。
    """
    global _REPO_INDEX
    if _REPO_INDEX is not None:
        return _REPO_INDEX
    defs: Dict[str, List[str]] = {}
    attrs: Dict[str, List[Tuple[str, str]]] = {}
    for module in _iter_repo_modules():
        path = _module_path(module)
        if path is None:
            continue
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        for m in _DEF_RE.finditer(blob):
            defs.setdefault(m.group(1).decode("ascii", "ignore"), []).append(module)
        for m in _ATTR_CLASS_RE.finditer(blob):
            attr = m.group(1).decode("ascii", "ignore")
            cls = m.group(2).decode("ascii", "ignore")
            pair = (cls, module)
            if pair not in attrs.setdefault(attr, []):
                attrs[attr].append(pair)
    _REPO_INDEX = (
        {k: tuple(sorted(set(v))) for k, v in defs.items()},
        {k: tuple(sorted(set(v))) for k, v in attrs.items()},
    )
    return _REPO_INDEX


def _name_index() -> Dict[str, Tuple[str, ...]]:
    """仓库内"函数名 → 定义模块"索引（**仅在名字唯一时才可解析**）

    【为什么需要它】本仓库大量执行链路写成 `self.mcp.connect_http(...)` /
    `discovery.scan_mcp_services(...)`：静态上拿不到对象的类，但**函数名在仓库内唯一**
    时仍能定位到唯一定义处（实测 `connect_http` / `connect_stdio` / `disconnect` /
    `list_connections` / `scan_mcp_services` 在 `agent/**` 内均唯一）。
    【不易·不猜】名字不唯一（如 `start` / `stop` / `run`，实测分别有 20 / 27 / 16 处定义）
    时**不解析**，计入 `unresolved` 而不是随便挑一个 —— 猜错会把 local 误报成 remote。
    """
    return _repo_index()[0]


# ════════════════════════════════════════════════════════════
#  AST 小工具
# ════════════════════════════════════════════════════════════


def _dotted(node: Any) -> str:
    """把调用目标渲染成点分字符串（`urllib.request.urlopen` / `self.mcp.connect_http`）

    非"名字/属性链"形态（下标、调用结果、lambda）⇒ 返回空串。
    """
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return ""
    return ".".join(reversed(parts))


@lru_cache(maxsize=None)
def _is_boundary(dotted: str) -> Optional[str]:
    """点分调用路径是否命中跨边界原语（返回命中的原语，未命中返回 None）

    三种形态都算命中：
      · 完全相同           `subprocess.run`
      · 后缀（模块别名）   `_sp.run`（`import subprocess as _sp`）
      · **前缀（成员调用）** `requests.Session.request` —— 前缀 `requests.Session`

    【不易·结果按调用字符串记忆化】本函数是**纯函数**（只读模块级原语表常量，
    不清点文件、不带状态），cProfile 实测一次清单判定里被调 55.5k 次、占 3.5s，
    而点分字符串的去重度极高 ⇒ 记忆化后同一字符串只算一次。
    """
    if not dotted:
        return None
    for prim in _BOUNDARY_CALLS:
        if dotted == prim or dotted.endswith("." + prim):
            return prim
        if dotted.startswith(prim + "."):
            return prim
    # 形如 `subprocess.run(...)` 的别名导入（`from subprocess import run`）：
    # 只按**末段**匹配少数无歧义的名字，避免把业务里的 `run()` 误判成子进程
    tail = dotted.split(".")[-1]
    if tail in ("create_subprocess_exec", "create_subprocess_shell", "urlopen",
                "Popen", "check_output", "ServerProxy", "Dispatch"):
        for prim in _BOUNDARY_CALLS:
            if prim.endswith("." + tail):
                return prim
    return None


def _local_ffi_hits(tree: ast.Module) -> List[str]:
    """同进程 FFI 命中（**不改变 location**，只作为备注证据）"""
    hits: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            for prim in _LOCAL_FFI:
                if dotted == prim or dotted.endswith("." + prim):
                    hits.add(prim)
    return sorted(hits)


def _body_boundary_primitives(body: Sequence[ast.stmt]) -> List[str]:
    """一组语句（及其子树）里直接出现的跨边界原语（按调用点判定，不按 import 判定）

    【不易·与"合成 Module"等价】原实现对 `ast.Module(body=list(body), type_ignores=[])`
    做 `ast.walk`；`ast.walk` 对该合成 Module 的可达节点集合 = 各语句子树之并
    （Module 自身不是 `ast.Call`，`type_ignores=[]` 无子节点），且本函数返回
    `sorted(set)` ⇒ 与遍历顺序无关，结果**逐字相同**。
    """
    hits: Set[str] = set()
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                prim = _is_boundary(_dotted(node.func))
                if prim:
                    hits.add(prim)
    return sorted(hits)


def _module_boundary_primitives(tree: ast.Module) -> List[str]:
    """一个模块**自身**直接包含的跨边界原语（按调用点判定，不按 import 判定）

    【不易·为什么不看 import】只看 import 会把 `import urllib.parse` 这种
    纯字符串处理也算成网络边界。实测：`agent/tools/pdf_tools.py` 等模块
    import 了 `subprocess` 只为拿 `TimeoutExpired` 异常类，并无调用点。
    """
    return _body_boundary_primitives(tree.body)


def _import_aliases(module: str) -> Dict[str, str]:
    """模块内 `别名 → 模块点名` 映射（`import x.y as z` / `from a.b import c`）

    【不易·必须缓存】本函数原来接收 `ast.Module` 并在每次调用时 `ast.walk` 整棵树。
    实测：一次清单判定会触发 **329 次** `_import_aliases`，单次全树遍历在
    `agent/orchestrator/orchestrator.py`（279KB）这类大模块上是数十万节点 ⇒
    单工具判定被拖到 3.7s、91 个工具共 49s（cProfile 实测 `ast.walk` 占 85% 时间）。
    改为按**模块名**缓存后回到毫秒级。
    """
    cached = _ALIAS_CACHE.get(module)
    if cached is not None:
        return cached
    tree = _load_tree(module)
    out: Dict[str, str] = {}
    if tree is not None:
        # 本模块所属包（相对导入 `from .http_client import X` 要按它展开）
        # 【不易·必须处理相对导入】`agent/web/__init__.py` 用 `from .http_client import HttpClient`
        # 再导出；不展开相对层级时会得到 `http_client.HttpClient`（缺少 `agent.web.` 前缀）⇒
        # 解析不到真实定义模块（实测 `web_get` 因此被漏判成 local）。
        # 【不易·包与非包不同】`__init__.py` 的包就是模块自身（`agent.web`），
        # 普通模块的包是父级（`agent.web.http_client` → `agent.web`）——
        # 两者混淆会把 `from .http_client import X` 展开成 `agent.http_client`（实测踩到）。
        _mp = _module_path(module) or ""
        pkg_parts = module.split(".") if _mp.endswith("__init__.py") else module.split(".")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    out[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    keep = len(pkg_parts) - (node.level - 1)
                    base = ".".join(pkg_parts[:max(0, keep)] + ([base] if base else []))
                if not base:
                    continue
                for a in node.names:
                    # `from <pkg>.tools import <sub>` ⇒ 别名指向子模块
                    out.setdefault(a.asname or a.name, f"{base}.{a.name}")
    _ALIAS_CACHE[module] = out
    return out


def _find_function(module: str, name: str) -> Optional[ast.AST]:
    """在模块内找函数定义（**含嵌套函数**，同名取先出现的）

    【不易·必须含嵌套函数】本仓库的工具几乎都定义在 `def register_all(dl):` **内部**
    （装饰器注册 + 闭包捕获 `dl`）。实测：只扫模块顶层与类体时，`system_tools._shell_execute`
    等一大批执行器会"查不到定义"⇒ 判定回退成保守 local（漏判 81 条）。
    【不易·必须缓存】一次判定会多次查同一模块；不缓存时 `ast.walk` 占 85% 时间
    （cProfile 实测单工具 3.7s / 91 个工具 49s）。按模块名缓存后全量判定 0.8s。
    """
    symbols = _SYMBOL_CACHE.get(module)
    if symbols is None:
        tree = _load_tree(module)
        symbols = {}
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.setdefault(node.name, node)
        _SYMBOL_CACHE[module] = symbols
    return symbols.get(name)


def _enclosing_class(module: str, func: str) -> str:
    """函数所在的类名（不在类里 ⇒ 空串）

    【为什么需要】`self._session.request(...)` 这类调用要先知道"self 是哪个类"，
    才能去查该类的 `self._session = …` 赋值点（见 `_class_attr_boundary`）。
    """
    key = f"{module}:{func}"
    if key in _ENCLOSING_CACHE:
        return _ENCLOSING_CACHE[key]
    name = ""
    tree = _load_tree(module)
    if tree is not None:
        # 【不易·索引查表，判定逐字不变】原实现每次整树 ast.walk 找"直接子节点里
        # 有同名函数的类"，索引在建索引的那一趟里用**同一条判据、同一遍历顺序**
        # （ast.walk、setdefault ⇒ 取第一个命中的类）算好了。
        name = _node_index(module, tree).enclosing_class(func)
    _ENCLOSING_CACHE[key] = name
    return name


def _func_return_boundary(module: str, func: str, _depth: int = 0) -> Optional[str]:
    """函数**返回值**是否是跨边界对象（`return requests.Session()`）

    【为什么需要】网络出口常写成"工厂函数返回客户端、调用方再调它的方法"：
        `self._session = self._build_session()` → `def _build_session(): s = requests.Session(); return s`
    只看调用点会漏掉边界；看返回值就能把 `self._session.request(...)` 认定成跨边界。
    【不易·必须跟局部变量】实测 `_build_session` 是"先赋值、后 `return s`"的写法，
    只认 `return <Call>` 会全部漏掉 —— 故先收集函数内 `局部变量 = <边界调用>` 再核对返回值。
    """
    if _depth > 2:
        return None
    node = _find_function(module, func)
    if node is None:
        return None
    locals_: Dict[str, str] = {}
    returns: List[ast.expr] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call):
            prim = (_is_boundary(_dotted(sub.value.func))
                    or _attribute_origin_boundary(_dotted(sub.value.func), module))
            if not prim:
                nxt = _resolve_callee(_dotted(sub.value.func), module)
                if nxt is not None and nxt != (module, func):
                    prim = _func_return_boundary(nxt[0], nxt[1], _depth + 1)
            if prim:
                for t in sub.targets:
                    if isinstance(t, ast.Name):
                        locals_[t.id] = prim
        elif isinstance(sub, ast.Return) and sub.value is not None:
            returns.append(sub.value)
    for value in returns:
        if isinstance(value, ast.Call):
            dotted = _dotted(value.func)
            prim = _is_boundary(dotted) or _attribute_origin_boundary(dotted, module)
            if prim:
                return prim
            nxt = _resolve_callee(dotted, module)
            if nxt is not None and nxt != (module, func):
                prim = _func_return_boundary(nxt[0], nxt[1], _depth + 1)
                if prim:
                    return prim
        elif isinstance(value, ast.Name) and value.id in locals_:
            return locals_[value.id]
    return None


def _class_attr_boundary(module: str, cls: str, attr: str) -> Optional[str]:
    """类实例属性 `self.<attr>` 的跨边界事实（查 `__init__` / 类体内的赋值点）"""
    if not cls:
        return None
    tree = _load_tree(module)
    if tree is None:
        return None
    # 【不易·索引只换"找类"这一步】原来每次调用整树 ast.walk 找同名 ClassDef；
    # 索引里按 ast.walk 顺序保留了**全部**同名类节点 ⇒ "第一个无原语就继续找下一个
    # 同名类"的语义逐字不变，被替换掉的只有"找类"的遍历。
    for node in _node_index(module, tree).class_nodes(cls):
        for sub in ast.walk(node):
            if not isinstance(sub, (ast.Assign, ast.AnnAssign)):
                continue
            value = sub.value
            if not isinstance(value, ast.Call):
                continue
            targets = [sub.target] if isinstance(sub, ast.AnnAssign) else list(sub.targets)
            if not any(_dotted(t) == f"self.{attr}" for t in targets):
                continue
            dotted = _dotted(value.func)
            prim = _is_boundary(dotted) or _attribute_origin_boundary(dotted, module)
            if prim:
                return prim
            nxt = _resolve_callee(dotted, module)
            if nxt is not None:
                prim = _func_return_boundary(nxt[0], nxt[1])
                if prim:
                    return prim
    return None


# ════════════════════════════════════════════════════════════
#  有界调用链解析
# ════════════════════════════════════════════════════════════


def _class_origin(module: str, cls: str, _depth: int = 0) -> Optional[str]:
    """类名 → 点分位置（`agent.web.http_client.HttpClient` / `requests.Session`）

    先在**赋值所在模块**里找本模块定义的类，再查该模块的 import 别名表；
    别名指向的模块若是"再导出"（`agent/web/__init__.py` 转发），再追一层。
    都找不到 ⇒ None（不猜）。
    """
    tree = _load_tree(module)
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return f"{module}.{cls}"
    target = _import_aliases(module).get(cls)
    if not target:
        return None
    # 再导出：`from agent.web import HttpClient`（真实定义在 agent/web/http_client.py）
    if _depth < 2 and target.rpartition(".")[0]:
        pkg, _, leaf = target.rpartition(".")
        pkg_tree = _load_tree(pkg)
        if pkg_tree is not None and not any(
                isinstance(n, ast.ClassDef) and n.name == leaf for n in pkg_tree.body):
            deeper = _class_origin(pkg, leaf, _depth + 1)
            if deeper:
                return deeper
    return target


def _resolve_attr_class(attr: str, module: str) -> Optional[str]:
    """实例属性名 → 类的点分位置（**优先本模块的赋值点**，跨模块唯一时才采信）"""
    pairs = _repo_index()[1].get(attr) or ()
    if not pairs:
        return None
    local = [p for p in pairs if p[1] == module]
    use = local or list(pairs)
    origins = {o for o in (_class_origin(m, c) for c, m in use) if o}
    return origins.pop() if len(origins) == 1 else None


def _resolve_callee(dotted: str, module: str) -> Optional[Tuple[str, str]]:
    """把一个调用点解析成 `(模块, 函数名)`；解析不到返回 None

    解析顺序（前三步确定，第四步是"名字唯一才用"的保守启发）：
      ① 本模块内的同名函数；
      ② 经 import 别名 → 目标模块里的同名函数（`from agent.x import y` 的两种形态）；
      ③ **实例属性 → 类 → 方法**（`dl._web_http.post` / `self._session.request`）；
      ④ 仓库内**唯一**同名函数（否则不解析，交给 unresolved）。
    """
    if not dotted:
        return None
    parts = dotted.split(".")
    tail = parts[-1]
    # ① 本模块内
    if _find_function(module, tail) is not None:
        return module, tail
    # ② 经别名
    aliases = _import_aliases(module)
    head = parts[0]
    target_mod = aliases.get(head)
    if target_mod:
        if len(parts) == 2:
            cand_mod = target_mod
        else:
            cand_mod = ".".join([target_mod] + parts[1:-1])
        if _find_function(cand_mod, tail) is not None:
            return cand_mod, tail
    # ③ 实例属性 → 类
    if len(parts) >= 3:
        origin = _resolve_attr_class(parts[-2], module)
        if origin and "." in origin:
            cls_mod, _, _cls = origin.rpartition(".")
            if _find_function(cls_mod, tail) is not None:
                return cls_mod, tail
    # ④ 仓库内唯一名
    mods = _name_index().get(tail) or ()
    if len(mods) == 1:
        return mods[0], tail
    return None


def _attribute_origin_boundary(dotted: str, module: str) -> Optional[str]:
    """`obj.attr.method` 形态：经"属性 → 类"还原出的边界路径（`requests.Session.request`）

    【为什么单独一条】`agent/web/http_client.py` 的网络出口写成
    `self._session.request(...)`，而 `_session = requests.Session()` 才是边界事实。
    没有这条，`web_post` 会被漏判成 `local`（实测）。
    """
    parts = dotted.split(".")
    if len(parts) < 3:
        return None
    origin = _resolve_attr_class(parts[-2], module)
    if not origin:
        return None
    # 类的定义模块不在扫描根内 ⇒ 第三方类：用"点分位置 + 方法名"直接对表
    if origin.rpartition(".")[0]:
        cand = f"{origin}.{parts[-1]}"
        prim = _is_boundary(cand)
        if prim:
            return prim
    return None


def _class_boundary(module: str, cls: str) -> Optional[str]:
    """类**整体**的跨边界事实：类里任一方法直接含跨边界原语 ⇒ 该类是跨边界客户端

    【为什么需要它】（v1.4 铁律的"客户端类"情形）MCP 连接器 `McpConnector` 的
    `disconnect(service_id)` 只做"摘掉字典项 + 停掉那个客户端"，**自身不出现任何边界原语**；
    跨边界的事实落在**同一个类的** `connect_stdio`（起子进程）/ `connect_http`（HTTP）上。
    实测：不加这条，`disconnect_mcp` 会被判成 local —— 那是把"传输客户端的关闭动作"
    误判成同进程操作。判定依据仍是可核事实（同类其他方法的原语），不是人工声明。
    """
    if not cls:
        return None
    tree = _load_tree(module)
    if tree is None:
        return None
    # 【不易·本函数是 L6 的第一热点】cProfile 实测 251 次调用 → 14.9s，成本全在
    # "每次调用都整树 ast.walk 找同名类 + 对类体/方法体各再遍历一遍"。
    # 现在：类节点来自索引（按 ast.walk 顺序保留的**全部**同名类），类体/方法体的
    # 原语由索引按节点记忆化（`_body_boundary_primitives` 与"合成 Module 再遍历"
    # 等价，见该函数说明）⇒ 判定分支与返回值**逐字不变**，只是不再重复遍历。
    index = _node_index(module, tree)
    for node in index.class_nodes(cls):
        prims = index.body_prims_of(node)
        if prims:
            return prims[0]
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prims = index.body_prims_of(sub)
                if prims:
                    return prims[0]
    return None


def _walk_chain(root_module: str, root_func: str
                ) -> Tuple[List[Dict[str, str]], List[str], List[str]]:
    """从 `(模块, 函数)` 出发做有界调用链遍历

    Returns:
        (boundary_hits, unresolved, ffi_hits)
        boundary_hits: `[{"primitive":…, "via_module":…, "via_func":…, "kind":…}]` 逐条可核
        unresolved:    解析不到 callee 的调用点表达式（**不猜**，如实披露）
    """
    hits: List[Dict[str, str]] = []
    unresolved: List[str] = []
    ffi: Set[str] = set()
    seen: Set[Tuple[str, str]] = set()
    visited_nodes = 0
    stack: List[Tuple[str, str, int]] = [(root_module, root_func, 0)]

    while stack and visited_nodes < _MAX_NODES:
        module, func, depth = stack.pop()
        key = (module, func)
        if key in seen or depth > _MAX_DEPTH:
            continue
        seen.add(key)
        visited_nodes += 1
        node = _find_function(module, func)
        if node is None:
            continue
        ffi.update(_local_ffi_hits(ast.Module(body=[node], type_ignores=[])))
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            dotted = _dotted(sub.func)
            prim = _is_boundary(dotted) or _attribute_origin_boundary(dotted, module)
            if not prim and dotted.startswith("self.") and len(dotted.split(".")) >= 3:
                prim = _class_attr_boundary(module, _enclosing_class(module, func),
                                            dotted.split(".")[1])
            if prim:
                hits.append({"primitive": prim, "via_module": module,
                             "via_func": func, "kind": "direct"})
                continue
            # ── 规则 E：字符串字面量分发（`getattr(obj, "list_mcp_connections")`）──
            # 【为什么需要它】仓库里大量"兼容旧接口"的写法用 `getattr(..., "<方法名>")`
            # 取方法再调用，静态上看不到调用点；实测 `list_mcp_connections` 就靠这条才能
            # 落到 `agent.tools.mcp_connector`（否则会被漏判成 local）。
            if isinstance(sub.func, ast.Name) and sub.func.id == "getattr" and len(sub.args) >= 2:
                lit = sub.args[1]
                if isinstance(lit, ast.Constant) and isinstance(lit.value, str) and lit.value:
                    nxt = _resolve_callee(lit.value, module)
                    if nxt is not None:
                        stack.append((nxt[0], nxt[1], depth + 1))
                    continue
            nxt = _resolve_callee(dotted, module)
            if nxt is None:
                if dotted:
                    unresolved.append(dotted)
                continue
            stack.append((nxt[0], nxt[1], depth + 1))

    # ── 兜底事实：链路里出现过"跨边界客户端类"的方法 ──
    # 只在**函数级完全没命中**时启用，避免无谓放大（见 `_class_boundary` 的理由）。
    if not hits:
        seen_classes: Set[Tuple[str, str]] = set()
        for module, func in sorted(seen):
            cls = _enclosing_class(module, func)
            if not cls or (module, cls) in seen_classes:
                continue
            seen_classes.add((module, cls))
            prim = _class_boundary(module, cls)
            if prim:
                hits.append({"primitive": prim, "via_module": module,
                             "via_func": func, "kind": "class_transport"})
    return hits, sorted(set(unresolved)), sorted(ffi)


# ════════════════════════════════════════════════════════════
#  传输模块（供证据说明用）
# ════════════════════════════════════════════════════════════

_TRANSPORT_MODULE_CACHE: Dict[str, List[str]] = {}


def module_boundary_primitives(module: str) -> List[str]:
    """模块自身直接包含的跨边界原语（缓存；模块不存在 ⇒ 空表）"""
    if module in _TRANSPORT_MODULE_CACHE:
        return _TRANSPORT_MODULE_CACHE[module]
    tree = _load_tree(module)
    out = _module_boundary_primitives(tree) if tree is not None else []
    if tree is None:
        # 第三方模块不算"仓库内传输模块"（`subprocess` 之类由原语表直接命中）
        out = []
    _TRANSPORT_MODULE_CACHE[module] = out
    return out


# ════════════════════════════════════════════════════════════
#  对外判定入口
# ════════════════════════════════════════════════════════════


def parse_executor(host_executor: str) -> Tuple[str, str]:
    """`模块:函数` → `(模块, 函数)`；非法形态 ⇒ `("", "")`"""
    text = str(host_executor or "").strip()
    if ":" not in text:
        return "", ""
    module, _, func = text.partition(":")
    return module.strip(), func.strip()


def judge_executor_location(host_executor: str, *, declared: str = "",
                            registry_source: str = "") -> Dict[str, Any]:
    """由**执行器事实**判定 `location`

    Args:
        host_executor: `模块:函数`（清单的 `host_executor` 字段）
        declared: 声明值（YAML/覆盖表的 `location`）。非空时作为**钉住值**返回，
            但 `consistent=False` 表示与事实不符（`--check` 据此非零退出）。
        registry_source: 注册来源事实（`registry_facts()["source"]`），仅作佐证。

    Returns:
        `{"location", "source", "declared", "consistent", "evidence", "unresolved", "ffi"}`
        - `location`：最终取值
        - `source`：判定来源（见 `LOCATION_SOURCES`）
        - `consistent`：声明与事实是否一致（无声明时恒为 True）
        - `evidence`：逐条可核的事实说明（**不接受黑箱结论**）
    """
    module, func = parse_executor(host_executor)
    evidence: List[str] = []
    unresolved: List[str] = []
    ffi: List[str] = []

    if not module or not func:
        derived, src = DEFAULT_LOCATION, "default"
        evidence.append("无执行器（host_executor 为空）：按保守默认 remote 处理")
    else:
        hits, unresolved, ffi = _walk_chain(module, func)
        if hits:
            derived, src = "remote", "executor_boundary"
            seen_keys: Set[str] = set()
            for h in hits:
                key = f"{h['kind']}|{h['via_module']}|{h['primitive']}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                via = f"{h['via_module']}:{h['via_func']}"
                evidence.append(f"调用链经 {via} 命中跨边界原语 {h['primitive']}")
        else:
            # 无硬边界事实：先看注册来源佐证，再看保守兜底。
            # 【不易】来源佐证只在**语义与目标一致**时采用：
            #   `mcp`/`mcp_admin` 的注册来源本身就意味着"这个工具的存在意义是跨边界传输"，
            #   `builtin` 无来源语义 ⇒ 不作为佐证（否则会把 87 个内置工具全判成 local，
            #   那是"从注册来源推执行边界"的错误，v1.4 §5.1 明确两者不同构）。
            hint = _SOURCE_HINT.get(str(registry_source or "").strip())
            if hint and hint[0] == "remote":
                derived, src = "remote", "registry_source"
                evidence.append(f"注册来源佐证：{hint[1]}")
            else:
                derived, src = "local", "executor_boundary"
                evidence.append(
                    f"调用链（{module}:{func}）在 {_MAX_DEPTH} 跳内未命中任何跨边界原语"
                    + (f"；另有 {len(unresolved)} 个调用点静态无法解析（见 unresolved）"
                       if unresolved else ""))

    declared_val = str(declared or "").strip().lower()
    if declared_val:
        consistent = declared_val == derived
        final = declared_val if declared_val in LOCATIONS else derived
        if consistent:
            evidence.append(f"声明钉住 location={declared_val}，与事实判定一致")
        else:
            evidence.append(
                f"⚠️ 声明 location={declared_val} 与事实判定 {derived} **不一致**"
                f"（--check 会非零退出）")
        return {
            "location": final, "source": "declaration", "declared": declared_val,
            "consistent": consistent, "derived": derived,
            "derived_source": src, "evidence": evidence,
            "unresolved": unresolved, "ffi": ffi,
        }

    if ffi:
        evidence.append(
            "同进程 FFI（v1.4 §2.5 归 local，但副作用极强，需安全审查）：" + ", ".join(ffi))
    return {
        "location": derived, "source": src, "declared": "",
        "consistent": True, "derived": derived, "derived_source": src,
        "evidence": evidence, "unresolved": unresolved, "ffi": ffi,
    }


def judge_skill_location(sid: str, facts: Dict[str, Any], *, declared: str = "") -> Dict[str, Any]:
    """技能侧 `location` 判定（**从技能事实**判，不看技能名）

    事实依据（`callability._skill_sources()` 的产品）：
      - `has_scripts=True` ⇒ 由 `SkillExecutor` 以 `subprocess.run` 起子进程执行 ⇒ `remote`
        （实测 `agent/skills_mgmt/executor.py:202` 的 `subprocess.run`）
      - 否则（纯提示词技能）⇒ 由 `ContextInjector` 在**本进程内**拼进提示词 ⇒ `local`
    """
    has_scripts = bool(facts.get("has_scripts"))
    executor = str(facts.get("_executor") or "")
    evidence: List[str] = []
    if has_scripts:
        derived = "remote"
        evidence.append(
            "带脚本技能：SkillExecutor（agent/skills_mgmt/executor.py:202）以 subprocess.run "
            "起子进程执行脚本 ⇒ 跨进程边界")
    else:
        derived = "local"
        evidence.append(
            "纯提示词技能：ContextInjector 在**本进程内**把内容注入提示词，无子进程/无网络调用")
    if executor:
        evidence.append(f"执行器：{executor}")

    declared_val = str(declared or "").strip().lower()
    if declared_val:
        consistent = declared_val == derived
        if consistent:
            evidence.append(f"声明钉住 location={declared_val}，与事实判定一致")
        else:
            evidence.append(f"⚠️ 声明 location={declared_val} 与事实判定 {derived} 不一致")
        return {"location": declared_val, "source": "declaration", "declared": declared_val,
                "consistent": consistent, "derived": derived, "derived_source": "skill_chain",
                "evidence": evidence, "unresolved": [], "ffi": []}
    return {"location": derived, "source": "skill_chain", "declared": "",
            "consistent": True, "derived": derived, "derived_source": "skill_chain",
            "evidence": evidence, "unresolved": [], "ffi": []}


def summarize_locations(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """`location` 分布统计（供清单 `counts` 与盘点表表头）"""
    out: Dict[str, Any] = {loc: 0 for loc in LOCATIONS}
    by_source: Dict[str, int] = {}
    by_kind: Dict[str, Dict[str, int]] = {}
    for e in entries:
        loc = str(e.get("location") or "")
        if loc in out:
            out[loc] += 1
        src = str(e.get("location_source") or "")
        by_source[src] = by_source.get(src, 0) + 1
        kind = str(e.get("kind") or e.get("tool_type") or "?")
        by_kind.setdefault(kind, {k: 0 for k in LOCATIONS})[loc] = \
            by_kind.setdefault(kind, {k: 0 for k in LOCATIONS}).get(loc, 0) + 1
    out["by_source"] = dict(sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0])))
    out["by_kind"] = by_kind
    return out


__all__ = [
    "LOCATIONS", "DEFAULT_LOCATION", "LOCATION_SOURCES",
    "parse_executor", "judge_executor_location", "judge_skill_location",
    "module_boundary_primitives", "summarize_locations", "invalidate_cache",
]
