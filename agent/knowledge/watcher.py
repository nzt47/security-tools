"""素材层文件监听（任务1 Step 3 · 低摩擦自动登记）。

新文件落入 knowledge/inbox 时自动登记 .meta.json 并追加 log.md。
实现主体在 `agent.knowledge.ingest.KnowledgeWatcher`（单一事实源），
本模块只做两件事（【简易】最小封装，不复制实现）：
  1. 对外再导出 `KnowledgeWatcher`，使契约"独立 watcher 模块"可被直接 import；
  2. 提供契约要求的进程内单例入口 `start_knowledge_watcher`。

【不易】进程内单例：重复调用 `start_knowledge_watcher` 不重复启动（契约 Step 3），
        同一时刻至多一个活动监听实例。
【变易】`stop_knowledge_watcher` 用于释放单例（CLI/测试生命周期收尾），无活动实例时空操作。

CLI: python -m agent.knowledge.ingest --watch（监听入口仍走 ingest 的 --watch 子命令）
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from agent.knowledge.ingest import KnowledgeWatcher, get_knowledge_root  # noqa: F401

__all__ = ["KnowledgeWatcher", "get_knowledge_root",
           "start_knowledge_watcher", "stop_knowledge_watcher"]

_LOCK = threading.Lock()
_ACTIVE: Optional[KnowledgeWatcher] = None


def start_knowledge_watcher(knowledge_root: str | Path) -> None:
    """启动 knowledge/ 素材层监听（进程内单例，重复调用不重复启动）。

    :param knowledge_root: knowledge 根目录（同 ingest.get_knowledge_root 语义）
    """
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None and _ACTIVE.is_running:
            return  # 已在监听：幂等返回，不重复启动
        _ACTIVE = KnowledgeWatcher(str(knowledge_root))
        _ACTIVE.start()


def stop_knowledge_watcher() -> None:
    """停止当前单例监听实例（无活动实例则空操作）。"""
    global _ACTIVE
    with _LOCK:
        if _ACTIVE is not None:
            _ACTIVE.stop()
            _ACTIVE = None
