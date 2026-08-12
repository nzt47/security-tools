"""阶段 2（D9 升级）：计划持久化存储层（配置感知门面）

【不易】基于既有 PlanDB（persistence.py）扩展——save_plan_checkpoint /
  _load_plans_from_disk / executor.persistence 注入等调用方语义零变化；
  存量配置 planning.persist_dir / planning.persist_db 优先于新配置 storage.path，
  存量部署零迁移。
【变易】planning.storage.enabled（默认 true）可整体关闭持久化；
  planning.storage.path 可指定库路径（默认 ./data/planning/plans.db）。
【简易】薄门面：路径解析 + 开关 + 恢复语义，SQLite 实现全部复用 PlanDB。
"""

import logging
import os
from typing import Dict, Optional

from .persistence import PlanDB

logger = logging.getLogger(__name__)

# 阶段 2 默认存储路径（无任何 planning 配置时生效）
DEFAULT_STORAGE_PATH = os.path.join("data", "planning", "plans.db")


class PlanningStorage(PlanDB):
    """计划持久化门面：继承 PlanDB 全部落库能力（计划/任务/执行记录/转换历史）"""

    @classmethod
    def resolve_db_path(cls, planning_config: Optional[dict]) -> str:
        """解析 SQLite 库路径。

        优先级（向后兼容存量配置）：
          1. planning.storage.path          （阶段 2 新配置）
          2. planning.persist_db            （阶段 1 配置）
          3. planning.persist_dir/plans.db  （阶段 1 配置）
          4. 默认 ./data/planning/plans.db  （阶段 2 规格）

        Args:
            planning_config: core.config["planning"]（可能为 None/空）
        """
        planning_config = planning_config or {}
        storage_config = planning_config.get("storage", {}) or {}

        path = storage_config.get("path")
        if path:
            return path
        persist_db = planning_config.get("persist_db")
        if persist_db:
            return persist_db
        persist_dir = planning_config.get("persist_dir")
        if persist_dir:
            return os.path.join(persist_dir, "plans.db")
        return DEFAULT_STORAGE_PATH

    @classmethod
    def is_enabled(cls, planning_config: Optional[dict]) -> bool:
        """存储开关：planning.storage.enabled（默认 true）"""
        planning_config = planning_config or {}
        storage_config = planning_config.get("storage", {}) or {}
        return bool(storage_config.get("enabled", True))

    def __init__(self, db_path: str):
        super().__init__(db_path)
        logger.info(f"[PlanningStorage] 已初始化: {db_path}")

    # 恢复语义与 PlanDB 一致（load_unfinished_plans 继承自父类）
    def load_unfinished_plans(self) -> Dict[str, object]:
        """恢复未完成计划（EXECUTING/PAUSED 等可恢复状态，语义与阶段 1 一致）"""
        return super().load_unfinished_plans()
