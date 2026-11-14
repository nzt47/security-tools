#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""现场读数：三库「按类装载」到底装了哪些类（**只读**，不写任何东西）。

为什么要这个脚本：
验收①要的是"A 检索不到 B 的类（向量 / 知识 / 经验三条都要）"。单测能证明代码路径，
但**现场**是另一回事 —— 数据分布（是不是全 general）、配置（哪条线声明了哪些类）、
以及"某个库根本没接上"这三件事只有现场跑一遍才看得见。

用法：
    python scripts/verify_topics_isolation.py                 # 默认：读 agent_lines 里的两条线对照
    python scripts/verify_topics_isolation.py --line engineering --line knowledge
    python scripts/verify_topics_isolation.py --topics general,ops --topics general
    python scripts/verify_topics_isolation.py --skip-vector    # 不起编码器（快，只统计向量分布）
    python scripts/verify_topics_isolation.py --json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.memory import topic_policy as P  # noqa: E402
from agent.topics import DEFAULT_TOPIC, TOPICS, normalize_topics  # noqa: E402

VEC_DB_REL = os.path.join("data", "memory", "agent_memory_vec.db")
WIKI_ROOT_REL = "knowledge/wiki"
EXPERIENCE_REL = os.path.join("data", "experience", "samples.ndjson")
LINES_DIR_REL = os.path.join("data", "agent_lines")
#: 每库每次探查的取回条数（只读；够看出分布即可，不追求全量）
PROBE_TOP_K = 50
#: 现场探查用的查询词：跨库都用它，便于横向对照
PROBE_QUERY = "部署 缓存 迁移 卡片"


def _rel(path: str) -> str:
    return path.replace(os.sep, "/")


# ════════════════════════════════════════════════════════════
#  数据分布（只读统计，不依赖检索栈）
# ════════════════════════════════════════════════════════════


def vector_distribution() -> dict:
    path = os.path.join(ROOT, VEC_DB_REL)
    if not os.path.exists(path):
        return {"available": False, "reason": "no sqlite-vec db"}
    con = sqlite3.connect("file:%s?mode=ro" % _rel(path), uri=True)
    try:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'meta_%'"
        ).fetchone()
        if not table:
            return {"available": False, "reason": "no meta table"}
        rows = con.execute("SELECT metadata FROM %s" % table[0]).fetchall()
    finally:
        con.close()
    counter = collections.Counter()
    for (raw,) in rows:
        try:
            meta = json.loads(raw) if raw else {}
        except Exception:
            meta = {}
        counter[(meta or {}).get("topic") or "(未声明)"] += 1
    return {"available": True, "total": len(rows), "by_topic": dict(counter)}


def knowledge_distribution() -> dict:
    root = os.path.join(ROOT, WIKI_ROOT_REL)
    if not os.path.isdir(root):
        return {"available": False, "reason": "no wiki root"}
    from agent.knowledge.card import CardStore

    cards = CardStore(root).list()
    counter = collections.Counter(c.topic or "(未声明)" for c in cards)
    return {"available": True, "total": len(cards), "by_topic": dict(counter)}


def experience_distribution() -> dict:
    path = os.path.join(ROOT, EXPERIENCE_REL)
    if not os.path.exists(path):
        return {"available": False, "reason": "no samples"}
    from agent.skills_mgmt.experience_index import ExperienceIndex

    idx = ExperienceIndex(path, use_vector=False)
    idx.load()
    counter = collections.Counter((m.get("_topic") or DEFAULT_TOPIC)
                                  for m in idx._docs.values())
    return {"available": True, "total": len(idx._docs), "by_topic": dict(counter)}


# ════════════════════════════════════════════════════════════
#  按 profile 探查（真的走检索路径）
# ════════════════════════════════════════════════════════════


def probe_vector(whitelist, *, skip: bool) -> dict:
    if skip:
        return {"skipped": True}
    try:
        from memory.vector_store.vector_store import VectorStore

        store = VectorStore(collection_name="agent_memory")
        items, applied = P.search_with_topics(store, PROBE_QUERY, top_k=PROBE_TOP_K,
                                             topics=whitelist)
        return {"applied": list(applied),
                "topics": sorted({(i.metadata or {}).get("topic") or DEFAULT_TOPIC
                                  for i in items}),
                "count": len(items)}
    except Exception as exc:  # noqa: BLE001 现场探查不因环境失败而中断
        return {"error": type(exc).__name__}


def probe_knowledge(whitelist) -> dict:
    try:
        from agent.knowledge.card import CardStore
        from agent.knowledge.search import KnowledgeSearch

        root = os.path.join(ROOT, WIKI_ROOT_REL)
        searcher = KnowledgeSearch(CardStore(root), topics=whitelist, min_score=0.0)
        hits = searcher.search(PROBE_QUERY, top_k=PROBE_TOP_K)
        cards = {c.slug: c for c in CardStore(root).list()}
        return {"applied": list(normalize_topics(whitelist)),
                "topics": sorted({(cards[h.slug].topic or DEFAULT_TOPIC)
                                  for h in hits if h.slug in cards}),
                "count": len(hits)}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}


def probe_experience(whitelist) -> dict:
    try:
        from agent.skills_mgmt.experience_index import ExperienceIndex

        idx = ExperienceIndex(os.path.join(ROOT, EXPERIENCE_REL), use_vector=False)
        idx.load()
        idx.build()
        hits = idx.search(PROBE_QUERY, top_k=PROBE_TOP_K, min_bm25_score=0.0,
                          topics=whitelist)
        return {"applied": list(normalize_topics(whitelist)),
                "topics": sorted({(h["meta"].get("_topic") or DEFAULT_TOPIC) for h in hits}),
                "count": len(hits)}
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}


def line_topics(line_id: str) -> dict:
    from agent.lines.registry import LineRegistry

    profile = LineRegistry().load(line_id)
    if profile is None:
        return {"line": line_id, "error": "not found"}
    return {"line": line_id, "topics": list(profile.topics)}


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三库按类装载的现场读数（只读）")
    ap.add_argument("--line", action="append", default=[],
                    help="按主线档案的 topics 探查（可重复）")
    ap.add_argument("--topics", action="append", default=[],
                    help="直接给白名单（逗号分隔；可重复）")
    ap.add_argument("--skip-vector", action="store_true",
                    help="不对向量库做检索探查（不起编码器），只统计分布")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    profiles = []
    for line in args.line:
        info = line_topics(line)
        profiles.append({"label": "line:%s" % line,
                         "topics": info.get("topics") or [DEFAULT_TOPIC],
                         "error": info.get("error", "")})
    for raw in args.topics:
        profiles.append({"label": "topics:%s" % raw,
                         "topics": list(normalize_topics(raw.split(","))), "error": ""})
    if not profiles:
        # 默认对照：两条典型线（工程 / 知识）
        for line in ("engineering", "knowledge"):
            info = line_topics(line)
            if info.get("error"):
                continue
            profiles.append({"label": "line:%s" % line,
                             "topics": info.get("topics") or [DEFAULT_TOPIC], "error": ""})
        if not profiles:
            profiles.append({"label": "fallback:general", "topics": [DEFAULT_TOPIC],
                             "error": ""})

    report = {
        "vocabulary": list(TOPICS),
        "default": DEFAULT_TOPIC,
        "query": PROBE_QUERY,
        "distribution": {
            "vector": vector_distribution(),
            "knowledge": knowledge_distribution(),
            "experience": experience_distribution(),
        },
        "profiles": [],
    }
    for prof in profiles:
        wl = prof["topics"]
        report["profiles"].append({
            "label": prof["label"],
            "topics": list(wl),
            "error": prof.get("error", ""),
            "vector": probe_vector(wl, skip=args.skip_vector),
            "knowledge": probe_knowledge(wl),
            "experience": probe_experience(wl),
        })

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("== 词表 ==", ", ".join(report["vocabulary"]), "｜兜底:", report["default"])
    print("== 数据分布（只读统计）==")
    for name, dist in report["distribution"].items():
        if not dist.get("available", True):
            print("  %-10s %s" % (name, dist.get("reason") or "不可用"))
        else:
            print("  %-10s 共 %-5s %s" % (name, dist.get("total"), dist.get("by_topic")))
    print("== 按 profile 探查（query=%r）==" % report["query"])
    for prof in report["profiles"]:
        print("  [%s] 装载 %s" % (prof["label"], ",".join(prof["topics"])))
        for store in ("vector", "knowledge", "experience"):
            got = prof[store]
            if got.get("skipped"):
                print("    %-10s （--skip-vector 跳过）" % store)
            elif got.get("error"):
                print("    %-10s 探查失败: %s" % (store, got["error"]))
            else:
                print("    %-10s 命中 %-3s 可见类=%s" % (store, got.get("count"),
                                                        got.get("topics") or "（空）"))
        leak = []
        allowed = set(normalize_topics(prof["topics"]))
        for store in ("vector", "knowledge", "experience"):
            for topic in (prof[store].get("topics") or []):
                if topic not in allowed:
                    leak.append("%s:%s" % (store, topic))
        print("    → 越权类: %s" % ("、".join(leak) if leak else "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
