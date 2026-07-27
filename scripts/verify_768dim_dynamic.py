"""[P4 修复验证] 768 维（bge-large）embedding 动态维度测试

验证修复后的 _init_vec_table 在 768 维数据下能否：
1. 正确创建 vec0 表（维度=768，不是 384）
2. save 双写成功（主表 + vec0 表）
3. search semantic 走 KNN 路径返回正确结果
4. recall@k = 100%（归一化向量 + L2 距离）
5. 维度不匹配时正确降级（vec0 表 384 维 + 数据 768 维 → 降级纯 Python）

运行方式：
    python scripts/verify_768dim_dynamic.py
    pytest scripts/verify_768dim_dynamic.py -v

环境说明：
    - sqlite-vec 可用：测试 KNN 路径
    - sqlite-vec 不可用：测试纯 Python 降级路径（跳过 KNN 验证）
"""

import asyncio
import math
import os
import sqlite3
import struct
import sys
import tempfile
import shutil
import gc
from pathlib import Path

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.memory.long_term_memory import (
    LongTermMemory,
    _normalize_vector,
    _embedding_to_blob,
    _blob_to_embedding,
    _check_sqlite_vec_available,
)


# ═══════════════════════════════════════════════════════════════
# [Windows 兼容] 临时目录管理器
# SQLite 连接占用 db 文件导致 TemporaryDirectory cleanup 失败，
# 改用手动管理 + gc + 宽容删除
# ═══════════════════════════════════════════════════════════════

class TmpDir:
    """手动管理的临时目录（Windows 兼容）"""

    def __init__(self, prefix: str = "tlm_test_"):
        self.path = tempfile.mkdtemp(prefix=prefix)

    def cleanup(self):
        """宽容删除：强制 gc 后尝试删除，失败则忽略（OS 会在重启后清理）"""
        gc.collect()  # 强制垃圾回收，关闭 SQLite 连接
        try:
            shutil.rmtree(self.path, ignore_errors=True)
        except Exception:
            pass  # Windows 上文件可能被占用，忽略

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup()

    def join(self, name: str) -> str:
        return os.path.join(self.path, name)


# ═══════════════════════════════════════════════════════════════
# 测试数据生成
# ═══════════════════════════════════════════════════════════════

def make_embedding(dim: int, seed: int, scale: float = 1.0) -> list[float]:
    """生成确定性的 dim 维 embedding（基于 seed，可复现）"""
    import random
    rng = random.Random(seed)
    return [rng.gauss(0, scale) for _ in range(dim)]


def make_similar_embedding(base: list[float], noise_scale: float = 0.1) -> list[float]:
    """生成与 base 相似的 embedding（加小噪声）"""
    import random
    rng = random.Random(42)
    return [x + rng.gauss(0, noise_scale) for x in base]


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def get_vec_table_dim(db_path: str) -> int | None:
    """查询 vec0 表的维度"""
    import re
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ltm_vec_index'"
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    match = re.search(r'float\[(\d+)\]', row[0])
    return int(match.group(1)) if match else None


def get_vec_count(db_path: str) -> int:
    """查询 vec0 表的条目数"""
    try:
        import sqlite_vec
        conn = sqlite3.connect(db_path)
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        count = conn.execute("SELECT COUNT(*) FROM ltm_vec_index").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return -1


def recall_at_k(expected: list[str], actual: list[str], k: int) -> float:
    """计算 recall@k"""
    expected_set = set(expected[:k])
    actual_set = set(actual[:k])
    if not expected_set:
        return 1.0
    return len(expected_set & actual_set) / len(expected_set)


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════

VEC_AVAILABLE = _check_sqlite_vec_available()
DIM_768 = 768  # bge-large 维度
DIM_384 = 384  # bge-small 维度（向后兼容验证）


async def test_768_dim_vec_table_creation():
    """[测试1] 768 维数据：vec0 表应创建为 768 维（不是 384）"""
    print("\n[测试1] 768 维 vec0 表创建")
    print(f"  sqlite-vec 可用: {VEC_AVAILABLE}")

    with TmpDir() as tmpdir:
        db_path = tmpdir.join("test_768.db")

        # 先写入一条 768 维数据，让 _detect_embedding_dim 能检测到维度
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE long_term_memory (
                key TEXT PRIMARY KEY, content TEXT NOT NULL,
                importance INTEGER DEFAULT 3, tags TEXT DEFAULT '[]',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                last_accessed REAL NOT NULL, access_count INTEGER DEFAULT 0,
                sensitive INTEGER DEFAULT 0, verified INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}', embedding BLOB DEFAULT NULL
            )
        """)
        emb = make_embedding(DIM_768, seed=1)
        conn.execute(
            "INSERT INTO long_term_memory (key, content, created_at, updated_at, last_accessed, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("seed", "seed_content", 1000.0, 1000.0, 1000.0, _embedding_to_blob(emb))
        )
        conn.commit()
        conn.close()

        # 实例化 LTM（_init_vec_table 会检测到 768 维数据）
        ltm = LongTermMemory(db_path=db_path)

        if VEC_AVAILABLE:
            vec_dim = get_vec_table_dim(db_path)
            print(f"  vec0 表维度: {vec_dim}")
            assert vec_dim == DIM_768, f"[FAIL] vec0 表维度应为 {DIM_768}，实际 {vec_dim}"
            print(f"  [OK] vec0 表创建为 {DIM_768} 维（动态推断成功）")
        else:
            print(f"  [SKIP] sqlite-vec 不可用，跳过 vec0 表维度检查")


async def test_768_dim_save_and_search():
    """[测试2] 768 维数据：save + search semantic 全链路"""
    print("\n[测试2] 768 维 save + search semantic")

    with TmpDir() as tmpdir:
        db_path = tmpdir.join("test_768_search.db")
        ltm = LongTermMemory(db_path=db_path)

        # 准备 5 条 768 维 embedding
        # emb1 和 emb2 相似（同主题），emb3/emb4/emb5 不同
        base_emb = make_embedding(DIM_768, seed=100)
        emb1 = base_emb
        emb2 = make_similar_embedding(base_emb, noise_scale=0.05)  # 与 emb1 很相似
        emb3 = make_embedding(DIM_768, seed=200)  # 不同主题
        emb4 = make_embedding(DIM_768, seed=300)  # 不同主题
        emb5 = make_embedding(DIM_768, seed=400)  # 不同主题

        await ltm.save("doc1", "文档1内容", embedding=emb1, tags=["topic_a"])
        await ltm.save("doc2", "文档2内容", embedding=emb2, tags=["topic_a"])
        await ltm.save("doc3", "文档3内容", embedding=emb3, tags=["topic_b"])
        await ltm.save("doc4", "文档4内容", embedding=emb4, tags=["topic_c"])
        await ltm.save("doc5", "文档5内容", embedding=emb5, tags=["topic_d"])

        # 验证主表写入
        entry = await ltm.get("doc1")
        assert entry is not None, "[FAIL] get('doc1') 返回 None"
        assert entry.embedding is not None, "[FAIL] embedding 为 None"
        assert len(entry.embedding) == DIM_768, f"[FAIL] embedding 维度应为 {DIM_768}，实际 {len(entry.embedding)}"
        print(f"  [OK] 主表写入: 5 条 768 维 embedding")

        # 验证 get 返回原始 embedding（不是归一化值）
        assert entry.embedding == emb1 or all(
            abs(a - b) < 1e-5 for a, b in zip(entry.embedding, emb1)
        ), "[FAIL] get 应返回原始 embedding，不是归一化值"
        print(f"  [OK] get 返回原始 embedding（非归一化值）")

        if VEC_AVAILABLE:
            # 验证 vec0 表写入
            vec_count = get_vec_count(db_path)
            print(f"  vec0 表条目数: {vec_count}")
            assert vec_count == 5, f"[FAIL] vec0 表应有 5 条，实际 {vec_count}"

            # search semantic：用 emb1 查询，emb1 和 emb2 应排前 2
            results = await ltm.search("query", mode="semantic", query_embedding=emb1, top_k=3)
            print(f"  KNN 搜索返回: {len(results)} 条")
            assert len(results) >= 2, f"[FAIL] 应至少返回 2 条结果"

            top_keys = [r.metadata.get("key") for r in results[:2]]
            print(f"  Top-2 keys: {top_keys}")
            assert "doc1" in top_keys, f"[FAIL] doc1 应在 Top-2 中"
            assert "doc2" in top_keys, f"[FAIL] doc2（与 doc1 相似）应在 Top-2 中"
            print(f"  [OK] KNN 搜索正确返回相似文档（doc1, doc2）")
        else:
            # 纯 Python 降级路径
            results = await ltm.search("query", mode="semantic", query_embedding=emb1, top_k=3)
            print(f"  纯 Python 搜索返回: {len(results)} 条")
            assert len(results) >= 2, f"[FAIL] 应至少返回 2 条结果"
            print(f"  [OK] 纯 Python 降级路径正常")


async def test_768_dim_recall():
    """[测试3] 768 维 recall@10 验证（KNN vs 纯 Python 一致性）"""
    print("\n[测试3] 768 维 recall@10 验证")

    if not VEC_AVAILABLE:
        print("  [SKIP] sqlite-vec 不可用，跳过 recall 对比")
        return

    with TmpDir() as tmpdir:
        db_path = tmpdir.join("test_768_recall.db")
        ltm = LongTermMemory(db_path=db_path)

        # 写入 20 条 768 维数据
        keys = []
        embeddings = []
        for i in range(20):
            emb = make_embedding(DIM_768, seed=i * 10)
            key = f"item_{i}"
            await ltm.save(key, f"内容_{i}", embedding=emb)
            keys.append(key)
            embeddings.append(emb)

        # 用第 0 条 embedding 查询
        query_emb = embeddings[0]
        top_k = 10

        # KNN 路径结果
        knn_results = await ltm.search("query", mode="semantic", query_embedding=query_emb, top_k=top_k)
        knn_keys = [r.metadata.get("key") for r in knn_results]

        # 纯 Python 路径结果（临时禁用 KNN）
        original_flag = ltm._use_vec_knn
        ltm._use_vec_knn = False
        python_results = await ltm.search("query", mode="semantic", query_embedding=query_emb, top_k=top_k)
        python_keys = [r.metadata.get("key") for r in python_results]
        ltm._use_vec_knn = original_flag  # 恢复

        # recall@10
        recall = recall_at_k(python_keys, knn_keys, top_k)
        print(f"  KNN Top-{top_k}: {knn_keys}")
        print(f"  Python Top-{top_k}: {python_keys}")
        print(f"  recall@{top_k}: {recall:.2%}")

        assert recall == 1.0, f"[FAIL] recall@{top_k} 应为 100%，实际 {recall:.2%}"
        print(f"  [OK] recall@{top_k} = 100%（归一化向量 L2 排序 == 余弦相似度排序）")


async def test_dimension_mismatch_degradation():
    """[测试4] 维度不匹配时降级纯 Python"""
    print("\n[测试4] 维度不匹配降级验证")

    if not VEC_AVAILABLE:
        print("  [SKIP] sqlite-vec 不可用，跳过此测试")
        return

    with TmpDir() as tmpdir:
        db_path = tmpdir.join("test_mismatch.db")

        # Step 1: 先创建 384 维 vec0 表（模拟旧数据）
        import sqlite_vec
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE long_term_memory (
                key TEXT PRIMARY KEY, content TEXT NOT NULL,
                importance INTEGER DEFAULT 3, tags TEXT DEFAULT '[]',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                last_accessed REAL NOT NULL, access_count INTEGER DEFAULT 0,
                sensitive INTEGER DEFAULT 0, verified INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}', embedding BLOB DEFAULT NULL
            )
        """)
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        conn.execute("CREATE VIRTUAL TABLE ltm_vec_index USING vec0(embedding float[384])")
        # 写入一条 384 维数据
        emb384 = make_embedding(DIM_384, seed=1)
        conn.execute(
            "INSERT INTO long_term_memory (key, content, created_at, updated_at, last_accessed, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("old", "old_content", 1000.0, 1000.0, 1000.0, _embedding_to_blob(emb384))
        )
        conn.commit()
        conn.close()

        # Step 2: 模拟升级到 768 维模型，写入 768 维数据
        conn = sqlite3.connect(db_path)
        emb768 = make_embedding(DIM_768, seed=2)
        conn.execute(
            "INSERT INTO long_term_memory (key, content, created_at, updated_at, last_accessed, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("new", "new_content", 2000.0, 2000.0, 2000.0, _embedding_to_blob(emb768))
        )
        conn.commit()
        conn.close()

        # Step 3: 实例化 LTM，应检测到维度不匹配并降级
        ltm = LongTermMemory(db_path=db_path)

        # 验证降级
        assert ltm._use_vec_knn is False, "[FAIL] 维度不匹配应降级 _use_vec_knn=False"
        print(f"  [OK] 检测到维度不匹配（vec0=384, data=768），降级纯 Python")

        # 验证降级后仍能 search（纯 Python 路径）
        results = await ltm.search("query", mode="semantic", query_embedding=emb768, top_k=2)
        print(f"  降级后 search 返回: {len(results)} 条")
        assert len(results) >= 1, "[FAIL] 降级后应仍能 search"
        print(f"  [OK] 降级后纯 Python search 正常")


async def test_384_dim_backward_compatibility():
    """[测试5] 384 维数据向后兼容（无数据时默认 384）"""
    print("\n[测试5] 384 维向后兼容")

    with TmpDir() as tmpdir:
        db_path = tmpdir.join("test_384_compat.db")
        ltm = LongTermMemory(db_path=db_path)  # 空数据库

        # 写入 384 维数据
        emb = make_embedding(DIM_384, seed=1)
        await ltm.save("k1", "content1", embedding=emb)

        entry = await ltm.get("k1")
        assert entry is not None
        assert len(entry.embedding) == DIM_384
        print(f"  [OK] 384 维数据写入/读取正常（向后兼容）")

        if VEC_AVAILABLE:
            vec_dim = get_vec_table_dim(db_path)
            print(f"  vec0 表维度: {vec_dim}")
            assert vec_dim == DIM_384, f"[FAIL] vec0 表维度应为 {DIM_384}，实际 {vec_dim}"
            print(f"  [OK] 空数据库默认创建 384 维 vec0 表（向后兼容）")


async def test_mixed_dimension_save():
    """[测试6] 混合维度 save：768 维 vec0 表写入 384 维数据应不影响主表"""
    print("\n[测试6] 混合维度 save（主表不受 vec0 失败影响）")

    if not VEC_AVAILABLE:
        print("  [SKIP] sqlite-vec 不可用，跳过此测试")
        return

    with TmpDir() as tmpdir:
        db_path = tmpdir.join("test_mixed.db")
        ltm = LongTermMemory(db_path=db_path)

        # 先写入 768 维数据（vec0 表创建为 768 维）
        emb768 = make_embedding(DIM_768, seed=1)
        await ltm.save("k1", "content_768", embedding=emb768)

        # 再写入 384 维数据（vec0 INSERT 应失败，但主表应成功）
        emb384 = make_embedding(DIM_384, seed=2)
        ok = await ltm.save("k2", "content_384", embedding=emb384)
        assert ok is True, "[FAIL] vec0 双写失败不应影响主表 save 返回值"
        print(f"  [OK] 384 维数据写入 768 维 vec0 表：主表 save 成功")

        # 验证主表数据完整
        entry = await ltm.get("k2")
        assert entry is not None, "[FAIL] get('k2') 返回 None"
        assert len(entry.embedding) == DIM_384, f"[FAIL] embedding 维度应为 {DIM_384}"
        print(f"  [OK] 主表 384 维 embedding 完整保存")


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("[P4 修复验证] 768 维动态维度测试")
    print(f"sqlite-vec 可用: {VEC_AVAILABLE}")
    print(f"Python: {sys.version.split()[0]}")
    print("=" * 60)

    tests = [
        ("768 维 vec0 表创建", test_768_dim_vec_table_creation),
        ("768 维 save + search", test_768_dim_save_and_search),
        ("768 维 recall@10", test_768_dim_recall),
        ("维度不匹配降级", test_dimension_mismatch_degradation),
        ("384 维向后兼容", test_384_dim_backward_compatibility),
        ("混合维度 save", test_mixed_dimension_save),
    ]

    passed = 0
    failed = 0
    skipped = 0

    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {e}")
            failed += 1
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: 通过={passed}, 失败={failed}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    # 兼容 pytest 和直接运行
    if "pytest" in sys.modules:
        # pytest 模式：暴露 async 测试函数
        async def test_768_dim_vec_table_creation_pytest():
            await test_768_dim_vec_table_creation()

        async def test_768_dim_save_and_search_pytest():
            await test_768_dim_save_and_search()

        async def test_768_dim_recall_pytest():
            await test_768_dim_recall()

        async def test_dimension_mismatch_degradation_pytest():
            await test_dimension_mismatch_degradation()

        async def test_384_dim_backward_compatibility_pytest():
            await test_384_dim_backward_compatibility()

        async def test_mixed_dimension_save_pytest():
            await test_mixed_dimension_save()
    else:
        # 直接运行模式
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
