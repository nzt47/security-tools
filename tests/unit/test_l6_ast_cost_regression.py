"""L6 成本回归判据：AST 索引 / 指纹缓存的"**只做一次**"守护

【为什么单开一个文件】
    `docs/closeout/遗留问题立项_20260922.md` 的 **L6「CI 负载敏感」**：整仓 AST 扫描类
    用例在 2 核争用 runner 上时而 35s、时而 300s+，同一 commit 时而红时而绿。根治口径是
    "只加索引/缓存，**不改任何判定结果**"：
      · `agent/lines/location.py` 加**每模块节点索引**（整树 `ast.walk` 只做一次）；
      · `agent/lines/callability.py` 的工具文档读取加**文件指纹缓存**（YAML 不再重复解析）；
      · `scripts/scan_settings.py` 把"常量索引趟 + 提取趟"的**两次 ast.parse 收敛为一次**。

【判据为什么用"计数器"而不是"秒数"】
    计时判据在争用 runner 上必然 flaky（这正是 L6 的病灶）。故主判据一律钉**调用次数**
    （`ast.walk` / `ast.parse` / `_read_yaml` 被调了几次），确定性且毫秒级；
    只保留**一条上界放宽 3 个数量级**的计时判据作为兜底（见 `test_热查询...`）。
    【不易·本文件自己必须很轻】**不做任何全仓扫描**：location 侧只用真实仓库里的
    1~2 个模块，scan_settings 侧只用 `tmp_path` 里 3 个小文件。

【不易·不改断言语义、不放宽任何预算】本文件**只新增**判据，不动任何既有用例的断言。
"""

from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: L6 点名的"跨边界客户端类"（`disconnect_mcp` 靠它才判成 remote —— 见 location.py 说明）
LOC_MODULE = "agent.tools.mcp_connector"
LOC_CLASS = "McpConnector"


def _load_scanner():
    """按路径加载 `scripts/scan_settings.py`（scripts/ 不是包）

    与 `tests/unit/test_settings_registry.py::_load_scanner` 同款：必须**先注册进
    `sys.modules` 再 exec`，否则模块内 `@dataclass` 在 3.12 下会报
    `AttributeError: 'NoneType' object has no attribute '__dict__'`（S7-01 实测踩坑）。
    """
    path = REPO_ROOT / "scripts" / "scan_settings.py"
    spec = importlib.util.spec_from_file_location("l6_scan_settings", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:                                    # pragma: no cover
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def scanner():
    return _load_scanner()


# ════════════════════════════════════════════════════════════
#  一、`location` 每模块节点索引：整树遍历只做一次
# ════════════════════════════════════════════════════════════


def _count_ast_walk(monkeypatch):
    """把 `ast.walk` 换成计数器版（返回一个 list，长度即调用次数）"""
    calls = []
    real_walk = ast.walk

    def counting_walk(node):
        calls.append(node)
        return real_walk(node)

    monkeypatch.setattr(ast, "walk", counting_walk)
    return calls


class TestLocationNodeIndex:
    def test_同一模块的整树遍历只做一次(self, monkeypatch):
        """★ 成本判据：索引建好之后，**重复查询不再产生任何 `ast.walk`**

        改前：`_class_boundary` 每次调用都 `ast.walk` 整树 + 类体/方法体再各遍历一遍
        （cProfile 实测 251 次调用 → 14.9s）。改后：第一次建索引，之后是 dict 查表。
        """
        from agent.lines import location as L

        L.invalidate_cache()
        calls = _count_ast_walk(monkeypatch)

        first = L._class_boundary(LOC_MODULE, LOC_CLASS)
        after_first = len(calls)
        second = L._class_boundary(LOC_MODULE, LOC_CLASS)
        warm_calls = len(calls) - after_first

        assert first is not None, "McpConnector 应命中跨边界原语（否则本用例失去意义）"
        assert second == first, "索引不得改变判定结果"
        assert after_first > 0, "首次调用必须真的建了索引（否则判据是空跑）"
        assert warm_calls == 0, f"热查询仍在整树遍历：{warm_calls} 次 ast.walk"

    def test_同一模块的索引只建一次(self, monkeypatch):
        """`_node_index` 对同一模块只建一次索引（第二次查询不再走 `ast.walk`）"""
        from agent.lines import location as L

        L.invalidate_cache()
        tree = L._load_tree(LOC_MODULE)
        assert tree is not None
        calls = _count_ast_walk(monkeypatch)
        idx1 = L._node_index(LOC_MODULE, tree)
        built = len(calls)
        idx2 = L._node_index(LOC_MODULE, tree)
        assert idx1 is idx2
        assert built > 0
        assert len(calls) == built, "第二次数索引又走了一遍 ast.walk"

    def test_invalidate_cache_同时清掉节点索引(self):
        """`invalidate_cache()` 必须把新索引一起清掉（否则 mtime 变了还拿旧索引）"""
        from agent.lines import location as L

        L.invalidate_cache()
        tree = L._load_tree(LOC_MODULE)
        assert tree is not None
        L._node_index(LOC_MODULE, tree)
        assert LOC_MODULE in L._NODE_INDEX_CACHE
        L.invalidate_cache()
        assert L._NODE_INDEX_CACHE == {}
        assert L._TREE_CACHE == {}

    def test_文件变了索引跟着失效(self):
        """索引与**树对象**绑定：树换了（文件被改 ⇒ mtime 变）就必须重建，不拿旧索引答新问题"""
        from agent.lines import location as L

        L.invalidate_cache()
        tree = L._load_tree(LOC_MODULE)
        assert tree is not None
        idx_old = L._node_index(LOC_MODULE, tree)
        # 伪造一次"文件被改"：把缓存的树换成另一棵（mtime 仍写真实值 ⇒ _load_tree 认它）
        real_mtime = os.stat(L._module_path(LOC_MODULE)).st_mtime_ns
        other = ast.parse("class McpConnector:\n    pass\n")
        L._TREE_CACHE[LOC_MODULE] = (real_mtime, other)
        assert L._load_tree(LOC_MODULE) is other
        idx_new = L._node_index(LOC_MODULE, other)
        assert idx_new is not idx_old, "树换了却复用了旧索引"
        assert len(idx_new.class_nodes("McpConnector")) == 1
        L.invalidate_cache()

    def test_索引化后判定与整树遍历原实现逐字一致(self):
        """★ 语义判据：拿**原实现的逐字复制**当对照物，对真实模块逐类对拍

        对照物刻意用原技法写（整树 `ast.walk` + 合成 `ast.Module` 再遍历），
        不调用本次新增的任何辅助函数，避免"自己证明自己"。
        """
        from agent.lines import location as L

        def orig_body_prims(stmts):
            hits = set()
            for node in ast.walk(ast.Module(body=list(stmts), type_ignores=[])):
                if isinstance(node, ast.Call):
                    prim = L._is_boundary(L._dotted(node.func))
                    if prim:
                        hits.add(prim)
            return sorted(hits)

        def orig_class_boundary(module, cls):
            tree = L._load_tree(module)
            if tree is None:
                return None
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or node.name != cls:
                    continue
                prims = orig_body_prims(node.body)
                if prims:
                    return prims[0]
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        prims = orig_body_prims(sub.body)
                        if prims:
                            return prims[0]
            return None

        def orig_enclosing(module, func):
            tree = L._load_tree(module)
            if tree is None:
                return ""
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for sub in node.body:
                    if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and sub.name == func):
                        return node.name
            return ""

        L.invalidate_cache()
        checked = 0
        for module in (LOC_MODULE, "agent.web.http_client"):
            tree = L._load_tree(module)
            assert tree is not None
            L._node_index(module, tree)
            for cls_name in sorted({n.name for n in ast.walk(tree)
                                    if isinstance(n, ast.ClassDef)}):
                assert L._class_boundary(module, cls_name) == orig_class_boundary(module, cls_name)
                checked += 1
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            assert L._enclosing_class(module, sub.name) == \
                                orig_enclosing(module, sub.name)
                            checked += 1
        assert checked > 0

    def test_热查询成本远低于整树遍历(self):
        """兜底计时判据（**上界放宽 3 个数量级**，只为抓"整树遍历回来了"这种回归）

        改前：每次调用都整树 `ast.walk`（cProfile 实测 251 次 → 14.9s ⇒ 单次 ≈59ms），
        50 次约 3s；改后：50 次热查询是纯 dict 查表（本机 <1ms）。这里只要求 <1.0s。
        """
        from agent.lines import location as L

        L.invalidate_cache()
        L._class_boundary(LOC_MODULE, LOC_CLASS)          # 预热：建索引
        t0 = time.perf_counter()
        for _ in range(50):
            L._class_boundary(LOC_MODULE, LOC_CLASS)
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"50 次热查询耗时 {elapsed:.3f}s（改前口径下约 3s）"


# ════════════════════════════════════════════════════════════
#  二、`callability.load_tool_docs` 指纹缓存：YAML 不重复解析
# ════════════════════════════════════════════════════════════


class TestToolDocsFingerprintCache:
    def test_第二次读取不再解析任何_YAML(self, monkeypatch):
        """★ 成本判据：指纹命中 ⇒ **零次** `_read_yaml`（原实现每次 200+ 次 safe_load）"""
        from agent.lines import callability as C

        warm = C.load_tool_docs()
        assert warm, "工具定义目录必须有 YAML（否则本用例失去意义）"

        calls = []
        real_read = C._read_yaml

        def counting_read(path):
            calls.append(path)
            return real_read(path)

        monkeypatch.setattr(C, "_read_yaml", counting_read)
        again = C.load_tool_docs()
        assert calls == [], f"指纹命中却仍解析了 {len(calls)} 个 YAML"
        assert again == warm, "缓存不得改变文档内容"

    def test_缓存命中仍返回私有对象(self):
        """命中时返回 deepcopy：调用方就地改 doc 不得污染后续调用

        （原实现每次都重新 safe_load ⇒ 调用方拿到的一定是私有对象；缓存必须保持这一点）
        """
        from agent.lines import callability as C

        first = C.load_tool_docs()
        name = sorted(first)[0]
        first[name]["_l6_probe"] = "mutated"
        second = C.load_tool_docs()
        assert second[name] is not first[name]
        assert "_l6_probe" not in second[name]

    def test_缓存键含逐文件指纹(self):
        """键 = `(目录绝对路径, ((文件名, mtime_ns, 字节数), …))`：文件一变键就变"""
        from agent.lines import callability as C

        C.load_tool_docs()
        assert len(C._TOOL_DOCS_CACHE) == 1
        key = next(iter(C._TOOL_DOCS_CACHE))
        assert key[0] == os.path.abspath(C.TOOL_DEFS_DIR)
        assert key[1], "指纹不能为空"
        assert all(len(row) == 3 and row[0].endswith(".yaml") for row in key[1])


# ════════════════════════════════════════════════════════════
#  三、`scan_settings.scan_paths`：同一文件只 `ast.parse` 一次
# ════════════════════════════════════════════════════════════

_LOCAL_PY = '''import os

_L6_LOCAL_ENV = "L6_LOCAL_ENV"


def read_local():
    return os.environ.get(_L6_LOCAL_ENV, "0")
'''

_PEER_PY = '''import os


def read_peer():
    return os.environ.get(L6_PEER_ENV, "0")
'''

_CONSTS_PY = '''L6_PEER_ENV = "L6_PEER_ENV"
'''


def _write_corpus(root: pathlib.Path, *, cross_module: bool) -> pathlib.Path:
    """极小的临时语料（**不碰任何仓库内文件**）；`cross_module` 决定是否存在跨模块常量"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod_local.py").write_text(_LOCAL_PY, encoding="utf-8")
    if cross_module:
        (root / "mod_peer.py").write_text(_PEER_PY, encoding="utf-8")
        (root / "mod_consts.py").write_text(_CONSTS_PY, encoding="utf-8")
    return root


def _reference_report(scanner, root: pathlib.Path):
    """**旧口径的参考实现**（两趟：常量索引趟 + 提取趟），用模块里保留的两个函数拼出来

    `scan_paths` 已不再走这条路，但它仍在模块里（`_build_global_consts` / `scan_file`），
    正好当"改前行为"的对照物 —— 新口径的输出必须与它**逐字节相同**。
    """
    report = scanner.ScanReport()
    global_consts = scanner._build_global_consts(root, [root])
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root)).replace(os.sep, "/")
        report.files_scanned += 1
        reads, err = scanner.scan_file(path, rel, global_consts)
        if err:
            report.parse_errors.append(err)
        scanner._collect(report, reads)
    return report


class TestScanSettingsSingleParse:
    def test_同一文件只解析一次(self, scanner, tmp_path, monkeypatch):
        """★ 成本判据（无跨模块常量）：`ast.parse` 次数 == 文件数"""
        root = _write_corpus(tmp_path / "corpus", cross_module=False)
        parses = []
        real_parse = ast.parse

        def counting_parse(source, *a, **kw):
            parses.append(1)
            return real_parse(source, *a, **kw)

        monkeypatch.setattr(ast, "parse", counting_parse)
        report = scanner.scan_paths([root], root)
        monkeypatch.undo()

        assert report.files_scanned == 1
        assert len(parses) == 1, f"同一文件被解析了 {len(parses)} 次（应为 1 次）"

    def test_只有命中全仓常量索引的文件才重扫(self, scanner, tmp_path, monkeypatch):
        """跨模块常量命中 ⇒ 该文件用**完整索引**重扫一次（其余文件仍只解析一次）"""
        root = _write_corpus(tmp_path / "corpus", cross_module=True)
        parses = []
        real_parse = ast.parse

        def counting_parse(source, *a, **kw):
            parses.append(1)
            return real_parse(source, *a, **kw)

        monkeypatch.setattr(ast, "parse", counting_parse)
        report = scanner.scan_paths([root], root)
        monkeypatch.undo()

        assert report.files_scanned == 3
        # 3 个文件各一次 + mod_peer.py 命中 L6_PEER_ENV 后重扫一次
        assert len(parses) == 4, f"解析次数 {len(parses)}（期望 3 + 1）"
        assert "L6_PEER_ENV" in {rp.name for rp in report.managed}

    def test_输出与旧两趟口径逐字节相同(self, scanner, tmp_path):
        """★ 语义判据：新口径 vs 旧口径参考实现，同一语料上 `to_dict()` 完全相同"""
        root = _write_corpus(tmp_path / "corpus", cross_module=True)
        new = scanner.scan_paths([root], root)
        old = _reference_report(scanner, root)
        assert new.to_dict() == old.to_dict()

    def test_语法错误文件的报错文案与旧口径一致(self, scanner, tmp_path):
        """单次解析不能把"报错文案"弄丢：`parse_errors` 仍带文件名（与旧口径相同）"""
        root = tmp_path / "corpus"
        root.mkdir()
        (root / "broken.py").write_text("def oops(:\n    pass\n", encoding="utf-8")
        new = scanner.scan_paths([root], root)
        old = _reference_report(scanner, root)
        assert new.parse_errors == old.parse_errors
        assert new.parse_errors and "broken.py" in new.parse_errors[0]["error"]
