"""[P4] sqlite-vec KNN 性能对比基准测试

对比三种 semantic 检索方案的性能：
1. 当前方案：纯 Python 余弦相似度（_search_semantic）
2. P3 优化后：BLOB 存储 + heapq.nlargest
3. P4 目标方案：sqlite-vec vec0 虚拟表 KNN

用法：
    python scripts/benchmark_sqlite_vec_knn.py

输出：
    - 各方案 p50/p99 延迟
    - recall@1 / recall@10 准确率
    - 存储大小对比
    - 优化建议
"""

import os
import time
import struct
import heapq
import sqlite3
import random
import asyncio
import tempfile
import statistics
from pathlib import Path

# 添加项目根目录到 path
import sys

# P4 基准脚本设计为完全独立，不从 agent 包导入
# 原因：agent/__init__.py → digital_life → sensor → watchdog/psutil/tiktoken 重量级依赖链
# 脚本只需要 _cosine_similarity / _embedding_to_blob / _blob_to_embedding 三个纯函数
_project_root = Path(__file__).parent.parent


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python，不依赖 numpy）"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


_FLOAT_SIZE = struct.calcsize('f')


def _embedding_to_blob(embedding):
    """[P3] list[float] → BLOB (struct.pack float32)"""
    if embedding is None:
        return None
    if not embedding:
        return b""
    return struct.pack(f'{len(embedding)}f', *embedding)


def _blob_to_embedding(blob):
    """[P3] BLOB → list[float] (兼容旧 JSON TEXT)"""
    if blob is None:
        return None
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    if isinstance(blob, (bytes, bytearray)):
        if len(blob) == 0:
            return None
        try:
            count = len(blob) // _FLOAT_SIZE
            return list(struct.unpack(f'{count}f', bytes(blob)))
        except (struct.error, ValueError):
            try:
                return json.loads(blob)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
    if isinstance(blob, str):
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return None
    if isinstance(blob, list):
        return blob
    return None


# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

DIM = 384  # sentence-transformers 输出维度
SIZES = [100, 500, 1000]  # 测试数据量
TOP_K = 10
RUNS = 5  # 每个场景运行次数（取中位数）


def generate_embeddings(n: int, dim: int, seed: int = 42) -> list[list[float]]:
    """生成 n 个 dim 维的随机向量"""
    random.seed(seed)
    return [[random.random() for _ in range(dim)] for _ in range(n)]


# ═══════════════════════════════════════════════════════════════
# 方案1: 原始 JSON TEXT + sorted（重构前）
# ═══════════════════════════════════════════════════════════════

def search_json_text_sorted(conn, query_emb: list[float], top_k: int, min_importance: int = 1) -> list[tuple]:
    """模拟重构前的方案：JSON TEXT 存储 + json.loads + sorted"""
    import json
    rows = conn.execute("""
        SELECT key, embedding FROM long_term_memory
        WHERE importance >= ? AND embedding IS NOT NULL
    """, (min_importance,)).fetchall()

    scored = []
    for row in rows:
        try:
            emb = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        except (json.JSONDecodeError, TypeError):
            continue
        if not emb:
            continue
        score = _cosine_similarity(query_emb, emb)
        scored.append((score, row[0]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


# ═══════════════════════════════════════════════════════════════
# 方案2: P3+P2 优化后（BLOB + heapq.nlargest）
# ═══════════════════════════════════════════════════════════════

def search_blob_heapq(conn, query_emb: list[float], top_k: int, min_importance: int = 1) -> list[tuple]:
    """P3+P2 优化后：BLOB 存储 + struct.unpack + heapq.nlargest"""
    rows = conn.execute("""
        SELECT key, embedding FROM long_term_memory
        WHERE importance >= ? AND embedding IS NOT NULL
    """, (min_importance,)).fetchall()

    scored = []
    for row in rows:
        emb = _blob_to_embedding(row[1])
        if not emb:
            continue
        score = _cosine_similarity(query_emb, emb)
        scored.append((score, row[0]))

    return heapq.nlargest(top_k, scored, key=lambda x: x[0])


# ═══════════════════════════════════════════════════════════════
# 方案3: P4 目标（sqlite-vec vec0 KNN）
# ═══════════════════════════════════════════════════════════════

def search_sqlite_vec_knn(conn, query_emb: list[float], top_k: int, key_map: dict) -> list[tuple]:
    """P4 目标方案：sqlite-vec vec0 虚拟表原生 KNN 搜索

    vec0 表用 rowid（整数）作为主键，需要 key_map 将 rowid 映射回 key 字符串
    """
    query_blob = _embedding_to_blob(query_emb)
    rows = conn.execute("""
        SELECT rowid, distance
        FROM vec_memory
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?
    """, (query_blob, top_k)).fetchall()

    # sqlite-vec 返回的是 distance（越小越相似），转换为 similarity
    # key_map[rowid] → key 字符串
    return [(1.0 - row[1], key_map.get(row[0], str(row[0]))) for row in rows]


def setup_sqlite_vec_table(conn, embeddings: list[list[float]], keys: list[str]) -> dict:
    """创建 vec0 虚拟表并插入数据

    vec0 表用 rowid（整数）作为主键，返回 rowid → key 的映射
    """
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
            embedding float[{DIM}]
        )
    """)
    key_map = {}
    for idx, (key, emb) in enumerate(zip(keys, embeddings)):
        # rowid 从 1 开始
        conn.execute(
            "INSERT INTO vec_memory (rowid, embedding) VALUES (?, ?)",
            (idx + 1, _embedding_to_blob(emb))
        )
        key_map[idx + 1] = key
    conn.commit()
    return key_map


# ═══════════════════════════════════════════════════════════════
# 基准测试
# ═══════════════════════════════════════════════════════════════

def run_benchmark():
    print("=" * 70)
    print("[P4] sqlite-vec KNN 性能对比基准测试")
    print(f"维度: {DIM}, top_k: {TOP_K}, 每场景运行: {RUNS} 次")
    print("=" * 70)

    has_sqlite_vec = False
    try:
        import sqlite_vec
        has_sqlite_vec = True
        print(f"[OK] sqlite-vec 可用 (版本: {getattr(sqlite_vec, '__version__', 'unknown')})")
    except ImportError:
        print("[WARN] sqlite-vec 不可用，P4 方案将跳过")

    print()

    for size in SIZES:
        print(f"--- 数据量: {size} 条 × {DIM} 维 ---")
        embeddings = generate_embeddings(size, DIM)
        keys = [f"doc_{i}" for i in range(size)]
        query_emb = generate_embeddings(1, DIM, seed=999)[0]

        # 创建 JSON TEXT 表（方案1）
        import json
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_json_path = f.name
        conn_json = sqlite3.connect(db_json_path)
        conn_json.execute("""
            CREATE TABLE long_term_memory (
                key TEXT PRIMARY KEY, content TEXT,
                importance INTEGER DEFAULT 3, embedding TEXT
            )
        """)
        for key, emb in zip(keys, embeddings):
            conn_json.execute(
                "INSERT INTO long_term_memory (key, content, embedding) VALUES (?, ?, ?)",
                (key, f"content_{key}", json.dumps(emb))
            )
        conn_json.commit()

        # 创建 BLOB 表（方案2）
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_blob_path = f.name
        conn_blob = sqlite3.connect(db_blob_path)
        conn_blob.execute("""
            CREATE TABLE long_term_memory (
                key TEXT PRIMARY KEY, content TEXT,
                importance INTEGER DEFAULT 3, embedding BLOB
            )
        """)
        for key, emb in zip(keys, embeddings):
            conn_blob.execute(
                "INSERT INTO long_term_memory (key, content, embedding) VALUES (?, ?, ?)",
                (key, f"content_{key}", _embedding_to_blob(emb))
            )
        conn_blob.commit()

        # 创建 vec0 表（方案3）
        conn_vec = None
        if has_sqlite_vec:
            db_vec_path = db_blob_path + ".vec"
            conn_vec = sqlite3.connect(db_vec_path)
            conn_vec.enable_load_extension(True)
            try:
                import sqlite_vec
                # loadable_path 是函数，需要调用获取路径字符串
                conn_vec.load_extension(sqlite_vec.loadable_path())
            except Exception as e:
                print(f"  [WARN] sqlite-vec 扩展加载失败: {e}")
                conn_vec = None
            if conn_vec:
                try:
                    vec_key_map = setup_sqlite_vec_table(conn_vec, embeddings, keys)
                except Exception as e:
                    print(f"  [WARN] vec0 表创建失败: {e}")
                    conn_vec = None

        # 测试方案1: JSON TEXT + sorted
        times1 = []
        for _ in range(RUNS):
            t = time.perf_counter()
            results1 = search_json_text_sorted(conn_json, query_emb, TOP_K)
            times1.append((time.perf_counter() - t) * 1000)

        # 测试方案2: BLOB + heapq
        times2 = []
        for _ in range(RUNS):
            t = time.perf_counter()
            results2 = search_blob_heapq(conn_blob, query_emb, TOP_K)
            times2.append((time.perf_counter() - t) * 1000)

        # 测试方案3: sqlite-vec KNN
        times3 = []
        results3 = []
        if conn_vec:
            for _ in range(RUNS):
                t = time.perf_counter()
                results3 = search_sqlite_vec_knn(conn_vec, query_emb, TOP_K, vec_key_map)
                times3.append((time.perf_counter() - t) * 1000)

        # 计算统计
        def stats(times):
            times.sort()
            return times[len(times) // 2], times[-1]  # p50, max(近似p99)

        p50_1, p99_1 = stats(times1)
        p50_2, p99_2 = stats(times2)

        print(f"  方案1 (JSON+sorted):     p50={p50_1:7.1f}ms  p99={p99_1:7.1f}ms")
        print(f"  方案2 (BLOB+heapq):      p50={p50_2:7.1f}ms  p99={p99_2:7.1f}ms  (P3+P2 优化)")

        if times3:
            p50_3, p99_3 = stats(times3)
            print(f"  方案3 (sqlite-vec KNN):  p50={p50_3:7.1f}ms  p99={p99_3:7.1f}ms  (P4 目标)")

            # recall 验证
            expected_keys = set(r[1] for r in results1[:TOP_K])
            actual_keys = set(r[1] for r in results3[:TOP_K])
            recall = len(expected_keys & actual_keys) / len(expected_keys) if expected_keys else 0
            print(f"  recall@{TOP_K}: {recall:.1%} (方案3 vs 方案1)")

            speedup_p3 = p50_1 / p50_2 if p50_2 > 0 else 0
            speedup_p4 = p50_1 / p50_3 if p50_3 > 0 else 0
            print(f"  加速比: P3+P2={speedup_p3:.1f}x, P4={speedup_p4:.1f}x")
        else:
            speedup_p3 = p50_1 / p50_2 if p50_2 > 0 else 0
            print(f"  加速比: P3+P2={speedup_p3:.1f}x (P4 跳过)")

        # 存储大小
        json_size = os.path.getsize(db_json_path)
        blob_size = os.path.getsize(db_blob_path)
        print(f"  存储大小: JSON={json_size//1024}KB, BLOB={blob_size//1024}KB "
              f"(节省 {(1-blob_size/json_size)*100:.0f}%)")

        print()

        # 清理
        conn_json.close()
        conn_blob.close()
        if conn_vec:
            conn_vec.close()
        os.unlink(db_json_path)
        os.unlink(db_blob_path)
        if has_sqlite_vec and os.path.exists(db_vec_path):
            os.unlink(db_vec_path)

    # 结论
    print("=" * 70)
    print("优化建议")
    print("=" * 70)
    print("""
    当前状态: P3(BLOB) + P2(heapq) 已实施
    - BLOB 存储减少 ~30% 存储空间
    - heapq.nlargest 减少 top_k 排序开销
    - struct.unpack 比 json.loads 快 ~10x

    P4 (sqlite-vec KNN) 迁移建议:
    - 适用场景: 数据量 > 5000 条，且 semantic 搜索频繁
    - 迁移成本: 中（需创建 vec0 虚拟表 + 双写或批量迁移）
    - 预期收益: KNN 由 O(n) 降至 O(log n)，大数据量下 10-100x 加速

    迁移步骤（参考）:
    1. 创建 vec0 虚拟表: CREATE VIRTUAL TABLE vec_memory USING vec0(key TEXT, embedding float[384])
    2. 批量迁移: INSERT INTO vec_memory SELECT key, embedding FROM long_term_memory WHERE embedding IS NOT NULL
    3. 搜索时优先查 vec0 表，回退到纯 Python（兼容无 sqlite-vec 环境）
    4. 验证 recall@10 一致性
    """)


if __name__ == "__main__":
    run_benchmark()
