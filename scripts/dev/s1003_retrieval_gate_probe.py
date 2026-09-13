#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S10-03 现象B 固化探针：低质候选是否通过 _RRF_QUALITY_MIN 质量门

背景（真机，TASK-S9-01 验收报告 §3.2）:
    query = "2 加 3 等于多少？只回答数字"
    SkillLoader.match() 直接调用（min_score=0.01，与真机探针一致）:
        self_reflection  rrf_normalized=0.9954  tfidf_score=0.1
                         vector_score=None     bm25_score=3.5184
    ⇒ `tfidf_score=0.1` 属噪声级相似度，却进了候选。

根因（代码行级）:
    agent/skills_mgmt/loader.py:1516-1522 的 `max_raw_score` 把**无界** BM25 原始分
    （3.5184）与**有界**余弦相似度（tfidf 0.1）放在同一个 max() 里比较，
    再与有界阈值 `_RRF_QUALITY_MIN = 0.3`（loader.py:820）比大小
    ⇒ 量纲混用，无界分恒赢，门禁形同虚设。

本探针只读、不改分：输出同一批 query 的候选列表与各路分数，供改前/改后逐字对比。

用法（在 worktree 根目录）:
    $env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
    python scripts/dev/s1003_retrieval_gate_probe.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from agent.skills_mgmt.file_store import SkillFileStore  # noqa: E402
from agent.skills_mgmt.loader import SkillLoader  # noqa: E402

#: 真机探针批：负样本（噪声级）+ 正样本（真匹配）混合，用于判断「真变好/真变坏」
QUERIES = [
    "2 加 3 等于多少？只回答数字",     # 真机负样本：tfidf=0.1 + bm25=3.5184
    "帮我订一张机票",                  # 既有负样本库典型 query
    "今天天气真好",                    # 既有负样本库典型 query
    "1+1 等于几",                      # 负样本（无技能语义）
    "写一首关于春天的诗",              # 负样本（无技能语义）
    "自我反思一下你的回答",            # 真机正样本：tfidf=0.4444 / bm25=15.1856
    "帮我解析PDF文件",                 # 正样本（字面）
    "总结一下之前的对话记忆",          # 正样本（字面）
    "请帮我梳理历史记忆并压缩",        # 正样本（语义）
    "费马小定理证明",                  # 专有名词（BM25 强项；见 loader._tokenize docstring）
    "PDF解析",                         # 专有名词 + 技能名精确匹配
]

#: 与真机探针一致：SkillLoader.match 直接调用（默认 min_score=0.01）
MIN_SCORE = float(os.getenv("S1003_MIN_SCORE", "0.01"))
TOP_K = int(os.getenv("S1003_TOP_K", "5"))

_BOUNDED_KEYS = ("tfidf_score", "vector_score", "rerank_score")


def _bounded(breakdown: dict) -> list:
    out = []
    for key in _BOUNDED_KEYS:
        value = breakdown.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def main() -> int:
    repo = Path(os.getenv("S1003_SKILLS_REPO", str(_PROJECT_ROOT / "data" / "skills_repo")))
    store = SkillFileStore(repo_path=str(repo))
    loader = SkillLoader(file_store=store)
    index = store.load_metadata_index()
    print("=" * 78)
    print("S10-03 现象B 探针 — 检索层质量门（_RRF_QUALITY_MIN=%s）" % loader._RRF_QUALITY_MIN)
    print("skills_repo=%s  技能数=%d  min_score=%s  top_k=%d" % (repo, len(index), MIN_SCORE, TOP_K))
    print("=" * 78)

    payload = []
    for query in QUERIES:
        result = loader.match(
            query,
            top_k=TOP_K,
            enabled_only=True,
            min_score=MIN_SCORE,
            use_vector=True,      # 与真机语义层配置一致（向量后端离线时会 fast-exit）
            use_bm25=True,
            fusion_mode="rrf",
        )
        matches = list(getattr(result, "matches", None) or [])
        rows = []
        for m in matches:
            bd = dict(getattr(m, "score_breakdown", None) or {})
            bounded = _bounded(bd)
            rows.append({
                "skill_id": m.skill_id,
                "score": round(float(m.score), 6),
                "tfidf_score": bd.get("tfidf_score"),
                "vector_score": bd.get("vector_score"),
                "bm25_score": bd.get("bm25_score"),
                "rrf_normalized": bd.get("rrf_normalized"),
                "max_bounded": round(max(bounded), 6) if bounded else None,
                "max_raw_all": round(max(
                    [v for k, v in bd.items()
                     if k.endswith("_score") and k != "rrf_score"
                     and isinstance(v, (int, float)) and not isinstance(v, bool)]
                    or [0.0]), 6),
            })
        payload.append({
            "query": query,
            "retrieval_method": getattr(result, "retrieval_method", None),
            "fallback_used": getattr(result, "fallback_used", None),
            "candidate_count": len(rows),
            "candidates": rows,
        })
        print("\nQ=%s" % query)
        print("  retrieval_method=%s  fallback_used=%s  candidates=%d"
              % (payload[-1]["retrieval_method"], payload[-1]["fallback_used"], len(rows)))
        if not rows:
            print("  （质量门拦截 / 无候选）")
        for i, r in enumerate(rows, 1):
            print("  #%d %-22s score=%-8s tfidf=%-8s vec=%-6s bm25=%-8s "
                  "rrf_norm=%-8s max_bounded=%-8s max_raw_all=%s"
                  % (i, r["skill_id"], r["score"], r["tfidf_score"],
                     r["vector_score"], r["bm25_score"], r["rrf_normalized"],
                     r["max_bounded"], r["max_raw_all"]))

    out = Path(os.getenv("S1003_OUT", str(_PROJECT_ROOT / ".p6_snapshots" / "s1003_gate_probe.json")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[json] %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
