"""Subagent 管理器——分身生命周期管理

从 orchestrator.py 提取，减轻主编排器负担。
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SubagentManager:
    """分身管理器——创建、销毁、热更新、列举、执行分身任务"""

    def __init__(self, orchestrator: Any):
        """绑定到 Orchestrator 实例

        Args:
            orchestrator: Orchestrator 实例（或其子类 DigitalLife）
        """
        self._o = orchestrator

    def create(self, config) -> object:
        """创建一个新分身"""
        if not self._o._subagent_mgr:
            raise RuntimeError("分身系统未启用，请设置 subagent.enabled=True")

        from agent.subagent.container import SubagentConfig

        if isinstance(config, dict):
            config = SubagentConfig(**config)
        elif not isinstance(config, SubagentConfig):
            raise TypeError(f"config 必须是 SubagentConfig 或 dict，收到: {type(config).__name__}")

        start = time.time()
        container = self._o._subagent_mgr.create(config)
        elapsed = (time.time() - start) * 1000
        logger.info("[Subagent:%s] 创建完成 (%.1fms)", container.id, elapsed)
        return container

    def destroy(self, name: str) -> dict:
        """销毁指定分身"""
        if not self._o._subagent_mgr:
            raise RuntimeError("分身系统未启用")

        container = self._o._subagent_mgr.get(name)
        if not container:
            raise ValueError(f"分身不存在: {name}")

        report = self._o._subagent_mgr.destroy(container)

        if report.get("memory_delta_keys"):
            logger.info("[Subagent:%s] 记忆增量待持久化: %s",
                        container.id, report["memory_delta_keys"])

        return report

    def hot_reload(self, name: str, new_config: dict):
        """热更新分身配置"""
        if not self._o._subagent_mgr:
            raise RuntimeError("分身系统未启用")

        container = self._o._subagent_mgr.get(name)
        if not container:
            raise ValueError(f"分身不存在: {name}")

        from agent.subagent.container import SubagentConfig

        if isinstance(new_config, dict):
            new_config_obj = SubagentConfig(**new_config)
        else:
            new_config_obj = new_config

        self._o._subagent_mgr.hot_reload(container, new_config_obj)
        logger.info("[Subagent] 分身热更新完成: %s", name)

    def list(self) -> list[dict]:
        """列出所有活跃分身的状态"""
        if not self._o._subagent_mgr:
            return []
        return [sa.get_status() for sa in self._o._subagent_mgr.list()]

    def get(self, name: str) -> Optional[dict]:
        """获取指定分身的详细状态"""
        if not self._o._subagent_mgr:
            return None
        container = self._o._subagent_mgr.get(name)
        return container.get_status() if container else None

    def execute(self, name: str, task: str) -> dict:
        """在指定分身中执行任务"""
        if not self._o._subagent_mgr:
            raise RuntimeError("分身系统未启用")

        container = self._o._subagent_mgr.get(name)
        if not container:
            raise ValueError(f"分身不存在: {name}")

        result = container.execute(task)
        return {
            "output": result.output,
            "trace_id": result.trace_id,
            "error": result.error,
            "duration_ms": round(result.duration_ms, 1),
            "timestamp": result.timestamp,
        }
