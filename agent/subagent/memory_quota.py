"""向后兼容 shim —— scoped 配额/熔断实现已迁至 memory 域（agent/memory/quota.py）

【为什么迁移（S3 收口：架构规则 no_circular_dependency 修复）】
    agent/memory/scoped_store.py 属 memory 域，需要 MemoryQuotaGuard / guard_from_quota /
    审计事件。若它反向 import agent.subagent.memory_quota，就与
    agent.subagent.memory_broker -> agent.memory.scoped_store 构成依赖环。
    纯配额逻辑与域无关（仅标准库），迁到 memory 域后依赖方向单向：
    subagent -> memory，环被拆掉（不靠 legacy_exemptions 豁免）。

    本模块保留原名与全部导出：既有 import（memory_broker / 单测）逐字可用。
"""

from agent.memory.quota import *  # noqa: F401,F403
from agent.memory.quota import __all__ as _QUOTA_ALL

__all__ = list(_QUOTA_ALL)
