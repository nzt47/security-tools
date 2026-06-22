"""云枢 Memory Abstraction Layer (P2)

统一的记忆抽象层，为所有记忆提供商定义标准接口。

架构:
    MemoryInterface (base.py)
        ├── HolographicAdapter   — 本地 SQLite FTS5（默认兜底）
        ├── Mem0Adapter          — 语义事实提取与去重
        └── (更多适配器可扩展)

    MemoryRouter (router.py)
        ├── 基于任务特征的智能路由
        ├── 自动降级与容错
        └── 多级缓存集成

用法:
    from agent.memory import MemoryRouter, HolographicAdapter, Mem0Adapter

    router = MemoryRouter()
    router.register("holographic", HolographicAdapter())
    router.register("mem0", Mem0Adapter())

    # 根据任务类型自动选择适配器
    adapter = router.route("fact_extraction")
    results = await adapter.search("关键词")
"""

from agent.memory.base import (
    MemoryInterface,
    MemoryResult,
    MemoryCapability,
)
from agent.memory.router import MemoryRouter
from agent.memory.adapters import (
    HolographicAdapter,
    Mem0Adapter,
)

__all__ = [
    "MemoryInterface",
    "MemoryResult",
    "MemoryCapability",
    "MemoryRouter",
    "HolographicAdapter",
    "Mem0Adapter",
]

__version__ = "0.1.0"
