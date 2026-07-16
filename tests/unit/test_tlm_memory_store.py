"""TLM MemoryStore 单元测试 — HolographicAdapter 三表合一扩展

覆盖验收项：
1. 三表事务一致性（写主表成功但 FTS 失败时回滚）
2. 向量异步写入 + 重试（mock embedding 失败 2 次后成功）
3. sqlite-vec 不可用时降级（_vec_available=False，search 返回空）
4. Schema 迁移幂等性（连续调用两次 _migrate_schema_if_needed 不报错）
5. 并发写入（10 线程并发 save_with_embedding，验证无数据损坏）

约束遵循：
- project_memory: 持锁操作严禁包含 I/O
- project_memory: 重试逻辑必须用统一 RetryPolicy 类
- 不变量: save/search 旧接口签名未变
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

# ── 依赖可用性检测 ──
# 模块级保存真实 sqlite_vec 引用，供 autouse fixture 覆盖 conftest.py 的全局禁用
try:
    import sqlite_vec
    _REAL_SQLITE_VEC = sqlite_vec
    _HAS_SQLITE_VEC = True
except ImportError:
    _REAL_SQLITE_VEC = None
    _HAS_SQLITE_VEC = False

from agent.memory.adapters.holographic_adapter import HolographicAdapter


# ──────────────────────────────────────────────────────────────
# 公共 fixture
# ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_sqlite_vec_for_tlm_tests():
    """覆盖 conftest.py 的全局禁用，为 TLM 测试启用真实 sqlite_vec 模块。

    Why: tests/unit/conftest.py 的 _disable_optional_systems_safety fixture
    默认 patch.dict(sys.modules, {'sqlite_vec': None}) 禁用 sqlite-vec，
    避免间接实例化 VectorStore 的测试触发 55s+ 模型加载。本测试文件需要
    真实 sqlite-vec 验证向量层，通过 patch.dict 嵌套覆盖启用（内层覆盖外层）。
    """
    if _REAL_SQLITE_VEC is None:
        yield
        return
    with patch.dict(sys.modules, {'sqlite_vec': _REAL_SQLITE_VEC}):
        yield


@pytest.fixture
def tmp_db_path(tmp_path):
    """每个测试用独立的临时 db 文件"""
    return str(tmp_path / "tlm_test.db")


@pytest.fixture
def adapter(tmp_db_path):
    """默认 adapter（sqlite-vec 可用则启用，否则降级）"""
    return HolographicAdapter(db_path=tmp_db_path, enable_cache=False)


@pytest.fixture
def vec_enabled(adapter):
    """跳过 sqlite-vec 不可用的环境的向量测试"""
    if not adapter._vec_available:
        pytest.skip("sqlite-vec 不可用，跳过向量相关测试")
    return adapter


# ──────────────────────────────────────────────────────────────
# 验收 1: 三表事务一致性 — 主表写入成功但 FTS 失败时必须回滚
# ──────────────────────────────────────────────────────────────

class TestTransactionConsistency:
    """[不易] 主表 + FTS 必须同事务，FTS 失败时主表回滚"""

    @pytest.mark.unit
    def test_fts_failure_rolls_back_main_table(self, tmp_db_path):
        """FTS 插入失败时，主表 memory_items 不应残留数据"""
        # 先正常初始化一个 adapter 建好表
        real_adapter = HolographicAdapter(db_path=tmp_db_path, enable_cache=False)
        # 构造一个会让 FTS INSERT 抛异常的 conn
        # save() 内部 execute 顺序：
        #   1. INSERT INTO memory_items
        #   2. DELETE FROM memory_fts
        #   3. INSERT INTO memory_fts  ← 让这次抛异常
        real_conn = real_adapter._get_conn()

        class _FlakyConn:
            """代理真实 conn，在第 3 次 execute（FTS INSERT）时抛异常"""
            def __init__(self, inner):
                self._inner = inner
                self._call_count = 0
                self.row_factory = inner.row_factory

            def execute(self, sql, *args, **kwargs):
                self._call_count += 1
                # FTS INSERT 是第 3 次 execute（INSERT主表 / DELETE FTS / INSERT FTS）
                if self._call_count == 3 and "INSERT INTO memory_fts" in sql:
                    raise RuntimeError("模拟 FTS5 写入失败")
                return self._inner.execute(sql, *args, **kwargs)

            def commit(self):
                return self._inner.commit()

            def rollback(self):
                return self._inner.rollback()

            def close(self):
                return self._inner.close()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                # 模拟 sqlite3.Connection 的 with 行为：异常时回滚
                if exc_type is not None:
                    self._inner.rollback()
                else:
                    pass  # 正常退出不自动 commit（保持与原 conn 一致）
                return False

        flaky_conn = _FlakyConn(real_conn)

        # patch _get_conn 返回 flaky_conn
        with patch.object(real_adapter, "_get_conn", return_value=flaky_conn):
            ok = asyncio.run(real_adapter.save("tx_key", "数据内容", {"tag": "tx"}))

        # FTS 失败 → save 返回 False
        assert ok is False, "FTS 失败时 save 必须返回 False"

        # 验证主表已回滚，无残留数据
        with real_adapter._get_conn() as verify_conn:
            row = verify_conn.execute(
                "SELECT COUNT(*) as c FROM memory_items WHERE key = ?", ("tx_key",)
            ).fetchone()
            assert row["c"] == 0, "FTS 失败时主表必须回滚，不应残留 tx_key"

    @pytest.mark.unit
    def test_normal_save_commits_both_tables(self, adapter):
        """正常写入：主表 + FTS 都有数据"""
        ok = asyncio.run(adapter.save("normal_key", "正常内容", {"tag": "ok"}))
        assert ok is True
        with adapter._get_conn() as conn:
            main_count = conn.execute(
                "SELECT COUNT(*) as c FROM memory_items WHERE key = ?", ("normal_key",)
            ).fetchone()["c"]
            fts_count = conn.execute(
                "SELECT COUNT(*) as c FROM memory_fts WHERE key = ?", ("normal_key",)
            ).fetchone()["c"]
        assert main_count == 1
        assert fts_count == 1


# ──────────────────────────────────────────────────────────────
# 验收 2: 向量异步写入 + 重试（失败 2 次后成功）
# ──────────────────────────────────────────────────────────────

class TestVectorWriteRetry:
    """[变易] 向量写入失败重试 1s/2s/4s，重试耗尽写入兜底表"""

    @pytest.mark.unit
    def test_retry_succeeds_after_two_failures(self, vec_enabled):
        """mock _write_vec_row 前 2 次失败，第 3 次成功 → 最终返回 True"""
        adapter = vec_enabled
        call_count = {"n": 0}

        original_write = adapter._write_vec_row

        def flaky_write(key, embedding):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise RuntimeError(f"模拟第 {call_count['n']} 次写入失败")
            return original_write(key, embedding)

        # patch time.sleep 避免测试实际等待 1s+2s
        with patch("agent.memory.adapters.holographic_adapter.time.sleep") as mock_sleep:
            with patch.object(adapter, "_write_vec_row", side_effect=flaky_write):
                with patch.object(adapter, "_write_vec_failed") as mock_fallback:
                    result = adapter._retry_vec_write("retry_key", [0.1] * adapter._VEC_DIM, max_retries=3)

        assert result is True, "第 3 次成功后应返回 True"
        assert call_count["n"] == 3, "应尝试 3 次（2 次失败 + 1 次成功）"
        # 成功后不应写入兜底表
        mock_fallback.assert_not_called()
        # 应有 2 次退避等待（标称 1s + 2s，RetryPolicy 默认 jitter=0.1 允许 ±10% 抖动）
        assert mock_sleep.call_count == 2
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        # 验证退避序列递增且符合指数退避标称值（1s, 2s），容忍 jitter
        assert 0.9 <= delays[0] <= 1.1, f"首次退避应在 ~1s 附近（含 jitter），实际 {delays[0]}"
        assert 1.8 <= delays[1] <= 2.2, f"第二次退避应在 ~2s 附近（含 jitter），实际 {delays[1]}"
        assert delays[1] > delays[0], "退避时延必须递增"

    @pytest.mark.unit
    def test_retry_exhausted_writes_fallback_table(self, vec_enabled):
        """重试全部失败 → 写入 memories_vec_failed 兜底表"""
        adapter = vec_enabled

        with patch("agent.memory.adapters.holographic_adapter.time.sleep"):
            with patch.object(adapter, "_write_vec_row", side_effect=RuntimeError("持续失败")):
                with patch.object(adapter, "_write_vec_failed") as mock_fallback:
                    result = adapter._retry_vec_write("fail_key", [0.2] * adapter._VEC_DIM, max_retries=3)

        assert result is False, "全部失败应返回 False"
        mock_fallback.assert_called_once()
        args = mock_fallback.call_args.args
        assert args[0] == "fail_key"
        assert "持续失败" in args[2]

    @pytest.mark.unit
    def test_save_with_embedding_triggers_async_vec_write(self, vec_enabled):
        """save_with_embedding 提供 embedding 时应启动后台线程写向量"""
        adapter = vec_enabled
        thread_started = {"flag": False}

        def fake_thread_target(*args, **kwargs):
            thread_started["flag"] = True

        with patch("threading.Thread") as MockThread:
            MockThread.return_value.start = lambda: None
            # 让 Thread 构造时记录 target 调用
            MockThread.side_effect = lambda **kw: MagicMock(start=lambda: fake_thread_target(**kw))
            ok = asyncio.run(adapter.save_with_embedding(
                "async_key", "内容", {"t": 1}, [0.3] * adapter._VEC_DIM
            ))

        assert ok is True
        # 验证启动了后台线程
        assert MockThread.called, "embedding 非空时必须启动后台线程写向量"


# ──────────────────────────────────────────────────────────────
# 验收 3: sqlite-vec 不可用时降级
# ──────────────────────────────────────────────────────────────

class TestSqliteVecDegradation:
    """[不易] sqlite-vec 不可用时必须降级为纯 FTS5，禁抛异常"""

    @pytest.mark.unit
    def test_degradation_when_sqlite_vec_unavailable(self, tmp_db_path):
        """patch sqlite_vec 不可导入 → _vec_available=False，不抛异常"""
        # 让 import sqlite_vec 失败
        with patch.dict(sys.modules, {"sqlite_vec": None}):
            adapter = HolographicAdapter(db_path=tmp_db_path, enable_cache=False)

        # 不变量：降级不抛异常，且 _vec_available=False
        assert adapter._vec_available is False, "sqlite-vec 不可用时必须降级为 False"
        # 主表 + FTS 仍可用（核心兜底能力）
        ok = asyncio.run(adapter.save("degrade_key", "降级内容", {"tag": "degrade"}))
        assert ok is True
        results = asyncio.run(adapter.search("降级", top_k=5))
        assert len(results) >= 1, "降级后 FTS5 仍可搜索"

    @pytest.mark.unit
    def test_search_vector_returns_empty_when_degraded(self, tmp_db_path):
        """降级模式下 search_vector 必须返回空列表，不抛异常"""
        with patch.dict(sys.modules, {"sqlite_vec": None}):
            adapter = HolographicAdapter(db_path=tmp_db_path, enable_cache=False)

        results = asyncio.run(adapter.search_vector([0.1] * 512, top_k=5))
        assert results == [], "降级时 search_vector 必须返回空列表"

    @pytest.mark.unit
    def test_save_with_embedding_skips_vec_when_degraded(self, tmp_db_path):
        """降级模式下 save_with_embedding 仍成功（仅写主表+FTS，跳过向量）"""
        with patch.dict(sys.modules, {"sqlite_vec": None}):
            adapter = HolographicAdapter(db_path=tmp_db_path, enable_cache=False)

        ok = asyncio.run(adapter.save_with_embedding(
            "degrade_vec_key", "内容", {"t": 1}, [0.5] * 512
        ))
        assert ok is True, "降级模式主表写入仍应成功"

    @pytest.mark.unit
    def test_load_extension_failure_degrades_gracefully(self, tmp_db_path):
        """sqlite_vec.load 抛异常时也必须降级"""
        if not _HAS_SQLITE_VEC:
            pytest.skip("sqlite-vec 未安装，无法 patch load 方法")
        original_load = _REAL_SQLITE_VEC.load

        def failing_load(conn):
            raise RuntimeError("模拟 load_extension not authorized")

        with patch.object(_REAL_SQLITE_VEC, "load", side_effect=failing_load):
            adapter = HolographicAdapter(db_path=tmp_db_path, enable_cache=False)

        assert adapter._vec_available is False, "load 失败时必须降级"
        # 恢复
        _REAL_SQLITE_VEC.load = original_load


# ──────────────────────────────────────────────────────────────
# 验收 4: Schema 迁移幂等性
# ──────────────────────────────────────────────────────────────

class TestSchemaMigrationIdempotency:
    """[简易] _migrate_schema_if_needed 必须幂等，可安全重复调用"""

    @pytest.mark.unit
    def test_migration_adds_missing_columns(self, tmp_db_path):
        """首次迁移应补齐 access_count/last_accessed/type/category"""
        adapter = HolographicAdapter(db_path=tmp_db_path, enable_cache=False)
        with adapter._get_conn() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(memory_items)").fetchall()}

        for required in ("access_count", "last_accessed", "type", "category"):
            assert required in cols, f"迁移后应包含字段 {required}"

    @pytest.mark.unit
    def test_migration_idempotent_on_second_call(self, adapter):
        """连续第二次调用 _migrate_schema_if_needed 不应报错"""
        # adapter 初始化时已迁移一次，这里再调一次
        # 不应抛异常
        adapter._migrate_schema_if_needed()
        adapter._migrate_schema_if_needed()  # 第三次再确认幂等

        # 字段仍在
        with adapter._get_conn() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(memory_items)").fetchall()}
        for required in ("access_count", "last_accessed", "type", "category"):
            assert required in cols

    @pytest.mark.unit
    def test_failed_table_created_after_migration(self, adapter):
        """迁移后 memories_vec_failed 兜底表必须存在"""
        with adapter._get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                ("memories_vec_failed",),
            ).fetchone()
        assert row is not None, "memories_vec_failed 表必须存在"


# ──────────────────────────────────────────────────────────────
# 验收 5: 并发写入 — 10 线程并发 save_with_embedding 无数据损坏
# ──────────────────────────────────────────────────────────────

class TestConcurrentWrite:
    """[变易] 并发写入主表+FTS 无数据损坏"""

    @pytest.mark.unit
    def test_ten_threads_concurrent_save_no_corruption(self, vec_enabled):
        """10 线程并发 save_with_embedding，验证主表数据完整"""
        adapter = vec_enabled
        N = 10
        errors = []
        barrier = threading.Barrier(N)

        def worker(idx):
            try:
                barrier.wait(timeout=5)
                key = f"conc_{idx}"
                # 短向量避免大内存开销
                emb = [float(idx)] * adapter._VEC_DIM
                ok = asyncio.run(adapter.save_with_embedding(
                    key, f"并发内容 {idx}", {"idx": idx}, emb
                ))
                if not ok:
                    errors.append(f"worker {idx} save 返回 False")
            except Exception as e:
                errors.append(f"worker {idx} 异常: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"并发写入出现错误: {errors}"

        # 等待后台向量写入线程完成
        time.sleep(1.5)

        # 验证主表数据完整（10 条全到）
        with adapter._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM memory_items WHERE key LIKE 'conc_%'"
            ).fetchone()["c"]
        assert count == N, f"主表应有 {N} 条，实际 {count}"

        # 验证 FTS 数据完整
        with adapter._get_conn() as conn:
            fts_count = conn.execute(
                "SELECT COUNT(*) as c FROM memory_fts WHERE key LIKE 'conc_%'"
            ).fetchone()["c"]
        assert fts_count == N, f"FTS 应有 {N} 条，实际 {fts_count}"

        # 验证每个 key 的内容正确（无串号）
        with adapter._get_conn() as conn:
            rows = conn.execute(
                "SELECT key, data FROM memory_items WHERE key LIKE 'conc_%' ORDER BY key"
            ).fetchall()
        for row in rows:
            idx = int(row["key"].split("_")[1])
            assert row["data"] == f"并发内容 {idx}", f"数据串号: key={row['key']}, data={row['data']}"

    @pytest.mark.unit
    def test_concurrent_save_without_embedding(self, adapter):
        """降级模式（无向量）下并发写入也无损坏"""
        N = 10
        errors = []
        barrier = threading.Barrier(N)

        def worker(idx):
            try:
                barrier.wait(timeout=5)
                ok = asyncio.run(adapter.save(f"plain_{idx}", f"内容 {idx}", {"idx": idx}))
                if not ok:
                    errors.append(f"worker {idx} save 返回 False")
            except Exception as e:
                errors.append(f"worker {idx} 异常: {e}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"并发写入错误: {errors}"

        with adapter._get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM memory_items WHERE key LIKE 'plain_%'"
            ).fetchone()["c"]
        assert count == N


# ──────────────────────────────────────────────────────────────
# 接口契约验证 — save/search 旧接口签名不变
# ──────────────────────────────────────────────────────────────

class TestInterfaceContract:
    """[不易] 旧接口签名不可变"""

    @pytest.mark.unit
    def test_save_signature_unchanged(self):
        """save(key, data, metadata=None) 签名不变"""
        import inspect
        sig = inspect.signature(HolographicAdapter.save)
        params = list(sig.parameters.keys())
        assert params == ["self", "key", "data", "metadata"], f"save 签名变化: {params}"
        # metadata 默认 None
        assert sig.parameters["metadata"].default is None

    @pytest.mark.unit
    def test_search_signature_unchanged(self):
        """search(query, top_k=5) 签名不变"""
        import inspect
        sig = inspect.signature(HolographicAdapter.search)
        params = list(sig.parameters.keys())
        assert params == ["self", "query", "top_k"], f"search 签名变化: {params}"
        assert sig.parameters["top_k"].default == 5

    @pytest.mark.unit
    def test_new_methods_exist(self):
        """新增方法存在"""
        assert hasattr(HolographicAdapter, "save_with_embedding")
        assert hasattr(HolographicAdapter, "search_vector")
        assert hasattr(HolographicAdapter, "_init_vec_table")
        assert hasattr(HolographicAdapter, "_migrate_schema_if_needed")
        assert hasattr(HolographicAdapter, "_retry_vec_write")
