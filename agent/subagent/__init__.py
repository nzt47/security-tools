"""Subagent — 云枢分身生命周期抽象 + **真实委派执行器**（v7.2 §3.9/§3.10）

分身是一个独立容器，包含选配的 LLM、记忆提供商、工具集和独立的上下文窗口。

设计思想（设计文档 2.2, 2.3, 6.1）：
- 分身全选配：每个分身可独立配置 LLM、记忆、工具
- 热更新：运行时替换配置
- 沙箱隔离：基于显式权限声明的执行隔离

【v7.2（S4-04）真实现】在既有骨架上升级，不另起新包：

  ``delegation.py``   委派契约八要素 + ``task_file`` 物化（§3.9/§3.10；缺任一即拒绝）
  ``channel.py``      §3.10 CLI 通道 + 输出解析三级降级 + 外来文本 taint
  ``executor.py``     真执行器（LLM 循环 / CLI 通道 / 并行编排 / 全闸门）
  ``collection.py``   回收三件套（产物 + 轨迹 + 反思）+ 计费 + stage 闸门
  ``credentials.py``  §5.9 临时凭据（TTL ≤ 任务时长，``finally`` 销毁）
  ``toolset.py``      §5.7 机制 3 工具裁剪子集（对齐 S4-01 Actor 矩阵）
  ``barrier.py``      §4.2 并发上限 + 回压（``ConcurrencyBarrier``）
  ``sandbox.py``      §5.9 第三方执行默认隔离（无宿主网络/无 SSH agent/无 $HOME）

使用方式（真实委派）::

    from agent.subagent.delegation import DelegationContext
    from agent.subagent.executor import DelegationExecutor

    ctx = DelegationContext(
        goal="把 docs/zh 下 12 篇设计稿抽取为可复现步骤序列",
        constraints=["只读仓库，不改文件"],
        prior_artifacts=["docs/zh/a.md"],
        prohibitions=["不得访问网络"],
        artifact_format="JSON Lines：每行 {name, steps[]}",
        budget_tokens=20000,
        timeout_seconds=300,
        callback_url="internal://pipeline/stage2",
        tenant_id="default", subject_id="owner",
    )
    outcome = DelegationExecutor(llm=my_llm, audit=audit, trace=facade).execute(
        ctx, tools=["read_file"], authorized_capabilities=["read_file"])
    if outcome.triad is not None and outcome.triad.is_complete:
        ...        # 三件套齐全 → 可计成本、可推进 stage

使用方式（既有骨架，行为不变）::

    mgr = SubagentLifecycleManager()
    config = SubagentConfig(name="code-helper", model_id="gpt-4", memory_provider="holographic")
    agent = mgr.create(config)
    result = agent.execute("帮我写一段 Python 代码")
    mgr.destroy(agent)
"""

from agent.subagent.container import SubagentConfig, SubagentContainer
from agent.subagent.lifecycle import SubagentLifecycleManager
from agent.subagent.sandbox import Sandbox, PermissionDenied

# ── v7.2 新增：委派契约 / 通道 / 执行器 / 回收 / 凭据 / 工具裁剪 ──
from agent.subagent.barrier import (
    BackpressureTimeout,
    ConcurrencyBarrier,
    ConcurrencyBarrierStats,
)
from agent.subagent.channel import (
    E_UPSTREAM_FORMAT,
    TIER_JSONL,
    TIER_JSONL_RETRY,
    TIER_TEXT_EXTRACT,
    TIER_UPSTREAM_FORMAT,
    TaintedText,
    TaintViolation,
)
from agent.subagent.collection import (
    CollectedTriad,
    CostLedger,
    CostRecord,
    Reflection,
    StageBlocked,
    StageGate,
    TriadCollector,
    collect,
)
from agent.subagent.credentials import (
    CredentialDestroyed,
    CredentialTTLTooLong,
    TemporaryCredential,
    TemporaryCredentialManager,
    credential_scope,
)
from agent.subagent.delegation import (
    EIGHT_ELEMENTS,
    DelegationContext,
    DelegationRejected,
    build_task_file,
    validate_eight_elements,
    write_task_file,
)
from agent.subagent.executor import (
    CAPABILITY_DELEGATE,
    DelegationExecutor,
    ExecutionOutcome,
    LlmChannelExecutor,
    build_executor,
)
from agent.subagent.toolset import (
    E_TOOL_NOT_AUTHORIZED,
    SubAgentToolset,
    ToolNotAuthorized,
)

__all__ = [
    # 既有骨架（行为不变）
    "SubagentConfig",
    "SubagentContainer",
    "SubagentLifecycleManager",
    "Sandbox",
    "PermissionDenied",
    # 委派契约（§3.9 / §3.10）
    "DelegationContext",
    "DelegationRejected",
    "EIGHT_ELEMENTS",
    "build_task_file",
    "write_task_file",
    "validate_eight_elements",
    # 通道（§3.10）
    "E_UPSTREAM_FORMAT",
    "TIER_JSONL",
    "TIER_JSONL_RETRY",
    "TIER_TEXT_EXTRACT",
    "TIER_UPSTREAM_FORMAT",
    "TaintedText",
    "TaintViolation",
    # 执行器
    "DelegationExecutor",
    "ExecutionOutcome",
    "LlmChannelExecutor",
    "build_executor",
    "CAPABILITY_DELEGATE",
    # 回收三件套（§3.9）
    "CollectedTriad",
    "collect",
    "TriadCollector",
    "Reflection",
    "CostLedger",
    "CostRecord",
    "StageGate",
    "StageBlocked",
    # 临时凭据（§5.9）
    "TemporaryCredential",
    "TemporaryCredentialManager",
    "credential_scope",
    "CredentialTTLTooLong",
    "CredentialDestroyed",
    # 工具裁剪（§5.7 机制 3）
    "SubAgentToolset",
    "ToolNotAuthorized",
    "E_TOOL_NOT_AUTHORIZED",
    # 并发屏障（§4.2）
    "ConcurrencyBarrier",
    "ConcurrencyBarrierStats",
    "BackpressureTimeout",
]
