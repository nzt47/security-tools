# -*- coding: utf-8 -*-
r"""文档表示 A/B：索引文本该放什么？（去重语料上的诚实对照）

【被否掉的三个方向】
  A 查询长度归一  —— 只治标：doc 侧的长文本偏置仍在
  B 向量腿        —— 实测余弦 0.45~0.58 全挤在一起，无判别力（见 项目结项报告）
  C 取消闸门      —— 见 eval/gate_feasibility.py：无任何信号 AUC>=0.8

【本脚本验证的方向 D：改**被索引的文本**】
  现状 description = task[:1500]，即**原始提示词全文**。后果：
    - 48.1% 样本的 task>=800 字，长的子代理任务书把"经验"挤掉；
    - 真正的经验载荷（pitfalls / signal / 改动文件）**根本没进索引**。

  四个变体（全部在**去重后**语料上跑，查询排除同指纹条目）：
    V0  task[:1500]                     —— 现状基线
    V1  task[:300]                      —— 只留"要做什么"
    V2  task[:300] + 踩坑 + 信号 + 文件 —— 经验载荷入索引
    V3  仅踩坑 + 信号 + 文件             —— 完全去掉提示词
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from typing import Dict, List, Set

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from agent.skills_mgmt.experience_index import ExperienceIndex  # noqa: E402

SAMPLES = os.path.join(_ROOT, "data", "experience", "samples.ndjson")
TMP = os.path.join("data", "cache", "docvar")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f-]{20,}")


def fp(t: str) -> str:
    return re.sub(r"\s+", "", _UUID.sub("<UUID>", t or ""))[:400]


def ptext(p) -> str:
    if isinstance(p, str):
        return p
    if isinstance(p, dict):
        return " ".join(str(p.get(k) or "") for k in
                        ("summary", "description", "title", "pitfall", "detail", "fix", "cause"))
    return str(p)


def build_text(s: dict, variant: str) -> str:
    task = (s.get("task") or "").strip()
    pf = [ptext(p) for p in (s.get("pitfalls") or [])]
    sg = s.get("signal")
    sg = " ".join(str(v) for v in sg.values()) if isinstance(sg, dict) else str(sg or "")
    nm = [os.path.basename(d.get("path") or "") for d in (s.get("diffs") or []) if d.get("path")]
    exp = " ".join([" ".join(pf), sg, " ".join(sorted(set(nm))[:10])]).strip()
    if variant == "V0":
        return task[:1500]
    if variant == "V1":
        return task[:300]
    if variant == "V2":
        return (task[:300] + " || 经验: " + exp)[:1500]
    return ("经验: " + exp + " || " + task[:150])[:1500]


def main() -> int:
    rows = [json.loads(l) for l in open(SAMPLES, encoding="utf-8") if l.strip()]
    grp: Dict[str, List[dict]] = defaultdict(list)
    for s in rows:
        grp[fp(s.get("task") or "")].append(s)
    dedup = [max(v, key=lambda s: (len(s.get("pitfalls") or []),
                                   len({d.get("path") for d in (s.get("diffs") or [])})))
             for v in grp.values()]
    print("去重后语料 %d 条" % len(dedup))

    fs = {s["id"]: {d.get("path", "") for d in (s.get("diffs") or []) if d.get("path")} for s in dedup}
    tt = {s["id"]: s.get("task_type") for s in dedup}
    fpid = {s["id"]: fp(s.get("task") or "") for s in dedup}
    byfile: Dict[str, List[str]] = defaultdict(list)
    for s in dedup:
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
    have = [s["id"] for s in dedup if fam.get(s["id"])]
    print("有同族兄弟（可评测）: %d 条" % len(have))
    print("=" * 84)

    os.makedirs(TMP, exist_ok=True)
    print("%-6s %-10s %-10s %-10s %s" % ("变体", "MRR@10", "recall@1", "recall@5", "top1 是否为同族"))

    for variant in ("V0", "V1", "V2", "V3"):
        rows_v = []
        for s in dedup:
            t = dict(s)
            t["task"] = build_text(s, variant)
            rows_v.append(t)
        p = os.path.join(TMP, variant + ".ndjson")
        with open(p, "w", encoding="utf-8") as fh:
            for s in rows_v:
                fh.write(json.dumps(s, ensure_ascii=False) + "\n")
        idx = ExperienceIndex(p, persist_dir=os.path.join(TMP, variant + "_idx"), use_vector=False)
        idx.load()
        idx.build()

        rr: List[float] = []
        r1 = r5 = 0
        for sid in have:
            s = next(x for x in dedup if x["id"] == sid)
            q = (s.get("task") or "").strip()
            if not q:
                continue
            hits = [h for h in idx.search(q, top_k=30, include_unverified=True, min_bm25_score=0.0)
                    if fpid.get(h["id"]) != fpid[sid]][:10]
            pos = next((r for r, h in enumerate(hits, 1) if h["id"] in fam[sid]), None)
            rr.append(1.0 / pos if pos else 0.0)
            r1 += pos == 1
            r5 += bool(pos) and pos <= 5
        N = len(rr)
        print("%-6s %-10.3f %-10s %-10s %d/%d"
              % (variant, sum(rr) / max(N, 1), "%d/%d" % (r1, N), "%d/%d" % (r5, N), r1, N))
    print()
    print("说明：查询始终用**原始 task**（模拟真实用户输入），只改被索引的文档文本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
