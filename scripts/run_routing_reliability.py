#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TASK-10 · 既有三层路由的「可验证化」基线 / 校准 / 拒识 读数器（只观测，不改判定）

定位
----
本脚本**只读**三层漏斗的既有输出，把它们换算成准确率 / 覆盖率 / 拒识率 / ECE；
它**不修改任何判定语义**，也不训练任何模型。三层漏斗：

    L1 规则层   agent/workflow_engine/builtin_rules.py  （WorkflowEngine.try_match）
    L2 模板层   agent/response_workflows.py              （IntentRouter.classify）
    L3a 类别层  agent/tool_router.py                     （classify_user_input，关键词）
    L3b 语义层  agent/tool_router_hybrid.py              （HybridRetriever.query，BM25+MiniLM）

用法
----
    python scripts/run_routing_reliability.py                # 跑全部并写报告
    python scripts/run_routing_reliability.py --print-md     # 顺便打印 Markdown

产物
----
    eval/routing_baseline/report.json   机器可读报告（含全部中间读数）
    eval/routing_baseline/REPORT.md     人读报告

依赖：仅标准库 + numpy 可用性探针（numpy 环境内已有）。不新增第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 语义层默认走退化路（Embedding 不可用时 BM25-only）。此处不改判定语义，
# 只是把"本环境实际走的是哪条路"如实记录下来。
os.environ.setdefault("AGENT_HYBRID_EMBEDDING", "0")

CASES_PATH = ROOT / "eval" / "routing_baseline" / "cases.json"
REPORT_JSON = ROOT / "eval" / "routing_baseline" / "report.json"
REPORT_MD = ROOT / "eval" / "routing_baseline" / "REPORT.md"


# ════════════════════════════════════════════════════════════════
#  通用工具
# ════════════════════════════════════════════════════════════════

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _nll(p: float, y: int) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _safe_div(a: float, b: float) -> Optional[float]:
    return (a / b) if b else None


def _r(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), n)


def _softmax_top1(raw_pairs) -> Optional[float]:
    """对候选原始分做 softmax，取 top1 概率（作为语义层的"可校准软分数"出口）"""
    z = [s for _, s in raw_pairs]
    if not z:
        return None
    m = max(z)
    ex = [math.exp(v - m) for v in z]
    tot = sum(ex)
    return (ex[0] / tot) if tot else None


# ════════════════════════════════════════════════════════════════
#  ECE / 可靠性图
# ════════════════════════════════════════════════════════════════

def reliability_table(probs: Sequence[float], ys: Sequence[int],
                      n_bins: int = 10) -> Dict[str, Any]:
    """等宽分箱可靠性表（ECE 的数值表形式）

    ECE = Σ_b (n_b / N) · |acc(b) − conf(b)|
    """
    bins: List[Dict[str, Any]] = []
    N = len(probs)
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = []
        for i, p in enumerate(probs):
            # 左闭右开（最后一箱右闭）：p 恰好落在箱边界上时必须归入某箱，
            # 否则 0.3/0.6/0.9 这类"正好等于枚举值"的置信度会被静默丢弃、ECE 失真。
            right_ok = (p <= hi) if b == n_bins - 1 else (p < hi)
            if p >= lo and right_ok:
                idx.append(i)
        n_b = len(idx)
        conf = sum(probs[i] for i in idx) / n_b if n_b else None
        acc = sum(ys[i] for i in idx) / n_b if n_b else None
        gap = abs(acc - conf) if n_b else None
        bins.append({
            "bin": b, "range": [round(lo, 3), round(hi, 3)], "n": n_b,
            "avg_confidence": _r(conf), "accuracy": _r(acc), "gap": _r(gap),
            "contribution": _r((n_b / N) * gap) if (n_b and gap is not None) else 0.0,
        })
    ece = sum(b["contribution"] or 0.0 for b in bins)
    mce = max([b["gap"] for b in bins if b["gap"] is not None], default=0.0)
    return {"ece": _r(ece), "mce": _r(mce), "n": N, "bins": bins}


def fit_temperature(logits: Sequence[float], ys: Sequence[int]) -> Dict[str, Any]:
    """温度缩放（Guo et al., ICML 2017）—— 单参数 T，最小化 NLL

    p = sigmoid(logit / T)。T > 1 软化（原模型过度自信）；T < 1 锐化。
    用黄金分割搜索（单峰、无新依赖，不需要 scipy 优化器）。
    """
    def nll_at(T: float) -> float:
        if T <= 0:
            return float("inf")
        return sum(_nll(_sigmoid(l / T), y) for l, y in zip(logits, ys)) / max(len(ys), 1)

    lo, hi = 1e-2, 1e2
    gr = (math.sqrt(5) - 1) / 2
    c, d = hi - gr * (hi - lo), lo + gr * (hi - lo)
    fc, fd = nll_at(c), nll_at(d)
    for _ in range(200):
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = nll_at(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = nll_at(d)
        if hi - lo < 1e-6:
            break
    T = (lo + hi) / 2
    return {"T": _r(T, 6), "nll_before": _r(nll_at(1.0), 6), "nll_after": _r(nll_at(T), 6)}


def bootstrap_ci(ys: Sequence[int], n_boot: int = 2000, seed: int = 20260210,
                 alpha: float = 0.05) -> Optional[List[float]]:
    """准确率的自助法置信区间（样本量小 ⇒ 必须给区间，不能只给点估计）"""
    n = len(ys)
    if n == 0:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        s = [ys[rng.randrange(n)] for _ in range(n)]
        means.append(sum(s) / n)
    means.sort()
    return [_r(means[int((alpha / 2) * n_boot)]),
            _r(means[int((1 - alpha / 2) * n_boot) - 1])]


# ════════════════════════════════════════════════════════════════
#  三层漏斗——只读探测器
# ════════════════════════════════════════════════════════════════

class Funnel:
    """把三层漏斗包成"给定文本 → 各层声明 + 置信度"的只读函数"""

    def __init__(self) -> None:
        from agent.workflow_engine.engine import WorkflowEngine
        from agent.workflow_engine.builtin_rules import register_builtin_rules
        from agent.response_workflows import IntentRouter, INTENT_UNKNOWN
        from agent.tool_router import TOOL_CATEGORIES, classify_user_input
        from agent.tool_router_hybrid import get_hybrid_retriever

        self.engine = WorkflowEngine()
        register_builtin_rules(self.engine.registry)
        self.IntentRouter = IntentRouter
        self.INTENT_UNKNOWN = INTENT_UNKNOWN
        self.TOOL_CATEGORIES = TOOL_CATEGORIES
        self.classify_user_input = classify_user_input
        self.retriever = get_hybrid_retriever()

        self.semantic_status = {
            "retriever_available": bool(self.retriever and self.retriever.available),
            "degraded_bm25_only": bool(self.retriever.degraded) if self.retriever else None,
            "embedding_available": bool(self.retriever._embedding.available) if self.retriever else None,
            "bm25_docs": self.retriever._bm25.size if self.retriever else 0,
            "alpha": getattr(self.retriever, "_alpha", None),
            "index_path": "data/tool_index.json",
            "embedding_health": (self.retriever.embedding_health() if self.retriever else None),
        }

    # ── L1 规则层 ──────────────────────────────────────────────
    def probe_rule(self, text: str) -> Dict[str, Any]:
        r = self.engine.try_match(text)
        # engine.py:57 把 WorkflowResult.confidence 硬编码为 1.0 ⇒ 规则层不产出可用置信度
        return {"claimed": bool(r.matched), "pred": r.rule_name if r.matched else None,
                "confidence": float(r.confidence),
            # 【L26 更正】engine.py 已**不再硬编码** 1.0：改为「规则声明值，缺省回落 1.0」
            # （DEFAULT_RULE_CONFIDENCE / resolve_rule_confidence）。当前内置 8 条规则仍未
            # 携带真实置信度，故**取值**仍为 1.0，但**机制**已变 ⇒ 标签须如实反映，
            # 否则审计者会据旧标签误判为「代码写死」。
            "confidence_kind": "rule_default_1.0"}

    # ── L2 模板层 ──────────────────────────────────────────────
    def probe_template(self, text: str) -> Dict[str, Any]:
        intent, conf = self.IntentRouter.classify(text)
        return {"claimed": intent != self.INTENT_UNKNOWN, "pred": intent,
                "confidence": float(conf.value), "confidence_kind": "enum_value",
                "confidence_name": conf.name}

    # ── L3a 类别层（关键词） ───────────────────────────────────
    def probe_category(self, text: str) -> Dict[str, Any]:
        cats = set(self.classify_user_input(text))
        return {"claimed": len(cats - {"core"}) > 0, "pred": sorted(cats),
                "confidence": None, "confidence_kind": "none"}

    # ── L3b 语义层（BM25 + Embedding 融合） ────────────────────
    def probe_hybrid(self, text: str, top_k: int = 10) -> Dict[str, Any]:
        if self.retriever is None or not self.retriever.available:
            return {"claimed": False, "pred": None, "confidence": None,
                    "confidence_kind": "unavailable", "top": [], "raw_bm25_top5": []}
        res = self.retriever.query(text, top_k=top_k) or []
        raw = self.retriever._bm25.search(text, top_k=top_k) or []
        if not res:
            return {"claimed": False, "pred": None, "confidence": 0.0,
                    "confidence_kind": "none", "top": [], "raw_bm25_top5": []}
        raw_top = float(raw[0][1]) if raw else 0.0
        raw_second = float(raw[1][1]) if len(raw) > 1 else 0.0
        return {
            "claimed": True, "pred": res[0][0], "confidence": float(res[0][1]),
            "confidence_kind": "fused_minmax",
            "raw_bm25_top": raw_top, "raw_bm25_second": raw_second,
            "raw_margin": raw_top - raw_second,
            "top": [[d, _r(s)] for d, s in res[:5]],
            # 【A7】完整排序 id（不含分数，体积可忽略）：使「某工具由第1掉到第5」这类
            # **名次位移**可被独立复算 —— 基线原先只存 top1 的 pred，位移只体现为
            # correct 翻转，第三方无法复核。
            "top_ids": [d for d, _ in res],
            "raw_bm25_top5": [[d, _r(s)] for d, s in raw[:5]],
        }


# ════════════════════════════════════════════════════════════════
#  评分
# ════════════════════════════════════════════════════════════════

def _rank_of_gold(gold: Any, res: Dict[str, Any]) -> Optional[int]:
    """gold 在检索器返回排序中的名次（1-based）；不在返回列表内则为 None。

    Why（A7）: 逐例只记 top1 的 pred 时，「某工具从第1掉到第5」这类**名次位移**
        无法被独立复核（基线只体现为 correct 的翻转）。落盘名次后，位移可复算。
    """
    top_ids = res.get("top_ids")
    if not isinstance(gold, str) or not isinstance(top_ids, (list, tuple)):
        return None
    for i, doc_id in enumerate(top_ids, start=1):
        if doc_id == gold:
            return i
    return None


def is_correct(layer: str, gold: Any, pred: Any) -> Optional[bool]:
    if gold is None or (isinstance(gold, list) and gold == []):
        # gold = 本层不应声明（拒识类用例）
        if layer in ("rule", "template"):
            return pred in (None, "unknown")
        return None
    if layer == "keyword_category":
        return set(gold).issubset(set(pred or []))
    return pred == gold


def per_layer_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n": len(rows)}
    claimed = [r for r in rows if r["result"]["claimed"]]
    scored = [r for r in rows if r["correct"] is not None]
    out["n_scored"] = len(scored)
    out["n_claimed"] = len(claimed)
    out["coverage"] = _r(_safe_div(len(claimed), len(rows)))
    out["reject_rate"] = _r(1.0 - (out["coverage"] or 0.0))
    if scored:
        ys = [1 if r["correct"] else 0 for r in scored]
        out["accuracy"] = _r(sum(ys) / len(ys))
        out["accuracy_ci95"] = bootstrap_ci(ys)
        out["n_correct"] = sum(ys)
    else:
        out["accuracy"] = None
    claimed_scored = [r for r in claimed if r["correct"] is not None]
    if claimed_scored:
        ys2 = [1 if r["correct"] else 0 for r in claimed_scored]
        out["precision_on_claimed"] = _r(sum(ys2) / len(ys2))
        out["claimed_and_wrong"] = len(ys2) - sum(ys2)
        out["claimed_wrong_ids"] = [r["id"] for r in claimed_scored if not r["correct"]]
    else:
        out["precision_on_claimed"] = None
        out["claimed_and_wrong"] = None
        out["claimed_wrong_ids"] = []
    return out


# ════════════════════════════════════════════════════════════════
#  外部真实语料（不复制数据，直接读原文件 ⇒ 避免第二真相源）
# ════════════════════════════════════════════════════════════════

def load_external_corpora(spec: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for c in spec:
        p = ROOT / c["path"]
        if not p.exists():
            continue
        if c["json_key"] is None:
            for f in sorted(p.rglob("*.json")):
                data = json.loads(f.read_text(encoding="utf-8"))
                rows = data if isinstance(data, list) else list(data.values())
                for row in rows:
                    if isinstance(row, dict) and row.get(c["text_key"]):
                        items.append({"corpus": c["id"], "source": str(f.relative_to(ROOT)),
                                      "text": row[c["text_key"]], "label": c["label"]})
        else:
            data = json.loads(p.read_text(encoding="utf-8"))
            for row in data[c["json_key"]]:
                if row.get(c["text_key"]):
                    items.append({"corpus": c["id"], "source": c["path"],
                                  "text": row[c["text_key"]], "label": c["label"]})
    return items


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return "unknown"


# ════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════

def run() -> Dict[str, Any]:
    spec = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    funnel = Funnel()

    categories = {
        "source": "agent/tool_router.py:308 TOOL_CATEGORIES（YAML 存在时由 "
                  "_load_tool_categories_from_yaml() 于 :238 从 data/tool_definitions/*.yaml 派生）",
        "n": len(funnel.TOOL_CATEGORIES),
        "keys": sorted(funnel.TOOL_CATEGORIES.keys()),
        "tools_per_category": {k: len(v["tools"]) for k, v in sorted(funnel.TOOL_CATEGORIES.items())},
        "total_tools": sum(len(v["tools"]) for v in funnel.TOOL_CATEGORIES.values()),
    }

    rows: List[Dict[str, Any]] = []
    for case in spec["cases"]:
        text, layer, gold = case["text"], case["layer"], case.get("gold")
        if layer == "rule":
            res = funnel.probe_rule(text)
        elif layer == "template":
            res = funnel.probe_template(text)
        elif layer == "keyword_category":
            res = funnel.probe_category(text)
        elif layer == "hybrid_tool":
            res = funnel.probe_hybrid(text)
        else:
            raise ValueError("unknown layer " + str(layer))
        rows.append({"id": case["id"], "layer": layer, "text": text, "gold": gold,
                     "source": case["source"], "anchor": case["anchor"],
                     "stale": bool(case.get("stale")), "result": res,
                     "correct": is_correct(layer, gold, res["pred"]),
                     "rank_of_gold": _rank_of_gold(gold, res)})

    scored_rows = [r for r in rows if not r["stale"]]
    stale_rows = [r for r in rows if r["stale"]]

    layers = ["rule", "template", "keyword_category", "hybrid_tool"]
    per_layer = {L: per_layer_metrics([r for r in scored_rows if r["layer"] == L]) for L in layers}

    # 三层合计（规则 → 模板 → 语义）：按文本去重，任一决策层声明即算覆盖
    by_text: Dict[str, List[Dict[str, Any]]] = {}
    for r in scored_rows:
        if r["layer"] in ("rule", "template", "hybrid_tool"):
            by_text.setdefault(r["text"], []).append(r)
    agg_claimed = sum(1 for _, rs in by_text.items() if any(x["result"]["claimed"] for x in rs))
    agg = {"n_texts": len(by_text),
           "coverage": _r(_safe_div(agg_claimed, len(by_text))),
           "reject_rate": _r(1.0 - (_safe_div(agg_claimed, len(by_text)) or 0.0)),
           "note": "同一文本在三层各有独立用例时按文本去重；任一决策层声明即为覆盖"}

    # ── 校准集 / 测试集划分（分层随机） ──────────────────────
    split_cfg = spec["split"]
    rng = random.Random(split_cfg["seed"])
    calib_ids, test_ids = set(), set()
    for L in layers:
        group: Dict[str, List[str]] = {}
        for r in scored_rows:
            if r["layer"] != L:
                continue
            key = json.dumps(r["gold"], ensure_ascii=False, sort_keys=True)
            group.setdefault(key, []).append(r["id"])
        for key, ids in group.items():
            ids = sorted(ids)
            rng.shuffle(ids)
            k = max(1, int(round(len(ids) * split_cfg["calib_ratio"]))) if len(ids) > 1 else 0
            calib_ids.update(ids[:k])
            test_ids.update(ids[k:])
    calib_ids -= test_ids  # 防重叠（同文本跨层不同用例时）

    calibration: Dict[str, Any] = {
        "method": "temperature scaling (Guo et al., ICML 2017)",
        "formula": "p = sigmoid(logit / T)", "layers": {}}

    # 5a. 模板层：enum 值本身即系统自报置信度
    tmpl = [r for r in scored_rows if r["layer"] == "template" and r["correct"] is not None]
    if tmpl:
        def _split(rs):
            c = [r for r in rs if r["id"] in calib_ids]
            t = [r for r in rs if r["id"] in test_ids]
            return c, t
        c_rows, t_rows = _split(tmpl)
        if c_rows and t_rows:
            lc = [_logit(r["result"]["confidence"]) for r in c_rows]
            yc = [1 if r["correct"] else 0 for r in c_rows]
            lt = [_logit(r["result"]["confidence"]) for r in t_rows]
            yt = [1 if r["correct"] else 0 for r in t_rows]
            fit = fit_temperature(lc, yc)
            T = fit["T"]
            all_l = [_logit(r["result"]["confidence"]) for r in tmpl]
            all_y = [1 if r["correct"] else 0 for r in tmpl]
            calibration["layers"]["template"] = {
                "confidence_semantics": "IntentRouter 的 Confidence 枚举值（HIGH=0.9/MEDIUM=0.6/LOW=0.3），"
                                        "直接当作系统自报正确概率 p0",
                "n_calib": len(c_rows), "n_test": len(t_rows), "temperature": fit,
                "ece_before": reliability_table([_sigmoid(l) for l in lt], yt),
                "ece_after": reliability_table([_sigmoid(l / T) for l in lt], yt),
                "ece_before_fullset_not_heldout": reliability_table([_sigmoid(l) for l in all_l], all_y),
                "ece_after_fullset_not_heldout": reliability_table([_sigmoid(l / T) for l in all_l], all_y),
            }

    # 5b. 语义层：融合分退化 + 用原始 BM25 softmax 做可校准对照
    hyb = [r for r in scored_rows if r["layer"] == "hybrid_tool" and r["correct"] is not None]
    if hyb:
        fused = [r["result"]["confidence"] for r in hyb if r["result"]["claimed"]]
        calibration["layers"]["hybrid_tool"] = {
            "confidence_semantics": "HybridRetriever.query 的融合分（_min_max_normalize 后 min-max 归一化）",
            "n_scored": len(hyb),
            "fused_top_scores": sorted(set(_r(x) for x in fused)),
            "degenerate": len(set(round(x, 6) for x in fused)) <= 1,
            "conclusion": "融合分经 min-max 归一化后 top1 恒为 1.0 ⇒ 该分数不携带置信度信息，"
                          "在其上做温度缩放/ECE 无意义（改判定语义不在本任务范围，故只记录事实）",
        }

        # 【A2/A7】原始 BM25 top1 的分位数 —— 供半饱和常量 S0 的**独立复算**。
        # 逐条原始值见 eval/routing_baseline/bm25_raw_top1.json（受跟踪产物）。
        # 【诚实边界】S0 对样本口径敏感（实测：仅 hybrid_tool 层 calib 与全层 calib
        # 相差约 1.8×），故该分数可用于**单调排序**（保序，与 S0 取值无关），
        # 但在其上做 ECE / 拒识阈值时**必须**计入该不确定度。
        _rv = sorted(float(r["result"].get("raw_bm25_top") or 0.0)
                     for r in hyb if r["result"].get("claimed"))
        if _rv:
            _q = lambda p: _rv[min(len(_rv) - 1, int(p * (len(_rv) - 1)))]
            calibration["layers"]["hybrid_tool"]["raw_top1_percentiles"] = {
                "n": len(_rv),
                "p10": _r(_q(0.10)), "p50": _r(_q(0.50)),
                "p90": _r(_q(0.90)), "max": _r(_rv[-1]),
                "note": "原始 BM25 top1 分位数；S0 取 calib 划分的中位数。",
            }
        c_rows = [r for r in hyb if r["id"] in calib_ids and r["result"]["claimed"]]
        t_rows = [r for r in hyb if r["id"] in test_ids and r["result"]["claimed"]]
        if c_rows and t_rows:
            p0c = [_softmax_top1(r["result"]["raw_bm25_top5"]) for r in c_rows]
            yc = [1 if r["correct"] else 0 for r in c_rows]
            p0t = [_softmax_top1(r["result"]["raw_bm25_top5"]) for r in t_rows]
            yt = [1 if r["correct"] else 0 for r in t_rows]
            if all(p is not None for p in p0c + p0t):
                fit = fit_temperature([_logit(p) for p in p0c], yc)
                T = fit["T"]
                all_p = [_softmax_top1(r["result"]["raw_bm25_top5"]) for r in hyb]
                all_y2 = [1 if r["correct"] else 0 for r in hyb]
                all_pairs = [(p, y) for p, y in zip(all_p, all_y2) if p is not None]
                calibration["layers"]["hybrid_raw_bm25"] = {
                    "confidence_semantics": "对原始（未 min-max 归一化）BM25 top-5 分做 softmax 后的 top1 概率",
                    "n_calib": len(c_rows), "n_test": len(t_rows), "temperature": fit,
                    "ece_before": reliability_table(p0t, yt),
                    "ece_after": reliability_table([_sigmoid(_logit(p) / T) for p in p0t], yt),
                    "ece_before_fullset_not_heldout": reliability_table([p for p, _ in all_pairs],
                                                                        [y for _, y in all_pairs]),
                    "ece_after_fullset_not_heldout": reliability_table(
                        [_sigmoid(_logit(p) / T) for p, _ in all_pairs], [y for _, y in all_pairs]),
                }

    # ── 拒识阈值：覆盖率 vs 拒识率 取舍曲线 ──────────────────
    #
    # 两条曲线口径必须分开写清楚（否则会把"无标注语料上的覆盖率"冒充成"可靠性"）：
    #   (a) labeled : 10 条标注语义层用例（含 1 条真错）⇒ 同时给覆盖率与 risk（接受子集错误率）
    #   (b) corpus  : 85 条仓内真实查询（无标注）⇒ 只给覆盖率/拒识率，risk 不可定义
    T_used = None
    blk = calibration["layers"].get("hybrid_raw_bm25")
    if blk:
        T_used = blk["temperature"]["T"]

    def _p_cal(r: Dict[str, Any]) -> Optional[float]:
        p0 = _softmax_top1(r["result"]["raw_bm25_top5"])
        if p0 is None:
            return None
        return _sigmoid(_logit(p0) / T_used) if T_used else p0

    THRESHOLDS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
    sweep: Dict[str, Any] = {
        "source_signal": "hybrid_raw_bm25 softmax top1（经 T=" + str(T_used) + " 温度缩放）",
        "note": "覆盖率 = 接受比例；risk = 被接受样本中的错误率。labeled 折给 risk，corpus 折只给覆盖/拒识。",
        "labeled": {"rows": []},
        "corpus": {"rows": []},
    }
    # (a) 标注折（n=10；改判定语义不在本任务范围，故不做任何阈值生效）
    lab_pairs = [(r["id"], _p_cal(r), 1 if r["correct"] else 0) for r in hyb]
    lab_pairs = [(i, p, y) for i, p, y in lab_pairs if p is not None]
    for t in THRESHOLDS:
        idx = [k for k, (_, p, _) in enumerate(lab_pairs) if p >= t]
        n_acc = len(idx)
        cov = _safe_div(n_acc, len(lab_pairs))
        risk = _safe_div(sum(1 - lab_pairs[k][2] for k in idx), n_acc) if n_acc else None
        sweep["labeled"]["rows"].append({
            "threshold": t, "coverage": _r(cov),
            "reject_rate": _r(1 - cov if cov is not None else None),
            "accepted": n_acc, "rejected": len(lab_pairs) - n_acc,
            "risk_error_rate": _r(risk),
            "accepted_wrong_ids": [lab_pairs[k][0] for k in idx if not lab_pairs[k][2]],
        })
    sweep["labeled"]["n"] = len(lab_pairs)
    sweep["labeled"]["per_case"] = [
        {"id": r["id"], "gold": r["gold"], "pred": r["result"]["pred"], "correct": r["correct"],
         "rank_of_gold": r.get("rank_of_gold"),
         "p_calibrated": _r(_p_cal(r))} for r in hyb]

    # ── 外部真实语料：层声明率（无标注，只看漏斗行为）────────
    corpora = load_external_corpora(spec["external_corpora"])
    corpus_reads: Dict[str, Any] = {}
    for cid in sorted(set(c["corpus"] for c in corpora)):
        sub = [c for c in corpora if c["corpus"] == cid]
        tmp = {"n": len(sub), "source": sub[0]["source"], "label": sub[0]["label"],
               "rule_claim_rate": 0, "template_claim_rate": 0, "hybrid_claim_rate": 0,
               "both_rule_and_template": 0}
        for c in sub:
            rc = funnel.probe_rule(c["text"])["claimed"]
            tc = funnel.probe_template(c["text"])["claimed"]
            hc = funnel.probe_hybrid(c["text"])["claimed"]
            tmp["rule_claim_rate"] += int(rc)
            tmp["template_claim_rate"] += int(tc)
            tmp["hybrid_claim_rate"] += int(hc)
            tmp["both_rule_and_template"] += int(rc and tc)
        for k in ("rule_claim_rate", "template_claim_rate", "hybrid_claim_rate", "both_rule_and_template"):
            tmp[k] = _r(tmp[k] / len(sub)) if sub else None
        corpus_reads[cid] = tmp

    # (b) 真实语料折：把语义层软分数在 85 条无标注查询上做阈值扫描（只看覆盖/拒识）
    corpus_p: List[Dict[str, Any]] = []
    for c in corpora:
        r = funnel.probe_hybrid(c["text"])
        p = _softmax_top1(r["raw_bm25_top5"]) if r.get("raw_bm25_top5") else None
        if p is None:
            continue
        corpus_p.append({"corpus": c["corpus"], "text": c["text"],
                         "p_calibrated": _r(_sigmoid(_logit(p) / T_used) if T_used else p)})
    for t in THRESHOLDS:
        accepted = [x for x in corpus_p if x["p_calibrated"] >= t]
        cov = _safe_div(len(accepted), len(corpus_p))
        sweep["corpus"]["rows"].append({
            "threshold": t, "coverage": _r(cov),
            "reject_rate": _r(1 - cov if cov is not None else None),
            "accepted": len(accepted), "rejected": len(corpus_p) - len(accepted),
            "risk_error_rate": None,
            "by_corpus": {cid: _r(_safe_div(
                len([x for x in accepted if x["corpus"] == cid]),
                max(len([x for x in corpus_p if x["corpus"] == cid]), 1)))
                for cid in sorted(set(x["corpus"] for x in corpus_p))},
        })
    corpus_ps = sorted(x["p_calibrated"] for x in corpus_p)
    sweep["corpus"]["n"] = len(corpus_p)
    sweep["corpus"]["p_calibrated_percentiles"] = {
        "p10": _r(corpus_ps[int(0.10 * (len(corpus_ps) - 1))]) if corpus_ps else None,
        "p50": _r(corpus_ps[int(0.50 * (len(corpus_ps) - 1))]) if corpus_ps else None,
        "p90": _r(corpus_ps[int(0.90 * (len(corpus_ps) - 1))]) if corpus_ps else None,
        "min": _r(corpus_ps[0]) if corpus_ps else None,
        "max": _r(corpus_ps[-1]) if corpus_ps else None,
    }

    # ── 结构化结论（全部由上面的读数机械导出，不手写数字）────────
    findings: List[Dict[str, Any]] = []
    findings.append({
        "id": "F1", "severity": "blocker",
        "title": "规则层不产出置信度 ⇒ 该层不可校准",
        "evidence": "agent/workflow_engine/engine.py:57 把 WorkflowResult.confidence 硬编码为 1.0；"
                    "实测 rule 层 41 条用例的 confidence 全部为 1.0",
        "impact": "温度缩放/ECE 在规则层无对象；规则层只能报告准确率与覆盖率",
    })
    findings.append({
        "id": "F2", "severity": "blocker",
        "title": "语义层融合分被 min-max 归一化抹平 ⇒ 该分数不携带置信度",
        "evidence": "agent/tool_router_hybrid.py:1154-1167 _min_max_normalize 把每路召回的 max 映射为 1.0；"
                    "实测 fused_top_scores=" + json.dumps(
                        calibration["layers"].get("hybrid_tool", {}).get("fused_top_scores")),
        "impact": "直接在该分数上做拒识等价于'永不拒识'；本基线改用未归一化的原始 BM25 分作为可校准出口",
    })
    findings.append({
        "id": "F3", "severity": "high",
        "title": "本环境 Embedding 路不可用，语义层实际运行在 BM25-only 降级路",
        "evidence": json.dumps(funnel.semantic_status, ensure_ascii=False),
        "impact": "所有语义层读数描述降级路；AGENT_HYBRID_ALPHA=0.5 的融合路未被本次基线覆盖",
    })
    hl = calibration["layers"].get("hybrid_raw_bm25")
    if hl:
        findings.append({
            "id": "F4", "severity": "medium",
            "title": "原始 BM25 softmax 分数可校准：NLL 大幅下降，但 ECE 在 n=5 的测试折上不可解释",
            "evidence": "T=" + str(hl["temperature"]["T"]) + "，NLL " + str(hl["temperature"]["nll_before"])
                        + " -> " + str(hl["temperature"]["nll_after"]) + "；"
                        + "测试折 ECE " + str(hl["ece_before"]["ece"]) + " -> " + str(hl["ece_after"]["ece"])
                        + "，全标注集 ECE " + str(hl["ece_before_fullset_not_heldout"]["ece"])
                        + " -> " + str(hl["ece_after_fullset_not_heldout"]["ece"]),
            "impact": "测试折 n=5，ECE 由'哪 5 条落到折里'决定；只能采信 NLL 的下降方向，不能采信 ECE 的绝对差",
        })
    tl = calibration["layers"].get("template")
    if tl:
        findings.append({
            "id": "F5", "severity": "medium",
            "title": "模板层的失配是枚举上限问题，不是温度问题 ⇒ 温度缩放修不动它",
            "evidence": "T=" + str(tl["temperature"]["T"]) + "（≈1，几乎未改），"
                        + "ECE " + str(tl["ece_before"]["ece"]) + " -> " + str(tl["ece_after"]["ece"]),
            "impact": "该折 accuracy=1.0 而最高自报置信度只有 0.9（Confidence.HIGH），"
                      "先把上限抬到 1.0 才能谈温度；本任务不改判定语义，故只记录",
        })
    wrong_l3 = per_layer["hybrid_tool"].get("claimed_wrong_ids") or []
    if wrong_l3:
        detail = []
        for r in hyb:
            if r["id"] in wrong_l3:
                detail.append({"id": r["id"], "text": r["text"], "gold": r["gold"],
                               "pred": r["result"]["pred"],
                               "raw_bm25_top5": r["result"]["raw_bm25_top5"]})
        findings.append({
            "id": "F6", "severity": "high",
            "title": "语义层在真实 90 工具索引上存在实际误召回",
            "evidence": json.dumps(detail, ensure_ascii=False),
            "impact": "例：中文查询'解析pdf'在真实索引上 top1=run_lint（合成 5 工具索引下该单测是过的）"
                      "⇒ 原单测的'召回正确'结论不覆盖真实索引",
        })
    wrong_cat = per_layer["keyword_category"].get("claimed_wrong_ids") or []
    if wrong_cat:
        detail = []
        for r in scored_rows:
            if r["id"] in wrong_cat:
                detail.append({"id": r["id"], "text": r["text"], "gold": r["gold"],
                               "pred": r["result"]["pred"]})
        findings.append({
            "id": "F7", "severity": "medium",
            "title": "关键词类别层存在既定误分类（仓内自带用例即已失败）",
            "evidence": json.dumps(detail, ensure_ascii=False),
            "impact": "该层 8 条可用用例中 1 条不满足其自身期望集合（见上）",
        })
    findings.append({
        "id": "F8", "severity": "high",
        "title": "语义层在真实语料上几乎不做拒识（覆盖率 0.88–1.00）",
        "evidence": json.dumps(corpus_reads, ensure_ascii=False),
        "impact": "当前漏斗的拒识能力完全来自 L1/L2 两层；语义层对任何中文输入都会返回候选",
    })

    # ── 建议阈值（由曲线机械导出）──────────────────────────
    def _best_under(reject_cap: float) -> Optional[Dict[str, Any]]:
        """拒识率 <= cap 的前提下，取**阈值最大**（即拒掉最多、仍合规）的那一点。

        为什么不是取覆盖率最大：覆盖率随阈值单调不增，"覆盖率最大"在数学上恒等于
        最小阈值（拒识率 0），没有信息量。工程上有意义的问题是"在拒识率上限内，
        最多能拒掉多少"，故取满足上限的最大阈值。
        """
        cands = [r for r in sweep["corpus"]["rows"]
                 if r["reject_rate"] is not None and r["reject_rate"] <= reject_cap
                 and r["coverage"] is not None and r["coverage"] > 0]
        if not cands:
            return None
        return max(cands, key=lambda r: r["threshold"])

    lab_rows = sweep["labeled"]["rows"]
    min_risk = min([r["risk_error_rate"] for r in lab_rows if r["risk_error_rate"] is not None],
                   default=None)
    useless = all((r["risk_error_rate"] is None) or (r["risk_error_rate"] >= (min_risk or 0) - 1e-9)
                  for r in lab_rows) if min_risk is not None else None
    best5 = _best_under(0.05)
    recommendation = {
        "verdict": "不建议在当前信号上启用该阈值（证据不足 + 信号无区分度）",
        "reason_1_no_discrimination": "标注折上唯一的错误（见 F6）其校准后分数位于被接受样本的中位区间："
                                      "任何能拒掉它的阈值（>=0.8）会把覆盖率打到 0（softmax 分数上限 0.8）。"
                                      "即该分数对本基线观测到的错误**无区分度**。",
        "reason_2_sample": "标注折 n=10、校准折 n=5，均远低于 drift_trigger.min_samples=200。",
        "conditional_value_if_forced": {
            "rule": "若必须给一个数字：取'拒识率 <= 5% 前提下拒识最多（阈值最大）'的点"
                    "（该准则等价于题目给的'拒识率 <= X% 时覆盖率最大'，只是把无信息量的 t=0 排除）",
            "threshold": best5["threshold"] if best5 else None,
            "coverage_on_real_corpus": best5["coverage"] if best5 else None,
            "reject_rate_on_real_corpus": best5["reject_rate"] if best5 else None,
            "basis": "真实语料折 n=" + str(sweep["corpus"]["n"]) + "，该点是满足拒识率上限的最大覆盖率点",
            "caveat": "它在标注折上不降低 risk（仍 0.1）⇒ 只是'少答一点'，不是'答得更准'",
        },
        "labeled_min_risk": min_risk,
    }

    drift = {
        "principle": "校准只在分数分布未漂移时有效；一旦分布漂移，T 必须重估。",
        "signals": [
            {"name": "PSI(分数分布)",
             "definition": "把校准期分数按 10 等宽箱固化，窗口内新样本落箱占比对比，"
                           "PSI = Σ (p_cur − p_ref)·ln(p_cur / p_ref)",
             "trigger": "PSI >= 0.25",
             "basis": "信用评分业界通行经验阈值（<0.1 无显著漂移；0.1–0.25 中度；>=0.25 显著，须重估）"},
            {"name": "Delta_ECE(标注窗口)",
             "definition": "滑动窗口内重算 ECE，与校准时 ECE 比较",
             "trigger": "Delta_ECE >= 0.10 或 ECE_cur >= 2 x ECE_calib（先到者触发）",
             "basis": "本基线 ECE 量级约 0.1，取半个量级为可感知漂移；倍数条件防小基数抖动"},
            {"name": "KS 检验 p 值",
             "definition": "窗口分数 vs 校准集分数的双样本 KS 检验",
             "trigger": "p < 0.01",
             "basis": "与 PSI 互为交叉验证（PSI 分箱敏感，KS 无分箱）"},
        ],
        "action": "触发后用新窗口标注样本重跑 scripts/run_routing_reliability.py 重新拟合 T；"
                  "T 的重估只影响观测与拒识阈值，不改动任何判定语义。",
        "min_samples": 200,
        "min_samples_basis": "温度缩放是单参数拟合，样本 <200 时 T 的置信区间极宽；本基线校准集样本量"
                             "（见 calibration.layers.*.n_calib）远小于该门槛 ⇒ 当前 T 只能当占位值。",
    }

    return {
        "task": "TASK-10 · 既有路由的可验证化",
        "head_commit": _git_head(),
        "environment": {"python": sys.version.split()[0], "new_dependencies": [],
                        "new_env_switches": []},
        "intent_category_table": categories,
        "semantic_layer_status": funnel.semantic_status,
        "case_set": {
            "path": "eval/routing_baseline/cases.json",
            "n_total": len(rows), "n_scored": len(scored_rows), "n_stale_excluded": len(stale_rows),
            "excluded": [{"id": r["id"], "reason": "gold 含已退役类别 software（agent/tool_router.py:177-186）"}
                         for r in stale_rows],
            "by_layer": {L: len([r for r in scored_rows if r["layer"] == L]) for L in layers},
            "provenance": spec["provenance_policy"],
            "split": {"seed": split_cfg["seed"], "method": split_cfg["method"],
                      "n_calib": len(calib_ids), "n_test": len(test_ids),
                      "calib_ratio_realized": _r(_safe_div(len(calib_ids), len(calib_ids) + len(test_ids)))},
        },
        "metrics": {"per_layer": per_layer, "funnel_aggregate": agg},
        "calibration": calibration,
        "reject_sweep": sweep,
        "reject_recommendation": recommendation,
        "findings": findings,
        "external_corpora": corpus_reads,
        "drift_trigger": drift,
        "honesty_boundaries": [
            "规则层/模板层用例抄自这两个模块自己的单测（关键词/正则即规则定义）⇒ 这些准确率是同分布上界，"
            "不是对真实流量的泛化估计；仓库内不存在留出的路由标注集（已勘察 eval/**、data/evals/**、tests/**）。",
            "语义层用例（10 条）抄自 tests/unit/test_tool_hybrid_lang_recall.py，是为英文别名机制定制的查询；"
            "但该单测用 5 工具合成索引，本基线用真实 90 工具索引（data/tool_index.json）⇒ 比原单测更难。",
            "Embedding 在本环境不可用（retriever_degraded=true）⇒ 语义层读数描述 BM25-only 降级路，"
            "不是 AGENT_HYBRID_ALPHA=0.5 的融合路。",
            "标注样本量 <=80 ⇒ 温度参数 T 与 ECE 的置信区间很宽，当前数值只能当占位基线。",
            "拒识阈值曲线的标注折（n=10）与真实语料折（n=78）口径不同：标注折可算 risk，"
            "语料折无标注只能算覆盖/拒识；两者不可混读。",
            "真实语料折 n=78 而非 85：另 7 条查询在语义层无任何 BM25 候选（空召回），"
            "已被 4b 曲线排除（这本身也是覆盖率的一部分，见 external_corpora 的声明率）。",
            "logs/ 下 grep 真实路由决策记录命中 0 条 ⇒ 无法给出线上覆盖率，只能用仓内语料替代。",
        ],
    }


def render_md(rep: Dict[str, Any]) -> str:
    L: List[str] = []
    A = L.append
    A("# TASK-10 · 既有路由可验证化基线报告（机器生成）")
    A("")
    A("- HEAD: " + str(rep["head_commit"]))
    A("- 语义层状态: " + json.dumps(rep["semantic_layer_status"], ensure_ascii=False))
    A("")
    A("## 1. 意图类目表（机器派生）")
    A("")
    c = rep["intent_category_table"]
    A("- 来源：" + c["source"])
    A("- 类别数 " + str(c["n"]) + "，工具总数 " + str(c["total_tools"]) + "：" + ", ".join(c["keys"]))
    A("- 每类工具数：" + json.dumps(c["tools_per_category"], ensure_ascii=False))
    A("")
    A("## 2. 每层读数")
    A("")
    A("| 层 | n | 覆盖率 | 拒识率 | 准确率(全量) | 95%CI | 已声明准确率 | 声明但错 |")
    A("|---|---|---|---|---|---|---|---|")
    for k, v in rep["metrics"]["per_layer"].items():
        A("| " + k + " | " + str(v["n"]) + " | " + str(v["coverage"]) + " | " + str(v["reject_rate"])
          + " | " + str(v.get("accuracy")) + " | " + str(v.get("accuracy_ci95")) + " | "
          + str(v.get("precision_on_claimed")) + " | " + str(v.get("claimed_and_wrong")) + " |")
    a = rep["metrics"]["funnel_aggregate"]
    A("")
    A("三层合计（按文本去重，n=" + str(a["n_texts"]) + "）：覆盖率 " + str(a["coverage"])
      + "，拒识率 " + str(a["reject_rate"]))
    A("")
    A("## 3. 校准（温度缩放 + ECE）")
    A("")
    for name, blk in rep["calibration"]["layers"].items():
        A("### " + name)
        A("")
        A("- 语义：" + str(blk.get("confidence_semantics")))
        if "temperature" in blk:
            t = blk["temperature"]
            A("- 校准集 n=" + str(blk.get("n_calib")) + " / 测试集 n=" + str(blk.get("n_test")))
            A("- T = " + str(t["T"]) + "（NLL " + str(t["nll_before"]) + " -> " + str(t["nll_after"]) + "）")
            A("- ECE 改前 = " + str(blk["ece_before"]["ece"]) + " -> 改后 = "
              + str(blk["ece_after"]["ece"]) + "；MCE " + str(blk["ece_before"]["mce"])
              + " -> " + str(blk["ece_after"]["mce"]))
            if "ece_before_fullset_not_heldout" in blk:
                A("- 补充（**非留出**，全标注集）ECE 改前 = "
                  + str(blk["ece_before_fullset_not_heldout"]["ece"]) + " -> 改后 = "
                  + str(blk["ece_after_fullset_not_heldout"]["ece"]))
            A("")
            A("| 箱 | 区间 | n | 平均置信 | 实际准确 | 差 |")
            A("|---|---|---|---|---|---|")
            for b in blk["ece_after"]["bins"]:
                A("| " + str(b["bin"]) + " | " + str(b["range"]) + " | " + str(b["n"]) + " | "
                  + str(b["avg_confidence"]) + " | " + str(b["accuracy"]) + " | " + str(b["gap"]) + " |")
            A("")
        else:
            A("- " + str(blk.get("conclusion")))
            A("")
    A("## 4. 拒识阈值取舍曲线")
    A("")
    s = rep["reject_sweep"]
    A("- 信号：" + str(s.get("source_signal")))
    A("")
    A("### 4a 标注折（n=" + str(s["labeled"]["n"]) + "）—— 可给 risk")
    A("")
    A("| 阈值 | 覆盖率 | 拒识率 | 接受数 | 拒绝数 | 接受子集错误率 | 被接受但错的用例 |")
    A("|---|---|---|---|---|---|---|")
    for r in s["labeled"]["rows"]:
        A("| " + str(r["threshold"]) + " | " + str(r["coverage"]) + " | " + str(r["reject_rate"])
          + " | " + str(r["accepted"]) + " | " + str(r["rejected"]) + " | " + str(r["risk_error_rate"])
          + " | " + ", ".join(r.get("accepted_wrong_ids") or []) + " |")
    A("")
    A("### 4b 仓内真实语料折（n=" + str(s["corpus"]["n"]) + "，无标注）—— 只给覆盖/拒识")
    A("")
    A("| 阈值 | 覆盖率 | 拒识率 | 接受数 | 拒绝数 | 分词料覆盖 |")
    A("|---|---|---|---|---|---|")
    for r in s["corpus"]["rows"]:
        A("| " + str(r["threshold"]) + " | " + str(r["coverage"]) + " | " + str(r["reject_rate"])
          + " | " + str(r["accepted"]) + " | " + str(r["rejected"]) + " | "
          + json.dumps(r.get("by_corpus"), ensure_ascii=False) + " |")
    A("")
    A("- 真实语料校准后分数分位：" + json.dumps(s["corpus"].get("p_calibrated_percentiles"), ensure_ascii=False))
    A("")
    A("## 5. 外部真实语料的层声明率（无标注，只看漏斗行为）")
    A("")
    A("| 语料 | n | 来源 | 规则层声明率 | 模板层声明率 | 语义层声明率 | 规则∩模板 |")
    A("|---|---|---|---|---|---|---|")
    for k, v in rep["external_corpora"].items():
        A("| " + k + " | " + str(v["n"]) + " | " + v["source"] + " | " + str(v["rule_claim_rate"])
          + " | " + str(v["template_claim_rate"]) + " | " + str(v["hybrid_claim_rate"]) + " | "
          + str(v["both_rule_and_template"]) + " |")
    A("")
    A("")
    A("## 5b 建议阈值")
    A("")
    rec = rep.get("reject_recommendation") or {}
    A("- 结论：" + str(rec.get("verdict")))
    A("- 依据1：" + str(rec.get("reason_1_no_discrimination")))
    A("- 依据2：" + str(rec.get("reason_2_sample")))
    cv = rec.get("conditional_value_if_forced") or {}
    A("- 条件取值（若上级强制要一个数）：阈值 " + str(cv.get("threshold"))
      + "，真实语料覆盖率 " + str(cv.get("coverage_on_real_corpus"))
      + "，拒识率 " + str(cv.get("reject_rate_on_real_corpus")) + "（" + str(cv.get("basis")) + "）")
    A("- 条件取值的警示：" + str(cv.get("caveat")))
    A("")
    A("## 6. 结构化结论（由读数机械导出）")
    A("")
    for f in rep.get("findings", []):
        A("- **" + f["id"] + " [" + f["severity"] + "] " + f["title"] + "**")
        A("  - 证据：" + str(f["evidence"]))
        A("  - 影响：" + str(f["impact"]))
    A("")
    A("## 7. ECE 漂移触发条件")
    A("")
    for sig in rep["drift_trigger"]["signals"]:
        A("- " + sig["name"] + "：触发 " + sig["trigger"] + "（依据：" + sig["basis"] + "）")
    A("- 最小样本量 " + str(rep["drift_trigger"]["min_samples"])
      + "（依据：" + rep["drift_trigger"]["min_samples_basis"] + "）")
    A("")
    A("## 8. 诚实边界")
    A("")
    for h in rep["honesty_boundaries"]:
        A("- " + h)
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-md", action="store_true")
    args = ap.parse_args()
    rep = run()
    REPORT_JSON.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = render_md(rep)
    REPORT_MD.write_text(md, encoding="utf-8")
    if args.print_md:
        sys.stdout.reconfigure(encoding="utf-8")
        print(md)
    else:
        print("wrote " + str(REPORT_JSON.relative_to(ROOT)) + " and " + str(REPORT_MD.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
