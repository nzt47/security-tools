# -*- coding: utf-8 -*-
r"""闸门可行性判定：能否用**长度鲁棒**的信号把"真找到邻居"与"只是文本像"分开？

【背景】原始 BM25 分被证伪为"查询长度计"：
    正/负题按长度分布 ⇒ 假间隔；同族/非同族的 top1 分区间也完全重叠（p10 57.7 vs p90 2153.3）。
    故需找**比值型**信号（分子分母同时随长度缩放）。

候选信号（都在同一次检索内取，无需额外计算）：
    raw        : s1                        —— 基线，已知无效
    contrast   : s1 / mean(s2..s20)        —— 顶端相对背景的突出度
    gap        : (s1 - s2) / s1            —— 顶端与次端的相对落差
    coverage   : |query terms ∩ doc1 terms| / |query terms|  —— 查询词覆盖率
    logcontrast: log(s1+1) - mean(log(si+1))

判据：以"top1 是否为同族兄弟"为标签算 AUC。
    AUC >= 0.80 ⇒ 可用作闸门；0.6~0.8 ⇒ 勉强；< 0.6 ⇒ 无判别力，闸门这条路作废。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Set, Tuple

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from agent.skills_mgmt.experience_index import ExperienceIndex  # noqa: E402

SAMPLES = os.path.join(_ROOT, "data", "experience", "samples.ndjson")
_TOK = re.compile(r"[0-9A-Za-z_]+|[\u4e00-\u9fff]")


def toks(t: str) -> Set[str]:
    return set(_TOK.findall(t or ""))


def auc(pos: List[float], neg: List[float]) -> float:
    """秩和法 AUC（含并列折半）。"""
    if not pos or not neg:
        return float("nan")
    pair = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    # 平均秩
    ranks = [0.0] * len(pair)
    i = 0
    while i < len(pair):
        j = i
        while j + 1 < len(pair) and pair[j + 1][0] == pair[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k2 in range(i, j + 1):
            ranks[k2] = avg
        i = j + 1
    rp = sum(r for r, (_, lab) in zip(ranks, pair) if lab == 1)
    np_, nn = len(pos), len(neg)
    return (rp - np_ * (np_ + 1) / 2.0) / (np_ * nn)


def main() -> int:
    rows = [json.loads(ln) for ln in open(SAMPLES, encoding="utf-8") if ln.strip()]
    fs = {s["id"]: {d.get("path", "") for d in (s.get("diffs") or []) if d.get("path")} for s in rows}
    tt = {s["id"]: s.get("task_type") for s in rows}
    byfile: Dict[str, List[str]] = defaultdict(list)
    for s in rows:
        for p in fs[s["id"]]:
            byfile[p].append(s["id"])
    fam: Dict[str, Set[str]] = defaultdict(set)
    for sid, paths in fs.items():
        if not paths:
            continue
        cand: Set[str] = set()
        for p in paths:
            cand.update(byfile[p])
        cand.discard(sid)
        for o in cand:
            if tt[o] != tt[sid] or not fs[o]:
                continue
            u = len(fs[sid] | fs[o])
            if u and len(fs[sid] & fs[o]) / u >= 0.5:
                fam[sid].add(o)

    idx = ExperienceIndex(SAMPLES, persist_dir=os.path.join("data", "cache", "loo"),
                          use_vector=False)
    idx.load()
    idx.build()
    print("语料 %d 条 | 有同族 %d 条" % (len(rows), sum(1 for s in rows if fam.get(s["id"]))))
    print("=" * 78)

    # 全库检索一次，收集信号与标签
    sig: Dict[str, List[Tuple[float, int]]] = {k: [] for k in
                                               ("raw", "contrast", "gap", "coverage", "logcontrast",
                                                "cluster", "vote80")}
    for s in rows:
        sid, q = s["id"], (s.get("task") or "").strip()
        if not q:
            continue
        hits = [h for h in idx.search(q, top_k=25, include_unverified=True,
                                      min_bm25_score=0.0) if h["id"] != sid]
        if len(hits) < 2:
            continue
        s1 = hits[0]["raw_bm25"]
        rest = [h["raw_bm25"] for h in hits[1:20]]
        m = sum(rest) / len(rest)
        label = 1 if hits[0]["id"] in fam.get(sid, ()) else 0
        qt = toks(q)
        dt = toks(hits[0]["meta"].get("description") or "")
        cov = len(qt & dt) / max(len(qt), 1)
        lc = math.log(s1 + 1) - sum(math.log(x + 1) for x in rest) / len(rest)
        # ── 结构性信号：真同族会把"兄弟"一起带上来 ⇒ top5 内部应互相像 ──
        top5 = [set(toks(h["meta"].get("description") or "")) for h in hits[:5]]
        pair = 0.0
        for i in range(len(top5)):
            for j in range(i + 1, len(top5)):
                u = len(top5[i] | top5[j])
                if u:
                    pair = max(pair, len(top5[i] & top5[j]) / u)
        vote = sum(1 for d in top5
                   if len(qt & d) / max(len(qt), 1) >= 0.80) / max(len(top5), 1)
        for k, v in (("raw", s1), ("contrast", s1 / (m + 1e-9)), ("gap", (s1 - rest[0]) / (s1 + 1e-9)),
                     ("coverage", cov), ("logcontrast", lc),
                     ("cluster", pair), ("vote80", vote)):
            sig[k].append((v, label))

    print("【闸门信号判别力】标签 = top1 是否为同族兄弟（正 %d / 负 %d）"
          % (sum(l for _, l in sig["raw"]), sum(1 - l for _, l in sig["raw"])))
    print("  %-12s %8s %8s %8s   %s" % ("信号", "正例均值", "负例均值", "AUC", "结论"))
    best = ("", 0.0)
    for k, v in sig.items():
        p = [x for x, l in v if l == 1]
        n = [x for x, l in v if l == 0]
        a = auc(p, n)
        verdict = "可用" if a >= 0.80 else ("勉强" if a >= 0.60 else "无判别力")
        print("  %-12s %8.3f %8.3f %8.3f   %s"
              % (k, sum(p) / len(p), sum(n) / len(n), a, verdict))
        if a > best[1]:
            best = (k, a)
    print()
    print("最佳信号：%s (AUC=%.3f)" % best)
    if best[1] < 0.80:
        print("⇒ **不存在可用的绝对/相对分数闸门**。")
        print("  建议：闸门不放在『分数』上，改为 k 截断 + 无条件注入，")
        print("        把『脏』的问题交给语料质量（去机器注入样本、去长提示词堆）解决。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
