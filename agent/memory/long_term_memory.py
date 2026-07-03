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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LongTermMemoryEntry":
        """从字典反序列化"""
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
        self._init_db()

        logger.info("[LongTermMemory] 初始化完成: db=%s", db_path)

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
                metadata TEXT DEFAULT '{{}}'
            )
        """)
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

    def _get_conn(self) -> Any:
        """获取数据库连接"""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
    ) -> bool:
        """保存长期记忆

        Args:
            key: 记忆唯一标识
            content: 记忆内容
            importance: 重要性评分 (1-5)
            tags: 标签列表
            sensitive: 是否为敏感信息
            metadata: 附加元数据

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
        now = time.time()

        with self._lock:
            try:
                with self._get_conn() as conn:
                    conn.execute(f"""
                        INSERT INTO {self._TABLE_NAME}
                        (key, content, importance, tags, created_at, updated_at, last_accessed, access_count, sensitive, verified, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            content = excluded.content,
                            importance = excluded.importance,
                            tags = excluded.tags,
                            updated_at = excluded.updated_at,
                            sensitive = excluded.sensitive,
                            metadata = excluded.metadata
                    """, (key, content_str, importance, tags_str, now, now, now, 0, int(sensitive), 0, meta_str))
                    
                    if self._auto_commit:
                        conn.commit()

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
    ) -> list[MemoryResult]:
        """搜索长期记忆

        Args:
            query: 搜索关键词
            top_k: 返回结果数量上限
            min_importance: 最小重要性阈值
            include_sensitive: 是否包含敏感信息

        Returns:
            匹配的 MemoryResult 列表
        """
        if not query:
            return []

        with self._lock:
            try:
                with self._get_conn() as conn:
                    safe_query = query.replace('"', '""')
                    
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

                results = []
                for row in rows:
                    row = dict(row)
                    content = row["content"]
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, (dict, list)):
                            content = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass

                    results.append(MemoryResult(
                        content=content,
                        confidence=min(1.0, row["importance"] / 5.0),
                        source="long_term_memory",
                        metadata={
                            "key": row["key"],
                            "importance": row["importance"],
                            "tags": json.loads(row["tags"]),
                            "sensitive": bool(row["sensitive"]),
                            "verified": bool(row["verified"]),
                        },
                    ))

                # ── 业务指标埋点：记忆搜索 ──
                if _BUSINESS_METRICS_AVAILABLE:
                    # 记录搜索（命中）
                    record_memory_search(
                        memory_type="long_term",
                        search_method="keyword",
                        hit=len(results) > 0,
                    )
                
                return results

            except Exception as e:
                logger.error("[LongTermMemory] 搜索失败: query=%s, error=%s", query, e)
                
                # ── 业务指标埋点：记忆搜索失败 ──
                if _BUSINESS_METRICS_AVAILABLE:
                    record_memory_search(
                        memory_type="long_term",
                        search_method="keyword",
                        hit=False,
                    )
                
                return []

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

                    conn.execute(f"DELETE FROM {self._TABLE_NAME} WHERE key = ?", (key,))
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
            except Exception as e:
                logger.error("[LongTermMemory] 统计失败: %s", e)
                return {}

        return {
            "total_entries": total,
            "sensitive_entries": sensitive_count,
            "verified_entries": verified_count,
            "high_importance_entries": high_importance,
            "unverified_entries": total - verified_count,
        }

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
