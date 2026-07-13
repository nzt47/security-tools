"""向量数据迁移脚本: 现有存储 → sqlite-vec

支持的源:
    1. JSON 文件 (VectorStore fallback 模式): {collection_name}.json
    2. ChromaDB (VectorStore 主模式): ./data/chroma/

目标:
    sqlite-vec 数据库 (vec0 虚拟表)

用法:
    # 自动检测源 (优先 ChromaDB, 回退 JSON)
    python scripts/migrate_to_sqlite_vec.py

    # 指定 JSON 源
    python scripts/migrate_to_sqlite_vec.py --source json --source-path ./data/memory/agent_memory.json

    # 指定 ChromaDB 源
    python scripts/migrate_to_sqlite_vec.py --source chromadb --source-path ./data/chroma

    # Dry-run (仅检查, 不写入)
    python scripts/migrate_to_sqlite_vec.py --dry-run

    # 自定义维度 (无 sentence_transformers 时)
    python scripts/migrate_to_sqlite_vec.py --dim 384 --no-encoder

输出:
    - stdout: JSON 格式迁移报告
    - stderr: 人类可读进度日志
    - 退出码: 0=成功, 1=失败

约束:
    - 不修改源数据
    - 目标数据库已存在时自动备份
    - 无 sentence_transformers 时用确定性伪 embedding (仅结构测试, 不可用于生产 KNN)
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── 常量 ──

DEFAULT_SOURCE_JSON = "./data/memory/agent_memory.json"
DEFAULT_SOURCE_CHROMA = "./data/chroma"
DEFAULT_TARGET_DB = "./data/memory/memory_vec.db"
DEFAULT_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 输出维度
DEFAULT_COLLECTION = "agent_memory"
DEFAULT_BATCH_SIZE = 100

# ── 工具函数 ──


def log(msg: str, *, level: str = "INFO"):
    """输出到 stderr 的日志"""
    print(f"[{level}] {msg}", file=sys.stderr, flush=True)


def serialize_vec(v: list[float]) -> bytes:
    """float list → little-endian float32 blob (sqlite-vec 期望格式)"""
    return struct.pack(f"<{len(v)}f", *v)


def deterministic_pseudo_embedding(text: str, dim: int) -> list[float]:
    """基于 hash 的确定性伪 embedding

    无 sentence_transformers 时使用。仅用于结构测试，不可用于生产 KNN。
    同一 text 总是生成同一向量，保证迁移可重试。
    """
    result = [0.0] * dim
    if not text:
        return result
    # 分词 + hash 投影到 dim 维
    tokens = text.lower().split()
    for token in tokens:
        h = hashlib.md5(token.encode("utf-8")).digest()
        for i in range(dim):
            # 每 4 字节映射到一个维度
            byte_idx = (i * 4) % len(h)
            val = struct.unpack("<I", h[byte_idx:byte_idx + 4])[0]
            result[i] += (val / 0xFFFFFFFF - 0.5) * 0.1
    # L2 归一化
    norm = sum(x * x for x in result) ** 0.5
    if norm > 0:
        result = [x / norm for x in result]
    return result


# ── Embedding 编码器 ──


class EmbeddingEncoder:
    """embedding 编码器: 优先 sentence_transformers, 回退伪 embedding"""

    def __init__(self, model_name: str, dim: int, force_pseudo: bool = False):
        self.model_name = model_name
        self.dim = dim
        self._model = None
        self._mode = "pseudo"  # "sentence_transformers" | "pseudo"

        if force_pseudo:
            log("强制使用伪 embedding (--no-encoder)")
            return

        try:
            from sentence_transformers import SentenceTransformer
            log(f"加载 sentence_transformers 模型: {model_name} ...")
            t0 = time.perf_counter()
            self._model = SentenceTransformer(model_name)
            self.dim = self._model.get_sentence_embedding_dimension()
            self._mode = "sentence_transformers"
            log(f"模型加载成功, 实际维度={self.dim}, 耗时={(time.perf_counter()-t0)*1000:.0f}ms")
        except ImportError:
            log("sentence_transformers 未安装, 使用伪 embedding", level="WARNING")
        except Exception as e:
            log(f"sentence_transformers 加载失败: {type(e).__name__}: {e}", level="WARNING")
            log("回退到伪 embedding", level="WARNING")

    @property
    def mode(self) -> str:
        return self._mode

    def encode(self, text: str) -> list[float]:
        if self._model is not None:
            vec = self._model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        return deterministic_pseudo_embedding(text, self.dim)


# ── 源读取器 ──


def read_json_source(source_path: str) -> list[dict]:
    """读取 JSON 源 (VectorStore fallback 格式)"""
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"JSON 源不存在: {source_path}")

    log(f"读取 JSON 源: {source_path}")
    t0 = time.perf_counter()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"JSON 源格式错误: 期望 list, 实际 {type(data).__name__}")

    elapsed = (time.perf_counter() - t0) * 1000
    log(f"读取完成: {len(data)} 条, 耗时={elapsed:.0f}ms")
    return data


def read_chromadb_source(source_path: str, collection_name: str) -> list[dict]:
    """读取 ChromaDB 源"""
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"ChromaDB 源不存在: {source_path}")

    log(f"读取 ChromaDB 源: {source_path}, collection={collection_name}")
    t0 = time.perf_counter()

    try:
        import chromadb
    except ImportError as e:
        raise ImportError(f"chromadb 未安装, 无法读取 ChromaDB 源: {e}")

    client = chromadb.PersistentClient(path=str(path))
    try:
        collection = client.get_collection(name=collection_name)
    except Exception as e:
        raise RuntimeError(f"获取 collection '{collection_name}' 失败: {e}")

    # 全量读取
    result = collection.get(include=["documents", "metadatas", "embeddings"])
    elapsed = (time.perf_counter() - t0) * 1000

    items = []
    ids = result.get("ids", [])
    documents = result.get("documents", [])
    metadatas = result.get("metadatas", [])
    embeddings = result.get("embeddings", [])

    for i, item_id in enumerate(ids):
        item = {
            "id": item_id,
            "content": documents[i] if i < len(documents) else "",
            "metadata": metadatas[i] if i < len(metadatas) else {},
            "timestamp": (metadatas[i] if i < len(metadatas) else {}).get(
                "created_at", ""
            ),
        }
        if i < len(embeddings) and embeddings[i] is not None:
            item["_embedding"] = list(embeddings[i])
        items.append(item)

    log(f"读取完成: {len(items)} 条, 耗时={elapsed:.0f}ms")
    return items


def auto_detect_source() -> tuple[str, str]:
    """自动检测源类型, 返回 (source_type, source_path)"""
    if Path(DEFAULT_SOURCE_CHROMA).exists():
        return "chromadb", DEFAULT_SOURCE_CHROMA
    if Path(DEFAULT_SOURCE_JSON).exists():
        return "json", DEFAULT_SOURCE_JSON
    raise FileNotFoundError(
        f"未找到向量数据源。检查以下路径:\n"
        f"  ChromaDB: {DEFAULT_SOURCE_CHROMA}\n"
        f"  JSON:     {DEFAULT_SOURCE_JSON}"
    )


# ── 目标初始化 ──


def init_target_db(target_path: str, dim: int, collection_name: str) -> sqlite3.Connection:
    """初始化 sqlite-vec 目标数据库"""
    path = Path(target_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 备份已存在的目标数据库
    if path.exists():
        backup_path = path.with_suffix(
            f".backup.{int(time.time())}.db"
        )
        log(f"目标数据库已存在, 备份到: {backup_path}")
        shutil.copy2(path, backup_path)

    conn = sqlite3.connect(str(path))

    # 加载 sqlite-vec 扩展 (需要先启用 load_extension)
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        log("sqlite-vec 扩展加载成功")
    except ImportError:
        raise ImportError(
            "sqlite-vec 未安装。请运行: pip install sqlite-vec"
        )
    except Exception as e:
        raise RuntimeError(f"sqlite-vec 扩展加载失败: {e}")

    # 创建 vec0 虚拟表
    table_name = f"vec_{collection_name}"
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.execute(
        f"CREATE VIRTUAL TABLE {table_name} USING vec0("
        f"  id TEXT PRIMARY KEY, "
        f"  content TEXT, "
        f"  metadata TEXT, "
        f"  timestamp TEXT, "
        f"  embedding FLOAT[{dim}]"
        f")"
    )

    # 创建元数据表 (存储迁移信息)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS migration_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()

    log(f"目标数据库初始化完成: {target_path}, dim={dim}, table={table_name}")
    return conn


# ── 迁移主逻辑 ──


def migrate(
    items: list[dict],
    conn: sqlite3.Connection,
    encoder: EmbeddingEncoder,
    collection_name: str,
    batch_size: int,
    dry_run: bool,
) -> dict:
    """执行迁移"""
    table_name = f"vec_{collection_name}"
    total = len(items)
    migrated = 0
    failed = 0
    errors: list[dict] = []
    skipped_no_content = 0
    reused_embedding = 0

    log(f"开始迁移: {total} 条, batch_size={batch_size}, dry_run={dry_run}")

    t0 = time.perf_counter()
    batch: list[tuple] = []

    for i, item in enumerate(items):
        item_id = str(item.get("id", f"mem_{i:06d}"))
        content = item.get("content", "")
        metadata = json.dumps(item.get("metadata", {}), ensure_ascii=False)
        timestamp = item.get("timestamp", "")

        if not content:
            skipped_no_content += 1
            continue

        # 优先复用源数据的 embedding (ChromaDB 模式)
        embedding = item.get("_embedding")
        if embedding is not None:
            reused_embedding += 1
        else:
            try:
                embedding = encoder.encode(content)
            except Exception as e:
                failed += 1
                errors.append({
                    "id": item_id,
                    "error": f"encode failed: {type(e).__name__}: {e}",
                })
                continue

        try:
            embedding_blob = serialize_vec(embedding)
        except Exception as e:
            failed += 1
            errors.append({
                "id": item_id,
                "error": f"serialize failed: {type(e).__name__}: {e}",
            })
            continue

        batch.append((item_id, content, metadata, timestamp, embedding_blob))

        if len(batch) >= batch_size:
            if not dry_run:
                conn.executemany(
                    f"INSERT INTO {table_name} "
                    f"(id, content, metadata, timestamp, embedding) "
                    f"VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                conn.commit()
            migrated += len(batch)
            batch = []

            if migrated % (batch_size * 10) == 0:
                elapsed = time.perf_counter() - t0
                rate = migrated / elapsed if elapsed > 0 else 0
                log(
                    f"进度: {migrated}/{total} "
                    f"({migrated/total*100:.1f}%), "
                    f"rate={rate:.1f}条/s"
                )

    # 插入剩余批次
    if batch:
        if not dry_run:
            conn.executemany(
                f"INSERT INTO {table_name} "
                f"(id, content, metadata, timestamp, embedding) "
                f"VALUES (?, ?, ?, ?, ?)",
                batch,
            )
            conn.commit()
        migrated += len(batch)

    elapsed = (time.perf_counter() - t0) * 1000

    # 写入迁移元信息
    if not dry_run:
        conn.execute(
            "INSERT OR REPLACE INTO migration_meta (key, value) VALUES (?, ?)",
            ("migrated_at", datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO migration_meta (key, value) VALUES (?, ?)",
            ("encoder_mode", encoder.mode),
        )
        conn.execute(
            "INSERT OR REPLACE INTO migration_meta (key, value) VALUES (?, ?)",
            ("dim", str(encoder.dim)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO migration_meta (key, value) VALUES (?, ?)",
            ("source_count", str(total)),
        )
        conn.commit()

    report = {
        "total": total,
        "migrated": migrated,
        "failed": failed,
        "skipped_no_content": skipped_no_content,
        "reused_embedding": reused_embedding,
        "encoder_mode": encoder.mode,
        "dim": encoder.dim,
        "elapsed_ms": round(elapsed, 2),
        "dry_run": dry_run,
        "errors": errors[:10],  # 仅保留前 10 个错误
        "errors_truncated": max(0, len(errors) - 10),
    }

    log(
        f"迁移完成: migrated={migrated}, failed={failed}, "
        f"skipped={skipped_no_content}, elapsed={elapsed:.0f}ms"
    )
    return report


# ── 验证 ──


def verify(
    conn: sqlite3.Connection,
    encoder: EmbeddingEncoder,
    collection_name: str,
    sample_size: int = 5,
) -> dict:
    """验证迁移结果: KNN recall 测试"""
    table_name = f"vec_{collection_name}"
    log(f"验证迁移结果 (sample_size={sample_size})...")

    # 统计总数
    count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    log(f"目标表总数: {count}")

    if count == 0:
        return {"count": 0, "recall_at_1": 0.0, "samples": []}

    # 随机抽样验证
    samples = conn.execute(
        f"SELECT id, content FROM {table_name} ORDER BY RANDOM() LIMIT ?",
        (sample_size,),
    ).fetchall()

    recall_hits = 0
    sample_results = []

    for item_id, content in samples:
        try:
            query_vec = encoder.encode(content)
            query_blob = serialize_vec(query_vec)

            cur = conn.execute(
                f"SELECT id, distance FROM {table_name} "
                f"WHERE embedding MATCH ? "
                f"ORDER BY distance LIMIT 1",
                (query_blob,),
            )
            row = cur.fetchone()

            if row and row[0] == item_id:
                recall_hits += 1
                sample_results.append({
                    "id": item_id,
                    "recall": True,
                    "distance": row[1],
                })
            else:
                sample_results.append({
                    "id": item_id,
                    "recall": False,
                    "expected": item_id,
                    "actual": row[0] if row else None,
                    "distance": row[1] if row else None,
                })
        except Exception as e:
            sample_results.append({
                "id": item_id,
                "recall": False,
                "error": f"{type(e).__name__}: {e}",
            })

    recall_at_1 = recall_hits / len(samples) if samples else 0.0
    log(f"recall@1 = {recall_at_1:.2%} ({recall_hits}/{len(samples)})")

    return {
        "count": count,
        "recall_at_1": round(recall_at_1, 4),
        "samples": sample_results,
    }


# ── 主入口 ──


def main() -> int:
    parser = argparse.ArgumentParser(
        description="向量数据迁移脚本: 现有存储 → sqlite-vec"
    )
    parser.add_argument(
        "--source",
        choices=["auto", "json", "chromadb"],
        default="auto",
        help="源类型 (默认: auto 自动检测)",
    )
    parser.add_argument(
        "--source-path",
        default=None,
        help="源路径 (auto 模式下忽略)",
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET_DB,
        help=f"目标数据库路径 (默认: {DEFAULT_TARGET_DB})",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"collection 名称 (默认: {DEFAULT_COLLECTION})",
    )
    parser.add_argument(
        "--dim",
        type=int,
        default=DEFAULT_DIM,
        help=f"embedding 维度 (默认: {DEFAULT_DIM}, 有 encoder 时自动覆盖)",
    )
    parser.add_argument(
        "--model",
        default="paraphrase-multilingual-MiniLM-L12-v2",
        help="sentence_transformers 模型名 (默认: paraphrase-multilingual-MiniLM-L12-v2)",
    )
    parser.add_argument(
        "--no-encoder",
        action="store_true",
        help="强制使用伪 embedding (不加载 sentence_transformers)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"批量插入大小 (默认: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅检查, 不写入目标数据库",
    )
    parser.add_argument(
        "--verify-samples",
        type=int,
        default=5,
        help="验证阶段的抽样数量 (默认: 5)",
    )

    args = parser.parse_args()

    log("=" * 60)
    log("向量数据迁移脚本: 现有存储 → sqlite-vec")
    log("=" * 60)

    # 1. 确定源
    if args.source == "auto":
        source_type, source_path = auto_detect_source()
        log(f"自动检测源: type={source_type}, path={source_path}")
    else:
        source_type = args.source
        source_path = args.source_path or (
            DEFAULT_SOURCE_CHROMA if source_type == "chromadb" else DEFAULT_SOURCE_JSON
        )

    # 2. 读取源数据
    try:
        if source_type == "json":
            items = read_json_source(source_path)
        else:
            items = read_chromadb_source(source_path, args.collection)
    except Exception as e:
        log(f"读取源失败: {type(e).__name__}: {e}", level="ERROR")
        return 1

    if not items:
        log("源数据为空, 无需迁移", level="WARNING")
        print(json.dumps({
            "status": "empty_source",
            "migrated": 0,
            "message": "源数据为空",
        }, ensure_ascii=False, indent=2))
        return 0

    # 3. 初始化 encoder
    encoder = EmbeddingEncoder(
        model_name=args.model,
        dim=args.dim,
        force_pseudo=args.no_encoder,
    )

    # 4. Dry-run 模式: 仅检查, 不初始化目标数据库
    if args.dry_run:
        log("Dry-run 模式: 仅检查, 不写入", level="WARNING")
        report = {
            "status": "dry_run",
            "source": {"type": source_type, "path": source_path, "count": len(items)},
            "encoder": {"mode": encoder.mode, "dim": encoder.dim},
            "target": args.target,
            "would_migrate": len(items),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # 5. 初始化目标数据库
    try:
        conn = init_target_db(args.target, encoder.dim, args.collection)
    except Exception as e:
        log(f"目标数据库初始化失败: {type(e).__name__}: {e}", level="ERROR")
        return 1

    # 6. 执行迁移
    try:
        migration_report = migrate(
            items=items,
            conn=conn,
            encoder=encoder,
            collection_name=args.collection,
            batch_size=args.batch_size,
            dry_run=False,
        )
    except Exception as e:
        log(f"迁移失败: {type(e).__name__}: {e}", level="ERROR")
        conn.close()
        return 1

    # 7. 验证
    verification_report = verify(
        conn=conn,
        encoder=encoder,
        collection_name=args.collection,
        sample_size=args.verify_samples,
    )

    conn.close()

    # 8. 输出最终报告
    final_report = {
        "status": "success" if migration_report["failed"] == 0 else "partial",
        "source": {"type": source_type, "path": source_path},
        "target": {
            "path": args.target,
            "collection": args.collection,
            "dim": encoder.dim,
        },
        "encoder": {"mode": encoder.mode, "model": args.model},
        "migration": migration_report,
        "verification": verification_report,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    print(json.dumps(final_report, ensure_ascii=False, indent=2))

    log("=" * 60)
    log(
        f"迁移{'成功' if migration_report['failed'] == 0 else '部分成功'}: "
        f"migrated={migration_report['migrated']}, "
        f"failed={migration_report['failed']}, "
        f"recall@1={verification_report['recall_at_1']:.2%}"
    )
    log("=" * 60)

    return 0 if migration_report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
