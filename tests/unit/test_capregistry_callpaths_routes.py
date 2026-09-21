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
from pathlib import Path
from typing import Any, Dict, List

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.capregistry.call_sites import (  # noqa: E402
    DEAD_MODULES, EXEMPT_CALL_SITES, VIOLATION_SCOPE_PREFIXES, anchor_key,
    reachability_of)


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
        assert body["data"]["total"] == 114
        assert body["data"]["returned"] == 114
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
        assert body["data"]["total"] == 114
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
        assert h.get_json()["data"]["registry"]["total"] == 114

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
