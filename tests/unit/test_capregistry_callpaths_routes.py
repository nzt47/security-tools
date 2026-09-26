"""调用路径普查 + 非 LLM 入口路由（TASK-05 E1b / E3 / E6 / E7）

`E1b` 的硬要求（`TASK-05` §5）：

> - 存在 `scripts/audit_call_paths.py`，输出**全部**能触发能力执行的路径及其
>   "是否过闸门 / 是否带身份"**以及"当前是否可达"**
> - **4 处已定位的直调全部处置** …
> - `--check` 模式在发现**未登记的直调**时**非零退出**（**必须实测这个负例**）

本文件把"负例"做成**可自动执行的用例**：直接调 `audit_call_paths.main()`，
不用起子进程（避免 `TASK-00` §0.2d 第 4 类的管道坑）。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.capregistry.call_sites import (  # noqa: E402
    DEAD_MODULES, EXEMPT_CALL_SITES, VIOLATION_SCOPE_PREFIXES, anchor_key,
    reachability_of)

# ════════════════════════════════════════════════════════════
#  超时预算：显式给出，不依赖全局默认（pytest.ini 的明文要求）
# ════════════════════════════════════════════════════════════
# 【为什么本文件要显式给 300s】本文件的成本是**全仓 AST 扫描**：
#   - 本文件自己的 docstring 记载：`scan()` 单次 **17.7s**、同进程第二次仍 18.7s（无缓存时），
#     而本文件有 1 个 module 级 scan 夹具 + 5 次 `main(["--check"])` ⇒ **单进程 6 次全仓 AST ≈ 108s**；
#   - pytest.ini 的 addopts 早已把全局默认从 60s 提到 120s，并**明文要求**：
#     "极慢测试应显式 @pytest.mark.timeout(N) 覆盖，不要依赖全局默认"；
#   - 而 CI 的分片命令又用命令行 `--timeout=60` 覆盖了 ini（命令行优先于 ini），
#     2 核 runner + `-n 2 --dist=loadscope` 下本文件必然击穿 60s ⇒ 实测 CI 连续三轮
#     出现 `Failed: Timeout (>60.0s) from pytest-timeout`（同一文件 9 条用例）。
# 【这不改任何断言】：只把**预算**按实测成本显式化，与 test_concurrency_multi_writer.py
#   既有的 `@pytest.mark.timeout(300)` 同款做法。
pytestmark = pytest.mark.timeout(300)


def _load_audit_module():
    """按路径加载 `scripts/audit_call_paths.py`（它不在包内，不能直接 import）"""
    path = _ROOT / "scripts" / "audit_call_paths.py"
    spec = importlib.util.spec_from_file_location("_audit_call_paths_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit():
    return _load_audit_module()


@pytest.fixture(scope="module")
def findings(audit):
    return audit.scan()


# ════════════════════════════════════════════════════════════
#  清单覆盖与分类
# ════════════════════════════════════════════════════════════


class TestCallPathInventory:
    def test_清单非空且四类都在(self, findings):
        kinds = {f.path_kind for f in findings}
        assert "funnel" in kinds, "必须能看见收口路径"
        assert "direct" in kinds or "remote_primitive" in kinds, \
            "必须能看见直调路径"
        assert len(findings) > 0

    def test_每条都带四要素(self, findings, audit):
        """`TASK-05` E1b 要求每条输出：是否过闸门 / 是否带身份 / 是否可达 / 是否直调"""
        for f in findings:
            d = f.to_dict()
            for key in ("via_registry", "via_gate", "has_identity", "reachable",
                        "reachability", "path_kind", "trigger", "file", "symbol",
                        "anchor"):
                assert key in d, f"{f.file}:{f.lineno} 缺少字段 {key}"
            assert str(f.reachability).strip(), "可达性必须给出依据而不是空串"

    def test_四处已知缺口全部被看见(self, findings):
        """★ E1b：`TASK-05` §2.3c 的 4 处已定位直调**必须在清单里**

        锚点用 `路径::符号名`（**不用行号** —— 行号会漂移，`ci.yml:562` 就是前车之鉴）。
        """
        anchors = {f.anchor for f in findings}
        required = [
            # ① CI 面的知识库审计 CLI（`ci.yml` 的 knowledge-audit-smoke job 触发）
            "agent/knowledge/__main__.py::cmd_audit",
            # ② 后台任务执行器（**实测更正：过闸门但缺身份**）
            "agent/async_executor.py::_run_task",
            # ③ MCP 服务端 tools/call（**实测更正：过闸门，协议层无身份**）
            "mcp_services/yunshu_mcp_server.py::_handle_tools_call",
            # ④ 技能 MCP 适配器的远程直调（潜在风险，当前不可达）
            "agent/skills_mgmt/mcp_adapter.py::_call_tool",
        ]
        missing = [a for a in required if a not in anchors]
        assert missing == [], f"清单漏掉了已知缺口：{missing}"

    def test_当前不可达的直调与可达的直调被区分(self, findings):
        """★ `TASK-05` §2.3c 明令：**不要把"当前不可达"算作现实缺口**

        可达性判据是**实测**的（`mcp` SDK 是否可导入 / 死模块是否有 importer），
        不是硬编码 —— 硬编码会在 SDK 装上后静默过期。
        """
        p3 = [f for f in findings if f.path_kind == "remote_primitive"]
        mcp_adapter = [f for f in p3
                       if f.file == "agent/skills_mgmt/mcp_adapter.py"]
        assert mcp_adapter, "mcp_adapter 的远程直调必须在清单里"
        assert mcp_adapter[0].reachable is False, \
            "官方 mcp SDK 未安装 ⇒ 该路径当前不可达（实测判据）"
        assert "装上" in mcp_adapter[0].reachability, \
            "可达性说明必须写明'一旦装上即生效'"

    def test_死模块被标为不可达(self, findings):
        """死模块的判据是"**生产范围内零调用方**"，不是字面上的"零 importer"

        （`agent/mcp_executor.py` 实测被 `scripts/check_mcp_log_level.py` import，
        故"零 importer"会得出错误结论 —— 见 `_reachability` 的口径修正注释。）
        """
        seen = False
        for f in findings:
            if f.file in DEAD_MODULES:
                seen = True
                assert f.reachable is False, f"{f.file} 是死模块，应判不可达"
                assert "零调用方" in f.reachability, \
                    f"可达性依据必须写明'生产代码零调用方'，实际：{f.reachability}"
        assert seen, "死模块的调用点应当在清单里被看见"


# ════════════════════════════════════════════════════════════
#  `--check`：**必须实测负例**
# ════════════════════════════════════════════════════════════


class TestCheckMode:
    def test_正例_当前仓库通过_check(self, audit):
        rc = audit.main(["--check"])
        assert rc == 0, "当前仓库应当没有未登记的直调（否则就是本任务没做完）"

    def test_负例_未登记的直调_非零退出(self, audit, monkeypatch):
        """★★ E1b 的硬要求：**实测这个负例**

        做法：从 `EXEMPT_CALL_SITES` 里删掉**一条真实的直调登记**
        （`agent/skills_mgmt/mcp_adapter.py::_build` —— 它确实被 AST 扫描器
        以 `remote_primitive` 命中且在硬失败范围内），等价于"这条直调没登记"。
        `--check` 必须**非零退出**。

        【为什么不删 `cmd_audit` 那条】`cmd_audit` 是**经符号存在性**兜底登记的
        （AST 启发式抓不到"经 CLI 间接触发"），删掉它只会让清单少一条、
        **不会**产生"未登记直调" ⇒ 那样测不到退出码逻辑。
        负例必须打在**真的会被扫出来的**直调上。

        【2026-09-21 修一处锚点漂移 —— 本负例曾**静默失效**】
        原受害条目是 `agent/skills_mgmt/mcp_adapter.py::_call_tool`。
        TASK-08 给 MCP 调用加超时/重试预算时，把 SDK 调用路径重构成了
        `_build()` 内层闭包（`mcp_adapter.py:372`）⇒ **`_call_tool` 这个符号不再存在**。
        于是本用例变成"删掉一个**本就不存在的**条目" ⇒ 未登记数仍为 0 ⇒
        **负例测不出非零退出，静默失效**（实测 `assert 0 == 1`）。
        改用当前**真实存在且会被扫到**的 `_build` 作为受害条目。
        ⇒ **教训：负例必须锚在"扫描器当前真能命中的符号"上；重构会让负例静默失效。**
        """
        victim = "agent/skills_mgmt/mcp_adapter.py::_build"
        assert victim in EXEMPT_CALL_SITES, "测试前提：该例外必须存在"
        patched = {k: v for k, v in EXEMPT_CALL_SITES.items() if k != victim}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        rc = audit.main(["--check"])
        assert rc == 1, "删掉例外登记后 --check 必须非零退出"

    def test_负例_白名单腐化_非零退出(self, audit, monkeypatch):
        """★ 防"什么都放行"：登记了但**代码里找不到对应符号** ⇒ 非零退出

        这条不变量是 `EXEMPT_CALL_SITES` 不腐化成空转声明的结构保证。
        """
        patched = dict(EXEMPT_CALL_SITES)
        patched["agent/knowledge/__main__.py::definitely_no_such_symbol"] = {
            "reason": "故意造一个腐化条目", "identity": "x", "audit": "x"}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        rc = audit.main(["--check"])
        assert rc == 1, "白名单腐化必须非零退出"

    def test_负例_虚假登记不产生额外例外(self, audit, monkeypatch):
        """把**真实存在**的直调登记成例外 ⇒ `--check` 通过（证明例外机制真的在生效）"""
        patched = dict(EXEMPT_CALL_SITES)
        patched["agent/knowledge/tools.py::kb_lint"] = {
            "reason": "测试用：显式登记", "identity": "llm", "audit": "有"}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        assert audit.main(["--check"]) == 0


# ════════════════════════════════════════════════════════════
#  例外表本身的纪律
# ════════════════════════════════════════════════════════════


class TestExemptTableDiscipline:
    def test_每条例外都有理由_身份_审计说明(self):
        """`TASK-05` §5：例外必须**逐点、带理由、带身份**，不得宽泛放行"""
        for anchor, meta in EXEMPT_CALL_SITES.items():
            assert "::" in anchor, f"{anchor} 不是 `路径::符号名` 形态"
            for field in ("reason", "identity", "audit"):
                val = str(meta.get(field) or "").strip()
                assert val, f"{anchor} 的 {field} 为空（例外必须逐点说明）"
            assert not anchor.endswith("/*"), f"{anchor} 是宽泛模式（禁止）"
            assert "*" not in anchor, f"{anchor} 含通配符（禁止宽泛放行）"

    def test_理由不是敷衍话术(self):
        """理由必须**具体**：含"为什么"，而不是"暂不处理"这类空话"""
        vague = ("暂时", "以后再", "TODO", "先这样", "无所谓", "都行")
        for anchor, meta in EXEMPT_CALL_SITES.items():
            reason = str(meta.get("reason") or "")
            assert len(reason) >= 30, f"{anchor} 的理由太短（{len(reason)} 字）"
            assert not any(v in reason for v in vague), \
                f"{anchor} 的理由含敷衍话术：{reason[:60]}"

    def test_硬失败范围是被显式声明的(self):
        assert VIOLATION_SCOPE_PREFIXES, "违规范围必须显式声明，不能含糊"
        assert all(p.endswith("/") for p in VIOLATION_SCOPE_PREFIXES)
        assert "agent/" in VIOLATION_SCOPE_PREFIXES

    def test_anchor_key_归一化(self):
        assert anchor_key("agent\\x.py", "A::b") == "agent/x.py::b"
        assert anchor_key("./agent/x.py", "b") == "agent/x.py::b"
        assert anchor_key("agent/x.py", "") == "agent/x.py::<module>"

    def test_reachability_of_对远程原语实测_sdk(self):
        reach, why = reachability_of("x.py", "y", "session.call_tool",
                                    "remote_primitive")
        try:
            import mcp  # noqa: F401
            assert reach is True
        except ImportError:
            assert reach is False
        assert why.strip()


# ════════════════════════════════════════════════════════════
#  进程级扫描缓存（【不易·2026-09-21】遗留 L3 的另一半）
# ════════════════════════════════════════════════════════════
#
# 为什么要缓存：实测 `scan()` 单次 17.7s、**同进程第二次仍 18.7s**（无缓存），
#   而本文件有 1 个 module 级 `scan()` 夹具 + 5 次 `main(["--check"])`
#   ⇒ 单进程 6 次全仓 AST ≈ 108s，4 路并行争用下击穿 `--timeout=120`
#   ⇒ 两次全量回归都在 chunk_0/chunk_3 出现 `Timeout`（`os._exit(1)`），
#      该块其余文件**从未执行**。
#
# 本节锁定两件事，缺一不可：
#   ① **命中**：同一仓库状态重复 scan 必须复用（否则加缓存等于没加）；
#   ② **失效**：仓库内容/文件集合/例外表变化后必须重算 ——
#      缓存给出**过期但看起来正常**的门禁结论，比超时更危险（超时至少报 error）。


def _write_probe(root: Path, body: str) -> Path:
    """在合成仓库根下写一个探针文件（路径固定，便于反复改写以触发指纹变化）"""
    pkg = root / "agent"
    pkg.mkdir(parents=True, exist_ok=True)
    target = pkg / "probe_synthetic.py"
    target.write_text(body, encoding="utf-8")
    return target


_PROBE_V1 = (
    "from agent.tools import call\n"
    "\n"
    "\n"
    "def handler_alpha():\n"
    '    call("cap_alpha")\n'
)
_PROBE_V2 = _PROBE_V1 + (
    "\n"
    "\n"
    "def handler_beta():\n"
    '    call("cap_beta")\n'
)


class TestParseCache:
    """`_parse` 的 AST 缓存：消除同一文件的重复解析 + 内容变化必须失效

    【为什么单独立一组（2026-09-21）】实测一次冷扫描对 1396 个受控文件触发
    **2802 次 `_parse`（2.01× 重复，其中 5 个文件被解析 4 次）** —— 调用点是
    `_collect_findings` 主循环 + `_symbol_exists` / `_symbol_lineno`（按 finding 调用）。
    `scan()` 的进程级缓存只覆盖"整轮扫描"这一层，**管不到轮内的重复解析**。
    """

    @pytest.fixture
    def synthetic(self, audit, tmp_path, monkeypatch):
        """把受控文件列表指向合成根（不碰仓库真实文件）"""
        monkeypatch.setattr(audit, "tracked_python_files",
                            lambda root: ["agent/probe_synthetic.py"])
        # 【2026-09-24 · L6】跨进程产物目录钉到本用例的 tmp_path：
        #   合成根的指纹本来就每次运行都不同，但显式隔离后
        #   ① 测试产物不会落进共享临时目录，②"冷扫 / 热扫"两个断言确定可复现。
        monkeypatch.setenv(audit._DISK_CACHE_DIR_ENV, str(tmp_path / "scan_cache"))
        return tmp_path

    def test_同一文件重复解析被缓存消除(self, audit, tmp_path):
        """★ 核心不变量：同一文件（内容未变）第二次解析必须命中缓存

        【为什么直接测 `_parse` 而不只测 `scan()`】合成探针不产生 finding ⇒
        `_symbol_lineno`（按 finding 调用 `_parse`）根本不会被执行，**轮内重复解析
        在合成夹具上复现不出来**。首版用例只断言"第二次 scan 的 miss 不增"，
        结果**禁用缓存后依然全绿**（变异探针实测）—— 即它锁不住目标。
        真实现象（1396 文件 → 2802 次 `_parse`）必须在 `_parse` 这一层锁：
        直接对同一个未缓存文件解析两次，断言第二次不读盘。
        """
        target = tmp_path / "probe_same.py"
        target.write_text("x = 1\n", encoding="utf-8")

        audit.parse_cache_clear()
        first = audit._parse(str(tmp_path), "probe_same.py")
        assert first is not None, "首次解析失败，夹具有问题"
        base = audit.parse_cache_info()
        assert base["miss"] >= 1 and base["entries"] >= 1, f"首次未入缓存：{base}"

        second = audit._parse(str(tmp_path), "probe_same.py")
        after = audit.parse_cache_info()
        assert second is first, (
            "同一文件第二次解析返回了不同对象 ⇒ 走了重新解析（缓存未生效）；"
            f"统计 {base} → {after}"
        )
        assert after["miss"] == base["miss"], (
            f"第二次解析仍读了盘（miss {base['miss']} → {after['miss']}）⇒ AST 缓存未生效"
        )

    def test_文件集合扫描不产生轮内重复解析(self, audit, synthetic, monkeypatch):
        """同一次 `scan()` 内，同一文件**只应读盘一次**

        【口径演进·L19（2026-09-21）】原来轮内有**两个解析入口**
        （`collect_registered_handlers` 与主循环）⇒ 判据只能退而求其次地写
        "**命中了**（`hit >= 1`）"。L19 给 `collect_registered_handlers` 加了
        **源码字节预筛**（源码里没有 `register` 字面量的文件不可能注册 handler）
        ⇒ 本探针（`_PROBE_V1` 里没有 `register`）**只剩主循环一个入口**，
        于是旧的 `hit >= 1` **不再锁得住它想锁的东西**（命中可能来自仓库里别的文件，
        实测就是如此）⇒ 改成**直接数该文件被 `_parse` 了几次**：恰好 1 次。
        既不是 0（漏扫），也不是 ≥2（重复读盘）——口径比原来更强、也更直白。
        """
        _write_probe(synthetic, _PROBE_V1)
        reads: List[str] = []
        real_parse = audit._parse

        def counting_parse(root: str, rel: str):
            reads.append(rel)
            return real_parse(root, rel)

        monkeypatch.setattr(audit, "_parse", counting_parse)
        audit.parse_cache_clear()
        audit.scan_cache_clear()
        audit.scan(str(synthetic))
        probe_reads = [r for r in reads if r.endswith("probe_synthetic.py")]
        assert probe_reads == ["agent/probe_synthetic.py"], (
            f"同一次 scan() 内探针文件被解析了 {len(probe_reads)} 次（应为 1 次）："
            f"{probe_reads}"
        )
        info = audit.parse_cache_info()
        assert info["entries"] >= 1, f"解析结果未入缓存：{info}"

    def test_文件内容变化后_AST_缓存失效(self, audit, synthetic):
        """★ 缓存键含 mtime_ns+size ⇒ 改了文件必须重新解析（否则 `--check` 给出过期结论）"""
        _write_probe(synthetic, _PROBE_V1)
        audit.parse_cache_clear()
        audit.scan(str(synthetic))
        before = audit.parse_cache_info()["miss"]

        # 改内容（size 与 mtime_ns 至少变一个）⇒ 必须重新解析该文件
        _write_probe(synthetic, _PROBE_V2)
        got = {f.capability for f in audit.scan(str(synthetic))}
        after = audit.parse_cache_info()["miss"]
        assert got == {"cap_alpha", "cap_beta"}, (
            f"文件内容变化后结论未更新 ⇒ AST 缓存未失效：{got}"
        )
        assert after > before, (
            f"文件已改动但未重新解析（miss 仍为 {before}）⇒ 缓存键未含文件指纹"
        )


def test_身份判定快路径与旧口径等价(audit):
    """L19：`_identity_leaves` 是 `_scope_has_identity` 的**等价**快路径

    【为什么要锁】L19 把"每个收口调用点走一遍整棵树"改成"每个文件算一次身份表"
    （见 `_identity_leaves` 的说明）。这类"性能改写"最容易悄悄改口径 ——
    所以直接把两条路径在同一棵树上对拍：**逐个符号断言结论一致**。
    """
    tree = ast.parse(
        "def handler_with_id():\n"
        "    call('cap_a', session_source='cli')\n"
        "\n"
        "\n"
        "def handler_without_id():\n"
        "    call('cap_b')\n"
        "\n"
        "\n"
        "def handler_calls_marker():\n"
        "    set_session_source('cli')\n")
    sym = audit._SymbolIndex()
    sym.visit(tree)
    fast = audit._identity_leaves(tree, sym)
    for leaf in ("handler_with_id", "handler_without_id", "handler_calls_marker",
                 "<module>"):
        slow = audit._scope_has_identity(tree, sym, leaf)
        assert (leaf in fast) == slow, (
            f"{leaf}: 快路径判 {leaf in fast}，旧口径判 {slow} ⇒ 改写改变了口径"
        )
    assert "handler_with_id" in fast and "handler_without_id" not in fast, \
        f"身份表本身不对：{fast}"


class TestScanCache:
    """`audit_call_paths.scan()` 的进程级缓存：命中 + 失效"""

    @pytest.fixture
    def synthetic(self, audit, tmp_path, monkeypatch):
        """把受控文件列表指向合成根（避免用 monkeypatch 改仓库真实文件，见 D15）"""
        monkeypatch.setattr(audit, "tracked_python_files",
                            lambda root: ["agent/probe_synthetic.py"])
        # 【2026-09-24 · L6】同上：合成根用例把跨进程产物目录钉到 tmp_path
        monkeypatch.setenv(audit._DISK_CACHE_DIR_ENV, str(tmp_path / "scan_cache"))
        return tmp_path

    def test_重复_scan_命中缓存且结论一致(self, audit, findings):
        """同进程第二次 scan 必须复用（这正是 108s → ~22s 的来源）"""
        audit.scan()                     # 先确保存在缓存条目（本用例内首次也无妨）
        before = audit.scan_cache_info()
        again = audit.scan()
        after = audit.scan_cache_info()
        assert after["hit"] == before["hit"] + 1, (
            f"重复 scan 未命中缓存：{before} → {after}（缓存形同虚设）"
        )
        assert [f.to_dict() for f in again] == [f.to_dict() for f in findings], \
            "命中缓存时返回的结论与首次扫描不一致"

    def test_缓存可在同进程内显式清空(self, audit):
        audit.scan()
        audit.scan_cache_clear()
        assert audit.scan_cache_info()["entries"] == 0

    def test_文件内容变化后缓存失效(self, audit, synthetic):
        """★ 核心不变量：仓库内容变了，缓存**必须**失效（否则 `--check` 给出过期结论）"""
        _write_probe(synthetic, _PROBE_V1)
        first = {f.capability for f in audit.scan(str(synthetic))}
        assert first == {"cap_alpha"}, f"合成夹具未被扫到：{first}"

        # 同状态再扫一次 ⇒ 应当命中缓存
        before = audit.scan_cache_info()
        assert {f.capability for f in audit.scan(str(synthetic))} == first
        assert audit.scan_cache_info()["hit"] == before["hit"] + 1

        # 改内容（size 与 mtime_ns 至少变一个）⇒ 必须重算
        _write_probe(synthetic, _PROBE_V2)
        second = {f.capability for f in audit.scan(str(synthetic))}
        assert second == {"cap_alpha", "cap_beta"}, (
            f"文件内容变化后仍返回旧结论 ⇒ 缓存未失效：{second}"
        )

    def test_文件集合变化后缓存失效(self, audit, synthetic, monkeypatch):
        """新增/删除受控文件同样必须失效（指纹含文件集合，不只含单个文件的状态）"""
        _write_probe(synthetic, _PROBE_V1)
        assert {f.capability for f in audit.scan(str(synthetic))} == {"cap_alpha"}

        (synthetic / "agent" / "probe_second.py").write_text(
            "from agent.tools import call\n"
            "\n"
            "\n"
            "def handler_gamma():\n"
            '    call("cap_gamma")\n',
            encoding="utf-8")
        monkeypatch.setattr(
            audit, "tracked_python_files",
            lambda root: ["agent/probe_synthetic.py", "agent/probe_second.py"])
        got = {f.capability for f in audit.scan(str(synthetic))}
        assert got == {"cap_alpha", "cap_gamma"}, f"文件集合变化未失效缓存：{got}"

    def test_例外表变化后不返回过期结论(self, audit, synthetic, monkeypatch):
        """★ **最关键**：例外表（运行期可变的模块级全局）变了，缓存**不得**给出旧结论

        测试正是用 monkeypatch 改 `EXEMPT_CALL_SITES` 来造负例；若缓存把
        "是否豁免"一起缓存了，那些负例会**静默失效**（本仓已发生过一次，见
        `TestCheckMode::test_负例_未登记的直调_非零退出` 的注释）。
        实现上靠"前半段可缓存、配置相关的收尾每次都跑"来保证，本用例锁定该性质。
        """
        _write_probe(synthetic, _PROBE_V1)
        anchor = "agent/probe_synthetic.py::handler_alpha"
        assert not [f for f in audit.scan(str(synthetic)) if f.anchor == anchor and f.exempt]

        patched = dict(EXEMPT_CALL_SITES)
        patched[anchor] = {"reason": "测试用：显式登记探针直调", "identity": "llm",
                           "audit": "有"}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        # 注意：**不**清缓存 —— 正是要验证"配置变了也能立即生效"
        hit = [f for f in audit.scan(str(synthetic)) if f.anchor == anchor and f.exempt]
        assert hit, "改例外表后仍返回旧的豁免结论 ⇒ 缓存覆盖了配置相关判定（负例会静默失效）"

    def test_返回副本_调用方改写不污染缓存(self, audit, synthetic):
        """`_apply_policy` 会就地改写 Finding 字段 ⇒ 必须返回副本，否则缓存会被污染"""
        _write_probe(synthetic, _PROBE_V1)
        first = audit.scan(str(synthetic))
        first[0].reachable = False
        first[0].reachability = "被测试改坏"
        again = audit.scan(str(synthetic))
        assert again[0].reachability != "被测试改坏", "缓存被调用方改写污染了"


# ════════════════════════════════════════════════════════════
#  非 LLM 入口路由（E1 / E6 / E7 的 HTTP 侧）
# ════════════════════════════════════════════════════════════


@pytest.fixture
def cap_client(monkeypatch):
    """只挂能力层路由的 Flask 测试客户端（真实 WSGI 请求）"""
    from flask import Flask
    from agent.server_routes.routes_capabilities import register_routes
    app = Flask("caproutes_test")
    register_routes(app, lambda: None)
    return app.test_client()


class TestCapabilityRoutes:
    def test_开关关闭时端点不存在(self, monkeypatch, audit):
        """回滚方案：`CP_CAPABILITY_API_ENABLED=0` ⇒ `/capabilities/*` 全部 404"""
        from flask import Flask
        from agent.server_routes.routes_capabilities import register_routes
        monkeypatch.setenv("CP_CAPABILITY_API_ENABLED", "0")
        app = Flask("caproutes_off")
        register_routes(app, lambda: None)
        c = app.test_client()
        assert c.get("/capabilities/tools").status_code == 404
        assert c.post("/capabilities/invoke", json={"name": "x"}).status_code == 404
        assert c.get("/capabilities/health").status_code == 404

    def test_get_tools_返回全量清单与裁剪字段(self, cap_client):
        r = cap_client.get("/capabilities/tools?limit=2000")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        # 【G1-C 2026-09-26】114 → 119（H-3 迁移的 5 条技能进入能力面）
        assert body["data"]["total"] == 119
        assert body["data"]["returned"] == 119
        item = body["data"]["items"][0]
        for key in ("name", "kind", "location", "owner", "callable_by",
                    "impl_status", "capability_id", "tenant_id"):
            assert key in item

    def test_默认分页不静默截断(self, cap_client):
        """默认分页 + `truncated` 明示（10k 压测逼出来的设计）"""
        body = cap_client.get("/capabilities/tools").get_json()
        assert body["data"]["limit"] == 500
        assert body["data"]["limit_source"] == "default_page"
        assert "truncated" in body["data"]

    def test_模型能力裁剪(self, cap_client):
        body = cap_client.get("/capabilities/tools?model=none").get_json()
        assert body["data"]["model_capability"]["supports_tool_calling"] is False
        assert body["data"]["returned"] == 0
        assert body["data"]["total"] == 119
        body2 = cap_client.get("/capabilities/tools?model=deepseek-chat").get_json()
        assert body2["data"]["returned"] > 0

    def test_调度双工具面可区分(self, cap_client):
        """★ 交付物 #15：`/capabilities/tools` 能看出哪个 `schedule_task` 未实现"""
        body = cap_client.get("/capabilities/tools?q=schedule_task&limit=2000").get_json()
        items = {i["name"]: i for i in body["data"]["items"]}
        assert "schedule_task" in items
        assert items["schedule_task"]["impl_status"] == "not_implemented"
        assert items["schedule_task"]["impl_status_reason"], "必须附上证据"

    def test_invoke_缺参数归_validation_error(self, cap_client):
        assert cap_client.post("/capabilities/invoke", json={}).status_code == 400
        assert cap_client.post("/capabilities/invoke",
                               json={"name": "x", "args": []}).status_code == 400

    def test_invoke_未知能力归_not_found(self, cap_client):
        r = cap_client.post("/capabilities/invoke", json={"name": "__nope__"})
        assert r.status_code == 404
        assert r.get_json()["code"] == "not_found"

    def test_describe_与_health(self, cap_client):
        r = cap_client.get("/capabilities/json_query")
        assert r.status_code == 200
        assert r.get_json()["data"]["name"] == "json_query"
        assert cap_client.get("/capabilities/__nope__").status_code == 404
        h = cap_client.get("/capabilities/health")
        assert h.status_code == 200
        assert h.get_json()["data"]["registry"]["total"] == 119

    def test_异常不外泄为_html_或原文(self, cap_client, monkeypatch):
        """★ 铁律：任何异常都不能把 HTML / 原文喂出去

        做法：让真实 Registry 的 `list_envelope` 抛异常 —— 路由的 catch-all
        必须把它折叠成 `internal_error` + 固定描述，且**不得**回显原文。
        （不能 patch 模块级名字：`_registry()` 里的导入在 `register_routes`
        时就绑成了闭包，patch 模块属性无效 —— 实测踩到 AttributeError。）
        """
        from agent.capregistry.view import CapabilityRegistry

        def _boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("C:\\Users\\secret\\path 内部错误 <html>502</html>")

        monkeypatch.setattr(CapabilityRegistry, "list_envelope", _boom)
        r = cap_client.get("/capabilities/tools?limit=2000")
        assert r.status_code == 500
        body = r.get_json()
        blob = json.dumps(body, ensure_ascii=False)
        assert "C:\\Users" not in blob
        assert "secret" not in blob
        assert "<html>" not in blob
        assert body["code"] == "internal_error"
        assert body["error"]["message"] == \
            "内部错误，已脱敏；请查看服务端日志定位"


# ════════════════════════════════════════════════════════════
#  跨进程扫描产物（2026-09-24 · L6 根治 CI 负载敏感）
# ════════════════════════════════════════════════════════════
# 【为什么要有这一组】CI 跑的是 `-n 2 --dist=loadscope`，而 xdist 的 loadscope 对
#   **带类的测试模块**是**按类分组**的 ⇒ 同一个测试文件的各个类会落到**不同 worker 进程**，
#   每个进程各付一次全仓扫描。2026-09-22 那轮 CI（job 106915652550）的日志里：
#   gw0 与 gw1 **同时**卡在 `_collect_findings` 里被 `Timeout (>300.0s)` 打断，
#   而**被中断的扫描不会留下任何缓存** ⇒ gw0 的 `TestCheckMode` 四条用例各自又从零冷扫
#   ⇒ 单文件 6 次全仓 AST、约 20 分钟。
#   本组锁定四件事：① 另一个进程（= 清空进程内缓存）能直接复用产物；
#   ② 文件内容 / 文件集合变化必须失效；③ 例外表变化**不得**被产物冻住；
#   ④ 产物损坏或关开关 ⇒ 退回重扫，绝不给出过期结论。


class TestCrossProcessScanArtifact:
    """`scan()` 的**跨进程磁盘产物**：命中 + 失效 + 不冻结配置相关判定"""

    @staticmethod
    def _require_disk_cache(audit):
        """产物层被关掉时跳过本用例（`CP_AUDIT_SCAN_DISK_CACHE=0` 是合法的回退开关）

        【为什么要显式跳过】关掉产物后"跨进程复用"这件事**本来就不存在**，
        断言必然失败 —— 那是把"开关生效"误报成"功能坏了"。回退开关的有效性由
        `test_关闭开关时不写产物` 单独锁定。
        """
        if not audit._disk_cache_dir():
            pytest.skip("跨进程产物已关闭（CP_AUDIT_SCAN_DISK_CACHE=0）")

    @pytest.fixture
    def repo_like(self, audit, tmp_path, monkeypatch):
        """合成仓库根 + 隔离的产物目录（产物目录钉在 tmp_path，不污染共享临时目录）"""
        monkeypatch.setenv(audit._DISK_CACHE_DIR_ENV, str(tmp_path / "scan_cache"))
        monkeypatch.setattr(audit, "tracked_python_files",
                            lambda root: ["agent/probe_synthetic.py"])
        audit.scan_cache_clear()
        audit.parse_cache_clear()
        return tmp_path

    def test_另一个进程直接复用产物_不再冷扫(self, audit, repo_like, monkeypatch):
        """★ 核心不变量：清空进程内缓存（= 换一个 worker 进程）后必须命中磁盘产物

        这就是"6 次全仓 AST → 1 次"的机制本身。没有它，CI 上每个 worker、以及每次
        被超时打断后的重试都要从零扫一遍（实测那轮 CI 因此多花约 20 分钟）。
        """
        self._require_disk_cache(audit)
        _write_probe(repo_like, _PROBE_V1)
        first = {f.capability for f in audit.scan(str(repo_like))}
        assert first == {"cap_alpha"}, f"合成夹具未被扫到：{first}"

        audit.scan_cache_clear()            # 模拟"另一个 worker 进程"的空缓存
        calls: List[str] = []
        real_collect = audit._collect_findings

        def counting_collect(root, files):
            calls.append(root)
            return real_collect(root, files)

        monkeypatch.setattr(audit, "_collect_findings", counting_collect)
        again = {f.capability for f in audit.scan(str(repo_like))}
        assert again == first, "跨进程复用拿到的结论与首次不一致"
        assert calls == [], (
            "换了进程仍然重新冷扫 ⇒ 磁盘产物没生效（CI 上这正是 6 次全仓 AST 的来源）"
        )
        assert audit.scan_cache_info()["disk_hit"] >= 1

    def test_文件内容变化后产物必须失效(self, audit, repo_like):
        """改了文件还复用产物 = 过期门禁结论（比超时更危险），必须重扫"""
        _write_probe(repo_like, _PROBE_V1)
        assert {f.capability for f in audit.scan(str(repo_like))} == {"cap_alpha"}
        audit.scan_cache_clear()
        assert {f.capability for f in audit.scan(str(repo_like))} == {"cap_alpha"}

        _write_probe(repo_like, _PROBE_V2)   # 内容变了（mtime_ns/size 至少变一个）
        got = {f.capability for f in audit.scan(str(repo_like))}
        assert got == {"cap_alpha", "cap_beta"}, f"改了文件仍返回旧结论：{got}"

    def test_新增文件后产物必须失效(self, audit, repo_like, monkeypatch):
        """文件**集合**也是指纹的一部分：新增受控文件必须让产物作废"""
        _write_probe(repo_like, _PROBE_V1)
        assert {f.capability for f in audit.scan(str(repo_like))} == {"cap_alpha"}

        (repo_like / "agent" / "probe_second.py").write_text(
            "from agent.tools import call\n"
            "\n"
            "\n"
            "def handler_gamma():\n"
            '    call("cap_gamma")\n',
            encoding="utf-8")
        monkeypatch.setattr(
            audit, "tracked_python_files",
            lambda root: ["agent/probe_synthetic.py", "agent/probe_second.py"])
        got = {f.capability for f in audit.scan(str(repo_like))}
        assert got == {"cap_alpha", "cap_gamma"}, f"新增文件后仍返回旧结论：{got}"

    def test_例外表变化不被产物冻住(self, audit, repo_like, monkeypatch):
        """★★ 最关键：产物里**只有** `_collect_findings` 的输出，配置相关判定每次重算

        测试正是用 monkeypatch 改 `EXEMPT_CALL_SITES` 造负例；若产物把"是否豁免"
        一起冻住，那些负例会**静默失效**（本仓已发生过一次）。
        这里刻意先让产物命中（清掉进程内缓存），再改例外表 —— 结论必须立刻变。
        """
        _write_probe(repo_like, _PROBE_V1)
        anchor = "agent/probe_synthetic.py::handler_alpha"
        assert not [f for f in audit.scan(str(repo_like))
                    if f.anchor == anchor and f.exempt]

        audit.scan_cache_clear()             # 下一次访问只可能命中磁盘产物
        patched = dict(EXEMPT_CALL_SITES)
        patched[anchor] = {"reason": "测试用：显式登记探针直调条目", "identity": "llm",
                           "audit": "有"}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        hit = [f for f in audit.scan(str(repo_like))
               if f.anchor == anchor and f.exempt]
        assert hit, "改例外表后仍返回旧的豁免结论 ⇒ 产物把配置相关判定也冻住了"

    def test_产物损坏按未命中处理(self, audit, repo_like):
        """坏产物（半截 JSON / 版本不符）必须**退回重扫**：不抛异常、不给空结论"""
        self._require_disk_cache(audit)
        _write_probe(repo_like, _PROBE_V1)
        assert {f.capability for f in audit.scan(str(repo_like))} == {"cap_alpha"}

        cache_dir = Path(audit.scan_cache_info()["disk_dir"])
        artifacts = sorted(cache_dir.glob("scan-*.json"))
        assert artifacts, "没有写出磁盘产物，则「损坏按未命中」这条不变量无从谈起"
        artifacts[0].write_text('{"format": 1, "findings": [{"trunc', encoding="utf-8")

        audit.scan_cache_clear()
        got = {f.capability for f in audit.scan(str(repo_like))}
        assert got == {"cap_alpha"}, f"坏产物没有退回重扫：{got}"
        assert audit.scan_cache_info()["disk_error"] >= 1

    def test_关闭开关时不写产物(self, audit, repo_like, monkeypatch):
        """`CP_AUDIT_SCAN_DISK_CACHE=0` 必须彻底关闭（回退开关）"""
        monkeypatch.setenv(audit._DISK_CACHE_OFF_ENV, "0")
        before = audit.scan_cache_info()["disk_write"]
        _write_probe(repo_like, _PROBE_V1)
        assert {f.capability for f in audit.scan(str(repo_like))} == {"cap_alpha"}
        assert audit.scan_cache_info()["disk_write"] == before, "关掉开关后仍在写产物"
        assert not list((repo_like / "scan_cache").glob("scan-*.json"))

    def test_默认产物目录走系统临时目录(self, audit, monkeypatch):
        """产物只落**系统临时目录**：绝不碰 data/、绝不写仓库里被跟踪的路径

        【注意口径】测试期 `tests/conftest.py` 的 `_safe_tmp_directory` 会把
        `tempfile.tempdir` 重定向到仓库内的 `.pytest_tmp/`（pytest 的临时区、
        已在 .gitignore 第 40 行，且 `git ls-files` 看不见）—— 所以"在仓库目录下"
        这件事在 pytest 里是**临时区**的正常表现，不是污染工作区。
        真正要钉住的是：① 默认目录 = `<tempfile.gettempdir()>/cp_audit_call_paths`；
        ② 路径里不出现 `data` 段（操作员实时状态目录绝不能被写）。
        """
        monkeypatch.delenv(audit._DISK_CACHE_DIR_ENV, raising=False)
        monkeypatch.delenv(audit._DISK_CACHE_OFF_ENV, raising=False)
        directory = audit._disk_cache_dir()
        assert directory, "默认应当给出产物目录"
        assert Path(directory) == Path(tempfile.gettempdir()) / audit._DISK_CACHE_DIRNAME, \
            f"默认产物目录必须走系统临时目录，实际：{directory}"
        assert "data" not in Path(directory).parts, f"产物目录含 data 段：{directory}"


# ════════════════════════════════════════════════════════════
#  字节预筛 / 单次遍历的**等价性**（2026-09-24 · L6 的成本改造）
# ════════════════════════════════════════════════════════════
# 这两项都是"**只降成本、不改判据**"的性能改写 —— 恰恰是最容易悄悄改口径的一类。
# 本组用三个 oracle 钉住它：
#   ① 预筛标识集**必须由判据常量派生**（将来加原语不会漏扫）；
#   ② 合成语料上"开预筛 vs 关预筛"逐字段对拍；
#   ③ 符号锚点与**旧递归口径**（内联 oracle）对拍。


def _legacy_symbol_anchors(tree: ast.Module) -> Dict[int, str]:
    """改造前 `_SymbolIndex` 的算法（递归符号栈）—— 仅作对拍 oracle 使用"""
    stack: List[str] = []
    out: Dict[int, str] = {}

    def walk(node: Any) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stack.append(child.name)
                walk(child)
                stack.pop()
            else:
                if isinstance(child, ast.Call):
                    out[id(child)] = "::".join(stack) or "<module>"
                walk(child)

    walk(tree)
    return out


#: 预筛等价性合成语料：逐条覆盖三类判据分支 + 两种"应当被跳过"的形态
_PREFILTER_CORPUS = {
    # ① P1 收口路径：import 别名
    "agent/p_import.py": (
        "from agent.tools import call\n"
        "\n"
        "\n"
        "def f_import():\n"
        '    call("cap_import")\n'
    ),
    # ② P1 收口路径：属性链（含跨行写法）
    "agent/p_attr.py": (
        "import agent.tools as tools\n"
        "\n"
        "\n"
        "def f_attr():\n"
        '    tools.\n'
        '        call("cap_attr")\n'
    ),
    # ③ P1 收口路径：裸别名（无 import 可解析时的兜底分支）
    "agent/p_alias.py": (
        "def f_alias():\n"
        '    call_tool("cap_alias")\n'
    ),
    # ④ P3 远程原语
    "agent/p_remote.py": (
        "def f_remote(session):\n"
        "    session.call_tool(name='x')\n"
    ),
    # ⑤ P2 直调：注册面在本文件，调用点在 p_direct.py（跨模块）
    "agent/p_reg.py": (
        "from agent.tools import register\n"
        "\n"
        "\n"
        '@register("cap_registered")\n'
        "def handler_registered():\n"
        "    pass\n"
    ),
    "agent/p_direct.py": (
        "from agent.p_reg import handler_registered\n"
        "\n"
        "\n"
        "def f_direct():\n"
        "    handler_registered()\n"
    ),
    # ⑥ 只在注释/字符串里出现 ⇒ 预筛**必须保守命中**（多扫不漏扫）
    "agent/p_comment.py": (
        "# 这里提到 call( 但不是调用点\n"
        "TEXT = '字符串里的 call( 同样不是调用点'\n"
        "\n"
        "\n"
        "def f_comment():\n"
        "    return TEXT\n"
    ),
    # ⑦ 不含任何参与判据的标识符 ⇒ 预筛应当跳过（这正是省下来的成本）
    "agent/p_quiet.py": (
        "def f_quiet():\n"
        "    return 1 + 1\n"
    ),
}


class TestPrefilterAndTraversalEquivalence:
    """字节预筛 + 单次遍历：**成本降了、判据没变**"""

    @pytest.fixture
    def corpus(self, tmp_path):
        for rel, body in _PREFILTER_CORPUS.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return tmp_path, sorted(_PREFILTER_CORPUS)

    def test_预筛标识集必须覆盖全部判据名字(self, audit):
        """★ 防"加了新原语却忘了加进预筛" —— 那会变成**静默漏扫**（门禁假绿）"""
        assert audit._CALL_SITE_NAME_EXTRA == (
            frozenset({"call"}) | audit._FUNNEL_ALIASES | audit._REMOTE_PRIMITIVES), \
            "预筛标识集必须由判据常量派生（手抄的话，将来加名字就会漏扫）"
        names = sorted(set(audit._CALL_SITE_NAME_EXTRA) | {"some_registered_handler"})
        pattern = audit._call_site_pattern(names)
        for name in names:
            assert pattern.search(("x = %s(" % name).encode("utf-8")), \
                f"预筛正则漏掉判据名字：{name}"
        for negative in (b"callback(", b"mycall(", b"call_toolbox(", b"invoke_toolz("):
            assert not pattern.search(negative), f"预筛正则边界不对：{negative}"

    def test_预筛命中应当命中的形态(self, audit):
        pattern = audit._call_site_pattern([])
        # 注意：`register(...)` **不**由本预筛负责（注册面有自己的
        # `_source_mentions_register` 预筛），这里只覆盖**调用点**标识符。
        for src in (b"tools.call('x')", b"tools\n    .call('x')", b"call_tool('x')",
                    b"session.call_tool(name='x')", b"# call(  ",
                    b'TEXT = "call_tool"', b"x = obj.callTool()"):
            assert pattern.search(src), f"预筛漏掉应当命中的形态：{src}"
        assert not pattern.search(b"def f():\n    return 1 + 1\n"), \
            "不含调用点标识符的文件不该命中（否则预筛等于没生效）"

    def test_开预筛与关预筛结论逐字段一致(self, audit, corpus, monkeypatch):
        """★ 核心等价性：预筛只应**少读文件**，不能少任何 Finding"""
        root, files = corpus
        pattern = audit._call_site_pattern([])
        skipped = [rel for rel in files
                   if not audit._source_may_contain_call_site(str(root), rel, pattern)]
        assert "agent/p_quiet.py" in skipped, \
            "预筛没有跳过不可能含调用点的文件（等于没生效，用例也测不实）"
        assert "agent/p_comment.py" not in skipped, \
            "只出现在注释/字符串里的文件必须保守命中（否则就是真漏扫）"

        with_filter = [f.to_dict() for f in audit._collect_findings(str(root), files)]

        monkeypatch.setattr(audit, "_source_may_contain_call_site",
                            lambda _root, _rel, _pattern: True)   # 关掉预筛
        without_filter = [f.to_dict() for f in audit._collect_findings(str(root), files)]

        assert with_filter == without_filter, (
            "开/关预筛的 Findings 不一致 ⇒ 预筛改变了判据：\n"
            f"开：{with_filter}\n关：{without_filter}"
        )
        kinds = {f["path_kind"] for f in with_filter}
        assert {"funnel", "direct", "remote_primitive"} <= kinds, \
            f"合成语料没覆盖到三类判据分支，等价性就测不实：{kinds}"

    def test_符号锚点与旧递归口径等价(self, audit):
        """内联旧口径（递归符号栈）当 oracle，对拍**每一个** `ast.Call` 的锚点

        单次遍历改写最容易出的错就是"作用域算错一格"；嵌套类 / 装饰器 / 默认参数 /
        lambda 都在语料里覆盖到。
        """
        tree = ast.parse(
            "def outer():\n"
            "    class Inner:\n"
            "        @decorator(call('cap_deco'))\n"
            "        def method(self, cb=call('cap_default')):\n"
            "            return lambda: call('cap_lambda')\n"
            "\n"
            "\n"
            "call('cap_module')\n"
        )
        expected = _legacy_symbol_anchors(tree)
        assert expected, "oracle 没算出任何锚点，用例失效"
        index = audit._TreeIndex(tree)
        got = {id(n): index.symbol_of(n)
               for n in index.nodes if isinstance(n, ast.Call)}
        assert got == expected, f"符号锚点口径漂移：\n新={got}\n旧={expected}"

    def test_绑定表与_ast_walk_同序等价(self, audit):
        """`module_bindings_from_nodes` 与 `module_bindings` 必须等价

        "同名重复 import 后者覆盖"依赖遍历顺序（两次 import 同名时以**后遍历到**者为准）
        ⇒ 顺序等价必须显式钉住，否则单次遍历改写会悄悄改绑定表。
        """
        tree = ast.parse(
            "import os\n"
            "from a import dup\n"
            "def f():\n"
            "    from b import dup\n"
            "    return dup\n"
            "from c import dup\n"
        )
        assert audit.module_bindings_from_nodes(audit._TreeIndex(tree).nodes) \
            == audit.module_bindings(tree), "单次遍历改写了 import 绑定表的语义"


