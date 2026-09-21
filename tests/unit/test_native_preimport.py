# -*- coding: utf-8 -*-
"""S11-01 守卫：原生扩展导入顺序固化的**实现唯一 / 入口最前 / 共用 / 开关语义**

【本文件守护四条不易性质】
  1. **实现唯一**：`agent/utils/native_preimport.py` 是唯一实现。
     `tests/integration/conftest.py` 不得再内联第二份 —— 两份实现必然各自漂移：
     顺序或开关语义只改一处 ⇒ 另一边**静默**失去保护，而任何守卫都看不出来。
  2. **进程入口最前**：`app_server.py` 必须在**任何其它 `agent.*` / `plugins.*` 导入
     之前**完成预导入。理由：`plugins/__init__.py` 会连带导入 memory/admin/skills/
     chat 等重模块，它们一旦先加载，pyarrow 的"干净窗口"就没了（这正是 S10-05
     遗留 #1 的形状）。
  3. **unit / integration 共用**：`tests/conftest.py`（tests 根）也在模块级调用同一
     函数。S10-05 遗留 #6：`tests/unit` 历史上同样崩过，此前保护只覆盖
     tests/integration ⇒ unit 处于**无保护**状态。
  4. **开关语义正确**：`CP_NATIVE_PREIMPORT_ENABLED` 默认开启、可关闭，
     且已登记 `agent/settings/registry.py`（零缺口硬守卫依赖它）。

【为什么用 AST 而不是"import app_server 再断言"】
  `import app_server` 会执行整个模块级装配（Flask 蓝图、日志系统、健康看板、
  atexit 注册、单例创建…），把单测变成一次真实启动。本文件**只读源码**，零副作用。
  更关键：被守护的事实是"**导入顺序**"——它无法从运行期事后观测
  （`sys.modules` 只反映谁在，不反映谁先），只能从源码机械判定。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import pathlib

import pytest

from agent.utils import native_preimport as npi

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_SERVER = REPO_ROOT / "app_server.py"
TESTS_CONFTEST = REPO_ROOT / "tests" / "conftest.py"
INTEGRATION_CONFTEST = REPO_ROOT / "tests" / "integration" / "conftest.py"

#: 允许早于原生预导入的 agent.* 导入（有且仅有 .env 装载器）
#: 理由：预导入的开关可写在 `.env`（守「配置走 .env」单一数据源），
#:      故 .env 装载必须**先**于读取该开关。除此以外不得有任何 agent.* 抢先。
_ALLOWED_BEFORE_PIN = frozenset({"agent.env_config_manager"})

#: 函数体/类体在 import 期**不执行**，故遍历时不进入（否则函数内延迟导入会污染顺序）
_NOT_EXECUTED_AT_IMPORT = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
#: 这些语句的 body 在 import 期**会**执行，必须继续下探
_DESCEND = (
    ast.Try, ast.If, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While,
)


def _module_level_imports(path: pathlib.Path) -> list[tuple[int, str]]:
    """按源码顺序返回「import 期真正会执行」的 (行号, 模块名)。

    下探 top-level try/if/with/for/while 的 body；**不进入**函数体与类体
    （其中是延迟导入，import 期不执行）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, _NOT_EXECUTED_AT_IMPORT):
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.append((node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                found.append((node.lineno, node.module or ""))
            elif isinstance(node, _DESCEND):
                walk(getattr(node, "body", []) or [])
                walk(getattr(node, "orelse", []) or [])
                walk(getattr(node, "finalbody", []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    walk(handler.body)

    walk(tree.body)
    found.sort(key=lambda item: item[0])
    return found


def _module_level_calls(path: pathlib.Path) -> list[tuple[int, str]]:
    """按源码顺序返回「import 期真正会执行」的 (行号, 被调用函数名)。

    覆盖 `_x = pin(...)` 这类**赋值右侧**的调用（app_server 正是这种写法），
    否则"只 import 不调用"会被误判成"调用了"（假绿灯）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, _NOT_EXECUTED_AT_IMPORT):
            return
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else "")
            found.append((node.lineno, name))
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in tree.body:
        visit(statement)
    found.sort(key=lambda item: item[0])
    return found


def _imported_alias(path: pathlib.Path, module: str, symbol: str) -> list[str]:
    """返回 `from <module> import <symbol> [as X]` 在本文件用到的本地名（可空）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or (node.module or "") != module:
            continue
        for alias in node.names:
            if alias.name == symbol:
                names.append(alias.asname or alias.name)
    return names


# ════════════════════════════════════════════════════════════
#  一、实现唯一（禁止第二份）
# ════════════════════════════════════════════════════════════


class TestSharedImplementationIsUnique:
    def test_shared_module_is_the_canonical_order(self):
        """固化顺序本身就是契约：numpy → pyarrow → pandas → sklearn。"""
        assert npi.NATIVE_IMPORT_ORDER == ("numpy", "pyarrow", "pandas", "sklearn")
        # pyarrow 必须紧跟 numpy：这是"干净窗口"的核心，不是可调项
        assert npi.NATIVE_IMPORT_ORDER.index("pyarrow") == 1

    def test_integration_conftest_has_no_duplicate_implementation(self):
        """tests/integration/conftest.py 不得再内联第二份实现（S11-01 提升后）。"""
        source = INTEGRATION_CONFTEST.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(INTEGRATION_CONFTEST))

        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert "_NATIVE_IMPORT_ORDER" not in assigned, (
            "旧内联实现（_NATIVE_IMPORT_ORDER）又出现了：两份实现会各自漂移"
        )
        assert "_NATIVE_PREIMPORT_RESULTS" not in assigned, (
            "旧内联结果字典又出现了：报告口径会与共用模块分叉"
        )

        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_pin_native_import_order" not in defined, (
            "旧内联函数（_pin_native_import_order）又出现了：禁止复制第二份"
        )

    def test_integration_conftest_references_shared_module(self):
        """反向：它必须真的引用共用模块（否则等于悄悄移除了保护）。"""
        assert _imported_alias(
            INTEGRATION_CONFTEST, "agent.utils.native_preimport", "pin_native_import_order"
        ), "tests/integration/conftest.py 未引用共用预导入实现"

    def test_integration_conftest_bootstraps_repo_root_for_cropped_entry(self):
        """裁剪入口（裸 `pytest --confcutdir=tests/integration`）下上层 conftest 不加载。

        此时 `tests/conftest.py` 的 `sys.path.insert(0, PROJECT_ROOT)` 不执行 ⇒
        本文件对共用模块的 import 会 `ModuleNotFoundError`，**整个 conftest 装载失败**
        （实测 rc=4；基线同组合是「129 collected / 48 errors」，即本改动会让它**更糟**）。
        故本文件必须自带 sys.path 兜底，且必须早于对共用模块的 import。
        """
        tree = ast.parse(
            INTEGRATION_CONFTEST.read_text(encoding="utf-8"),
            filename=str(INTEGRATION_CONFTEST),
        )
        shared_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "") == "agent.utils.native_preimport"
        ]
        assert shared_lines, "未引用共用预导入实现"
        bootstrap_lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and ast.unparse(node.func.value) == "sys.path"
        ]
        assert bootstrap_lines, (
            "tests/integration/conftest.py 缺少 sys.path 兜底：裁剪入口会 ImportError"
        )
        assert min(bootstrap_lines) < min(shared_lines), (
            "sys.path 兜底必须早于共用模块的 import，否则兜底无效"
        )


# ════════════════════════════════════════════════════════════
#  二、进程入口最前（app_server.py）
# ════════════════════════════════════════════════════════════


class TestProcessEntryOrdering:
    def test_app_server_pins_before_any_other_agent_or_plugins_import(self):
        imports = _module_level_imports(APP_SERVER)
        # 反"空报告"守卫：若 AST 遍历什么都没找到，下面的 offenders 断言会**空过**
        # （假绿灯）。故先要求报告本身非空、且真的还有 agent.* 导入排在后面。
        assert len(imports) > 20, f"app_server.py 模块级导入仅解析出 {len(imports)} 条，报告可疑"
        assert any(m.startswith("agent.") for _l, m in imports), "报告里没有 agent.* 导入，判定无意义"

        pin_lines = [
            line for line, module in imports if module == "agent.utils.native_preimport"
        ]
        assert pin_lines, "app_server.py 未在模块级引用共用预导入实现"
        pin_line = min(pin_lines)

        offenders = [
            (line, module)
            for line, module in imports
            if line < pin_line
            and (
                module == "plugins"
                or module.startswith("plugins.")
                or (module.startswith("agent.") and module not in _ALLOWED_BEFORE_PIN)
            )
        ]
        assert offenders == [], (
            "以下导入早于原生预导入，会抢走 pyarrow 的干净窗口（S10-05 遗留 #1 的形状）: "
            f"{offenders}"
        )
        # 正向：预导入之后**仍有** agent.* / plugins.* 导入 —— 说明它确实在"中间最前"，
        # 而不是因为整个文件只剩它一条导入才被判定通过。
        assert any(
            module == "plugins" or module.startswith("plugins.")
            for line, module in imports
            if line > pin_line
        ), "预导入之后没有任何 plugins.* 导入，判定对象可能已消失"

    def test_app_server_actually_calls_the_pin(self):
        """只 import 不调用 = 保护没装上（假绿灯的典型形状）。"""
        aliases = _imported_alias(
            APP_SERVER, "agent.utils.native_preimport", "pin_native_import_order"
        )
        assert aliases, "app_server.py 未引用 pin_native_import_order"
        called = {name for _line, name in _module_level_calls(APP_SERVER)}
        assert called, "模块级调用解析结果为空，判定无意义"
        assert called & set(aliases), (
            f"app_server.py 在模块级未调用预导入（实际调用: {sorted(called)}）"
        )

    def test_app_server_does_not_import_shared_module_lazily(self):
        """必须是模块级 import 期就执行；放进函数体就等于没提到进程入口。"""
        imports = _module_level_imports(APP_SERVER)
        assert any(module == "agent.utils.native_preimport" for _l, module in imports)


# ════════════════════════════════════════════════════════════
#  三、unit / integration 共用同一道保护（tests/conftest.py）
# ════════════════════════════════════════════════════════════


class TestTestsRootConftestSharesProtection:
    def test_tests_root_conftest_pins_at_module_level(self):
        aliases = _imported_alias(
            TESTS_CONFTEST, "agent.utils.native_preimport", "pin_native_import_order"
        )
        assert aliases, "tests/conftest.py 未引用共用预导入实现（unit 仍无保护）"
        called = {name for _line, name in _module_level_calls(TESTS_CONFTEST)}
        assert called & set(aliases), (
            "tests/conftest.py 未在模块级调用预导入 —— unit/integration 不共用保护"
        )

    def test_report_header_is_defined_exactly_once_in_tests(self):
        """报告头只能定义一次。

        若 `tests/conftest.py` 与 `tests/integration/conftest.py` 都定义
        `pytest_report_header`，pytest 会把两处结果**拼接**，integration 跑一次
        会打印两遍同一行，而 unit 一遍也没有（unit 不进 integration conftest）。
        """
        def defines(path: pathlib.Path) -> bool:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            return any(
                isinstance(node, ast.FunctionDef) and node.name == "pytest_report_header"
                for node in tree.body
            )

        assert defines(TESTS_CONFTEST), "tests/conftest.py 未定义 pytest_report_header"
        assert not defines(INTEGRATION_CONFTEST), (
            "tests/integration/conftest.py 又定义了 pytest_report_header：报告头会打印两遍"
        )

    def test_protection_already_applied_in_this_process(self):
        """进程级证据：unit 套件跑起来时保护确实已生效（不是"只写了代码"）。

        判据取"能被看见"这一最弱但最关键的性质：结果字典里必须出现每个模块，
        且**不得**全部为 failed（那说明环境缺件，属另一类问题，另行失败）。
        """
        recorded = {k: v for k, v in npi.PREIMPORT_RESULTS.items() if k != npi.STATUS_KEY}
        if not npi.is_enabled():
            pytest.skip("CP_NATIVE_PREIMPORT_ENABLED=0，本次关闭了保护（跳过进程级断言）")
        assert recorded, "预导入结果为空 —— 保护未在本进程执行"
        for name in npi.NATIVE_IMPORT_ORDER:
            assert name in recorded, f"{name} 无预导入记录（保护未覆盖该模块）"
            assert not recorded[name].startswith("failed"), (
                f"{name} 预导入失败: {recorded[name]}"
            )


# ════════════════════════════════════════════════════════════
#  四、开关语义（默认开 / 可关 / 已登记注册表）
# ════════════════════════════════════════════════════════════

_GATE_PROBE = (
    "import sys;"
    "import agent.utils.native_preimport as m;"
    "r = m.pin_native_import_order();"
    "print('enabled=' + str(m.is_enabled()));"
    "print('status=' + str(r.get(m.STATUS_KEY)));"
    "print('pyarrow_loaded=' + str('pyarrow' in sys.modules));"
)


def _probe_gate(value: str | None) -> dict[str, str]:
    """在**干净子进程**里探测开关语义（父进程的 sys.modules 已被预导入污染）。"""
    env = {k: v for k, v in os.environ.items() if k != "CP_NATIVE_PREIMPORT_ENABLED"}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if value is not None:
        env["CP_NATIVE_PREIMPORT_ENABLED"] = value
    proc = subprocess.run(
        [sys.executable, "-c", _GATE_PROBE],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, f"探测子进程失败 rc={proc.returncode}\n{proc.stderr}"
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            out[key.strip()] = val.strip()
    return out


class TestSwitchSemantics:
    def test_default_is_enabled_and_imports_the_stack(self):
        """未设置 = 开启（与 S10-05 以来 tests 路径的现状等价）。"""
        probe = _probe_gate(None)
        assert probe["enabled"] == "True"
        assert probe["status"] == npi.STATUS_ENABLED
        assert probe["pyarrow_loaded"] == "True"

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_explicit_falsy_values_disable_the_pin(self, value):
        """关闭 = 预导入**真的不发生**（不是"标志位变了但照跑"）。"""
        probe = _probe_gate(value)
        assert probe["enabled"] == "False"
        assert probe["status"] == npi.STATUS_DISABLED
        assert probe["pyarrow_loaded"] == "False", (
            "开关已关但 pyarrow 仍被预导入 —— 开关是假的"
        )

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_explicit_truthy_values_keep_the_pin(self, value):
        probe = _probe_gate(value)
        assert probe["enabled"] == "True"
        assert probe["status"] == npi.STATUS_ENABLED

    def test_is_idempotent_and_never_overwrites_first_observation(self):
        """幂等：重复调用不得把首次真实耗时覆写成 cached（否则报告失去证据力）。"""
        before = dict(npi.PREIMPORT_RESULTS)
        again = npi.pin_native_import_order()
        assert again == before, (
            "重复调用改写了首次观测结果：报告里将看不到真实耗时（假绿灯风险）"
        )


class TestRegistryRegistration:
    def test_registered_with_default_matching_the_real_read_point(self):
        """默认值与类型必须取自**真实读取点**：未设置 ⇒ is_enabled() 为 True。"""
        from agent.settings import registry as R

        spec = R.get_spec("CP_NATIVE_PREIMPORT_ENABLED")
        assert spec is not None, "新增 env 未登记开关注册表（零缺口守卫会变红）"
        assert spec.default is True, f"注册表默认值 {spec.default!r} 与读取点不一致"
        assert spec.env_name == "CP_NATIVE_PREIMPORT_ENABLED"

    def test_owner_points_to_a_real_file(self):
        from agent.settings import registry as R

        spec = R.get_spec("CP_NATIVE_PREIMPORT_ENABLED")
        assert spec is not None
        assert spec.owner_module, "owner 不得为空（UI 要展示归属子系统）"
        assert (REPO_ROOT / spec.owner_module).is_file(), (
            f"owner 指向不存在的文件: {spec.owner_module}"
        )
        assert spec.owner_module == "agent/utils/native_preimport.py"

    def test_risk_is_b_conservative_because_disabling_lowers_the_guard(self):
        """风险级：B（拿不准取更严）。

        关闭后长寿命进程可能在**已加载重型原生库之后**被 arrow.dll 的原生初始化
        打成 0xC0000005（进程被系统直接终止、无 traceback）—— 与同表
        `CP_ARCHIVE_LOCK_ENABLED` 一族「关掉即失去完整性/存活强保护」同型。
        """
        from agent.settings import registry as R

        spec = R.get_spec("CP_NATIVE_PREIMPORT_ENABLED")
        assert spec is not None
        assert spec.risk == R.RISK_B, f"风险级应为 B（保守），实际 {spec.risk}"
        assert spec.needs_restart is True, "该开关在进程入口读取，必须标记需重启生效"

# ════════════════════════════════════════════════════════════
#  五、【TASK-03 加强】固化顺序 == **真实执行顺序**
# ════════════════════════════════════════════════════════════
#
#  Why 要在既有 `TestSharedImplementationIsUnique::test_shared_module_is_the_canonical_order`
#  之外再补一层：那一条断言的是**常量**（`NATIVE_IMPORT_ORDER`）和**调用点**，
#  它证明不了"预导入真的按这个次序发生"。常量对而执行顺序错，是完全可能的
#  （例如 pending 列表被排序、被 set 化、或 extra 被插到前面），而这类改动
#  恰恰会让 pyarrow 失去"干净窗口"——正是本防线要防的那件事。
#
#  既有 `TestTestsRootConftestSharesProtection::test_protection_already_applied_in_this_process`
#  只断言"四个模块都被导入过"，**不校验相对次序**（`sys.modules` 是集合语义，
#  本就无法反映先后）。故本类用 meta_path finder 在**干净子进程**里录制真实
#  的首次解析次序，把次序本身钉死。

#: 在干净子进程里录制"原生栈**首次**被解析"的顺序并执行预导入。
#: 用 meta_path finder 而不是 sys.addaudithook：实测本机 CPython 3.12 下
#: `import` 审计事件未触达（探针返回空列表），而 finder 稳定可观测。
_ORDER_RECORDER = """
import json, sys

STACK = ("numpy", "pyarrow", "pandas", "sklearn")
recorded = []


class _Recorder:
    # 只记录**首次**解析（已在 sys.modules 的模块不会再走 find_spec）
    def find_spec(self, name, path=None, target=None):
        if name in STACK and name not in recorded:
            recorded.append(name)
        return None


sys.meta_path.insert(0, _Recorder())

mode = sys.argv[1]
control = None
if mode == "preload_then_pin":
    # 负对照：故意让 pandas 抢在预导入**之前**被解析
    import pandas  # noqa: F401
    control = list(recorded)
    recorded.clear()

from agent.utils.native_preimport import pin_native_import_order

results = pin_native_import_order()
print(json.dumps({
    "recorded": recorded,
    "control": control,
    "results": {k: v for k, v in results.items()},
}))
"""


def _run_order_recorder(mode: str) -> dict:
    """在干净子进程里跑录制探针（父进程的 sys.modules 已被污染）。"""
    env = {k: v for k, v in os.environ.items() if k != "CP_NATIVE_PREIMPORT_ENABLED"}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", _ORDER_RECORDER, mode],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, f"录制探针失败 rc={proc.returncode}\n{proc.stderr}"
    last = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert last, f"录制探针无输出\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    return json.loads(last[-1])


class TestRealExecutionOrderMatchesTheDeclaredOrder:
    def test_stack_is_imported_in_exactly_the_declared_order(self):
        """固化顺序常量正确**且**它就是真实发生过的解析次序。"""
        got = _run_order_recorder("clean")
        # 反"空报告"守卫：录制器没观测到任何东西时，下面的断言会**空过**
        assert len(got["recorded"]) == len(npi.NATIVE_IMPORT_ORDER), (
            f"录制到 {got['recorded']}，期望覆盖完整固化栈；"
            "录制器可能已失效（判定将变成假绿灯）"
        )
        assert got["recorded"] == list(npi.NATIVE_IMPORT_ORDER), (
            f"真实解析顺序 {got['recorded']} != 固化顺序 {list(npi.NATIVE_IMPORT_ORDER)}"
        )
        # pyarrow 必须紧跟 numpy —— 这是"干净窗口"的核心，不是可调项
        assert got["recorded"].index("pyarrow") == 1

    def test_recorder_actually_observes_imports(self):
        """负对照：让 pandas 先被解析 ⇒ 录制结果**必须**随之改变。

        没有这条，"录制器恒返回固化顺序"这种假绿灯无法被区分出来。
        """
        got = _run_order_recorder("preload_then_pin")
        # 实测：`import pandas` 自身会连带解析 numpy 与 pyarrow
        # （control 实测 = ['pandas', 'numpy', 'pyarrow']）—— **这正是本防线
        #   存在的理由**：pandas 的导入链里有 pyarrow，谁先谁后就决定了
        #   arrow.dll 的原生初始化发生在什么时刻。
        # 故负对照的判据取"pandas 排在 numpy 之前"，而不是"control 恰为一项"。
        assert got["control"], "负对照未录制到任何项，说明录制器已失效"
        assert got["control"][0] == "pandas", (
            f"负对照失败：抢先导入 pandas 后录制到 {got['control']}，"
            "首个不是 pandas，说明录制器并未真的按发生顺序观测 import"
        )
        assert got["control"].index("pandas") < got["control"].index("numpy"), (
            f"负对照失败：pandas 未排在 numpy 之前（实际 {got['control']}）"
        )
        # 【本防线生效的机理，逐字可验】负对照阶段已解析的成员进了 sys.modules
        # ⇒ 预导入只需补齐**剩下**的，且补齐时仍严格遵循固化顺序。
        # 断言不写死"剩下哪些"（各机 pandas 的导入链可能略有差异），
        # 而是用**同一次运行观测到的 control** 反推期望值：
        already = set(got["control"])
        expected = [m for m in npi.NATIVE_IMPORT_ORDER if m not in already]
        assert got["recorded"] == expected, (
            f"补齐阶段的解析顺序 {got['recorded']} != 固化顺序扣掉已解析成员 "
            f"{expected}（已解析 {sorted(already)}）"
        )
        # 已被解析的成员**不得**再被记为"首次解析" —— 这正是"崩溃路径不可达"
        # 的机械表达：pyarrow 一旦先完成原生初始化，后续 import 命中 sys.modules。
        assert already.isdisjoint(got["recorded"])

