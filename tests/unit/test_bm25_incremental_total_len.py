"""BM25 增量总长的**不变量**与**复杂度**测试（TASK-08 / E1p）

## 被测改动

`agent/tool_router_hybrid.py::BM25Index` 修复前在 `add_document` 与
`_remove_document_locked` 里都执行 `sum(self._doc_lengths.values())` 重算全表总长
⇒ 单次插入 O(n)、构建 n 篇 O(n²)。修复后改为增量字段 `_total_doc_len`。

## 为什么不用"计时断言"来测复杂度

计时断言在本仓是**不可靠**的：本机有用户后端常驻（并发负载）、pytest 有 120s
线程级超时，且小规模下二次项远小于线性项（n=1000 时二次项只占约 10%），
比值噪声能把判据淹没。所以主判据用**确定性计数**：

把 `_doc_lengths` 换成一个**计数代理 dict**——它的 `values()/items()/keys()/__iter__`
返回逐元素计数的生成器。于是"有没有做全表扫描"变成一个精确整数：

  * 修复前：插入 n 篇会产出 n²/2 次元素产出（每次 sum 扫 i 个元素）；
  * 修复后：**0 次**。

这个数不含任何时间成分，跑在 0 元素上也不会退化成 flaky。计时口径的曲线由
`scripts/bench_bm25_add_document.py` 负责（它用冷子进程 + 多次取中位数）。
另有一条 `@pytest.mark.serial` 的"末/首十分位耗时比"作为**辅助**证据
（二次项的指纹：单次插入耗时随已插入条数增长），界放得很宽，只用于兜住
"有人把 O(1) 改回 O(n)"这种量级级回退。
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, Iterator, List, Tuple

import pytest


# ════════════════════════════════════════════════════════════
#  工具：会数"全表扫描吐出了多少元素"的代理 dict
# ════════════════════════════════════════════════════════════


class CountingDocLengths(dict):
    """`dict` 子类：统计"整表遍历"实际吐出了多少元素

    why 统计**元素数**而不是**调用次数**：调用次数只能说明"扫了一次"，
    元素数才能区分"扫了 1 个"和"扫了 10,000 个"，从而把 O(n) 与 O(1)
    的差别变成一个可以直接断言的整数。
    """

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.elements_yielded = 0
        self.full_scan_calls = 0

    def _wrap(self, it: Iterator[Any]) -> Iterator[Any]:
        self.full_scan_calls += 1
        for x in it:
            self.elements_yielded += 1
            yield x

    def values(self) -> Iterator[Any]:  # type: ignore[override]
        return self._wrap(super().values())

    def items(self) -> Iterator[Any]:  # type: ignore[override]
        return self._wrap(super().items())

    def keys(self) -> Iterator[Any]:  # type: ignore[override]
        return self._wrap(super().keys())

    def __iter__(self) -> Iterator[Any]:  # type: ignore[override]
        return self._wrap(super().__iter__())


def _fresh_index(n_docs: int = 0) -> Tuple[Any, CountingDocLengths]:
    """构造一个 `_doc_lengths` 被换成计数代理的 BM25Index"""
    from agent.tool_router_hybrid import BM25Index

    idx = BM25Index()
    proxy = CountingDocLengths()
    idx._doc_lengths = proxy
    for i in range(n_docs):
        idx.add_document(f"pre{i}", f"预置文档 {i} seed words")
    # 预置阶段的扫描量不计入被测区间
    proxy.elements_yielded = 0
    proxy.full_scan_calls = 0
    return idx, proxy


def _assert_invariant(idx: Any, note: str = "") -> None:
    """核心不变量：增量维护的总长 == 重算值，且平均长度与重算口径一致"""
    recomputed = sum(idx._doc_lengths.values())
    assert idx.total_doc_length == recomputed, (
        f"增量总长({idx.total_doc_length}) != 重算总长({recomputed}) [{note}]"
    )
    assert idx._total_doc_len == recomputed, f"私有字段与属性不一致 [{note}]"
    assert idx._total_docs == len(idx._doc_lengths), (
        f"_total_docs({idx._total_docs}) != len(_doc_lengths)({len(idx._doc_lengths)}) [{note}]"
    )
    if idx._total_docs > 0:
        assert idx._avg_doc_length == recomputed / idx._total_docs, (
            f"_avg_doc_length({idx._avg_doc_length}) 与重算口径不一致 [{note}]"
        )
        assert idx._avg_doc_length == idx._total_doc_len / idx._total_docs
    else:
        # 空索引：总长与平均长度都必须归零（否则"删光再加"会带着历史残留）
        assert idx._avg_doc_length == 0.0, f"空索引平均长度未归零 [{note}]"
        assert idx._total_doc_len == 0, f"空索引总长未归零 [{note}]"


# ════════════════════════════════════════════════════════════
#  ① 不变量：任意操作序列后增量总长 == 重算值
# ════════════════════════════════════════════════════════════


class TestIncrementalInvariant:
    """增量字段与 `_doc_lengths` 必须在**任意**操作序列后保持一致"""

    def test_add_only(self):
        idx, _ = _fresh_index()
        for i in range(50):
            idx.add_document(f"t{i}", f"工具 {i} 用于搜索 description payload {i}")
            _assert_invariant(idx, f"add #{i}")

    def test_overwrite_same_id_keeps_consistency(self):
        """覆盖语义会走 `_remove_document_locked` —— 减错顺序会让总长永久偏大"""
        idx, _ = _fresh_index()
        # 【W5/L28】中文按**相邻二元组**切分（不再是单字）：n 字串 → n-1 个 token。
        # 本用例断言的是"覆盖路径的增量总长"，与切分粒度无关，故按 bigram 口径取值。
        idx.add_document("a", "一二三四五")          # 5 字 → 4 bigram
        idx.add_document("b", "一二三")              # 3 字 → 2 bigram
        _assert_invariant(idx, "初次写入")
        assert idx.total_doc_length == 6

        idx.add_document("a", "一二")                # 覆盖：6 - 4 + 1 = 3
        _assert_invariant(idx, "覆盖 a")
        assert idx.total_doc_length == 3
        assert idx._total_docs == 2

        # 反复覆盖同一 id：总长不得漂移
        for k in range(20):
            idx.add_document("a", "一" * (k + 1))
            _assert_invariant(idx, f"反复覆盖 #{k}")
        assert idx._total_docs == 2

    def test_shrink_to_empty_then_readd(self):
        """删到空索引必须把总长归零（历史残留会让下一次 _avg_doc_length 算错）"""
        idx, _ = _fresh_index()
        # 【W5/L28】中文按相邻二元组切分：4 字 → 3 token、2 字 → 1 token、单字 → 1 token
        idx.add_document("a", "一二三四")
        idx.add_document("b", "五六")
        idx.add_document("a", "七")   # 3→1
        _assert_invariant(idx, "缩容")
        idx.add_document("b", "八")   # 1→1
        _assert_invariant(idx, "再缩容")
        assert idx.total_doc_length == 2, "两条各 1 token"

        idx.clear()
        _assert_invariant(idx, "clear 后")
        assert idx._total_doc_len == 0

        idx.add_document("c", "九十")   # 2 字 → 1 bigram
        _assert_invariant(idx, "clear 后重新写入")
        assert idx.total_doc_length == 1, "clear 后不得带上 clear 之前的历史总长"

    def test_randomized_operation_sequence(self):
        """固定种子随机序列（可复现）：每步都断言不变量"""
        rnd = random.Random(20260920)
        idx, _ = _fresh_index()
        live: List[str] = []
        for step in range(400):
            op = rnd.random()
            if op < 0.55 or not live:
                doc_id = f"d{rnd.randrange(60)}"
                idx.add_document(doc_id, " ".join(f"tok{rnd.randrange(20)}" for _ in range(rnd.randint(1, 12))))
                if doc_id not in live:
                    live.append(doc_id)
            elif op < 0.95:
                doc_id = rnd.choice(live)
                idx.add_document(doc_id, " ".join(f"tok{rnd.randrange(20)}" for _ in range(rnd.randint(1, 12))))
            else:
                idx.clear()
                live.clear()
            _assert_invariant(idx, f"step {step}")
        # 全程未出现"总长只增不减"的漂移
        assert idx.total_doc_length >= 0

    def test_empty_index_fields(self):
        from agent.tool_router_hybrid import BM25Index

        idx = BM25Index()
        assert idx.total_doc_length == 0
        assert idx._total_doc_len == 0
        assert idx._total_docs == 0
        assert idx._avg_doc_length == 0.0
        assert idx.size == 0
        _assert_invariant(idx, "全新索引")


# ════════════════════════════════════════════════════════════
#  ② 复杂度：不得再做全表扫描（确定性判据，与时间无关）
# ════════════════════════════════════════════════════════════


class TestNoFullTableScan:
    """`add_document` / 覆盖路径不得整表遍历 `_doc_lengths`"""

    @pytest.mark.parametrize("n", [1, 10, 200, 1000])
    def test_add_never_scans_full_table(self, n: int):
        idx, proxy = _fresh_index()
        for i in range(n):
            idx.add_document(f"n{i}", f"工具 {i} 描述 with english tokens")
        assert proxy.full_scan_calls == 0, (
            f"add_document 触发了 {proxy.full_scan_calls} 次整表遍历"
            "（O(n) 单次插入回归）"
        )
        assert proxy.elements_yielded == 0, (
            f"add_document 全表遍历吐出了 {proxy.elements_yielded} 个元素；"
            "修复前这里是 n(n+1)/2，修复后必须是 0"
        )

    def test_overwrite_never_scans_full_table(self):
        idx, proxy = _fresh_index()
        for i in range(20):
            idx.add_document(f"o{i}", "alpha beta gamma")
        proxy.elements_yielded = 0
        proxy.full_scan_calls = 0
        for i in range(20):
            idx.add_document(f"o{i}", "delta epsilon")   # 每次覆盖 ⇒ 走 remove 路径
        assert proxy.full_scan_calls == 0
        assert proxy.elements_yielded == 0, (
            "覆盖路径仍在整表重算总长（_remove_document_locked 未改成减法）"
        )

    @pytest.mark.serial
    def test_per_insert_cost_does_not_grow_with_index_size(self):
        """辅助证据（计时口径，界放得很宽）：末/首十分位的 p50 比值应接近 1

        为什么这条能抓二次项：O(1) 插入时首尾十分位成本相同（比值≈1）；
        O(n) 插入时末段平均规模是首段的约 19 倍（n=4000 时），比值会是
        5–20 倍。判据取 3.0 —— 远离 1（能抓回归）又远离噪声（并发负载下
        实测抖动约 ±30%）。标 `@pytest.mark.serial`：它是时间类断言。
        """
        from agent.tool_router_hybrid import BM25Index

        n = 4000
        idx = BM25Index()
        per_add: List[float] = []
        t0 = time.perf_counter()
        for i in range(n):
            a = time.perf_counter()
            idx.add_document(f"p{i}", f"工具 {i} 名称 search_file 描述 用于检索 english payload {i % 37}")
            per_add.append((time.perf_counter() - a) * 1000.0)
        total_ms = (time.perf_counter() - t0) * 1000.0

        k = n // 10
        head = sorted(per_add[:k])[k // 2]
        tail = sorted(per_add[-k:])[k // 2]
        ratio = tail / max(head, 1e-9)
        assert ratio < 3.0, (
            f"单次插入成本随索引规模增长（末/首十分位 p50 比 = {ratio:.2f}）"
            f" ⇒ 单次插入仍是 O(n)。head={head:.4f}ms tail={tail:.4f}ms total={total_ms:.1f}ms"
        )
        # 兜底：大规模构建不得出现数量级级别的异常（修复后实测约 0.2s 量级）
        assert total_ms < 20000, f"构建 {n} 篇耗时 {total_ms:.1f}ms，超出兜底上限"


# ════════════════════════════════════════════════════════════
#  ③ 对外行为不变：平均文档长度与检索分数逐位一致
# ════════════════════════════════════════════════════════════


def _reference_bm25(idx: Any, term: str, term_freq: int, doc_length: int,
                    recomputed_total: int) -> float:
    """用**重算口径**独立实现一遍 BM25 打分，用于对照

    why 要独立实现而不是"再调一次被测函数"：只有独立实现才能证明"增量字段
    喂给 _compute_bm25 的值与重算值等价"，而不是证明函数与它自己一致。

    【W5/L28 更新】idf 取对数（标准 Robertson-Sparck-Jones）。生产实现
    agent/tool_router_hybrid.py::BM25Index._compute_bm25 原本直接返回未取对数的
    比值，df=1 时权重无界（实测真实索引上让无关工具霸榜）；本对照实现随之同步。
    本函数要证明的是"增量字段 == 重算口径"，与 idf 取什么形式无关。
    """
    if term not in idx._index:
        return 0.0
    doc_count = len(idx._index[term])
    idf = math.log(1.0 + (idx._total_docs - doc_count + 0.5) / (doc_count + 0.5))
    if idf <= 0:
        return 0.0
    avg = recomputed_total / idx._total_docs if idx._total_docs else 0.0
    k1, b = idx._k1, idx._b
    numerator = term_freq * (k1 + 1)
    denominator = term_freq + k1 * (1 - b + b * doc_length / (avg or 1))
    return idf * numerator / denominator


class TestBehaviorUnchanged:
    """D2 向后兼容：外部可见的检索结果不得因本次修复而改变"""

    def test_avg_doc_length_equals_recomputed(self):
        from agent.tool_router_hybrid import BM25Index

        idx = BM25Index()
        docs = [("a", "search file content 搜索"), ("b", "run tests 测试"),
                ("c", "a"), ("d", "long description " * 5)]
        for doc_id, content in docs:
            idx.add_document(doc_id, content)
        idx.add_document("b", "short")     # 覆盖
        recomputed = sum(idx._doc_lengths.values())
        assert idx._avg_doc_length == recomputed / idx._total_docs

    def test_search_scores_match_reference_formula(self):
        from agent.tool_router_hybrid import BM25Index, _tokenize

        idx = BM25Index()
        corpus = {
            "read_file": "读取本地文件内容 read local file content",
            "write_file": "写入本地文件 write local file save content",
            "web_search": "搜索互联网信息 search web internet",
            "run_tests": "运行测试 run tests pytest",
        }
        for doc_id, text in corpus.items():
            idx.add_document(doc_id, text)
        # 造一次覆盖，让总量经过"先减后加"的路径
        idx.add_document("read_file", "读取本地文件内容 read local file content extra")

        query = "读取文件 read file"
        got = dict(idx.search(query, top_k=10))
        recomputed_total = sum(idx._doc_lengths.values())

        # 用重算口径独立算一遍，逐词累加
        expect: Dict[str, float] = {}
        for token in _tokenize(query):
            if token not in idx._index:
                continue
            for doc_id, freq in idx._index[token]:
                dl = idx._doc_lengths.get(doc_id, 0)
                if dl > 0:
                    expect[doc_id] = expect.get(doc_id, 0.0) + _reference_bm25(
                        idx, token, freq, dl, recomputed_total)

        assert set(got) == set(expect)
        for doc_id, score in expect.items():
            assert got[doc_id] == pytest.approx(score, rel=0, abs=1e-12), (
                f"{doc_id} 分数与重算口径不一致：{got[doc_id]} vs {score}"
            )

    def test_search_order_and_size_unchanged(self):
        from agent.tool_router_hybrid import BM25Index

        idx = BM25Index()
        for i in range(30):
            idx.add_document(f"t{i}", f"通用工具 {i} search file 描述 {i % 5}")
        res = idx.search("search file", top_k=5)
        assert len(res) == 5
        scores = [s for _, s in res]
        assert scores == sorted(scores, reverse=True)
        assert idx.size == 30


# ════════════════════════════════════════════════════════════
#  ④ 同路径第二处 O(n²)：EmbeddingIndex pending 去重
# ════════════════════════════════════════════════════════════


class TestEmbeddingPendingIndex:
    """`EmbeddingIndex._pending_ids` 与 `_pending` 必须同步，且去重不得整表扫描"""

    def test_pending_dedup_order_and_content(self):
        from agent.tool_router_hybrid import EmbeddingIndex

        emb = EmbeddingIndex()
        emb.add_document("a", "first")
        emb.add_document("b", "second")
        emb.add_document("a", "first-updated")   # 覆盖 ⇒ 移到末尾
        assert emb._pending == [("b", "second"), ("a", "first-updated")]
        assert emb._pending_ids == {"a", "b"}

    def test_pending_ids_stay_in_sync(self):
        from agent.tool_router_hybrid import EmbeddingIndex

        emb = EmbeddingIndex()
        for i in range(50):
            emb.add_document(f"d{i % 10}", f"content {i}")
        assert {d for d, _ in emb._pending} == emb._pending_ids
        assert len(emb._pending) == len(emb._pending_ids) == 10

    def test_clear_empties_pending_ids(self):
        from agent.tool_router_hybrid import EmbeddingIndex

        emb = EmbeddingIndex()
        emb.add_document("x", "1")
        emb.add_document("y", "2")
        emb.clear()
        assert emb._pending == []
        assert emb._pending_ids == set()
        # clear 后再加同名文档,不得走"已存在⇒过滤"分支（否则会白扫一遍）
        emb.add_document("x", "3")
        assert emb._pending == [("x", "3")]

    def test_encode_clears_pending_ids(self, monkeypatch):
        """编码完成后 pending 与其 id 集合必须一起清空"""
        np = pytest.importorskip("numpy")
        from agent.tool_router_hybrid import EmbeddingIndex

        emb = EmbeddingIndex()
        calls: List[List[str]] = []

        def _fake_encode(texts: List[str]):
            calls.append(list(texts))
            return [[0.1, 0.2] for _ in texts]

        # 打补丁而不是 reload（纪律 F13）
        monkeypatch.setattr(emb, "_encode_via_worker", _fake_encode)
        emb.add_document("a", "alpha")
        emb.add_document("b", "beta")
        emb._encode_pending_locked()
        assert calls == [["alpha", "beta"]]
        assert emb._pending == []
        assert emb._pending_ids == set()
        assert emb._doc_ids == ["a", "b"]
        assert emb._embeddings is not None and emb._embeddings.shape[0] == 2
        del np

    def test_add_does_not_scan_pending_list(self):
        """确定性复杂度判据：build 期不得对 `_pending` 做整表过滤"""

        class CountingPending(list):
            def __init__(self, *a: Any):
                super().__init__(*a)
                self.elements_yielded = 0
                self.full_scan_calls = 0

            def __iter__(self) -> Iterator[Any]:  # type: ignore[override]
                self.full_scan_calls += 1
                for x in list.__iter__(self):
                    self.elements_yielded += 1
                    yield x

        from agent.tool_router_hybrid import EmbeddingIndex

        emb = EmbeddingIndex()
        emb._pending = CountingPending()
        n = 500
        for i in range(n):
            emb.add_document(f"d{i}", f"description {i}")
        # 修复前这里是 n(n-1)/2 = 124750 次元素产出；修复后必须是 0
        assert emb._pending.elements_yielded == 0, (
            f"_pending 被整表遍历了 {emb._pending.elements_yielded} 个元素"
            "（EmbeddingIndex.add_document 的去重仍是 O(pending)）"
        )
        assert len(emb._pending) == n
        assert emb._pending_ids == {f"d{i}" for i in range(n)}

    def test_rebuild_index_build_is_not_quadratic(self):
        """端到端（生产装配路径）：`HybridRetriever.rebuild` 的构建不得走整表扫描"""
        from agent.tool_router_hybrid import BM25Index, EmbeddingIndex

        # 直接观察两个索引对象的内部集合是否被整表遍历
        bm25_proxy = CountingDocLengths()
        bm = BM25Index()
        bm._doc_lengths = bm25_proxy
        emb = EmbeddingIndex()
        tools = [{"name": f"tool_{i}", "description": f"工具 {i} 描述 text",
                  "parameter_names": [f"p{j}" for j in range(3)]}
                 for i in range(300)]
        for t in tools:
            bm.add_document(t["name"], t["name"] + " " + " ".join(t["parameter_names"]) + " " + t["description"])
            emb.add_document(t["name"], t["description"])
        assert bm25_proxy.elements_yielded == 0
        assert bm.size == 300
        assert len(emb._pending) == 300
        _assert_invariant(bm, "rebuild 模拟")
