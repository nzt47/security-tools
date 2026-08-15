"""offline_evolver 周期定时调度（TASK-05 Step 2）

背景（Why）:
    offline_evolver.py 的帕累托前沿批量进化管线完整，但定时触发依赖
    EVOLUTION_SCHEDULE_ENABLED 的 cron 注册（默认关闭），且其 _scheduled_run
    不产生 TASK-03 预留的"进化采纳率"KPI（record_evolution_candidate）。
    本模块在不触碰 offline_evolver.py 进化算法与 BatchEvolutionReport 结构的
    前提下（【不易】），注册周级 interval 任务（config learning.evolver.interval_days
    默认 7），回调包装 service.evolve_batch(trigger="scheduler") 并逐条记录 KPI、
    写审计摘要；dry_run 默认 true 时只做候选筛选预演，零提交、零副作用。

【不易】约束（禁止触碰）:
    - 不修改 offline_evolver.py 的进化算法与 BatchEvolutionReport 结构
    - 提交门槛保持 improvement>=0.05（算法内部既有逻辑，本模块不覆写）
    - 候选筛选沿用既有逻辑（usage>=min_usage 且 success_rate<target）
    - 变异失败/评估异常跳过不中断批量（evolve_batch 既有语义）
    - 默认 dry_run=true：不产生任何变异体提交 / KPI 计数 / 审计写入

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    LEARNING_EVOLVER_ENABLED      / learning.evolver.enabled      (默认 false)
    LEARNING_EVOLVER_INTERVAL_DAYS / learning.evolver.interval_days (默认 7)
    LEARNING_EVOLVER_DRY_RUN      / learning.evolver.dry_run      (默认 true)
    LEARNING_EVOLVER_AUDIT_FILE   / learning.evolver.audit_file   (默认 data/evolution_schedule_audit.jsonl)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TASK_NAME = "周期进化"
DEFAULT_INTERVAL_DAYS = 7
DEFAULT_AUDIT_FILE = "data/evolution_schedule_audit.jsonl"
DEFAULT_DRY_RUN = True
_ENV_PREFIX = "LEARNING_EVOLVER"


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    import yaml as _yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


def _enabled() -> bool:
    """优先级: 环境变量 > config.yaml learning.evolver.enabled > 默认 false。"""
    env = os.environ.get(f"{_ENV_PREFIX}_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("evolver", {})
                   or {}).get("enabled")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认
        logger.debug("[EvolutionScheduler] config.yaml 读取失败: %s", e)
    return False


def _interval_days() -> int:
    """优先级: 环境变量 > config.yaml learning.evolver.interval_days > 默认 7。"""
    env = os.environ.get(f"{_ENV_PREFIX}_INTERVAL_DAYS")
    if env is not None and env.strip():
        try:
            return max(1, int(env.strip()))
        except ValueError:
            logger.warning("[EvolutionScheduler] 非法 interval_days=%r，使用默认 %d",
                           env, DEFAULT_INTERVAL_DAYS)
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("evolver", {})
                   or {}).get("interval_days")
            if val is not None:
                try:
                    return max(1, int(val))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001
        logger.debug("[EvolutionScheduler] config.yaml 读取失败: %s", e)
    return DEFAULT_INTERVAL_DAYS


def _dry_run() -> bool:
    """dry-run 默认 true（不可变约束：默认不产生写操作）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_DRY_RUN")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("evolver", {})
                   or {}).get("dry_run")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001
        logger.debug("[EvolutionScheduler] config.yaml 读取失败: %s", e)
    return DEFAULT_DRY_RUN


def _audit_file() -> str:
    """审计日志路径（默认 data/evolution_schedule_audit.jsonl，env 覆盖）。"""
    env = os.environ.get(f"{_ENV_PREFIX}_AUDIT_FILE")
    if env is not None and env.strip():
        return env.strip()
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("learning", {}) or {}).get("evolver", {})
                   or {}).get("audit_file")
            if val:
                return str(val)
    except Exception as e:  # noqa: BLE001
        logger.debug("[EvolutionScheduler] config.yaml 读取失败: %s", e)
    return DEFAULT_AUDIT_FILE


class EvolutionScheduler:
    """offline_evolver 周期调度包装（默认 dry-run，不动算法）"""

    def __init__(self, *, service: Optional[Any] = None,
                 audit_path: Optional[str] = None):
        """Args:
            service: SkillsMgmtService 实例（None 时懒加载默认实例）
            audit_path: 审计日志路径（None 时按配置/默认）
        """
        self._service = service
        self._audit_path = audit_path
        self._scheduled_task_id: Optional[str] = None

    def _get_service(self) -> Any:
        if self._service is None:
            from agent.skills_mgmt.service import SkillsMgmtService
            self._service = SkillsMgmtService()
        return self._service

    # ─── 主入口 ───

    def run(self, *, dry_run: bool = True, max_rounds: int = 1,
            trigger: str = "scheduler") -> Dict[str, Any]:
        """执行一轮周期进化（dry_run 只预演候选，零提交/零KPI/零审计）。

        Args:
            dry_run: True 只筛选候选生成预演报告；False 实际进化并记录 KPI/审计
            max_rounds: 最大进化轮次（透传 evolve_batch）
            trigger: 触发来源（scheduler/manual/api）

        Returns:
            报告 dict：dry_run/total_skills/evolved/skipped/failed/
            planned_candidates(仅 dry_run)/adopted_candidates/total_candidates/
            cost_tokens/audit_log
        """
        svc = self._get_service()
        report: Dict[str, Any] = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "trigger": trigger,
            "dry_run": bool(dry_run),
            "audit_log": self._audit_path or _audit_file(),
        }
        if dry_run:
            return self._preview(svc, report)
        return self._run_real(svc, report, max_rounds=max_rounds)

    def _preview(self, svc: Any, report: Dict[str, Any]) -> Dict[str, Any]:
        """dry-run 预演：只筛选候选（只读 store，零副作用）。"""
        try:
            evolver = svc._new_evolver()
            candidates = evolver._select_candidates()
        except Exception as e:  # noqa: BLE001 预演失败不影响调度
            logger.warning("[EvolutionScheduler] 候选预演失败: %s", e)
            report["planned_candidates"] = []
            report["error"] = str(e)
        else:
            report["planned_candidates"] = [
                {"skill_id": s.id, "usage_count": s.metrics.usage_count,
                 "success_rate": round(s.metrics.success_rate, 4)}
                for s in candidates]
        report["total_skills"] = len(report["planned_candidates"])
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        return report

    def _run_real(self, svc: Any, report: Dict[str, Any], *,
                  max_rounds: int) -> Dict[str, Any]:
        """正式执行：svc.evolve_batch（既有真实评估器/动态预算/谱系逻辑）。

        任一异常 → 报告 error 不中断调度线程；KPI 与审计仅成功路径产生。
        """
        try:
            batch = svc.evolve_batch(
                None, max_rounds=max_rounds, trigger=report["trigger"])
        except Exception as e:  # noqa: BLE001
            logger.error("[EvolutionScheduler] evolve_batch 失败: %s", e)
            report["error"] = str(e)
            report["finished_at"] = datetime.now().isoformat(timespec="seconds")
            return report

        report["total_skills"] = batch.get("total_skills", 0)
        report["evolved_count"] = batch.get("evolved_count", 0)
        report["skipped_count"] = batch.get("skipped_count", 0)
        report["failed_count"] = batch.get("failed_count", 0)
        report["avg_improvement"] = batch.get("avg_improvement")
        report["cost_tokens"] = batch.get("cost_tokens", 0)
        report["budget_breached"] = batch.get("budget_breached", False)
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")

        # TASK-03 KPI：进化采纳率（候选决策计入分母，提交计入分子）
        total_candidates = (report["evolved_count"] + report["skipped_count"]
                            + report["failed_count"])
        report["total_candidates"] = total_candidates
        report["adopted_candidates"] = report["evolved_count"]
        _record_evolution_kpi(total_candidates, report["evolved_count"])
        self._audit(report)
        return report

    # ─── 审计 ───

    def _audit(self, report: Dict[str, Any]) -> None:
        """正式运行批次摘要写 JSONL 审计（失败仅告警不阻断）。"""
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "evolution_schedule_run",
            "trigger": report.get("trigger"),
            "dry_run": False,
            "total_skills": report.get("total_skills", 0),
            "evolved_count": report.get("evolved_count", 0),
            "skipped_count": report.get("skipped_count", 0),
            "failed_count": report.get("failed_count", 0),
            "avg_improvement": report.get("avg_improvement"),
            "cost_tokens": report.get("cost_tokens", 0),
            "adopted_candidates": report.get("adopted_candidates", 0),
            "total_candidates": report.get("total_candidates", 0),
        }
        try:
            path = Path(self._audit_path or _audit_file())
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("[EvolutionScheduler] 审计日志写入失败: %s", e)

    # ─── 调度注册（与 TASK-04 同一 task_scheduler 收口） ───

    def schedule(self, *, interval_days: Optional[int] = None,
                 max_rounds: int = 1) -> Dict[str, Any]:
        """注册周级进化任务（默认关闭，安全底线）。"""
        days = interval_days if interval_days is not None else _interval_days()
        if not _enabled():
            logger.warning(
                "[EvolutionScheduler] 调度默认关闭（安全底线）；"
                "开启: config learning.evolver.enabled=true / "
                ".env LEARNING_EVOLVER_ENABLED=true")
            return {
                "status": "disabled",
                "interval_days": days,
                "dry_run": _dry_run(),
                "note": "learning.evolver.enabled=false，进化调度默认关闭（安全底线）",
            }
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001 调度器不可用
            logger.error("[EvolutionScheduler] 调度器不可用: %s", e)
            return {"status": "error", "error": str(e)}
        sched.add_interval_task(
            TASK_NAME, func=self._scheduled_run,
            interval_seconds=days * 86400)
        self._scheduled_task_id = (
            sched.tasks[-1]["task_id"] if sched.tasks else TASK_NAME)
        logger.info("[EvolutionScheduler] 周级进化任务已注册 interval_days=%d "
                    "dry_run=%s", days, _dry_run())
        return {
            "status": "scheduled",
            "task_id": self._scheduled_task_id,
            "interval_days": days,
            "dry_run": _dry_run(),
            "note": "定时执行 evolve_batch(trigger=scheduler)，提交门槛 improvement>=0.05",
        }

    def unschedule(self) -> bool:
        """注销周期进化任务（按固定任务名定位，可跨实例）。"""
        try:
            from agent.task_scheduler import get_scheduler
            sched = get_scheduler()
        except Exception as e:  # noqa: BLE001
            logger.error("[EvolutionScheduler] 调度注销失败: %s", e)
            return False
        for task in sched.tasks:
            if task.get("name") == TASK_NAME:
                removed = sched.remove_task(task["task_id"])
                self._scheduled_task_id = None
                return removed
        return False

    def _scheduled_run(self) -> None:
        """调度触发入口：跑一轮周期进化；异常不抛出（调度线程稳定性）。"""
        logger.info("[EvolutionScheduler] scheduled_run.start dry_run=%s",
                    _dry_run())
        try:
            self.run(dry_run=_dry_run(), trigger="scheduler")
        except Exception as e:  # noqa: BLE001
            logger.error("[EvolutionScheduler] scheduled_run 失败: %s", e)


def _record_evolution_kpi(total_candidates: int, adopted: int) -> None:
    """TASK-03 进化采纳率 KPI（埋点不可用静默降级）。"""
    if total_candidates <= 0:
        return
    try:
        from agent.learning_metrics import get_learning_metrics
        metrics = get_learning_metrics()
        for _ in range(total_candidates - adopted):
            metrics.record_evolution_candidate(adopted=False)
        for _ in range(adopted):
            metrics.record_evolution_candidate(adopted=True)
    except Exception as e:  # noqa: BLE001
        logger.debug("[EvolutionScheduler] KPI record_evolution_candidate 失败: %s", e)


__all__: List[str] = [
    "EvolutionScheduler", "TASK_NAME", "DEFAULT_INTERVAL_DAYS",
    "DEFAULT_AUDIT_FILE", "_enabled", "_interval_days", "_dry_run",
    "_audit_file",
]
