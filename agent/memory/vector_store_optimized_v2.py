"""向量存储模块 - 优化版本 V2
实现倒排索引和查询优化

功能：
- 倒排索引加速关键词搜索
- BM25评分算法优化相关性排序
- 异步查询支持
- 批量操作优化
- 查询缓存机制
"""

import logging
import re
import asyncio
import threading
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from collections import OrderedDict, defaultdict

logger = logging.getLogger(__name__)


@dataclass
class MemoryItem:
    """记忆项"""
    id: str
    content: str
    metadata: Dict[str, Any]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InvertedIndex:
    """倒排索引实现

    用于加速关键词搜索，支持：
    - 词项到文档的映射
    - TF-IDF评分
    - BM25评分
    """

    def __init__(self):
        self._index: Dict[str, List[Tuple[str, int]]] = {}  # term -> [(doc_id, frequency), ...]
        self._doc_lengths: Dict[str, int] = {}  # doc_id -> number of terms
        self._total_docs = 0
        self._avg_doc_length = 0.0
        self._lock = threading.RLock()

    def _tokenize(self, text: str) -> List[str]:
        """分词处理"""
        text = text.lower()
        words = re.findall(r'[\w]+', text)
        return [w for w in words if len(w) > 2]

    def _compute_tf(self, term_freq: int, doc_length: int) -> float:
        """计算词频(TF)"""
        return term_freq / doc_length if doc_length > 0 else 0.0

    def _compute_idf(self, term: str) -> float:
        """计算逆文档频率(IDF)"""
        if term not in self._index:
            return 0.0
        doc_count = len(self._index[term])
        return max(0.0, (self._total_docs - doc_count + 0.5) / (doc_count + 0.5))

    def _compute_bm25(self, term_freq: int, doc_length: int, k1: float = 1.5, b: float = 0.75) -> float:
        """计算BM25评分"""
        idf = self._compute_idf(term)
        if idf == 0:
            return 0.0

        numerator = term_freq * (k1 + 1)
        denominator = term_freq + k1 * (1 - b + b * doc_length / self._avg_doc_length)
        
        return idf * numerator / denominator

    def add_document(self, doc_id: str, content: str):
        """添加文档到索引"""
        tokens = self._tokenize(content)
        term_counts = defaultdict(int)
        
        for token in tokens:
            term_counts[token] += 1

        with self._lock:
            # 更新倒排索引
            for term, freq in term_counts.items():
                if term not in self._index:
                    self._index[term] = []
                self._index[term].append((doc_id, freq))

            # 更新文档长度
            self._doc_lengths[doc_id] = len(tokens)
            self._total_docs += 1

            # 更新平均文档长度
            total_length = sum(self._doc_lengths.values())
            self._avg_doc_length = total_length / self._total_docs if self._total_docs > 0 else 0.0

    def remove_document(self, doc_id: str):
        """从索引中移除文档"""
        with self._lock:
            if doc_id not in self._doc_lengths:
                return

            # 从倒排索引中移除
            for term, postings in list(self._index.items()):
                new_postings = [(did, freq) for did, freq in postings if did != doc_id]
                if new_postings:
                    self._index[term] = new_postings
                else:
                    del self._index[term]

            # 更新文档长度记录
            del self._doc_lengths[doc_id]
            self._total_docs -= 1

            # 更新平均文档长度
            if self._total_docs > 0:
                total_length = sum(self._doc_lengths.values())
                self._avg_doc_length = total_length / self._total_docs
            else:
                self._avg_doc_length = 0.0

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """搜索查询，返回(doc_id, score)列表"""
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
                        score = self._compute_bm25(freq, doc_length)
                        scores[doc_id] += score

        # 按评分排序
        results = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return results

    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计信息"""
        total_terms = len(self._index)
        total_postings = sum(len(postings) for postings in self._index.values())
        
        return {
            'total_terms': total_terms,
            'total_postings': total_postings,
            'total_docs': self._total_docs,
            'avg_doc_length': self._avg_doc_length
        }


class LRUQueryCache:
    """查询缓存（LRU策略）"""

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[Tuple[str, int], Tuple[List[Any], float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _is_expired(self, timestamp: float) -> bool:
        return (datetime.now().timestamp() - timestamp) > self.ttl_seconds

    def get(self, query: str, top_k: int) -> Optional[List[Any]]:
        key = (query, top_k)
        if key in self._cache:
            results, timestamp = self._cache.pop(key)
            if not self._is_expired(timestamp):
                self._cache[key] = (results, datetime.now().timestamp())
                self.hits += 1
                logger.debug(f"[QueryCache] 命中: {query[:30]}... top_k={top_k}")
                return results
            else:
                self.misses += 1
        else:
            self.misses += 1
        return None

    def set(self, query: str, top_k: int, results: List[Any]):
        key = (query, top_k)
        if key in self._cache:
            self._cache.pop(key)
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (results, datetime.now().timestamp())
        logger.debug(f"[QueryCache] 缓存: {query[:30]}... top_k={top_k}")

    def invalidate(self):
        """失效所有缓存"""
        logger.info("[QueryCache] 失效所有缓存")
        self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 2),
            "size": len(self._cache)
        }


class VectorStoreOptimized:
    """优化版向量存储

    优化点：
    1. 倒排索引加速关键词搜索
    2. BM25评分算法优化相关性排序
    3. 查询缓存机制
    4. 批量操作优化
    5. 异步查询支持
    """

    def __init__(
        self,
        collection_name: str = "agent_memory",
        persist_dir: str = "./data/memory",
        batch_size: int = 100,
        auto_flush: bool = True,
        cache_size: int = 100,
        cache_ttl: int = 300,
        enable_inverted_index: bool = True
    ):
        """初始化优化版向量存储

        Args:
            collection_name: 集合名称
            persist_dir: 持久化目录
            batch_size: 批量写入阈值
            auto_flush: 是否自动flush
            cache_size: 查询缓存大小
            cache_ttl: 缓存过期时间（秒）
            enable_inverted_index: 是否启用倒排索引
        """
        logger.info("[VectorStoreOptimized] 初始化开始")

        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self.batch_size = batch_size
        self.auto_flush = auto_flush
        self._pending_writes = []
        self._write_count = 0

        # 查询缓存
        self._query_cache = LRUQueryCache(max_size=cache_size, ttl_seconds=cache_ttl)
        logger.info(f"[VectorStoreOptimized] 查询缓存: size={cache_size}, ttl={cache_ttl}秒")

        # 倒排索引
        self._enable_inverted_index = enable_inverted_index
        if enable_inverted_index:
            self._inverted_index = InvertedIndex()
            logger.info("[VectorStoreOptimized] 倒排索引已启用")
        else:
            self._inverted_index = None

        # 内存中的缓存
        self.items: List[MemoryItem] = []
        self._id_to_index: Dict[str, int] = {}  # 加速ID查找

        logger.info(f"[VectorStoreOptimized] 初始化完成: {collection_name}")

    def _load(self):
        """从存储加载（占位实现）"""
        # 在实际实现中，这里会从磁盘加载数据并重建倒排索引
        pass

    def _save(self):
        """保存到存储（占位实现）"""
        pass

    def add(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加记忆项"""
        # 失效缓存
        self._query_cache.invalidate()

        item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        metadata = metadata or {}
        metadata["created_at"] = datetime.now().isoformat()

        item = MemoryItem(
            id=item_id,
            content=content,
            metadata=metadata,
            timestamp=datetime.now().isoformat()
        )

        self.items.append(item)
        self._id_to_index[item_id] = len(self.items) - 1

        # 更新倒排索引
        if self._inverted_index:
            self._inverted_index.add_document(item_id, content)

        # 批量优化
        if not self.auto_flush:
            self._pending_writes.append(item)
            self._write_count += 1
            if self._write_count >= self.batch_size:
                self._flush_pending()
        else:
            self._save()

        logger.info(f"✅ 添加记忆: {item_id}")
        return item_id

    def batch_add(self, items: List[Dict[str, Any]]) -> List[str]:
        """批量添加记忆项"""
        # 失效缓存
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
            self.items.append(item)
            self._id_to_index[item_id] = len(self.items) - 1

            # 更新倒排索引
            if self._inverted_index:
                self._inverted_index.add_document(item_id, content)

            item_ids.append(item_id)

        self._save()
        logger.info(f"✅ 批量添加完成: {len(items)} 条")

        return item_ids

    def _flush_pending(self):
        """Flush待写入的缓存"""
        if self._pending_writes:
            self._save()
            self._pending_writes = []
            self._write_count = 0

    def flush(self):
        """手动flush缓存"""
        self._flush_pending()

    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """关键词搜索（使用倒排索引和BM25评分）"""
        logger.debug(f"[VectorStoreOptimized.search] query='{query[:50]}...', top_k={top_k}")

        # 尝试从缓存获取
        cached_results = self._query_cache.get(query, top_k)
        if cached_results is not None:
            logger.debug(f"✅ [缓存命中] 返回 {len(cached_results)} 条")
            return cached_results

        # 使用倒排索引搜索
        if self._inverted_index:
            results = self._search_with_index(query, top_k)
        else:
            results = self._search_fallback(query, top_k)

        # 存入缓存
        self._query_cache.set(query, top_k, results)

        logger.debug(f"✅ [缓存未命中] 返回 {len(results)} 条")
        return results

    def _search_with_index(self, query: str, top_k: int) -> List[MemoryItem]:
        """使用倒排索引搜索"""
        doc_scores = self._inverted_index.search(query, top_k * 2)  # 获取更多候选

        results = []
        seen_ids = set()

        for doc_id, score in doc_scores:
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)

            idx = self._id_to_index.get(doc_id)
            if idx is not None:
                item = self.items[idx]
                # 将评分添加到metadata用于调试
                item.metadata['_score'] = score
                results.append(item)

            if len(results) >= top_k:
                break

        return results

    def _search_fallback(self, query: str, top_k: int) -> List[MemoryItem]:
        """备用搜索方法（不使用倒排索引）"""
        results = []
        query_lower = query.lower()

        for item in reversed(self.items):
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
        """异步搜索"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.search, query, top_k)

    def get_by_id(self, item_id: str) -> Optional[MemoryItem]:
        """根据ID获取记忆项"""
        idx = self._id_to_index.get(item_id)
        if idx is not None:
            return self.items[idx]
        return None

    def get_recent(self, limit: int = 10) -> List[MemoryItem]:
        """获取最近的记忆"""
        return list(reversed(self.items[-limit:]))

    def clear(self):
        """清空记忆"""
        self._query_cache.invalidate()
        self.items = []
        self._id_to_index.clear()
        if self._inverted_index:
            self._inverted_index = InvertedIndex()
        self._save()
        logger.info("[VectorStoreOptimized] 记忆已清空")

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        return self._query_cache.get_stats()

    def get_index_stats(self) -> Optional[Dict[str, Any]]:
        """获取倒排索引统计"""
        if self._inverted_index:
            return self._inverted_index.get_stats()
        return None


class KnowledgeBaseOptimized:
    """优化版知识库"""

    def __init__(self, store: Optional[VectorStoreOptimized] = None):
        self.store = store or VectorStoreOptimized(collection_name="knowledge_base")

    def add_document(self, content: str, source: str, tags: Optional[List[str]] = None):
        """添加文档到知识库"""
        self.store.add(
            content=content,
            metadata={
                "type": "document",
                "source": source,
                "tags": tags or []
            }
        )

    def query(self, question: str, top_k: int = 3) -> str:
        """查询知识库"""
        results = self.store.search(question, top_k)
        if not results:
            return "（知识库中未找到相关信息）"

        context = "\n【知识库检索结果】\n"
        for i, item in enumerate(results, 1):
            context += f"\n{i}. {item.content}\n"
            if item.metadata.get("source"):
                context += f"   来源: {item.metadata['source']}\n"
        return context

    async def query_async(self, question: str, top_k: int = 3) -> str:
        """异步查询知识库"""
        results = await self.store.search_async(question, top_k)
        if not results:
            return "（知识库中未找到相关信息）"

        context = "\n【知识库检索结果】\n"
        for i, item in enumerate(results, 1):
            context += f"\n{i}. {item.content}\n"
            if item.metadata.get("source"):
                context += f"   来源: {item.metadata['source']}\n"
        return context
