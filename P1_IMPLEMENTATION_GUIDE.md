# 🧠 向量数据库与长期记忆系统实现方案

## 📦 1. 快速开始（10分钟集成）

### 1.1 安装依赖

```bash
pip install chromadb sentence-transformers numpy
# 或者使用FAISS（轻量本地版本）
pip install faiss-cpu sentence-transformers numpy
```

---

## 🏗️ 2. 完整代码实现

### 2.1 创建 `agent/memory/` 模块

```python
# agent/memory/__init__.py
from .vector_store import VectorStore, MemoryItem
from .knowledge_base import KnowledgeBase

__all__ = ["VectorStore", "MemoryItem", "KnowledgeBase"]
```

### 2.2 核心向量存储实现

```python
# agent/memory/vector_store.py
"""
向量存储模块 - 支持对话历史记忆和语义检索
"""

import os
import json
import logging
import numpy as np
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

# 尝试导入向量数据库
try:
    import chromadb
    from chromadb.utils import embedding_functions
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

try:
    from sentence_transformers import SentenceTransformer
    HAS_TRANSFORMERS = True
except ImportError:
    HAS_TRANSFORMERS = False

logger = logging.getLogger(__name__)


@dataclass
class MemoryItem:
    """记忆项"""
    id: str
    content: str
    metadata: Dict[str, Any]
    timestamp: str
    embedding: Optional[List[float]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VectorStore:
    """向量存储基类"""
    
    def __init__(self, collection_name: str = "agent_memory", persist_dir: str = "./data/memory"):
        """初始化向量存储
        
        Args:
            collection_name: 集合名称
            persist_dir: 持久化目录
        """
        self.collection_name = collection_name
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        
        # 选择后端
        if HAS_CHROMA:
            self.backend = "chroma"
            self._init_chroma()
        elif HAS_FAISS and HAS_TRANSFORMERS:
            self.backend = "faiss"
            self._init_faiss()
        else:
            self.backend = "simple"
            self._init_simple()
            logger.warning("使用简单内存存储，建议安装chromadb或faiss")
        
        logger.info(f"向量存储初始化完成，后端: {self.backend}")
    
    def _init_chroma(self):
        """初始化ChromaDB"""
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.collection = self.client.get_or_create_collection(name=self.collection_name)
        logger.info(f"ChromaDB集合: {self.collection_name}")
    
    def _init_faiss(self):
        """初始化FAISS"""
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.dimension = 384
        self.index = faiss.IndexFlatL2(self.dimension)
        self.items: List[MemoryItem] = []
        self._load_faiss()
        logger.info("FAISS索引初始化完成")
    
    def _init_simple(self):
        """初始化简单存储（仅内存，不推荐生产使用）"""
        self.items: List[MemoryItem] = []
        logger.warning("使用简单存储，重启后数据会丢失")
    
    def _get_embedding(self, text: str) -> List[float]:
        """获取文本向量（统一接口）"""
        if self.backend == "chroma":
            # Chroma会自动处理
            return []
        elif self.backend == "faiss":
            return self.model.encode(text).tolist()
        else:
            # 简单模式不使用向量
            return []
    
    def add(self, content: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加记忆项
        
        Args:
            content: 记忆内容
            metadata: 元数据
            
        Returns:
            记忆项ID
        """
        item_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        metadata = metadata or {}
        metadata["created_at"] = datetime.now().isoformat()
        
        item = MemoryItem(
            id=item_id,
            content=content,
            metadata=metadata,
            timestamp=datetime.now().isoformat()
        )
        
        if self.backend == "chroma":
            self.collection.add(
                documents=[content],
                metadatas=[metadata],
                ids=[item_id]
            )
        elif self.backend == "faiss":
            embedding = self._get_embedding(content)
            item.embedding = embedding
            self.index.add(np.array([embedding], dtype=np.float32))
            self.items.append(item)
            self._save_faiss()
        else:
            self.items.append(item)
        
        logger.debug(f"添加记忆: {item_id}")
        return item_id
    
    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """语义检索
        
        Args:
            query: 查询文本
            top_k: 返回数量
            
        Returns:
            匹配的记忆项列表
        """
        if self.backend == "chroma":
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k
            )
            items = []
            for i, doc_id in enumerate(results["ids"][0]):
                items.append(MemoryItem(
                    id=doc_id,
                    content=results["documents"][0][i],
                    metadata=results["metadatas"][0][i],
                    timestamp=results["metadatas"][0][i].get("created_at", "")
                ))
            return items
        elif self.backend == "faiss" and self.items:
            query_emb = self._get_embedding(query)
            _, indices = self.index.search(np.array([query_emb], dtype=np.float32), top_k)
            return [self.items[i] for i in indices[0] if i < len(self.items)]
        else:
            # 简单模式：关键词匹配
            results = []
            for item in reversed(self.items):
                if query.lower() in item.content.lower():
                    results.append(item)
                    if len(results) >= top_k:
                        break
            return results
    
    def get_recent(self, limit: int = 10) -> List[MemoryItem]:
        """获取最近的记忆"""
        if self.backend == "chroma":
            # Chroma需要按时间排序，这里简化
            results = self.collection.peek(limit)
            items = []
            for i, doc_id in enumerate(results["ids"]):
                items.append(MemoryItem(
                    id=doc_id,
                    content=results["documents"][i],
                    metadata=results["metadatas"][i],
                    timestamp=results["metadatas"][i].get("created_at", "")
                ))
            return items
        else:
            return list(reversed(self.items[-limit:]))
    
    def _save_faiss(self):
        """保存FAISS索引"""
        if self.backend == "faiss":
            faiss.write_index(self.index, os.path.join(self.persist_dir, "faiss.index"))
            with open(os.path.join(self.persist_dir, "items.json"), "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in self.items], f, ensure_ascii=False)
    
    def _load_faiss(self):
        """加载FAISS索引"""
        index_path = os.path.join(self.persist_dir, "faiss.index")
        items_path = os.path.join(self.persist_dir, "items.json")
        if os.path.exists(index_path) and os.path.exists(items_path):
            self.index = faiss.read_index(index_path)
            with open(items_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.items = [MemoryItem(**item) for item in data]


class KnowledgeBase:
    """知识库 - 基于向量存储的知识管理"""
    
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
        """查询知识库并返回整理后的上下文"""
        results = self.store.search(question, top_k)
        if not results:
            return "（知识库中未找到相关信息）"
        
        context = "【知识库检索结果】\n"
        for i, item in enumerate(results, 1):
            context += f"\n{i}. {item.content}\n"
            if item.metadata.get("source"):
                context += f"   来源: {item.metadata['source']}\n"
        return context
```

---

## 🔗 3. 集成到 DigitalLife

修改 `agent/digital_life.py`，添加记忆系统：

```python
# 在文件开头添加
from agent.memory import VectorStore, KnowledgeBase

# 在 DigitalLife.__init__ 中添加
class DigitalLife:
    def __init__(self, ...):
        # ... 现有代码 ...
        
        # 新增：初始化记忆系统
        self.memory = VectorStore(collection_name="conversation_history")
        self.knowledge_base = KnowledgeBase()
        
        logger.info("长期记忆系统初始化完成")
    
    # 新增：保存对话到记忆
    def _save_to_memory(self, role: str, content: str):
        """保存对话到记忆"""
        self.memory.add(
            content=content,
            metadata={"role": role, "type": "conversation"}
        )
    
    # 修改 chat 方法
    async def chat(self, message: str, ...):
        # 保存用户消息
        self._save_to_memory("user", message)
        
        # 检索相关记忆
        related_memories = self.memory.search(message, top_k=3)
        
        # 构建上下文
        memory_context = ""
        if related_memories:
            memory_context = "\n【相关历史对话】\n"
            for mem in related_memories:
                memory_context += f"- {mem.content}\n"
        
        # 检索知识库
        kb_context = self.knowledge_base.query(message)
        
        # 把记忆和知识库加入到 prompt 中
        enhanced_message = f"{memory_context}\n{kb_context}\n\n用户问题: {message}"
        
        # ... 原有处理逻辑 ...
        
        # 保存助手回复
        self._save_to_memory("assistant", response)
        
        return response
```

---

## 🧪 4. 测试代码

创建 `test_memory.py`：

```python
import asyncio
import logging
from agent.memory import VectorStore, KnowledgeBase

logging.basicConfig(level=logging.INFO)

async def test_memory():
    print("=" * 60)
    print("测试向量记忆系统")
    print("=" * 60)
    
    # 1. 初始化
    store = VectorStore()
    
    # 2. 添加一些记忆
    store.add("我叫张三，喜欢编程", metadata={"type": "personal"})
    store.add("昨天我完成了规划引擎的开发", metadata={"type": "work"})
    store.add("周末想去爬山，需要准备运动鞋", metadata={"type": "plan"})
    store.add("我的API Key是 sk-1234567890abcdef", metadata={"type": "secret"})
    
    # 3. 测试搜索
    print("\n🔍 搜索 '爬山'...")
    results = store.search("周末有什么计划？", top_k=2)
    for item in results:
        print(f"  - {item.content}")
    
    # 4. 测试知识库
    print("\n📚 测试知识库...")
    kb = KnowledgeBase()
    kb.add_document(
        "Python是一种解释型、面向对象的高级编程语言",
        source="编程入门",
        tags=["python", "programming"]
    )
    kb.add_document(
        "ChromaDB是一个轻量级的向量数据库",
        source="技术文档",
        tags=["vector", "database"]
    )
    
    print(kb.query("什么是向量数据库？"))
    
    print("\n✅ 测试完成！")

if __name__ == "__main__":
    asyncio.run(test_memory())
```

---

## 📊 5. 功能特性对比

| 特性 | ChromaDB | FAISS | 简单存储 |
|------|----------|-------|----------|
| 持久化 | ✅ | ✅ | ❌ |
| 语义检索 | ✅ | ✅ | ❌ |
| 易用性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 性能（大数据） | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐ |
| 依赖大小 | 中等 | 大 | 无 |

**推荐**：开发环境用ChromaDB，生产环境根据数据量选择Chroma或FAISS

---

## 🚀 6. 下一步

完成基础集成后，可以扩展：
- 自动总结对话到记忆
- 记忆重要性评分
- 记忆遗忘机制
- 多语言支持
