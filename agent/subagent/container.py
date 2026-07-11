"""Subagent 容器 — 分身独立容器定义

每个 SubagentContainer 持有：
- 选配的 LLM（模型 ID）
- 选配的记忆提供商
- 选配的工具集
- 独立的上下文窗口
- 执行结果追踪
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from agent.subagent.sandbox import Sandbox, PermissionDenied

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
#  配置定义
# ════════════════════════════════════════════════════════════════════


@dataclass
class SubagentConfig:
    """分身选配配置

    每个分身独立选配 LLM、记忆、工具集和权限。

    Attributes:
        name: 分身名称（唯一标识）
        model_id: 选配的 LLM 模型 ID（如 'gpt-4', 'claude-3-opus'）
        memory_provider: 记忆提供商名称（来自 MemoryRouter，如 'holographic', 'mem0'）
        tool_sources: 选配的工具源列表（如 ['builtin', 'mcp:filesystem']）
        context_window: 独立上下文窗口大小
        permissions: 权限声明列表
            - 'read': 读取文件/信息
            - 'write': 写入/修改
            - 'execute': 执行命令
            - 'network': 网络访问
            - 'system': 系统级操作
        tags: 标签（用于任务匹配）
        ttl_seconds: 存活时间（秒），超时自动销毁；0 表示永久
    """
    name: str
    model_id: str
    memory_provider: str = "holographic"
    tool_sources: list[str] = field(default_factory=list)
    context_window: int = 4096
    permissions: list[str] = field(default_factory=lambda: ["read"])
    tags: list[str] = field(default_factory=list)
    ttl_seconds: int = 0  # 0 = 永久存活


# ════════════════════════════════════════════════════════════════════
#  执行结果
# ════════════════════════════════════════════════════════════════════


@dataclass
class ExecutionResult:
    """分身执行结果

    Attributes:
        output: 执行输出文本
        trace_id: 执行追踪 ID
        memory_delta: 执行过程中产生的记忆变更（key → data 映射）
        tool_calls: 执行的工具调用记录
        error: 错误信息（如有）
        duration_ms: 执行耗时（毫秒）
        timestamp: 执行完成时间
    """
    output: str = ""
    trace_id: str = ""
    memory_delta: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: float = 0.0
    timestamp: str = ""


# ════════════════════════════════════════════════════════════════════
#  分身容器
# ════════════════════════════════════════════════════════════════════


class SubagentContainer:
    """分身容器

    每个实例代表一个独立的分身，包含：
    - 独立的上下文（消息历史）
    - 选配的组件（LLM、记忆、工具）
    - 生命周期状态
    - 执行隔离沙箱
    """

    def __init__(self, config: SubagentConfig):
        """
        Args:
            config: 分身配置
        """
        self.config = config
        self.id: str = f"sa-{uuid.uuid4().hex[:12]}"
        self.created_at: float = time.time()
        self.updated_at: float = self.created_at
        self._is_destroyed: bool = False

        # 独立上下文（消息历史）
        self.context: list[dict] = []

        # 沙箱（基于配置的权限声明）
        self._sandbox = Sandbox(allowed_permissions=set(config.permissions))

        # 运行时内存增量（执行产生的记忆变更）
        self._memory_delta: dict[str, Any] = {}

        logger.info("[Subagent] 创建分身: %s (id=%s, model=%s, memory=%s, permissions=%s)",
                    config.name, self.id, config.model_id, config.memory_provider, config.permissions)

    # ── 属性 ──

    @property
    def is_expired(self) -> bool:
        """是否已超时

        Returns:
            True 表示已超过 ttl_seconds 存活时间
        """
        if self.config.ttl_seconds <= 0:
            return False
        return (time.time() - self.created_at) > self.config.ttl_seconds

    @property
    def age_seconds(self) -> float:
        """分身已存活时间（秒）"""
        return time.time() - self.created_at

    @property
    def is_destroyed(self) -> bool:
        """是否已销毁"""
        return self._is_destroyed

    # ── 核心执行 ──

    def execute(self, task: str) -> ExecutionResult:
        """在隔离环境中执行任务

        执行流程：
        1. 检查分身是否已销毁
        2. 记录任务到上下文
        3. 权限检查（通过沙箱）
        4. 执行任务（选配组件由外部注入）
        5. 记录执行结果

        Args:
            task: 要执行的任务描述

        Returns:
            ExecutionResult: 执行结果
        """
        start_time = time.time()
        trace_id = uuid.uuid4().hex[:16]

        if self._is_destroyed:
            return ExecutionResult(
                error="分身已销毁，无法执行任务",
                trace_id=trace_id,
                duration_ms=0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        logger.info("[Subagent:%s] 执行任务: trace=%s, task=%.80s", self.id, trace_id, task)

        # 记录任务到上下文
        self.context.append({
            "role": "user",
            "content": task,
            "trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        try:
            # 沙箱权限检查骨架
            # TODO(P5): 实现完整的隔离执行引擎
            self._sandbox.check_execute(task)

            # 占位：实际 LLM 调用由外部机制注入
            # 目前返回占位结果，供集成测试验证
            output = (
                f"[Subagent:{self.config.name}] 收到任务: {task[:100]}...\n"
                f"  模型: {self.config.model_id}\n"
                f"  记忆: {self.config.memory_provider}\n"
                f"  Trace: {trace_id}\n"
                f"  (骨架实现，实际执行由 Orchestrator 注入)"
            )

            result = ExecutionResult(
                output=output,
                trace_id=trace_id,
                memory_delta=dict(self._memory_delta),
                duration_ms=(time.time() - start_time) * 1000,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            self.context.append({
                "role": "assistant",
                "content": result.output,
                "trace_id": trace_id,
            })

            self.updated_at = time.time()
            return result

        except PermissionDenied as e:
            logger.warning("[Subagent:%s] 权限拒绝: %s", self.id, e)
            return ExecutionResult(
                error=str(e),
                trace_id=trace_id,
                duration_ms=(time.time() - start_time) * 1000,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error("[Subagent:%s] 执行异常: %s", self.id, e)
            return ExecutionResult(
                error=f"执行异常: {e}",
                trace_id=trace_id,
                duration_ms=(time.time() - start_time) * 1000,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    # ── 记忆增量管理 ──

    def record_memory_delta(self, key: str, data: Any):
        """记录执行过程中产生的记忆变更

        Args:
            key: 记忆键
            data: 记忆数据
        """
        self._memory_delta[key] = data

    def get_memory_delta(self) -> dict[str, Any]:
        """获取记忆增量（用于持久化）"""
        return dict(self._memory_delta)

    def clear_memory_delta(self):
        """清空记忆增量（持久化后调用）"""
        self._memory_delta.clear()

    # ── 上下文管理 ──

    def clear_context(self):
        """清空上下文（保留分身，重置会话）"""
        self.context.clear()
        logger.info("[Subagent:%s] 上下文已清空", self.id)

    # ── 状态查询 ──

    def get_status(self) -> dict:
        """获取分身状态报告"""
        return {
            "id": self.id,
            "name": self.config.name,
            "model_id": self.config.model_id,
            "memory_provider": self.config.memory_provider,
            "tool_sources": list(self.config.tool_sources),
            "permissions": list(self.config.permissions),
            "tags": list(self.config.tags),
            "context_window": self.config.context_window,
            "context_used": len(self.context),
            "ttl_seconds": self.config.ttl_seconds,
            "age_seconds": round(self.age_seconds, 1),
            "is_expired": self.is_expired,
            "is_destroyed": self._is_destroyed,
            "created_at": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
            "updated_at": datetime.fromtimestamp(self.updated_at, tz=timezone.utc).isoformat(),
            "memory_delta_size": len(self._memory_delta),
        }

    def __repr__(self) -> str:
        return f"<Subagent {self.config.name} ({self.id}) model={self.config.model_id}>"
