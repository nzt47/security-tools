"""Subagent 生命周期管理 — 分身的创建、销毁、热更新与监控

SubagentLifecycleManager 管理所有分身容器的全生命周期：
- create(config) → 创建并初始化分身
- destroy(subagent) → 清理资源 + 持久化记忆增量
- hot_reload(subagent, new_config) → 运行时热替换配置
- list() / get() → 分身查询
- gc() → 超时分身自动清理
"""

from __future__ import annotations  # 使 list[str] 等注解延迟求值，避免与类方法 list() 冲突

import logging
import math
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence, Tuple

from agent.subagent.container import SubagentConfig, SubagentContainer

logger = logging.getLogger(__name__)

#: 批量委派里「建分身失败」的证据码（容量上限 / 名称冲突 / 档案被销毁）
E_SUBAGENT_UNAVAILABLE = "E_SUBAGENT_UNAVAILABLE"

#: 该失败的机器可读分类（与 executor 既有 sub_reason 口径一致：backpressure/credentials/…）
SUB_REASON_SUBAGENT_UNAVAILABLE = "subagent_unavailable"


class SubagentLifecycleError(Exception):
    """分身生命周期异常"""
    pass


class SubagentLifecycleManager:
    """分身生命周期管理器

    负责分身的完整生命周期管理：
    创建 → 配置 → 执行 → 热更新 → 销毁
    """

    def __init__(self, max_subagents: int = 20):
        """
        Args:
            max_subagents: 最大活跃分身数（防止资源耗尽）
        """
        self._subagents: dict[str, SubagentContainer] = {}
        self._max_subagents = max_subagents
        self._total_created: int = 0
        self._total_destroyed: int = 0
        # Why RLock 保护 _subagents 与计数：create 的「容量检查→写入」为 TOCTOU
        # 序列（并发可超卖超过 max_subagents）；_total_created/_total_destroyed 为
        # 读-改-写非原子。RLock 允许 gc→destroy 同线程重入，锁内仅内存 dict/
        # 整数变更与纯内存容器构建，无 I/O。
        self._lock = threading.RLock()
        logger.info("[SubagentLifecycle] 初始化完成，最大分身数: %d", max_subagents)

    # ════════════════════════════════════════════════════════════════════
    #  创建
    # ════════════════════════════════════════════════════════════════════

    def create(self, config: SubagentConfig) -> SubagentContainer:
        """创建并初始化一个分身

        执行流程：
        1. 名称唯一性检查
        2. 容量检查
        3. 超时分身清理
        4. 创建 SubagentContainer

        Args:
            config: 分身配置

        Returns:
            创建好的 SubagentContainer

        Raises:
            SubagentLifecycleError: 名称冲突或已达上限
        """
        with self._lock:
            # 名称唯一性检查
            if config.name in self._subagents:
                raise SubagentLifecycleError(
                    f"分身名称已存在: {config.name}。如需替换，请先 destroy() 或使用 hot_reload()"
                )

            # 容量检查（先做一次 GC；RLock 同线程重入）
            self.gc()
            if len(self._subagents) >= self._max_subagents:
                raise SubagentLifecycleError(
                    f"分身数量已达上限 ({self._max_subagents})。"
                    f"请先销毁不再使用的分身，或调整 max_subagents"
                )

            # 创建容器（纯内存构建，无 I/O，遵守持锁纪律）
            container = SubagentContainer(config)
            self._subagents[config.name] = container
            self._total_created += 1

        logger.info("[SubagentLifecycle] 分身已创建: %s (id=%s, 活跃=%d, 总计=%d)",
                    config.name, container.id, len(self._subagents), self._total_created)

        return container

    # ════════════════════════════════════════════════════════════════════
    #  销毁
    # ════════════════════════════════════════════════════════════════════

    def destroy(self, subagent: SubagentContainer) -> dict[str, object]:
        """销毁一个分身

        执行清理：
        1. 标记为已销毁
        2. 提取记忆增量（memory_delta）供持久化
        3. 从管理器中移除

        Args:
            subagent: 要销毁的分身容器

        Returns:
            包含记忆增量的清理报告
        """
        with self._lock:
            name = subagent.config.name
            # get_memory_delta 为纯内存 dict 拷贝（遵守持锁纪律：锁内无 I/O）
            memory_delta = subagent.get_memory_delta()

            # 标记销毁
            subagent._is_destroyed = True

            # 从管理器中移除
            if name in self._subagents:
                del self._subagents[name]

            self._total_destroyed += 1

            cleanup_report = {
                "name": name,
                "id": subagent.id,
                "model_id": subagent.config.model_id,
                "memory_provider": subagent.config.memory_provider,
                "context_size": len(subagent.context),
                "memory_delta_keys": list(memory_delta.keys()),
                "age_seconds": round(subagent.age_seconds, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        logger.info("[SubagentLifecycle] 分身已销毁: %s (id=%s, 年龄=%.1fs, 记忆增量=%d项)",
                    name, subagent.id, subagent.age_seconds, len(memory_delta))

        return cleanup_report

    # ════════════════════════════════════════════════════════════════════
    #  热更新
    # ════════════════════════════════════════════════════════════════════

    def hot_reload(self, subagent: SubagentContainer, new_config: SubagentConfig):
        """运行时热更新分身配置

        更新策略（设计文档 2.3）：
        - 模型 ID 变更 → 下次 execute() 生效
        - 记忆提供商变更 → 下次 execute() 生效
        - 工具源变更 → 下次 execute() 生效
        - 权限变更 → 立即更新沙箱
        - 名称变更 → 更新管理器索引

        Args:
            subagent: 目标分身
            new_config: 新配置

        Raises:
            SubagentLifecycleError: 新名称与其他分身冲突
        """
        with self._lock:
            old_name = subagent.config.name
            new_name = new_config.name

            # 如果改名，检查新名称是否可用
            if new_name != old_name:
                if new_name in self._subagents:
                    raise SubagentLifecycleError(
                        f"分身名称已存在，无法热更新: {new_name}"
                    )

            # 记录变更日志
            changes = []
            if new_config.model_id != subagent.config.model_id:
                changes.append(f"model: {subagent.config.model_id} -> {new_config.model_id}")
            if new_config.memory_provider != subagent.config.memory_provider:
                changes.append(f"memory: {subagent.config.memory_provider} -> {new_config.memory_provider}")
            if new_config.tool_sources != subagent.config.tool_sources:
                changes.append(f"tools: {subagent.config.tool_sources} -> {new_config.tool_sources}")
            if new_config.permissions != subagent.config.permissions:
                changes.append(f"permissions: {subagent.config.permissions} -> {new_config.permissions}")
                # 权限变更立即更新沙箱（模块已由 container.py 加载，sys.modules 命中）
                subagent._sandbox = __import__("agent.subagent.sandbox", fromlist=["Sandbox"]).Sandbox(
                    allowed_permissions=set(new_config.permissions)
                )

            # 更新配置
            subagent.config = new_config

            # 如果改名，更新索引（check→pop/insert 与 create 的 TOCTOU 同源，锁内原子）
            if new_name != old_name:
                self._subagents[new_name] = self._subagents.pop(old_name)

            subagent.updated_at = time.time()

        logger.info("[SubagentLifecycle] 分身热更新: %s -> %s (%s)",
                    old_name, new_name, "; ".join(changes) if changes else "无变更")

    # ════════════════════════════════════════════════════════════════════
    #  查询
    # ════════════════════════════════════════════════════════════════════

    def get(self, name: str) -> Optional[SubagentContainer]:
        """按名称获取分身

        Args:
            name: 分身名称

        Returns:
            SubagentContainer 或 None
        """
        with self._lock:
            return self._subagents.get(name)

    def get_by_id(self, subagent_id: str) -> Optional[SubagentContainer]:
        """按 ID 获取分身

        Args:
            subagent_id: 分身 ID

        Returns:
            SubagentContainer 或 None
        """
        with self._lock:
            for sa in self._subagents.values():
                if sa.id == subagent_id:
                    return sa
        return None

    def list(self) -> list[SubagentContainer]:
        """获取当前所有活跃分身

        Returns:
            活跃分身列表
        """
        with self._lock:
            return list(self._subagents.values())

    def list_by_tag(self, tag: str) -> List[SubagentContainer]:
        """按标签列出分身

        Args:
            tag: 标签名称

        Returns:
            匹配的分身列表
        """
        with self._lock:
            return [sa for sa in self._subagents.values() if tag in sa.config.tags]

    def list_by_permission(self, permission: str) -> List[SubagentContainer]:
        """按权限列出分身

        Args:
            permission: 权限名称（如 'network', 'execute'）

        Returns:
            匹配的分身列表
        """
        with self._lock:
            return [sa for sa in self._subagents.values() if permission in sa.config.permissions]

    def count(self) -> int:
        """当前活跃分身数"""
        with self._lock:
            return len(self._subagents)

    # ════════════════════════════════════════════════════════════════════
    #  真实委派（v7.2 §3.9 / §5.9）
    # ════════════════════════════════════════════════════════════════════

    def _prepare_config(self, config: SubagentConfig, ctx: Any) -> SubagentConfig:
        """把一份配置模板适配到**一次**委派：TTL 取契约⑦ + 名称冲突消歧

        ``delegate`` 与 ``delegate_many`` 共用这一份实现（单一权威，不复制纪律）：

        1. **分身 TTL 取契约⑦**：``config.ttl_seconds`` 为 0（永久）时改为
           ``ceil(timeout_seconds)``——分身存活期不应超过委派契约声明的任务时长，
           否则超时后的分身会长期残留。TTL **就地写回**传入的模板（既有行为不变）。
        2. **名称冲突自动消歧**：并行委派常复用同一份配置模板，同名会撞
           ``create()`` 的唯一性检查；此处按 ``delegation_id`` 追加后缀，**不改**
           ``create()`` 自身的一致性行为。

        Returns:
            可直接交给 ``create()`` 的配置（冲突时是一个改名后的副本）。
        """
        if not config.ttl_seconds or config.ttl_seconds <= 0:
            try:
                config.ttl_seconds = max(1, int(math.ceil(float(ctx.timeout_seconds))))
            except (TypeError, ValueError, AttributeError):
                pass

        name = config.name
        with self._lock:
            taken = name in self._subagents
        if taken:
            config = replace(config, name=f"{name}-{getattr(ctx, 'delegation_id', 'x')}")
        return config

    def _unavailable_outcome(self, ctx: Any, error: str) -> Any:
        """建分身失败时的**就地失败结果**（批量语义：不拖垮同批其余任务）"""
        from agent.subagent.executor import ExecutionOutcome

        return ExecutionOutcome(
            delegation_id=str(getattr(ctx, "delegation_id", "") or ""), ok=False,
            error_code=E_SUBAGENT_UNAVAILABLE, error=error,
            sub_reason=SUB_REASON_SUBAGENT_UNAVAILABLE)

    def delegate(
        self,
        config: SubagentConfig,
        ctx: Any,
        *,
        executor: Any = None,
        llm: Any = None,
        destroy_after: bool = True,
        tools: Any = (),
        authorized_capabilities: Any = None,
        credentials: Any = (),
        parent_trace: Any = None,
        input_text: str = "",
    ) -> Any:
        """创建分身 → 执行委派 → （默认）销毁：分身生命周期与委派契约对齐

        两条与 §5.9 / §3.9 对齐的语义（**分身 TTL 取契约⑦**、**名称冲突自动消歧**）
        由 :meth:`_prepare_config` 统一实现，单发与批量走同一份纪律。

        Args:
            config: 分身配置模板。
            ctx: 委派上下文（八要素）。
            executor / llm / tools / authorized_capabilities / credentials /
                parent_trace / input_text: 透传 ``SubagentContainer.run_delegation``。
            destroy_after: 执行后是否销毁分身（默认 True——委派结束即回收）。

        Returns:
            ``ExecutionOutcome``。
        """
        config = self._prepare_config(config, ctx)

        container = self.create(config)
        try:
            return container.run_delegation(
                ctx, executor=executor, llm=llm, tools=tools,
                authorized_capabilities=authorized_capabilities,
                credentials=credentials, parent_trace=parent_trace,
                input_text=input_text)
        finally:
            if destroy_after and not container.is_destroyed:
                self.destroy(container)

    def delegate_many(
        self,
        specs: "Sequence[Tuple[SubagentConfig, Any]]",
        *,
        executor: Any = None,
        llm: Any = None,
        destroy_after: bool = True,
        max_concurrency: Optional[int] = None,
        tools: Any = (),
        authorized_capabilities: Any = None,
        tools_for: Any = None,
        authorized_for: Any = None,
        credentials_for: Any = None,
        parent_trace: Any = None,
    ) -> List[Any]:
        """**批量**委派：每个任务一个分身 → 并发执行 → 统一回收（``delegate`` 的批量版）

        ``delegate()`` 是「一条上下文一个分身」的**串行**入口；本方法把同一套分身纪律
        （TTL 取契约⑦、名称冲突消歧、执行后回收）推广到一批任务，而并发原语**直接复用**
        ``DelegationExecutor.execute_many``（不自建线程池、不复制屏障/回压逻辑）。

        与 ``delegate()`` 的三点差别：
        1. 逐任务**复制**配置模板后再适配 —— 同一模板被多条任务复用时，各任务的 TTL
           分别取自己的契约⑦，不会互相覆盖；
        2. 建分身失败（容量上限/名称冲突）**就地收敛**为 ``ok=False`` 的结果
           （``E_SUBAGENT_UNAVAILABLE``），不影响同批其余任务（单发入口是失败即抛）；
        3. 逐任务工具集经 ``tools_for`` / ``authorized_for`` 工厂传入（同一批里各任务
           可以各自持有不同的授权集，例如"各按自己的主线档案装配"）。

        Args:
            specs: ``[(SubagentConfig 模板, DelegationContext), …]``，顺序即结果顺序。
            executor: 注入的执行器；缺省按 ``llm`` 新建 ``DelegationExecutor``。
            llm: 缺省执行器所用的 LLM。
            destroy_after: 执行后是否销毁全部分身（默认 True——批量结束即回收）。
            max_concurrency / tools / authorized_capabilities / tools_for /
                authorized_for / credentials_for / parent_trace: 透传 ``execute_many``。

        Returns:
            与 ``specs`` **等长同序**的 ``ExecutionOutcome`` 列表。
        """
        items: list = [(config, ctx) for config, ctx in specs]
        if not items:
            return []
        if executor is None:
            from agent.subagent.executor import DelegationExecutor

            executor = DelegationExecutor(llm=llm)

        results: list = [None] * len(items)
        containers: dict = {}
        runnable: list = []
        contexts: list = []
        for idx, (config, ctx) in enumerate(items):
            # 逐任务复制模板：批量里 TTL 取各自契约⑦（不共享模板的可变字段）
            prepared = self._prepare_config(replace(config), ctx)
            try:
                containers[idx] = self.create(prepared)
            except SubagentLifecycleError as e:
                logger.warning("[SubagentLifecycle] 批量委派建分身失败（第 %d 条）: %s",
                               idx + 1, e)
                results[idx] = self._unavailable_outcome(ctx, str(e))
                continue
            runnable.append(idx)
            contexts.append(ctx)

        try:
            if contexts:
                outcomes = list(executor.execute_many(
                    contexts, max_concurrency=max_concurrency, tools=tools,
                    authorized_capabilities=authorized_capabilities,
                    tools_for=tools_for, authorized_for=authorized_for,
                    credentials_for=credentials_for, parent_trace=parent_trace))
                # execute_many 保证按输入顺序返回；仍优先按 delegation_id 归属，
                # 命不中时回退到位置（与 executor 的顺序契约一致）
                by_id = {str(getattr(o, "delegation_id", "") or ""): o for o in outcomes}
                for pos, idx in enumerate(runnable):
                    ctx = items[idx][1]
                    outcome = by_id.get(str(getattr(ctx, "delegation_id", "") or ""))
                    if outcome is None and pos < len(outcomes):
                        outcome = outcomes[pos]
                    if outcome is None:
                        outcome = self._unavailable_outcome(
                            ctx, "执行器未返回该任务的结果")
                    results[idx] = outcome
        finally:
            if destroy_after:
                for container in containers.values():
                    if not container.is_destroyed:
                        self.destroy(container)

        filled = sum(1 for r in results if r is not None)
        logger.info("[SubagentLifecycle] 批量委派完成: 任务=%d 已建分身=%d 已回填结果=%d",
                    len(items), len(containers), filled)
        return results

    # ════════════════════════════════════════════════════════════════════
    #  垃圾回收
    # ════════════════════════════════════════════════════════════════════

    def gc(self) -> int:
        """回收已超时的分身

        自动销毁所有 ttl 到期的分身，释放资源。

        Returns:
            回收的分身数量
        """
        with self._lock:
            # 读快照后逐一销毁（RLock 重入 destroy；快照避免迭代中 dict 变更崩溃）
            expired = [sa for sa in self._subagents.values() if sa.is_expired]
            for sa in expired:
                self.destroy(sa)
        if expired:
            logger.info("[SubagentLifecycle] GC 回收 %d 个超时分身", len(expired))
        return len(expired)

    # ════════════════════════════════════════════════════════════════════
    #  统计信息
    # ════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """获取生命周期管理器统计信息"""
        with self._lock:
            subagents = list(self._subagents.values())
            return {
                "active_count": len(subagents),
                "max_subagents": self._max_subagents,
                "total_created": self._total_created,
                "total_destroyed": self._total_destroyed,
                "usage_pct": round(len(subagents) / max(self._max_subagents, 1) * 100, 1),
                "subagents": [
                    {
                        "id": sa.id,
                        "name": sa.config.name,
                        "model_id": sa.config.model_id,
                        "memory_provider": sa.config.memory_provider,
                        "permissions": list(sa.config.permissions),
                        "context_size": len(sa.context),
                        "age_seconds": round(sa.age_seconds, 1),
                        "is_expired": sa.is_expired,
                    }
                    for sa in subagents
                ],
            }

    def __repr__(self) -> str:
        with self._lock:
            return f"<SubagentLifecycleManager 活跃={len(self._subagents)}/{self._max_subagents}>"
