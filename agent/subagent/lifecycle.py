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
import time
from datetime import datetime, timezone
from typing import Optional

from agent.subagent.container import SubagentConfig, SubagentContainer

logger = logging.getLogger(__name__)


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
        # 名称唯一性检查
        if config.name in self._subagents:
            raise SubagentLifecycleError(
                f"分身名称已存在: {config.name}。如需替换，请先 destroy() 或使用 hot_reload()"
            )

        # 容量检查（先做一次 GC）
        self.gc()
        if len(self._subagents) >= self._max_subagents:
            raise SubagentLifecycleError(
                f"分身数量已达上限 ({self._max_subagents})。"
                f"请先销毁不再使用的分身，或调整 max_subagents"
            )

        # 创建容器
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
        name = subagent.config.name
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
            # 权限变更立即更新沙箱
            subagent._sandbox = __import__("agent.subagent.sandbox", fromlist=["Sandbox"]).Sandbox(
                allowed_permissions=set(new_config.permissions)
            )

        # 更新配置
        subagent.config = new_config

        # 如果改名，更新索引
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
        return self._subagents.get(name)

    def get_by_id(self, subagent_id: str) -> Optional[SubagentContainer]:
        """按 ID 获取分身

        Args:
            subagent_id: 分身 ID

        Returns:
            SubagentContainer 或 None
        """
        for sa in self._subagents.values():
            if sa.id == subagent_id:
                return sa
        return None

    def list(self) -> list[SubagentContainer]:
        """获取当前所有活跃分身

        Returns:
            活跃分身列表
        """
        return list(self._subagents.values())

    def list_by_tag(self, tag: str) -> list[SubagentContainer]:
        """按标签列出分身

        Args:
            tag: 标签名称

        Returns:
            匹配的分身列表
        """
        return [sa for sa in self._subagents.values() if tag in sa.config.tags]

    def list_by_permission(self, permission: str) -> list[SubagentContainer]:
        """按权限列出分身

        Args:
            permission: 权限名称（如 'network', 'execute'）

        Returns:
            匹配的分身列表
        """
        return [sa for sa in self._subagents.values() if permission in sa.config.permissions]

    def count(self) -> int:
        """当前活跃分身数"""
        return len(self._subagents)

    # ════════════════════════════════════════════════════════════════════
    #  垃圾回收
    # ════════════════════════════════════════════════════════════════════

    def gc(self) -> int:
        """回收已超时的分身

        自动销毁所有 ttl 到期的分身，释放资源。

        Returns:
            回收的分身数量
        """
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
        return f"<SubagentLifecycleManager 活跃={len(self._subagents)}/{self._max_subagents}>"
