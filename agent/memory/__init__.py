"""云枢 Memory Abstraction Layer (P2) + 记忆四层与租户隔离 (v7.2 §4.3 / P7.2-08)

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

    【S5-01 新增】记忆四层与租户隔离（兼容叠加，不改既有检索公开行为）:
        ├── taxonomy.py       — 四层模型（working/fact/preference/strategy）+ §3.11 MemoryEntry
        ├── tenancy.py        — P7.2-08 隔离矩阵（租户隔离 / 偏好随 subject 携带 / 策略只读）
        ├── layered_store.py  — 分片存储（复用既有 LongTermMemory 引擎与检索）
        ├── forgetting.py     — 遗忘三触发 + TTL + 快照 30 天 + 被遗忘权
        └── identity.py       — 主体伪名化（审计标识符匿名化 = 销毁伪名盐）

用法:
    from agent.memory import MemoryRouter, HolographicAdapter, Mem0Adapter

    router = MemoryRouter()
    router.register("holographic", HolographicAdapter())
    router.register("mem0", Mem0Adapter())

    # 根据任务类型自动选择适配器
    adapter = router.route("fact_extraction")
    results = await adapter.search("关键词")

    # 【S5-01】四层记忆 + 租户隔离
    from agent.memory import LayeredMemoryStore
    store = LayeredMemoryStore()
    await store.write("项目使用 pytest", memory_type="fact", workspace_root="/repo/a")
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
from agent.memory.identity import (
    ERASED_MARKER,
    SubjectPseudonymizer,
    record_memory_audit,
    subject_ref,
)
from agent.memory.taxonomy import (
    GLOBAL_SCOPE,
    LAYER_POLICY,
    MemoryEntry,
    MemoryEntryError,
    MemoryType,
)
from agent.memory.tenancy import (
    ISOLATION_MATRIX,
    MemoryWriteRejected,
    MissingTenancyError,
    TenancyContext,
    TenancyPolicy,
    WriteChannel,
    isolation_matrix_rows,
    resolve_tenancy,
    tenant_id_for_workspace,
)
from agent.memory.layered_store import LayeredMemoryStore, WriteResult
from agent.memory.forgetting import (
    ErasureResult,
    ForgetTrigger,
    ForgettingEngine,
    MemorySnapshotStore,
)

__all__ = [
    "MemoryInterface",
    "MemoryResult",
    "MemoryCapability",
    "MemoryRouter",
    "HolographicAdapter",
    "Mem0Adapter",
    # ── S5-01 记忆四层与租户隔离 ──
    "MemoryEntry",
    "MemoryEntryError",
    "MemoryType",
    "LAYER_POLICY",
    "GLOBAL_SCOPE",
    "LayeredMemoryStore",
    "WriteResult",
    "TenancyContext",
    "TenancyPolicy",
    "WriteChannel",
    "ISOLATION_MATRIX",
    "isolation_matrix_rows",
    "resolve_tenancy",
    "tenant_id_for_workspace",
    "MissingTenancyError",
    "MemoryWriteRejected",
    "ForgettingEngine",
    "ForgetTrigger",
    "ErasureResult",
    "MemorySnapshotStore",
    "SubjectPseudonymizer",
    "ERASED_MARKER",
    "subject_ref",
    "record_memory_audit",
]

__version__ = "0.1.0"
