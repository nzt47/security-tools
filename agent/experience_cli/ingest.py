# -*- coding: utf-8 -*-
r"""learn ingest —— 把样本语料建成检索索引（方案 P2）。

做三件事：
  1. 载入 samples.ndjson
  2. 建双腿索引（BM25 + 向量；向量默认可关，见 --no-vector），并落缓存
  3. 写审计链（batch_id 即回滚凭据）

【与方案的偏差】方案原为「向量化 + capregistry 注册 + 审计签名」。P0 实测：
  capregistry 是**构建期只读派生视图**（spec.py:28-29 无写入方法、数据源是受 CI
  守门的生成物），运行期经验数据无法接入 ⇒ 改为独立索引 + 审计链留痕。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid


def cmd_ingest(args: argparse.Namespace) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if not os.path.isfile(args.ndjson):
        print("[FATAL] 语料不存在: %s" % args.ndjson, file=sys.stderr)
        return 2

    from agent.skills_mgmt.experience_index import ExperienceIndex

    t0 = time.time()
    idx = ExperienceIndex(args.ndjson, persist_dir=args.persist_dir,
                          use_vector=not args.no_vector)
    n = idx.load()
    if n == 0:
        print("[FATAL] 语料为空", file=sys.stderr)
        return 2
    st = idx.build()
    elapsed = time.time() - t0

    batch_id = uuid.uuid4().hex[:16]
    rec = {"batch_id": batch_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "corpus": args.ndjson, "docs": st.get("docs"),
           "bm25": st.get("bm25"), "vector": st.get("vector"),
           "vector_cached": st.get("vector_cached"), "elapsed_sec": round(elapsed, 1)}
    try:
        from agent.audit.facade import audit
        audit.record("experience.ingest", batch_id, payload=rec, source="agent")
        audit_ok = True
    except Exception as exc:  # noqa: BLE001
        audit_ok = False
        print("[warn] 审计链写入失败（不影响索引）: %s" % str(exc)[:120], file=sys.stderr)

    print("=" * 60)
    print("ingest 完成：%.1fs" % elapsed)
    print("  语料      : %s" % args.ndjson)
    print("  条数      : %d" % n)
    print("  构建      : %s" % st)
    print("  batch_id  : %s" % batch_id)
    print("  审计      : %s" % ("已写入" if audit_ok else "未写入"))
    print("  落盘      : %s" % args.persist_dir)
    return 0
