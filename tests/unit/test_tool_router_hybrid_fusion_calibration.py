# -*- coding: utf-8 -*-
"""W5/L27 融合分「可校准」回归 —— 融合分不得退化为常数

背景（TASK-10 实测 / 裁定 D-20260921-16）
----------------------------------------
`HybridRetriever._query_locked` 原用 `_min_max_normalize` 归一化 BM25 / Embedding
两路分数：**每路的 max 被强映射为 1.0**。于是只要某路给出候选，该路 top1 恒为 1.0，
真实索引上实测 `fused_top_scores=[1.0]` —— 在这条分数上做 ECE 或阈值拒识
**等价于「永不拒识」**，语义层的「可验证可靠性」结构性不可实现。

本文件守住三件事（对应 L27 的验收）
----------------------------------
1. 校准**单调** ⇒ 不扰动既有排序：Embedding 不可用的降级路上，
   `query()` 的顺序必须与 raw BM25 的顺序逐位一致（改前 min-max 同样保序，
   故这条断言证明「排序未被意外打乱」）；
2. 校准**查询无关** ⇒ 分数可跨查询比较，top1 不再是不变的常数，且恒 < 1.0；
3. `AGENT_HYBRID_ALPHA` 的**融合权重语义与默认值 0.5 不变**（本任务不得改权重）。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REAL_INDEX = ROOT / "data" / "tool_index.json"


@pytest.fixture(autouse=True)
def _disable_embedding_probe(monkeypatch):
    """禁用 Embedding 探测：走纯 BM25 降级路（秒级、无子进程/模型加载）"""
    monkeypatch.setenv("AGENT_HYBRID_EMBEDDING", "0")


@pytest.fixture(autouse=True)
def _reset_hybrid_singleton():
    from agent.tool_router_hybrid import reset_hybrid_retriever
    import agent.tool_router_hybrid as mod

    reset_hybrid_retriever()
    mod._PROBE_RESULT = None
    yield
    reset_hybrid_retriever()
    mod._PROBE_RESULT = None


# ════════════════════════════════════════════════════════════
#  ① 校准函数本身：单调 + 有界 + 不把 max 钉在 1.0
# ════════════════════════════════════════════════════════════

class TestCalibrationShape:
    def test_bm25_calibration_is_monotone_and_bounded(self):
        from agent.tool_router_hybrid import _BM25_HALF_SATURATION, _calibrate_bm25_scores

        raw = [("a", 1.0), ("b", 10.0), ("c", 100.0)]
        got = _calibrate_bm25_scores(raw)
        assert [d for d, _ in got] == ["a", "b", "c"], "校准不得改变本路顺序（保序性）"
        vals = [v for _, v in got]
        assert vals[0] < vals[1] < vals[2], "必须严格单调"
        assert all(0.0 <= v < 1.0 for v in vals), "有界且**恒不等于 1.0**"
        # 半饱和点语义：s == S0 时恰好 0.5
        assert _calibrate_bm25_scores([("x", _BM25_HALF_SATURATION)])[0][1] == pytest.approx(0.5)

    def test_bm25_calibration_does_not_pin_max_to_one(self):
        """核心回归：同一批分数里 max **不得**被映射成 1.0（min-max 的老行为）"""
        from agent.tool_router_hybrid import _calibrate_bm25_scores

        got = dict(_calibrate_bm25_scores([("big", 500.0), ("small", 3.0)]))
        assert got["big"] < 1.0, f"最大值被钉成 1.0 ⇒ 分数不可校准（L27 复发）: {got}"
        assert got["small"] > 0.0, "最小值不得被钉成 0.0"
        # 不同证据强度必须得到**不同**的分：这是 ECE/拒识可用的前提
        assert got["big"] != got["small"]

    def test_bm25_calibration_is_query_independent(self):
        """同一 raw 分在任何一批候选里都必须得到同一个校准分（查询无关）"""
        from agent.tool_router_hybrid import _calibrate_bm25_scores

        alone = dict(_calibrate_bm25_scores([("x", 24.34)]))["x"]
        with_others = dict(_calibrate_bm25_scores(
            [("x", 24.34), ("y", 72.47), ("z", 0.5)]))["x"]
        assert alone == with_others, (
            f"校准分随候选集合变化（={alone} vs {with_others}）⇒ 参考尺度仍是 per-query 极值，"
            "跨查询不可比，ECE/拒识失去意义"
        )

    def test_cosine_calibration_maps_cutoff_to_zero(self):
        from agent.tool_router_hybrid import _COSINE_CUTOFF, _calibrate_cosine_scores

        got = dict(_calibrate_cosine_scores([("lo", _COSINE_CUTOFF), ("hi", 1.0)]))
        assert got["lo"] == pytest.approx(0.0)
        assert got["hi"] == pytest.approx(1.0)
        assert all(0.0 <= v <= 1.0 for v in got.values())

    def test_min_max_helper_still_exists_but_is_not_the_fusion_path(self):
        """对外符号不删（老调用方可 import），但其契约已不再用于融合"""
        from agent.tool_router_hybrid import _min_max_normalize

        assert _min_max_normalize([("d1", 1.0), ("d2", 5.0)]) == [("d1", 0.0), ("d2", 1.0)]


# ════════════════════════════════════════════════════════════
#  ② 真实索引：融合分不再退化 + 排序未被扰动
# ════════════════════════════════════════════════════════════

@pytest.fixture
def real_retriever():
    if not REAL_INDEX.exists():
        pytest.skip(f"真实索引缺失: {REAL_INDEX}")
    from agent.tool_router_hybrid import HybridRetriever

    return HybridRetriever(alpha=0.5, index_path=str(REAL_INDEX))


class TestRealIndexFusedScores:
    QUERIES = ["extract text from pdf", "解析pdf", "合并pdf", "查询天气", "搜索网页"]

    def test_fused_top1_scores_are_not_a_constant(self, real_retriever):
        """L27 本体：多条查询的融合 top1 分不得全部相同（改前恒为 [1.0]）"""
        tops = []
        for q in self.QUERIES:
            res = real_retriever.query(q, top_k=10)
            assert res, f"查询 {q!r} 应有结果"
            tops.append(round(res[0][1], 9))
        assert len(set(tops)) > 1, f"融合 top1 分退化为例外常数 {set(tops)}（L27 复发）"
        assert all(0.0 < t < 1.0 for t in tops), f"校准分必须落在 (0,1)：{tops}"
        # 并把 raw 分量纲一并断言：校准分可回溯到原始证据
        stats = real_retriever._last_query_stats
        assert stats.get("raw_bm25_top5"), "必须透出归一化前的 raw BM25 分"
        assert stats.get("bm25_half_saturation") and stats.get("cosine_floor")

    def test_degraded_path_order_matches_raw_bm25(self, real_retriever):
        """单调校准 ⇒ 降级路上融合顺序与 raw BM25 顺序逐位一致（不扰动既有排序）"""
        assert real_retriever.degraded is True, "本环境须走 BM25-only 降级路"
        for q in self.QUERIES + ["读取pdf文件的内容", "跑个 shell command"]:
            fused = [d for d, _ in (real_retriever.query(q, top_k=10) or [])]
            raw = [d for d, _ in real_retriever._bm25.search(q, top_k=10)]
            assert fused == raw, f"查询 {q!r} 排序被扰动：融合 {fused} vs raw {raw}"

    def test_alpha_default_is_unchanged(self, real_retriever, monkeypatch):
        """AGENT_HYBRID_ALPHA 的默认值（0.5）与语义不得被本任务改动"""
        from agent.tool_router_hybrid import _DEFAULT_ALPHA, _resolve_alpha_from_env

        assert _DEFAULT_ALPHA == 0.5, "融合权重默认值被改动（本任务禁止）"
        monkeypatch.delenv("AGENT_HYBRID_ALPHA", raising=False)
        assert _resolve_alpha_from_env() == 0.5, "环境变量缺省时必须回落到 0.5"
        assert real_retriever._alpha == 0.5


# ════════════════════════════════════════════════════════════
#  ③ alpha 仍是融合权重（用受控 Embedding 桩验证等权语义）
# ════════════════════════════════════════════════════════════

class _StubEmbedding:
    """受控 Embedding 路：返回固定余弦分，用于验证融合公式本身"""

    def __init__(self, scores):
        self._scores = scores

    @property
    def available(self):
        return True

    def search(self, text, top_k=10):
        return sorted(self._scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def clear(self):
        self._scores = {}


class TestAlphaStillIsFusionWeight:
    TOOLS = [
        {"name": "alpha_tool", "description": "alpha 描述 搜索 天气", "parameter_names": ["q"]},
        {"name": "beta_tool", "description": "beta 描述 文件 读取", "parameter_names": ["p"]},
    ]

    def _retriever(self, tmp_path, alpha):
        from agent.tool_router_hybrid import HybridRetriever

        p = tmp_path / "tool_index.json"
        p.write_text(json.dumps({"tools": self.TOOLS}, ensure_ascii=False), encoding="utf-8")
        r = HybridRetriever(alpha=alpha, index_path=str(p))
        assert r.available and r.degraded is True
        # 构造后换入受控 Embedding 桩（rebuild 已完成，不会再被 clear 掉）
        r._embedding = _StubEmbedding({"alpha_tool": 0.9, "beta_tool": 0.3})
        return r

    def test_fused_is_weighted_sum_of_calibrated_paths(self, tmp_path):
        from agent.tool_router_hybrid import (
            _calibrate_bm25_scores, _calibrate_cosine_scores, _COSINE_CUTOFF,
        )

        q = "搜索 天气 文件"
        r = self._retriever(tmp_path, 0.5)
        got = dict(r.query(q, top_k=5))
        bm = dict(_calibrate_bm25_scores(r._bm25.search(q, top_k=10)))
        em = dict(_calibrate_cosine_scores(
            [(d, s) for d, s in r._embedding.search(q, top_k=10) if s >= _COSINE_CUTOFF]))
        for doc in set(bm) | set(em):
            expect = 0.5 * bm.get(doc, 0.0) + 0.5 * em.get(doc, 0.0)
            assert got[doc] == pytest.approx(expect, abs=1e-12), f"{doc} 融合公式被改动"

    def test_alpha_one_is_pure_bm25_and_zero_is_pure_embedding(self, tmp_path):
        from agent.tool_router_hybrid import (
            _calibrate_bm25_scores, _calibrate_cosine_scores, _COSINE_CUTOFF,
        )

        q = "搜索 天气 文件"
        r1 = self._retriever(tmp_path, 1.0)
        bm = dict(_calibrate_bm25_scores(r1._bm25.search(q, top_k=10)))
        got1 = dict(r1.query(q, top_k=5))
        assert got1["alpha_tool"] == pytest.approx(bm["alpha_tool"], abs=1e-12), (
            "alpha=1 必须退化为纯 BM25（权重语义不变）")

        r0 = self._retriever(tmp_path, 0.0)
        em = dict(_calibrate_cosine_scores(
            [(d, s) for d, s in r0._embedding.search(q, top_k=10) if s >= _COSINE_CUTOFF]))
        got0 = dict(r0.query(q, top_k=5))
        assert got0["alpha_tool"] == pytest.approx(em["alpha_tool"], abs=1e-12), (
            "alpha=0 必须退化为纯 Embedding（权重语义不变）")


# ════════════════════════════════════════════════════════════
#  ④ S0 的可复算性（W5 复核 A2/A3：取值必须有受跟踪产物 + 每次重算）
# ════════════════════════════════════════════════════════════

class TestS0Provenance:
    """`_BM25_HALF_SATURATION` 必须能从受跟踪产物**复算**出来

    复核 A3 的判据：注释里的分位数若无仓库内出处，就是不可复现的常数。
    本类每次运行都重算 calib 划分的 n/p10/p50/p90/max，并与
    `eval/routing_baseline/bm25_raw_top1.json` 逐条比对；S0 取该中位数。
    索引、分词或 idf 一变，这里立刻红。
    """

    ARTIFACT = ROOT / "eval" / "routing_baseline" / "bm25_raw_top1.json"
    CASES = ROOT / "eval" / "routing_baseline" / "cases.json"

    @staticmethod
    def _split_ids(spec):
        """复刻 scripts/run_routing_reliability.py:378-395 的分层随机划分"""
        import random

        cfg = spec["split"]
        rng = random.Random(cfg["seed"])
        scored = [c for c in spec["cases"] if not c.get("stale")]
        layers = ["rule", "template", "keyword_category", "hybrid_tool"]
        calib, test = set(), set()
        for L in layers:
            group = {}
            for r in scored:
                if r["layer"] != L:
                    continue
                group.setdefault(json.dumps(r.get("gold"), ensure_ascii=False,
                                           sort_keys=True), []).append(r["id"])
            for _key, ids in group.items():
                ids = sorted(ids)
                rng.shuffle(ids)
                k = max(1, int(round(len(ids) * cfg["calib_ratio"]))) if len(ids) > 1 else 0
                calib.update(ids[:k])
                test.update(ids[k:])
        return calib - test, test

    @pytest.fixture(scope="class")
    def artifact(self):
        assert self.ARTIFACT.exists(), (
            f"缺少受跟踪产物 {self.ARTIFACT}（S0 将不可复算）")
        return json.loads(self.ARTIFACT.read_text(encoding="utf-8"))

    def test_artifact_split_matches_recomputed_split(self, artifact):
        spec = json.loads(self.CASES.read_text(encoding="utf-8"))
        calib, test = self._split_ids(spec)
        assert sorted(calib) == artifact["split"]["calib_ids"], "calib 划分已漂移"
        assert sorted(test) == artifact["split"]["test_ids"], "test 划分已漂移"
        assert len(calib) == 33 and len(test) == 42, "划分规模应与 report.json 声明一致"

    def test_artifact_scores_match_recomputed_bm25(self, artifact):
        import hashlib

        from agent.tool_router_hybrid import BM25Index

        index_path = ROOT / "data" / "tool_index.json"
        # 【跨平台】与生成器同口径：按 LF 归一化后哈希（理由见 gen_baseline_bm25_raw.py 的说明）
        sha = hashlib.sha256(
            index_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]
        assert sha == artifact["scoring_口径"]["index_sha256_16"], (
            "data/tool_index.json 已变化 ⇒ 产物过期，须重新导出")

        tools = json.loads(index_path.read_text(encoding="utf-8"))["tools"]
        bm = BM25Index()
        for t in tools:
            bm.add_document(t["name"], t["name"] + " " + " ".join(t.get("parameter_names") or [])
                            + " " + t.get("description", ""))
        for row in artifact["per_case"]:
            got = bm.search(row["text"], top_k=5)
            got_top1 = round(got[0][1], 6) if got else None
            assert got_top1 == row["raw_bm25_top1"], (
                f"{row['id']} 的 raw BM25 top1 与产物不一致：{got_top1} vs {row['raw_bm25_top1']}")

    def test_s0_equals_calib_median_from_artifact(self, artifact):
        from agent.tool_router_hybrid import _BM25_HALF_SATURATION

        calib_ids = set(artifact["split"]["calib_ids"])
        vals = sorted(r["raw_bm25_top1"] for r in artifact["per_case"]
                      if r["id"] in calib_ids and r["raw_bm25_top1"] is not None)
        assert len(vals) == artifact["stats"]["bigram_log"]["calib_all_layers"]["n_with_hits"]
        median = round(statistics.median(vals), 4)
        assert _BM25_HALF_SATURATION == median, (
            f"S0({_BM25_HALF_SATURATION}) != calib 划分 raw BM25 top1 中位数({median})")

    def test_artifact_reports_the_percentiles_cited_in_source(self, artifact):
        """代码注释引用的 n/p10/p50/p90/max 必须与产物逐项相同（防假注释）"""
        st = artifact["stats"]["bigram_log"]["calib_all_layers"]
        assert (st["n_with_hits"], st["p10"], st["p50_median"], st["p90"], st["max"]) == (
            11, 3.0472, 5.5375, 10.9457, 11.408), f"注释引用的分位数与产物不符：{st}"

    def test_uni_log_caliber_is_also_reproducible(self, artifact, monkeypatch):
        """提交1（仅对数 idf、单字分词）口径同样可复算（A1-b-prime 需要该口径的 S0）"""
        import re as _re

        import agent.tool_router_hybrid as mod
        from agent.tool_router_hybrid import BM25Index

        old_re = _re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")
        monkeypatch.setattr(mod, "_tokenize", lambda t: old_re.findall((t or "").lower()))
        index_path = ROOT / "data" / "tool_index.json"
        bm = BM25Index()
        for t in json.loads(index_path.read_text(encoding="utf-8"))["tools"]:
            bm.add_document(t["name"], t["name"] + " " + " ".join(t.get("parameter_names") or [])
                            + " " + t.get("description", ""))
        for row in artifact["per_case"]:
            got = bm.search(row["text"], top_k=5)
            got_top1 = round(got[0][1], 6) if got else None
            assert got_top1 == row["raw_bm25_top1_uni_log"], (
                "uni+log 口径 raw top1 与产物不一致: %s -> %s vs %s"
                % (row["id"], got_top1, row["raw_bm25_top1_uni_log"]))
        calib_ids = set(artifact["split"]["calib_ids"])
        vals = sorted(r["raw_bm25_top1_uni_log"] for r in artifact["per_case"]
                      if r["id"] in calib_ids and r["raw_bm25_top1_uni_log"] is not None)
        median = round(statistics.median(vals), 4)
        assert artifact["S0_derivation"]["commit1_uni_log"] == median, "提交1 口径的 S0 与产物不符"
        assert artifact["stats"]["uni_log"]["calib_all_layers"]["p50_median"] == median

    def test_declared_value_used_matches_the_constant(self, artifact):
        """把「产物里声明用的值」纳入守护（复核：原产物 value_used=61.5 与常量/统计双双矛盾）"""
        from agent.tool_router_hybrid import _BM25_HALF_SATURATION

        used = artifact["S0_derivation"]["value_used"]
        assert used == _BM25_HALF_SATURATION, (
            "产物 S0_derivation.value_used=%s 与代码常量 %s 不一致 —— 产物把核心主张写反了"
            % (used, _BM25_HALF_SATURATION))
        assert used == artifact["stats"]["bigram_log"]["calib_all_layers"]["p50_median"], (
            "产物自相矛盾：value_used=%s 与其自身算出的 calib 中位数不符" % (used,))

    def test_artifact_is_rebuildable_from_tracked_generator(self, artifact):
        """产物**可重建**（而不只是数值可复算）：生成器已入库，重跑须逐位一致

        复核意见：原产物自述「临时生成器」⇒ 数值可复算但产物不可重建。
        本断言用入库的 scripts/gen_baseline_bm25_raw.py 重建并逐位比对（仅忽略 generated_at）。
        """
        import importlib.util

        gen = ROOT / "scripts" / "gen_baseline_bm25_raw.py"
        assert gen.exists(), "生成器未入库 ⇒ 产物不可重建"
        spec = importlib.util.spec_from_file_location("gen_baseline_bm25_raw", str(gen))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rebuilt = module.build_document()
        a = dict(artifact)
        rebuilt.pop("generated_at", None)
        a.pop("generated_at", None)
        assert rebuilt == a, (
            "产物与入库生成器的输出不一致：键差异=%s"
            % (sorted(set(rebuilt) ^ set(a)) or [k for k in a if a[k] != rebuilt.get(k)],))

    def test_s0_is_derived_from_calib_split_only(self, artifact):
        """A3 的泄漏判据：S0 **不得**取自含 test 划分的口径"""
        from agent.tool_router_hybrid import _BM25_HALF_SATURATION

        st = artifact["stats"]["bigram_log"]
        assert _BM25_HALF_SATURATION == st["calib_all_layers"]["p50_median"]
        assert _BM25_HALF_SATURATION != st["test_all_layers"]["p50_median"], "S0 不得取自 test 划分"
        assert _BM25_HALF_SATURATION != st["all_all_layers"]["p50_median"], (
            "S0 取自全集（含 test）⇒ 后续 ECE 存在泄漏")
