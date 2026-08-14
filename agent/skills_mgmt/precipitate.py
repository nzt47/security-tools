"""记忆/反馈 → Skill DRAFT 沉淀调度器（TASK-04 Step 2）

背景（Why）:
    memory_abstractor 代码完整但全局无调用方（CHANGELOG 曾修"死代码导入"）。
    本模块接线其主入口 abstract_new_skills 为定时任务（复用 task_scheduler，
    与 offline_evolver.schedule 同模式），默认关闭（安全底线）。

【不易】约束（禁止触碰）:
    - auto_register 保持默认 False（本调度强制 false）：沉淀只产 DRAFT 草稿，
      不调 create_manual、不写入 skills_mgmt store，符合"不擅自注册技能"
    - 默认关闭：learning.precipitate_enabled=false（.env LEARNING_PRECIPITATE_ENABLED 覆盖）
    - 草稿去向（变更说明裁决 R2）：质量门控通过的草稿只写审计日志 +
      沉淀增量 KPI（TASK-03 learning.artifacts.skill），不落盘

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    LEARNING_PRECIPITATE_ENABLED       / learning.precipitate_enabled      (默认 false)
    LEARNING_PRECIPITATE_INTERVAL_HOURS / learning.precipitate.interval_hours (默认 24)
    LEARNING_PRECIPITATE_AUDIT_FILE    / learning.precipitate.audit_file   (默认 data/precipitate_audit.jsonl)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TASK_NAME = "技能沉淀"
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_AUDIT_FILE = "data/precipitate_audit.jsonl"
# 环境变量覆盖 config.yaml 的前缀
_ENV_PREFIX = "LEARNING_PRECIPITATE"

# 审计 JSONL 并发写锁（Why: Windows 上 append 模式由 CRT 模拟
# 「lseek 到 EOF 再写」，多句柄并发写非原子，会相互覆盖截断导致
# 多字节 UTF-8 字符损坏——2026-08-14 P0 #3 高并发压力测试发现；
# 进程内锁串行化写后完整）
_audit_write_lock = threading.Lock()


def _precipitate_enabled() -> bool:
    """优先级: 环境变量 > config.yaml learning.precipitate_enabled > 默认 false。"""
    env = os.environ.get(f"{_ENV_PREFIX}_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = (cfg.get("learning", {}) or {}).get("precipitate_enabled")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug("[Precipitate] config.yaml 读取失败: %s", e)
    return False


def _precipitate_interval_hours() -> int:
    """优先级: 环境变量 > config.yaml learning.precipitate.interval_hours > 默认 24。"""
    env = os.environ.get(f"{_ENV_PREFIX}_INTERVAL_HOURS")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            logger.warning("[Precipitate] 非法 interval_hours=%r，使用默认 24", env)
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("precipitate", {})
                   or {}).get("interval_hours")
            if val is not None:
                try:
                    return max(1, int(val))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug("[Precipitate] config.yaml 读取失败: %s", e)
    return DEFAULT_INTERVAL_HOURS


def _audit_file() -> str:
    """审计日志路径（默认 data/precipitate_audit.jsonl，env 覆盖）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_AUDIT_FILE")
    if env is not None and env.strip():
        return env.strip()
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("precipitate", {})
                   or {}).get("audit_file")
            if val:
                return str(val)
    except Exception as e:  # noqa: BLE001
        logger.debug("[Precipitate] config.yaml 读取失败: %s", e)
    return DEFAULT_AUDIT_FILE


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    import yaml as _yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


class PrecipitateScheduler:
    """memory_abstractor 定时沉淀调度器（默认关闭）"""

    def __init__(self, *, abstractor: Optional[Any] = None,
                 audit_path: Optional[str] = None):
        """Args:
            abstractor: MemorySkillAbstractor 实例（None 时懒加载）
            audit_path: 审计日志路径（None 时按配置/默认）
        """
        self._abstractor = abstractor
        self._audit_path = audit_path
        self._interval_hours = DEFAULT_INTERVAL_HOURS
        self._days = 30
        self._max_skills = 5
        self._scheduled_task_id: Optional[str] = None

    def _get_abstractor(self) -> Any:
        if self._abstractor is None:
            from agent.skills_mgmt.memory_abstractor import MemorySkillAbstractor
            self._abstractor = MemorySkillAbstractor()
        return self._abstractor

    # ─── 调度注册 ───

    def schedule(self, *, interval_hours: Optional[int] = None,
                 days: int = 30, max_skills: int = 5,
                 auto_register: bool = False) -> Dict[str, Any]:
        """注册沉淀定时任务（默认关闭，安全底线）。

        auto_register 无论传入什么都被强制为 False（【不易】不变式）。
        """
        if auto_register:
            logger.warning(
                "[Precipitate] auto_register=True 被拒绝，强制回退 False（不变式：不擅自注册）")
            auto_register = False
        self._interval_hours = (
            interval_hours if interval_hours is not None
            else _precipitate_interval_hours())
        self._days = days
        self._max_skills = max_skills

        if not _precipitate_enabled():
            logger.warning(
                "[Precipitate] 沉淀调度默认关闭（安全底线）；"
                "开启: config learning.precipitate_enabled=true / .env LEARNING_PRECIPITATE_ENABLED=true")
            return {
                "status": "disabled",
                "interval_hours": self._interval_hours,
                "auto_register": False,
                "note": "learning.precipitate_enabled=false，沉淀调度默认关闭（安全底线）",
            }

        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001 调度器不可用
            logger.error("[Precipitate] 调度器不可用: %s", e)
            return {"status": "error", "error": str(e)}

        sched.add_interval_task(
            TASK_NAME, func=self._scheduled_run,
            interval_seconds=self._interval_hours * 3600)
        self._scheduled_task_id = (
            sched.tasks[-1]["task_id"] if sched.tasks else "precipitate")
        logger.info("[Precipitate] 定时沉淀已注册 interval_hours=%d task_id=%s",
                    self._interval_hours, self._scheduled_task_id)
        return {
            "status": "scheduled",
            "task_id": self._scheduled_task_id,
            "interval_hours": self._interval_hours,
            "auto_register": False,
            "note": "定时运行 abstract_new_skills(auto_register=False)，产物仅 DRAFT+审计+KPI",
        }

    def unschedule(self) -> bool:
        """注销沉淀 cron 任务（按固定任务名定位，可跨实例）。"""
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001
            logger.error("[Precipitate] 调度注销失败: %s", e)
            return False
        for task in sched.tasks:
            if task.get("name") == TASK_NAME:
                removed = sched.remove_task(task["task_id"])
                self._scheduled_task_id = None
                return removed
        return False

    # ─── 定时执行 ───

    def _scheduled_run(self) -> None:
        """调度触发入口：跑一轮抽象；异常不抛出（调度线程稳定性）。"""
        logger.info("[Precipitate] scheduled_run.start interval_hours=%d",
                    self._interval_hours)
        try:
            results = self._get_abstractor().abstract_new_skills(
                days=self._days, max_skills=self._max_skills,
                auto_register=False)
        except Exception as e:  # noqa: BLE001 抽象失败不阻断调度线程
            logger.error("[Precipitate] scheduled_run 失败: %s", e)
            return
        passed = 0
        for r in results:
            if not r.get("quality_gate_passed"):
                continue
            passed += 1
            self._audit_draft(r)
            _kpi_record("skill")
        logger.info("[Precipitate] scheduled_run 完成 草稿=%d 质量门控通过=%d",
                    len(results), passed)

    # ─── 内部 ───

    def _audit_draft(self, result: Dict[str, Any]) -> None:
        """质量门控通过的草稿 → JSONL 审计日志（草稿不落盘，审计留痕）。

        P0 #3 阶段 0 数据物化（2026-08-14）: 审计记录携带完整草稿内容
        draft_body（memory_abstractor.result["draft"] 的 JSON 序列化），
        供人工确认闭环（confirm_precipitate_draft）重建草稿；序列化失败
        降级为 draft_content_preview 摘要（不阻断审计）。
        """
        draft = result.get("draft") or {}
        logger.debug("[Precipitate] _audit_draft 开始: draft_skill_id=%s draft_name=%s "
                     "cluster_id=%s cluster_size=%s success_rate=%s",
                     result.get("draft_skill_id"), result.get("draft_name"),
                     result.get("cluster_id"), result.get("cluster_size"),
                     result.get("success_rate"))
        try:
            draft_body = json.dumps(draft, ensure_ascii=False)
            logger.debug("[Precipitate] _audit_draft draft_body 序列化成功: "
                         "draft_skill_id=%s body_len=%d",
                         result.get("draft_skill_id"), len(draft_body))
        except (TypeError, ValueError) as e:
            # 降级为 preview 摘要（不阻断审计）；调试日志暴露降级原因与降级内容
            logger.debug("[Precipitate] _audit_draft draft_body 序列化失败，降级 preview: "
                         "draft_skill_id=%s 原因=%s", result.get("draft_skill_id"), e)
            draft_body = json.dumps({
                "name": result.get("draft_name"),
                "description": result.get("draft_description"),
                "content": result.get("draft_content_preview", ""),
            }, ensure_ascii=False)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "precipitate_draft",
            "draft_skill_id": result.get("draft_skill_id"),
            "draft_name": result.get("draft_name"),
            "cluster_id": result.get("cluster_id"),
            "cluster_size": result.get("cluster_size"),
            "success_rate": result.get("success_rate"),
            "registered": bool(result.get("registered")),
            "draft_body": draft_body,
        }
        try:
            with _audit_write_lock:  # 串行化 append 写（Windows 非原子，见模块注释）
                path = Path(self._audit_path or _audit_file())
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            logger.debug("[Precipitate] _audit_draft 审计写入成功: path=%s "
                         "draft_skill_id=%s rec_len=%d",
                         path, result.get("draft_skill_id"), len(json.dumps(rec, ensure_ascii=False)))
        except OSError as e:
            logger.warning("[Precipitate] 审计日志写入失败: %s", e)


def _kpi_record(artifact_type: str) -> None:
    """沉淀增量 KPI（TASK-03 learning.artifacts.*）；埋点不可用静默降级。"""
    try:
        from agent.learning_metrics import get_learning_metrics
        get_learning_metrics().record_artifact(artifact_type)
    except Exception as e:  # noqa: BLE001
        logger.debug("[Precipitate] KPI record_artifact 失败: %s", e)


# 供上层（service 网关 / 测试）引用的只读常量
__all__: List[str] = [
    "PrecipitateScheduler", "TASK_NAME", "DEFAULT_INTERVAL_HOURS",
    "DEFAULT_AUDIT_FILE", "_precipitate_enabled", "_precipitate_interval_hours",
]
