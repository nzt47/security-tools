#!/usr/bin/env python
"""TLM 向量检索集成测试 — 端到端 mock 数据验证

构造 3 个语义簇（编程/美食/旅行）的 mock embedding，验证：
1. 三表合一写入（主表 + FTS + 向量表）
2. KNN 向量检索的语义正确性（同簇 distance 小，异簇 distance 大）
3. sqlite-vec 降级路径
4. 向量写入重试 + 兜底表补偿
5. FTS5 全文检索与向量检索的协同

运行方式：
    python scripts/run_tlm_vec_integration.py
"""
import asyncio
import logging
import os
import random
import sys
import tempfile
import time
from unittest.mock import patch

# 确保项目根在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.memory.adapters.holographic_adapter import HolographicAdapter


def _count_vec_rows(adapter) -> int:
    """安全查询 memories_vec 行数（需在新连接上加载 sqlite_vec 扩展）"""
    try:
        import sqlite_vec
        with adapter._get_conn() as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            row = conn.execute(f"SELECT COUNT(*) as c FROM {adapter._VEC_TABLE}").fetchone()
            return row["c"] if row else 0
    except Exception as e:
        logger.warning(f"查询向量表行数失败: {e}")
        return -1


# ── 日志配置：让 logger.info 输出到控制台 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)8s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tlm_integration")


# ──────────────────────────────────────────────────────────────
# Mock 数据构造
# ──────────────────────────────────────────────────────────────

# 3 个语义簇：用 one-hot 风格的 mock embedding（前 3 维区分簇，其余 0）
# 同簇 distance ≈ 0（仅噪声），异簇 distance ≈ sqrt(2) ≈ 1.414
DIM = 512
CLUSTERS = {
    "programming": {
        "label": "编程",
        "base": [1.0, 0.0, 0.0],  # one-hot 第 0 维
        "texts": [
            "Python 异步编程最佳实践",
            "SQLite 向量检索性能优化",
            "React 组件状态管理方案",
            "Git 分支管理策略与工作流",
            "Docker 容器化部署指南",
        ],
    },
    "food": {
        "label": "美食",
        "base": [0.0, 1.0, 0.0],  # one-hot 第 1 维
        "texts": [
            "川菜麻婆豆腐的家常做法",
            "意大利面酱料调配秘诀",
            "日式拉面汤底熬制工艺",
            "法式甜点马卡龙制作",
            "广式早茶虾饺皇食谱",
        ],
    },
    "travel": {
        "label": "旅行",
        "base": [0.0, 0.0, 1.0],  # one-hot 第 2 维
        "texts": [
            "京都樱花季赏花路线",
            "冰岛极光观测最佳时间",
            "西藏高原徒步装备清单",
            "马尔代夫浮潜攻略",
            "欧洲火车通票使用指南",
        ],
    },
}


def make_mock_embedding(cluster_id: int, seed: int = None) -> list[float]:
    """构造 mock embedding：one-hot 基底 + 小噪声

    同簇向量 distance ≈ 0.01*sqrt(512) ≈ 0.22（仅噪声）
    异簇向量 distance ≈ sqrt(2) ≈ 1.414（one-hot 正交）
    """
    rng = random.Random(seed) if seed is not None else random
    base = [0.0] * DIM
    base[cluster_id] = 1.0
    # 加小噪声让同簇向量不完全相同
    noise = [rng.gauss(0, 0.01) for _ in range(DIM)]
    return [b + n for b, n in zip(base, noise)]


def make_query_embedding(cluster_id: int) -> list[float]:
    """查询向量：纯净 one-hot，不加噪声（模拟理想查询）"""
    vec = [0.0] * DIM
    vec[cluster_id] = 1.0
    return vec


# ──────────────────────────────────────────────────────────────
# 集成测试场景
# ──────────────────────────────────────────────────────────────

def test_full_integration():
    """场景 1：端到端三表合一写入 + KNN 语义检索"""
    print("\n" + "=" * 70)
    print("场景 1：端到端三表合一写入 + KNN 语义检索")
    print("=" * 70)

    tmp_dir = tempfile.mkdtemp(prefix="tlm_integration_")
    db_path = os.path.join(tmp_dir, "integration.db")
    adapter = HolographicAdapter(db_path=db_path, enable_cache=False)

    if not adapter._vec_available:
        print(f"[SKIP] sqlite-vec 不可用（_vec_available=False），跳过向量集成测试")
        return False

    print(f"[INFO] db={db_path}, vec_available={adapter._vec_available}, dim={adapter._VEC_DIM}")

    # 写入 15 条 mock 数据
    cluster_ids = {"programming": 0, "food": 1, "travel": 2}
    written = []
    for cluster_name, cluster in CLUSTERS.items():
        cid = cluster_ids[cluster_name]
        for i, text in enumerate(cluster["texts"]):
            key = f"{cluster_name}_{i}"
            emb = make_mock_embedding(cid, seed=i)
            ok = asyncio.run(adapter.save_with_embedding(
                key, text,
                {"cluster": cluster_name, "label": cluster["label"]},
                emb,
            ))
            written.append((key, cluster_name, text, ok))

    print(f"[INFO] 已写入 {len(written)} 条记录")

    # 等待后台向量写入线程完成
    print("[INFO] 等待后台向量写入线程完成...")
    time.sleep(2.0)

    # 验证三表数据条数
    with adapter._get_conn() as conn:
        main_count = conn.execute(f"SELECT COUNT(*) as c FROM {adapter._CONTENT_TABLE}").fetchone()["c"]
        fts_count = conn.execute(f"SELECT COUNT(*) as c FROM {adapter._FTS_TABLE}").fetchone()["c"]
    vec_count = _count_vec_rows(adapter)  # 向量表需加载扩展后查询

    print(f"[VERIFY] 三表数据条数: 主表={main_count}, FTS={fts_count}, 向量表={vec_count}")
    assert main_count == 15, f"主表应有 15 条，实际 {main_count}"
    assert fts_count == 15, f"FTS 应有 15 条，实际 {fts_count}"
    assert vec_count == 15, f"向量表应有 15 条，实际 {vec_count}"

    # KNN 检索：每个簇用纯净查询向量检索 top_k=5
    all_pass = True
    for cluster_name, cluster in CLUSTERS.items():
        cid = cluster_ids[cluster_name]
        query_emb = make_query_embedding(cid)
        results = asyncio.run(adapter.search_vector(query_emb, top_k=5))

        print(f"\n[SEARCH] 查询簇={cluster['label']}({cluster_name}), top_k=5")
        print(f"  命中 {len(results)} 条:")
        for idx, r in enumerate(results):
            dist = r.metadata["distance"]
            r_key = r.metadata.get("key", "")
            print(f"    {idx + 1}. key={r_key}, content={r.content[:30]}, distance={dist:.4f}, confidence={r.confidence:.4f}")

        # 验证：top_k=5 应该都是同簇（写入时 key = f"{cluster_name}_{i}"）
        same_cluster = sum(1 for r in results if r.metadata.get("key", "").startswith(cluster_name))
        print(f"  [VERIFY] 同簇命中: {same_cluster}/5")
        if same_cluster < 5:
            print(f"  [WARN] 期望 5 条同簇，实际 {same_cluster} 条")
            all_pass = False

    # 验证 FTS5 全文检索仍正常工作（降级兜底能力）
    print("\n[FTS] 验证 FTS5 全文检索:")
    fts_results = asyncio.run(adapter.search("Python", top_k=3))
    print(f"  搜索 'Python' 命中 {len(fts_results)} 条")
    for r in fts_results:
        print(f"    - {r.content} (confidence={r.confidence:.4f})")
    assert len(fts_results) >= 1, "FTS5 应能搜到 Python 相关记录"

    print(f"\n[RESULT] 场景 1 {'通过 [PASS]' if all_pass else '部分失败 [FAIL]'}")
    return all_pass


def test_degradation_path():
    """场景 2：sqlite-vec 不可用时的降级路径"""
    print("\n" + "=" * 70)
    print("场景 2：sqlite-vec 不可用时的降级路径")
    print("=" * 70)

    tmp_dir = tempfile.mkdtemp(prefix="tlm_degrade_")
    db_path = os.path.join(tmp_dir, "degrade.db")

    # patch sqlite_vec 不可导入
    import sys as _sys
    original = _sys.modules.get("sqlite_vec")
    with patch.dict(_sys.modules, {"sqlite_vec": None}):
        adapter = HolographicAdapter(db_path=db_path, enable_cache=False)

    print(f"[INFO] vec_available={adapter._vec_available}")
    assert adapter._vec_available is False, "降级模式下 _vec_available 必须为 False"

    # 主表 + FTS 仍可写入
    ok = asyncio.run(adapter.save("degrade_key", "降级模式测试内容", {"tag": "degrade"}))
    assert ok, "降级模式下主表写入仍应成功"
    print(f"[VERIFY] 降级模式主表写入: {ok}")

    # search_vector 返回空列表
    results = asyncio.run(adapter.search_vector([0.1] * 512, top_k=5))
    assert results == [], "降级模式 search_vector 应返回空列表"
    print(f"[VERIFY] 降级模式 search_vector 返回: {results} (期望 [])")

    # FTS5 搜索仍正常
    fts_results = asyncio.run(adapter.search("降级", top_k=3))
    assert len(fts_results) >= 1, "降级模式 FTS5 应能正常搜索"
    print(f"[VERIFY] 降级模式 FTS5 搜索命中: {len(fts_results)} 条")

    # 恢复
    if original is not None:
        _sys.modules["sqlite_vec"] = original

    print("[RESULT] 场景 2 通过 [PASS]")
    return True


def test_retry_and_fallback():
    """场景 3：向量写入重试 + 兜底表补偿"""
    print("\n" + "=" * 70)
    print("场景 3：向量写入重试 + 兜底表补偿")
    print("=" * 70)

    tmp_dir = tempfile.mkdtemp(prefix="tlm_retry_")
    db_path = os.path.join(tmp_dir, "retry.db")
    adapter = HolographicAdapter(db_path=db_path, enable_cache=False)

    if not adapter._vec_available:
        print("[SKIP] sqlite-vec 不可用，跳过重试测试")
        return False

    # 场景 3a：mock _write_vec_row 前 2 次失败，第 3 次成功
    print("\n[3a] 重试 2 次后成功:")
    call_count = {"n": 0}
    original_write = adapter._write_vec_row

    def flaky_write(key, embedding):
        call_count["n"] += 1
        if call_count["n"] <= 2:
            raise RuntimeError(f"模拟第 {call_count['n']} 次失败")
        return original_write(key, embedding)

    with patch("agent.memory.adapters.holographic_adapter.time.sleep"):  # 跳过实际等待
        with patch.object(adapter, "_write_vec_row", side_effect=flaky_write):
            with patch.object(adapter, "_write_vec_failed") as mock_fallback:
                result = adapter._retry_vec_write("retry_ok_key", [0.1] * DIM, max_retries=3)

    print(f"  尝试次数: {call_count['n']} (期望 3)")
    print(f"  兜底表调用: {mock_fallback.called} (期望 False)")
    # _retry_vec_write 是 void 方法（后台线程执行），通过调用次数和兜底表未调用来验证成功
    assert call_count["n"] == 3 and not mock_fallback.called, f"期望 3 次尝试且未写兜底表，实际 {call_count['n']} 次, 兜底表调用={mock_fallback.called}"

    # 场景 3b：重试全部失败 → 写入兜底表
    print("\n[3b] 重试全部失败 → 兜底表:")
    with patch("agent.memory.adapters.holographic_adapter.time.sleep"):
        with patch.object(adapter, "_write_vec_row", side_effect=RuntimeError("持续失败")):
            result = adapter._retry_vec_write("retry_fail_key", [0.2] * DIM, max_retries=3)

    print(f"  最终结果: 已写入兜底表 (期望)")
    # 验证兜底表有记录
    with adapter._get_conn() as conn:
        row = conn.execute(
            f"SELECT key, error, retries FROM {adapter._VEC_FAILED_TABLE} WHERE key = ?",
            ("retry_fail_key",),
        ).fetchone()
    print(f"  兜底表记录: key={row['key']}, error={row['error']}, retries={row['retries']}")
    assert row is not None and row["key"] == "retry_fail_key"

    # 场景 3c：兜底表重放成功
    print("\n[3c] 兜底表重放:")
    replayed = adapter.replay_vec_failed()
    print(f"  重放成功条数: {replayed} (期望 1)")
    assert replayed == 1, f"期望重放 1 条，实际 {replayed}"

    # 验证兜底记录已删除
    with adapter._get_conn() as conn:
        row = conn.execute(
            f"SELECT key FROM {adapter._VEC_FAILED_TABLE} WHERE key = ?",
            ("retry_fail_key",),
        ).fetchone()
    print(f"  重放后兜底表记录: {'已删除 [OK]' if row is None else '仍存在 [FAIL]'}")
    assert row is None

    print("\n[RESULT] 场景 3 通过 [PASS]")
    return True


def test_embedding_callback():
    """场景 4：embedding 缺失时通过回调生成"""
    print("\n" + "=" * 70)
    print("场景 4：embedding 缺失时通过 _embedding_func 回调生成")
    print("=" * 70)

    tmp_dir = tempfile.mkdtemp(prefix="tlm_callback_")
    db_path = os.path.join(tmp_dir, "callback.db")
    adapter = HolographicAdapter(db_path=db_path, enable_cache=False)

    if not adapter._vec_available:
        print("[SKIP] sqlite-vec 不可用，跳过回调测试")
        return False

    # 注入 mock embedding 生成回调
    def mock_embed_func(text: str) -> list[float]:
        print(f"  [callback] 为文本生成 mock embedding: text='{text[:20]}...'")
        emb = [0.0] * DIM
        emb[0] = 1.0  # 简化：所有都映射到簇 0
        return emb

    adapter._embedding_func = mock_embed_func

    # embedding=None → 触发后台回调生成
    ok = asyncio.run(adapter.save_with_embedding("cb_key", "回调生成的向量", {"t": 1}, embedding=None))
    assert ok, "save_with_embedding 应成功"
    print(f"[VERIFY] save_with_embedding(embedding=None) 返回: {ok}")

    # 等待后台线程完成
    time.sleep(1.0)

    # 验证向量已写入
    results = asyncio.run(adapter.search_vector(make_query_embedding(0), top_k=3))
    print(f"[VERIFY] 向量检索命中: {len(results)} 条")
    keys = [r.metadata.get("key") for r in results]
    print(f"  命中 keys: {keys}")
    assert "cb_key" in keys, "回调生成的向量应可被检索到"

    print("[RESULT] 场景 4 通过 [PASS]")
    return True


def test_concurrent_write():
    """场景 5：并发写入无数据损坏"""
    print("\n" + "=" * 70)
    print("场景 5：10 线程并发写入无数据损坏")
    print("=" * 70)

    import threading
    tmp_dir = tempfile.mkdtemp(prefix="tlm_conc_")
    db_path = os.path.join(tmp_dir, "concurrent.db")
    adapter = HolographicAdapter(db_path=db_path, enable_cache=False)

    N = 10
    errors = []
    barrier = threading.Barrier(N)

    def worker(idx):
        try:
            barrier.wait(timeout=5)
            key = f"conc_{idx}"
            emb = make_mock_embedding(idx % 3, seed=idx)
            ok = asyncio.run(adapter.save_with_embedding(
                key, f"并发内容 {idx}", {"idx": idx}, emb
            ))
            if not ok:
                errors.append(f"worker {idx} save 返回 False")
        except Exception as e:
            errors.append(f"worker {idx} 异常: {e}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.time() - t0

    print(f"[INFO] {N} 线程并发写入完成, 耗时 {elapsed:.2f}s")
    print(f"[VERIFY] 错误数: {len(errors)}")
    if errors:
        for e in errors:
            print(f"  - {e}")

    # 等待后台向量写入
    time.sleep(2.0)

    with adapter._get_conn() as conn:
        main_count = conn.execute(
            f"SELECT COUNT(*) as c FROM {adapter._CONTENT_TABLE} WHERE key LIKE 'conc_%'"
        ).fetchone()["c"]
        fts_count = conn.execute(
            f"SELECT COUNT(*) as c FROM {adapter._FTS_TABLE} WHERE key LIKE 'conc_%'"
        ).fetchone()["c"]
    vec_count = _count_vec_rows(adapter)  # 向量表需加载扩展后查询

    print(f"[VERIFY] 主表={main_count}/10, FTS={fts_count}/10, 向量表={vec_count}")
    assert main_count == N and fts_count == N, f"主表/FTS 应各 {N} 条"
    if adapter._vec_available:
        assert vec_count == N, f"向量表应有 {N} 条，实际 {vec_count}"

    print("[RESULT] 场景 5 通过 [PASS]")
    return True


# ──────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────

def main():
    print("╔" + "═" * 68 + "╗")
    print("║" + " TLM 向量检索集成测试 — HolographicAdapter 三表合一".center(68) + "║")
    print("╚" + "═" * 68 + "╝")

    results = {}
    for name, fn in [
        ("端到端三表+KNN", test_full_integration),
        ("降级路径", test_degradation_path),
        ("重试+兜底", test_retry_and_fallback),
        ("embedding回调", test_embedding_callback),
        ("并发写入", test_concurrent_write),
    ]:
        try:
            results[name] = fn()
        except Exception as e:
            import traceback
            print(f"\n[ERROR] 场景 '{name}' 异常: {e}")
            traceback.print_exc()
            results[name] = False

    # 汇总
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " 集成测试汇总".center(68) + "║")
    print("╠" + "═" * 68 + "╣")
    passed = 0
    for name, ok in results.items():
        status = "[PASS]" if ok else "[FAIL]"
        print(f"║  {name:<20s} {status}")
        if ok:
            passed += 1
    print("╚" + "═" * 68 + "╝")
    print(f"\n总计: {passed}/{len(results)} 场景通过")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
