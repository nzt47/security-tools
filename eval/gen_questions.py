#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""gen_questions.py —— 生成经验库评测题【候选集】（方案 P5）。

方案要求「从历史会话手工挑 20 题（12 成功 + 8 失败）」。本脚本产出**候选集**，
供人工过目后定稿：12 道正题（语料内确有对应条目）+ 8 道负题（语料内**不该**命中）。

【与方案的一致性】
  - 12 正 + 8 负 = 20 题，写入 eval/questions.yaml；
  - 正题的 expect 为样本 id（人工可替换为更贴切的条目）；
  - 负题 expect 为 null，用于测「不该命中的时候是否乱命中」。

【查询构造】不直接用 task 全文（那等于把答案抄进查询，自检索必然满分、没有信息量）。
  改为取 task 的**特征词**：长度 >= 3 的中英文片段去重后取前若干个，并去停用词。
  同时保留一列 query_full 供对照，便于区分"真检索能力"与"字符串重合"。

零 token：纯本地。
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, List

STOP = set("""the and for with that this from are was were will would can could should
修复 问题 失败 增加 新增 修改 调整 支持 优化 实现 使用 进行 一个 我们 这个 那个 以及
the a an of to in on at is are be it its as by or not no yes""".split())

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_\-\.]{2,}|[\u4e00-\u9fff]{2,}")


def keywords(text: str, k: int = 8) -> str:
    toks = [t for t in TOKEN.findall(text or "") if t.lower() not in STOP]
    seen, out = set(), []
    for t in toks:
        if t.lower() in seen:
            continue
        seen.add(t.lower()); out.append(t)
        if len(out) >= k:
            break
    return " ".join(out)


NEGATIVES = [
    {"id": "neg-01", "query": "如何配置 Kubernetes Ingress 的 TLS 证书自动续期"},
    {"id": "neg-02", "query": "Rust 生命周期标注与 borrow checker 报错处理"},
    {"id": "neg-03", "query": "PostgreSQL 分区表 pg_partman 的安装与保留策略"},
    {"id": "neg-04", "query": "SwiftUI 动画过渡与 GeometryReader 布局"},
    {"id": "neg-05", "query": "Terraform 远程 state 在 S3 的锁定配置"},
    {"id": "neg-06", "query": "Salesforce Apex 触发器批量化的最佳实践"},
    {"id": "neg-07", "query": "Blender 几何节点程序化建模入门"},
    {"id": "neg-08", "query": "Elixir GenServer 监督树的重启策略设计"},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", required=True, help="samples.ndjson")
    ap.add_argument("--out", required=True, help="questions.yaml")
    ap.add_argument("--n-pos", type=int, default=12)
    args = ap.parse_args()

    rows: List[Dict[str, Any]] = []
    with open(args.samples, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

    # 正题：取 verified=pass，且 task 足够长的；按 (task_type, lang) 轮转以增加多样性
    pool = [r for r in rows if r.get("verified") == "pass" and len(r.get("task") or "") >= 20]
    pool.sort(key=lambda r: r.get("id") or "")
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for r in pool:
        key = "%s|%s" % (r.get("task_type"), (r.get("stack") or {}).get("lang"))
        buckets.setdefault(key, []).append(r)

    picked: List[Dict[str, Any]] = []
    keys = sorted(buckets)
    i = 0
    while len(picked) < args.n_pos and keys:
        key = keys[i % len(keys)]
        if buckets[key]:
            picked.append(buckets[key].pop(0))
        else:
            keys.remove(key)
            continue
        i += 1

    pos = []
    for n, r in enumerate(picked, 1):
        pos.append({
            "id": "pos-%02d" % n,
            "query": keywords(r.get("task") or ""),
            "query_full": (r.get("task") or "")[:200],
            "expect": r.get("id"),
            "task_type": r.get("task_type"),
            "lang": (r.get("stack") or {}).get("lang"),
        })

    lines = [
        "# 经验库评测题（方案 P5）",
        "#",
        "# 【状态】由 gen_questions.py 生成的**候选集**，未经人工过目。",
        "#   方案要求「从历史会话手工挑 20 题（12 成功 + 8 失败）」——",
        "#   正题的 expect 可按人工判断替换为更贴切的条目；负题可直接增删。",
        "#",
        "# 【字段】",
        "#   query       检索词（由 task 特征词构造，非全文 —— 避免把答案抄进查询）",
        "#   query_full  task 全文前 200 字（对照用）",
        "#   expect      期望命中的样本 id；null 表示负题（不该命中）",
        "#",
        "# 【评测】python eval_experience.py --questions <本文件> --samples <语料>",
        "",
        "questions:",
    ]
    for q in pos + NEGATIVES:
        lines.append("  - id: %s" % q["id"])
        lines.append("    query: %s" % json.dumps(q["query"], ensure_ascii=False))
        if "query_full" in q:
            lines.append("    query_full: %s" % json.dumps(q["query_full"], ensure_ascii=False))
        lines.append("    expect: %s" % (q["expect"] if q.get("expect") else "null"))
        for k in ("task_type", "lang"):
            if q.get(k):
                lines.append("    %s: %s" % (k, q[k]))
        lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("已写出 %s：正题 %d + 负题 %d = %d 题"
          % (args.out, len(pos), len(NEGATIVES), len(pos) + len(NEGATIVES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
