"""[P4] recall 验证脚本 — 验证归一化向量后 sqlite-vec KNN 的召回率

对比三种距离度量在 top_k 检索下的 recall@10：
1. 纯 Python 余弦相似度（方案1/2 基准）
2. sqlite-vec L2 距离（未归一化向量）
3. sqlite-vec L2 距离（归一化向量）

数学原理：
- 余弦相似度 = (a·b) / (|a|·|b|)
- L2 距离 = ||a - b||² = |a|² + |b|² - 2(a·b)
- 归一化后 |a|=|b|=1，所以 L2 距离 = 2 - 2(a·b) = 2(1 - 余弦相似度)
- 因此归一化向量的 L2 排序 == 余弦相似度排序

用法（WSL Linux）：
    python3 scripts/verify_recall_normalized.py
"""

import os
import sys
import time
import struct
import heapq
import sqlite3
import random
import math
import tempfile
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# 纯函数（从 long_term_memory.py 复制，避免依赖链）
# ═══════════════════════════════════════════════════════════════

def _cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _embedding_to_blob(embedding):
    if embedding is None:
        return None
    if not embedding:
        return b""
    return struct.pack(f'{len(embedding)}f', *embedding)


def normalize(vec):
    """L2 归一化：vec / |vec|"""
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]


# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

DIM = 384
SIZES = [100, 500, 1000]
TOP_K = 10
RUNS = 3


def generate_vectors(n, dim, seed=42, normalized=False):
    """生成 n 个 dim 维向量"""
    random.seed(seed)
    vecs = [[random.gauss(0, 1) for _ in range(dim)] for _ in range(n)]
    if normalized:
        vecs = [normalize(v) for v in vecs]
    return vecs


def cosine_topk(query, docs, top_k):
    """方案1/2: 纯 Python 余弦相似度"""
    scored = [(i, _cosine_similarity(query, doc)) for i, doc in enumerate(docs)]
    return [i for i, _ in heapq.nlargest(top_k, scored, key=lambda x: x[1])]


def sqlite_vec_topk(conn, query_blob, top_k):
    """方案3: sqlite-vec L2 距离 KNN"""
    rows = conn.execute(
        "SELECT rowid FROM vec_memory WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (query_blob, top_k)
    ).fetchall()
    return [r[0] - 1 for r in rows]  # rowid 从 1 开始，转为 0-based 索引


def setup_vec_table(conn, vectors, dim):
    """创建 vec0 表并插入数据"""
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(embedding float[{dim}])")
    for idx, vec in enumerate(vectors):
        conn.execute("INSERT INTO vec_memory (rowid, embedding) VALUES (?, ?)", (idx + 1, _embedding_to_blob(vec)))
    conn.commit()


def recall_at_k(expected, actual, k):
    """计算 recall@k"""
    expected_set = set(expected[:k])
    actual_set = set(actual[:k])
    if not expected_set:
        return 1.0
    return len(expected_set & actual_set) / len(expected_set)


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def run_verification():
    print("=" * 70)
    print("[P4] recall 验证 — 归一化向量 vs 未归一化向量")
    print(f"维度: {DIM}, top_k: {TOP_K}")
    print("=" * 70)

    # 检查 sqlite-vec
    try:
        import sqlite_vec
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        print("[OK] sqlite-vec 可用")
    except Exception as e:
        print(f"[FAIL] sqlite-vec 不可用: {e}")
        print("请在 WSL Linux 环境运行: pip3 install sqlite-vec")
        return

    print()

    for size in SIZES:
        print(f"--- 数据量: {size} 条 ---")

        # 生成查询向量（固定 seed 保证可复现）
        query_raw = generate_vectors(1, DIM, seed=999, normalized=False)[0]
        query_norm = normalize(query_raw)

        # === 场景 A: 未归一化向量 ===
        docs_raw = generate_vectors(size, DIM, seed=42, normalized=False)

        # 基准: 余弦相似度
        expected_raw = cosine_topk(query_raw, docs_raw, TOP_K)

        # sqlite-vec L2（未归一化）
        conn_a = sqlite3.connect(":memory:")
        conn_a.enable_load_extension(True)
        conn_a.load_extension(sqlite_vec.loadable_path())
        setup_vec_table(conn_a, docs_raw, DIM)
        actual_raw = sqlite_vec_topk(conn_a, _embedding_to_blob(query_raw), TOP_K)
        conn_a.close()

        recall_raw = recall_at_k(expected_raw, actual_raw, TOP_K)

        # === 场景 B: 归一化向量 ===
        docs_norm = [normalize(v) for v in docs_raw]

        # 基准: 余弦相似度（归一化后结果应与未归一化一致）
        expected_norm = cosine_topk(query_norm, docs_norm, TOP_K)

        # sqlite-vec L2（归一化后）
        conn_b = sqlite3.connect(":memory:")
        conn_b.enable_load_extension(True)
        conn_b.load_extension(sqlite_vec.loadable_path())
        setup_vec_table(conn_b, docs_norm, DIM)
        actual_norm = sqlite_vec_topk(conn_b, _embedding_to_blob(query_norm), TOP_K)
        conn_b.close()

        recall_norm = recall_at_k(expected_norm, actual_norm, TOP_K)

        # 验证余弦相似度基准是否一致（归一化不应改变余弦排序）
        cosine_consistent = expected_raw == expected_norm

        print(f"  未归一化:")
        print(f"    余弦相似度基准: {expected_raw[:5]}...")
        print(f"    sqlite-vec L2:  {actual_raw[:5]}...")
        print(f"    recall@{TOP_K}: {recall_raw:.0%}")
        print(f"  归一化后:")
        print(f"    余弦相似度基准: {expected_norm[:5]}...")
        print(f"    sqlite-vec L2:  {actual_norm[:5]}...")
        print(f"    recall@{TOP_K}: {recall_norm:.0%}")
        print(f"  余弦基准一致性: {'✅ 一致' if cosine_consistent else '❌ 不一致'}")
        print(f"  归一化提升: {recall_norm - recall_raw:+.0%}")
        print()

    print("=" * 70)
    print("结论")
    print("=" * 70)
    print("""
    数学证明:
    - 余弦相似度: cos(a,b) = (a·b) / (|a|·|b|)
    - L2 距离:    L2(a,b) = |a|² + |b|² - 2(a·b)
    - 归一化后 |a|=|b|=1, 所以 L2 = 2 - 2·cos(a,b)
    - 即: L2 距离排序 == 余弦相似度排序（反向）

    预期结果:
    - 未归一化: recall@10 较低（40-90%），因为 |a|≠|b| 时 L2 排序 ≠ 余弦排序
    - 归一化后: recall@10 = 100%（数学等价）

    生产建议:
    - save(embedding=) 时自动归一化（保证 vec0 表数据一致）
    - search(query_embedding=) 时自动归一化查询向量
    - 这样 sqlite-vec KNN 的结果与纯 Python 余弦相似度完全一致
    """)


if __name__ == "__main__":
    run_verification()
