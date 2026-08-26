"""TLM 数据迁移脚本集成测试

覆盖：
- 1000 条测试数据迁移成功，行数一致，字段完整
- 断点续传（中断 → resume → 最终一致）
- 向量重生成（旧向量 384 维 ≠ 512 维）
- 回滚机制（建表失败 → 删 target + 保留 backup）
- dry-run 不写 target
- ChromaDB 不可用 → 全量重生成
- 吞吐量 > 100 ops/s
- 向量重生成并行度参数（--workers / --encode-batch）不破坏结果

测试用 --no-encoder + 伪 embedding，避免下载真实 bge 模型。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

# ── 加载迁移脚本模块（scripts/ 非包，用 importlib）──
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import migrate_to_tlm  # noqa: E402


# ── Helpers ──


def _has_sqlite_vec() -> bool:
    """检测 sqlite_vec 是否可用"""
    try:
        import sqlite_vec  # noqa: F401
        return True
    except ImportError:
        return False


def build_source_db(
    path: Path, n_main: int = 1000, start: int = 0
) -> None:
    """构造 source-db：memory_items + memory_fts（schema 与 HolographicAdapter 一致）"""
    from agent.memory.adapters.holographic_adapter import HolographicAdapter
    HolographicAdapter(db_path=str(path), enable_cache=False)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    now = time.time()
    rows = []
    for i in range(start, start + n_main):
        key = f"mem_{i:06d}"
        data = f"测试记忆内容 {i}，用于验证迁移完整性"
        metadata = json.dumps({"index": i, "tag": "test"}, ensure_ascii=False)
        rows.append((key, data, metadata, now, now, 0))
    conn.executemany(
        f"INSERT OR REPLACE INTO {migrate_to_tlm.CONTENT_TABLE} "
        f"(key, data, metadata, created_at, updated_at, hit_count) "
        f"VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    # 同步 FTS
    conn.executemany(
        f"INSERT INTO {migrate_to_tlm.FTS_TABLE} (key, data, metadata) VALUES (?, ?, ?)",
        [(r[0], r[1], r[2]) for r in rows],
    )
    conn.commit()
    conn.close()


def build_source_chroma(
    path: Path, n_vec: int = 100, dim: int = 384, collection: str = "agent_memory"
) -> bool:
    """构造 source-chroma：n_vec 条 dim 维向量"""
    try:
        import chromadb
    except Exception:
        return False

    try:
        client = chromadb.PersistentClient(path=str(path))
        try:
            client.delete_collection(name=collection)
        except Exception:
            pass
        coll = client.create_collection(name=collection)

        ids = [f"mem_{i:06d}" for i in range(n_vec)]
        import random
        random.seed(42)
        embeddings = [[random.gauss(0, 1) for _ in range(dim)] for _ in range(n_vec)]
        metadatas = [{"index": i} for i in range(n_vec)]
        coll.add(ids=ids, embeddings=embeddings, metadatas=metadatas)
        return True
    except Exception as e:
        pytest.skip(f"chromadb 构造失败（Windows 已知不兼容）: {e}")


def make_config(
    source_db: Path,
    source_chroma: Path,
    target_db: Path,
    backup_dir: Path,
    **overrides,
) -> migrate_to_tlm.MigrationConfig:
    """构建测试配置"""
    base = dict(
        source_db=str(source_db),
        source_chroma=str(source_chroma),
        target_db=str(target_db),
        backup_dir=str(backup_dir),
        batch_size=100,
        resume=False,
        dry_run=False,
        no_encoder=True,  # 测试用伪 embedding
        model_name="BAAI/bge-small-zh-v1.5",
        vec_dim=512,
        collection_name="agent_memory",
        workers=4,
        encode_batch=1,
    )
    base.update(overrides)
    return migrate_to_tlm.MigrationConfig(**base)


# ── Fixtures ──


@pytest.fixture
def migration_setup(tmp_path):
    """端到端测试夹具：source-db(1000) + source-chroma(100,384) + config"""
    source_db = tmp_path / "holographic.db"
    source_chroma = tmp_path / "chroma"
    target_db = tmp_path / "memory_tlm.db"
    backup_dir = tmp_path / "backup"

    build_source_db(source_db, n_main=1000)
    # chroma 可能不可用（Windows），容错构造
    build_source_chroma(source_chroma, n_vec=100, dim=384)

    config = make_config(source_db, source_chroma, target_db, backup_dir)
    yield config, source_db, target_db, backup_dir


# ── 测试用例 ──


class TestTlmMigration:
    """TLM 迁移核心测试"""

    def test_full_migration_1000_rows(self, migration_setup):
        """1000 条迁移成功，行数一致，字段完整"""
        config, source_db, target_db, _ = migration_setup

        report = migrate_to_tlm.run_migration(config)

        assert report.status == "success", f"迁移失败: {report.errors}"
        assert report.migrated_main == 1000
        assert report.failed_main == 0

        # 行数校验
        conn = sqlite3.connect(str(target_db))
        conn.row_factory = sqlite3.Row
        main_count = conn.execute(
            f"SELECT COUNT(*) FROM {migrate_to_tlm.CONTENT_TABLE}"
        ).fetchone()[0]
        fts_count = conn.execute(
            f"SELECT COUNT(*) FROM {migrate_to_tlm.FTS_TABLE}"
        ).fetchone()[0]
        assert main_count == 1000
        assert fts_count == 1000

        # 抽检字段完整性（前 10 条）
        rows = conn.execute(
            f"SELECT key, data, metadata FROM {migrate_to_tlm.CONTENT_TABLE} "
            f"ORDER BY key LIMIT 10"
        ).fetchall()
        assert len(rows) == 10
        for row in rows:
            assert row["key"]
            assert row["data"]
            meta = json.loads(row["metadata"])
            assert "index" in meta
        conn.close()

        # 校验报告
        assert report.validation["row_count_ok"] is True
        assert report.validation["sample_ok"] is True
        assert report.validation["fts_ok"] is True

    def test_resume_after_interrupt(self, migration_setup):
        """断点续传：中断 → resume → 最终一致"""
        config, source_db, target_db, _ = migration_setup

        # 模拟中断：patch write_main_batch 第 6 次抛异常
        # 同时 patch rollback 为 no-op（模拟进程被杀，来不及回滚）
        call_count = {"n": 0}
        original_write = migrate_to_tlm.write_main_batch

        def flaky_write(conn, batch):
            call_count["n"] += 1
            if call_count["n"] == 6:
                raise RuntimeError("simulated interrupt at batch 6")
            return original_write(conn, batch)

        with patch.object(migrate_to_tlm, "write_main_batch", side_effect=flaky_write):
            with patch.object(migrate_to_tlm, "rollback", lambda *a, **kw: None):
                report1 = migrate_to_tlm.run_migration(config)

        # 首次运行应失败（中断）
        assert report1.status == "failed"
        # 前 5 批应已提交（500 条）
        conn = sqlite3.connect(str(target_db))
        count_after_interrupt = conn.execute(
            f"SELECT COUNT(*) FROM {migrate_to_tlm.CONTENT_TABLE}"
        ).fetchone()[0]
        conn.close()
        assert count_after_interrupt == 500, \
            f"中断后应有 500 条，实际 {count_after_interrupt}"

        # 重跑 resume
        config_resume = replace(config, resume=True)
        report2 = migrate_to_tlm.run_migration(config_resume)

        assert report2.status == "success", f"resume 失败: {report2.errors}"
        assert report2.migrated_main == 500  # 补齐剩余 500 条
        assert report2.skipped_main == 500  # 跳过已存在 500 条

        # 最终行数一致
        conn = sqlite3.connect(str(target_db))
        final_count = conn.execute(
            f"SELECT COUNT(*) FROM {migrate_to_tlm.CONTENT_TABLE}"
        ).fetchone()[0]
        conn.close()
        assert final_count == 1000

        # 无重复 key
        conn = sqlite3.connect(str(target_db))
        distinct = conn.execute(
            f"SELECT COUNT(DISTINCT key) FROM {migrate_to_tlm.CONTENT_TABLE}"
        ).fetchone()[0]
        conn.close()
        assert distinct == 1000

    @pytest.mark.skipif(not _has_sqlite_vec(), reason="需 sqlite_vec 支持向量层")
    def test_vector_regeneration_dim_mismatch(self, tmp_path):
        """旧向量 384 维 ≠ 512 维 → 重生成 512 维"""
        source_db = tmp_path / "holographic.db"
        source_chroma = tmp_path / "chroma"
        target_db = tmp_path / "memory_tlm.db"
        backup_dir = tmp_path / "backup"

        build_source_db(source_db, n_main=1000)
        chroma_ok = build_source_chroma(source_chroma, n_vec=100, dim=384)

        config = make_config(source_db, source_chroma, target_db, backup_dir)
        report = migrate_to_tlm.run_migration(config)

        assert report.status == "success", f"迁移失败: {report.errors}"
        assert report.vec_available is True

        # 100 条 384 维向量都不匹配 → reused=0
        assert report.reused_vec == 0
        # 所有 1000 条都重生成
        assert report.regenerated_vec == 1000
        assert report.failed_vec == 0

        # 验证 target memories_vec 维度 == 512
        conn = sqlite3.connect(str(target_db))
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            vec_count = conn.execute(
                f"SELECT COUNT(*) FROM {migrate_to_tlm.VEC_TABLE}"
            ).fetchone()[0]
            assert vec_count == 1000
            row = conn.execute(
                f"SELECT embedding FROM {migrate_to_tlm.VEC_TABLE} LIMIT 1"
            ).fetchone()
            emb_blob = bytes(row[0])
            emb_list = migrate_to_tlm.deserialize_vec(emb_blob)
            assert len(emb_list) == 512
        finally:
            conn.close()

    def test_rollback_on_schema_failure(self, migration_setup):
        """建表失败 → 删 target → 保留 backup"""
        config, source_db, target_db, backup_dir = migration_setup

        # patch HolographicAdapter._init_db 抛异常（建表失败）
        with patch(
            "agent.memory.adapters.holographic_adapter.HolographicAdapter._init_db",
            side_effect=RuntimeError("schema init failed"),
        ):
            report = migrate_to_tlm.run_migration(config)

        assert report.status == "failed"
        # target-db 应被删除（回滚）
        assert not target_db.exists(), "target-db 应被回滚删除"
        # backup 应保留
        assert backup_dir.exists(), "backup 应保留"
        assert (backup_dir / source_db.name).exists(), "source-db 副本应在 backup 中"

    def test_dry_run(self, migration_setup, capsys):
        """dry-run 不写 target"""
        config, source_db, target_db, _ = migration_setup
        config = replace(config, dry_run=True)

        report = migrate_to_tlm.run_migration(config)

        assert report.status == "dry_run"
        assert not target_db.exists(), "dry-run 不应创建 target-db"
        # stdout 应有 JSON 计划
        captured = capsys.readouterr()
        plan = json.loads(captured.out)
        assert plan["status"] == "dry_run"
        assert plan["source_main_count"] == 1000

    @pytest.mark.skipif(not _has_sqlite_vec(), reason="需 sqlite_vec 支持向量层")
    def test_chroma_unavailable_fallback(self, tmp_path):
        """ChromaDB 不可用 → 全量重生成"""
        source_db = tmp_path / "holographic.db"
        source_chroma = tmp_path / "chroma"  # 不创建，模拟不存在
        target_db = tmp_path / "memory_tlm.db"
        backup_dir = tmp_path / "backup"

        build_source_db(source_db, n_main=200)  # 少量数据加速测试

        config = make_config(source_db, source_chroma, target_db, backup_dir)
        report = migrate_to_tlm.run_migration(config)

        assert report.status == "success", f"迁移失败: {report.errors}"
        assert report.chroma_available is False
        # 全量重生成
        assert report.reused_vec == 0
        assert report.regenerated_vec == 200

    def test_throughput_above_100_ops(self, migration_setup):
        """迁移吞吐量 > 100 ops/s（不含向量重生成，pseudo encoder）"""
        config, source_db, target_db, _ = migration_setup

        report = migrate_to_tlm.run_migration(config)

        assert report.status == "success"
        assert report.throughput_ops > 100, \
            f"吞吐量 {report.throughput_ops} 未达 100 ops/s"
        assert report.elapsed_sec > 0

    @pytest.mark.skipif(not _has_sqlite_vec(), reason="需 sqlite_vec 支持向量层")
    def test_workers_encode_batch_params(self, tmp_path):
        """--workers / --encode-batch 参数不破坏迁移结果"""
        source_db = tmp_path / "holographic.db"
        source_chroma = tmp_path / "chroma"
        target_db = tmp_path / "memory_tlm.db"
        backup_dir = tmp_path / "backup"

        build_source_db(source_db, n_main=500)
        config = make_config(
            source_db, source_chroma, target_db, backup_dir,
            workers=2, encode_batch=16,
        )
        report = migrate_to_tlm.run_migration(config)

        assert report.status == "success", f"迁移失败: {report.errors}"
        assert report.regenerated_vec == 500
        assert report.failed_vec == 0

        conn = sqlite3.connect(str(target_db))
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            count = conn.execute(
                f"SELECT COUNT(*) FROM {migrate_to_tlm.VEC_TABLE}"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 500


class TestTlmMigrationEdgeCases:
    """边界用例"""

    def test_empty_source(self, tmp_path):
        """空 source → 主表 0 条"""
        source_db = tmp_path / "holographic.db"
        source_chroma = tmp_path / "chroma"
        target_db = tmp_path / "memory_tlm.db"
        backup_dir = tmp_path / "backup"

        # 建空 source（只有表结构，无数据）
        from agent.memory.adapters.holographic_adapter import HolographicAdapter
        HolographicAdapter(db_path=str(source_db), enable_cache=False)

        config = make_config(source_db, source_chroma, target_db, backup_dir)
        report = migrate_to_tlm.run_migration(config)

        assert report.status == "success"
        assert report.total_main == 0
        assert report.migrated_main == 0

    def test_malformed_metadata(self, tmp_path):
        """metadata 非 JSON → 清洗为 '{}'"""
        source_db = tmp_path / "holographic.db"
        source_chroma = tmp_path / "chroma"
        target_db = tmp_path / "memory_tlm.db"
        backup_dir = tmp_path / "backup"

        # 建表 + 插入非法 metadata
        from agent.memory.adapters.holographic_adapter import HolographicAdapter
        HolographicAdapter(db_path=str(source_db), enable_cache=False)
        conn = sqlite3.connect(str(source_db))
        now = time.time()
        conn.execute(
            f"INSERT INTO {migrate_to_tlm.CONTENT_TABLE} "
            f"(key, data, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("bad_meta_001", "内容", "not-a-json", now, now),
        )
        conn.commit()
        conn.close()

        config = make_config(source_db, source_chroma, target_db, backup_dir)
        report = migrate_to_tlm.run_migration(config)

        assert report.status == "success"
        conn = sqlite3.connect(str(target_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT metadata FROM {migrate_to_tlm.CONTENT_TABLE} WHERE key = ?",
            ("bad_meta_001",),
        ).fetchone()
        conn.close()
        # 清洗为 '{}'
        assert json.loads(row["metadata"]) == {}

    def test_duplicate_key_in_source(self, tmp_path):
        """source 有重复 key → target 去重（INSERT OR IGNORE）"""
        source_db = tmp_path / "holographic.db"
        source_chroma = tmp_path / "chroma"
        target_db = tmp_path / "memory_tlm.db"
        backup_dir = tmp_path / "backup"

        from agent.memory.adapters.holographic_adapter import HolographicAdapter
        HolographicAdapter(db_path=str(source_db), enable_cache=False)
        conn = sqlite3.connect(str(source_db))
        now = time.time()
        conn.executemany(
            f"INSERT INTO {migrate_to_tlm.CONTENT_TABLE} "
            f"(key, data, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("dup_001", "内容1", "{}", now, now),
                ("dup_002", "内容2", "{}", now, now),
            ],
        )
        conn.commit()
        conn.close()

        config = make_config(source_db, source_chroma, target_db, backup_dir)
        report1 = migrate_to_tlm.run_migration(config)
        assert report1.status == "success"
        assert report1.migrated_main == 2

        # 再跑一次（resume 模式），验证不重复
        config_resume = replace(config, resume=True)
        report2 = migrate_to_tlm.run_migration(config_resume)
        assert report2.status == "success"
        assert report2.skipped_main == 2
        assert report2.migrated_main == 0

        conn = sqlite3.connect(str(target_db))
        count = conn.execute(
            f"SELECT COUNT(*) FROM {migrate_to_tlm.CONTENT_TABLE}"
        ).fetchone()[0]
        conn.close()
        assert count == 2  # 仍为 2，无重复


class TestTlmMigrationValidation:
    """校验专项"""

    def test_validate_row_count_mismatch(self, tmp_path):
        """校验函数正确识别行数不一致"""
        source_db = tmp_path / "holographic.db"
        source_chroma = tmp_path / "chroma"
        target_db = tmp_path / "memory_tlm.db"
        backup_dir = tmp_path / "backup"

        build_source_db(source_db, n_main=100)
        config = make_config(source_db, source_chroma, target_db, backup_dir)
        # 正常迁移
        report = migrate_to_tlm.run_migration(config)
        assert report.status == "success"

        # 手动从 target 删除 10 条，制造行数不一致
        conn = sqlite3.connect(str(target_db))
        conn.execute(
            f"DELETE FROM {migrate_to_tlm.CONTENT_TABLE} "
            f"WHERE key IN (SELECT key FROM {migrate_to_tlm.CONTENT_TABLE} LIMIT 10)"
        )
        conn.commit()
        conn.close()

        # 重新校验
        encoder = migrate_to_tlm.EmbeddingEncoder(config.model_name, config.vec_dim, force_pseudo=True)
        ctx = migrate_to_tlm.MigrationContext(
            config=config,
            report=migrate_to_tlm.MigrationReport(),
            encoder=encoder,
        )
        ctx.target_conn = migrate_to_tlm.open_target_conn(config.target_db, False)
        ctx.vec_available = False
        validation = migrate_to_tlm.validate(ctx)
        ctx.target_conn.close()
        assert validation["row_count_ok"] is False
