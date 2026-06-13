"""
Agent 记忆模块

提供统一的向量存储和知识管理功能。

架构说明：
- VectorStore: 统一的向量存储实现
  - 自动检测 ChromaDB 可用性
  - 支持 ChromaDB 语义搜索
  - 自动降级到 JSON 关键词搜索
  
- KnowledgeBase: 知识库管理

导出接口：
- VectorStore: 向量存储类
- MemoryItem: 记忆项数据类
- KnowledgeBase: 知识库类
"""

from .vector_store import VectorStore, MemoryItem, KnowledgeBase

__all__ = [
    "VectorStore",
    "MemoryItem", 
    "KnowledgeBase",
]

__version__ = "2.0.0"
