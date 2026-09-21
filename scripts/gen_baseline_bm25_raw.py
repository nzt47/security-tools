#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建 eval/routing_baseline/bm25_raw_top1.json（S0 可复算的唯一生成入口）

定位
----
W5/L27/L28 的融合分把"半饱和点" S0 锚在 `BM25 raw top1 的中位数` 上。为了让这个
常数**可被第三方复算**，本脚本把逐条 raw BM25 top1 落盘为受跟踪产物，并同时给出
calib / test / 全集三种划分的分位数。复核 A3 的要件：常数不得无出处。

为什么必须入库（复核意见）
--------------------------
该产物原先由临时脚本产出、脚本未入库 ⇒ 数值可复算、但**产物本身不可重建**。
本文件就是那个生成器：零新依赖、只读 `eval/routing_baseline/cases.json` 与
`data/tool_index.json`，写出的内容对同一输入逐位可复现（除 generated_at）。

双口径
------
- `bigram_log`：提交2 起的生产口径（CJK 相邻二元组 + 对数 idf + idf 证据下限）
- `uni_log`   ：提交1 的口径（单字 + 对数 idf + 同一下限），供 (b') 的分步提交使用
两列各自给 S0，避免"取哪个口径"成为口头约定。

用法：python scripts/gen_baseline_bm25_raw.py [--out <path>]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import random
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault("AGENT_HYBRID_EMBEDDING", "0")  # 纯 BM25：不启动 Embedding 子进程

import agent.tool_router_hybrid as mod
from agent.tool_router_hybrid import (
    _BM25_HALF_SATURATION, _MIN_IDF_COVERAGE, BM25Index,
)

CASES = ROOT / "eval" / "routing_baseline" / "cases.json"
INDEX = ROOT / "data" / "tool_index.json"
DEFAULT_OUT = ROOT / "eval" / "routing_baseline" / "bm25_raw_top1.json"

_ASCII = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")
_SINGLE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")
LAYERS = ["rule", "template", "keyword_category", "hybrid_tool"]


def tokenize_bigram(text: str) -> List[str]:
    """生产口径：CJK 相邻二元组 + ASCII 整词（与 agent/tool_router_hybrid._tokenize 等价）"""
    toks: List[str] = []
    for seg in _ASCII.findall((text or "").lower()):
        if len(seg) > 1 and not seg.isascii():
            toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            toks.append(seg)
    return toks


def tokenize_unigram(text: str) -> List[str]:
    """提交1 口径：CJK 单字（改前分词）"""
    return _SINGLE.findall((text or "").lower())


def split_ids(spec: Dict[str, Any]):
    """复刻 scripts/run_routing_reliability.py:378-395 的分层随机划分（同 seed 同顺序）"""
    cfg = spec["split"]
    rng = random.Random(cfg["seed"])
    scored = [c for c in spec["cases"] if not c.get("stale")]
    calib, test = set(), set()
    for layer in LAYERS:
        group: Dict[str, List[str]] = {}
        for row in scored:
            if row["layer"] != layer:
                continue
            key = json.dumps(row.get("gold"), ensure_ascii=False, sort_keys=True)
            group.setdefault(key, []).append(row["id"])
        for _key, ids in group.items():
            ids = sorted(ids)
            rng.shuffle(ids)
            k = max(1, int(round(len(ids) * cfg["calib_ratio"]))) if len(ids) > 1 else 0
            calib.update(ids[:k])
            test.update(ids[k:])
    return calib - test, test


def percentiles(values: List[float]) -> Dict[str, Any]:
    """分位数：p10/p90 用最近秩，p50 用**真中位数**（偶数个取中间两值平均）

    复核备忘：最近秩与真中位数是两个不同定义，比较前必须先核定义。
    """
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n_with_hits": 0}
    nearest = lambda p: vals[min(len(vals) - 1, int(p * (len(vals) - 1)))]
    return {
        "n_with_hits": len(vals),
        "min": round(vals[0], 4),
        "p10": round(nearest(.10), 4),
        "p50_median": round(statistics.median(vals), 4),
        "p90": round(nearest(.90), 4),
        "max": round(vals[-1], 4),
        "mean": round(statistics.mean(vals), 4),
    }


def _measure(bm: BM25Index, cases: List[Dict[str, Any]], calib_ids) -> List[Dict[str, Any]]:
    rows = []
    for c in cases:
        res = bm.search(c["text"], top_k=5)
        rows.append({
            "id": c["id"], "layer": c["layer"], "text": c["text"], "gold": c.get("gold"),
            "split": ("stale" if c.get("stale") else ("calib" if c["id"] in calib_ids else "test")),
            "raw_bm25_top1": round(res[0][1], 6) if res else None,
            "raw_bm25_second": round(res[1][1], 6) if len(res) > 1 else None,
            "raw_bm25_top5": [[d, round(s, 6)] for d, s in res],
            "top1_tool": res[0][0] if res else None,
        })
    return rows


def _ratio_idf(self, term: str, term_freq: int, doc_length: int) -> float:
    """改前的 idf 形态：**未取对数的比值**（提交2 之前的生产实现）

    Why 需要它：commit1（L27 单独）不改 idf，其 S0 必须按"单字 + 比值 idf"口径导出，
    否则 commit1 的常数又会变成"无出处的字面量"（正是 §1.2 打的那一点）。
    """
    if term not in self._index:
        return 0.0
    doc_count = len(self._index[term])
    idf = (self._total_docs - doc_count + 0.5) / (doc_count + 0.5)
    if idf <= 0:
        return 0.0
    return idf * term_freq * (self._k1 + 1) / (
        term_freq + self._k1 * (1 - self._b + self._b * doc_length / (self._avg_doc_length or 1)))


def _run_caliber(tok, idf_mode, tools, cases, calib_ids):
    """建索引 + 检索**全程**同一口径（分词 + idf 同时锁定，否则产物与口径不符）"""
    old_tok = mod._tokenize
    old_idf = BM25Index._compute_bm25
    mod._tokenize = tok
    if idf_mode == "ratio":
        BM25Index._compute_bm25 = _ratio_idf
    try:
        bm = BM25Index()
        for name, content in tools:
            bm.add_document(name, content)
        return bm, _measure(bm, cases, calib_ids)
    finally:
        mod._tokenize = old_tok
        BM25Index._compute_bm25 = old_idf


def _split_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for kind in ("calib", "test", "all"):
        sel = rows if kind == "all" else [r for r in rows if r["split"] == kind]
        out["%s_all_layers" % kind] = percentiles([r["raw_bm25_top1"] for r in sel])
        if kind == "calib":
            out["calib_hybrid_tool_only"] = percentiles(
                [r["raw_bm25_top1"] for r in sel if r["layer"] == "hybrid_tool"])
    return out


def build_document(caliber: str = "bigram_log") -> Dict[str, Any]:
    """产出产物 dict（不落盘）：测试直接调它即可验证"产物可重建"

    Args:
        caliber: 决定 `S0_derivation.value_used` 取哪一列 —— 必须与**当前生产代码的
            分词口径**一致，否则产物会与代码常量分岔（复核 A2/A3 的那类自相矛盾）。
            "bigram"（提交2 起，默认）/ "uni"（提交1 态）。
    """
    spec = json.loads(CASES.read_text(encoding="utf-8"))
    calib_ids, test_ids = split_ids(spec)
    index_data = json.loads(INDEX.read_text(encoding="utf-8"))
    tools = [
        (t["name"],
         t["name"] + " " + " ".join(t.get("parameter_names") or []) + " " + t.get("description", ""))
        for t in index_data["tools"]
    ]
    # 三个口径：提交2 起的生产态(bigram+log)、提交1 的分词态(uni+log)、
    # 以及**改前基线**(uni+ratio) —— 后者是 commit1(L27 单独) 的 S0 出处。
    bm_bi, rows_bi = _run_caliber(tokenize_bigram, "log", tools, spec["cases"], calib_ids)
    bm_uni, rows_uni = _run_caliber(tokenize_unigram, "log", tools, spec["cases"], calib_ids)
    _bm_ur, rows_ur = _run_caliber(tokenize_unigram, "ratio", tools, spec["cases"], calib_ids)
    st_bi, st_uni, st_ur = _split_stats(rows_bi), _split_stats(rows_uni), _split_stats(rows_ur)
    s0_bi = st_bi["calib_all_layers"]["p50_median"]
    s0_uni = st_uni["calib_all_layers"]["p50_median"]
    s0_ur = st_ur["calib_all_layers"]["p50_median"]
    s0_by_caliber = {"bigram_log": s0_bi, "uni_log": s0_uni, "uni_ratio": s0_ur}
    used = s0_by_caliber[caliber]

    return {
        "schema": "w5/l28-bm25-raw-top1/v2",
        "task": "W5 批次1-B：L27/L28/A8 —— S0 可复算的证据落盘（双口径，供两次提交各用一份）",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "generator": "scripts/gen_baseline_bm25_raw.py（已入库；python scripts/gen_baseline_bm25_raw.py 可重建本产物）",
        "scoring_口径": {
            "current": "CJK 相邻二元组 + log idf + idf 证据下限",
            "tokenizer_commit2": "bigram（agent/tool_router_hybrid.py::_tokenize）",
            "tokenizer_commit1": "unigram 单字（本文件 uni_log / uni_ratio 两列）",
            "idf_commit1": "未取对数的比值 idf（uni_ratio 列 = commit1/L27 单独 的 S0 出处）",
            "idf": "log(1 + (N-df+0.5)/(df+0.5))（bigram_log / uni_log 列）",
            "idf": "log(1 + (N-df+0.5)/(df+0.5))",
            "min_idf_coverage": _MIN_IDF_COVERAGE,
            "k1": bm_bi._k1,
            "b": bm_bi._b,
            "index_source": "data/tool_index.json",
            "index_sha256_16": hashlib.sha256(INDEX.read_bytes()).hexdigest()[:16],
            "n_docs": bm_bi.size,
            "avg_doc_length_bigram": round(bm_bi._avg_doc_length, 4),
            "avg_doc_length_unigram": round(bm_uni._avg_doc_length, 4),
        },
        "split": {
            "source": "eval/routing_baseline/cases.json#split",
            "seed": spec["split"]["seed"], "method": spec["split"].get("method"),
            "n_calib": len(calib_ids), "n_test": len(test_ids),
            "calib_ids": sorted(calib_ids), "test_ids": sorted(test_ids),
        },
        "stats": {"bigram_log": st_bi, "uni_log": st_uni, "uni_ratio": st_ur},
        "S0_derivation": {
            "rule": "S0 = calib 划分中有 BM25 命中的用例 raw_bm25_top1 的中位数（真中位数）",
            "commit2_bigram_log": s0_bi,
            "commit1_uni_log": s0_uni,
            "commit1_uni_ratio": s0_ur,
            "alt_caliber_hybrid_only_commit2": st_bi["calib_hybrid_tool_only"]["p50_median"],
            # 【复核修正】value_used 必须是**算出来的**（原先写死 61.5 —— 改前单字/比值 idf 时代
            # 的旧值，于是这份"为可复算而生"的产物自己写反了自己的核心主张）。
            # 【分阶段提交】它取 **--caliber 指定列**：提交1（单字口径）用 uni，提交2 用 bigram，
            # 这样产物在任何提交点上都与代码常量同源、不会分岔。
            "value_used": used,
            "value_used_caliber": caliber,
            "note": ("两次提交各用同口径的 S0；value_used 取 --caliber 指定列，须与代码常量一致；"
                     "换 hybrid_tool-only 口径时 S0 变为 alt 值（约 1.8x），该不确定度必须随分数交付"),
        },
        "per_case": [
            dict(r,
                 raw_bm25_top1_uni_log=u["raw_bm25_top1"],
                 raw_bm25_top5_uni_log=u["raw_bm25_top5"],
                 raw_bm25_top1_uni_ratio=z["raw_bm25_top1"],
                 raw_bm25_top5_uni_ratio=z["raw_bm25_top5"])
            for r, u, z in zip(rows_bi, rows_uni, rows_ur)
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="重建 bm25_raw_top1.json（S0 的可复算证据）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出路径（默认仓库内受跟踪产物）")
    ap.add_argument("--caliber", choices=("bigram_log", "uni_log", "uni_ratio"),
                    default="bigram_log",
                    help="value_used 取哪一列：bigram_log（提交2 起，默认）/ "
                         "uni_log（L28 中途态）/ uni_ratio（commit1 = L27 单独）")
    args = ap.parse_args()
    doc = build_document(caliber=args.caliber)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    st = doc["stats"][args.caliber]["calib_all_layers"]
    print("[gen_baseline_bm25_raw] written: %s" % args.out)
    print("  calib n=%d p10=%s p50=%s p90=%s max=%s"
          % (st["n_with_hits"], st["p10"], st["p50_median"], st["p90"], st["max"]))
    print("  caliber=%s" % args.caliber)
    print("  S0(bigram_log)=%s  S0(uni_log)=%s  value_used=%s  (常量 _BM25_HALF_SATURATION=%s)"
          % (doc["S0_derivation"]["commit2_bigram_log"], doc["S0_derivation"]["commit1_uni_log"],
             doc["S0_derivation"]["value_used"], _BM25_HALF_SATURATION))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())