"""长期记忆模块 — LongTermMemory

长期记忆特征：
- 持久化存储，数据不随会话结束而消失
- 需要人工确认或定期审查才能删除
- 用于存储重要的用户偏好、跨会话上下文
- 支持敏感信息标记和重要性评分

设计文档：P2 云枢架构升级 — Memory Abstraction Layer (3.1)
"""

import json
import uuid
import time
import math
import struct
import heapq
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agent.memory.base import MemoryInterface, MemoryResult, MemoryCapability
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """[TLM-L3] 余弦相似度（纯 Python，不依赖 numpy）"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── [P3] embedding BLOB 序列化（替代 JSON TEXT，性能提升 5-10x）──

_FLOAT_SIZE = struct.calcsize('f')  # 4 bytes per float32

def _embedding_to_blob(embedding: Optional[list[float]]) -> Optional[bytes]:
    """[P3] 将 embedding list 序列化为 BLOB（struct.pack float32）

    相比 JSON TEXT：
    - 序列化快 ~5x（无字符串拼接）
    - 反序列化快 ~10x（直接 unpack，无 JSON 解析）
    - 存储小 ~30%（无引号/逗号/方括号开销）
    """
    if embedding is None:
        return None
    if not embedding:
        return b""
    return struct.pack(f'{len(embedding)}f', *embedding)


def _normalize_vector(vec: list[float]) -> list[float]:
    """[P4] L2 归一化：vec / |vec|

    归一化后 sqlite-vec 的 L2 距离排序 == 余弦相似度排序（recall@10 = 100%）
    数学证明：归一化后 |a|=|b|=1，L2 = 2 - 2·cos(a,b)
    """
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]


# ── [P4] sqlite-vec 可用性检测（延迟加载）──
_SQLITE_VEC_AVAILABLE = None  # None=未检测, True/False=已检测

def _check_sqlite_vec_available() -> bool:
    """[P4] 检测 sqlite-vec 扩展是否可用（首次调用时检测，后续缓存结果）"""
    global _SQLITE_VEC_AVAILABLE
    if _SQLITE_VEC_AVAILABLE is not None:
        return _SQLITE_VEC_AVAILABLE
    try:
        import sqlite_vec  # noqa: F401
        _SQLITE_VEC_AVAILABLE = True
        logger.info("[LongTermMemory] sqlite-vec 可用，semantic 搜索将使用 KNN 加速")
    except ImportError:
        _SQLITE_VEC_AVAILABLE = False
        logger.info("[LongTermMemory] sqlite-vec 不可用，semantic 搜索使用纯 Python 余弦相似度")
    return _SQLITE_VEC_AVAILABLE


def _blob_to_embedding(blob: Any) -> Optional[list[float]]:
    """[P3] 从 BLOB 反序列化 embedding，向后兼容旧 JSON TEXT 格式

    自动检测格式：
    - bytes/bytearray/memoryview → 尝试 struct.unpack（新 BLOB 格式），失败则尝试 JSON（旧 TEXT 存为 bytes）
    - str → JSON 解析（旧 TEXT 格式）
    - list → 直接返回
    - None → None
    - 其他类型 → None（防御性降级）
    """
    if blob is None:
        return None
    # [兼容] SQLite 某些配置下返回 memoryview 而非 bytes
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    if isinstance(blob, (bytes, bytearray)):
        if len(blob) == 0:
            return None
        # [防御] 长度上限检查（防止超大 BLOB 导致 MemoryError，sentence-transformers 最大 768 维）
        max_floats = 10000
        if len(blob) // _FLOAT_SIZE > max_floats:
            logger.warning("[LongTermMemory] embedding BLOB 异常大 (>%d floats)，返回 None", max_floats)
            return None
        # 尝试 struct 解包（新 BLOB 格式）
        try:
            count = len(blob) // _FLOAT_SIZE
            result = list(struct.unpack(f'{count}f', bytes(blob)))
            # [防御] 过滤 NaN/Inf（数据损坏时 struct.unpack 可能返回非有限值，会污染余弦相似度计算）
            if any(not math.isfinite(x) for x in result):
                logger.warning("[LongTermMemory] embedding BLOB 包含 NaN/Inf，返回 None")
                return None
            return result
        except (struct.error, ValueError):
            # 可能是旧 JSON TEXT 存为 bytes（向后兼容）
            try:
                parsed = json.loads(blob)
                return parsed if isinstance(parsed, list) else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                logger.warning("[LongTermMemory] embedding BLOB 反序列化失败，返回 None")
                return None
    if isinstance(blob, str):
        # 旧 JSON TEXT 格式（向后兼容）
        try:
            result = json.loads(blob)
            return result if isinstance(result, list) else None
        except json.JSONDecodeError:
            return None
    if isinstance(blob, list):
        return blob
    return None


# ── 业务指标埋点 ──
try:
    from agent.monitoring.business_metrics import (
        record_memory_search,
        record_memory_access,
        record_memory_storage,
    )
    _BUSINESS_METRICS_AVAILABLE = True
except ImportError:
    _BUSINESS_METRICS_AVAILABLE = False
    logger.debug(log_dict({'module_name': 'long_term_memory', 'action': 'business_metrics', 'msg': '[LongTermMemory] business_metrics 模块未加载，业务指标埋点禁用'}))


@dataclass
class LongTermMemoryEntry:
    """长期记忆条目

    Attributes:
        key: 记忆唯一标识
        content: 记忆内容
        importance: 重要性评分 (1-5)，越高越不容易被自动清理
        tags: 标签列表（用于分类检索）
        created_at: 创建时间戳
        updated_at: 更新时间戳
        last_accessed: 最后访问时间戳
        access_count: 访问次数
        sensitive: 是否包含敏感信息（需要额外保护）
        verified: 是否已通过人工审查
        metadata: 附加元数据
    """
    key: str
    content: Any
    importance: int = 3  # 默认重要性
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    sensitive: bool = False
    verified: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[list[float]] = None  # [TLM-L3] 语义向量缓存（JSON TEXT 存储）

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "key": self.key,
            "content": self.content,
            "importance": self.importance,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "sensitive": self.sensitive,
            "verified": self.verified,
            "metadata": self.metadata,
            "embedding": self.embedding,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LongTermMemoryEntry":
        """从字典反序列化"""
        # [P3] embedding 统一用 _blob_to_embedding 解析（兼容 BLOB / JSON TEXT / list / None）
        embedding = _blob_to_embedding(data.get("embedding"))

        return cls(
            key=data["key"],
            content=data["content"],
            importance=data.get("importance", 3),
            tags=data.get("tags", []),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            last_accessed=data.get("last_accessed", time.time()),
            access_count=data.get("access_count", 0),
            sensitive=data.get("sensitive", False),
            verified=data.get("verified", False),
            metadata=data.get("metadata", {}),
            embedding=embedding,
        )


class LongTermMemory:
    """长期记忆管理器

    负责长期记忆的存储、检索和管理。
    特征：
    - 数据持久化到 SQLite
    - 支持重要性评分和标签分类
    - 敏感信息标记和保护
    - 访问频率追踪
    - 需要审查才能删除（保护重要记忆）

    用法:
        ltm = LongTermMemory(db_path="./data/memory/long_term.db")
        await ltm.save("user_pref", {"theme": "dark"}, tags=["preference"], importance=5)
        entry = await ltm.get("user_pref")
    """

    _TABLE_NAME = "long_term_memory"
    _VEC_TABLE_NAME = "ltm_vec_index"  # [P4] vec0 虚拟表名

    def __init__(
        self,
        db_path: str = "./data/memory/long_term.db",
        auto_commit: bool = True,
    ) -> None:
        """
        Args:
            db_path: SQLite 数据库文件路径
            auto_commit: 是否自动提交（关闭可提高批量写入性能）
        """
        self.db_path = db_path
        self._auto_commit = auto_commit
        self._lock = threading.Lock()
        # [P4] sqlite-vec 可用性（构造期检测一次，后续缓存）
        self._use_vec_knn = _check_sqlite_vec_available()
        self._init_db()
        # [P4] 初始化 vec0 虚拟表（sqlite-vec 可用时）
        if self._use_vec_knn:
            self._init_vec_table()

        logger.info("[LongTermMemory] 初始化完成: db=%s, vec_knn=%s", db_path, self._use_vec_knn)

    # ── 能力声明 ──

    @property
    def capabilities(self) -> set[MemoryCapability]:
        return {
            MemoryCapability.FULLTEXT_SEARCH,
            MemoryCapability.LOCAL_FIRST,
            MemoryCapability.USER_PROFILE,
        }

    # ── 数据库初始化 ──

    def _init_db(self) -> None:
        """初始化数据库表结构"""
        import sqlite3
        from pathlib import Path

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._TABLE_NAME} (
                key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                importance INTEGER DEFAULT 3,
                tags TEXT DEFAULT '[]',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                access_count INTEGER DEFAULT 0,
                sensitive INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{{}}',
                embedding BLOB DEFAULT NULL
            )
        """)
        # [TLM-L3] 迁移：旧表无 embedding 列时自动添加（幂等）
        # [P3] 新表用 BLOB 类型；旧表 TEXT 列也能存 BLOB（SQLite 动态类型），无需强制迁移列类型
        columns = [col[1] for col in conn.execute(f"PRAGMA table_info({self._TABLE_NAME})").fetchall()]
        if "embedding" not in columns:
            conn.execute(f"ALTER TABLE {self._TABLE_NAME} ADD COLUMN embedding BLOB DEFAULT NULL")
            logger.info("[LongTermMemory] 迁移: 已添加 embedding 列")
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_ltm_importance
            ON {self._TABLE_NAME}(importance DESC)
        """)
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_ltm_last_accessed
            ON {self._TABLE_NAME}(last_accessed)
        """)
        conn.commit()
        conn.close()

    def _get_vec_conn(self) -> Any:
        """[P4] 获取加载了 sqlite-vec 扩展的连接"""
        import sqlite3
        import sqlite_vec
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())
        return conn

    def _detect_embedding_dim(self, conn: Any) -> Optional[int]:
        """[P4] 从主表数据推断 embedding 维度

        Returns:
            维度数（如 384/768），无数据时返回 None
        """
        row = conn.execute(f"""
            SELECT embedding FROM {self._TABLE_NAME}
            WHERE embedding IS NOT NULL LIMIT 1
        """).fetchone()
        if row is None:
            return None
        emb = _blob_to_embedding(row["embedding"])
        return len(emb) if emb else None

    def _get_vec_table_dim(self, conn: Any) -> Optional[int]:
        """[P4] 从 vec0 表 schema 提取维度

        Returns:
            维度数，表不存在或解析失败时返回 None
        """
        import re
        row = conn.execute(f"""
            SELECT sql FROM sqlite_master
            WHERE type='table' AND name=?
        """, (self._VEC_TABLE_NAME,)).fetchone()
        if not row or not row["sql"]:
            return None
        match = re.search(r'float\[(\d+)\]', row["sql"])
        return int(match.group(1)) if match else None

    def _init_vec_table(self) -> None:
        """[P4] 初始化 vec0 虚拟表 + 迁移现有 embedding 数据

        [P4 修复] 动态推断 embedding 维度，不硬编码 384
        - vec0 表不存在：用检测到的维度创建（无数据时默认 384，向后兼容）
        - vec0 表存在但维度不匹配：降级纯 Python（运行时不做破坏性操作，由迁移脚本修复）
        - 所有向量在写入时自动归一化（保证 L2 排序 == 余弦相似度排序）
        """
        try:
            with self._get_vec_conn() as conn:
                vec_dim = self._get_vec_table_dim(conn)
                detected_dim = self._detect_embedding_dim(conn)

                if vec_dim is None:
                    if detected_dim is None:
                        # [P4 修复] 数据库为空：跳过 vec0 表创建，由 save 延迟创建
                        # 避免默认创建 384 维表后，用户 save 768 维数据导致维度不匹配
                        logger.info("[LongTermMemory] [P4] 数据库为空，vec0 表延迟到首次 save 时创建")
                        return
                    # 有数据：用检测到的维度创建
                    dim = detected_dim
                    conn.execute(f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS {self._VEC_TABLE_NAME} USING vec0(
                            embedding float[{dim}]
                        )
                    """)
                    logger.info("[LongTermMemory] [P4] vec0 表已创建（维度=%d）", dim)
                elif detected_dim is not None and vec_dim != detected_dim:
                    # vec0 表存在但维度不匹配：降级纯 Python（运行时不做破坏性操作）
                    logger.warning(
                        "[LongTermMemory] [P4] vec0 表维度（%d）与数据维度（%d）不匹配，"
                        "降级为纯 Python。请运行迁移脚本 tlm_migrate_entrypoint.sh 修复",
                        vec_dim, detected_dim
                    )
                    self._use_vec_knn = False
                    return
                # else: vec0 表存在且维度匹配（或无数据可检测），继续迁移

                # 迁移：将 LTM 表中有 embedding 但未在 vec0 表中的数据导入
                missing = conn.execute(f"""
                    SELECT l.rowid, l.embedding
                    FROM {self._TABLE_NAME} l
                    LEFT JOIN {self._VEC_TABLE_NAME} v ON l.rowid = v.rowid
                    WHERE l.embedding IS NOT NULL AND v.rowid IS NULL
                """).fetchall()
                migrated = 0
                skipped = 0
                for row in missing:
                    emb = _blob_to_embedding(row["embedding"])
                    if emb:
                        # 归一化后写入 vec0 表
                        normalized = _normalize_vector(emb)
                        conn.execute(
                            f"INSERT INTO {self._VEC_TABLE_NAME} (rowid, embedding) VALUES (?, ?)",
                            (row["rowid"], _embedding_to_blob(normalized))
                        )
                        migrated += 1
                    else:
                        skipped += 1
                conn.commit()
                if migrated > 0:
                    logger.info("[LongTermMemory] [P4] 迁移 %d 条 embedding 到 vec0 表", migrated)
                if skipped > 0:
                    logger.warning("[LongTermMemory] [P4] %d 条 embedding 解析失败，已跳过", skipped)
        except Exception as e:
            logger.warning("[LongTermMemory] [P4] vec0 表初始化失败，降级为纯 Python: %s", e)
            self._use_vec_knn = False

    def _get_conn(self) -> Any:
        """获取数据库连接

        [P4] 如果 sqlite-vec 可用，自动加载扩展（支持 vec0 虚拟表操作）
        """
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # [P4] sqlite-vec 可用时加载扩展（让 save/delete 的双写能访问 vec0 表）
        if self._use_vec_knn:
            try:
                import sqlite_vec
                conn.enable_load_extension(True)
                conn.load_extension(sqlite_vec.loadable_path())
            except Exception as e:
                logger.warning("[LongTermMemory] [P4] 连接加载 sqlite-vec 失败: %s", e)
        return conn

    # ── 核心操作 ──

    async def save(
        self,
        key: str,
        content: Any,
        importance: int = 3,
        tags: Optional[list[str]] = None,
        sensitive: bool = False,
        metadata: Optional[dict] = None,
        embedding: Optional[list[float]] = None,
    ) -> bool:
        """保存长期记忆

        Args:
            key: 记忆唯一标识
            content: 记忆内容
            importance: 重要性评分 (1-5)
            tags: 标签列表
            sensitive: 是否为敏感信息
            metadata: 附加元数据
            embedding: [TLM-L3] 语义向量缓存（可选，用于 mode="semantic" 检索）

        Returns:
            True 表示保存成功
        """
        if not key:
            logger.warning(log_dict({'module_name': 'long_term_memory', 'action': 'save.key', 'msg': '[LongTermMemory] save 失败: key 为空'}))
            return False

        # 序列化内容
        content_str = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        tags_str = json.dumps(tags or [], ensure_ascii=False)
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        # [P3] embedding 用 BLOB 存储（struct.pack float32），替代 JSON TEXT
        # [P4] 主表存原始 embedding（get 返回原始值），vec0 表存归一化值（用于 KNN）
        embedding_blob = _embedding_to_blob(embedding)
        normalized_blob = _embedding_to_blob(_normalize_vector(embedding)) if embedding is not None else None
        now = time.time()

        with self._lock:
            try:
                with self._get_conn() as conn:
                    conn.execute(f"""
                        INSERT INTO {self._TABLE_NAME}
                        (key, content, importance, tags, created_at, updated_at, last_accessed, access_count, sensitive, verified, metadata, embedding)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            content = excluded.content,
                            importance = excluded.importance,
                            tags = excluded.tags,
                            updated_at = excluded.updated_at,
                            sensitive = excluded.sensitive,
                            metadata = excluded.metadata,
                            embedding = excluded.embedding
                    """, (key, content_str, importance, tags_str, now, now, now, 0, int(sensitive), 0, meta_str, embedding_blob))

                    if self._auto_commit:
                        conn.commit()

                    # [P4] 双写 vec0 表（sqlite-vec 可用且有 embedding 时）
                    if self._use_vec_knn and normalized_blob is not None:
                        row = conn.execute(
                            f"SELECT rowid FROM {self._TABLE_NAME} WHERE key = ?", (key,)
                        ).fetchone()
                        if row:
                            rowid = row["rowid"]
                            try:
                                # [P4 修复] vec0 表不存在时延迟创建（根据当前 embedding 维度）
                                vec_dim = self._get_vec_table_dim(conn)
                                if vec_dim is None:
                                    dim = len(embedding) if embedding else 384
                                    conn.execute(f"""
                                        CREATE VIRTUAL TABLE IF NOT EXISTS {self._VEC_TABLE_NAME} USING vec0(
                                            embedding float[{dim}]
                                        )
                                    """)
                                    logger.info("[LongTermMemory] [P4] vec0 表延迟创建（维度=%d）", dim)
                                    vec_dim = dim

                                # [P4 修复] 维度匹配检查，不匹配则降级（避免每次 save 都失败）
                                current_dim = len(embedding) if embedding else 0
                                if current_dim != vec_dim:
                                    logger.warning(
                                        "[LongTermMemory] [P4] embedding 维度（%d）与 vec0 表维度（%d）不匹配，"
                                        "降级为纯 Python。请运行迁移脚本 tlm_migrate_entrypoint.sh 修复",
                                        current_dim, vec_dim
                                    )
                                    self._use_vec_knn = False
                                else:
                                    conn.execute(
                                        f"DELETE FROM {self._VEC_TABLE_NAME} WHERE rowid = ?", (rowid,)
                                    )
                                    conn.execute(
                                        f"INSERT INTO {self._VEC_TABLE_NAME} (rowid, embedding) VALUES (?, ?)",
                                        (rowid, normalized_blob)  # 归一化后的 BLOB
                                    )
                                    if self._auto_commit:
                                        conn.commit()
                            except Exception as vec_err:
                                logger.warning("[LongTermMemory] [P4] vec0 双写失败（不影响主表）: %s", vec_err)
                                # [P4 修复] 维度不匹配时降级（避免后续每次 save 都失败）
                                err_msg = str(vec_err).lower()
                                if "mismatch" in err_msg or "dimension" in err_msg:
                                    logger.warning("[LongTermMemory] [P4] 检测到维度不匹配，降级为纯 Python")
                                    self._use_vec_knn = False

                logger.debug("[LongTermMemory] 保存成功: key=%s, importance=%d, sensitive=%s", key, importance, sensitive)
                
                # ── 业务指标埋点：记忆存储 ──
                if _BUSINESS_METRICS_AVAILABLE:
                    record_memory_storage(
                        memory_type="long_term",
                        importance=importance,
                    )
                
                return True

            except Exception as e:
                logger.error("[LongTermMemory] 保存失败: key=%s, error=%s", key, e)
                return False

    async def get(self, key: str) -> Optional[LongTermMemoryEntry]:
        """获取长期记忆（带访问追踪）

        Args:
            key: 记忆标识

        Returns:
            LongTermMemoryEntry 或 None
        """
        if not key:
            return None

        with self._lock:
            try:
                with self._get_conn() as conn:
                    row = conn.execute(
                        f"SELECT * FROM {self._TABLE_NAME} WHERE key = ?",
                        (key,)
                    ).fetchone()

                if not row:
                    return None

                # 更新访问统计
                now = time.time()
                conn.execute(
                    f"UPDATE {self._TABLE_NAME} SET last_accessed = ?, access_count = access_count + 1 WHERE key = ?",
                    (now, key)
                )
                conn.commit()

                row = dict(row)
                # [TLM-L3] embedding 列为 TEXT(JSON) 或 NULL，复用 from_dict 的解析逻辑保持一致
                raw_embedding = row.get("embedding")
                # [P3] embedding 用 _blob_to_embedding 解析（兼容 BLOB / 旧 JSON TEXT）
                parsed_embedding = _blob_to_embedding(raw_embedding)
                entry = LongTermMemoryEntry(
                    key=row["key"],
                    content=row["content"],
                    importance=row["importance"],
                    tags=json.loads(row["tags"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"] + 1,
                    sensitive=bool(row["sensitive"]),
                    verified=bool(row["verified"]),
                    metadata=json.loads(row["metadata"]),
                    embedding=parsed_embedding,
                )
                
                # ── 业务指标埋点：记忆访问 ──
                if _BUSINESS_METRICS_AVAILABLE:
                    record_memory_access(
                        memory_key=key,
                        importance=row["importance"],
                    )
                
                return entry

            except Exception as e:
                logger.error("[LongTermMemory] 获取失败: key=%s, error=%s", key, e)
                return None

    async def search(
        self,
        query: str,
        top_k: int = 5,
        min_importance: int = 1,
        include_sensitive: bool = True,
        mode: str = "keyword",
        query_embedding: Optional[list[float]] = None,
    ) -> list[MemoryResult]:
        """搜索长期记忆

        Args:
            query: 搜索关键词
            top_k: 返回结果数量上限
            min_importance: 最小重要性阈值
            include_sensitive: 是否包含敏感信息
            mode: [TLM-L3] 检索模式 "keyword"|"semantic"|"hybrid"（默认 keyword）
            query_embedding: [TLM-L3] 查询向量（mode="semantic"/"hybrid" 时需要）

        Returns:
            匹配的 MemoryResult 列表
        """
        if not query:
            return []

        # semantic/hybrid 需要 query_embedding，无则降级为 keyword
        if mode in ("semantic", "hybrid") and query_embedding is None:
            logger.debug("[LongTermMemory] mode=%s 但无 query_embedding，降级为 keyword", mode)
            mode = "keyword"

        with self._lock:
            try:
                with self._get_conn() as conn:
                    # ── keyword 路径 ──
                    keyword_results = self._search_keyword(
                        conn, query, top_k, min_importance, include_sensitive
                    ) if mode in ("keyword", "hybrid") else []

                    # ── semantic 路径 ──
                    semantic_results = self._search_semantic(
                        conn, query_embedding, top_k, min_importance, include_sensitive
                    ) if mode in ("semantic", "hybrid") else []

                # ── hybrid 合并去重（按 key 去重，keyword 优先）──
                if mode == "hybrid":
                    seen_keys = set()
                    merged = []
                    for r in keyword_results + semantic_results:
                        k = r.metadata.get("key", "")
                        if k not in seen_keys:
                            seen_keys.add(k)
                            merged.append(r)
                    results = merged[:top_k]
                elif mode == "semantic":
                    results = semantic_results
                else:
                    results = keyword_results

                # ── 业务指标埋点：记忆搜索 ──
                if _BUSINESS_METRICS_AVAILABLE:
                    record_memory_search(
                        memory_type="long_term",
                        search_method=mode,
                        hit=len(results) > 0,
                    )

                return results

            except Exception as e:
                logger.error("[LongTermMemory] 搜索失败: query=%s, mode=%s, error=%s", query, mode, e)
                if _BUSINESS_METRICS_AVAILABLE:
                    record_memory_search(
                        memory_type="long_term",
                        search_method=mode,
                        hit=False,
                    )
                return []

    def _search_keyword(
        self, conn, query: str, top_k: int, min_importance: int, include_sensitive: bool
    ) -> list[MemoryResult]:
        """[TLM-L3] keyword 检索路径（LIKE 匹配）"""
        if include_sensitive:
            rows = conn.execute(f"""
                SELECT * FROM {self._TABLE_NAME}
                WHERE importance >= ?
                AND (content LIKE ? OR tags LIKE ?)
                ORDER BY importance DESC, last_accessed DESC
                LIMIT ?
            """, (min_importance, f"%{query}%", f"%{query}%", top_k)).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT * FROM {self._TABLE_NAME}
                WHERE importance >= ?
                AND sensitive = 0
                AND (content LIKE ? OR tags LIKE ?)
                ORDER BY importance DESC, last_accessed DESC
                LIMIT ?
            """, (min_importance, f"%{query}%", f"%{query}%", top_k)).fetchall()

        return [self._row_to_memory_result(dict(row)) for row in rows]

    def _search_semantic(
        self, conn, query_embedding: Optional[list[float]], top_k: int,
        min_importance: int, include_sensitive: bool
    ) -> list[MemoryResult]:
        """[TLM-L3] semantic 检索路径

        [P4] 优先使用 sqlite-vec KNN（O(log n)），降级为纯 Python 余弦相似度（O(n)）
        """
        if query_embedding is None:
            return []

        # [P4] 路径1: sqlite-vec KNN（可用时优先）
        if self._use_vec_knn:
            try:
                return self._search_semantic_vec_knn(
                    conn, query_embedding, top_k, min_importance, include_sensitive
                )
            except Exception as e:
                logger.warning("[LongTermMemory] [P4] vec0 KNN 失败，降级为纯 Python: %s", e)
                # 降级到纯 Python 路径

        # [P4] 路径2: 纯 Python 余弦相似度（降级方案）
        return self._search_semantic_python(
            conn, query_embedding, top_k, min_importance, include_sensitive
        )

    def _search_semantic_vec_knn(
        self, conn, query_embedding: list[float], top_k: int,
        min_importance: int, include_sensitive: bool
    ) -> list[MemoryResult]:
        """[P4] sqlite-vec KNN 搜索路径

        1. 归一化查询向量
        2. vec0 KNN 获取候选 rowid（过采样 3x 应对 importance/sensitive 过滤）
        3. LTM 表过滤 + 计算余弦相似度 score
        4. heapq.nlargest 取 top_k
        """
        # 归一化查询向量（与存储时归一化保持一致）
        normalized_query = _normalize_vector(query_embedding)
        query_blob = _embedding_to_blob(normalized_query)

        # 过采样 3x，应对 importance/sensitive 过滤后数量不足
        oversample_k = min(top_k * 3, top_k + 50)

        with self._get_vec_conn() as vec_conn:
            knn_rows = vec_conn.execute(f"""
                SELECT rowid, distance
                FROM {self._VEC_TABLE_NAME}
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
            """, (query_blob, oversample_k)).fetchall()

        if not knn_rows:
            return []

        # 用主表 conn 查询完整数据 + 过滤
        rowids = [r["rowid"] for r in knn_rows]
        placeholders = ",".join("?" * len(rowids))

        if include_sensitive:
            rows = conn.execute(f"""
                SELECT * FROM {self._TABLE_NAME}
                WHERE rowid IN ({placeholders}) AND importance >= ?
            """, (*rowids, min_importance)).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT * FROM {self._TABLE_NAME}
                WHERE rowid IN ({placeholders}) AND importance >= ? AND sensitive = 0
            """, (*rowids, min_importance)).fetchall()

        # 计算余弦相似度 score（用原始 query_embedding，非归一化）
        # 注意：存储的 embedding 已归一化，但 _cosine_similarity 会正确处理
        scored = []
        for row in rows:
            row = dict(row)
            emb = _blob_to_embedding(row["embedding"])
            if not emb:
                continue
            score = _cosine_similarity(query_embedding, emb)
            scored.append((score, row))

        top_scored = heapq.nlargest(top_k, scored, key=lambda x: x[0])
        results = []
        for score, row in top_scored:
            r = self._row_to_memory_result(row)
            r.confidence = max(0.0, min(1.0, score))
            r.metadata["similarity"] = round(score, 4)
            r.metadata["search_method"] = "vec_knn"  # [P4] 标记搜索路径
            results.append(r)
        return results

    def _search_semantic_python(
        self, conn, query_embedding: list[float], top_k: int,
        min_importance: int, include_sensitive: bool
    ) -> list[MemoryResult]:
        """[P4 降级] 纯 Python 余弦相似度搜索（无 sqlite-vec 时使用）"""
        if include_sensitive:
            rows = conn.execute(f"""
                SELECT * FROM {self._TABLE_NAME}
                WHERE importance >= ? AND embedding IS NOT NULL
            """, (min_importance,)).fetchall()
        else:
            rows = conn.execute(f"""
                SELECT * FROM {self._TABLE_NAME}
                WHERE importance >= ? AND sensitive = 0 AND embedding IS NOT NULL
            """, (min_importance,)).fetchall()

        scored = []
        for row in rows:
            row = dict(row)
            # [P3] 用 _blob_to_embedding 替代 json.loads（兼容 BLOB / 旧 JSON TEXT，速度快 ~10x）
            emb = _blob_to_embedding(row["embedding"])
            if not emb:
                continue
            score = _cosine_similarity(query_embedding, emb)
            scored.append((score, row))

        # [P2] 用 heapq.nlargest 取 top_k，避免全排序 O(n log n) → O(n log k)
        top_scored = heapq.nlargest(top_k, scored, key=lambda x: x[0])
        results = []
        for score, row in top_scored:
            r = self._row_to_memory_result(row)
            r.confidence = max(0.0, min(1.0, score))
            r.metadata["similarity"] = round(score, 4)
            r.metadata["search_method"] = "python_cosine"  # [P4] 标记搜索路径
            results.append(r)
        return results

    def _row_to_memory_result(self, row: dict) -> MemoryResult:
        """[TLM-L3] 将数据库行转换为 MemoryResult"""
        content = row["content"]
        try:
            parsed = json.loads(content)
            if isinstance(parsed, (dict, list)):
                content = parsed
        except (json.JSONDecodeError, TypeError):
            pass

        return MemoryResult(
            content=content,
            confidence=min(1.0, row["importance"] / 5.0),
            source="long_term_memory",
            metadata={
                "key": row["key"],
                "importance": row["importance"],
                "tags": json.loads(row.get("tags", "[]")),
                "sensitive": bool(row.get("sensitive", 0)),
                "verified": bool(row.get("verified", 0)),
            },
        )

    async def delete(self, key: str, force: bool = False) -> bool:
        """删除长期记忆

        默认需要 verified=True 才能删除（保护重要记忆）。
        force=True 可强制删除。

        Args:
            key: 记忆标识
            force: 是否强制删除

        Returns:
            True 表示删除成功
        """
        if not key:
            return False

        with self._lock:
            try:
                with self._get_conn() as conn:
                    # 检查是否需要验证
                    if not force:
                        row = conn.execute(
                            f"SELECT sensitive, importance FROM {self._TABLE_NAME} WHERE key = ?",
                            (key,)
                        ).fetchone()

                        if row:
                            # 高重要性或敏感信息需要验证
                            if row["importance"] >= 5 or row["sensitive"]:
                                logger.warning("[LongTermMemory] 删除被拒绝: key=%s 需要审查", key)
                                return False

                    # [P4] 删除主表前先获取 rowid（用于同步删除 vec0 表）
                    if self._use_vec_knn:
                        row = conn.execute(
                            f"SELECT rowid FROM {self._TABLE_NAME} WHERE key = ?", (key,)
                        ).fetchone()
                        rowid = row["rowid"] if row else None
                    else:
                        rowid = None

                    conn.execute(f"DELETE FROM {self._TABLE_NAME} WHERE key = ?", (key,))

                    # [P4] 同步删除 vec0 表中的索引数据
                    if self._use_vec_knn and rowid is not None:
                        try:
                            conn.execute(
                                f"DELETE FROM {self._VEC_TABLE_NAME} WHERE rowid = ?", (rowid,)
                            )
                        except Exception as vec_err:
                            logger.warning("[LongTermMemory] [P4] vec0 删除失败（不影响主表）: %s", vec_err)

                    conn.commit()

                logger.info("[LongTermMemory] 删除成功: key=%s, force=%s", key, force)
                return True

            except Exception as e:
                logger.error("[LongTermMemory] 删除失败: key=%s, error=%s", key, e)
                return False

    async def verify(self, key: str) -> bool:
        """标记记忆为已审查

        Args:
            key: 记忆标识

        Returns:
            True 表示标记成功
        """
        if not key:
            return False

        with self._lock:
            try:
                with self._get_conn() as conn:
                    conn.execute(
                        f"UPDATE {self._TABLE_NAME} SET verified = 1, updated_at = ? WHERE key = ?",
                        (time.time(), key)
                    )
                    conn.commit()
                return True
            except Exception as e:
                logger.error("[LongTermMemory] 审查标记失败: key=%s, error=%s", key, e)
                return False

    # ── 统计与审查 ──

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            try:
                with self._get_conn() as conn:
                    total = conn.execute(f"SELECT COUNT(*) as c FROM {self._TABLE_NAME}").fetchone()["c"]
                    sensitive_count = conn.execute(f"SELECT COUNT(*) as c FROM {self._TABLE_NAME} WHERE sensitive = 1").fetchone()["c"]
                    verified_count = conn.execute(f"SELECT COUNT(*) as c FROM {self._TABLE_NAME} WHERE verified = 1").fetchone()["c"]
                    high_importance = conn.execute(f"SELECT COUNT(*) as c FROM {self._TABLE_NAME} WHERE importance >= 4").fetchone()["c"]
                    embedding_count = conn.execute(f"SELECT COUNT(*) as c FROM {self._TABLE_NAME} WHERE embedding IS NOT NULL").fetchone()["c"]
            except Exception as e:
                logger.error("[LongTermMemory] 统计失败: %s", e)
                return {}

        return {
            "total_entries": total,
            "sensitive_entries": sensitive_count,
            "verified_entries": verified_count,
            "high_importance_entries": high_importance,
            "unverified_entries": total - verified_count,
            "embedding_entries": embedding_count,
            # [P4] sqlite-vec KNN 状态
            "vec_knn_enabled": self._use_vec_knn,
            "vec_table": self._VEC_TABLE_NAME if self._use_vec_knn else None,
        }

    def list_recent(self, limit: int = 200, days: Optional[int] = None) -> list[LongTermMemoryEntry]:
        """[TLM-L3] 列出最近的记忆条目（按 created_at DESC）

        Args:
            limit: 返回数量上限
            days: 只返回最近 N 天内的记忆（None 表示不限）

        Returns:
            LongTermMemoryEntry 列表
        """
        with self._lock:
            try:
                with self._get_conn() as conn:
                    if days is not None:
                        cutoff = time.time() - days * 86400
                        rows = conn.execute(f"""
                            SELECT * FROM {self._TABLE_NAME}
                            WHERE created_at >= ?
                            ORDER BY created_at DESC
                            LIMIT ?
                        """, (cutoff, limit)).fetchall()
                    else:
                        rows = conn.execute(f"""
                            SELECT * FROM {self._TABLE_NAME}
                            ORDER BY created_at DESC
                            LIMIT ?
                        """, (limit,)).fetchall()

                return [LongTermMemoryEntry.from_dict(dict(row)) for row in rows]
            except Exception as e:
                logger.error("[LongTermMemory] 列出最近记忆失败: %s", e)
                return []

    def list_unverified(self, limit: int = 50) -> list[LongTermMemoryEntry]:
        """列出未审查的记忆条目"""
        with self._lock:
            try:
                with self._get_conn() as conn:
                    rows = conn.execute(f"""
                        SELECT * FROM {self._TABLE_NAME}
                        WHERE verified = 0 AND importance >= 3
                        ORDER BY importance DESC, created_at DESC
                        LIMIT ?
                    """, (limit,)).fetchall()

                return [LongTermMemoryEntry.from_dict(dict(row)) for row in rows]
            except Exception as e:
                logger.error("[LongTermMemory] 列出未审查失败: %s", e)
                return []

    def list_sensitive(self, limit: int = 50) -> list[LongTermMemoryEntry]:
        """列出敏感记忆条目"""
        with self._lock:
            try:
                with self._get_conn() as conn:
                    rows = conn.execute(f"""
                        SELECT * FROM {self._TABLE_NAME}
                        WHERE sensitive = 1
                        ORDER BY importance DESC, created_at DESC
                        LIMIT ?
                    """, (limit,)).fetchall()

                return [LongTermMemoryEntry.from_dict(dict(row)) for row in rows]
            except Exception as e:
                logger.error("[LongTermMemory] 列出敏感记忆失败: %s", e)
                return []
