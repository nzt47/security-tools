# -*- coding: utf-8 -*-
"""`scan_settings.py` 的「一次 BFS」改造等价性锁（L6 残留 · 2026-09-29）

背景
----
`_extract` 原先让 6 个收集器**各自** `ast.walk(tree)` 一次，且两个收集器还对**每个函数**
再做一次子树遍历求最大行号（且各算一遍）。实测（765 文件）：全仓 `ast.walk` 共访问
**1,590 万个节点**（≈ 每文件整体遍历 6~7 次），占扫描累计耗时 **73.7%**。
改造后合并为**一次 BFS**（节点表 + 父指针），再由一次逆序汇总出「子树内最大行号」。

本文件钉死改造的**两条正确性前提**（都是"看起来显然、错了却会静默改判据"的那种）：

1. **保序**：`_walk_nodes_and_ends(tree)[0]` 必须与 stdlib `ast.walk(tree)` **逐元素同序**。
   多个收集器是"扫全树挑条件"，顺序不影响结果；但逆序汇总**依赖**"父先于子"，
   一旦有人图省事改成 DFS，这里的断言会先响。
2. **子树最大行号与原式逐值相同**：调用侧一律写 `max(ends.get(id(n), -1), start)`，
   必须等于改前的 `max(getattr(x, "lineno", start) for x in ast.walk(n))`。
   特别地**不能用 `node.end_lineno`** 走捷径 —— 那是源码跨度末行（多行调用落在闭合
   括号那行），比"子树内最大 lineno"更大 ⇒ 函数体区间变宽 ⇒ `_is_pass_through` 漂移。

还有一条**非空转**断言：语料里必须真的存在"子树最大值 > 本节点值"与"缺 lineno 的节点"，
否则上面两条等价性在平凡语料上恒真，锁了个寂寞。
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_scanner():
    """按路径加载 `scripts/scan_settings.py`（与 `test_settings_registry.py` 同法）"""
    path = ROOT / "scripts" / "scan_settings.py"
    spec = importlib.util.spec_from_file_location("cp_scan_settings_walk", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def scanner():
    return _load_scanner()


#: 语料：刻意选**多行构造 / 嵌套函数 / 装饰器 / lambda / 类**都有的真实文件，
#: 且都远小于整仓扫描（本文件只锁"遍历等价"，不重复整仓扫描的守卫）。
_CORPUS = (
    "scripts/scan_settings.py",
    "agent/orchestrator/lifecycle_manager.py",
    "agent/digital_life.py",
    "tests/unit/test_settings_registry.py",
)


def _trees():
    out = []
    for rel in _CORPUS:
        path = ROOT / rel
        assert path.is_file(), f"语料文件不存在：{rel}"
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    return out


def _naive_end(node: ast.AST) -> int:
    """改前的原式（逐字照抄），作为 oracle"""
    start = getattr(node, "lineno", 0)
    return max((getattr(n, "lineno", start) for n in ast.walk(node)), default=start)


class TestWalkOrderIsPreserved:
    def test_nodes_match_ast_walk_elementwise(self, scanner):
        for rel, tree in _trees():
            nodes, _ends = scanner._walk_nodes_and_ends(tree)
            assert [id(n) for n in nodes] == [id(n) for n in ast.walk(tree)], (
                f"{rel}：`_walk_nodes_and_ends` 的节点顺序与 `ast.walk` 不一致 —— "
                "逆序汇总依赖「父先于子」，顺序一改判据就会静默漂移"
            )

    def test_parent_appears_before_child(self, scanner):
        """「父先于子」单独钉一条：即使将来重写 BFS，这条不变量也不许丢"""
        for rel, tree in _trees():
            nodes, _ends = scanner._walk_nodes_and_ends(tree)
            pos = {id(n): i for i, n in enumerate(nodes)}
            for node in nodes:
                for child in ast.iter_child_nodes(node):
                    assert pos[id(node)] < pos[id(child)], f"{rel}：子节点排在了父节点之前"


class TestSubtreeEndLinesMatchNaive:
    def test_every_node_matches_oracle(self, scanner):
        checked = 0
        for rel, tree in _trees():
            nodes, ends = scanner._walk_nodes_and_ends(tree)
            for node in nodes:
                start = getattr(node, "lineno", 0)
                got = max(ends.get(id(node), -1), start)
                assert got == _naive_end(node), (
                    f"{rel}:{start} 子树最大行号 {got} ≠ 原式 {_naive_end(node)}"
                )
                checked += 1
        assert checked > 20000, f"语料太小（{checked} 个节点），等价性近似空转"

    def test_corpus_is_not_vacuous(self, scanner):
        """非空转：语料必须真的覆盖"子树最大值 > 本节点""缺 lineno""多行构造"""
        has_deeper = has_missing = has_multiline = False
        total = 0
        for _rel, tree in _trees():
            nodes, ends = scanner._walk_nodes_and_ends(tree)
            total += len(nodes)
            for node in nodes:
                start = getattr(node, "lineno", 0)
                if max(ends.get(id(node), -1), start) > start:
                    has_deeper = True
                if not hasattr(node, "lineno"):
                    has_missing = True
                if (getattr(node, "end_lineno", None) or 0) > (start or 0):
                    has_multiline = True
        assert has_deeper, "语料里没有任何「子树比本节点更深」的节点 ⇒ 等价性没有区分度"
        assert has_missing, "语料里没有缺 lineno 的节点 ⇒ start 兜底分支没被覆盖"
        assert has_multiline, "语料里没有多行构造与 lineno 不同的节点"
        assert total > 20000

    def test_end_lineno_shortcut_would_differ(self, scanner):
        """说明书级反证：`end_lineno` 与原式**确实不同**，所以不许走那条捷径"""
        differs = 0
        for _rel, tree in _trees():
            for node in ast.walk(tree):
                end_lineno = getattr(node, "end_lineno", None)
                if end_lineno is None:
                    continue
                if end_lineno != _naive_end(node):
                    differs += 1
        assert differs > 0, (
            "若 end_lineno 与原式恰好处处相同，本改造就可以用一行捷径 —— "
            "断言失效说明语料选错了"
        )


class TestCollectorsShareNodesWithoutChangingResults:
    def test_assign_targets_same_with_and_without_nodes(self, scanner):
        for rel, tree in _trees():
            nodes, _ends = scanner._walk_nodes_and_ends(tree)
            a = scanner._Extractor(rel, [])
            a._assign_targets(tree)
            b = scanner._Extractor(rel, [])
            b._assign_targets(tree, nodes)
            assert a._target_subscripts == b._target_subscripts, rel

    def test_local_consts_same_with_and_without_nodes(self, scanner):
        for rel, tree in _trees():
            nodes, _ends = scanner._walk_nodes_and_ends(tree)
            assert scanner._local_consts(tree) == scanner._local_consts(tree, nodes), rel

    def test_loop_literals_same_with_and_without_nodes(self, scanner):
        for rel, tree in _trees():
            nodes, _ends = scanner._walk_nodes_and_ends(tree)
            a = scanner._Extractor(rel, [])
            a.collect_loop_literals(tree)
            b = scanner._Extractor(rel, [])
            b.collect_loop_literals(tree, nodes)
            assert a._loop_literals == b._loop_literals, rel
            assert a._loop_from_name == b._loop_from_name, rel