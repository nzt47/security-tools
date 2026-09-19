"""防复发守卫：日期平移法的**盲区**（TASK-S11-08 §三：四类 + 本轮新查出的第 5 类）

════════════════════════════════════════════════════════════════════════
〇、这份守卫解决什么
════════════════════════════════════════════════════════════════════════
`tests/_date_shift_plugin.py`（"把今天整体平移 N 天"）是查"日期定时炸弹"的主力工具，
但它**原理上有四类查不到的东西**。S11-07 §六 只披露了这些盲区，**没有固化检查手段**；
本文件把四类盲区各自变成**可复跑、带正反自检**的检查，避免"披露过 = 处理过"。

| # | 盲区 | 为什么平移法查不出 | 本文件的手段 |
|---|---|---|---|
| 1 | 文件系统时钟不平移 | `os.stat().st_mtime` / `os.utime()` / `fromtimestamp()` 走**真实 OS 时钟**，平移只动 Python 的"今天" | ① :func:`test_fs_clock_is_not_mixed_with_python_today`（同函数内混用即报警）；② **第二形状** :func:`test_mtime_is_not_derived_from_the_unshifted_posix_clock`（`time.time()` 派生的值写进 mtime —— 修复前的慢档写法，形状里没有时钟调用，①抓不到）；③ 人工分诊表 |
| 2 | 导入期取时钟 vs 运行期取时钟 | 平移对"导入期"和"运行期"是**同步**挪的，永远不会制造两者分歧 | ①静态：:func:`test_no_import_time_clock_constants`；②动态：**晚替换探针** `CP_DATE_SHIFT_LATE=1`（端到端自检见 :func:`test_late_probe_detects_import_time_constant`） |
| 3 | 只用 `time.time()` 推"今天是哪天" | 平移不替换 `time` 模块 | :func:`test_time_time_is_not_used_to_derive_the_day`（判据见 :data:`TIME_DERIVED_DAY_ATTRS`） |
| 4 | 闭包 / 默认参数里已捕获的时钟值 | 默认参数在 **def 求值期**求值；闭包捕获的是**值** | :func:`test_no_clock_captured_in_default_arguments`、:func:`test_no_clock_captured_in_escaping_closures` |

**第 5 类（TASK-S11-08 本轮新查出，S11-07 §六 未列）——跨进程**：
测试用**被平移的**时钟造夹具，而被测代码跑在 `subprocess` 里（如
`python -m agent.knowledge audit`）：子进程是**新解释器**，插件只替换**当前进程**的
`datetime` ⇒ 子进程读**真实**时钟 ⇒ 父/子口径必然差 delta 天。
覆盖手段：:func:`test_cross_process_children_read_the_unshifted_clock`（静态候选人）
+ 四臂实跑分类（CONTROL 必须为绿，否则才是替换伪影）。
已复现实例：`tests/integration/test_knowledge_audit_ci_edge.py`（−400 实测
`exact90` 卡被判 490 天过期：`days_unaccessed=490`，而它按父进程口径"恰好 90 天"）。

════════════════════════════════════════════════════════════════════════
一、口径（宁窄勿宽 —— 宽了必然误报，误报的守卫活不下来）
════════════════════════════════════════════════════════════════════════
**刻意不报警**的写法与理由（宽口径会把噪声淹没真信号）：

* 局部（函数内）`now = datetime.now()` —— 调用期取值，与产品侧同源，**不是**导入期常量；
* 函数内的默认参数 `def g(t=date.today())` —— 该 def 在**测试运行时**求值，捕获与使用
  同一瞬间，无跨零点分叉（只有**模块级/类体**的 `def` 才是导入期求值 ⇒ 报警）；
* 闭包在同一调用瞬间被使用（内层函数在外层函数体内就被调用）—— 捕获与使用同一瞬间；
  只有**逃逸**（外层 `return` 该内层函数、或把它当回调传给别的调用）才报警；
* `time.time()` 用于**测耗时/超时/存样本时间戳**（`elapsed = time.time() - t0`、
  `deadline = time.time() + t`、`timestamp=time.time()`）—— 与"今天是哪天"无关，
  两侧都没有"日期"语义，不存在口径分叉；只有把它**换算成日历日/日期串**才报警；
* `os.utime()` / `st_mtime` 用于"把 mtime 拨到某个**显式给定**的旧日、或断言 mtime
  **未变**"—— 只要不与 Python 的"今天/since"比较，就不报警。

════════════════════════════════════════════════════════════════════════
二、守卫也会烂：每条规则都配**正反自检**
════════════════════════════════════════════════════════════════════════
* 正向：每个检测器必须命中它的"样本缺陷"（否则守卫是安静的空转）；
* 反向：惰性/无关写法必须**不**命中（否则守卫会被绕过或关掉）；
* 覆盖面下界：扫描面必须真的读到含时钟调用的文件（防止筛选条件写错导致 0 命中，
  这是 ``test_date_bomb_guard.py`` 实现期真实踩过的坑）。

盲区 #2 的**动态臂（晚替换探针）判定纪律**（TASK-S11-08 实测补充）：
晚模式在**收集之后**才替换时钟，此时 `yaml` 等第三方库早已按**真实类**注册好 representer，
随后安装假类 ⇒ 假类实例喂 `yaml.safe_dump` 会 `RepresenterError`
⇒ **任何"晚模式失败"都必须先跑它自己的对照臂**
`CP_DATE_SHIFT_LATE=1 CP_DATE_SHIFT_CONTROL=1`（照样替换、delta=0）。
实测：`test_knowledge_card.py` + `test_knowledge_cli.py` 晚模式 `6 failed`、
**晚模式 delta=0 同样 `6 failed`**、**早模式 delta=0 `108 passed`** ⇒ 6 个全是晚模式伪影。

════════════════════════════════════════════════════════════════════════
三、可复跑入口（不需要 pytest）
════════════════════════════════════════════════════════════════════════
::

    python tests/unit/test_date_shift_blindspots_guard.py --scan     # 四类盲区全量报告

盲区 #2 的**动态**臂（晚替换探针，S11-07 §4.4 的手法固化）::

    CP_DATE_SHIFT_DAYS=1 CP_DATE_SHIFT_LATE=1 python -m pytest <目标> \\
        -p no:randomly -p tests._date_shift_plugin
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"
_THIS_FILE = Path(__file__).resolve()

#: 命中记录：(类别, 位置, 说明)
Hit = Tuple[str, str, str]


# ════════════════════════════════════════════════════════════════════
#  时钟调用的识别（唯一的"什么算读时钟"定义，全文件复用）
# ════════════════════════════════════════════════════════════════════

#: `X.Y` 形态的时钟调用（取点号链的**末两段**判定，以容纳
#: `datetime.date.today()` / `dt.datetime.now()` / 别名导入等写法）
_CLOCK_DOTTED = {
    ("date", "today"),
    ("datetime", "today"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
}

#: 裸名调用（`from datetime import date` 后写 `today()` 极少见，但 `now()` 常见）
_BARE_CLOCK = {"today", "utcnow"}

#: 文件系统时钟相关的属性名（盲区 #1）
_FS_CLOCK_ATTRS = {"st_mtime", "st_mtime_ns", "st_ctime", "utime"}

#: "把 epoch 换算成日历日/日期串"的属性名（盲区 #3 的判据）
TIME_DERIVED_DAY_ATTRS = {"fromtimestamp", "localtime", "gmtime", "strftime"}

#: 启动子进程的调用（盲区 #5「跨进程」的判据）
_SPAWN_ATTRS = {"run", "Popen", "call", "check_call", "check_output",
                "system", "spawnv", "spawnl"}

#: 与"今天"比较的常见名字（盲区 #1 的另一侧）
_TODAY_SIDE_NAMES = {"today", "now", "utcnow", "since", "since_days", "cutoff",
                     "_clock", "old_days", "keep_days", "retention_days"}

_ISO_ANY = re.compile(r"20\d\d-\d\d-\d\d")


def _dotted(node: ast.AST) -> str:
    """把 `a.b.c` 形态还原成字符串（取不到属性链时返回空串）"""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def clock_kind(call: ast.Call) -> Optional[str]:
    """若该 `Call` 是"读真实时钟"，返回其判定形态；否则 `None`"""
    f = call.func
    if isinstance(f, ast.Attribute):
        dotted = _dotted(f).split(".")
        if len(dotted) >= 2 and tuple(dotted[-2:]) in _CLOCK_DOTTED:
            return ".".join(dotted[-2:])
        return None
    if isinstance(f, ast.Name):
        if f.id in _BARE_CLOCK:
            return f"{f.id}()"
        if f.id == "today":
            return "today()"
    return None


def clock_calls_in(node: Optional[ast.AST]) -> List[ast.Call]:
    """子树里出现的时钟调用（模块级"值"与默认参数共用这一条判据）"""
    if node is None:
        return []
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call) and clock_kind(n) is not None]


# ════════════════════════════════════════════════════════════════════
#  检测器 ①：导入期取时钟（盲区 #2，静态臂）
# ════════════════════════════════════════════════════════════════════

#: 模块级/类体里可能**仍属同一作用域**的复合语句
_SCOPE_BLOCKS = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.While)


def _iter_scope_assignments(body: Sequence[ast.stmt], scope: str,
                            out: List[Tuple[str, ast.stmt]]) -> None:
    """收集 `scope` 作用域内的赋值语句（**不下潜进函数体**：那是运行期）

    函数体里 `now = datetime.now()` 是**调用期**取值，与产品侧同源，不是盲区；
    只有模块级 / 类体的赋值才是"导入期取时钟"。
    """
    for st in body:
        if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            out.append((scope, st))
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue                                  # 运行期，刻意不下潜
        elif isinstance(st, ast.ClassDef):
            out.append((f"{scope}.{st.name}", st))     # 类体本身（装饰器等）
            _iter_scope_assignments(st.body, f"{scope}.{st.name}", out)
        elif isinstance(st, _SCOPE_BLOCKS):
            for name in ("body", "orelse", "finalbody"):
                _iter_scope_assignments(getattr(st, name, None) or [], scope, out)
            for handler in getattr(st, "handlers", None) or []:
                _iter_scope_assignments(handler.body, scope, out)


def _assigned_names(stmt: ast.stmt) -> List[str]:
    targets: List[ast.AST] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
        targets = [stmt.target]
    names: List[str] = []
    for t in targets:
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, ast.Attribute):
            names.append(_dotted(t))
    return names


def detect_import_time_clock(src: str) -> List[Hit]:
    """模块级/类体里由时钟调用**直接**推导出的常量（盲区 #2 静态臂）

    只覆盖**直接**形态（值里就有时钟调用）。**间接**形态
    （`X = week_key()`、`X = _days_ago(3)` —— 时钟藏在被调函数里）静态查不出，
    由盲目 #2 的**动态臂**（晚替换探针）覆盖 —— 两者互补，缺一不可。
    """
    return _import_time_clock(ast.parse(src))


def _import_time_clock(tree: ast.Module) -> List[Hit]:
    """`detect_import_time_clock` 的 tree 版（`scan_all` 每文件只 parse 一次）"""
    assignments: List[Tuple[str, ast.stmt]] = []
    _iter_scope_assignments(tree.body, "<module>", assignments)
    hits: List[Hit] = []
    for scope, st in assignments:
        calls = clock_calls_in(getattr(st, "value", None))
        if not calls:
            continue
        for name in _assigned_names(st):
            kk = sorted({k for k in (clock_kind(c) for c in calls) if k})
            hits.append(("import_time_clock", f"{scope}:{name}@{st.lineno}",
                         f"第{st.lineno}行 由 {'/'.join(kk)} 在**导入期**取值"))
    return hits


# ════════════════════════════════════════════════════════════════════
#  检测器 ②：默认参数里捕获时钟（盲区 #4-a）
# ════════════════════════════════════════════════════════════════════


def _defs_with_scope(tree: ast.AST) -> List[Tuple[str, ast.AST]]:
    """产出 (作用域, def/lambda 节点)；作用域为 `<module>` / 类名 / 外层函数名"""
    out: List[Tuple[str, ast.AST]] = []

    def _walk(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((scope, child))
                _walk(child, f"{scope}.{child.name}")
            elif isinstance(child, ast.ClassDef):
                _walk(child, f"{scope}.{child.name}")
            elif isinstance(child, ast.Lambda):
                out.append((scope, child))
                _walk(child, scope)
            else:
                _walk(child, scope)

    _walk(tree, "<module>")
    return out


def detect_default_arg_clock(src: str) -> List[Hit]:
    """默认参数里的时钟调用（`def f(t=date.today())` —— **def 求值期**求值）

    作用域决定危害：`<module>` / 类体里的 def 是**导入期**求值（与盲区 #2 同类，
    平移法两侧同步挪 ⇒ 查不出）；函数内的 def 在运行期求值（同瞬间使用，不报警）。
    """
    return _default_arg_clock(ast.parse(src))


def _default_arg_clock(tree: ast.Module) -> List[Hit]:
    """`detect_default_arg_clock` 的 tree 版"""
    hits: List[Hit] = []
    for scope, node in _defs_with_scope(tree):
        args = getattr(node, "args", None)
        if args is None:
            continue
        defaults = list(args.defaults) + [d for d in args.kw_defaults if d is not None]
        for d in defaults:
            calls = clock_calls_in(d)
            if not calls:
                continue
            name = getattr(node, "name", "<lambda>")
            lineno = getattr(node, "lineno", 0)
            hits.append(("default_arg_clock", f"{scope}:{name}@{lineno}",
                         f"第{lineno}行 默认参数在 def 求值期取时钟"
                         f"（作用域={scope}）"))
    return hits


# ════════════════════════════════════════════════════════════════════
#  检测器 ③：逃逸闭包捕获时钟值（盲区 #4-b）
# ════════════════════════════════════════════════════════════════════


def _inner_names(fn: ast.AST) -> Dict[str, ast.AST]:
    """直接嵌套在 `fn` 里的函数/lambda：名字 → 节点"""
    out: Dict[str, ast.AST] = {}
    for child in ast.iter_child_nodes(fn):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[child.name] = child
        elif isinstance(child, ast.Assign) and isinstance(child.value, ast.Lambda):
            for t in child.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = child.value
    return out


def _escapes(fn: ast.AST, inner_name: str) -> bool:
    """内层函数是否**逃逸**（被 return 出去 / 当参数传给别的调用）

    逃逸才危险：捕获时刻与使用时刻分离 ⇒ 越长命越可能跨过零点或被平移工具漏掉。
    内层函数在外层函数体里**当场调用**的写法（`def g(): ...; g()`）捕获与使用
    同一瞬间，刻意不报警（见模块 docstring §一）。
    """
    for n in ast.walk(fn):
        if n is fn:
            continue
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Name) \
                and n.value.id == inner_name:
            return True
        if isinstance(n, ast.Call):
            for a in list(n.args) + [k.value for k in n.keywords]:
                if isinstance(a, ast.Name) and a.id == inner_name:
                    return True
    return False


def detect_escaping_closure_clock(src: str) -> List[Hit]:
    """外层函数把"时钟调用结果"绑到局部名，**逃逸**的内层函数又引用它

    这是"闭包引用已捕获的真实类/值"的可判定形态：捕获发生在 `def` 之前的赋值，
    使用发生在别人调用内层函数时。
    """
    return _escaping_closure_clock(ast.parse(src))


def _escaping_closure_clock(tree: ast.AST) -> List[Hit]:
    """`detect_escaping_closure_clock` 的 tree 版"""
    fn_nodes = [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    hits: List[Hit] = []
    for fn in fn_nodes:
        bound: Dict[str, int] = {}
        for st in fn.body:
            if isinstance(st, (ast.Assign, ast.AnnAssign)) and \
                    clock_calls_in(getattr(st, "value", None)):
                for name in _assigned_names(st):
                    bound[name] = st.lineno
        if not bound:
            continue
        for inner_name, inner in _inner_names(fn).items():
            used = {n.id for n in ast.walk(inner) if isinstance(n, ast.Name)}
            shared = sorted(used & set(bound))
            if not shared:
                continue
            if not _escapes(fn, inner_name):
                continue
            hits.append((
                "escaping_closure_clock",
                f"{fn.name}:{inner_name}@{fn.lineno}",
                f"第{bound[shared[0]]}行捕获时钟值 {shared}，"
                f"逃逸的内层函数 {inner_name} 引用它（捕获/使用时刻分离）"))
    return hits


# ════════════════════════════════════════════════════════════════════
#  检测器 ④：文件系统时钟与 Python 的"今天"混用（盲区 #1）
# ════════════════════════════════════════════════════════════════════


def detect_fs_clock_vs_today(src: str) -> List[Hit]:
    """**同一函数**内既碰文件系统时钟、又碰 Python 的"今天/since"

    判据（窄）：函数体里同时出现
      · 文件系统侧：`st_mtime` / `st_mtime_ns` / `st_ctime` / `utime` / `fromtimestamp`；
      · Python 侧：时钟调用，或 `today/since/cutoff/old_days/...` 这类名字。
    只碰一侧的不报警（把 mtime 拨到**显式给定**的旧日、或断言 mtime 未变，都与
    "今天是哪天"无关）。
    """
    return _fs_clock_vs_today(ast.parse(src))


def _fs_clock_vs_today(tree: ast.AST) -> List[Hit]:
    """`detect_fs_clock_vs_today` 的 tree 版"""
    hits: List[Hit] = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        fs = sorted(attrs & _FS_CLOCK_ATTRS)
        if not fs:
            continue
        calls = clock_calls_in(fn)
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        today_side = sorted(names & _TODAY_SIDE_NAMES)
        if not calls and not today_side:
            continue
        hits.append((
            "fs_clock_vs_today", f"{fn.name}@{fn.lineno}",
            f"文件系统时钟={fs}；Python 今天侧="
            f"{[clock_kind(c) for c in calls if clock_kind(c)] or today_side}"))
    return hits


# ════════════════════════════════════════════════════════════════════
#  检测器 ⑤：用 time.time() 推"今天是哪天"（盲区 #3）
# ════════════════════════════════════════════════════════════════════


def _time_time_names(fn: ast.AST) -> Dict[str, int]:
    """函数内由 `time.time()`（或其算术派生）绑定的局部名 → 行号"""
    out: Dict[str, int] = {}
    for st in ast.walk(fn):
        if not isinstance(st, (ast.Assign, ast.AnnAssign)):
            continue
        value = getattr(st, "value", None)
        if value is None:
            continue
        if not any(isinstance(n, ast.Attribute) and n.attr == "time"
                   and _dotted(n) == "time.time" for n in ast.walk(value)):
            continue
        for name in _assigned_names(st):
            out[name] = st.lineno
    return out


def detect_time_time_derived_day(src: str) -> List[Hit]:
    """把 `time.time()` 换算成**日历日/日期串**的写法（盲区 #3）

    只报两种可判定形态：
      · `X.fromtimestamp(<time.time() 或它的派生名>)` / `localtime` / `gmtime`；
      · `strftime(...)` 的实参里含 `time.time()` 或它的派生名。
    **刻意不报** `elapsed = time.time() - t0` / `deadline = time.time() + t` /
    `timestamp=time.time()`（测耗时、超时、样本时间戳，与"今天是哪天"无关）。
    """
    return _time_time_derived_day(ast.parse(src))


def _time_time_derived_day(tree: ast.AST) -> List[Hit]:
    """`detect_time_time_derived_day` 的 tree 版"""
    hits: List[Hit] = []
    fns = [n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in fns:
        derived = _time_time_names(fn)
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            func = call.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            if attr not in TIME_DERIVED_DAY_ATTRS:
                continue
            args = list(call.args) + [k.value for k in call.keywords]
            suspicious = False
            for a in args:
                if any(isinstance(n, ast.Attribute) and _dotted(n) == "time.time"
                       for n in ast.walk(a)):
                    suspicious = True
                elif isinstance(a, ast.Name) and a.id in derived:
                    suspicious = True
            if suspicious:
                hits.append((
                    "time_time_derived_day", f"{fn.name}@{call.lineno}",
                    f"第{call.lineno}行 {attr}() 的实参来自 time.time()"
                    f"（该值**不被平移**）"))
    return hits


# ════════════════════════════════════════════════════════════════════
#  检测器 ⑥：跨进程（子进程读真实时钟，不被平移）—— 盲区 #5
# ════════════════════════════════════════════════════════════════════


def _spawn_calls(tree: ast.Module) -> List[ast.Call]:
    """文件内启动子进程的调用（`subprocess.*` / `os.system` / `os.spawn*`）"""
    out: List[ast.Call] = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        dotted = _dotted(n.func)
        if dotted.startswith("subprocess.") and dotted.split(".")[-1] in _SPAWN_ATTRS:
            out.append(n)
        elif dotted == "os.system" or dotted.startswith("os.spawn"):
            out.append(n)
    return out


def detect_cross_process_clock(src: str) -> List[Hit]:
    """文件级：**既起子进程、又读时钟** ⇒ 父/子时钟口径可能分叉（盲区 #5）

    判据是**文件级**而非函数级：`_audit_cli()` 这类 helper 起子进程，夹具却在
    另一个测试函数里由 `date.today()` 构造 —— 函数级判据会漏掉这种真实形态
    （`tests/integration/test_knowledge_audit_ci_edge.py` 正是如此）。

    命中**不等于**缺陷：子进程若只做与"今天是哪天"无关的事（或断言不落在
    "夹具日 vs 子进程今日"的边界上）则无实例。命中只表示"必须四臂实跑分类"。
    """
    return _cross_process_clock(ast.parse(src))


def _cross_process_clock(tree: ast.Module) -> List[Hit]:
    """`detect_cross_process_clock` 的 tree 版"""
    spawn = _spawn_calls(tree)
    if not spawn:
        return []
    clocks = clock_calls_in(tree)
    if not clocks:
        return []
    return [("cross_process_clock", "<whole-file>",
             f"第{spawn[0].lineno}行起子进程（共{len(spawn)}处），"
             f"第{clocks[0].lineno}行等处读时钟（共{len(clocks)}处）："
             f"子进程是**新解释器**、不被平移 ⇒ 父进程用被平移的时钟造的夹具"
             f"与子进程的真实时钟判定会差 delta 天")]


# ════════════════════════════════════════════════════════════════════
#  检测器 ⑦：用 time.time() 派生文件 mtime（盲区 #1 的第二形状）
# ════════════════════════════════════════════════════════════════════


def detect_mtime_from_time_time(src: str) -> List[Hit]:
    """**同函数**内把 `time.time()` 派生的值喂给文件 mtime 操作

    【为什么单列一条（S11-08 遗留收口期实测）】本工具**刻意不平移** `time.time()`
    与文件系统时钟，而被测的"旧文件/过期"判据通常是 `datetime.now()` 派生
    ⇒ 用 `time.time()` 给文件设 mtime 属**两个时钟源混用**：平移下必然差 delta 天
    （慢档 `test_task_scheduler.py` 实测 4 处：−400 下旧文件落不到截止线内）。

    ⚠️ **这是 `detect_fs_clock_vs_today` 抓不到的形状**：那条规则的判据要求同函数内
    出现时钟调用，而**修复前的写法里没有**（产品的比较基准在别的模块）
    ⇒ 该类只能靠四臂实跑发现。本规则改为按"`time.time()` 的值**流入** mtime 操作"
    这一可判定形状报警，从而把"修复前的形状"也钉住。

    **刻意不报**：同函数里既测耗时（`elapsed = time.time() - t0`）、又把 mtime 设成
    **显式常量**——值没流入 mtime，属惰性（见 `_SAMPLE_INERT`）。
    """
    return _mtime_from_time_time(ast.parse(src))


def _carries_posix_clock(node: ast.AST, derived: Dict[str, int]) -> bool:
    """表达式是否携带 `time.time()`：直接调用，或引用了它的派生局部名"""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and _dotted(n.func) == "time.time":
            return True
        if isinstance(n, ast.Name) and n.id in derived:
            return True
    return False


def _mtime_from_time_time(tree: ast.Module) -> List[Hit]:
    """`detect_mtime_from_time_time` 的 tree 版"""
    hits: List[Hit] = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        if not (attrs & _FS_CLOCK_ATTRS):
            continue
        derived = _time_time_names(fn)          # 由 time.time() 绑定的局部名 → 行号
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            attr = call.func.attr if isinstance(call.func, ast.Attribute) else None
            if attr != "utime":
                continue
            args = list(call.args) + [k.value for k in call.keywords]
            if any(_carries_posix_clock(a, derived) for a in args):
                hits.append((
                    "mtime_from_time_time", f"{fn.name}@{call.lineno}",
                    f"第{call.lineno}行把 `time.time()` 派生的值写进 mtime"
                    f"（该时钟**不被平移**）⇒ 与被测的 `datetime.now()` 判据差 delta 天；"
                    f"请改用 `datetime.now().timestamp()` 派生（与被测同源）"))
                break
    return hits


# ════════════════════════════════════════════════════════════════════
#  扫描面
# ════════════════════════════════════════════════════════════════════

_DETECTORS = (
    ("import_time_clock", _import_time_clock),
    ("default_arg_clock", _default_arg_clock),
    ("escaping_closure_clock", _escaping_closure_clock),
    ("fs_clock_vs_today", _fs_clock_vs_today),
    ("mtime_from_time_time", _mtime_from_time_time),
    ("time_time_derived_day", _time_time_derived_day),
    ("cross_process_clock", _cross_process_clock),
)


def _iter_sources(root: Path = _TESTS_ROOT):
    for p in sorted(root.rglob("*.py")):
        if p.resolve() == _THIS_FILE:
            continue                                   # 本文件内是"样本字符串"，排除自身
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        yield p.relative_to(_REPO_ROOT).as_posix(), src


_SCAN_CACHE: Dict[str, Dict[str, List[Hit]]] = {}


def scan_all(root: Path = _TESTS_ROOT) -> Dict[str, List[Hit]]:
    """全部盲区全量扫描 → {类别: [(类别, 位置, 说明), ...]}

    每个文件**只 parse 一次**（各检测器共用同一棵 AST）；结果按 root 缓存，
    避免守卫/CLI 反复全量重扫（首版实测：无缓存时 `--scan` 120s 都跑不完）。
    """
    key = str(root)
    if key in _SCAN_CACHE:
        return _SCAN_CACHE[key]
    out: Dict[str, List[Hit]] = {name: [] for name, _ in _DETECTORS}
    for rel, src in _iter_sources(root):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for name, detector in _DETECTORS:
            for _n, loc, why in detector(tree):
                out[name].append((name, f"{rel}::{loc}", why))
    _SCAN_CACHE[key] = out
    return out


def surface_stats(root: Path = _TESTS_ROOT) -> Dict[str, int]:
    """扫描面的量化（防"筛选条件写错导致 0 命中"的假绿）+ 盲区 #3 的背景量"""
    stats = {"files": 0, "files_with_clock_call": 0, "time_time_calls": 0,
             "fs_clock_files": 0, "iso_files": 0}
    for _rel, src in _iter_sources(root):
        stats["files"] += 1
        if _ISO_ANY.search(src):
            stats["iso_files"] += 1
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        if any(isinstance(n, ast.Call) and clock_kind(n) is not None
               for n in ast.walk(tree)):
            stats["files_with_clock_call"] += 1
        stats["time_time_calls"] += sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _dotted(n.func) == "time.time")
        if any(isinstance(n, ast.Attribute) and n.attr in _FS_CLOCK_ATTRS
               for n in ast.walk(tree)):
            stats["fs_clock_files"] += 1
    return stats


# ════════════════════════════════════════════════════════════════════
#  已裁定登记表（每条必须写明"为什么不会随日期变化 / 为什么已中和"）
# ════════════════════════════════════════════════════════════════════

#: (相对路径, 位置) → 理由。**每条必须写明"为什么它不会随日期变化 / 为什么已中和"**，
#: 不接受"已知边界、无需处理"这类空话。
ALLOWLIST: Dict[Tuple[str, str], str] = {
    # ── 盲区 #2：模块级取时钟（静态臂）──────────────────────────────
    ("tests/integration/quick_recovery_check.py",
     "tests/integration/quick_recovery_check.py::<module>:now@21"):
        "独立运维脚本，**不被 pytest 收集**（文件名不匹配 `python_files = test_*.py`）；"
        "模块级 `now` 只是脚本启动时打印当前时间（L22），脚本自身就是运行期，"
        "不存在'被测代码 vs 夹具'的口径分叉。",
    ("tests/unit/test_s5_03_cost_brake.py",
     "tests/unit/test_s5_03_cost_brake.py::<module>:BASE@42"):
        "S11-07 已中和：`_refresh_base`（autouse，L61-64）在每个用例开始前把它重算为"
        "**调用期**的 `_base_now()`（`monkeypatch.setattr(sys.modules[__name__], "
        "\"BASE\", _base_now())`）。模块级初值仅为兼容导入期引用，测试运行期间"
        "不再被使用；断言逐字未改。**保留该行是有意的**：它让'导入期常量'这个"
        "形状留在源码里可被本守卫持续看见，而不是被悄悄删掉。",

    # ── 盲区 #1：文件系统时钟 vs Python 的"今天" ────────────────────
    ("tests/unit/test_knowledge_card.py",
     "tests/unit/test_knowledge_card.py::test_list_since_returns_only_new_files@905"):
        "**已按统一时钟口径修正（S11-07 §2.4）**：mtime 与 since 同源自**同一个** "
        "`now`（旧卡 now-2h、新卡 now、since now-1min），不再依赖"
        "'create 出来的 mtime 恰好等于 Python now'的隐含假设。本轮复查无需再改。",
    ("tests/unit/test_knowledge_skill_bridge.py",
     "tests/unit/test_knowledge_skill_bridge.py::test_convert_cards_since_only_new@224"):
        "同上一类（S11-07 §2.4 同款修正）：两个 mtime 与 since 全部由同一个 `now` "
        "派生 ⇒ 不存在'两个时钟来源'的比较。",
    ("tests/unit/test_task_scheduler.py",
     "tests/unit/test_task_scheduler.py::test_cleanup_old_logs@351"):
        "两侧同源：`old_time`（now-31 天）与产品侧的比较基准都由同一个 "
        "`datetime.now()` 派生，平移/回溯下**同步**移动 ⇒ 不构成盲区实例"
        "（31 > 阈值 30，相对关系恒成立）。"
        "**S11-08 遗留 #4 收口（2026-09-15）**：原用例无断言、且用 "
        "`patch('agent.task_scheduler.Path')` 造替身——但 `DATA_DIR` 是导入期真实 `Path`，"
        "替身从未生效（补断言即暴露：`unlink` 0 次）⇒ 用例实际空转。"
        "已改为**真实 tmp 目录**（patch `DATA_DIR`）+ 真跑 glob/stat/unlink，"
        "并补两条断言（31 天前必删 / 29.99 天前必留，避开微秒漂移）。"
        "三臂（不平移/+400/−400）实测 1 passed ×3。",

    ("tests/conftest.py",
     "tests/conftest.py::_no_stray_approval_store@77"):
        "**与日期语义完全无关，两套时钟不参与任何比较**（2026-09-20 裁定）。"
        "该用例级守卫用 `st_mtime_ns`（文件系统时钟）作为**不透明的变更令牌**："
        "它只比较『用例执行窗口前后该值是否相等』，从而判断审批库是否在本窗口内被写。"
        "这与本盲区针对的缺陷形状（**把文件 mtime 与 Python 的『今天』放在一起比较**，"
        "例如 `since = now - 1h` 去筛 `st_mtime`）**不同**：此处两个读数都取自"
        "**同一套文件系统时钟**，且判定是纯等式比较，不涉及任何日期/时区/零点边界。"
        "命中的 `now` 侧来自失败消息里的**诊断打印**（把前后数值原样报给开发者看），"
        "**不被任何逻辑读取**，与 `st_mtime_ns` 无任何比较关系。"
        "⇒ 日期平移（把『今天』改掉）不会改变本守卫的判定结果；"
        "把它一起平移反而无意义（mtime 本就由 OS 时钟给定，见本文件"
        "`test_mtime_is_not_derived_from_the_unshifted_posix_clock` 的同一口径）。"
        "**背景**：该守卫的判据在 2026-09-20 由『后端是否在跑』升级为 `(size, mtime_ns)` 差集，"
        "本次裁定即针对升级后的写法（提交主题：stray-approval 守卫改用 mtime 差集）。",

    # ── 盲区 #3：time.time() 换算成日期串 ──────────────────────────
    ("tests/integration/test_resource_monitor_integration.py",
     "tests/integration/test_resource_monitor_integration.py"
     "::test_post_init_generates_iso_time@125"):
        "自洽性断言：`expected`（L125）与被测 `snap.iso_time` 都由**同一个** "
        "`ts = time.time()`（L122）换算而来 ⇒ 与'今天是哪天'无关。"
        "且工具**刻意不平移** `fromtimestamp`（S11-07 §六#5：显式 epoch 属"
        "'固定时间戳数据'，平移它反而造新伪影），两侧口径仍一致。",

    # ── 盲区 #5：跨进程（本轮新查出）──────────────────────────────
    ("tests/integration/test_knowledge_audit_ci_edge.py",
     "tests/integration/test_knowledge_audit_ci_edge.py::<whole-file>"):
        "**【真实实例，已修复（S11-08 遗留 #1 收口，2026-09-15）】**原状：父进程用被平移的 "
        "`date.today()`（L142/L180）造卡，子进程 `python -m agent.knowledge audit`（L72）"
        "读**真实**时钟 ⇒ 四臂实跑 不平移 4 passed｜CONTROL 4 passed｜+400 **2 failed**｜"
        "−400 **3 failed**（−400 探针量化：父进程 today=2025-08-11，子进程把「恰好 90 天」"
        "的卡判成 `days_unaccessed=490`）。"
        "**处置：已按 S11-08 §八#1 建议方案 (a) 修复** —— `agent.knowledge audit` 增加"
        "**可选** `--now`（ISO 日期，缺省 `None` = 走 `date.today()`，**默认行为零变化**），"
        "`lint_all`/`run_knowledge_audit` 增加同名可选参数贯通；测试侧 `_audit_cli` 传"
        "父进程 `date.today()` ⇒ 两侧口径一致。**修后四臂 4 passed ×4**。"
        "本形状（子进程 + 时钟）仍保留在检测器视野内，故登记而非移除。",
    ("tests/unit/test_knowledge_cli.py",
     "tests/unit/test_knowledge_cli.py::<whole-file>"):
        "同样跨进程（`_run_cli` L65 跑 `python -m agent.knowledge`）+ `make_card` 用"
        "`date.today()`（L52，S11-06 已改相对）。四臂实跑**全绿**：不平移/CONTROL/"
        "+400/−400 各 **49 passed**（含本文件与 skill_bridge）⇒ 其断言不落在"
        "'夹具日 vs 子进程今日'的边界上（卡面无过期声明）⇒ 无实例。",
    ("tests/unit/test_knowledge_skill_bridge.py",
     "tests/unit/test_knowledge_skill_bridge.py::<whole-file>"):
        "同 `test_knowledge_cli.py`：四臂实跑各 **49 passed**（两次同批命令），无实例。",
    ("tests/unit/test_task_scheduler.py",
     "tests/unit/test_task_scheduler.py::<whole-file>"):
        "该文件 `pytestmark = pytest.mark.slow`（L8）⇒ 默认 fast 模式下 **80 项全部 skip**"
        "（四臂实跑一致：`80 skipped in 0.74s`）⇒ 其中的子进程/时钟代码本轮未被执行，"
        "无实例。**残留已于 S11-08 遗留收口消除**：慢档（本文件 + comprehensive + "
        "integration 三件套共 259 项）现纳入夜间工作流 `date-shift-guard.yml` 的四臂矩阵"
        "（`--runslow`），实测 259 passed ×4。",

    # ── S11-08 遗留收口（2026-09-15）：mtime 与产品 cutoff 统一为 datetime.now() ──
    # 命名说明：以下四条的探测形状是"同函数内既有 os.utime（FS 时钟）又有
    # datetime.now（Python 今天侧）"。判定关键**不是形状，而是两侧是否同源**——
    # 产品 `cleanup_old_logs` 的 cutoff 由 `datetime.now()` 派生，故只要 mtime 也由
    # `datetime.now()` 派生即同源、平移下同步移动；原实现用 `time.time()`（工具
    # **刻意不平移**的时钟源）才是真分叉。
    ("tests/unit/test_task_scheduler.py",
     "tests/unit/test_task_scheduler.py::test_cleanup_old_logs_with_files@843"):
        "**已按统一时钟口径修正（S11-08 遗留 #3 收口）**：mtime 由 "
        "`datetime.now().timestamp() - 40/5 天` 派生，与产品 `cleanup_old_logs` 的 "
        "cutoff（`datetime.now()` 派生）**同源** ⇒ 平移/回溯下同步移动。"
        "原用 `time.time()`（工具只挪 datetime、不挪 time.time）⇒ ±400 实跑曾红："
        "−400 下旧文件落不到截止线内。修后慢档三件套四臂 **259 passed ×4**。",
    ("tests/unit/test_task_scheduler.py",
     "tests/unit/test_task_scheduler.py::test_cleanup_old_logs_deletes_old_file@1238"):
        "同上（S11-08 遗留 #3 收口）：`old_time` 改由 "
        "`datetime.now().timestamp() - 40 天` 派生，与产品 cutoff 同源；"
        "原 `time.time()` 在 −400 下使旧文件判不到「过期」"
        "（`assert not old_file.exists()` 失败）。",
    ("tests/unit/test_task_scheduler_comprehensive.py",
     "tests/unit/test_task_scheduler_comprehensive.py::test_cleanup_old_logs_no_exception@830"):
        "同上（S11-08 遗留 #3 收口）：本用例只验「不抛异常」、无断言，原 `time.time()` "
        "不会致红；对齐为 `datetime.now()` 是为避免将来补断言时踩同一口径分叉。",
    ("tests/integration/test_task_scheduler_integration.py",
     "tests/integration/test_task_scheduler_integration.py::test_cleanup_old_logs_with_files@818"):
        "同上（S11-08 遗留 #3 收口）：旧卡 mtime 与**新卡** mtime 都显式设为 "
        "`datetime.now()` 派生。关键在后者——原实现新卡**不设** mtime ⇒ 取真实 FS 时间，"
        "+400 下会被平移后的 cutoff（真实今天+370 天）判为过期而误删 ⇒ "
        "`assert new_file.exists()` 1 failed。两侧同源后四臂 259 passed ×4。",

    # ── 盲区 #1 **第二形状**：`time.time()` 派生的值写进 mtime ──────────
    # （本条规则是 S11-08 遗留收口**之后**新加的：上面 4 条登记的是"修好之后"的形状，
    #   而"修复前的形状"在 detect_fs_clock_vs_today 下抓不到 —— 同函数内没有时钟调用。
    #   新规则按"值流入 mtime"判定，把修复前的形状也钉住。）
    ("tests/unit/test_task_scheduler.py",
     "tests/unit/test_task_scheduler.py::test_cleanup_old_logs_exception@1281"):
        "**惰性、无实例**：该用例只验证「`datetime.now()` 抛异常时错误被记录」"
        "（`assert mock_logger.error.called`）—— 它把 `agent.task_scheduler.datetime` "
        "整体换成会抛异常的 MagicMock，产品在算 cutoff 时**即抛错**，"
        "故 L1281 设的 mtime **从不参与任何判定**，`time.time()` 与"
        "「被测 cutoff 由 `datetime.now()` 派生」不构成实际分叉。"
        "（同文件其余 3 处同形写法已在 S11-08 遗留 #3 收口时改为 "
        "`datetime.now().timestamp()`。）",
}


# ════════════════════════════════════════════════════════════════════
#  守卫
# ════════════════════════════════════════════════════════════════════


def _unexpected(name: str, root: Path = _TESTS_ROOT) -> List[Hit]:
    """未登记（= 守卫要报红）的命中；位置串形如 `tests/unit/x.py::f@12`"""
    return [h for h in scan_all(root)[name]
            if (h[1].split("::")[0], h[1]) not in ALLOWLIST]


def _fail(name: str, hits: List[Hit], advice: str) -> None:
    if hits:
        lines = [f"  · {loc}  {why}" for _n, loc, why in hits[:40]]
        pytest.fail(f"日期平移法盲区「{name}」发现未裁定命中：\n" + "\n".join(lines)
                    + f"\n\n{advice}\n（若确认无害，请登记到本文件 ALLOWLIST 并写明理由）")


def test_no_import_time_clock_constants():
    """盲区 #2（静态臂）：模块级/类体不得由时钟调用推导常量"""
    _fail("import_time_clock", _unexpected("import_time_clock"),
          "请改为**调用期**取值（函数内取时钟），或让它可注入；"
          "模块级 `X = date.today()` 会在跨零点时与产品侧的运行期取时钟分叉。")


def test_no_clock_captured_in_default_arguments():
    """盲区 #4-a：模块级/类体的 def 不得在默认参数里取时钟"""
    module_level = [h for h in _unexpected("default_arg_clock")
                    if h[1].split("::", 1)[1].startswith("<module>")]
    _fail("default_arg_clock", module_level,
          "默认参数在 def 求值期求值：模块级 def ⇒ 导入期取时钟（夜间跑必炸）。"
          "请改为函数体内取值或显式传参。")
    # 函数内 def 的同形写法只作为信息输出（同瞬间使用，不构成分叉）


def test_no_clock_captured_in_escaping_closures():
    """盲区 #4-b：逃逸闭包不得捕获时钟值"""
    _fail("escaping_closure_clock", _unexpected("escaping_closure_clock"),
          "捕获时刻与使用时刻分离 ⇒ 可能跨过零点或被平移工具漏掉；"
          "请在闭包内**调用期**重新取时钟。")


def test_fs_clock_is_not_mixed_with_python_today():
    """盲区 #1：同一函数内不得混用"文件系统时钟"与"Python 的今天" """
    _fail("fs_clock_vs_today", _unexpected("fs_clock_vs_today"),
          "mtime 走**真实 OS 时钟**、平移不参与 ⇒ 与 Python 的今天口径不一致时"
          "会产生假红/漏判（S11-07 §2.4 实测）。请统一时钟口径"
          "（把 since 由同一时钟派生，或两侧都用显式给定值）。")


def test_time_time_is_not_used_to_derive_the_day():
    """盲区 #3：不得用 `time.time()` 换算"今天是哪天" """
    _fail("time_time_derived_day", _unexpected("time_time_derived_day"),
          "`time` 模块不被平移 ⇒ 用它推「今天」的代码平移法测不出。"
          "请改用 `date.today()` 等可被平移/可注入的口径。")


def test_mtime_is_not_derived_from_the_unshifted_posix_clock():
    """盲区 #1 **第二形状**：`time.time()` 派生的值不得写进文件 mtime

    修复前的慢档写法（`old_time = time.time() - 40*86400; os.utime(...)`）在
    `detect_fs_clock_vs_today` 下**抓不到**（同函数内没有时钟调用），只能靠四臂实跑发现
    —— 本规则把它钉住，避免"修好之后再悄悄回来"。
    """
    _fail("mtime_from_time_time", _unexpected("mtime_from_time_time"),
          "`time.time()` 与文件系统时钟**都不被平移**，而被测的『旧文件/过期』判据"
          "通常是 `datetime.now()` 派生 ⇒ 平移下必然差 delta 天。"
          "修法：mtime 也走 `datetime.now().timestamp()`（与被测同源）；"
          "若确认该 mtime 不参与任何判定（惰性），请登记 ALLOWLIST 并写明理由。")


def test_cross_process_children_read_the_unshifted_clock():
    """盲区 #5（本轮新查出）：既起子进程、又用（可被平移的）时钟造夹具

    命中只表示"必须四臂实跑分类"，不表示缺陷。分类口径：
    **CONTROL 绿 + 仅平移档红** ⇒ 跨进程口径差（工具盲区，非产品缺陷、非替换伪影）。
    """
    _fail("cross_process_clock", _unexpected("cross_process_clock"),
          "子进程是**新解释器**，插件只替换当前进程 ⇒ 父（被平移）/子（真实）时钟"
          "差 delta 天；−400 实测：夹具卡被子进程判成 490 天过期"
          "（`days_unaccessed=490`，父进程口径是「恰好 90 天」）。"
          "请四臂实跑（不平移 / CONTROL / +400 / −400）分类并把实跑证据写进 ALLOWLIST。")


# ── 自检（守卫也会烂）────────────────────────────────────────────

_SAMPLE_IMPORT_TIME = (
    "import datetime as dt\n"
    "TODAY = dt.date.today().isoformat()\n"
    "class C:\n"
    "    WEEK = dt.datetime.now().astimezone().isoformat()\n"
    "def f():\n"
    "    local_now = dt.datetime.now()      # 运行期取值：**不**得命中\n"
    "    return local_now\n"
)

_SAMPLE_DEFAULT_ARG = (
    "import datetime as dt\n"
    "def f(t=dt.date.today()):\n"          # 模块级 def ⇒ 导入期求值
    "    return t\n"
)

_SAMPLE_CLOSURE = (
    "import datetime as dt\n"
    "def outer():\n"
    "    day = dt.date.today().isoformat()\n"
    "    def inner():\n"
    "        return day\n"
    "    return inner                        # 逃逸\n"
)

_SAMPLE_FS_CLOCK = (
    "import datetime as dt, os\n"
    "def f(path):\n"
    "    since = dt.datetime.now().timestamp()\n"
    "    return os.stat(path).st_mtime < since\n"
)

_SAMPLE_TIME_DERIVED = (
    "import time, datetime as dt\n"
    "def f():\n"
    "    ts = time.time()\n"
    "    return dt.date.fromtimestamp(ts).isoformat()\n"
)

_SAMPLE_INERT = (
    "import time, datetime as dt, os\n"
    "def f(t0, path):\n"
    "    elapsed = time.time() - t0                  # 测耗时：无关\n"
    "    os.utime(path, (1_600_000_000, 1_600_000_000))   # 显式固定日：无关\n"
    "    return elapsed, os.stat(path).st_mtime\n"
    "def g():\n"
    "    def inner():\n"
    "        return dt.date.today().isoformat()\n"
    "    return inner()                              # 当场调用：不逃逸\n"
)

_SAMPLE_CROSS_PROCESS = (
    "import subprocess, sys, datetime as dt\n"
    "def run_cli():\n"
    "    return subprocess.run([sys.executable, '-m', 'agent.knowledge', 'audit'])\n"
    "def test_fixture():\n"
    "    day = dt.date.today().isoformat()\n"      # 父进程（被平移）造夹具
    "    assert run_cli().returncode == 0\n"       # 子进程读真实时钟
)

_SAMPLE_SPAWN_ONLY = (
    "import subprocess, sys\n"
    "def test_smoke():\n"
    "    assert subprocess.run([sys.executable, '-V']).returncode == 0\n"
)

_SAMPLE_MTIME_POSIX = (
    "import os, time\n"
    "def f(path):\n"
    "    ts = time.time() - 40 * 86400\n"
    "    os.utime(path, (ts, ts))\n"
)

_SAMPLE_MTIME_PYTHON = (
    "import os, datetime as dt\n"
    "def f(path):\n"
    "    ts = dt.datetime.now().timestamp() - 40 * 86400\n"
    "    os.utime(path, (ts, ts))\n"
)


@pytest.mark.parametrize("detector,sample,expect", [
    (detect_import_time_clock, _SAMPLE_IMPORT_TIME, 2),
    (detect_default_arg_clock, _SAMPLE_DEFAULT_ARG, 1),
    (detect_escaping_closure_clock, _SAMPLE_CLOSURE, 1),
    (detect_fs_clock_vs_today, _SAMPLE_FS_CLOCK, 1),
    (detect_time_time_derived_day, _SAMPLE_TIME_DERIVED, 1),
    (detect_cross_process_clock, _SAMPLE_CROSS_PROCESS, 1),
    (detect_mtime_from_time_time, _SAMPLE_MTIME_POSIX, 1),
])
def test_detectors_catch_their_sample_defects(detector, sample, expect):
    """**正向对照**：每个检测器必须命中它的样本缺陷（否则守卫是空转的假绿）"""
    hits = detector(sample)
    assert len(hits) == expect, f"{detector.__name__} 命中 {hits}，期望 {expect} 条"


def test_detectors_ignore_inert_shapes():
    """**反向对照**：惰性/无关写法必须**不**命中（误报会让守卫被绕过）"""
    assert detect_import_time_clock(_SAMPLE_INERT) == []
    assert detect_default_arg_clock(_SAMPLE_INERT) == []
    assert detect_escaping_closure_clock(_SAMPLE_INERT) == []
    assert detect_fs_clock_vs_today(_SAMPLE_INERT) == []
    assert detect_time_time_derived_day(_SAMPLE_INERT) == []
    assert detect_cross_process_clock(_SAMPLE_INERT) == []
    # 只有子进程、不读时钟 ⇒ 不得命中（跨进程本身不是问题，"跨进程 + 时钟"才是）
    assert detect_cross_process_clock(_SAMPLE_SPAWN_ONLY) == []
    # 只读时钟、不起子进程 ⇒ 不得命中
    assert detect_cross_process_clock(_SAMPLE_DEFAULT_ARG) == []
    # 测耗时 + **显式常量** mtime（值没流入 mtime）⇒ 不得命中
    assert detect_mtime_from_time_time(_SAMPLE_INERT) == []
    # mtime 由 **datetime.now()** 派生（与被测同源）⇒ 不得命中
    assert detect_mtime_from_time_time(_SAMPLE_MTIME_PYTHON) == []


def test_import_time_detector_skips_function_locals():
    """口径钉死：**函数内**取时钟不得被误判为"导入期取时钟"（这是本规则的关键边界）"""
    fn_local = ("import datetime as dt\n"
                "def f():\n"
                "    now = dt.datetime.now()\n"
                "    return now.isoformat()\n")
    assert detect_import_time_clock(fn_local) == []
    # 而模块级同形写法必须命中（否则上面那条就成了空转）
    assert len(detect_import_time_clock("import datetime as dt\nNOW = dt.datetime.now()\n")) == 1


@pytest.mark.slow
def test_scan_surface_is_not_silently_empty():
    """**防自欺**：扫描面必须真的读到含时钟调用的文件（历史踩过"筛选条件写错 ⇒ 0 命中"）

    下界取自 2026-09-15 的**实测基线**（`--scan` 输出）：
    files=790 / files_with_clock_call=68 / time_time_calls=450 / iso_files=191。
    下界刻意留出余量（防止正常的用例增删把守卫逼成假红），但不允许量级坍塌。

    【不易·2026-09-20 补标 `slow` —— 它此前是"首轮全量杀手"】
    本用例会**全仓扫描**测试面（实测 790 文件 / 450 处 `time.time()`），
    **隔离单跑耗时 46.37s**，而全局预算是 `--timeout=120`（`pytest.ini:47`）
    ⇒ **余量仅 2.6×**。

    后果（2026-09-20 实测，非推测）：当机器上还有其它 pytest 进程（分块/并行）时，
    IO 与 WMI 慢路径叠加使它越过 120s ⇒ `pytest-timeout` 的 thread 法执行
    **`os._exit(1)` 直接杀掉整个 pytest 进程**（机制见 `pytest.ini:33-45`），
    导致**该文件之后的 580 个测试文件从未执行、且不输出结束摘要**，
    退出码只有 1，与"真有测试失败"无法区分。

    本仓对该类"环境性慢测试"已有既定分流机制：`@pytest.mark.slow`，
    `scripts/run_full_pytest.py --mode fast`（默认）排除、`--mode slow` 单独跑
    （**单块不分块** ⇒ 无 CPU 争用，46s 远在预算内）。故标注后：
      · 默认 fast 回归**不再被它拖垮**（不再有"丢 580 个文件"的风险）
      · 它仍在 slow lane 中**照常被监控**，守卫能力不减

    【变易】不要改成"放宽下界"来省时间 —— 那会削弱本用例的核心价值
    （防"筛选条件写错导致守卫空转"）。代价应通过**分流**支付，而不是削弱断言。
    """
    stats = surface_stats()
    assert stats["files"] >= 600, f"扫描到的测试文件仅 {stats['files']} 个，明显偏少"
    assert stats["files_with_clock_call"] >= 50, (
        f"只有 {stats['files_with_clock_call']} 个文件含时钟调用（实测基线 68），"
        f"明显偏少 ⇒ 时钟识别规则可能写错（守卫会退化成空转）")
    assert stats["time_time_calls"] >= 300, (
        f"`time.time()` 只数到 {stats['time_time_calls']} 处（实测基线 450），"
        f"明显偏少 ⇒ 盲区 #3 的背景量统计失真")
    assert stats["iso_files"] >= 150, (
        f"含硬编码 ISO 日期的文件只数到 {stats['iso_files']} 个（实测基线 191）")


# ── 盲区 #2 动态臂：晚替换探针（S11-07 §4.4 手法固化）────────────

_SYNTH_IMPORT_TIME = '''\
"""合成用例：**导入期**取时钟（晚替换探针必须抓到它）"""
import datetime as dt

_TODAY = dt.date.today().isoformat()          # 导入期求值


def test_import_time_constant_equals_runtime_clock():
    runtime = dt.date.today().isoformat()     # 产品侧口径：运行期取时钟
    assert _TODAY == runtime
'''

_SYNTH_CALL_TIME = '''\
"""合成用例：**调用期**取时钟（晚替换探针必须放它过 —— 反向对照）"""
import datetime as dt


def test_call_time_value_equals_runtime_clock():
    captured = dt.date.today().isoformat()
    runtime = dt.date.today().isoformat()
    assert captured == runtime
'''


def _run_probe(target: Path, *, late: bool, shift: int = 1) -> subprocess.CompletedProcess:
    """在**子进程**里跑一次探针（不污染本进程的 datetime 安装状态）"""
    env = dict(os.environ)
    env["CP_DATE_SHIFT_DAYS"] = str(shift)
    env.pop("CP_DATE_SHIFT_CONTROL", None)
    if late:
        env["CP_DATE_SHIFT_LATE"] = "1"
    else:
        env.pop("CP_DATE_SHIFT_LATE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(target), "-q", "--tb=line",
         "-p", "no:randomly", "-p", "tests._date_shift_plugin",
         "-p", "no:cacheprovider"],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)


def test_late_probe_detects_import_time_constant(tmp_path):
    """**探针正向自检（端到端）**：晚替换探针必须抓到"导入期取时钟"

    背景：平移法对"导入期"与"运行期"是**同步**挪的，永远不会制造分歧
    ⇒ 只有"晚替换"（收集完成后再替换时钟）才能确定性复刻"导入期 vs 运行期差一天"。
    这条用例就是该探针的"有牙齿"证明：合成用例必须在探针下变红。
    """
    target = tmp_path / "test_synth_import_time_clock.py"
    target.write_text(_SYNTH_IMPORT_TIME, encoding="utf-8")
    proc = _run_probe(target, late=True)
    assert proc.returncode != 0, (
        "晚替换探针**没能**抓到导入期取时钟 ⇒ 探针已退化成空转（假绿）：\n"
        + proc.stdout[-2000:])
    assert "1 failed" in proc.stdout, proc.stdout[-2000:]


def test_late_probe_does_not_flag_call_time_capture(tmp_path):
    """**探针反向对照**：调用期取时钟的写法必须通过（否则探针只会制造噪声）"""
    target = tmp_path / "test_synth_call_time_clock.py"
    target.write_text(_SYNTH_CALL_TIME, encoding="utf-8")
    proc = _run_probe(target, late=True)
    assert proc.returncode == 0, (
        "调用期取时钟被误报 ⇒ 探针会产生噪声：\n" + proc.stdout[-2000:])
    assert "1 passed" in proc.stdout, proc.stdout[-2000:]


def test_early_probe_is_blind_to_import_time_constant(tmp_path):
    """**盲区存在性的证据**：同一合成用例在**普通平移**下是绿的

    这是"为什么必须另立晚替换探针"的可复算证明 —— 若哪天这条变红，
    说明普通平移法已能覆盖盲区 #2，本探针可以退休（守卫会提醒重新核定）。
    """
    target = tmp_path / "test_synth_import_time_clock.py"
    target.write_text(_SYNTH_IMPORT_TIME, encoding="utf-8")
    proc = _run_probe(target, late=False)
    assert proc.returncode == 0, (
        "普通平移法竟然抓到了导入期取时钟？请重新核定盲区 #2 的结论：\n"
        + proc.stdout[-2000:])


# ════════════════════════════════════════════════════════════════════
#  收集期预热（S11-10 · R9：修 Shard 4 的"超时级联"）
# ════════════════════════════════════════════════════════════════════
# Why（不易，本行是"把一次性成本移出计时窗口"，不是放宽任何判据）：
#   本文件的守卫要跑一次 `tests/` 全树 AST 扫描（`scan_all()`，按 root 缓存）。
#   这是**一次性** CPU 密集型成本，且全部落在**首个**需要它的用例上：
#   本机实测 `--durations=8` → 首个用例 16.77s，其余用例命中缓存后亚秒级。
#   在 CI 上（4 核 runner + `-n 2 --dist=loadscope`，同 shard 还有 ~3100 个用例）
#   这个成本**会突破 pytest 全局 `--timeout=60`**（CI 实跑形态：7 条守卫用例
#   全部 `Failed: Timeout (>60.0s)`）。一旦首个用例超时，扫描结果**没能写进缓存**；
#   xdist 在 worker 被超时终结后重启 worker，下一条守卫用例又成了"首个"、
#   再次全量扫描、再次超时 ⇒ **7 条级联超时**，并波及同 worker 的其它用例
#   （`b54c3174` 与 `2a677f0b` 两次失败集合**逐字相同**：7 条守卫 + 1 条同 worker 用例；
#   载轻的 `a08f92e9` 则全绿）。
# 做法（变易）：把这次一次性扫描提前到**模块导入期（pytest 收集期）**完成 ——
#   收集不在 `--timeout=60` 的计时窗口内，级联的触发条件随之消失。
#   **断言、检测器、扫描范围、授权清单一字未改**（改的只是"何时付这份成本"）。
# 代价（变易）：每个收集本模块的进程多付一次全量扫描（本机 ~17s；`--dist=loadscope`
#   下同一模块只落一个 worker，故每个 shard 至多一次）；CLI 路径
#   `python tests/unit/test_date_shift_blindspots_guard.py --scan` 因 `scan_all()`
#   按 root 缓存，**不会**重复扫描。
scan_all()


# ════════════════════════════════════════════════════════════════════
#  可复算入口：python tests/unit/test_date_shift_blindspots_guard.py --scan
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":  # pragma: no cover
    if "--scan" not in sys.argv:
        print("用法: python tests/unit/test_date_shift_blindspots_guard.py --scan")
        sys.exit(2)
    _stats = surface_stats()
    print("── 扫描面 ────────────────────────────────────────────────")
    for _k, _v in _stats.items():
        print(f"  {_k:26s}: {_v}")
    print("── 盲区命中 ──────────────────────────────────────────────")
    _all = scan_all()
    _total_unexpected = 0
    for _name, _ in _DETECTORS:
        _hits = _all[_name]
        _unexp = _unexpected(_name)
        _total_unexpected += len(_unexp)
        print(f"  [{_name}] 命中={len(_hits)} 已裁定={len(_hits) - len(_unexp)} "
              f"未裁定={len(_unexp)}")
        for _n, _loc, _why in _unexp:
            print(f"      ✗ {_loc}  {_why}")
        for _n, _loc, _why in _hits:
            if (_loc.split("::")[0], _loc) in ALLOWLIST:
                print(f"      √ {_loc}  {ALLOWLIST[(_loc.split('::')[0], _loc)]}")
    print(f"── 未裁定合计: {_total_unexpected} ─────────────────────────")
    sys.exit(1 if _total_unexpected else 0)
