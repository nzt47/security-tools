
"""
向量存储模块 - 支持对话历史记忆和语义检索
优化版本 - Phase 3 + 查询缓存优化
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from collections import OrderedDict

# 使用 Phase 3 的 core 抽象层
from core.storage import BaseStorage, create_storage

logger = logging.getLogger(__name__)


class LRUCache:
    """
    LRU (Least Recently Used) 缓存
    
    用于缓存查询结果，提升搜索性能
    """
    
    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[Tuple[str, int], Tuple[List[Any], float]] = OrderedDict()
        self.hits = 0
        self.misses = 0
    
    def _is_expired(self, timestamp: float) -> bool:
        if self.ttl_seconds is None:
            return False
        return (datetime.now().timestamp() - timestamp) > self.ttl_seconds
    
    def get(self, query: str, top_k: int) -> Optional[List[Any]]:
        key = (query, top_k)
        if key in self._cache:
            results, timestamp = self._cache.pop(key)
            if not self._is_expired(timestamp):
                self._cache[key] = (results, datetime.now().timestamp())
                self.hits += 1
                logger.debug(f"[LRUCache] 命中: {query[:30]}... top_k={top_k}")
                return results
            else:
                self.misses += 1
        else:
            self.misses += 1
        return None
    
    def set(self, query: str, top_k: int, results: List[Any]) -> None:
        key = (query, top_k)
        if key in self._cache:
            self._cache.pop(key)
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (results, datetime.now().timestamp())
        logger.debug(f"[LRUCache] 缓存: {query[:30]}... top_k={top_k}")
    
    def invalidate(self) -> None:
        """失效所有缓存（在添加新记忆时调用）"""
        logger.info("[LRUCache] 失效所有缓存")
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


@dataclass
class MemoryItem:
    """记忆项（保持原有接口）"""
    id: str
    content: str
    metadata: Dict[str, Any]
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VectorStore:
    """向量存储 - 优化版本
    
    Phase 3 重构 + 查询缓存优化
    """
    
    def __init__(self, collection_name: str = "agent_memory", persist_dir: str = "./data/memory", 
                 batch_size: int = 100, auto_flush: bool = True,
                 cache_size: int = 100, cache_ttl: int = 300):
        """初始化向量存储
        
        Args:
            collection_name: 集合名称
            persist_dir: 持久化目录
            batch_size: 批量写入阈值
            auto_flush: 是否自动flush
            cache_size: 查询缓存大小
            cache_ttl: 缓存过期时间（秒）
        """
        logger.info("[VectorStore] __init__ 开始初始化")
        
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        
        # 批量操作优化参数
        self.batch_size = batch_size
        self.auto_flush = auto_flush
        self._pending_writes = []
        self._write_count = 0
        
        # 查询缓存优化
        self._query_cache = LRUCache(max_size=cache_size, ttl_seconds=cache_ttl)
        logger.info(f"[VectorStore] 查询缓存: size={cache_size}, ttl={cache_ttl}秒")
        
        # 使用 Phase 3 的统一存储抽象
        logger.info(f"[VectorStore] 创建底层存储: type=json, base_dir={persist_dir}")
        self._storage = create_storage(
            storage_type="json",
            base_dir=persist_dir
        )
        
        # 内存中的缓存（保持原有接口）
        self.items: List[MemoryItem] = []
        self._storage_key = collection_name
        
        self._load()
        
        logger.info(f"向量存储初始化完成: {collection_name}")
        logger.info(f"   ├─ 集合名称: {collection_name}")
        logger.info(f"   ├─ 持久化目录: {persist_dir}")
        logger.info(f"   ├─ 存储键: {self._storage_key}")
        logger.info(f"   └─ 当前记忆数: {len(self.items)}")
        logger.info("[VectorStore] __init__ 初始化完成")
    
    def _load(self):
        """从存储加载"""
        logger.info(f"[VectorStore._load] 开始加载: key={self._storage_key}")
        
        data = self._storage.load(self._storage_key, default=[])
        
        self.items: List[MemoryItem] = []
        if data:
            try:
                self.items = [MemoryItem(**item) for item in data]
                logger.info(f"   ├─ 加载记忆数: {len(self.items)}")
                logger.info(f"   └─ ✅ 加载成功")
            except Exception as e:
                logger.warning(f"加载记忆失败: {e}")
        else:
            logger.info(f"   └─ ✅ 新建空记忆库")
    
    def _save(self):
        """保存到存储"""
        logger.info(f"[VectorStore._save] 开始保存: key={self._storage_key}")
        
        try:
            data = [item.to_dict() for item in self.items]
            self._storage.save(self._storage_key, data)
            logger.debug(f"💾 保存成功: {len(self.items)} 条记忆")
        except Exception as e:
            logger.error(f"❌ 保存记忆失败: {e}")
    
    def add(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加记忆项"""
        logger.info("[VectorStore.add] 开始添加记忆")
        
        # 添加新记忆后需要失效缓存
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
        
        # 批量优化：延迟写入
        if not self.auto_flush:
            self._pending_writes.append(item)
            self._write_count += 1
            if self._write_count >= self.batch_size:
                self._flush_pending()
        else:
            self._save()
        
        logger.info(f"✅ 添加记忆: {item_id}")
        logger.info(f"   └─ 当前总数: {len(self.items)}")
        return item_id
    
    def batch_add(self, items: List[Dict[str, Any]]) -> List[str]:
        """批量添加记忆项（优化版本）"""
        logger.info(f"[VectorStore.batch_add] 批量添加: {len(items)} 条")
        
        # 批量添加后需要失效缓存
        self._query_cache.invalidate()
        
        start_time = datetime.now()
        item_ids = []
        
        for i, item_data in enumerate(items):
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
            item_ids.append(item_id)
        
        self._save()
        
        elapsed = (datetime.now() - start_time).total_seconds()
        throughput = len(items) / elapsed if elapsed > 0 else 0
        
        logger.info(f"✅ 批量添加完成: {len(items)} 条, 吞吐量: {throughput:.2f} 条/秒")
        
        return item_ids
    
    def _flush_pending(self):
        """Flush 待写入的缓存"""
        if self._pending_writes:
            self._save()
            self._pending_writes = []
            self._write_count = 0
    
    def flush(self):
        """手动flush缓存"""
        self._flush_pending()
    
    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """关键词搜索（带缓存优化）
        
        Args:
            query: 查询文本
            top_k: 返回数量
            
        Returns:
            匹配的记忆项列表
        """
        logger.info(f"[VectorStore.search] query='{query[:50]}...', top_k={top_k}")
        
        # 尝试从缓存获取
        cached_results = self._query_cache.get(query, top_k)
        if cached_results is not None:
            logger.info(f"✅ [缓存命中] 返回 {len(cached_results)} 条")
            return cached_results
        
        # 执行搜索
        results = []
        query_lower = query.lower()
        
        for item in reversed(self.items):
            content_lower = item.content.lower()
            score = 0
            
            # 精确匹配
            if query_lower in content_lower:
                score += 10
            
            # 关键词匹配
            match_count = sum(1 for char in query_lower if char in content_lower and char.strip())
            if match_count >= len(query_lower) * 0.3:
                score += match_count
            
            if score > 0:
                results.append((score, item))
        
        # 按分数排序
        results.sort(key=lambda x: x[0], reverse=True)
        final_results = [item for _, item in results[:top_k]]
        
        # 存入缓存
        self._query_cache.set(query, top_k, final_results)
        
        logger.info(f"✅ [缓存未命中] 返回 {len(final_results)} 条")
        
        return final_results
    
    def get_recent(self, limit: int = 10) -> List[MemoryItem]:
        """获取最近的记忆"""
        logger.debug(f"[VectorStore.get_recent] limit={limit}")
        return list(reversed(self.items[-limit:]))
    
    def clear(self):
        """清空记忆"""
        logger.info("[VectorStore.clear] 开始清空")
        self._query_cache.invalidate()
        before_count = len(self.items)
        self.items = []
        self._save()
        logger.info(f"🗑️ 记忆已清空: {before_count} 条")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        return self._query_cache.get_stats()


class KnowledgeBase:
    """知识库"""
    
    def __init__(self, store: Optional[VectorStore] = None):
        self.store = store or VectorStore(collection_name="knowledge_base")
    
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
