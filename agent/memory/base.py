"""记忆抽象层 — MemoryInterface 定义

为所有记忆提供商定义统一接口，支持：
- save() / search() / get_profile() / update_graph()
- 统一 MemoryResult 返回类型
- 按置信度和来源追踪

设计文档：P2 云枢架构升级 — Memory Abstraction Layer (2.2.2, 3.1)
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class MemoryCapability(Enum):
    """记忆提供商能力标签"""
    SEMANTIC_SEARCH = "semantic_search"      # 语义/向量搜索
    FULLTEXT_SEARCH = "fulltext_search"      # 全文/关键词搜索
    FACT_EXTRACTION = "fact_extraction"      # 事实提取与去重
    KNOWLEDGE_GRAPH = "knowledge_graph"      # 知识图谱
    USER_PROFILE = "user_profile"            # 用户画像
    LOCAL_FIRST = "local_first"              # 纯本地运行
    REMOTE_SYNC = "remote_sync"              # 云端同步


@dataclass
class MemoryResult:
    """统一的记忆查询结果

    Attributes:
        content: 记忆内容
        confidence: 置信度 (0.0 ~ 1.0)
        source: 提供商名称（如 'holographic', 'mem0'）
        metadata: 附加元数据
    """
    content: Any
    confidence: float
    source: str
    metadata: dict = field(default_factory=dict)


class MemoryInterface(ABC):
    """记忆提供商统一接口

    所有适配器（HolographicAdapter, Mem0Adapter 等）必须实现此接口。
    """

    @abstractmethod
    async def save(
        self,
        key: str,
        data: Any,
        metadata: Optional[dict] = None
    ) -> bool:
        """保存记忆

        Args:
            key: 记忆的唯一标识
            data: 记忆内容（字符串或可序列化对象）
            metadata: 附加元数据（时间戳、类别、重要性等）

        Returns:
            True 表示保存成功
        """
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 5
    ) -> list[MemoryResult]:
        """搜索记忆

        Args:
            query: 搜索关键词
            top_k: 最多返回结果数

        Returns:
            按置信度降序排列的 MemoryResult 列表
        """
        ...

    @abstractmethod
    async def get_profile(self, user_id: str) -> dict:
        """获取用户画像

        Args:
            user_id: 用户标识

        Returns:
            用户特征/偏好字典，无数据时返回空 dict
        """
        ...

    @abstractmethod
    async def update_graph(
        self,
        entities: list,
        relations: list
    ) -> bool:
        """更新知识图谱

        Args:
            entities: 实体列表，每个实体为 dict（包含 name, type 等）
            relations: 关系列表，每个关系为 dict（包含 source, target, type 等）

        Returns:
            True 表示更新成功
        """
        ...

    # ── 可选能力声明 ──

    @property
    def capabilities(self) -> set[MemoryCapability]:
        """返回此提供商支持的能力集合"""
        return set()

    def to_dict(self) -> dict:
        """返回适配器元信息"""
        return {
            "name": self.__class__.__name__,
            "capabilities": [c.value for c in self.capabilities],
        }
