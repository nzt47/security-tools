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
    # [TLM-L2] 向量表与兜底表（新增，与主表/FTS 同库部署）
    _VEC_TABLE = "memories_vec"
    _VEC_FAILED_TABLE = "memories_vec_failed"
    _VEC_DIM = 512  # 向量维度（与 DDL 中 FLOAT[512] 对齐）

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

        # [TLM-L2] 向量层状态：sqlite-vec 不可用时降级为纯 FTS5 + BM25，禁抛异常
        self._vec_available = False
        # [TLM-L3] embedding 生成回调（T2 任务注入真实模型，当前 None 占位）
        self._embedding_func = None
        # [TLM-L2] thread-local 连接缓存：避免每次操作重复 connect + load_extension
        self._conn_local = threading.local()
        # [TLM-L2] 熔断机制：连续失败达阈值后自动降级 _vec_available=False（缺口 D）
        self._vec_fail_count = 0
        self._vec_fail_threshold = 5

        self._init_db()
        self._init_vec_table()            # [TLM-L2] 向量表初始化（失败降级，不抛异常）
        self._migrate_schema_if_needed()  # [TLM] Schema 迁移（幂等）

        logger.info("[HolographicAdapter] 初始化完成: db=%s, vec_available=%s", db_path, self._vec_available)

    # ── 能力声明 ──

    @property
    def capabilities(self) -> set[MemoryCapability]:
        return {MemoryCapability.FULLTEXT_SEARCH, MemoryCapability.LOCAL_FIRST}

    # ── 数据库初始化 ──

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（thread-local 缓存复用）

        Why: sqlite-vec 扩展加载开销大，缓存连接避免每次操作重复 load_extension。
        SQLite Connection 的 with 语句仅 commit/rollback 不 close，缓存复用安全。
        扩展加载状态用 thread-local 标志，每个线程的连接只加载一次。
        """
        conn = getattr(self._conn_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # [TLM-L2] 设置 busy_timeout=5000ms，避免 SQLITE_BUSY 直接抛异常（缺口 C）
            conn.execute("PRAGMA busy_timeout=5000")
            self._conn_local.conn = conn
        # 按需加载 sqlite-vec 扩展（_vec_available=True 且当前线程连接未加载时）
        if self._vec_available and not getattr(self._conn_local, "vec_loaded", False):
            try:
                import sqlite_vec
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                self._conn_local.vec_loaded = True
            except Exception as e:
                # 加载失败不阻断，调用方 try-except 兜底降级
                logger.debug("[HolographicAdapter][conn] sqlite-vec 扩展按需加载失败: %s", e)
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

    # [TLM-L2] 熔断机制 — 连续失败达阈值后自动降级（缺口 D）
    def _record_vec_failure(self):
        """记录向量层失败，达阈值后熔断降级

        Why: 避免 sqlite-vec 运行时持续不可用时，每次操作都触发无意义的重试 + 兜底表写入。
        连续失败达 _vec_fail_threshold 次后，自动设 _vec_available=False。
        """
        self._vec_fail_count += 1
        if self._vec_fail_count >= self._vec_fail_threshold and self._vec_available:
            self._vec_available = False
            logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.circuit_break', 'msg': f'[HolographicAdapter][vec] 熔断触发：连续失败 {self._vec_fail_count} 次 ≥ 阈值 {self._vec_fail_threshold}，自动降级 _vec_available=False'}))
            logger.info("[HolographicAdapter][vec] 熔断路径: 失败计数 %d → _vec_available=False", self._vec_fail_count)

    def _reset_vec_circuit(self):
        """重置熔断器，恢复向量层可用状态（供后台探活调用）

        探活成功后调用此方法重置失败计数和 _vec_available 标志。
        """
        self._vec_fail_count = 0
        self._vec_available = True
        logger.info("[HolographicAdapter][vec] 熔断器重置: _vec_available=True, fail_count=0")

    # [TLM-L2] 向量表初始化 — sqlite-vec 不可用时降级，禁抛异常
    def _init_vec_table(self):
        """创建 memories_vec 虚拟表（sqlite-vec 扩展）

        加载策略（按 TLM_DESIGN §5.2 结论）：
        1. 优先 sqlite_vec.load(conn) Python 适配器（不依赖 ENABLE_LOAD_EXTENSION 编译选项）
        2. 失败时 fallback 到 conn.load_extension('sqlite_vec') 原生扩展加载
        3. 全部失败：设置 _vec_available=False，记日志，不抛异常（降级为纯 FTS5）
        """
        self._vec_available = False
        try:
            import sqlite_vec  # noqa: F401  延迟导入，可选依赖
        except ImportError:
            logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.import_failed', 'msg': '[HolographicAdapter] sqlite-vec 未安装，降级为纯 FTS5 + BM25'}))
            logger.info("[HolographicAdapter][vec] 降级路径: sqlite_vec 模块不可导入 → _vec_available=False")
            return

        try:
            with self._get_conn() as conn:
                loaded = False
                # 优先 Python 适配器（TLM_DESIGN §5.2 确认可用）
                # 注意：sqlite_vec.load 内部仍调 conn.load_extension，需先开启扩展加载权限
                try:
                    conn.enable_load_extension(True)
                    sqlite_vec.load(conn)
                    loaded = True
                    self._conn_local.vec_loaded = True  # 标记当前线程连接已加载扩展
                    logger.info("[HolographicAdapter][vec] sqlite_vec.load(conn) 加载成功（Python 适配器路径）")
                except Exception as e_py:
                    logger.info("[HolographicAdapter][vec] Python 适配器加载失败: %s → 尝试原生 load_extension", e_py)
                # Fallback 原生扩展加载（按文件名加载）
                if not loaded:
                    try:
                        conn.enable_load_extension(True)
                        conn.load_extension('sqlite_vec')
                        loaded = True
                        logger.info("[HolographicAdapter][vec] load_extension('sqlite_vec') 加载成功（原生路径）")
                    except Exception as e_native:
                        logger.info("[HolographicAdapter][vec] 原生 load_extension 失败: %s", e_native)

                if not loaded:
                    logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.load_failed', 'msg': '[HolographicAdapter] sqlite-vec 扩展加载全部失败，降级为纯 FTS5 + BM25'}))
                    logger.info("[HolographicAdapter][vec] 降级路径: 两种加载方式均失败 → _vec_available=False")
                    return

                conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._VEC_TABLE} "
                    f"USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[{self._VEC_DIM}])"
                )
                conn.commit()
            self._vec_available = True
            logger.info("[HolographicAdapter][vec] 向量表就绪: table=%s, dim=%d → _vec_available=True", self._VEC_TABLE, self._VEC_DIM)
        except Exception as e:
            # 兜底：任何意外异常都降级，绝不抛出（守不易约束）
            self._vec_available = False
            logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'vec.init_failed', 'msg': f'[HolographicAdapter] memories_vec 初始化失败，降级运行: {e}'}))
            logger.info("[HolographicAdapter][vec] 降级路径: 初始化异常 %s → _vec_available=False", e)

    # [TLM] Schema 迁移 — 幂等，可安全重复调用
    def _migrate_schema_if_needed(self):
        """补齐 memory_items 表的 TLM 扩展字段，并创建向量写入兜底表

        - access_count / last_accessed / type / category：缺失则 ALTER TABLE ADD COLUMN
        - memories_vec_failed：向量写入重试耗尽后的兜底存储表，供后台补偿
        """
        required_columns = {
            "access_count": "INTEGER DEFAULT 0",
            "last_accessed": "REAL",
            "type": "TEXT",
            "category": "TEXT",
        }
        try:
            with self._get_conn() as conn:
                # PRAGMA table_info 只读，锁内仅做 schema 查询与 DDL，无外部 I/O 回调
                existing_cols = {
                    row["name"]
                    for row in conn.execute(f"PRAGMA table_info({self._CONTENT_TABLE})").fetchall()
                }
                added = []
                for col, decl in required_columns.items():
                    if col not in existing_cols:
                        conn.execute(f"ALTER TABLE {self._CONTENT_TABLE} ADD COLUMN {col} {decl}")
                        logger.info("[HolographicAdapter][migrate] 新增字段: %s %s", col, decl)
                        added.append(col)
                # 兜底表（向量写入失败补偿）
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._VEC_FAILED_TABLE} ("
                    "key TEXT PRIMARY KEY, "
                    "embedding BLOB, "
                    "error TEXT, "
                    "created_at REAL NOT NULL, "
                    "retries INTEGER DEFAULT 0)"
                )
                conn.commit()
                logger.info("[HolographicAdapter][migrate] 迁移完成: 新增字段=%s, 兜底表=%s 已就绪",
                            added if added else "无", self._VEC_FAILED_TABLE)
        except Exception as e:
            # schema 迁移失败不阻断启动（守不易：旧 schema 仍可用，仅缺少新字段）
            logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'migrate.failed', 'msg': f'[HolographicAdapter] Schema 迁移失败（继续以旧 schema 运行）: {e}'}))
            logger.info("[HolographicAdapter][migrate] 迁移失败但继续运行: %s", e)

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

    # [TLM-L2] 带向量的保存 — 主表+FTS 同事务，向量层异步写入
    async def save_with_embedding(
        self,
        key: str,
        data: Any,
        metadata: Optional[dict] = None,
        embedding: Optional[list] = None,
    ) -> bool:
        """保存记忆 + 向量（向量层失败不影响主表写入）

        - 主表 + FTS 同事务写入（与 save 一致）
        - 向量层可用时异步写入 memories_vec（含重试）
        - 向量层不可用时跳过，仅写主表+FTS
        - embedding 缺失时通过 _embedding_func 回调生成
        """
        # 先写主表+FTS
        ok = await self.save(key, data, metadata)
        if not ok:
            return False

        if not self._vec_available:
            logger.info("[HolographicAdapter][vec] save_with_embedding: 向量层不可用，跳过向量写入 key=%s", key)
            return True

        # embedding 缺失：尝试回调生成
        if embedding is None:
            if self._embedding_func is None:
                logger.info("[HolographicAdapter][vec] embedding 缺失且无回调，跳过向量写入 key=%s", key)
                return True
            # 异步生成 embedding 后写入（不阻塞主流程）
            t = threading.Thread(
                target=self._async_embed_and_write,
                args=(key, data),
                daemon=True,
            )
            t.start()
            return True

        # 维度校验
        if len(embedding) != self._VEC_DIM:
            logger.warning("[HolographicAdapter][vec] 维度不匹配: 期望 %d, 实际 %d, 跳过向量写入 key=%s",
                           self._VEC_DIM, len(embedding), key)
            return True

        # 异步写入向量层（重试 + 兜底）
        t = threading.Thread(
            target=self._retry_vec_write,
            args=(key, embedding),
            daemon=True,
        )
        t.start()
        return True

    # [TLM-L2] 向量检索 — KNN 查询
    async def search_vector(
        self,
        query_embedding: list,
        top_k: int = 5,
    ) -> list[MemoryResult]:
        """向量 KNN 检索（sqlite-vec 不可用时返回空列表）"""
        if not self._vec_available:
            logger.info("[HolographicAdapter][vec] search_vector: 向量层不可用，返回空列表")
            return []

        if len(query_embedding) != self._VEC_DIM:
            logger.warning("[HolographicAdapter][vec] 查询维度不匹配: 期望 %d, 实际 %d",
                           self._VEC_DIM, len(query_embedding))
            return []

        try:
            import sqlite_vec
            with self._lock:
                with self._get_conn() as conn:
                    # sqlite-vec 扩展已由 _get_conn 按需加载（thread-local 缓存）
                    query_blob = sqlite_vec.serialize_float32(query_embedding)
                    rows = conn.execute(f"""
                        SELECT id, distance
                        FROM {self._VEC_TABLE}
                        WHERE embedding MATCH ?
                        ORDER BY distance
                        LIMIT ?
                    """, (query_blob, top_k)).fetchall()

            results = []
            for row in rows:
                row = dict(row)
                # distance 越小越相似，转换为 confidence
                distance = float(row.get("distance", 1.0))
                confidence = max(0.0, 1.0 - distance / 2.0)
                # 从主表取 content
                key = row.get("id")
                content = ""
                meta = {}
                try:
                    with self._get_conn() as conn2:
                        main_row = conn2.execute(
                            f"SELECT data, metadata FROM {self._CONTENT_TABLE} WHERE key = ?",
                            (key,),
                        ).fetchone()
                    if main_row:
                        main_row = dict(main_row)
                        content = main_row["data"]
                        try:
                            meta = json.loads(main_row.get("metadata", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            pass
                except Exception:
                    pass

                results.append(MemoryResult(
                    content=content,
                    confidence=confidence,
                    source="holographic_vec",
                    metadata={"key": key, "distance": distance, **meta},
                ))
            logger.info("[HolographicAdapter][vec] search_vector 命中 %d 条 (top_k=%d)", len(results), top_k)
            return results
        except Exception as e:
            self._record_vec_failure()  # [TLM-L2] 熔断计数
            logger.warning(log_dict({'module_name': 'holographic_adapter', 'action': 'search_vector.failed', 'msg': f'[HolographicAdapter] 向量检索失败: {e}'}))
            logger.info("[HolographicAdapter][vec] search_vector 异常 %s → 返回空列表（fail_count=%d）", e, self._vec_fail_count)
            return []

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
                    # [TLM-L2] 清理向量表（存在时）
                    if self._vec_available:
                        try:
                            conn.execute(f"DELETE FROM {self._VEC_TABLE}")
                        except Exception as e_vec:
                            logger.warning("[HolographicAdapter] 清空向量表失败（忽略）: %s", e_vec)
                    # 清理兜底表
                    try:
                        conn.execute(f"DELETE FROM {self._VEC_FAILED_TABLE}")
                    except Exception:
                        pass
                    conn.commit()

                if self._cache:
                    self._cache.clear()
                logger.info(log_dict({'module_name': 'holographic_adapter', 'action': 'log', 'msg': '[HolographicAdapter] 已清空所有记忆'}))
                return True
            except Exception as e:
                logger.error("[HolographicAdapter] 清空失败: %s", e)
                return False

    # ── [TLM-L2] 向量层写入辅助方法 ──

    def _retry_vec_write(self, key: str, embedding: list, max_retries: int = 3):
        """向量写入重试（指数退避 1s/2s/4s），耗尽后写兜底表

        使用项目统一 RetryPolicy 类（exponential + jitter）
        """
        from agent.error_handler import RetryPolicy
        policy = RetryPolicy(
            max_retries=max_retries,
            initial_delay=1.0,
            backoff_factor=2.0,
            strategy="exponential",
        )
        last_error = None
        for attempt in range(max_retries):
            try:
                self._write_vec_row(key, embedding)
                logger.info("[HolographicAdapter][vec] 向量写入成功 key=%s (attempt=%d)", key, attempt + 1)
                return
            except Exception as e:
                last_error = e
                logger.warning("[HolographicAdapter][vec] 向量写入失败 key=%s attempt=%d/%d error=%s",
                               key, attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    delay = policy.calculate_delay(attempt)
                    logger.info("[HolographicAdapter][vec] 重试等待 %.2fs (attempt=%d)", delay, attempt + 1)
                    import time as _time
                    _time.sleep(delay)

        # 重试耗尽，写兜底表
        logger.error("[HolographicAdapter][vec] 向量写入重试耗尽 key=%s → 写兜底表 %s", key, self._VEC_FAILED_TABLE)
        self._write_vec_failed(key, embedding, str(last_error))
        self._record_vec_failure()  # [TLM-L2] 熔断计数

    def _write_vec_row(self, key: str, embedding: list):
        """写入向量行（DELETE+INSERT 模拟 upsert，vec0 不支持 UPDATE）"""
        import sqlite_vec
        with self._lock:
            with self._get_conn() as conn:
                # sqlite-vec 扩展已由 _get_conn 按需加载（thread-local 缓存）
                blob = sqlite_vec.serialize_float32(embedding)
                conn.execute(f"DELETE FROM {self._VEC_TABLE} WHERE id = ?", (key,))
                conn.execute(
                    f"INSERT INTO {self._VEC_TABLE} (id, embedding) VALUES (?, ?)",
                    (key, blob),
                )
                conn.commit()

    def _write_vec_failed(self, key: str, embedding: list, error: str):
        """写入兜底表（embedding 用 JSON bytes 存 BLOB）"""
        now = time.time()
        blob = json.dumps(embedding).encode("utf-8")
        try:
            with self._lock:
                with self._get_conn() as conn:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {self._VEC_FAILED_TABLE} "
                        f"(key, embedding, error, created_at, retries) VALUES (?, ?, ?, ?, ?)",
                        (key, blob, error, now, 3),
                    )
                    conn.commit()
        except Exception as e:
            logger.error("[HolographicAdapter][vec] 兜底表写入失败 key=%s error=%s", key, e)

    def _async_embed_and_write(self, key: str, data: Any):
        """通过 _embedding_func 回调生成 embedding 后写入向量层"""
        try:
            embedding = self._embedding_func(data)
            if embedding is None:
                logger.info("[HolographicAdapter][vec] embedding 回调返回 None，跳过 key=%s", key)
                return
            if len(embedding) != self._VEC_DIM:
                logger.warning("[HolographicAdapter][vec] 回调 embedding 维度不匹配: 期望 %d, 实际 %d",
                               self._VEC_DIM, len(embedding))
                return
            self._retry_vec_write(key, embedding)
        except Exception as e:
            logger.error("[HolographicAdapter][vec] embedding 回调异常 key=%s error=%s", key, e)

    def replay_vec_failed(self, max_items: int = 100) -> int:
        """重放兜底表中的失败向量写入（后台补偿）

        Returns:
            成功重放的条数
        """
        if not self._vec_available:
            logger.info("[HolographicAdapter][vec] replay_vec_failed: 向量层不可用，跳过")
            return 0

        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    f"SELECT key, embedding FROM {self._VEC_FAILED_TABLE} LIMIT ?",
                    (max_items,),
                ).fetchall()
        except Exception as e:
            logger.error("[HolographicAdapter][vec] 读取兜底表失败: %s", e)
            return 0

        replayed = 0
        for row in rows:
            row = dict(row)
            key = row["key"]
            try:
                embedding = json.loads(row["embedding"].decode("utf-8"))
                self._write_vec_row(key, embedding)
                # 重放成功，从兜底表删除
                with self._get_conn() as conn:
                    conn.execute(f"DELETE FROM {self._VEC_FAILED_TABLE} WHERE key = ?", (key,))
                    conn.commit()
                replayed += 1
                logger.info("[HolographicAdapter][vec] 兜底表重放成功 key=%s", key)
            except Exception as e:
                logger.warning("[HolographicAdapter][vec] 兜底表重放失败 key=%s error=%s", key, e)

        logger.info("[HolographicAdapter][vec] replay_vec_failed 完成: 重放 %d 条", replayed)
        return replayed
