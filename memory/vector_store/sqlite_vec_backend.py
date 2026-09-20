"""SqliteVecBackend — 基于 sqlite-vec 的轻量级向量存储后端

TLM L3 层语义记忆的 sqlite-vec 实现。
相比 ChromaDB，sqlite-vec 无需 torch/onnxruntime 等重量级依赖，
仅依赖 sqlite-vec 扩展（~1MB），适合本地优先部署。

设计原则：
- 线程安全：写入操作加 threading.Lock 保护
- WAL 模式：支持并发读写
- 向量维度构造期固定，不可变
- API 与 ChromaDB collection 对齐，便于 VectorStore 无缝切换

依赖：
- sqlite-vec>=0.1.9 (vec0 虚拟表)
- sqlite3 (标准库)
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SqliteVecBackend:
    """sqlite-vec 向量存储后端

    使用 vec0 虚拟表存储向量，metadata 存储在普通表中。
    支持精确 KNN 查询（余弦距离）。
    """

    def __init__(
        self,
        db_path: str,
        collection_name: str = "agent_memory",
        dim: int = 384,
    ):
        """初始化 sqlite-vec 后端

        Args:
            db_path: SQLite 数据库文件路径
            collection_name: 集合名称（用作表名后缀）
            dim: 向量维度（构造期固定，不可变）
        """
        self.db_path = db_path
        self.collection_name = collection_name
        self.dim = dim
        self._lock = threading.Lock()

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()
        logger.info(
            "[SqliteVecBackend] 初始化完成: db=%s, dim=%d, table=%s",
            db_path, dim, self._vec_table,
        )

    @property
    def _vec_table(self) -> str:
        return f"vec_{self.collection_name}"

    @property
    def _meta_table(self) -> str:
        return f"meta_{self.collection_name}"

    def _get_conn(self) -> sqlite3.Connection:
        """获取已加载 sqlite-vec 扩展的连接（WAL 模式）"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            import sqlite_vec
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception as e:
            conn.close()
            raise RuntimeError(f"sqlite-vec 扩展加载失败: {e}") from e
        return conn

    def _init_db(self) -> None:
        """初始化表结构：vec0 虚拟表 + metadata 表"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._vec_table} "
                    f"USING vec0(id TEXT PRIMARY KEY, embedding float[{self.dim}])"
                )
                conn.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self._meta_table} (
                        id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        metadata TEXT DEFAULT '{{}}',
                        timestamp TEXT DEFAULT '',
                        created_at REAL DEFAULT 0
                    )"""
                )
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._meta_table}_created "
                    f"ON {self._meta_table}(created_at DESC)"
                )
                conn.commit()
            finally:
                conn.close()

    def add(
        self,
        item_id: str,
        content: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: str = "",
    ) -> bool:
        """添加单条向量

        Args:
            item_id: 记忆 ID
            content: 文本内容
            embedding: 向量（维度必须与构造期 dim 一致）
            metadata: 元数据
            timestamp: 时间戳

        Returns:
            True 表示成功
        """
        if len(embedding) != self.dim:
            raise ValueError(
                f"embedding 维度不匹配: expected={self.dim}, got={len(embedding)}"
            )

        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        created_at = datetime.now().timestamp()

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {self._vec_table} (id, embedding) VALUES (?, ?)",
                    (item_id, _encode_vec(embedding)),
                )
                conn.execute(
                    f"""INSERT OR REPLACE INTO {self._meta_table}
                    (id, content, metadata, timestamp, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (item_id, content, meta_str, timestamp, created_at),
                )
                conn.commit()
                return True
            except Exception as e:
                logger.error("[SqliteVecBackend] add 失败: id=%s, error=%s", item_id, e)
                return False
            finally:
                conn.close()

    def batch_add(
        self,
        items: List[Dict[str, Any]],
    ) -> int:
        """批量添加向量

        Args:
            items: 每项包含 id, content, embedding, metadata(可选), timestamp(可选)

        Returns:
            成功添加的数量
        """
        if not items:
            return 0

        success = 0
        with self._lock:
            conn = self._get_conn()
            try:
                for item in items:
                    item_id = item["id"]
                    content = item["content"]
                    embedding = item["embedding"]
                    if len(embedding) != self.dim:
                        logger.warning(
                            "[SqliteVecBackend] batch_add 维度跳过: id=%s, dim=%d",
                            item_id, len(embedding),
                        )
                        continue
                    metadata = item.get("metadata", {})
                    timestamp = item.get("timestamp", "")
                    meta_str = json.dumps(metadata, ensure_ascii=False)
                    created_at = datetime.now().timestamp()

                    conn.execute(
                        f"INSERT OR REPLACE INTO {self._vec_table} (id, embedding) VALUES (?, ?)",
                        (item_id, _encode_vec(embedding)),
                    )
                    conn.execute(
                        f"""INSERT OR REPLACE INTO {self._meta_table}
                        (id, content, metadata, timestamp, created_at)
                        VALUES (?, ?, ?, ?, ?)""",
                        (item_id, content, meta_str, timestamp, created_at),
                    )
                    success += 1
                conn.commit()
            except Exception as e:
                logger.error("[SqliteVecBackend] batch_add 失败: %s", e)
                conn.rollback()
            finally:
                conn.close()

        logger.info("[SqliteVecBackend] batch_add: %d/%d 成功", success, len(items))
        return success

    def search(
        self,
        query_vec: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """KNN 向量搜索

        Args:
            query_vec: 查询向量
            top_k: 返回结果数量

        Returns:
            结果列表，每项含 id, content, metadata, timestamp, distance
        """
        if len(query_vec) != self.dim:
            raise ValueError(
                f"query_vec 维度不匹配: expected={self.dim}, got={len(query_vec)}"
            )

        conn = self._get_conn()
        try:
            # 【2026-09-21 跨平台修复】KNN 约束只用 `k = ?`，不用 `ORDER BY … LIMIT ?`。
            #
            #   sqlite-vec 对 vec0 表的 KNN 查询要求满足以下**之一**：外层 LIMIT 直接
            #   作用于 vec0，或 vec0 的 WHERE 上带 `k = ?`。原实现写的是
            #   `ORDER BY distance LIMIT ?` 的子查询，它**在 Windows 上能过、在 CI 的
            #   Linux/Python 3.12.14 上必红**：
            #       [SqliteVecBackend] search 失败:
            #       A LIMIT or 'k = ?' constraint is required on vec0 knn queries.
            #   两个平台的 SQLite 查询计划器会把该子查询以不同方式拉平/下推，导致
            #   「LIMIT 属于 vec0」这一前提在 Linux 上不再成立（实测本机 0.1.9 /
            #   0.1.10a2 / 0.1.10a4 三种扩展版本在 Windows 下都放行，故与扩展版本无关，
            #   是平台/计划器差异）。
            #
            #   `k = ?` 是 sqlite-vec 文档给出的**规范 KNN 约束写法**，不依赖计划器是否
            #   下推 LIMIT，因此在两个平台上等价且稳定；代价为零（仍走 KNN 索引路径，
            #   返回行数与 top_k 一致）。写 `k = ?` 后 ORDER BY 不再必要 —— KNN 结果
            #   本就按 distance 升序返回。
            rows = conn.execute(
                f"""SELECT k.id, m.content, m.metadata, m.timestamp, k.distance
                FROM (
                    SELECT id, distance FROM {self._vec_table}
                    WHERE embedding MATCH ?
                      AND k = ?
                ) k
                JOIN {self._meta_table} m ON k.id = m.id""",
                (_encode_vec(query_vec), top_k),
            ).fetchall()

            results = []
            for row in rows:
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "metadata": json.loads(row[2]) if row[2] else {},
                    "timestamp": row[3],
                    "distance": row[4],
                })
            return results
        except Exception as e:
            # 【2026-09-21】这里**保持**既有"不向上抛"的契约（`VectorStore.search` 与
            #   Chroma 分支都是 `except → []`，上层按降级处置），但把"降级"这件事
            #   显式写进日志：返回空列表的语义是「查询失败」，**不是**「查无结果」。
            #   两者在上层不可区分，所以必须在这里出声 —— 否则一次平台性的 SQL 失败
            #   会表现为"记忆检索暂时没命中"，这正是本文件 2026-09-21 那次跨平台
            #   静默失效的成因（CI 报错 5 次、单测全绿）。
            logger.error(
                "[SqliteVecBackend] search 失败（**返回空列表，语义=查询失败，非查无结果**）: "
                "db=%s table=%s top_k=%s error=%s",
                self.db_path, self._vec_table, top_k, e,
            )
            return []
        finally:
            conn.close()

    def get_by_id(self, item_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 查找"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                f"SELECT id, content, metadata, timestamp FROM {self._meta_table} WHERE id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "content": row[1],
                "metadata": json.loads(row[2]) if row[2] else {},
                "timestamp": row[3],
            }
        finally:
            conn.close()

    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近添加的记忆"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT id, content, metadata, timestamp FROM {self._meta_table} "
                f"ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "content": row[1],
                    "metadata": json.loads(row[2]) if row[2] else {},
                    "timestamp": row[3],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def clear(self) -> int:
        """清空所有数据

        Returns:
            删除的数量
        """
        with self._lock:
            conn = self._get_conn()
            try:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {self._meta_table}"
                ).fetchone()[0]
                conn.execute(f"DELETE FROM {self._vec_table}")
                conn.execute(f"DELETE FROM {self._meta_table}")
                conn.commit()
                logger.info("[SqliteVecBackend] clear: 删除 %d 条", count)
                return count
            except Exception as e:
                logger.error("[SqliteVecBackend] clear 失败: %s", e)
                return 0
            finally:
                conn.close()

    def count(self) -> int:
        """获取记忆数量"""
        conn = self._get_conn()
        try:
            return conn.execute(
                f"SELECT COUNT(*) FROM {self._meta_table}"
            ).fetchone()[0]
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "backend": "sqlite_vec",
            "db_path": self.db_path,
            "collection": self.collection_name,
            "dim": self.dim,
            "total_entries": self.count(),
            "vec_table": self._vec_table,
            "meta_table": self._meta_table,
        }


def _encode_vec(vec: List[float]) -> bytes:
    """将 float 列表编码为 sqlite-vec 接受的 bytes 格式

    sqlite-vec 接受 IEEE 754 little-endian float32 数组。
    """
    import struct
    return struct.pack(f"{len(vec)}f", *vec)
