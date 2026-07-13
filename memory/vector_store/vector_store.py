"""
向量存储模块 - 基于ChromaDB的语义检索增强版（已整合优化版本）

支持真正的语义向量搜索和知识管理。
提供向后兼容接口，默认使用 ChromaDB 实现。

整合来源:
- vector_store_optimized.py  → LRU查询缓存
- vector_store_optimized_v2.py → 倒排索引 + BM25评分 + 异步查询 + 批量操作

功能：
- ChromaDB 语义搜索（首选）
- JSON Fallback + 倒排索引 + BM25 关键词搜索（次选）
- LRU 查询缓存（所有搜索路径共享）
- 批量添加、ID 查找、异步搜索
"""

import os
import json
import re
import asyncio
import threading
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from collections import OrderedDict, defaultdict

logger = logging.getLogger(__name__)

# 延迟导入：chromadb / sentence_transformers（→ torch）是重量级依赖，
# CI 上首次导入可能需要 2-3 分钟。模块导入时不加载，仅在首次实例化 VectorStore 时检测。
# 通过 _check_chroma_available() 更新这两个标志。
HAS_CHROMA = False
HAS_SENTENCE_TRANSFORMERS = False

_chroma_deps_checked = False


def _check_chroma_available():
    """延迟检测 chromadb + sentence_transformers 是否可用

    避免模块导入时拉起 torch/chromadb 等重量级依赖（CI 上首次导入 torch 可能需要 2-3 分钟）。
    首次调用时执行导入检测，后续调用直接返回。检测结果会更新模块级
    HAS_CHROMA / HAS_SENTENCE_TRANSFORMERS 标志。
    """
    global HAS_CHROMA, HAS_SENTENCE_TRANSFORMERS, _chroma_deps_checked
    if _chroma_deps_checked:
        return
    _chroma_deps_checked = True
    try:
        import chromadb  # noqa: F401
        from chromadb.config import Settings  # noqa: F401
        HAS_CHROMA = True
        logger.info("[OK] ChromaDB loaded")
    except ImportError:
        logger.warning("[WARN] ChromaDB not installed, using JSON fallback")
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401
        HAS_SENTENCE_TRANSFORMERS = True
        logger.info("[OK] Sentence Transformers loaded")
    except ImportError:
        logger.warning("[WARN] Sentence Transformers not installed, using keyword search")


@dataclass
class MemoryItem:
    """记忆项数据类"""
    id: str
    content: str
    metadata: Dict[str, Any]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MemoryItem':
        return cls(**data)


# ═══════════════════════════════════════════════════════════════
# 倒排索引 + BM25 评分（来自 vector_store_optimized_v2.py）
# ═══════════════════════════════════════════════════════════════

class InvertedIndex:
    """倒排索引 — 使用 BM25 算法进行关键词搜索评分

    相比原始的字符匹配评分（_search_fallback 中的评分逻辑），
    BM25 是业界标准的文本检索算法，能提供更准确的排序结果。
    """

    def __init__(self):
        self._index: Dict[str, List[Tuple[str, int]]] = {}  # term -> [(doc_id, frequency), ...]
        self._doc_lengths: Dict[str, int] = {}  # doc_id -> number of terms
        self._total_docs = 0
        self._avg_doc_length = 0.0
        self._lock = threading.RLock()

    def _tokenize(self, text: str) -> List[str]:
        """分词处理 — 仅提取有意义的英文单词（>=3 字符）

        中文搜索走原始的 _search_fallback 的字符重叠评分，效果已足够。
        BM25 专注英文关键词搜索，这是其优势场景。
        """
        return [w.lower() for w in re.findall(r'[a-zA-Z]{3,}', text)]

    def _compute_bm25(self, term: str, term_freq: int, doc_length: int,
                       k1: float = 1.5, b: float = 0.75) -> float:
        """计算 BM25 评分

        参数:
            term: 查询词项（用于计算 IDF）
            term_freq: 词项在文档中的出现频率
            doc_length: 文档长度（词数）
            k1: 饱和度参数
            b: 长度归一化参数
        """
        if term not in self._index:
            return 0.0
        doc_count = len(self._index[term])
        idf = (self._total_docs - doc_count + 0.5) / (doc_count + 0.5)
        if idf <= 0:
            return 0.0

        numerator = term_freq * (k1 + 1)
        denominator = term_freq + k1 * (1 - b + b * doc_length / (self._avg_doc_length or 1))
        return idf * numerator / denominator

    def add_document(self, doc_id: str, content: str):
        """添加文档到索引"""
        tokens = self._tokenize(content)
        term_counts = defaultdict(int)
        for token in tokens:
            term_counts[token] += 1

        with self._lock:
            for term, freq in term_counts.items():
                if term not in self._index:
                    self._index[term] = []
                self._index[term].append((doc_id, freq))

            self._doc_lengths[doc_id] = len(tokens)
            self._total_docs += 1
            total_length = sum(self._doc_lengths.values())
            self._avg_doc_length = total_length / self._total_docs if self._total_docs > 0 else 0.0

    def remove_document(self, doc_id: str):
        """从索引中移除文档"""
        with self._lock:
            if doc_id not in self._doc_lengths:
                return
            for term, postings in list(self._index.items()):
                new_postings = [(did, freq) for did, freq in postings if did != doc_id]
                self._index[term] = new_postings if new_postings else None
            self._index = {k: v for k, v in self._index.items() if v is not None}
            del self._doc_lengths[doc_id]
            self._total_docs -= 1
            if self._total_docs > 0:
                total_length = sum(self._doc_lengths.values())
                self._avg_doc_length = total_length / self._total_docs
            else:
                self._avg_doc_length = 0.0

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """搜索查询，返回 (doc_id, score) 列表"""
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores = defaultdict(float)
        with self._lock:
            for token in query_tokens:
                if token not in self._index:
                    continue
                for doc_id, freq in self._index[token]:
                    doc_length = self._doc_lengths.get(doc_id, 0)
                    if doc_length > 0:
                        scores[doc_id] += self._compute_bm25(token, freq, doc_length)

        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        return {
            'total_terms': len(self._index),
            'total_postings': sum(len(p) for p in self._index.values()),
            'total_docs': self._total_docs,
            'avg_doc_length': self._avg_doc_length,
        }


# ═══════════════════════════════════════════════════════════════
# LRU 查询缓存（来自 vector_store_optimized[_v2].py）
# ═══════════════════════════════════════════════════════════════

class LRUQueryCache:
    """LRU 查询缓存 — 避免重复查询的重复计算

    特性：
    - TTL 过期：缓存项在指定时间后自动失效
    - LRU 淘汰：超出最大容量时淘汰最久未使用的项
    - 命中率统计：便于监控缓存效率
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[Tuple[str, int], Tuple[List[Any], float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _is_expired(self, timestamp: float) -> bool:
        return (datetime.now().timestamp() - timestamp) > self.ttl_seconds

    def get(self, query: str, top_k: int) -> Optional[List[Any]]:
        """获取缓存结果（命中时自动更新访问时间）"""
        key = (query, top_k)
        if key in self._cache:
            results, timestamp = self._cache.pop(key)
            if not self._is_expired(timestamp):
                self._cache[key] = (results, datetime.now().timestamp())
                self.hits += 1
                return results
            self.misses += 1
        else:
            self.misses += 1
        return None

    def set(self, query: str, top_k: int, results: List[Any]):
        """设置缓存结果"""
        key = (query, top_k)
        if key in self._cache:
            self._cache.pop(key)
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (results, datetime.now().timestamp())

    def invalidate(self):
        """失效所有缓存（在添加/删除/清空记忆时调用）"""
        self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存命中统计"""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 2),
            "size": len(self._cache),
        }


# ═══════════════════════════════════════════════════════════════
# 向量存储 — 统一实现
# ═══════════════════════════════════════════════════════════════

class VectorStore:
    """
    向量存储 — 统一实现

    根据环境自动选择存储引擎（优先级：sqlite-vec > chromadb > JSON）：
    - sqlite-vec（首选：轻量级，需 sqlite-vec + sentence-transformers，384 维）
    - ChromaDB（次选：需 chromadb + sentence-transformers）
    - JSON Fallback + 倒排索引 BM25（兜底：纯文本关键词搜索）

    优化特性（已整合）：
    - 倒排索引 + BM25 评分：替代原始字符匹配，排序更准确
    - LRU 查询缓存：重复查询直接从缓存返回，大幅提速
    - 批量添加（batch_add）：批量写入优化
    - ID 查找（get_by_id）：直接定位记忆项
    - 异步搜索（search_async）：非阻塞搜索

    线程安全：
    - _backend 字段在构造期确定后不可变，运行期不再修改
    - _use_chroma 为只读 property（基于 _backend 派生）
    """

    def __init__(self, collection_name: str = "agent_memory",
                 persist_dir: str = "./data/memory",
                 model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
                 cache_size: int = 100, cache_ttl: int = 300,
                 enable_inverted_index: bool = True):
        """
        初始化向量存储

        Args:
            collection_name: 集合名称
            persist_dir: 持久化目录
            model_name: Sentence Transformers 模型名称
            cache_size: 查询缓存最大条数（设为 0 禁用缓存）
            cache_ttl: 缓存过期时间（秒）
            enable_inverted_index: 是否启用倒排索引（仅 JSON fallback 模式生效）
        """
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self.model_name = model_name
        self._storage_path = os.path.join(persist_dir, f"{collection_name}.json")

        os.makedirs(persist_dir, exist_ok=True)

        # ── 查询缓存（两个搜索路径共享）──
        self._query_cache = LRUQueryCache(
            max_size=cache_size,
            ttl_seconds=cache_ttl,
        ) if cache_size > 0 else None

        # ── 存储引擎初始化（优先级：sqlite-vec > chromadb > JSON）──
        # _backend 在构造期确定后不可变，保证线程安全（运行期不再修改 _use_chroma）
        self._backend = "json"
        self._sqlite_vec_backend = None
        self._encoder = None  # sentence_transformers 编码器（sqlite-vec/chromadb 共用）
        self._items: List[MemoryItem] = []
        self._id_to_index: Dict[str, int] = {}
        self._inverted_index = None
        self._chroma_client = None
        self._chroma_collection = None

        _check_chroma_available()

        # 优先级 1: sqlite-vec（轻量级，需 sentence_transformers 编码）
        if HAS_SENTENCE_TRANSFORMERS and self._init_sqlite_vec():
            self._backend = "sqlite_vec"
        # 优先级 2: ChromaDB（重量级，需 chromadb + sentence_transformers）
        elif HAS_CHROMA and HAS_SENTENCE_TRANSFORMERS:
            self._backend = "chromadb"
            self._init_chroma()  # 内部失败时会将 _backend 改为 "json"
        # 优先级 3: JSON Fallback + BM25
        else:
            self._backend = "json"
            self._load_from_file()
            if enable_inverted_index:
                self._inverted_index = InvertedIndex()
                self._rebuild_inverted_index()
                logger.info("[OK] 倒排索引已启用 (BM25)")

        logger.info(f"向量存储初始化完成: {collection_name}")
        logger.info(f"   ├─ 持久化目录: {persist_dir}")
        logger.info(f"   ├─ 存储后端: {self._backend}")

    @property
    def _use_chroma(self) -> bool:
        """是否使用 ChromaDB 后端（只读，基于 _backend 不可变字段派生）

        保留以兼容现有代码的 _use_chroma 检查。
        """
        return self._backend == "chromadb"

    def _init_sqlite_vec(self) -> bool:
        """初始化 sqlite-vec 后端

        Returns:
            True 表示成功初始化
        """
        try:
            import sqlite_vec  # noqa: F401
            from sentence_transformers import SentenceTransformer
            # 延迟导入，避免模块导入时拉起 sqlite-vec 扩展
            from .sqlite_vec_backend import SqliteVecBackend

            # 先初始化 encoder，再从 encoder 动态获取向量维度
            self._encoder = SentenceTransformer(self.model_name)
            dim = self._encoder.get_sentence_embedding_dimension()

            db_path = os.path.join(self.persist_dir, f"{self.collection_name}_vec.db")
            self._sqlite_vec_backend = SqliteVecBackend(
                db_path=db_path,
                collection_name=self.collection_name,
                dim=dim,
            )
            logger.info(f"✅ sqlite-vec 后端启用: {db_path} (dim={dim})")
            return True
        except ImportError as e:
            logger.info(f"sqlite-vec 不可用，降级: {e}")
            return False
        except Exception as e:
            logger.warning(f"sqlite-vec 初始化失败: {e}")
            return False

    def _init_chroma(self):
        """初始化 ChromaDB"""
        try:
            # 局部导入重量级依赖（chromadb / sentence_transformers → torch），
            # 避免在模块导入时拉起 torch。_check_chroma_available() 已确认这些模块可用。
            import chromadb
            from chromadb.config import Settings
            from sentence_transformers import SentenceTransformer
            # chromadb 0.4.x：PersistentClient 才真正持久化到磁盘
            # 旧版用 chromadb.Client(Settings(persist_directory=...)) 实际创建的是 ephemeral 客户端，
            # 且 ephemeral client 有单例缓存，第二次以不同 settings 实例化会报
            # "An instance of Chroma already exists for ephemeral with different settings"
            self._chroma_client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=Settings(anonymized_telemetry=False)
            )
            self._chroma_collection = self._chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "云枢智能体记忆库"}
            )
            self._encoder = SentenceTransformer(self.model_name)
            logger.info(f"✅ ChromaDB 集合创建成功: {self.collection_name}")
        except Exception as e:
            logger.warning(f"⚠️ ChromaDB 初始化失败: {e}，使用 fallback")
            # 构造期允许修改 _backend（尚未对外发布）
            self._backend = "json"
            self._items = []
            self._id_to_index = {}
            # fallback 必须加载磁盘 JSON，否则持久化失效（vs2 重新打开时 _items 为空）
            self._load_from_file()
            # fallback 必须重建倒排索引，否则 BM25 搜索返回 0 结果
            if self._inverted_index is None:
                self._inverted_index = InvertedIndex()
            self._rebuild_inverted_index()

    def _load_from_file(self):
        """从 JSON 文件加载记忆"""
        if os.path.exists(self._storage_path):
            try:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._items = [MemoryItem.from_dict(item) for item in data]
                    self._id_to_index = {item.id: i for i, item in enumerate(self._items)}
                logger.info(f"📂 加载记忆: {len(self._items)} 条")
            except Exception as e:
                logger.warning(f"加载记忆失败: {e}")
                self._items = []
                self._id_to_index = {}
        else:
            self._items = []
            self._id_to_index = {}
            logger.info("📂 新建空记忆库")

    def _save_to_file(self):
        """保存记忆到 JSON 文件"""
        try:
            os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
            with open(self._storage_path, "w", encoding="utf-8") as f:
                data = [item.to_dict() for item in self._items]
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存记忆失败: {e}")

    def _rebuild_inverted_index(self):
        """重建倒排索引（从现有 items 重建）"""
        if not self._inverted_index:
            return
        for item in self._items:
            self._inverted_index.add_document(item.id, item.content)

    @property
    def count(self) -> int:
        """获取记忆数量"""
        if self._backend == "sqlite_vec":
            return self._sqlite_vec_backend.count()
        if self._use_chroma:
            try:
                return self._chroma_collection.count()
            except Exception:
                pass
        return len(self._items)

    @property
    def items(self) -> List[MemoryItem]:
        """获取所有记忆项"""
        if self._backend == "sqlite_vec":
            # sqlite-vec 不支持高效全量拉取，仅返回最近 N 条
            recent = self._sqlite_vec_backend.get_recent(limit=10000)
            return [
                MemoryItem(
                    id=r["id"], content=r["content"],
                    metadata=r["metadata"], timestamp=r["timestamp"],
                ) for r in recent
            ]
        if self._use_chroma:
            try:
                all_data = self._chroma_collection.get()
                return [
                    MemoryItem(
                        id=all_data["ids"][i],
                        content=all_data["documents"][i],
                        metadata=all_data["metadatas"][i],
                        timestamp=all_data["metadatas"][i].get("created_at", "")
                    )
                    for i in range(len(all_data["ids"]))
                ]
            except Exception:
                return []
        return self._items

    # ── 添加 ──

    def add(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        添加记忆项

        Args:
            content: 记忆内容
            metadata: 元数据

        Returns:
            记忆项ID
        """
        item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        metadata = metadata or {}
        metadata["created_at"] = datetime.now().isoformat()

        # 失效缓存
        if self._query_cache:
            self._query_cache.invalidate()

        if self._backend == "sqlite_vec":
            embedding = self._encoder.encode([content]).tolist()
            if self._sqlite_vec_backend.add(
                item_id=item_id,
                content=content,
                embedding=embedding[0],
                metadata=metadata,
                timestamp=metadata["created_at"],
            ):
                logger.info(f"✅ 添加记忆 [sqlite-vec]: {item_id}")
            else:
                logger.error(f"sqlite-vec 添加失败: {item_id}")
        elif self._backend == "chromadb":
            try:
                embedding = self._encoder.encode([content]).tolist()
                self._chroma_collection.add(
                    ids=[item_id],
                    documents=[content],
                    metadatas=[metadata],
                    embeddings=embedding
                )
                logger.info(f"✅ 添加记忆 [Chroma]: {item_id}")
            except Exception as e:
                # 不再修改 _backend（线程安全），仅本次降级到 JSON 路径
                logger.warning(f"ChromaDB 添加失败: {e}，本次降级到 JSON")
                self._add_fallback(item_id, content, metadata)
        else:  # json
            self._add_fallback(item_id, content, metadata)

        logger.debug(f"   ├─ 内容: {content[:60]}...")
        logger.debug(f"   └─ 当前总数: {self.count}")
        return item_id

    def batch_add(self, items: List[Dict[str, Any]]) -> List[str]:
        """批量添加记忆项

        Args:
            items: 记忆项列表，每项包含 content（必填）和 metadata（可选）

        Returns:
            记忆项ID列表
        """
        if self._backend == "sqlite_vec":
            # 失效缓存
            if self._query_cache:
                self._query_cache.invalidate()

            contents = [item.get("content", "") for item in items]
            embeddings = self._encoder.encode(contents).tolist()
            now_iso = datetime.now().isoformat()
            backend_items = []
            item_ids = []
            for i, item_data in enumerate(items):
                content = item_data.get("content", "")
                metadata = item_data.get("metadata", {})
                metadata["created_at"] = now_iso
                item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{i}"
                backend_items.append({
                    "id": item_id,
                    "content": content,
                    "embedding": embeddings[i],
                    "metadata": metadata,
                    "timestamp": now_iso,
                })
                item_ids.append(item_id)
            self._sqlite_vec_backend.batch_add(backend_items)
            logger.info(f"✅ 批量添加完成 [sqlite-vec]: {len(item_ids)} 条")
            return item_ids

        if self._use_chroma:
            # ChromaDB 模式下逐条添加
            return [self.add(item.get("content", ""), item.get("metadata")) for item in items]

        # 失效缓存
        if self._query_cache:
            self._query_cache.invalidate()

        item_ids = []
        for item_data in items:
            content = item_data.get("content", "")
            metadata = item_data.get("metadata", {})
            metadata["created_at"] = datetime.now().isoformat()

            item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            item = MemoryItem(
                id=item_id,
                content=content,
                metadata=metadata,
                timestamp=datetime.now().isoformat()
            )
            self._items.append(item)
            self._id_to_index[item_id] = len(self._items) - 1
            if self._inverted_index:
                self._inverted_index.add_document(item_id, content)
            item_ids.append(item_id)

        self._save_to_file()
        logger.info(f"✅ 批量添加完成: {len(items)} 条")
        return item_ids

    def _add_fallback(self, item_id: str, content: str, metadata: Dict):
        """Fallback 模式添加一条记忆"""
        item = MemoryItem(
            id=item_id,
            content=content,
            metadata=metadata,
            timestamp=datetime.now().isoformat()
        )
        self._items.append(item)
        self._id_to_index[item_id] = len(self._items) - 1
        if self._inverted_index:
            self._inverted_index.add_document(item_id, content)
        self._save_to_file()
        logger.info(f"✅ 添加记忆 [Fallback]: {item_id}")

    # ── 搜索 ──

    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """
        搜索记忆

        搜索路径（按 _backend 自动选择）:
        1. sqlite-vec KNN 向量搜索（首选）
        2. ChromaDB 语义搜索（次选）
        3. 倒排索引 + BM25 关键词搜索（JSON fallback 模式）
        4. 原始字符匹配（兜底）

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            匹配的记忆项列表
        """
        logger.info(f"🔍 搜索记忆: query='{query[:50]}...', top_k={top_k}")

        # ── 查询缓存命中 ──
        if self._query_cache:
            cached = self._query_cache.get(query, top_k)
            if cached is not None:
                logger.info(f"   ├─ [缓存命中] 返回 {len(cached)} 条")
                return cached

        # ── sqlite-vec KNN 搜索 ──
        if self._backend == "sqlite_vec":
            try:
                query_vec = self._encoder.encode([query]).tolist()[0]
                raw_results = self._sqlite_vec_backend.search(query_vec, top_k=top_k)
                items = [
                    MemoryItem(
                        id=r["id"], content=r["content"],
                        metadata=r["metadata"], timestamp=r["timestamp"],
                    ) for r in raw_results
                ]
                logger.info(f"   ├─ sqlite-vec 匹配结果数: {len(items)}")
                if self._query_cache:
                    self._query_cache.set(query, top_k, items)
                return items
            except Exception as e:
                logger.error(f"sqlite-vec 搜索失败: {e}")
                return []

        # ── ChromaDB 搜索 ──
        if self._use_chroma:
            try:
                results = self._chroma_collection.query(
                    query_texts=[query],
                    n_results=top_k
                )
                items = []
                if results["ids"] and len(results["ids"][0]) > 0:
                    for i in range(len(results["ids"][0])):
                        items.append(MemoryItem(
                            id=results["ids"][0][i],
                            content=results["documents"][0][i],
                            metadata=results["metadatas"][0][i],
                            timestamp=results["metadatas"][0][i].get("created_at", "")
                        ))
                logger.info(f"   ├─ ChromaDB 匹配结果数: {len(items)}")

                # 写入缓存
                if self._query_cache:
                    self._query_cache.set(query, top_k, items)
                return items
            except Exception as e:
                # 不再修改 _backend（线程安全），仅本次降级到 JSON 搜索路径
                logger.warning(f"ChromaDB 搜索失败: {e}，本次降级到 JSON")

        # ── JSON Fallback 搜索 ──
        # 混合策略：BM25（英文精准搜索） + 原始评分（中英文兜底）
        if self._inverted_index:
            results = self._bm25_search(query, top_k)
            # BM25 无结果时降级到原始字符匹配
            if not results:
                results = self._search_fallback(query, top_k)
        else:
            results = self._search_fallback(query, top_k)

        # 写入缓存
        if self._query_cache:
            self._query_cache.set(query, top_k, results)

        logger.info(f"   └─ 返回: {len(results)} 条")
        return results

    def _bm25_search(self, query: str, top_k: int) -> List[MemoryItem]:
        """BM25 倒排索引搜索（替代原始 _search_fallback）"""
        doc_scores = self._inverted_index.search(query, top_k * 2)
        results = []
        seen_ids = set()
        for doc_id, score in doc_scores:
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            idx = self._id_to_index.get(doc_id)
            if idx is not None:
                item = self._items[idx]
                item.metadata['_score'] = round(score, 4)
                results.append(item)
            if len(results) >= top_k:
                break
        return results

    def _search_fallback(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """原始字符匹配搜索（兜底：当倒排索引不可用时）"""
        results = []
        query_lower = query.lower()
        for item in reversed(self._items):
            content_lower = item.content.lower()
            score = 0
            if query_lower in content_lower:
                score += 10
            match_count = sum(1 for char in query_lower if char in content_lower and char.strip())
            if match_count >= len(query_lower) * 0.3:
                score += match_count
            if score > 0:
                results.append((score, item))
        results.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in results[:top_k]]

    async def search_async(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """异步搜索（在后台线程中执行搜索，不阻塞事件循环）"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.search, query, top_k)

    # ── ID 查找 ──

    def _has_english_tokens(self, query: str) -> bool:
        """检查查询是否包含有意义的英文 token"""
        # 使用 ASCII 模式检查英文字母（避免 Unicode 模式下中文也被算作 \w）
        return bool(re.search(r'[a-zA-Z]{3,}', query))

    def get_by_id(self, item_id: str) -> Optional[MemoryItem]:
        """根据 ID 获取记忆项"""
        if self._backend == "sqlite_vec":
            r = self._sqlite_vec_backend.get_by_id(item_id)
            if r is None:
                return None
            return MemoryItem(
                id=r["id"], content=r["content"],
                metadata=r["metadata"], timestamp=r["timestamp"],
            )
        if self._use_chroma:
            try:
                all_data = self._chroma_collection.get()
                for i, cid in enumerate(all_data["ids"]):
                    if cid == item_id:
                        return MemoryItem(
                            id=cid,
                            content=all_data["documents"][i],
                            metadata=all_data["metadatas"][i],
                            timestamp=all_data["metadatas"][i].get("created_at", "")
                        )
            except Exception:
                pass
            return None
        idx = self._id_to_index.get(item_id)
        if idx is not None:
            return self._items[idx]
        return None

    # ── 获取最近 ──

    def get_recent(self, limit: int = 10) -> List[MemoryItem]:
        """获取最近的记忆"""
        if self._backend == "sqlite_vec":
            rows = self._sqlite_vec_backend.get_recent(limit=limit)
            return [
                MemoryItem(
                    id=r["id"], content=r["content"],
                    metadata=r["metadata"], timestamp=r["timestamp"],
                ) for r in rows
            ]
        if self._use_chroma:
            try:
                all_items = self.items
                all_items.sort(key=lambda x: x.timestamp, reverse=True)
                return all_items[:limit]
            except Exception:
                pass
        return list(reversed(self._items[-limit:]))

    # ── 清空 ──

    def clear(self):
        """清空记忆"""
        if self._query_cache:
            self._query_cache.invalidate()

        if self._backend == "sqlite_vec":
            self._sqlite_vec_backend.clear()
            logger.info("🗑️ sqlite-vec 数据已清空")
            return

        if self._use_chroma:
            try:
                self._chroma_client.delete_collection(self.collection_name)
                self._chroma_collection = self._chroma_client.create_collection(
                    name=self.collection_name
                )
                logger.info("🗑️ ChromaDB 集合已清空")
            except Exception as e:
                logger.warning(f"清空失败: {e}")

        self._items = []
        self._id_to_index = {}
        if self._inverted_index:
            self._inverted_index = InvertedIndex()
        self._save_to_file()
        logger.info("🗑️ 记忆已清空")

    # ── 统计信息 ──

    def get_stats(self) -> Dict[str, Any]:
        """获取存储统计信息"""
        stats = {
            "backend": self._backend,
            "type": "sqlite_vec" if self._backend == "sqlite_vec"
                    else ("chroma" if self._use_chroma else "fallback"),
            "count": self.count,
            "persist_dir": self.persist_dir,
            "collection_name": self.collection_name,
        }
        if self._backend == "sqlite_vec" and self._sqlite_vec_backend:
            stats["sqlite_vec"] = self._sqlite_vec_backend.get_stats()
        if self._query_cache:
            stats["cache"] = self._query_cache.get_stats()
        if self._inverted_index:
            stats["inverted_index"] = self._inverted_index.get_stats()
        return stats

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取查询缓存统计"""
        return self._query_cache.get_stats() if self._query_cache else {"hits": 0, "misses": 0, "hit_rate": 0, "size": 0}

    def get_index_stats(self) -> Optional[Dict[str, Any]]:
        """获取倒排索引统计"""
        return self._inverted_index.get_stats() if self._inverted_index else None


# ═══════════════════════════════════════════════════════════════
# 知识库
# ═══════════════════════════════════════════════════════════════

class KnowledgeBase:
    """
    知识库 - 基于向量存储的知识管理

    用于管理和查询结构化知识文档。
    """

    def __init__(self, store: Optional[VectorStore] = None):
        """
        初始化知识库

        Args:
            store: 向量存储实例，默认创建新实例
        """
        self.store = store or VectorStore(collection_name="knowledge_base")

    def add_document(self, content: str, source: str, tags: Optional[List[str]] = None):
        """
        添加文档到知识库

        Args:
            content: 文档内容
            source: 文档来源
            tags: 标签列表
        """
        self.store.add(
            content=content,
            metadata={
                "type": "document",
                "source": source,
                "tags": tags or []
            }
        )
        logger.info(f"[KnowledgeBase] 添加文档: {source}")

    def _format_results(self, results: List[MemoryItem]) -> str:
        """格式化搜索结果"""
        if not results:
            return "（知识库中未找到相关信息）"
        context = "\n【知识库检索结果】\n"
        for i, item in enumerate(results, 1):
            context += f"\n{i}. {item.content}\n"
            if item.metadata.get("source"):
                context += f"   来源: {item.metadata['source']}\n"
        return context

    def query(self, question: str, top_k: int = 3) -> str:
        """
        查询知识库

        Args:
            question: 查询问题
            top_k: 返回结果数量

        Returns:
            格式化的查询结果
        """
        return self._format_results(self.store.search(question, top_k))

    async def query_async(self, question: str, top_k: int = 3) -> str:
        """异步查询知识库"""
        results = await self.store.search_async(question, top_k)
        return self._format_results(results)


VectorStore = VectorStore
MemoryItem = MemoryItem
