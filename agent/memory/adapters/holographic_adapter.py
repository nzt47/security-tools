"""HolographicAdapter — 本地优先的 SQLite + FTS5 记忆适配器

功能：
- 全文搜索（FTS5）：高性能关键词检索
- 无外部依赖：纯 Python + sqlite3，无需 API Key
- 持久化存储：数据存储在本地 SQLite 文件
- 自动过期：可选的 TTL 清理
- 实现 MemoryInterface 全部方法

设计思想：
- Holographic（全息）记忆：本地优先，快速读写
- 作为 MemoryRouter 的默认兜底适配器

前置依赖（可选）：
- agent.caching.multi_level_cache — 查询缓存加速
"""

import os
import json
import uuid
import time
import sqlite3
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from agent.memory.base import (
    MemoryInterface,
    MemoryResult,
    MemoryCapability,
)
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



class HolographicAdapter(MemoryInterface):
    """本地 SQLite + FTS5 记忆适配器

    用法:
        adapter = HolographicAdapter(db_path="./data/memory/holographic.db")
        await adapter.save("key_001", "这是一段记忆内容", {"tag": "note"})
        results = await adapter.search("记忆内容", top_k=3)
    """

    # FTS5 全文搜索表名
    _FTS_TABLE = "memory_fts"
    _CONTENT_TABLE = "memory_items"

    def __init__(
        self,
        db_path: str = "./data/memory/holographic.db",
        cache_max_size: int = 100,
        cache_ttl: int = 60,
        enable_cache: bool = True,
    ):
        """
        Args:
            db_path: SQLite 数据库文件路径
            cache_max_size: 查询缓存最大条目数
            cache_ttl: 查询缓存 TTL（秒）
            enable_cache: 是否启用查询缓存
        """
        self.db_path = db_path
        self._lock = threading.Lock()

        # 缓存层（复用项目现有多级缓存）
        self._cache = None
        if enable_cache:
            try:
                from agent.caching.multi_level_cache import MultiLevelCache
                self._cache = MultiLevelCache(
                    l1_max_size=cache_max_size,
                    l1_ttl=cache_ttl,
                    l2_enabled=False,
                )
            except ImportError:
                logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'multilevelcache', 'msg': '[HolographicAdapter] MultiLevelCache 不可用，跳过缓存'}))

        # 确保目录存在并初始化数据库
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

        logger.info("[HolographicAdapter] 初始化完成: db=%s", db_path)

    # ── 能力声明 ──

    @property
    def capabilities(self) -> set[MemoryCapability]:
        return {MemoryCapability.FULLTEXT_SEARCH, MemoryCapability.LOCAL_FIRST}

    # ── 数据库初始化 ──

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（线程本地）"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化数据库表结构"""
        with self._get_conn() as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._CONTENT_TABLE} (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    metadata TEXT DEFAULT '{{}}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    hit_count INTEGER DEFAULT 0
                )
            """)
            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {self._FTS_TABLE}
                USING fts5(key, data, metadata, tokenize='unicode61')

            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_memory_created
                ON {self._CONTENT_TABLE}(created_at)
            """)
            conn.commit()
        logger.debug(log_dict({'module_name': 'holographic_adapter', 'action': 'log', 'msg': '[HolographicAdapter] 数据库表结构已就绪'}))

    # ── MemoryInterface 实现 ──

    async def save(
        self,
        key: str,
        data: Any,
        metadata: Optional[dict] = None,
    ) -> bool:
        """保存记忆到本地 SQLite + 同步 FTS5 索引"""
        if not key:
            logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'save.key', 'msg': '[HolographicAdapter] save 失败: key 为空'}))
            return False

        # 序列化 data（如果非字符串）
        data_str = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        now = time.time()

        with self._lock:
            try:
                with self._get_conn() as conn:
                    # 写入内容表
                    conn.execute(f"""
                        INSERT INTO {self._CONTENT_TABLE} (key, data, metadata, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            data = excluded.data,
                            metadata = excluded.metadata,
                            updated_at = excluded.updated_at
                    """, (key, data_str, meta_str, now, now))

                    # 同步 FTS 索引（删除旧索引 + 插入新索引）
                    conn.execute(f"DELETE FROM {self._FTS_TABLE} WHERE key = ?", (key,))
                    conn.execute(f"""
                        INSERT INTO {self._FTS_TABLE} (key, data, metadata)
                        VALUES (?, ?, ?)
                    """, (key, data_str, meta_str))
                    conn.commit()

                # 使查询缓存失效
                if self._cache:
                    self._cache.clear()

                logger.debug("[HolographicAdapter] 保存成功: key=%s", key)
                return True

            except Exception as e:
                logger.error("[HolographicAdapter] 保存失败: key=%s, error=%s", key, e)
                return False

    async def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[MemoryResult]:
        """全文搜索记忆（FTS5 + 前缀匹配兜底）"""
        if not query:
            return []

        # 查缓存
        cache_key = f"search:{query}:{top_k}"
        if self._cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        with self._lock:
            try:
                with self._get_conn() as conn:
                    # FTS5 搜索：使用双引号精确匹配 + 前缀匹配
                    # 转义特殊字符并构建 FTS5 查询
                    safe_query = query.replace('"', '""')
                    fts_query = f'"{safe_query}" OR {safe_query}*'

                    rows = conn.execute(f"""
                        SELECT c.key, c.data, c.metadata, c.created_at,
                               rank as score
                        FROM {self._FTS_TABLE} f
                        JOIN {self._CONTENT_TABLE} c USING (key)
                        WHERE {self._FTS_TABLE} MATCH ?
                        ORDER BY rank
                        LIMIT ?
                    """, (fts_query, top_k)).fetchall()

                # 如果 FTS5 没有结果，降级为 LIKE 搜索
                if not rows:
                    rows = self._like_fallback(query, top_k, conn)

                # 构建 MemoryResult
                results = []
                for row in rows:
                    row = dict(row)
                    # 尝试解析 metadata
                    meta = {}
                    try:
                        meta = json.loads(row.get("metadata", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        pass

                    # 如果 data 是 JSON 字符串，尝试解析回对象
                    content = row["data"]
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, (dict, list)):
                            content = parsed
                    except (json.JSONDecodeError, TypeError):
                        pass

                    # FTS5 rank 越低越好，转换为置信度
                    raw_score = float(row.get("score", 0))
                    confidence = max(0.0, min(1.0, 1.0 - raw_score * 0.01))

                    results.append(MemoryResult(
                        content=content,
                        confidence=confidence,
                        source="holographic",
                        metadata={
                            "key": row["key"],
                            "created_at": row.get("created_at"),
                            **meta,
                        },
                    ))

                # 写入缓存
                if self._cache:
                    self._cache.set(cache_key, results)

                return results

            except Exception as e:
                logger.error("[HolographicAdapter] 搜索失败: query=%s, error=%s", query, e)
                return []

    def _like_fallback(
        self,
        query: str,
        top_k: int,
        conn,
    ) -> list:
        """FTS5 匹配失败时的 LIKE 降级搜索"""
        try:
            pattern = f"%{query}%"
            rows = conn.execute(f"""
                SELECT key, data, metadata, created_at, 0.5 as score
                FROM {self._CONTENT_TABLE}
                WHERE data LIKE ? OR metadata LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (pattern, pattern, top_k)).fetchall()
            return rows
        except Exception as e:
            logger.warning("[HolographicAdapter] LIKE 降级搜索失败: %s", e)
            return []

    async def get_profile(self, user_id: str) -> dict:
        """从记忆碎片中提取用户画像

        扫描 metadata 中包含指定 user_id 的记忆，聚合为用户画像。
        """
        if not user_id:
            return {}

        with self._lock:
            try:
                with self._get_conn() as conn:
                    # 搜索包含该用户 ID 的记忆
                    pattern = f"%{user_id}%"
                    rows = conn.execute(f"""
                        SELECT data, metadata, created_at
                        FROM {self._CONTENT_TABLE}
                        WHERE key LIKE ? OR metadata LIKE ?
                        ORDER BY created_at DESC
                        LIMIT 50
                    """, (pattern, pattern)).fetchall()

                if not rows:
                    return {}

                # 聚合用户画像
                profile = {
                    "user_id": user_id,
                    "source": "holographic",
                    "memory_count": len(rows),
                    "recent_mentions": [dict(r)["data"][:200] for r in rows[:5]],
                }

                # 从 metadata 中提取偏好标签
                tags = set()
                for r in rows:
                    meta = dict(r).get("metadata", "{}")
                    try:
                        m = json.loads(meta)
                        if isinstance(m, dict):
                            tag = m.get("tag") or m.get("category")
                            if tag:
                                tags.add(str(tag))
                    except (json.JSONDecodeError, TypeError):
                        pass

                if tags:
                    profile["tags"] = list(tags)

                return profile

            except Exception as e:
                logger.error("[HolographicAdapter] 获取画像失败: user_id=%s, error=%s", user_id, e)
                return {}

    async def update_graph(
        self,
        entities: list,
        relations: list,
    ) -> bool:
        """更新本地知识图谱（存储为特殊 key 的记忆）

        HolographicAdapter 没有原生图引擎，将图谱数据扁平存储。
        """
        if not entities and not relations:
            return True

        # 将图谱数据打包为 JSON，以 _graph_ 前缀的 key 存储
        graph_data = {
            "entities": entities,
            "relations": relations,
            "updated_at": time.time(),
        }

        try:
            success = await self.save(
                key="_graph_snapshot",
                data=graph_data,
                metadata={"type": "knowledge_graph", "entity_count": len(entities)},
            )
            return success
        except Exception as e:
            logger.error("[HolographicAdapter] 更新图谱失败: %s", e)
            return False

    # ── 辅助方法 ──

    def get_stats(self) -> dict:
        """获取适配器统计信息"""
        with self._lock:
            try:
                with self._get_conn() as conn:
                    count = conn.execute(
                        f"SELECT COUNT(*) as c FROM {self._CONTENT_TABLE}"
                    ).fetchone()["c"]
                    db_size = Path(self.db_path).stat().st_size if Path(self.db_path).exists() else 0
            except Exception as e:
                logger.warning("[HolographicAdapter] 统计失败: %s", e)
                count = -1
                db_size = 0

        return {
            "name": "HolographicAdapter",
            "source": "holographic",
            "db_path": self.db_path,
            "db_size_bytes": db_size,
            "total_items": count,
            "capabilities": [c.value for c in self.capabilities],
            "cache_enabled": self._cache is not None,
        }

    async def delete(self, key: str) -> bool:
        """删除指定记忆"""
        if not key:
            return False

        with self._lock:
            try:
                with self._get_conn() as conn:
                    conn.execute(f"DELETE FROM {self._CONTENT_TABLE} WHERE key = ?", (key,))
                    conn.execute(f"DELETE FROM {self._FTS_TABLE} WHERE key = ?", (key,))
                    conn.commit()

                if self._cache:
                    self._cache.clear()
                return True
            except Exception as e:
                logger.error("[HolographicAdapter] 删除失败: key=%s, error=%s", key, e)
                return False

    async def clear(self) -> bool:
        """清空所有记忆"""
        with self._lock:
            try:
                with self._get_conn() as conn:
                    conn.execute(f"DELETE FROM {self._CONTENT_TABLE}")
                    conn.execute(f"DELETE FROM {self._FTS_TABLE}")
                    conn.commit()

                if self._cache:
                    self._cache.clear()
                logger.info(log_dict({'module_name': 'holographic_adapter', 'action': 'log', 'msg': '[HolographicAdapter] 已清空所有记忆'}))
                return True
            except Exception as e:
                logger.error("[HolographicAdapter] 清空失败: %s", e)
                return False
