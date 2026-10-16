"""Subagent 管理器——分身生命周期管理

从 orchestrator.py 提取，减轻主编排器负担。
"""

import logging
import time
from typing import Optional, Any

logger = logging.getLogger(__name__)


class SubagentManager:
    """分身管理器——创建、销毁、热更新、列举、执行分身任务"""

    def __init__(self, orchestrator: Any):
        """绑定到 Orchestrator 实例

        Args:
            orchestrator: Orchestrator 实例（或其子类 DigitalLife）
        """
        self._o = orchestrator

    def _persist_enabled(self) -> bool:
        """是否落盘分身定义（P3）。

        【为什么用一个显式开关】本仓单测大量用 FakeYunshu / mock orchestrator 走
        /api/subagent/create（见 test_subagent_delegate_route 等）；若无门控，测试
        会把 '测试分身' 之类写进 data/subagents/，进而在真机重启时被恢复成容器。
        故默认**关**，由 config.yaml 的 subagent.persist_definitions 打开。
        mock orchestrator 通常没有 _config（或不是 dict）⇒ 恒关，测试零副作用。
        """
        cfg = getattr(self._o, "_config", None)
        if not isinstance(cfg, dict):
            return False
        return bool((cfg.get("subagent") or {}).get("persist_definitions", False))

    def _maybe_persist(self, config, raw_dict) -> None:
        """ttl=0（永久档）的显式创建落盘，供重启/回收后按定义恢复。best-effort。"""
        if not self._persist_enabled():
            return
        if int(getattr(config, "ttl_seconds", 0) or 0) != 0:
            return
        try:
            from dataclasses import asdict

            from agent.subagent import persistence
            payload = raw_dict if raw_dict is not None else asdict(config)
            persistence.persist_definition(payload)
        except Exception as e:  # noqa: BLE001 落盘失败不阻断创建
            logger.warning("[Subagent:%s] 定义持久化失败（重启后不会自动恢复）: %s",
                           getattr(config, "name", "?"), e)

    def _maybe_remove_definition(self, name: str) -> None:
        """destroy 时同步删定义，避免"销毁后重启又复活"。best-effort。"""
        if not self._persist_enabled():
            return
        try:
            from agent.subagent import persistence
            persistence.remove_definition(name)
        except Exception as e:  # noqa: BLE001 删除失败不阻断销毁
            logger.warning("[Subagent:%s] 定义删除失败（重启后可能复活）: %s", name, e)

    def create(self, config) -> object:
        """创建一个新分身"""
        if not self._o._subagent_mgr:
            raise RuntimeError("分身系统未启用，请设置 subagent.enabled=True")

        from agent.subagent.container import SubagentConfig

        raw_dict = dict(config) if isinstance(config, dict) else None
        if isinstance(config, dict):
            config = SubagentConfig(**config)
        elif not isinstance(config, SubagentConfig):
            raise TypeError(f"config 必须是 SubagentConfig 或 dict，收到: {type(config).__name__}")

        start = time.time()
        container = self._o._subagent_mgr.create(config)
        elapsed = (time.time() - start) * 1000
        logger.info("[Subagent:%s] 创建完成 (%.1fms)", container.id, elapsed)
        self._maybe_persist(config, raw_dict)
        return container

    def destroy(self, name: str) -> dict:
        """销毁指定分身"""
        if not self._o._subagent_mgr:
            raise RuntimeError("分身系统未启用")

        container = self._o._subagent_mgr.get(name)
        if not container:
            raise ValueError(f"分身不存在: {name}")

        report = self._o._subagent_mgr.destroy(container)
        self._maybe_remove_definition(name)

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
