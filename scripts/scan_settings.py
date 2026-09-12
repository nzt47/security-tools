"""开关机械提取器（TASK-S7-01 步骤 1）

【任务定位】
    「开关中心」的**唯一事实源**是 `agent/settings/registry.py`（`SettingSpec` 表）。
    但手写清单会漂移：代码里新增一个 `os.getenv("FOO_ENABLED")` 而注册表没跟上时，
    UI 会**悄悄少一个开关**（且没人发现）。本脚本把「代码里到底读了哪些开关」用
    **AST 机械提取**出来，并与注册表比对，缺口即失败（CI 守护见
    `tests/unit/test_settings_registry.py::test_scan_zero_gap`）。

【为什么用 AST 而不是正则】
    本仓库的开关读取点大量使用**模块级常量**间接寻址，例如：

        _ENV_ENABLED = "AUTHZ_ALERT_ENABLED"
        def alerts_enabled() -> bool:
            return _env_flag(_ENV_ENABLED, "1")

    正则只能看到 `_env_flag(_ENV_ENABLED, ...)`（常量名，不是开关名），会把
    `_ENV_ENABLED` 误当成开关名；反之 WSGI 中间件里的 `environ.get("PATH_INFO")`
    又会误报成开关。故本脚本：

    1. 用 `ast` 解析每个文件，建立**同文件常量表**（名 → 字符串字面量）；
    2. 只认「已知读取助手」与「os 环境访问」两类调用，并做**作用域检查**
       （函数参数名为 `environ` / `env` 时，`environ.get(...)` 是 WSGI environ，
       不是进程环境，必须排除）；
    3. 第一个实参经常量表解析成真实开关名；解析不出字面量时归入 `dynamic`
       （f-string / 变量拼接），单独披露而非丢弃。

【三类结果（不混为一谈，避免"零缺口"变成空话）】
    | 分类 | 含义 | 与注册表的关系 |
    |---|---|---|
    | `managed` | 真实开关读取点（env 名可解析） | **必须 100% 被注册表覆盖**，缺口即失败 |
    | `dynamic` | 动态名（前缀家族，如 `SKILLS_ASSESS_<KEY>`） | 必须登记在注册表的 `dynamic_prefix` 白名单 |
    | `process_env` | 进程/WSGI/运行时的环境读取（非开关） | 必须命中**显式排除表**（带理由），新增排除项需改代码留痕 |

【用法】
    python scripts/scan_settings.py                 # 人读摘要
    python scripts/scan_settings.py --json out.json # 机器可读报告
    python scripts/scan_settings.py --check         # 缺口检查（非零退出 = 有缺口）
    python scripts/scan_settings.py --path agent    # 只扫指定子目录
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: 仓库根（scripts/ 的上一级）
REPO_ROOT = Path(__file__).resolve().parent.parent

#: 默认扫描根（相对仓库根）
DEFAULT_ROOTS: Tuple[str, ...] = ("agent",)

# ════════════════════════════════════════════════════════════
#  读取助手识别
# ════════════════════════════════════════════════════════════

#: 已知的「环境开关读取助手」名（各模块自带的私有助手；名字即契约）
KNOWN_READ_HELPERS: frozenset = frozenset({
    "_env_flag", "_env_flag_bool", "_env_bool", "_env_int", "_env_float",
    "_env_str", "_env_path", "_bool_env", "_flag", "_env", "_getenv",
    "_read_env", "_env_value",
    # 【S8-02 实测补登：三态开关读取助手】`DecisionLog` 的
    # `CP_POLICY_DECISION_LOG_LOCK_APPENDS` 走 `_env_optional_flag(...)`
    # ——它的语义正是"**未设置 ≠ False**"（未设置 = auto），因此**必须**用三态助手，
    # 不能用 `_env_flag`。而本名单原无此名，且下面的正则也匹配不到
    # （`_env_optional_flag` 中间夹了 `optional`，不在 `(?:flag|bool|int|float|str|path|value)`
    # 允许集内）⇒ **零缺口硬守卫出现假阴性**：该开关读得到、却扫不出、于是可以
    # "未登记还不报错"（本项目实测发生）。故显式登记名字。
    # 【为什么补名单而不放宽正则（不易）】正则放宽会重演 S7-01 踩过的坑
    # （`_` + 可选类型后缀 把 `self._path(x)` 这类普通方法误判成开关读取助手）；
    # 名单是显式的、可审计的，新增一个助手名就补一行。
    "_env_optional_flag",
})

#: 助手名匹配模式（覆盖 `_env_flag` / `_env_flag_bool` / `_env_int` 一类命名）
#: 纪律：**必须带 env/getenv 词干**。早期版本写成「`_` + 可选类型后缀」，
#: 结果把 `self._path(x)` 这类普通方法误判成开关读取助手（S7-01 实测踩坑）。
_READ_HELPER_PATTERN = re.compile(
    r"^_?(?:env|getenv|read_env|envs?)(?:_(?:flag|bool|int|float|str|path|value))*$"
    r"|^_env_(?:flag|bool|int|float|str|path)(?:_bool)?$")

#: 进程环境**直通白名单**读取点（把父进程 env 原样转发给子进程，不是云枢开关）
#: 形态：`for key in _ENV_WHITELIST: os.environ.get(key)`——名字来自集合，
#: 机械上无法判定它是开关还是透传，故**显式声明**并单独披露（新增项须改本表）。
PASS_THROUGH_SITES: Dict[str, str] = {
    "agent/skills_mgmt/executor.py::_ENV_WHITELIST":
        "技能子进程环境白名单：把父进程既有 env 原样转发（PYTHONUTF8 等），"
        "不是可切换的云枢开关",
    # ── 以下三项于 2026-09-13 补（S8-03 灰度容器隔离引入；当时使零缺口硬守卫变红）──
    # 说明：三处都**只读环境变量用于转发或留证**，没有任何"运维可切换行为"的语义，
    #       故按本表口径（透传白名单≠开关）显式声明。**未声明的来源照样进
    #       `undeclared_passthrough`**（见 check_gaps），本表不放宽任何判定。
    "agent/digestion/isolation.py::HOST_ENV_ALLOWLIST":
        "隔离子进程的宿主环境白名单：把运行 Python 必需的 OS 变量"
        "（SystemRoot/WINDIR/COMSPEC/PATHEXT/NUMBER_OF_PROCESSORS/"
        "PROCESSOR_ARCHITECTURE）原样转发给隔离子进程；它们是操作系统提供的值，"
        "不是云枢开关（改了也不改变云枢行为，只会让子进程起不来）",
    "agent/digestion/isolation_worker.py::ISOLATION_ENV_WATCH":
        "隔离环境**留证**名单：对固定的敏感变量清单（HOME/SSH_*/各代理/各家凭据）"
        "做只读快照，用于「凭据可见性」的如实披露（`platform_env_raw`）；"
        "只读出值、不做任何切换，故非开关",
    "agent/digestion/isolation_worker.py::names":
        "隔离诊断 op（`_op_env_dump`）的导出名单：默认取 ISOLATION_ENV_WATCH，"
        "调用方（探测作业）可显式覆盖；用途是只读回报'哪些变量可见'，非开关",
}

#: 助手名 → 值类型推断
_HELPER_TYPE_HINTS: Tuple[Tuple[str, str], ...] = (
    ("flag", "bool"), ("bool", "bool"), ("int", "int"),
    ("float", "float"), ("path", "path"), ("str", "str"),
)

#: 进程环境访问属性链（os.getenv / os.environ.get）
#: 注意：**不含 `os.environ.pop`**——那是"删除"（写），不是配置读取。
_OS_ENV_CALLS: frozenset = frozenset({"os.getenv", "os.environ.get"})

#: 允许的「运行时名字」读取形态 → 理由
#: 形态：`os.environ.get(<表达式>)`，其中表达式**不是**某个具体开关名，而是
#: "当前条目自己声明的 env 名"（名字来自开关注册表本身）。这类读取必须显式声明，
#: 否则归入缺口；新增形态需改本表并留痕（防"用 catch-all 掩盖真实缺口"）。
RUNTIME_NAME_READS: Dict[str, str] = {
    "spec.env_name":
        "读取『当前条目声明的 env 名』（开关注册表字段），不是某个具体开关——"
        "开关中心按注册表遍历，名字由表决定而非硬编码",
}

#: 仅在「外层函数无同名参数」时才算进程环境的裸访问（WSGI `environ` 同名）
_BARE_ENV_CALLS: frozenset = frozenset({"environ.get", "env.get", "getenv"})

#: 外层作用域参数若叫这些名字，则裸 `X.get(...)` 视为 WSGI/第三方 environ
_ENVIRON_PARAM_NAMES: frozenset = frozenset({"environ", "env", "wsgi_environ"})

# ════════════════════════════════════════════════════════════
#  显式排除表（进程 / 运行时环境变量，**不是开关**）
# ════════════════════════════════════════════════════════════
# 纪律：本表只放「读它不是为了让运维切换行为」的名字。每一条都必须有理由，
#       单测据此断言排除集**恰好**等于本表（新增排除项必须改代码并留痕）。
PROCESS_ENV_DENYLIST: Dict[str, str] = {
    "PATH_INFO": "WSGI environ（HTTP 请求），非进程环境",
    "REQUEST_METHOD": "WSGI environ（HTTP 请求），非进程环境",
    "SERVER_NAME": "WSGI environ（HTTP 请求），非进程环境",
    "SERVER_PORT": "WSGI environ（HTTP 请求），非进程环境",
    "REMOTE_ADDR": "WSGI environ（HTTP 请求），非进程环境",
    "HTTP_HOST": "WSGI environ（HTTP 请求），非进程环境",
    "QUERY_STRING": "WSGI environ（HTTP 请求），非进程环境",
    "CONTENT_TYPE": "WSGI environ（HTTP 请求），非进程环境",
    "wsgi.url_scheme": "WSGI environ（HTTP 请求），非进程环境",
    "USER": "操作系统会话变量（当前用户），非开关",
    "USERNAME": "操作系统会话变量（当前用户），非开关",
    "HOME": "操作系统会话变量（用户主目录），非开关",
    "USERPROFILE": "操作系统会话变量（用户主目录），非开关",
    "SSH_AUTH_SOCK": "沙箱/SSH 代理套接字（外部运行时注入），非开关",
    "TRACE_ID": "本进程内的 trace 上下文变量（跨模块传递用），非开关",
    "PYTHONUTF8": "解释器启动参数（Windows 中文环境字节口径），进程启动前生效，非开关",
    "PYTHONIOENCODING": "解释器启动参数（stdout 编码），进程启动前生效，非开关",
    "HF_HOME": "第三方库（huggingface）缓存目录，由外部环境注入，非云枢开关",
    "TRANSFORMERS_CACHE": "第三方库（transformers）缓存目录，由外部环境注入，非云枢开关",
    "SENTENCE_TRANSFORMERS_HOME": "第三方库缓存目录，由外部环境注入，非云枢开关",
    "HF_HUB_DOWNLOAD_TIMEOUT": "第三方库（huggingface）下载超时，由外部环境注入，非云枢开关",
}

# ════════════════════════════════════════════════════════════
#  动态名（f-string / 拼接）识别
# ════════════════════════════════════════════════════════════

#: 动态开关家族的注册表白名单由 `agent/settings/registry.py` 提供
#: （`dynamic_prefixes()`）；本脚本只负责把它们提取出来并交给检查器比对。
_DYNAMIC_NAME_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)$")

#: 环境变量形态的**名字前缀**（如 `CP_REPAIR_`）：全大写 + 数字 + 下划线，且以 `_` 结尾。
#: 用于把"拼接助手"里的普通字符串模板（`"cache:" + name`）与真正的开关名前缀区分开。
_ENV_PREFIX_SHAPE = re.compile(r"^[A-Z][A-Z0-9_]*_$")


# ════════════════════════════════════════════════════════════
#  提取结果数据结构
# ════════════════════════════════════════════════════════════

@dataclass
class ReadPoint:
    """一个开关读取点"""

    name: str = ""              # 开关名（env 名）；动态名时为空
    dynamic_prefix: str = ""    # 动态名家族前缀（如 "SKILLS_ASSESS_"）
    loop_source: str = ""       # 循环集合来源符号（kind=loop_collection 时）
    runtime_expr: str = ""      # 运行时名字表达式（kind=runtime_name 时）
    kind: str = "env"           # 读取方式：helper / os.environ / subscript / dynamic
    helper: str = ""            # 助手函数名（kind=helper 时）
    value_type: str = ""        # 推断值类型（bool/int/float/str/path）
    default_literal: str = ""   # 调用点字面量默认值（无则空）
    module: str = ""            # 相对仓库根的模块路径
    line: int = 0
    source_line: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "dynamic_prefix": self.dynamic_prefix,
            "kind": self.kind, "helper": self.helper,
            "value_type": self.value_type,
            "default_literal": self.default_literal,
            "module": self.module, "line": self.line,
            "loop_source": self.loop_source,
            "runtime_expr": self.runtime_expr,
            "source_line": self.source_line,
        }


@dataclass
class ScanReport:
    """一次扫描的全部结果"""

    managed: List[ReadPoint] = field(default_factory=list)
    dynamic: List[ReadPoint] = field(default_factory=list)
    process_env: List[ReadPoint] = field(default_factory=list)
    passthrough: List[ReadPoint] = field(default_factory=list)
    runtime_reads: List[ReadPoint] = field(default_factory=list)
    files_scanned: int = 0
    parse_errors: List[Dict[str, str]] = field(default_factory=list)

    # ── 派生视图 ──

    def managed_names(self) -> Dict[str, List[ReadPoint]]:
        """managed 读取点按开关名聚合（名 → 全部读取点）"""
        out: Dict[str, List[ReadPoint]] = {}
        for rp in self.managed:
            out.setdefault(rp.name, []).append(rp)
        return out

    def dynamic_prefixes(self) -> Dict[str, List[ReadPoint]]:
        out: Dict[str, List[ReadPoint]] = {}
        for rp in self.dynamic:
            out.setdefault(rp.dynamic_prefix, []).append(rp)
        return out

    def process_env_names(self) -> Set[str]:
        return {rp.name for rp in self.process_env}

    def to_dict(self) -> Dict[str, Any]:
        managed_by_name = self.managed_names()
        return {
            "files_scanned": self.files_scanned,
            "counts": {
                "managed_names": len(managed_by_name),
                "managed_read_points": len(self.managed),
                "dynamic_families": len(self.dynamic_prefixes()),
                "dynamic_read_points": len(self.dynamic),
                "process_env_names": len(self.process_env_names()),
                "passthrough_sites": len({rp.loop_source for rp in self.passthrough}),
                "runtime_name_exprs": len({rp.runtime_expr
                                           for rp in self.runtime_reads}),
                "parse_errors": len(self.parse_errors),
            },
            "managed": {k: [rp.to_dict() for rp in v]
                        for k, v in sorted(managed_by_name.items())},
            "dynamic": {k: [rp.to_dict() for rp in v]
                        for k, v in sorted(self.dynamic_prefixes().items())},
            "process_env": sorted(self.process_env_names()),
            "passthrough": sorted({rp.loop_source for rp in self.passthrough}),
            "runtime_name_reads": sorted({rp.runtime_expr
                                          for rp in self.runtime_reads}),
            "parse_errors": self.parse_errors,
        }


# ════════════════════════════════════════════════════════════
#  AST 提取
# ════════════════════════════════════════════════════════════

class _Extractor(ast.NodeVisitor):
    """单文件提取器

    - 同文件常量表：模块级/类级/函数级 `NAME = "LITERAL"`（值须为全大写开关名；
      支持 `A + "_X"` 与 `f"{A}_X"` 折叠），以及 `self.NAME = "LITERAL"`；
      同名多值时标记为不明确（解析失败 → dynamic）。
    - 作用域：`environ.get(...)` / `env.get(...)` 在参数同名函数内视为 WSGI，
      归入 process_env（不误报为开关）；赋值左值（`os.environ[k] = v`）不算读取点。
    """

    def __init__(self, module_rel: str, lines: Sequence[str],
                 global_consts: Optional[Dict[str, Optional[str]]] = None,
                 source_text: str = "") -> None:
        self.module = module_rel
        self._lines = lines
        self._source = source_text or "\n".join(lines)
        self.consts: Dict[str, Optional[str]] = {}
        #: 全仓常量索引（名 → 唯一字面量；跨模块 import 的常量回退用）
        self._global_consts: Dict[str, Optional[str]] = global_consts or {}
        self._environ_params: Set[str] = set()
        self._func_scopes: List[Tuple[int, int, Set[str]]] = []
        self._environ_ranges: List[Tuple[int, int]] = []
        self.local_helpers: Dict[str, Dict[str, str]] = {}
        #: 循环变量 → 字面量集合（形如 `for k, env_name in [("a","X_ENV")]`）
        self._loop_literals: Dict[str, List[str]] = {}
        #: 循环变量 → 具名集合符号（形如 `for key in _ENV_WHITELIST`）
        self._loop_from_name: Dict[str, str] = {}
        self.reads: List[ReadPoint] = []
        #: 赋值左值（`os.environ[k] = v` / `del os.environ[k]`）的行号
        self._target_subscripts: Set[int] = set()

    # ── 常量表 ──

    def _assign_targets(self, tree: ast.AST) -> None:
        """记录赋值/删除语句里的下标左值（它们不是读取点）"""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    for sub in ast.walk(tgt):
                        if isinstance(sub, ast.Subscript):
                            self._target_subscripts.add(id(sub))
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                if node.target is not None:
                    for sub in ast.walk(node.target):
                        if isinstance(sub, ast.Subscript):
                            self._target_subscripts.add(id(sub))
            elif isinstance(node, ast.Delete):
                for tgt in node.targets:
                    for sub in ast.walk(tgt):
                        if isinstance(sub, ast.Subscript):
                            self._target_subscripts.add(id(sub))

    def collect_constants(self, tree: ast.AST) -> None:
        """建立同文件常量表（支持 `A + "_X"` 与 `f"{A}_X"` 折叠）

        仓库里大量开关名由常量拼接而成（如 `_ENV_ENABLED = _PREFIX + "_ENABLED"`），
        折叠后才能拿到真实开关名——否则会退化成"动态家族"而看不全。
        还覆盖 `self.ENV_ROOT = "..."`（类里存常量、方法里 `os.environ.get(self.ENV_ROOT)`）。
        """
        for node in ast.walk(tree):
            targets: List[ast.expr] = []
            value: Optional[ast.expr] = None
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
                value = node.value
            else:
                continue
            literal = self._fold(value)
            if literal is None or not _DYNAMIC_NAME_PATTERN.match(literal):
                continue
            for tgt in targets:
                name = ""
                if isinstance(tgt, ast.Name):
                    name = tgt.id
                elif (isinstance(tgt, ast.Attribute)
                      and isinstance(tgt.value, ast.Name)
                      and tgt.value.id in ("self", "cls")):
                    name = f"{tgt.value.id}.{tgt.attr}"
                if not name:
                    continue
                if name in self.consts and self.consts[name] != literal:
                    self.consts[name] = None      # 同名多值 → 不明确
                else:
                    self.consts.setdefault(name, literal)

    def collect_loop_literals(self, tree: ast.AST) -> None:
        """收集「循环变量绑定到集合」的映射

        - 集合是**字面量**（`for k, env_name in [("tfidf", "SKILLS_FUSION_WEIGHT_TFIDF")]`）
          → 可静态解析出真实开关名（真实存在的开关，不能漏）；
        - 集合是**具名常量**（`for key in _ENV_WHITELIST`）→ 记下来源符号，
          交由 `PASS_THROUGH_SITES` 显式声明（透传白名单≠开关，见该表说明）。
        """
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            pool = _literal_collection(node.iter, self.consts)
            tgt = node.target
            names: List[str] = []
            if isinstance(tgt, ast.Name):
                names = [tgt.id]
            elif isinstance(tgt, (ast.Tuple, ast.List)):
                names = [e.id for e in tgt.elts if isinstance(e, ast.Name)]
            if pool:
                if isinstance(tgt, ast.Name):
                    self._loop_literals.setdefault(tgt.id, []).extend(pool)
                else:
                    for idx, nm in enumerate(names):
                        values = [row[idx] for row in pool if len(row) > idx]
                        if values:
                            self._loop_literals.setdefault(nm, []).extend(values)
            elif isinstance(node.iter, ast.Name):
                for nm in names:
                    self._loop_from_name[nm] = node.iter.id

    def _fold(self, node: Optional[ast.expr]) -> Optional[str]:
        """字符串常量折叠（字面量 / 已知常量 / 加法拼接 / f-string 静态部分）"""
        lit = _literal_str(node)
        if lit is not None:
            return lit
        if isinstance(node, ast.Name):
            got = self.consts.get(node.id)
            if got:
                return got
            return self._global_consts.get(node.id) or None
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in ("self", "cls")):
            # `self.ENV_ROOT` 既可能是类里 `self.X = "..."`，也可能引用同文件的
            # 模块级常量 `X = "..."`（forgetting.py 即后者）→ 两种都试
            return (self.consts.get(f"{node.value.id}.{node.attr}")
                    or self.consts.get(node.attr) or None)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._fold(node.left)
            right = self._fold(node.right)
            if left is not None and right is not None:
                return left + right
            return None
        if isinstance(node, ast.JoinedStr):
            parts: List[str] = []
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    folded = self._fold(part.value)
                    if folded is None:
                        return None               # 有运行时插值 → 不能折叠
                    parts.append(folded)
                else:
                    piece = _literal_str(part)
                    if piece is None:
                        return None
                    parts.append(piece)
            return "".join(parts)
        return None

    def _resolve_name(self, node: ast.expr) -> Tuple[str, bool]:
        """把表达式解析成开关名；返回 (名字, 是否可静态解析)"""
        folded = self._fold(node)
        if folded is not None:
            return folded, True
        # 循环变量绑定到字面量集合时，用其中最长的公共前缀家族表示
        if isinstance(node, ast.Name) and node.id in self._loop_literals:
            values = self._loop_literals[node.id]
            if len(values) == 1:
                return values[0], True
            return _common_prefix(values) + "*", False
        # 不能折叠时，尽量给出「动态家族前缀」（如 SKILLS_ASSESS_）
        return _dynamic_prefix(node, self.consts), False

    @staticmethod
    def _call_name(node: ast.expr) -> str:
        parts: List[str] = []
        cur: Optional[ast.expr] = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    def _enclosing_environ_params(self, tree: ast.AST) -> None:
        """收集「函数参数里出现 environ/env」的函数体行号区间

        简化实现：只要文件里存在名为 environ/env 的参数，就把该函数体内
        的裸 `environ.get` / `env.get` 视为 WSGI 访问。用 (start, end) 区间表示。
        """
        self._environ_ranges: List[Tuple[int, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = {a.arg for a in list(args.args) + list(args.posonlyargs)
                     + list(args.kwonlyargs)}
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
            if names & _ENVIRON_PARAM_NAMES:
                self._environ_params |= names & _ENVIRON_PARAM_NAMES
                start = getattr(node, "lineno", 0)
                end = max((getattr(n, "lineno", start) for n in ast.walk(node)),
                          default=start)
                self._environ_ranges.append((start, end))

    def _collect_function_scopes(self, tree: ast.AST) -> None:
        """收集每个函数体的 (行号区间, 形参名集合)——用于识别「直通助手」"""
        self._func_scopes: List[Tuple[int, int, Set[str]]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            names = {a.arg for a in list(args.args) + list(args.posonlyargs)
                     + list(args.kwonlyargs)}
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
            start = getattr(node, "lineno", 0)
            end = max((getattr(n, "lineno", start) for n in ast.walk(node)),
                      default=start)
            self._func_scopes.append((start, end, names))

    def _enclosing_params(self, lineno: int) -> Set[str]:
        best: Set[str] = set()
        best_span = None
        for start, end, names in self._func_scopes:
            if start <= lineno <= end:
                span = end - start
                if best_span is None or span < best_span:
                    best, best_span = names, span
        return best

    def _is_pass_through(self, node: ast.expr, arg: ast.expr) -> bool:
        """实参是本函数形参 → 该访问只是转发（真正的名字在调用方）"""
        if not isinstance(arg, ast.Name) or arg.id in self.consts:
            return False
        return arg.id in self._enclosing_params(getattr(node, "lineno", 0))

    def classify_local_helpers(self, tree: ast.AST) -> None:
        """识别本文件定义的开关读取助手，判定「直通」还是「前缀家族」

        | 形态 | 例子 | 处理 |
        |---|---|---|
        | 直通 | `def _env_flag(name, default): return os.getenv(name, default)...` | 调用点即读取点 |
        | 前缀家族（直接） | `def _env_bool(name, d): v = os.environ.get(f"{_ENV_PREFIX}_{name}")` | 调用点传的是**后缀**；真实名 = 前缀 + 后缀 |
        | 前缀家族（**二级转发**） | `def _env_int(env, name, d): return _env_text(env, name)`，而 `_env_text` 里 `key = ENV_PREFIX + name` | 名字在**内层**构造，外层只是转发；须沿转发链继承前缀 |

        二级转发是 S7-02（`agent/repair/policy.py`）实测踩到的形态：助手签名是
        `(env, name, default)`（**名字不是第一个参数**），而前缀拼接发生在其调用的
        内层函数里。只认「第一个实参就是名字」会把这类读取点丢进 `<unresolved>`，
        于是**真实开关名（`CP_REPAIR_*`）在 UI 里看不见**——故本方法做两件事：
        ① 模板扫描不依赖 env 访问方式（dict 查表也算），并能识别**任一**形参；
        ② 沿「本文件助手之间的转发」传播前缀，并记录名字参数的下标。
        """
        self.local_helpers: Dict[str, Dict[str, Any]] = {}
        self._helper_defs: Dict[str, ast.AST] = {}
        self._helper_params: Dict[str, List[str]] = {}

        # ── 第一轮：直接形态（模板 / 直通）──
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            base = node.name
            params = [a.arg for a in node.args.args]
            # 候选：① 名字像读取助手；② **名字不像但确实在构造环境变量名**
            #    （如 `_env_text(env, name)`：`key = ENV_PREFIX + name`）——后者若不收，
            #    二级转发链就断在中间，`CP_REPAIR_*` 整族会变成 <unresolved>。
            tpl_pre = _template_prefix_in(node, params, self.consts)[0] if params else ""
            if not (base in KNOWN_READ_HELPERS
                    or _READ_HELPER_PATTERN.match(base) or tpl_pre):
                continue
            self._helper_defs[base] = node
            self._helper_params[base] = params
            if not params:
                self.local_helpers[base] = {"mode": "self_read", "prefix": "",
                                            "name_index": 0}
                continue
            first = params[0]
            mode, prefix, name_param = "unknown", "", first
            # ① 模板扫描（**不要求是 env 访问**：`key = ENV_PREFIX + name` 也算）
            tpl_prefix, tpl_param = _template_prefix_in(node, params, self.consts)
            if tpl_prefix:
                mode, prefix, name_param = "family", tpl_prefix, tpl_param
            # ② env 访问扫描（补充：直通 / f-string 家族）
            for sub in ast.walk(node):
                arg = _env_access_arg(sub)
                if arg is None:
                    continue
                if isinstance(arg, ast.Name) and arg.id == first:
                    if mode != "family":
                        mode, name_param = "pass_through", first
                elif _uses_name(arg, first):
                    mode, prefix, name_param = "family", (
                        _dynamic_prefix(arg, self.consts) or prefix), first
            self.local_helpers[base] = {
                "mode": mode, "prefix": prefix, "name_param": name_param,
                "name_index": params.index(name_param) if name_param in params else 0}

        # ── 第二轮：沿转发链传播前缀（外层助手 → 内层助手；不动点迭代）──
        for _ in range(4):
            changed = False
            for name, info in self.local_helpers.items():
                if info.get("mode") == "family":
                    continue
                node = self._helper_defs.get(name)
                params = self._helper_params.get(name) or []
                if node is None or not params:
                    continue
                for sub in ast.walk(node):
                    if not isinstance(sub, ast.Call):
                        continue
                    callee = self._call_name(sub.func).split(".")[-1]
                    inner = self.local_helpers.get(callee)
                    if inner is None or callee == name:
                        continue
                    inner_params = self._helper_params.get(callee) or []
                    inner_param = inner.get("name_param", "")
                    if not inner.get("prefix") or inner_param not in inner_params:
                        continue
                    idx = inner_params.index(inner_param)
                    if len(sub.args) <= idx:
                        continue
                    passed = sub.args[idx]
                    if not isinstance(passed, ast.Name) or passed.id not in params:
                        continue
                    info.update({"mode": "family", "prefix": inner["prefix"],
                                 "name_param": passed.id,
                                 "name_index": params.index(passed.id)})
                    changed = True
                    break
            if not changed:
                break

    def _in_environ_scope(self, lineno: int) -> bool:
        if not self._environ_params:
            return False
        return any(start <= lineno <= end for start, end in self._environ_ranges)

    # ── 访问 ──

    def visit_Call(self, node: ast.Call) -> None:
        fname = self._call_name(node.func)
        base = fname.split(".")[-1]
        is_helper = bool(base) and (
            base in KNOWN_READ_HELPERS or bool(_READ_HELPER_PATTERN.match(base)))
        is_os_env = fname in _OS_ENV_CALLS
        is_bare_env = fname in _BARE_ENV_CALLS

        if (is_helper or is_os_env or is_bare_env) and node.args:
            arg0 = node.args[0]
            info = self.local_helpers.get(base, {}) if is_helper else {}
            is_family = info.get("mode") == "family"
            # 家族助手的名字参数**未必是第一个实参**（如 `_env_int(env, name, d)`）
            name_arg = arg0
            if is_family:
                idx = int(info.get("name_index", 0) or 0)
                if len(node.args) > idx:
                    name_arg = node.args[idx]
            kind = ("helper" if is_helper
                    else "os_environ" if is_os_env else "bare_environ")
            if is_bare_env and self._in_environ_scope(getattr(node, "lineno", 0)):
                # WSGI environ，不是进程环境 → 不计入开关（见 PROCESS_ENV_DENYLIST 说明）
                kind = "wsgi_environ"
            if self._is_pass_through(node, name_arg):
                # 助手内部转发形参：真实名字在调用方，这里不记
                kind = "pass_through"
                self._record(node, name="", resolved=False, kind=kind,
                             helper=base if is_helper else "", value_type="")
            elif is_family:
                # 前缀家族助手：调用点传的是后缀，真实名 = 前缀 + 后缀
                prefix = info.get("prefix", "")
                suffix = self._fold(name_arg)
                if suffix is not None:
                    self._record(node, name=prefix + suffix, resolved=True,
                                 kind="helper_family", helper=base,
                                 value_type=self._type_hint(base, node))
                else:
                    self._record(node, name=_dynamic_prefix(name_arg, self.consts)
                                 or prefix, resolved=False,
                                 kind="helper_family", helper=base,
                                 value_type=self._type_hint(base, node))
            else:
                name, resolved = self._resolve_name(arg0)
                kind_eff = kind
                if (not resolved and isinstance(arg0, ast.Name)
                        and arg0.id in self._loop_from_name):
                    # 名字来自具名集合：交由 PASS_THROUGH_SITES 显式裁定
                    #
                    # 【2026-09-13 修**静默丢弃**（守卫自身的漏洞）】
                    # 原实现此处**只设置 `kind_eff`/`name`，没有调用 `_record`**；
                    # 而后续分支是 `elif / elif / else` ⇒ 本 `if` 一旦命中，
                    # `_record` 永远不会被调用，该读取点被**整体丢弃**：
                    # 既不在 `managed`、也不在 `dynamic`、也不在 `passthrough`。
                    # 后果：`unregistered_dynamic` 与 `undeclared_passthrough` 都看不见它
                    # ⇒ **零缺口硬守卫存在一个静默漏洞**（实测被吞掉的包括既有
                    # `agent/skills_mgmt/executor.py::_ENV_WHITELIST` 与 S8-03 的
                    # 隔离环境白名单：读取点消失，守卫却是绿的）。
                    # 修法：与相邻分支同款**显式记录**为 `loop_collection`：
                    #   · 已在 `PASS_THROUGH_SITES` 声明 → 进 `report.passthrough`（如实披露）；
                    #   · 未声明 → 变成 `loop:<来源>` 动态家族 → `unregistered_dynamic` → 红灯。
                    # ⚠️ 这是**堵漏（判定更严）**，不是放宽；配套回归用例见
                    # `tests/unit/test_settings_registry.py::TestMechanicalZeroGap::`
                    # `test_named_collection_reads_are_disclosed_not_dropped`。
                    self._record(node, name="", resolved=False,
                                 kind="loop_collection",
                                 helper=base if is_helper else "",
                                 value_type=self._type_hint(base, node))
                    self.generic_visit(node)
                    return
                elif (isinstance(arg0, ast.Name)
                      and self._loop_literals.get(arg0.id)
                      and len(self._loop_literals[arg0.id]) > 1):
                    # 循环变量绑定到多个字面量（如三个融合权重）→ **每个都记一条**，
                    # 不能只记一个"家族前缀"（那会把真实开关漏出注册表）
                    for one in self._loop_literals[arg0.id]:
                        self._record(node, name=one, resolved=True,
                                     kind="loop_literal", helper="",
                                     value_type=self._type_hint(base, node))
                    self.generic_visit(node)
                    return
                elif not resolved and _expr_source(self._source, arg0) in (
                        RUNTIME_NAME_READS):
                    # 「运行时名字」读取（如 spec.env_name）→ 显式声明口径，不入缺口
                    self._record(node, name="", resolved=False,
                                 kind="runtime_name",
                                 helper=base if is_helper else "",
                                 value_type=self._type_hint(base, node),
                                 runtime_expr=_expr_source(self._source, arg0))
                    self.generic_visit(node)
                    return
                else:
                    self._record(node, name=name, resolved=resolved,
                                 kind=kind_eff,
                                 helper=base if is_helper else "",
                                 value_type=self._type_hint(base, node))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # 只认 `os.environ[...]`：裸 `env[...]` / `environ[...]` 在本仓库里
        # 全是普通 dict（如沙箱 env 补丁），误收会把 `env["PYTHONPATH"]`
        # 误报成开关（S7-01 实测踩坑）。赋值左值（`os.environ[k] = v`）同理。
        if (self._call_name(node.value) == "os.environ"
                and id(node) not in self._target_subscripts):
            arg = node.slice
            if self._is_pass_through(node, arg):
                self._record(node, name="", resolved=False, kind="pass_through",
                             helper="", value_type="")
            else:
                name, resolved = self._resolve_name(arg)
                self._record(node, name=name, resolved=resolved, kind="subscript",
                             helper="", value_type="")
        self.generic_visit(node)

    def _type_hint(self, base: str, call: ast.Call) -> str:
        for token, hint in _HELPER_TYPE_HINTS:
            if token in (base or "").lower():
                return hint
        return ""

    def _default_literal(self, call: ast.Call) -> str:
        for kw in call.keywords:
            if kw.arg in ("default", "default_value") and _literal_str(kw.value):
                return _literal_str(kw.value) or ""
        if len(call.args) >= 2:
            lit = _literal_str(call.args[1])
            if lit is not None:
                return lit
        return ""

    def _record(self, node: ast.expr, *, name: str, resolved: bool, kind: str,
                helper: str, value_type: str, runtime_expr: str = "") -> None:
        lineno = int(getattr(node, "lineno", 0) or 0)
        text = (self._lines[lineno - 1].strip() if 0 < lineno <= len(self._lines)
                else "")
        rp = ReadPoint(kind=kind, helper=helper, value_type=value_type,
                       module=self.module, line=lineno, source_line=text,
                       runtime_expr=runtime_expr,
                       default_literal=(self._default_literal(node)
                                        if isinstance(node, ast.Call) else ""))
        if kind == "loop_collection":
            arg0 = node.args[0] if isinstance(node, ast.Call) and node.args else None
            coll = (self._loop_from_name.get(arg0.id, "")           # type: ignore[union-attr]
                    if isinstance(arg0, ast.Name) else "")
            rp.loop_source = f"{self.module}::{coll}" if coll else self.module
            self.reads.append(rp)
            return
        if resolved and name:
            rp.name = name
        elif kind == "wsgi_environ":
            rp.name = name or "<wsgi>"
        else:
            rp.dynamic_prefix = name or "<unresolved>"
        self.reads.append(rp)


def _template_prefix_in(node: ast.AST, params: Sequence[str],
                        consts: Dict[str, Optional[str]]
                        ) -> Tuple[str, str]:
    """在函数体里找「**环境变量形态的**静态前缀 + 恰好一个形参」的字符串模板

    形态：`f"{_ENV_PREFIX}_{name}"`、`ENV_PREFIX + name`、`"X_" + key` 等。
    **不要求该表达式被用于 env 访问**——S7-02 的 `_env_text` 是拿它当**字典键**
    （`env.items()` 里比对），若只认 `os.environ.get` 就会漏掉整族真实开关名。

    【安全边界】前缀必须是**环境变量形态**（`^[A-Z][A-Z0-9_]*_$`）。
    否则形如 `def _cache_key(name): return "cache:" + name` 的普通拼接助手会被误判成
    "开关读取家族"，进而把 `cache:FOO` 这种**并不存在**的开关名写进候选清单——
    而"误报的名字"比"漏报"更危险（它会带着"已被机械提取证实"的外观进注册表）。

    Returns:
        `(前缀, 形参名)`；找不到返回 `("", "")`。
    """
    best: Tuple[str, str] = ("", "")
    for sub in ast.walk(node):
        if not isinstance(sub, (ast.JoinedStr, ast.BinOp)):
            continue
        if isinstance(sub, ast.BinOp) and not isinstance(sub.op, ast.Add):
            continue
        used = [n.id for n in ast.walk(sub)
                if isinstance(n, ast.Name) and n.id in params]
        if len(set(used)) != 1:
            continue                     # 用了 0 个或多个形参 → 不是本形态
        prefix = _dynamic_prefix(sub, consts)
        if not _ENV_PREFIX_SHAPE.match(prefix):
            continue                     # 非环境变量形态 → 不算开关名家族
        if len(prefix) > len(best[0]):
            best = (prefix, used[0])
    return best


def _literal_str(node: Optional[ast.expr]) -> Optional[str]:
    """字符串字面量（含隐式拼接）；非字面量返回 None"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dynamic_prefix(node: ast.expr,
                    consts: Optional[Dict[str, Optional[str]]] = None) -> str:
    """从 f-string / 拼接表达式里取**静态前缀**（如 "SKILL_CLEANUP_"）

    例：`f"{_ENV_PREFIX}_{name}"` 且 `_ENV_PREFIX = "SKILL_CLEANUP"` → `SKILL_CLEANUP_`。
    取不到则返回 ""（调用方按 `<unresolved>` 处理并披露）。
    """
    consts = consts or {}

    def fold(expr: ast.expr) -> Optional[str]:
        lit = _literal_str(expr)
        if lit is not None:
            return lit
        if isinstance(expr, ast.Name):
            return consts.get(expr.id) or None
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            left = fold(expr.left)
            if left is None:
                return None
            right = fold(expr.right)
            if right is None:
                return left                      # 左侧静态、右侧运行时 → 前缀
            return left + right
        return None

    if isinstance(node, ast.JoinedStr):
        parts: List[str] = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                piece = fold(part.value)
                if piece is None:
                    return "".join(parts)         # 运行时插值处截断 → 前缀
                parts.append(piece)
            else:
                piece = _literal_str(part)
                if piece is None:
                    return "".join(parts)
                parts.append(piece)
        return "".join(parts)
    folded = fold(node)
    return folded or ""


def _env_access_arg(node: ast.AST) -> Optional[ast.expr]:
    """取「环境访问节点」的第一个参数（非环境访问返回 None）"""
    if isinstance(node, ast.Call):
        parts: List[str] = []
        cur: Optional[ast.expr] = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        fname = ".".join(reversed(parts))
        base = fname.split(".")[-1]
        if fname in _OS_ENV_CALLS or fname in _BARE_ENV_CALLS:
            return node.args[0] if node.args else None
        if base in KNOWN_READ_HELPERS or _READ_HELPER_PATTERN.match(base):
            return node.args[0] if node.args else None
        return None
    if isinstance(node, ast.Subscript):
        if _call_name_of(node.value) == "os.environ":
            return node.slice
        return None
    return None


def _call_name_of(node: ast.expr) -> str:
    parts: List[str] = []
    cur: Optional[ast.expr] = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _uses_name(node: ast.AST, name: str) -> bool:
    """表达式里是否用到某个名字（用于判定「前缀家族」形态）"""
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def _expr_source(source: str, node: ast.expr) -> str:
    """取表达式的源码文本（用于「运行时名字」这类必须显式声明的形态）"""
    try:
        return (ast.get_source_segment(source, node) or "").strip()
    except Exception:                                    # pragma: no cover
        return ""


def _literal_collection(node: ast.expr,
                        consts: Dict[str, Optional[str]]) -> List[List[str]]:
    """把字面量集合解析成「若干字符串元组」；非字面量返回空

    支持 `["A", "B"]`、`("A", "B")`、`[("k", "X_ENV"), ...]`（元素为常量亦可）。
    """
    def one(expr: ast.expr) -> Optional[str]:
        lit = _literal_str(expr)
        if lit is not None:
            return lit
        if isinstance(expr, ast.Name):
            got = consts.get(expr.id)
            return got or None
        return None

    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return []
    rows: List[List[str]] = []
    for elt in node.elts:
        if isinstance(elt, (ast.Tuple, ast.List)):
            row = [one(e) for e in elt.elts]
            if any(v is None for v in row):
                return []
            rows.append([v for v in row if v is not None])
        else:
            val = one(elt)
            if val is None:
                return []
            rows.append([val])
    return rows


def _common_prefix(values: Sequence[str]) -> str:
    """取若干名字的最长公共前缀（用于表示动态家族）"""
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
    return prefix


def _build_global_consts(repo_root: Path,
                         roots: Iterable[Path]) -> Dict[str, Optional[str]]:
    """全仓常量索引（名 → 唯一字面量）

    用途：`ui_panels/data.py` 从 `digestion/internalize.py` **import** 常量后使用
    （`os.environ.get(PROMOTE_DIR_ENV)`），单文件常量表解析不出真实开关名。
    纪律：**同名多值即视为不明确**（置 None），宁可少解析也不误报。
    """
    index: Dict[str, Optional[str]] = {}
    skip_parts = {"__pycache__", ".worktrees", ".venv", "venv", "node_modules"}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            try:
                rel_to_root = path.relative_to(root)
            except ValueError:
                rel_to_root = path
            if any(p in skip_parts for p in rel_to_root.parts[:-1]):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            local: Dict[str, Optional[str]] = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                literal = _literal_str(node.value)
                if literal is None or not _DYNAMIC_NAME_PATTERN.match(literal):
                    continue
                for tgt in node.targets:
                    if not isinstance(tgt, ast.Name):
                        continue
                    if tgt.id in local and local[tgt.id] != literal:
                        local[tgt.id] = None
                    else:
                        local.setdefault(tgt.id, literal)
            for name, value in local.items():
                if name in index and index[name] != value:
                    index[name] = None
                else:
                    index.setdefault(name, value)
    return index


def scan_file(path: Path, rel: str,
              global_consts: Optional[Dict[str, Optional[str]]] = None
              ) -> Tuple[List[ReadPoint], Optional[Dict[str, str]]]:
    """扫描单个 Python 文件，返回 (读取点, 解析错误)

    顺序即正确性（缺一步就会误报/漏报）：
        1. 赋值左值标记 → 2. 常量折叠表 → 3. 函数作用域（直通助手判定）
        → 4. 本文件助手分类 → 5. 循环集合映射 → 6. 访问点收集
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as e:
        return [], {"module": rel, "error": f"SyntaxError: {e}"}
    extractor = _Extractor(rel, text.splitlines(), global_consts, text)
    extractor._assign_targets(tree)
    extractor.collect_constants(tree)
    extractor._enclosing_environ_params(tree)
    extractor._collect_function_scopes(tree)
    extractor.classify_local_helpers(tree)
    extractor.collect_loop_literals(tree)
    extractor.visit(tree)
    return extractor.reads, None


def scan_paths(roots: Iterable[Path], repo_root: Path = REPO_ROOT) -> ScanReport:
    """扫描若干根目录下的全部 .py（跳过 __pycache__ / .worktrees 等）

    两趟：先建全仓常量索引，再逐文件提取（跨模块 import 的常量名要能解析）。
    """
    report = ScanReport()
    root_list = list(roots)
    global_consts = _build_global_consts(repo_root, root_list)
    skip_parts = {"__pycache__", ".worktrees", ".venv", "venv", "node_modules",
                  ".git", "site-packages", "migrations"}
    for root in root_list:
        for path in sorted(root.rglob("*.py")):
            # 只按「相对扫描根」的目录名过滤：本仓库的 worktree 位于
            # `<repo>/.worktrees/<id>/`，若按绝对路径过滤会把整棵 worktree 跳过
            # （S7-01 实测踩坑）。故用 rel_to_root 判断。
            try:
                rel_to_root = path.relative_to(root)
            except ValueError:
                rel_to_root = path
            if any(part in skip_parts for part in rel_to_root.parts[:-1]):
                continue
            try:
                rel = str(path.relative_to(repo_root)).replace("\\", "/")
            except ValueError:
                rel = str(path).replace("\\", "/")
            report.files_scanned += 1
            reads, err = scan_file(path, rel, global_consts)
            if err:
                report.parse_errors.append(err)
            for rp in reads:
                if rp.kind in ("wsgi_environ", "pass_through"):
                    continue          # WSGI 请求上下文 / 助手形参转发：都不是开关读取点
                if rp.kind == "loop_collection":
                    if rp.loop_source in PASS_THROUGH_SITES:
                        report.passthrough.append(rp)
                    else:
                        rp.dynamic_prefix = f"loop:{rp.loop_source}"
                        report.dynamic.append(rp)
                    continue
                if rp.kind == "runtime_name":
                    if rp.runtime_expr in RUNTIME_NAME_READS:
                        report.runtime_reads.append(rp)
                    else:
                        rp.dynamic_prefix = f"runtime:{rp.runtime_expr}"
                        report.dynamic.append(rp)
                    continue
                if rp.name in PROCESS_ENV_DENYLIST:
                    report.process_env.append(rp)
                elif rp.name:
                    report.managed.append(rp)
                else:
                    report.dynamic.append(rp)
    return report


# ════════════════════════════════════════════════════════════
#  缺口检查（与注册表比对）
# ════════════════════════════════════════════════════════════

@dataclass
class GapReport:
    """注册表覆盖度报告"""

    extracted_names: List[str] = field(default_factory=list)
    registered_names: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)          # 提取到但未注册
    extra: List[str] = field(default_factory=list)            # 注册但代码未读到
    dynamic_families: List[str] = field(default_factory=list)
    unregistered_dynamic: List[str] = field(default_factory=list)
    process_env_names: List[str] = field(default_factory=list)
    undeclared_process_env: List[str] = field(default_factory=list)
    passthrough_sites: List[str] = field(default_factory=list)
    undeclared_passthrough: List[str] = field(default_factory=list)
    runtime_name_reads: List[str] = field(default_factory=list)
    undeclared_runtime_reads: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.unregistered_dynamic
                    or self.undeclared_process_env
                    or self.undeclared_passthrough
                    or self.undeclared_runtime_reads)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {
                "extracted_names": len(self.extracted_names),
                "registered_names": len(self.registered_names),
                "missing": len(self.missing),
                "extra": len(self.extra),
                "dynamic_families": len(self.dynamic_families),
                "unregistered_dynamic": len(self.unregistered_dynamic),
                "process_env_names": len(self.process_env_names),
                "passthrough_sites": len(self.passthrough_sites),
            },
            "missing": self.missing,
            "extra": self.extra,
            "dynamic_families": self.dynamic_families,
            "unregistered_dynamic": self.unregistered_dynamic,
            "process_env_names": self.process_env_names,
            "undeclared_process_env": self.undeclared_process_env,
            "passthrough_sites": self.passthrough_sites,
            "undeclared_passthrough": self.undeclared_passthrough,
            "runtime_name_reads": self.runtime_name_reads,
            "undeclared_runtime_reads": self.undeclared_runtime_reads,
        }


def check_gaps(report: ScanReport) -> GapReport:
    """把机械提取结果与 `agent/settings/registry.py` 比对（零缺口）"""
    # 以脚本方式运行时 sys.path[0] 是 scripts/，需显式加入仓库根才能 import agent.*
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from agent.settings.registry import dynamic_prefixes, registered_env_names

    gap = GapReport()
    extracted = report.managed_names()
    gap.extracted_names = sorted(extracted)
    gap.registered_names = sorted(registered_env_names())
    registered = set(gap.registered_names)
    gap.missing = [n for n in gap.extracted_names if n not in registered]
    gap.extra = [n for n in gap.registered_names if n not in extracted]
    gap.dynamic_families = sorted(report.dynamic_prefixes())
    declared_dynamic = set(dynamic_prefixes())
    gap.unregistered_dynamic = [p for p in gap.dynamic_families
                                if p not in declared_dynamic]
    gap.process_env_names = sorted(report.process_env_names())
    gap.undeclared_process_env = [n for n in gap.process_env_names
                                  if n not in PROCESS_ENV_DENYLIST]
    gap.passthrough_sites = sorted({rp.loop_source for rp in report.passthrough})
    gap.undeclared_passthrough = [s for s in gap.passthrough_sites
                                  if s not in PASS_THROUGH_SITES]
    gap.runtime_name_reads = sorted({rp.runtime_expr
                                     for rp in report.runtime_reads})
    gap.undeclared_runtime_reads = [e for e in gap.runtime_name_reads
                                    if e not in RUNTIME_NAME_READS]
    return gap


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════

def _print_summary(report: ScanReport, gap: Optional[GapReport]) -> None:
    payload = report.to_dict()
    counts = payload["counts"]
    print("=" * 66)
    print("开关机械提取摘要（scripts/scan_settings.py）")
    print("=" * 66)
    print(f"扫描文件        : {payload['files_scanned']}")
    print(f"managed 开关名  : {counts['managed_names']} "
          f"（读取点 {counts['managed_read_points']}）")
    print(f"dynamic 家族    : {counts['dynamic_families']} "
          f"（读取点 {counts['dynamic_read_points']}）")
    print(f"process_env 名  : {counts['process_env_names']}（显式排除，非开关）")
    print(f"pass-through 点 : {counts['passthrough_sites']}（显式声明，非开关）")
    print(f"运行时名字读取  : {counts['runtime_name_exprs']}（显式声明形态）")
    if counts["parse_errors"]:
        print(f"解析失败文件    : {counts['parse_errors']}")
    if gap is None:
        return
    print("-" * 66)
    print(f"注册表开关数    : {len(gap.registered_names)}")
    print(f"缺口（未注册）  : {len(gap.missing)}")
    print(f"注册但未读到    : {len(gap.extra)}")
    print(f"未声明的动态家族: {len(gap.unregistered_dynamic)}")
    print(f"未声明的排除项  : {len(gap.undeclared_process_env)}")
    if gap.missing:
        print("\n[缺口] 代码读到但注册表未覆盖的开关：")
        for name in gap.missing:
            rp = report.managed_names()[name][0]
            print(f"  - {name}  ({rp.module}:{rp.line})")
    if gap.unregistered_dynamic:
        print("\n[缺口] 未声明的动态开关家族：")
        for prefix in gap.unregistered_dynamic:
            rp = report.dynamic_prefixes()[prefix][0]
            print(f"  - {prefix}*  ({rp.module}:{rp.line})")
    if gap.undeclared_process_env:
        print("\n[缺口] 未声明的排除项（须改 PROCESS_ENV_DENYLIST 并给出理由）：")
        for name in gap.undeclared_process_env:
            print(f"  - {name}")
    if gap.undeclared_passthrough:
        print("\n[缺口] 未声明的透传点（须改 PASS_THROUGH_SITES 并给出理由）：")
        for site in gap.undeclared_passthrough:
            print(f"  - {site}")
    if gap.undeclared_runtime_reads:
        print("\n[缺口] 未声明的运行时名字读取（须改 RUNTIME_NAME_READS 并给出理由）：")
        for expr in gap.undeclared_runtime_reads:
            print(f"  - {expr}")
    print("-" * 66)
    print("结论：" + ("零缺口 ✅" if gap.ok else "存在缺口 ❌"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="开关读取点机械提取与注册表缺口检查（TASK-S7-01）")
    parser.add_argument("--path", action="append", default=None,
                        help="扫描根（可重复；默认 agent）")
    parser.add_argument("--json", default="", help="把完整报告写入该 JSON 文件")
    parser.add_argument("--check", action="store_true",
                        help="执行注册表缺口检查（有缺口 → 退出码 1）")
    parser.add_argument("--quiet", action="store_true", help="只输出结论行")
    args = parser.parse_args(argv)

    roots = [REPO_ROOT / p for p in (args.path or DEFAULT_ROOTS)]
    report = scan_paths(roots)

    gap: Optional[GapReport] = None
    if args.check:
        gap = check_gaps(report)
        payload = {"scan": report.to_dict(), "gap": gap.to_dict()}
    else:
        payload = {"scan": report.to_dict()}

    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.quiet:
        counts = report.to_dict()["counts"]
        print(f"managed={counts['managed_names']} "
              f"dynamic={counts['dynamic_families']} "
              f"process_env={counts['process_env_names']} "
              f"gap={'OK' if (gap is None or gap.ok) else 'FAIL'}")
    else:
        _print_summary(report, gap)

    if gap is not None and not gap.ok:
        return 1
    return 0


if __name__ == "__main__":                      # pragma: no cover
    sys.exit(main())
