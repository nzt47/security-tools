"""
向量存储模块 - 基于ChromaDB的语义检索增强版

支持真正的语义向量搜索和知识管理。
提供向后兼容接口，默认使用 ChromaDB 实现。

重构说明：
- 统一向量存储接口，移除重复的 MemoryItem 定义
- 提供 JSON fallback 实现（当 ChromaDB 不可用时）
- 保持与旧版 vector_store.py 的 API 兼容性
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

HAS_CHROMA = False
try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMA = True
    logger.info("✅ ChromaDB 加载成功")
except ImportError:
    logger.warning("⚠️ ChromaDB 未安装，使用 JSON fallback 实现")

HAS_SENTENCE_TRANSFORMERS = False
try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
    logger.info("✅ Sentence Transformers 加载成功")
except ImportError:
    logger.warning("⚠️ Sentence Transformers 未安装，使用关键词搜索")


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


class VectorStore:
    """
    向量存储 - 统一实现
    
    根据环境自动选择：
    - ChromaDB（需要安装 chromadb 和 sentence-transformers）
    - JSON Fallback（纯关键词搜索）
    
    提供方法：
    - add(): 添加记忆
    - search(): 搜索记忆
    - get_recent(): 获取最近记忆
    - clear(): 清空记忆
    """
    
    def __init__(self, collection_name: str = "agent_memory", 
                 persist_dir: str = "./data/memory",
                 model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        """
        初始化向量存储
        
        Args:
            collection_name: 集合名称
            persist_dir: 持久化目录
            model_name: Sentence Transformers 模型名称
        """
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        self.model_name = model_name
        self._storage_path = os.path.join(persist_dir, f"{collection_name}.json")
        
        os.makedirs(persist_dir, exist_ok=True)
        
        if HAS_CHROMA and HAS_SENTENCE_TRANSFORMERS:
            self._use_chroma = True
            self._init_chroma()
        else:
            self._use_chroma = False
            self._items: List[MemoryItem] = []
            self._load_from_file()
        
        logger.info(f"🚀 向量存储初始化完成: {collection_name}")
        logger.info(f"   ├─ 持久化目录: {persist_dir}")
        logger.info(f"   ├─ 存储类型: {'ChromaDB' if self._use_chroma else 'JSON Fallback'}")
        logger.info(f"   └─ 当前记忆数: {self.count}")
    
    def _init_chroma(self):
        """初始化 ChromaDB"""
        try:
            self._chroma_client = chromadb.Client(Settings(
                persist_directory=self.persist_dir,
                anonymized_telemetry=False
            ))
            self._chroma_collection = self._chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "云枢智能体记忆库"}
            )
            self._encoder = SentenceTransformer(self.model_name)
            logger.info(f"✅ ChromaDB 集合创建成功: {self.collection_name}")
        except Exception as e:
            logger.warning(f"⚠️ ChromaDB 初始化失败: {e}，使用 fallback")
            self._use_chroma = False
            self._items = []
    
    def _load_from_file(self):
        """从 JSON 文件加载记忆"""
        if os.path.exists(self._storage_path):
            try:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._items = [MemoryItem.from_dict(item) for item in data]
                logger.info(f"📂 加载记忆: {len(self._items)} 条")
            except Exception as e:
                logger.warning(f"加载记忆失败: {e}")
                self._items = []
        else:
            self._items = []
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
    
    @property
    def count(self) -> int:
        """获取记忆数量"""
        if self._use_chroma:
            return self._chroma_collection.count()
        return len(self._items)
    
    @property
    def items(self) -> List[MemoryItem]:
        """获取所有记忆项（仅 Fallback 模式）"""
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
        
        if self._use_chroma:
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
                logger.warning(f"ChromaDB 添加失败: {e}")
                self._use_chroma = False
                self._items = []
                self._add_fallback(item_id, content, metadata)
        else:
            self._add_fallback(item_id, content, metadata)
        
        logger.debug(f"   ├─ 内容: {content[:60]}...")
        logger.debug(f"   └─ 当前总数: {self.count}")
        return item_id
    
    def _add_fallback(self, item_id: str, content: str, metadata: Dict):
        """Fallback 模式添加"""
        item = MemoryItem(
            id=item_id,
            content=content,
            metadata=metadata,
            timestamp=datetime.now().isoformat()
        )
        self._items.append(item)
        self._save_to_file()
        logger.info(f"✅ 添加记忆 [Fallback]: {item_id}")
    
    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """
        搜索记忆
        
        Args:
            query: 查询文本
            top_k: 返回数量
            
        Returns:
            匹配的记忆项列表
        """
        logger.info(f"🔍 搜索记忆: query='{query[:50]}...', top_k={top_k}")
        
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
                
                logger.info(f"   ├─ 匹配结果数: {len(items)}")
                return items
            except Exception as e:
                logger.warning(f"ChromaDB 搜索失败: {e}，使用 fallback")
                self._use_chroma = False
        
        return self._search_fallback(query, top_k)
    
    def _search_fallback(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """Fallback 关键词搜索"""
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
        final_results = [item for _, item in results[:top_k]]
        
        logger.info(f"   ├─ 匹配结果数: {len(final_results)}")
        for i, item in enumerate(final_results, 1):
            logger.info(f"   │  {i}. {item.content[:50]}...")
        logger.info(f"   └─ 返回: {len(final_results)} 条")
        
        return final_results
    
    def get_recent(self, limit: int = 10) -> List[MemoryItem]:
        """获取最近的记忆"""
        if self._use_chroma:
            try:
                all_items = self.items
                all_items.sort(key=lambda x: x.timestamp, reverse=True)
                return all_items[:limit]
            except Exception:
                pass
        
        return list(reversed(self._items[-limit:]))
    
    def clear(self):
        """清空记忆"""
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
        self._save_to_file()
        logger.info("🗑️ 记忆已清空")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "type": "chroma" if self._use_chroma else "fallback",
            "count": self.count,
            "persist_dir": self.persist_dir,
            "collection_name": self.collection_name
        }


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
    
    def query(self, question: str, top_k: int = 3) -> str:
        """
        查询知识库
        
        Args:
            question: 查询问题
            top_k: 返回结果数量
            
        Returns:
            格式化的查询结果
        """
        results = self.store.search(question, top_k)
        if not results:
            return "（知识库中未找到相关信息）"
        
        context = "\n【知识库检索结果】\n"
        for i, item in enumerate(results, 1):
            context += f"\n{i}. {item.content}\n"
            if item.metadata.get("source"):
                context += f"   来源: {item.metadata['source']}\n"
        return context


VectorStore = VectorStore
MemoryItem = MemoryItem
