#!/usr/bin/env python3
"""
TLM 数据迁移脚本（一次性工具）

功能：
- 从旧 SQLite（holographic.db，schema: memory_items + memory_fts）批量迁移到新 target-db
- 从旧 ChromaDB（./data/chroma）读取向量，按 key 对齐到主表
- 旧向量维度 ≠ 目标维度时，用 bge-small-zh-v1.5 重新生成
- 支持断点续传（--resume）、干跑（--dry-run）、伪 embedding（--no-encoder，测试用）
- 迁移前自动备份 source；致命失败自动回滚（删 target-db）

不变量【不易】：
1. source-db / source-chroma 全程只读（只读 URI 连接）
2. 主表 INSERT + FTS DELETE+INSERT 同一事务
3. vec0 不支持 UPDATE → DELETE+INSERT
4. 向量写入重试用 agent.error_handler.RetryPolicy
5. sqlite-vec 不可用时降级为纯 FTS5（不中断主表迁移）
6. 向量写入失败写 memories_vec_failed 表，不中断
7. PRAGMA busy_timeout=5000
8. 向量重生成用本地 bge-small-zh-v1.5，禁云端 API

用法：
    # 默认全量迁移
    python scripts/migrate_to_tlm.py

    # 断点续传
    python scripts/migrate_to_tlm.py --resume

    # 干跑（仅打印计划）
    python scripts/migrate_to_tlm.py --dry-run

    # 测试模式（伪 embedding，无需下载真实模型）
    python scripts/migrate_to_tlm.py --no-encoder

    # 指定向量重生成并行度（性能调优）
    python scripts/migrate_to_tlm.py --workers 4 --encode-batch 32

退出码：0=成功，1=失败（已回滚）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

# 确保项目根目录在 sys.path（脚本直接运行时 sys.path[0] 是 scripts/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 常量（与 HolographicAdapter 对齐，见 agent/memory/adapters/holographic_adapter.py:51-57）──

CONTENT_TABLE = "memory_items"            # 主表（非 memories）
FTS_TABLE = "memory_fts"                  # FTS5 表（非 memories_fts）
VEC_TABLE = "memories_vec"                # 向量表（vec0 虚拟表）
VEC_FAILED_TABLE = "memories_vec_failed"  # 向量写入兜底表
DEFAULT_VEC_DIM = 512                     # bge-small-zh-v1.5 输出维度

DEFAULT_SOURCE_DB = "./data/memory/holographic.db"
DEFAULT_SOURCE_CHROMA = "./data/chroma"
DEFAULT_TARGET_DB = "./data/memory/memory_tlm.db"
DEFAULT_BATCH_SIZE = 100
DEFAULT_COLLECTION = "agent_memory"
DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"

# 向量重生成并行度（性能调优参数）
# 2026-08-26 基准结论（scripts/benchmark_tlm_regen.py，bge-m3 CPU 实测）：
#   - encode_batch 是决定性因素：batch=64 比 batch=1 快 2.16x（批量推理利用 torch 底层并行）
#   - workers 几乎无增益：torch 已占 6 底层线程，多 Python worker 争抢 GIL/CPU 反而略降
#   - 最优组合 workers=1 + encode_batch=64（实测 11.7 items/s vs 原 5.4 items/s）
DEFAULT_WORKERS = 1       # ThreadPoolExecutor max_workers（torch 已并行，多 worker 无益）
DEFAULT_ENCODE_BATCH = 64  # 每次 encode 的批量大小（批量推理是主要提速点）

# 环境变量覆盖（遵循项目约束：配置走 .env）
ENV_MODEL = os.environ.get("TLM_EMBEDDING_MODEL", DEFAULT_MODEL)
ENV_VEC_DIM = int(os.environ.get("TLM_EMBEDDING_DIM", str(DEFAULT_VEC_DIM)))


# ── 工具函数（复用 migrate_to_sqlite_vec.py 模式）──


def log(msg: str, *, level: str = "INFO") -> None:
    """输出到 stderr 的日志（stdout 留给 JSON 报告）"""
    print(f"[{level}] {msg}", file=sys.stderr, flush=True)


def serialize_vec(v: list[float]) -> bytes:
    """float list → little-endian float32 blob（sqlite-vec 期望格式）"""
    return struct.pack(f"<{len(v)}f", *v)


def deserialize_vec(blob: bytes) -> list[float]:
    """little-endian float32 blob → float list（校验时反序列化 memories_vec 的 embedding）"""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def deterministic_pseudo_embedding(text: str, dim: int) -> list[float]:
    """基于 hash 的确定性伪 embedding（无 sentence_transformers 时用，仅测试）

    同一 text 总是生成同一向量，保证迁移可重试 + 断点续传幂等。
    """
    result = [0.0] * dim
    if not text:
        return result
    tokens = text.lower().split()
    for token in tokens:
        h = hashlib.md5(token.encode("utf-8")).digest()
        for i in range(dim):
            byte_idx = (i * 4) % len(h)
            val = struct.unpack("<I", h[byte_idx:byte_idx + 4])[0]
            result[i] += (val / 0xFFFFFFFF - 0.5) * 0.1
    norm = sum(x * x for x in result) ** 0.5
    if norm > 0:
        result = [x / norm for x in result]
    return result


# ── Embedding 编码器（复用 + 新增 encode_batch）──


class EmbeddingEncoder:
    """embedding 编码器：优先 sentence_transformers，回退伪 embedding

    线程安全：模型在主线程加载一次，worker 共享 encode 推理（SentenceTransformer.encode 只读推理）。
    """

    def __init__(self, model_name: str, dim: int, force_pseudo: bool = False):
        self.model_name = model_name
        self.dim = dim
        self._model = None
        self._mode = "pseudo"
        self._actual_dim = dim

        if force_pseudo:
            log("强制使用伪 embedding (--no-encoder)")
            return

        try:
            from sentence_transformers import SentenceTransformer
            log(f"加载 sentence_transformers 模型: {model_name} ...")
            t0 = time.perf_counter()
            self._model = SentenceTransformer(model_name)
            self._actual_dim = self._model.get_sentence_embedding_dimension()
            self._mode = "sentence_transformers"
            log(f"模型加载成功, 实际维度={self._actual_dim}, 耗时={(time.perf_counter()-t0)*1000:.0f}ms")
        except ImportError:
            log("sentence_transformers 未安装, 使用伪 embedding", level="WARNING")
        except Exception as e:
            log(f"sentence_transformers 加载失败: {type(e).__name__}: {e}", level="WARNING")
            log("回退到伪 embedding", level="WARNING")

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def actual_dim(self) -> int:
        return self._actual_dim

    def encode(self, text: str) -> list[float]:
        if self._model is not None:
            vec = self._model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        return deterministic_pseudo_embedding(text, self.dim)

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """批量编码（sentence_transformers 原生支持批量，性能更优）"""
        if self._model is not None:
            vecs = self._model.encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vecs]
        return [deterministic_pseudo_embedding(t, self.dim) for t in texts]


# ── 数据类 ──


@dataclass
class MigrationConfig:
    """迁移配置"""
    source_db: str
    source_chroma: str
    target_db: str
    backup_dir: str
    batch_size: int = DEFAULT_BATCH_SIZE
    resume: bool = False
    dry_run: bool = False
    no_encoder: bool = False
    model_name: str = ENV_MODEL
    vec_dim: int = ENV_VEC_DIM
    collection_name: str = DEFAULT_COLLECTION
    workers: int = DEFAULT_WORKERS          # 向量重生成并行度
    encode_batch: int = DEFAULT_ENCODE_BATCH  # 批量编码大小


@dataclass
class MigrationReport:
    """迁移报告"""
    status: str = "pending"  # pending | success | failed | empty | dry_run
    # 主表
    total_main: int = 0
    migrated_main: int = 0
    skipped_main: int = 0
    failed_main: int = 0
    # 向量
    total_vec: int = 0
    reused_vec: int = 0
    regenerated_vec: int = 0
    failed_vec: int = 0
    # 校验
    validation: dict = field(default_factory=dict)
    # 元信息
    errors: list[dict] = field(default_factory=list)
    elapsed_sec: float = 0.0          # 总耗时
    main_elapsed_sec: float = 0.0     # 主表迁移耗时（不含向量重生成）
    vec_elapsed_sec: float = 0.0      # 向量重生成耗时
    throughput_ops: float = 0.0       # 主表迁移吞吐量（不含向量重生成）
    vec_throughput_ops: float = 0.0   # 向量重生成吞吐量
    vec_available: bool = False
    chroma_available: bool = False

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "main": {
                "total": self.total_main,
                "migrated": self.migrated_main,
                "skipped": self.skipped_main,
                "failed": self.failed_main,
            },
            "vectors": {
                "total": self.total_vec,
                "reused": self.reused_vec,
                "regenerated": self.regenerated_vec,
                "failed": self.failed_vec,
            },
            "validation": self.validation,
            "vec_available": self.vec_available,
            "chroma_available": self.chroma_available,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "main_elapsed_sec": round(self.main_elapsed_sec, 3),
            "vec_elapsed_sec": round(self.vec_elapsed_sec, 3),
            "throughput_ops": round(self.throughput_ops, 2),
            "vec_throughput_ops": round(self.vec_throughput_ops, 2),
            "errors": self.errors[:20],  # 截断，避免报告过大
        }


@dataclass
class MigrationContext:
    """运行时状态"""
    config: MigrationConfig
    report: MigrationReport
    encoder: EmbeddingEncoder
    target_conn: Optional[sqlite3.Connection] = None
    existing_main_keys: set[str] = field(default_factory=set)
    existing_vec_keys: set[str] = field(default_factory=set)
    failed_vec_keys: set[str] = field(default_factory=set)
    vec_available: bool = False
    backup_dir: Optional[Path] = None


# ── 源读取 ──


def _open_readonly_conn(db_path: str) -> sqlite3.Connection:
    """只读 URI 连接（机制保证 source 不被误写）"""
    path = Path(db_path).resolve()
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _get_source_columns(conn: sqlite3.Connection) -> set[str]:
    """获取 source memory_items 实际列名（兼容旧 schema 缺 TLM 扩展字段）"""
    rows = conn.execute(f"PRAGMA table_info({CONTENT_TABLE})").fetchall()
    return {row["name"] for row in rows}


def read_source_main_items(
    source_db: str, batch_size: int
) -> Iterator[list[dict]]:
    """分批读取 source memory_items（只读，含清洗）

    清洗规则：
    - 过滤空 key
    - metadata 非 JSON → 规整为 '{}'
    - 缺失字段填默认值（兼容旧 schema）
    """
    conn = _open_readonly_conn(source_db)
    try:
        available_cols = _get_source_columns(conn)
        # 动态构建 SELECT（只读实际存在的列）
        base_cols = ["key", "data", "metadata", "created_at", "updated_at", "hit_count"]
        ext_cols = ["access_count", "last_accessed", "type", "category"]
        select_cols = [c for c in base_cols if c in available_cols]
        ext_select = [c for c in ext_cols if c in available_cols]
        all_cols = select_cols + ext_select
        col_str = ", ".join(all_cols)

        total = conn.execute(f"SELECT COUNT(*) FROM {CONTENT_TABLE}").fetchone()[0]
        log(f"读取 source 主表: {total} 条, 列={all_cols}")

        offset = 0
        while offset < total:
            rows = conn.execute(
                f"SELECT {col_str} FROM {CONTENT_TABLE} "
                f"ORDER BY key LIMIT ? OFFSET ?",
                (batch_size, offset),
            ).fetchall()
            if not rows:
                break
            batch = []
            for row in rows:
                row = dict(row)
                key = row.get("key")
                if not key:  # 清洗：跳过空 key
                    continue
                # 清洗：metadata 非 JSON → '{}'
                meta_str = row.get("metadata") or "{}"
                try:
                    json.loads(meta_str)
                except (json.JSONDecodeError, TypeError):
                    meta_str = "{}"
                batch.append({
                    "key": key,
                    "data": row.get("data", ""),
                    "metadata": meta_str,
                    "created_at": float(row.get("created_at") or time.time()),
                    "updated_at": float(row.get("updated_at") or time.time()),
                    "hit_count": int(row.get("hit_count") or 0),
                    "access_count": int(row.get("access_count") or 0),
                    "last_accessed": row.get("last_accessed"),
                    "type": row.get("type"),
                    "category": row.get("category"),
                })
            if batch:
                yield batch
            offset += batch_size
    finally:
        conn.close()


def read_chroma_vectors_safe(
    source_path: str, collection_name: str
) -> tuple[dict[str, list[float]], bool]:
    """安全读取 ChromaDB 向量（Windows 不兼容时降级为空 dict + False）

    Returns:
        (key->embedding, ok)：ok=False 时调用方应全量重生成
    """
    path = Path(source_path)
    if not path.exists():
        log(f"source-chroma 不存在: {source_path}，将全量重生成向量", level="WARNING")
        return ({}, False)

    try:
        import chromadb
    except ImportError as e:
        log(f"chromadb 未安装, 无法读取旧向量: {e}", level="WARNING")
        return ({}, False)

    try:
        client = chromadb.PersistentClient(path=str(path))
        collection = client.get_collection(name=collection_name)
        result = collection.get(include=["embeddings", "metadatas"])
        vectors: dict[str, list[float]] = {}
        ids = result.get("ids", [])
        embeddings = result.get("embeddings", [])
        for i, item_id in enumerate(ids):
            if i < len(embeddings) and embeddings[i] is not None:
                vectors[str(item_id)] = list(embeddings[i])
        log(f"ChromaDB 读取成功: {len(vectors)} 条向量")
        return (vectors, True)
    except Exception as e:
        log(f"ChromaDB 读取失败（Windows 已知不兼容）: {type(e).__name__}: {e}", level="WARNING")
        log("降级为全量重生成向量", level="WARNING")
        return ({}, False)


# ── 目标操作 ──


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """加载 sqlite-vec 扩展（双路径 fallback，复用 HolographicAdapter 模式）"""
    try:
        import sqlite_vec
    except ImportError:
        log("sqlite_vec 未安装，向量层降级为不可用", level="WARNING")
        return False

    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return True
    except Exception as e_py:
        log(f"sqlite_vec.load 失败: {e_py}，尝试原生 load_extension", level="INFO")
    try:
        conn.enable_load_extension(True)
        conn.load_extension("sqlite_vec")
        return True
    except Exception as e_native:
        log(f"原生 load_extension 失败: {e_native}，向量层降级", level="WARNING")
        return False


def open_target_conn(db_path: str, load_vec: bool) -> sqlite3.Connection:
    """打开 target 连接，设置 busy_timeout，按需加载 sqlite-vec"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")  # [不易] 避免 SQLITE_BUSY 直接抛异常
    if load_vec:
        _load_sqlite_vec(conn)
    return conn


def init_target_schema(target_db: str) -> bool:
    """复用 HolographicAdapter 建表（实例化即触发 _init_db + _init_vec_table + _migrate_schema_if_needed）

    Why: sqlite-vec 双路径加载 + 降级逻辑已封装，重写违反 DRY。
    """
    try:
        from agent.memory.adapters.holographic_adapter import HolographicAdapter
    except ImportError as e:
        raise RuntimeError(f"无法 import HolographicAdapter: {e}")

    # [不易] enable_cache=False 避免 MultiLevelCache 副作用
    adapter = HolographicAdapter(db_path=target_db, enable_cache=False)
    vec_available = adapter._vec_available
    log(f"target 建表完成: vec_available={vec_available}")
    # 释放 adapter 的 thread-local 连接，转用独立连接做批量写入
    try:
        conn = getattr(adapter._conn_local, "conn", None)
        if conn is not None:
            conn.close()
    except Exception:
        pass
    return vec_available


def load_existing_keys(
    conn: sqlite3.Connection, table: str, key_col: str
) -> set[str]:
    """加载 target 已存在的 key 集合（resume 用，O(1) 查询）"""
    try:
        rows = conn.execute(f"SELECT {key_col} FROM {table}").fetchall()
        return {row[0] for row in rows if row[0]}
    except sqlite3.OperationalError:
        # 表不存在（vec 降级时 memories_vec 不存在）
        return set()


def write_main_batch(
    conn: sqlite3.Connection, batch: list[dict]
) -> tuple[int, int]:
    """写入主表 + FTS（同一事务）

    [不易] 主表 INSERT OR IGNORE（resume 幂等）+ FTS DELETE+INSERT（FTS5 不支持 INSERT OR IGNORE）
    失败时 with conn 自动 rollback。

    Returns:
        (written, skipped)
    """
    rows = [
        (
            r["key"], r["data"], r["metadata"],
            r["created_at"], r["updated_at"], r["hit_count"],
            r["access_count"], r["last_accessed"], r["type"], r["category"],
        )
        for r in batch
    ]

    with conn:  # [不易] 事务边界：主表 + FTS 原子提交
        cur = conn.executemany(
            f"INSERT OR IGNORE INTO {CONTENT_TABLE} "
            f"(key, data, metadata, created_at, updated_at, hit_count, "
            f"access_count, last_accessed, type, category) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        written = cur.rowcount if cur.rowcount >= 0 else len(rows)
        log(f"[write_main_batch] 主表 INSERT OR IGNORE: 输入 {len(rows)} 条, 实际写入 {written} 条")
        # FTS：先批量 DELETE 旧索引，再 INSERT（FTS5 虚拟表限制）
        conn.executemany(
            f"DELETE FROM {FTS_TABLE} WHERE key = ?",
            [(r[0],) for r in rows],
        )
        conn.executemany(
            f"INSERT INTO {FTS_TABLE} (key, data, metadata) VALUES (?, ?, ?)",
            [(r[0], r[1], r[2]) for r in rows],
        )
        log(f"[write_main_batch] FTS DELETE+INSERT 完成: {len(rows)} 条索引")
    skipped = len(rows) - written
    if skipped > 0:
        log(f"[write_main_batch] 跳过 {skipped} 条（已存在，resume 幂等）", level="DEBUG")
    return (written, skipped)


def write_vec_failed(
    conn: sqlite3.Connection, key: str, embedding: list, error: str
) -> None:
    """写入兜底表（embedding 用 JSON bytes 存 BLOB，复用 adapter 模式）"""
    now = time.time()
    blob = json.dumps(embedding).encode("utf-8")
    try:
        with conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {VEC_FAILED_TABLE} "
                f"(key, embedding, error, created_at, retries) VALUES (?, ?, ?, ?, ?)",
                (key, blob, error, now, 3),
            )
    except Exception as e:
        log(f"兜底表写入失败 key={key}: {e}", level="ERROR")


def write_vec_row(
    conn: sqlite3.Connection, key: str, embedding: list, vec_dim: int
) -> bool:
    """写入单条向量（RetryPolicy 重试 + DELETE+INSERT upsert）

    [不易] vec0 不支持 UPDATE；重试用项目统一 RetryPolicy（exponential）。
    失败写 memories_vec_failed 表。
    """
    if len(embedding) != vec_dim:
        log(f"[write_vec_row] 维度不匹配 key={key}: 期望 {vec_dim}, 实际 {len(embedding)} → 写兜底表", level="WARNING")
        write_vec_failed(conn, key, embedding, f"dim mismatch: {len(embedding)} != {vec_dim}")
        return False

    try:
        import sqlite_vec
    except ImportError:
        log(f"[write_vec_row] sqlite_vec 不可导入 key={key} → 写兜底表", level="WARNING")
        write_vec_failed(conn, key, embedding, "sqlite_vec not importable")
        return False

    from agent.error_handler import RetryPolicy
    policy = RetryPolicy(
        max_retries=3,
        initial_delay=0.1,
        backoff_factor=2.0,
        strategy="exponential",
    )
    blob = sqlite_vec.serialize_float32(embedding)
    last_error = None

    for attempt in range(3):
        try:
            with conn:
                conn.execute(f"DELETE FROM {VEC_TABLE} WHERE id = ?", (key,))
                conn.execute(
                    f"INSERT INTO {VEC_TABLE} (id, embedding) VALUES (?, ?)",
                    (key, blob),
                )
            if attempt > 0:
                log(f"[write_vec_row] 重试成功 key={key} (attempt={attempt+1}/3)")
            return True
        except Exception as e:
            last_error = e
            delay = policy.calculate_delay(attempt) if attempt < 2 else 0
            log(f"[write_vec_row] 写入失败 key={key} attempt={attempt+1}/3: {e} → 等待 {delay:.3f}s 后重试", level="WARNING")
            if attempt < 2:
                time.sleep(delay)

    log(f"[write_vec_row] 重试耗尽 key={key} → 写兜底表", level="ERROR")
    write_vec_failed(conn, key, embedding, str(last_error))
    return False


def _flush_vec_batch(
    ctx: MigrationContext, batch: list[tuple[str, list[float]]], vec_dim: int
) -> tuple[int, int]:
    """批量写向量（单线程，避免写竞争）"""
    success = failed = 0
    for key, emb in batch:
        if write_vec_row(ctx.target_conn, key, emb, vec_dim):
            ctx.existing_vec_keys.add(key)
            success += 1
        else:
            failed += 1
    return (success, failed)


def regenerate_vectors(
    ctx: MigrationContext, items: list[tuple[str, str]]
) -> tuple[int, int]:
    """并行重生成向量（ThreadPoolExecutor）+ 单线程批量写

    性能设计（2026-08 优化）：
    - encode_batch > 1 时：worker 内先攒满 encode_batch 再一次性编码（SentenceTransformer
      原生批量推理，CPU 上比逐条快 2-4 倍），避免逐条 encode 的固定开销（tokenize/后处理）。
    - workers > 1 时：多个 worker 并行编码；但注意 torch 推理线程会争抢 CPU 核心，
      bge-small 在 CPU 上通常 workers=1 + 大 encode_batch 最优（见性能分析报告）。
    - 模型在主线程加载一次，worker 共享 encode 推理（SentenceTransformer 只读，线程安全）。
    - [不易] 单 writer 避免 sqlite-vec 写竞争。
    """
    if not items:
        return (0, 0)

    success = failed = 0
    batch_buffer: list[tuple[str, list[float]]] = []
    write_batch_size = ctx.config.batch_size
    vec_dim = ctx.config.vec_dim
    workers = max(1, ctx.config.workers)
    encode_batch = max(1, ctx.config.encode_batch)

    log(f"[regenerate_vectors] 配置: workers={workers}, encode_batch={encode_batch}, "
        f"write_batch={write_batch_size}, 待处理 {len(items)} 条")

    def encode_batch_worker(chunk: list[tuple[str, str]]) -> list[tuple[str, list[float]]]:
        """worker 任务：对 chunk 做批量编码（encode_batch 分片）"""
        results: list[tuple[str, list[float]]] = []
        for i in range(0, len(chunk), encode_batch):
            sub = chunk[i:i + encode_batch]
            texts = [t for _, t in sub]
            embeddings = ctx.encoder.encode_batch(texts)
            for (key, _), emb in zip(sub, embeddings):
                results.append((key, emb))
        return results

    # 把 items 按 worker 数均分，每个 worker 处理一个子集（避免 as_completed 拆散批次）
    chunks = [items[i::workers] for i in range(workers) if items[i::workers]]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(encode_batch_worker, c): i for i, c in enumerate(chunks)}
        log(f"[regenerate_vectors] 提交 {len(chunks)} 个 worker 任务")
        for future in as_completed(futures):
            try:
                worker_results = future.result()
                batch_buffer.extend(worker_results)
                if len(batch_buffer) >= write_batch_size:
                    log(f"[regenerate_vectors] 攒满 {len(batch_buffer)} 条，开始批量写入")
                    s, f = _flush_vec_batch(ctx, batch_buffer, vec_dim)
                    success += s
                    failed += f
                    batch_buffer.clear()
                    log(f"[regenerate_vectors] 批次写入完成: 成功 {s}, 失败 {f}, 累计成功 {success}")
            except Exception as e:
                ctx.report.errors.append({"vector_regen": str(e)})
                failed += 1
                log(f"[regenerate_vectors] worker 异常: {e}", level="ERROR")

    # 写剩余
    if batch_buffer:
        s, f = _flush_vec_batch(ctx, batch_buffer, vec_dim)
        success += s
        failed += f

    log(f"[regenerate_vectors] 完成: 成功 {success}, 失败 {failed}")
    return (success, failed)


# ── 流程编排 ──


def precheck(config: MigrationConfig) -> list[str]:
    """预检查，返回错误列表（空=通过）"""
    errors: list[str] = []

    if not Path(config.source_db).exists():
        errors.append(f"source-db 不存在: {config.source_db}")

    if not Path(config.source_chroma).exists():
        log(f"source-chroma 不存在: {config.source_chroma}，将全量重生成向量", level="WARNING")

    target_dir = Path(config.target_db).parent
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        errors.append(f"target-db 目录不可创建: {e}")

    if config.resume and not Path(config.target_db).exists():
        errors.append(f"--resume 需要 target-db 已存在: {config.target_db}")

    # encoder 可加载性（非 dry-run 且非 no-encoder 时）
    if not config.dry_run and not config.no_encoder:
        try:
            enc = EmbeddingEncoder(config.model_name, config.vec_dim)
            if enc.mode == "pseudo":
                log("模型加载失败，将降级为伪 embedding", level="WARNING")
        except Exception as e:
            errors.append(f"encoder 初始化失败: {e}")

    return errors


def backup_sources(config: MigrationConfig) -> Path:
    """备份 source-db + source-chroma（source 只读，backup 仅作保险）"""
    backup_dir = Path(config.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    if Path(config.source_db).exists():
        dst = backup_dir / Path(config.source_db).name
        shutil.copy2(config.source_db, dst)
        log(f"备份 source-db → {dst}")

    if Path(config.source_chroma).exists():
        dst = backup_dir / "chroma"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(config.source_chroma, dst)
        log(f"备份 source-chroma → {dst}")

    log(f"备份完成: {backup_dir}")
    return backup_dir


def count_source_main(source_db: str) -> int:
    """统计 source 主表行数"""
    conn = _open_readonly_conn(source_db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {CONTENT_TABLE}").fetchone()[0]
    finally:
        conn.close()


def _extract_fts_keyword(data: str) -> str:
    """从 data 提取一个干净的 FTS5 查询词（避免特殊字符导致 MATCH 失败）

    提取优先级：
    1. 字母数字组合 ≥3 字符（覆盖 abc、user、550e8400 等常规标识符）
    2. 连续中文 ≥2 字（覆盖中文记忆内容）

    Why: 整条 data 作为 FTS5 phrase 查询时，[]()<> 等 FTS5 特殊字符会破坏 token 序列匹配。
    字母数字 ≥3 是 unicode61 tokenizer 稳定命中的最小长度（2 字符短词如 ab/12 在
    复杂上下文中 token 边界不稳定）。

    已知局限：
    - 纯版本号 v1.2.3：取到 'v1'（2 字符）虽能命中但不稳定，当前不特殊处理（低频）。
    - 纯日文/韩文假名：tokenizer 分词差异，可能不命中（低频）。
    """
    import re
    # 字母数字 ≥3 字符（unicode61 tokenizer 稳定命中阈值）
    m = re.search(r'[a-zA-Z0-9]{3,}', data)
    if m:
        return m.group()
    # 中文 ≥2 字
    m = re.search(r'[\u4e00-\u9fff]{2,}', data)
    if m:
        return m.group()
    return ""


def validate(ctx: MigrationContext) -> dict:
    """校验：行数 + 抽检 + FTS MATCH + KNN recall@1"""
    report: dict[str, Any] = {
        "row_count_ok": False,
        "sample_ok": False,
        "fts_ok": False,
        "knn_ok": None,  # None = N/A（vec 不可用）
    }
    conn = ctx.target_conn

    # 1. 行数对比
    try:
        src_count = count_source_main(ctx.config.source_db)
        tgt_count = conn.execute(
            f"SELECT COUNT(*) FROM {CONTENT_TABLE}"
        ).fetchone()[0]
        report["row_count_ok"] = (src_count == tgt_count)
        log(f"[validate] 行数对比: source={src_count}, target={tgt_count}, ok={report['row_count_ok']}")
        if not report["row_count_ok"]:
            log(f"[validate] 行数不一致: source={src_count}, target={tgt_count}", level="ERROR")
    except Exception as e:
        ctx.report.errors.append({"validate": f"row_count: {e}"})
        log(f"[validate] 行数对比异常: {e}", level="ERROR")
        return report

    # 2. 随机抽检 10 条字段完整性
    samples = conn.execute(
        f"SELECT key, data, metadata FROM {CONTENT_TABLE} ORDER BY RANDOM() LIMIT 10"
    ).fetchall()
    if samples:
        report["sample_ok"] = all(
            row["key"] and row["data"] is not None for row in samples
        )
        log(f"[validate] 字段抽检: 采样 {len(samples)} 条, ok={report['sample_ok']}")
    else:
        # 空数据，无样本可校验，视为通过
        report["sample_ok"] = True
        log("[validate] 字段抽检: 空数据，视为通过")

    # 3. FTS MATCH（10 条 content 反查）
    if samples:
        fts_hits = 0
        for row in samples:
            data = row["data"] or ""
            keyword = _extract_fts_keyword(data)
            if not keyword:
                continue
            safe = keyword.replace('"', '""')
            fts_query = f'"{safe}" OR {safe}*'
            try:
                hit = conn.execute(
                    f"SELECT 1 FROM {FTS_TABLE} f "
                    f"JOIN {CONTENT_TABLE} c USING(key) "
                    f"WHERE {FTS_TABLE} MATCH ? AND c.key = ? LIMIT 1",
                    (fts_query, row["key"]),
                ).fetchone()
                if hit:
                    fts_hits += 1
            except Exception as e:
                log(f"[validate] FTS MATCH 查询异常 key={row['key']}: {e}", level="WARNING")
        report["fts_ok"] = (fts_hits == len(samples))
        log(f"[validate] FTS MATCH: 命中 {fts_hits}/{len(samples)}, ok={report['fts_ok']}")
    else:
        # 空数据，无 FTS 可校验，视为通过
        report["fts_ok"] = True
        log("[validate] FTS MATCH: 空数据，视为通过")

    # 4. KNN recall@1（vec_available 时，10 条向量自查 top1==self）
    if ctx.vec_available:
        try:
            import sqlite_vec
            vec_samples = conn.execute(
                f"SELECT id, embedding FROM {VEC_TABLE} ORDER BY RANDOM() LIMIT 10"
            ).fetchall()
            if vec_samples:
                knn_hits = 0
                for vrow in vec_samples:
                    vid = vrow["id"]
                    emb_blob = vrow["embedding"]
                    emb_list = deserialize_vec(bytes(emb_blob))
                    query_blob = sqlite_vec.serialize_float32(emb_list)
                    krow = conn.execute(
                        f"SELECT id FROM {VEC_TABLE} "
                        f"WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
                        (query_blob,),
                    ).fetchone()
                    if krow and krow["id"] == vid:
                        knn_hits += 1
                report["knn_ok"] = (knn_hits == len(vec_samples))
                log(f"[validate] KNN recall@1: 命中 {knn_hits}/{len(vec_samples)}, ok={report['knn_ok']}")
            else:
                report["knn_ok"] = None
                log("[validate] KNN: 向量表为空，跳过 (knn_ok=None)")
        except Exception as e:
            log(f"[validate] KNN 校验异常: {e}", level="WARNING")
            report["knn_ok"] = None
    else:
        log("[validate] KNN: vec_available=False，跳过向量校验")

    log(f"[validate] 校验汇总: {report}")
    return report


def rollback(
    target_db_path: str, backup_dir: Optional[Path], is_resume: bool
) -> None:
    """回滚：分层策略

    [不易] 首次迁移失败 → 删 target-db；resume 模式失败 → 不删（保留续传数据）。
    rollback 自身 try/except 包裹，不掩盖原始错误。
    """
    log(f"[rollback] 触发回滚: target={target_db_path}, backup={backup_dir}, is_resume={is_resume}", level="WARNING")
    if is_resume:
        log("[rollback] resume 模式失败，保留 target-db 已迁移数据供下次续传", level="WARNING")
        return

    try:
        for suffix in ["", "-wal", "-shm"]:
            p = Path(f"{target_db_path}{suffix}")
            if p.exists():
                p.unlink()
                log(f"[rollback] 已删除 {p}")
        log(f"[rollback] 回滚完成，backup 保留在 {backup_dir}")
    except Exception as e:
        log(f"[rollback] 回滚本身失败（不掩盖原始错误）: {e}", level="ERROR")


def run_migration(config: MigrationConfig) -> MigrationReport:
    """主迁移流程"""
    report = MigrationReport()
    t_start = time.perf_counter()
    encoder = EmbeddingEncoder(config.model_name, config.vec_dim, force_pseudo=config.no_encoder)
    ctx = MigrationContext(config=config, report=report, encoder=encoder)

    backup_dir: Optional[Path] = None

    try:
        # 1. 预检查
        errors = precheck(config)
        if errors:
            for e in errors:
                log(e, level="ERROR")
            report.errors.append({"phase": "precheck", "errors": errors})
            report.status = "failed"
            return report

        # 2. dry-run 短路
        if config.dry_run:
            plan = {
                "status": "dry_run",
                "source_db": config.source_db,
                "source_chroma": config.source_chroma,
                "target_db": config.target_db,
                "batch_size": config.batch_size,
                "resume": config.resume,
                "model_name": config.model_name,
                "vec_dim": config.vec_dim,
                "workers": config.workers,
                "encode_batch": config.encode_batch,
                "source_main_count": (
                    count_source_main(config.source_db)
                    if Path(config.source_db).exists() else 0
                ),
            }
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            report.status = "dry_run"
            return report

        # 3. 备份
        backup_dir = backup_sources(config)
        ctx.backup_dir = backup_dir

        # 4. 建表（复用 HolographicAdapter）
        ctx.vec_available = init_target_schema(config.target_db)
        report.vec_available = ctx.vec_available

        # 5. 打开 target 连接 + 加载已存在 key（resume）
        ctx.target_conn = open_target_conn(config.target_db, ctx.vec_available)
        if config.resume:
            ctx.existing_main_keys = load_existing_keys(
                ctx.target_conn, CONTENT_TABLE, "key"
            )
            ctx.existing_vec_keys = load_existing_keys(
                ctx.target_conn, VEC_TABLE, "id"
            )
            ctx.failed_vec_keys = load_existing_keys(
                ctx.target_conn, VEC_FAILED_TABLE, "key"
            )
            log(f"resume 模式: 已存在主表 {len(ctx.existing_main_keys)} 条, "
                f"向量 {len(ctx.existing_vec_keys)} 条")

        # 6. 迁移主表 + FTS
        log("阶段 4/7: 迁移主表 + FTS")
        batch_idx = 0
        t_main_start = time.perf_counter()
        for batch in read_source_main_items(config.source_db, config.batch_size):
            batch_idx += 1
            report.total_main += len(batch)

            if config.resume:
                new_batch = [
                    r for r in batch if r["key"] not in ctx.existing_main_keys
                ]
                skipped = len(batch) - len(new_batch)
                report.skipped_main += skipped
                batch = new_batch

            if not batch:
                continue

            t_batch = time.perf_counter()
            try:
                written, skipped = write_main_batch(ctx.target_conn, batch)
                report.migrated_main += written
                report.skipped_main += skipped
                ctx.existing_main_keys.update(r["key"] for r in batch)
            except Exception as e:
                report.failed_main += len(batch)
                report.errors.append({"phase": "main", "batch": batch_idx, "error": str(e)})
                log(f"主表批次 {batch_idx} 失败: {e}", level="ERROR")
                raise  # 致命错误，触发回滚

            elapsed = time.perf_counter() - t_batch
            log(f"[batch {batch_idx}] 已迁移 {report.migrated_main} 条, "
                f"失败 {report.failed_main} 条, 耗时 {elapsed:.2f}s")

        report.main_elapsed_sec = time.perf_counter() - t_main_start
        log(f"主表迁移完成: 总 {report.total_main}, 成功 {report.migrated_main}, "
            f"跳过 {report.skipped_main}, 失败 {report.failed_main}, "
            f"耗时 {report.main_elapsed_sec:.2f}s")

        # 7. 迁移向量
        log("阶段 5/7: 迁移向量")
        if not ctx.vec_available:
            log("sqlite-vec 不可用，跳过向量迁移（仅写主表+FTS）", level="WARNING")
        else:
            chroma_vectors, chroma_ok = read_chroma_vectors_safe(
                config.source_chroma, config.collection_name
            )
            report.chroma_available = chroma_ok

            # 加载所有主表 key->content 用于对齐
            main_items_rows = ctx.target_conn.execute(
                f"SELECT key, data FROM {CONTENT_TABLE}"
            ).fetchall()
            main_items = {row["key"]: row["data"] for row in main_items_rows}
            report.total_vec = len(main_items)

            reuse_queue: list[tuple[str, list[float]]] = []
            regenerate_queue: list[tuple[str, str]] = []

            for key, content in main_items.items():
                if config.resume and key in ctx.existing_vec_keys:
                    continue  # 已迁移
                vec = chroma_vectors.get(key)
                if vec is not None and len(vec) == config.vec_dim:
                    reuse_queue.append((key, vec))
                else:
                    # 维度不匹配或无向量 → 重生成
                    regenerate_queue.append((key, content or ""))
            log(f"[migrate_vectors] 向量对齐决策: "
                f"总 {len(main_items)} 条, "
                f"复用候选 {len(reuse_queue)} 条 (维度 {config.vec_dim} 匹配), "
                f"重生成候选 {len(regenerate_queue)} 条 "
                f"(维度不匹配或无向量)")

            # 阶段 1：复用维度匹配的旧向量
            for key, vec in reuse_queue:
                if write_vec_row(ctx.target_conn, key, vec, config.vec_dim):
                    ctx.existing_vec_keys.add(key)
                    report.reused_vec += 1
                else:
                    report.failed_vec += 1
            log(f"复用旧向量 {report.reused_vec} 条，待重生成 {len(regenerate_queue)} 条")

            # 阶段 2：并行重生成
            if regenerate_queue:
                t_vec_start = time.perf_counter()
                regen_success, regen_failed = regenerate_vectors(ctx, regenerate_queue)
                report.vec_elapsed_sec = time.perf_counter() - t_vec_start
                if report.vec_elapsed_sec > 0:
                    report.vec_throughput_ops = regen_success / report.vec_elapsed_sec
                report.regenerated_vec = regen_success
                report.failed_vec += regen_failed
            log(f"向量迁移完成: 复用 {report.reused_vec}, 重生成 {report.regenerated_vec}, "
                f"失败 {report.failed_vec}, 耗时 {report.vec_elapsed_sec:.2f}s, "
                f"吞吐量 {report.vec_throughput_ops:.2f} vec/s")

        # 8. 校验
        log("阶段 6/7: 校验")
        report.validation = validate(ctx)
        validation_ok = all(
            v for v in report.validation.values() if v is not None
        )
        if not validation_ok:
            log(f"校验失败: {report.validation}", level="ERROR")
            # [不易] 先关闭连接再回滚（避免 Windows 文件锁）
            if ctx.target_conn is not None:
                try:
                    ctx.target_conn.close()
                    ctx.target_conn = None
                except Exception:
                    pass
            rollback(config.target_db, backup_dir, config.resume)
            report.status = "failed"
            report.elapsed_sec = time.perf_counter() - t_start
            return report

        # 9. 成功
        report.elapsed_sec = time.perf_counter() - t_start
        # [不易] 吞吐量只算主表迁移（不含向量重生成），符合任务语义
        if report.main_elapsed_sec > 0:
            report.throughput_ops = report.migrated_main / report.main_elapsed_sec
        report.status = "success"
        log(f"迁移成功: 总耗时 {report.elapsed_sec:.2f}s, "
            f"主表耗时 {report.main_elapsed_sec:.2f}s, "
            f"吞吐量 {report.throughput_ops:.2f} ops/s")
        return report

    except Exception as e:
        log(f"迁移异常: {type(e).__name__}: {e}", level="ERROR")
        report.errors.append({"fatal": str(e)})
        # [不易] 先关闭 target 连接再回滚（避免 Windows 文件锁 WinError 32）
        if ctx.target_conn is not None:
            try:
                ctx.target_conn.close()
                ctx.target_conn = None
            except Exception:
                pass
        rollback(config.target_db, backup_dir, config.resume)
        report.status = "failed"
        report.elapsed_sec = time.perf_counter() - t_start
        return report
    finally:
        if ctx.target_conn is not None:
            try:
                ctx.target_conn.close()
            except Exception:
                pass


# ── CLI ──


def build_config(args: argparse.Namespace) -> MigrationConfig:
    """从 CLI 参数构建配置"""
    backup_dir = args.backup_dir
    if backup_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = f"./data/memory/backup_{ts}"

    target_db = args.target_db
    if target_db is None:
        source_dir = Path(args.source_db).parent
        target_db = str(source_dir / "memory_tlm.db")

    return MigrationConfig(
        source_db=args.source_db,
        source_chroma=args.source_chroma,
        target_db=target_db,
        backup_dir=backup_dir,
        batch_size=args.batch_size,
        resume=args.resume,
        dry_run=args.dry_run,
        no_encoder=args.no_encoder,
        model_name=args.model or ENV_MODEL,
        vec_dim=args.vec_dim or ENV_VEC_DIM,
        collection_name=args.collection,
        workers=args.workers or DEFAULT_WORKERS,
        encode_batch=args.encode_batch or DEFAULT_ENCODE_BATCH,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TLM 数据迁移脚本：旧 SQLite + ChromaDB → 新 target-db（含向量重生成）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--source-db", default=DEFAULT_SOURCE_DB,
                        help=f"原 holographic.db 路径（默认 {DEFAULT_SOURCE_DB}）")
    parser.add_argument("--source-chroma", default=DEFAULT_SOURCE_CHROMA,
                        help=f"原 chromadb 目录路径（默认 {DEFAULT_SOURCE_CHROMA}）")
    parser.add_argument("--target-db", default=None,
                        help="目标 .db 路径（默认与 source-db 同目录，文件名 memory_tlm.db）")
    parser.add_argument("--backup-dir", default=None,
                        help="备份目录（默认 ./data/memory/backup_<timestamp>）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"批量大小（默认 {DEFAULT_BATCH_SIZE}）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续传模式（跳过 target-db 中已存在的 key）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印迁移计划，不实际写入")
    parser.add_argument("--no-encoder", action="store_true",
                        help="用伪 embedding（测试用，避免加载真实 bge 模型）")
    parser.add_argument("--model", default=None,
                        help=f"覆盖 embedding 模型（默认 {ENV_MODEL}）")
    parser.add_argument("--vec-dim", type=int, default=None,
                        help=f"覆盖向量维度（默认 {ENV_VEC_DIM}）")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION,
                        help=f"chroma collection 名（默认 {DEFAULT_COLLECTION}）")
    parser.add_argument("--workers", type=int, default=None,
                        help=f"向量重生成 ThreadPoolExecutor 并行度（默认 {DEFAULT_WORKERS}）")
    parser.add_argument("--encode-batch", type=int, default=None,
                        help=f"每次 encode 批量大小（默认 {DEFAULT_ENCODE_BATCH}，"
                             f">1 启用批量推理，CPU 上性能更优）")

    args = parser.parse_args()
    config = build_config(args)

    report = run_migration(config)

    # stdout 输出 JSON 报告
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

    # [不易] dry_run 是正常执行（打印计划），不算失败
    return 0 if report.status in ("success", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
